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
import time
import json
import shlex
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
from urllib.parse import urljoin

from .config import Config
from .ssh import SshProxy
from .logger import logger

CONTAINER_NAME = 'container'
# on the node, where the injected files live; mounted in the container
HOST_INJECT_DIR = "/opt/rhubarbe/inject"
CONTAINER_INJECT_DIR = "/.rhubarbe"
# on the node, what is mounted as /var in the container
PERSISTENT_VAR = "/home/container/container_fs"


_start_time = None


def start_clock(start=None):
    """
    origin of the +NNNs counters in the messages; called at the beginning of
    the command, so that the counters carry on from the frisbee phase;
    returns it, to be given to Display(start_time=...)
    """
    global _start_time                                  # pylint: disable=w0603
    _start_time = start or time.time()
    return _start_time


def say(text):
    """print on the terminal, with the same prefix as the loading phase"""
    if _start_time is None:
        start_clock()
    print(f"{time.strftime('%H:%M:%S')} - "
          f"+{int(time.time() - _start_time):03}s: {text}", flush=True)


def prefix():
    """the namespace in the registry that is ours, e.g. r2lab"""
    return Config().value('docker', 'prefix').strip('/')


def _looks_like_registry(first_component):
    return ('.' in first_component or ':' in first_component
            or first_component == 'localhost')


def save_reference(ref):
    """
    the full reference under which 'docker save -o REF' pushes:
    registry/prefix/name[:tag]; the registry and the prefix are added
    unless they are already there; an explicit other registry is left alone

    'docker load' passes references untouched, so that docker resolves
    them as usual (Docker Hub, through the mirror if there is one)
    """
    first, sep, _ = ref.partition('/')
    if sep and _looks_like_registry(first):
        return ref
    if not ref.startswith(f"{prefix()}/"):
        ref = f"{prefix()}/{ref}"
    return f"{Config().value('docker', 'registry')}/{ref}"


def repository_and_tag(ref):
    """
    from a reference given to rmi (name, prefix/name, or a full
    registry/prefix/name, with an optional :tag), returns (repository, tag)
    where repository is the name in the registry, always under our prefix;
    raises ValueError if the reference is not in our namespace
    """
    registry = Config().value('docker', 'registry')
    first, sep, rest = ref.partition('/')
    if sep and _looks_like_registry(first):
        if first != registry:
            raise ValueError(f"{first} is not the registry {registry}")
        ref = rest
    name, colon, tag = ref.rpartition(':')
    if not colon or '/' in tag:
        name, tag = ref, 'latest'
    if not name.startswith(f"{prefix()}/"):
        name = f"{prefix()}/{name}"
    return name, tag


def registry_api_url(path=""):
    """
    the URL of the registry API (v2) of the [docker] registry,
    optionally followed by a path like _catalog
    """
    url = f"https://{Config().value('docker', 'registry')}/v2"
    return f"{url}/{path.lstrip('/')}" if path else url


def _registry_get_json(url):
    """GET a registry URL; returns (json, next page url or None)"""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    nxt = response.links.get('next', {}).get('url')
    return response.json(), urljoin(url, nxt) if nxt else None


def _list_references(api_url, label, keep):
    """
    all the label/name:tag of a registry whose v2 API is at api_url,
    for the names for which keep(name) is true;
    raises requests.RequestException if the registry cannot be probed
    """
    names = []
    url = f"{api_url}/_catalog?n=500"
    while url:
        data, url = _registry_get_json(url)
        names += data.get('repositories') or []
    names = [name for name in names if keep(name)]

    def tags(name):
        data, _ = _registry_get_json(f"{api_url}/{name}/tags/list")
        return [f"{label(name)}:{tag}" for tag in data.get('tags') or []]

    with ThreadPoolExecutor(max_workers=8) as executor:
        return sorted(ref for refs in executor.map(tags, sorted(names))
                      for ref in refs)


