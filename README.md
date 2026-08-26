# 🎯 Dart Highscore Board

Ein schlankes, lokales Dart-Scoreboard für den Raspberry Pi – gebaut für Büros, Vereinsräume oder jede andere Location mit einer Dartscheibe und ein bisschen Ehrgeiz.

![Version](https://img.shields.io/badge/version-1.0.0-blue) ![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python) ![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask) ![License](https://img.shields.io/badge/License-MIT-green)

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
- **Raspberry-Pi-Addons** – Bildschirmschoner sowie HDMI-CEC-Manager im Adminbereich installierbar
- **Kein Datenbank-Overhead** – alle Daten liegen in schlanken JSON-Dateien

---

## 📸 Ansichten

| Statisch (2×2) | Rotation (Vollbild) |
|---|---|
| Alle 4 Kategorien gleichzeitig | Eine Kategorie groß, wechselt automatisch |

---

## 🛠️ Installation

### Raspberry Pi: ZIP auf USB installieren

Auf dem Entwicklungsrechner wird ein sauberes Archiv ohne lokale Daten, Caches oder Zugangsdaten erstellt:

```bash
./scripts/create-release-zip.sh
```

Kopiere `dist/dart-scoreboard-<version>.zip` auf den USB-Stick, entpacke es auf dem Raspberry Pi und führe im entpackten Ordner aus:

```bash
chmod +x install.sh
./install.sh
```

Das Installationsskript installiert die Python- und optionalen Addon-Abhängigkeiten, erzeugt eine virtuelle Umgebung und richtet `dart-scoreboard.service` als systemd-User-Service ein. Danach ist das Board unter `http://<raspberry-pi-ip>:5000` erreichbar.

Für Systeme, auf denen die erforderlichen APT-Pakete bereits installiert sind, kann `./install.sh --skip-system-packages` verwendet werden. Der Dienststatus ist mit `systemctl --user status dart-scoreboard.service` abrufbar.

> Das Script benötigt für die APT-Installation und das dauerhafte Starten nach dem Login `sudo`.

---

## 📁 Projektstruktur

```
dart-highscore/
├── app.py                  # Flask-Backend, Routen & Aggregationslogik
├── VERSION                 # Anwendungsversion nach SemVer
├── install.sh              # Raspberry-Pi-Installationsroutine
├── requirements.txt        # Python-Abhängigkeiten
├── Addons/
│   ├── Raspberry-CEC/      # HDMI-CEC-Manager
│   └── Raspberry-Screensaver/
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

### Addons

Im Admin-Panel unter **Addons** werden die optionalen Raspberry-Pi-Erweiterungen eingerichtet:

- **CEC-Manager:** Den Gerätenamen (maximal 14 Zeichen), die Standby-Zeit und die Aufweckzeit speichern und anschließend **CEC-Manager installieren**. Innerhalb des Zeitfensters von Aufwecken bis Standby hält der Manager den TV per CEC aktiv; außerhalb schickt er ihn einmalig in Standby. Gleiche Aufweck- und Standby-Zeiten bedeuten Dauerbetrieb. Voraussetzung ist ein CEC-fähiger, per HDMI angeschlossener TV.
- **Bildschirmschoner:** **Screensaver installieren** kopiert das Kiosk-/Autostart-Skript für Wayland/Sway. Er wird erst nach der Installation des Grundsystems eingerichtet.

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

Ergebnisse aus Matches gegen einen BOT-Gegner mit dem Namen `BOT LEVEL 1` bis `BOT LEVEL 9` werden nicht in `scores.json`/`players.json` aufgenommen, damit BOTs nicht als Spieler erscheinen und echte Spieler-Statistiken nicht verfälscht werden. Stattdessen landen sie – mit dem gleichen Aufbau wie `scores.json` plus einem zusätzlichen Feld `bot_level` – getrennt in `bot_scores.json` und werden im Admin-Panel (Tab „Addons“) pro Spieler und Bot-Level ausgewertet.

---

## 🔖 Versionierung

Die Anwendung verwendet [Semantic Versioning](https://semver.org/lang/de/): `MAJOR.MINOR.PATCH`.

- **MAJOR:** inkompatible Änderungen
- **MINOR:** rückwärtskompatible Funktionen
- **PATCH:** rückwärtskompatible Fehlerkorrekturen

Die aktuelle Version steht in [`VERSION`](VERSION). Für ein Release wird sie dort erhöht und anschließend mit `scripts/create-release-zip.sh` ein neues ZIP erstellt.

---

## 📄 Lizenz

MIT – mach damit was du willst. Ein Stern auf GitHub freut uns trotzdem. ⭐
