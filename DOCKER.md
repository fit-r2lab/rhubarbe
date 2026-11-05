# `rhubarbe docker` - what the setup must provide

```
rhubarbe docker load [--host-image NAME | -k] [-i CONTAINER_REF] [--wait] NODES...
rhubarbe docker save NODE -o CONTAINER_REF
```

- `load` burns the host image (frisbee), then pulls and runs the container;
  with `-k/--keep-host` it skips the burn, for nodes that already run a
  docker-capable image, and just replaces the container (the previous one,
  if any, is reported and removed)
- `save` commits the container of a node and pushes the result

`rhubarbe docker load` / `save` assume the pieces below. If one is missing,
the command fails at the `pull`, `run` or `push` step.

## 1. The host image (frisbee)

A frisbee image (default `docker`, or `podman`; `[docker] host_image`) with
docker, and its own sshd on **port 2222**, leaving port 22 to the container.
It must not know about any registry (see section 3).
What an image must contain is documented with the imaging material, in
`r2lab-imaging/docker/host-images.md`.

## 2. The container image

Any image from Docker Hub, quay.io or the private registry should do:

- it is started with `-dit`, so images whose command is a shell (plain
  `ubuntu`) stay alive; images that exit by themselves are reported as
  failures, with their exit code and last logs
- the container shares the host network (`--network=host`), so `ssh fitNN`
  on port 22 must land in it. Two cases:
  - the image runs its own sshd (e.g. systemd-based system images): nothing
    to do; rhubarbe waits up to `[docker] sshd_grace` seconds for port 22
  - otherwise, if `[docker] dropbear` is set, rhubarbe **injects a static
    dropbear**: the binary is uploaded to the node
    (`/opt/rhubarbe/inject`), mounted read-only in the container as
    `/.rhubarbe`, and started with `docker exec -u 0`. The binary is not
    written in the image, but root's password is emptied in the
    container's `/etc/shadow` and dropbear runs with `-B`: **no
    authentication**, like on the nodes themselves, access control being done
    upstream by the gateway. Build the binary with
    `r2lab-imaging/docker/dropbear/build-dropbear.sh`
    (same architecture as the nodes). Requirements on the image: a root
    user, `/etc/shadow`, `sed` and a shell; not for distroless images
- the container is always called `container`; the monitor and `docker save`
  rely on that name

## 3. Registries

Two roles, which one registry process cannot both play:

| role | used by | writable | where |
|------|---------|----------|-------|
| **private registry** | `docker save` pushes there; users' images live there | yes | `[docker] registry`, `registry.pl.sophia.inria.fr` (Caddy + registry:2, valid TLS, no auth; `dockerhub.pl.sophia.inria.fr` is an older alias, to be phased out) |
| **pull-through cache** of Docker Hub | makes `-i ubuntu` fast, like on a laptop | no, by design | `[docker] mirror`, optional; e.g. `http://registry.theblueprintfactory.org` (maintained by colleagues) |

Observed on 2026-11-04: the private registry holds only `python-nodejs` and
`python-nodejs-rust`; it does not proxy Docker Hub.

### Who configures what

**Host images know nothing about registries.** At `docker load` time, once
the host has booted, rhubarbe configures the node's engine:

- docker: merges `registry-mirrors` (and `insecure-registries` for an
  `http://` mirror) into `/etc/docker/daemon.json`, preserving other keys,
  then `systemctl restart docker`
- podman (detected from `docker --version`): writes
  `/etc/containers/registries.conf.d/rhubarbe.conf`, mirroring `docker.io`

If the mirror moves, change `[docker] mirror` - no image to rebuild. No
`mirror` key means no configuration and docker's defaults.

### Image names

- `docker load -i REF`: **REF is passed untouched** to `docker pull`.
  So `ubuntu`, `jupyter/base-notebook` go to Docker Hub (through the mirror),
  `quay.io/org/img` goes to quay.io (not cached), and one's own images are
  named in full: `registry.example/foo`
- `docker save -o REF`: the only place where the private registry is
  hard-wired; REF is prefixed with `[docker] registry` unless its first
  component looks like a registry (contains `.` or `:`, or is `localhost`)

### Private registry caveats

- valid TLS, so no `insecure-registries` needed
- no authentication: anyone on the net can push or delete
- no garbage collection: `docker save` commits whole containers

## 4. Checklist for testing

Validated on fit11 (2026-11-05): `load -i ubuntu:latest` then
`load -k -i fedora:latest`, each followed by `ssh fit11` landing in the
container (sshd injected).

Still to check:

1. the container controls the hardware: `ssh fit11 'uname -r; ls /lib/modules;
   modprobe dummy && lsmod | grep dummy; ls /sys/class/net; ip link'`
2. `rhubarbe docker save 11 -o test-save`, then
   `curl https://registry.pl.sophia.inria.fr/v2/_catalog`, and
   `rhubarbe docker load -k -i registry.pl.sophia.inria.fr/test-save 11`
3. the same with `--host-image podman` (fedora host, docker CLI through
   podman-docker; mirror configuration goes through `registries.conf.d`)
4. an image that runs its own sshd, e.g. `jrei/systemd-ubuntu:24.04`
   (nothing should be injected)
5. what the node got from the mirror setting:
   `ssh -p 2222 root@fit11 cat /etc/docker/daemon.json`
