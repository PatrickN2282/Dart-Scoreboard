# Architektur und Bestandsanalyse

## Ursprünglicher Stand

Die Anwendung ist ein Python-/Flask-Monolith (`app.py`) mit serverseitigen
Jinja-Templates und Vanilla-JavaScript. Es gibt keine Datenbank; Spieler,
Ergebnisse, Konfiguration und Importstatus werden als JSON unter `data/`
gespeichert. `index.html` ist die öffentliche Anzeige, `admin.html` enthält
Datenpflege, Design, Imports und Add-on-Bedienung.

Ursprünglich waren Add-ons fest im Python-Code verdrahtet. CEC kopierte zwei
Dateien und startete einen systemd-User-Service. Der Bildschirmschoner kopierte
ein Skript und einen Desktop-Autostart, hatte aber keinen tatsächlichen
enabled/active-Status. Es gab nur Installieren-Schaltflächen, keinen allgemeinen
Update-, Start-, Stop-, Restart- oder Deinstallationsfluss. Die Adminanzeige
kannte den tatsächlichen Systemzustand nicht.

Der alte Screensaver beendete bei Mausbewegung alle Chromium-Prozesse mittels
`pkill -f chromium`. Auf dem Spielsystem konnte das auch die Autodarts-Umgebung
treffen. Chromium und `WAYLAND_DISPLAY=wayland-0` waren fest codiert.

## Aktuelle Komponenten

- `app.py`: Webrouten, Score-Aggregation, Adminlogik und bestehender Autodarts-Import.
- `addon_system.py`: Manifest-Erkennung, sichere Dateiinstallation, systemd-Aktionen und Ist-Status.
- `Addons/Raspberry-Screensaver`: primäre Idle-Anzeige im Autodarts-Spielbetrieb.
- `Addons/Raspberry-Kiosk`: optionale permanente Scoreboard-Anzeige.
- `Addons/Raspberry-CEC`: TV-Zeitplan, CEC und HDMI-Audio.
- `install.sh`: Anwendung, Python-Umgebung, Pakete und Hauptservice.

JSON-Schreibvorgänge über `save_json` ersetzen Dateien atomar. Damit sehen
gleichzeitige Leser keine halb geschriebenen Konfigurations- oder Scoredateien.

## Sicherheitsgrenze

Der Adminbereich besitzt gemäß Einsatzvorgabe keine Authentifizierung und muss
deshalb in einem kontrollierten LAN bleiben. Die neue Systemintegrationsgrenze
ist unabhängig davon eng:

- ausschließlich mitgelieferte, validierte Manifeste;
- Ziele nur unter dem Home-Verzeichnis des Servicebenutzers;
- feste Service-Namen und Aktions-Allowlist;
- subprocess-Aufrufe als Argumentlisten, niemals `shell=True`;
- keine Root-Kommandos oder freien Browserparameter;
- Runtime-Konfiguration Modus `0600`;
- CEC-Gerätepfad ausschließlich `/dev/cecN`;
- Anwendungsservice mit `NoNewPrivileges`, `PrivateTmp`, eingeschränktem
  Systemdateisystem und restriktiver Umask.

## Autodarts-Synchronisation

Die Zugangsdaten liegen als `autodarts_email` und `autodarts_password` in
`data/config.json`. Der Installer setzt JSON-Dateien auf Modus `0600`. Das
Autodarts-Formular sendet dieselbe `save_config`-Aktion wie andere Adminformulare;
fehlende Felder behalten ihre bisherigen Werte.

Der gemeinsame Ablauf ist:

1. `POST /admin/run_autodarts` startet einen daemonisierten Worker-Thread im
   Modus `backfill` oder `incremental`.
2. Ein pro Prozess geltendes Lock verhindert parallele Läufe.
3. `autodarts_api_login` ruft
   `https://api.autodarts.com/auth/v1/login` mit 15 Sekunden Timeout auf.
4. Match-IDs kommen bevorzugt aus
   `https://api.autodarts.com/as/v0/matches/filter`; die Sortierung wird immer
   ausdrücklich gesetzt.
5. Gesamt- und Leg-Statistiken kommen aus
   `https://api.autodarts.com/as/v0/matches/{id}/stats`.
6. Wenn beim inkrementellen API-Abruf keine IDs gefunden werden, versucht
   Playwright als letzten Fallback die Seiten unter
   `https://play.autodarts.com/history/matches` mit DOM-Selektoren bzw. einem
   persistenten Browserprofil.
