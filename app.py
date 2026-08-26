import os
import json
import random
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
    "font_family": "'Segoe UI', Roboto, sans-serif"
}

# Datei für bereits importierte Match-IDs
IMPORTED_MATCHES_FILE = os.path.join(DATA_DIR, 'imported_matches.json')

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


def get_cumulative_stats():
    """Berechnet alle kumulativen Statistiken und gibt sie zurück"""
    scores = load_json(SCORES_FILE)
    players_list = load_json(PLAYERS_FILE)
    players_map = {p["id"]: p for p in players_list}

    cumulative = {}
    for s in scores:
        pid = s.get("player_id")
        if pid not in players_map:
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

    return cumulative, players_map, players_list


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

    # finalize: compute averages and ratios. Only derive these from points_sum/darts_thrown
    # when we actually have darts to divide by; otherwise keep whatever value the API
    # already provided above (do not clobber it with None/0).
    for k, v in stats.items():
        darts = v.get('darts_thrown', 0) or 0
        pts_sum = v.get('points_sum', 0) or 0
        if darts:
            v['average'] = float(pts_sum) / float(darts) * 3.0
        elif v.get('average') is None:
            v['average'] = None
        f9_darts = v.get('first9_darts', 0) or 0
        f9_pts = v.get('first9_points_sum', 0) or 0
        if f9_darts:
            v['first9_average'] = float(f9_pts) / float(f9_darts) * 3.0
        elif v.get('first9_average') is None:
            v['first9_average'] = None
        atts = v.get('checkout_attempts', 0) or 0
        succ = v.get('checkout_success', 0) or 0
        v['checkout_ratio'] = float(succ) / float(atts) if atts else None

    return stats


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
        f9_pts = stats.get('first9_points_sum', 0) or 0
        f9_darts = stats.get('first9_darts', 0) or 0
        first3_average = round(float(f9_pts) / float(f9_darts) * 3.0, 2) if f9_darts else None
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

    cumulative, players_map, players_list = get_cumulative_stats()

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
        f9_darts = vals.get('first9_darts', 0) or 0
        f9_pts = vals.get('first9_points_sum', 0) or 0
        if f9_darts:
            first3_avg = round(float(f9_pts) / float(f9_darts) * 3.0, 2)
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
        pid = s.get("player_id")
        if pid not in players_map:
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
        pid = s.get("player_id")
        if pid not in players_map:
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
    cumulative, players_map, players_list = get_cumulative_stats()
    h2h_data = generate_head_to_head_data(cumulative, players_map, players_list)
    return jsonify(h2h_data)


