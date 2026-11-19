"""The transport bar: seek bar, play buttons, tracks, speed, volume and fullscreen."""
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .playlist_view import fmt_time  # noqa: E402

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0)


def speed_label(rate):
    return f'{rate:g}×'


def _button(icon, tip, action=None, css=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, focus_on_click=False)
    b.add_css_class('flat')
    if css:
        b.add_css_class(css)
    if action:
        b.set_action_name(action)
    return b


def _menu_button(icon, tip):
    b = Gtk.MenuButton(icon_name=icon, tooltip_text=tip, focus_on_click=False)
    b.add_css_class('flat')
    b.set_direction(Gtk.ArrowType.UP)
    return b


class Controls(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.END)
        self.win = win
        self.settings = win.settings
        self.player = win.player
        self.add_css_class('fs-controls')
        self._dragging = False
        self._seek_timer = 0
        self._duration = 0.0
        self._pos = 0.0

        # Seek row
        self.cur = Gtk.Label(label='0:00', xalign=1)
        self.cur.add_css_class('time')
        self.seek = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, draw_value=False,
                              focus_on_click=False, has_tooltip=True)
        self.seek.add_css_class('seek')
        self.seek.set_range(0, 1)
        self.seek.set_increments(5, 30)
        self.seek.connect('change-value', self._on_seek_change)
        self.seek.connect('query-tooltip', self._seek_tip)
        self.end = Gtk.Button(focus_on_click=False, tooltip_text='Switch between time left and total length')
        self.end.add_css_class('flat')
        self.end_label = Gtk.Label(label='0:00', xalign=0)
        self.end_label.add_css_class('time')
        self.end.set_child(self.end_label)
        self.end.connect('clicked', lambda *_: self.settings.set(
            'time_mode', 'total' if self.settings['time_mode'] == 'remaining' else 'remaining'))

        # Buttons
        self.prev = _button('media-skip-backward-symbolic', 'Previous (P)', 'win.previous')
        self.back = _button('media-seek-backward-symbolic', '', 'win.skip-back')
        self.play = _button('media-playback-start-symbolic', 'Play (Space)', 'win.toggle-play', 'play')
        self.play.remove_css_class('flat')
        self.play.add_css_class('suggested-action')
        self.fwd = _button('media-seek-forward-symbolic', '', 'win.skip-forward')
        self.next = _button('media-skip-forward-symbolic', 'Next (N)', 'win.next')

        self.audio_btn = _menu_button('audio-x-generic-symbolic', 'Audio track (B)')
        self.subs_btn = _menu_button('media-view-subtitles-symbolic', 'Subtitles (V)')
        self.speed_btn = _menu_button('fireside-speed-symbolic', 'Speed: 1×')
        self.speed_btn.set_menu_model(self._speed_menu())
        self.fx_btn = _button('fireside-equalizer-symbolic', 'Effects and filters (Ctrl+E)', 'win.effects')
        self.list_btn = Gtk.ToggleButton(icon_name='view-list-symbolic', tooltip_text='Queue (F9)',
                                         focus_on_click=False)
        self.list_btn.add_css_class('flat')
        self.list_btn.set_action_name('win.playlist')

        self.mute = _button('audio-volume-high-symbolic', 'Mute (M)', 'win.mute')
        self.vol = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, width_request=96,
                             focus_on_click=False, valign=Gtk.Align.CENTER)
        self.vol.add_css_class('vol')
        self.vol.set_range(0, 1.25)
        self.vol.set_increments(0.05, 0.1)
        self.vol.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self.vol.set_value(self.settings['volume'])
        self._vol_handler = self.vol.connect('value-changed', lambda s: self.settings.set('volume', round(s.get_value(), 3)))
        self.full = _button('view-fullscreen-symbolic', 'Fullscreen (F)', 'win.fullscreen')
        self.audio_only = Gtk.ToggleButton(icon_name='audio-headphones-symbolic', focus_on_click=False,
                                           tooltip_text='Play as audio only', action_name='win.audio-only')
        self.audio_only.add_css_class('flat')

        # What's playing, shown when the bar sits under the library.
        self.now = Gtk.Button(focus_on_click=False, tooltip_text='Show what is playing', visible=False)
        self.now.add_css_class('flat')
        self.now.add_css_class('now-playing')
        nbox = Gtk.Box(spacing=10)
        self.now_art = Gtk.Image(icon_name='audio-x-generic-symbolic', pixel_size=20)
        self.now_art.add_css_class('now-art')
        nbox.append(self.now_art)
        ntext = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.now_title = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=26)
        self.now_title.add_css_class('heading')
        self.now_sub = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=26)
        self.now_sub.add_css_class('caption')
        self.now_sub.add_css_class('dim-label')
        ntext.append(self.now_title)
        ntext.append(self.now_sub)
        nbox.append(ntext)
        self.now.set_child(nbox)
        self.now.connect('clicked', lambda *_: win.show_page('player'))
        self._now_on = False

        self.seek_row = Gtk.Box(spacing=8)
        self.btn_row = Gtk.Box(spacing=2)
        self.append(self.seek_row)
        self.append(self.btn_row)

        self.settings.connect('changed', self._settings_changed)
        self.player.connect('position', self._on_position)
        self.player.connect('state-changed', self._on_state)
        self.player.connect('tracks-changed', lambda *_: self.refresh_tracks())
        self.layout()
        self._sync_skip_tips()
        self._sync_volume()
        self.refresh_tracks()

    # ---- layout --------------------------------------------------------------------------

    def _clear(self, box):
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def layout(self, docked_override=False):
        mode = 'docked' if docked_override else self.settings['controls']
        for m in ('floating', 'docked', 'minimal'):
            (self.add_css_class if m == mode else self.remove_css_class)(m)
        self._clear(self.seek_row)
        self._clear(self.btn_row)
        if mode == 'minimal':
            self.btn_row.set_visible(False)
            for w in (self.play, self.cur, self.seek, self.end, self.audio_btn, self.subs_btn,
                      self.list_btn, self.mute, self.full):
                self.seek_row.append(w)
            self.seek_row.set_margin_top(0)
            return
        self.btn_row.set_visible(True)
        for w in (self.cur, self.seek, self.end):
            self.seek_row.append(w)
        left = Gtk.Box(spacing=2, hexpand=True, halign=Gtk.Align.START)
        left.append(self.now)
        for w in (self.audio_btn, self.subs_btn, self.speed_btn, self.fx_btn, self.audio_only):
            left.append(w)
        mid = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        for w in (self.prev, self.back, self.play, self.fwd, self.next):
            mid.append(w)
        right = Gtk.Box(spacing=2, hexpand=True, halign=Gtk.Align.END)
        for w in (self.list_btn, self.mute, self.vol, self.full):
            right.append(w)
        for w in (left, mid, right):
            self.btn_row.append(w)
        self.seek_row.set_margin_top(2)
        # Keep the play button in the very middle, whatever sits either side.
        sizer = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        sizer.add_widget(left)
        sizer.add_widget(right)
        self._sizer = sizer

    # ---- menus ---------------------------------------------------------------------------

    def _speed_menu(self):
        menu = Gio.Menu()
        sec = Gio.Menu()
        for r in SPEEDS:
            sec.append(speed_label(r), f'win.speed::{r:g}')
        menu.append_section(None, sec)
        more = Gio.Menu()
        more.append('Slower', 'win.speed-down')
        more.append('Faster', 'win.speed-up')
        more.append('Normal speed', 'win.speed-reset')
        menu.append_section(None, more)
        return menu

    def show_now_playing(self, on):
        self._now_on = on
        self.now.set_visible(on and self.player.uri is not None)
        for w in (self.subs_btn, self.speed_btn, self.fx_btn):
            w.set_visible(not on)
        self.update_now()

    def update_now(self, texture=None):
        self.now_title.set_label(self.win.media_title() if self.player.uri else '')
        sub = self.player.tags.get('artist') or ''
        self.now_sub.set_label(sub)
        self.now_sub.set_visible(bool(sub))
        if texture is not None:
            self.now_art.set_from_paintable(texture)
            self.now_art.set_pixel_size(40)
        elif not self.player.tags.get('image'):
            self.now_art.set_from_icon_name('video-x-generic-symbolic' if self.player.has_video
                                            else 'audio-x-generic-symbolic')
            self.now_art.set_pixel_size(20)

    def refresh_tracks(self):
        audio = self.player.audio_tracks()
        menu = Gio.Menu()
        if audio:
            for i, name in enumerate(audio):
                menu.append(name, f'win.audio-track({i})')
        else:
            menu.append('No audio tracks', 'win.none')
        self.audio_btn.set_menu_model(menu)
        self.audio_btn.set_sensitive(len(audio) > 0)
        self.audio_btn.set_tooltip_text('Audio track (B)' if len(audio) else 'Audio track: open a file with sound first')
        self.audio_btn.set_visible(len(audio) > 1 or self.settings['controls'] != 'minimal')

        texts = self.player.text_tracks()
        menu = Gio.Menu()
        tracks = Gio.Menu()
        tracks.append('Off', 'win.subtitle-track(-1)')
        for i, name in enumerate(texts):
            tracks.append(name, f'win.subtitle-track({i})')
        menu.append_section(None, tracks)
        extra = Gio.Menu()
        extra.append('Add subtitle file…', 'win.load-subtitle')
        menu.append_section(None, extra)
        self.subs_btn.set_menu_model(menu)
        self.subs_btn.set_sensitive(self.player.uri is not None)
        self.subs_btn.set_tooltip_text('Subtitles (V)' if self.player.uri else 'Subtitles: open a video first')
        self.win.sync_track_states()

    # ---- updates -------------------------------------------------------------------------

    def _settings_changed(self, _s, key):
        if key == 'controls':
            self.refresh_tracks()  # the window re-places and re-lays the bar
        elif key in ('volume', 'muted'):
            self._sync_volume()
        elif key == 'skip_seconds':
            self._sync_skip_tips()
        elif key == 'time_mode':
            self._show_times(self._pos, self._duration)
        elif key == 'speed':
            self.speed_btn.set_tooltip_text(f"Speed: {speed_label(self.settings['speed'])} ([ and ])")
            if self.settings['speed'] != 1.0:
                self.speed_btn.add_css_class('accent')
            else:
                self.speed_btn.remove_css_class('accent')

    def _sync_skip_tips(self):
        n = self.settings['skip_seconds']
        self.back.set_tooltip_text(f'Back {n} seconds (Left)')
        self.fwd.set_tooltip_text(f'Forward {n} seconds (Right)')

    def _sync_volume(self):
        v = self.settings['volume']
        muted = self.settings['muted']
        if abs(self.vol.get_value() - v) > 0.001:
            self.vol.handler_block(self._vol_handler)
            self.vol.set_value(v)
            self.vol.handler_unblock(self._vol_handler)
        if muted or v <= 0.001:
            icon = 'audio-volume-muted-symbolic'
        elif v < 0.34:
            icon = 'audio-volume-low-symbolic'
        elif v < 0.67:
            icon = 'audio-volume-medium-symbolic'
        else:
            icon = 'audio-volume-high-symbolic'
        self.mute.set_icon_name(icon)
        self.mute.set_tooltip_text(('Unmute' if muted else 'Mute') + f' (M) · Volume {round(v * 100)}%')

    def _on_state(self, _p, state):
        playing = state == 'playing'
        self.play.set_icon_name('media-playback-pause-symbolic' if playing else 'media-playback-start-symbolic')
        self.play.set_tooltip_text('Pause (Space)' if playing else 'Play (Space)')

    def _on_position(self, _p, pos, dur):
        self._pos, self._duration = pos, dur
        if self._dragging:
            return
        self.seek.set_range(0, max(dur, 0.001))
        self.seek.set_value(pos)
        self.seek.set_sensitive(dur > 0)
        self._show_times(pos, dur)

    def _show_times(self, pos, dur):
        hours = dur >= 3600
        self.cur.set_label(fmt_time(pos, hours))
        if not dur:
            self.end_label.set_label('–:––' if self.player.uri else '0:00')
        elif self.settings['time_mode'] == 'remaining':
            self.end_label.set_label('-' + fmt_time(dur - pos, hours))
        else:
            self.end_label.set_label(fmt_time(dur, hours))

    def _on_seek_change(self, _scale, _scroll, value):
        if not self._duration:
            return True
        self._dragging = True
        value = max(0.0, min(value, self._duration))
        self.seek.set_value(value)
        self._show_times(value, self._duration)
        self.player.seek(value, fast=True)
        if self._seek_timer:
            GLib.source_remove(self._seek_timer)
        self._seek_timer = GLib.timeout_add(220, self._finish_seek, value)
        return True

    def _finish_seek(self, value):
        self._seek_timer = 0
        self.player.seek(value)
        self._dragging = False
        return False

    def _seek_tip(self, scale, x, _y, _kb, tooltip):
        if not self._duration:
            return False
        w = scale.get_width()
        frac = max(0.0, min(1.0, (x - 7) / max(1, w - 14)))
        tooltip.set_text(fmt_time(frac * self._duration, self._duration >= 3600))
        return True

    def is_busy(self):
        """True while the pointer is on the bar or one of its menus is open."""
        if self._dragging:
            return True
        for b in (self.audio_btn, self.subs_btn, self.speed_btn):
            if b.get_active():
                return True
        return False
