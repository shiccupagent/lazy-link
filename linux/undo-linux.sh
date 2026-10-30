#!/usr/bin/env bash
# undo-linux.sh - backs out setup-linux.sh, using the state file it wrote, and then checks each step is gone.
#   sudo bash undo-linux.sh [--dry-run] [--remove-work-folder]
# Keeps /srv/lazy-link/models (the downloaded models, often tens of GB) unless --remove-work-folder is given.
# Leaves installed packages (ethtool, ufw, openssh-server) alone: they are normal system tools.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK=/srv/lazy-link
STATE_DIR=/var/lib/lazy-link
STATE="$STATE_DIR/setup-state"
UNIT=/etc/systemd/system/lazy-link-runner.service
WOL_UNIT=/etc/systemd/system/lazy-link-wol.service
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-lazy-link.conf
SUDOERS=/etc/sudoers.d/lazy-link-suspend

DRY=0; REMOVE_WORK=0
FAILS=0

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) DRY=1 ;;
        --remove-work-folder) REMOVE_WORK=1 ;;
        -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "undo-linux: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then echo "undo-linux: run it with sudo" >&2; exit 1; fi

step() { echo; echo "== $*"; }
run() { if [ "$DRY" = 1 ]; then echo "   would run: $*"; return 0; fi; "$@"; }
fail() { echo "   FAILED: $*"; FAILS=$((FAILS + 1)); }

# What setup recorded (empty if there is no record).
recorded() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | head -1; }
has_record() { [ -f "$STATE" ] && grep -q "^$1" "$STATE"; }

if [ -f "$STATE" ]; then
    echo "Setup record: $STATE"
else
    echo "No setup record found ($STATE). Removing the known Lazy Link pieces if they are there."
fi

# ---- 1. Stop the runner and the account's processes ----------------------------------------------
step "Stop the job runner"
if [ -f "$UNIT" ] || systemctl cat lazy-link-runner.service >/dev/null 2>&1; then
    run systemctl disable --now lazy-link-runner.service || fail "could not stop lazy-link-runner"
else
    echo "   not installed"
fi
if has_record created_user=lazy && id lazy >/dev/null 2>&1; then
    run pkill -TERM -u lazy 2>/dev/null
    for i in 1 2 3 4 5; do pgrep -u lazy >/dev/null 2>&1 || break; sleep 1; done
    run pkill -KILL -u lazy 2>/dev/null
fi

# ---- 2. Remove the unit files, sudo rule, sshd rule, firewall rule --------------------------------
step "Remove the service, sudo rule, sign-in rule and firewall rule"
if [ -f "$WOL_UNIT" ] || systemctl cat lazy-link-wol.service >/dev/null 2>&1; then
    run systemctl disable --now lazy-link-wol.service 2>/dev/null
fi
for f in "$UNIT" "$WOL_UNIT"; do
    if [ -e "$f" ]; then run rm -f "$f" || fail "could not remove $f"; echo "   removed $f"; fi
done
run systemctl daemon-reload
if [ -e "$SUDOERS" ]; then run rm -f "$SUDOERS" || fail "could not remove $SUDOERS"; echo "   removed $SUDOERS"; fi
if [ -e "$SSHD_DROPIN" ]; then
    run rm -f "$SSHD_DROPIN" || fail "could not remove $SSHD_DROPIN"
    echo "   removed $SSHD_DROPIN"
    if [ "$DRY" != 1 ]; then
        if /usr/sbin/sshd -t && (systemctl reload ssh || systemctl reload sshd); then echo "   sshd reloaded"
        else fail "sshd did not accept the change; check: sudo sshd -t"; fi
    fi
fi
subnet=$(recorded ufw_subnet)
if [ -n "$subnet" ]; then
    if command -v ufw >/dev/null 2>&1; then
        run ufw --force delete allow from "$subnet" to any port 22 proto tcp >/dev/null 2>&1 \
            && echo "   firewall rule for $subnet removed" \
            || echo "   no firewall rule for $subnet (already gone)"
    fi
fi

# ---- 3. Remove the account -----------------------------------------------------------------------
step "Remove the 'lazy' account"
if has_record created_user=lazy; then
    if id lazy >/dev/null 2>&1; then
        run userdel -r lazy || fail "userdel lazy failed (processes still running? see: ps -u lazy)"
    else
        echo "   already gone"
    fi
else
    echo "   not made by setup (no record): left alone"
fi

# ---- 4. Work folder -------------------------------------------------------------------------------
step "Work folder $WORK"
if [ -d "$WORK" ]; then
    if [ "$REMOVE_WORK" = 1 ]; then
        run rm -rf "$WORK" || fail "could not remove $WORK"
        echo "   removed $WORK (with models)"
    else
        for p in jobs runner pc-info.json runner.log runner.log.1 runner.lock runner.heartbeat; do
            run rm -rf "$WORK/$p"
        done
        if [ -d "$WORK/models" ]; then
            run chown -R root:root "$WORK/models"
            echo "   kept the models: $WORK/models (add --remove-work-folder to delete them too)"
        fi
    fi
else
    echo "   not present"
fi

# ---- 5. The record itself -------------------------------------------------------------------------
step "Setup record"
if [ -f "$STATE" ]; then run rm -f "$STATE"; echo "   removed $STATE"; fi
run rmdir "$STATE_DIR" 2>/dev/null

# ---- 6. Check: proof each piece is gone -----------------------------------------------------------
if [ "$DRY" != 1 ]; then
    step "Check"
    id lazy >/dev/null 2>&1 && fail "user lazy still exists" || echo "   user lazy: gone"
    systemctl cat lazy-link-runner.service >/dev/null 2>&1 && fail "runner service still defined" || echo "   runner service: gone"
    [ -e "$UNIT" ] && fail "$UNIT still there" || echo "   runner unit file: gone"
    [ -e "$SSHD_DROPIN" ] && fail "$SSHD_DROPIN still there" || echo "   sshd rule file: gone"
    [ -e "$SUDOERS" ] && fail "$SUDOERS still there" || echo "   sudo rule file: gone"
    [ -e "$STATE" ] && fail "$STATE still there" || echo "   setup record: gone"
    if [ "$REMOVE_WORK" = 1 ]; then
        [ -e "$WORK" ] && fail "$WORK still there" || echo "   work folder: gone"
    else
        [ -e "$WORK/jobs" ] && fail "$WORK/jobs still there" || echo "   jobs folder: gone"
        [ -d "$WORK/models" ] && echo "   models kept: $WORK/models"
    fi
    if command -v ufw >/dev/null 2>&1 && [ -n "$subnet" ]; then
        ufw status 2>/dev/null | grep -q "22/tcp.*ALLOW.*$subnet" && fail "ufw rule for $subnet still there" || echo "   firewall rule: gone"
    fi
fi

echo
if [ "$DRY" = 1 ]; then echo "Dry run done: nothing was changed."
elif [ "$FAILS" -eq 0 ]; then echo "Undo done. Nothing from setup-linux.sh is left (except models, if kept)."
else echo "Undo finished with $FAILS problem(s) above."; exit 1
fi
