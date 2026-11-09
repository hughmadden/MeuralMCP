#!/bin/bash
# Keep every Meural frame's clock inside the MITM chain's validity window.
#
# Chain valid 2025-05-14 .. 2028-12-31. Outside it the frame aborts with
# certificate_expired (45) and stops polling. The LAN API is plain HTTP, so this
# works even when a frame's TLS is already broken.
#
# Frames come from frames.txt (one IP per line) plus any current neighbour that
# answers /remote/identify as a Meural.
set -u

FRAMES=/var/lib/meural-api/frames.txt
ANCHOR_FILE=/var/lib/meural-api/clock.anchor
MARK=$(mktemp)

if [ -f "$ANCHOR_FILE" ]; then
    read -r ANCHOR_EPOCH < "$ANCHOR_FILE"
else
    ANCHOR_EPOCH=$(date +%s)   # anchor to first run
    printf "%s" "$ANCHOR_EPOCH" > "$ANCHOR_FILE" 2>/dev/null || true
fi
TARGET=$(( ANCHOR_EPOCH + ($(date +%s) - ANCHOR_EPOCH) ))
# Clamp inside the certificate's validity window; override to match your chain.
LO=$(date -u -d "${PIN_LO:-2025-07-01T00:00:00Z}" +%s)
HI=$(date -u -d "${PIN_HI:-2028-10-01T00:00:00Z}" +%s)
[ "$TARGET" -lt "$LO" ] && TARGET=$LO
[ "$TARGET" -gt "$HI" ] && TARGET=$HI

collect() {
    [ -f "$FRAMES" ] && sed -e 's/#.*//' -e 's/[[:space:]]//g' "$FRAMES" | grep -E '^[0-9]+\.' || true
    ip -4 neigh 2>/dev/null | awk '$1 ~ /^10\./ {print $1}' || true
}

pin() {
    local ip="$1" id
    id=$(curl -sS -m 3 "http://$ip/remote/identify/" 2>/dev/null) || return 1
    case "$id" in *'"product_key"'*) ;; *) return 1 ;; esac
    curl -sS -m 5 "http://$ip/remote/control_command/rtc/$TARGET" >/dev/null 2>&1 \
        && echo "pinned $ip"
}

for _ in $(seq 1 24); do
    collect | sort -u > "$MARK"
    n=0
    while read -r ip; do
        [ -n "$ip" ] || continue
        pin "$ip" && n=$((n + 1))
    done < "$MARK"
    if [ "$n" -gt 0 ]; then
        echo "pinned $n frame(s) to $(date -u -d "@$TARGET" +%FT%TZ)"
        rm -f "$MARK"
        exit 0
    fi
    sleep 5
done
echo "no frames reachable" >&2
rm -f "$MARK"
exit 1
