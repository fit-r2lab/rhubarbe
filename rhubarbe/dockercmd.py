"""
Support for 'rhubarbe docker': run a user container on nodes that were
loaded with a docker-enabled frisbee host image.

Conventions (see the [docker] section in rhubarbe.conf)
* the remote command is always 'docker' (on podman hosts that is
  provided by podman-docker)
* the container is always named 'container' - the monitor, the login shell
  and 'docker save' rely on that
* the container owns ssh port 22; the host's sshd listens on
  [docker] host_ssh_port (2222) and is what we use to drive the node
"""

# pylint: disable=logging-fstring-interpolation

import os
import json
import shlex
import asyncio
from pathlib import Path

from .config import Config
from .ssh import SshProxy
from .logger import logger

CONTAINER_NAME = 'container'


def qualify_image(ref, registry):
    """
    used by 'docker save' only: prefix an image reference with the
    private registry, unless it already designates one, i.e. its first
    component contains a '.' or a ':' or is 'localhost'

    'docker load' passes references untouched, so that docker resolves
    them as usual (Docker Hub, through the mirror if there is one)
    """
    first, sep, _ = ref.partition('/')
    if sep and ('.' in first or ':' in first or first == 'localhost'):
        return ref
    return f"{registry}/{ref}"


# on the node, where the injected files live; mounted in the container
HOST_INJECT_DIR = "/opt/rhubarbe/inject"
CONTAINER_INJECT_DIR = "/.rhubarbe"
# on the node, what is mounted as /var in the container
PERSISTENT_VAR = "/home/container/container_fs"


def run_command(image, inject=False):
    """
    the 'docker run' command that creates the user container

    -dit : a tty and stdin, so that images whose command is a shell
    (like plain ubuntu) stay alive instead of exiting at once
    inject : mount the injected files (static sshd) in the container
    """
    dns = ""
    try:
        dns = f" --dns {Config().value('docker', 'dns')}"
    except Exception:                                   # pylint: disable=w0703
        pass
    mount = (f" -v {HOST_INJECT_DIR}:{CONTAINER_INJECT_DIR}:ro"
             if inject else "")
    # docker creates the source of a bind mount, podman does not
    return (
        f"mkdir -p {PERSISTENT_VAR} && "
        f"docker run -dit --name {CONTAINER_NAME} "
        f"--network=host --privileged --cap-add=ALL "
        f"-v /lib/modules:/lib/modules:ro -v /sys/fs/cgroup:/sys/fs/cgroup -v /var/run/dbus:/var/run/dbus "
        f"-v {PERSISTENT_VAR}:/var{mount}{dns} "
        f"{shlex.quote(image)}")


def _config_value(key):
    """optional [docker] key, None if absent"""
    try:
        return Config().value('docker', key) or None
    except Exception:                                   # pylint: disable=w0703
        return None


def mirror_settings():
    """
    returns (url, host, insecure) for the [docker] mirror, or None;
    a mirror given as http://... is deemed insecure
    """
    url = _config_value('mirror')
    if not url:
        return None
    scheme, sep, rest = url.rpartition('://')
    host = rest.rstrip('/')
    return url, host, (not sep) or scheme == 'http'


def merged_daemon_json(current_text, url, host, insecure):
    """
    merge the mirror settings into the contents of a docker daemon.json,
    other settings are preserved; returns the new text
    """
    try:
        daemon = json.loads(current_text) if current_text.strip() else {}
    except json.JSONDecodeError:
        daemon = {}
    mirrors = daemon.setdefault('registry-mirrors', [])
    if url not in mirrors:
        mirrors.append(url)
    if insecure:
        registries = daemon.setdefault('insecure-registries', [])
        if host not in registries:
            registries.append(host)
    return json.dumps(daemon, indent=2) + "\n"


def podman_registries_conf(host, insecure):
    """the registries.conf.d drop-in to mirror docker.io"""
    return (
        '[[registry]]\n'
        'prefix = "docker.io"\n'
        'location = "docker.io"\n'
        '\n'
        '[[registry.mirror]]\n'
        f'location = "{host}"\n'
        f'insecure = {"true" if insecure else "false"}\n')


