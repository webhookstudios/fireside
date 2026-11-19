"""Preferences: themes, accent, control bar style and playback choices."""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import themes  # noqa: E402
from ..settings import DEFAULTS  # noqa: E402

LOOK_KEYS = ('theme', 'scheme', 'accent', 'controls', 'radius', 'seek_height', 'controls_opacity',
             'hide_delay', 'backdrop', 'big_play', 'playlist_side')


def _rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _round_rect(cr, x, y, w, h, r):
    import math
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class ThemeCard(Gtk.Button):
    """A small drawing of the player in a theme's colors."""

    def __init__(self, theme_id, settings):
        super().__init__(tooltip_text=themes.THEMES[theme_id]['label'])
        self.theme_id = theme_id
        self.add_css_class('flat')
        self.add_css_class('theme-card')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        art = Gtk.DrawingArea(content_width=132, content_height=80)
        art.set_draw_func(self._draw)
        box.append(art)
        name = Gtk.Label(label=themes.THEMES[theme_id]['label'])
        name.add_css_class('theme-name')
        box.append(name)
        self.set_child(box)
        self.connect('clicked', lambda *_: settings.set('theme', theme_id))

    def _draw(self, _a, cr, w, h):
        win, head, accent, fg, stage = themes.swatch(self.theme_id)
        _round_rect(cr, 0, 0, w, h, 10)
        cr.clip()
        cr.set_source_rgb(*_rgb(win))
        cr.paint()
        cr.set_source_rgb(*_rgb(head))
        cr.rectangle(0, 0, w, 16)
        cr.fill()
        cr.set_source_rgb(*_rgb(stage))
        cr.rectangle(6, 22, w - 12, h - 28)
        cr.fill()
        # control bar
        cr.set_source_rgba(*_rgb(win), 0.92)
        _round_rect(cr, 14, h - 24, w - 28, 14, 5)
        cr.fill()
        cr.set_source_rgba(*_rgb(fg), 0.25)
        cr.rectangle(22, h - 18, w - 44, 2.5)
        cr.fill()
        cr.set_source_rgb(*_rgb(accent))
        cr.rectangle(22, h - 18, (w - 44) * 0.42, 2.5)
        cr.fill()
        cr.arc(w / 2, 22 + (h - 28) / 2 - 6, 9, 0, 6.2832)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cx, cy = w / 2 + 1, 22 + (h - 28) / 2 - 6
        cr.move_to(cx - 3, cy - 4.5)
        cr.line_to(cx + 4.5, cy)
        cr.line_to(cx - 3, cy + 4.5)
        cr.close_path()
        cr.fill()


