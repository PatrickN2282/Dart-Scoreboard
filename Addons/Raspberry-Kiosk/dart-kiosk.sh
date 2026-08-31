#!/usr/bin/env bash
set -u

KIOSK_URL="http://127.0.0.1:5000/"
KIOSK_BROWSER=""
KIOSK_DISPLAY_MODE="auto"
KIOSK_HIDE_CURSOR=1
readonly CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/dart-scoreboard/kiosk.conf"
readonly PROFILE_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/dart-scoreboard/chromium-kiosk"

if [ -r "$CONFIG_FILE" ]; then
    # Written by the local application with shell-quoted, validated values.
    # shellcheck disable=SC1090
    . "$CONFIG_FILE"
fi

find_browser() {
    if [ -n "$KIOSK_BROWSER" ] && [ -x "$KIOSK_BROWSER" ]; then
        printf '%s\n' "$KIOSK_BROWSER"
        return 0
    fi
    command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null
}

detect_display() {
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID}}"
    if [ "$KIOSK_DISPLAY_MODE" != "x11" ]; then
        if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
            return 0
        fi
        local socket
        socket=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'wayland-*' -printf '%f\n' 2>/dev/null | sort | head -n1)
        if [ -n "$socket" ]; then
            export WAYLAND_DISPLAY="$socket"
            export XDG_SESSION_TYPE=wayland
            return 0
        fi
    fi
    if [ "$KIOSK_DISPLAY_MODE" != "wayland" ]; then
        export DISPLAY="${DISPLAY:-:0}"
        if [ -S "/tmp/.X11-unix/X${DISPLAY#:}" ]; then
            export XDG_SESSION_TYPE=x11
            return 0
        fi
    fi
    return 1
}

browser=$(find_browser) || {
    echo "Chromium wurde nicht gefunden (erwartet: chromium oder chromium-browser)." >&2
    exit 69
}

for _attempt in $(seq 1 120); do
    detect_display && break
    sleep 1
done
if ! detect_display; then
    echo "Keine nutzbare Wayland- oder X11-Desktop-Session gefunden." >&2
    exit 75
fi

# Avoid Chromium's error page while the local Flask service is still starting.
for _attempt in $(seq 1 60); do
    if command -v curl >/dev/null 2>&1 && curl --fail --silent --show-error --max-time 2 "$KIOSK_URL" >/dev/null; then
        break
    fi
    sleep 1
done

mkdir -p "$PROFILE_DIR"
flags=(
    --kiosk
    --start-maximized
    --no-first-run
    --noerrdialogs
    --disable-infobars
    --password-store=basic
    --user-data-dir="$PROFILE_DIR"
)
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    flags+=(--ozone-platform=wayland)
fi
if [ "$KIOSK_HIDE_CURSOR" = "1" ]; then
    flags+=(--hide-scrollbars)
fi

echo "Starte Chromium-Kiosk auf ${XDG_SESSION_TYPE:-unbekannt}: $KIOSK_URL"
exec "$browser" "${flags[@]}" "$KIOSK_URL"
