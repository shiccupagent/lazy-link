#!/usr/bin/env bash
# setup-linux.sh - makes a Linux PC a Lazy Link render box. Run once, as root, on the PC:
#   sudo bash setup-linux.sh [--dry-run] [--key-file FILE | --key "<key line from mac-key.pub>"]
#                            [--subnet 192.168.1.50/24] [--no-firewall]
# Without a key option it reads mac-key.pub from this folder, or asks you to paste the key.
# --dry-run prints every change and changes nothing.
#
# What it does (undo-linux.sh reverses each step, using the state file it writes as it goes):
#   1. account 'lazylink': no password, no sudo, no admin; the Mac's key is its only way in
#   2. sshd drop-in: 'lazylink' signs in with a key only; other accounts are unchanged
#   3. /srv/lazylink: jobs/, runner/ (runner.sh) and models/ (undo keeps models/)
#   4. systemd service lazy-link-runner: runs runner.sh as 'lazylink', restarts it if it stops
#   5. sudoers line: 'lazylink' may run 'systemctl suspend' as root and nothing else
#   6. firewall: ssh (port 22) from the home network only; this script never turns ufw on
#   7. Wake on Magic Packet for the wired card (installs ethtool with apt if missing)
#   8. pc-info.json, read by the Mac's `pcjob pair --os linux`
#
# Installed before 2026-10-30 (account 'lazy', folder /srv/lazy-link)? Run this again: it renames
# 'lazy' to 'lazylink' and the folder to /srv/lazylink, keeps the models, and restarts the runner.
# Then pair this PC again from the Mac.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
USER_NAME=lazylink
LEGACY_USER=lazy
WORK=/srv/lazylink
LEGACY_WORK=/srv/lazy-link
STATE_DIR=/var/lib/lazy-link
STATE="$STATE_DIR/setup-state"
UNIT=/etc/systemd/system/lazy-link-runner.service
WOL_UNIT=/etc/systemd/system/lazy-link-wol.service
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-lazy-link.conf
SUDOERS=/etc/sudoers.d/lazy-link-suspend

DRY=0; KEY=""; KEY_FILE=""; SUBNET=""; SKIP_FW=0

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }
die() {
    echo "setup-linux: $*" >&2
    [ "$DRY" = 1 ] || echo "setup-linux: stopped part-way. Run: sudo bash $HERE/undo-linux.sh" >&2
    exit 1
}
step() { echo; echo "== $*"; }
# Runs a command for real, or only shows it in a dry run.
run() { if [ "$DRY" = 1 ]; then echo "   would run: $*"; return 0; fi; "$@"; }
# Writes stdin to a file with the given mode and owner; in a dry run only says so.
put() {  # path mode owner
    if [ "$DRY" = 1 ]; then echo "   would write $1"; cat > /dev/null; return 0; fi
    mkdir -p "$(dirname "$1")" && cat > "$1" && chmod "$2" "$1" && chown "$3" "$1"
}
# Remembers a change so undo-linux.sh can reverse it.
record() {
    [ "$DRY" = 1 ] && return 0
    mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR" && echo "$1" >> "$STATE"
}
# What setup recorded (empty if there is no record); has_record matches a whole line.
recorded() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | head -1; }
has_record() { [ -f "$STATE" ] && grep -qx -- "$1" "$STATE"; }
# Upgrade from 'lazy': the record now names 'lazylink' and /srv/lazylink (a dry run only says so).
rewrite_state() {
    if [ "$DRY" = 1 ]; then echo "   would update the setup record: created_user=lazylink, paths under /srv/lazylink"; return 0; fi
    sed 's#/srv/lazy-link#/srv/lazylink#g; s/^created_user=lazy$/created_user=lazylink/' "$STATE" > "$STATE.new" \
        && mv -f "$STATE.new" "$STATE" && echo "migrated_from=$LEGACY_USER" >> "$STATE"
}
# Disks as "mount<TAB>total_bytes<TAB>free_bytes" (same filter as check.sh).
disk_rows() {
    df -P -B1 -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs -x ramfs -x autofs \
       -x fuse.lxcfs -x nsfs -x vfat 2>/dev/null | awk 'NR > 1 && $6 !~ /^\/(boot|snap|run)/ {print $6 "\t" $2 "\t" $4}'
}

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) DRY=1 ;;
        --key) KEY=${2:-}; shift ;;
        --key-file) KEY_FILE=${2:-}; shift ;;
        --subnet) SUBNET=${2:-}; shift ;;
        --no-firewall) SKIP_FW=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# ---- 0. This PC ---------------------------------------------------------------------------------
