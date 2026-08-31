import os
import re
import json
import random
import subprocess
import time
import shlex
import signal
import threading
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, jsonify
import socket
import requests
import qrcode
import io
import base64
import hashlib

from addon_system import (
    ALLOWED_ACTIONS,
    AddonError,
    addon_status,
    all_addon_statuses,
    discover_addons,
    manage_addon,
)

# --- Konfiguration ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
DATA_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data')
PLAYERS_FILE = os.path.join(DATA_DIR, 'players.json')
SCORES_FILE  = os.path.join(DATA_DIR, 'scores.json')
CONFIG_FILE  = os.path.join(DATA_DIR, 'config.json')
VERSION_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'VERSION')
# Ergebnisse gegen BOT-Gegner werden getrennt von der normalen Spieler-Statistik
# gespeichert, damit Bots nicht als Spieler auftauchen und "echte" Statistiken
# nicht durch (meist deutlich einfachere/schwerere) Bot-Matches verfälscht werden.
BOT_SCORES_FILE = os.path.join(DATA_DIR, 'bot_scores.json')

# Verzeichnis mit optionalen Zusatzfunktionen (z.B. Bildschirmschoner-Skripte)
ADDONS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'Addons')
CEC_CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'dart-scoreboard')
CEC_CONFIG_FILE = os.path.join(CEC_CONFIG_DIR, 'cec.conf')
SCREENSAVER_CONFIG_FILE = os.path.join(CEC_CONFIG_DIR, 'screensaver.conf')
SCREENSAVER_PID_FILE = os.path.join(CEC_CONFIG_DIR, 'screensaver.pid')
KIOSK_CONFIG_FILE = os.path.join(CEC_CONFIG_DIR, 'kiosk.conf')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

BACKGROUND_FILENAME = "background.jpg"

DEFAULT_CONFIG = {
    "background_url": None,
    "static_limit": 5,
    "rotation_limit": 10,
    "leaderboard_limit": 10,
    "static_h2_size": "2.5em",
    "rotation_h2_size": "3.5em",
    "static_td_size": "2.0em",
    "rotation_td_size": "1.5em",
    "font_family": "'Segoe UI', Roboto, sans-serif",
    # Wartezeiten (in Sekunden) für die einzelnen Ansichten der Rotation
    "rotation_duration_grid1": 300,
    "rotation_duration_grid2": 60,
    "rotation_duration_grid3": 60,
    "rotation_duration_rankings": 30,
    "rotation_duration_winrate": 60,
    "rotation_duration_player": 60,
    "rotation_duration_h2h": 60,
    # Minuten bis die Seite automatisch neu geladen wird
    "rotation_refresh_minutes": 10,
    "autodarts_email": "",
    "autodarts_password": "",
    "autodarts_enabled": False,
    "autodarts_interval_minutes": 60,
    "autodarts_user_data_dir": "",
    "cec_enabled": False,
    "cec_device_name": "Dart Scoreboard",
    "cec_standby_time": "22:00",
    "cec_wake_time": "08:00",
    "cec_adapter": "",
    "cec_check_interval": 50,
    "screensaver_idle_time": 300,
    "kiosk_url": "http://127.0.0.1:5000/",
    "kiosk_display_mode": "auto",
    "kiosk_hide_cursor": True,
}

# Datei für bereits importierte Match-IDs
IMPORTED_MATCHES_FILE = os.path.join(DATA_DIR, 'imported_matches.json')
AUTODARTS_SYNC_STATE_FILE = os.path.join(DATA_DIR, 'autodarts_sync_state.json')

# Status-Datei für laufende/letzte Autodarts-Läufe (für Live-Feedback im Admin-Bereich)
AUTODARTS_STATUS_FILE = os.path.join(DATA_DIR, 'autodarts_status.json')
AUTODARTS_LAST_RESULT_FILE = os.path.join(DATA_DIR, 'autodarts_last_result.json')
AUTODARTS_DEBUG_SCREENSHOT = os.path.join(DATA_DIR, 'autodarts_debug.png')
AUTODARTS_RUN_LOCK = threading.Lock()
AUTODARTS_SCHEDULER_LOCK = threading.Lock()
AUTODARTS_SYNC_STATE_LOCK = threading.RLock()
AUTODARTS_SCHEDULER_STARTED = False
JSON_WRITE_LOCK = threading.RLock()
AUTODARTS_PAGE_SIZE = 50
AUTODARTS_INCREMENTAL_MAX_PAGES = 10
AUTODARTS_BACKFILL_MAX_PAGES = 1000
try:
    AUTODARTS_LOCAL_TIMEZONE = ZoneInfo('Europe/Berlin')
except Exception:
    # Windows-Python enthält die IANA-Daten nicht immer systemweit. Das
    # requirements-Paket `tzdata` liefert sie bei Neuinstallationen; der lokale
    # Offset hält bestehende Entwicklungsumgebungen trotzdem startfähig.
    AUTODARTS_LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


# --- Hilfsfunktionen ---

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_qr_code(url: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=6,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="white", back_color="transparent")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def load_json(filepath):
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        if filepath == CONFIG_FILE:
            return DEFAULT_CONFIG
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if filepath == CONFIG_FILE:
                return {**DEFAULT_CONFIG, **data}
            return data
    except json.JSONDecodeError:
        if filepath == CONFIG_FILE:
            return DEFAULT_CONFIG
        return []


def get_app_version():
    try:
        with open(VERSION_FILE, 'r', encoding='utf-8') as f:
            version = f.read().strip()
        if re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', version):
            return version
    except OSError:
        pass
    return "0.0.0"


