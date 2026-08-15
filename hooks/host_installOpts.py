# host_installOpts.py -- drive the HardenedBSD ISO install via bsdinstall's
# scripted mode instead of OCR-driving the TUI.
#
# This runs against the REMIXED ISO that hooks/host_beforeBuild.py produced.
# That hook does two things this one depends on: it gives the installer a
# serial console (the stock amd64 ISO is VGA-only), and it bakes the
# distribution sets into /usr/freebsd-dist on the ISO itself. Because the sets
# are already there, bsdinstall never fetches anything -- there is no
# BSDINSTALL_DISTSITE here, no host web server involved, and no network
# transfer that could corrupt a set.
#
# That last point is the whole reason this hook is short now. The earlier
# version served the sets over HTTP from the build host, and bsdinstall's
# checksum step twice decided kernel.txz did not match. It reports that with a
# modal dialog and an [OK] button; nothing is driving the console at that
# point, so the screen freezes and every later poll reports "no new screen
# text" -- indistinguishable from a hang, and it burned 30 min of CI and 40 min
# locally before the error was even visible. The host copies verified
# byte-exact against MANIFEST every time, and pre-fetching inside the guest is
# not possible either (the live system's /tmp is RAM-backed and cannot hold
# 628 MB). Taking the network out of the path removes the failure entirely.
#
# Flow (each step verified locally against release 15 under KVM):
#   1. "Console type [vt100]:" -> enter
#   2. Welcome dialog -> 'L' selects Live System
#   3. getty on ttyu0 -> log in as root (no password)
#   4. push install_runner.sh via inputFileNC, which writes
#      /tmp/installerconfig, runs `bsdinstall script`, and powers off.

log("hardenedbsd installOpts: scripted bsdinstall over ttyu0, sets on the ISO")

# Read the host's id_rsa.pub now. build.py creates it lazily later in
# _gen_enablessh_local, but the installerconfig has to bake it in before that,
# so mirror the bootstrap.
_idrsa = os.path.join(HOME, ".ssh", "id_rsa")
if not os.path.exists(_idrsa):
    run(["ssh-keygen", "-f", _idrsa, "-q", "-N", ""])
_HOST_PUBKEY = open(_idrsa + ".pub").read().rstrip("\n")
log("hardenedbsd installOpts: host pubkey = %s..." % _HOST_PUBKEY[:60])


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

# Networking is still needed -- not for the install (the sets are local now)
# but for the installed system's first boot, and inputFileNC below pushes the
# runner over nc from the host. The Live System comes up with vtnet0 link-up
# but no IPv4, because bsdinstall would normally run dhclient from the Network
# Configuration screen that the Live System path skips.
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

# The sets ride on the ISO at /usr/freebsd-dist, which is where bsdinstall
# looks by default for an ISO install. Prove they are there before doing
# anything -- if this ever prints MARK_SETS_MISSING then the remix in
# host_beforeBuild.py stopped adding them, and the failure says so in one
# line instead of surfacing as an undismissable dialog twenty minutes later.
for s in MANIFEST kernel.txz base.txz; do
    if [ -s "/usr/freebsd-dist/$s" ]; then
        echo "MARK_SET_PRESENT=$s size=$(stat -f %z /usr/freebsd-dist/$s)"
    else
        echo "MARK_SETS_MISSING=$s"
        echo "INSTALL_FAILED distribution sets are not on the ISO"
        exit 1
    fi
done

cat > /tmp/installerconfig <<CFG
PARTITIONS=DEFAULT
DISTRIBUTIONS="kernel.txz base.txz"
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

# Bound it. bsdinstall reports several classes of problem with a modal dialog
# and waits for an [OK] nobody will press; without a bound that is the whole
# waitForText ceiling spent on a frozen screen. 45 min is generous for a TCG
# runner extracting a 577 MB base.txz and still fails an order of magnitude
# sooner than the silent case did.
bsdinstall script /tmp/installerconfig 2>&1 &
bpid=$!
n=0
while kill -0 "$bpid" 2>/dev/null && [ "$n" -lt 270 ]; do
    sleep 10
    n=$((n + 1))
done
if kill -0 "$bpid" 2>/dev/null; then
    kill -9 "$bpid" 2>/dev/null
    echo "INSTALL_FAILED bsdinstall still running after 45 min -- killed"
    exit 1
fi
wait "$bpid"
rc=$?
echo "==== bsdinstall script exit $rc ===="
if [ $rc -eq 0 ]; then
    echo "MARK_INSTALL_DONE"
    sync
    sleep 5
    shutdown -p now
else
    echo "INSTALL_FAILED bsdinstall returned $rc"
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
waitForText("MARK_INSTALL_DONE", "3000")
