#!/usr/bin/env python3
"""
Smoke test for `rhubarbe docker load`, to be run by hand on a real testbed
(typically the preplab) by someone who holds a lease.

It drives the rhubarbe command line and ssh only, through subprocess, so that
what gets tested is the real interface; only the standard library is needed.

The sequence, for the selected nodes:
  1. rhubarbe off
  2. rhubarbe docker load --wait -i IMAGE          (host image is burned)
  3. rhubarbe docker load -k --wait -i IMAGE       (same image, host kept)
  4. rhubarbe docker load -k --wait -i IMAGE2      (another image)
and after each of the loads, all the checks in CHECKS are run on each node.

Then the registry round trips, on the first node:
  5. rhubarbe docker save, then check the image is listed
  6. rhubarbe docker rmi, then check it is gone (needs to run as root)
  7. an image is created by hand in the registry, outside our prefix (needs
     admin credentials, taken from the [docker] config), rhubarbe docker rmi must
     refuse to delete it, and the test then deletes it itself
The registry has no API to garbage-collect: with --gc-command, a command of
yours is run after the rmi (e.g. an ssh to the registry host).

To add a check: write a function (target, spec) -> Result and decorate it
with @check("name"). To support another image: add an ImageSpec in IMAGES.

Takes about 10 minutes.
"""

import base64
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

HOST_SSH_PORT = 2222
OFF_TIMEOUT = 120
LOAD_TIMEOUT = 900
SSH_TIMEOUT = 30
PORT_DEADLINE = 30

VERBOSE = False


####################
# what we know about images

@dataclass
class ImageSpec:
    """what is expected to be seen inside a container of that image"""
    ref: str
    # the ID= line of /etc/os-release; None if we don't know (or care)
    os_id: str | None = None


IMAGES = {spec.ref: spec for spec in (
    ImageSpec("alpine:latest", "alpine"),
    ImageSpec("archlinux:latest", "arch"),
    ImageSpec("ubuntu:latest", "ubuntu"),
    ImageSpec("fedora:latest", "fedora"),
    ImageSpec("debian:latest", "debian"),
)}


def image_spec(ref):
    return IMAGES.get(ref) or ImageSpec(ref)


####################
# results

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Result:
    status: str
    detail: str = ""


def ok(detail=""):
    return Result(PASS, detail)


def ko(detail=""):
    return Result(FAIL, detail)


def tail(text, lines=4):
    return " | ".join(text.strip().splitlines()[-lines:])


####################
# talking to the outside world

def run(command, timeout, *, stream=False):
    """
    run a command, return (returncode, output); never raises
    with stream=True, the output is also shown as it comes
    """
    if VERBOSE:
        print(f"    $ {' '.join(command)}", flush=True)
    begin = time.time()
    try:
        with subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace") as proc:
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
            lines = []
            try:
                for line in proc.stdout:
                    lines.append(line)
                    if stream:
                        print(f"    | {line.rstrip()}", flush=True)
                proc.wait()
            finally:
                timer.cancel()
            code, output = proc.returncode, "".join(lines)
            if code == -9 and time.time() - begin >= timeout:
                code, output = 124, output + f"\ntimeout after {timeout}s"
    except OSError as exc:
        code, output = 127, str(exc)
    if VERBOSE:
        print(f"      -> {code} in {time.time() - begin:.1f}s", flush=True)
    return code, output


@dataclass
class Target:
    """one node under test"""
    name: str

    def ssh(self, command, *, port=22, timeout=SSH_TIMEOUT):
        """run a command in the container (port 22) or on the host (2222)"""
        return run(
            ["ssh", "-p", str(port),
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             f"root@{self.name}", command],
            timeout)

    def port_open(self, port, deadline=PORT_DEADLINE):
        end = time.time() + deadline
        while True:
            try:
                with socket.create_connection((self.name, port), timeout=3):
                    return True
            except OSError:
                if time.time() > end:
                    return False
                time.sleep(1)


####################
# the checks

CHECKS: list[tuple] = []


def check(name):
    def register(function: Callable[[Target, ImageSpec], Result]):
        CHECKS.append((name, function))
        return function
    return register


@check("host-ssh-2222")
def _host_port(target, spec):                       # pylint: disable=w0613
    if target.port_open(HOST_SSH_PORT):
        return ok()
    return ko(f"nothing on port {HOST_SSH_PORT}")


@check("container-ssh-22")
def _container_port(target, spec):                  # pylint: disable=w0613
    if target.port_open(22):
        return ok()
    return ko("nothing on port 22")


