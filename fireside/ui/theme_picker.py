"""Theme and accent swatches for the top of the main menu, so changing the look is one click."""
import math

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # noqa: E402

from .. import themes  # noqa: E402


def _rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Swatch(Gtk.Button):
    SIZE = 30

    def __init__(self, tip, draw, on_click):
        super().__init__(tooltip_text=tip, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class('swatch')
        area = Gtk.DrawingArea(content_width=self.SIZE, content_height=self.SIZE)
        area.set_draw_func(lambda _a, cr, w, h: draw(cr, w, h))
        self.area = area
        self.set_child(area)
        self.connect('clicked', lambda *_: on_click())


class ThemePicker(Gtk.Box):
    def __init__(self, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=4,
                         margin_start=6, margin_end=6)
        self.settings = settings
        lab = Gtk.Label(label='Theme', xalign=0, margin_start=4)
        lab.add_css_class('caption-heading')
        lab.add_css_class('dim-label')
        self.append(lab)
        self.theme_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                                      min_children_per_line=5, column_spacing=4, row_spacing=4, homogeneous=True)
        self.theme_btns = {}
        for tid, t in themes.THEMES.items():
            b = Swatch(t['label'], lambda cr, w, h, tid=tid: self._draw_theme(cr, w, h, tid),
                       lambda tid=tid: settings.set('theme', tid))
            self.theme_btns[tid] = b
            self.theme_grid.append(b)
        self.append(self.theme_grid)

        lab = Gtk.Label(label='Accent color', xalign=0, margin_start=4, margin_top=4)
        lab.add_css_class('caption-heading')
        lab.add_css_class('dim-label')
        self.append(lab)
        self.accent_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                                       min_children_per_line=5, column_spacing=4, row_spacing=4, homogeneous=True)
        self.accent_btns = {}
        for color, name in themes.ACCENTS:
            b = Swatch(name, lambda cr, w, h, c=color: self._draw_accent(cr, w, h, c),
                       lambda c=color: settings.set('accent', c))
            self.accent_btns[color] = b
            self.accent_grid.append(b)
        self.append(self.accent_grid)
        self._sig = settings.connect('changed', lambda _s, k: self._sync() if k in ('theme', 'accent') else None)
        self._sync()

    def _draw_theme(self, cr, w, h, tid):
        win, _head, accent, _fg, _stage = themes.swatch(tid)
        r = w / 2 - 1
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(win))
        cr.fill_preserve()
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.45)
        cr.set_line_width(1)
        cr.stroke()
        cr.arc(w / 2, h / 2, r - 1, -math.pi / 4, 3 * math.pi / 4)
        cr.close_path()
        cr.set_source_rgb(*_rgb(accent))
        cr.fill()

    def _draw_accent(self, cr, w, h, color):
        r = w / 2 - 1
        if color:
            cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
            cr.set_source_rgb(*_rgb(color))
            cr.fill()
            return
        # The theme's own color: half the theme's accent, half its window color.
        win, _head, accent, _fg, _stage = themes.swatch(self.settings['theme'])
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.set_source_rgb(*_rgb(accent))
        cr.fill()
        cr.arc(w / 2, h / 2, r, -math.pi / 4, 3 * math.pi / 4)
        cr.close_path()
        cr.set_source_rgb(*_rgb(win))
        cr.fill()

    def _sync(self):
        for tid, b in self.theme_btns.items():
            (b.add_css_class if tid == self.settings['theme'] else b.remove_css_class)('selected')
        for c, b in self.accent_btns.items():
            (b.add_css_class if c == self.settings['accent'] else b.remove_css_class)('selected')
            b.area.queue_draw()
