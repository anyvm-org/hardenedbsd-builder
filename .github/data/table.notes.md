
amd64 only: HardenedBSD builds no other architecture, and it publishes
installer ISOs rather than VM images, so this builder takes the ISO path.

Release `15` is HardenedBSD's 15-STABLE branch and `14` is 14-STABLE. Each
one follows that branch's rolling `LATEST` installer -- upstream publishes
`build-N` directories and moves `LATEST` to the newest -- so an image is a
snapshot of the branch at build time, not a fixed upstream version, the same
way nextbsd-builder tracks a `continuous` tag.

The installer ISO is VGA-only upstream (a stock boot produces zero bytes on
COM1), so `hooks/host_beforeBuild.py` remixes it with a serial console before
the build starts. That hook documents the three traps involved -- xorriso's
`replay` silently dropping the El Torito record, the ISO volume label being
load-bearing for the root mount, and `-osirrox` extraction losing root
ownership -- each of which produces a broken image rather than an error.
