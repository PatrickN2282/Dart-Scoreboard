# Dart Screensaver – Raspberry Pi

Startet nach einer definierten Inaktivitätszeit automatisch Chromium im Vollbild und zeigt eine konfigurierbare URL. Wird die Maus bewegt, schließt sich Chromium wieder.

---

## Voraussetzungen

- Raspberry Pi OS mit Wayland
- `swayidle` installiert (`sudo apt install swayidle`)
- `chromium` installiert

---

## Dateien

| Datei | Pfad |
|---|---|
| Script | `/home/autodarts/dart_screensaver.sh` |
| Autostart | `~/.config/autostart/dart-screensaver.desktop` |

---

## Installation

### 1. Script anlegen

```bash
cat > /home/autodarts/dart_screensaver.sh << 'EOF'
#!/bin/bash

IDLE_TIME=300
URL="http://localhost:5000"

export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

exec swayidle -w \
  timeout $IDLE_TIME 'WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 chromium --ozone-platform=wayland --start-fullscreen --password-store=basic --no-first-run --disable-sync --noerrdialogs --disable-infobars "http://localhost:5000" &' \
  resume 'pkill -f chromium'
EOF

chmod +x /home/autodarts/dart_screensaver.sh
```

### 2. Autostart einrichten

```bash
mkdir -p ~/.config/autostart

cat > ~/.config/autostart/dart-screensaver.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Dart Screensaver
Exec=/home/autodarts/dart_screensaver.sh
X-GNOME-Autostart-enabled=true
EOF
```

### 3. Manuell starten (ohne Reboot)

```bash
/home/autodarts/dart_screensaver.sh &
```

---

## Konfiguration

In `/home/autodarts/dart_screensaver.sh` können folgende Werte angepasst werden:

| Variable | Standard | Beschreibung |
|---|---|---|
| `IDLE_TIME` | `300` | Sekunden bis Chromium startet |
| `URL` | `http://localhost:5000` | Webseite die angezeigt wird |
| `WAYLAND_DISPLAY` | `wayland-0` | Wayland-Socket |
| `XDG_RUNTIME_DIR` | `/run/user/1000` | Wayland Runtime-Verzeichnis |

---

## Änderungen übernehmen

Nach jeder Änderung am Script muss `swayidle` neu gestartet werden:

```bash
pkill swayidle && /home/autodarts/dart_screensaver.sh &
```

Oder per Reboot:

```bash
sudo reboot
```

---

## Prüfen ob swayidle läuft

```bash
pgrep -a swayidle
```
