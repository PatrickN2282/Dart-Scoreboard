#!/bin/bash

IDLE_TIME=300
URL="http://localhost:5000"

export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

exec swayidle -w \
  timeout $IDLE_TIME 'WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 chromium --ozone-platform=wayland --start-fullscreen --password-store=basic --no-first-run --disable-sync --noerrdialogs --disable-infobars "http://localhost:5000" &' \
  resume 'pkill -f chromium'
