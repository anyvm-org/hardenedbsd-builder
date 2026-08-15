
amd64 only: HardenedBSD builds no other architecture, and it publishes
installer ISOs rather than VM images, so this builder takes the ISO path.

Each release tracks its branch's rolling `LATEST` installer rather than a
numbered version, the same way nextbsd-builder tracks a `continuous` tag.

The installer ISO is VGA-only upstream (a stock boot produces zero bytes on
COM1), so `hooks/host_beforeBuild.py` remixes it with a serial console before
the build starts. That hook documents the two traps involved -- xorriso's
`replay` silently dropping the El Torito record, and `-osirrox` extraction
losing root ownership -- both of which produce a broken image rather than an
error.