async def configure_mirror(ssh):
    """
    point the node's engine at the [docker] mirror (if any); this is done
    at load time, so that host images need not know about any registry.
    returns True if all went well (or if there was nothing to do)
    """
    settings = mirror_settings()
    if settings is None:
        return True
    url, host, insecure = settings
    version = await ssh.run("docker --version 2>&1") or ""
    if 'podman' in version.lower():
        conf = podman_registries_conf(host, insecure)
        path = "/etc/containers/registries.conf.d/rhubarbe.conf"
        # nodocker silences the banner that podman-docker prints on stderr
        return await _step(
            ssh,
            f"mkdir -p {os.path.dirname(path)} && "
            f"touch /etc/containers/nodocker && "
            f"printf %s {shlex.quote(conf)} > {path}",
            "mirror (podman)")
    current = await ssh.run(
        "cat /etc/docker/daemon.json 2>/dev/null || true") or ""
    new = merged_daemon_json(current, url, host, insecure)
    try:
        if json.loads(current) == json.loads(new):
            # already configured: spare a docker restart, which also
            # kills the running container
            return True
    except json.JSONDecodeError:
        pass
    return await _step(
        ssh,
        f"mkdir -p /etc/docker && "
        f"printf %s {shlex.quote(new)} > /etc/docker/daemon.json && "
        f"systemctl restart docker",
        "mirror (docker)")


async def _step(ssh, command, label):
    """run one command on the host; returns True if it went well"""
    status, output = await ssh.run(command, with_status=True)
    ok = status == 0
    if not ok:
        print(f"{ssh.node.control_hostname()}: {label} FAILED "
              f"(status={status}) {(output or '').strip()}")
    logger.info(f"{ssh.node.control_hostname()}: {label} -> {status}")
    return ok


async def remove_previous(ssh):
    """
    trash the container of a previous session, if any, and say so;
    -v also removes its anonymous volumes
    """
    name = ssh.node.control_hostname()
    status, output = await ssh.run(
        f"docker ps -a --filter name=^{CONTAINER_NAME}$ "
        f"--format '{{{{.Image}}}} ({{{{.Status}}}})' 2>/dev/null",
        with_status=True)
    previous = (output or '').strip()
    if status != 0:
        print(f"{name}: could not look for a previous container: {previous}")
        return False
    if not previous:
        print(f"{name}: no previous container")
        return True
    print(f"{name}: removing previous container {previous}")
    return await _step(ssh, f"docker rm -fv {CONTAINER_NAME}", "remove")


async def _still_running(ssh):
    """
    'docker run -d' succeeds even if the container's command exits at once
    (e.g. a plain ubuntu image runs bash, which finds no tty and quits);
    so give it a moment and check it is still there
    """
    status, output = await ssh.run(
        f"sleep 2; docker inspect -f '{{{{.State.Running}}}}' {CONTAINER_NAME}"
        f" || true", with_status=True)
    if status == 0 and (output or '').strip().endswith('true'):
        return True
    _, logs = await ssh.run(
        f"docker inspect -f 'exit code {{{{.State.ExitCode}}}}, "
        f"command: {{{{.Path}}}} {{{{.Args}}}}' {CONTAINER_NAME}; "
        f"docker logs --tail 5 {CONTAINER_NAME} 2>&1", with_status=True)
    print(f"{ssh.node.control_hostname()}: container exited right after "
          f"start - its image must run a long-lived command "
          f"(init/systemd or sshd -D)\n{(logs or '').strip()}")
    return False


def _config_path(key):
    value = _config_value(key)
    return Path(value).expanduser() if value else None


async def prepare_injection(ssh):
    """
    upload the static sshd (dropbear) in the host's HOST_INJECT_DIR,
    to be mounted in the container.
    returns None if injection is not configured, else True/False for success
    """
    dropbear = _config_path('dropbear')
    if dropbear is None:
        return None
    name = ssh.node.control_hostname()
    if not dropbear.exists():
        print(f"{name}: [docker] dropbear = {dropbear} not found; "
              f"see r2lab-imaging/docker/dropbear")
        return False
    if not await _step(ssh, f"mkdir -p {HOST_INJECT_DIR}", "inject dir"):
        return False
    if not await ssh.put(dropbear, f"{HOST_INJECT_DIR}/dropbear"):
        print(f"{name}: could not upload {dropbear}")
        return False
    return await _step(ssh, f"chmod 755 {HOST_INJECT_DIR}/dropbear",
                       "inject dropbear")


