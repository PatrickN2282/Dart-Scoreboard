#!/bin/bash

IDLE_TIME=300
URL="http://localhost:5000"

export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

printf -v chromium_command \
  'chromium --ozone-platform=wayland --start-fullscreen --password-store=basic --no-first-run --disable-sync --noerrdialogs --disable-infobars %q &' \
  "$URL"
exec swayidle -w \
  timeout "$IDLE_TIME" "$chromium_command" \
  resume 'pkill -f chromium'
