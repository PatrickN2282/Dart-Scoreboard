# Dart Screensaver – Raspberry Pi

Startet nach einer definierten Inaktivitätszeit automatisch Chromium im Vollbild und zeigt das lokale Scoreboard. Wird die Maus bewegt, schließt sich ausschließlich das separate Screensaver-Chromium-Profil; eine laufende Autodarts-Spielumgebung bleibt geöffnet.

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

Das Installationsskript erzeugt `~/.config/autostart/dart-screensaver.desktop`, installiert `dart-screensaver.service` als systemd-User-Service und aktiviert ihn. Für eine systemweite Dateikopie verwende `bash install.sh --system`; der Prozess läuft weiterhin als User-Service.

Bevorzugt wird die Installation und Verwaltung im Adminbereich. Dort werden auch der tatsächliche enabled/active-Status sowie Start, Stop, Restart, Update und Deinstallation angeboten.

### 2. Manuell starten (ohne Reboot)

```bash
~/.local/bin/dart-screensaver &
```

---

## Konfiguration

Die Wartezeit wird im Adminbereich unter **Addons → Bildschirmschoner** eingestellt.
Sie wird in `~/.config/dart-scoreboard/screensaver.conf` gespeichert; ein bereits
installierter Screensaver wird beim Speichern automatisch neu gestartet. Die übrigen Werte können vor der Installation im
Skript angepasst werden:

| Variable | Standard | Beschreibung |
|---|---|---|
| `URL` | `http://localhost:5000` | Webseite die angezeigt wird |
| `WAYLAND_DISPLAY` | `wayland-0` | Wayland-Socket |
| `XDG_RUNTIME_DIR` | `/run/user/<UID>` | Wayland Runtime-Verzeichnis |

---

## Änderungen übernehmen

Nach jeder Änderung am Script muss der verwaltete Dienst neu gestartet werden:

```bash
systemctl --user restart dart-screensaver.service
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
