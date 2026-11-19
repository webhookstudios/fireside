"""Media keys and the system's media controls, through the MPRIS D-Bus interface."""
from gi.repository import Gio, GLib

XML = '''
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="CanSetFullscreen" type="b" access="read"/>
    <property name="Fullscreen" type="b" access="readwrite"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition"><arg direction="in" name="TrackId" type="o"/><arg direction="in" name="Position" type="x"/></method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
'''
PATH = '/org/mpris/MediaPlayer2'
ROOT = 'org.mpris.MediaPlayer2'
PLAYER = 'org.mpris.MediaPlayer2.Player'


class Mpris:
    def __init__(self, app, win):
        self.app = app
        self.win = win
        self.conn = None
        self.ids = []
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, 'org.mpris.MediaPlayer2.fireside',
                                      Gio.BusNameOwnerFlags.NONE, self._acquired, None, None)
        p = win.player
        p.connect('state-changed', lambda *_: self.changed('PlaybackStatus', 'CanPlay', 'CanPause', 'CanSeek'))
        p.connect('tags-changed', lambda *_: self.changed('Metadata'))
        p.connect('ready', lambda *_: self.changed('Metadata', 'CanGoNext', 'CanGoPrevious'))
        win.settings.connect('changed', self._settings_changed)

    def _acquired(self, conn, _name):
        self.conn = conn
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        for iface in info.interfaces:
            self.ids.append(conn.register_object(PATH, iface, self._call, self._get, self._set))

    def _settings_changed(self, _s, key):
        if key == 'volume':
            self.changed('Volume')
        elif key == 'repeat':
            self.changed('LoopStatus')
        elif key == 'shuffle':
            self.changed('Shuffle')
        elif key == 'speed':
            self.changed('Rate')

    def changed(self, *names):
        if not self.conn:
            return
        props = {}
        for n in names:
            v = self._value(PLAYER, n)
            if v is not None:
                props[n] = v
        self.conn.emit_signal(None, PATH, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                              GLib.Variant('(sa{sv}as)', (PLAYER, props, [])))

    def seeked(self, seconds):
        if self.conn:
            self.conn.emit_signal(None, PATH, PLAYER, 'Seeked', GLib.Variant('(x)', (int(seconds * 1e6),)))

    def _call(self, _conn, _sender, _path, iface, method, params, invocation):
        w = self.win
        act = w.activate_action
        if method == 'Raise':
            w.present()
        elif method == 'Quit':
            self.app.quit()
        elif method == 'Next':
            act('win.next')
        elif method == 'Previous':
            act('win.previous')
        elif method == 'Pause':
            w.player.pause()
        elif method == 'Play':
            w.player.play()
        elif method == 'PlayPause':
            act('win.toggle-play')
        elif method == 'Stop':
            act('win.stop')
        elif method == 'Seek':
            w.player.skip(params.unpack()[0] / 1e6)
        elif method == 'SetPosition':
            w.player.seek(params.unpack()[1] / 1e6)
        elif method == 'OpenUri':
            w.open_sources([params.unpack()[0]], replace=True)
        invocation.return_value(None)

    def _get(self, _conn, _sender, _path, iface, prop):
        return self._value(iface, prop)

    def _set(self, _conn, _sender, _path, iface, prop, value):
        s = self.win.settings
        v = value.unpack()
        if prop == 'Volume':
            s.set('volume', max(0.0, min(1.25, float(v))))
        elif prop == 'LoopStatus':
            s.set('repeat', {'None': 'off', 'Playlist': 'all', 'Track': 'one'}.get(v, 'off'))
        elif prop == 'Shuffle':
            s.set('shuffle', bool(v))
        elif prop == 'Rate':
            self.win.set_speed(float(v))
        elif prop == 'Fullscreen':
            self.win.set_fullscreened(bool(v))
        return True

    def _value(self, iface, prop):
        w = self.win
        p = w.player
        s = w.settings
        V = GLib.Variant
        if iface == ROOT:
            return {
                'CanQuit': V('b', True), 'CanRaise': V('b', True), 'CanSetFullscreen': V('b', True),
                'Fullscreen': V('b', w.is_fullscreen()), 'HasTrackList': V('b', False),
                'Identity': V('s', 'Fireside'), 'DesktopEntry': V('s', 'io.github.webhookstudios.Fireside'),
                'SupportedUriSchemes': V('as', ['file', 'http', 'https', 'rtsp', 'rtmp', 'smb', 'sftp']),
                'SupportedMimeTypes': V('as', ['video/*', 'audio/*']),
            }.get(prop)
        has = p.uri is not None
        if prop == 'PlaybackStatus':
            return V('s', {'playing': 'Playing', 'paused': 'Paused'}.get(p.state, 'Stopped'))
        if prop == 'LoopStatus':
            return V('s', {'off': 'None', 'all': 'Playlist', 'one': 'Track'}[s['repeat']])
        if prop == 'Rate':
            return V('d', s['speed'])
        if prop == 'Shuffle':
            return V('b', s['shuffle'])
        if prop == 'Volume':
            return V('d', s['volume'])
        if prop == 'Position':
            return V('x', int(p.position() * 1e6))
        if prop == 'MinimumRate':
            return V('d', 0.25)
        if prop == 'MaximumRate':
            return V('d', 4.0)
        if prop in ('CanGoNext', 'CanGoPrevious'):
            return V('b', len(w.playlist) > 1)
        if prop in ('CanPlay', 'CanPause', 'CanSeek'):
            return V('b', has)
        if prop == 'CanControl':
            return V('b', True)
        if prop == 'Metadata':
            meta = {'mpris:trackid': V('o', '/io/github/webhookstudios/Fireside/' +
                                       (f'track{max(0, w.playlist.current)}' if has else 'none'))}
            if has:
                meta['xesam:title'] = V('s', w.media_title())
                meta['xesam:url'] = V('s', p.uri)
                if p.duration:
                    meta['mpris:length'] = V('x', int(p.duration * 1e6))
                if p.tags.get('artist'):
                    meta['xesam:artist'] = V('as', [p.tags['artist']])
                if p.tags.get('album'):
                    meta['xesam:album'] = V('s', p.tags['album'])
            return V('a{sv}', meta)
        return None

    def close(self):
        if self.conn:
            for i in self.ids:
                self.conn.unregister_object(i)
        Gio.bus_unown_name(self.owner)