@check("container-image")
def _container_image(target, spec):
    code, out = target.ssh("docker inspect -f '{{.Config.Image}}' container",
                           port=HOST_SSH_PORT)
    if code != 0:
        return ko(tail(out))
    found = out.strip()
    # podman reports fully qualified names: docker.io/library/alpine:latest
    plain = re.sub(r'^(docker\.io/)?(library/)?', '', found)
    return ok(found) if plain == spec.ref else ko(
        f"container runs {found!r}, expected {spec.ref!r}")


@check("os-release")
def _os_release(target, spec):
    code, out = target.ssh("cat /etc/os-release")
    if code != 0:
        return ko(tail(out))
    match = re.search(r'^ID="?([^"\n]+)"?$', out, re.M)
    found = match.group(1) if match else None
    if spec.os_id is None:
        return ok(f"ID={found} (not checked)")
    return ok(f"ID={found}") if found == spec.os_id else ko(
        f"ID={found}, expected {spec.os_id}")


@check("modprobe")
def _modprobe(target, spec):                        # pylint: disable=w0613
    code, out = target.ssh(
        "modprobe dummy && lsmod | grep -q '^dummy'; rc=$?; "
        "rmmod dummy 2>/dev/null; exit $rc")
    return ok() if code == 0 else ko(tail(out) or f"status {code}")


def _interfaces(target):
    code, out = target.ssh("ls /sys/class/net")
    return code, out


def _wanted_interfaces(out, wanted=("control", "data")):
    names = set(out.split())
    missing = [name for name in wanted if name not in names]
    return ko(f"missing {missing} in {sorted(names)}") if missing else ok()


@check("sys-class-net")
def _sys_class_net(target, spec):                   # pylint: disable=w0613
    code, out = _interfaces(target)
    return ko(tail(out)) if code != 0 else _wanted_interfaces(out)


@check("ip-link")
def _ip_link(target, spec):                         # pylint: disable=w0613
    code, out = target.ssh("ip link show")
    if code != 0:
        return ko(tail(out))
    names = set(re.findall(r'^\d+: ([^:@\s]+)', out, re.M))
    return _wanted_interfaces(" ".join(names))


####################
# the sequence

@dataclass
class Phase:
    title: str
    command: list[str] | None = None
    timeout: int = 0
    # for the loads, the image expected after the command; None: no checks
    spec: ImageSpec | None = None
    # instead of a command: function(args, targets) -> [(check name, Result)]
    function: Callable | None = None


SAVE_NAME = "smoke-test"
FOREIGN_NAME = "smoke-foreign"
EMPTY_CONFIG = (b'{"architecture":"amd64","os":"linux","config":{},'
                b'"rootfs":{"type":"layers","diff_ids":[]}}')
MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"


