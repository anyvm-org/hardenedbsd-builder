How the images are built:

Each image is built automatically in the
[anyvm-org/hardenedbsd-builder](https://github.com/anyvm-org/hardenedbsd-builder)
repo's GitHub Actions: it downloads the official HardenedBSD installer
ISO, remixes it for a serial console, boots it in QEMU, answers the
installer unattended, enables ssh, pre-installs the packages listed in
the conf, and exports the installed disk as a compressed qcow2 image.

Upstream install media: the official HardenedBSD installers from
https://installers.hardenedbsd.org/pub/.
