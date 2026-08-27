# HDMI Audio & CEC Manager

Der Manager hält einen CEC-fähigen Fernseher im eingestellten Zeitfenster aktiv, schickt ihn außerhalb des Fensters gezielt in Standby und stellt HDMI-Audio wieder her.

## Einrichtung über den Adminbereich

1. Die Grundanwendung mit `./install.sh` installieren. Dabei wird `cec-utils` mit installiert.
2. Im Adminbereich **Addons → CEC-Manager** einen Namen sowie Standby- und Aufweckzeit eintragen und den Zeitplan aktivieren.
3. **CEC-Manager installieren** auswählen.

Die Konfiguration wird sicher in `~/.config/dart-scoreboard/cec.conf` gespeichert. Der Dienst prüft sie in jedem Zyklus; geänderte Zeiten und Gerätenamen werden daher ohne Neuinstallation übernommen.

## Zeitfenster

- Bei **Aufwecken 08:00** und **Standby 22:00** bleibt der Fernseher zwischen 08:00 und 22:00 aktiv.
- Zeitfenster über Mitternacht werden unterstützt, beispielsweise Aufwecken 18:00 und Standby 06:00.
- Gleiche Zeiten bedeuten Dauerbetrieb.
- Ein deaktivierter Zeitplan sendet keine Keep-Alive-Signale und keine Schaltbefehle.

## Manuelle Installation

Falls der Adminbereich nicht erreichbar ist:

```bash
mkdir -p ~/.local/bin ~/.config/systemd/user
cp hdmi-audio-cec.sh ~/.local/bin/hdmi-audio-cec.sh
cp hdmi-audio-cec.service ~/.config/systemd/user/
chmod +x ~/.local/bin/hdmi-audio-cec.sh
systemctl --user daemon-reload
systemctl --user enable --now hdmi-audio-cec.service
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
sudo loginctl enable-linger "$USER"
```
