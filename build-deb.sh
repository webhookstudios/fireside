#!/bin/sh
# Builds dist/fireside_<version>_all.deb. Needs only dpkg-deb.
#
#   /usr/lib/fireside/          the program (Python)
#   /usr/bin/fireside           the launcher
#   /usr/share/icons/hicolor/   the app icon and two symbolic icons used in the control bar
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_ID=io.github.webhookstudios.Fireside
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/fireside/__init__.py")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

LIB="$STAGE/usr/lib/fireside"
ICONS="$STAGE/usr/share/icons/hicolor/scalable"
install -d "$LIB/fireside/ui" "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
    "$ICONS/apps" "$ICONS/actions" "$STAGE/usr/share/doc/fireside"

install -m 644 "$ROOT"/fireside/*.py "$LIB/fireside/"
install -m 644 "$ROOT"/fireside/ui/*.py "$LIB/fireside/ui/"
cp -r "$ROOT/fireside/sedge" "$LIB/fireside/"
find "$LIB/fireside/sedge" -name __pycache__ -prune -exec rm -rf {} +
find "$LIB/fireside/sedge" -type f -exec chmod 644 {} +
install -m 755 "$ROOT/data/fireside" "$STAGE/usr/bin/fireside"
install -m 644 "$ROOT/data/$APP_ID.desktop" "$STAGE/usr/share/applications/"
install -m 644 "$ROOT/data/$APP_ID.svg" "$ICONS/apps/"
install -m 644 "$ROOT"/data/icons/hicolor/scalable/actions/*.svg "$ICONS/actions/"

cat > "$STAGE/usr/share/doc/fireside/copyright" <<EOT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Fireside
Source: https://github.com/webhookstudios/fireside

Files: *
Copyright: $(date +%Y) webhookstudios
License: GPL-2+
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in /usr/share/common-licenses/GPL-2.

Files: */sedge/fonts/*
Copyright: 2023 The Young Serif Project Authors; 2015 The Public Sans Project Authors
License: OFL-1.1
 The full license texts are in the sedge/fonts/ folder.
EOT
chmod 644 "$STAGE/usr/share/doc/fireside/copyright"

cat > "$STAGE/DEBIAN/control" <<EOT
Package: fireside
Version: $VERSION
Architecture: all
Maintainer: webhookstudios <354619278+webhookstudios@users.noreply.github.com>
Installed-Size: $(du -sk "$STAGE/usr" | cut -f1)
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, python3-cairo, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.8), gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-gtk4
Recommends: gstreamer1.0-libav, gstreamer1.0-plugins-bad, gstreamer1.0-plugins-ugly, gstreamer1.0-pipewire | gstreamer1.0-pulseaudio
Section: video
Priority: optional
Homepage: https://webhookstudioapps.netlify.app
Description: Media player for videos, music and streams
 Fireside plays almost any video or music file, network streams and
 playlists. It has a library with folder thumbnails, favorite places,
 saved playlists and a music section, plus subtitles, audio and subtitle
 tracks, speed control, a ten-band equalizer, picture adjustments,
 snapshots and a play-as-audio mode. Nine color themes, accent colors and
 three control bar styles let you make it your own.
 .
 Part of the Sedge family of webhookstudios apps.
EOT

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/fireside_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "Built $OUT"
