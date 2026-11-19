"""The playlist side panel."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402


def fmt_time(seconds, force_hours=False):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h or force_hours else f'{m}:{s:02d}'


def icon_button(icon, tip, action=None, toggle=False):
    btn = (Gtk.ToggleButton if toggle else Gtk.Button)(icon_name=icon, tooltip_text=tip)
    btn.add_css_class('flat')
    if action:
        btn.set_action_name(action)
    return btn


class Row(Gtk.ListBoxRow):
    def __init__(self, view, index, item):
        super().__init__()
        self.index = index
        box = Gtk.Box(spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label=item.title, xalign=0, ellipsize=3)
        self.title.add_css_class('pl-title')
        self.title.set_tooltip_text(item.title)
        meta = ' · '.join(x for x in (item.artist, fmt_time(item.duration) if item.duration else '') if x)
        self.meta = Gtk.Label(label=meta, xalign=0, ellipsize=3, visible=bool(meta))
        self.meta.add_css_class('pl-meta')
        text.append(self.title)
        text.append(self.meta)
        box.append(text)
        rm = icon_button('window-close-symbolic', 'Remove from the queue')
        rm.set_valign(Gtk.Align.CENTER)
        rm.connect('clicked', lambda *_: view.playlist.remove(self.index))
        box.append(rm)
        self.set_child(box)

        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect('prepare', lambda *_: Gdk.ContentProvider.new_for_value(GObject.Value(int, self.index)))
        self.add_controller(drag)
        drop = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        drop.connect('enter', lambda *_: (self.add_css_class('drop-above'), Gdk.DragAction.MOVE)[1])
        drop.connect('leave', lambda *_: self.remove_css_class('drop-above'))
        drop.connect('drop', self._drop, view)
        self.add_controller(drop)

    def _drop(self, target, value, _x, _y, view):
        self.remove_css_class('drop-above')
        view.playlist.move(int(value), self.index)
        return True


class PlaylistView(Gtk.Box):
    def __init__(self, playlist, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.playlist = playlist
        self.settings = settings

        head = Gtk.Box(spacing=2, margin_start=14, margin_end=8, margin_top=10, margin_bottom=6)
        label = Gtk.Label(label='Queue', xalign=0, hexpand=True)
        label.add_css_class('playlist-header')
        head.append(label)
        self.count = Gtk.Label()
        self.count.add_css_class('dim-label')
        self.count.set_margin_end(6)
        head.append(self.count)
        self.append(head)

        tools = Gtk.Box(spacing=2, margin_start=8, margin_end=8, margin_bottom=6)
        tools.append(icon_button('list-add-symbolic', 'Add files', 'win.add-files'))
        tools.append(icon_button('folder-open-symbolic', 'Add a folder', 'win.add-folder'))
        self.shuffle = icon_button('media-playlist-shuffle-symbolic', 'Shuffle', toggle=True)
        self.shuffle.set_active(settings['shuffle'])
        self.shuffle.connect('toggled', lambda b: settings.set('shuffle', b.get_active()))
        tools.append(self.shuffle)
        self.repeat = Gtk.Button()
        self.repeat.add_css_class('flat')
        self.repeat.connect('clicked', self._cycle_repeat)
        tools.append(self.repeat)
        tools.append(Gtk.Box(hexpand=True))
        tools.append(icon_button('document-save-symbolic', 'Save the queue as a playlist (Ctrl+S)', 'win.save-playlist'))
        tools.append(icon_button('edit-clear-all-symbolic', 'Clear the queue', 'win.clear-playlist'))
        self.append(tools)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=False)
        self.list.add_css_class('playlist')
        self.list.add_css_class('navigation-sidebar')
        self.list.connect('row-activated', lambda _l, row: self.activate_action('win.play-index',
                                                                                 GObject.Variant('i', row.index)))
        self.empty = Adw.StatusPage(icon_name='view-list-symbolic', title='Nothing queued',
                                    description='Drop files here or add them with the buttons above.')
        self.empty.add_css_class('compact')
        self.stack = Gtk.Stack()
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
        self.stack.add_named(scroller, 'list')
        self.stack.add_named(self.empty, 'empty')
        self.append(self.stack)

        playlist.connect('changed', lambda *_: self.rebuild())
        playlist.connect('current-changed', lambda *_: self._mark_current())
        playlist.connect('item-updated', self._updated)
        settings.connect('changed', self._settings_changed)
        self._sync_repeat()
        self.rebuild()

    def _settings_changed(self, _s, key):
        if key == 'repeat':
            self._sync_repeat()
        elif key == 'shuffle' and self.shuffle.get_active() != self.settings['shuffle']:
            self.shuffle.set_active(self.settings['shuffle'])

    def _cycle_repeat(self, *_):
        nxt = {'off': 'all', 'all': 'one', 'one': 'off'}[self.settings['repeat']]
        self.settings.set('repeat', nxt)

    def _sync_repeat(self):
        mode = self.settings['repeat']
        icon, tip = {'off': ('media-playlist-consecutive-symbolic', 'Repeat: off'),
                     'all': ('media-playlist-repeat-symbolic', 'Repeat: whole queue'),
                     'one': ('media-playlist-repeat-song-symbolic', 'Repeat: this item')}[mode]
        self.repeat.set_icon_name(icon)
        self.repeat.set_tooltip_text(tip + '. Click to change')
        if mode == 'off':
            self.repeat.remove_css_class('accent')
        else:
            self.repeat.add_css_class('accent')

    def rebuild(self):
        self.list.remove_all()
        for i, item in enumerate(self.playlist.items):
            self.list.append(Row(self, i, item))
        n = len(self.playlist)
        self.count.set_label((f'{n} item' + ('' if n == 1 else 's')) if n else '')
        self.stack.set_visible_child_name('list' if n else 'empty')
        self._mark_current()

    def _updated(self, _p, index):
        row = self.list.get_row_at_index(index)
        if row:
            item = self.playlist.items[index]
            row.title.set_label(item.title)
            row.title.set_tooltip_text(item.title)
            meta = ' · '.join(x for x in (item.artist, fmt_time(item.duration) if item.duration else '') if x)
            row.meta.set_label(meta)
            row.meta.set_visible(bool(meta))

    def _mark_current(self):
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            if i == self.playlist.current:
                row.add_css_class('current')
            else:
                row.remove_css_class('current')
            i += 1
