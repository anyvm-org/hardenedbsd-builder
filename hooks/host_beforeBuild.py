# host_beforeBuild.py -- remix the upstream installer ISO so the installer
# runs on a serial console.
#
# WHY THIS EXISTS. HardenedBSD's amd64 installer ISO is VGA-only. Measured,
# not assumed: booting the stock bootonly.iso under KVM for 100 s produced
# exactly 0 bytes on COM1, while a framebuffer dump at the same moment showed
# the bsdinstall Welcome dialog sitting there. Every other console-driven
# builder here works because its guest's native console IS a serial line
# (freebsd/powerpc64 gets ttyu0 from SPAPR, sparc64 from OpenBIOS); amd64 has
# no such luck. The alternatives were OCR-driving the TUI over VNC -- fragile,
# and the omnios experience with OCR misreads is not one to repeat -- or
# giving the ISO a serial console. This does the latter.
#
# WHAT IT DOES. Extract the ISO, append console="comconsole" to
# /boot/loader.conf, rebuild. Verified locally: after the remix the boot
# reaches `Starting primary installer on ttyu0` and the whole
# Console-type -> Welcome -> Live System -> root shell sequence is drivable
# over the serial chardev, with dynamically linked binaries executing
# normally in that shell.
#
# TWO TRAPS, both of which cost a wasted boot before being found:
#
#  1. `xorriso -boot_image any replay` SILENTLY DESTROYS the boot record on
#     this ISO. The EFI El Torito entry is a "hidden" image whose size
#     xorriso cannot infer ("Found hidden El-Torito image. Its size could not
#     be figured out"), so replay drops it and the output ISO reports
#     `Boot record : (system area only)` instead of `El Torito`. It does not
#     fail -- it produces a non-bootable ISO, and the resulting empty serial
#     log looks exactly like "serial does not work". So the boot records are
#     rebuilt EXPLICITLY here, the way FreeBSD's own mkisoimages.sh does.
#
#  2. Extracting with -osirrox loses the original RockRidge ownership: every
#     file ends up owned by the build user, and HardenedBSD's hardening reacts
#     -- "/etc/login.conf is not owned by root", "ldconfig: ignoring directory
#     not owned by root", and login failing with setusercontext(). Passing
#     `-uid 0 -gid 0` at rebuild time restores root ownership and clears all
#     of it. (One "ld-elf.so.1: Tainted process refusing to run binary"
#     message still appears once during rc even after the fix; it is cosmetic
#     -- binaries in the live shell were verified to execute.)
#
# The output is written to the exact path createVM() would have downloaded
# to, and createVM() skips its download when that file exists. clearVM() runs
# between this hook and createVM() but only removes .qcow2/.img/state files,
# never the .iso -- so the remixed image survives.

import re

_osname = env("VM_OS_NAME")
_rel = env("VM_RELEASE")
_iso_out = wf("%s.iso" % _osname)

# EVERY cached path here is keyed by release. The output cannot be -- createVM
# looks for exactly wf("<osname>.iso") -- so it gets a stamp file recording
# which release it was remixed for.
#
# Without this the caches collide across releases in a shared WORKDIR. CI never
# sees it (each job starts on a fresh runner), but a second local build does:
# after building 15, the workdir still holds 15's upstream ISO and its extracted
# tree, and a 14 build would skip the download, re-wrap 15's contents, and
# install RELEASE 15 while reporting itself as 14. That failure is silent and
# produces a plausible-looking green build, which is the worst kind.
_iso_src = wf("%s-%s-upstream.iso" % (_osname, _rel))
_tree = wf("isotree-%s" % _rel)
_stamp = wf("%s.iso.release" % _osname)

# The stamp carries a recipe tag as well as the release, so that changing WHAT
# the remix puts on the ISO invalidates a cached one. Bump the tag whenever the
# remix gains or loses content -- "sets1" is: serial console + the distribution
# sets baked into /usr/freebsd-dist.
_RECIPE = "sets1"
_stamp_want = "%s %s" % (_rel, _RECIPE)

_cached = None
if os.path.exists(_stamp):
    try:
        _cached = open(_stamp).read().strip()
    except OSError:
        _cached = None
_cached_rel = _cached

if os.path.exists(_iso_out) and _cached == _stamp_want:
    log("hardenedbsd beforeBuild: %s already remixed for release %s, skipping"
        % (_iso_out, _rel))