def list_images(patterns=(), show_all=False):
    """
    the images in the private registry, as a sorted list of references
    registry/name:tag; only the names that contain one of the patterns
    (all of them if there are none); only our own namespace (the prefix)
    unless show_all
    raises requests.RequestException if the registry cannot be probed
    """
    registry = Config().value('docker', 'registry')
    return _list_references(
        registry_api_url(), lambda name: f"{registry}/{name}",
        lambda name: (show_all or name.startswith(f"{prefix()}/"))
        and (not patterns or any(pat in name for pat in patterns)))


def list_mirror_images(patterns=()):
    """
    what the pull-through cache of Docker Hub holds, as references that
    can be given to docker load -i (ubuntu:latest rather than
    library/ubuntu:latest); None if there is no mirror configured
    raises requests.RequestException if the mirror cannot be probed
    """
    settings = mirror_settings()
    if settings is None:
        return None
    url, _, _ = settings
    if '://' not in url:
        url = f"https://{url}"

    def label(name):
        return name[len("library/"):] if name.startswith("library/") else name

    return _list_references(
        f"{url.rstrip('/')}/v2", label,
        lambda name: not patterns or any(pat in name for pat in patterns))


MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def registry_credentials():
    """
    (user, password) from [docker] registry_user / registry_password,
    or None unless both are set; meant for the root-only ~/.rhubarbe.conf
    """
    user = _config_value('registry_user')
    # raw: a '%' in a password is not an interpolation
    try:
        password = Config().parser['docker'].get(
            'registry_password', None, raw=True)
    except KeyError:
        password = None
    return (user, password) if user and password else None


def manifest_digest(repository, tag):
    """the digest of what a tag points to, None if there is no such tag"""
    response = requests.head(
        registry_api_url(f"{repository}/manifests/{tag}"),
        headers={'Accept': MANIFEST_TYPES}, timeout=10)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.headers['Docker-Content-Digest']


def delete_manifest(repository, digest):
    """
    delete by digest, which removes all the tags that point to it;
    the blobs are only reclaimed by the registry's garbage collection
    returns an error message, or None if all went well
    """
    response = requests.delete(
        registry_api_url(f"{repository}/manifests/{digest}"),
        auth=registry_credentials(), timeout=10)
    if response.status_code == 202:
        return None
    if response.status_code in (400, 405):
        return ("the registry does not allow deletion "
                "(REGISTRY_STORAGE_DELETE_ENABLED)")
    if response.status_code in (401, 403):
        return ("deletion refused: authentication is required for it "
                + ("(the credentials in [docker] were rejected)"
                   if registry_credentials() else
                   "(set registry_user and registry_password in [docker], "
                   "see DOCKER.md)"))
    return f"status {response.status_code}: {response.text.strip()[:200]}"


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


def podman_registries_conf(host=None, insecure=False):
    """
    the registries.conf.d drop-in for podman: short names go to docker.io
    like docker does (podman otherwise refuses names that are not in its
    alias list, without a tty to ask), and docker.io is mirrored if
    there is a mirror
    """
    conf = (
        'unqualified-search-registries = ["docker.io"]\n'
        'short-name-mode = "permissive"\n')
    if host:
        conf += (
            '\n'
            '[[registry]]\n'
            'prefix = "docker.io"\n'
            'location = "docker.io"\n'
            '\n'
            '[[registry.mirror]]\n'
            f'location = "{host}"\n'
            f'insecure = {"true" if insecure else "false"}\n')
    return conf


async def configure_mirror(ssh):
    """
    point the node's engine at the [docker] mirror (if any); this is done
    at load time, so that host images need not know about any registry.
    returns True if all went well (or if there was nothing to do)
    """
    settings = mirror_settings()
    version = await ssh.run("docker --version 2>&1") or ""
    is_podman = 'podman' in version.lower()
    if settings is None and not is_podman:
        return True
    url, host, insecure = settings or (None, None, False)
    say(f"{ssh.node.control_hostname()}: configuring the registry "
        f"mirror ({'podman' if is_podman else 'docker'})")
    if is_podman:
        # written even without a mirror, for the short names
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
        say(f"{ssh.node.control_hostname()}: {label} FAILED "
              f"(status={status}) {(output or '').strip()}")
    logger.info(f"{ssh.node.control_hostname()}: {label} -> {status}")
    return ok


