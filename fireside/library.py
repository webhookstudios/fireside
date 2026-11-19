"""The library behind the home page: thumbnails, the music index and saved playlists."""
import hashlib
import json
import os
import queue
import threading

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib, GObject, Gst, GstPbutils  # noqa: E402

from .player import MEDIA_EXTS, title_for  # noqa: E402
from .settings import STATE  # noqa: E402

AUDIO_EXTS = {'.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.aac', '.wav', '.wma', '.alac', '.aiff',
              '.aif', '.ape', '.wv', '.mid', '.midi', '.ac3', '.dts', '.mka'}
LIST_EXTS = {'.m3u', '.m3u8', '.pls'}
COVER_NAMES = ('cover', 'folder', 'front', 'album', 'albumart')
THUMBS = os.path.join(GLib.get_user_cache_dir(), 'thumbnails', 'large')
COVERS = os.path.join(GLib.get_user_cache_dir(), 'fireside', 'covers')
PLAYLISTS = os.path.join(GLib.get_user_data_dir(), 'fireside', 'playlists')


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXTS:
        return 'audio'
    if ext in LIST_EXTS:
        return 'list'
    if ext in MEDIA_EXTS:
        return 'video'
    return None


def music_dir():
    if os.environ.get('FIRESIDE_MUSIC_DIR'):
        return os.environ['FIRESIDE_MUSIC_DIR']
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC) or os.path.expanduser('~/Music')


def videos_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS) or os.path.expanduser('~/Videos')


def list_folder(path):
    """Subfolders and playable files in one folder, each sorted by name. Hidden items are skipped."""
    folders, files = [], []
    try:
        names = os.listdir(path)
    except OSError:
        return folders, files
    for name in sorted(names, key=str.lower):
        if name.startswith('.'):
            continue
        full = os.path.join(path, name)
        if os.path.isdir(full):
            folders.append(full)
        elif kind_of(full):
            files.append(full)
    return folders, files


