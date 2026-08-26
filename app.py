import os
import re
import json
import random
import shutil
import stat
import subprocess
import time
import shlex
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify
import socket
import requests
import qrcode
import io
import base64
import hashlib

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
SCREENSAVER_ADDON_DIR = os.path.join(ADDONS_DIR, 'Raspberry-Screensaver')
CEC_ADDON_DIR = os.path.join(ADDONS_DIR, 'Raspberry-CEC')
CEC_CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'dart-scoreboard')
CEC_CONFIG_FILE = os.path.join(CEC_CONFIG_DIR, 'cec.conf')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

BACKGROUND_FILENAME = "background.jpg"

DEFAULT_CONFIG = {
    "background_url": None,
    "static_limit": 5,
    "rotation_limit": 10,
    "static_h2_size": "2.5em",
    "rotation_h2_size": "3.5em",
    "static_td_size": "2.0em",
    "rotation_td_size": "1.5em",
    "font_family": "'Segoe UI', Roboto, sans-serif",
    # Wartezeiten (in Sekunden) für die einzelnen Ansichten der Rotation
    "rotation_duration_grid1": 300,
    "rotation_duration_grid2": 60,
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
}

# Datei für bereits importierte Match-IDs
IMPORTED_MATCHES_FILE = os.path.join(DATA_DIR, 'imported_matches.json')

