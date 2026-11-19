"""What shows while music plays: a slow warm glow with drifting embers, the cover art and the title."""
import math
import random

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .. import themes  # noqa: E402


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Embers(Gtk.DrawingArea):
    def __init__(self, settings, style):
        super().__init__(hexpand=True, vexpand=True)
        self.settings = settings
        self.style = style
        self.running = False
        self.want = False
        self.connect('map', lambda *_: self.set_running(self.want))
        self.connect('unmap', lambda *_: self._halt())
        self.sparks = []
        self.t = 0.0
        self._tick = 0
        self._last = None
        self.set_draw_func(self._draw)

    def set_running(self, want):
        self.want = want
        on = want and self.settings['backdrop'] == 'embers' and self.get_mapped()
        if on == self.running:
            return
        if on:
            self.running = True
            self._last = None
            self._tick = self.add_tick_callback(self._advance)
        else:
            self._halt()
        self.queue_draw()

    def _halt(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self.running = False

    def _advance(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        dt = 0 if self._last is None else min(0.1, now - self._last)
        self._last = now
        self.t += dt
        w, h = self.get_width(), self.get_height()
        if w and h and len(self.sparks) < 46 and random.random() < dt * 14:
            self.sparks.append({
                'x': w / 2 + random.gauss(0, w * 0.12), 'y': h + 6,
                'vy': random.uniform(26, 70), 'sway': random.uniform(0.6, 1.8),
                'phase': random.uniform(0, 6.28), 'r': random.uniform(1.2, 3.2),
                'life': 0.0, 'span': random.uniform(4, 9),
            })
        for s in self.sparks:
            s['life'] += dt
            s['y'] -= s['vy'] * dt
            s['x'] += math.sin(self.t * s['sway'] + s['phase']) * 14 * dt
        self.sparks = [s for s in self.sparks if s['life'] < s['span'] and s['y'] > -10]
        self.queue_draw()
        return True

    def _draw(self, _area, cr, w, h):
        pal, _dark = themes.palette(self.settings['theme'], self.style.get_dark())
        stage = _rgb(pal['stage'])
        accent = _rgb(self.settings['accent'] or pal['accent_bg'])
        light = _rgb(pal['accent'])
        cr.set_source_rgb(*stage)
        cr.paint()
        if self.settings['backdrop'] == 'plain':
            return
        import cairo
        # Two soft glows that breathe slowly, low in the frame like a fire seen across a room.
        breathe = 0.5 + 0.5 * math.sin(self.t * 0.7)
        for cx, cy, rad, col, a in ((w * 0.5, h * 1.05, max(w, h) * (0.62 + 0.04 * breathe), accent, 0.55),
                                    (w * (0.42 + 0.05 * math.sin(self.t * 0.31)), h * 0.98,
                                     max(w, h) * 0.38, light, 0.22 + 0.08 * breathe)):
            g = cairo.RadialGradient(cx, cy, 0, cx, cy, rad)
            g.add_color_stop_rgba(0, *col, a)
            g.add_color_stop_rgba(1, *col, 0)
            cr.set_source(g)
            cr.paint()
        for s in self.sparks:
            fade = math.sin(math.pi * min(1.0, s['life'] / s['span']))
            g = cairo.RadialGradient(s['x'], s['y'], 0, s['x'], s['y'], s['r'] * 3)
            g.add_color_stop_rgba(0, *light, 0.9 * fade)
            g.add_color_stop_rgba(1, *light, 0)
            cr.set_source(g)
            cr.arc(s['x'], s['y'], s['r'] * 3, 0, 2 * math.pi)
            cr.fill()


class Hearth(Gtk.Overlay):
    def __init__(self, settings, style):
        super().__init__()
        self.settings = settings
        self.embers = Embers(settings, style)
        self.set_child(self.embers)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, margin_bottom=60)
        self.cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=240, height_request=240,
                                 halign=Gtk.Align.CENTER, visible=False, can_shrink=True)
        self.cover.add_css_class('hearth-cover')
        self.cover.set_overflow(Gtk.Overflow.HIDDEN)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40,
                               margin_top=14)
        self.title.add_css_class('hearth-title')
        self.sub = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50)
        self.sub.add_css_class('hearth-sub')
        for w in (self.cover, self.title, self.sub):
            box.append(w)
        self.add_overlay(box)
        settings.connect('changed', self._changed)

    def _changed(self, _s, key):
        if key in ('backdrop', 'theme', 'accent'):
            self.embers.queue_draw()
            if key == 'backdrop':
                self.embers.set_running(self.embers.want)
                self.cover.set_visible(self.cover.get_paintable() is not None and
                                       self.settings['backdrop'] != 'plain')

    def show_media(self, title, artist, album, image_bytes):
        self.title.set_label(title or '')
        self.sub.set_label(' · '.join(x for x in (artist, album) if x))
        self.sub.set_visible(bool(artist or album))
        texture = None
        if image_bytes:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write_bytes(image_bytes)
                loader.close()
                texture = Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
            except GLib.Error:
                texture = None
        self.cover.set_paintable(texture)
        self.cover.set_visible(texture is not None and self.settings['backdrop'] != 'plain')
