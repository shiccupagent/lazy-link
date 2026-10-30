#!/usr/bin/env bash
# runner.sh - the Linux job runner. Same protocol as pc/runner.ps1 (the Windows one).
# setup-linux.sh copies it to /srv/lazy-link/runner/runner.sh; the lazy-link-runner systemd
# service runs it as the hidden 'lazy' user (Restart=always), outside any SSH session, so a
# dropped link never kills a job.
#
# A job is a folder /srv/lazy-link/jobs/<id>/ that the Mac fills (mac/pcjob):
#   in.tar    the job folder from the Mac; must contain job.sh
#   job.json  {"timeout_s": 3600}
#   ready     written last; the runner only picks up folders that have it
# The runner unpacks in.tar into work/, runs job.sh there (env RENDER_OUT = the out/ folder,
# RENDER_ROOT = /srv/lazy-link, FFMPEG and BLENDER when found), keeps the machine awake while it
# runs, ends the job's whole process group on timeout or when a 'cancel' file appears, then packs
# out/ into out.tar. status.json is the state: queued -> running -> done | failed | timeout | cancelled.
# One job at a time, oldest first. When this file changes on disk the runner exits and systemd
# starts the new copy (that is how `pcjob push-runner` updates it).
#
# Run by `bash`, so the file needs no execute bit (pcjob pushes it with scp).

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOBS="$ROOT/jobs"
LOG="$ROOT/runner.log"
BEAT="$ROOT/runner.heartbeat"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# Tools a job may use, found once at start (the job account can only use what it can read).
export RENDER_ROOT="$ROOT"
FFMPEG="$(command -v ffmpeg 2>/dev/null)"; [ -n "$FFMPEG" ] && export FFMPEG
BLENDER="$(command -v blender 2>/dev/null)"; [ -n "$BLENDER" ] && export BLENDER

log() {
    if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ]; then mv -f "$LOG" "$LOG.1"; fi
    printf '%s %s\n' "$(date -Is | cut -c1-19)" "$*" >> "$LOG"
}

# JSON string for the status file: escape backslash and quote, flatten line breaks.
jstr() {
    local s=$1
    s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\r'/ }; s=${s//$'\n'/ }; s=${s//$'\t'/ }
    printf '"%s"' "$s"
}

# Job state, set by run_job before each write_status call.
ST_ID=""; ST_STARTED=""; ST_TIMEOUT=3600; ST_PID=""; ST_EXIT=""; ST_SECONDS=""; ST_ENDED=""

write_status() {  # dir state [error]
    local dir=$1 state=$2 err=${3:-} tmp
    tmp="$dir/status.tmp"
    {
        printf '{\n'
        printf '  "id": %s,\n' "$(jstr "$ST_ID")"
        printf '  "state": %s,\n' "$(jstr "$state")"
        printf '  "started": %s,\n' "$(jstr "$ST_STARTED")"
        printf '  "timeout_s": %s,\n' "$ST_TIMEOUT"
        printf '  "pid": %s,\n' "${ST_PID:-null}"
        printf '  "exit": %s,\n' "${ST_EXIT:-null}"
        printf '  "seconds": %s,\n' "${ST_SECONDS:-null}"
        if [ -n "$ST_ENDED" ]; then printf '  "ended": %s,\n' "$(jstr "$ST_ENDED")"; else printf '  "ended": null,\n'; fi
        if [ -n "$err" ]; then printf '  "error": %s,\n' "$(jstr "$err")"; fi
        printf '  "updated": %s\n' "$(jstr "$(date -Is)")"
        printf '}\n'
    } > "$tmp" && mv -f "$tmp" "$dir/status.json"
}