else:
    if os.path.exists(_iso_out):
        log("hardenedbsd beforeBuild: %s was remixed for release %r, not %r "
            "-- rebuilding it" % (_iso_out, _cached_rel, _rel))
        try: os.remove(_iso_out)
        except OSError: pass
    try: os.remove(_stamp)
    except OSError: pass

    if not os.path.exists(_iso_src):
        log("hardenedbsd beforeBuild: downloading %s" % env("VM_ISO_LINK"))
        download(env("VM_ISO_LINK"), _iso_src)

    if not shutil.which("xorriso"):
        log("hardenedbsd beforeBuild: installing xorriso")
        _run_quiet(["sudo", "-E", "apt-get", "install", "-y", "-qq", "xorriso"],
                   env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    if not shutil.which("xorriso"):
        log("FATAL: xorriso is required to remix the installer ISO")
        sys.exit(1)

    # --- locate the hidden EFI El Torito image before touching anything -----
    # `-report_el_torito plain` prints one row per boot image:
    #   El Torito boot img : 2  UEFI  y  none  0x0000  0x00   4096   20
    # where Ldsiz (4096) counts 512-byte sectors and LBA (20) counts 2048-byte
    # blocks. Parse it rather than hardcoding: the numbers differ per branch
    # and per build, and a wrong offset yields a silently unbootable ISO.
    _rep = subprocess.run(["xorriso", "-indev", _iso_src,
                           "-report_el_torito", "plain"],
                          capture_output=True, text=True).stdout
    _efi_lba = _efi_sects = None
    for _line in _rep.splitlines():
        m = re.search(r"El Torito boot img :\s+\d+\s+UEFI\s+\S+\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(\d+)",
                      _line)
        if m:
            _efi_sects, _efi_lba = int(m.group(1)), int(m.group(2))
            break
    if _efi_lba is None:
        log("FATAL: no UEFI El Torito entry found in %s; report was:\n%s"
            % (_iso_src, _rep))
        sys.exit(1)
    log("hardenedbsd beforeBuild: EFI boot image at LBA %d, %d * 512 bytes"
        % (_efi_lba, _efi_sects))

    # --- the volume id is LOAD-BEARING, not cosmetic ------------------------
    # The ISO's /etc/fstab mounts the root filesystem BY LABEL:
    #     /dev/iso9660/<VOLID>  /  cd9660 ro
    # so the rebuilt ISO must keep the original volume id byte for byte. A
    # made-up label (this hook first shipped with "HARDENEDBSD_INSTALL")
    # produces an ISO that boots the loader and the kernel perfectly and then
    # stops dead at:
    #     Mounting from cd9660:/dev/iso9660/<original> failed with error 19.
    #     mountroot>
    # -- an interactive prompt no console driver is waiting for, so the build
    # just burns its waitForText ceiling. Read the real label instead.
    _pvd = subprocess.run(["xorriso", "-indev", _iso_src, "-pvd_info"],
                          capture_output=True, text=True)
    _pvd_txt = (_pvd.stdout or "") + (_pvd.stderr or "")
    _volid = None
    for _pat in (r"Volume\s*Id\s*:\s*'([^']+)'", r"Volume\s*Id\s*:\s*(\S.*?)\s*$"):
        m = re.search(_pat, _pvd_txt, re.M | re.I)
        if m:
            _volid = m.group(1).strip()
            break
    if not _volid:
        log("FATAL: could not read the volume id from %s -- refusing to guess, "
            "because a wrong label breaks the root mount. -pvd_info said:\n%s"
            % (_iso_src, _pvd_txt))
        sys.exit(1)
    log("hardenedbsd beforeBuild: preserving volume id %r" % _volid)

    # --- extract (reuse an existing tree: a retry must not pay for it twice) -
    if not os.path.isdir(_tree):
        log("hardenedbsd beforeBuild: extracting the ISO tree")
        must_run(["xorriso", "-osirrox", "on", "-indev", _iso_src,
                  "-extract", "/", _tree], "xorriso extract")
    # The extracted tree comes out read-only (mode 0444 from the ISO), which
    # blocks both the loader.conf append below and the rebuild.
    must_sh("chmod -R u+w %s" % shlex.quote(_tree), "chmod extracted tree")

    # --- carve the EFI image out by LBA ------------------------------------
    # 4 * 512 == 2048, so Ldsiz/4 is the count in 2048-byte blocks.
    _efi_img = os.path.join(_tree, "efiboot.img")
    must_sh("dd if=%s of=%s bs=2048 skip=%d count=%d status=none"
            % (shlex.quote(_iso_src), shlex.quote(_efi_img),
               _efi_lba, _efi_sects // 4),
            "carve efiboot.img")

    # --- bake the distribution sets into the ISO ----------------------------
    # bootonly.iso ships /usr/freebsd-dist/MANIFEST but none of the sets, so
    # bsdinstall would fetch them over the network. That path cost two long
    # failures: bsdinstall's checksum step decided kernel.txz did not match and
    # put up a modal error dialog with an [OK] button that nothing is driving,
    # so the console froze and the build waited out its ceiling -- 30 min in CI,
    # 40 min locally -- with no error visible anywhere. The host copies verified
    # byte-exact against MANIFEST every time, so whatever went wrong was in the
    # guest's fetch, and pre-fetching inside the guest is not an option either:
    # the live system's /tmp is RAM-backed and cannot hold 628 MB.
    #
    # Putting the sets ON the ISO removes the whole failure surface. bsdinstall
    # finds them in BSDINSTALL_DISTDIR (which for an ISO install IS
    # /usr/freebsd-dist) and never fetches anything -- no HTTP, no resume, no
    # checksum surprise, and no dependency on the host web server, which also
    # means this builder no longer conflicts with another build over port 8000.
    # It is what disc1.iso does; we are turning bootonly into its equivalent.
    _distdir = os.path.join(_tree, "usr", "freebsd-dist")
    os.makedirs(_distdir, exist_ok=True)
    _dist_base = env("VM_ISO_LINK").rsplit("/", 1)[0]
    for _fn in ("MANIFEST", "kernel.txz", "base.txz"):
        _dst = os.path.join(_distdir, _fn)
        if os.path.exists(_dst) and os.path.getsize(_dst) > 0:
            log("hardenedbsd beforeBuild: %s already in the ISO tree (%d bytes)"
                % (_fn, os.path.getsize(_dst)))
            continue
        log("hardenedbsd beforeBuild: adding %s to the ISO tree" % _fn)
        download("%s/%s" % (_dist_base, _fn), _dst)

    # Verify them HERE, while a mismatch can still name itself. Inside the
    # guest the same mismatch is a dialog nobody can dismiss.
    import hashlib as _hashlib
    _want = {}
    with open(os.path.join(_distdir, "MANIFEST")) as _mf:
        for _line in _mf:
            _p = _line.split()
            if len(_p) >= 2:
                _want[_p[0]] = _p[1]
    for _fn in ("kernel.txz", "base.txz"):
        _dst = os.path.join(_distdir, _fn)
        _hh = _hashlib.sha256()
        with open(_dst, "rb") as _fh:
            for _chunk in iter(lambda: _fh.read(1024 * 1024), b""):
                _hh.update(_chunk)
        if _hh.hexdigest() != _want.get(_fn):
            log("FATAL: %s does not match MANIFEST before it even reaches the "
                "ISO.\n  expected %s\n  actual   %s"
                % (_fn, _want.get(_fn), _hh.hexdigest()))
            sys.exit(1)
        log("hardenedbsd beforeBuild: %s verified (%s)"
            % (_fn, _hh.hexdigest()[:16]))

    # --- give the loader a serial console (idempotent) ----------------------
    _lc = os.path.join(_tree, "boot", "loader.conf")
    _marker = "# anyvm hardenedbsd-builder: serial console"
    _cur = open(_lc).read() if os.path.exists(_lc) else ""
    if _marker not in _cur:
        with open(_lc, "a") as _f:
            _f.write("\n%s\n" % _marker)
            _f.write('console="comconsole"\n')
            _f.write('comconsole_speed="115200"\n')
            _f.write('boot_serial="YES"\n')
            # The stock ISO waits 10 s at the beastie menu; nothing is going
            # to press a key, so cut it to keep the build short.
            _f.write('autoboot_delay="3"\n')
    log("hardenedbsd beforeBuild: loader.conf is now:\n%s" % open(_lc).read())

    # --- rebuild with the boot records spelled out --------------------------
    try: os.remove(_iso_out)
    except OSError: pass
    must_run(["xorriso", "-as", "mkisofs",
              "-R", "-J", "-V", _volid,
              "-uid", "0", "-gid", "0",
              "-b", "boot/cdboot", "-no-emul-boot",
              "-eltorito-alt-boot", "-e", "efiboot.img", "-no-emul-boot",
              "-o", _iso_out, _tree], "xorriso rebuild")

    # --- prove the boot record survived -------------------------------------
    # Skipping this check is how trap 1 above stays invisible until a build
    # has burned its whole console timeout on a VM that never started.
    #
    # Check the "El Torito boot img" rows, NOT the "Boot record : El Torito"
    # line from xorriso's drive summary: that summary goes to STDERR while the
    # report goes to stdout, so a stdout-only check for it fails on a
    # perfectly good ISO. (It did exactly that on the first real build --
    # the ISO was fine, both entries present, and the hook aborted anyway.)
    # Merge both streams and assert on what the report actually contains.
    _p2 = subprocess.run(["xorriso", "-indev", _iso_out,
                          "-report_el_torito", "plain"],
                         capture_output=True, text=True)
    _rep2 = (_p2.stdout or "") + (_p2.stderr or "")
    _imgs = re.findall(r"El Torito boot img :\s+\d+\s+(\S+)", _rep2)
    if "BIOS" not in _imgs:
        log("FATAL: the rebuilt ISO has no BIOS El Torito entry "
            "(found %r); report:\n%s" % (_imgs, _rep2))
        sys.exit(1)
    if "/boot/cdboot" not in _rep2:
        log("FATAL: the rebuilt ISO's BIOS boot image is not /boot/cdboot; "
            "report:\n%s" % _rep2)
        sys.exit(1)
    log("hardenedbsd beforeBuild: El Torito entries present: %s" % ", ".join(_imgs))

    # Stamp LAST, only once the ISO has passed its checks -- an interrupted
    # remix must not leave a stamp claiming the output is good.
    with open(_stamp, "w") as _sf:
        _sf.write(_stamp_want + "\n")
    log("hardenedbsd beforeBuild: remixed ISO ready (%d bytes) for release %s, "
        "El Torito intact" % (os.path.getsize(_iso_out), _rel))