def write_cec_config(config):
    """Schreibt die vom CEC-Addon gelesene Laufzeitkonfiguration."""
    os.makedirs(CEC_CONFIG_DIR, mode=0o700, exist_ok=True)
    adapter = str(config.get('cec_adapter', '')).strip()
    if adapter and not re.fullmatch(r'/dev/cec[0-9]+', adapter):
        adapter = ''
    device_name = str(config.get('cec_device_name', 'Dart Scoreboard')).strip()
    if not re.fullmatch(r'[ -~]{1,14}', device_name):
        device_name = 'Dart Scoreboard'
    content = (
        "# Wird vom Dart Scoreboard Adminbereich verwaltet.\n"
        f"CEC_ENABLED={'1' if config.get('cec_enabled') else '0'}\n"
        f"CEC_NAME={shlex.quote(device_name)}\n"
        f"CEC_STANDBY_TIME={shlex.quote(config.get('cec_standby_time', '22:00'))}\n"
        f"CEC_WAKE_TIME={shlex.quote(config.get('cec_wake_time', '08:00'))}\n"
        f"CEC_ADAPTER={shlex.quote(adapter)}\n"
        f"CEC_CHECK_INTERVAL={min(3600, max(10, int(config.get('cec_check_interval', 50))))}\n"
    )
    with open(CEC_CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(CEC_CONFIG_FILE, 0o600)


def write_screensaver_config(config):
    """Schreibt die vom Screensaver-Addon gelesene Laufzeitkonfiguration."""
    os.makedirs(CEC_CONFIG_DIR, mode=0o700, exist_ok=True)
    content = (
        "# Wird vom Dart Scoreboard Adminbereich verwaltet.\n"
        f"SCREENSAVER_IDLE_TIME={int(config.get('screensaver_idle_time', 300))}\n"
    )
    with open(SCREENSAVER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(SCREENSAVER_CONFIG_FILE, 0o600)


def write_kiosk_config(config):
    """Write the validated runtime settings consumed by the kiosk launcher."""
    os.makedirs(CEC_CONFIG_DIR, mode=0o700, exist_ok=True)
    url = str(config.get('kiosk_url') or DEFAULT_CONFIG['kiosk_url']).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        url = DEFAULT_CONFIG['kiosk_url']
    display_mode = config.get('kiosk_display_mode', 'auto')
    if display_mode not in {'auto', 'wayland', 'x11'}:
        display_mode = 'auto'
    content = (
        "# Wird vom Dart Scoreboard Adminbereich verwaltet.\n"
        f"KIOSK_URL={shlex.quote(url)}\n"
        "KIOSK_BROWSER=''\n"
        f"KIOSK_DISPLAY_MODE={shlex.quote(display_mode)}\n"
        f"KIOSK_HIDE_CURSOR={'1' if config.get('kiosk_hide_cursor', True) else '0'}\n"
    )
    with open(KIOSK_CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(KIOSK_CONFIG_FILE, 0o600)


def restart_screensaver():
    """Startet den installierten Screensaver mit der aktuellen Konfiguration neu."""
    script_path = os.path.join(os.path.expanduser('~'), '.local', 'bin', 'dart-screensaver')
    if not os.path.isfile(script_path):
        return

    # Current installations are managed by systemd. Keep the PID based branch
    # below as a migration path for older desktop-autostart installations.
    service_path = os.path.join(
        os.path.expanduser('~'), '.config', 'systemd', 'user', 'dart-screensaver.service'
    )
    if os.path.isfile(service_path):
        result = subprocess.run(
            ['systemctl', '--user', 'try-restart', 'dart-screensaver.service'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode == 0:
            return

    pid = None
    try:
        with open(SCREENSAVER_PID_FILE, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            if b'dart-screensaver' not in f.read():
                raise OSError('Screensaver PID gehört nicht zum Dart Screensaver.')
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        # Installationen vor der PID-Datei-Version laufen direkt als swayidle.
        pid = None
        subprocess.run(
            ['pkill', '-f', r'^swayidle -w timeout [0-9]+ .*http://localhost:5000'],
            capture_output=True, text=True, timeout=5, check=False,
        )

    deadline = time.monotonic() + 5
    if pid is not None:
        while os.path.exists(SCREENSAVER_PID_FILE) and time.monotonic() < deadline:
            time.sleep(0.05)
        if os.path.exists(SCREENSAVER_PID_FILE):
            raise RuntimeError('Screensaver wurde nicht beendet.')
    else:
        while time.monotonic() < deadline:
            result = subprocess.run(
                ['pgrep', '-f', r'^swayidle -w timeout [0-9]+ .*http://localhost:5000'],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode != 0:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('Screensaver wurde nicht beendet.')

    subprocess.Popen(
        [script_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def save_json(filepath, data):
    """Atomically replace JSON files so readers never observe partial writes."""
    directory = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(directory, exist_ok=True)
    with JSON_WRITE_LOCK:
        fd, temporary_path = tempfile.mkstemp(prefix='.dart-scoreboard-', suffix='.json', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, filepath)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


def load_imported_matches():
    if not os.path.exists(IMPORTED_MATCHES_FILE) or os.path.getsize(IMPORTED_MATCHES_FILE) == 0:
        return []
    try:
        with open(IMPORTED_MATCHES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_imported_matches(lst):
    save_json(IMPORTED_MATCHES_FILE, lst)


def utc_now():
    return datetime.now(timezone.utc)


def datetime_to_iso(value):
    """Normalisiert einen Zeitpunkt als UTC-ISO-String."""
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            try:
                value = datetime.strptime(raw, '%d.%m.%Y %H:%M')
                value = value.replace(tzinfo=AUTODARTS_LOCAL_TIMEZONE)
            except ValueError:
                return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_datetime(value):
    normalized = datetime_to_iso(value)
    if not normalized:
        return None
    return datetime.fromisoformat(normalized.replace('Z', '+00:00'))


def format_local_datetime(value):
    parsed = parse_datetime(value)
    if not parsed:
        return ''
    return parsed.astimezone(AUTODARTS_LOCAL_TIMEZONE).strftime('%d.%m.%Y %H:%M')


def newest_timestamp(*values):
    parsed = [(parse_datetime(value), value) for value in values if value]
    parsed = [(stamp, original) for stamp, original in parsed if stamp is not None]
    if not parsed:
        return None
    return datetime_to_iso(max(parsed, key=lambda item: item[0])[0])


def uuid7_timestamp(match_id):
    """Liest den Zeitanteil einer UUIDv7 als letzten, rein lokalen Fallback."""
    try:
        compact = str(match_id).replace('-', '')
        if len(compact) != 32 or compact[12] != '7':
            return None
        milliseconds = int(compact[:12], 16)
        return datetime_to_iso(datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


def match_played_at(result, match_id=None):
    """Ermittelt den tatsächlichen Spielzeitpunkt mit abgestuften Fallbacks."""
    for key in ('finishedAt', 'finished_at', 'played_at'):
        value = datetime_to_iso((result or {}).get(key))
        if value:
            return value

    game_times = []
    for game in (result or {}).get('games', []) or []:
        for key in ('finishedAt', 'finished_at'):
            value = datetime_to_iso(game.get(key))
            if value:
                game_times.append(value)
                break
    if game_times:
        return newest_timestamp(*game_times)

    for key in ('createdAt', 'created_at'):
        value = datetime_to_iso((result or {}).get(key))
        if value:
            return value

    game_times = []
    for game in (result or {}).get('games', []) or []:
        for key in ('createdAt', 'created_at'):
            value = datetime_to_iso(game.get(key))
            if value:
                game_times.append(value)
                break
    if game_times:
        return newest_timestamp(*game_times)
    return uuid7_timestamp(match_id or (result or {}).get('id'))


def turn_finished_at(turn, fallback=None):
    for key in ('finishedAt', 'finished_at'):
        value = datetime_to_iso((turn or {}).get(key))
        if value:
            return value
    throw_times = []
    for throw in (turn or {}).get('throws', []) or []:
        for key in ('finishedAt', 'finished_at', 'createdAt', 'created_at'):
            value = datetime_to_iso(throw.get(key))
            if value:
                throw_times.append(value)
                break
    if throw_times:
        return newest_timestamp(*throw_times)
    for key in ('createdAt', 'created_at'):
        value = datetime_to_iso((turn or {}).get(key))
        if value:
            return value
    return datetime_to_iso(fallback)


def is_double_bull_throw(throw):
    """Erkennt das innere Bullseye unabhängig von kleinen API-Varianten."""
    throw = throw or {}
    segment = throw.get('segment') or {}
    name = str(segment.get('name') or throw.get('segmentName') or '').strip().upper()
    number = segment.get('number', throw.get('number'))
    multiplier = throw.get('multiplier')
    if multiplier is None:
        multiplier = segment.get('multiplier')
    try:
        number = int(number)
    except (TypeError, ValueError):
        number = None
    try:
        multiplier = int(multiplier)
    except (TypeError, ValueError):
        multiplier = None
    return name in {'D25', 'DB', 'DBULL', 'DOUBLEBULL', 'INNERBULL', 'BULLSEYE'} or (
        number == 25 and multiplier == 2
    )


def last_scoring_throw(turn):
    """Liefert den letzten wertenden Dart eines Turns (ohne aufgefüllte Misses)."""
    throws = (turn or {}).get('throws') or []
    scoring = []
    for throw in throws:
        segment = throw.get('segment') or {}
        number = segment.get('number', throw.get('number'))
        multiplier = throw.get('multiplier')
        if multiplier is None:
            multiplier = segment.get('multiplier')
        try:
            value = int(number or 0) * int(multiplier or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0 and is_double_bull_throw(throw):
            value = 50
        if value > 0:
            scoring.append(throw)
    return scoring[-1] if scoring else (throws[-1] if throws else None)


def default_autodarts_sync_state():
    return {
        'schema_version': 1,
        'initial_import_completed': False,
        'backfill_next_page': 0,
        'pending_matches': {},
        'last_check_at': None,
        'last_success_at': None,
        'next_check_at': None,
        'interval_minutes': None,
        'newest_finished_at': None,
    }


def load_autodarts_sync_state():
    with AUTODARTS_SYNC_STATE_LOCK:
        raw = load_json(AUTODARTS_SYNC_STATE_FILE)
        if not isinstance(raw, dict):
            raw = {}
        state = {**default_autodarts_sync_state(), **raw}
        if not isinstance(state.get('pending_matches'), dict):
            state['pending_matches'] = {}
        return state


def save_autodarts_sync_state(state):
    with AUTODARTS_SYNC_STATE_LOCK:
        save_json(AUTODARTS_SYNC_STATE_FILE, state)


def load_autodarts_status():
    """Liest den aktuellen Status eines Autodarts-Laufs (für Polling im Admin-Bereich)."""
    if not os.path.exists(AUTODARTS_STATUS_FILE) or os.path.getsize(AUTODARTS_STATUS_FILE) == 0:
        return {"state": "idle", "message": "Noch kein Lauf gestartet."}
    try:
        with open(AUTODARTS_STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"state": "idle", "message": "Noch kein Lauf gestartet."}


def save_autodarts_status(state: str, message: str = "", **extra):
    """Schreibt den aktuellen Status eines Autodarts-Laufs, z.B. 'running', 'success', 'error'."""
    status = {
        "state": state,
        "message": message,
        "updated_at": datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
    }
    status.update(extra)
    try:
        save_json(AUTODARTS_STATUS_FILE, status)
    except Exception:
        pass


def autodarts_api_login(email: str, password: str):
    """Loggt sich über die Autodarts-API ein (unabhängig von Playwright/DOM-Selektoren).
    Gibt (token, error_message) zurück. token ist None bei Fehlschlag."""
    if not email or not password:
        return None, "Autodarts-Credentials nicht konfiguriert"
    try:
        auth_resp = requests.post(
            'https://api.autodarts.com/auth/v1/login',
            json={'client_id': 'autodarts-play', 'email': email, 'password': password},
            timeout=15,
        )
    except Exception as e:
        return None, f"API-Login fehlgeschlagen (Verbindung): {e}"

    if auth_resp.status_code == 401:
        return None, "Falsche Zugangsdaten (E-Mail/Passwort)."
    if not auth_resp.ok:
        return None, f"API-Login fehlgeschlagen (Status {auth_resp.status_code})."

    try:
        token = auth_resp.json().get('access_token')
    except Exception:
        token = None

    if not token:
        return None, "API-Login lieferte keinen Access-Token zurück."
    return token, None


def normalized_player_name(name):
    return (name or "").strip().casefold()


def get_player_stat_names(player):
    """Liefert alle Namen, unter denen ein Spieler in Statistiken erscheinen kann."""
    names = []
    for name in [player.get("autodarts_name"), *(player.get("stat_names") or [])]:
        normalized = normalized_player_name(name)
        if normalized and normalized not in {normalized_player_name(value) for value in names}:
            names.append(name.strip())
    return names


def find_player_by_stat_names(players, names):
    """Findet einen Spieler anhand eines Autodarts-Handles oder Statistik-Alias."""
    candidates = {normalized_player_name(name) for name in names}
    candidates.discard("")
    if not candidates:
        return None

    for player in players:
        if candidates.intersection(
            normalized_player_name(name) for name in get_player_stat_names(player)
        ):
            return player
    return None


def consolidate_duplicate_players(players, scores=None, bot_scores=None):
    """Migriert alte, doppelte Handle-Einträge zu einer Spieleridentität."""
    changed = False
    for player in players:
        aliases = list(player.get("stat_names") or [])
        if "stat_names" not in player and player.get("name"):
            aliases.append(player["name"])
        aliases.append(player.get("autodarts_name", ""))
        unique_aliases = []
        seen_aliases = set()
        for alias in aliases:
            normalized = normalized_player_name(alias)
            if normalized and normalized not in seen_aliases:
                seen_aliases.add(normalized)
                unique_aliases.append(alias.strip())
        if player.get("stat_names") != unique_aliases:
            player["stat_names"] = unique_aliases
            changed = True

    players_by_handle = {}
    retained_players = []
    id_mapping = {}
    for player in players:
        handle = normalized_player_name(player.get("autodarts_name"))
        primary = players_by_handle.get(handle) if handle else None
        if primary is None:
            retained_players.append(player)
            if handle:
                players_by_handle[handle] = player
            continue

        changed = True
        id_mapping[player["id"]] = primary["id"]
        for alias in get_player_stat_names(player):
            if normalized_player_name(alias) not in {
                normalized_player_name(value) for value in primary["stat_names"]
            }:
                primary["stat_names"].append(alias)
        if primary.get("image", "dummy.png") == "dummy.png" and player.get("image") != "dummy.png":
            primary["image"] = player["image"]

    if id_mapping:
        for score_list in (scores, bot_scores):
            if score_list is None:
                continue
            for score in score_list:
                player_id = score.get("player_id")
                if player_id in id_mapping:
                    score["player_id"] = id_mapping[player_id]

    return retained_players, changed or bool(id_mapping)


def get_player_id(player_name, players):
    normalized_name = player_name.strip()
    if not normalized_name:
        return None

    for p in players:
        if p["name"] == normalized_name:
            return p["id"]

    new_id = max((p["id"] for p in players), default=0) + 1
    new_player = {
        "id": new_id,
        "name": normalized_name,
        "image": "dummy.png",
        "autodarts_name": "",
        "stat_names": [normalized_name],
    }
    players.append(new_player)
    save_json(PLAYERS_FILE, players)
    return new_id


def get_player_by_id(player_id):
    players = load_json(PLAYERS_FILE)
    for p in players:
        if p["id"] == player_id:
            return p
    return None


# --- BOT-Erkennung ---
# Autodarts-Bot-Gegner tragen zuverlässig den Namen "BOT LEVEL X", wobei X
# zwischen 1 und 9 liegt. Andere Felder der API werden bewusst nicht für die
# Erkennung verwendet, damit menschliche Gegner nicht falsch herausgefiltert
# werden.
BOT_LEVEL_NAME_RE = re.compile(r'^BOT LEVEL ([1-9])$', re.IGNORECASE)


def detect_bot_level(name):
    """Ermittelt das Bot-Level eines Spielers.

    Gibt das Bot-Level zurück oder None, wenn es sich um einen menschlichen
    Spieler handelt."""
    name = (name or '').strip()
    m = BOT_LEVEL_NAME_RE.match(name)
    if m:
        return int(m.group(1))
    return None


def is_bot_player(p):
    """p: rohes Spieler-dict aus der Autodarts-API (result['players'])."""
    name = p.get('name') or p.get('username') or ''
    return detect_bot_level(name) is not None


def background_exists():
    return os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME))


def add_podium_rank(entries: list, sort_key: str) -> list:
    PODIUM = {1: "gold", 2: "silver", 3: "bronze"}
    for i, entry in enumerate(entries):
        if i == 0:
            entry["rank"] = 1
        else:
            prev_val = entries[i - 1].get(sort_key)
            curr_val = entry.get(sort_key)
            if curr_val == prev_val:
                entry["rank"] = entries[i - 1]["rank"]
            else:
                entry["rank"] = i + 1
        entry["podium_class"] = PODIUM.get(entry["rank"], "")
    return entries


def calculate_win_rate_score(wins: int, total: int) -> float:
    if total == 0:
        return 0.0
    win_rate = wins / total
    weight = min(total / 10.0, 1.0)
    return win_rate * weight


def get_scoreboard_player_groups(players_list):
    """Fasst alte doppelte Handle-Einträge für die Anzeige zusammen."""
    player_ids = {}
    players_map = {}
    display_players = []
    autodarts_groups = {}

    for player in players_list:
        player_id = player.get("id")
        if player_id is None:
            continue

        autodarts_name = normalized_player_name(player.get("autodarts_name"))
        if autodarts_name:
            display_id = autodarts_groups.get(autodarts_name)
            if display_id is None:
                display_id = player_id
                autodarts_groups[autodarts_name] = display_id
        else:
            display_id = player_id

        player_ids[player_id] = display_id
        if display_id not in players_map:
            players_map[display_id] = player
            display_players.append(player)

    return player_ids, players_map, display_players


def get_cumulative_stats():
    """Berechnet alle kumulativen Statistiken und gibt sie zurück"""
    scores = load_json(SCORES_FILE)
    players_list = load_json(PLAYERS_FILE)
    player_ids, players_map, display_players = get_scoreboard_player_groups(players_list)

    cumulative = {}
    for s in scores:
        pid = player_ids.get(s.get("player_id"))
        if pid is None:
            continue
        if pid not in cumulative:
            cumulative[pid] = {
                "legs": 0, "max180": 0, "last180_date": "", "last180_at": None,
                "bull_finishes": 0, "last_bull_finish_date": "", "last_bull_finish_at": None,
                "s60": 0, "s100": 0, "s140": 0, "s170": 0, "s180": 0,
                "games_played": 0,
                "best_finish": 0,
                # extended aggregates
                "points_sum": 0,
                "darts_thrown": 0,
                "first9_points_sum": 0,
                "first9_darts": 0,
                "first3_points_sum": 0,
                "first3_darts": 0,
                "checkout_success": 0,
                "checkout_attempts": 0,
                "segment_hits": {},
                "classic_26": 0,
                "checkout_finishes": {},
            }
        cumulative[pid]["legs"]   += s.get("legs",   0)
        cumulative[pid]["max180"] += s.get("max180", 0)
        cumulative[pid]["s60"]    += s.get("s60",    0)
        cumulative[pid]["s100"]   += s.get("s100",   0)
        cumulative[pid]["s140"]   += s.get("s140",   0)
        cumulative[pid]["s170"]   += s.get("s170",   0)
        cumulative[pid]["s180"]   += s.get("s180",   0)
        cumulative[pid]["games_played"] += s.get("games_played", 0)
        cumulative[pid]["bull_finishes"] += s.get("bull_finishes", 0) or 0
        # extended sums
        cumulative[pid]["points_sum"] += s.get("points_sum", 0) or 0
        cumulative[pid]["darts_thrown"] += s.get("darts_thrown", 0) or 0
        cumulative[pid]["first9_points_sum"] += s.get("first9_points_sum", 0) or 0
        cumulative[pid]["first9_darts"] += s.get("first9_darts", 0) or 0
        cumulative[pid]["first3_points_sum"] += s.get("first3_points_sum", 0) or 0
        cumulative[pid]["first3_darts"] += s.get("first3_darts", 0) or 0
        cumulative[pid]["checkout_success"] += s.get("checkout_success", 0) or 0
        cumulative[pid]["checkout_attempts"] += s.get("checkout_attempts", 0) or 0
        cumulative[pid]["classic_26"] += s.get("classic_26", 0) or 0
        # merge segment_hits dict
        segs = s.get("segment_hits") or {}
        for k,v in segs.items():
            cumulative[pid]["segment_hits"][k] = cumulative[pid]["segment_hits"].get(k, 0) + (v or 0)
        finishes = s.get("checkout_finishes") or {}
        for checkout, count in finishes.items():
            checkout_key = str(checkout)
            cumulative[pid]["checkout_finishes"][checkout_key] = (
                cumulative[pid]["checkout_finishes"].get(checkout_key, 0) + (count or 0)
            )
        
        finish_val = s.get("finish", 0)
        if finish_val > cumulative[pid].get("best_finish", 0):
            cumulative[pid]["best_finish"] = finish_val
            
        if s.get("max180", 0) > 0 or s.get("s180", 0) > 0:
            candidate_raw = s.get('last_180_at') or s.get('played_at') or s.get('date')
            candidate = parse_datetime(candidate_raw)
            current = parse_datetime(cumulative[pid].get('last180_at'))
            if candidate and (current is None or candidate > current):
                cumulative[pid]['last180_at'] = datetime_to_iso(candidate)
                cumulative[pid]['last180_date'] = format_local_datetime(candidate)
            elif current is None and not cumulative[pid].get('last180_date'):
                # Alte, nicht parsebare manuelle Datumswerte bleiben sichtbar.
                cumulative[pid]['last180_date'] = s.get('date', '')

        if s.get('bull_finishes', 0) > 0:
            candidate_raw = s.get('last_bull_finish_at') or s.get('played_at') or s.get('date')
            candidate = parse_datetime(candidate_raw)
            current = parse_datetime(cumulative[pid].get('last_bull_finish_at'))
            if candidate and (current is None or candidate > current):
                cumulative[pid]['last_bull_finish_at'] = datetime_to_iso(candidate)
                cumulative[pid]['last_bull_finish_date'] = format_local_datetime(candidate)
            elif current is None and not cumulative[pid].get('last_bull_finish_date'):
                cumulative[pid]['last_bull_finish_date'] = s.get('date', '')

    return cumulative, players_map, display_players, player_ids


def get_bot_cumulative_stats():
    """Berechnet kumulative Statistiken pro Spieler getrennt nach BOT-Level.

    Ergebnisse gegen BOTs fließen nie in get_cumulative_stats() ein; stattdessen
    werden sie hier separat je (Spieler, Bot-Level) aggregiert, damit man sich
    z.B. anschauen kann, wie gut man gegen "Bot Level 3" abschneidet, ohne dass
    dies die "echte" Spielerstatistik verfälscht."""
    bot_scores = load_json(BOT_SCORES_FILE)
    players_list = load_json(PLAYERS_FILE)
    players_map = {p["id"]: p for p in players_list}

    cumulative = {}
    for s in bot_scores:
        pid = s.get("player_id")
        if pid not in players_map:
            continue
        level = s.get("bot_level")
        key = (pid, level)
        if key not in cumulative:
            cumulative[key] = {
                "player_id": pid,
                "bot_level": level,
                "legs": 0,
                "games_played": 0,
                "points_sum": 0,
                "darts_thrown": 0,
            }
        cumulative[key]["legs"] += s.get("legs", 0) or 0
        cumulative[key]["games_played"] += s.get("games_played", 0) or 0
        cumulative[key]["points_sum"] += s.get("points_sum", 0) or 0
        cumulative[key]["darts_thrown"] += s.get("darts_thrown", 0) or 0

    result = []
    for (pid, level), vals in cumulative.items():
        darts = vals["darts_thrown"] or 0
        pts = vals["points_sum"] or 0
        result.append({
            "player_id": pid,
            "name": players_map.get(pid, {}).get("name", "Unbekannt"),
            "bot_level": level,
            "legs": vals["legs"],
            "games_played": vals["games_played"],
            "average": round(float(pts) / float(darts) * 3.0, 2) if darts else None,
        })
    result.sort(key=lambda x: (x["name"], str(x["bot_level"]) if x["bot_level"] is not None else ""))
    return result


def compute_score_hash(score_obj: dict) -> str:
    """Erzeugt eine konsistente Signatur für einen Score-Eintrag zur Duplikaterkennung."""
    keys = [
        "player_id", "legs", "finish", "max180", "darts301",
        "s60", "s100", "s140", "s170", "s180", "games_played"
    ]
    normalized = {k: int(score_obj.get(k, 0) or 0) if k != "player_id" else int(score_obj.get(k) or 0) for k in keys}
    # Zwei unterschiedliche Matches können zufällig exakt dieselben Statistiken
    # erzeugen. Für Autodarts-Einträge gehört deshalb die Match-ID zur Signatur;
    # manuelle und ältere Einträge behalten ihre bisherige Hash-Berechnung.
    if score_obj.get('autodarts_match_id'):
        normalized['autodarts_match_id'] = str(score_obj['autodarts_match_id'])
    # stable serialization
    payload = json.dumps(normalized, sort_keys=True, separators=(',', ':'))
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()


def extract_stats_from_result(res):
    """Extrahiert aggregierte Statistiken aus einer Match-API-Antwort.
    Rückgabe: dict mapping playerId -> stats dict (s60/s100/.../average/first9_average/...)
    """
    stats = {}
    players = res.get('players', [])
    # Determine whether match-level aggregated stats are provided by the API
    match_stats_raw = res.get('matchStats') or {}
    use_match_level = bool(match_stats_raw)

    # helper to read from possible matchStats shapes
    def _find_match_stats_for(pid):
        if not match_stats_raw:
            return None
        if isinstance(match_stats_raw, dict):
            return match_stats_raw.get(pid) or match_stats_raw.get(str(pid))
        if isinstance(match_stats_raw, list):
            for item in match_stats_raw:
                if item.get('playerId') == pid or item.get('id') == pid or str(item.get('playerId')) == str(pid):
                    return item
        return None

    # init
    for p in players:
        key = p.get('id') or p.get('userId') or p.get('name')
        stats[key] = {
            'autodarts_name': p.get('name') or p.get('username') or '',
            'legs': 0,
            's60': 0,
            's100': 0,
            's140': 0,
            's170': 0,
            's180': 0,
            'max180': 0,
            'bestCheckout': 0,
            'best_checkout_at': None,
            'min_darts_to_checkout': None,
            'checkout_success': 0,
            'checkout_attempts': 0,
            'points_sum': 0,
            'darts_thrown': 0,
            'first9_points_sum': 0,
            'first9_darts': 0,
            'first3_points_sum': 0,
            'first3_darts': 0,
            'segment_hits': {},
            'classic_26': 0,
            'checkout_finishes': {},
            'last_180_at': None,
            'bull_finishes': 0,
            'last_bull_finish_at': None,
        }

    # fill legs from scores summary
    scores_arr = res.get('scores') or []
    for idx, s in enumerate(scores_arr):
        if idx < len(players):
            p = players[idx]
            key = p.get('id') or p.get('userId') or p.get('name')
            try:
                stats[key]['legs'] = int(s.get('legs', 0) or 0)
            except Exception:
                pass

    # If API provides match-level aggregated stats, use them for the main metrics
    if use_match_level:
        for p in players:
            key = p.get('id') or p.get('userId') or p.get('name')
            m = _find_match_stats_for(key)
            if not m:
                continue
            # map common fields if present
            def _get_any(src, keys, default=0):
                for k in keys:
                    if k in src and src.get(k) is not None:
                        return src.get(k)
                return default

            darts_val = int(_get_any(m, ['dartsThrown', 'darts_thrown', 'darts']) or 0)
            raw_points = _get_any(m, ['score', 'scoreTotal', 'points_sum', 'points', 'pointsSum'])
            avg_val = m.get('average')
            # Autodarts' aggregated matchStats reports 'score' as 0 (it only carries a
            # real total on the per-leg stats endpoint), so fall back to deriving the
            # total scored points from the (reliable) average and darts thrown instead
            # of trusting a 0 that would otherwise wipe out the average below.
            if not raw_points and isinstance(avg_val, (int, float)) and darts_val:
                raw_points = avg_val * darts_val / 3.0
            stats[key]['points_sum'] = int(round(raw_points or 0))
            stats[key]['darts_thrown'] = darts_val
            stats[key]['s60'] = int(_get_any(m, ['plus60', 'plus_60', 's60', 'count60']) or 0)
            stats[key]['s100'] = int(_get_any(m, ['plus100', 'plus_100', 's100', 'count100']) or 0)
            stats[key]['s140'] = int(_get_any(m, ['plus140', 'plus_140', 's140', 'count140']) or 0)
            stats[key]['s170'] = int(_get_any(m, ['plus170', 'plus_170', 's170', 'count170']) or 0)
            stats[key]['s180'] = int(_get_any(m, ['total180', 'total_180', 's180', 'count180']) or 0)
            stats[key]['max180'] = int(_get_any(m, ['max180', 'max_180', 'total180']) or 0)
            f9_darts_val = int(_get_any(m, ['first9Darts', 'first9_darts']) or 0)
            raw_f9_points = _get_any(m, ['first9Score', 'first9_points_sum', 'first9Points'])
            f9_avg_val = m.get('first9Average')
            if not raw_f9_points and isinstance(f9_avg_val, (int, float)) and f9_darts_val:
                raw_f9_points = f9_avg_val * f9_darts_val / 3.0
            stats[key]['first9_points_sum'] = int(round(raw_f9_points or 0))
            stats[key]['first9_darts'] = f9_darts_val
            stats[key]['checkout_attempts'] = int(_get_any(m, ['checkouts', 'checkoutAttempts', 'checkout_attempts']) or 0)
            stats[key]['checkout_success'] = int(_get_any(m, ['checkoutsHit', 'checkout_success', 'checkoutHits']) or 0)
            stats[key]['bestCheckout'] = int(_get_any(m, ['checkoutPoints', 'bestCheckout', 'best_finish', 'best_checkout']) or 0)
            # set provided averages if present; these take precedence over the values
            # derived from points_sum/darts_thrown further below, since the API's
            # 'average'/'first9Average' fields are authoritative when present.
            if isinstance(avg_val, (int, float)):
                stats[key]['average'] = float(avg_val)
            if isinstance(f9_avg_val, (int, float)):
                stats[key]['first9_average'] = float(f9_avg_val)
            # checkout ratio if available
            if m.get('checkoutPercent') is not None:
                try:
                    stats[key]['checkout_ratio'] = float(m.get('checkoutPercent'))
                except Exception:
                    pass
            # average will be recomputed below from points_sum/darts_thrown only when the
            # API didn't already provide it (see finalize step)

    # Always collect segment hits and the exact timestamp of the latest 180 from
    # per-leg turns. Main aggregates are not recomputed when match-level stats
    # are available.
    played_at_fallback = match_played_at(res, res.get('id'))
    for game in res.get('games', []):
        for turn in game.get('turns', []) or []:
            pid = turn.get('playerId')
            if not pid:
                continue
            st = stats.setdefault(pid, {})
            try:
                turn_points = int(turn.get('points', 0) or 0)
            except (TypeError, ValueError):
                turn_points = 0
            if turn_points == 180 and not turn.get('busted'):
                candidate = turn_finished_at(turn, played_at_fallback)
                st['last_180_at'] = newest_timestamp(st.get('last_180_at'), candidate)
            throws = turn.get('throws') or []
            turn_segment_keys = []
            for th in throws:
                seg = th.get('segment') or {}
                num = seg.get('number') or seg.get('name')
                if num is None:
                    continue
                try:
                    num_s = str(num)
                except Exception:
                    num_s = str(num)
                mult = th.get('multiplier') if th.get('multiplier') is not None else seg.get('multiplier')
                try:
                    mult = int(mult)
                except Exception:
                    mult = 1
                prefix = 'S'
                if mult == 2:
                    prefix = 'D'
                elif mult == 3:
                    prefix = 'T'
                key = f"{prefix}{num_s}"
                turn_segment_keys.append(key.upper())
                seg_hits = st.setdefault('segment_hits', {})
                seg_hits[key] = seg_hits.get(key, 0) + 1
            if (
                not turn.get('busted')
                and turn_points == 26
                and len(throws) == 3
                and sorted(turn_segment_keys) == ['S1', 'S20', 'S5']
            ):
                st['classic_26'] = int(st.get('classic_26', 0) or 0) + 1

    # Zeitpunkt des höchsten Checkouts: bevorzugt wird der letzte Zug des
    # jeweiligen Leg-Gewinners. `legStats` liefert den Checkout-Wert, die
    # zugehörige Spiel-/Turn-Struktur den echten Zeitpunkt.
    leg_stats_entries = res.get('legStats') or []
    for game_index, game in enumerate(res.get('games', []) or []):
        winner_pid = game.get('winnerPlayerId')
        winner_index = game.get('winner')
        if not winner_pid and isinstance(winner_index, int) and 0 <= winner_index < len(players):
            winner = players[winner_index]
            winner_pid = winner.get('id') or winner.get('userId') or winner.get('name')
        if not winner_pid:
            continue

        winner_turns = [
            turn for turn in game.get('turns', []) or []
            if turn.get('playerId') == winner_pid and not turn.get('busted')
        ]
        checkout_turns = [
            turn for turn in winner_turns
            if str(turn.get('score')).strip() == '0'
        ]
        winning_turn = (checkout_turns or winner_turns)[-1] if winner_turns else None
        try:
            checkout_points = int((winning_turn or {}).get('points', 0) or 0)
        except (TypeError, ValueError):
            checkout_points = 0

        if game_index < len(leg_stats_entries):
            leg = leg_stats_entries[game_index] or {}
            leg_values = leg.get('stats') or []
            leg_winner = leg.get('winner')
            if isinstance(leg_winner, int) and 0 <= leg_winner < len(leg_values):
                try:
                    checkout_points = int(
                        leg_values[leg_winner].get('checkoutPoints') or checkout_points
                    )
                except (TypeError, ValueError):
                    pass

        if checkout_points <= 0:
            continue
        st = stats.setdefault(winner_pid, {})
        checkout_finishes = st.setdefault('checkout_finishes', {})
        checkout_key = str(checkout_points)
        checkout_finishes[checkout_key] = checkout_finishes.get(checkout_key, 0) + 1
        current_best = int(st.get('bestCheckout', 0) or 0)
        checkout_at = turn_finished_at(
            winning_turn or {},
            game.get('finishedAt') or game.get('finished_at') or played_at_fallback,
        )
        if checkout_points > current_best:
            st['bestCheckout'] = checkout_points
            st['best_checkout_at'] = checkout_at
        elif checkout_points == current_best:
            st['best_checkout_at'] = newest_timestamp(st.get('best_checkout_at'), checkout_at)

        # Ein Bull-Finish liegt nur vor, wenn der letzte wertende Dart des
        # erfolgreichen Sieger-Turns das innere Bullseye (D25) getroffen hat.
        # Normale Bull-Treffer in früheren Turns werden dadurch nicht mitgezählt.
        finishing_throw = last_scoring_throw(winning_turn)
        if finishing_throw and is_double_bull_throw(finishing_throw):
            st['bull_finishes'] = int(st.get('bull_finishes', 0) or 0) + 1
            throw_at = None
            for timestamp_key in ('finishedAt', 'finished_at', 'createdAt', 'created_at'):
                throw_at = datetime_to_iso(finishing_throw.get(timestamp_key))
                if throw_at:
                    break
            st['last_bull_finish_at'] = newest_timestamp(
                st.get('last_bull_finish_at'),
                throw_at or checkout_at,
            )

    # "First 3 Average" (Punkte-Schnitt des allerersten Aufnahme/Visits jedes Legs,
    # d.h. der ersten 3 geworfenen Darts). Autodarts liefert dafür (anders als für
    # "first9Average") kein aggregiertes Feld, daher wird dieser Wert - unabhängig
    # davon ob match-level Stats vorliegen - immer aus den Turns berechnet.
    for game in res.get('games', []):
        turns_by_player_f3 = {}
        for turn in game.get('turns', []) or []:
            pid = turn.get('playerId')
            if not pid:
                continue
            turns_by_player_f3.setdefault(pid, []).append(turn)
        for pid, turns_list in turns_by_player_f3.items():
            first_turn = turns_list[:1]
            for t in first_turn:
                if t.get('busted'):
                    continue
                pts = int(t.get('points', 0) or 0)
                throws = t.get('throws') or []
                darts = len(throws)
                st = stats.setdefault(pid, {})
                st['first3_points_sum'] = st.get('first3_points_sum', 0) + pts
                st['first3_darts'] = st.get('first3_darts', 0) + darts

    # If match-level stats were not present, fall back to computing aggregates from turns
    if not use_match_level:
        for game in res.get('games', []):
            winner_pid = game.get('winnerPlayerId') or game.get('winner')
            turns_by_player = {}
            for turn in game.get('turns', []):
                pid = turn.get('playerId')
                if not pid:
                    continue
                turns_by_player.setdefault(pid, []).append(turn)

            for pid, turns_list in turns_by_player.items():
                first_three = turns_list[:3]
                for t in first_three:
                    if t.get('busted'):
                        continue
                    pts = int(t.get('points', 0) or 0)
                    throws = t.get('throws') or []
                    darts = len(throws)
                    stats.setdefault(pid, {}).setdefault('first9_points_sum', 0)
                    stats.setdefault(pid, {}).setdefault('first9_darts', 0)
                    stats[pid]['first9_points_sum'] = stats[pid].get('first9_points_sum', 0) + pts
                    stats[pid]['first9_darts'] = stats[pid].get('first9_darts', 0) + darts

            for turn in game.get('turns', []):
                pid = turn.get('playerId')
                if not pid:
                    continue
                st = stats.setdefault(pid, {
                    'autodarts_name': '', 'legs': 0, 's60': 0, 's100': 0,
                    's140': 0, 's170': 0, 's180': 0, 'max180': 0,
                    'bestCheckout': 0, 'min_darts_to_checkout': None,
                    'best_checkout_at': None,
                    'checkout_success': 0, 'checkout_attempts': 0,
                    'points_sum': 0, 'darts_thrown': 0,
                    'first9_points_sum': 0, 'first9_darts': 0,
                    'segment_hits': {},
                    'classic_26': 0,
                    'checkout_finishes': {},
                    'last_180_at': None,
                    'bull_finishes': 0,
                    'last_bull_finish_at': None,
                })
                if turn.get('busted'):
                    continue
                try:
                    pts = int(turn.get('points', 0) or 0)
                except Exception:
                    pts = 0
                throws = turn.get('throws') or []
                darts = len(throws)
                if pts == 180:
                    st['s180'] = st.get('s180', 0) + 1
                elif 170 <= pts < 180:
                    st['s170'] = st.get('s170', 0) + 1
                elif 140 <= pts < 170:
                    st['s140'] = st.get('s140', 0) + 1
                elif 100 <= pts < 140:
                    st['s100'] = st.get('s100', 0) + 1
                elif 60 <= pts < 100:
                    st['s60'] = st.get('s60', 0) + 1
                if pts == 180:
                    st['max180'] = max(st.get('max180', 0), 1)
                st['points_sum'] = st.get('points_sum', 0) + pts
                st['darts_thrown'] = st.get('darts_thrown', 0) + darts

                checkout_pts = None
                if 'checkoutPoints' in turn:
                    try:
                        checkout_pts = int(turn.get('checkoutPoints') or 0)
                    except Exception:
                        checkout_pts = None
                elif isinstance(turn.get('checkout'), (int, float)):
                    try:
                        checkout_pts = int(turn.get('checkout') or 0)
                    except Exception:
                        checkout_pts = None

                attempt = False
                if turn.get('checkoutAttempt') or turn.get('checkoutAttempted') or ('checkoutAttempt' in turn):
                    attempt = True
                else:
                    for th in throws:
                        seg = th.get('segment') or {}
                        if seg.get('bed') == 'D' or seg.get('multiplier') == 2:
                            attempt = True
                            break

                if attempt:
                    st['checkout_attempts'] = st.get('checkout_attempts', 0) + 1

                if winner_pid and pid == winner_pid and checkout_pts and checkout_pts > 0:
                    st['bestCheckout'] = max(st.get('bestCheckout', 0) or 0, checkout_pts)
                    st['checkout_success'] = st.get('checkout_success', 0) + 1
                    if darts:
                        prev = st.get('min_darts_to_checkout')
                        if prev is None or darts < prev:
                            st['min_darts_to_checkout'] = darts

    # Fewest darts thrown for a finish (checkout): the Autodarts "stats" endpoint
    # provides a 'legStats' list with one entry per leg, each holding per-player
    # stats (including 'dartsThrown') plus the index of the winning player. The leg
    # winner's 'dartsThrown' is the number of darts they needed to check out that
    # leg, so track the smallest value seen per player. This is independent of
    # whether match-level aggregated stats are available and therefore always runs.
    for leg in (res.get('legStats') or []):
        leg_stats_list = leg.get('stats') or []
        winner_idx = leg.get('winner')
        if not isinstance(winner_idx, int) or not (0 <= winner_idx < len(leg_stats_list)):
            continue
        winner_stat = leg_stats_list[winner_idx]
        pid = winner_stat.get('playerId')
        if not pid:
            continue
        checkout_pts = winner_stat.get('checkoutPoints')
        if not checkout_pts:
            continue
        try:
            darts = int(winner_stat.get('dartsThrown') or 0)
        except Exception:
            darts = 0
        if not darts:
            continue
        st = stats.setdefault(pid, {})
        prev = st.get('min_darts_to_checkout')
        if prev is None or darts < prev:
            st['min_darts_to_checkout'] = darts

    # finalize: compute averages and ratios. Prefer an average already supplied directly
    # by the API (set above from match-level stats); only derive it from points_sum/
    # darts_thrown as a fallback, since that recomputation loses precision due to the
    # int(round(...)) applied to points_sum.
    for k, v in stats.items():
        darts = v.get('darts_thrown', 0) or 0
        pts_sum = v.get('points_sum', 0) or 0
        if v.get('average') is None:
            v['average'] = float(pts_sum) / float(darts) * 3.0 if darts else None
        f9_darts = v.get('first9_darts', 0) or 0
        f9_pts = v.get('first9_points_sum', 0) or 0
        if v.get('first9_average') is None:
            v['first9_average'] = float(f9_pts) / float(f9_darts) * 3.0 if f9_darts else None
        f3_darts = v.get('first3_darts', 0) or 0
        f3_pts = v.get('first3_points_sum', 0) or 0
        v['first3_average'] = float(f3_pts) / float(f3_darts) * 3.0 if f3_darts else None
        atts = v.get('checkout_attempts', 0) or 0
        succ = v.get('checkout_success', 0) or 0
        v['checkout_ratio'] = float(succ) / float(atts) if atts else None
        if int(v.get('bestCheckout', 0) or 0) > 0 and not v.get('best_checkout_at'):
            v['best_checkout_at'] = played_at_fallback

    return stats


def import_match_result_to_scores(result, games_len=None, match_id=None, imported_at=None):
    """Wandelt ein einzelnes Autodarts-Match-Ergebnis in Score-Einträge um und
    speichert sie. BOT-Gegner (siehe detect_bot_level) werden dabei nicht als
    eigenständige Spieler angelegt; stattdessen werden die Ergebnisse der
    menschlichen Spieler aus einem Match gegen einen BOT getrennt in
    BOT_SCORES_FILE (pro Bot-Level) abgelegt, statt in die reguläre
    Spieler-Statistik einzufließen.

    Gibt (imported_count, bot_imported_count) zurück."""
    if games_len is None:
        games_len = len(result.get('games', []) or [])

    match_id = match_id or result.get('id') or result.get('matchId') or result.get('_id')
    imported_at = datetime_to_iso(imported_at) or datetime_to_iso(utc_now())
    played_at = match_played_at(result, match_id) or imported_at
    display_date = format_local_datetime(played_at) or datetime.now().strftime('%d.%m.%Y %H:%M')

    stats = extract_stats_from_result(result)
    players = result.get('players', [])

    bot_levels = [
        detect_bot_level(p.get('name') or p.get('username') or '')
        for p in players
    ]
    bot_levels = [lvl for lvl in bot_levels if lvl is not None]
    is_bot_match = len(bot_levels) > 0
    distinct_bot_levels = sorted(set(bot_levels))
    # Normalfall: genau ein BOT-Gegner (bzw. mehrere BOTs desselben Levels) im
    # Match -> eindeutiges Level. Enthält ein Match ausnahmsweise BOTs mit
    # unterschiedlichen Levels, wird das nicht stillschweigend verworfen,
    # sondern als kombinierter Wert (z.B. "3+5") abgelegt.
    if len(distinct_bot_levels) == 1:
        bot_level = distinct_bot_levels[0]
    elif distinct_bot_levels:
        bot_level = '+'.join(str(level) for level in distinct_bot_levels)
    else:
        bot_level = None

    scores = load_json(SCORES_FILE)
    bot_scores = load_json(BOT_SCORES_FILE)
    players_local = load_json(PLAYERS_FILE)
    players_local, players_changed = consolidate_duplicate_players(
        players_local, scores, bot_scores
    )

    imported_count = 0
    bot_imported_count = 0
    regular_updated_count = 0
    bot_updated_count = 0

    for p in players:
        name = p.get('name') or p.get('username') or ''
        if detect_bot_level(name) is not None:
            # BOT-Spieler werden nicht als Spieler angelegt und nicht als
            # eigenständiger Score-Eintrag gespeichert.
            continue

        key = p.get('id') or p.get('userId') or p.get('name')
        st = stats.get(key, {})
        entry = {
            'autodarts_name': p.get('name') or p.get('username') or '',
            'player_name': None,
            'legs': int(st.get('legs', 0) or 0),
            'finish': int(st.get('bestCheckout', 0) or 0),
            'max180': int(st.get('max180', 0) or 0),
            'darts301': int(st.get('min_darts_to_checkout') or 0),
            's60': int(st.get('s60', 0) or 0),
            's100': int(st.get('s100', 0) or 0),
            's140': int(st.get('s140', 0) or 0),
            's170': int(st.get('s170', 0) or 0),
            's180': int(st.get('s180', 0) or 0),
            'min_darts_to_checkout': st.get('min_darts_to_checkout'),
            'checkout_ratio': st.get('checkout_ratio'),
            'checkout_success': int(st.get('checkout_success', 0) or 0),
            'checkout_attempts': int(st.get('checkout_attempts', 0) or 0),
            'segment_hits': st.get('segment_hits', {}),
            'classic_26': int(st.get('classic_26', 0) or 0),
            'checkout_finishes': st.get('checkout_finishes', {}),
            'average': st.get('average'),
            'first9_average': st.get('first9_average'),
            'first9_points_sum': int(st.get('first9_points_sum', 0) or 0),
            'first9_darts': int(st.get('first9_darts', 0) or 0),
            'first3_average': st.get('first3_average'),
            'first3_points_sum': int(st.get('first3_points_sum', 0) or 0),
            'first3_darts': int(st.get('first3_darts', 0) or 0),
            'darts_thrown': st.get('darts_thrown'),
            'points_sum': st.get('points_sum'),
            'last_180_at': st.get('last_180_at') or (
                played_at if int(st.get('s180', 0) or 0) > 0 or int(st.get('max180', 0) or 0) > 0 else None
            ),
            'best_checkout_at': st.get('best_checkout_at') or (
                played_at if int(st.get('bestCheckout', 0) or 0) > 0 else None
            ),
            'bull_finishes': int(st.get('bull_finishes', 0) or 0),
            'last_bull_finish_at': st.get('last_bull_finish_at'),
            # record the number of legs played in this match, so cumulative stats
            # aggregate "legs played" instead of "matches played" (one match can
            # contain several legs).
            'total_games_in_import': games_len or 1,
        }

        player = find_player_by_stat_names(
            players_local, [p.get('name'), p.get('username')]
        )
        pid = player["id"] if player else None
        if not pid:
            pname = entry.get('player_name') or entry.get('autodarts_name')
            pid = get_player_id(pname, players_local)

        new_score = {
            'player_id': pid,
            'legs': entry.get('legs', 0),
            'finish': entry.get('finish', 0),
            'max180': entry.get('max180', 0),
            'darts301': entry.get('darts301', 0),
            's60': entry.get('s60', 0),
            's100': entry.get('s100', 0),
            's140': entry.get('s140', 0),
            's170': entry.get('s170', 0),
            's180': entry.get('s180', 0),
            'games_played': int(entry.get('total_games_in_import') or entry.get('games_played') or 1),
            # `date` bleibt für alte Ansichten kompatibel, enthält bei
            # Autodarts-Daten aber das tatsächliche Spieldatum.
            'date': display_date,
            'played_at': played_at,
            'imported_at': imported_at,
            'autodarts_match_id': match_id,
            'source': 'autodarts',
            # extended fields
            'segment_hits': entry.get('segment_hits', {}),
            'classic_26': entry.get('classic_26', 0),
            'checkout_finishes': entry.get('checkout_finishes', {}),
            'average': entry.get('average'),
            'first9_average': entry.get('first9_average'),
            'first9_points_sum': entry.get('first9_points_sum', 0),
            'first9_darts': entry.get('first9_darts', 0),
            'first3_average': entry.get('first3_average'),
            'first3_points_sum': entry.get('first3_points_sum', 0),
            'first3_darts': entry.get('first3_darts', 0),
            'darts_thrown': entry.get('darts_thrown'),
            'points_sum': entry.get('points_sum'),
            'min_darts_to_checkout': entry.get('min_darts_to_checkout'),
            'checkout_ratio': entry.get('checkout_ratio'),
            'checkout_success': entry.get('checkout_success', 0),
            'checkout_attempts': entry.get('checkout_attempts', 0),
            'last_180_at': entry.get('last_180_at'),
            'best_checkout_at': entry.get('best_checkout_at'),
            'bull_finishes': entry.get('bull_finishes', 0),
            'last_bull_finish_at': entry.get('last_bull_finish_at'),
        }
        if is_bot_match:
            new_score['bot_level'] = bot_level

        try:
            new_hash = compute_score_hash(new_score)
            new_score['score_hash'] = new_hash
            legacy_score = dict(new_score)
            legacy_score.pop('autodarts_match_id', None)
            legacy_hash = compute_score_hash(legacy_score)
        except Exception:
            new_hash = None
            legacy_hash = None

        target_list = bot_scores if is_bot_match else scores
        dup = False
        for ex in target_list:
            # Ein Match besitzt je Teilnehmer einen eigenen Statistikdatensatz.
            # Die Match-ID allein ist deshalb kein eindeutiger Schlüssel: Beim
            # Import eines Mehrspieler-Matches würde sonst jeder weitere Spieler
            # den zuvor verarbeiteten Spieler desselben Matches überschreiben.
            if (
                match_id
                and ex.get('autodarts_match_id') == match_id
                and ex.get('player_id') == pid
            ):
                original_imported_at = ex.get('imported_at')
                ex.update(new_score)
                if original_imported_at:
                    ex['imported_at'] = original_imported_at
                dup = True
                if is_bot_match:
                    bot_updated_count += 1
                else:
                    regular_updated_count += 1
                break
            if ex.get('score_hash') and new_hash and ex.get('score_hash') == new_hash:
                dup = True
                break
            try:
                if compute_score_hash(ex) == new_hash:
                    dup = True
                    break
            except Exception:
                pass
            # Migration für Einträge aus Versionen vor 1.2.0: Diese besaßen
            # bereits einen Statistik-Hash, aber noch keine Match-ID. Der erste
            # passende historische Datensatz wird angereichert; weitere echte
            # Matches mit identischen Werten erhalten dank Match-ID eigene Zeilen.
            try:
                legacy_matches = (
                    not ex.get('autodarts_match_id')
                    and bool(ex.get('score_hash'))
                    and legacy_hash
                    and (
                        ex.get('score_hash') == legacy_hash
                        or compute_score_hash(ex) == legacy_hash
                    )
                )
            except Exception:
                legacy_matches = False
            if legacy_matches:
                ex.update(new_score)
                dup = True
                if is_bot_match:
                    bot_updated_count += 1
                else:
                    regular_updated_count += 1
                break
        if not dup:
            target_list.append(new_score)
            if is_bot_match:
                bot_imported_count += 1
            else:
                imported_count += 1

    if players_changed or imported_count or bot_imported_count:
        save_json(PLAYERS_FILE, players_local)
    if players_changed or imported_count or regular_updated_count:
        save_json(SCORES_FILE, scores)
    if players_changed or bot_imported_count or bot_updated_count:
        save_json(BOT_SCORES_FILE, bot_scores)

    return imported_count, bot_imported_count, regular_updated_count + bot_updated_count


def generate_head_to_head_data(cumulative, players_map, players_list):
    """Generiert frische H2H-Daten mit zufälligen Spielern"""
    if len(players_list) < 2:
        return None
    
    # Nur Spieler mit Daten wählen
    active_players = [p for p in players_list if cumulative.get(p["id"], {}).get("games_played", 0) > 0]
    if len(active_players) < 2:
        active_players = players_list  # Fallback zu allen
    
    # Zwei verschiedene zufällige Spieler wählen
    player_samples = random.sample(active_players, 2)
    
    h2h_data = []
    for p in player_samples:
        pid = p["id"]
        stats = cumulative.get(pid, {})
        
        total_games = stats.get("games_played", 0)
        wins = stats.get("legs", 0)
        win_rate = round((wins / total_games * 100), 1) if total_games > 0 else 0
        # compute averages and checkout ratio
        pts_sum = stats.get('points_sum', 0) or 0
        darts = stats.get('darts_thrown', 0) or 0
        average = round(float(pts_sum) / float(darts) * 3.0, 2) if darts else None
        f3_pts = stats.get('first3_points_sum', 0) or 0
        f3_darts = stats.get('first3_darts', 0) or 0
        first3_average = round(float(f3_pts) / float(f3_darts) * 3.0, 2) if f3_darts else None
        checkout_attempts = stats.get('checkout_attempts', 0) or 0
        checkout_success = stats.get('checkout_success', 0) or 0
        checkout_ratio = round((float(checkout_success) / checkout_attempts * 100), 1) if checkout_attempts else None
        # T20 hits
        t20_hits = stats.get('segment_hits', {}).get('T20', 0)

        h2h_data.append({
            "id": pid,
            "name": p["name"],
            "image": p.get("image", "dummy.png"),
            "wins": wins,
            "total_games": total_games,
            "win_rate": win_rate,
            "finish": stats.get("best_finish", 0),
            "max180": stats.get("max180", 0) + stats.get("s180", 0),
            "s100_plus": stats.get("s100", 0) + stats.get("s140", 0) + stats.get("s170", 0) + stats.get("s180", 0),
            "checkout_ratio": checkout_ratio,
            "t20_hits": t20_hits,
            "average": average,
            "first3_average": first3_average,
        })
    
    # Vergleiche markieren
    comparison = {
        "players": h2h_data,
        "wins_leader": h2h_data[0]["id"] if h2h_data[0]["wins"] > h2h_data[1]["wins"] else h2h_data[1]["id"] if h2h_data[1]["wins"] > h2h_data[0]["wins"] else None,
        "winrate_leader": h2h_data[0]["id"] if h2h_data[0]["win_rate"] > h2h_data[1]["win_rate"] else h2h_data[1]["id"] if h2h_data[1]["win_rate"] > h2h_data[0]["win_rate"] else None,
        "finish_leader": h2h_data[0]["id"] if h2h_data[0]["finish"] > h2h_data[1]["finish"] else h2h_data[1]["id"] if h2h_data[1]["finish"] > h2h_data[0]["finish"] else None,
        "t180_leader": h2h_data[0]["id"] if h2h_data[0]["max180"] > h2h_data[1]["max180"] else h2h_data[1]["id"] if h2h_data[1]["max180"] > h2h_data[0]["max180"] else None,
    }
    
    return comparison


# --- Routen ---

@app.route("/")
def index():
    scores = load_json(SCORES_FILE)
    config = load_json(CONFIG_FILE)
    local_ip = get_local_ip()
    qr_url = f"http://{local_ip}:5000"
    qr_code = generate_qr_code(qr_url)

    cumulative, players_map, players_list, player_ids = get_cumulative_stats()

    def player_name(pid):
        return players_map.get(pid, {}).get("name", "Unbekannt")

    def player_image(pid):
        return players_map.get(pid, {}).get("image", "dummy.png")

    # Statistiken aufbereiten
    most_legs = []
    most_180s = []
    most_s60 = []
    most_s100 = []
    most_s140 = []
    most_s170 = []
    win_rate_stats = []
    
    for pid, vals in cumulative.items():
        base = {"name": player_name(pid), "image": player_image(pid)}
        
        if vals["legs"] > 0:
            most_legs.append({**base, "legs": vals["legs"]})
        
        total_180 = vals["max180"] + vals["s180"]
        if total_180 > 0:
            most_180s.append({**base, "max180": total_180, "last180_date": vals["last180_date"]})
        
        if vals["s60"] > 0:
            most_s60.append({**base, "s60": vals["s60"]})
        if vals["s100"] > 0:
            most_s100.append({**base, "s100": vals["s100"]})
        if vals["s140"] > 0:
            most_s140.append({**base, "s140": vals["s140"]})
        if vals["s170"] > 0:
            most_s170.append({**base, "s170": vals["s170"]})
        
        if vals["games_played"] > 0:
            wins = vals["legs"]
            total = vals["games_played"]
            win_rate = (wins / total * 100) if total > 0 else 0
            weighted_score = calculate_win_rate_score(wins, total)
            
            win_rate_stats.append({
                **base,
                "wins": wins,
                "total_games": total,
                "win_rate": round(win_rate, 1),
                "weighted_score": weighted_score
            })

    most_legs = add_podium_rank(sorted(most_legs, key=lambda x: x["legs"], reverse=True), "legs")
    most_180s = add_podium_rank(sorted(most_180s, key=lambda x: x["max180"], reverse=True), "max180")
    most_s60 = add_podium_rank(sorted(most_s60, key=lambda x: x["s60"], reverse=True), "s60")
    most_s100 = add_podium_rank(sorted(most_s100, key=lambda x: x["s100"], reverse=True), "s100")
    most_s140 = add_podium_rank(sorted(most_s140, key=lambda x: x["s140"], reverse=True), "s140")
    most_s170 = add_podium_rank(sorted(most_s170, key=lambda x: x["s170"], reverse=True), "s170")
    win_rate_stats = add_podium_rank(sorted(win_rate_stats, key=lambda x: x["weighted_score"], reverse=True), "weighted_score")

    # Neue Metriken: T20 Treffer und Checkout-Quote
    most_t20 = []
    most_checkout = []
    for pid, vals in cumulative.items():
        base = {"name": player_name(pid), "image": player_image(pid)}
        t20 = vals.get('segment_hits', {}).get('T20', 0)
        if t20 > 0:
            most_t20.append({**base, "t20": t20})
        # checkout ratio (percent)
        atts = vals.get('checkout_attempts', 0)
        succ = vals.get('checkout_success', 0)
        if atts:
            ratio = round((succ / atts) * 100, 1)
            most_checkout.append({**base, "checkout_ratio": ratio})

    most_t20 = add_podium_rank(sorted(most_t20, key=lambda x: x["t20"], reverse=True), "t20")
    most_checkout = add_podium_rank(sorted(most_checkout, key=lambda x: x["checkout_ratio"], reverse=True), "checkout_ratio")

    # Most First3 average and overall average
    most_first3 = []
    most_average = []
    for pid, vals in cumulative.items():
        base = {"name": player_name(pid), "image": player_image(pid)}
        f3_darts = vals.get('first3_darts', 0) or 0
        f3_pts = vals.get('first3_points_sum', 0) or 0
        if f3_darts:
            first3_avg = round(float(f3_pts) / float(f3_darts) * 3.0, 2)
            most_first3.append({**base, 'first3_average': first3_avg})
        pts = vals.get('points_sum', 0) or 0
        darts = vals.get('darts_thrown', 0) or 0
        if darts:
            avg = round(float(pts) / float(darts) * 3.0, 2)
            most_average.append({**base, 'average': avg})

    most_first3 = add_podium_rank(sorted(most_first3, key=lambda x: x['first3_average'], reverse=True), 'first3_average')
    most_average = add_podium_rank(sorted(most_average, key=lambda x: x['average'], reverse=True), 'average')

    # Spaß- und Mengenstatistiken aus den detaillierten Autodarts-Aufnahmen.
    most_classic_26 = []
    favorite_checkouts = []
    most_hit_segments = []
    most_darts_thrown = []
    valid_segment = re.compile(r'^[SDT](?:[1-9]|1[0-9]|20|25)$')
    for pid, vals in cumulative.items():
        base = {"name": player_name(pid), "image": player_image(pid)}

        classic_26 = int(vals.get('classic_26', 0) or 0)
        if classic_26 > 0:
            most_classic_26.append({**base, 'classic_26': classic_26})

        checkout_counts = vals.get('checkout_finishes') or {}
        usable_checkouts = []
        for checkout, count in checkout_counts.items():
            try:
                checkout_value = int(checkout)
                checkout_count = int(count or 0)
            except (TypeError, ValueError):
                continue
            if checkout_value > 0 and checkout_count > 0:
                usable_checkouts.append((checkout_value, checkout_count))
        if usable_checkouts:
            favorite_checkout, favorite_count = max(
                usable_checkouts, key=lambda item: (item[1], item[0])
            )
            favorite_checkouts.append({
                **base,
                'favorite_checkout': favorite_checkout,
                'favorite_checkout_count': favorite_count,
            })

        segment_counts = vals.get('segment_hits') or {}
        usable_segments = [
            (str(segment).upper(), int(count or 0))
            for segment, count in segment_counts.items()
            if valid_segment.fullmatch(str(segment).upper()) and int(count or 0) > 0
        ]
        if usable_segments:
            most_hit_segment, most_hit_count = max(
                usable_segments, key=lambda item: (item[1], item[0])
            )
            most_hit_segments.append({
                **base,
                'most_hit_segment': most_hit_segment,
                'most_hit_count': most_hit_count,
            })

        darts_thrown = int(vals.get('darts_thrown', 0) or 0)
        if darts_thrown > 0:
            most_darts_thrown.append({**base, 'darts_thrown': darts_thrown})

    most_classic_26 = add_podium_rank(
        sorted(most_classic_26, key=lambda x: x['classic_26'], reverse=True),
        'classic_26',
    )
    favorite_checkouts = add_podium_rank(
        sorted(favorite_checkouts, key=lambda x: x['favorite_checkout_count'], reverse=True),
        'favorite_checkout_count',
    )
    most_hit_segments = add_podium_rank(
        sorted(most_hit_segments, key=lambda x: x['most_hit_count'], reverse=True),
        'most_hit_count',
    )
    most_darts_thrown = add_podium_rank(
        sorted(most_darts_thrown, key=lambda x: x['darts_thrown'], reverse=True),
        'darts_thrown',
    )

    # Höchstes Finish
    finish_best = {}
    for s in scores:
        pid = player_ids.get(s.get("player_id"))
        if pid is None:
            continue
        val = s.get("finish", 0)
        checkout_at = s.get('best_checkout_at') or s.get('played_at') or s.get('date')
        previous = finish_best.get(pid, {})
        previous_at = parse_datetime(previous.get('finish_at'))
        checkout_time = parse_datetime(checkout_at)
        is_better = val > previous.get('finish', 0)
        is_newer_tie = (
            val > 0 and val == previous.get('finish', 0)
            and checkout_time is not None
            and (previous_at is None or checkout_time > previous_at)
        )
        if val > 0 and (is_better or is_newer_tie):
            finish_best[pid] = {
                "name": player_name(pid),
                "image": player_image(pid),
                "finish": val,
                "finish_at": datetime_to_iso(checkout_time) if checkout_time else None,
                "finish_date": format_local_datetime(checkout_time) or s.get("date", ""),
            }
    highest_finish = add_podium_rank(sorted(finish_best.values(), key=lambda x: x["finish"], reverse=True), "finish")

    # Wenigste Darts 301
    darts301_best = {}
    for s in scores:
        pid = player_ids.get(s.get("player_id"))
        if pid is None:
            continue
        val = s.get("darts301", 0)
        if val > 0 and val < darts301_best.get(pid, {}).get("darts301", 9999):
            darts301_best[pid] = {
                "name": player_name(pid),
                "image": player_image(pid),
                "darts301": val,
            }
    lowest_darts301 = add_podium_rank(sorted(darts301_best.values(), key=lambda x: x["darts301"], reverse=False), "darts301")

    # Vollbild-Ranglisten verwenden ein einheitliches, kompaktes Datenformat.
    # Sie werden clientseitig als einzelne Karten in die Rotation eingereiht.
    leaderboard_limit = max(1, int(config.get('leaderboard_limit', 10) or 10))

    def leaderboard(title, value_label, entries, value_key, value_formatter=None):
        rows = []
        for entry in entries[:leaderboard_limit]:
            value = entry.get(value_key)
            if value_formatter:
                value = value_formatter(entry)
            rows.append({
                'rank': entry.get('rank'),
                'podium_class': entry.get('podium_class', ''),
                'name': entry.get('name', 'Unbekannt'),
                'image_url': url_for(
                    'static', filename='uploads/' + (entry.get('image') or 'dummy.png')
                ),
                'value': value,
            })
        return {'title': title, 'value_label': value_label, 'rows': rows}

    additional_rankings = [
        leaderboard('🏆 Meiste Legs gewonnen', 'Legs', most_legs, 'legs'),
        leaderboard('💥 Meiste 180er', '180er', most_180s, 'max180'),
        leaderboard('🎯 Höchstes Finish', 'Finish', highest_finish, 'finish'),
        leaderboard('💨 Wenigste Würfe – 301', 'Darts', lowest_darts301, 'darts301'),
        leaderboard('🎯 Meiste T20 Treffer', 'T20', most_t20, 't20'),
        leaderboard(
            '✅ Beste Checkout-Quote', 'Quote', most_checkout, 'checkout_ratio',
            lambda entry: f"{entry.get('checkout_ratio', 0)}%",
        ),
        leaderboard('🎯 First 3 Darts (Avg)', 'First3', most_first3, 'first3_average'),
        leaderboard('📈 Durchschnitt (Avg)', 'Avg', most_average, 'average'),
        leaderboard('🟡 Meiste 60+ Aufnahmen', '60+', most_s60, 's60'),
        leaderboard('🟠 Meiste 100+ Aufnahmen', '100+', most_s100, 's100'),
        leaderboard('🔴 Meiste 140+ Aufnahmen', '140+', most_s140, 's140'),
        leaderboard('💜 Meiste 170+ Aufnahmen', '170+', most_s170, 's170'),
        leaderboard('⚡ Zack... 26', '26er', most_classic_26, 'classic_26'),
        leaderboard(
            '❤️ Lieblings Checkout', 'Finish', favorite_checkouts, 'favorite_checkout',
            lambda entry: (
                f"{entry.get('favorite_checkout')} ({entry.get('favorite_checkout_count')}×)"
            ),
        ),
        leaderboard(
            '📍 Most Hit', 'Feld', most_hit_segments, 'most_hit_segment',
            lambda entry: f"{entry.get('most_hit_segment')} ({entry.get('most_hit_count')}×)",
        ),
        leaderboard(
            '🎯 Darts thrown', 'Darts', most_darts_thrown, 'darts_thrown',
            lambda entry: f"{int(entry.get('darts_thrown', 0)):,}".replace(',', '.'),
        ),
    ]
    additional_rankings = [ranking for ranking in additional_rankings if ranking['rows']]

    # KEIN H2H hier mehr - wird per AJAX geladen!
    bg_image = f"uploads/{BACKGROUND_FILENAME}" if background_exists() else None

    return render_template(
        "index.html",
        most_legs=most_legs,
        highest_finish=highest_finish,
        most_180s=most_180s,
        lowest_darts301=lowest_darts301,
        most_s60=most_s60,
        most_s100=most_s100,
        most_s140=most_s140,
        most_s170=most_s170,
        win_rate_stats=win_rate_stats,
        most_t20=most_t20,
        most_checkout=most_checkout,
        most_first3=most_first3,
        most_average=most_average,
        most_classic_26=most_classic_26,
        favorite_checkouts=favorite_checkouts,
        most_hit_segments=most_hit_segments,
        most_darts_thrown=most_darts_thrown,
        additional_rankings=additional_rankings,
        bg_image=bg_image,
        config=config,
        local_ip=local_ip,
        qr_code=qr_code,
        qr_url=qr_url,
    )


# NEU: AJAX Endpoint für frische H2H-Daten
@app.route("/api/h2h")
def api_h2h():
    """Liefert frische zufällige H2H-Daten für die Rotation"""
    cumulative, players_map, players_list, _ = get_cumulative_stats()
    h2h_data = generate_head_to_head_data(cumulative, players_map, players_list)
    return jsonify(h2h_data)


@app.route("/api/player_card")
def api_player_card():
    """Return a random player's detailed stats for rotation player card."""
    cumulative, players_map, players_list, _ = get_cumulative_stats()
    import random
    # choose only players with some games
    candidates = [p for p in players_list if cumulative.get(p['id'], {}).get('games_played', 0) > 0]
    if not candidates:
        candidates = players_list
    if not candidates:
        return jsonify({"ok": False, "error": "no players"}), 404
    p = random.choice(candidates)
    stats = cumulative.get(p['id'], {})
    # build card
    card = {
        'id': p['id'],
        'name': p['name'],
        'image': p.get('image','dummy.png'),
        'games_played': stats.get('games_played',0),
        'legs': stats.get('legs',0),
        'average': round(float(stats.get('points_sum',0))/float(stats.get('darts_thrown',1))*3.0,2) if stats.get('darts_thrown') else None,
        'first3_average': None,
        'checkout_ratio': None,
        't20_hits': stats.get('segment_hits',{}).get('T20',0),
        'darts_thrown': stats.get('darts_thrown', 0),
        'classic_26': int(stats.get('classic_26', 0) or 0),
        'favorite_checkout': None,
        'favorite_checkout_count': 0,
        'most_hit_segment': None,
        'most_hit_count': 0,
        'bull_finishes': stats.get('bull_finishes', 0),
        'last_bull_finish_date': stats.get('last_bull_finish_date', ''),
    }
    # first3
    f3_darts = stats.get('first3_darts',0)
    f3_pts = stats.get('first3_points_sum',0)
    if f3_darts:
        card['first3_average'] = round(float(f3_pts)/float(f3_darts)*3.0,2)
    # checkout ratio
    atts = stats.get('checkout_attempts',0)
    succ = stats.get('checkout_success',0)
    if atts:
        card['checkout_ratio'] = round(float(succ)/atts*100,1)

    checkout_candidates = []
    for checkout, count in (stats.get('checkout_finishes') or {}).items():
        try:
            checkout_value = int(checkout)
            checkout_count = int(count or 0)
        except (TypeError, ValueError):
            continue
        if checkout_value > 0 and checkout_count > 0:
            checkout_candidates.append((checkout_value, checkout_count))
    if checkout_candidates:
        favorite_checkout, favorite_count = max(
            checkout_candidates, key=lambda item: (item[1], item[0])
        )
        card['favorite_checkout'] = favorite_checkout
        card['favorite_checkout_count'] = favorite_count

    valid_segment = re.compile(r'^[SDT](?:[1-9]|1[0-9]|20|25)$')
    segment_candidates = []
    for segment, count in (stats.get('segment_hits') or {}).items():
        segment_name = str(segment).upper()
        try:
            segment_count = int(count or 0)
        except (TypeError, ValueError):
            continue
        if valid_segment.fullmatch(segment_name) and segment_count > 0:
            segment_candidates.append((segment_name, segment_count))
    if segment_candidates:
        most_hit_segment, most_hit_count = max(
            segment_candidates, key=lambda item: (item[1], item[0])
        )
        card['most_hit_segment'] = most_hit_segment
        card['most_hit_count'] = most_hit_count

    return jsonify({'ok': True, 'player': card})


@app.route("/admin", methods=["GET", "POST"])
def admin():
    players = load_json(PLAYERS_FILE)
    scores = load_json(SCORES_FILE)
    config = load_json(CONFIG_FILE)
    bot_scores = load_json(BOT_SCORES_FILE)
    players, players_consolidated = consolidate_duplicate_players(players, scores, bot_scores)
    if players_consolidated:
        save_json(PLAYERS_FILE, players)
        save_json(SCORES_FILE, scores)
        save_json(BOT_SCORES_FILE, bot_scores)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_score":
            player_id = request.form.get("player_id")
            new_player_name = request.form.get("new_player_name")

            if new_player_name:
                player_id = get_player_id(new_player_name, players)
            elif player_id:
                try:
                    player_id = int(player_id)
                except ValueError:
                    player_id = None

            if player_id:
                try:
                    legs_add = int(request.form.get("legs") or 0)
                    finish = int(request.form.get("finish") or 0)
                    max180_add = int(request.form.get("max180") or 0)
                    darts301_add = int(request.form.get("darts301") or 0)
                    s60_add = int(request.form.get("s60") or 0)
                    s100_add = int(request.form.get("s100") or 0)
                    s140_add = int(request.form.get("s140") or 0)
                    s170_add = int(request.form.get("s170") or 0)
                    s180_add = int(request.form.get("s180") or 0)
                    games_played_add = int(request.form.get("games_played") or 0)
                except ValueError:
                    legs_add = finish = max180_add = darts301_add = 0
                    s60_add = s100_add = s140_add = s170_add = s180_add = 0
                    games_played_add = 0

                any_value = any([
                    legs_add, finish, max180_add, darts301_add,
                    s60_add, s100_add, s140_add, s170_add, s180_add,
                    games_played_add,
                ])
                if any_value:
                    new_score = {
                        "player_id": player_id,
                        "legs": legs_add,
                        "finish": finish,
                        "max180": max180_add,
                        "darts301": darts301_add,
                        "s60": s60_add,
                        "s100": s100_add,
                        "s140": s140_add,
                        "s170": s170_add,
                        "s180": s180_add,
                        "games_played": games_played_add,
                        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                    }
                    scores.append(new_score)
                    save_json(SCORES_FILE, scores)

        elif action == "upload_image":
            player_id = request.form.get("player_id")
            if player_id:
                try:
                    player_id = int(player_id)
                    file = request.files.get("player_image")
                    if file and file.filename != '':
                        filename = f"player_{player_id}.png"
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                        for p in players:
                            if p["id"] == player_id:
                                p["image"] = filename
                                break
                        save_json(PLAYERS_FILE, players)
                except ValueError:
                    pass

        elif action == "upload_background":
            file = request.files.get("background_image")
            if file and file.filename != '':
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME)
                if os.path.exists(old_path):
                    os.remove(old_path)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME))

        elif action == "delete_player":
            try:
                player_id_to_delete = int(request.form.get("player_id_to_delete"))
                players = [p for p in players if p["id"] != player_id_to_delete]
                save_json(PLAYERS_FILE, players)
                scores = [s for s in scores if s["player_id"] != player_id_to_delete]
                save_json(SCORES_FILE, scores)
                bot_scores = load_json(BOT_SCORES_FILE)
                bot_scores = [s for s in bot_scores if s.get("player_id") != player_id_to_delete]
                save_json(BOT_SCORES_FILE, bot_scores)
            except ValueError:
                pass

        elif action == "delete_score":
            try:
                score_index = int(request.form.get("score_index_to_delete"))
                if 0 <= score_index < len(scores):
                    scores.pop(score_index)
                    save_json(SCORES_FILE, scores)
            except ValueError:
                pass

        elif action == "undo_last_score":
            if scores:
                scores.pop()
                save_json(SCORES_FILE, scores)

        elif action == "rename_player":
            try:
                rename_id = int(request.form.get("rename_player_id"))
                rename_name = request.form.get("rename_player_name", "").strip()
                if rename_name:
                    for p in players:
                        if p["id"] == rename_id:
                            p["name"] = rename_name
                            break
                    save_json(PLAYERS_FILE, players)
            except ValueError:
                pass

        elif action == "save_player_identity":
            try:
                player_id = int(request.form.get("player_id"))
                ad_name = request.form.get("autodarts_name", "").strip()
                display_name = request.form.get("display_name", "").strip()
                for p in players:
                    if p["id"] == player_id:
                        aliases = request.form.get("stat_names", "").splitlines()
                        aliases.append(ad_name)
                        p["autodarts_name"] = ad_name
                        if display_name:
                            p["name"] = display_name
                        p["stat_names"] = aliases
                        break
                players, changed = consolidate_duplicate_players(players, scores, bot_scores)
                if changed:
                    save_json(SCORES_FILE, scores)
                    save_json(BOT_SCORES_FILE, bot_scores)
                save_json(PLAYERS_FILE, players)
            except ValueError:
                pass

        elif action == "save_config":
            try:
                # Mehrere getrennte Formulare im Admin-Bereich senden alle "save_config",
                # aber jeweils nur ihre eigenen Felder. Fehlende Felder dürfen daher nicht
                # auf einen Festwert zurückfallen, sondern müssen den zuvor gespeicherten
                # Wert behalten - sonst überschreibt z.B. das Speichern der Wartezeiten
                # die Autodarts-Zugangsdaten (oder umgekehrt).
                def _form_int(name, fallback):
                    raw = request.form.get(name)
                    if raw is None or raw == "":
                        return fallback
                    return int(raw)

                def _form_str(name, fallback):
                    raw = request.form.get(name)
                    return raw if raw not in (None, "") else fallback

                # Der Autodarts-Zeitplan hat eine eigene Checkbox; nur übernehmen, wenn
                # das Autodarts-Formular tatsächlich abgeschickt wurde (erkennbar am
                # Intervall-Feld, das nur dort vorkommt), sonst bisherigen Wert behalten.
                if "autodarts_interval_minutes" in request.form:
                    autodarts_enabled = request.form.get("autodarts_enabled") == 'on'
                else:
                    autodarts_enabled = config.get("autodarts_enabled", False)

                if "cec_device_name" in request.form:
                    cec_enabled = request.form.get("cec_enabled") == 'on'
                else:
                    cec_enabled = config.get("cec_enabled", False)

                if "kiosk_url" in request.form:
                    kiosk_hide_cursor = request.form.get("kiosk_hide_cursor") == 'on'
                else:
                    kiosk_hide_cursor = config.get("kiosk_hide_cursor", True)

                def _form_time(name, fallback):
                    value = _form_str(name, fallback)
                    return value if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) else fallback

                def _form_cec_adapter():
                    value = _form_str("cec_adapter", config.get("cec_adapter", "")).strip()
                    return value if not value or re.fullmatch(r'/dev/cec[0-9]+', value) else ""

                def _form_kiosk_url():
                    fallback = config.get("kiosk_url", DEFAULT_CONFIG["kiosk_url"])
                    value = _form_str("kiosk_url", fallback).strip()
                    parsed = urlparse(value)
                    return value if parsed.scheme in {"http", "https"} and parsed.netloc else fallback

                new_config = {
                    "static_limit": _form_int("static_limit", config.get("static_limit", 5)),
                    "rotation_limit": _form_int("rotation_limit", config.get("rotation_limit", 10)),
                    "leaderboard_limit": min(
                        50, max(1, _form_int(
                            "leaderboard_limit", config.get("leaderboard_limit", 10)
                        ))
                    ),
                    "static_h2_size": _form_str("static_h2_size", config.get("static_h2_size", "2.5em")),
                    "rotation_h2_size": _form_str("rotation_h2_size", config.get("rotation_h2_size", "3.5em")),
                    "static_td_size": _form_str("static_td_size", config.get("static_td_size", "2.0em")),
                    "rotation_td_size": _form_str("rotation_td_size", config.get("rotation_td_size", "1.5em")),
                    "font_family": _form_str("font_family", config.get("font_family", "'Segoe UI', Roboto, sans-serif")),
                    # Wartezeiten (Sekunden) der einzelnen Rotations-Ansichten
                    "rotation_duration_grid1": _form_int("rotation_duration_grid1", config.get("rotation_duration_grid1", 300)),
                    "rotation_duration_grid2": _form_int("rotation_duration_grid2", config.get("rotation_duration_grid2", 60)),
                    "rotation_duration_grid3": _form_int("rotation_duration_grid3", config.get("rotation_duration_grid3", 60)),
                    "rotation_duration_rankings": _form_int("rotation_duration_rankings", config.get("rotation_duration_rankings", 30)),
                    "rotation_duration_winrate": _form_int("rotation_duration_winrate", config.get("rotation_duration_winrate", 60)),
                    "rotation_duration_player": _form_int("rotation_duration_player", config.get("rotation_duration_player", 60)),
                    "rotation_duration_h2h": _form_int("rotation_duration_h2h", config.get("rotation_duration_h2h", 60)),
                    "rotation_refresh_minutes": _form_int("rotation_refresh_minutes", config.get("rotation_refresh_minutes", 10)),
                    # Autodarts credentials & scheduling
                    "autodarts_email": _form_str("autodarts_email", config.get("autodarts_email", "")),
                    "autodarts_password": _form_str("autodarts_password", config.get("autodarts_password", "")),
                    "autodarts_enabled": autodarts_enabled,
                    "autodarts_interval_minutes": _form_int("autodarts_interval_minutes", config.get("autodarts_interval_minutes", 60)),
                    "autodarts_user_data_dir": _form_str("autodarts_user_data_dir", config.get("autodarts_user_data_dir", "")),
                    "cec_enabled": cec_enabled,
                    "cec_device_name": (_form_str(
                        "cec_device_name", config.get("cec_device_name", "Dart Scoreboard")
                    ).strip()[:14] or config.get("cec_device_name", "Dart Scoreboard")),
                    "cec_standby_time": _form_time("cec_standby_time", config.get("cec_standby_time", "22:00")),
                    "cec_wake_time": _form_time("cec_wake_time", config.get("cec_wake_time", "08:00")),
                    "cec_adapter": _form_cec_adapter(),
                    "cec_check_interval": min(
                        3600, max(10, _form_int("cec_check_interval", config.get("cec_check_interval", 50)))
                    ),
                    "screensaver_idle_time": min(
                        86400,
                        max(1, _form_int(
                            "screensaver_idle_time",
                            config.get("screensaver_idle_time", 300),
                        )),
                    ),
                    "kiosk_url": _form_kiosk_url(),
                    "kiosk_display_mode": (
                        request.form.get("kiosk_display_mode")
                        if request.form.get("kiosk_display_mode") in {"auto", "wayland", "x11"}
                        else config.get("kiosk_display_mode", "auto")
                    ),
                    "kiosk_hide_cursor": kiosk_hide_cursor,
                }
                current_config = load_json(CONFIG_FILE)
                current_config.update(new_config)
                save_json(CONFIG_FILE, current_config)
                write_cec_config(current_config)
                write_screensaver_config(current_config)
                write_kiosk_config(current_config)
                if "cec_device_name" in request.form:
                    try:
                        subprocess.run(
                            ['systemctl', '--user', 'try-restart', 'hdmi-audio-cec.service'],
                            capture_output=True, text=True, timeout=15, check=False,
                        )
                    except (OSError, subprocess.SubprocessError):
                        pass
                if "screensaver_idle_time" in request.form:
                    try:
                        restart_screensaver()
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        app.logger.exception('Screensaver-Neustart fehlgeschlagen')
                if "kiosk_url" in request.form:
                    try:
                        subprocess.run(
                            ['systemctl', '--user', 'try-restart', 'dart-kiosk.service'],
                            capture_output=True, text=True, timeout=15, check=False,
                        )
                    except (OSError, subprocess.SubprocessError):
                        app.logger.exception('Kiosk-Neustart fehlgeschlagen')
            except ValueError:
                pass

        return redirect(url_for("admin"))

    admin_scores = []
    for idx, s in enumerate(scores):
        p = get_player_by_id(s.get("player_id"))
        admin_scores.append({
            "index": idx,
            "name": p["name"] if p else "Unbekannt",
            "date": s.get("date", "N/A"),
            "legs": s.get("legs", 0),
            "finish": s.get("finish", 0),
            "max180": s.get("max180", 0),
            "darts301": s.get("darts301", 0),
            "s60": s.get("s60", 0),
            "s100": s.get("s100", 0),
            "s140": s.get("s140", 0),
            "s170": s.get("s170", 0),
            "s180": s.get("s180", 0),
            "score_hash": s.get("score_hash", ""),
            "games_played": s.get("games_played", 0),
        })

    # Presentation-only grouping for the collapsible admin overview.  For
    # Autodarts entries, `date` is the actual match date; manual entries retain
    # their entry date. The stored score order and indices remain unchanged.
    grouped_scores = {}
    for score in reversed(admin_scores):
        raw_date = score.get("date") or "N/A"
        import_date = raw_date.split(" ", 1)[0] if raw_date != "N/A" else "Ohne Datum"
        grouped_scores.setdefault(import_date, []).append(score)
    score_groups = [
        {"date": import_date, "scores": grouped, "count": len(grouped)}
        for import_date, grouped in grouped_scores.items()
    ]

    bg_image = f"uploads/{BACKGROUND_FILENAME}" if background_exists() else None

    # load last autodarts run result if available
    last_result = {}
    try:
        lr_path = AUTODARTS_LAST_RESULT_FILE
        if os.path.exists(lr_path):
            with open(lr_path, 'r', encoding='utf-8') as f:
                last_result = json.load(f)
    except Exception:
        last_result = {}

    autodarts_status = load_autodarts_status()
    autodarts_sync_state = load_autodarts_sync_state()
    autodarts_sync_state['last_check_display'] = (
        format_local_datetime(autodarts_sync_state.get('last_check_at')) or 'nie'
    )
    autodarts_sync_state['last_success_display'] = (
        format_local_datetime(autodarts_sync_state.get('last_success_at')) or 'nie'
    )
    autodarts_sync_state['next_check_display'] = (
        format_local_datetime(autodarts_sync_state.get('next_check_at')) or 'nicht geplant'
    )
    pending_values = list(autodarts_sync_state.get('pending_matches', {}).values())
    autodarts_sync_state['pending_count'] = sum(
        1 for item in pending_values if item.get('status') == 'pending'
    )
    autodarts_sync_state['failed_count'] = sum(
        1 for item in pending_values if item.get('status') == 'failed'
    )
    bot_stats = get_bot_cumulative_stats()
    try:
        addon_statuses = all_addon_statuses(ADDONS_DIR, os.path.expanduser('~'))
    except AddonError as exc:
        app.logger.error('Add-on-Erkennung fehlgeschlagen: %s', exc)
        addon_statuses = {}

    return render_template(
        "admin.html",
        players=players,
        scores=admin_scores,
        score_groups=score_groups,
        background_exists=background_exists(),
        bg_image=bg_image,
        config=config,
        autodarts_last_result=last_result,
        autodarts_status=autodarts_status,
        autodarts_sync_state=autodarts_sync_state,
        bot_stats=bot_stats,
        addon_statuses=addon_statuses,
        app_version=get_app_version(),
    )


@app.route("/admin/import", methods=["POST"])
def admin_import():
    try:
        payload = request.get_json(force=True)
        if not payload or not isinstance(payload, list):
            return {"ok": False, "error": "Ungültiges Format"}, 400

        players = load_json(PLAYERS_FILE)
        scores = load_json(SCORES_FILE)
        imported = 0
        duplicates = 0
        now = datetime.now().strftime("%d.%m.%Y %H:%M")

        players, players_consolidated = consolidate_duplicate_players(players, scores)

        for entry in payload:
            pid = entry.get("player_id")
            if pid:
                try:
                    pid = int(pid)
                except Exception:
                    pid = None
            if not pid:
                ad_name = (entry.get("autodarts_name") or entry.get("player_name") or "").strip()
                player = find_player_by_stat_names(players, [ad_name])
                pid = player["id"] if player else None
                if not pid:
                    name = (entry.get("player_name") or ad_name or "").strip()
                    if not name:
                        continue
                    pid = get_player_id(name, players)

            # Werte parsen (sicher)
            try:
                legs = int(entry.get("legs", 0) or 0)
                finish = int(entry.get("finish", 0) or 0)
                m180 = int(entry.get("max180", 0) or 0)
                d301 = int(entry.get("darts301", 0) or 0)
                s60 = int(entry.get("s60", 0) or 0)
                s100 = int(entry.get("s100", 0) or 0)
                s140 = int(entry.get("s140", 0) or 0)
                s170 = int(entry.get("s170", 0) or 0)
                s180 = int(entry.get("s180", 0) or 0)
            except Exception:
                continue

            if not any([legs, finish, m180, d301, s60, s100, s140, s170, s180]):
                continue

            # games_played: benutze vorrangig vom Client übergebenen Gesamtwert (total_games_in_import),
            # sonst fallback auf ggf. übergebenes games_played oder 1 (ein importierter Match zählt als ein Spiel)
            try:
                    games_played = int(entry.get("total_games_in_import") or entry.get("games_played") or 1)
            except Exception:
                games_played = 1

            new_score = {
                "player_id": pid,
                "legs": legs,
                "finish": finish,
                "max180": m180,
                "darts301": d301,
                "s60": s60,
                "s100": s100,
                "s140": s140,
                "s170": s170,
                "s180": s180,
                "games_played": games_played,
                "date": now,
            }

            # Duplikat-Prüfung via Hash
            try:
                new_hash = compute_score_hash(new_score)
            except Exception:
                new_hash = None

            is_dup = False
            if new_hash:
                for existing in scores:
                    # Falls vorhandener Eintrag schon einen Hash hat, schnell prüfen
                    if existing.get("score_hash") and existing.get("score_hash") == new_hash:
                        is_dup = True
                        break
                    # sonst Hash dynamisch berechnen und vergleichen
                    try:
                        if compute_score_hash(existing) == new_hash:
                            is_dup = True
                            break
                    except Exception:
                        continue

            if is_dup:
                duplicates += 1
                continue

            if new_hash:
                new_score["score_hash"] = new_hash

            scores.append(new_score)
            imported += 1

        if players_consolidated or imported:
            save_json(SCORES_FILE, scores)
        save_json(PLAYERS_FILE, players)
        return {"ok": True, "imported": imported, "duplicates": duplicates}

    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


def _autodarts_form_login(page, email, password):
    """Füllt das Login-Formular auf play.autodarts.com aus (analog zu scripts/autodarts_fetch.py).
    Gibt (erfolgreich: bool, fehlermeldung: str|None) zurück."""
    try:
        page.goto('https://play.autodarts.com/login', timeout=30000)
        page.wait_for_load_state('networkidle')
    except Exception as e:
        return False, f'Login-Seite konnte nicht geladen werden: {e}'

    filled = False
    for email_sel, pass_sel in (
        ('input[name="email"]', 'input[name="password"]'),
        ('input[type="email"]', 'input[type="password"]'),
        ('#username', '#password'),
    ):
        try:
            if page.query_selector(email_sel) and page.query_selector(pass_sel):
                page.fill(email_sel, email)
                page.fill(pass_sel, password)
                filled = True
                break
        except Exception:
            continue

    if not filled:
        return False, 'Login-Formular nicht gefunden (Seite nutzt evtl. nur OAuth-Anbieter).'

    try:
        page.click('button[type="submit"]')
    except Exception as e:
        return False, f'Login-Button konnte nicht geklickt werden: {e}'

    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass

    # Erfolg prüfen: nach Login sollte die URL nicht mehr auf die Login-Seite zeigen
    try:
        if '/login' in page.url or '/auth' in page.url:
            return False, 'Falsche Zugangsdaten oder Login-Formular hat sich nicht wie erwartet verhalten.'
    except Exception:
        pass

    return True, None


def _save_autodarts_debug_screenshot(page):
    """Speichert bei Login-/Scraping-Fehlern einen Screenshot + URL zur Diagnose ohne sichtbaren Browser."""
    try:
        page.screenshot(path=AUTODARTS_DEBUG_SCREENSHOT)
    except Exception:
        pass


def _autodarts_match_id(item):
    if not isinstance(item, dict):
        return None
    return item.get('id') or item.get('matchId') or item.get('_id')


def _autodarts_list_page(token, page_no, sort):
    response = requests.get(
        'https://api.autodarts.com/as/v0/matches/filter',
        params={'size': AUTODARTS_PAGE_SIZE, 'page': page_no, 'sort': sort},
        headers={'Authorization': 'Bearer ' + token},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f'Match-Liste lieferte Status {response.status_code}.')
    payload = response.json()
    items = payload.get('items') or []
    return items, bool(payload.get('last')) or len(items) < AUTODARTS_PAGE_SIZE


def _remember_match_problem(state, match_id, message, status='pending', metadata=None):
    pending = state.setdefault('pending_matches', {})
    previous = pending.get(match_id) or {}
    pending[match_id] = {
        'status': status,
        'attempts': int(previous.get('attempts', 0) or 0) + 1,
        'first_seen_at': previous.get('first_seen_at') or datetime_to_iso(utc_now()),
        'last_attempt_at': datetime_to_iso(utc_now()),
        'last_error': message,
        'played_at': match_played_at(metadata or {}, match_id),
    }


def _autodarts_import_one_match(token, match_id, metadata, imported_matches, state, force=False):
    """Importiert ein Match und liefert (neu_importiert, fehler, verarbeitet)."""
    was_imported = match_id in imported_matches
    if was_imported and not force:
        state.setdefault('pending_matches', {}).pop(match_id, None)
        return False, None, False

    api_url = f'https://api.autodarts.com/as/v0/matches/{match_id}/stats'
    try:
        response = requests.get(
            api_url,
            headers={'Authorization': 'Bearer ' + token},
            timeout=15,
        )
    except Exception as exc:
        message = f'Fetch {match_id} fehlgeschlagen: {exc}'
        _remember_match_problem(state, match_id, message, 'pending', metadata)
        return False, message, False

    if not response.ok:
        message = f'Stats für Match {match_id} konnten nicht geladen werden (Status {response.status_code}).'
        retryable = response.status_code == 404 or response.status_code >= 500
        _remember_match_problem(state, match_id, message, 'pending' if retryable else 'failed', metadata)
        return False, message, False

    try:
        result = response.json()
    except Exception as exc:
        message = f'Stats für Match {match_id} enthalten kein gültiges JSON: {exc}'
        _remember_match_problem(state, match_id, message, 'pending', metadata)
        return False, message, False

    # Die Listenansicht enthält häufig den vollständigeren Match-Zeitstempel.
    for key in ('createdAt', 'created_at', 'finishedAt', 'finished_at'):
        if not result.get(key) and isinstance(metadata, dict) and metadata.get(key):
            result[key] = metadata[key]
    result.setdefault('id', match_id)

    explicitly_finished = any(result.get(key) for key in ('finishedAt', 'finished_at'))
    if not explicitly_finished:
        explicitly_finished = any(
            game.get('finishedAt') or game.get('finished_at')
            for game in result.get('games', []) or []
        )
    if not explicitly_finished:
        message = f'Match {match_id} ist noch nicht abgeschlossen; erneuter Versuch folgt.'
        _remember_match_problem(state, match_id, message, 'pending', result)
        return False, message, False

    games_len = len(result.get('games', []) or [])
    for leg_idx in range(games_len):
        try:
            leg_response = requests.get(
                f'{api_url}?leg={leg_idx}',
                headers={'Authorization': 'Bearer ' + token},
                timeout=15,
            )
            if leg_response.ok:
                leg_result = leg_response.json()
                if leg_result.get('games'):
                    result['games'][leg_idx] = leg_result['games'][0]
        except Exception:
            # Match-Level-Statistiken bleiben auch ohne Leg-Ergänzung nutzbar.
            pass

    try:
        import_match_result_to_scores(result, games_len=games_len, match_id=match_id)
    except Exception as exc:
        message = f'Verarbeitung von Match {match_id} fehlgeschlagen: {exc}'
        _remember_match_problem(state, match_id, message, 'pending', result)
        return False, message, False

    if not was_imported:
        imported_matches.append(match_id)
        save_imported_matches(imported_matches)
    state.setdefault('pending_matches', {}).pop(match_id, None)
    played_at = match_played_at(result, match_id)
    state['newest_finished_at'] = newest_timestamp(state.get('newest_finished_at'), played_at)
    save_autodarts_sync_state(state)
    return not was_imported, None, True


def _autodarts_browser_ids(cfg, max_pages):
    """Letzter inkrementeller Fallback; die Web-Historie ist neueste zuerst sortiert."""
    ids = []
    errors = []
    page_htmls = []
    if sync_playwright is None:
        return ids, ['Playwright ist nicht installiert; Browser-Fallback nicht möglich.'], page_htmls

    p = browser = context = page = None
    try:
        p = sync_playwright().start()
        profile = cfg.get('autodarts_user_data_dir') or None
        if profile:
            context = p.chromium.launch_persistent_context(user_data_dir=profile, headless=True)
            page = context.new_page()
        else:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            login_ok, login_error = _autodarts_form_login(
                page, cfg.get('autodarts_email'), cfg.get('autodarts_password')
            )
            if not login_ok:
                errors.append(login_error or 'Automatischer Formular-Login fehlgeschlagen.')

        for page_no in range(max_pages):
            url = 'https://play.autodarts.com/history/matches'
            if page_no:
                url += f'?page={page_no}'
            page.goto(url)
            page.wait_for_load_state('networkidle')
            try:
                page_htmls.append({'page': page_no, 'html_snippet': page.content()[:5000]})
            except Exception:
                page_htmls.append({'page': page_no, 'html_snippet': ''})
            try:
                page.wait_for_selector('a[href*="/history/matches/"]', timeout=5000)
            except Exception:
                pass
            links = page.eval_on_selector_all(
                'a[href*="/history/matches/"]',
                'els => els.map(e => e.getAttribute("href"))',
            )
            if not links:
                break
            for link in links:
                match_id = link.rstrip('/').split('/')[-1]
                if match_id and match_id not in ids:
                    ids.append(match_id)
    except Exception as exc:
        errors.append(f'Playwright-Fallback fehlgeschlagen: {exc}')
        if page is not None:
            _save_autodarts_debug_screenshot(page)
    finally:
        try:
            if context:
                context.close()
            elif browser:
                browser.close()
        except Exception:
            pass
        try:
            if p:
                p.stop()
        except Exception:
            pass
    return ids, errors, page_htmls


def autodarts_collect_and_import(mode='incremental', max_pages=None):
    """Synchronisiert Autodarts vollständig oder inkrementell.

    `backfill` liest explizit älteste zuerst und speichert den Seitenfortschritt.
    `incremental` liest explizit neueste zuerst und stoppt an einer vollständig
    bekannten Seite. `auto` wählt anhand des persistenten Sync-Zustands.
    """
    cfg = load_json(CONFIG_FILE)
    email = cfg.get('autodarts_email')
    password = cfg.get('autodarts_password')
    if not email or not password:
        return {'ok': False, 'error': 'Autodarts-Credentials nicht konfiguriert'}

    state = load_autodarts_sync_state()
    if mode == 'auto':
        mode = 'incremental' if state.get('initial_import_completed') else 'backfill'
    if mode not in {'backfill', 'incremental'}:
        return {'ok': False, 'error': 'Unbekannter Autodarts-Importmodus.'}

    # Ein bewusst erneut gestarteter vollständiger Abgleich beginnt wieder auf
    # Seite 0; ein unterbrochener Erstabgleich wird dagegen fortgesetzt.
    if mode == 'backfill' and state.get('initial_import_completed'):
        state['initial_import_completed'] = False
        state['backfill_next_page'] = 0
        save_autodarts_sync_state(state)

    token, login_error = autodarts_api_login(email, password)
    if not token:
        return {'ok': False, 'error': login_error or 'Autodarts-API-Login fehlgeschlagen.'}

    imported_matches = load_imported_matches()
    if not isinstance(imported_matches, list):
        imported_matches = []
    imported_set = set(imported_matches)
    new_imported = []
    refreshed_matches = []
    errors = []
    collected_ids = []
    page_htmls = []
    pages_scanned = 0

    # Noch nicht fertige Matches werden unabhängig von ihrer inzwischen weit
    # zurückliegenden Verlaufsseite erneut versucht. Permanente Fehler bleiben
    # sichtbar, werden aber nur bei einem vollständigen Abgleich erneut geprüft.
    retry_statuses = {'pending', 'failed'} if mode == 'backfill' else {'pending'}
    retry_items = [
        (match_id, details)
        for match_id, details in list(state.get('pending_matches', {}).items())
        if details.get('status') in retry_statuses and match_id not in imported_set
    ]
    for match_id, details in retry_items:
        imported, error, _processed = _autodarts_import_one_match(
            token, match_id, details, imported_matches, state
        )
        if imported:
            imported_set.add(match_id)
            new_imported.append(match_id)
        elif error:
            errors.append(error)

    if mode == 'backfill':
        page_no = max(0, int(state.get('backfill_next_page', 0) or 0))
        page_limit = max_pages or AUTODARTS_BACKFILL_MAX_PAGES
        while pages_scanned < page_limit:
            try:
                items, is_last = _autodarts_list_page(token, page_no, 'finished_at')
            except Exception as exc:
                errors.append(f'Erstabgleich Seite {page_no + 1} fehlgeschlagen: {exc}')
                break

            pages_scanned += 1
            save_autodarts_status(
                'running',
                f'Erstabgleich: Seite {page_no + 1}, bisher {len(new_imported)} Match(es) importiert …',
                mode=mode,
                pages_scanned=pages_scanned,
            )
            for item in items:
                match_id = _autodarts_match_id(item)
                if not match_id:
                    continue
                if match_id not in collected_ids:
                    collected_ids.append(match_id)
                was_known = match_id in imported_set
                imported, error, processed = _autodarts_import_one_match(
                    token, match_id, item, imported_matches, state, force=was_known
                )
                if imported:
                    imported_set.add(match_id)
                    new_imported.append(match_id)
                elif was_known and processed:
                    refreshed_matches.append(match_id)
                elif error:
                    errors.append(error)

            state['backfill_next_page'] = page_no + 1
            save_autodarts_sync_state(state)
            if is_last:
                state['initial_import_completed'] = True
                state['backfill_next_page'] = 0
                save_autodarts_sync_state(state)
                break
            page_no += 1
        else:
            errors.append(
                f'Erstabgleich erreichte das Sicherheitslimit von {page_limit} Seiten und wird beim nächsten Lauf fortgesetzt.'
            )

    else:
        page_limit = max_pages or AUTODARTS_INCREMENTAL_MAX_PAGES
        api_returned_items = False
        for page_no in range(page_limit):
            try:
                items, is_last = _autodarts_list_page(token, page_no, '-finished_at')
            except Exception as exc:
                errors.append(f'Neue Matches, Seite {page_no + 1}, konnten nicht geladen werden: {exc}')
                break

            pages_scanned += 1
            if items:
                api_returned_items = True
            page_ids = [_autodarts_match_id(item) for item in items]
            page_ids = [match_id for match_id in page_ids if match_id]
            for match_id in page_ids:
                if match_id not in collected_ids:
                    collected_ids.append(match_id)

            known_before_page = imported_set | set(state.get('pending_matches', {}))
            completely_known = bool(page_ids) and all(
                match_id in known_before_page for match_id in page_ids
            )
            if not completely_known:
                items_by_id = {_autodarts_match_id(item): item for item in items}
                for match_id in page_ids:
                    if match_id in imported_set or match_id in state.get('pending_matches', {}):
                        continue
                    imported, error, _processed = _autodarts_import_one_match(
                        token, match_id, items_by_id.get(match_id) or {}, imported_matches, state
                    )
                    if imported:
                        imported_set.add(match_id)
                        new_imported.append(match_id)
                    elif error:
                        errors.append(error)

            if completely_known or is_last or not items:
                break
        else:
            errors.append(
                f'Die Suche erreichte das Sicherheitslimit von {page_limit} Seiten, bevor eine vollständig bekannte Seite gefunden wurde.'
            )

        # Die API bleibt der verlässliche Hauptweg. Nur wenn sie erfolgreich
        # erreichbar war, aber keine Elemente lieferte, wird die sichtbare
        # Verlaufsliste als letzter inkrementeller Fallback geprüft.
        if not api_returned_items and not collected_ids:
            browser_ids, browser_errors, page_htmls = _autodarts_browser_ids(cfg, page_limit)
            pages_scanned += len(page_htmls)
            errors.extend(browser_errors)
            for match_id in browser_ids:
                if match_id not in collected_ids:
                    collected_ids.append(match_id)
                if match_id in imported_set or match_id in state.get('pending_matches', {}):
                    continue
                imported, error, _processed = _autodarts_import_one_match(
                    token, match_id, {}, imported_matches, state
                )
                if imported:
                    imported_set.add(match_id)
                    new_imported.append(match_id)
                elif error:
                    errors.append(error)

    save_imported_matches(imported_matches)
    save_autodarts_sync_state(state)
    pending_count = sum(
        1 for item in state.get('pending_matches', {}).values() if item.get('status') == 'pending'
    )
    failed_count = sum(
        1 for item in state.get('pending_matches', {}).values() if item.get('status') == 'failed'
    )
    run_ok = pages_scanned > 0
    return {
        'ok': run_ok,
        'error': None if run_ok else 'Die Autodarts-Matchliste konnte nicht geladen werden.',
        'mode': mode,
        'initial_import_completed': bool(state.get('initial_import_completed')),
        'pages_scanned': pages_scanned,
        'imported_matches': new_imported,
        'refreshed_matches': refreshed_matches,
        'errors': errors,
        'pending_count': pending_count,
        'failed_count': failed_count,
        'collected_match_ids': collected_ids,
        'page_htmls': page_htmls,
    }


def start_autodarts_import(mode='auto', max_pages=None):
    """Startet einen vollständigen oder inkrementellen Hintergrundabgleich."""
    if not AUTODARTS_RUN_LOCK.acquire(blocking=False):
        return False

    def worker(import_mode, pages):
        try:
            current_state = load_autodarts_sync_state()
            effective_mode = import_mode
            if effective_mode == 'auto':
                effective_mode = (
                    'incremental' if current_state.get('initial_import_completed') else 'backfill'
                )
            label = 'Vollständiger Erstabgleich' if effective_mode == 'backfill' else 'Suche nach neuen Matches'
            save_autodarts_status('running', f'{label} läuft …', mode=effective_mode)
            try:
                res = autodarts_collect_and_import(mode=import_mode, max_pages=pages)
            except Exception as e:
                res = {"ok": False, "error": str(e)}

            finished_at = utc_now()
            cfg = load_json(CONFIG_FILE)
            cfg['autodarts_last_run'] = format_local_datetime(finished_at)
            save_json(CONFIG_FILE, cfg)
            save_json(AUTODARTS_LAST_RESULT_FILE, res)

            sync_state = load_autodarts_sync_state()
            sync_state['last_check_at'] = datetime_to_iso(finished_at)
            if res.get('ok'):
                sync_state['last_success_at'] = datetime_to_iso(finished_at)
            try:
                interval = max(1, int(cfg.get('autodarts_interval_minutes', 60)))
            except (TypeError, ValueError):
                interval = 60
            sync_state['interval_minutes'] = interval
            sync_state['next_check_at'] = (
                datetime_to_iso(finished_at + timedelta(minutes=interval))
                if cfg.get('autodarts_enabled') else None
            )
            save_autodarts_sync_state(sync_state)

            if res.get('ok'):
                errors = res.get('errors') or []
                mode_label = 'Erstabgleich' if res.get('mode') == 'backfill' else 'Neue-Matches-Suche'
                details = (
                    f"{mode_label} beendet. Seiten: {res.get('pages_scanned', 0)}, "
                    f"importiert: {len(res.get('imported_matches') or [])}, "
                    f"aktualisiert: {len(res.get('refreshed_matches') or [])}, "
                    f"ausstehend: {res.get('pending_count', 0)}, fehlgeschlagen: {res.get('failed_count', 0)}."
                )
                if errors:
                    save_autodarts_status('success', f'{details} {len(errors)} Hinweis(e).', errors=errors)
                else:
                    save_autodarts_status('success', details)
            else:
                save_autodarts_status('error', res.get('error') or 'Unbekannter Fehler.', errors=res.get('errors') or [])
        except Exception as e:
            app.logger.exception('Autodarts-Hintergrundimport fehlgeschlagen')
            save_autodarts_status('error', str(e))
        finally:
            AUTODARTS_RUN_LOCK.release()

    t = threading.Thread(target=worker, args=(mode, max_pages))
    t.daemon = True
    t.start()
    return True


def autodarts_scheduler_tick(config=None, now=None):
    """Führt genau eine testbare Scheduler-Entscheidung aus."""
    config = config or load_json(CONFIG_FILE)
    enabled = bool(config.get('autodarts_enabled'))
    try:
        interval = max(1, int(config.get('autodarts_interval_minutes', 60)))
    except (TypeError, ValueError):
        interval = 60

    now = now or utc_now()
    state = load_autodarts_sync_state()
    if not enabled:
        if state.get('next_check_at') is not None or state.get('interval_minutes') != interval:
            state['next_check_at'] = None
            state['interval_minutes'] = interval
            save_autodarts_sync_state(state)
        return False

    next_check = parse_datetime(state.get('next_check_at'))
    if state.get('interval_minutes') != interval or next_check is None:
        # Aktivierung, geändertes Intervall oder eine neue Installation:
        # sofort abgleichen statt erst ein vollständiges Intervall zu warten.
        next_check = now
        state['interval_minutes'] = interval
        state['next_check_at'] = datetime_to_iso(next_check)
        save_autodarts_sync_state(state)
    if now < next_check:
        return False

    # Provisorischer Wiederanlaufpunkt. Der Worker ersetzt ihn nach Abschluss
    # durch „Ende + Intervall“. Nach einem Prozessabsturz erfolgt spätestens
    # nach fünf Minuten ein neuer Versuch.
    retry_minutes = min(5, interval)
    state['next_check_at'] = datetime_to_iso(now + timedelta(minutes=retry_minutes))
    state['interval_minutes'] = interval
    save_autodarts_sync_state(state)
    return bool(start_autodarts_import(mode='auto'))


def autodarts_scheduler():
    """Persistenter Scheduler: überfällige Läufe starten auch nach Neustarts sofort."""
    while True:
        autodarts_scheduler_tick()
        time.sleep(5)


def start_autodarts_scheduler():
    """Stellt sicher, dass der Scheduler nur einmal pro Serverprozess läuft."""
    global AUTODARTS_SCHEDULER_STARTED
    with AUTODARTS_SCHEDULER_LOCK:
        if AUTODARTS_SCHEDULER_STARTED:
            return
        AUTODARTS_SCHEDULER_STARTED = True
        thread = threading.Thread(target=autodarts_scheduler, daemon=True)
        thread.start()


@app.route('/admin/run_autodarts', methods=['POST'])
def admin_run_autodarts():
    # Beide Buttons nutzen denselben Worker; nur der Suchmodus unterscheidet sich.
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'
    mode = request.form.get('mode') or 'incremental'
    if mode not in {'backfill', 'incremental'}:
        if is_ajax:
            return jsonify({'ok': False, 'error': 'Unbekannter Importmodus.'}), 400
        return redirect(url_for('admin'))
    started = start_autodarts_import(mode=mode)

    if is_ajax:
        return jsonify({"ok": started, "started": started, "error": None if started else "Autodarts-Abruf läuft bereits."}), 202 if started else 409
    return redirect(url_for('admin'))


@app.route('/admin/autodarts_status', methods=['GET'])
def admin_autodarts_status():
    """Liefert den aktuellen Status eines laufenden/letzten Autodarts-Laufs für Live-Polling."""
    status = load_autodarts_status()
    last_result = {}
    try:
        lr_path = AUTODARTS_LAST_RESULT_FILE
        if os.path.exists(lr_path):
            with open(lr_path, 'r', encoding='utf-8') as f:
                last_result = json.load(f)
    except Exception:
        last_result = {}
    return jsonify({
        "status": status,
        "last_result": last_result,
        "sync_state": load_autodarts_sync_state(),
    })


@app.route('/admin/autodarts_manual_login', methods=['POST'])
def admin_autodarts_manual_login():
    """Startet einen sichtbaren Browser für die manuelle Anmeldung bei Autodarts.
    Erfordert einen konfigurierten Chromium-Profile-Ordner (autodarts_user_data_dir) sowie
    ein lokales Display; funktioniert daher nur auf Rechnern mit sichtbarem Bildschirm
    (z.B. beim Debuggen), nicht auf einem headless-Server."""
    if sync_playwright is None:
        return jsonify({"ok": False, "error": "Playwright ist nicht installiert."}), 400

    cfg = load_json(CONFIG_FILE)
    user_data_dir = cfg.get('autodarts_user_data_dir')
    if not user_data_dir:
        return jsonify({"ok": False, "error": "Bitte zuerst einen Chromium Profile-Ordner (autodarts_user_data_dir) speichern."}), 400

    # Ein sichtbarer Browser (headless=False) benötigt ein lokales Display. Auf einem
    # headless-Linux-Server (z.B. ohne X-Server/Xvfb) schlägt das Starten des Browsers
    # sofort fehl. Das prüfen wir vorab, um einen sofortigen, verständlichen Fehler
    # statt eines kryptischen Playwright-Fehlers zurückzugeben.
    if os.name == 'posix' and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        msg = ('Kein lokales Display gefunden (Umgebungsvariable DISPLAY ist nicht gesetzt). '
               'Die manuelle Anmeldung öffnet einen sichtbaren Browser und funktioniert daher nur '
               'auf einem Rechner mit Bildschirm bzw. mit eingerichtetem X-Server/Xvfb, nicht auf '
               'einem headless-Server.')
        save_autodarts_status('manual_login_error', msg)
        return jsonify({"ok": False, "error": msg}), 400

    def worker(profile_dir):
        save_autodarts_status('manual_login_pending', 'Bitte im geöffneten Browser-Fenster manuell anmelden…')
        p = None
        context = None
        try:
            p = sync_playwright().start()
            context = p.chromium.launch_persistent_context(profile_dir, headless=False)
            page = context.new_page()
            page.goto('https://play.autodarts.com/login')

            # Auf erfolgreichen Login warten: URL verlässt /login bzw. /auth
            deadline = time.time() + 300  # 5 Minuten Zeit für die manuelle Anmeldung
            logged_in = False
            while time.time() < deadline:
                try:
                    if '/login' not in page.url and '/auth' not in page.url:
                        logged_in = True
                        break
                except Exception:
                    break
                time.sleep(1)

            if logged_in:
                save_autodarts_status('manual_login_success', 'Manuelle Anmeldung erfolgreich. Session wurde im Profil-Ordner gespeichert.')
            else:
                save_autodarts_status('manual_login_timeout', 'Zeitüberschreitung: Es wurde innerhalb von 5 Minuten keine erfolgreiche Anmeldung erkannt.')
        except Exception as e:
            err_text = str(e)
            if "Executable doesn't exist" in err_text or "playwright install" in err_text:
                hint = 'Bitte die Playwright-Browser installieren (z.B. mit "playwright install chromium").'
            elif not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
                hint = 'Evtl. kein Display verfügbar (Umgebungsvariable DISPLAY ist nicht gesetzt).'
            else:
                hint = ''
            prefix = f'Manueller Login fehlgeschlagen: {hint}' if hint else 'Manueller Login fehlgeschlagen:'
            save_autodarts_status('manual_login_error', f'{prefix} {err_text}')
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if p:
                    p.stop()
            except Exception:
                pass

    import threading
    t = threading.Thread(target=worker, args=(user_data_dir,))
    t.daemon = True
    t.start()
    return jsonify({"ok": True, "started": True})


@app.route('/admin/import_match', methods=['POST'])
def admin_import_match():
    mid = request.form.get('match_id') or (request.get_json(silent=True) or {}).get('match_id')
    if not mid:
        return redirect(url_for('admin'))
    # Accept full URLs as input; extract the last path segment as ID
    try:
        if isinstance(mid, str) and ('/' in mid or '?' in mid):
            # remove query string and fragments
            mid_clean = mid.split('?')[0].split('#')[0].rstrip('/')
            # take last path segment
            parts = mid_clean.split('/')
            candidate = parts[-1] if parts else mid_clean
            # if candidate looks like a UUID or contains '-' assume it's the id
            if candidate:
                mid = candidate
    except Exception:
        pass

    cfg = load_json(CONFIG_FILE)
    email = cfg.get('autodarts_email')
    password = cfg.get('autodarts_password')
    if not email or not password:
        save_autodarts_status('error', 'Autodarts-Credentials nicht konfiguriert.')
        return redirect(url_for('admin'))

    try:
        token, login_error = autodarts_api_login(email, password)
        if not token:
            save_autodarts_status('error', login_error or 'Autodarts-API-Login fehlgeschlagen.')
            return redirect(url_for('admin'))

        imported_matches = load_imported_matches()
        if not isinstance(imported_matches, list):
            imported_matches = []
        state = load_autodarts_sync_state()
        imported, error, _processed = _autodarts_import_one_match(
            token, mid, {}, imported_matches, state
        )
        save_autodarts_sync_state(state)
        if imported:
            save_autodarts_status('success', f'Einzelmatch {mid} wurde importiert.')
        elif mid in imported_matches:
            save_autodarts_status('success', f'Einzelmatch {mid} war bereits importiert.')
        else:
            save_autodarts_status('error', error or f'Einzelmatch {mid} konnte nicht importiert werden.')
    except Exception as exc:
        app.logger.exception('Autodarts-Einzelimport fehlgeschlagen')
        save_autodarts_status('error', f'Einzelimport fehlgeschlagen: {exc}')

    return redirect(url_for('admin'))


@app.route('/admin/install_screensaver', methods=['POST'])
def admin_install_screensaver():
    """Backward-compatible alias for the managed screensaver installation."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'
    try:
        manifest = discover_addons(ADDONS_DIR).get('screensaver')
        if not manifest:
            msg = 'Screensaver-Addon wurde nicht gefunden.'
            if is_ajax:
                return jsonify({"ok": False, "error": msg}), 404
            return redirect(url_for('admin'))
        write_screensaver_config(load_json(CONFIG_FILE))
        manage_addon(manifest, 'install', os.path.expanduser('~'))
        msg = 'Screensaver installiert, aktiviert und gestartet.'
        if is_ajax:
            return jsonify({"ok": True, "message": msg})
        return redirect(url_for('admin'))
    except (OSError, AddonError):
        app.logger.exception('Screensaver-Installation fehlgeschlagen')
        msg = 'Installation fehlgeschlagen. Details siehe Server-Log.'
        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 500
        return redirect(url_for('admin'))


@app.route('/admin/install_cec', methods=['POST'])
def admin_install_cec():
    """Backward-compatible alias for the manifest-driven CEC installation."""
    try:
        manifest = discover_addons(ADDONS_DIR).get('cec')
        if not manifest:
            return jsonify({"ok": False, "error": "CEC-Addon wurde nicht gefunden."}), 404
        write_cec_config(load_json(CONFIG_FILE))
        manage_addon(manifest, 'install', os.path.expanduser('~'))
        return jsonify({
            "ok": True,
            "message": "CEC-Manager installiert und aktiviert. Die Konfiguration wird beim Speichern übernommen.",
        })
    except (OSError, AddonError):
        app.logger.exception('CEC-Installation fehlgeschlagen')
        return jsonify({
            "ok": False,
            "error": "CEC-Installation fehlgeschlagen. Details stehen im Server-Log.",
        }), 500


@app.route('/admin/addons/status', methods=['GET'])
def admin_addons_status():
    """Return actual installed/enabled/active states for shipped add-ons."""
    try:
        return jsonify({"ok": True, "addons": all_addon_statuses(ADDONS_DIR, os.path.expanduser('~'))})
    except AddonError as exc:
        return jsonify({"ok": False, "error": str(exc), "addons": {}}), 500


@app.route('/admin/addons/<addon_id>/<action>', methods=['POST'])
def admin_manage_addon(addon_id, action):
    """Manage one bundled add-on through an exact, validated action allow-list."""
    if action not in ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": "Nicht erlaubte Add-on-Aktion."}), 400
    try:
        manifests = discover_addons(ADDONS_DIR)
        manifest = manifests.get(addon_id)
        if not manifest:
            return jsonify({"ok": False, "error": "Unbekanntes Add-on."}), 404
        config = load_json(CONFIG_FILE)
        if addon_id == 'cec':
            write_cec_config(config)
        elif addon_id == 'kiosk':
            write_kiosk_config(config)
        elif addon_id == 'screensaver':
            write_screensaver_config(config)
        if action in {'install', 'update', 'start', 'restart'} and addon_id in {'kiosk', 'screensaver'}:
            other_id = 'screensaver' if addon_id == 'kiosk' else 'kiosk'
            other = manifests.get(other_id)
            if other:
                other_status = addon_status(other, os.path.expanduser('~'))
                if other_status.get('active'):
                    manage_addon(other, 'stop', os.path.expanduser('~'))
        manage_addon(manifest, action, os.path.expanduser('~'))
        status = addon_status(manifest, os.path.expanduser('~'))
        return jsonify({
            "ok": True,
            "message": f"{manifest['name']}: {action} erfolgreich.",
            "status": status,
        })
    except AddonError as exc:
        app.logger.error('Add-on-Aktion %s/%s fehlgeschlagen: %s', addon_id, action, exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == '__main__':
    debug = os.environ.get('DART_SCOREBOARD_DEBUG') == '1'
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_autodarts_scheduler()
    app.run(debug=debug, host='0.0.0.0', port=5000)
