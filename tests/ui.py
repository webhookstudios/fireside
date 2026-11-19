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
from gi.repository import Gio, GLib, Gtk  # noqa: E402

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