read_timeout() {  # dir -> seconds (default 3600)
    local t
    t=$(sed -n 's/.*"timeout_s"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$1/job.json" 2>/dev/null | head -1)
    echo "${t:-3600}"
}

# Ends a process and everything it started: its process group (the job runs under setsid, so its
# group id is its pid) and its child processes. A job left running in the background is ended the
# same way when the job finishes, like closing the Windows job object.
kill_tree() {  # pid
    local child
    for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
    kill -TERM "$1" 2>/dev/null
}
stop_job() {  # pid
    local pid=$1 n=0
    kill -TERM -- "-$pid" 2>/dev/null
    kill_tree "$pid"
    while [ "$n" -lt 5 ] && kill -0 "$pid" 2>/dev/null; do sleep 2; n=$((n + 1)); done
    kill -KILL -- "-$pid" 2>/dev/null
    kill -KILL "$pid" 2>/dev/null
}

# Keeps the machine from sleeping while a job runs (systemd inhibitor; the Windows runner calls
# SetThreadExecutionState for the same thing). Missing systemd-inhibit is logged, not fatal.
INHIBIT_PID=""
start_awake() {  # job id
    systemd-inhibit --what=sleep:idle --who="Lazy Link" --why="render job $1" --mode=block sleep infinity \
        >/dev/null 2>&1 &
    INHIBIT_PID=$!
}
stop_awake() {
    if [ -n "$INHIBIT_PID" ]; then kill "$INHIBIT_PID" 2>/dev/null; wait "$INHIBIT_PID" 2>/dev/null; fi
    INHIBIT_PID=""
}

run_job() {  # dir
    local dir=$1 id end err rc pid state t0 work out
    id=$(basename "$dir")
    ST_ID=$id; ST_STARTED=$(date -Is); ST_TIMEOUT=$(read_timeout "$dir")
    ST_PID=""; ST_EXIT=""; ST_SECONDS=""; ST_ENDED=""
    write_status "$dir" running
    log "start $id (timeout ${ST_TIMEOUT}s)"
    work="$dir/work"; out="$dir/out"
    mkdir -p "$work" "$out"
    t0=$SECONDS; end=done; err=""; pid=""

    if ! tar -xf "$dir/in.tar" -C "$work" 2>>"$dir/err.txt"; then
        err="tar unpack failed"; end=failed
    elif [ ! -f "$work/job.sh" ]; then
        err="job.sh missing from in.tar"; end=failed
    else
        export RENDER_JOB="$id" RENDER_OUT="$out"
        # Own process group (setsid): the job and everything it starts can be ended together.
        (cd "$work" && exec setsid bash job.sh) > "$dir/log.txt" 2> "$dir/err.txt" < /dev/null &
        pid=$!
        ST_PID=$pid
        write_status "$dir" running
        start_awake "$id"
        while true; do
            state=$(ps -o stat= -p "$pid" 2>/dev/null)
            [ -z "$state" ] && break
            case $state in Z*) break ;; esac
            date -Is > "$BEAT"
            if [ -f "$dir/cancel" ]; then end=cancelled; stop_job "$pid"; break; fi
            if [ $((SECONDS - t0)) -gt "$ST_TIMEOUT" ]; then end=timeout; stop_job "$pid"; break; fi
            sleep 2
        done
        wait "$pid" 2>/dev/null; rc=$?
        stop_awake
        kill -KILL -- "-$pid" 2>/dev/null
        ST_EXIT=$rc
        if [ "$end" = done ] && [ "$rc" -ne 0 ]; then end=failed; fi
    fi

    if ! tar -cf "$dir/out.tar" -C "$out" . 2>>"$dir/err.txt"; then
        err="${err:+$err; }packing out.tar failed"
    fi
    ST_SECONDS=$((SECONDS - t0)); ST_ENDED=$(date -Is)
    write_status "$dir" "$end" "$err"
    log "end $id $end exit=${ST_EXIT:-none} ${ST_SECONDS}s"
}

# The oldest folder that has 'ready' and no status.json yet, or nothing.
next_job() {
    local mtime d
    find "$JOBS" -mindepth 2 -maxdepth 2 -name ready -printf '%T@\t%h\n' 2>/dev/null | sort -n |
    while IFS=$'\t' read -r mtime d; do
        if [ ! -f "$d/status.json" ]; then echo "$d"; break; fi
    done
}

main() {
    mkdir -p "$JOBS"
    # One runner at a time: the lock stays held for this runner's whole life.
    exec 9> "$ROOT/runner.lock"
    if ! flock -n 9; then log "another runner holds runner.lock; pid $$ exits"; sleep 30; exit 0; fi
    log "runner up (pid $$)"

    # A job left 'running' by a crash or reboot is marked failed, never silently rerun.
    local d sf
    for d in "$JOBS"/*/; do
        d=${d%/}; sf="$d/status.json"
        [ -f "$sf" ] || continue
        if grep -q '"state": "running"' "$sf"; then
            ST_ID=$(basename "$d")
            ST_STARTED=$(sed -n 's/.*"started": *"\([^"]*\)".*/\1/p' "$sf" | head -1)
            ST_TIMEOUT=$(sed -n 's/.*"timeout_s": *\([0-9][0-9]*\).*/\1/p' "$sf" | head -1)
            ST_TIMEOUT=${ST_TIMEOUT:-3600}; ST_PID=""; ST_EXIT=""; ST_SECONDS=""; ST_ENDED=""
            write_status "$d" failed "runner restarted while this job was running"
            log "marked $ST_ID failed (runner restarted)"
        fi
    done

    local SELF_HASH next
    SELF_HASH=$(sha256sum "$SELF" | cut -d' ' -f1)
    while true; do
        date -Is > "$BEAT"
        if [ "$(sha256sum "$SELF" | cut -d' ' -f1)" != "$SELF_HASH" ]; then
            log "runner.sh changed; restarting"; exit 0
        fi
        next=$(next_job)
        if [ -z "$next" ]; then sleep 2; continue; fi
        run_job "$next" || log "runner error on $(basename "$next")"
    done
}

main "$@"
