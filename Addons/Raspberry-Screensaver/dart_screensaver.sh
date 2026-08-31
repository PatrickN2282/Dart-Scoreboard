#!/bin/bash

IDLE_TIME=300
URL="http://localhost:5000"
readonly SCREENSAVER_CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/dart-scoreboard/screensaver.conf"
readonly SCREENSAVER_PID_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/dart-scoreboard/screensaver.pid"
readonly CHROMIUM_PROFILE_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/dart-scoreboard/chromium-screensaver"
readonly CHROMIUM_PID_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/dart-scoreboard-screensaver-browser.pid"

if [ -r "$SCREENSAVER_CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  . "$SCREENSAVER_CONFIG_FILE"
fi

if ! [[ "$SCREENSAVER_IDLE_TIME" =~ ^[1-9][0-9]*$ ]]; then
  SCREENSAVER_IDLE_TIME="$IDLE_TIME"
fi
IDLE_TIME="$SCREENSAVER_IDLE_TIME"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
  wayland_socket=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'wayland-*' -printf '%f\n' 2>/dev/null | sort | head -n1)
  [ -z "$wayland_socket" ] || export WAYLAND_DISPLAY="$wayland_socket"
fi

mkdir -p "$CHROMIUM_PROFILE_DIR"

stop_browser() {
  browser_pid=""
  if [ -r "$CHROMIUM_PID_FILE" ]; then
    read -r browser_pid < "$CHROMIUM_PID_FILE" || true
  fi

  if [[ "$browser_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$browser_pid" 2>/dev/null; then
    browser_cmdline=$(tr '\0' ' ' < "/proc/$browser_pid/cmdline" 2>/dev/null || true)
    if [[ "$browser_cmdline" == *"--user-data-dir=$CHROMIUM_PROFILE_DIR"* ]]; then
      kill "$browser_pid" 2>/dev/null || true
    fi
  fi

  rm -f "$CHROMIUM_PID_FILE"
  # Fallback für Chromium-Versionen, deren Startprozess sich nach dem Forken
  # beendet. Anders als früher erscheint dieses Muster nicht in der
  # swayidle-Prozesszeile und kann den Idle-Wächter daher nicht mit beenden.
  pkill -f -- "--user-data-dir=$CHROMIUM_PROFILE_DIR" 2>/dev/null || true
}

start_browser() {
  stop_browser
  browser=$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null) || {
    echo "Chromium wurde nicht gefunden." >&2
    return 69
  }
  browser_flags=(--kiosk --start-maximized --password-store=basic --no-first-run --disable-sync --noerrdialogs --disable-infobars)
  [ -z "${WAYLAND_DISPLAY:-}" ] || browser_flags+=(--ozone-platform=wayland)
  "$browser" "${browser_flags[@]}" --user-data-dir="$CHROMIUM_PROFILE_DIR" "$URL" >/dev/null 2>&1 &
  printf '%s\n' "$!" > "$CHROMIUM_PID_FILE"
}

case "${1:-}" in
  --show)
    start_browser
    exit $?
    ;;
  --hide)
    stop_browser
    exit 0
    ;;
esac

printf -v chromium_command '%q --show' "$0"
printf -v stop_command '%q --hide' "$0"

mkdir -p "$(dirname "$SCREENSAVER_PID_FILE")"
printf '%s\n' "$$" > "$SCREENSAVER_PID_FILE"

swayidle_pid=""
cleanup() {
  [ -z "$swayidle_pid" ] || kill "$swayidle_pid" 2>/dev/null || true
  stop_browser
  rm -f "$SCREENSAVER_PID_FILE"
}
trap 'cleanup; exit 0' HUP INT TERM

swayidle -w \
  timeout "$IDLE_TIME" "$chromium_command" \
  resume "$stop_command" &
swayidle_pid=$!

set +e
wait "$swayidle_pid"
swayidle_status=$?
cleanup
exit "$swayidle_status"
