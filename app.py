import os
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
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

# Sicherstellen, dass die Ordner existieren
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
    """Versucht, die lokale IP-Adresse des ausführenden Geräts zu ermitteln."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_qr_code(url: str) -> str:
    """
    Erzeugt einen QR-Code für die übergebene URL und gibt ihn
    als Base64-kodiertes PNG-Datenstring zurück (verwendbar direkt als
    src-Attribut eines <img>-Tags).
    """
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
    """Lädt Daten aus einer JSON-Datei."""
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
    """Speichert Daten in einer JSON-Datei."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_player_id(player_name, players):
    """Sucht Spieler-ID oder erstellt neuen Spieler."""
    normalized_name = player_name.strip()
    if not normalized_name:
        return None

    for p in players:
        if p["name"] == normalized_name:
            return p["id"]

    # Sichere ID-Vergabe: Immer größte vorhandene ID + 1
    new_id = max((p["id"] for p in players), default=0) + 1
    new_player = {"id": new_id, "name": normalized_name, "image": "dummy.png"}
    players.append(new_player)
    save_json(PLAYERS_FILE, players)
    return new_id


def get_player_by_id(player_id):
    """Gibt Spielerobjekt anhand der ID zurück."""
    players = load_json(PLAYERS_FILE)
    for p in players:
        if p["id"] == player_id:
            return p
    return None


def background_exists():
    """Prüft, ob ein Hintergrundbild existiert."""
    return os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME))


def add_podium_rank(entries: list, sort_key: str) -> list:
    """
    Fügt jedem Eintrag in einer sortierten Liste ein 'rank'-Feld (1-basiert)
    und ein 'podium_class'-Feld ('gold', 'silver', 'bronze', '') hinzu.
    Gleiche Werte beim sort_key (Tie) erhalten denselben Rang.
    sort_key wird explizit übergeben – kein Raten anhand vorhandener Felder.
    """
    PODIUM = {1: "gold", 2: "silver", 3: "bronze"}
    for i, entry in enumerate(entries):
        if i == 0:
            entry["rank"] = 1
        else:
            prev_val = entries[i - 1].get(sort_key)
            curr_val = entry.get(sort_key)
            if curr_val == prev_val:
                # Gleichstand: gleichen Rang wie Vorgänger übernehmen
                entry["rank"] = entries[i - 1]["rank"]
            else:
                # Kein Gleichstand: tatsächliche Position (1-basiert)
                entry["rank"] = i + 1
        entry["podium_class"] = PODIUM.get(entry["rank"], "")
    return entries


# --- Routen ---

@app.route("/")
def index():
    scores   = load_json(SCORES_FILE)
    config   = load_json(CONFIG_FILE)
    local_ip = get_local_ip()
    qr_url   = f"http://{local_ip}:5000"
    qr_code  = generate_qr_code(qr_url)

    # Spieler einmalig als Dict laden – kein wiederholtes Disk-Lesen
    players_list = load_json(PLAYERS_FILE)
    players_map  = {p["id"]: p for p in players_list}

    def player_name(pid):
        return players_map.get(pid, {}).get("name", "Unbekannt")

    def player_image(pid):
        return players_map.get(pid, {}).get("image", "dummy.png")

    # ── Legs & 180er: kumuliert pro Spieler ──────────────────────────
    cumulative = {}
    for s in scores:
        pid = s.get("player_id")
        if pid not in players_map:
            continue  # verwaiste Scores (Spieler gelöscht) überspringen
        if pid not in cumulative:
            cumulative[pid] = {"legs": 0, "max180": 0}
        cumulative[pid]["legs"]   += s.get("legs",   0)
        cumulative[pid]["max180"] += s.get("max180", 0)

    most_legs = []
    most_180s = []
    for pid, vals in cumulative.items():
        if vals["legs"] > 0:
            most_legs.append({
                "name":  player_name(pid),
                "image": player_image(pid),
                "legs":  vals["legs"],
            })
        if vals["max180"] > 0:
            most_180s.append({
                "name":   player_name(pid),
                "image":  player_image(pid),
                "max180": vals["max180"],
            })

    most_legs = add_podium_rank(
        sorted(most_legs, key=lambda x: x["legs"],   reverse=True), "legs")
    most_180s = add_podium_rank(
        sorted(most_180s, key=lambda x: x["max180"], reverse=True), "max180")

    # ── Höchstes Finish: pro Spieler nur der beste Einzelwert ────────
    finish_best: dict = {}
    for s in scores:
        pid = s.get("player_id")
        if pid not in players_map:
            continue
        val = s.get("finish", 0)
        if val > 0 and val > finish_best.get(pid, {}).get("finish", 0):
            finish_best[pid] = {
                "name":   player_name(pid),
                "image":  player_image(pid),
                "finish": val,
            }
    highest_finish = add_podium_rank(
        sorted(finish_best.values(),
               key=lambda x: x["finish"], reverse=True),
        "finish"
    )

    # ── Wenigste Darts 301: pro Spieler nur der beste Einzelwert ─────
    darts301_best: dict = {}
    for s in scores:
        pid = s.get("player_id")
        if pid not in players_map:
            continue
        val = s.get("darts301", 0)
        if val > 0 and val < darts301_best.get(pid, {}).get("darts301", 9999):
            darts301_best[pid] = {
                "name":    player_name(pid),
                "image":   player_image(pid),
                "darts301": val,
            }
    lowest_darts301 = add_podium_rank(
        sorted(darts301_best.values(),
               key=lambda x: x["darts301"], reverse=False),
        "darts301"
    )

    bg_image = f"uploads/{BACKGROUND_FILENAME}" if background_exists() else None

    return render_template(
        "index.html",
        most_legs=most_legs,
        highest_finish=highest_finish,
        most_180s=most_180s,
        lowest_darts301=lowest_darts301,
        bg_image=bg_image,
        config=config,
        local_ip=local_ip,
        qr_code=qr_code,
        qr_url=qr_url,
    )


