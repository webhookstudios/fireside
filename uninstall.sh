#!/bin/sh
# Removes the menu entry added by install.sh. Your settings and playlists stay.
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$DATA/applications/io.github.webhookstudios.Fireside.desktop" \
      "$DATA/icons/hicolor/scalable/apps/io.github.webhookstudios.Fireside.svg" \
      "$DATA/icons/hicolor/scalable/actions/fireside-speed-symbolic.svg" \
      "$DATA/icons/hicolor/scalable/actions/fireside-equalizer-symbolic.svg"
update-desktop-database "$DATA/applications" 2>/dev/null || true
# A user icon folder can carry an old icon list that hides new icons, so rebuild it.
[ -f "$DATA/icons/hicolor/icon-theme.cache" ] && gtk-update-icon-cache -f -q --ignore-theme-index "$DATA/icons/hicolor" 2>/dev/null || true
echo "Fireside's menu entry is removed."
