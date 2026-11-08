# `rhubarbe docker` - what the setup must provide

```
rhubarbe docker load [--host-image NAME | -k] [-i CONTAINER_REF] [--wait] NODES...
rhubarbe docker save NODE -o CONTAINER_REF
rhubarbe docker images [-a | -m] [PATTERN...]
rhubarbe docker rmi [-y] IMAGE...
rhubarbe docker url [PATH]
```

- `load` burns the host image (frisbee), then pulls and runs the container;
  with `-k/--keep-host` it skips the burn, for nodes that already run a
  docker-capable image, and just replaces the container (the previous one,
  if any, is reported and removed)
- `save` commits the container of a node and pushes the result
- `images` lists what our namespace in the private registry holds (`-a`
  for the whole registry), as `registry/name:tag` references that can be
  pasted in `-i`; the patterns are substrings of the image name. With
  `-m/--mirror`, lists what the pull-through cache holds instead (what is
  already cached, so quick to pull), as `ubuntu:latest`
- `rmi` deletes images from our namespace; reserved to root (see below)
- `url` prints the URL of the registry API, to craft requests by hand:
  `curl $(rhubarbe docker url)/_catalog`

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
| **private registry** | `docker save` pushes there; users' images live there | yes | `[docker] registry`, `registry.pl.sophia.inria.fr` (Caddy in front of registry:3, valid TLS, open reads, writes open from the testbed gateways only, under our prefix; `dockerhub.pl.sophia.inria.fr` is an older alias, to be phased out) |
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
  if there is a mirror. This is done even without a mirror: it also makes
  short names (`-i mint`) search docker.io like docker does, whereas podman
  alone refuses the ones that are not in its alias list

If the mirror moves, change `[docker] mirror` - no image to rebuild. No
`mirror` key means no configuration and docker's defaults.

### Image names

- `docker load -i REF`: before anything is done to the nodes, the image is
  looked for (a HEAD on its manifest, with the anonymous token that Docker
  Hub, quay.io... give); if it is missing the command stops at once,
  and if we cannot tell (no network from here...) it goes on, the pull on the
  node being the authority. Then REF is resolved:
  - a reference that names a registry is passed untouched:
    `quay.io/org/img` goes to quay.io (not cached), `registry.example/foo`
    to that registry
  - `r2lab/foo` (our prefix) goes to our registry
  - otherwise, our namespace is tried first, so that what `save -o foo`
    pushed loads with `-i foo`, and the resolution is printed; if it is
    not there, REF goes to Docker Hub (through the mirror) as before:
    `ubuntu`, `jupyter/base-notebook`. When a saved image hides a Docker Hub
    one, a warning says so; `-i docker.io/library/NAME` forces the Hub
- `docker save -o REF` asks for confirmation if the image already exists
  (`-y` to skip), then pushes to our namespace in the private registry:
  `save -o my-arch` pushes `registry/r2lab/my-arch`, where `registry` and
  `r2lab` (the prefix) are `[docker] registry` and `[docker] prefix`. They
  are added unless already there; a reference with another registry is left
  alone (and will most likely be refused, see below)
- `docker rmi REF`: deletes from the same namespace, with the same rules
  for completing REF (`:latest` too). It asks for confirmation unless `-y`.
  Deleting a tag deletes all the tags that point to the same image, and
  the disk space is only reclaimed by the registry's garbage collection
  (`registry garbage-collect` on the server). Deletion has to be enabled in
  the registry (`REGISTRY_STORAGE_DELETE_ENABLED=true`)

The registry has no notion of an image owner: a prefix is just a path. It is
a shared space, like the one for frisbee images. The `rmi` restriction to
root is a guard against honest mistakes, not against anybody who would
send the request by hand: this is a "we're all gentlemen here" setup.

### Private registry: how it must be set up

`rhubarbe docker save` and `rmi` use **no credentials**: nodes are not a
place to keep a secret, since every experimenter is root on them, and
`rhubarbe.conf.local` is readable by everybody as well. So the registry must
accept writes from the testbed, and only from there, and only in our
namespace:

- TLS with a valid certificate, so that no `insecure-registries` is needed
- reads open to everybody
- writes (POST, PUT, PATCH, DELETE) accepted without authentication from
  the addresses of the testbed gateways (faraday, and the preplab for
  tests), which is what nodes appear as when they reach the registry, but
  only under `/v2/r2lab/` (the prefix)
- all other writes require basic-auth credentials. That is how the GitHub
  worker (`boxtops`) pushes the images it builds in other namespaces
  (`python-nodejs`...), with a `docker login` done once on that box. The
  images of that other project are thus out of reach of the nodes

The current setup is a Caddy server in front of a `registry:3` container
(`dockerhub:6000`). Each `handle` is tried in turn and the first match
wins, so each one proxies by itself:

