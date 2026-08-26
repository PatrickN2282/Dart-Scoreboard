#!/usr/bin/env bash
# Installiert eine entpackte Dart-Scoreboard-Release auf Raspberry Pi OS.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/dart-scoreboard.service"
INSTALL_SYSTEM_PACKAGES=true

if [[ "${1:-}" == "--skip-system-packages" ]]; then
    INSTALL_SYSTEM_PACKAGES=false
elif [[ -n "${1:-}" ]]; then
    echo "Nutzung: $0 [--skip-system-packages]"
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 ist nicht installiert."
    exit 1
fi

if "$INSTALL_SYSTEM_PACKAGES"; then
    if ! command -v sudo >/dev/null 2>&1; then
        echo "Für die Paketinstallation wird sudo benötigt. Nutze alternativ --skip-system-packages."
        exit 1
    fi
    echo "Installiere Raspberry-Pi-Abhängigkeiten …"
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip cec-utils swayidle
    if apt-cache show chromium >/dev/null 2>&1; then
        sudo apt-get install -y chromium
    else
        sudo apt-get install -y chromium-browser
    fi
fi

echo "Erstelle Python-Umgebung …"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/data" "$APP_DIR/static/uploads" "$SERVICE_DIR"
for file in players scores bot_scores imported_matches; do
    [[ -f "$APP_DIR/data/$file.json" ]] || printf '[]\n' > "$APP_DIR/data/$file.json"
done
[[ -f "$APP_DIR/data/config.json" ]] || printf '{}\n' > "$APP_DIR/data/config.json"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Dart Scoreboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python $APP_DIR/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now dart-scoreboard.service

if command -v loginctl >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo loginctl enable-linger "$USER"
fi

echo
echo "Installation abgeschlossen. Öffne: http://$(hostname -I | awk '{print $1}'):5000"
echo "Status: systemctl --user status dart-scoreboard.service"
