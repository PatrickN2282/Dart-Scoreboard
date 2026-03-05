import os
import json
import random
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify
import socket
import qrcode
import io
import base64

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
                "best_finish": 0
            }
        cumulative[pid]["legs"]   += s.get("legs",   0)
        cumulative[pid]["max180"] += s.get("max180", 0)
        cumulative[pid]["s60"]    += s.get("s60",    0)
        cumulative[pid]["s100"]   += s.get("s100",   0)
        cumulative[pid]["s140"]   += s.get("s140",   0)
        cumulative[pid]["s170"]   += s.get("s170",   0)
        cumulative[pid]["s180"]   += s.get("s180",   0)
        cumulative[pid]["games_played"] += s.get("games_played", 0)
        
        finish_val = s.get("finish", 0)
        if finish_val > cumulative[pid].get("best_finish", 0):
            cumulative[pid]["best_finish"] = finish_val
            
        if s.get("max180", 0) > 0 or s.get("s180", 0) > 0:
            cumulative[pid]["last180_date"] = s.get("date", "")

    return cumulative, players_map, players_list


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
        
        h2h_data.append({
            "id": pid,
            "name": p["name"],
            "image": p.get("image", "dummy.png"),
            "wins": wins,
            "total_games": total_games,
            "win_rate": win_rate,
            "finish": stats.get("best_finish", 0),
            "max180": stats.get("max180", 0) + stats.get("s180", 0),
            "s100_plus": stats.get("s100", 0) + stats.get("s140", 0) + stats.get("s170", 0) + stats.get("s180", 0)
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
            "games_played": s.get("games_played", 0),
        })

    bg_image = f"uploads/{BACKGROUND_FILENAME}" if background_exists() else None

    return render_template(
        "admin.html",
        players=players,
        scores=admin_scores,
        background_exists=background_exists(),
        bg_image=bg_image,
        config=config,
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
        now = datetime.now().strftime("%d.%m.%Y %H:%M")

        autodarts_map = {
            p.get("autodarts_name", "").strip().lower(): p["id"]
            for p in players
            if p.get("autodarts_name", "").strip()
        }

        total_legs_in_import = sum(entry.get("legs", 0) for entry in payload)
        
        for entry in payload:
            pid = entry.get("player_id")
            if pid:
                pid = int(pid)
            else:
                ad_name = (entry.get("autodarts_name") or entry.get("player_name") or "").strip()
                pid = autodarts_map.get(ad_name.lower())
                if not pid:
                    name = (entry.get("player_name") or ad_name or "").strip()
                    if not name:
                        continue
                    pid = get_player_id(name, players)

            legs = int(entry.get("legs", 0) or 0)
            finish = int(entry.get("finish", 0) or 0)
            m180 = int(entry.get("max180", 0) or 0)
            d301 = int(entry.get("darts301", 0) or 0)
            s60 = int(entry.get("s60", 0) or 0)
            s100 = int(entry.get("s100", 0) or 0)
            s140 = int(entry.get("s140", 0) or 0)
            s170 = int(entry.get("s170", 0) or 0)
            s180 = int(entry.get("s180", 0) or 0)

            if not any([legs, finish, m180, d301, s60, s100, s140, s170, s180]):
                continue

            scores.append({
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
                "games_played": total_legs_in_import,
                "date": now,
            })
            imported += 1

        save_json(SCORES_FILE, scores)
        save_json(PLAYERS_FILE, players)
        return {"ok": True, "imported": imported}

    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)