"""The mini player: a small window with the picture or cover art and the play buttons.

GNOME doesn't let apps put themselves above other windows, so the tooltip points to the
window menu (right-click the mini player) where Always on Top lives.
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Pango  # noqa: E402


def _btn(icon, tip, action=None, cb=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, focus_on_click=False, valign=Gtk.Align.CENTER)
    b.add_css_class('flat')
    b.add_css_class('circular')
    if action:
        b.set_action_name(action)
    if cb:
        b.connect('clicked', lambda *_: cb())
    return b


class MiniPlayer(Adw.Window):
    def __init__(self, win):
        super().__init__(application=win.get_application(), title='Fireside', default_width=380,
                         default_height=112, resizable=False)
        self.win = win
        self.player = win.player
        self.add_css_class('mini-player')
        # The window's actions live on the main window; borrow them so the buttons work here too.
        self.insert_action_group('win', win)

        handle = Gtk.WindowHandle()
        handle.set_tooltip_text('Drag to move. Right-click for Always on Top')
        row = Gtk.Box(spacing=12, margin_start=10, margin_end=8, margin_top=10, margin_bottom=10)
        self.art = Gtk.Overlay(width_request=92, height_request=92, valign=Gtk.Align.CENTER)
        self.art.add_css_class('mini-art')
        self.art.set_overflow(Gtk.Overflow.HIDDEN)
        self.art_icon = Gtk.Image(icon_name='audio-x-generic-symbolic', pixel_size=32)
        self.art.set_child(self.art_icon)
        self.art_pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self.art.add_overlay(self.art_pic)
        row.append(self.art)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        top = Gtk.Box(spacing=4)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        self.title.add_css_class('heading')
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)
        self.sub.add_css_class('caption')
        self.sub.add_css_class('dim-label')
        text.append(self.title)
        text.append(self.sub)
        top.append(text)
        top.append(_btn('view-fullscreen-symbolic', 'Back to the full window (Ctrl+M)', cb=self.back))
        col.append(top)
        self.bar = Gtk.ProgressBar(margin_top=6, margin_bottom=4)
        self.bar.add_css_class('mini-progress')
        col.append(self.bar)
        btns = Gtk.Box(spacing=2, halign=Gtk.Align.START)
        btns.append(_btn('media-skip-backward-symbolic', 'Previous', 'win.previous'))
        self.play = _btn('media-playback-start-symbolic', 'Play', 'win.toggle-play')
        self.play.remove_css_class('flat')
        self.play.add_css_class('suggested-action')
        btns.append(self.play)
        btns.append(_btn('media-skip-forward-symbolic', 'Next', 'win.next'))
        btns.append(_btn('audio-volume-high-symbolic', 'Mute (M)', 'win.mute'))
        col.append(btns)
        row.append(col)
        handle.set_child(row)
        self.set_content(handle)

        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        self.add_controller(keys)

        self._ids = [
            self.player.connect('state-changed', lambda *_: self._sync_state()),
            self.player.connect('position', self._position),
            self.player.connect('tags-changed', lambda *_: self.refresh()),
            self.player.connect('track-changed', lambda *_: self.refresh()),
            self.player.connect('video-changed', lambda *_: self.refresh()),
        ]
        self.connect('close-request', self._closing)
        self.refresh()
        self._sync_state()

    def refresh(self):
        p = self.player
        self.title.set_label(self.win.media_title() if p.uri else 'Nothing playing')
        self.sub.set_label(p.tags.get('artist') or '')
        if p.has_video:
            self.art_pic.set_paintable(p.paintable)
            self.art_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            self.art_pic.set_visible(True)
        else:
            cover = self.win.hearth.cover.get_paintable()
            self.art_pic.set_paintable(cover)
            self.art_pic.set_content_fit(Gtk.ContentFit.COVER)
            self.art_pic.set_visible(cover is not None)
        self.art_icon.set_visible(not self.art_pic.get_visible())

    def _sync_state(self):
        playing = self.player.state == 'playing'
        self.play.set_icon_name('media-playback-pause-symbolic' if playing else 'media-playback-start-symbolic')
        self.play.set_tooltip_text('Pause (Space)' if playing else 'Play (Space)')

    def _position(self, _p, pos, dur):
        self.bar.set_fraction(min(1.0, pos / dur) if dur else 0.0)

    def _key(self, _c, keyval, _code, mods):
        from gi.repository import Gdk
        name = Gdk.keyval_name(keyval) or ''
        ctrl = mods & Gdk.ModifierType.CONTROL_MASK
        if name == 'm' and ctrl or name == 'Escape':
            self.back()
            return True
        action = {'space': 'toggle-play', 'n': 'next', 'p': 'previous', 'm': 'mute',
                  'Left': 'skip-back', 'Right': 'skip-forward'}.get(name)
        if action and not ctrl:
            self.win.activate_action('win.' + action)
            return True
        return False

    def back(self):
        self.close()

    def _closing(self, *_):
        for i in self._ids:
            self.player.disconnect(i)
        self._ids = []
        self.art_pic.set_paintable(None)
        self.win.mini = None
        self.win.set_visible(True)
        self.win.present()
        return False
