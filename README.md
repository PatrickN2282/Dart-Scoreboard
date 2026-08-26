# 🎯 Dart Highscore Board

Ein schlankes, lokales Dart-Scoreboard für den Raspberry Pi – gebaut für Büros, Vereinsräume oder jede andere Location mit einer Dartscheibe und ein bisschen Ehrgeiz.

![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python) ![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask) ![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ Features

- **4 Statistik-Kategorien** – Meiste Legs, Höchstes Finish, Meiste 180er, Wenigste Darts (301)
- **Zwei Anzeigemodi** – 2×2 Kachelansicht (statisch) und Einzelkarten-Rotation mit automatischem Wechsel alle 15 Sekunden
- **Automatischer Moduswechsel** – nach 10 Minuten wechselt das Board selbstständig zwischen statisch und Rotation
- **Auto-Refresh** – Scoreboard lädt alle 60 Sekunden neu und zeigt frisch eingetragene Scores
- **Podium-Hervorhebung** – Gold / Silber / Bronze mit Shine-Effekt und gestaffelter Einlauf-Animation
- **Spielerbilder** – Profilfotos pro Spieler, werden in der Karte und im Rotationsmodus angezeigt
- **QR-Code** – zeigt die lokale IP zur schnellen Verbindung vom Handy
- **Admin-Panel** – Score eintragen, Spieler verwalten, Bilder hochladen, Design konfigurieren
- **Konfigurierbar** – Schriftarten, Schriftgrößen und Limits direkt im Browser einstellbar
- **Kein Datenbank-Overhead** – alle Daten liegen in schlanken JSON-Dateien

---

## 📸 Ansichten

| Statisch (2×2) | Rotation (Vollbild) |
|---|---|
| Alle 4 Kategorien gleichzeitig | Eine Kategorie groß, wechselt automatisch |

---

## 🛠️ Installation

### Voraussetzungen

- Raspberry Pi (oder beliebiger Linux-Rechner)
- Python 3.9+
- pip

### Setup

```bash
# Repository klonen
git clone https://github.com/dein-name/dart-highscore.git
cd dart-highscore

# Abhängigkeiten installieren
pip install flask qrcode[pil]

# Dateistruktur anlegen
mkdir -p data static/uploads

# Platzhalter-Bild für Spieler ohne Foto ablegen
# (dummy.png in static/uploads/ kopieren)

# Server starten
python app.py
```

Die App ist dann unter `http://<raspberry-pi-ip>:5000` erreichbar.

### Als Dienst einrichten (autostart)

```bash
# Systemd-Service anlegen
sudo nano /etc/systemd/system/dartboard.service
```

```ini
[Unit]
Description=Dart Highscore Board
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/dart-highscore
ExecStart=/usr/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable dartboard
sudo systemctl start dartboard
```

---

## 📁 Projektstruktur

```
dart-highscore/
├── app.py                  # Flask-Backend, Routen & Aggregationslogik
├── data/
│   ├── players.json        # Spielerdaten (ID, Name, Bild)
│   ├── scores.json         # Alle Score-Einträge
│   └── config.json         # Design- & Limit-Einstellungen
├── static/
│   ├── main.css            # Scoreboard-Styles
│   ├── admin.css           # Admin-Panel-Styles
│   └── uploads/            # Spielerbilder & Hintergrundbild
└── templates/
    ├── index.html          # Scoreboard (öffentliche Ansicht)
    └── admin.html          # Admin-Panel
```

---

## ⚙️ Konfiguration

Alle Design-Einstellungen sind im Admin-Panel unter **Design & Limits** erreichbar:

| Einstellung | Beschreibung | Standard |
|---|---|---|
| `static_limit` | Sichtbare Zeilen im 2×2-Grid | 5 |
| `rotation_limit` | Max. Zeilen im Rotationsmodus | 10 |
| `static_h2_size` | Überschriftgröße statisch | 2.5em |
| `rotation_h2_size` | Überschriftgröße Rotation | 3.5em |
| `static_td_size` | Tabellenschrift statisch | 2.0em |
| `font_family` | Schriftart | Segoe UI |

---

## 🎮 Bedienung

### Scoreboard (`/`)
- **Modus-Umschalter** (oben links) – manuell zwischen statisch und Rotation wechseln
- **QR-Code** (unten rechts) – zum schnellen Aufrufen der Seite vom Handy

### Admin-Panel (`/admin`)
- **Score eintragen** – Spieler wählen oder neu anlegen, Werte eintragen, speichern
- **Übersicht** – alle Einträge einsehen und einzeln löschen
- **Spieler** – Profilbild hochladen, umbenennen, löschen
- **Design & Limits** – Hintergrundbild, Schriftarten, Größen konfigurieren

---

## 📊 Datenstruktur

**`players.json`**
```json
[
  { "id": 1, "name": "Max Mustermann", "image": "player_1.png" }
]
```

**`scores.json`**
```json
[
  {
    "player_id": 1,
    "legs": 5,
    "finish": 141,
    "max180": 2,
    "darts301": 18,
    "date": "31.01.2025 19:55"
  }
]
```

> **Hinweis:** `legs` und `max180` werden über alle Einträge kumuliert. `finish` und `darts301` zeigen den besten Einzelwert pro Spieler. Beim Autodarts-Import kommen zusätzlich u.a. `average`, `first9_average` (Schnitt der ersten 9 Darts) und `first3_average` (Schnitt der ersten 3 Darts/des ersten Aufnahms je Leg) hinzu.

**`bot_scores.json`**

Ergebnisse aus Matches gegen einen BOT-Gegner (z.B. `autodartsbotX`) werden nicht in `scores.json`/`players.json` aufgenommen, damit BOTs nicht als Spieler erscheinen und echte Spieler-Statistiken nicht verfälscht werden. Stattdessen landen sie – mit dem gleichen Aufbau wie `scores.json` plus einem zusätzlichen Feld `bot_level` – getrennt in `bot_scores.json` und werden im Admin-Panel (Tab „Addons“) pro Spieler und Bot-Level ausgewertet.

---

## 🚀 Als Bildschirmschoner einrichten

Um das Scoreboard automatisch auf einem angeschlossenen Monitor zu starten, kann ein einfaches Skript genutzt werden das den Browser im Kiosk-Modus öffnet:

```bash
# Beispiel für Chromium auf Raspberry Pi OS
chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:5000
```

Alternativ enthält `/Addons/Raspberry-Screensaver` ein fertiges Skript (inkl. Autostart-Eintrag) für Wayland/Sway-Umgebungen. Über den Button **🖥️ Screensaver installieren** im Admin-Panel (Tab „Addons“) wird es automatisch ins Home-Verzeichnis kopiert und eingerichtet.

---

## 📄 Lizenz

MIT – mach damit was du willst. Ein Stern auf GitHub freut uns trotzdem. ⭐
