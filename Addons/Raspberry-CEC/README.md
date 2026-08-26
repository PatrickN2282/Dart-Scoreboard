# HDMI Audio & CEC Manager – Installation

## Voraussetzungen

```bash
sudo apt install cec-utils
```

## Installation

```bash
# 1. Script installieren
mkdir -p ~/.local/bin ~/.local/log
cp hdmi-audio-cec.sh ~/.local/bin/hdmi-audio-cec.sh
chmod +x ~/.local/bin/hdmi-audio-cec.sh

# 2. Service installieren
mkdir -p ~/.config/systemd/user/
cp hdmi-audio-cec.service ~/.config/systemd/user/

# 3. Service aktivieren
systemctl --user daemon-reload
systemctl --user enable hdmi-audio-cec.service
systemctl --user start hdmi-audio-cec.service
```

## Überprüfung

```bash
# Status anzeigen
systemctl --user status hdmi-audio-cec.service

# Log verfolgen (live)
journalctl --user -u hdmi-audio-cec -f

# Oder Log-Datei
tail -f ~/.local/log/hdmi-audio-cec.log
```

## Falls CEC-Name nicht erscheint

Manche TVs ignorieren den OSD-Namen bis ein `scan` durchgeführt wird.
Test manuell:
```bash
echo -e "scan\nq" | cec-client -s -d 1
```

## Troubleshooting

**HDMI-Sink nicht gefunden:**
```bash
wpctl status   # Zeigt alle Audio-Sinks
```

**CEC funktioniert nicht:**
```bash
# Prüfe ob /dev/cec0 existiert
ls /dev/cec*
# Manuelle CEC-Diagnose
echo "scan" | cec-client -s -d 4
```

**Service startet nicht:**
```bash
# Linger aktivieren (damit User-Services ohne Login laufen)
loginctl enable-linger $USER
```
