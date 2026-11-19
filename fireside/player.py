"""The playback engine: a GStreamer playbin drawing into a GTK paintable."""
import os

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstAudio', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('GstPbutils', '1.0')
gi.require_version('GstTag', '1.0')
from gi.repository import Gio, GLib, GObject, Gst, GstAudio, GstPbutils, GstTag, GstVideo  # noqa: E402

Gst.init(None)

SECOND = Gst.SECOND
FLAG_VIDEO = 1 << 0
FLAG_TEXT = 1 << 2
SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt', '.sub', '.smi', '.txt')
MEDIA_EXTS = {
    '.mp4', '.m4v', '.mkv', '.webm', '.avi', '.mov', '.wmv', '.flv', '.mpg', '.mpeg', '.ts', '.m2ts',
    '.mts', '.3gp', '.ogv', '.vob', '.divx', '.rm', '.rmvb', '.asf',
    '.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.aac', '.wav', '.wma', '.alac', '.aiff',
    '.aif', '.ape', '.mka', '.wv', '.mid', '.midi', '.ac3', '.dts', '.m3u', '.m3u8', '.pls',
}
EQ_FREQS = ('31', '62', '125', '250', '500', '1k', '2k', '4k', '8k', '16k')
EQ_PRESETS = {
    'flat': ('Flat', [0] * 10),
    'bass': ('Bass boost', [7, 6, 5, 3, 1, 0, 0, 0, 0, 0]),
    'treble': ('Treble boost', [0, 0, 0, 0, 0, 1, 3, 5, 6, 7]),
    'voice': ('Voices', [-3, -2, -1, 1, 4, 5, 4, 2, 0, -2]),
    'cinema': ('Cinema', [5, 4, 2, 0, -1, 0, 2, 3, 3, 2]),
    'rock': ('Rock', [5, 4, 2, -1, -2, -1, 2, 4, 5, 5]),
    'pop': ('Pop', [-1, 2, 4, 5, 3, 0, -1, -1, 0, 1]),
    'jazz': ('Jazz', [3, 2, 1, 2, -1, -1, 0, 1, 2, 3]),
    'classical': ('Classical', [4, 3, 2, 1, -1, -1, 0, 2, 3, 4]),
    'electronic': ('Electronic', [5, 4, 1, 0, -2, 2, 1, 2, 4, 5]),
    'night': ('Late night', [-4, -3, -1, 1, 3, 3, 2, 0, -2, -4]),
}


def is_media(path):
    return os.path.splitext(path)[1].lower() in MEDIA_EXTS


def uri_for(path_or_uri):
    if '://' in path_or_uri:
        return path_or_uri
    return Gio.File.new_for_path(path_or_uri).get_uri()


def title_for(uri):
    """A readable name from a URI, used until the file's own title tag arrives."""
    f = Gio.File.new_for_uri(uri)
    name = f.get_basename() if f.get_path() else uri.rstrip('/').rsplit('/', 1)[-1]
    name = GLib.uri_unescape_string(name or uri, None) or uri
    if f.get_path():
        name = os.path.splitext(name)[0]
    return name.replace('_', ' ')


def find_subtitle(uri):
    """Look next to a local file for a subtitle file with the same name."""
    path = Gio.File.new_for_uri(uri).get_path()
    if not path:
        return None
    folder, base = os.path.split(path)
    stem = os.path.splitext(base)[0].lower()
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    hits = []
    for name in names:
        low = name.lower()
        root, ext = os.path.splitext(low)
        if ext in SUB_EXTS and (root == stem or root.startswith(stem + '.')):
            hits.append(name)
    if not hits:
        return None
    hits.sort(key=lambda n: (len(n), n))
    return Gio.File.new_for_path(os.path.join(folder, hits[0])).get_uri()


