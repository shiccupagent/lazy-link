#!/bin/bash
# setup-mac.sh - makes an Apple Silicon Mac a Lazy Link render box. Run once, as root, on that Mac:
#   sudo bash setup-mac.sh [--dry-run] [--key-file FILE | --key "<key line from mac-key.pub>"]
# Without a key option it reads mac-key.pub from this folder, or asks you to paste the key.
# --dry-run prints every change and changes nothing; it runs without sudo.
# Installed before 2026-10-30? Re-run this script: it renames the 'render' account and its folder to
# 'lazylink' and keeps your models. Then pair this Mac again from your other computer.
#
# What it does (undo-mac.sh reverses each step, using the state file it writes as it goes):
#   0. checks: Apple Silicon, Command Line Tools, the sshd include line, no earlier setup
#   1. account 'lazylink': standard (not an administrator), hidden from the login window, random password
#      that is never shown; the other computer's key is its only way in
#   2. sign-in rule: sshd drop-in, only 'lazylink' may sign in, with a key only; other accounts unchanged
#   3. Remote Login (the SSH server) on, if it was off
#   4. work folder ~lazylink/lazylink: jobs/, runner/ (runner.sh) and models/ (undo keeps models/)
#   5. LaunchDaemon com.lazylink.runner: runs runner.sh as 'lazylink' and restarts it if it stops
#   6. sleep rule: 'lazylink' may run 'pmset sleepnow' as root and nothing else
#   7. Wake for network access (pmset womp 1), if it was off; on Apple Silicon this needs the Ethernet port
#   8. pc-info.json, read by `pcjob pair --os mac` on your other computer
#   9. upgrade only: the old 'render' account and its leftover home are removed once the runner answers
#
# Runs under macOS's own /bin/bash (3.2): no bash 4 features.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER_USER=lazylink
LEGACY_USER=render                        # the account before 2026-10-30; an upgrade moves it
HOME_DIR="/Users/$RUNNER_USER"
WORK="$HOME_DIR/lazylink"
LEGACY_HOME="/Users/$LEGACY_USER"
LEGACY_WORK="$LEGACY_HOME/lazy-link"
STATE_DIR=/var/db/lazy-link
STATE="$STATE_DIR/setup-state"
DAEMON_LABEL=com.lazylink.runner
DAEMON=/Library/LaunchDaemons/com.lazylink.runner.plist
SSHD_LABEL=com.openssh.sshd
SSHD_PLIST=/System/Library/LaunchDaemons/ssh.plist
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-lazy-link.conf
SUDOERS=/etc/sudoers.d/lazy-link-sleep

DRY=0; KEY=""; KEY_FILE=""; MIGRATE=0; RUNNER_UP=0

usage() { awk 'NR > 1 && /^$/ { exit } NR > 1 { print }' "$0" | sed 's/^# \{0,1\}//'; }
die() {
    echo "setup-mac: $*" >&2
    [ "$DRY" = 1 ] || echo "setup-mac: stopped part-way. Run: sudo bash $HERE/undo-mac.sh" >&2
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
# Remembers a change so undo-mac.sh can reverse it.
record() {
    [ "$DRY" = 1 ] && return 0
    mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR" && echo "$1" >> "$STATE"
}
# Upgrade only: the record names the paths and account as they were, so rewrite them to the new names
# (undo then reverses the real paths) and note where the account came from.
rewrite_state() {
    [ "$DRY" = 1 ] && return 0
    sed -e "s#$LEGACY_WORK#$WORK#g" -e "s#^created_user=$LEGACY_USER\$#created_user=$RUNNER_USER#" "$STATE" > "$STATE.new" \
        && echo "migrated_from=$LEGACY_USER" >> "$STATE.new" && mv "$STATE.new" "$STATE"
}
# Number of set bits in a netmask given as hex (0xffffff00 -> 24).
prefix_of() {
    m=$(( $1 )); n=0
    while [ "$m" -gt 0 ]; do n=$(( n + (m & 1) )); m=$(( m >> 1 )); done
    echo "$n"
}

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) DRY=1 ;;
        --key) KEY=${2:-}; shift ;;
        --key-file) KEY_FILE=${2:-}; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# ---- 0. This Mac ---------------------------------------------------------------------------------