step "This PC"
if [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then die "run it with sudo: sudo bash $0"; fi
[ -d /run/systemd/system ] || die "this PC does not run systemd; the runner service needs it"
[ -x /usr/sbin/sshd ] || die "openssh-server is not installed (sudo apt install openssh-server)"
[ -r "$HERE/runner.sh" ] || die "runner.sh is missing next to this script ($HERE)"
MIGRATE=0
if [ -e "$STATE" ]; then
    if has_record created_user=lazylink; then die "already set up (record: $STATE). Run undo-linux.sh first to start again."
    elif has_record created_user=lazy; then MIGRATE=1
    else die "the setup record $STATE is not one this script wrote; not touching it"
    fi
fi
echo "   $(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-Linux}")"
if [ "$MIGRATE" = 1 ]; then
    step "Upgrade: this PC was set up with the old 'lazy' account and $LEGACY_WORK"
    if [ -d "$LEGACY_WORK/jobs" ] && grep -l '"state": "running"' "$LEGACY_WORK"/jobs/*/status.json >/dev/null 2>&1; then
        die "a job is running now; wait until it finishes (or is cancelled), then run this again"
    fi
    id "$USER_NAME" >/dev/null 2>&1 && die "a user named '$USER_NAME' already exists and is not the old account; not touching it"
    [ -d "$WORK" ] && die "both $LEGACY_WORK and $WORK exist; not guessing which one to keep. Move $WORK away, then run this again"
    if [ -d "$LEGACY_WORK" ] && mountpoint -q "$LEGACY_WORK" 2>/dev/null; then
        die "$LEGACY_WORK is a mount point and cannot be renamed; move its contents to $WORK by hand, then run this again"
    fi
    echo "   the old account is renamed (same user ID: its files and sign-in key come with it)"
    echo "   $LEGACY_WORK is renamed to $WORK (jobs and models come with it)"
fi

# ---- 1. The key -----------------------------------------------------------------------------------
step "The Mac's key (the only way in)"
if [ -n "$KEY" ]; then key=$KEY
elif [ -n "$KEY_FILE" ]; then key=$(cat "$KEY_FILE") || die "cannot read $KEY_FILE"
elif [ -f "$HERE/mac-key.pub" ]; then key=$(cat "$HERE/mac-key.pub")
else
    echo "   No key file next to this script. Paste the Mac's public key (one line, starts with ssh-), then press Enter:"
    read -r key < /dev/tty || die "no key given (use --key-file FILE)"
fi
key=$(printf '%s\n' "$key" | head -1)
tmpkey=$(mktemp) || die "mktemp failed"
printf '%s\n' "$key" > "$tmpkey"
fp=$(ssh-keygen -lf "$tmpkey" 2>/dev/null | awk '{print $2, $NF}')
rm -f "$tmpkey"
[ -n "$fp" ] || die "that is not an SSH public key (it should start with ssh-ed25519 or ssh-rsa)"
echo "   key fingerprint: $fp"

# ---- 2. Account -----------------------------------------------------------------------------------
BASE_HOME="$(useradd -D | sed -n 's/^HOME=//p')"
USER_HOME="$BASE_HOME/$USER_NAME"  # where the home folder is (useradd default base, then the account name)
step "Account '$USER_NAME' (the job runner: no password, no sudo, no admin)"
if [ "$MIGRATE" = 1 ]; then
    # usermod -l keeps the user ID, so the home folder, files and group memberships carry over.
    run systemctl stop lazy-link-runner.service || die "could not stop the runner (see: journalctl -u lazy-link-runner -n 30)"
    run pkill -TERM -u "$LEGACY_USER" 2>/dev/null
    if [ "$DRY" != 1 ]; then for i in 1 2 3 4 5; do pgrep -u "$LEGACY_USER" >/dev/null 2>&1 || break; sleep 1; done; fi
    run pkill -KILL -u "$LEGACY_USER" 2>/dev/null
    run usermod -l "$USER_NAME" -d "$USER_HOME" -m "$LEGACY_USER" || die "could not rename '$LEGACY_USER' (see: ps -u $LEGACY_USER)"
    rewrite_state
    if getent group "$LEGACY_USER" >/dev/null && ! getent group "$USER_NAME" >/dev/null; then
        run groupmod -n "$USER_NAME" "$LEGACY_USER" || die "could not rename the '$LEGACY_USER' group"
    fi
    echo "   renamed '$LEGACY_USER' to '$USER_NAME'"
else
    if id "$USER_NAME" >/dev/null 2>&1; then
        die "a user named '$USER_NAME' already exists and this setup did not make it; not touching it"
    fi
    run useradd --create-home --shell /bin/bash --comment "Lazy Link job runner" "$USER_NAME" || die "useradd failed"
    record created_user="$USER_NAME"
fi
for g in video render; do
    if getent group "$g" >/dev/null; then run usermod -aG "$g" "$USER_NAME" || die "could not add $USER_NAME to $g"; fi
done
run install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$USER_HOME/.ssh" || die "could not create the $USER_NAME account's .ssh folder"
printf '%s\n' "$key" | put "$USER_HOME/.ssh/authorized_keys" 600 "$USER_NAME:$USER_NAME" || die "could not write authorized_keys"
echo "   account ready; the Mac's key is installed"

# ---- 3. sshd: key only for the runner account -----------------------------------------------------
step "Sign-in rule: '$USER_NAME' uses a key only (other accounts unchanged)"
put "$SSHD_DROPIN" 644 root:root <<'EOF' || die "could not write $SSHD_DROPIN"
# Lazy Link: the 'lazylink' account (the Mac's job runner) signs in with its key only.
# A Match block runs until the next Match, so 'Match all' ends it before the rest of sshd_config.
Match User lazylink
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
Match all
EOF
record sshd_dropin="$SSHD_DROPIN"
if [ "$DRY" != 1 ]; then
    if ! /usr/sbin/sshd -t; then rm -f "$SSHD_DROPIN"; die "sshd rejected the new rule (removed it again)"; fi
    run systemctl reload ssh || run systemctl reload sshd || die "could not reload sshd"
    if /usr/sbin/sshd -T -C user=lazylink,host=localhost,addr=127.0.0.1 2>/dev/null | grep -qx 'passwordauthentication no'; then
        echo "   checked with sshd: password sign-in is off for $USER_NAME"
    else
        die "sshd still allows a password for $USER_NAME"
    fi
fi

# ---- 4. Work folder and runner --------------------------------------------------------------------
step "Work folder $WORK"
if [ "$MIGRATE" = 1 ] && [ -d "$LEGACY_WORK" ]; then
    run mv "$LEGACY_WORK" "$WORK" || die "could not rename $LEGACY_WORK to $WORK"
fi
if [ -d "$WORK" ]; then preexisted=yes; else preexisted=no; fi
run install -d -m 755 -o "$USER_NAME" -g "$USER_NAME" "$WORK" "$WORK/jobs" "$WORK/runner" "$WORK/models" || die "could not create $WORK"
record work_folder="$WORK"
record work_preexisted="$preexisted"
run install -m 644 -o "$USER_NAME" -g "$USER_NAME" "$HERE/runner.sh" "$WORK/runner/runner.sh" || die "could not copy runner.sh"
echo "   jobs/, runner/, models/ ready"

# ---- 5. The runner service ------------------------------------------------------------------------
step "Job runner service (lazy-link-runner)"
put "$UNIT" 644 root:root <<EOF || die "could not write $UNIT"
[Unit]
Description=Lazy Link job runner
Wants=network-online.target
After=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$WORK
ExecStart=/bin/bash $WORK/runner/runner.sh
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
record unit="$UNIT"
run systemctl daemon-reload || die "systemctl daemon-reload failed"
run systemctl enable --now lazy-link-runner.service || die "could not start the runner (see: journalctl -u lazy-link-runner -n 30)"
record service=lazy-link-runner.service
if [ "$DRY" != 1 ]; then
    ok=0
    for i in $(seq 1 15); do
        if [ -f "$WORK/runner.heartbeat" ] && [ $(( $(date +%s) - $(stat -c %Y "$WORK/runner.heartbeat") )) -lt 30 ]; then ok=1; break; fi
        sleep 2
    done
    if [ "$ok" = 1 ]; then echo "   Job runner: running"
    else echo "   Job runner: NOT running yet. See: journalctl -u lazy-link-runner -n 30"; fi
fi

# ---- 6. Suspend rule ------------------------------------------------------------------------------
step "Sleep rule: '$USER_NAME' may run 'systemctl suspend' as root, and nothing else"
SYSCTL=$(command -v systemctl)
echo "$USER_NAME ALL=(root) NOPASSWD: $SYSCTL suspend" | put "$SUDOERS" 440 root:root || die "could not write $SUDOERS"
if [ "$DRY" != 1 ]; then
    visudo -cf "$SUDOERS" >/dev/null || { rm -f "$SUDOERS"; die "sudoers line rejected (removed again)"; }
    if sudo -n -l -U "$USER_NAME" 2>/dev/null | grep -q "$SYSCTL suspend"; then echo "   checked: sudo shows the suspend rule for $USER_NAME"
    else die "sudo does not show the suspend rule for $USER_NAME"; fi
fi
record sudoers="$SUDOERS"

# ---- 7. Firewall ----------------------------------------------------------------------------------
step "Firewall: ssh from the home network only"
if [ "$SKIP_FW" = 1 ]; then
    echo "   skipped (--no-firewall)"
elif ! command -v ufw >/dev/null 2>&1; then
    echo "   ufw is not installed, so no rule was added. Install it (sudo apt install ufw), then add the rule:"
    echo "   sudo ufw allow from <your home network> to any port 22 proto tcp"
else
    if [ -z "$SUBNET" ]; then
        iface=$(ip -o -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
        [ -n "$iface" ] && SUBNET=$(ip -4 route show dev "$iface" scope link 2>/dev/null | awk 'NR == 1 {print $1}')
    fi
    if [ -z "$SUBNET" ]; then
        echo "   could not work out the home network: rerun with --subnet YOUR-NETWORK/24 (for example 192.168.1.50/24). No rule added."
    else
        echo "   home network: $SUBNET"
        run ufw allow from "$SUBNET" to any port 22 proto tcp comment 'lazy-link ssh' || die "ufw rule failed"
        record ufw_subnet="$SUBNET"
        if [ "$DRY" != 1 ] && ufw status 2>/dev/null | grep -q 'Status: active'; then
            echo "   firewall is on: the rule is active"
        else
            echo "   firewall (ufw) is OFF. The rule is saved and applies once it is on. This script does not turn it on;"
            echo "   to turn it on yourself: sudo ufw enable"
        fi
    fi
fi

# ---- 8. Wake on Magic Packet ----------------------------------------------------------------------
step "Wake on Magic Packet (lets the Mac wake this PC over the cable)"
if ! command -v ethtool >/dev/null 2>&1; then
    run apt-get install -y -q ethtool || echo "   could not install ethtool: Wake on LAN not set"
fi
ETHTOOL=$(command -v ethtool)
wol_lines=""
for path in /sys/class/net/*; do
    n=$(basename "$path")
    [ "$n" = lo ] && continue
    [ -e "$path/device" ] || continue
    [ -d "$path/wireless" ] && continue
    wol_lines="${wol_lines}ExecStart=-$ETHTOOL -s $n wol g
"
done
if [ -z "$wol_lines" ]; then
    echo "   no wired network card found: Wake on LAN skipped (it works over a cable only)"
elif [ -n "$ETHTOOL" ] || [ "$DRY" = 1 ]; then
    put "$WOL_UNIT" 644 root:root <<EOF || die "could not write $WOL_UNIT"
[Unit]
Description=Lazy Link: Wake on Magic Packet for the wired network card
After=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
$wol_lines
[Install]
WantedBy=multi-user.target
EOF
    record wol_unit="$WOL_UNIT"
    run systemctl daemon-reload || die "systemctl daemon-reload failed"
    run systemctl enable --now lazy-link-wol.service || echo "   Wake on LAN unit did not start (the card may not support it)"
fi

# ---- 9. pc-info.json for the Mac ------------------------------------------------------------------
step "pc-info.json (the Mac reads it when pairing)"
nets=""
for n in $(ip -o -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' | sort -u); do
    addr=$(ip -o -4 addr show dev "$n" scope global 2>/dev/null | awk 'NR == 1 {print $4}')
    [ -n "$addr" ] || continue
    if [ -d "/sys/class/net/$n/wireless" ] || [ -d "/sys/class/net/$n/phy80211" ]; then wired=false; else wired=true; fi
    mac=$(cat "/sys/class/net/$n/address" 2>/dev/null)
    nets="${nets:+$nets,}
    {\"name\": \"$n\", \"ip\": \"${addr%/*}\", \"prefix\": ${addr#*/}, \"mac\": \"$mac\", \"wired\": $wired, \"category\": null}"
