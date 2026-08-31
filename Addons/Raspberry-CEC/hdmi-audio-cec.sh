#!/bin/bash
# ============================================================
# HDMI Audio & CEC Manager für Raspberry Pi 5 / PiOS
# Läuft als User-Service (nicht root!)
# Ziele:
#   1. TV im konfigurierten Zeitfenster via CEC wecken und wach halten
#   2. TV außerhalb des Zeitfensters gezielt in Standby schicken
#   3. CEC-Gerätename setzen und HDMI-Audio wiederherstellen
# ============================================================

readonly LOCKFILE="${XDG_RUNTIME_DIR:-/run/user/${UID}}/hdmi-audio-cec.lock"
readonly CEC_CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/dart-scoreboard/cec.conf"
readonly VOLUME=0.80          # 80% Lautstärke
readonly MAX_BOOT_WAIT=180    # Max. Wartezeit auf TV beim Boot (Sekunden)

# --- Locking (verhindert Doppelstart) ---
exec 200>"$LOCKFILE"
flock -n 200 || { echo "Bereits aktiv, beende." >&2; exit 1; }
trap 'flock -u 200; rm -f "$LOCKFILE"' EXIT

# --- Logging ---
log() {
    printf '%s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >&2
}

load_runtime_config() {
    CEC_ENABLED=1
    CEC_NAME="Dart Scoreboard"
    CEC_STANDBY_TIME="22:00"
    CEC_WAKE_TIME="08:00"
    CEC_ADAPTER=""
    CEC_CHECK_INTERVAL=50

    if [ -r "$CEC_CONFIG_FILE" ]; then
        # Die Datei wird ausschließlich vom lokalen Adminbereich mit sicheren Werten geschrieben.
        # shellcheck disable=SC1090
        . "$CEC_CONFIG_FILE"
    fi
    [[ "$CEC_ENABLED" =~ ^[01]$ ]] || CEC_ENABLED=0
    [[ "$CEC_STANDBY_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || CEC_STANDBY_TIME="22:00"
    [[ "$CEC_WAKE_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || CEC_WAKE_TIME="08:00"
    [[ -z "$CEC_ADAPTER" || "$CEC_ADAPTER" =~ ^/dev/cec[0-9]+$ ]] || CEC_ADAPTER=""
    CEC_NAME="${CEC_NAME:0:14}"
    if ! [[ "$CEC_CHECK_INTERVAL" =~ ^[0-9]+$ ]] || [ "$CEC_CHECK_INTERVAL" -lt 10 ] || [ "$CEC_CHECK_INTERVAL" -gt 3600 ]; then
        CEC_CHECK_INTERVAL=50
    fi
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
    local args=(-s -d 1 -o "$CEC_NAME")
    [ -z "$CEC_ADAPTER" ] || args+=("$CEC_ADAPTER")
    printf '%s\n' "$@" | timeout "$timeout" cec-client "${args[@]}" 2>/dev/null
}

# Der OSD-Name wird von libCEC beim Öffnen des Adapters angekündigt. Dadurch
# wird keine fest codierte (und möglicherweise falsche) logische Adresse benutzt.
cec_set_name() {
    if ! cec_available; then
        log "CEC: cec-client nicht installiert, überspringe"
        return 1
    fi

    log "CEC: Melde OSD-Name '$CEC_NAME' an"
    if cec_send 8 "scan" >/dev/null; then
        log "CEC: Adapter erreichbar, Name angekündigt"
        return 0
    fi
    log "CEC: Adapter nicht erreichbar; Name konnte nicht angekündigt werden"
    return 1
}

cec_tv_is_on() {
    cec_available || return 1
    cec_send 5 "pow 0" | grep -qiE 'power status: (on|in transition standby to on)'
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

cec_standby_tv() {
    if ! cec_available; then
        return 1
    fi

    log "CEC: Schicke TV in Standby..."
    cec_send 8 "standby 0" >/dev/null || true
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
# Zeitplan
# ============================================================

time_to_minutes() {
    local value="$1"
    [[ "$value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || return 1
    local hours="${value%%:*}"
    local minutes="${value##*:}"
    echo $((10#$hours * 60 + 10#$minutes))
}

schedule_is_active() {
    local standby wake now
    standby=$(time_to_minutes "$CEC_STANDBY_TIME") || return 1
    wake=$(time_to_minutes "$CEC_WAKE_TIME") || return 1
    now=$((10#$(date +%H) * 60 + 10#$(date +%M)))

    # Gleiche Zeiten bedeuten absichtlich einen durchgehend aktiven TV.
    if [ "$wake" -eq "$standby" ]; then
        return 0
    elif [ "$wake" -lt "$standby" ]; then
        [ "$now" -ge "$wake" ] && [ "$now" -lt "$standby" ]
    else
        [ "$now" -ge "$wake" ] || [ "$now" -lt "$standby" ]
    fi
}

desired_mode() {
    if [ "$CEC_ENABLED" != "1" ]; then
        echo "disabled"
    elif schedule_is_active; then
        echo "active"
    else
        echo "standby"
    fi
}

activate_tv() {
    log "CEC: Aktives Zeitfenster (${CEC_WAKE_TIME}–${CEC_STANDBY_TIME}), aktiviere TV"
    cec_wake_tv || true
    wait_for_pipewire || return 1
    wait_for_hdmi_sink || true
    setup_audio_with_retry || true
}

# ============================================================
# Hauptprogramm
# ============================================================
main() {
    load_runtime_config
    log "=============================="
    log "START HDMI-Audio-CEC Manager"
    log "User: $(whoami), PID: $$"
    log "Konfiguration: Name '$CEC_NAME', Standby ${CEC_STANDBY_TIME}, Wecken ${CEC_WAKE_TIME}"
    log "=============================="

    log "Starte Zeitplan- und Keep-Alive-Loop (Intervall aus Laufzeitkonfiguration)"
    local last_mode=""
    local last_name=""
    local audio_check_counter=0
    local audio_check_interval=6  # Alle 6 × KEEPALIVE_SEC = ~5 Min Audio prüfen

    while true; do
        load_runtime_config
        local mode
        mode=$(desired_mode)

        if [ "$mode" != "$last_mode" ]; then
            case "$mode" in
                active)
                    activate_tv || log "WARNUNG: Aktivierung unvollständig; versuche es im nächsten Zyklus erneut."
                    ;;
                standby)
                    log "CEC: Standby-Zeitfenster (${CEC_STANDBY_TIME}–${CEC_WAKE_TIME})"
                    cec_standby_tv || true
                    ;;
                disabled)
                    log "CEC: Zeitplan deaktiviert; sende keine Keep-Alive-Signale."
                    ;;
            esac
            last_mode="$mode"
            audio_check_counter=0
        elif [ "$mode" = "active" ]; then
            if cec_tv_is_on; then
                cec_keepalive
            else
                log "CEC: TV meldet nicht 'on' oder antwortet nicht; sende Wake-Kommando."
                cec_wake_tv || true
            fi
            audio_check_counter=$((audio_check_counter + 1))
            if [ "$audio_check_counter" -ge "$audio_check_interval" ]; then
                audio_check_counter=0
                setup_env
                if ! hdmi_sink_is_default; then
                    log "Keep-Alive: HDMI nicht default, repariere..."
                    set_audio_hdmi || true
                fi
            fi
        fi

        if [ "$mode" = "active" ] && [ "$CEC_NAME" != "$last_name" ]; then
            cec_set_name || true
            last_name="$CEC_NAME"
        fi

        sleep "$CEC_CHECK_INTERVAL"
    done
}

trap 'log "Signal empfangen, beende."; exit 0' SIGTERM SIGINT SIGHUP
main
