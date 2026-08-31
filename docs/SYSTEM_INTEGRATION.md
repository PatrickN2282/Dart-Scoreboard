# Raspberry-Pi-Systemintegration

## Zielsystem und Betriebsarten

Unterstützt wird ein Raspberry Pi 5 mit Raspberry Pi OS Desktop (64 Bit). Eine
Desktop-Ausgabe ist erforderlich; Raspberry Pi OS Lite reicht ohne zusätzlich
eingerichteten Wayland-/X11-Compositor nicht aus. Aktuelle Raspberry-Pi-OS-
Installationen verwenden Labwc/Wayland. Die Launcher erkennen Wayland zur
Laufzeit und der optionale Dauer-Kiosk unterstützt zusätzlich X11.

Für den normalen Autodarts-Spielbetrieb ist der **Scoreboard-Bildschirmschoner**
vorgesehen: Während niemand spielt, öffnet er das lokale Scoreboard. Bei neuer
Mausaktivität beendet er ausschließlich Chromium-Prozesse seines separaten
Screensaver-Profils. Die Autodarts-Spielumgebung bleibt geöffnet.

Der **Dauer-Kiosk** ist eine alternative Betriebsart für Geräte, die nur das
Scoreboard anzeigen. Beim Start einer der beiden Betriebsarten stoppt der
Adminbereich eine laufende andere Betriebsart. Beide sollten nicht gleichzeitig
aktiv sein.

Referenzen:

- Raspberry Pi: <https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/>
- Raspberry Pi OS und Labwc: <https://www.raspberrypi.com/news/a-new-release-of-raspberry-pi-os/>

## Installation

```bash
chmod +x install.sh
./install.sh
```

Das Skript installiert standardmäßig `python3-venv`, `python3-pip`, `cec-utils`,
`swayidle`, `wireplumber`, `curl` und Chromium, kopiert die Anwendung nach
`/opt/Dart-Scoreboard`, erzeugt `.venv` und richtet
`dart-scoreboard.service` als systemd-User-Service ein. Bereits vorhandene
Nutzdaten und Uploads bleiben bei Updates erhalten.

Wenn die Systempakete bereits vorhanden sind:

```bash
./install.sh --skip-system-packages
```

Der Installer aktiviert `loginctl linger` für den ausführenden Benutzer. Dadurch
läuft der Anwendungs-User-Service auch nach einem Neustart ohne interaktiven
Login. Die grafischen Add-ons benötigen weiterhin eine automatisch gestartete
Desktop-Session.

Wichtige Prüfungen:

```bash
systemctl --user status dart-scoreboard.service
journalctl --user -u dart-scoreboard.service -b
curl --fail http://127.0.0.1:5000/
```

Der Adminbereich besitzt auf ausdrücklichen Wunsch keine Anmeldung und ist für
ein kontrolliertes lokales Netz vorgesehen. Er darf nicht direkt aus dem
Internet erreichbar sein. Add-on-Aktionen laufen ohne Root-Rechte als derselbe
Benutzer wie die Anwendung.

## Add-on-Modell

Systemdienste werden aus `Addons/*/addon.json` erkannt. Ein Manifest beschreibt:

- stabile ID, Name, Version und Beschreibung;
- Typ `systemd-user` und einen festen Service-Namen;
- erforderliche Kommandos;
- Laufzeit-Konfigurationsdatei;
- eine feste Liste aus Quelldatei, Ziel relativ zu `$HOME` und Dateimodus.

Der Manager akzeptiert nur die Aktionen `install`, `update`, `start`, `stop`,
`restart` und `uninstall`. Zielpfade dürfen das Home-Verzeichnis nicht verlassen,
Service-Namen werden validiert und jeder Prozess wird als Argumentliste ohne
`shell=True` gestartet. Flask führt kein `sudo` aus.

Der Status stammt nicht aus einem Aktiv-Flag, sondern aus den installierten
Dateien und aus:

```bash
systemctl --user is-enabled SERVICE
systemctl --user is-active SERVICE
```

Damit werden die Zustände `nicht installiert`, `nicht konfiguriert`,
`installiert`, `gestoppt`, `läuft` und `fehlerhaft` unterschieden. Fehlende
Abhängigkeiten werden separat gemeldet.

### Admin-Ablauf

1. Adminformular validiert und speichert Benutzerkonfiguration in `data/config.json`.
2. Die Anwendung schreibt eine minimale Laufzeitdatei unter
   `~/.config/dart-scoreboard/` mit Modus `0600`.
3. `Installieren/Aktualisieren` kopiert ausschließlich die im Manifest genannten Dateien.
4. Der Manager führt `daemon-reload`, `enable --now` und `restart` aus.
5. Die Status-API fragt den tatsächlichen enabled/active-Zustand ab.
6. Die Adminseite aktualisiert die Anzeige alle 15 Sekunden.

## Scoreboard-Bildschirmschoner

Komponenten:

- `dart-screensaver.service`: Restart und Journal-Logging;
- `dart-screensaver.desktop`: importiert beim Desktopstart die grafischen
  Sessionvariablen in systemd und startet den Dienst neu;
- `dart_screensaver.sh`: startet `swayidle` und das separate Chromium-Profil;
- `screensaver.conf`: validierte Inaktivitätszeit.

Voraussetzungen sind Labwc/Wayland, `swayidle` und Chromium. Die Wartezeit wird
im Adminbereich eingestellt. Ein Update der Einstellung startet einen bereits
installierten Dienst neu.