class Player(GObject.Object):
    __gsignals__ = {
        'state-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'position': (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        'media-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'tags-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'tracks-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'video-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'buffering': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'eos': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'ready': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.uri = None
        self.sub_uri = None
        self.state = 'stopped'
        self.rate = 1.0
        self.duration = 0.0
        self.has_video = False
        self.tags = {}
        self._want_playing = False
        self._buffering = False
        self._seek_on_ready = None
        self._ticker = 0
        self._balance = {}
        self._announced = False
        self._known_pos = 0.0
        self._ready_sent = False
        self.audio_only = False
        self.stream_title = None

        self.playbin = Gst.ElementFactory.make('playbin', 'player')
        sink = Gst.ElementFactory.make('gtk4paintablesink', 'gtksink')
        self.paintable = sink.props.paintable
        video_sink = sink
        if self.paintable.props.gl_context is not None:
            glbin = Gst.ElementFactory.make('glsinkbin', 'glsink')
            if glbin is not None:
                glbin.props.sink = sink
                video_sink = glbin
        self.playbin.props.video_sink = video_sink

        self.audio_bin = Gst.parse_bin_from_description(
            'audioconvert ! scaletempo name=tempo ! audioconvert ! equalizer-10bands name=eq ! audioconvert', True)
        self.eq = self.audio_bin.get_by_name('eq')
        self.playbin.props.audio_filter = self.audio_bin

        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_message)
        self.playbin.connect('audio-tags-changed', lambda *_: GLib.idle_add(self._tracks_changed))
        self.playbin.connect('text-tags-changed', lambda *_: GLib.idle_add(self._tracks_changed))

    # ---- loading -------------------------------------------------------------------------

    def load(self, uri, play=True, start=0.0, sub_uri=None):
        self.playbin.set_state(Gst.State.NULL)
        self.uri = uri
        self.sub_uri = sub_uri
        self.duration = 0.0
        self.tags = {}
        self._announced = False
        self._known_pos = start or 0.0
        self._ready_sent = False
        self.stream_title = None
        flags = int(self.playbin.props.flags)
        self.playbin.props.flags = (flags & ~FLAG_VIDEO) if self.audio_only else (flags | FLAG_VIDEO)
        self._want_playing = play
        self._seek_on_ready = start if start and start > 0 else None
        self.playbin.props.uri = uri
        self.playbin.props.suburi = sub_uri
        self.emit('media-changed', uri)
        self.emit('tags-changed')
        self.playbin.set_state(Gst.State.PLAYING if play and not self._seek_on_ready else Gst.State.PAUSED)

    def set_subtitle_file(self, sub_uri):
        """playbin only takes a subtitle file before it starts, so reopen at the same spot."""
        if not self.uri:
            return
        pos = self.position()
        self.load(self.uri, play=self.state == 'playing', start=pos, sub_uri=sub_uri)
        self.set_subtitles_on(True)

    def set_audio_only(self, on):
        """Play just the sound of a video. playbin only takes this when it starts, so reopen."""
        if on == self.audio_only:
            return
        self.audio_only = on
        if self.uri:
            self.load(self.uri, play=self.state == 'playing' or self._want_playing,
                      start=self.position(), sub_uri=self.sub_uri)

    def stop(self):
        self._want_playing = False
        self.playbin.set_state(Gst.State.NULL)
        self.uri = None
        self._set_state('stopped')
        self._stop_ticker()

    # ---- transport -----------------------------------------------------------------------

    def play(self):
        if not self.uri:
            return
        self._want_playing = True
        if not self._buffering:
            self.playbin.set_state(Gst.State.PLAYING)

    def pause(self):
        self._want_playing = False
        self.playbin.set_state(Gst.State.PAUSED)

    def toggle(self):
        if self.state == 'playing' or (self._want_playing and self._buffering):
            self.pause()
        else:
            self.play()

    def position(self):
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        if ok and pos > 0:
            self._known_pos = pos / SECOND
        # Right after a seek the pipeline briefly answers 0; keep the last real answer instead.
        return self._known_pos

    def seek(self, seconds, fast=False):
        if not self.uri:
            return
        seconds = max(0.0, seconds)
        if self.duration:
            seconds = min(seconds, max(0.0, self.duration - 0.05))
        flags = Gst.SeekFlags.FLUSH | (Gst.SeekFlags.KEY_UNIT | Gst.SeekFlags.SNAP_NEAREST if fast
                                       else Gst.SeekFlags.ACCURATE)
        self.playbin.seek(self.rate, Gst.Format.TIME, flags, Gst.SeekType.SET, int(seconds * SECOND),
                          Gst.SeekType.NONE, -1)
        self._known_pos = seconds
        self.emit('position', seconds, self.duration)

    def skip(self, delta):
        self.seek(self.position() + delta)

    def step_frame(self, forward=True):
        if not self.uri:
            return
        self.pause()
        if forward:
            self.playbin.send_event(Gst.Event.new_step(Gst.Format.BUFFERS, 1, 1.0, True, False))
        else:
            # There is no backwards step for most files, so seek back by about one frame.
            self.seek(max(0.0, self.position() - 0.04))
        GLib.timeout_add(60, self._tick_once)

    def set_rate(self, rate):
        self.rate = rate
        if not self.uri:
            return
        self.playbin.seek(rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                              Gst.SeekType.SET, int(self.position() * SECOND), Gst.SeekType.NONE, -1)

    # ---- audio and picture ---------------------------------------------------------------

    def set_volume(self, value):
        self.playbin.set_volume(GstAudio.StreamVolumeFormat.CUBIC, max(0.0, min(1.5, value)))

    def set_muted(self, muted):
        self.playbin.props.mute = muted

    def set_eq(self, enabled, bands):
        for i, gain in enumerate(bands[:10]):
            self.eq.set_property(f'band{i}', float(gain) if enabled else 0.0)

    def set_balance(self, brightness, contrast, saturation, hue):
        """Values in the videobalance ranges: brightness and hue -1..1, contrast and saturation 0..2."""
        self._balance = {'BRIGHTNESS': brightness, 'CONTRAST': contrast - 1.0,
                         'SATURATION': saturation - 1.0, 'HUE': hue}
        self._apply_balance()

    def _apply_balance(self):
        try:
            channels = GstVideo.ColorBalance.list_channels(self.playbin)
        except Exception:
            return
        for ch in channels or []:
            name = ch.label.upper()
            for key, val in self._balance.items():
                if key in name:
                    mid = (ch.max_value + ch.min_value) / 2
                    half = (ch.max_value - ch.min_value) / 2
                    GstVideo.ColorBalance.set_value(self.playbin, ch, int(mid + val * half))

    # ---- tracks --------------------------------------------------------------------------

    def _track_name(self, kind, index):
        tags = self.playbin.emit(f'get-{kind}-tags', index)
        bits = []
        if tags:
            ok, title = tags.get_string(Gst.TAG_TITLE)
            if ok and title:
                bits.append(title)
            ok, lang = tags.get_string(Gst.TAG_LANGUAGE_NAME)
            if not ok or not lang:
                ok, code = tags.get_string(Gst.TAG_LANGUAGE_CODE)
                if ok and code:
                    lang = GstTag.tag_get_language_name(code) or code
            if lang and lang not in bits:
                bits.append(lang)
            if kind == 'audio':
                ok, codec = tags.get_string(Gst.TAG_AUDIO_CODEC)
                if ok and codec and not bits:
                    bits.append(codec)
        return ' · '.join(bits) or f'Track {index + 1}'

    def audio_tracks(self):
        return [self._track_name('audio', i) for i in range(self.playbin.props.n_audio)]

    def text_tracks(self):
        return [self._track_name('text', i) for i in range(self.playbin.props.n_text)]

    def current_audio(self):
        return self.playbin.props.current_audio

    def current_text(self):
        return self.playbin.props.current_text if self.subtitles_on() else -1

    def set_audio_track(self, index):
        self.playbin.props.current_audio = index

    def set_text_track(self, index):
        if index < 0:
            self.set_subtitles_on(False)
        else:
            self.set_subtitles_on(True)
            self.playbin.props.current_text = index

    def subtitles_on(self):
        return bool(int(self.playbin.props.flags) & FLAG_TEXT)

    def set_subtitles_on(self, on):
        flags = int(self.playbin.props.flags)
        self.playbin.props.flags = (flags | FLAG_TEXT) if on else (flags & ~FLAG_TEXT)

    def set_subtitle_font(self, desc):
        self.playbin.props.subtitle_font_desc = desc

    # ---- snapshot ------------------------------------------------------------------------

    def snapshot(self, path):
        sample = self.playbin.emit('convert-sample', Gst.Caps.from_string('image/png'))
        if sample is None:
            return False
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            with open(path, 'wb') as f:
                f.write(bytes(info.data))
        finally:
            buf.unmap(info)
        return True

    # ---- bus -----------------------------------------------------------------------------

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.emit('state-changed', state)

    def _tracks_changed(self):
        self.emit('tracks-changed')
        return False

    def _query_duration(self):
        ok, dur = self.playbin.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            self.duration = dur / SECOND

    def _on_message(self, _bus, msg):
        t = msg.type
        if t == Gst.MessageType.STATE_CHANGED and msg.src == self.playbin:
            _old, new, _pending = msg.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._set_state('playing')
                self._start_ticker()
            elif new == Gst.State.PAUSED:
                self._set_state('paused')
                self._stop_ticker()
                self._tick_once()
        elif t == Gst.MessageType.ASYNC_DONE:
            self._query_duration()
            has_video = self.playbin.props.n_video > 0 and not self.audio_only
            if has_video != self.has_video or not self._announced:
                self._announced = True
                self.has_video = has_video
                self.emit('video-changed', has_video)
            if self._seek_on_ready is not None:
                start, self._seek_on_ready = self._seek_on_ready, None
                self.seek(start)
                if self._want_playing:
                    self.playbin.set_state(Gst.State.PLAYING)
                return
            self._tick_once()
            if self._ready_sent:
                return  # a seek finishing, not a new file
            self._ready_sent = True
            self._apply_balance()
            if self.rate != 1.0:
                self.set_rate(self.rate)
            self.emit('tracks-changed')
            self.emit('ready')
        elif t == Gst.MessageType.DURATION_CHANGED:
            self._query_duration()
        elif t == Gst.MessageType.TAG:
            self._read_tags(msg.parse_tag())
        elif t == Gst.MessageType.BUFFERING:
            pct = msg.parse_buffering()
            self.emit('buffering', pct)
            if pct < 100 and not self._buffering:
                self._buffering = True
                if self._want_playing:
                    self.playbin.set_state(Gst.State.PAUSED)
            elif pct >= 100 and self._buffering:
                self._buffering = False
                if self._want_playing:
                    self.playbin.set_state(Gst.State.PLAYING)
        elif t == Gst.MessageType.EOS:
            self._stop_ticker()
            self.emit('eos')
        elif t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            self.playbin.set_state(Gst.State.NULL)
            self._set_state('stopped')
            self._stop_ticker()
            text = err.message
            if GstPbutils.is_missing_plugin_message(msg) or 'codec' in (debug or '').lower():
                text += ' A codec for this file may be missing.'
            self.emit('error', text)
        elif t == Gst.MessageType.ELEMENT and GstPbutils.is_missing_plugin_message(msg):
            desc = GstPbutils.missing_plugin_message_get_description(msg)
            self.emit('error', f'Fireside needs an extra part to play this: {desc}.')

    def _read_tags(self, taglist):
        changed = False
        # A title on one stream is often just a track's name ("Audio", "English"), so it only
        # counts as the file's title for music, which the window decides once it knows.
        if taglist.get_scope() == Gst.TagScope.STREAM:
            ok, val = taglist.get_string(Gst.TAG_TITLE)
            if ok and val and not self.stream_title:
                self.stream_title = val
                changed = True
        global_tags = taglist.get_scope() == Gst.TagScope.GLOBAL
        for key, tag in ((('title', Gst.TAG_TITLE),) if global_tags else ()) + (
                         ('artist', Gst.TAG_ARTIST), ('album', Gst.TAG_ALBUM),
                         ('genre', Gst.TAG_GENRE), ('vcodec', Gst.TAG_VIDEO_CODEC),
                         ('acodec', Gst.TAG_AUDIO_CODEC), ('container', Gst.TAG_CONTAINER_FORMAT)):
            ok, val = taglist.get_string(tag)
            if ok and val and self.tags.get(key) != val:
                self.tags[key] = val
                changed = True
        ok, bitrate = taglist.get_uint(Gst.TAG_BITRATE)
        if ok and bitrate:
            self.tags['bitrate'] = bitrate
        for tag in (Gst.TAG_IMAGE, Gst.TAG_PREVIEW_IMAGE):
            if 'image' in self.tags:
                break
            ok, sample = taglist.get_sample(tag)
            if ok and sample:
                buf = sample.get_buffer()
                ok, info = buf.map(Gst.MapFlags.READ)
                if ok:
                    self.tags['image'] = GLib.Bytes.new(bytes(info.data))
                    buf.unmap(info)
                    changed = True
        if changed:
            self.emit('tags-changed')

    # ---- position ticker -----------------------------------------------------------------

    def _start_ticker(self):
        if not self._ticker:
            self._ticker = GLib.timeout_add(200, self._tick)

    def _stop_ticker(self):
        if self._ticker:
            GLib.source_remove(self._ticker)
            self._ticker = 0

    def _tick(self):
        if not self.duration:
            self._query_duration()
        self.emit('position', self.position(), self.duration)
        return True

    def _tick_once(self):
        self._tick()
        return False

    def video_info(self):
        """Width, height and frame rate of the current video, if known."""
        pad = self.playbin.emit('get-video-pad', 0) if self.playbin.props.n_video else None
        if not pad:
            return None
        caps = pad.get_current_caps()
        if not caps:
            return None
        s = caps.get_structure(0)
        ok_w, w = s.get_int('width')
        ok_h, h = s.get_int('height')
        ok_f, num, den = s.get_fraction('framerate')
        return (w if ok_w else 0, h if ok_h else 0, (num / den) if ok_f and den else 0)

    def shutdown(self):
        self._stop_ticker()
        self.playbin.set_state(Gst.State.NULL)