done
fingerprint=$(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null | awk '{print $2}')
if command -v nvidia-smi >/dev/null 2>&1; then
    gpu=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | paste -sd ';' - | sed 's/;/; /g')
else
    gpu="nvidia-smi not found"
fi
disks=$(disk_rows | while IFS=$'\t' read -r mnt total free; do
    printf '{"drive": "%s", "free_gb": %d}, ' "$mnt" $((free / 1073741824)); done)
disks=${disks%, }
put "$WORK/pc-info.json" 644 "$USER_NAME:$USER_NAME" <<EOF || die "could not write pc-info.json"
{
  "networks": [${nets}
  ],
  "fingerprint": "$fingerprint",
  "gpu": "$gpu",
  "fast_startup": null,
  "disks": [$disks]
}
EOF
record pc_info="$WORK/pc-info.json"
if [ "$DRY" != 1 ]; then
    echo "   addresses: $(awk -F'"' '/"ip"/ {printf "%s ", $4}' "$WORK/pc-info.json")"
fi

# ---- Done -----------------------------------------------------------------------------------------
echo
if [ "$DRY" = 1 ]; then
    echo "Dry run done: nothing was changed."
else
    echo "Setup done. Give your AI agent (or yourself) this PC's address (listed above) and this account name: $USER_NAME."
    if [ "$MIGRATE" = 1 ]; then
        echo "Upgraded from the old 'lazy' account: the models are kept. Pair this PC again from the Mac"
        echo "(the Mac still signs in as 'lazy' until then)."
    fi
    echo "To back out later: sudo bash $HERE/undo-linux.sh"
fi
