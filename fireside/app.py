import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402

APP_ID = 'io.github.webhookstudios.Fireside'
HERE = os.path.dirname(os.path.abspath(__file__))

SHORTCUTS = [
    ('Playback', [
        ('space', 'Play or pause'), ('Left', 'Skip back'), ('Right', 'Skip forward'),
        ('<Shift>Left', 'Skip back further'), ('<Shift>Right', 'Skip forward further'),
        ('comma', 'Previous frame'), ('period', 'Next frame'),
        ('bracketleft', 'Slower'), ('bracketright', 'Faster'), ('equal', 'Normal speed'),
        ('p', 'Previous'), ('n', 'Next'),
    ]),
    ('Sound and picture', [
        ('Up', 'Volume up'), ('Down', 'Volume down'), ('m', 'Mute'),
        ('b', 'Next audio track'), ('v', 'Next subtitle track'), ('a', 'Change video size'),
        ('s', 'Take a snapshot'), ('f', 'Fullscreen'), ('Escape', 'Leave fullscreen'),
    ]),
    ('Window', [
        ('<Ctrl>o', 'Open files'), ('<Ctrl><Shift>o', 'Open a folder'), ('<Ctrl>l', 'Open a location'),
        ('F9', 'Show or hide the queue'), ('<Ctrl>e', 'Effects and filters'),
        ('<Ctrl>i', 'Media information'), ('<Ctrl>s', 'Save the queue as a playlist'),
        ('<Ctrl>comma', 'Preferences'), ('<Ctrl>q', 'Quit'),
    ]),
]


class FiresideApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None
        GLib.set_application_name('Fireside')

    def do_startup(self):
        Adw.Application.do_startup(self)
        from . import sedge
        from .settings import Settings, State
        from .themes import ThemeManager
        sedge.apply()
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for d in (os.path.join(os.path.dirname(HERE), 'data', 'icons'), '/usr/share/fireside/icons'):
            if os.path.isdir(d):
                theme.add_search_path(d)
        self.settings = Settings()
        self.state = State()
        self.themes = ThemeManager(self.settings)
        from .library import MusicIndex, PlaylistStore, Thumbnailer, music_dir
        self.thumbs = Thumbnailer()
        self.music = MusicIndex([music_dir()])
        self.playlists = PlaylistStore()

        for name, cb in (('preferences', self._prefs), ('shortcuts', self._shortcuts),
                         ('about', self._about), ('quit', lambda *_: self._quit())):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            self.add_action(a)
        for action, accels in (('app.quit', ['<Ctrl>q']), ('app.preferences', ['<Ctrl>comma']),
                               ('app.shortcuts', ['<Ctrl>question']), ('win.open-files', ['<Ctrl>o']),
                               ('win.open-folder', ['<Ctrl><Shift>o']), ('win.open-location', ['<Ctrl>l']),
                               ('win.effects', ['<Ctrl>e']), ('win.info', ['<Ctrl>i']),
                               ('win.save-playlist', ['<Ctrl>s'])):
            self.set_accels_for_action(action, accels)

    def _window(self):
        if not self.win:
            from .mpris import Mpris
            from .ui.window import FiresideWindow
            self.win = FiresideWindow(self)
            try:
                self.win.mpris = Mpris(self, self.win)
            except Exception:  # Media keys are a nicety; never let them stop the player.
                self.win.mpris = None
        return self.win

    def do_activate(self):
        self._window().present()

    def do_open(self, files, n_files, hint):
        win = self._window()
        win.present()
        win.open_sources([f.get_uri() for f in files], replace=True)

    def _quit(self):
        if self.win:
            self.win.close()
        self.quit()

    def _prefs(self, *_):
        from .ui.prefs import PrefsDialog
        PrefsDialog(self.settings).present(self._window())

    def _shortcuts(self, *_):
        dlg = Adw.ShortcutsDialog()
        for title, items in SHORTCUTS:
            sec = Adw.ShortcutsSection(title=title)
            for accel, label in items:
                sec.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dlg.add(sec)
        dlg.present(self._window())

    def _about(self, *_):
        about = Adw.AboutDialog(
            application_name='Fireside', application_icon=APP_ID, version=__version__,
            developer_name='webhookstudios', license_type=Gtk.License.GPL_2_0,
            website='https://webhookstudioapps.netlify.app',
            issue_url='https://github.com/webhookstudios/fireside/issues',
            comments='A media player for videos, music and streams, with themes to make it your own.',
            copyright='© 2026 webhookstudios')
        about.present(self._window())


def main():
    return FiresideApp().run(sys.argv)