7. `import_match_result_to_scores` schreibt normale und BOT-Ergebnisse in die
   bestehenden JSON-Strukturen; `imported_matches.json` verhindert Doppelimport.
8. `/admin/autodarts_status` liefert Live-Status und das letzte Ergebnis.

### Erstabgleich und inkrementeller Betrieb

`autodarts_interval_minutes` ist ein wiederkehrendes Intervall und keine
Uhrzeit. Aktivierung, Intervalländerung oder eine überfällige persistierte
Ausführung lösen sofort einen Lauf aus. Nach Abschluss wird der nächste Termin
als „Ende + Intervall“ in `data/autodarts_sync_state.json` gespeichert. Ein
Neustart setzt den Countdown daher nicht zurück.

Solange `initial_import_completed` falsch ist, arbeitet der Scheduler im
`backfill`-Modus. Er lädt jeweils 50 Matches mit `sort=finished_at`, also älteste
zuerst, bis die API `last: true` meldet. `backfill_next_page` wird nach jeder
Seite gespeichert, sodass ein Abbruch fortgesetzt werden kann.

Danach verwendet er `incremental` und `sort=-finished_at`. Er beginnt immer bei
Seite 0 und liest weiter, bis eine vollständig bekannte Seite erreicht ist.
Die normale Adminoberfläche besitzt deshalb keine einstellbare Seitenzahl mehr.
Interne Sicherheitsgrenzen verhindern Endlosschleifen.

Noch nicht fertige Matches und temporäre Fehler werden in `pending_matches`
gespeichert und unabhängig von ihrer alten Verlaufsseite erneut versucht.
Permanente 4xx-Fehler bleiben als `failed` sichtbar und werden bei einem erneut
gestarteten vollständigen Abgleich wieder geprüft. Erst eine erfolgreich
gespeicherte Match-ID wird in `imported_matches.json` aufgenommen.

### Spiel- und Ereigniszeitpunkte

Autodarts-Einträge speichern zusätzlich:

- `autodarts_match_id` als eindeutige Herkunft und Bestandteil des Score-Hashes;
- `played_at` als UTC-Zeitpunkt des Match-Endes;
- `imported_at` als separaten technischen Importzeitpunkt;
- `last_180_at` aus dem konkreten 180er-Turn;
- `best_checkout_at` aus dem Turn beziehungsweise Leg des höchsten Checkouts;
- `bull_finishes` als Anzahl erfolgreicher Checkouts mit `D25` als letztem
  wertenden Dart und `last_bull_finish_at` als Zeitpunkt des letzten Treffers.

Das kompatible Feld `date` enthält für Autodarts das lokal formatierte
Spieldatum, für manuelle Einträge weiterhin den Erfassungszeitpunkt. Fehlen
Detailzeitpunkte, wird stufenweise auf Match-Ende, Match-Start, UUID-v7-Zeit und
zuletzt Importzeit zurückgefallen. „Letzte 180“ und „Höchster Checkout“ wählen
ihren zeitlich passenden Datensatz ausdrücklich per Zeitvergleich und hängen
nicht mehr von der Reihenfolge in `scores.json` ab.

Zusätzlich existieren der ausgeblendete manuelle Statistikimport (`/admin/import`), der Import
einer Match-ID/-URL (`/admin/import_match`) und der sichtbare manuelle
Playwright-Login (`/admin/autodarts_manual_login`). Der Scheduler liest alle fünf
Sekunden Konfiguration und startet im konfigurierten Minutenintervall.

Externe Abhängigkeiten bleiben:

- externe API-Routen und Playwright-DOM-Selektoren können sich ändern;
- ein persistentes Browserprofil kann durch eine andere Chromium-Instanz gesperrt sein;
- Zugangsdaten liegen technisch bedingt im lokalen JSON-Speicher und werden vom
  Passwortfeld wieder gerendert, dürfen also weder veröffentlicht noch geloggt werden.

## Noch auf echter Hardware zu prüfen

Windows kann Python, Templates, Manifestvalidierung und Prozessargumente testen,
aber nicht systemd, Labwc, CEC oder PipeWire emulieren. Vor Produktivsetzung sind
deshalb die Hardwaretests aus `SYSTEM_INTEGRATION.md` erforderlich.
