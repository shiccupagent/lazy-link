#!/bin/bash
# undo-mac.sh - backs out setup-mac.sh, using the state file it wrote, and then checks each step is gone.
#   sudo bash undo-mac.sh [--dry-run] [--remove-work-folder]
# Keeps ~lazylink/lazylink/models (the downloaded models, often tens of GB) unless --remove-work-folder
# is given. Also removes what an install from before 2026-10-30 left: the 'render' account and ~render/lazy-link.
# Leaves Command Line Tools and Homebrew alone. Remote Login is turned off only if setup turned it on,
# and Wake for network access goes back to the value it had before setup.
# --dry-run runs without sudo and changes nothing.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER_USER=lazylink
HOME_DIR="/Users/$RUNNER_USER"
WORK="$HOME_DIR/lazylink"
LEGACY_USER=render
LEGACY_HOME="/Users/$LEGACY_USER"
LEGACY_WORK="$LEGACY_HOME/lazy-link"
STATE_DIR=/var/db/lazy-link
STATE="$STATE_DIR/setup-state"
DAEMON_LABEL=com.lazylink.runner
DAEMON=/Library/LaunchDaemons/com.lazylink.runner.plist
SSHD_LABEL=com.openssh.sshd
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-lazy-link.conf
SUDOERS=/etc/sudoers.d/lazy-link-sleep

DRY=0; REMOVE_WORK=0
FAILS=0

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) DRY=1 ;;
        --remove-work-folder) REMOVE_WORK=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "undo-mac: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then echo "undo-mac: run it with sudo" >&2; exit 1; fi

step() { echo; echo "== $*"; }
run() { if [ "$DRY" = 1 ]; then echo "   would run: $*"; return 0; fi; "$@"; }
fail() { echo "   FAILED: $*"; FAILS=$((FAILS + 1)); }

# What setup recorded (empty if there is no record); has_record matches a whole line.
recorded() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | head -1; }
has_record() { [ -f "$STATE" ] && grep -qx -- "$1" "$STATE"; }

if [ -f "$STATE" ]; then
    echo "Setup record: $STATE"
else
    echo "No setup record found ($STATE). Removing the known Lazy Link pieces if they are there."
fi

# The accounts this PC's record says setup made: the runner account, and 'render' if an install from
# before 2026-10-30 (or an upgrade from it) made one.
OWNED=$(recorded created_user)
ACCOUNTS=""
[ -n "$OWNED" ] && ACCOUNTS="$OWNED"
LEGACY_OURS=0
if has_record created_user=$LEGACY_USER || has_record migrated_from=$LEGACY_USER; then LEGACY_OURS=1; fi
if [ "$LEGACY_OURS" = 1 ] && [ "$OWNED" != "$LEGACY_USER" ]; then ACCOUNTS="${ACCOUNTS:+$ACCOUNTS }$LEGACY_USER"; fi
# Whether setup made the current account's home folder (and so may remove it with --remove-work-folder).
MADE_HOME=0
if [ "$OWNED" = "$RUNNER_USER" ] && has_record home_created=yes; then MADE_HOME=1; fi

# ---- 1. Stop the runner and the accounts' processes -----------------------------------------------
step "Stop the job runner"
if [ -f "$DAEMON" ] || launchctl print "system/$DAEMON_LABEL" >/dev/null 2>&1; then
    run launchctl bootout system "$DAEMON" 2>/dev/null || run launchctl bootout "system/$DAEMON_LABEL" 2>/dev/null \
        || fail "could not stop $DAEMON_LABEL (see: sudo launchctl print system/$DAEMON_LABEL)"
    echo "   runner stopped"
else
    echo "   not running"
fi
for U in $ACCOUNTS; do
    if dscl . -read "/Users/$U" >/dev/null 2>&1; then
        run pkill -TERM -u "$U" 2>/dev/null
        if [ "$DRY" != 1 ]; then
            for i in 1 2 3 4 5; do pgrep -u "$U" >/dev/null 2>&1 || break; sleep 1; done
        fi
        run pkill -KILL -u "$U" 2>/dev/null
    fi
done

# ---- 2. Remove the daemon file, sleep rule and sign-in rule; put Remote Login back -------------
step "Remove the runner, sleep rule and sign-in rule"
if [ -e "$DAEMON" ]; then run rm -f "$DAEMON" || fail "could not remove $DAEMON"; echo "   removed $DAEMON"; fi
if [ -e "$SUDOERS" ]; then run rm -f "$SUDOERS" || fail "could not remove $SUDOERS"; echo "   removed $SUDOERS"; fi
if [ -e "$SSHD_DROPIN" ]; then
    run rm -f "$SSHD_DROPIN" || fail "could not remove $SSHD_DROPIN"
    echo "   removed $SSHD_DROPIN"
    if [ "$DRY" != 1 ]; then
        /usr/sbin/sshd -t && echo "   sshd accepts the config without the rule" || fail "sshd rejected the config; check: sudo sshd -t"
    fi
fi
if [ "$(recorded sshd_was_on)" = no ]; then
    run launchctl bootout system "/System/Library/LaunchDaemons/ssh.plist" 2>/dev/null
    run launchctl disable "system/$SSHD_LABEL" || fail "could not turn Remote Login back off"
    echo "   Remote Login turned back off (it was off before setup)"