async def _port22_listening(ssh):
    _, output = await ssh.run("ss -H -ltn 'sport = :22'", with_status=True)
    return bool((output or '').strip())


async def ensure_sshd(ssh, injected):
    """
    make sure something answers on port 22 in the container:
    images that run their own sshd (systemd ones...) are left alone;
    otherwise, if injection is set up, start the injected dropbear
    """
    grace = int(_config_value('sshd_grace') or 10)
    for _ in range(grace):
        if await _port22_listening(ssh):
            return True
        await asyncio.sleep(1)
    if not injected:
        # not an error as such: the image may not offer ssh at all
        print(f"{ssh.node.control_hostname()}: nothing listens on port 22 "
              f"in the container (no sshd in the image, and "
              f"[docker] dropbear is not set)")
        return True
    # no access control on the testbed (access is controlled upstream, at
    # the gateway): root gets an empty password, and dropbear accepts it
    d = CONTAINER_INJECT_DIR
    if not await _step(
            ssh,
            f"docker exec -u 0 {CONTAINER_NAME} "
            f"sed -i 's/^root:[^:]*:/root::/' /etc/shadow",
            "empty root password"):
        return False
    # -R generates the host keys on first connection, but only in the
    # default location, whose directory must exist
    if not await _step(
            ssh, f"docker exec -u 0 {CONTAINER_NAME} mkdir -p /etc/dropbear",
            "create /etc/dropbear"):
        return False
    # in the foreground (-F) with logs (-E), so that its stderr can go
    # in a file in the container, /tmp/dropbear.log, for debugging
    if not await _step(
            ssh,
            f"docker exec -u 0 -d {CONTAINER_NAME} sh -c "
            f"'{d}/dropbear -F -E -B -R -p 22 2> /tmp/dropbear.log'",
            "start injected sshd"):
        return False
    for _ in range(5):
        await asyncio.sleep(1)
        if await _port22_listening(ssh):
            return True
    print(f"{ssh.node.control_hostname()}: injected sshd does not listen "
          f"on port 22")
    return False


async def deploy(node, image, *, host_port, backoff, wait_container):
    """
    on a node that has booted its docker host image:
    pull the container image and run it; returns True if all went well
    """
    name = node.control_hostname()
    if not await SshProxy(node, port=host_port).wait_for(backoff):
        return False
    async with SshProxy(node, port=host_port) as ssh:
        if not await ssh.connect(timeout=10.):
            print(f"{name}: host ssh on port {host_port} KO")
            return False
        # the previous container goes first: configuring the engine may
        # restart it, and the pull must happen once the mirror is set up
        if not await remove_previous(ssh):
            return False
        if not await configure_mirror(ssh):
            return False
        if not await _step(ssh, f"docker pull {shlex.quote(image)}", "pull"):
            return False
        injected = await prepare_injection(ssh)
        if injected is False:
            return False
        if not await _step(ssh, run_command(image, bool(injected)), "run"):
            return False
        if not await _still_running(ssh):
            return False
        if not await ensure_sshd(ssh, bool(injected)):
            return False
    if wait_container:
        # container's sshd, on the standard port
        if not await SshProxy(node).wait_for(backoff):
            return False
    print(f"{name}: container running {image}")
    return True


async def commit_and_push(node, image, *, host_port):
    """
    commit the container of a node and push the resulting image
    """
    async with SshProxy(node, port=host_port) as ssh:
        if not await ssh.connect(timeout=10.):
            print(f"{node.control_hostname()}: host ssh on port {host_port} KO")
            return False
        for label, command in (
                ("commit", f"docker commit {CONTAINER_NAME} {shlex.quote(image)}"),
                ("push", f"docker push {shlex.quote(image)}")):
            if not await _step(ssh, command, label):
                return False
    print(f"{node.control_hostname()}: pushed {image}")
    return True