```bash
systemctl --user status dart-screensaver.service
journalctl --user -u dart-screensaver.service -f
```

## Dauer-Kiosk

`dart-kiosk.service` startet Chromium mit einem eigenen Profil und den kleinen,
aktuellen Flags `--kiosk`, `--start-maximized`, `--no-first-run`,
`--noerrdialogs`, `--disable-infobars` und `--password-store=basic`. Der Launcher:

- sucht `chromium` oder `chromium-browser`;
- erkennt Wayland-Sockets oder X11;
- wartet auf die grafische Sitzung und auf die konfigurierte HTTP(S)-URL;
- verwendet unter Wayland `--ozone-platform=wayland`;
- überlässt Restart und Beenden systemd.

Die URL, die automatische/Wayland/X11-Auswahl und das Ausblenden des Mauszeigers
werden im Adminbereich gespeichert.

```bash
systemctl --user status dart-kiosk.service
journalctl --user -u dart-kiosk.service -f
```

## HDMI-CEC

Der CEC-Manager verwendet `cec-client` aus `cec-utils` (libCEC) und `wpctl` aus
WirePlumber. libCEC unterstützt den Linux-Kernel-CEC-Treiber des Raspberry Pi.
Alle CEC-Aufrufe besitzen ein Timeout; ein fehlender oder getrennter Fernseher
blockiert weder Flask noch Chromium, da CEC in einem eigenen User-Service läuft.

Konfigurierbar sind:

- Zeitplan aktiv/inaktiv;
- Aufweck- und Standby-Zeit, auch über Mitternacht;
- OSD-Gerätename (druckbares ASCII, maximal 14 Zeichen);
- optional `/dev/cecN`, sonst libCEC-Autoerkennung;
- Prüfintervall von 10 bis 3600 Sekunden.

Im aktiven Zeitfenster fragt der Manager `pow 0` ab, sendet bei Bedarf `on 0`
und `as`, und prüft regelmäßig HDMI-Audio. Außerhalb wird einmalig `standby 0`
gesendet. Der OSD-Name wird über libCECs `--osd-name` angekündigt; es wird keine
logische CEC-Adresse fest angenommen.

```bash
systemctl --user status hdmi-audio-cec.service
journalctl --user -u hdmi-audio-cec.service -f
cec-client -l
printf 'scan\n' | cec-client -s -d 4
wpctl status
```

## Troubleshooting

### Add-on wird nicht angezeigt

- Existiert `Addons/NAME/addon.json`?
- Ist das JSON gültig und die ID eindeutig?
- Zeigt `journalctl --user -u dart-scoreboard.service` einen Manifestfehler?

### Installation oder Aktion schlägt fehl

- Die Statusmeldung auf fehlende Kommandos prüfen.
- `systemctl --user status SERVICE` und `journalctl --user -u SERVICE -b` lesen.
- `loginctl show-user "$USER" -p Linger` prüfen.
- Der Backend-Benutzer muss sein eigenes `systemctl --user` erreichen können.

### Aktiviert, aber nicht laufend

`enabled` bedeutet nur Autostart. `active` ist der tatsächliche Prozesszustand.
Das Journal enthält den konkreten Startfehler. Bei grafischen Diensten außerdem
`echo "$WAYLAND_DISPLAY $DISPLAY $XDG_RUNTIME_DIR"` in einem Desktop-Terminal
prüfen.

### Schwarzer Bildschirm oder Chromium startet nicht

- Raspberry Pi OS Desktop statt Lite verwenden.
- `command -v chromium chromium-browser` prüfen.
- `curl http://127.0.0.1:5000/` prüfen.
- Nur eine der Betriebsarten Screensaver/Dauer-Kiosk starten.
- Kiosk-Journal auf fehlende Wayland-/X11-Sitzung prüfen.

### Bildschirmschoner reagiert nicht

- `command -v swayidle` und `$XDG_RUNTIME_DIR/wayland-*` prüfen.
- Der Dienst ist für Wayland gedacht; unter reinem X11 den Dauer-Kiosk oder eine
  passende X11-Idle-Lösung verwenden.
- Nach Mausbewegung wird nur das Profil
  `~/.local/state/dart-scoreboard/chromium-screensaver` beendet.

### TV reagiert nicht auf CEC

- CEC im TV-Menü aktivieren (Hersteller verwenden eigene Markennamen).
- HDMI-Kabel und `/dev/cec*` prüfen.
- Zunächst Adapterfeld leer lassen, danach bei mehreren Geräten gezielt
  `/dev/cec0` oder `/dev/cec1` konfigurieren.
- Manche TVs ignorieren Active Source, Standby oder OSD-Namen teilweise.

### Autodarts-Import schlägt fehl

- Zugangsdaten im Adminbereich neu speichern und je nach Zustand „Erstabgleich
  starten/fortsetzen“ oder „Jetzt nach neuen Matches suchen“ verwenden.
- Live-Status, `data/autodarts_last_result.json` und den persistenten Fortschritt
  in `data/autodarts_sync_state.json` prüfen.
- API-/DNS-Erreichbarkeit und Systemzeit prüfen.
- Der Playwright-Fallback benötigt zusätzlich einen installierten
  Playwright-Chromium-Browser; der normale API-Pfad benötigt ihn nicht.
- Ein gleichzeitig geöffnetes persistentes Chromium-Profil kann den manuellen
  Login blockieren. Screensaver und Kiosk verwenden deshalb eigene Profile.