PULL_DIAGNOSES = (
    (("short-name resolution",),
     "that is not a known short name; give a full reference, "
     "e.g. docker.io/library/NAME, quay.io/ORG/NAME or "
     "registry.example/NAME"),
    (("manifest unknown", "not found", "name unknown", "pull access denied",
      "requested access to the resource is denied", "repository does not exist",
      "denied"),
     "no such image, or not accessible; check the name and the tag"),
    (("toomanyrequests", "rate limit"),
     "the registry refuses more pulls for now (rate limit)"),
    (("no such host", "i/o timeout", "timeout", "connection refused",
      "network is unreachable", "temporary failure in name resolution"),
     "cannot reach the registry from the node"),
)


def pull_diagnosis(output):
    """a human-friendly reason for a failed pull, None if we have no idea"""
    lowered = (output or "").lower()
    for patterns, message in PULL_DIAGNOSES:
        if any(pattern in lowered for pattern in patterns):
            return message
    return None


async def _pull(ssh, image):
    """docker pull; says in plain words why it failed"""
    name = ssh.node.control_hostname()
    say(f"{name}: pulling {image}")
    begin = time.time()
    status, output = await ssh.run(
        f"docker pull {shlex.quote(image)}", with_status=True)
    logger.info(f"{name}: pull -> {status}")
    if status == 0:
        say(f"{name}: pulled {image} in {time.time() - begin:.0f}s")
        return True
    lines = (output or "").strip().splitlines()
    why = pull_diagnosis(output)
    say(f"{name}: cannot pull {image}"
          + (f": {why}" if why else f" (status={status})"))
    if lines:
        say(f"{name}: engine said: {lines[-1]}")
    return False


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
        say(f"{name}: could not look for a previous container: {previous}")
        return False
    if not previous:
        say(f"{name}: no previous container")
        return True
    say(f"{name}: removing previous container {previous}")
    return await _step(ssh, f"docker rm -fv {CONTAINER_NAME}", "remove")