def http(method, url, *, data=None, headers=None, auth=None):
    """returns (status, headers, body); never raises on http errors"""
    headers = dict(headers or {})
    if auth:
        token = base64.b64encode(":".join(auth).encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()
    except OSError as exc:
        return 0, {}, str(exc).encode()


def registry_url(args):
    """https://registry/v2, as printed by rhubarbe docker url"""
    code, out = run([args.rhubarbe, "docker", "url"], 30)
    return out.strip() if code == 0 else None


def listed(args, name):
    """is that name in rhubarbe docker images -a ?"""
    code, out = run([args.rhubarbe, "docker", "images", "-a", name], 60)
    return code == 0 and f"/{name}:" in out, out


def phase_save_rmi(args, targets):
    node = targets[0].name
    results = []
    code, out = run([args.rhubarbe, "docker", "save", node, "-o", SAVE_NAME],
                    LOAD_TIMEOUT, stream=True)
    if code != 0:
        return [("save", ko(f"status {code}: {tail(out)}"))]
    results.append(("save", ok()))
    found, out = listed(args, SAVE_NAME)
    results.append(("saved-image-listed",
                    ok() if found else ko(f"not in: {tail(out)}")))
    if os.geteuid() != 0:
        return results + [("rmi", Result(SKIP, "rmi is reserved to root"))]
    code, out = run([args.rhubarbe, "docker", "rmi", "-y", SAVE_NAME], 60)
    results.append(("rmi", ok() if code == 0
                    else ko(f"status {code}: {tail(out)}")))
    found, out = listed(args, SAVE_NAME)
    results.append(("rmi-image-gone",
                    ko("still listed") if found else ok()))
    if args.gc_command:
        code, out = run(shlex.split(args.gc_command), 600)
        results.append(("registry-gc", ok() if code == 0
                        else ko(f"status {code}: {tail(out)}")))
    return results


def create_foreign_image(url, auth):
    """
    push by hand a minimal image (a config blob, no layer) under a name that
    is outside our prefix; returns (manifest digest, None) or (None, error)
    """
    name = FOREIGN_NAME
    status, headers, body = http("POST", f"{url}/{name}/blobs/uploads/",
                                 auth=auth)
    if status != 202:
        return None, f"upload start: {status} {body[:100]!r}"
    digest = "sha256:" + hashlib.sha256(EMPTY_CONFIG).hexdigest()
    location = urllib.parse.urljoin(url, headers["Location"])
    location += ("&" if "?" in location else "?") + f"digest={digest}"
    status, _, body = http(
        "PUT", location, data=EMPTY_CONFIG, auth=auth,
        headers={"Content-Type": "application/octet-stream"})
    if status != 201:
        return None, f"blob upload: {status} {body[:100]!r}"
    manifest = json.dumps({
        "schemaVersion": 2, "mediaType": MANIFEST_V2,
        "config": {"mediaType":
                   "application/vnd.docker.container.image.v1+json",
                   "size": len(EMPTY_CONFIG), "digest": digest},
        "layers": []}).encode()
    status, headers, body = http(
        "PUT", f"{url}/{name}/manifests/latest", data=manifest, auth=auth,
        headers={"Content-Type": MANIFEST_V2})
    if status != 201:
        return None, f"manifest upload: {status} {body[:100]!r}"
    return headers["Docker-Content-Digest"], None


def configured_credentials():
    """
    (user, password) from [docker] registry_user / registry_password, as
    rhubarbe sees them (the config layers, i.e. root's ~/.rhubarbe.conf);
    this is the one place where the test imports rhubarbe, from the same
    tree as this script. None if not configured.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from rhubarbe.dockercmd import registry_credentials
        return registry_credentials()
    except Exception:                                   # pylint: disable=w0703
        return None


def phase_foreign_rmi(args, targets):               # pylint: disable=w0613
    explicit = (args.registry_user, args.registry_password)
    credentials = explicit if all(explicit) else configured_credentials()
    if not credentials:
        return [("foreign-image", Result(
            SKIP, "no admin credentials: neither registry_user and "
            "registry_password in the [docker] config, nor "
            "--registry-user and REGISTRY_PASSWORD"))]
    if os.geteuid() != 0:
        return [("foreign-image", Result(SKIP, "rmi is reserved to root"))]
    url = registry_url(args)
    if not url:
        return [("foreign-image", ko("cannot get the registry url"))]
    auth = credentials
    digest, error = create_foreign_image(url, auth)
    if error:
        return [("foreign-image-created", ko(error))]
    results = [("foreign-image-created", ok())]
    try:
        host = urllib.parse.urlparse(url).netloc
        code, out = run([args.rhubarbe, "docker", "rmi", "-y",
                         f"{host}/{FOREIGN_NAME}:latest"], 60)
        results.append(("rmi-refuses-foreign", ko("rmi succeeded")
                        if code == 0 else ok(tail(out, 1))))
        status, _, _ = http("HEAD", f"{url}/{FOREIGN_NAME}/manifests/latest",
                            headers={"Accept": MANIFEST_V2})
        results.append(("foreign-image-survives",
                        ok() if status == 200 else ko(f"HEAD: {status}")))
    finally:
        status, _, body = http(
            "DELETE", f"{url}/{FOREIGN_NAME}/manifests/{digest}", auth=auth)
        results.append(("foreign-image-cleanup", ok() if status == 202
                        else ko(f"left over, DELETE: {status} "
                                f"{body[:100]!r}")))
    return results


def build_phases(args):
    rb = args.rhubarbe
    nodes = args.nodes
    host_image = [] if args.engine == "docker" \
        else ["--host-image", args.engine]
    registry = [] if args.no_registry else [
        Phase("save, then rmi", function=phase_save_rmi),
        Phase("rmi refuses an image outside our prefix",
              function=phase_foreign_rmi)]
    if args.skip_loads:
        return registry
    return [
        Phase("off", [rb, "off", *nodes], OFF_TIMEOUT),
        Phase(f"load {args.image} ('{args.engine}' host image)",
              [rb, "docker", "load", "--wait", *host_image,
               "-i", args.image, *nodes],
              LOAD_TIMEOUT, image_spec(args.image)),
        Phase(f"load -k {args.image} (same image)",
              [rb, "docker", "load", "-k", "--wait",
               "-i", args.image, *nodes],
              LOAD_TIMEOUT, image_spec(args.image)),
        Phase(f"load -k {args.image2} (other image)",
              [rb, "docker", "load", "-k", "--wait",
               "-i", args.image2, *nodes],
              LOAD_TIMEOUT, image_spec(args.image2)),
    ] + registry


def resolve_nodes(args):
    code, out = run([args.rhubarbe, "nodes", *args.nodes], 30)
    names = out.split()
    if code != 0 or not names:
        sys.exit(f"cannot resolve nodes {args.nodes}: {out.strip()}")
    return names


def diagnostics(args):
    """what to look at when a load failed"""
    print("  --- diagnostics", flush=True)
    _, out = run([args.rhubarbe, "status", *args.nodes], 30)
    print(f"  rhubarbe status: {tail(out, 6)}")
    log = Path.home() / "rhubarbe.log"
    try:
        lines = log.read_text(errors="replace").splitlines()[-15:]
        print(f"  last lines of {log}:")
        for line in lines:
            print(f"    {line}")
    except OSError as exc:
        print(f"  cannot read {log}: {exc}")
    print("  --- end of diagnostics", flush=True)


def main():
    global VERBOSE                                  # pylint: disable=w0603
    parser = ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("nodes", nargs="+",
                        help="nodes as understood by rhubarbe, e.g. 11 or 1-3")
    parser.add_argument("--engine", choices=("docker", "podman"),
                        default="docker",
                        help="host image to burn (default: %(default)s)")
    parser.add_argument("--image", default="alpine:latest",
                        help="container image, loaded twice "
                        "(default: %(default)s)")
    parser.add_argument("--image2", default="archlinux:latest",
                        help="another container image, to be loaded with -k; "
                        "needs ip and modprobe (default: %(default)s)")
    parser.add_argument("--rhubarbe", default="rhubarbe",
                        help="the rhubarbe command (default: %(default)s)")
    parser.add_argument("--settle", type=int, default=10,
                        help="seconds to wait after turning the nodes off, "
                        "before loading (default: %(default)s)")
    parser.add_argument("--skip-loads", action="store_true",
                        help="only run the registry phases, on a node that "
                        "already runs a container")
    parser.add_argument("--no-registry", action="store_true",
                        help="skip the registry phases (save, rmi)")
    parser.add_argument("--registry-user",
                        default=os.environ.get("REGISTRY_USER"),
                        help="admin login, to create by hand the image "
                        "outside our prefix (default: [docker] "
                        "registry_user; env: REGISTRY_USER)")
    parser.add_argument("--registry-password",
                        default=os.environ.get("REGISTRY_PASSWORD"),
                        help="its password (env: REGISTRY_PASSWORD, "
                        "preferred, as command lines show in ps)")
    parser.add_argument("--gc-command",
                        help="command that garbage-collects the registry, "
                        "run after rmi; there is no API for that")
    parser.add_argument("--fail-fast", action="store_true",
                        help="stop at the first failure")
    parser.add_argument("--dry-run", action="store_true",
                        help="only show the commands and checks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    phases = build_phases(args)
    if args.dry_run:
        for phase in phases:
            print(f"{phase.title}\n    $ "
                  f"{' '.join(phase.command or ['(python)'])}")
        print("checks after each load:", ", ".join(n for n, _ in CHECKS))
        return 0

    targets = [Target(name) for name in resolve_nodes(args)]
    records = []                    # (phase title, node, check, Result)
    stop = False

    def record(phase, node, name, result):
        records.append((phase.title, node, name, result))
        detail = f"  ({result.detail})" if result.detail else ""
        print(f"  {result.status}  {node}  {name}{detail}", flush=True)
        return result.status == FAIL

    for phase in phases:
        if stop:
            break
        print(f"\n=== {phase.title}", flush=True)
        if phase.function:
            for name, result in phase.function(args, targets):
                if record(phase, targets[0].name, name, result) \
                        and args.fail_fast:
                    stop = True
            continue
        code, out = run(phase.command, phase.timeout, stream=True)
        step_failed = code != 0
        if record(phase, "-", "command",
                  ko(f"status {code}: {tail(out)}") if step_failed else ok()):
            stop = args.fail_fast
        if phase.spec is None:
            if not step_failed and args.settle:
                print(f"  (waiting {args.settle}s for the nodes to settle)",
                      flush=True)
                time.sleep(args.settle)
            continue
        if step_failed:
            diagnostics(args)
        for target in targets:
            for name, function in CHECKS:
                if step_failed:
                    result = Result(SKIP, "the load failed")
                else:
                    result = function(target, phase.spec)
                if record(phase, target.name, name, result) \
                        and args.fail_fast:
                    stop = True
                    break
            if stop:
                break

    counts = {status: sum(1 for r in records if r[3].status == status)
              for status in (PASS, FAIL, SKIP)}
    print(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, "
          f"{counts[SKIP]} skipped")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