@app.route("/api/player_card")
def api_player_card():
    """Return a random player's detailed stats for rotation player card."""
    cumulative, players_map, players_list = get_cumulative_stats()
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
        't20_hits': stats.get('segment_hits',{}).get('T20',0)
    }
    # first3
    f9_darts = stats.get('first9_darts',0)
    f9_pts = stats.get('first9_points_sum',0)
    if f9_darts:
        card['first3_average'] = round(float(f9_pts)/float(f9_darts)*3.0,2)
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
                new_config = {
                    "static_limit": int(request.form.get("static_limit") or 5),
                    "rotation_limit": int(request.form.get("rotation_limit") or 10),
                    "static_h2_size": request.form.get("static_h2_size") or "2.5em",
                    "rotation_h2_size": request.form.get("rotation_h2_size") or "3.5em",
                    "static_td_size": request.form.get("static_td_size") or "2.0em",
                    "rotation_td_size": request.form.get("rotation_td_size") or "1.5em",
                    "font_family": request.form.get("font_family") or "'Segoe UI', Roboto, sans-serif",
                    # Autodarts credentials & scheduling
                    "autodarts_email": request.form.get("autodarts_email") or "",
                    "autodarts_password": request.form.get("autodarts_password") or "",
                    "autodarts_enabled": True if request.form.get("autodarts_enabled") == 'on' else False,
                    "autodarts_interval_minutes": int(request.form.get("autodarts_interval_minutes") or 60),
                    "autodarts_user_data_dir": request.form.get("autodarts_user_data_dir") or "",
                }
                current_config = load_json(CONFIG_FILE)
                current_config.update(new_config)
                save_json(CONFIG_FILE, current_config)
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

    return render_template(
        "admin.html",
        players=players,
        scores=admin_scores,
        background_exists=background_exists(),
        bg_image=bg_image,
        config=config,
        autodarts_last_result=last_result,
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


def autodarts_collect_and_import(max_pages=2):
    """Verwendet Playwright, um sich anzumelden, Match-IDs zu sammeln und Stats-API aufzurufen.
    Gibt ein dict mit importierten IDs und Fehlern zurück."""
    if sync_playwright is None:
        return {"ok": False, "error": "playwright not installed"}

    cfg = load_json(CONFIG_FILE)
    email = cfg.get('autodarts_email')
    password = cfg.get('autodarts_password')
    if not email or not password:
        return {"ok": False, "error": "Autodarts-Credentials nicht konfiguriert"}

    imported_matches = load_imported_matches()
    new_imported = []
    errors = []
    page_htmls = []

    try:
        p = sync_playwright().start()
        user_data_dir = cfg.get('autodarts_user_data_dir') or None
        if user_data_dir:
            # reuse existing browser profile (so session cookies are present)
            try:
                context = p.chromium.launch_persistent_context(user_data_dir=user_data_dir, headless=True)
                page = context.new_page()
            except Exception as e:
                errors.append(f'Failed to launch persistent context: {e}')
                p.stop()
                return {"ok": False, "error": 'persistent_context_failed', "errors": errors}
        else:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Login attempt only if no profile dir provided
            page.goto('https://play.autodarts.com')
            page.wait_for_load_state('networkidle')
            # try to click a sign-in link or fill form if available
            try:
                # try to open a sign-in modal or page
                # click link with 'Sign in' or German variants
                sign_sel = "a[href*='auth/v1/providers']"
                els = page.query_selector_all(sign_sel)
                if els:
                    # nothing to do; OAuth providers require interactive auth
                    errors.append('Site uses OAuth providers; automated email/password login may not be supported')
                    browser.close()
                    p.stop()
                    return {"ok": False, "error": 'oauth_only', "errors": errors}
            except Exception:
                pass

        # Collect match ids
        match_ids = []
        for page_no in range(max_pages):
            url = 'https://play.autodarts.com/history/matches' if page_no == 0 else f'https://play.autodarts.com/history/matches?page={page_no}'
            page.goto(url)
            page.wait_for_load_state('networkidle')
            try:
                html = page.content()
                page_htmls.append({'page': page_no, 'html_snippet': html[:5000]})
            except Exception:
                page_htmls.append({'page': page_no, 'html_snippet': ''})
            # Wait briefly for SPA to render match links
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

        # If no match ids found via page anchors, try token-based API listing as fallback
        if not match_ids:
            try:
                cfg3 = load_json(CONFIG_FILE)
                email3 = cfg3.get('autodarts_email')
                password3 = cfg3.get('autodarts_password')
                if email3 and password3:
                    auth_resp = requests.post('https://api.autodarts.com/auth/v1/login', json={'client_id': 'autodarts-play', 'email': email3, 'password': password3}, timeout=15)
                    if auth_resp.ok:
                        token = auth_resp.json().get('access_token')
                        # use the client-side filter endpoint which returns paged items
                        try:
                            page_no = 0
                            size = 50
                            while page_no < max_pages:
                                url = f'https://api.autodarts.com/as/v0/matches/filter?size={size}&page={page_no}'
                                lm = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
                                if not lm.ok:
                                    break
                                lj = lm.json()
                                items = lj.get('items') or []
                                if not items:
                                    break
                                for item in items:
                                    mid = item.get('id') or item.get('matchId') or item.get('_id')
                                    if mid and mid not in match_ids:
                                        match_ids.append(mid)
                                # stop if we've reached last page
                                if lj.get('last'):
                                    break
                                page_no += 1
                        except Exception:
                            pass
            except Exception:
                pass

        # Final fallback: attempt to fetch matches from the browser context (may use session cookies)
        if not match_ids:
            try:
                api_fetch = page.evaluate('(url) => fetch(url).then(r=>r.ok? r.json(): {status:r.status}).catch(e=>({error:e.toString()}))', 'https://api.autodarts.com/as/v0/matches?limit=50')
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

        # For each match id, fetch stats API via fetch (cookies present)
        for mid in match_ids:
            if mid in imported_matches:
                continue
            api_url = f'https://api.autodarts.com/as/v0/matches/{mid}/stats'
            # Prefer token-authenticated stats fetch when credentials are configured
            result = None
            try:
                cfg2 = load_json(CONFIG_FILE)
                email2 = cfg2.get('autodarts_email')
                password2 = cfg2.get('autodarts_password')
                token = None
                if email2 and password2:
                    auth_resp = requests.post('https://api.autodarts.com/auth/v1/login', json={'client_id': 'autodarts-play', 'email': email2, 'password': password2}, timeout=15)
                    if auth_resp.ok:
                        token = auth_resp.json().get('access_token')
                if token:
                    try:
                        r = requests.get(api_url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
                        if r.ok:
                            result = r.json()
                        else:
                            # treat explicit 401 as unauthorized so we can record it
                            if r.status_code == 401:
                                errors.append(f'Unauthorized for match {mid}')
                                continue
                    except Exception as e:
                        errors.append(f'Fetch {mid} failed (token): {e}')
                # fallback to page fetch if no token or token fetch failed
                if not result:
                    try:
                        result = page.evaluate('(url) => fetch(url).then(r=>r.ok? r.json(): {status:r.status}).catch(e=>({error:e.toString()}))', api_url)
                    except Exception as e:
                        errors.append(f'Fetch {mid} failed: {e}')
                        continue

            except Exception:
                # any unexpected error: try page fetch
                try:
                    result = page.evaluate('(url) => fetch(url).then(r=>r.ok? r.json(): {status:r.status}).catch(e=>({error:e.toString()}))', api_url)
                except Exception as e:
                    errors.append(f'Fetch {mid} failed: {e}')
                    continue

            # If result indicates unauthorized (from page fetch), record and continue
            if isinstance(result, dict) and result.get('status') == 401:
                errors.append(f'Unauthorized for match {mid}')
                continue

            # If we have a token, try per-leg augmentation to ensure games are detailed
            try:
                if token:
                    games_len = len(result.get('games', []) or [])
                    for leg_idx in range(games_len):
                        leg_url = f'https://api.autodarts.com/as/v0/matches/{mid}/stats?leg={leg_idx}'
                        try:
                            lr = requests.get(leg_url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
                            if lr.ok:
                                lrj = lr.json()
                                if lrj.get('games') and len(lrj.get('games')) > 0:
                                    result['games'][leg_idx] = lrj['games'][0]
                        except Exception:
                            pass
            except Exception:
                pass

            # Convert result to payload entries expected by admin_import
            # This mapping depends on API shape; attempt reasonable mapping
            try:
                payload = []
                stats = extract_stats_from_result(result)
                # compute total legs
                total_legs = sum([s.get('legs', 0) for s in result.get('scores', [])])
                # build payload entries
                players = result.get('players', [])
                for p in players:
                    key = p.get('id') or p.get('userId') or p.get('name')
                    st = stats.get(key, {})
                    entry = {
                        'autodarts_name': p.get('name') or p.get('username') or '',
                        'player_name': None,
                        'legs': int(st.get('legs', 0) or 0),
                        'finish': int(st.get('bestCheckout', 0) or 0),
                        'max180': int(st.get('max180', 0) or 0),
                        'darts301': 0,
                        's60': int(st.get('s60', 0) or 0),
                        's100': int(st.get('s100', 0) or 0),
                        's140': int(st.get('s140', 0) or 0),
                        's170': int(st.get('s170', 0) or 0),
                        's180': int(st.get('s180', 0) or 0),
                        'min_darts_to_checkout': st.get('min_darts_to_checkout'),
                        'checkout_ratio': st.get('checkout_ratio'),
                        'segment_hits': st.get('segment_hits', {}),
                        'average': st.get('average'),
                        'first9_average': st.get('first9_average'),
                        'darts_thrown': st.get('darts_thrown'),
                        'points_sum': st.get('points_sum'),
                        # record that this import represents one match (not each leg as a separate game)
                        'total_games_in_import': 1,
                    }
                    payload.append(entry)

                # Call admin_import logic directly by POSTing to endpoint using Flask test_client-like approach
                # Simpler: call internal function admin_import() expects request context; we'll call via requests to localhost
                # To avoid HTTP, we reuse admin_import logic by simulating request: call save to scores directly
                scores = load_json(SCORES_FILE)
                players_local = load_json(PLAYERS_FILE)
                imported_count = 0
                for entry in payload:
                    # map autodarts_name to player or create new
                    pid = None
                    ad = (entry.get('autodarts_name') or '').strip()
                    if ad:
                        for pl in players_local:
                            if pl.get('autodarts_name','').strip().lower() == ad.lower():
                                pid = pl['id']
                                break
                    if not pid:
                        name = entry.get('player_name') or entry.get('autodarts_name')
                        pid = get_player_id(name, players_local)

                    new_score = {
                        'player_id': pid,
                        'legs': entry.get('legs',0),
                        'finish': entry.get('finish',0),
                        'max180': entry.get('max180',0),
                        'darts301': entry.get('darts301',0),
                        's60': entry.get('s60',0),
                        's100': entry.get('s100',0),
                        's140': entry.get('s140',0),
                        's170': entry.get('s170',0),
                        's180': entry.get('s180',0),
                        'games_played': int(entry.get('total_games_in_import') or entry.get('games_played') or 1),
                        'date': datetime.now().strftime('%d.%m.%Y %H:%M'),
                        # extended fields
                        'segment_hits': entry.get('segment_hits', {}),
                        'average': entry.get('average'),
                        'first9_average': entry.get('first9_average'),
                        'darts_thrown': entry.get('darts_thrown'),
                        'points_sum': entry.get('points_sum'),
                        'min_darts_to_checkout': entry.get('min_darts_to_checkout'),
                        'checkout_ratio': entry.get('checkout_ratio'),
                    }
                    # compute hash and check duplicates
                    try:
                        new_hash = compute_score_hash(new_score)
                        new_score['score_hash'] = new_hash
                    except Exception:
                        new_hash = None

                    dup = False
                    for ex in scores:
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
                        scores.append(new_score)
                        imported_count += 1

                save_json(SCORES_FILE, scores)
                save_json(PLAYERS_FILE, players_local)

                # mark match id as imported
                imported_matches.append(mid)
                new_imported.append(mid)

            except Exception as e:
                errors.append(f'Process {mid} failed: {e}')

        # persist imported matches
        save_imported_matches(imported_matches)
        # close browser/context
        try:
            if user_data_dir:
                try:
                    context.close()
                except Exception:
                    pass
            else:
                try:
                    browser.close()
                except Exception:
                    pass
        finally:
            try:
                p.stop()
            except Exception:
                pass

        return {"ok": True, "imported_matches": new_imported, "errors": errors, "collected_match_ids": match_ids, "page_htmls": page_htmls}

    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route('/admin/run_autodarts', methods=['POST'])
def admin_run_autodarts():
    # Run in background thread to avoid long blocking request
    max_pages = int(request.form.get('autodarts_pages') or 2)

    def worker(pages):
        res = autodarts_collect_and_import(max_pages=pages)
        # update last run in config
        cfg = load_json(CONFIG_FILE)
        cfg['autodarts_last_run'] = datetime.now().strftime('%d.%m.%Y %H:%M')
        save_json(CONFIG_FILE, cfg)
        # optionally log results to file
        with open(os.path.join(DATA_DIR, 'autodarts_last_result.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=2, ensure_ascii=False)

    import threading
    t = threading.Thread(target=worker, args=(max_pages,))
    t.daemon = True
    t.start()
    return redirect(url_for('admin'))


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
        auth_resp = requests.post('https://api.autodarts.com/auth/v1/login', json={'client_id': 'autodarts-play', 'email': email, 'password': password}, timeout=15)
        if not auth_resp.ok:
            return redirect(url_for('admin'))
        token = auth_resp.json().get('access_token')

        # try stats endpoint first
        headers = {'Authorization': f'Bearer {token}'}
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

        # convert & save using shared logic
        payload = []
        stats = extract_stats_from_result(result)
        total_legs = sum([s.get('legs', 0) for s in result.get('scores', [])])
        players = result.get('players', [])
        for p in players:
            # must match the key precedence used in extract_stats_from_result (id first),
            # otherwise stats lookup silently returns an empty dict for every player.
            key = p.get('id') or p.get('userId') or p.get('name')
            st = stats.get(key, {})
            entry = {
                'autodarts_name': p.get('name') or p.get('username') or '',
                'player_name': None,
                'legs': int(st.get('legs', 0) or 0),
                'finish': int(st.get('bestCheckout', 0) or 0),
                'max180': int(st.get('max180', 0) or 0),
                'darts301': 0,
                's60': int(st.get('s60', 0) or 0),
                's100': int(st.get('s100', 0) or 0),
                's140': int(st.get('s140', 0) or 0),
                's170': int(st.get('s170', 0) or 0),
                's180': int(st.get('s180', 0) or 0),
                'min_darts_to_checkout': st.get('min_darts_to_checkout'),
                'checkout_ratio': st.get('checkout_ratio'),
                'segment_hits': st.get('segment_hits', {}),
                'average': st.get('average'),
                'first9_average': st.get('first9_average'),
                'darts_thrown': st.get('darts_thrown'),
                'points_sum': st.get('points_sum'),
                # record that this import represents one match (not each leg as a separate game)
                'total_games_in_import': 1,
            }
            payload.append(entry)

        # save to scores exactly like admin_import
        scores = load_json(SCORES_FILE)
        players_local = load_json(PLAYERS_FILE)
        imported_count = 0
        for entry in payload:
            pid = None
            ad = (entry.get('autodarts_name') or '').strip()
            if ad:
                for pl in players_local:
                    if pl.get('autodarts_name','').strip().lower() == ad.lower():
                        pid = pl['id']
                        break
            if not pid:
                name = entry.get('player_name') or entry.get('autodarts_name')
                pid = get_player_id(name, players_local)

            new_score = {
                'player_id': pid,
                'legs': entry.get('legs',0),
                'finish': entry.get('finish',0),
                'max180': entry.get('max180',0),
                'darts301': entry.get('darts301',0),
                's60': entry.get('s60',0),
                's100': entry.get('s100',0),
                's140': entry.get('s140',0),
                's170': entry.get('s170',0),
                's180': entry.get('s180',0),
                'games_played': int(entry.get('total_games_in_import') or entry.get('games_played') or 1),
                'date': datetime.now().strftime('%d.%m.%Y %H:%M'),
                'segment_hits': entry.get('segment_hits', {}),
                'average': entry.get('average'),
                'first9_average': entry.get('first9_average'),
                'darts_thrown': entry.get('darts_thrown'),
                'points_sum': entry.get('points_sum'),
                'min_darts_to_checkout': entry.get('min_darts_to_checkout'),
                'checkout_ratio': entry.get('checkout_ratio'),
            }
            try:
                new_hash = compute_score_hash(new_score)
                new_score['score_hash'] = new_hash
            except Exception:
                new_hash = None

            dup = False
            for ex in scores:
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
                scores.append(new_score)
                imported_count += 1

        save_json(SCORES_FILE, scores)
        save_json(PLAYERS_FILE, players_local)
        # mark imported
        ims = load_json(IMPORTED_MATCHES_FILE)
        if mid not in ims:
            ims.append(mid)
            save_imported_matches(ims)

    except Exception:
        pass

    return redirect(url_for('admin'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)