#!/usr/bin/env bash
# Installationsskript für den Raspberry Pi Screensaver (Kiosk start)
# Legt die Skripte an und kann die .desktop Autostart-Datei per Benutzer-Kopie installieren.
# Nutzung: sudo bash install.sh [--system]

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$HERE"

SRC_SH="$SCRIPTS_DIR/dart_screensaver.sh"
SRC_DESKTOP="$SCRIPTS_DIR/dart-screensaver.desktop"

DST_BIN="/usr/local/bin/dart-screensaver"
USER_AUTOSTART_DIR="$HOME/.config/autostart"
SYSTEM_AUTOSTART_DIR="/etc/xdg/autostart"

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

# Kopiere Skript nach /usr/local/bin (erfordert sudo)
if $install_system; then
  echo "Installiere systemweit nach $DST_BIN (erfordert sudo)..."
  sudo cp "$SRC_SH" "$DST_BIN"
  sudo chmod +x "$DST_BIN"
else
  DST_USER_BIN="$HOME/.local/bin/dart-screensaver"
  mkdir -p "$HOME/.local/bin"
  echo "Installiere lokal nach $DST_USER_BIN..."
  cp "$SRC_SH" "$DST_USER_BIN"
  chmod +x "$DST_USER_BIN"
  echo "Achte darauf, dass $HOME/.local/bin in PATH ist oder starte das Skript direkt." 
fi

# Installiere .desktop Datei in Autostart
if [[ -f "$SRC_DESKTOP" ]]; then
  if $install_system; then
    echo "Kopiere $SRC_DESKTOP nach $SYSTEM_AUTOSTART_DIR (erfordert sudo)..."
    sudo cp "$SRC_DESKTOP" "$SYSTEM_AUTOSTART_DIR/"
  else
    mkdir -p "$USER_AUTOSTART_DIR"
    echo "Kopiere $SRC_DESKTOP nach $USER_AUTOSTART_DIR/..."
    cp "$SRC_DESKTOP" "$USER_AUTOSTART_DIR/"
  fi
  echo "Autostart-Eintrag installiert."
else
  echo "Keine .desktop Datei zum Installieren gefunden. Überspringe Autostart.";
fi

cat <<EOF
Installation abgeschlossen.
- Skript: ${install_system:+systemweit }${install_system:-false} installiert.
- Wenn du systemweite Installation wolltest, rufe: sudo bash install.sh --system
- Prüfe ggf. die Pfade in dart-screensaver.desktop (Exec=) und passe bei Bedarf an.
EOF
