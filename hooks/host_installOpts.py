# host_installOpts.py -- drive the HardenedBSD ISO install via bsdinstall's
# scripted mode instead of OCR-driving the TUI.
#
# This runs against the REMIXED ISO that hooks/host_beforeBuild.py produced,
# so the installer is on ttyu0 and everything below is plain serial I/O.
#
# Flow (each step verified locally against 15-stable under KVM):
#   1. "Console type [vt100]:" -> enter
#   2. Welcome dialog -> 'L' selects Live System
#   3. getty on ttyu0 -> log in as root (no password)
#   4. push install_runner.sh via inputFileNC, which writes
#      /tmp/installerconfig, runs `bsdinstall script`, and powers off.
#
# Cloud-image builders never set VM_ISO_LINK, so run_hook("installOpts") is
# only ever reached here. No gating needed.

log("hardenedbsd installOpts: scripted bsdinstall over ttyu0")

# Read the host's id_rsa.pub now. build.py creates it lazily later in
# _gen_enablessh_local, but the installerconfig has to bake it in before that,
# so mirror the bootstrap.
_idrsa = os.path.join(HOME, ".ssh", "id_rsa")
if not os.path.exists(_idrsa):
    run(["ssh-keygen", "-f", _idrsa, "-q", "-N", ""])
_HOST_PUBKEY = open(_idrsa + ".pub").read().rstrip("\n")
log("hardenedbsd installOpts: host pubkey = %s..." % _HOST_PUBKEY[:60])

# Pre-download the distribution sets onto the HOST so the guest can fetch them
# over SLIRP from the http.server build.py's startWeb() already runs in cwd.
# bootonly.iso deliberately carries no sets, and the Live System root is
# read-only cd9660 so dhclient cannot even write /etc/resolv.conf -- letting
# bsdinstall fetch from its own mirror would die on name resolution.
#
# Unlike freebsd-builder there is no download/archive mirror race to probe
# here: HardenedBSD publishes MANIFEST, base.txz and kernel.txz in the SAME
# LATEST directory as the ISO, so the base URL is just the ISO's dirname and
# always moves in lockstep with the ISO we actually booted.
_DIST_BASE = env("VM_ISO_LINK").rsplit("/", 1)[0]
log("hardenedbsd installOpts: dist base = %s" % _DIST_BASE)
for _fn in ("MANIFEST", "kernel.txz", "base.txz"):
    if os.path.exists(_fn) and os.path.getsize(_fn) > 0:
        log("hardenedbsd installOpts: %s already cached (%d bytes)"
            % (_fn, os.path.getsize(_fn)))
        continue
    log("hardenedbsd installOpts: pre-downloading %s/%s" % (_DIST_BASE, _fn))
    download("%s/%s" % (_DIST_BASE, _fn), _fn)
log("hardenedbsd installOpts: distfiles cached: %s"
    % ", ".join("%s=%d" % (f, os.path.getsize(f))
                for f in ("MANIFEST", "kernel.txz", "base.txz")
                if os.path.exists(f)))


# Step 1: the vt100 confirmation that appears before bsdinstall proper.
# Measured at ~12 s from power-on under KVM; the ceiling is for TCG.
waitForText("Console type", "600")
time.sleep(2)
inputKeys("enter")

# Step 2: Welcome dialog. Buttons are [Install] [Shell] [Live System] with
# focus on Install; 'L' is the Live System accelerator. bsddialog wants a beat
# between the letter and the enter to register the keypress.
#
# Match on the body text, not the title: the title is "Welcome", but the body
# "Would you like to begin an installation or use the live system?" is the
# string that cannot collide with the earlier "Welcome to HardenedBSD!" that
# rc prints on the way up. Matching the banner instead fires too early.
waitForText("begin an installation", "600")
time.sleep(2)
inputKeys("string L")
time.sleep(2)
inputKeys("enter")

# Step 3: the Live System path finishes booting and drops to a getty on
# ttyu0. Default login is root with no password.
waitForText("login:", "600")
time.sleep(3)
inputKeys("string root")
time.sleep(1)
inputKeys("enter")

