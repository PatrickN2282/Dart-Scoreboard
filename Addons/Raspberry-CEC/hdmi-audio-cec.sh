#!/bin/bash
# ============================================================
# HDMI Audio & CEC Manager für Raspberry Pi 5 / PiOS
# Läuft als User-Service (nicht root!)
# Ziele:
#   1. TV aus Standby wecken via CEC
#   2. CEC-Gerätename setzen ("Pi 5 - Autodarts")
#   3. Audio-Ausgang auf HDMI setzen
#   4. TV dauerhaft wach halten (CEC Keep-Alive)
# ============================================================

# --- Konfiguration ---
readonly CEC_NAME="Pi 5 - Autodarts"
readonly LOGFILE="${HOME}/.local/log/hdmi-audio-cec.log"
readonly LOCKFILE="/tmp/hdmi-audio-cec-${UID}.lock"
readonly VOLUME=0.80          # 80% Lautstärke
readonly KEEPALIVE_SEC=50     # Keep-Alive alle 50s (TV-Timeout meist 60s)
readonly MAX_BOOT_WAIT=180    # Max. Wartezeit auf TV beim Boot (Sekunden)

# --- Locking (verhindert Doppelstart) ---
exec 200>"$LOCKFILE"
flock -n 200 || { echo "Bereits aktiv, beende." >&2; exit 1; }
trap 'flock -u 200; rm -f "$LOCKFILE"' EXIT

# --- Logging ---
mkdir -p "$(dirname "$LOGFILE")"
log() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S'): $1"
    echo "$msg" >> "$LOGFILE"
    echo "$msg" >&2
}

# --- PipeWire/PulseAudio Umgebung sicherstellen ---
# WICHTIG: Als User-Service sind diese normalerweise gesetzt,
# aber beim frühen Boot evtl. noch nicht.
setup_env() {
    export XDG_RUNTIME_DIR="/run/user/${UID}"
    export PULSE_RUNTIME_PATH="${XDG_RUNTIME_DIR}/pulse"
    # Wayland nur setzen wenn vorhanden, sonst wpctl trotzdem nutzbar
    if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -S "${XDG_RUNTIME_DIR}/wayland-0" ]; then
        export WAYLAND_DISPLAY="wayland-0"
    fi
}

# ============================================================
# CEC-Funktionen
# ============================================================

# Prüfe ob cec-client verfügbar ist
cec_available() {
    command -v cec-client >/dev/null 2>&1
}

# Sende CEC-Kommandos (gibt Ausgabe zurück)
cec_send() {
    local timeout="${1:-5}"
    shift
    # Jede Zeile ist ein Kommando
    printf '%s\n' "$@" | timeout "$timeout" cec-client -s -d 1 2>/dev/null
}