# Status-Datei für laufende/letzte Autodarts-Läufe (für Live-Feedback im Admin-Bereich)
AUTODARTS_STATUS_FILE = os.path.join(DATA_DIR, 'autodarts_status.json')
AUTODARTS_DEBUG_SCREENSHOT = os.path.join(DATA_DIR, 'autodarts_debug.png')

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
    content = (
        "# Wird vom Dart Scoreboard Adminbereich verwaltet.\n"
        f"CEC_ENABLED={'1' if config.get('cec_enabled') else '0'}\n"
        f"CEC_NAME={shlex.quote(config.get('cec_device_name', 'Dart Scoreboard'))}\n"
        f"CEC_STANDBY_TIME={shlex.quote(config.get('cec_standby_time', '22:00'))}\n"
        f"CEC_WAKE_TIME={shlex.quote(config.get('cec_wake_time', '08:00'))}\n"
    )
    with open(CEC_CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(CEC_CONFIG_FILE, 0o600)


def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_imported_matches():
    if not os.path.exists(IMPORTED_MATCHES_FILE) or os.path.getsize(IMPORTED_MATCHES_FILE) == 0:
        return []
    try:
        with open(IMPORTED_MATCHES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_imported_matches(lst):
    with open(IMPORTED_MATCHES_FILE, 'w', encoding='utf-8') as f:
        json.dump(lst, f, indent=4, ensure_ascii=False)


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
        with open(AUTODARTS_STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(status, f, indent=4, ensure_ascii=False)
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


def get_player_id(player_name, players):
    normalized_name = player_name.strip()
    if not normalized_name:
        return None

    for p in players:
        if p["name"] == normalized_name:
            return p["id"]

    new_id = max((p["id"] for p in players), default=0) + 1
    new_player = {"id": new_id, "name": normalized_name, "image": "dummy.png", "autodarts_name": ""}
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
    """Fasst Spieler mit demselben Autodarts-Namen für die Anzeige zusammen."""
    player_ids = {}
    players_map = {}
    display_players = []
    autodarts_groups = {}

    for player in players_list:
        player_id = player.get("id")
        if player_id is None:
            continue

        autodarts_name = (player.get("autodarts_name") or "").strip().casefold()
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
                "legs": 0, "max180": 0, "last180_date": "",
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
            }
        cumulative[pid]["legs"]   += s.get("legs",   0)
        cumulative[pid]["max180"] += s.get("max180", 0)
        cumulative[pid]["s60"]    += s.get("s60",    0)
        cumulative[pid]["s100"]   += s.get("s100",   0)
        cumulative[pid]["s140"]   += s.get("s140",   0)
        cumulative[pid]["s170"]   += s.get("s170",   0)
        cumulative[pid]["s180"]   += s.get("s180",   0)
        cumulative[pid]["games_played"] += s.get("games_played", 0)
        # extended sums
        cumulative[pid]["points_sum"] += s.get("points_sum", 0) or 0
        cumulative[pid]["darts_thrown"] += s.get("darts_thrown", 0) or 0
        cumulative[pid]["first9_points_sum"] += s.get("first9_points_sum", 0) or 0
        cumulative[pid]["first9_darts"] += s.get("first9_darts", 0) or 0
        cumulative[pid]["first3_points_sum"] += s.get("first3_points_sum", 0) or 0
        cumulative[pid]["first3_darts"] += s.get("first3_darts", 0) or 0
        cumulative[pid]["checkout_success"] += s.get("checkout_success", 0) or 0
        cumulative[pid]["checkout_attempts"] += s.get("checkout_attempts", 0) or 0
        # merge segment_hits dict
        segs = s.get("segment_hits") or {}
        for k,v in segs.items():
            cumulative[pid]["segment_hits"][k] = cumulative[pid]["segment_hits"].get(k, 0) + (v or 0)
        
        finish_val = s.get("finish", 0)
        if finish_val > cumulative[pid].get("best_finish", 0):
            cumulative[pid]["best_finish"] = finish_val
            
        if s.get("max180", 0) > 0 or s.get("s180", 0) > 0:
            cumulative[pid]["last180_date"] = s.get("date", "")

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

    # Always collect segment hits from per-leg throws (do not use them to recompute the main aggregates
    # when match-level stats are available)
    for game in res.get('games', []):
        for turn in game.get('turns', []) or []:
            pid = turn.get('playerId')
            if not pid:
                continue
            st = stats.setdefault(pid, {})
            throws = turn.get('throws') or []
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
                seg_hits = st.setdefault('segment_hits', {})
                seg_hits[key] = seg_hits.get(key, 0) + 1

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
                    'checkout_success': 0, 'checkout_attempts': 0,
                    'points_sum': 0, 'darts_thrown': 0,
                    'first9_points_sum': 0, 'first9_darts': 0,
                    'segment_hits': {},
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

    return stats


def import_match_result_to_scores(result, games_len=None):
    """Wandelt ein einzelnes Autodarts-Match-Ergebnis in Score-Einträge um und
    speichert sie. BOT-Gegner (siehe detect_bot_level) werden dabei nicht als
    eigenständige Spieler angelegt; stattdessen werden die Ergebnisse der
    menschlichen Spieler aus einem Match gegen einen BOT getrennt in
    BOT_SCORES_FILE (pro Bot-Level) abgelegt, statt in die reguläre
    Spieler-Statistik einzufließen.

    Gibt (imported_count, bot_imported_count) zurück."""
    if games_len is None:
        games_len = len(result.get('games', []) or [])

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

    imported_count = 0
    bot_imported_count = 0

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
            'average': st.get('average'),
            'first9_average': st.get('first9_average'),
            'first9_points_sum': int(st.get('first9_points_sum', 0) or 0),
            'first9_darts': int(st.get('first9_darts', 0) or 0),
            'first3_average': st.get('first3_average'),
            'first3_points_sum': int(st.get('first3_points_sum', 0) or 0),
            'first3_darts': int(st.get('first3_darts', 0) or 0),
            'darts_thrown': st.get('darts_thrown'),
            'points_sum': st.get('points_sum'),
            # record the number of legs played in this match, so cumulative stats
            # aggregate "legs played" instead of "matches played" (one match can
            # contain several legs).
            'total_games_in_import': games_len or 1,
        }

        pid = None
        ad = (entry.get('autodarts_name') or '').strip()
        if ad:
            for pl in players_local:
                if pl.get('autodarts_name', '').strip().lower() == ad.lower():
                    pid = pl['id']
                    break
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
            'date': datetime.now().strftime('%d.%m.%Y %H:%M'),
            # extended fields
            'segment_hits': entry.get('segment_hits', {}),
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
        }
        if is_bot_match:
            new_score['bot_level'] = bot_level

        try:
            new_hash = compute_score_hash(new_score)
            new_score['score_hash'] = new_hash
        except Exception:
            new_hash = None

        target_list = bot_scores if is_bot_match else scores
        dup = False
        for ex in target_list:
            if ex.get('score_hash') and new_hash and ex.get('score_hash') == new_hash:
                dup = True
                break
            try:
                if compute_score_hash(ex) == new_hash:
                    dup = True
                    break
            except Exception:
                continue
        if not dup:
            target_list.append(new_score)
            if is_bot_match:
                bot_imported_count += 1
            else:
                imported_count += 1

    save_json(PLAYERS_FILE, players_local)
    if imported_count:
        save_json(SCORES_FILE, scores)
    if bot_imported_count:
        save_json(BOT_SCORES_FILE, bot_scores)

    return imported_count, bot_imported_count


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

    # Höchstes Finish
    finish_best = {}
    for s in scores:
        pid = player_ids.get(s.get("player_id"))
        if pid is None:
            continue
        val = s.get("finish", 0)
        if val > 0 and val > finish_best.get(pid, {}).get("finish", 0):
            finish_best[pid] = {
                "name": player_name(pid),
                "image": player_image(pid),
                "finish": val,
                "finish_date": s.get("date", ""),
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

    return jsonify({'ok': True, 'player': card})


@app.route("/admin", methods=["GET", "POST"])
def admin():
    players = load_json(PLAYERS_FILE)
    scores = load_json(SCORES_FILE)
    config = load_json(CONFIG_FILE)

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

        elif action == "save_autodarts_name":
            try:
                ad_id = int(request.form.get("autodarts_player_id"))
                ad_name = request.form.get("autodarts_name", "").strip()
                for p in players:
                    if p["id"] == ad_id:
                        p["autodarts_name"] = ad_name
                        break
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

                def _form_time(name, fallback):
                    value = _form_str(name, fallback)
                    return value if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) else fallback

                new_config = {
                    "static_limit": _form_int("static_limit", config.get("static_limit", 5)),
                    "rotation_limit": _form_int("rotation_limit", config.get("rotation_limit", 10)),
                    "static_h2_size": _form_str("static_h2_size", config.get("static_h2_size", "2.5em")),
                    "rotation_h2_size": _form_str("rotation_h2_size", config.get("rotation_h2_size", "3.5em")),
                    "static_td_size": _form_str("static_td_size", config.get("static_td_size", "2.0em")),
                    "rotation_td_size": _form_str("rotation_td_size", config.get("rotation_td_size", "1.5em")),
                    "font_family": _form_str("font_family", config.get("font_family", "'Segoe UI', Roboto, sans-serif")),
                    # Wartezeiten (Sekunden) der einzelnen Rotations-Ansichten
                    "rotation_duration_grid1": _form_int("rotation_duration_grid1", config.get("rotation_duration_grid1", 300)),
                    "rotation_duration_grid2": _form_int("rotation_duration_grid2", config.get("rotation_duration_grid2", 60)),
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
                }
                current_config = load_json(CONFIG_FILE)
                current_config.update(new_config)
                save_json(CONFIG_FILE, current_config)
                write_cec_config(current_config)
                if "cec_device_name" in request.form:
                    try:
                        subprocess.run(
                            ['systemctl', '--user', 'try-restart', 'hdmi-audio-cec.service'],
                            capture_output=True, text=True, timeout=15, check=False,
                        )
                    except (OSError, subprocess.SubprocessError):
                        pass
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

    bg_image = f"uploads/{BACKGROUND_FILENAME}" if background_exists() else None

    # load last autodarts run result if available
    last_result = {}
    try:
        lr_path = os.path.join(DATA_DIR, 'autodarts_last_result.json')
        if os.path.exists(lr_path):
            with open(lr_path, 'r', encoding='utf-8') as f:
                last_result = json.load(f)
    except Exception:
        last_result = {}

    autodarts_status = load_autodarts_status()
    bot_stats = get_bot_cumulative_stats()

    return render_template(
        "admin.html",
        players=players,
        scores=admin_scores,
        background_exists=background_exists(),
        bg_image=bg_image,
        config=config,
        autodarts_last_result=last_result,
        autodarts_status=autodarts_status,
        bot_stats=bot_stats,
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

        autodarts_map = {
            p.get("autodarts_name", "").strip().lower(): p["id"]
            for p in players
            if p.get("autodarts_name", "").strip()
        }

        for entry in payload:
            pid = entry.get("player_id")
            if pid:
                try:
                    pid = int(pid)
                except Exception:
                    pid = None
            if not pid:
                ad_name = (entry.get("autodarts_name") or entry.get("player_name") or "").strip()
                pid = autodarts_map.get(ad_name.lower())
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


def autodarts_collect_and_import(max_pages=2):
    """Sammelt Autodarts-Match-IDs und importiert deren Statistiken.

    Bevorzugt den zuverlässigen API-Token-Login (unabhängig von HTML-Struktur/Selektoren).
    Nur wenn darüber keine Matches ermittelt werden können, wird Playwright als Fallback
    genutzt - inklusive echtem Formular-Login (Email/Passwort), analog zu
    scripts/autodarts_fetch.py. Gibt ein dict mit importierten IDs und Fehlern zurück."""
    cfg = load_json(CONFIG_FILE)
    email = cfg.get('autodarts_email')
    password = cfg.get('autodarts_password')
    if not email or not password:
        return {"ok": False, "error": "Autodarts-Credentials nicht konfiguriert"}

    imported_matches = load_imported_matches()
    new_imported = []
    errors = []
    page_htmls = []
    match_ids = []

    # 1) Zuverlässigster Weg: Login über die Autodarts-API. Kein Browser nötig und
    #    unabhängig von HTML-Selektoren, die sich jederzeit ändern können.
    token, login_error = autodarts_api_login(email, password)
    if login_error:
        errors.append(login_error)

    if token:
        try:
            page_no = 0
            size = 50
            while page_no < max_pages:
                url = f'https://api.autodarts.com/as/v0/matches/filter?size={size}&page={page_no}'
                lm = requests.get(url, headers={'Authorization': 'Bearer ' + token}, timeout=15)
                if not lm.ok:
                    if lm.status_code == 401:
                        errors.append('API-Token wurde beim Abrufen der Match-Liste abgelehnt (401).')
                    break
                lj = lm.json()
                items = lj.get('items') or []
                if not items:
                    break
                for item in items:
                    mid = item.get('id') or item.get('matchId') or item.get('_id')
                    if mid and mid not in match_ids:
                        match_ids.append(mid)
                if lj.get('last'):
                    break
                page_no += 1
        except Exception as e:
            errors.append(f'Match-Liste über API konnte nicht geladen werden: {e}')

    # 2) Fallback: Playwright-Browser, falls die API-Route keine Matches geliefert hat.
    user_data_dir = cfg.get('autodarts_user_data_dir') or None
    if not match_ids:
        if sync_playwright is None:
            errors.append('Playwright ist nicht installiert; Browser-Fallback nicht möglich (pip install playwright && playwright install chromium).')
        else:
            p = None
            browser = None
            context = None
            page = None
            try:
                p = sync_playwright().start()
                if user_data_dir:
                    # Wiederverwendung eines bereits (z.B. manuell) eingeloggten Chromium-Profils
                    try:
                        context = p.chromium.launch_persistent_context(user_data_dir=user_data_dir, headless=True)
                        page = context.new_page()
                    except Exception as e:
                        errors.append(f'Chromium-Profil konnte nicht geladen werden: {e}')
                else:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()
                    login_ok, form_login_error = _autodarts_form_login(page, email, password)
                    if not login_ok:
                        errors.append(form_login_error or 'Automatischer Formular-Login fehlgeschlagen.')
                        _save_autodarts_debug_screenshot(page)

                if page is not None:
                    for page_no in range(max_pages):
                        url = 'https://play.autodarts.com/history/matches' if page_no == 0 else f'https://play.autodarts.com/history/matches?page={page_no}'
                        page.goto(url)
                        page.wait_for_load_state('networkidle')
                        try:
                            html = page.content()
                            page_htmls.append({'page': page_no, 'html_snippet': html[:5000]})
                        except Exception:
                            page_htmls.append({'page': page_no, 'html_snippet': ''})
                        try:
                            page.wait_for_selector('a[href*="/history/matches/"]', timeout=5000)
                        except Exception:
                            pass
                        links = page.eval_on_selector_all('a[href*="/history/matches/"]', 'els => els.map(e => e.getAttribute("href"))')
                        for l in links:
                            if '/history/matches/' in l:
                                parts = l.rstrip('/').split('/')
                                mid = parts[-1]
                                if mid and mid not in match_ids:
                                    match_ids.append(mid)
                        if not links:
                            errors.append('Keine Matches auf der Verlaufsseite gefunden; vermutlich nicht eingeloggt. Nutze "Manuell anmelden" oder prüfe die Zugangsdaten.')
                            _save_autodarts_debug_screenshot(page)
                            break

                    # Letzter Fallback: API-Aufruf über die Browser-Session (Cookies)
                    if not match_ids:
                        try:
                            api_fetch = page.evaluate(
                                '(url) => fetch(url).then(r=>r.ok? r.json(): {status:r.status}).catch(e=>({error:e.toString()}))',
                                'https://api.autodarts.com/as/v0/matches?limit=50',
                            )
                            if api_fetch:
                                candidates = []
                                if isinstance(api_fetch, list):
                                    candidates = api_fetch
                                elif isinstance(api_fetch, dict):
                                    candidates = api_fetch.get('matches') or api_fetch.get('data') or []
                                for item in candidates:
                                    mid = item.get('id') or item.get('matchId') or item.get('_id')
                                    if mid and mid not in match_ids:
                                        match_ids.append(mid)
                        except Exception:
                            pass
            except Exception as e:
                errors.append(f'Playwright-Fallback fehlgeschlagen: {e}')
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

    if not match_ids:
        save_imported_matches(imported_matches)
        return {"ok": False, "error": "Keine Matches gefunden", "errors": errors, "collected_match_ids": match_ids, "page_htmls": page_htmls}

    # 3) Für jede noch nicht importierte Match-ID die Stats-API abrufen (Token bevorzugt).
    #    Der Login wurde bereits in Schritt 1 versucht; ein erneuter Versuch mit denselben
    #    Zugangsdaten würde nur denselben Fehler wiederholen.
    for mid in match_ids:
        if mid in imported_matches:
            continue
        if not token:
            errors.append(f'Kein gültiges API-Token vorhanden; Stats für Match {mid} übersprungen.')
            continue

        api_url = f'https://api.autodarts.com/as/v0/matches/{mid}/stats'
        try:
            r = requests.get(api_url, headers={'Authorization': 'Bearer ' + token}, timeout=15)
            if r.status_code == 401:
                errors.append(f'Nicht autorisiert (401) für Match {mid}.')
                continue
            if not r.ok:
                errors.append(f'Stats für Match {mid} konnten nicht geladen werden (Status {r.status_code}).')
                continue
            result = r.json()
        except Exception as e:
            errors.append(f'Fetch {mid} fehlgeschlagen: {e}')
            continue

        # Per-Leg-Ergänzung, damit einzelne Legs möglichst detailliert sind
        try:
            games_len = len(result.get('games', []) or [])
            for leg_idx in range(games_len):
                leg_url = f'https://api.autodarts.com/as/v0/matches/{mid}/stats?leg={leg_idx}'
                try:
                    lr = requests.get(leg_url, headers={'Authorization': 'Bearer ' + token}, timeout=15)
                    if lr.ok:
                        lrj = lr.json()
                        if lrj.get('games') and len(lrj.get('games')) > 0:
                            result['games'][leg_idx] = lrj['games'][0]
                except Exception:
                    pass
        except Exception:
            pass

        # Convert result to score entries via die gemeinsame Import-Logik
        # (auch verwendet von admin_import_match): erkennt und filtert BOT-Gegner.
        try:
            import_match_result_to_scores(result, games_len=games_len)

            # mark match id as imported
            imported_matches.append(mid)
            new_imported.append(mid)

        except Exception as e:
            errors.append(f'Process {mid} failed: {e}')

    # persist imported matches
    save_imported_matches(imported_matches)

    return {"ok": True, "imported_matches": new_imported, "errors": errors, "collected_match_ids": match_ids, "page_htmls": page_htmls}


@app.route('/admin/run_autodarts', methods=['POST'])
def admin_run_autodarts():
    # Run in background thread to avoid long blocking request
    max_pages = int(request.form.get('autodarts_pages') or 2)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'

    def worker(pages):
        save_autodarts_status('running', 'Autodarts-Abruf läuft…')
        try:
            res = autodarts_collect_and_import(max_pages=pages)
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        # update last run in config
        cfg = load_json(CONFIG_FILE)
        cfg['autodarts_last_run'] = datetime.now().strftime('%d.%m.%Y %H:%M')
        save_json(CONFIG_FILE, cfg)
        # optionally log results to file
        with open(os.path.join(DATA_DIR, 'autodarts_last_result.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=2, ensure_ascii=False)

        if res.get('ok'):
            errors = res.get('errors') or []
            if errors:
                save_autodarts_status('success', f"Lauf beendet mit {len(errors)} Hinweis(en). Importiert: {len(res.get('imported_matches') or [])}.", errors=errors)
            else:
                save_autodarts_status('success', f"Erfolgreich. Importiert: {len(res.get('imported_matches') or [])} Match(es).")
        else:
            save_autodarts_status('error', res.get('error') or 'Unbekannter Fehler.', errors=res.get('errors') or [])

    import threading
    t = threading.Thread(target=worker, args=(max_pages,))
    t.daemon = True
    t.start()

    if is_ajax:
        return jsonify({"ok": True, "started": True})
    return redirect(url_for('admin'))


@app.route('/admin/autodarts_status', methods=['GET'])
def admin_autodarts_status():
    """Liefert den aktuellen Status eines laufenden/letzten Autodarts-Laufs für Live-Polling."""
    status = load_autodarts_status()
    last_result = {}
    try:
        lr_path = os.path.join(DATA_DIR, 'autodarts_last_result.json')
        if os.path.exists(lr_path):
            with open(lr_path, 'r', encoding='utf-8') as f:
                last_result = json.load(f)
    except Exception:
        last_result = {}
    return jsonify({"status": status, "last_result": last_result})


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
        # cannot proceed without credentials
        return redirect(url_for('admin'))

    try:
        token, login_error = autodarts_api_login(email, password)
        if not token:
            return redirect(url_for('admin'))

        # try stats endpoint first
        headers = {'Authorization': 'Bearer ' + token}
        r = requests.get(f'https://api.autodarts.com/as/v0/matches/{mid}/stats', headers=headers, timeout=15)
        if r.ok:
            result = r.json()
        else:
            r2 = requests.get(f'https://api.autodarts.com/as/v0/matches/{mid}', headers=headers, timeout=15)
            if not r2.ok:
                return redirect(url_for('admin'))
            result = r2.json()

        # try per-leg augmentation
        games_len = len(result.get('games', []) or [])
        for leg_idx in range(games_len):
            try:
                lr = requests.get(f'https://api.autodarts.com/as/v0/matches/{mid}/stats?leg={leg_idx}', headers=headers, timeout=15)
                if lr.ok:
                    lrj = lr.json()
                    if lrj.get('games') and len(lrj.get('games')) > 0:
                        result['games'][leg_idx] = lrj['games'][0]
            except Exception:
                pass

        # convert & save using shared logic (auch für BOT-Erkennung/-Filterung)
        import_match_result_to_scores(result, games_len=games_len)

        # mark imported
        ims = load_json(IMPORTED_MATCHES_FILE)
        if mid not in ims:
            ims.append(mid)
            save_imported_matches(ims)

    except Exception:
        pass

    return redirect(url_for('admin'))


@app.route('/admin/install_screensaver', methods=['POST'])
def admin_install_screensaver():
    """Installiert den Bildschirmschoner aus /Addons/Raspberry-Screensaver im
    Home-Verzeichnis des aktuellen Nutzers (Skript + Autostart-Eintrag)."""
    script_src = os.path.join(SCREENSAVER_ADDON_DIR, 'dart_screensaver.sh')
    desktop_src = os.path.join(SCREENSAVER_ADDON_DIR, 'dart-screensaver.desktop')

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json'

    if not os.path.isfile(script_src) or not os.path.isfile(desktop_src):
        msg = 'Screensaver-Dateien wurden in /Addons/Raspberry-Screensaver nicht gefunden.'
        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 404
        return redirect(url_for('admin'))

    try:
        home_dir = os.path.expanduser('~')
        script_dst = os.path.join(home_dir, 'screensaver.sh')
        autostart_dir = os.path.join(home_dir, '.config', 'autostart')
        desktop_dst = os.path.join(autostart_dir, 'dart-screensaver.desktop')

        os.makedirs(autostart_dir, exist_ok=True)

        shutil.copyfile(script_src, script_dst)
        file_stat = os.stat(script_dst)
        os.chmod(script_dst, file_stat.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        with open(desktop_src, 'r', encoding='utf-8') as f:
            desktop_content = f.read()
        # Der mitgelieferte Exec-Pfad ist ein Platzhalter (/home/autodarts/...);
        # auf das tatsächlich installierte Skript zeigen lassen.
        desktop_content = re.sub(
            r'^Exec=.*$', f'Exec={script_dst}', desktop_content, flags=re.MULTILINE
        )
        with open(desktop_dst, 'w', encoding='utf-8') as f:
            f.write(desktop_content)

        msg = f'Screensaver installiert: {script_dst} (Autostart: {desktop_dst}).'
        if is_ajax:
            return jsonify({"ok": True, "message": msg})
        return redirect(url_for('admin'))
    except Exception as e:
        app.logger.exception('Screensaver-Installation fehlgeschlagen')
        msg = 'Installation fehlgeschlagen. Details siehe Server-Log.'
        if is_ajax:
            return jsonify({"ok": False, "error": msg}), 500
        return redirect(url_for('admin'))


@app.route('/admin/install_cec', methods=['POST'])
def admin_install_cec():
    """Installiert und aktiviert den CEC-Manager als systemd-User-Service."""
    script_src = os.path.join(CEC_ADDON_DIR, 'hdmi-audio-cec.sh')
    service_src = os.path.join(CEC_ADDON_DIR, 'hdmi-audio-cec.service')

    if not os.path.isfile(script_src) or not os.path.isfile(service_src):
        return jsonify({"ok": False, "error": "CEC-Addon-Dateien wurden nicht gefunden."}), 404

    try:
        home_dir = os.path.expanduser('~')
        bin_dir = os.path.join(home_dir, '.local', 'bin')
        service_dir = os.path.join(home_dir, '.config', 'systemd', 'user')
        script_dst = os.path.join(bin_dir, 'hdmi-audio-cec.sh')
        service_dst = os.path.join(service_dir, 'hdmi-audio-cec.service')

        os.makedirs(bin_dir, exist_ok=True)
        os.makedirs(service_dir, exist_ok=True)
        shutil.copyfile(script_src, script_dst)
        shutil.copyfile(service_src, service_dst)
        os.chmod(script_dst, os.stat(script_dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        write_cec_config(load_json(CONFIG_FILE))

        result = subprocess.run(
            ['systemctl', '--user', 'daemon-reload'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'systemctl --user ist nicht verfügbar.')
        result = subprocess.run(
            ['systemctl', '--user', 'enable', '--now', 'hdmi-audio-cec.service'],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'CEC-Service konnte nicht aktiviert werden.')

        return jsonify({
            "ok": True,
            "message": "CEC-Manager installiert und aktiviert. Die Konfiguration wird beim Speichern übernommen.",
        })
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        app.logger.exception('CEC-Installation fehlgeschlagen')
        return jsonify({
            "ok": False,
            "error": "CEC-Installation fehlgeschlagen. Details stehen im Server-Log.",
        }), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)