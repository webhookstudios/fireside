"""The Live TV page: free channels by country and category, with favorites and search."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..livetv import default_country  # noqa: E402

ALL = 'All channels'
FAVS = 'Favorites'


class LiveTVPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.settings = win.settings
        self.tv = win.app.livetv
        self.country = self.settings['tv_country'] or default_country()
        self._loaded = False
        self._binds = {}   # logo url -> [picture widgets showing it]

        head = Gtk.Box(spacing=8, margin_start=24, margin_end=18, margin_top=18, margin_bottom=6)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        t = Gtk.Label(label='Live TV', xalign=0)
        t.add_css_class('lib-title')
        text.append(t)
        self.sub = Gtk.Label(xalign=0, wrap=True,
                             label='Free channels from a public, community-kept list. '
                                   'Some may not play everywhere or may be off the air.')
        self.sub.add_css_class('dim-label')
        text.append(self.sub)
        head.append(text)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Get the latest channel list',
                             valign=Gtk.Align.CENTER)
        refresh.add_css_class('flat')
        refresh.connect('clicked', lambda *_: self._load(force=True))
        head.append(refresh)
        self.append(head)

        bar = Gtk.Box(spacing=8, margin_start=24, margin_end=24, margin_bottom=10)
        self.country_model = Gtk.StringList()
        self.country_drop = Gtk.DropDown(model=self.country_model, enable_search=True,
                                         tooltip_text='Country')
        self.country_drop.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, 'string'))
        self._country_handler = self.country_drop.connect('notify::selected', self._country_chosen)
        bar.append(self.country_drop)
        self.cat_model = Gtk.StringList.new([ALL, FAVS])
        self.cat_drop = Gtk.DropDown(model=self.cat_model, tooltip_text='Category')
        self.cat_drop.connect('notify::selected', lambda *_: self._filter())
        bar.append(self.cat_drop)
        self.search = Gtk.SearchEntry(placeholder_text='Search channels', hexpand=True)
        self.search.connect('search-changed', lambda *_: self._filter())
        bar.append(self.search)
        self.append(bar)

        self.store = Gio.ListStore()
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup)
        factory.connect('bind', self._bind)
        factory.connect('unbind', self._unbind)
        self.grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 max_columns=10, min_columns=2, single_click_activate=True)
        self.grid.add_css_class('tv-grid')
        self.grid.connect('activate', self._activate)

        self.status = Adw.StatusPage(icon_name='fireside-tv-symbolic', title='Getting channels…')
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.grid, vexpand=True), 'grid')
        self.stack.add_named(self.status, 'status')
        self.stack.set_visible_child_name('status')
        self.append(self.stack)

        self.tv.connect('countries', lambda *_: self._fill_countries())
        self.tv.connect('channels', self._got)
        self.tv.connect('failed', lambda *_: self._failed())
        self.tv.connect('logo', self._logo_ready)

    # ---- loading -------------------------------------------------------------------------

    def shown(self):
        if not self._loaded:
            self._loaded = True
            self.tv.load_countries()
            self._load()

    def _load(self, force=False):
        self.status.set_title('Getting channels…')
        self.status.set_description('')
        self.stack.set_visible_child_name('status')
        self.tv.load(self.country, force=force)

    def _fill_countries(self):
        self.country_drop.handler_block(self._country_handler)
        self._codes = [c for c, _n in self.tv.country_list]
        self.country_model.splice(0, self.country_model.get_n_items(), [n for _c, n in self.tv.country_list])
        if self.country in self._codes:
            self.country_drop.set_selected(self._codes.index(self.country))
        self.country_drop.handler_unblock(self._country_handler)

    def _country_chosen(self, drop, _p):
        i = drop.get_selected()
        if 0 <= i < len(getattr(self, '_codes', [])):
            self.country = self._codes[i]
            self.settings.set('tv_country', self.country)
            self._load()

    def _failed(self):
        self.status.set_title("Couldn't get the channel list")
        self.status.set_description('Check your internet connection, then try the refresh button.')
        self.stack.set_visible_child_name('status')

    def _got(self, _tv, code):
        if code != self.country:
            return
        chans = self.tv.channels.get(code, [])
        groups = sorted({g for c in chans for g in c.groups})
        current = self.cat_model.get_string(self.cat_drop.get_selected()) if self.cat_model.get_n_items() else ALL
        self.cat_model.splice(0, self.cat_model.get_n_items(), [ALL, FAVS] + groups)
        names = [ALL, FAVS] + groups
        self.cat_drop.set_selected(names.index(current) if current in names else 0)
        self._filter()

    def _filter(self):
        chans = self.tv.channels.get(self.country)
        if chans is None:
            return
        cat = self.cat_model.get_string(self.cat_drop.get_selected()) or ALL
        q = self.search.get_text().strip().lower()
        favs = set(self.settings['tv_favorites'])
        if cat == FAVS:
            # Favorites from every country, not just this one.
            seen = {c.url: c for lst in self.tv.channels.values() for c in lst}
            chans = [seen[u] for u in self.settings['tv_favorites'] if u in seen]
        shown = [c for c in chans if (cat in (ALL, FAVS) or cat in c.groups) and (not q or q in c.title.lower())]
        self.shown_channels = shown
        self.store.splice(0, self.store.get_n_items(), shown)
        n = len(shown)
        if n:
            self.stack.set_visible_child_name('grid')
        else:
            self.status.set_title('No channels here' if cat != FAVS else 'No favorite channels yet')
            self.status.set_description('Star a channel to keep it here.' if cat == FAVS else
                                        'Try another category or search.')
            self.stack.set_visible_child_name('status')
        del favs

    # ---- tiles ---------------------------------------------------------------------------

    def _setup(self, _f, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6, width_request=168)
        box.add_css_class('tv-tile')
        frame = Gtk.Overlay(height_request=96)
        frame.add_css_class('tv-logo')
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        icon = Gtk.Image(icon_name='fireside-tv-symbolic', pixel_size=32)
        icon.add_css_class('dim-label')
        frame.set_child(icon)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, visible=False,
                          margin_top=14, margin_bottom=14, margin_start=18, margin_end=18)
        frame.add_overlay(pic)
        star = Gtk.Button(icon_name='non-starred-symbolic', halign=Gtk.Align.END, valign=Gtk.Align.START,
                          margin_top=4, margin_end=4)
        star.add_css_class('osd')
        star.add_css_class('circular')
        star.add_css_class('tv-star')
        star.connect('clicked', self._star_clicked, item)
        frame.add_overlay(star)
        live = Gtk.Label(label='LIVE', halign=Gtk.Align.START, valign=Gtk.Align.START, margin_top=6, margin_start=6)
        live.add_css_class('tv-live')
        frame.add_overlay(live)
        box.append(frame)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('lib-name')
        box.append(name)
        meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        meta.add_css_class('caption')
        meta.add_css_class('dim-label')
        box.append(meta)
        item.set_child(box)
        item.widgets = (icon, pic, star, name, meta)

    def _bind(self, _f, item):
        ch = item.get_item()
        icon, pic, star, name, meta = item.widgets
        name.set_label(ch.title)
        bits = [', '.join(ch.groups[:2])]
        if ch.quality:
            bits.append(ch.quality)
        if ch.blocked:
            bits.append('may be region-locked')
        meta.set_label(' · '.join(b for b in bits if b))
        item.get_child().set_tooltip_text(ch.title)
        fav = ch.url in self.settings['tv_favorites']
        star.set_icon_name('starred-symbolic' if fav else 'non-starred-symbolic')
        star.set_tooltip_text('Remove from favorites' if fav else 'Add to favorites')
        (star.add_css_class if fav else star.remove_css_class)('on')
        path = self.tv.logo_path(ch.logo)
        self._show_logo(icon, pic, path)
        if ch.logo:
            self._binds.setdefault(ch.logo, []).append((icon, pic))

    def _unbind(self, _f, item):
        ch = item.get_item()
        if ch and ch.logo in self._binds:
            icon, pic = item.widgets[0], item.widgets[1]
            self._binds[ch.logo] = [w for w in self._binds[ch.logo] if w[1] is not pic]

    def _show_logo(self, icon, pic, path):
        if path:
            try:
                pic.set_paintable(Gdk.Texture.new_from_filename(path))
                pic.set_visible(True)
                icon.set_visible(False)
                return
            except GLib.Error:
                pass
        pic.set_paintable(None)
        pic.set_visible(False)
        icon.set_visible(True)

    def _logo_ready(self, _tv, url, path):
        for icon, pic in self._binds.get(url, []):
            self._show_logo(icon, pic, path)

    def _star_clicked(self, _b, item):
        ch = item.get_item()
        favs = list(self.settings['tv_favorites'])
        if ch.url in favs:
            favs.remove(ch.url)
        else:
            favs.append(ch.url)
        self.settings.set('tv_favorites', favs)
        self._bind(None, item)
        if self.cat_model.get_string(self.cat_drop.get_selected()) == FAVS:
            self._filter()

    def _activate(self, _grid, pos):
        chans = getattr(self, 'shown_channels', [])
        if 0 <= pos < len(chans):
            # The shown channels become the queue, so Next and Previous flip channels.
            self.win.play_entries([(c.url, c.title) for c in chans], pos)