step "This Mac"
if [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then die "run it with sudo: sudo bash $0"; fi
[ "$(uname -m)" = arm64 ] || die "this needs an Apple Silicon Mac (this one is $(uname -m))"
echo "   macOS $(sw_vers -productVersion 2>/dev/null)"
xcode-select -p >/dev/null 2>&1 || die "the Command Line Tools are not installed (the imagegen and local setups need python3 from them). Run 'xcode-select --install' yourself, let it finish, then run this again."
[ -x /usr/sbin/sshd ] || die "sshd is missing from /usr/sbin"
[ -r "$HERE/runner.sh" ] || die "runner.sh is missing next to this script ($HERE)"
grep -q '^Include /etc/ssh/sshd_config.d/\*' /etc/ssh/sshd_config 2>/dev/null \
    || die "/etc/ssh/sshd_config has no 'Include /etc/ssh/sshd_config.d/*' line, so the sign-in rule would be ignored. Nothing was changed; stopping."
if [ -e "$STATE" ]; then
    if grep -qx "created_user=$RUNNER_USER" "$STATE"; then
        die "already set up (record: $STATE). Run undo-mac.sh first to start again."
    elif grep -qx "created_user=$LEGACY_USER" "$STATE"; then
        MIGRATE=1
        echo "   found the earlier setup (account '$LEGACY_USER'): this run upgrades it to '$RUNNER_USER'"
    else
        die "a setup record exists but does not name the runner account ($STATE); not touching it"
    fi
fi
if [ "$MIGRATE" = 1 ]; then
    dscl . -read "/Users/$LEGACY_USER" >/dev/null 2>&1 \
        || die "the record names '$LEGACY_USER' but that account is gone; run undo-mac.sh, then this again"
    if [ -d "$LEGACY_WORK/jobs" ] && grep -l '"state": "running"' "$LEGACY_WORK"/jobs/*/status.json >/dev/null 2>&1; then
        die "a job is running now; wait until it finishes (or is cancelled), then run this again"
    fi
    if [ -e "$LEGACY_WORK" ] && [ "$(stat -f %d "$LEGACY_WORK")" != "$(stat -f %d /Users)" ]; then
        die "$LEGACY_WORK is not on the same disk as /Users, so it cannot be moved; not touching it"
    fi
fi
if dscl . -read "/Users/$RUNNER_USER" >/dev/null 2>&1; then
    die "a user named '$RUNNER_USER' already exists and this setup did not make it; not touching it"
fi
[ -e "$HOME_DIR" ] && die "$HOME_DIR already exists and this setup did not make it; not touching it"
echo "   checks passed"

# ---- 1. The key -----------------------------------------------------------------------------------
step "The other computer's key (the only way in)"
key=""
if [ -n "$KEY" ]; then key=$KEY
elif [ -n "$KEY_FILE" ]; then key=$(cat "$KEY_FILE") || die "cannot read $KEY_FILE"
elif [ -f "$HERE/mac-key.pub" ]; then key=$(cat "$HERE/mac-key.pub")
elif [ "$MIGRATE" = 1 ] && [ -r "$LEGACY_HOME/.ssh/authorized_keys" ]; then
    key=$(head -1 "$LEGACY_HOME/.ssh/authorized_keys"); echo "   using the key already installed for '$LEGACY_USER'"
elif [ "$DRY" = 1 ]; then echo "   no key file yet: a real run asks for one (or pass --key-file)"
else
    echo "   No key file next to this script. Paste the public key of the computer you pair from"
    echo "   (one line, starts with ssh-), then press Enter:"
    read -r key < /dev/tty || die "no key given (use --key-file FILE)"
fi
if [ -n "$key" ]; then
    key=$(printf '%s\n' "$key" | head -1)
    tmpkey=$(mktemp) || die "mktemp failed"
    printf '%s\n' "$key" > "$tmpkey"
    fp=$(ssh-keygen -lf "$tmpkey" 2>/dev/null | awk '{print $2, $NF}')
    rm -f "$tmpkey"
    [ -n "$fp" ] || die "that is not an SSH public key (it should start with ssh-ed25519 or ssh-rsa)"
    echo "   key fingerprint: $fp"
fi

# ---- 2. Account -----------------------------------------------------------------------------------
if [ "$MIGRATE" = 1 ]; then
    step "Stop the old job runner ('$LEGACY_USER')"
    if [ "$DRY" = 1 ]; then
        echo "   would run: launchctl bootout system/$DAEMON_LABEL, then stop the processes of '$LEGACY_USER'"
    else
        launchctl bootout "system/$DAEMON_LABEL" 2>/dev/null
        for i in 1 2 3 4 5 6 7 8 9 10; do
            pgrep -u "$LEGACY_USER" >/dev/null 2>&1 || break
            pkill -TERM -u "$LEGACY_USER" 2>/dev/null; sleep 1
        done
        if pgrep -u "$LEGACY_USER" >/dev/null 2>&1; then
            echo "setup-mac: processes of '$LEGACY_USER' are still running (see: ps -u $LEGACY_USER). Nothing was moved; run this again once they stop." >&2
            exit 1
        fi
        echo "   old runner stopped"
    fi
fi
step "Account '$RUNNER_USER' (the job runner: standard, not an administrator, hidden, no sudo)"
if [ "$DRY" = 1 ]; then
    echo "   would run: sysadminctl -addUser $RUNNER_USER (standard account, home $HOME_DIR, shell /bin/bash, random password)"
    echo "   would run: dscl . -create /Users/$RUNNER_USER IsHidden 1"
else
    if [ "$MIGRATE" != 1 ]; then record created_user=$RUNNER_USER; record home_created=yes; fi
    PW=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)
    [ ${#PW} -eq 32 ] || die "could not make a password"
    out=$(sysadminctl -addUser "$RUNNER_USER" -fullName "Lazy Link runner" -home "$HOME_DIR" -shell /bin/bash -password "$PW" 2>&1)
    rc=$?
    if [ $rc -ne 0 ] || ! dscl . -read "/Users/$RUNNER_USER" >/dev/null 2>&1; then
        printf '%s\n' "$out" | tail -3 | sed "s/$PW/********/g"
        unset PW
        die "sysadminctl could not make the account (its last message is above)"
    fi
    unset PW
    dscl . -create "/Users/$RUNNER_USER" IsHidden 1 || die "could not hide the account from the login window"
    if dscl . -read /Groups/admin GroupMembership 2>/dev/null | tr ' ' '\n' | grep -qx "$RUNNER_USER"; then
        die "'$RUNNER_USER' became an administrator; that is not allowed. Undo with: sudo bash $HERE/undo-mac.sh"
    fi
    echo "   account made: standard user, hidden, password random and not shown"
fi
if [ "$MIGRATE" = 1 ]; then
    step "Move the old work folder (models and jobs) to '$RUNNER_USER'"
    if [ -d "$LEGACY_WORK" ]; then
        run mv "$LEGACY_WORK" "$WORK" || die "could not move $LEGACY_WORK to $WORK"
        run chown -R "$RUNNER_USER:staff" "$WORK" || die "could not hand $WORK to '$RUNNER_USER'"
        # The old heartbeat would make the new runner look alive before it has started.
        run rm -f "$WORK/runner.heartbeat"
        echo "   moved $LEGACY_WORK to $WORK"
    else
        echo "   no old work folder: nothing to move"
    fi
    if [ "$DRY" != 1 ]; then rewrite_state; fi
fi

# ---- 3. The key, in place -------------------------------------------------------------------------
step "Key for '$RUNNER_USER'"
run install -d -m 700 -o "$RUNNER_USER" -g staff "$HOME_DIR/.ssh" || die "could not create $HOME_DIR/.ssh"
if [ -n "$key" ]; then
    printf '%s\n' "$key" | put "$HOME_DIR/.ssh/authorized_keys" 600 "$RUNNER_USER:staff" || die "could not write authorized_keys"
    [ "$DRY" = 1 ] || echo "   the key is installed"
fi

# ---- 4. sshd: only 'lazylink', key only -----------------------------------------------------------
step "Sign-in rule: only '$RUNNER_USER' may sign in over SSH, with a key (other accounts lose SSH sign-in; see docs/MAC-PC.md)"
put "$SSHD_DROPIN" 644 root:wheel <<EOF || die "could not write $SSHD_DROPIN"
# Lazy Link: only the '$RUNNER_USER' account (the job runner) may sign in over SSH, with its key only.
# sshd_config includes this folder near its top, so these lines are read first and win.
AllowUsers $RUNNER_USER
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
EOF
record sshd_dropin="$SSHD_DROPIN"
if [ "$DRY" != 1 ]; then
    /usr/sbin/sshd -t || { rm -f "$SSHD_DROPIN"; die "sshd rejected the new rule (removed it again)"; }
    if /usr/sbin/sshd -T -C user=$RUNNER_USER,host=localhost,addr=127.0.0.1 2>/dev/null | grep -qx 'passwordauthentication no'; then
        echo "   checked with sshd: password sign-in is off for $RUNNER_USER"
    else
        die "sshd still allows a password for $RUNNER_USER"
    fi
    echo "   (sshd starts a fresh process for each connection, so the rule applies from the next sign-in)"
fi

# ---- 5. Remote Login (the SSH server) -------------------------------------------------------------
step "Remote Login (the SSH server) on"
if [ "$DRY" != 1 ]; then
    disabled=$(launchctl print-disabled system 2>/dev/null | grep "\"$SSHD_LABEL\"" | sed -n -E 's/.*=> *(true|false).*/\1/p' | head -1)
    if launchctl print "system/$SSHD_LABEL" >/dev/null 2>&1 && [ "$disabled" != true ]; then
        echo "   already on: left as it is"
    else
        record sshd_was_on=no
        launchctl enable "system/$SSHD_LABEL" || die "launchctl could not enable Remote Login"
        if ! launchctl print "system/$SSHD_LABEL" >/dev/null 2>&1; then
            launchctl bootstrap system "$SSHD_PLIST" || die "launchctl could not start Remote Login"
        fi
        sleep 2
        if /usr/bin/nc -z -w 3 127.0.0.1 22 2>/dev/null; then echo "   Remote Login is on (port 22 answers)"
        else die "Remote Login was started but port 22 does not answer; see: sudo launchctl print system/$SSHD_LABEL"; fi
    fi
else
    echo "   would run: launchctl enable system/$SSHD_LABEL, and start it if it is not running"
fi

# ---- 6. Work folder and runner --------------------------------------------------------------------
step "Work folder $WORK"
run install -d -m 755 -o "$RUNNER_USER" -g staff "$WORK" "$WORK/jobs" "$WORK/runner" "$WORK/models" || die "could not create $WORK"
[ "$MIGRATE" = 1 ] || record work_folder="$WORK"
run install -m 644 -o "$RUNNER_USER" -g staff "$HERE/runner.sh" "$WORK/runner/runner.sh" || die "could not copy runner.sh"
echo "   jobs/, runner/, models/ ready"

# ---- 7. The runner service ------------------------------------------------------------------------
step "Job runner (LaunchDaemon $DAEMON_LABEL)"
put "$DAEMON" 644 root:wheel <<EOF || die "could not write $DAEMON"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$DAEMON_LABEL</string>
    <key>UserName</key>
    <string>$RUNNER_USER</string>
    <key>GroupName</key>
    <string>staff</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$WORK/runner/runner.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$WORK</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$WORK/runner/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>$WORK/runner/launchd.log</string>
</dict>
</plist>
EOF
if [ "$DRY" != 1 ]; then plutil -lint "$DAEMON" >/dev/null || die "the runner's plist did not pass plutil"; fi
record daemon="$DAEMON"
run launchctl bootstrap system "$DAEMON" || die "launchctl could not start the runner (see: sudo launchctl print system/$DAEMON_LABEL)"
if [ "$DRY" != 1 ]; then
    ok=0
    for i in $(seq 1 20); do
        if [ -f "$WORK/runner.heartbeat" ] && [ $(( $(date +%s) - $(stat -f %m "$WORK/runner.heartbeat") )) -lt 30 ]; then ok=1; break; fi
        sleep 2
    done
    if [ "$ok" = 1 ]; then RUNNER_UP=1; echo "   Job runner: running"
    else echo "   Job runner: NOT running yet. See: $WORK/runner/launchd.log and $WORK/runner.log"; fi
fi

# ---- 8. Sleep rule --------------------------------------------------------------------------------
step "Sleep rule: '$RUNNER_USER' may run 'pmset sleepnow' as root, and nothing else"
echo "$RUNNER_USER ALL=(root) NOPASSWD: /usr/bin/pmset sleepnow" | put "$SUDOERS" 440 root:wheel || die "could not write $SUDOERS"
if [ "$DRY" != 1 ]; then
    visudo -cf "$SUDOERS" >/dev/null || { rm -f "$SUDOERS"; die "sudoers line rejected (removed again)"; }
    if sudo -n -l -U "$RUNNER_USER" 2>/dev/null | grep -q 'pmset sleepnow'; then echo "   checked: sudo shows the sleep rule for $RUNNER_USER"
    else die "sudo does not show the sleep rule for $RUNNER_USER (the sudoers folder may not be included in /etc/sudoers)"; fi
fi
record sudoers="$SUDOERS"

# ---- 9. Wake for network access -------------------------------------------------------------------
step "Wake for network access (pmset womp)"
prev_womp=$(pmset -g custom 2>/dev/null | awk '$1 == "womp" { print $2; exit }')
case $prev_womp in
    1) echo "   already on: left as it is" ;;
    *)
        run pmset -a womp 1 || die "could not turn on Wake for network access"
        record womp_prev="${prev_womp:-0}"
        echo "   turned on (it was ${prev_womp:-unknown}). On Apple Silicon, wake works over the Ethernet port only."
        ;;
