#!/usr/bin/env bash
# check.sh - job for `./check.py` on a Linux PC: what this PC has, and how ready it is after months
# or years switched off. Same keys as pc/check.ps1 so check.py reads both the same way.
# Runs as the hidden 'lazy' account (no sudo, no admin): it only reads, never changes or deletes
# anything. Writes $RENDER_OUT/check.json. No addresses, network-card IDs or user names go in it.
# Fields Windows-only or not applicable here are null: os.build, fast_startup, sleep_after_min.
set -u
OUT="${RENDER_OUT:?RENDER_OUT is not set (run this as a job)}/check.json"

jstr() { local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }
jnum() { if [ -n "$1" ]; then printf '%s' "$1"; else printf 'null'; fi; }
trim() { local s=$1; s=${s#"${s%%[![:space:]]*}"}; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

checked=$(date +%Y-%m-%dT%H:%M:%S)

os_name=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-Linux}")
os_version=$(. /etc/os-release 2>/dev/null; printf '%s' "${VERSION_ID:-}")

uptime_hours=$(awk '{printf "%.1f", $1/3600}' /proc/uptime 2>/dev/null)

cpu_name=$(lscpu 2>/dev/null | sed -n 's/^Model name: *//p' | head -1)
cpu_threads=$(nproc 2>/dev/null)
ram_gb=$(awk '/^MemTotal:/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null)

# NVIDIA cards: nvidia-smi comes with the driver.
gpus=""
if command -v nvidia-smi >/dev/null 2>&1; then
    while IFS=, read -r g_name g_mem g_drv; do
        g_name=$(trim "$g_name"); g_mem=$(trim "$g_mem"); g_drv=$(trim "$g_drv")
        [ -z "$g_name" ] && continue
        vram=$(awk -v m="$g_mem" 'BEGIN {printf "%.1f", m/1024}')
        gpus="${gpus:+$gpus, }{\"name\": $(jstr "$g_name"), \"vram_gb\": $vram, \"driver\": $(jstr "$g_drv")}"
    done < <(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null)
fi

# Disks: every real mounted filesystem (mount point + free space), like the Windows fixed drives.
disks=$(df -P -B1 -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs -x ramfs -x autofs \
           -x fuse.lxcfs -x nsfs -x vfat -x 9p -x virtiofs -x iso9660 -x fuse.sshfs -x nfs -x nfs4 -x cifs 2>/dev/null | awk 'NR > 1 && $6 !~ /^\/(boot|snap|run)/ {
        printf "%s\t%d\t%d\n", $6, $2/1073741824 + 0.5, $4/1073741824 + 0.5 }' |
    while IFS=$'\t' read -r mnt total free; do
        printf '{"drive": %s, "total_gb": %s, "free_gb": %s}, ' "$(jstr "$mnt")" "$total" "$free"
    done)
disks=${disks%, }

# Last system update installed (apt history; the Windows check reads the Windows Update history).
last_update=$(grep '^End-Date:' /var/log/apt/history.log 2>/dev/null | tail -1 | awk '{print $2}')

# Network: real (not virtual) interfaces, like the Windows check's network cards.
network=""
wol_seen=0; wol_on=0
for path in /sys/class/net/*; do
    name=$(basename "$path")
    [ "$name" = lo ] && continue
    [ -e "$path/device" ] || continue
    if [ -d "$path/wireless" ] || [ -d "$path/phy80211" ]; then kind=wifi; else kind=cable; fi
    state=$(cat "$path/operstate" 2>/dev/null)
    if [ "$state" = up ]; then connected=true; else connected=false; fi
    speed=$(cat "$path/speed" 2>/dev/null); case $speed in ''|*[!0-9]*) speed=0 ;; esac
    if [ "$connected" = false ]; then speed=0; fi
    network="${network:+$network, }{\"kind\": \"$kind\", \"connected\": $connected, \"mbps\": $speed}"
    if [ "$kind" = cable ] && command -v ethtool >/dev/null 2>&1; then
        wol=$(ethtool "$name" 2>/dev/null | sed -n 's/^[[:space:]]*Wake-on: *//p' | head -1)
        if [ -n "$wol" ]; then
            wol_seen=1
            case $wol in *g*) wol_on=1 ;; esac
        fi
    fi
done

wake_on_magic_packet=null
if [ "$wol_seen" = 1 ]; then if [ "$wol_on" = 1 ]; then wake_on_magic_packet=true; else wake_on_magic_packet=false; fi; fi

{
    printf '{\n'
    printf '  "checked": %s,\n' "$(jstr "$checked")"
    printf '  "os": {"name": %s, "version": %s, "build": null},\n' "$(jstr "$os_name")" "$(jstr "$os_version")"
    printf '  "uptime_hours": %s,\n' "$(jnum "$uptime_hours")"
    printf '  "cpu": {"name": %s, "threads": %s},\n' "$(if [ -n "$cpu_name" ]; then jstr "$cpu_name"; else printf null; fi)" "$(jnum "$cpu_threads")"
    printf '  "ram_gb": %s,\n' "$(jnum "$ram_gb")"
    printf '  "gpus": [%s],\n' "$gpus"
    printf '  "disks": [%s],\n' "$disks"
    printf '  "last_update": %s,\n' "$(if [ -n "$last_update" ]; then jstr "$last_update"; else printf null; fi)"
    printf '  "fast_startup": null,\n'
    printf '  "network": [%s],\n' "$network"
    printf '  "wake_on_magic_packet": %s,\n' "$wake_on_magic_packet"
    printf '  "sleep_after_min": null\n'
    printf '}\n'
} > "$OUT"

cat "$OUT"
