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
| Script | `~/.local/bin/dart-screensaver` |
| Autostart | `~/.config/autostart/dart-screensaver.desktop` |

---

## Installation

### 1. Addon installieren

```bash
bash install.sh
```

Das Installationsskript erzeugt `~/.config/autostart/dart-screensaver.desktop` und trägt den tatsächlich installierten Skriptpfad ein. Für eine systemweite Installation verwende `sudo bash install.sh --system`.

### 2. Manuell starten (ohne Reboot)

```bash
~/.local/bin/dart-screensaver &
```

---

## Konfiguration

In `dart_screensaver.sh` können vor der Installation folgende Werte angepasst werden:

| Variable | Standard | Beschreibung |
|---|---|---|
| `IDLE_TIME` | `300` | Sekunden bis Chromium startet |
| `URL` | `http://localhost:5000` | Webseite die angezeigt wird |
| `WAYLAND_DISPLAY` | `wayland-0` | Wayland-Socket |
| `XDG_RUNTIME_DIR` | `/run/user/<UID>` | Wayland Runtime-Verzeichnis |

---

## Änderungen übernehmen

Nach jeder Änderung am Script muss `swayidle` neu gestartet werden:

```bash
pkill swayidle && ~/.local/bin/dart-screensaver &
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