async def _still_running(ssh):
    """
    'docker run -d' succeeds even if the container's command exits at once
    (e.g. a plain ubuntu image runs bash, which finds no tty and quits);
    so give it a moment and check it is still there
    """
    say(f"{ssh.node.control_hostname()}: checking that the container stays up")
    status, output = await ssh.run(
        f"sleep 2; docker inspect -f '{{{{.State.Running}}}}' {CONTAINER_NAME}"
        f" || true", with_status=True)
    if status == 0 and (output or '').strip().endswith('true'):
        return True
    _, logs = await ssh.run(
        f"docker inspect -f 'exit code {{{{.State.ExitCode}}}}, "
        f"command: {{{{.Path}}}} {{{{.Args}}}}' {CONTAINER_NAME}; "
        f"docker logs --tail 5 {CONTAINER_NAME} 2>&1", with_status=True)
    say(f"{ssh.node.control_hostname()}: container exited right after "
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
        say(f"{name}: [docker] dropbear = {dropbear} not found; "
              f"see r2lab-imaging/docker/dropbear")
        return False
    say(f"{name}: uploading the injected sshd")
    if not await _step(ssh, f"mkdir -p {HOST_INJECT_DIR}", "inject dir"):
        return False
    if not await ssh.put(dropbear, f"{HOST_INJECT_DIR}/dropbear"):
        say(f"{name}: could not upload {dropbear}")
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
    name = ssh.node.control_hostname()
    say(f"{name}: looking for an sshd in the container")
    for _ in range(grace):
        if await _port22_listening(ssh):
            say(f"{name}: sshd found in the container")
            return True
        await asyncio.sleep(1)
    if not injected:
        # not an error as such: the image may not offer ssh at all
        say(f"{ssh.node.control_hostname()}: nothing listens on port 22 "
              f"in the container (no sshd in the image, and "
              f"[docker] dropbear is not set)")
        return True
    # no access control on the testbed (access is controlled upstream, at
    # the gateway): root gets an empty password, and dropbear accepts it
    d = CONTAINER_INJECT_DIR
    say(f"{name}: none found, injecting a static sshd")
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
    say(f"{ssh.node.control_hostname()}: injected sshd does not listen "
          f"on port 22")
    return False


async def deploy(node, image, **kwargs):
    """deploy_container, but any exception is shown, not swallowed"""
    try:
        return await _deploy(node, image, **kwargs)
    except Exception as exc:                            # pylint: disable=w0703
        import traceback
        logger.error(f"{node.control_hostname()}: deploy failed\n"
                     f"{traceback.format_exc()}")
        say(f"{node.control_hostname()}: exception "
              f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return False


async def _deploy(node, image, *, host_port, backoff, wait_container):
    """
    on a node that has booted its docker host image:
    pull the container image and run it; returns True if all went well
    """
    name = node.control_hostname()
    say(f"{name}: waiting for the host to boot (ssh on port {host_port})")
    if not await SshProxy(node, port=host_port).wait_for(backoff):
        return False
    say(f"{name}: host ssh is up")
    async with SshProxy(node, port=host_port) as ssh:
        if not await ssh.connect(timeout=10.):
            say(f"{name}: host ssh on port {host_port} KO")
            return False
        # the previous container goes first: configuring the engine may
        # restart it, and the pull must happen once the mirror is set up
        if not await remove_previous(ssh):
            return False
        if not await configure_mirror(ssh):
            return False
        if not await _pull(ssh, image):
            return False
        injected = await prepare_injection(ssh)
        if injected is False:
            return False
        say(f"{name}: starting the container")
        if not await _step(ssh, run_command(image, bool(injected)), "run"):
            return False
        if not await _still_running(ssh):
            return False
        if not await ensure_sshd(ssh, bool(injected)):
            return False
    if wait_container:
        # container's sshd, on the standard port
        say(f"{name}: waiting for the container's ssh on port 22")
        if not await SshProxy(node).wait_for(backoff):
            return False
    say(f"{name}: container running {image}")
    return True


def push_command(image):
    """
    the push command; with credentials configured, it logs in first, in a
    throw-away config directory removed right after, so that nothing
    stays on the node (DOCKER_CONFIG for docker, REGISTRY_AUTH_FILE for podman)
    """
    push = f"docker push {shlex.quote(image)}"
    credentials = registry_credentials()
    if not credentials:
        return push
    user, password = credentials
    registry = shlex.quote(Config().value('docker', 'registry'))
    return (
        'd=$(mktemp -d) && export DOCKER_CONFIG=$d REGISTRY_AUTH_FILE=$d/config.json && '
        f'printf %s {shlex.quote(password)} | '
        f'docker login -u {shlex.quote(user)} --password-stdin {registry} >/dev/null && '
        f'{push}; st=$?; rm -rf "$d"; exit $st')


async def commit_and_push(node, image, *, host_port):
    """
    commit the container of a node and push the resulting image
    """
    async with SshProxy(node, port=host_port) as ssh:
        if not await ssh.connect(timeout=10.):
            say(f"{node.control_hostname()}: host ssh on port {host_port} KO")
            return False
        name = node.control_hostname()
        for label, doing, done, command in (
                ("commit", f"committing the container as {image}",
                 "committed",
                 f"docker commit {CONTAINER_NAME} {shlex.quote(image)}"),
                ("push", f"pushing {image}", "pushed",
                 push_command(image))):
            say(f"{name}: {doing}")
            begin = time.time()
            if not await _step(ssh, command, label):
                return False
            say(f"{name}: {done} in {time.time() - begin:.0f}s")
    say(f"{name}: saved {image}")
    return True
