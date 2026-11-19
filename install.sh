#!/bin/sh
# Adds a menu entry that runs Fireside straight from this folder. For the proper install, use the deb package.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" "$DATA/icons/hicolor/scalable/actions"
cp "$ROOT/data/io.github.webhookstudios.Fireside.svg" "$DATA/icons/hicolor/scalable/apps/"
cp "$ROOT"/data/icons/hicolor/scalable/actions/*.svg "$DATA/icons/hicolor/scalable/actions/"
sed -e "s|^Exec=.*|Exec=env PYTHONPATH=$ROOT python3 -m fireside %U|" \
    "$ROOT/data/io.github.webhookstudios.Fireside.desktop" > "$DATA/applications/io.github.webhookstudios.Fireside.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true
# A user icon folder can carry an old icon list that hides new icons, so rebuild it.
[ -f "$DATA/icons/hicolor/icon-theme.cache" ] && gtk-update-icon-cache -f -q --ignore-theme-index "$DATA/icons/hicolor" 2>/dev/null || true
echo "Fireside is in your apps menu. Or run: PYTHONPATH=$ROOT python3 -m fireside"
