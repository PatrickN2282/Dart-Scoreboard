#!/usr/bin/env bash
# Erstellt ein übertragbares Release-Archiv ohne lokale Daten, Caches oder Zugangsdaten.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
ARCHIVE_NAME="dart-scoreboard-${VERSION}.zip"
OUTPUT_DIR_INPUT="${1:-$ROOT_DIR/dist}"
STAGE_DIR="$(mktemp -d)"
APP_DIR="$STAGE_DIR/dart-scoreboard-${VERSION}"

trap 'rm -rf "$STAGE_DIR"' EXIT
mkdir -p "$APP_DIR/data" "$APP_DIR/static/uploads" "$OUTPUT_DIR_INPUT"
OUTPUT_DIR="$(cd "$OUTPUT_DIR_INPUT" && pwd)"

cp "$ROOT_DIR/app.py" "$ROOT_DIR/README.md" "$ROOT_DIR/requirements.txt" \
   "$ROOT_DIR/install.sh" "$ROOT_DIR/VERSION" "$APP_DIR/"
cp -R "$ROOT_DIR/Addons" "$ROOT_DIR/templates" "$APP_DIR/"
cp "$ROOT_DIR/static/admin.css" "$ROOT_DIR/static/main.css" "$APP_DIR/static/"
cp "$ROOT_DIR/static/uploads/dummy.png" "$APP_DIR/static/uploads/"

printf '[]\n' > "$APP_DIR/data/players.json"
printf '[]\n' > "$APP_DIR/data/scores.json"
printf '[]\n' > "$APP_DIR/data/bot_scores.json"
printf '[]\n' > "$APP_DIR/data/imported_matches.json"
cat > "$APP_DIR/data/config.json" <<'EOF'
{
    "background_url": null,
    "static_limit": 5,
    "rotation_limit": 10,
    "static_h2_size": "2.5em",
    "rotation_h2_size": "3.5em",
    "static_td_size": "2.0em",
    "rotation_td_size": "1.5em",
    "font_family": "'Segoe UI', Roboto, sans-serif",
    "cec_enabled": false,
    "cec_device_name": "Dart Scoreboard",
    "cec_standby_time": "22:00",
    "cec_wake_time": "08:00"
}
EOF

(
    cd "$STAGE_DIR"
    zip -qr "$OUTPUT_DIR/$ARCHIVE_NAME" "$(basename "$APP_DIR")"
)
echo "Release erstellt: $OUTPUT_DIR/$ARCHIVE_NAME"