# Step 4: at the root shell. The login shell runs `resizewin`, which BLOCKS
# reading stdin for several seconds waiting for a cursor-position report that
# never comes over this chardev; anything typed during that window is eaten by
# resizewin rather than the shell. Do not try to waitForText the resizewin
# timeout -- that string is already in the scrollback from bsdinstall's own
# startup, so the wait returns instantly. Just sleep it out.
time.sleep(30)

# Sentinel: if this echoes back, the shell really is reading.
string("echo MARK_SHELL_READY")
enter()
waitForText("MARK_SHELL_READY", "120")
time.sleep(2)

# The Live System boots with vtnet0 link-up but NO IPv4 -- bsdinstall would
# normally run dhclient from its Network Configuration screen, which the Live
# System path skips. Without an address `nc 192.168.122.1` just returns no
# route and inputFileNC silently delivers nothing.
string("dhclient vtnet0 && echo MARK_NET_OK || echo MARK_NET_FAIL")
enter()
waitForText("MARK_NET_OK", "120")
time.sleep(3)

with open("install_runner.sh", "w") as f:
    f.write((
"""#!/bin/sh
# NOT using `set -e` so we see exactly where things fall over.
echo "==== install_runner: starting at $(date) ===="
echo "uname: $(uname -a)"

DISTSITE="http://192.168.122.1:8000"
echo "MARK_DISTSITE=$DISTSITE"

cat > /tmp/installerconfig <<CFG
PARTITIONS=DEFAULT
DISTRIBUTIONS="kernel.txz base.txz"
BSDINSTALL_DISTSITE=$DISTSITE
nonInteractive=YES

#!/bin/sh
# Bake the host pubkey straight into root's authorized_keys so the host can
# ssh in immediately after the post-install reboot, with no console-paste
# handshake. No root password is set: the pubkey is the only credential and
# the build runs with empty-password root.
sysrc hostname="hardenedbsd"
sysrc sshd_enable="YES"
sysrc ifconfig_vtnet0="DHCP"
sysrc ifconfig_vtnet0_ipv6="inet6 ifdisabled"

# CRITICAL, and the one thing that does not carry over from the ISO: the
# serial console. The remix in host_beforeBuild.py patched the ISO's
# loader.conf, but bsdinstall writes the INSTALLED system a fresh one, and on
# amd64 its default console is VGA. Without these three lines the install
# succeeds, the machine reboots, and it is never reachable again -- the build
# would then sit in _wait_ssh until the ceiling with a perfectly healthy guest
# talking to a screen nobody is reading.
echo 'console="comconsole"' >> /boot/loader.conf
echo 'comconsole_speed="115200"' >> /boot/loader.conf
echo 'boot_serial="YES"' >> /boot/loader.conf

echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
echo 'PermitEmptyPasswords yes' >> /etc/ssh/sshd_config
echo 'AcceptEnv *' >> /etc/ssh/sshd_config
echo 'StrictModes no' >> /etc/ssh/sshd_config
mkdir -p /root/.ssh
chmod 700 /root/.ssh
cat > /root/.ssh/authorized_keys <<'KEYS'
${HOST_PUBKEY}
KEYS
chmod 600 /root/.ssh/authorized_keys
CFG
echo "installerconfig wc: $(wc -c < /tmp/installerconfig)"
echo "---- about to call bsdinstall ----"
export BSDINSTALL_DISTSITE="$DISTSITE"
bsdinstall script /tmp/installerconfig 2>&1
rc=$?
echo "==== bsdinstall script exit $rc ===="
if [ $rc -eq 0 ]; then
    echo "MARK_INSTALL_DONE"
    sync
    sleep 5
    shutdown -p now
else
    echo "INSTALL_FAILED -- bsdinstall returned $rc"
fi
""").replace("${HOST_PUBKEY}", _HOST_PUBKEY))

time.sleep(2)
inputFileNC("install_runner.sh")

# Block until install_runner prints its success marker (followed by
# shutdown -p, so the VM goes down and main() picks up at _wait_vm_down) or
# its failure marker (which deliberately does NOT power off, leaving the VM
# up for forensics).
log("hardenedbsd installOpts: install_runner.sh pushed via nc; "
    "waiting for MARK_INSTALL_DONE or INSTALL_FAILED")
waitForText("MARK_INSTALL_DONE", "2400")
