#!/bin/bash

IDLE_TIME=300
URL="http://localhost:5000"
readonly SCREENSAVER_CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/dart-scoreboard/screensaver.conf"

if [ -r "$SCREENSAVER_CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  . "$SCREENSAVER_CONFIG_FILE"
fi

if ! [[ "$SCREENSAVER_IDLE_TIME" =~ ^[1-9][0-9]*$ ]]; then
  SCREENSAVER_IDLE_TIME="$IDLE_TIME"
fi
IDLE_TIME="$SCREENSAVER_IDLE_TIME"

export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

printf -v chromium_command \
  'chromium --ozone-platform=wayland --start-fullscreen --password-store=basic --no-first-run --disable-sync --noerrdialogs --disable-infobars %q &' \
  "$URL"
exec swayidle -w \
  timeout "$IDLE_TIME" "$chromium_command" \
  resume 'pkill -f chromium'