```
registry.pl.sophia.inria.fr, dockerhub.pl.sophia.inria.fr {
    # from the testbed gateways, our namespace is open
    @testbed_writes {
        method POST PUT PATCH DELETE
        remote_ip <FARADAY_IP> <PREPLAB_IP>
        path /v2/r2lab/*
    }
    # any other write needs credentials
    @other_writes {
        method POST PUT PATCH DELETE
    }
    handle @testbed_writes {
        reverse_proxy dockerhub:6000
    }
    handle @other_writes {
        basic_auth /v2/* {
            # bcrypt hash, as produced by `caddy hash-password`
            admin "<HASH>"
        }
        reverse_proxy dockerhub:6000
    }
    # reads
    handle {
        reverse_proxy dockerhub:6000
    }
}
```

Not tested against a real Caddy at the time of writing: validate it
(`caddy validate`) and try the checks below before relying on it.

Things to know:

- use `remote_ip` (the TCP peer), not `client_ip`, which follows
  `X-Forwarded-For` and could be spoofed if `trusted_proxies` is misconfigured
- if Caddy runs in docker, make sure it sees the real client addresses and
  not the bridge's. If it does not, nobody matches and nodes are refused
  (safe). Never "fix" that by listing private ranges like `172.16.0.0/12`,
  as that would open writes to everything that reaches the server
- list the IPv6 addresses of the gateways too if they reach the registry
  over IPv6
- check a change with `caddy validate --config /etc/caddy/Caddyfile`, and
  apply it with `caddy reload`, which keeps the old configuration if the new
  one is invalid
- to check it works, `curl -i -X POST
  https://registry.pl.sophia.inria.fr/v2/r2lab/probe/blobs/uploads/` must
  answer 401 from a laptop and 202 from a node (cancel the upload session
  it opens with a DELETE on the `Location` it returns), and the same
  request on `/v2/python-nodejs/blobs/uploads/` must answer 401 from the
  node too

Optional credentials: `rmi` and `save` send none by default. When they run
from somewhere the allowlist does not cover, set `registry_user` and
`registry_password` in `[docker]`, **in root's `~/.rhubarbe.conf` (mode 600)
only**, never in `rhubarbe.conf.local`, which every experimenter can read.
`rmi` then sends basic-auth with its DELETE. `save` logs in on the node with
a throw-away config directory, removed right after the push; the password
does travel to the node during the push, which every experimenter can reach
as root, so this is for occasional use by the admin, not a setup to keep.

What this accepts: anyone holding a lease can push to, overwrite and
delete (if deletion is enabled in the registry) anything under `r2lab/`,
whoever created it. The other namespaces are protected.

Other caveats:

- no garbage collection: `docker save` commits whole containers

## 4. Testing

`tests/docker_smoke.py NODES [--engine docker|podman]` runs the whole
sequence on real nodes (off, `load`, `load -k` with the same image, `load -k`
with another one) and checks ports 2222 and 22, the image, `/etc/os-release`,
`modprobe` and the `control` and `data` interfaces after each load. It needs
a lease and takes about 10 minutes. It has passed on fit11 with both the
`docker` and the `podman` host images, and `rhubarbe docker save` has been
validated by hand.

After the loads, it also runs `docker save` and `docker rmi` of a fixed name
(`smoke-test`, always the same to limit the disk usage), and checks the image
shows up in `docker images` then disappears. With admin credentials
(`registry_user` and `registry_password` of the `[docker]` config, or else
`REGISTRY_USER` and `REGISTRY_PASSWORD`) it also pushes by hand an image named
`smoke-foreign`, outside our prefix, checks that `rmi` refuses it, and deletes
it itself. As root only, since `rmi` is. The registry API has no garbage
collection: `--gc-command 'ssh host docker exec ... registry garbage-collect
...'` runs yours after the `rmi`. `--skip-loads` runs only these registry
phases, in about a minute, on a node that already runs a container.

Not covered yet:

1. `docker load -k` of the saved image (today by hand:
   `rhubarbe docker load -k -i registry.pl.sophia.inria.fr/r2lab/test-save 11`)
2. an image that runs its own sshd, e.g. `jrei/systemd-ubuntu:24.04`
   (nothing should be injected)
3. what the node got from the mirror setting:
   `ssh -p 2222 root@fit11 cat /etc/docker/daemon.json`

## appendix: docker names for the main linux distros

| tag | comment |
|-|-|
| `ubuntu:26.04` | |
| `fedora:45` | or `:latest` |
| `debian:trixie` | |
| `rockylinux:9` | |
| `almalinux:9` | |
| `opensuse/leap:15.6` | |
| `opensuse/tumbleweed` | |
| `archlinux:latest` | |
| `alpine:3.24` | or `:latest` |
| `quay.io/centos/centos:stream9` | |
| `linuxmintd/mint22-amd64` | third party|
