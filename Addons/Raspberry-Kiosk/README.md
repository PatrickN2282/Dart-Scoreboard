# Chromium-Kiosk

Dieses Add-on startet die öffentliche Scoreboard-Ansicht dauerhaft in einem
eigenen Chromium-Profil. Der Adminbereich installiert und verwaltet dafür den
systemd-User-Service `dart-kiosk.service`.

Der Launcher erkennt Labwc/Wayland und X11 zur Laufzeit, wartet auf die
grafische Sitzung sowie die lokale Scoreboard-URL und sucht sowohl `chromium`
als auch `chromium-browser`. Ein Desktop-Autostart-Eintrag importiert die
Session-Variablen in den systemd-User-Manager; der Dienst selbst übernimmt
Restart und Journal-Logging.

Diagnose:

```bash
systemctl --user status dart-kiosk.service
journalctl --user -u dart-kiosk.service -f
```
