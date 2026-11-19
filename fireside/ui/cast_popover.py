"""The 'Play on a TV' picker: looks for receivers on the network and starts casting."""
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import cast  # noqa: E402


class CastPopover(Gtk.Popover):
    def __init__(self, win):
        super().__init__(position=Gtk.PositionType.TOP)
        self.win = win
        self.add_css_class('menu')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8, width_request=280)
        head = Gtk.Box(spacing=8)
        title = Gtk.Label(label='Play on a TV or speaker', xalign=0, hexpand=True)
        title.add_css_class('heading')
        head.append(title)
        self.spinner = Adw.Spinner(visible=False)
        head.append(self.spinner)
        self.again = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Search again')
        self.again.add_css_class('flat')
        self.again.connect('clicked', lambda *_: self.scan())
        head.append(self.again)
        box.append(head)

        self.stop_btn = Gtk.Button(label='Stop Casting', visible=False)
        self.stop_btn.add_css_class('destructive-action')
        self.stop_btn.connect('clicked', lambda *_: (self.popdown(), self.win.stop_cast()))
        box.append(self.stop_btn)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class('boxed-list')
        self.list.connect('row-activated', self._chosen)
        box.append(self.list)
        self.hint = Gtk.Label(wrap=True, xalign=0, max_width_chars=36)
        self.hint.add_css_class('caption')
        self.hint.add_css_class('dim-label')
        box.append(self.hint)
        self.set_child(box)
        self._found = []
        self._scanning = False
        self.connect('show', lambda *_: self._shown())

    def _shown(self):
        self.stop_btn.set_visible(self.win.cast is not None)
        if not self._found and not self._scanning:
            self.scan()

    def scan(self):
        if self._scanning:
            return
        self._scanning = True
        self.spinner.set_visible(True)
        self.again.set_sensitive(False)
        self.hint.set_label('Looking on your network…')
        self.list.remove_all()

        def work():
            found = cast.find_dlna()
            found += cast.find_chromecasts()
            GLib.idle_add(self._done, found)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, found):
        self._scanning = False
        self._found = found
        self.spinner.set_visible(False)
        self.again.set_sensitive(True)
        self.list.remove_all()
        for rec in found:
            row = Adw.ActionRow(title=GLib.markup_escape_text(rec.name), activatable=True,
                                subtitle='Chromecast' if rec.kind == 'chromecast' else 'TV or speaker (DLNA)')
            row.add_prefix(Gtk.Image(icon_name='fireside-cast-symbolic'))
            row.receiver = rec
            self.list.append(row)
        self.list.set_visible(bool(found))
        tips = []
        if not found:
            tips.append('Nothing found. Make sure the TV is on and on the same network.')
        if not cast.chromecast_available():
            tips.append('For Chromecast devices, install python3-pychromecast.')
        self.hint.set_label(' '.join(tips))
        self.hint.set_visible(bool(tips))
        return False

    def _chosen(self, _l, row):
        self.popdown()
        self.win.start_cast(row.receiver)