esac

# ---- 10. pc-info.json for the other computer ------------------------------------------------------
step "pc-info.json (pcjob pair reads it on your other computer)"
nets=""
for dev in $(networksetup -listallhardwareports 2>/dev/null | awk -F': ' '/^Hardware Port:/ { port = $2 } /^Device:/ { print port "|" $2 }' |
    while IFS= read -r entry; do
        port=${entry%%|*}; dev=${entry#*|}
        if printf '%s' "$port" | grep -qE 'Wi-Fi|AirPort|Ethernet'; then echo "$dev"; fi
    done); do
    addr=$(ipconfig getifaddr "$dev" 2>/dev/null)
    [ -n "$addr" ] || continue
    mask=$(ifconfig "$dev" 2>/dev/null | awk '/inet / { print $4; exit }')
    prefix=$(prefix_of "${mask:-0xffffff00}")
    mac=$(ifconfig "$dev" 2>/dev/null | awk '/ether / { print $2; exit }')
    port=$(networksetup -listallhardwareports 2>/dev/null | awk -v d="$dev" -F': ' '/^Hardware Port:/ { port = $2 } /^Device:/ && $2 == d { print port; exit }')
    if printf '%s' "$port" | grep -q 'Ethernet'; then wired=true; else wired=false; fi
    nets="${nets:+$nets,}
    {\"name\": \"$dev\", \"ip\": \"$addr\", \"prefix\": $prefix, \"mac\": \"$mac\", \"wired\": $wired, \"category\": null}"
done
fingerprint=$(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null | awk '{print $2}')
gpu_name=$(system_profiler SPDisplaysDataType 2>/dev/null | sed -n 's/^ *Chipset Model: *//p' | head -1)
gpu="${gpu_name:-Apple GPU} (shares the Mac's memory)"
free_gb=$(df -P -k /System/Volumes/Data 2>/dev/null | awk 'NR == 2 { printf "%d", $4 / 1048576 }')
put "$WORK/pc-info.json" 644 "$RUNNER_USER:staff" <<EOF || die "could not write pc-info.json"
{
  "networks": [${nets}
  ],
  "fingerprint": "$fingerprint",
  "gpu": "$gpu",
  "fast_startup": null,
  "disks": [{"drive": "/", "free_gb": ${free_gb:-0}}]
}
EOF
record pc_info="$WORK/pc-info.json"
if [ "$DRY" != 1 ]; then
    echo "   addresses: $(awk -F'"' '/"ip"/ {printf "%s ", $4}' "$WORK/pc-info.json")"
fi

# ---- 11. The old account (upgrade only) ------------------------------------------------------------
if [ "$MIGRATE" = 1 ]; then
    step "Remove the old '$LEGACY_USER' account and its leftover home"
    if [ "$DRY" = 1 ]; then
        echo "   would run: sysadminctl -deleteUser $LEGACY_USER -keepHome, then rm -rf $LEGACY_HOME"
    elif [ "$RUNNER_UP" != 1 ]; then
        echo "   kept: the runner did not answer, so the old account stays for now."
        echo "   Look at $WORK/runner/launchd.log and $WORK/runner.log. undo-mac.sh removes the old account too."
    else
        sysadminctl -deleteUser "$LEGACY_USER" -keepHome >/dev/null 2>&1 || die "could not remove the old account '$LEGACY_USER' (its home is kept)"
        [ "$LEGACY_HOME" = "/Users/$LEGACY_USER" ] && rm -rf "$LEGACY_HOME"
        echo "   removed: account '$LEGACY_USER' and $LEGACY_HOME"
    fi
fi

# ---- Done -----------------------------------------------------------------------------------------
echo
if [ "$DRY" = 1 ]; then
    echo "Dry run done: nothing was changed."
elif [ "$MIGRATE" = 1 ] && [ "$RUNNER_UP" != 1 ]; then
    echo "Upgrade NOT finished: the job runner did not answer (see above). Your models are in $WORK/models."
    exit 1
else
    if [ "$MIGRATE" = 1 ]; then echo "Upgrade done: the 'render' account is now '$RUNNER_USER'. Your models are kept."; fi
    echo "Setup done. Pair from your other computer with this Mac's address (listed above) and the account name: $RUNNER_USER."
    if [ "$MIGRATE" = 1 ]; then echo "Pair this Mac again from your other computer (it still signs in as '$LEGACY_USER' until then)."; fi
    echo "To back out later: sudo bash $HERE/undo-mac.sh"
fi
