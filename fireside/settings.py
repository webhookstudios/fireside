"""Settings and remembered state, kept as small JSON files.

~/.config/fireside/settings.json   choices made in Preferences and the player
~/.local/state/fireside/state.json  resume points and recent files
"""
import json
import os

from gi.repository import GLib, GObject

CONFIG = os.path.join(GLib.get_user_config_dir(), 'fireside')
STATE = os.path.join(GLib.get_user_state_dir(), 'fireside')

DEFAULTS = {
    # Look
    'theme': 'sedge',
    'scheme': 'system',          # system, light, dark (the Sedge theme only)
    'accent': '',                # '' = the theme's own accent, else a hex color
    'controls': 'floating',      # floating, docked, minimal
    'radius': 16,
    'seek_height': 6,
    'controls_opacity': 0.86,
    'hide_delay': 3,
    'backdrop': 'embers',        # embers, cover, plain (audio files)
    'time_mode': 'remaining',    # remaining, total
    'playlist_side': 'right',
    'playlist_open': False,
    'big_play': True,
    # Playback
    'volume': 0.8,
    'muted': False,
    'speed': 1.0,
    'resume': True,
    'autoplay_next': True,
    'repeat': 'off',             # off, all, one
    'shuffle': False,
    'skip_seconds': 10,
    'fit': 'contain',            # contain, cover, fill, original
    'start_fullscreen': False,
    'inhibit': True,
    # Subtitles
    'sub_auto': True,
    'sub_size': 24,
    'sub_font': 'Public Sans Semi-Bold',
    # Audio
    'eq_on': False,
    'eq_preset': 'flat',
    'eq_bands': [0.0] * 10,
    # Picture
    'brightness': 0.0,
    'contrast': 1.0,
    'saturation': 1.0,
    'hue': 0.0,
    # Library
    'favorites': [],
    'folder_view': 'grid',       # grid, list
    # Snapshots
    'snapshot_dir': '',
    # Window
    'width': 1100,
    'height': 680,
}


def _load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Settings(GObject.Object):
    """Settings with a 'changed' signal carrying the key that changed.
    Writes are batched and saved shortly after the last change."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__()
        self._path = os.path.join(CONFIG, 'settings.json')
        self._data = dict(DEFAULTS)
        for key, value in _load(self._path).items():
            if key in DEFAULTS and type(value) is type(DEFAULTS[key]) or (
                    key in DEFAULTS and isinstance(DEFAULTS[key], float) and isinstance(value, int)):
                self._data[key] = value
        self._pending = 0

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key):
        return self._data[key]

    def set(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.emit('changed', key)
        self._queue()

    def reset(self, keys):
        for key in keys:
            self.set(key, DEFAULTS[key] if not isinstance(DEFAULTS[key], list) else list(DEFAULTS[key]))

    def _queue(self):
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(400, self.flush)

    def flush(self):
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0
        try:
            _save(self._path, self._data)
        except OSError:
            pass
        return False


class State:
    """Resume points (seconds, keyed by URI) and the recent files list."""
    MAX_RECENT = 12
    MAX_RESUME = 300

    def __init__(self):
        self._path = os.path.join(STATE, 'state.json')
        data = _load(self._path)
        self.resume = data.get('resume', {}) if isinstance(data.get('resume'), dict) else {}
        self.recent = [r for r in data.get('recent', []) if isinstance(r, dict) and 'uri' in r]

    def remember(self, uri, position, duration):
        # Forget the spot near the start and near the end, like a bookmark you no longer need.
        if duration and (position < 15 or position > duration - 20):
            self.resume.pop(uri, None)
        elif position >= 15:
            self.resume.pop(uri, None)
            self.resume[uri] = round(position, 1)
            while len(self.resume) > self.MAX_RESUME:
                self.resume.pop(next(iter(self.resume)))

    def add_recent(self, uri, title):
        self.recent = [r for r in self.recent if r['uri'] != uri]
        self.recent.insert(0, {'uri': uri, 'title': title})
        del self.recent[self.MAX_RECENT:]

    def clear_recent(self):
        self.recent = []

    def save(self):
        try:
            _save(self._path, {'resume': self.resume, 'recent': self.recent})
        except OSError:
            pass
