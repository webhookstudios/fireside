"""The play queue: what's in it, what plays next, and loading/saving .m3u lists."""
import os
import random

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import Gio, GLib, GObject, Gst, GstPbutils  # noqa: E402

from .player import is_media, title_for, uri_for  # noqa: E402


class Item:
    __slots__ = ('uri', 'title', 'duration', 'artist')

    def __init__(self, uri, title=None, duration=0.0):
        self.uri = uri
        self.title = title or title_for(uri)
        self.duration = duration
        self.artist = ''


def expand(paths_or_uris):
    """Files, folders (sorted, one level of subfolders deep) and .m3u/.pls lists → URIs."""
    out = []
    for p in paths_or_uris:
        uri = uri_for(p)
        path = Gio.File.new_for_uri(uri).get_path()
        if path and os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs.sort(key=str.lower)
                for name in sorted(files, key=str.lower):
                    full = os.path.join(root, name)
                    if is_media(full) and not name.lower().endswith(('.m3u', '.m3u8', '.pls')):
                        out.append(Gio.File.new_for_path(full).get_uri())
                if root.count(os.sep) - path.count(os.sep) >= 1:
                    dirs[:] = []
        elif path and path.lower().endswith(('.m3u', '.m3u8', '.pls')):
            out.extend(read_list(path))
        else:
            out.append(uri)
    return out


def read_list(path):
    folder = os.path.dirname(path)
    out = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    pls = path.lower().endswith('.pls')
    for line in lines:
        line = line.strip()
        if pls:
            if not line.lower().startswith('file'):
                continue
            line = line.split('=', 1)[-1].strip()
        if not line or line.startswith('#'):
            continue
        if '://' in line:
            out.append(line)
        else:
            full = line if os.path.isabs(line) else os.path.join(folder, line)
            out.append(Gio.File.new_for_path(full).get_uri())
    return out


class Playlist(GObject.Object):
    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'current-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'item-updated': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items = []
        self.current = -1
        self._order = []
        self._discoverer = None
        self._pending = set()

    def __len__(self):
        return len(self.items)

    def add(self, sources, replace=False):
        uris = expand(sources)
        if not uris:
            return -1
        if replace:
            self.items = []
            self.current = -1
        first = len(self.items)
        known = {it.uri for it in self.items}
        for uri in uris:
            if replace or uri not in known:
                self.items.append(Item(uri))
                known.add(uri)
            elif uri in known and len(uris) == 1:
                first = next(i for i, it in enumerate(self.items) if it.uri == uri)
        self._reshuffle()
        self.emit('changed')
        self._discover()
        return min(first, len(self.items) - 1)

    def remove(self, index):
        if not 0 <= index < len(self.items):
            return
        del self.items[index]
        if index < self.current:
            self.current -= 1
        elif index == self.current:
            self.current = -1
        self._reshuffle()
        self.emit('changed')

    def move(self, src, dst):
        if src == dst or not (0 <= src < len(self.items)) or not (0 <= dst <= len(self.items)):
            return
        cur = self.items[self.current] if self.current >= 0 else None
        item = self.items.pop(src)
        if dst > src:
            dst -= 1
        self.items.insert(dst, item)
        if cur is not None:
            self.current = self.items.index(cur)
        self._reshuffle()
        self.emit('changed')

    def clear(self):
        self.items = []
        self.current = -1
        self._order = []
        self.emit('changed')

    def set_current(self, index):
        self.current = index
        self.emit('current-changed', index)

    def current_item(self):
        return self.items[self.current] if 0 <= self.current < len(self.items) else None

    def _reshuffle(self):
        self._order = list(range(len(self.items)))
        random.shuffle(self._order)
        if self.current in self._order:
            self._order.remove(self.current)
            self._order.insert(0, self.current)

    def neighbor(self, step, wrap):
        """Index of the next (+1) or previous (-1) item, or -1 at the end of the list."""
        n = len(self.items)
        if n == 0:
            return -1
        if self.current < 0:
            return 0
        if self.settings['shuffle'] and n > 1:
            if len(self._order) != n:
                self._reshuffle()
            pos = self._order.index(self.current) if self.current in self._order else 0
            pos += step
            if 0 <= pos < n:
                return self._order[pos]
            if wrap:
                random.shuffle(self._order)
                return self._order[0 if step > 0 else -1]
            return -1
        i = self.current + step
        if 0 <= i < n:
            return i
        return (i % n) if wrap else -1

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for it in self.items:
                f.write(f'#EXTINF:{int(it.duration) if it.duration else -1},{it.title}\n')
                local = Gio.File.new_for_uri(it.uri).get_path()
                f.write((local or it.uri) + '\n')

    # Fill in titles and lengths in the background.
    def _discover(self):
        todo = [it for it in self.items if not it.duration and it.uri not in self._pending
                and it.uri.startswith('file:')]
        if not todo:
            return
        if self._discoverer is None:
            try:
                self._discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
            except GLib.Error:
                return
            self._discoverer.connect('discovered', self._discovered)
            self._discoverer.start()
        for it in todo:
            self._pending.add(it.uri)
            self._discoverer.discover_uri_async(it.uri)

    def _discovered(self, _d, info, _err):
        uri = info.get_uri()
        self._pending.discard(uri)
        dur = info.get_duration()
        tags = info.get_tags()
        for i, it in enumerate(self.items):
            if it.uri != uri:
                continue
            if dur and dur > 0:
                it.duration = dur / Gst.SECOND
            if tags and not info.get_video_streams():  # in videos a title is often just a track name
                ok, title = tags.get_string(Gst.TAG_TITLE)
                if ok and title and title.strip():
                    it.title = title.strip()
                ok, artist = tags.get_string(Gst.TAG_ARTIST)
                if ok and artist:
                    it.artist = artist
            self.emit('item-updated', i)
