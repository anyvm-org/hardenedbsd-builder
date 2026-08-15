

[![Build](https://github.com/anyvm-org/hardenedbsd-builder/actions/workflows/build.yml/badge.svg)](https://github.com/anyvm-org/hardenedbsd-builder/actions/workflows/build.yml)

Latest: 0.0.0


The image builder for `hardenedbsd`


All the supported releases are here:



| Release | x86_64 |
|---------|---------|
| 15 | ✅ (rsync,scp,sshfs,nfs,tar) |

<!-- shelved: 14 -->

amd64 only: HardenedBSD builds no other architecture, and it publishes
installer ISOs rather than VM images, so this builder takes the ISO path.

Release `15` is HardenedBSD's 15-STABLE branch. It follows that branch's
rolling `LATEST` installer -- upstream publishes `build-N` directories and
moves `LATEST` to the newest -- so an image is a snapshot of the branch at
build time, not a fixed upstream version, the same way nextbsd-builder tracks
a `continuous` tag.

Release 14 is deliberately absent. Upstream has moved on from it: the newest
14-STABLE installer is build-33 from 2025-12-17 and has not been rebuilt
since, and the matching package repository is gone --
`pkg.hardenedbsd.org/HardenedBSD/pkg/FreeBSD:14:amd64/` returns 404 while
`:15:` and `:16:` answer, so a 14 guest cannot even bootstrap `pkg`. Only
15-STABLE and current are maintained. `conf/hardenedbsd-14.conf` is kept on
disk with the details rather than deleted, so this does not have to be
rediscovered.

The installer ISO is VGA-only upstream (a stock boot produces zero bytes on
COM1), so `hooks/host_beforeBuild.py` remixes it with a serial console before
the build starts, and bakes the distribution sets into `/usr/freebsd-dist` on
that ISO so bsdinstall never fetches over the network. That hook documents the
three traps involved -- xorriso's `replay` silently dropping the El Torito
record, the ISO volume label being load-bearing for the root mount, and
`-osirrox` extraction losing root ownership -- each of which produces a broken
image rather than an error.




How to build:

1. Use the [manual.yml](.github/workflows/manual.yml) to build manually.
   
    Run the workflow manually, you will get a view-only webconsole from the output of the workflow, just open the link in your web browser.
   
    You will also get an interactive VNC connection port from the output, you can connect to the vm by any vnc client.

2. Run the builder locally on your Ubuntu machine.

    Just clone the repo. and run:
    ```bash
    python3 build.py conf/hardenedbsd-15.conf
    ```
   
