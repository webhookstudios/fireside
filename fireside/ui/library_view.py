"""The library: home, music, folders with thumbnails, favorite places and saved playlists."""
import os
import random

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..library import kind_of, list_folder, music_dir, videos_dir  # noqa: E402
from ..player import title_for  # noqa: E402
from .playlist_view import fmt_time  # noqa: E402

TILE_W = 196


def _uri(path):
    return Gio.File.new_for_path(path).get_uri()


def _flat(icon, tip, cb=None, action=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
    b.add_css_class('flat')
    if cb:
        b.connect('clicked', lambda *_: cb())
    if action:
        b.set_action_name(action)
    return b


def _page_head(title, subtitle=None):
    box = Gtk.Box(spacing=8, margin_start=24, margin_end=18, margin_top=18, margin_bottom=10)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    t = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
    t.add_css_class('lib-title')
    text.append(t)
    sub = Gtk.Label(label=subtitle or '', xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, visible=bool(subtitle))
    sub.add_css_class('dim-label')
    text.append(sub)
    box.append(text)
    box.title, box.sub = t, sub
    return box


class Tile(Gtk.FlowBoxChild):
    """A picture with a name under it: a folder, a video or a song."""

    def __init__(self, view, path, kind, label=None, meta=None):
        super().__init__()
        self.view, self.path, self.kind = view, path, kind
        self.add_css_class('lib-tile')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=TILE_W)
        h = TILE_W if kind in ('audio', 'album') else round(TILE_W * 9 / 16)
        self.frame = Gtk.Overlay(width_request=TILE_W, height_request=h)
        self.frame.add_css_class('lib-thumb')
        self.frame.set_overflow(Gtk.Overflow.HIDDEN)
        icon = {'folder': 'folder-symbolic', 'audio': 'audio-x-generic-symbolic', 'album': 'media-optical-symbolic',
                'list': 'view-list-symbolic'}.get(kind, 'video-x-generic-symbolic')
        self.icon = Gtk.Image(icon_name=icon, pixel_size=40, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.icon.add_css_class('dim-label')
        self.frame.set_child(self.icon)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self.frame.add_overlay(self.pic)
        if kind in ('video', 'audio'):
            play = Gtk.Image(icon_name='media-playback-start-symbolic', pixel_size=18,
                             halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=8, margin_bottom=8)
            play.add_css_class('lib-play-badge')
            self.frame.add_overlay(play)
        box.append(self.frame)
        name = Gtk.Label(label=label or (os.path.basename(path) if kind == 'folder' else title_for(_uri(path))),
                         xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=20, lines=2, wrap=True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.add_css_class('lib-name')
        box.append(name)
        if meta:
            m = Gtk.Label(label=meta, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            m.add_css_class('dim-label')
            m.add_css_class('caption')
            box.append(m)
        self.set_child(box)
        self.set_tooltip_text(os.path.basename(path) if path else label)

    def show_image(self, image_path):
        try:
            self.pic.set_file(Gio.File.new_for_path(image_path))
            self.pic.set_visible(True)
            self.icon.set_visible(False)
        except GLib.Error:
            pass


class SongRow(Gtk.ListBoxRow):
    def __init__(self, index, title, meta, duration, removable=None):
        super().__init__()
        self.index = index
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        num = Gtk.Label(label=str(index + 1), width_chars=3, xalign=1)
        num.add_css_class('dim-label')
        num.add_css_class('numeric')
        box.append(num)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        t = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(t)
        if meta:
            m = Gtk.Label(label=meta, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            m.add_css_class('dim-label')
            m.add_css_class('caption')
            text.append(m)
        box.append(text)
        if duration:
            d = Gtk.Label(label=fmt_time(duration))
            d.add_css_class('dim-label')
            d.add_css_class('numeric')
            box.append(d)
        if removable:
            box.append(_flat('window-close-symbolic', 'Remove from this playlist', lambda: removable(self.index)))
        self.set_child(box)


class LibraryView(Gtk.Box):
    def __init__(self, win, home):
        super().__init__()
        self.win = win
        self.settings = win.settings
        self.thumbs = win.app.thumbs
        self.music = win.app.music
        self.store = win.app.playlists
        self.folder = None
        self.collection = None   # (kind, name, entries) for an album, artist or playlist page
        self._tiles = {}

        # Sidebar
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.add_css_class('navigation-sidebar')
        self.side.set_header_func(self._side_header)
        self.side.connect('row-activated', self._side_activated)
        side_scroll = Gtk.ScrolledWindow(child=self.side, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         width_request=210)
        side_scroll.add_css_class('lib-sidebar')
        self.append(side_scroll)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Pages
        self.stack = Gtk.Stack(hexpand=True, vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(home, 'home')
        self.stack.add_named(self._folder_page(), 'folder')
        self.stack.add_named(self._music_page(), 'music')
        self.stack.add_named(self._collection_page(), 'collection')
        self.append(self.stack)

        self.thumbs.connect('ready', self._thumb_ready)
        self.music.connect('changed', lambda *_: self._fill_music())
        self.store.connect('changed', lambda *_: self.rebuild_sidebar())
        self.settings.connect('changed', self._settings_changed)
        self.rebuild_sidebar()

    # ---- sidebar -------------------------------------------------------------------------

    def _side_row(self, icon, label, key, section=None, removable=None):
        row = Gtk.ListBoxRow()
        row.key, row.section = key, section
        box = Gtk.Box(spacing=10)
        box.append(Gtk.Image(icon_name=icon))
        lab = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.append(lab)
        if removable:
            rm = _flat('window-close-symbolic', removable[0], removable[1])
            rm.add_css_class('side-remove')
            box.append(rm)
        row.set_child(box)
        row.set_tooltip_text(key[1] if key[0] == 'folder' else label)
        return row

    def rebuild_sidebar(self):
        current = getattr(self.side.get_selected_row(), 'key', None)
        self.side.remove_all()
        rows = [self._side_row('go-home-symbolic', 'Home', ('home', None)),
                self._side_row('folder-music-symbolic', 'Music', ('music', None)),
                self._side_row('folder-videos-symbolic', 'Videos', ('folder', videos_dir()))]
        for path in self.settings['favorites']:
            rows.append(self._side_row('starred-symbolic', os.path.basename(path.rstrip('/')) or path,
                                       ('folder', path), 'Favorites',
                                       ('Remove from favorites', lambda p=path: self.toggle_favorite(p))))
        for name in self.store.names():
            rows.append(self._side_row('view-list-symbolic', name, ('playlist', name), 'Playlists'))
        rows.append(self._side_row('list-add-symbolic', 'New Playlist', ('new-playlist', None),
                                   'Playlists' if not self.store.names() else None))
        if not self.settings['favorites']:
            pass
        for r in rows:
            self.side.append(r)
        for r in rows:
            if r.key == current:
                self.side.select_row(r)

    def _side_header(self, row, before):
        if row.section and (before is None or before.section != row.section):
            lab = Gtk.Label(label=row.section, xalign=0, margin_start=12, margin_top=14, margin_bottom=4)
            lab.add_css_class('heading')
            lab.add_css_class('dim-label')
            row.set_header(lab)
        else:
            row.set_header(None)

    def _side_activated(self, _l, row):
        kind, val = row.key
        if kind == 'home':
            self.show_home()
        elif kind == 'music':
            self.show_music()
        elif kind == 'folder':
            self.show_folder(val)
        elif kind == 'playlist':
            self.show_playlist(val)
        elif kind == 'new-playlist':
            self.win.ask_playlist_name(None, lambda name: (self.store.save(name, []), self.show_playlist(name)))

    def _select(self, key):
        i = 0
        while (row := self.side.get_row_at_index(i)) is not None:
            if row.key == key:
                self.side.select_row(row)
                return
            i += 1
        self.side.unselect_all()

    def _settings_changed(self, _s, key):
        if key == 'favorites':
            self.rebuild_sidebar()
            self._sync_star()
        elif key == 'folder_view' and self.folder:
            self.show_folder(self.folder)

    # ---- pages ---------------------------------------------------------------------------

    def show_home(self):
        self.stack.set_visible_child_name('home')
        self._select(('home', None))

    def show_music(self):
        self.stack.set_visible_child_name('music')
        self._select(('music', None))
        if not self.music.songs and not self.music.scanning:
            self.music.scan()
        self._fill_music()

    # Folder ---------------------------------------------------------------------------------

    def _folder_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.f_head = _page_head('Folder')
        self.f_up = _flat('go-up-symbolic', 'Up one folder', lambda: self.show_folder(os.path.dirname(self.folder)))
        self.f_play = _flat('media-playback-start-symbolic', 'Play everything here',
                            lambda: self.play_folder(shuffle=False))
        self.f_shuffle = _flat('media-playlist-shuffle-symbolic', 'Shuffle everything here',
                               lambda: self.play_folder(shuffle=True))
        self.f_audio = _flat('audio-headphones-symbolic', 'Play everything here as audio',
                             lambda: self.play_folder(shuffle=False, audio=True))
        self.f_queue = _flat('list-add-symbolic', 'Add everything here to the queue',
                             lambda: self.win.open_sources(self._folder_files(), replace=False))
        self.f_star = _flat('non-starred-symbolic', 'Add to favorites', lambda: self.toggle_favorite(self.folder))
        self.f_view = _flat('view-list-symbolic', 'Show as a list', lambda: self.settings.set(
            'folder_view', 'list' if self.settings['folder_view'] == 'grid' else 'grid'))
        for w in (self.f_up, self.f_play, self.f_shuffle, self.f_audio, self.f_queue, self.f_star, self.f_view):
            self.f_head.append(w)
        page.append(self.f_head)
        self.f_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True,
                                  homogeneous=False, column_spacing=14, row_spacing=18, valign=Gtk.Align.START,
                                  margin_start=24, margin_end=24, margin_bottom=24, max_children_per_line=12)
        self.f_grid.connect('child-activated', lambda _f, tile: self._open_tile(tile))
        self.f_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=24, margin_end=24,
                                  margin_bottom=24, valign=Gtk.Align.START)
        self.f_list.add_css_class('boxed-list')
        self.f_list.connect('row-activated', lambda _l, row: self._open_tile(row))
        self.f_empty = Adw.StatusPage(icon_name='folder-symbolic', title='Nothing to play here',
                                      description='This folder has no videos, music or subfolders.')
        self.f_stack = Gtk.Stack()
        self.f_stack.add_named(Gtk.ScrolledWindow(child=self.f_grid, vexpand=True), 'grid')
        self.f_stack.add_named(Gtk.ScrolledWindow(child=self.f_list, vexpand=True), 'list')
        self.f_stack.add_named(self.f_empty, 'empty')
        page.append(self.f_stack)
        for w in (self.f_grid, self.f_list):
            g = Gtk.GestureClick(button=3)
            g.connect('pressed', self._right_click, w)
            w.add_controller(g)
        return page

    def show_folder(self, path):
        if not path or not os.path.isdir(path):
            self.win.toast("That folder isn't there any more.")
            return
        self.folder = path
        self._tiles = {}
        self.f_head.title.set_label(os.path.basename(path.rstrip('/')) or path)
        self.f_head.sub.set_label(path.replace(os.path.expanduser('~'), '~', 1))
        self.f_head.sub.set_visible(True)
        self.f_up.set_sensitive(path != '/')
        self._sync_star()
        grid = self.settings['folder_view'] == 'grid'
        self.f_view.set_icon_name('view-list-symbolic' if grid else 'view-grid-symbolic')
        self.f_view.set_tooltip_text('Show as a list' if grid else 'Show as a grid')
        self.f_grid.remove_all()
        self.f_list.remove_all()
        folders, files = list_folder(path)
        for p in folders + files:
            kind = 'folder' if p in folders else kind_of(p)
            if grid:
                tile = Tile(self, p, kind)
                self.f_grid.append(tile)
            else:
                tile = self._list_row(p, kind)
                self.f_list.append(tile)
            self._tiles[p] = tile
            if kind in ('video', 'audio'):
                img = self.thumbs.request(p)
                if img:
                    tile.show_image(img)
        has_files = bool(files)
        for w in (self.f_play, self.f_shuffle, self.f_audio, self.f_queue):
            w.set_sensitive(has_files)
        self.f_stack.set_visible_child_name(('grid' if grid else 'list') if folders or files else 'empty')
        self.stack.set_visible_child_name('folder')
        key = ('folder', path)
        self._select(key)

    def _list_row(self, path, kind):
        row = Adw.ActionRow(title=GLib.markup_escape_text(
            os.path.basename(path) if kind == 'folder' else title_for(_uri(path))), activatable=True)
        row.path, row.kind = path, kind
        frame = Gtk.Overlay(width_request=64, height_request=36 if kind == 'video' else 40, valign=Gtk.Align.CENTER)
        frame.add_css_class('lib-thumb')
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        icon = Gtk.Image(icon_name={'folder': 'folder-symbolic', 'audio': 'audio-x-generic-symbolic',
                                    'list': 'view-list-symbolic'}.get(kind, 'video-x-generic-symbolic'))
        frame.set_child(icon)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        frame.add_overlay(pic)
        row.add_prefix(frame)
        if kind == 'folder':
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))

        def show_image(img):
            pic.set_file(Gio.File.new_for_path(img))
            pic.set_visible(True)
            icon.set_visible(False)
        row.show_image = show_image
        return row

    def _thumb_ready(self, _t, path, img):
        tile = self._tiles.get(path)
        if tile:
            tile.show_image(img)
        for t in self._album_tiles.get(path, []) if hasattr(self, '_album_tiles') else []:
            t.show_image(img)

    def _folder_files(self):
        return [p for p in list_folder(self.folder)[1] if kind_of(p) != 'list'] if self.folder else []

    def play_folder(self, shuffle=False, audio=False, start_path=None):
        files = self._folder_files()
        if not files:
            return
        if shuffle:
            random.shuffle(files)
        start = files.index(start_path) if start_path in files else 0
        self.win.play_list([_uri(p) for p in files], start, audio_only=audio)

    def _open_tile(self, tile):
        if tile.kind == 'folder':
            self.show_folder(tile.path)
        elif tile.kind == 'list':
            self.win.open_sources([tile.path], replace=True)
        else:
            self.play_folder(start_path=tile.path)

    def toggle_favorite(self, path):
        favs = list(self.settings['favorites'])
        if path in favs:
            favs.remove(path)
            self.win.toast(f'Removed {os.path.basename(path)} from favorites')
        else:
            favs.append(path)
            self.win.toast(f'Added {os.path.basename(path)} to favorites')
        self.settings.set('favorites', favs)

    def _sync_star(self):
        fav = self.folder in self.settings['favorites']
        self.f_star.set_icon_name('starred-symbolic' if fav else 'non-starred-symbolic')
        self.f_star.set_tooltip_text('Remove from favorites' if fav else 'Add to favorites')
        (self.f_star.add_css_class if fav else self.f_star.remove_css_class)('accent')

    # Right-click menus ---------------------------------------------------------------------

    def _right_click(self, gesture, _n, x, y, container):
        target = container.get_child_at_pos(x, y) if isinstance(container, Gtk.FlowBox) else \
            container.get_row_at_y(int(y))
        if target is None:
            return
        self.popup_item_menu(container, x, y, target.path, target.kind)

    def popup_item_menu(self, widget, x, y, path_or_uri, kind):
        menu = Gio.Menu()
        v = GLib.Variant('s', path_or_uri).print_(False)
        if kind == 'folder':
            menu.append('Open', f'win.lib-browse({v})')
            menu.append('Play All', f'win.lib-play({v})')
            menu.append('Play All as Audio', f'win.lib-play-audio({v})')
            menu.append('Add to Queue', f'win.lib-queue({v})')
            fav = path_or_uri in self.settings['favorites']
            menu.append('Remove from Favorites' if fav else 'Add to Favorites', f'win.lib-favorite({v})')
        else:
            menu.append('Play', f'win.lib-play({v})')
            menu.append('Play as Audio', f'win.lib-play-audio({v})')
            menu.append('Add to Queue', f'win.lib-queue({v})')
        sub = Gio.Menu()
        for name in self.store.names():
            sub.append(name, f"win.lib-add-to-playlist({GLib.Variant('(ss)', (name, path_or_uri)).print_(False)})")
        new = Gio.Menu()
        new.append('New Playlist…', f'win.lib-new-playlist({v})')
        sub.append_section(None, new)
        menu.append_submenu('Add to Playlist', sub)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(widget)
        pop.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect('closed', lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    # Music ----------------------------------------------------------------------------------

    def _music_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = _page_head('Music', music_dir().replace(os.path.expanduser('~'), '~', 1))
        self.m_head = head
        head.append(_flat('media-playback-start-symbolic', 'Play all songs', lambda: self._play_songs(False)))
        head.append(_flat('media-playlist-shuffle-symbolic', 'Shuffle all songs', lambda: self._play_songs(True)))
        head.append(_flat('view-refresh-symbolic', 'Look for new music', lambda: self.music.scan()))
        page.append(head)

        self.m_stack = Adw.ViewStack(vexpand=True)
        bar = Gtk.Box(spacing=12, margin_start=24, margin_end=24, margin_bottom=10)
        switch = Adw.InlineViewSwitcher(stack=self.m_stack, display_mode=Adw.InlineViewSwitcherDisplayMode.LABELS)
        bar.append(switch)
        self.m_search = Gtk.SearchEntry(placeholder_text='Search songs, artists and albums', hexpand=True)
        self.m_search.connect('search-changed', lambda *_: self._fill_songs())
        bar.append(self.m_search)
        page.append(bar)

        self.m_songs = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=24, margin_end=24,
                                   margin_bottom=24, valign=Gtk.Align.START)
        self.m_songs.add_css_class('boxed-list')
        self.m_songs.connect('row-activated', lambda _l, row: self._play_songs(False, row.index))
        g = Gtk.GestureClick(button=3)
        g.connect('pressed', self._song_menu)
        self.m_songs.add_controller(g)
        self.m_stack.add_titled(Gtk.ScrolledWindow(child=self.m_songs), 'songs', 'Songs')

        self.m_albums = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True,
                                    column_spacing=14, row_spacing=18, valign=Gtk.Align.START,
                                    margin_start=24, margin_end=24, margin_bottom=24, max_children_per_line=12)
        self.m_albums.connect('child-activated', lambda _f, t: self.show_collection('album', t.label, t.songs))
        self.m_stack.add_titled(Gtk.ScrolledWindow(child=self.m_albums), 'albums', 'Albums')

        self.m_artists = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=24, margin_end=24,
                                     margin_bottom=24, valign=Gtk.Align.START)
        self.m_artists.add_css_class('boxed-list')
        self.m_artists.connect('row-activated', lambda _l, r: self.show_collection('artist', r.name, r.songs))
        self.m_stack.add_titled(Gtk.ScrolledWindow(child=self.m_artists), 'artists', 'Artists')

        self.m_empty = Adw.StatusPage(icon_name='folder-music-symbolic', title='No music yet',
                                      description='Songs in your Music folder show up here.')
        self.m_outer = Gtk.Stack()
        self.m_outer.add_named(self.m_stack, 'music')
        self.m_outer.add_named(self.m_empty, 'empty')
        page.append(self.m_outer)
        self._shown_songs = []
        self._album_tiles = {}
        return page

    def _fill_music(self):
        songs = self.music.songs
        if self.music.scanning and not songs:
            self.m_empty.set_title('Looking for music…')
            self.m_empty.set_description('')
        else:
            self.m_empty.set_title('No music yet')
            self.m_empty.set_description('Songs in your Music folder show up here.')
        self.m_outer.set_visible_child_name('music' if songs else 'empty')
        n = len(songs)
        self.m_head.sub.set_label(f"{n} song{'' if n == 1 else 's'} in "
                                  + music_dir().replace(os.path.expanduser('~'), '~', 1))
        self._fill_songs()
        self.m_albums.remove_all()
        self._album_tiles = {}
        for album, artist, items in self.music.albums():
            tile = Tile(self, None, 'album', label=album, meta=artist or None)
            tile.label, tile.songs = album, items
            tile.set_tooltip_text(f'{album} · {artist}' if artist else album)
            self.m_albums.append(tile)
            first = items[0].path
            self._album_tiles.setdefault(first, []).append(tile)
            img = self.thumbs.request(first)
            if img:
                tile.show_image(img)
        self.m_artists.remove_all()
        for name, items in self.music.artists():
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), activatable=True,
                                subtitle=f"{len(items)} song{'' if len(items) == 1 else 's'}")
            row.add_prefix(Gtk.Image(icon_name='avatar-default-symbolic'))
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            row.name, row.songs = name, items
            self.m_artists.append(row)

    def _fill_songs(self):
        q = self.m_search.get_text().strip().lower()
        songs = [s for s in self.music.songs
                 if not q or q in s.title.lower() or q in s.artist.lower() or q in s.album.lower()]
        self._shown_songs = songs
        self.m_songs.remove_all()
        for i, s in enumerate(songs[:2000]):
            meta = ' · '.join(x for x in (s.artist, s.album) if x)
            self.m_songs.append(SongRow(i, s.title, meta, s.duration))

    def _play_songs(self, shuffle, start=0):
        songs = list(self._shown_songs)
        if not songs:
            return
        if shuffle:
            random.shuffle(songs)
            start = 0
        self.win.play_list([s.uri for s in songs], start, show_player=False)

    def _song_menu(self, _g, _n, x, y):
        row = self.m_songs.get_row_at_y(int(y))
        if row is not None:
            self.popup_item_menu(self.m_songs, x, y, self._shown_songs[row.index].path, 'audio')

    # Album, artist and playlist pages -------------------------------------------------------

    def _collection_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.c_head = _page_head('Playlist')
        self.c_back = _flat('go-previous-symbolic', 'Back to music', lambda: self.show_music())
        self.c_head.prepend(self.c_back)
        self.c_head.append(_flat('media-playback-start-symbolic', 'Play', lambda: self._play_collection(False)))
        self.c_head.append(_flat('media-playlist-shuffle-symbolic', 'Shuffle', lambda: self._play_collection(True)))
        self.c_head.append(_flat('list-add-symbolic', 'Add to the queue', lambda: self.win.open_sources(
            [u for u, _t, _m, _d in self.collection[2]], replace=False)))
        self.c_rename = _flat('document-edit-symbolic', 'Rename this playlist', self._rename_playlist)
        self.c_delete = _flat('user-trash-symbolic', 'Delete this playlist', self._delete_playlist)
        self.c_head.append(self.c_rename)
        self.c_head.append(self.c_delete)
        page.append(self.c_head)
        self.c_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=24, margin_end=24,
                                  margin_bottom=24, valign=Gtk.Align.START)
        self.c_list.add_css_class('boxed-list')
        self.c_list.connect('row-activated', lambda _l, row: self._play_collection(False, row.index))
        self.c_empty = Adw.StatusPage(icon_name='view-list-symbolic', title='This playlist is empty',
                                      description='Right-click a song, video or folder and choose Add to Playlist.')
        self.c_stack = Gtk.Stack()
        self.c_stack.add_named(Gtk.ScrolledWindow(child=self.c_list, vexpand=True), 'list')
        self.c_stack.add_named(self.c_empty, 'empty')
        page.append(self.c_stack)
        return page

    def show_collection(self, kind, name, songs):
        entries = [(s.uri, s.title, ' · '.join(x for x in (s.artist, s.album) if x), s.duration) for s in songs]
        self._show_collection(kind, name, entries)

    def show_playlist(self, name):
        entries = [(u, t, '', 0) for u, t in self.store.load(name)]
        self._show_collection('playlist', name, entries)
        self._select(('playlist', name))

    def _show_collection(self, kind, name, entries):
        self.collection = (kind, name, entries)
        self.c_head.title.set_label(name)
        n = len(entries)
        self.c_head.sub.set_label({'album': 'Album', 'artist': 'Artist', 'playlist': 'Playlist'}[kind]
                                  + f" · {n} item{'' if n == 1 else 's'}")
        self.c_head.sub.set_visible(True)
        self.c_back.set_visible(kind != 'playlist')
        self.c_rename.set_visible(kind == 'playlist')
        self.c_delete.set_visible(kind == 'playlist')
        self.c_list.remove_all()
        remove = self._remove_from_playlist if kind == 'playlist' else None
        for i, (_u, title, meta, dur) in enumerate(entries):
            self.c_list.append(SongRow(i, title, meta, dur, remove))
        self.c_stack.set_visible_child_name('list' if entries else 'empty')
        self.stack.set_visible_child_name('collection')
        if kind != 'playlist':
            self._select(('music', None))

    def _play_collection(self, shuffle, start=0):
        if not self.collection:
            return
        uris = [u for u, _t, _m, _d in self.collection[2]]
        if not uris:
            return
        if shuffle:
            random.shuffle(uris)
            start = 0
        audio = all(kind_of(Gio.File.new_for_uri(u).get_path() or u) == 'audio' for u in uris)
        self.win.play_list(uris, start, show_player=not audio)

    def _remove_from_playlist(self, index):
        kind, name, entries = self.collection
        entries = [e for i, e in enumerate(entries) if i != index]
        self.store.save(name, [(u, t) for u, t, _m, _d in entries])
        self.show_playlist(name)

    def _rename_playlist(self):
        old = self.collection[1]

        def done(new):
            if self.store.rename(old, new):
                self.show_playlist(new)
            else:
                self.win.toast('A playlist already has that name.')
        self.win.ask_playlist_name(old, done, heading='Rename Playlist', verb='Rename')

    def _delete_playlist(self):
        name = self.collection[1]
        dlg = Adw.AlertDialog(heading=f'Delete “{name}”?',
                              body='The playlist moves to the Trash. The files in it are not touched.')
        dlg.add_response('cancel', 'Cancel')
        dlg.add_response('delete', 'Delete')
        dlg.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response('cancel')

        def resp(_d, r):
            if r == 'delete':
                self.store.delete(name)
                self.show_home()
        dlg.connect('response', resp)
        dlg.present(self.win)
