"""The main window: the stage, the control bar, the queue and the library."""
import os
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..player import Player, find_subtitle, title_for  # noqa: E402
from ..playlist import Playlist  # noqa: E402
from .controls import SPEEDS, Controls, speed_label  # noqa: E402
from .hearth import Hearth  # noqa: E402
from .library_view import LibraryView  # noqa: E402
from .playlist_view import PlaylistView, fmt_time  # noqa: E402
from .prefs import default_snapshot_dir  # noqa: E402

FITS = {'contain': Gtk.ContentFit.CONTAIN, 'cover': Gtk.ContentFit.COVER,
        'fill': Gtk.ContentFit.FILL, 'original': Gtk.ContentFit.SCALE_DOWN}
FIT_NAMES = {'contain': 'Fit to window', 'cover': 'Fill the window', 'fill': 'Stretch', 'original': 'Original size'}

MEDIA_FILTER_TYPES = ('video/*', 'audio/*', 'application/x-matroska', 'application/vnd.rn-realmedia',
                      'audio/x-mpegurl', 'application/vnd.apple.mpegurl', 'audio/x-scpls')
SUB_PATTERNS = ('*.srt', '*.ass', '*.ssa', '*.vtt', '*.sub', '*.smi', '*.txt')


class FiresideWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Fireside')
        self.app = app
        self.settings = app.settings
        self.state = app.state
        self.set_default_size(self.settings['width'], self.settings['height'])
        self.set_size_request(360, 300)

        self.player = Player()
        self.playlist = Playlist(self.settings)
        self._inhibit = 0
        self._hide_timer = 0
        self._osd_timer = 0
        self._click_timer = 0
        self._last_motion = (0, 0)
        self._hover_controls = False
        self._hidden = False
        self._error_shown = False
        self.mpris = None
        self.mini = None
        self.cast = None
        self.cast_server = None

        self._build()
        self._actions()
        self._wire()
        self._apply_all_settings()
        self.connect('close-request', self._on_close)

    # ---- building ------------------------------------------------------------------------

    def _build(self):
        # Header
        self.header = Adw.HeaderBar()
        self.wtitle = Adw.WindowTitle(title='Fireside')
        self.header.set_title_widget(self.wtitle)
        for icon, tip, action in (('document-open-symbolic', 'Open files (Ctrl+O)', 'win.open-files'),
                                  ('insert-link-symbolic', 'Open a location (Ctrl+L)', 'win.open-location')):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=action)
            self.header.pack_start(b)
        menu = Gtk.MenuButton(icon_name='open-menu-symbolic', tooltip_text='Main menu',
                              menu_model=self._main_menu(), primary=True)
        self.header.pack_end(menu)
        self.pl_toggle = Gtk.ToggleButton(icon_name='sidebar-show-right-symbolic', tooltip_text='Queue (F9)',
                                          action_name='win.playlist')
        self.header.pack_end(self.pl_toggle)
        self.sleep_btn = Gtk.MenuButton(icon_name='alarm-symbolic', visible=False, menu_model=self._sleep_menu())
        self.sleep_btn.add_css_class('accent')
        self.header.pack_end(self.sleep_btn)
        self.lib_btn = Gtk.Button(icon_name='view-grid-symbolic', tooltip_text='Library', visible=False)
        self.lib_btn.connect('clicked', lambda *_: self.show_page(
            'player' if self.main_stack.get_visible_child_name() == 'welcome' else 'welcome'))
        self.header.pack_start(self.lib_btn)

        # Stage: video or music
        self.picture = Gtk.Picture(paintable=self.player.paintable, hexpand=True, vexpand=True)
        self.hearth = Hearth(self.settings, Adw.StyleManager.get_default())
        self.media_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.media_stack.add_named(self.picture, 'video')
        self.media_stack.add_named(self.hearth, 'audio')
        self.media_stack.add_css_class('stage')

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.media_stack)
        self.overlay.add_css_class('stage')

        self.big_play = Gtk.Button(icon_name='media-playback-start-symbolic', tooltip_text='Play (Space)',
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, visible=False,
                                   focus_on_click=False, action_name='win.toggle-play')
        self.big_play.add_css_class('big-play')
        self.big_play.get_child().set_pixel_size(36)
        self.overlay.add_overlay(self.big_play)

        self.spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, visible=False,
                                   width_request=48, height_request=48)
        self.overlay.add_overlay(self.spinner)

        self.osd = Gtk.Label()
        self.osd.add_css_class('osd-note')
        self.osd_rev = Gtk.Revealer(child=self.osd, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                    halign=Gtk.Align.END, valign=Gtk.Align.START, can_target=False)
        self.overlay.add_overlay(self.osd_rev)

        self.controls = Controls(self)
        self.ctl_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=True,
                                    valign=Gtk.Align.END, transition_duration=220)
        hover = Gtk.EventControllerMotion()
        hover.connect('enter', lambda *_: setattr(self, '_hover_controls', True))
        hover.connect('leave', lambda *_: setattr(self, '_hover_controls', False))
        self.controls.add_controller(hover)

        # Welcome
        self.welcome = self._welcome_page()
        self.library = LibraryView(self, self.welcome)

        self.main_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.add_named(self.library, 'welcome')
        self.main_stack.add_named(self.overlay, 'player')

        self.tview = Adw.ToolbarView(content=self.main_stack, top_bar_style=Adw.ToolbarStyle.FLAT)
        self.tview.add_top_bar(self.header)
        self.cast_banner = Adw.Banner(button_label='Stop Casting')
        self.cast_banner.connect('button-clicked', lambda *_: self.stop_cast())
        self.tview.add_top_bar(self.cast_banner)

        self.plview = PlaylistView(self.playlist, self.settings)
        # The control bar drops its less-used buttons when the player area is narrow.
        self.stage_bin = Adw.BreakpointBin(child=self.tview, width_request=340, height_request=260)
        c = self.controls
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 1000px'))
        for w in (c.vol, c.fx_btn, c.audio_only, c.mini_btn):
            narrow.add_setter(w, 'visible', False)
        self.stage_bin.add_breakpoint(narrow)
        tight = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 760px'))
        for w in (c.vol, c.fx_btn, c.audio_only, c.mini_btn, c.speed_btn, c.cast_btn, c.back, c.fwd,
                  c.audio_btn):
            tight.add_setter(w, 'visible', False)
        self.stage_bin.add_breakpoint(tight)
        self.split = Adw.OverlaySplitView(sidebar=self.plview, content=self.stage_bin, sidebar_width_fraction=0.28,
                                          min_sidebar_width=240, max_sidebar_width=380)
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 720sp'))
        bp.add_setter(self.split, 'collapsed', True)
        self.add_breakpoint(bp)

    def _main_menu(self):
        menu = Gio.Menu()
        s = Gio.Menu()
        s.append('Open Files…', 'win.open-files')
        s.append('Open Folder…', 'win.open-folder')
        s.append('Open Location…', 'win.open-location')
        self.recent_menu = Gio.Menu()
        s.append_submenu('Recent', self.recent_menu)
        menu.append_section(None, s)
        s = Gio.Menu()
        fit = Gio.Menu()
        for key, name in FIT_NAMES.items():
            fit.append(name, f'win.fit::{key}')
        s.append_submenu('Video Size', fit)
        s.append('Play as Audio Only', 'win.audio-only')
        s.append_submenu('Sleep Timer', self._sleep_menu())
        s.append('Mini Player', 'win.mini')
        s.append('Effects and Filters', 'win.effects')
        s.append('Take Snapshot', 'win.snapshot')
        s.append('Media Information', 'win.info')
        menu.append_section(None, s)
        s = Gio.Menu()
        s.append('Save Queue as Playlist…', 'win.save-playlist')
        s.append('Export Queue to a File…', 'win.export-playlist')
        menu.append_section(None, s)
        s = Gio.Menu()
        s.append('Preferences', 'app.preferences')
        s.append('Keyboard Shortcuts', 'app.shortcuts')
        s.append('About Fireside', 'app.about')
        menu.append_section(None, s)
        return menu

    def _welcome_page(self):
        page = Adw.StatusPage(icon_name='io.github.webhookstudios.Fireside', title='Fireside',
                              description='Open a video or some music, or drop files here.')
        page.add_css_class('welcome')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, halign=Gtk.Align.CENTER)
        btns = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        b = Gtk.Button(label='Open Files', action_name='win.open-files')
        b.add_css_class('pill')
        b.add_css_class('suggested-action')
        btns.append(b)
        b = Gtk.Button(label='Open Folder', action_name='win.open-folder')
        b.add_css_class('pill')
        btns.append(b)
        b = Gtk.Button(label='Open Location', action_name='win.open-location')
        b.add_css_class('pill')
        btns.append(b)
        box.append(btns)
        self.recent_group = Adw.PreferencesGroup(title='Recent', width_request=420)
        clear = Gtk.Button(icon_name='edit-clear-all-symbolic', tooltip_text='Clear the recent list',
                           action_name='win.clear-recent', valign=Gtk.Align.CENTER)
        clear.add_css_class('flat')
        self.recent_group.set_header_suffix(clear)
        self.recent_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.recent_list.add_css_class('boxed-list')
        self.recent_group.add(self.recent_list)
        self.cont_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        cont_label = Gtk.Label(label='Continue watching', xalign=0)
        cont_label.add_css_class('heading')
        self.cont_group.append(cont_label)
        self.cont_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True,
                                    max_children_per_line=4, min_children_per_line=1, column_spacing=12,
                                    row_spacing=12, homogeneous=True)
        self.cont_box.connect('child-activated', lambda _f, c: self.open_sources([c.uri], replace=True))
        self.cont_group.append(self.cont_box)
        self._cont_tiles = {}
        box.append(self.cont_group)
        box.append(self.recent_group)
        page.set_child(box)
        return page

    def refresh_recent(self):
        self.recent_list.remove_all()
        self.recent_menu.remove_all()
        for r in self.state.recent[:8]:
            path = Gio.File.new_for_uri(r['uri']).get_path()
            row = Adw.ActionRow(title=GLib.markup_escape_text(r['title']), activatable=True)
            where = os.path.dirname(path).replace(os.path.expanduser('~'), '~', 1) if path else r['uri']
            row.set_subtitle(GLib.markup_escape_text(where))
            row.set_subtitle_lines(1)
            pos = self.state.resume.get(r['uri'])
            if pos:
                lab = Gtk.Label(label=fmt_time(pos))
                lab.add_css_class('dim-label')
                lab.set_tooltip_text('Resumes here')
                row.add_suffix(lab)
            row.add_prefix(Gtk.Image(icon_name='media-playback-start-symbolic'))
            row.connect('activated', lambda _r, u: self.open_sources([u], replace=True), r['uri'])
            self.recent_list.append(row)
            self.recent_menu.append(r['title'][:60], f"win.open-recent({GLib.Variant('s', r['uri']).print_(False)})")
        self.recent_group.set_visible(bool(self.state.recent))
        self._refresh_continue()

    def _refresh_continue(self):
        """Videos stopped partway, newest first, with how much is left."""
        from ..library import kind_of
        self.cont_box.remove_all()
        self._cont_tiles = {}
        order = [r['uri'] for r in self.state.recent]
        uris = [u for u in reversed(list(self.state.resume)) if u not in order]
        uris = [u for u in order if u in self.state.resume] + uris
        shown = 0
        for uri in uris:
            path = Gio.File.new_for_uri(uri).get_path()
            if not path or not os.path.exists(path) or kind_of(path) != 'video':
                continue
            pos = self.state.resume[uri]
            length = self.state.lengths.get(uri, 0)
            child = Gtk.FlowBoxChild()
            child.uri = uri
            child.add_css_class('cont-tile')
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            frame = Gtk.Overlay(height_request=110)
            frame.add_css_class('lib-thumb')
            frame.set_overflow(Gtk.Overflow.HIDDEN)
            frame.set_child(Gtk.Image(icon_name='video-x-generic-symbolic', pixel_size=32))
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
            frame.add_overlay(pic)
            if length:
                bar = Gtk.ProgressBar(fraction=min(1.0, pos / length), valign=Gtk.Align.END)
                bar.add_css_class('cont-progress')
                frame.add_overlay(bar)
            forget = Gtk.Button(icon_name='window-close-symbolic', tooltip_text='Remove from Continue watching',
                                halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=6, margin_end=6)
            forget.add_css_class('osd')
            forget.add_css_class('circular')
            forget.add_css_class('cont-forget')
            forget.connect('clicked', lambda _b, u=uri: (self.state.forget(u), self.state.save(),
                                                          self._refresh_continue()))
            frame.add_overlay(forget)
            vbox.append(frame)
            name = Gtk.Label(label=title_for(uri), xalign=0, ellipsize=3)
            name.add_css_class('lib-name')
            vbox.append(name)
            left = f'{fmt_time(length - pos)} left' if length else f'Stopped at {fmt_time(pos)}'
            meta = Gtk.Label(label=left, xalign=0)
            meta.add_css_class('caption')
            meta.add_css_class('dim-label')
            vbox.append(meta)
            child.set_child(vbox)
            child.set_tooltip_text(f'Resume {title_for(uri)} at {fmt_time(pos)}')
            self.cont_box.append(child)

            def show(img, pic=pic):
                pic.set_file(Gio.File.new_for_path(img))
                pic.set_visible(True)
            self._cont_tiles[path] = show
            img = self.app.thumbs.request(path)
            if img:
                show(img)
            shown += 1
            if shown >= 8:
                break
        self.cont_group.set_visible(shown > 0)

    # ---- actions -------------------------------------------------------------------------

    def _add(self, name, cb, param=None, state=None):
        if state is not None:
            act = Gio.SimpleAction.new_stateful(name, GLib.VariantType(param) if param else None, state)
            act.connect('change-state', cb)
        else:
            act = Gio.SimpleAction.new(name, GLib.VariantType(param) if param else None)
            act.connect('activate', cb)
        self.add_action(act)
        return act

    def _actions(self):
        p = self.player
        self._add('open-files', lambda *_: self._pick_files(replace=True))
        self._add('open-folder', lambda *_: self._pick_folder(replace=True, browse=True))
        self._add('add-files', lambda *_: self._pick_files(replace=False))
        self._add('add-folder', lambda *_: self._pick_folder(replace=False))
        self._add('open-location', lambda *_: self._ask_location())
        self._add('open-recent', lambda _a, v: self.open_sources([v.unpack()], replace=True), 's')
        self._add('clear-recent', self._clear_recent)
        self._add('save-playlist', lambda *_: self._save_queue())
        self._add('export-playlist', lambda *_: self._save_playlist())
        self._add('lib-browse', lambda _a, v: self.browse(v.unpack()), 's')
        self._add('lib-play', lambda _a, v: self._lib_play(v.unpack(), False), 's')
        self._add('lib-play-audio', lambda _a, v: self._lib_play(v.unpack(), True), 's')
        self._add('lib-queue', lambda _a, v: self.open_sources([v.unpack()], replace=False), 's')
        self._add('lib-favorite', lambda _a, v: self.library.toggle_favorite(v.unpack()), 's')
        self._add('lib-add-to-playlist', self._lib_add_to_playlist, '(ss)')
        self._add('lib-new-playlist', lambda _a, v: self.ask_playlist_name(
            None, lambda name: self._add_to_named(name, v.unpack())), 's')
        self._add('mini', lambda *_: self.toggle_mini())
        self._add('sub-earlier', lambda *_: self._nudge_subs(-0.1))
        self._add('sub-later', lambda *_: self._nudge_subs(0.1))
        self._add('sub-reset', lambda *_: self._nudge_subs(None))
        self.sleep_action = self._add('sleep', lambda a, v: self.set_sleep(v.unpack()), 's', GLib.Variant('s', 'off'))
        self.audio_only_action = self._add('audio-only', self._toggle_audio_only, state=GLib.Variant('b', False))
        self._add('show-library', lambda *_: self.show_page('welcome'))
        self._add('clear-playlist', lambda *_: self._clear_playlist())
        self._add('play-index', lambda _a, v: self.play_index(v.unpack()), 'i')
        self._add('toggle-play', lambda *_: self._toggle_play())
        self._add('stop', lambda *_: self.stop())
        self._add('next', lambda *_: self.go(1, user=True))
        self._add('previous', lambda *_: self._previous())
        self._add('skip-forward', lambda *_: self._skip(1))
        self._add('skip-back', lambda *_: self._skip(-1))
        self._add('skip-forward-long', lambda *_: self._skip(6))
        self._add('skip-back-long', lambda *_: self._skip(-6))
        self._add('step-forward', lambda *_: p.step_frame(True))
        self._add('step-back', lambda *_: p.step_frame(False))
        self._add('volume-up', lambda *_: self._nudge_volume(0.05))
        self._add('volume-down', lambda *_: self._nudge_volume(-0.05))
        self._add('mute', lambda *_: self._toggle_mute())
        self._add('fullscreen', lambda *_: self.set_fullscreened(not self.is_fullscreen()))
        self._add('leave-fullscreen', lambda *_: self.set_fullscreened(False))
        self.pl_action = self._add('playlist', self._toggle_playlist, state=GLib.Variant('b', False))
        self.speed_action = self._add('speed', lambda a, v: self.set_speed(float(v.unpack())), 's',
                                      GLib.Variant('s', f"{self.settings['speed']:g}"))
        self._add('speed-up', lambda *_: self._step_speed(1))
        self._add('speed-down', lambda *_: self._step_speed(-1))
        self._add('speed-reset', lambda *_: self.set_speed(1.0))
        self.audio_action = self._add('audio-track', self._choose_audio, 'i', GLib.Variant('i', 0))
        self.text_action = self._add('subtitle-track', self._choose_text, 'i', GLib.Variant('i', -1))
        self._add('cycle-audio', lambda *_: self._cycle_audio())
        self._add('cycle-subtitle', lambda *_: self._cycle_text())
        self._add('load-subtitle', lambda *_: self._pick_subtitle())
        self.fit_action = self._add('fit', lambda a, v: self.settings.set('fit', v.unpack()), 's',
                                    GLib.Variant('s', self.settings['fit']))
        self._add('cycle-fit', lambda *_: self._cycle_fit())
        self._add('snapshot', lambda *_: self._snapshot())
        self._add('effects', lambda *_: self._effects())
        self._add('info', lambda *_: self._info())
        none = self._add('none', lambda *_: None)
        none.set_enabled(False)
        self._sync_enabled()

    def _sync_enabled(self):
        has = self.player.uri is not None
        for name in ('toggle-play', 'stop', 'skip-forward', 'skip-back', 'skip-forward-long', 'skip-back-long',
                     'step-forward', 'step-back', 'snapshot', 'info', 'load-subtitle', 'cycle-audio',
                     'cycle-subtitle'):
            self.lookup_action(name).set_enabled(has)
        self.lookup_action('snapshot').set_enabled(has and self.player.has_video)
        many = len(self.playlist) > 1
        self.lookup_action('next').set_enabled(many)
        self.lookup_action('previous').set_enabled(has)
        self.lookup_action('save-playlist').set_enabled(len(self.playlist) > 0)
        self.lookup_action('clear-playlist').set_enabled(len(self.playlist) > 0)
        self.lookup_action('export-playlist').set_enabled(len(self.playlist) > 0)
        self.lib_btn.set_visible(has)
        c = self.controls
        c.next.set_tooltip_text('Next (N)' if many else 'Next: add more to the playlist first')
        c.prev.set_tooltip_text('Previous (P)' if has else 'Previous: open something first')
        c.play.set_tooltip_text(c.play.get_tooltip_text() if has else 'Play: open a file first')

    # ---- wiring --------------------------------------------------------------------------

    def _wire(self):
        p = self.player
        p.connect('state-changed', self._on_state)
        p.connect('media-changed', lambda *_: self._sync_enabled())
        p.connect('ready', self._on_ready)
        p.connect('track-changed', self._on_gapless)
        p.next_provider = lambda: self._gapless_next
        self._gapless_next = None
        self._gapless_index = -1
        self._sleep_mode = 'off'
        self._sleep_deadline = 0
        self._sleep_timer = 0
        self._fading = False
        self.app.thumbs.connect('ready', lambda _t, path, img: self._cont_tiles.get(path, lambda i: None)(img))
        p.connect('tags-changed', lambda *_: self._on_tags())
        p.connect('video-changed', lambda _p, v: self._on_video(v))
        p.connect('eos', lambda *_: self._on_eos())
        p.connect('error', lambda _p, msg: self._on_error(msg))
        p.connect('buffering', lambda _p, pct: self.spinner.set_visible(pct < 100 and p.uri is not None))
        self.playlist.connect('changed', lambda *_: (self._sync_enabled(), self._plan_next()))
        self.settings.connect('changed', self._on_setting)
        self.split.connect('notify::show-sidebar', self._sidebar_shown)
        self.connect('notify::fullscreened', self._on_fullscreen)

        # Pointer on the stage: wake the controls, click to pause, double-click for fullscreen.
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._on_motion)
        self.overlay.add_controller(motion)
        click = Gtk.GestureClick(button=1)
        click.connect('pressed', self._on_click)
        self.media_stack.add_controller(click)
        rclick = Gtk.GestureClick(button=3)
        rclick.connect('pressed', self._on_right_click)
        self.media_stack.add_controller(rclick)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self._on_scroll)
        self.media_stack.add_controller(scroll)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._on_key)
        self.add_controller(keys)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self._on_drop)
        self.add_controller(drop)
        # Dropping on the playlist adds instead of replacing.
        drop2 = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop2.connect('drop', lambda _t, v, *_: self._on_drop(None, v, 0, 0, add=True))
        self.plview.add_controller(drop2)

        self.ctx_menu = Gtk.PopoverMenu.new_from_model(self._context_menu())
        self.ctx_menu.set_parent(self.media_stack)
        self.ctx_menu.set_has_arrow(False)

    def _context_menu(self):
        menu = Gio.Menu()
        s = Gio.Menu()
        s.append('Play or Pause', 'win.toggle-play')
        s.append('Next', 'win.next')
        s.append('Previous', 'win.previous')
        menu.append_section(None, s)
        s = Gio.Menu()
        speed = Gio.Menu()
        for r in SPEEDS:
            speed.append(speed_label(r), f'win.speed::{r:g}')
        s.append_submenu('Speed', speed)
        fit = Gio.Menu()
        for key, name in FIT_NAMES.items():
            fit.append(name, f'win.fit::{key}')
        s.append_submenu('Video Size', fit)
        s.append('Add Subtitle File…', 'win.load-subtitle')
        s.append('Play as Audio Only', 'win.audio-only')
        s.append_submenu('Sleep Timer', self._sleep_menu())
        s.append('Mini Player', 'win.mini')
        s.append('Effects and Filters', 'win.effects')
        s.append('Take Snapshot', 'win.snapshot')
        menu.append_section(None, s)
        s = Gio.Menu()
        s.append('Fullscreen', 'win.fullscreen')
        s.append('Media Information', 'win.info')
        menu.append_section(None, s)
        return menu

    def _apply_all_settings(self):
        s = self.settings
        self.player.set_volume(s['volume'])
        self.player.set_muted(s['muted'])
        self.player.rate = s['speed']
        self.player.set_eq(s['eq_on'], s['eq_bands'])
        self.player.set_balance(s['brightness'], s['contrast'], s['saturation'], s['hue'])
        self._apply_sub_font()
        self.picture.set_content_fit(FITS.get(s['fit'], Gtk.ContentFit.CONTAIN))
        self.split.set_sidebar_position(Gtk.PackType.START if s['playlist_side'] == 'left' else Gtk.PackType.END)
        self.split.set_show_sidebar(s['playlist_open'])
        self._place_controls()
        self.refresh_recent()

    def _apply_sub_font(self):
        from gi.repository import Pango
        desc = Pango.FontDescription.from_string(self.settings['sub_font'])
        desc.set_size(self.settings['sub_size'] * Pango.SCALE)
        self.player.set_subtitle_font(desc.to_string())

    def _on_setting(self, _s, key):
        s = self.settings
        if key == 'volume':
            self.player.set_volume(s['volume'])
            if self.cast:
                self.cast.set_volume(s['volume'])
        elif key == 'muted':
            self.player.set_muted(s['muted'])
        elif key in ('eq_on', 'eq_bands'):
            self.player.set_eq(s['eq_on'], s['eq_bands'])
        elif key in ('brightness', 'contrast', 'saturation', 'hue'):
            self.player.set_balance(s['brightness'], s['contrast'], s['saturation'], s['hue'])
        elif key in ('sub_font', 'sub_size'):
            self._apply_sub_font()
        elif key == 'fit':
            self.picture.set_content_fit(FITS.get(s['fit'], Gtk.ContentFit.CONTAIN))
            self.fit_action.set_state(GLib.Variant('s', s['fit']))
        elif key == 'playlist_side':
            self.split.set_sidebar_position(Gtk.PackType.START if s['playlist_side'] == 'left' else Gtk.PackType.END)
            self.pl_toggle.set_icon_name('sidebar-show-symbolic' if s['playlist_side'] == 'left'
                                         else 'sidebar-show-right-symbolic')
        elif key == 'controls':
            self._place_controls()
        elif key == 'big_play':
            self._sync_big_play()
        elif key == 'inhibit':
            self._sync_inhibit()

    # ---- control bar placement -----------------------------------------------------------

    def _place_controls(self):
        """Docked bars sit under the video; the others float over it. In fullscreen all float."""
        on_library = self.main_stack.get_visible_child_name() == 'welcome'
        docked = (self.settings['controls'] == 'docked' or on_library) and not self.is_fullscreen()
        parent = self.controls.get_parent()
        if parent is self.ctl_rev:
            self.ctl_rev.set_child(None)
        elif parent is not None:
            self.tview.remove(self.controls)
        if self.ctl_rev.get_parent() is None:
            self.overlay.add_overlay(self.ctl_rev)
        if docked:
            self.tview.add_bottom_bar(self.controls)
            self.ctl_rev.set_visible(False)
        else:
            self.ctl_rev.set_child(self.controls)
            self.ctl_rev.set_visible(True)
        self.controls.layout(docked_override=on_library and docked)
        if self.is_fullscreen() and self.settings['controls'] == 'docked':
            self.controls.remove_css_class('docked')
            self.controls.add_css_class('floating')
        self.tview.set_reveal_bottom_bars(docked and self.player.uri is not None)
        self.controls.show_now_playing(on_library)
        self._show_controls()

    # ---- opening -------------------------------------------------------------------------

    def show_page(self, name):
        """'player' or 'welcome' (the library)."""
        if name == 'player' and self.player.uri is None:
            name = 'welcome'
        self.main_stack.set_visible_child_name(name)
        on_lib = name == 'welcome'
        self.lib_btn.set_visible(self.player.uri is not None)
        self.lib_btn.set_icon_name('video-display-symbolic' if on_lib else 'view-grid-symbolic')
        self.lib_btn.set_tooltip_text('Back to what is playing' if on_lib else 'Library')
        self._place_controls()

    def browse(self, folder):
        self.show_page('welcome')
        self.library.show_folder(folder)

    def play_list(self, uris, start=0, audio_only=False, show_player=True):
        """Replace the queue with these and play from 'start'."""
        self.playlist.clear()
        self.playlist.add(uris)
        if self.player.audio_only != audio_only:
            self.player.audio_only = audio_only
            self.audio_only_action.set_state(GLib.Variant('b', audio_only))
        self.play_index(start, show_player=show_player)
        self._sync_enabled()

    def play_entries(self, entries, start=0):
        """Replace the queue with named streams (TV channels) and play one."""
        self.playlist.clear()
        self.playlist.add_entries(entries)
        if self.player.audio_only:
            self.player.audio_only = False
            self.audio_only_action.set_state(GLib.Variant('b', False))
        self.play_index(start, show_player=True)
        self._sync_enabled()

    def _lib_play(self, path, audio):
        if os.path.isdir(path):
            from ..library import kind_of
            from ..library import list_folder
            files = [p for p in list_folder(path)[1] if kind_of(p) != 'list']
            if not files:
                self.toast('Nothing playable in that folder.')
                return
            self.play_list([Gio.File.new_for_path(p).get_uri() for p in files], 0, audio_only=audio)
        else:
            uri = path if '://' in path else Gio.File.new_for_path(path).get_uri()
            self.play_list([uri], 0, audio_only=audio)

    def _entries_for(self, path):
        from ..playlist import expand
        known = {song.uri: song.title for song in self.app.music.songs}
        return [(u, known.get(u) or title_for(u)) for u in expand([path])]

    def _add_to_named(self, name, path):
        entries = self._entries_for(path)
        self.app.playlists.add(name, entries)
        n = len(entries)
        self.toast(f"Added {n} item{'' if n == 1 else 's'} to {name}")

    def _lib_add_to_playlist(self, _a, v):
        name, path = v.unpack()
        self._add_to_named(name, path)

    def ask_playlist_name(self, initial, done, heading='New Playlist', verb='Create'):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial or self.app.playlists.unique_name(), activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('ok', verb)
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('ok')
        dialog.set_close_response('cancel')

        def responded(_d, resp):
            name = entry.get_text().strip().replace('/', '-')
            if resp == 'ok' and name:
                done(name)
        dialog.connect('response', responded)
        dialog.present(self)
        entry.grab_focus()

    def _save_queue(self):
        if not len(self.playlist):
            return
        entries = [(it.uri, it.title) for it in self.playlist.items]

        def done(name):
            self.app.playlists.save(name, entries)
            self.toast(f'Saved the queue as {name}')
        self.ask_playlist_name(None, done, heading='Save Queue as Playlist', verb='Save')

    def _toggle_audio_only(self, action, value):
        on = value.unpack()
        action.set_state(value)
        self.player.set_audio_only(on)
        if self.player.uri:
            self.note('Playing as audio' if on else 'Playing video')

    def open_sources(self, sources, replace=True):
        if replace and len(sources) == 1:
            path = Gio.File.new_for_uri(sources[0]).get_path() if '://' in sources[0] else sources[0]
            if path and os.path.isdir(path):
                self.browse(path)
                return
        start = self.playlist.add(sources, replace=replace)
        if start < 0:
            self.toast('Nothing playable was found there.')
            return
        if replace or self.player.uri is None:
            self.play_index(start, show_player=True)
        self._sync_enabled()

    def play_index(self, index, show_player=None):
        if not 0 <= index < len(self.playlist):
            return
        self._save_resume()
        item = self.playlist.items[index]
        self.playlist.set_current(index)
        start = self.state.resume.get(item.uri, 0) if self.settings['resume'] else 0
        sub = find_subtitle(item.uri) if self.settings['sub_auto'] else None
        self._error_shown = False
        self.player.load(item.uri, play=self.cast is None, start=start, sub_uri=sub)
        if self.cast:
            self.cast.load(item.uri, item.title, start)
        self.player.rate = self.settings['speed']
        if show_player is None:
            show_player = self.main_stack.get_visible_child_name() == 'player'
        self.show_page('player' if show_player else 'welcome')
        self.state.add_recent(item.uri, item.title)
        self.state.save()
        self.refresh_recent()
        self._update_title()
        self.hearth.show_media(item.title, '', '', None)
        if start:
            t = Adw.Toast(title=f'Resumed at {fmt_time(start)}', button_label='Start Over', timeout=6)
            t.connect('button-clicked', lambda *_: self.player.seek(0))
            self.toasts.add_toast(t)
        self.spinner.set_visible(not item.uri.startswith('file:'))

    def go(self, step, user=False):
        wrap = self.settings['repeat'] == 'all' or user
        i = self.playlist.neighbor(step, wrap)
        if i >= 0:
            self.play_index(i, show_player=None)
            return True
        return False

    def _previous(self):
        # Like a CD player: the first press goes back to the start of this item.
        pos = self.cast.position if self.cast else self.player.position()
        if pos > 4 or len(self.playlist) < 2:
            self.seek_to(0)
        else:
            self.go(-1, user=True)

    def stop(self):
        if self.cast:
            self.stop_cast(resume_here=False)
        self._save_resume()
        self.player.stop()
        self._refresh_continue()
        self.playlist.set_current(-1)
        self.show_page('welcome')
        self._update_title()
        self._sync_enabled()
        self._sync_inhibit()
        self.big_play.set_visible(False)

    def seek_to(self, seconds, fast=False):
        if self.cast:
            if not fast:
                self.cast.seek(seconds)
            return
        self.player.seek(seconds, fast=fast)

    def _toggle_play(self):
        if self.cast:
            self.cast.toggle()
            return
        if self.player.uri is None and len(self.playlist):
            self.play_index(max(0, self.playlist.current))
            return
        self.player.toggle()

    # ---- player events -------------------------------------------------------------------

    def _on_state(self, _p, state):
        self._sync_big_play()
        self._sync_inhibit()
        if state == 'playing':
            self.spinner.set_visible(False)
            self._arm_hide()
        else:
            self._show_controls()
            if state == 'paused':
                self._save_resume()
        self.hearth.embers.set_running(state == 'playing' and not self.player.has_video)
        if self.mpris and state:
            pass

    # ---- gapless -----------------------------------------------------------------------

    def _plan_next(self):
        """Work out now which file follows, so playbin can start it with no gap.
        Only for music: videos and anything with a resume point start the normal way."""
        from ..library import kind_of
        self._gapless_next, self._gapless_index = None, -1
        p = self.player
        if not p.uri or p.has_video or self._sleep_mode == 'end' or not self.settings['autoplay_next']:
            return
        if self.settings['repeat'] == 'one':
            i = self.playlist.current
        else:
            i = self.playlist.neighbor(1, self.settings['repeat'] == 'all')
        if i < 0 or i >= len(self.playlist):
            return
        uri = self.playlist.items[i].uri
        path = Gio.File.new_for_uri(uri).get_path()
        if not path or kind_of(path) != 'audio' or uri in self.state.resume:
            return
        self._gapless_next, self._gapless_index = uri, i

    def _on_gapless(self, _p, uri):
        i = self._gapless_index
        if not (0 <= i < len(self.playlist)) or self.playlist.items[i].uri != uri:
            i = next((k for k, it in enumerate(self.playlist.items) if it.uri == uri), -1)
        if i >= 0:
            self.playlist.set_current(i)
            item = self.playlist.items[i]
            self.state.add_recent(uri, item.title)
            self.state.save()
            self.refresh_recent()
        self.hearth.show_media(self.media_title(), '', '', None)
        self._update_title()
        self.controls.update_now()
        self._plan_next()
        if self.mpris:
            self.mpris.changed('Metadata')

    # ---- casting ------------------------------------------------------------------------

    def start_cast(self, receiver):
        from ..cast import CastSession, FileServer
        if self.player.uri is None:
            return
        if self.cast:
            self.stop_cast(resume_here=False)
        if self.cast_server is None:
            try:
                self.cast_server = FileServer()
            except OSError as e:
                self.toast(f"Couldn't start casting: {e.strerror}")
                return
        pos = self.player.position()
        self.player.pause()
        session = CastSession(receiver, self.cast_server)
        session.connect('status', self._cast_status)
        session.connect('failed', lambda _s, msg: (self.toast(f'Lost the connection to {receiver.name}. {msg}'),
                                                    self.stop_cast()))
        session.connect('finished', lambda *_: self._on_eos())
        session.connect('started', lambda *_: session.set_volume(self.settings['volume']))
        self.cast = session
        session.position = pos
        session.load(self.player.uri, self.media_title(), pos)
        self.cast_banner.set_title(f'Playing on {GLib.markup_escape_text(receiver.name)}')
        self.cast_banner.set_revealed(True)
        self.controls.cast_btn.add_css_class('accent')
        self.big_play.set_visible(False)
        self.note(f'Sending to {receiver.name}…')

    def _cast_status(self, _s, state, pos, dur):
        p = self.player
        p.emit('position', pos, dur)
        p._set_state('playing' if state in ('playing', 'loading') else 'paused')

    def stop_cast(self, resume_here=True):
        session, self.cast = self.cast, None
        if not session:
            return
        pos = session.position
        session.stop()
        self.cast_banner.set_revealed(False)
        self.controls.cast_btn.remove_css_class('accent')
        if resume_here and self.player.uri:
            self.player.seek(pos)
            self.player._set_state('paused')
            self._sync_big_play()
            self.note('Back on this computer, paused')

    def toggle_mini(self):
        if self.mini:
            self.mini.close()
            return
        from .mini import MiniPlayer
        if self.is_fullscreen():
            self.unfullscreen()
        self.mini = MiniPlayer(self)
        self.mini.present()
        self.set_visible(False)

    # ---- subtitle timing and sleep timer ------------------------------------------------

    def _nudge_subs(self, delta):
        if self.player.uri is None:
            return
        off = 0.0 if delta is None else self.player.sub_offset + delta
        self.player.set_sub_offset(off)
        if abs(off) < 0.05:
            self.note('Subtitle timing: normal')
        else:
            self.note(f"Subtitles {abs(off):.1f} s {'later' if off > 0 else 'earlier'}")

    SLEEP_CHOICES = (('off', 'Off'), ('15', '15 minutes'), ('30', '30 minutes'), ('45', '45 minutes'),
                     ('60', '1 hour'), ('90', '1½ hours'), ('end', 'End of this item'))

    def _sleep_menu(self):
        menu = Gio.Menu()
        for key, label in self.SLEEP_CHOICES:
            menu.append(label, f'win.sleep::{key}')
        return menu

    def set_sleep(self, mode):
        self.sleep_action.set_state(GLib.Variant('s', mode))
        self._sleep_mode = mode
        if self._sleep_timer:
            GLib.source_remove(self._sleep_timer)
            self._sleep_timer = 0
        self._end_fade()
        if mode == 'off':
            self.sleep_btn.set_visible(False)
            self.note('Sleep timer off')
        elif mode == 'end':
            self._sleep_deadline = 0
            self.sleep_btn.set_visible(True)
            self.sleep_btn.set_tooltip_text('Stops at the end of this item. Click to change')
            self.note('Stopping at the end of this item')
        else:
            self._sleep_deadline = time.monotonic() + int(mode) * 60
            self.sleep_btn.set_visible(True)
            self._sleep_timer = GLib.timeout_add_seconds(1, self._sleep_tick)
            self._sleep_tick()
            self.note(f'Stopping in {dict(self.SLEEP_CHOICES)[mode]}')
        self._plan_next()

    def _sleep_tick(self):
        left = self._sleep_deadline - time.monotonic()
        mins = int(left // 60) + 1
        self.sleep_btn.set_tooltip_text(f"Stops in {mins} minute{'' if mins == 1 else 's'}. Click to change")
        if left <= 10 and self.player.state == 'playing':
            # Fade out over the last ten seconds rather than cutting off.
            self._fading = True
            self.player.set_volume(self.settings['volume'] * max(0.0, left) / 10)
        if left <= 0:
            self._sleep_timer = 0
            self._sleep_now()
            return False
        return True

    def _sleep_now(self):
        self.player.pause()
        self._save_resume()
        self._end_fade()
        self._sleep_mode = 'off'
        self.sleep_action.set_state(GLib.Variant('s', 'off'))
        self.sleep_btn.set_visible(False)
        if self.is_fullscreen():
            self.unfullscreen()
        self.toast('Sleep timer: paused')
        self._plan_next()

    def _end_fade(self):
        if self._fading:
            self._fading = False
            self.player.set_volume(self.settings['volume'])

    def _on_ready(self, _p):
        self._plan_next()
        self._sync_enabled()
        self.controls.show_now_playing(self.main_stack.get_visible_child_name() == 'welcome')
        self._update_title()
        item = self.playlist.current_item()
        if item and self.player.duration and not item.duration:
            item.duration = self.player.duration
            self.playlist.emit('item-updated', self.playlist.current)
        if self.player.has_video and self.settings['start_fullscreen'] and not self.is_fullscreen():
            self.fullscreen()

    def _on_video(self, has_video):
        self._on_tags()
        self.media_stack.set_visible_child_name('video' if has_video else 'audio')
        self.hearth.embers.set_running(not has_video and self.player.state == 'playing')
        self._sync_enabled()
        self._sync_big_play()
        self._sync_inhibit()

    def media_title(self):
        p = self.player
        t = p.tags.get('title') or (p.stream_title if p._announced and not p.has_video
                                    and not p.playbin.props.n_video else None)
        item = self.playlist.current_item()
        return t or (item.title if item else '') or (title_for(self.player.uri) if self.player.uri else '')

    def _on_tags(self):
        self._update_title()
        tags = self.player.tags
        self.hearth.show_media(self.media_title(), tags.get('artist'), tags.get('album'), tags.get('image'))
        self.controls.update_now(self.hearth.cover.get_paintable())
        if self.player.uri and self.state.recent and self.state.recent[0]['uri'] == self.player.uri:
            title = self.media_title()
            if title and self.state.recent[0]['title'] != title:
                self.state.recent[0]['title'] = title
                self.state.save()
                self.refresh_recent()

    def _update_title(self):
        if self.player.uri is None:
            self.wtitle.set_title('Fireside')
            self.wtitle.set_subtitle('')
            self.set_title('Fireside')
            return
        title = self.media_title()
        self.wtitle.set_title(title)
        sub = self.player.tags.get('artist', '')
        if len(self.playlist) > 1:
            pos = f'{self.playlist.current + 1} of {len(self.playlist)}'
            sub = f'{sub} · {pos}' if sub else pos
        self.wtitle.set_subtitle(sub)
        self.set_title(f'{title} – Fireside')

    def _on_eos(self):
        uri = self.player.uri
        self.state.forget(uri)
        if self._sleep_mode == 'end':
            self._sleep_now()
            self.player.seek(0)
            return
        if self.settings['repeat'] == 'one':
            self.player.seek(0)
            self.player.play()
            return
        if self.settings['autoplay_next'] and self.go(1):
            return
        # End of the list: rest on the start, paused.
        self.player.pause()
        self.player.seek(0)
        if self.is_fullscreen():
            self.unfullscreen()

    def _on_error(self, msg):
        self.spinner.set_visible(False)
        if not self._error_shown:
            self._error_shown = True
            self.toast(f"Couldn't play this. {msg}")
        if self.settings['autoplay_next'] and len(self.playlist) > 1:
            GLib.timeout_add(1200, lambda: self.go(1) and False)

    # ---- small helpers -------------------------------------------------------------------

    def set_fullscreened(self, on):
        if on:
            self.fullscreen()
        else:
            self.unfullscreen()

    def toast(self, text, timeout=4):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def note(self, text):
        self.osd.set_label(text)
        self.osd_rev.set_reveal_child(True)
        if self._osd_timer:
            GLib.source_remove(self._osd_timer)
        self._osd_timer = GLib.timeout_add(1300, self._hide_note)

    def _hide_note(self):
        self._osd_timer = 0
        self.osd_rev.set_reveal_child(False)
        return False

    def _skip(self, mult):
        if self.player.uri is None:
            return
        n = self.settings['skip_seconds'] * mult
        if self.cast:
            self.cast.seek(max(0.0, self.cast.position + n))
        else:
            self.player.skip(n)
        self.note(('+' if n > 0 else '−') + f'{abs(n)} s')
        if self.mpris:
            GLib.timeout_add(150, lambda: self.mpris.seeked(self.player.position()) and False)

    def _nudge_volume(self, delta):
        v = round(max(0.0, min(1.25, self.settings['volume'] + delta)), 2)
        self.settings.set('volume', v)
        if self.settings['muted'] and delta > 0:
            self.settings.set('muted', False)
        self.note(f'Volume {round(v * 100)}%')

    def _toggle_mute(self):
        self.settings.set('muted', not self.settings['muted'])
        self.note('Muted' if self.settings['muted'] else f"Volume {round(self.settings['volume'] * 100)}%")

    def set_speed(self, rate):
        rate = max(0.25, min(4.0, round(rate, 2)))
        self.settings.set('speed', rate)
        self.speed_action.set_state(GLib.Variant('s', f'{rate:g}'))
        self.player.set_rate(rate)
        self.note(f'Speed {speed_label(rate)}')

    def _step_speed(self, direction):
        cur = self.settings['speed']
        options = [r for r in SPEEDS if (r > cur + 0.001 if direction > 0 else r < cur - 0.001)]
        if options:
            self.set_speed(options[0] if direction > 0 else options[-1])

    def sync_track_states(self):
        if not hasattr(self, 'text_action'):
            return
        self.audio_action.set_state(GLib.Variant('i', max(0, self.player.current_audio())))
        self.text_action.set_state(GLib.Variant('i', self.player.current_text()
                                                if self.player.text_tracks() else -1))

    def _choose_audio(self, action, value):
        self.player.set_audio_track(value.unpack())
        action.set_state(value)
        names = self.player.audio_tracks()
        if 0 <= value.unpack() < len(names):
            self.note(f'Audio: {names[value.unpack()]}')

    def _choose_text(self, action, value):
        i = value.unpack()
        self.player.set_text_track(i)
        action.set_state(value)
        names = self.player.text_tracks()
        self.note('Subtitles off' if i < 0 or i >= len(names) else f'Subtitles: {names[i]}')

    def _cycle_audio(self):
        n = len(self.player.audio_tracks())
        if n > 1:
            self._choose_audio(self.audio_action, GLib.Variant('i', (self.player.current_audio() + 1) % n))

    def _cycle_text(self):
        n = len(self.player.text_tracks())
        if not n:
            self.note('No subtitles')
            return
        cur = self.player.current_text()
        nxt = cur + 1 if cur + 1 < n else -1
        self._choose_text(self.text_action, GLib.Variant('i', nxt))

    def _cycle_fit(self):
        keys = list(FIT_NAMES)
        nxt = keys[(keys.index(self.settings['fit']) + 1) % len(keys)] if self.settings['fit'] in keys else keys[0]
        self.settings.set('fit', nxt)
        self.note(FIT_NAMES[nxt])

    def _toggle_playlist(self, action, value):
        self.split.set_show_sidebar(value.unpack())

    def _sidebar_shown(self, *_):
        shown = self.split.get_show_sidebar()
        self.pl_action.set_state(GLib.Variant('b', shown))
        if not self.split.get_collapsed():
            self.settings.set('playlist_open', shown)

    def _sync_big_play(self):
        p = self.player
        self.big_play.set_visible(self.settings['big_play'] and p.uri is not None and p.has_video
                                  and p.state == 'paused')

    def _sync_inhibit(self):
        want = (self.settings['inhibit'] and self.player.state == 'playing' and self.player.has_video)
        if want and not self._inhibit:
            self._inhibit = self.app.inhibit(self, Gtk.ApplicationInhibitFlags.IDLE, 'Playing a video')
        elif not want and self._inhibit:
            self.app.uninhibit(self._inhibit)
            self._inhibit = 0

    def _save_resume(self):
        if self.player.uri and self.settings['resume'] and self.player.duration > 60:
            self.state.remember(self.player.uri, self.player.position(), self.player.duration)
            self.state.save()

    # ---- auto-hiding controls ------------------------------------------------------------

    def _show_controls(self):
        self.ctl_rev.set_reveal_child(True)
        self.set_cursor(None)
        self.media_stack.set_cursor(None)
        if self.is_fullscreen():
            self.tview.set_reveal_top_bars(True)
        self._hidden = False
        self._arm_hide()

    def _arm_hide(self):
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
        self._hide_timer = GLib.timeout_add(int(self.settings['hide_delay'] * 1000), self._maybe_hide)

    def _maybe_hide(self):
        self._hide_timer = 0
        p = self.player
        if p.state != 'playing' or self.controls.is_busy() or self._hover_controls:
            if p.state == 'playing':
                self._arm_hide()
            return False
        if not p.has_video and not self.is_fullscreen():
            return False
        self.ctl_rev.set_reveal_child(False)
        self.media_stack.set_cursor(Gdk.Cursor.new_from_name('none'))
        if self.is_fullscreen():
            self.tview.set_reveal_top_bars(False)
        self._hidden = True
        return False

    def _on_motion(self, _c, x, y):
        if abs(x - self._last_motion[0]) < 2 and abs(y - self._last_motion[1]) < 2:
            return
        self._last_motion = (x, y)
        if self._hidden:
            self._show_controls()
        else:
            self._arm_hide()

    def _on_click(self, gesture, n, _x, _y):
        if self.player.uri is None:
            return
        if n == 2:
            if self._click_timer:
                GLib.source_remove(self._click_timer)
                self._click_timer = 0
            self.set_fullscreened(not self.is_fullscreen())
        elif n == 1:
            if self._click_timer:
                GLib.source_remove(self._click_timer)
            delay = Gtk.Settings.get_default().props.gtk_double_click_time
            self._click_timer = GLib.timeout_add(delay, self._single_click)

    def _single_click(self):
        self._click_timer = 0
        if self.player.has_video:
            self.player.toggle()
        return False

    def _on_right_click(self, _g, _n, x, y):
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.ctx_menu.set_pointing_to(rect)
        self.ctx_menu.popup()

    def _on_scroll(self, _c, _dx, dy):
        if self.player.uri is None:
            return False
        self._nudge_volume(-0.05 if dy > 0 else 0.05)
        return True

    def _on_fullscreen(self, *_):
        fs = self.is_fullscreen()
        self.tview.set_extend_content_to_top_edge(fs)
        if not fs:
            self.tview.set_reveal_top_bars(True)
        self.controls.full.set_icon_name('view-restore-symbolic' if fs else 'view-fullscreen-symbolic')
        self.controls.full.set_tooltip_text('Leave fullscreen (F)' if fs else 'Fullscreen (F)')
        self._place_controls()

    # ---- keys ----------------------------------------------------------------------------

    KEYS = {
        'space': 'toggle-play', 'k': 'toggle-play', 'Left': 'skip-back', 'Right': 'skip-forward',
        'j': 'skip-back', 'l': 'skip-forward', 'Up': 'volume-up', 'Down': 'volume-down', 'm': 'mute',
        'f': 'fullscreen', 'F11': 'fullscreen', 'n': 'next', 'p': 'previous', 'period': 'step-forward',
        'comma': 'step-back', 'bracketright': 'speed-up', 'bracketleft': 'speed-down',
        'equal': 'speed-reset', 'BackSpace': 'speed-reset', 'v': 'cycle-subtitle', 'b': 'cycle-audio',
        's': 'snapshot', 'g': 'sub-earlier', 'h': 'sub-later', 'a': 'cycle-fit', 'F9': 'playlist', 'AudioPlay': 'toggle-play',
        'AudioPause': 'toggle-play', 'AudioNext': 'next', 'AudioPrev': 'previous', 'AudioStop': 'stop',
    }

    def _on_key(self, _c, keyval, _code, mods):
        if self.get_visible_dialog() is not None:
            return False
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.Editable)):
            return False
        mods &= Gtk.accelerator_get_default_mod_mask()
        name = Gdk.keyval_name(keyval) or ''
        if mods & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        if name == 'Escape':
            if self.is_fullscreen():
                self.unfullscreen()
                return True
            return False
        if mods & Gdk.ModifierType.SHIFT_MASK:
            if name == 'Left':
                self.activate_action('win.skip-back-long')
                return True
            if name == 'Right':
                self.activate_action('win.skip-forward-long')
                return True
            return False
        action = self.KEYS.get(name)
        if not action:
            return False
        # Arrow keys still move through the playlist and menus when those have focus.
        if name in ('Up', 'Down') and isinstance(focus, (Gtk.ListBoxRow, Gtk.ListBox)):
            return False
        if action == 'playlist':
            self.split.set_show_sidebar(not self.split.get_show_sidebar())
            return True
        self.activate_action('win.' + action)
        if action in ('toggle-play', 'skip-back', 'skip-forward'):
            self._show_controls()
        return True

    # ---- dialogs -------------------------------------------------------------------------

    def _media_filter(self):
        f = Gtk.FileFilter(name='Videos and music')
        for t in MEDIA_FILTER_TYPES:
            f.add_mime_type(t)
        for ext in ('mkv', 'mp4', 'webm', 'avi', 'mov', 'm3u', 'm3u8', 'pls', 'flac', 'opus'):
            f.add_suffix(ext)
        every = Gtk.FileFilter(name='All files')
        every.add_pattern('*')
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        store.append(every)
        return store, f

    def _start_folder(self, dlg):
        cur = self.player.uri and Gio.File.new_for_uri(self.player.uri).get_parent()
        if cur and cur.get_path():
            dlg.set_initial_folder(cur)
        else:
            vids = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS)
            if vids and os.path.isdir(vids):
                dlg.set_initial_folder(Gio.File.new_for_path(vids))

    def _pick_files(self, replace):
        dlg = Gtk.FileDialog(title='Open Files' if replace else 'Add Files')
        filters, default = self._media_filter()
        dlg.set_filters(filters)
        dlg.set_default_filter(default)
        self._start_folder(dlg)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            uris = [files.get_item(i).get_uri() for i in range(files.get_n_items())]
            if uris:
                self.open_sources(uris, replace=replace)
        dlg.open_multiple(self, None, done)

    def _pick_folder(self, replace, browse=False):
        dlg = Gtk.FileDialog(title='Open Folder' if replace else 'Add Folder')
        self._start_folder(dlg)

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f:
                (self.browse(f.get_path()) if browse and f.get_path() else self.open_sources([f.get_uri()], replace=replace))
        dlg.select_folder(self, None, done)

    def _pick_subtitle(self):
        dlg = Gtk.FileDialog(title='Add Subtitle File')
        f = Gtk.FileFilter(name='Subtitles')
        for pat in SUB_PATTERNS:
            f.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        self._start_folder(dlg)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except GLib.Error:
                return
            if file:
                self.player.set_subtitle_file(file.get_uri())
                self.note('Subtitles: ' + file.get_basename())
        dlg.open(self, None, done)

    def _ask_location(self):
        dialog = Adw.AlertDialog(heading='Open a Location',
                                 body='Paste the address of a stream or a file on the web.')
        entry = Gtk.Entry(placeholder_text='https://…', activates_default=True, width_chars=40)
        clip = self.get_clipboard()

        def got_text(c, res):
            try:
                text = c.read_text_finish(res)
            except GLib.Error:
                return
            if text and '://' in text and not entry.get_text():
                entry.set_text(text.strip())
                entry.select_region(0, -1)
        clip.read_text_async(None, got_text)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('open', 'Open')
        dialog.set_response_appearance('open', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('open')
        dialog.set_close_response('cancel')

        def responded(_d, resp):
            text = entry.get_text().strip()
            if resp == 'open' and text:
                if '://' not in text and not os.path.exists(os.path.expanduser(text)):
                    text = 'https://' + text
                self.open_sources([os.path.expanduser(text) if '://' not in text else text], replace=True)
        dialog.connect('response', responded)
        dialog.present(self)
        entry.grab_focus()

    def _save_playlist(self):
        dlg = Gtk.FileDialog(title='Save Playlist', initial_name='Playlist.m3u')

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                path = f.get_path()
                if not path.lower().endswith(('.m3u', '.m3u8')):
                    path += '.m3u'
                try:
                    self.playlist.save(path)
                    self.toast(f'Saved {os.path.basename(path)}')
                except OSError as e:
                    self.toast(f"Couldn't save the playlist: {e.strerror}")
        dlg.save(self, None, done)

    def _clear_playlist(self):
        playing = self.playlist.current_item()
        self.playlist.clear()
        if playing and self.player.uri:
            # Keep what's playing so the queue isn't empty under it.
            self.playlist.add([playing.uri])
            self.playlist.set_current(0)
        self._sync_enabled()

    def _clear_recent(self, *_):
        self.state.clear_recent()
        self.state.save()
        self.refresh_recent()

    def _snapshot(self):
        if not self.player.has_video:
            return
        folder = self.settings['snapshot_dir'] or default_snapshot_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.toast(f"Couldn't make the snapshot folder: {e.strerror}")
            return
        name = f"{self.media_title()[:60] or 'Snapshot'} {fmt_time(self.player.position(), True).replace(':', '.')}"
        name = name.replace('/', '-')
        path = os.path.join(folder, name + '.png')
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, f'{name} ({n}).png')
            n += 1
        if self._grab_frame(path) or self.player.snapshot(path):
            t = Adw.Toast(title='Snapshot saved', button_label='Show', timeout=5)
            t.connect('button-clicked', lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(path))
                      .open_containing_folder(self, None, None))
            self.toasts.add_toast(t)
        else:
            self.toast("Couldn't take a snapshot of this frame.")

    def _grab_frame(self, path):
        """Draw the current frame at the video's own size and save it as PNG."""
        from gi.repository import Graphene
        try:
            img = self.player.paintable.get_current_image()
            w, h = img.get_intrinsic_width(), img.get_intrinsic_height()
            if w <= 0 or h <= 0:
                return False
            snap = Gtk.Snapshot()
            img.snapshot(snap, w, h)
            node = snap.to_node()
            if node is None:
                return False
            rect = Graphene.Rect().init(0, 0, w, h)
            return self.get_renderer().render_texture(node, rect).save_to_png(path)
        except Exception:
            return False

    def _effects(self):
        from .effects import EffectsDialog
        EffectsDialog(self.settings).present(self)

    def _info(self):
        p = self.player
        if not p.uri:
            return
        dlg = Adw.Dialog(title='Media Information', content_width=460)
        page = Adw.PreferencesPage()
        rows = []
        t = p.tags
        rows.append(('Title', self.media_title()))
        rows.append(('Artist', t.get('artist')))
        rows.append(('Album', t.get('album')))
        rows.append(('Genre', t.get('genre')))
        path = Gio.File.new_for_uri(p.uri).get_path()
        rows.append(('Location', path or p.uri))
        if path:
            try:
                size = os.path.getsize(path)
                rows.append(('File size', GLib.format_size(size)))
            except OSError:
                pass
        rows.append(('Length', fmt_time(p.duration, p.duration >= 3600) if p.duration else None))
        rows.append(('Container', t.get('container')))
        vi = p.video_info()
        if vi:
            w, h, fps = vi
            rows.append(('Picture', f'{w} × {h}' + (f' at {fps:.3g} frames a second' if fps else '')))
        rows.append(('Video codec', t.get('vcodec')))
        rows.append(('Audio codec', t.get('acodec')))
        if t.get('bitrate'):
            rows.append(('Bit rate', f"{round(t['bitrate'] / 1000)} kb/s"))
        n_a, n_t = len(p.audio_tracks()), len(p.text_tracks())
        rows.append(('Tracks', f'{n_a} audio · {n_t} subtitle' + ('' if n_t == 1 else 's')))
        group = Adw.PreferencesGroup()
        for k, v in rows:
            if v:
                r = Adw.ActionRow(title=k, subtitle=GLib.markup_escape_text(str(v)), subtitle_selectable=True)
                r.add_css_class('property')
                group.add(r)
        page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        dlg.set_child(view)
        dlg.present(self)

    # ---- drops and closing ---------------------------------------------------------------

    def _on_drop(self, _target, value, _x, _y, add=False):
        files = value.get_files() if hasattr(value, 'get_files') else []
        uris = [f.get_uri() for f in files]
        subs = [u for u in uris if u.lower().endswith(tuple(p[1:] for p in SUB_PATTERNS))]
        if subs and self.player.uri and len(subs) == len(uris):
            self.player.set_subtitle_file(subs[0])
            self.note('Subtitles added')
            return True
        if uris:
            self.open_sources(uris, replace=not add)
        return True

    def _on_close(self, *_):
        if self.cast:
            self.stop_cast(resume_here=False)
        if self.cast_server:
            self.cast_server.close()
            self.cast_server = None
        self._save_resume()
        if not self.is_fullscreen() and not self.is_maximized():
            w, h = self.get_default_size()
            self.settings.set('width', w)
            self.settings.set('height', h)
        self.settings.flush()
        self.state.save()
        self.player.shutdown()
        return False