def folder_cover(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for name in names:
        root, ext = os.path.splitext(name.lower())
        if root in COVER_NAMES and ext in ('.jpg', '.jpeg', '.png', '.webp'):
            return os.path.join(folder, name)
    return None


# ---- thumbnails ------------------------------------------------------------------------------

def _thumb_path(uri):
    return os.path.join(THUMBS, hashlib.md5(uri.encode()).hexdigest() + '.png')


def cached_thumb(path):
    """A thumbnail the desktop already made (the Files app shares this cache), if it's current."""
    f = Gio.File.new_for_path(path)
    try:
        info = f.query_info('thumbnail::path,thumbnail::is-valid', Gio.FileQueryInfoFlags.NONE, None)
        tp = info.get_attribute_byte_string('thumbnail::path')
        if tp and info.get_attribute_boolean('thumbnail::is-valid') and os.path.exists(tp):
            return tp
    except GLib.Error:
        pass
    mine = _thumb_path(f.get_uri())
    try:
        if os.path.getmtime(mine) >= os.path.getmtime(path):
            return mine
    except OSError:
        pass
    return None


def _video_frame(uri, width=256):
    pipe = Gst.parse_launch(
        'playbin name=pb audio-sink=fakesink flags=0x1 video-sink="videoconvert ! videoscale ! '
        f'video/x-raw,format=RGB,width={width},pixel-aspect-ratio=1/1 ! appsink name=sink sync=false"')
    pipe.props.uri = uri
    bus = pipe.get_bus()
    try:
        pipe.set_state(Gst.State.PAUSED)
        msg = bus.timed_pop_filtered(8 * Gst.SECOND, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)
        if not msg or msg.type == Gst.MessageType.ERROR:
            return None
        ok, dur = pipe.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            # A frame a little way in, past any black lead-in.
            pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                             min(dur // 10, 120 * Gst.SECOND))
            bus.timed_pop_filtered(8 * Gst.SECOND, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)
        sink = pipe.get_by_name('sink')
        sample = sink.emit('pull-preroll') if sink else None
        if sample is None:
            return None
        s = sample.get_caps().get_structure(0)
        w, h = s.get_int('width')[1], s.get_int('height')[1]
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        data = bytes(info.data)
        buf.unmap(info)
        stride = len(data) // h if h else w * 3
        return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8,
                                               w, h, stride)
    finally:
        pipe.set_state(Gst.State.NULL)


def _audio_cover(uri):
    try:
        info = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(uri)
    except GLib.Error:
        return None
    tags = info.get_tags()
    if not tags:
        return None
    for tag in (Gst.TAG_IMAGE, Gst.TAG_PREVIEW_IMAGE):
        ok, sample = tags.get_sample(tag)
        if ok and sample:
            buf = sample.get_buffer()
            ok, mi = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            data = bytes(mi.data)
            buf.unmap(mi)
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                return loader.get_pixbuf()
            except GLib.Error:
                continue
    return None


class Thumbnailer(GObject.Object):
    """Makes thumbnails one at a time on a worker thread. 'ready' gives (file path, image path)."""
    __gsignals__ = {'ready': (GObject.SignalFlags.RUN_FIRST, None, (str, str))}

    def __init__(self):
        super().__init__()
        self._q = queue.LifoQueue()   # newest first: whatever the person is looking at now
        self._queued = set()
        self._failed = set()
        threading.Thread(target=self._work, daemon=True).start()

    def request(self, path):
        """The image path if one is ready now; otherwise None and 'ready' fires later."""
        kind = kind_of(path)
        if kind == 'audio':
            folder_img = folder_cover(os.path.dirname(path))
            mine = os.path.join(COVERS, hashlib.md5(path.encode()).hexdigest() + '.png')
            if os.path.exists(mine):
                return mine
            if path in self._failed:
                return folder_img
        elif kind == 'video':
            hit = cached_thumb(path)
            if hit:
                return hit
            if path in self._failed:
                return None
        else:
            return None
        if path not in self._queued:
            self._queued.add(path)
            self._q.put(path)
        return folder_img if kind == 'audio' else None

    def _work(self):
        while True:
            path = self._q.get()
            out = None
            try:
                uri = Gio.File.new_for_path(path).get_uri()
                if kind_of(path) == 'audio':
                    pix = _audio_cover(uri)
                    if pix:
                        pix = pix.scale_simple(256, max(1, round(256 * pix.get_height() / pix.get_width())),
                                               GdkPixbuf.InterpType.BILINEAR)
                        os.makedirs(COVERS, exist_ok=True)
                        out = os.path.join(COVERS, hashlib.md5(path.encode()).hexdigest() + '.png')
                        pix.savev(out, 'png', [], [])
                else:
                    pix = _video_frame(uri)
                    if pix:
                        os.makedirs(THUMBS, mode=0o700, exist_ok=True)
                        out = _thumb_path(uri)
                        mtime = str(int(os.path.getmtime(path)))
                        tmp = out + '.fireside.tmp'
                        pix.savev(tmp, 'png', ['tEXt::Thumb::URI', 'tEXt::Thumb::MTime', 'tEXt::Software'],
                                  [uri, mtime, 'Fireside'])
                        os.replace(tmp, out)
            except Exception:
                out = None
            if out is None:
                self._failed.add(path)
                if kind_of(path) == 'audio':
                    out = folder_cover(os.path.dirname(path))
            if out:
                GLib.idle_add(self.emit, 'ready', path, out)


# ---- music index -----------------------------------------------------------------------------

class Song:
    __slots__ = ('path', 'uri', 'title', 'artist', 'album', 'track', 'duration', 'mtime')

    def __init__(self, path, d=None):
        d = d or {}
        self.path = path
        self.uri = Gio.File.new_for_path(path).get_uri()
        self.title = d.get('title') or title_for(self.uri)
        self.artist = d.get('artist') or ''
        self.album = d.get('album') or ''
        self.track = d.get('track') or 0
        self.duration = d.get('duration') or 0.0
        self.mtime = d.get('mtime') or 0

    def to_json(self):
        return {'title': self.title, 'artist': self.artist, 'album': self.album, 'track': self.track,
                'duration': self.duration, 'mtime': self.mtime}


class MusicIndex(GObject.Object):
    """Every song under the music folders, with tags read once and kept in a cache file."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, folders):
        super().__init__()
        self.folders = folders
        self.songs = []
        self.scanning = False
        self._cache_path = os.path.join(STATE, 'music.json')

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        try:
            with open(self._cache_path) as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
        found = []
        for top in self.folders:
            for root, dirs, files in os.walk(top):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
                for name in files:
                    if kind_of(name) == 'audio':
                        found.append(os.path.join(root, name))
        songs = []
        disc = None
        dirty = False
        for i, path in enumerate(found):
            try:
                mtime = int(os.path.getmtime(path))
            except OSError:
                continue
            entry = cache.get(path)
            if not entry or entry.get('mtime') != mtime:
                if disc is None:
                    disc = GstPbutils.Discoverer.new(5 * Gst.SECOND)
                entry = self._read(disc, path)
                entry['mtime'] = mtime
                cache[path] = entry
                dirty = True
            songs.append(Song(path, entry))
            if dirty and i % 50 == 49:
                GLib.idle_add(self._publish, list(songs), False)
        cache = {s.path: s.to_json() for s in songs}
        try:
            os.makedirs(STATE, exist_ok=True)
            with open(self._cache_path, 'w') as f:
                json.dump(cache, f)
        except OSError:
            pass
        GLib.idle_add(self._publish, songs, True)

    @staticmethod
    def _read(disc, path):
        out = {}
        try:
            info = disc.discover_uri(Gio.File.new_for_path(path).get_uri())
        except GLib.Error:
            return out
        dur = info.get_duration()
        if dur and dur > 0:
            out['duration'] = dur / Gst.SECOND
        tags = info.get_tags()
        if tags:
            for key, tag in (('title', Gst.TAG_TITLE), ('artist', Gst.TAG_ARTIST), ('album', Gst.TAG_ALBUM)):
                ok, val = tags.get_string(tag)
                if ok and val and val.strip():
                    out[key] = val.strip()
            if 'artist' not in out:
                ok, val = tags.get_string(Gst.TAG_ALBUM_ARTIST)
                if ok and val:
                    out['artist'] = val.strip()
            ok, n = tags.get_uint(Gst.TAG_TRACK_NUMBER)
            if ok:
                out['track'] = n
        return out

    def _publish(self, songs, final):
        songs.sort(key=lambda s: (s.artist.lower() or '￿', s.album.lower(), s.track, s.title.lower()))
        self.songs = songs
        if final:
            self.scanning = False
        self.emit('changed')
        return False

    def albums(self):
        """(album, artist, [songs]) for every album, sorted by artist then album."""
        groups = {}
        for s in self.songs:
            key = (s.album or 'Other songs', s.artist)
            groups.setdefault(key, []).append(s)
        return [(k[0], k[1], v) for k, v in sorted(groups.items(), key=lambda kv: (kv[0][1].lower(), kv[0][0].lower()))]

    def artists(self):
        groups = {}
        for s in self.songs:
            groups.setdefault(s.artist or 'Unknown artist', []).append(s)
        return sorted(groups.items(), key=lambda kv: kv[0].lower())


# ---- saved playlists -------------------------------------------------------------------------

class PlaylistStore(GObject.Object):
    """Named playlists kept as .m3u8 files, so other players can read them too."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        os.makedirs(PLAYLISTS, exist_ok=True)

    def _path(self, name):
        safe = name.replace('/', '-').strip() or 'Playlist'
        return os.path.join(PLAYLISTS, safe + '.m3u8')

    def names(self):
        try:
            files = [f for f in os.listdir(PLAYLISTS) if f.endswith('.m3u8')]
        except OSError:
            return []
        files.sort(key=lambda f: f.lower())
        return [f[:-5] for f in files]

    def unique_name(self, base='New Playlist'):
        names = set(self.names())
        if base not in names:
            return base
        n = 2
        while f'{base} {n}' in names:
            n += 1
        return f'{base} {n}'

    def load(self, name):
        """[(uri, title)] for a playlist."""
        out = []
        title = None
        try:
            with open(self._path(name), encoding='utf-8') as f:
                for line in f.read().splitlines():
                    line = line.strip()
                    if line.startswith('#EXTINF:'):
                        title = line.split(',', 1)[1] if ',' in line else None
                    elif line and not line.startswith('#'):
                        uri = line if '://' in line else Gio.File.new_for_path(line).get_uri()
                        out.append((uri, title or title_for(uri)))
                        title = None
        except OSError:
            pass
        return out

    def save(self, name, entries):
        with open(self._path(name), 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for uri, title in entries:
                f.write(f'#EXTINF:-1,{title}\n')
                local = Gio.File.new_for_uri(uri).get_path()
                f.write((local or uri) + '\n')
        self.emit('changed')

    def add(self, name, entries):
        have = self.load(name)
        known = {u for u, _t in have}
        have.extend((u, t) for u, t in entries if u not in known)
        self.save(name, have)

    def rename(self, old, new):
        if old == new or not new.strip():
            return False
        if os.path.exists(self._path(new)):
            return False
        os.replace(self._path(old), self._path(new))
        self.emit('changed')
        return True

    def delete(self, name):
        try:
            Gio.File.new_for_path(self._path(name)).trash(None)
        except GLib.Error:
            try:
                os.remove(self._path(name))
            except OSError:
                pass
        self.emit('changed')
