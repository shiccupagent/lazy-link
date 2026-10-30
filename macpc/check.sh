#!/bin/sh
# check.sh - job for `./check.py` on an Apple Silicon Mac: what this Mac has, and how ready it is
# after months or years switched off. Same keys as linux/check.sh and pc/check.ps1 so check.py reads
# them the same way. Runs as the hidden 'render' account (no sudo): it only reads, never changes or
# deletes anything. Writes $RENDER_OUT/check.json. No addresses, network-card IDs or user names go in it.
# The chip's GPU shares the memory, so the graphics entry has unified_memory true and vram_gb = RAM
# (check.py counts a share of it as graphics memory). Fields not applicable here are null.
# Bash-free POSIX sh and BSD tools only (macOS): no jq, no GNU options.
PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
set -u
OUT="${RENDER_OUT:?RENDER_OUT is not set (run this as a job)}/check.json"

jstr() { s=$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'); printf '"%s"' "$s"; }
jnum() { if [ -n "$1" ]; then printf '%s' "$1"; else printf 'null'; fi; }

checked=$(date +%Y-%m-%dT%H:%M:%S)

os_name=$(sw_vers -productName 2>/dev/null)
[ -n "$os_name" ] || os_name="macOS"
os_version=$(sw_vers -productVersion 2>/dev/null)
os_build=$(sw_vers -buildVersion 2>/dev/null)

# Uptime: now minus the boot time the kernel reports.
boot=$(sysctl -n kern.boottime 2>/dev/null | sed -n 's/^{ sec = \([0-9]*\),.*/\1/p')
uptime_hours=""
if [ -n "$boot" ]; then
    uptime_hours=$(awk -v n="$(date +%s)" -v b="$boot" 'BEGIN { printf "%.1f", (n - b) / 3600 }')
fi

cpu_name=$(sysctl -n machdep.cpu.brand_string 2>/dev/null)
cpu_threads=$(sysctl -n hw.ncpu 2>/dev/null)
mem_bytes=$(sysctl -n hw.memsize 2>/dev/null)
ram_gb=""
if [ -n "$mem_bytes" ]; then ram_gb=$(awk -v m="$mem_bytes" 'BEGIN { printf "%.1f", m / 1073741824 }'); fi

# Graphics: the chip's GPU. Apple Silicon has no separate graphics memory; it shares the RAM.
gpu_name=$(system_profiler SPDisplaysDataType 2>/dev/null | sed -n 's/^ *Chipset Model: *//p' | head -1)
[ -n "$gpu_name" ] || gpu_name="$cpu_name"
gpus=""
if [ -n "$gpu_name" ] && [ -n "$ram_gb" ]; then
    gpus="{\"name\": $(jstr "$gpu_name"), \"vram_gb\": $ram_gb, \"driver\": \"Metal\", \"unified_memory\": true}"
fi

# Disk: the data volume, reported as "/" so check.py matches it to the work folder.
disks=""
data_vol=/System/Volumes/Data
[ -d "$data_vol" ] || data_vol=/
line=$(df -P -k "$data_vol" 2>/dev/null | awk 'NR == 2 { print $2, $4 }')
if [ -n "$line" ]; then
    set -- $line
    total=$(awk -v k="$1" 'BEGIN { printf "%d", k / 1048576 + 0.5 }')
    free=$(awk -v k="$2" 'BEGIN { printf "%d", k / 1048576 + 0.5 }')
    disks="{\"drive\": \"/\", \"total_gb\": $total, \"free_gb\": $free}"
fi

# Last system update: the newest date in the update history (MM/DD/YY or MM/DD/YYYY), as YYYY-MM-DD.
last_update=""
d=$(softwareupdate --history 2>/dev/null | grep -Eo '[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}' | tail -1)
if [ -n "$d" ]; then
    last_update=$(printf '%s' "$d" | awk -F/ '{ y = $3; if (length(y) == 2) y = "20" y; printf "%s-%02d-%02d", y, $1, $2 }')
fi

# Network: Wi-Fi or a wired port, and whether it is up. No addresses or hardware IDs are written out.
network=$(networksetup -listallhardwareports 2>/dev/null | awk -F': ' '/^Hardware Port:/ { port = $2 } /^Device:/ { print port "|" $2 }' |
    while IFS= read -r entry; do
        port=${entry%%|*}
        dev=${entry#*|}
        [ -n "$dev" ] || continue
        kind=cable
        if printf '%s' "$port" | grep -qE 'Wi-Fi|AirPort'; then kind=wifi
        elif ! printf '%s' "$port" | grep -q 'Ethernet'; then continue
        fi
        if ifconfig "$dev" 2>/dev/null | grep -q 'status: active'; then connected=true; else connected=false; fi
        printf '{"kind": "%s", "connected": %s, "mbps": 0}\n' "$kind" "$connected"
    done | paste -sd, - | sed 's/,/, /g')

pmset_custom=$(pmset -g custom 2>/dev/null)
womp=$(printf '%s\n' "$pmset_custom" | awk '$1 == "womp" { print $2; exit }')
wake_on_magic_packet=null
case $womp in 1) wake_on_magic_packet=true ;; 0) wake_on_magic_packet=false ;; esac
sleep_min=$(printf '%s\n' "$pmset_custom" | awk '$1 == "sleep" { print $2; exit }')
case $sleep_min in ''|*[!0-9]*) sleep_min=null ;; esac

{
    printf '{\n'
    printf '  "checked": %s,\n' "$(jstr "$checked")"
    printf '  "os": {"name": %s, "version": %s, "build": %s},\n' "$(jstr "$os_name")" "$(jstr "$os_version")" "$(if [ -n "$os_build" ]; then jstr "$os_build"; else printf null; fi)"
    printf '  "uptime_hours": %s,\n' "$(jnum "$uptime_hours")"
    printf '  "cpu": {"name": %s, "threads": %s},\n' "$(if [ -n "$cpu_name" ]; then jstr "$cpu_name"; else printf null; fi)" "$(jnum "$cpu_threads")"
    printf '  "ram_gb": %s,\n' "$(jnum "$ram_gb")"
    printf '  "gpus": [%s],\n' "$gpus"
    printf '  "disks": [%s],\n' "$disks"
    printf '  "last_update": %s,\n' "$(if [ -n "$last_update" ]; then jstr "$last_update"; else printf null; fi)"
    printf '  "fast_startup": null,\n'
    printf '  "network": [%s],\n' "$network"
    printf '  "wake_on_magic_packet": %s,\n' "$wake_on_magic_packet"
    printf '  "sleep_after_min": %s\n' "$sleep_min"
    printf '}\n'
} > "$OUT"

cat "$OUT"
