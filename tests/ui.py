#!/usr/bin/python3
"""Drives the real window: opens a video and a song, changes themes and bar styles, and checks
what the player reports. Uses throwaway settings folders.

    python3 tests/ui.py MEDIA_DIR [screenshot-dir]

MEDIA_DIR needs 'Sample Clip.mkv' (with 'Sample Clip.srt' beside it) and two tagged MP3s,
evening.mp3 ('Evening Song') and morning.mp3.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix='fireside-test-')
os.environ['FIRESIDE_MUSIC_DIR'] = sys.argv[1]
for var in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[var] = os.path.join(tmp, var.lower())

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst, Gtk  # noqa: E402

from fireside.app import FiresideApp  # noqa: E402

MEDIA = sys.argv[1]
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
VIDEO = os.path.join(MEDIA, 'Sample Clip.mkv')
SONG = os.path.join(MEDIA, 'evening.mp3')
failures = []
steps = []
app = FiresideApp()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what, flush=True)
    if not ok:
        failures.append(what)


def shot(name, tries=4):
    if not SHOTS:
        return
    win = app.win
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        # The window hasn't drawn its new page yet; try again after the next frames.
        if tries:
            GLib.timeout_add(250, lambda: shot(name, tries - 1) and False)
        else:
            print('no picture for ' + name, flush=True)
        return
    tex = win.get_renderer().render_texture(node, None)
    tex.save_to_png(os.path.join(SHOTS, name + '.png'))
    print('shot ' + name, flush=True)


def step(delay):
    def deco(fn):
        steps.append((delay, fn))
        return fn
    return deco


@step(2500)
def open_video():
    win = app.win
    win.open_sources([VIDEO], replace=True)


@step(2500)
def video_playing():
    win = app.win
    p = win.player
    check('video is playing', p.state == 'playing')
    check('video found', p.has_video)
    check('length read (30 s)', 29 < p.duration < 31)
    check('subtitle file loaded beside the video', p.sub_uri is not None and p.sub_uri.endswith('.srt'))
    check('one subtitle track', len(p.text_tracks()) == 1)
    check('one audio track', len(p.audio_tracks()) == 1)
    check(f'window title is the file name ({win.wtitle.get_title()!r}, tags {list(p.tags)})', win.wtitle.get_title() == 'Sample Clip')
    win._show_controls()
    shot('01-video-sedge')


@step(800)
def seek_and_speed():
    win = app.win
    p = win.player
    p.seek(12)
    win.set_speed(1.5)


@step(2500)
def check_seek():
    p = app.win.player
    pos = p.position()
    check(f'seek to 12 s then play at 1.5x (now {pos:.1f} s)', 13 < pos < 17)
    app.win.set_speed(1.0)


@step(500)
def subtitles_off_on():
    win = app.win
    win.activate_action('win.subtitle-track', GLib.Variant('i', -1))
    check('subtitles off', not win.player.subtitles_on())
    win.activate_action('win.subtitle-track', GLib.Variant('i', 0))
    check('subtitles on', win.player.subtitles_on())


@step(300)
def snapshot():
    win = app.win
    win.settings.set('snapshot_dir', os.path.join(tmp, 'snaps'))
    win.activate_action('win.snapshot')
    files = os.listdir(os.path.join(tmp, 'snaps')) if os.path.isdir(os.path.join(tmp, 'snaps')) else []
    check('snapshot saved as PNG', len(files) == 1 and files[0].endswith('.png'))


@step(300)
def themes_and_styles():
    s = app.settings
    s.set('theme', 'ember')
    s.set('controls', 'docked')


@step(700)
def shot_ember():
    app.win._show_controls()
    check('docked bar sits under the video', app.win.controls.get_parent() is not app.win.ctl_rev)
    shot('02-video-ember-docked')
    app.settings.set('theme', 'midnight')
    app.settings.set('controls', 'minimal')
    app.settings.set('accent', '#c9a227')


@step(700)
def shot_midnight():
    app.win._show_controls()
    check('minimal bar floats over the video', app.win.controls.get_parent() is app.win.ctl_rev)
    shot('03-video-midnight-minimal-amber')
    app.settings.set('accent', '')
    app.settings.set('controls', 'floating')
    app.settings.set('theme', 'driftwood')
    app.win.player.pause()


@step(800)
def shot_driftwood_paused():
    check('big play button shows while paused', app.win.big_play.get_visible())
    shot('04-video-driftwood-paused')
    app.settings.set('theme', 'sedge')


@step(500)
def add_song():
    app.win.open_sources([SONG], replace=False)
    check('song queued after the video', len(app.win.playlist) == 2)
    app.win.split.set_show_sidebar(True)
    app.win.activate_action('win.next')


@step(2200)
def song_playing():
    win = app.win
    p = win.player
    check('song is playing', p.state == 'playing')
    check('song has no video', not p.has_video)
    check('music backdrop shown', win.media_stack.get_visible_child_name() == 'audio')
    check(f'title tag read ({win.wtitle.get_title()!r} {p.tags.get("title")!r})', win.wtitle.get_title() == 'Evening Song')
    check('embers moving', win.hearth.embers.running)
    app.settings.set('theme', 'lantern')


@step(1500)
def shot_song():
    shot('05-song-lantern-playlist')
    app.settings.set('eq_on', True)
    app.settings.set('eq_bands', [6.0] + [0.0] * 9)
    check('equalizer band set', abs(app.win.player.eq.get_property('band0') - 6.0) < 0.01)


@step(400)
def prefs():
    app.settings.set('theme', 'sedge')
    app.activate_action('preferences')


@step(900)
def shot_prefs():
    shot('06-preferences')
    dlg = app.win.get_visible_dialog()
    check('preferences opened', dlg is not None)
    if dlg:
        dlg.force_close()
    app.win.activate_action('win.effects')


@step(800)
def shot_effects():
    shot('07-effects')
    dlg = app.win.get_visible_dialog()
    if dlg:
        dlg.force_close()
    app.win.stop()


@step(800)
def shot_welcome():
    check('welcome page after stop', app.win.main_stack.get_visible_child_name() == 'welcome')
    check('recent list has both files', len(app.state.recent) == 2)
    shot('08-home')
    app.win.browse(MEDIA)


@step(3500)
def folder_view():
    lib = app.win.library
    check('folder shows 3 files', len(lib._tiles) == 3)
    tile = lib._tiles.get(VIDEO)
    check('video thumbnail made', tile is not None and tile.pic.get_visible())
    lib.toggle_favorite(MEDIA)
    check('folder is a favorite', MEDIA in app.settings['favorites'])
    shot('09-folder-grid')
    app.playlists.save('Road Trip', [])
    app.win._add_to_named('Road Trip', MEDIA)
    check('folder added to playlist', len(app.playlists.load('Road Trip')) == 3)


@step(400)
def music_page():
    app.win.library.show_music()


@step(2500)
def music_listed():
    lib = app.win.library
    check(f'music index found 2 songs ({len(app.music.songs)})', len(app.music.songs) == 2)
    check('songs sorted by track', [s.title for s in app.music.songs] == ['Evening Song', 'Morning Walk'])
    lib._play_songs(False, 1)


@step(1500)
def music_playing_in_library():
    win = app.win
    check('song plays while library stays open', win.main_stack.get_visible_child_name() == 'welcome')
    check('bar docked under library', win.controls.get_parent() is not win.ctl_rev and win.tview.get_reveal_bottom_bars())
    check('now playing shows the song', win.controls.now_title.get_label() == 'Morning Walk')
    shot('10-music-library')
    win.library.show_playlist('Road Trip')


@step(600)
def playlist_page():
    shot('11-playlist')
    app.win._lib_play(VIDEO, True)


@step(2000)
def video_as_audio():
    win = app.win
    p = win.player
    check('video playing as audio', p.audio_only and not p.has_video and p.state == 'playing')
    check('music backdrop for audio-only video', win.media_stack.get_visible_child_name() == 'audio')
    win.show_page('player')
    shot('12-video-as-audio')


# ---- a stand-in TV for the casting test: answers SSDP searches and DLNA commands ----------

class FakeTV:
    def __init__(self):
        import socket, struct, threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        tv = self
        self.actions = []
        self.fetched = None
        self.state = 'STOPPED'
        self.uri = None

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = (f'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
                        '<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
                        '<friendlyName>Test Living Room TV</friendlyName><serviceList><service>'
                        '<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>'
                        '<controlURL>/avt</controlURL></service><service>'
                        '<serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>'
                        '<controlURL>/rcs</controlURL></service></serviceList></device></root>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                import re
                import urllib.request
                data = self.rfile.read(int(self.headers['Content-Length'])).decode()
                action = self.headers['SOAPAction'].strip('"').split('#')[1]
                tv.actions.append(action)
                extra = ''
                if action == 'SetAVTransportURI':
                    tv.uri = re.search(r'<CurrentURI>(.*?)</CurrentURI>', data).group(1).replace('&amp;', '&')
                    req = urllib.request.Request(tv.uri, headers={'Range': 'bytes=0-99'})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        tv.fetched = (r.status, len(r.read()), r.headers.get('Content-Type'))
                elif action == 'Play':
                    tv.state = 'PLAYING'
                elif action == 'Pause':
                    tv.state = 'PAUSED_PLAYBACK'
                elif action == 'Stop':
                    tv.state = 'STOPPED'
                elif action == 'GetTransportInfo':
                    extra = f'<CurrentTransportState>{tv.state}</CurrentTransportState>'
                elif action == 'GetPositionInfo':
                    extra = '<RelTime>0:00:07</RelTime><TrackDuration>0:00:30</TrackDuration>'
                body = (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                        f'<u:{action}Response xmlns:u="x">{extra}</u:{action}Response></s:Body></s:Envelope>').encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        loc = f'http://127.0.0.1:{self.http.server_address[1]}/desc.xml'
        self.ssdp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.ssdp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            self.ssdp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.ssdp.bind(('', 1900))
        self.ssdp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack('4sl', socket.inet_aton('239.255.255.250'), socket.INADDR_ANY))

        def answer():
            while True:
                try:
                    data, addr = self.ssdp.recvfrom(4096)
                except OSError:
                    return
                if b'M-SEARCH' in data and b'MediaRenderer' in data:
                    self.ssdp.sendto(('HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n'
                                      f'LOCATION: {loc}\r\nUSN: uuid:fake-tv\r\n\r\n').encode(), addr)
        threading.Thread(target=answer, daemon=True).start()


@step(300)
def subtitle_timing():
    win = app.win
    win.player.audio_only = False
    win.audio_only_action.set_state(GLib.Variant('b', False))
    win.open_sources([VIDEO], replace=True)


@step(2500)
def subtitle_timing_check():
    win = app.win
    win.activate_action('win.sub-later')
    win.activate_action('win.sub-later')
    check(f'subtitles moved 0.2 s later ({win.player.sub_offset})', abs(win.player.sub_offset - 0.2) < 0.01)
    check('playbin got the subtitle offset', win.player.playbin.props.text_offset == 200_000_000)
    win.activate_action('win.sub-reset')
    check('subtitle timing back to normal', win.player.playbin.props.text_offset == 0)
    win.player.seek(10)


@step(1200)
def continue_watching():
    win = app.win
    win.player.pause()
    # The test clip is only 30 s and resume points are kept for files over a minute,
    # so record one as if this were a 10-minute video.
    app.state.remember(win.player.uri, 120, 600)
    win.stop()


@step(800)
def continue_check():
    win = app.win
    n = 0
    child = win.cont_box.get_first_child()
    while child:
        n += 1
        child = child.get_next_sibling()
    check(f'continue watching shows the video ({n})', n == 1 and win.cont_group.get_visible())
    check('video length saved for the progress bar', app.state.lengths.get(Gio.File.new_for_path(VIDEO).get_uri()) == 600)
    win.library.show_home()
    shot('13-continue-watching')


@step(400)
def sleep_timer():
    win = app.win
    win.open_sources([VIDEO], replace=True)


@step(2000)
def sleep_check():
    import time as _t
    win = app.win
    win.activate_action('win.sleep', GLib.Variant('s', '15'))
    check('sleep timer shows in the header', win.sleep_btn.get_visible())
    win._sleep_deadline = _t.monotonic() + 1.5   # pretend the 15 minutes are nearly up


@step(3000)
def sleep_fired():
    win = app.win
    check('sleep timer paused playback', win.player.state == 'paused')
    check('sleep timer cleared', not win.sleep_btn.get_visible())
    check('volume restored after the fade', abs(win.player.playbin.get_volume(
        __import__('gi.repository.GstAudio', fromlist=['x']).StreamVolumeFormat.CUBIC) - app.settings['volume']) < 0.02)


@step(300)
def gapless_start():
    win = app.win
    songs = [os.path.join(MEDIA, 'evening.mp3'), os.path.join(MEDIA, 'morning.mp3')]
    win.play_list([Gio.File.new_for_path(p).get_uri() for p in songs], 0, show_player=True)


@step(1500)
def gapless_seek():
    win = app.win
    check('next song planned for gapless', win._gapless_next is not None and win._gapless_next.endswith('morning.mp3'))
    win.player.seek(win.player.duration - 1.5)


@step(3500)
def gapless_check():
    win = app.win
    p = win.player
    check('moved to the next song without reloading', p.uri.endswith('morning.mp3') and p._streams_started >= 2)
    check('queue marks the second song', win.playlist.current == 1)
    check(f'title follows the song ({win.wtitle.get_title()!r})', win.wtitle.get_title() == 'Morning Walk')
    check('still playing', p.state == 'playing')


@step(300)
def mini_open():
    app.win.activate_action('win.mini')


@step(800)
def mini_check():
    win = app.win
    check(f'mini player open, main window hidden (mini={win.mini is not None and win.mini.get_visible()}, main={win.get_visible()})',
          win.mini is not None and win.mini.get_visible() and not win.get_visible())
    if win.mini:
        check('mini shows the song', win.mini.title.get_label() == 'Morning Walk')
        mini = win.mini
        w, h = mini.get_width(), mini.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(mini).snapshot(snap, w, h)
        node = snap.to_node()
        if SHOTS and node:
            mini.get_renderer().render_texture(node, None).save_to_png(os.path.join(SHOTS, '14-mini.png'))
        win.mini.back()


@step(600)
def mini_closed():
    win = app.win
    check('back to the full window', win.mini is None and win.get_visible())
    global tv
    tv = FakeTV()
    win.open_sources([VIDEO], replace=True)


@step(2000)
def cast_find():
    from fireside import cast
    found = cast.find_dlna(timeout=2)
    names = [r.name for r in found]
    check(f'found the test TV on the network ({names})', 'Test Living Room TV' in names)
    rec = next((r for r in found if r.name == 'Test Living Room TV'), None)
    if rec:
        app.win.start_cast(rec)


@step(3500)
def cast_check():
    win = app.win
    check('TV was told to play', 'SetAVTransportURI' in tv.actions and 'Play' in tv.actions)
    check(f'TV could fetch the video ({tv.fetched})', tv.fetched is not None and tv.fetched[0] == 206
          and tv.fetched[1] == 100 and tv.fetched[2] == 'video/x-matroska')
    check('local player paused while casting', win.player.playbin.get_state(0)[1] != Gst.State.PLAYING)
    check('banner shows the TV', win.cast_banner.get_revealed() and 'Test Living Room TV' in win.cast_banner.get_title())
    check(f'position comes from the TV ({win.controls._pos})', abs(win.controls._pos - 7) < 0.5)
    shot('15-casting')
    win.activate_action('win.toggle-play')


@step(1500)
def cast_pause():
    win = app.win
    check('pause reached the TV', 'Pause' in tv.actions)
    win.stop_cast()


@step(800)
def cast_stopped():
    win = app.win
    check('casting stopped', win.cast is None and not win.cast_banner.get_revealed())
    check('TV told to stop', tv.actions[-1] == 'Stop' or 'Stop' in tv.actions[-3:])


@step(300)
def live_tv_open():
    win = app.win
    win.stop()
    win.library.show_tv()


@step(6000)
def live_tv_loaded():
    page = app.win.library.tv_page
    chans = app.livetv.channels.get(page.country, [])
    check(f'live TV channels loaded for {page.country} ({len(chans)})', len(chans) > 50)
    bad = [c.url for c in chans if 'jmp2.uk' in c.url or 'pluto' in c.url.lower()]
    check('relayed streams from other services left out', not bad)
    check('channels shown in the grid', page.store.get_n_items() == len(chans))
    shot('16-live-tv')
    names = [page.cat_model.get_string(i) for i in range(page.cat_model.get_n_items())]
    if 'News' in names:
        page.cat_drop.set_selected(names.index('News'))


@step(2500)
def live_tv_start():
    global tv_tries
    tv_tries = 0
    try_channel()


def try_channel():
    """Channels come and go, so try up to five until one plays."""
    global tv_tries
    page = app.win.library.tv_page
    if tv_tries < min(5, len(page.shown_channels)):
        page._activate(None, tv_tries)
        tv_tries += 1


@step(9000)
def live_tv_playing():
    win = app.win
    p = win.player
    for _ in range(4):
        if p.state == 'playing' and p.has_video:
            break
        try_channel()
        ctx = GLib.MainContext.default()
        end = GLib.get_monotonic_time() + 8_000_000
        while GLib.get_monotonic_time() < end and not (p.state == 'playing' and p.has_video):
            ctx.iteration(False)
    check(f'a live channel plays ({win.wtitle.get_title()!r}, tried {tv_tries})', p.state == 'playing' and p.has_video)
    check('time shows Live', win.controls.end_label.get_label() == 'Live')
    check('channel queue lets Next flip channels', len(win.playlist) == len(win.library.tv_page.shown_channels))
    shot('17-live-channel')


@step(300)
def narrow_bar():
    win = app.win
    win.split.set_show_sidebar(True)
    win.set_default_size(980, 640)


@step(1200)
def narrow_check():
    win = app.win
    c = win.controls
    avail = win.stage_bin.get_width()
    need = c.get_width() + 40
    check(f'control bar fits a narrow player ({c.get_width()} in {avail})', need <= avail + 1 or c.get_width() <= avail)
    check('volume slider hidden when narrow', not c.vol.get_visible())
    win.set_default_size(1100, 680)


@step(300)
def done():
    print('PASS' if not failures else f'{len(failures)} FAILED', flush=True)
    app.win.close()
    app.quit()


def run_steps(i=0):
    if i >= len(steps):
        return False
    delay, fn = steps[i]

    def go():
        try:
            fn()
        except Exception as e:  # report and carry on
            import traceback
            traceback.print_exc()
            check(f'{fn.__name__} raised {e!r}', False)
        run_steps(i + 1)
        return False
    GLib.timeout_add(delay, go)


def started(_app):
    if app.win is None:
        print('FAIL window did not open', flush=True)
        app.quit()
        return
    app.settings.set('muted', True)  # stay quiet while testing
    app.win.set_default_size(1100, 680)
    run_steps()


app.connect('activate', lambda a: GLib.idle_add(started, a))
status = app.run([sys.argv[0]])
sys.exit(1 if failures else status)