@app.route("/admin", methods=["GET", "POST"])
def admin():
    players = load_json(PLAYERS_FILE)
    scores  = load_json(SCORES_FILE)
    config  = load_json(CONFIG_FILE)

    if request.method == "POST":
        action = request.form.get("action")

        # --- 1. SCORE HINZUFÜGEN ---
        if action == "add_score":
            player_id      = request.form.get("player_id")
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
                    legs_add    = int(request.form.get("legs")    or 0)
                    finish      = int(request.form.get("finish")   or 0)
                    max180_add  = int(request.form.get("max180")   or 0)
                    darts301_add = int(request.form.get("darts301") or 0)
                except ValueError:
                    legs_add, finish, max180_add, darts301_add = 0, 0, 0, 0

                if legs_add > 0 or finish > 0 or max180_add > 0 or darts301_add > 0:
                    new_score = {
                        "player_id": player_id,
                        "legs":      legs_add,
                        "finish":    finish,
                        "max180":    max180_add,
                        "darts301":  darts301_add,
                        "date":      datetime.now().strftime("%d.%m.%Y %H:%M")
                    }
                    scores.append(new_score)
                    save_json(SCORES_FILE, scores)

        # --- 2. SPIELERBILD HOCHLADEN ---
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

        # --- 3. HINTERGRUND HOCHLADEN ---
        elif action == "upload_background":
            file = request.files.get("background_image")
            if file and file.filename != '':
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME)
                if os.path.exists(old_path):
                    os.remove(old_path)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], BACKGROUND_FILENAME))

        # --- 4. SPIELER LÖSCHEN ---
        elif action == "delete_player":
            try:
                player_id_to_delete = int(request.form.get("player_id_to_delete"))
                players = [p for p in players if p["id"] != player_id_to_delete]
                save_json(PLAYERS_FILE, players)
                scores  = [s for s in scores  if s["player_id"] != player_id_to_delete]
                save_json(SCORES_FILE, scores)
            except ValueError:
                pass

        # --- 5. STATISTIK-EINTRAG LÖSCHEN ---
        elif action == "delete_score":
            try:
                score_index = int(request.form.get("score_index_to_delete"))
                if 0 <= score_index < len(scores):
                    scores.pop(score_index)
                    save_json(SCORES_FILE, scores)
            except ValueError:
                pass

        # --- 6. LETZTEN EINTRAG RÜCKGÄNGIG ---
        elif action == "undo_last_score":
            if scores:
                scores.pop()
                save_json(SCORES_FILE, scores)

        # --- 7. SPIELER UMBENENNEN ---
        elif action == "rename_player":
            try:
                rename_id   = int(request.form.get("rename_player_id"))
                rename_name = request.form.get("rename_player_name", "").strip()
                if rename_name:
                    for p in players:
                        if p["id"] == rename_id:
                            p["name"] = rename_name
                            break
                    save_json(PLAYERS_FILE, players)
            except ValueError:
                pass

        # --- 8. KONFIGURATION SPEICHERN ---
        elif action == "save_config":
            try:
                new_config = {
                    "static_limit":    int(request.form.get("static_limit")    or 5),
                    "rotation_limit":  int(request.form.get("rotation_limit")  or 10),
                    "static_h2_size":  request.form.get("static_h2_size")      or "2.5em",
                    "rotation_h2_size": request.form.get("rotation_h2_size")   or "3.5em",
                    "static_td_size":  request.form.get("static_td_size")      or "2.0em",
                    "rotation_td_size": request.form.get("rotation_td_size")   or "1.5em",
                    "font_family":     request.form.get("font_family")         or "'Segoe UI', Roboto, sans-serif",
                }
                current_config = load_json(CONFIG_FILE)
                current_config.update(new_config)
                save_json(CONFIG_FILE, current_config)
            except ValueError:
                pass

        return redirect(url_for("admin"))

    # Scores für Admin-Tabelle vorbereiten
    admin_scores = []
    for idx, s in enumerate(scores):
        p = get_player_by_id(s.get("player_id"))
        admin_scores.append({
            "index":   idx,
            "name":    p["name"] if p else "Unbekannt",
            "date":    s.get("date", "N/A"),
            "legs":    s.get("legs",    0),
            "finish":  s.get("finish",  0),
            "max180":  s.get("max180",  0),
            "darts301": s.get("darts301", 0),
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