class PrefsDialog(Adw.PreferencesDialog):
    def __init__(self, settings):
        super().__init__(title='Preferences', search_enabled=False)
        self.settings = settings
        self.add(self._look_page())
        self.add(self._playback_page())
        self.add(self._subs_page())
        self._sig = settings.connect('changed', self._changed)
        self.connect('closed', lambda *_: settings.disconnect(self._sig))
        self._sync_cards()

    # ---- helpers -------------------------------------------------------------------------

    def _switch(self, key, title, subtitle=None):
        row = Adw.SwitchRow(title=title, active=self.settings[key])
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect('notify::active', lambda r, _p: self.settings.set(key, r.get_active()))
        return row

    def _combo(self, key, title, options, subtitle=None):
        ids = [o[0] for o in options]
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([o[1] for o in options]))
        if subtitle:
            row.set_subtitle(subtitle)
        cur = self.settings[key]
        row.set_selected(ids.index(cur) if cur in ids else 0)
        row.connect('notify::selected', lambda r, _p: self.settings.set(key, ids[r.get_selected()]))
        row._ids = ids
        return row

    def _spin(self, key, title, lo, hi, step, digits=0, subtitle=None, scale=1):
        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step, page_increment=step * 4,
                             value=self.settings[key] * scale)
        row = Adw.SpinRow(title=title, adjustment=adj, digits=digits)
        if subtitle:
            row.set_subtitle(subtitle)
        cast = int if isinstance(DEFAULTS[key], int) else float

        def changed(r, _p):
            val = r.get_value() / scale
            self.settings.set(key, cast(round(val)) if cast is int else round(val, 3))
        row.connect('notify::value', changed)
        return row

    # ---- pages ---------------------------------------------------------------------------

    def _look_page(self):
        page = Adw.PreferencesPage(title='Appearance', icon_name='applications-graphics-symbolic')

        g = Adw.PreferencesGroup(title='Theme')
        self.cards = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                 max_children_per_line=5, min_children_per_line=2, column_spacing=4,
                                 row_spacing=4)
        for tid in themes.THEMES:
            self.cards.append(ThemeCard(tid, self.settings))
        g.add(self.cards)
        self.scheme = self._combo('scheme', 'Light or dark',
                                  [('system', 'Follow system'), ('light', 'Light'), ('dark', 'Dark')])
        page.add(g)
        g = Adw.PreferencesGroup()
        g.add(self.scheme)
        page.add(g)

        g = Adw.PreferencesGroup(title='Accent color')
        dots = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=10,
                           min_children_per_line=5, column_spacing=6, row_spacing=6, margin_top=4)
        self.dots = []
        for color, name in themes.ACCENTS:
            b = Gtk.Button(tooltip_text=name, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            b.add_css_class('accent-dot')
            area = Gtk.DrawingArea(content_width=28, content_height=28)
            area.set_draw_func(self._draw_dot, color)
            b.set_child(area)
            b.set_overflow(Gtk.Overflow.HIDDEN)
            b.connect('clicked', lambda _b, c: self.settings.set('accent', c), color)
            b._color = color
            dots.append(b)
            self.dots.append(b)
        g.add(dots)
        page.add(g)

        g = Adw.PreferencesGroup(title='Controls')
        g.add(self._combo('controls', 'Control bar', [
            ('floating', 'Floating'), ('docked', 'Docked'), ('minimal', 'Minimal')],
            'Floating hovers over the video, docked sits under it, minimal is one thin row'))
        g.add(self._spin('radius', 'Corner roundness', 0, 28, 2, subtitle='In pixels'))
        g.add(self._spin('seek_height', 'Seek bar thickness', 2, 14, 1, subtitle='In pixels'))
        g.add(self._spin('controls_opacity', 'Control bar opacity', 40, 100, 5, subtitle='In percent', scale=100))
        g.add(self._spin('hide_delay', 'Hide controls after', 1, 15, 1, subtitle='Seconds without moving the mouse'))
        g.add(self._switch('big_play', 'Big play button', 'Show a large play button over paused video'))
        g.add(self._combo('playlist_side', 'Playlist side', [('right', 'Right'), ('left', 'Left')]))
        page.add(g)

        g = Adw.PreferencesGroup(title='Music')
        g.add(self._combo('backdrop', 'Backdrop', [
            ('embers', 'Drifting embers'), ('cover', 'Cover art on a warm glow'), ('plain', 'Plain')],
            'What fills the window when a file has no picture'))
        page.add(g)

        g = Adw.PreferencesGroup()
        reset = Adw.ButtonRow(title='Reset appearance')
        reset.connect('activated', self._reset_look)
        g.add(reset)
        page.add(g)
        return page

    def _playback_page(self):
        page = Adw.PreferencesPage(title='Playback', icon_name='media-playback-start-symbolic')
        g = Adw.PreferencesGroup(title='Playing')
        g.add(self._switch('resume', 'Resume where you left off',
                           'Long files start again from the spot you stopped'))
        g.add(self._switch('autoplay_next', 'Play the next item', 'Move on through the playlist by itself'))
        g.add(self._switch('start_fullscreen', 'Start videos in fullscreen'))
        g.add(self._switch('inhibit', 'Keep the screen awake', 'While a video is playing'))
        g.add(self._spin('skip_seconds', 'Skip length', 2, 120, 1, subtitle='Seconds for the skip buttons and arrow keys'))
        page.add(g)

        g = Adw.PreferencesGroup(title='Picture')
        g.add(self._combo('fit', 'Video size', [
            ('contain', 'Fit to window'), ('cover', 'Fill the window (crop edges)'),
            ('fill', 'Stretch to window'), ('original', 'Original size')]))
        page.add(g)

        g = Adw.PreferencesGroup(title='Snapshots')
        self.snap_row = Adw.ActionRow(title='Save snapshots in', activatable=True)
        self._show_snap_dir()
        btn = Gtk.Button(icon_name='folder-open-symbolic', tooltip_text='Choose a folder', valign=Gtk.Align.CENTER)
        btn.add_css_class('flat')
        btn.connect('clicked', self._choose_dir)
        self.snap_row.add_suffix(btn)
        self.snap_row.set_activatable_widget(btn)
        g.add(self.snap_row)
        page.add(g)
        return page

    def _subs_page(self):
        page = Adw.PreferencesPage(title='Subtitles', icon_name='media-view-subtitles-symbolic')
        g = Adw.PreferencesGroup(title='Subtitles')
        g.add(self._switch('sub_auto', 'Find subtitle files',
                           'Load a subtitle file with the same name as the video'))
        row = Adw.ActionRow(title='Font')
        fbtn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER, use_size=False,
                                    level=Gtk.FontLevel.FACE)
        fbtn.set_font_desc(Pango.FontDescription.from_string(self.settings['sub_font']))
        fbtn.connect('notify::font-desc', lambda b, _p: self.settings.set(
            'sub_font', b.get_font_desc().to_string() if b.get_font_desc() else DEFAULTS['sub_font']))
        row.add_suffix(fbtn)
        g.add(row)
        g.add(self._spin('sub_size', 'Size', 10, 72, 1, subtitle='In points'))
        page.add(g)
        return page

    # ---- state ---------------------------------------------------------------------------

    def _reset_look(self, *_):
        win = self.get_root()
        self.settings.reset(LOOK_KEYS)
        self.close()
        if win:
            GLib.idle_add(lambda: win.get_application().activate_action('preferences') and False)

    def _draw_dot(self, _a, cr, w, h, color):
        if color:
            cr.set_source_rgb(*_rgb(color))
            cr.arc(w / 2, h / 2, w / 2, 0, 6.2832)
            cr.fill()
        else:
            # The theme's own color: a dot split between the current theme's accent and its window.
            win, _head, accent, _fg, _stage = themes.swatch(self.settings['theme'])
            cr.set_source_rgb(*_rgb(accent))
            cr.arc(w / 2, h / 2, w / 2, 0, 6.2832)
            cr.fill()
            cr.set_source_rgb(*_rgb(win))
            cr.arc(w / 2, h / 2, w / 2, -0.785, 2.356)
            cr.fill()

    def _sync_cards(self):
        child = self.cards.get_first_child()
        while child:
            card = child.get_child()
            (card.add_css_class if card.theme_id == self.settings['theme'] else card.remove_css_class)('selected')
            child = child.get_next_sibling()
        for d in self.dots:
            (d.add_css_class if d._color == self.settings['accent'] else d.remove_css_class)('selected')
            d.get_child().queue_draw()
        self.scheme.set_sensitive(themes.THEMES[self.settings['theme']]['dark'] is None)
        self.scheme.set_subtitle('' if self.scheme.get_sensitive() else 'Set by the theme')

    def _changed(self, _s, key):
        if key in ('theme', 'accent'):
            self._sync_cards()
        if key in LOOK_KEYS and key not in ('theme', 'accent'):
            pass

    def _show_snap_dir(self):
        d = self.settings['snapshot_dir'] or default_snapshot_dir()
        self.snap_row.set_subtitle(d.replace(os.path.expanduser('~'), '~', 1))

    def _choose_dir(self, *_):
        dlg = Gtk.FileDialog(title='Save snapshots in')
        d = self.settings['snapshot_dir'] or default_snapshot_dir()
        if os.path.isdir(d):
            dlg.set_initial_folder(Gio.File.new_for_path(d))

        def done(dialog, res):
            try:
                f = dialog.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.settings.set('snapshot_dir', f.get_path())
                self._show_snap_dir()
        dlg.select_folder(self.get_root(), None, done)


def default_snapshot_dir():
    pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser('~/Pictures')
    return os.path.join(pics, 'Fireside')