else
    echo "   Remote Login: left as it is (setup did not turn it on)"
fi
prev_womp=$(recorded womp_prev)
if [ -n "$prev_womp" ]; then
    run pmset -a womp "$prev_womp" || fail "could not set Wake for network access back to $prev_womp"
    echo "   Wake for network access set back to $prev_womp"
fi

# ---- 3. Remove the accounts ----------------------------------------------------------------------
step "Remove the runner account(s): ${ACCOUNTS:-none in the record} (home folders are kept, see below)"
if [ -z "$ACCOUNTS" ]; then
    echo "   not made by setup (no record): left alone"
fi
for U in $ACCOUNTS; do
    if dscl . -read "/Users/$U" >/dev/null 2>&1; then
        run sysadminctl -deleteUser "$U" -keepHome || fail "sysadminctl could not delete $U (processes still running? see: ps -u $U)"
    else
        echo "   '$U': already gone"
    fi
done
if [ "$DRY" != 1 ]; then
    # the key must not outlive the account; the kept home folders may still hold it
    [ -d "$HOME_DIR/.ssh" ] && rm -rf "$HOME_DIR/.ssh" && echo "   removed $HOME_DIR/.ssh (the key)"
    if [ "$LEGACY_OURS" = 1 ] && [ -d "$LEGACY_HOME/.ssh" ]; then rm -rf "$LEGACY_HOME/.ssh" && echo "   removed $LEGACY_HOME/.ssh (the key)"; fi
fi

# ---- 4. Work folders (the current one, and the old ~render one if an earlier install left it) -----
# $1 = work folder, $2 = its home folder, $3 = 1 if setup made that home (then --remove-work-folder removes it)
clear_work() {
    F=$1; H=$2; MADE=$3
    step "Work folder $F"
    if [ ! -d "$F" ]; then echo "   not present"; return 0; fi
    if [ "$REMOVE_WORK" = 1 ]; then
        if [ "$MADE" = 1 ]; then
            run rm -rf "$H" || fail "could not remove $H"
            echo "   removed $H (the account's home folder, with models)"
        else
            run rm -rf "$F" || fail "could not remove $F"
            echo "   removed $F (with models)"
        fi
    else
        for p in jobs runner pc-info.json runner.log runner.log.1 runner.lockdir runner.heartbeat; do
            run rm -rf "$F/$p"
        done
        if [ -d "$F/models" ]; then
            run chown -R root:wheel "$F/models"
            echo "   kept the models: $F/models (add --remove-work-folder to delete them too)"
        fi
    fi
}
clear_work "$WORK" "$HOME_DIR" "$MADE_HOME"
if [ "$LEGACY_OURS" = 1 ]; then
    MADE_LEGACY_HOME=0
    has_record home_created=yes && MADE_LEGACY_HOME=1
    clear_work "$LEGACY_WORK" "$LEGACY_HOME" "$MADE_LEGACY_HOME"
fi

# ---- 5. The record itself -------------------------------------------------------------------------
step "Setup record"
if [ -f "$STATE" ]; then run rm -f "$STATE"; echo "   removed $STATE"; fi
run rmdir "$STATE_DIR" 2>/dev/null

# ---- 6. Check: proof each piece is gone -----------------------------------------------------------
if [ "$DRY" != 1 ]; then
    step "Check"
    for U in $ACCOUNTS; do
        dscl . -read "/Users/$U" >/dev/null 2>&1 && fail "account $U still exists" || echo "   account $U: gone"
    done
    launchctl print "system/$DAEMON_LABEL" >/dev/null 2>&1 && fail "runner still loaded" || echo "   runner: gone"
    [ -e "$DAEMON" ] && fail "$DAEMON still there" || echo "   runner file: gone"
    [ -e "$SSHD_DROPIN" ] && fail "$SSHD_DROPIN still there" || echo "   sign-in rule file: gone"
    [ -e "$SUDOERS" ] && fail "$SUDOERS still there" || echo "   sleep rule file: gone"
    [ -e "$STATE" ] && fail "$STATE still there" || echo "   setup record: gone"
    if [ "$REMOVE_WORK" = 1 ]; then
        [ -e "$WORK" ] && fail "$WORK still there" || echo "   work folder: gone"
        [ "$LEGACY_OURS" = 1 ] && { [ -e "$LEGACY_WORK" ] && fail "$LEGACY_WORK still there" || echo "   old work folder: gone"; }
    else
        [ -e "$WORK/jobs" ] && fail "$WORK/jobs still there" || echo "   jobs folder: gone"
        [ -d "$WORK/models" ] && echo "   models kept: $WORK/models"
        [ "$LEGACY_OURS" = 1 ] && [ -d "$LEGACY_WORK/models" ] && echo "   models kept: $LEGACY_WORK/models"
    fi
fi

echo
if [ "$DRY" = 1 ]; then echo "Dry run done: nothing was changed."
elif [ "$FAILS" -eq 0 ]; then echo "Undo done. Nothing from setup-mac.sh is left (except models, if kept)."
else echo "Undo finished with $FAILS problem(s) above."; exit 1
fi