# Konvertiere ASCII-String zu CEC-Hex-Payload
# CEC OSD-Name: max 14 Zeichen, Format: XX:XX:...
str_to_cec_hex() {
    local str="$1"
    local hex=""
    local i
    for (( i=0; i<${#str} && i<14; i++ )); do
        local char="${str:$i:1}"
        local code
        code=$(printf '%02x' "'$char")
        hex="${hex}${hex:+:}${code}"
    done
    echo "$hex"
}

# CEC-Gerätename setzen (OSD Name, OpCode 0x47)
cec_set_name() {
    if ! cec_available; then
        log "CEC: cec-client nicht installiert, überspringe"
        return 1
    fi

    local name_hex
    name_hex=$(str_to_cec_hex "$CEC_NAME")
    log "CEC: Setze Name '$CEC_NAME' (hex: $name_hex)"

    # 0x47 = Set OSD Name, "1" = Broadcast von Adresse 1 (Recording Device)
    # Format: <src><dst>:<opcode>:<payload>
    # "1F:47:..." = von Gerät 1, an alle (Broadcast F)
    cec_send 5 "tx 1F:47:${name_hex}" "scan" >/dev/null || true
    log "CEC: Name gesetzt"
}

# TV aus Standby wecken
cec_wake_tv() {
    if ! cec_available; then
        return 1
    fi

    log "CEC: Wecke TV (on 0)..."
    # "on 0"  = Power On an Adresse 0 (TV)
    # "as"    = Active Source (Pi meldet sich als aktive Quelle)
    cec_send 8 \
        "on 0" \
        "as" \
        >/dev/null || true
    log "CEC: Wake-Kommando gesendet"
}

# Keep-Alive: Verhindert TV-Standby durch CEC
cec_keepalive() {
    if ! cec_available; then
        return 0
    fi
    # "as" = Active Source Signal - hält TV wach
    cec_send 3 "as" >/dev/null 2>&1 || true
}

# ============================================================
# HDMI-Erkennung
# ============================================================

# Prüfe ob HDMI-Kabel physisch verbunden ist (sysfs)
hdmi_cable_connected() {
    local status_files
    status_files=$(find /sys/class/drm -name "status" 2>/dev/null | grep -i hdmi || true)

    while IFS= read -r f; do
        [ -f "$f" ] || continue
        if grep -q "^connected$" "$f" 2>/dev/null; then
            return 0
        fi
    done <<< "$status_files"
    return 1
}

# Hole HDMI-Sink-ID aus WirePlumber (wpctl)
get_hdmi_sink_id() {
    setup_env
    wpctl status 2>/dev/null \
        | awk '/Sinks:/{flag=1; next} /Sources:|Filters:|Streams:/{flag=0} flag' \
        | grep -i "hdmi" \
        | grep -oP '^\s*\*?\s*\K\d+(?=\.)' \
        | head -n1
}

# Ist der HDMI-Sink bereits als Default aktiv?
hdmi_sink_is_default() {
    local id="${1:-}"
    [ -z "$id" ] && id=$(get_hdmi_sink_id)
    [ -z "$id" ] && return 1
    setup_env
    wpctl status 2>/dev/null | grep -qP "^\s+\*\s+${id}\."
}

# ============================================================
# Audio-Setup
# ============================================================

# Setze HDMI als Standard-Audioausgang
set_audio_hdmi() {
    setup_env

    local hdmi_id
    hdmi_id=$(get_hdmi_sink_id)

    if [ -z "$hdmi_id" ]; then
        log "AUDIO: Kein HDMI-Sink in wpctl gefunden"
        return 1
    fi

    log "AUDIO: Setze Sink ID $hdmi_id als Default..."
    wpctl set-default "$hdmi_id"           2>/dev/null || true
    wpctl set-volume  "$hdmi_id" "$VOLUME" 2>/dev/null || true
    wpctl set-mute    "$hdmi_id" 0         2>/dev/null || true

    sleep 1

    if hdmi_sink_is_default "$hdmi_id"; then
        log "AUDIO: OK - Sink $hdmi_id ist aktiv (*)"
        return 0
    else
        log "AUDIO: WARNUNG - Sink $hdmi_id gesetzt, aber nicht als * markiert"
        # Trotzdem als Erfolg werten wenn ID gefunden wurde
        return 0
    fi
}

# Warte auf PipeWire/WirePlumber bereit
wait_for_pipewire() {
    log "AUDIO: Warte auf PipeWire/WirePlumber..."
    local i=0
    while [ $i -lt 60 ]; do
        setup_env
        if wpctl status >/dev/null 2>&1; then
            log "AUDIO: PipeWire bereit"
            return 0
        fi
        sleep 2
        i=$((i + 2))
    done
    log "AUDIO: FEHLER - PipeWire nicht erreichbar"
    return 1
}

# Warte auf HDMI-Sink (TV muss EDID senden = wach sein)
wait_for_hdmi_sink() {
    log "AUDIO: Warte auf HDMI-Sink (TV muss EDID bereitstellen)..."
    local i=0
    while [ $i -lt $MAX_BOOT_WAIT ]; do
        local id
        id=$(get_hdmi_sink_id)
        if [ -n "$id" ]; then
            log "AUDIO: HDMI-Sink gefunden (ID: $id) nach ${i}s"
            return 0
        fi
        sleep 3
        i=$((i + 3))
        if [ $((i % 30)) -eq 0 ]; then
            log "AUDIO: Noch kein Sink nach ${i}s, warte weiter..."
        fi
    done
    log "AUDIO: WARNUNG - Kein HDMI-Sink nach ${MAX_BOOT_WAIT}s"
    return 1
}

# Audio mit Retry und optionalem WirePlumber-Neustart
setup_audio_with_retry() {
    local attempts=0
    local max=4

    while [ $attempts -lt $max ]; do
        if set_audio_hdmi; then
            return 0
        fi

        attempts=$((attempts + 1))
        log "AUDIO: Versuch $attempts/$max fehlgeschlagen"

        if [ $attempts -lt $max ]; then
            log "AUDIO: Starte WirePlumber neu..."
            systemctl --user restart wireplumber.service 2>/dev/null || true
            sleep 6
            # Nach Neustart nochmal auf Sink warten
            wait_for_hdmi_sink || true
        fi
    done

    log "AUDIO: FEHLER - Setup nach $max Versuchen nicht erfolgreich"
    return 1
}

# ============================================================
# Hauptprogramm
# ============================================================
main() {
    log "=============================="
    log "START HDMI-Audio-CEC Manager"
    log "User: $(whoami), PID: $$"
    log "CEC-Name: $CEC_NAME"
    log "=============================="

    # 1. Auf PipeWire warten (systemd --user Services brauchen Zeit beim Boot)
    wait_for_pipewire || {
        log "KRITISCH: PipeWire nicht verfügbar, beende"
        exit 1
    }

    # 2. Auf HDMI-Kabel warten (kurz, meist sofort verbunden)
    log "Prüfe HDMI-Kabel..."
    local cable_wait=0
    while ! hdmi_cable_connected && [ $cable_wait -lt 30 ]; do
        sleep 2
        cable_wait=$((cable_wait + 2))
    done
    if hdmi_cable_connected; then
        log "HDMI-Kabel verbunden"
    else
        log "WARNUNG: HDMI-Kabel nicht erkannt (fahre trotzdem fort)"
    fi

    # 3. CEC: TV wecken + Name setzen
    cec_wake_tv
    cec_set_name

    # 4. Warte auf TV (EDID) → HDMI-Sink erscheint in wpctl
    if wait_for_hdmi_sink; then
        # 5. Audio auf HDMI setzen
        setup_audio_with_retry
    else
        log "Fallback: Versuche Audio-Setup ohne bestätigten Sink..."
        setup_audio_with_retry || true
    fi

    # 6. Nochmaliger Check nach 20s (manche TVs brauchen extra Zeit)
    log "Warte 20s für finalen Check..."
    sleep 20

    if ! hdmi_sink_is_default; then
        log "Finaler Check: HDMI nicht default, letzter Versuch..."
        set_audio_hdmi || true
    else
        log "Finaler Check: Audio OK"
    fi

    # 7. Keep-Alive-Loop
    log "Starte Keep-Alive-Loop (alle ${KEEPALIVE_SEC}s)"
    local audio_check_counter=0
    local audio_check_interval=6  # Alle 6 × KEEPALIVE_SEC = ~5 Min Audio prüfen

    while true; do
        sleep "$KEEPALIVE_SEC"

        # CEC Keep-Alive (verhindert TV-Standby)
        cec_keepalive

        # Periodisch Audio prüfen und ggf. reparieren
        audio_check_counter=$((audio_check_counter + 1))
        if [ $audio_check_counter -ge $audio_check_interval ]; then
            audio_check_counter=0
            setup_env
            if ! hdmi_sink_is_default; then
                log "Keep-Alive: HDMI nicht default, repariere..."
                set_audio_hdmi || true
            fi
        fi
    done
}

trap 'log "Signal empfangen, beende."; exit 0' SIGTERM SIGINT SIGHUP
main
