#!/usr/bin/env bash
# Installationsskript für den Raspberry Pi Screensaver (Kiosk start)
# Legt die Skripte an und kann die .desktop Autostart-Datei per Benutzer-Kopie installieren.
# Nutzung: bash install.sh [--system]

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$HERE"

SRC_SH="$SCRIPTS_DIR/dart_screensaver.sh"
SRC_DESKTOP="$SCRIPTS_DIR/dart-screensaver.desktop"
SRC_SERVICE="$SCRIPTS_DIR/dart-screensaver.service"

USER_AUTOSTART_DIR="$HOME/.config/autostart"
SYSTEM_AUTOSTART_DIR="/etc/xdg/autostart"
USER_SERVICE_DIR="$HOME/.config/systemd/user"
SYSTEM_SERVICE_DIR="/etc/systemd/user"

install_system=false
if [[ "${1:-}" == "--system" ]]; then
  install_system=true
fi

if [[ ! -f "$SRC_SH" ]]; then
  echo "Fehler: $SRC_SH nicht gefunden. Bitte sicherstellen, dass die Dateien im Addons-Ordner liegen."
  exit 2
fi

if [[ ! -f "$SRC_DESKTOP" ]]; then
  echo "Warnung: $SRC_DESKTOP nicht gefunden. Die .desktop Datei wird nicht installiert."
fi

if [[ ! -f "$SRC_SERVICE" ]]; then
  echo "Fehler: $SRC_SERVICE nicht gefunden."
  exit 2
fi

if $install_system; then
  DST_BIN="/usr/local/bin/dart-screensaver"
  echo "Installiere systemweit nach $DST_BIN (erfordert sudo)..."
  sudo install -Dm755 "$SRC_SH" "$DST_BIN"
  DST_EXEC="$DST_BIN"
else
  DST_EXEC="$HOME/.local/bin/dart-screensaver"
  mkdir -p "$(dirname "$DST_EXEC")"
  echo "Installiere lokal nach $DST_EXEC..."
  install -m755 "$SRC_SH" "$DST_EXEC"
fi

# Installiere den systemd-User-Service. Er liefert tatsächlichen enabled/active-
# Status und startet swayidle bei einem Fehler kontrolliert neu.
if $install_system; then
  sudo install -d -m755 "$SYSTEM_SERVICE_DIR"
  sed "s|ExecStart=%h/.local/bin/dart-screensaver|ExecStart=$DST_EXEC|" "$SRC_SERVICE" \
    | sudo tee "$SYSTEM_SERVICE_DIR/dart-screensaver.service" >/dev/null
  sudo chmod 644 "$SYSTEM_SERVICE_DIR/dart-screensaver.service"
else
  mkdir -p "$USER_SERVICE_DIR"
  install -m644 "$SRC_SERVICE" "$USER_SERVICE_DIR/dart-screensaver.service"
fi

# Installiere .desktop Datei in Autostart
if [[ -f "$SRC_DESKTOP" ]]; then
  escaped_exec="${DST_EXEC//\\/\\\\}"
  escaped_exec="${escaped_exec//&/\\&}"
  escaped_exec="${escaped_exec//|/\\|}"
  if $install_system; then
    echo "Kopiere $SRC_DESKTOP nach $SYSTEM_AUTOSTART_DIR (erfordert sudo)..."
    sed "s|@DART_SCREENSAVER_EXEC@|$escaped_exec|g" "$SRC_DESKTOP" \
      | sudo tee "$SYSTEM_AUTOSTART_DIR/dart-screensaver.desktop" >/dev/null
    sudo chmod 644 "$SYSTEM_AUTOSTART_DIR/dart-screensaver.desktop"
  else
    mkdir -p "$USER_AUTOSTART_DIR"
    echo "Kopiere $SRC_DESKTOP nach $USER_AUTOSTART_DIR/..."
    sed "s|@DART_SCREENSAVER_EXEC@|$escaped_exec|g" "$SRC_DESKTOP" \
      > "$USER_AUTOSTART_DIR/dart-screensaver.desktop"
    chmod 644 "$USER_AUTOSTART_DIR/dart-screensaver.desktop"
  fi
  echo "Autostart-Eintrag installiert."
else
  echo "Keine .desktop Datei zum Installieren gefunden. Überspringe Autostart.";
fi

systemctl --user daemon-reload
systemctl --user enable --now dart-screensaver.service

cat <<EOF
Installation abgeschlossen.
- Skript: $DST_EXEC
- Der Autostart-Eintrag verweist auf genau diesen Pfad.
- Service: dart-screensaver.service (aktiviert und gestartet)
EOF
