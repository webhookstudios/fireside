"""Casting to TVs and speakers on the local network.

Two kinds of receiver:
- DLNA / UPnP media renderers (most smart TVs, many speakers), spoken to directly with SSDP and SOAP.
- Chromecast devices, through the pychromecast library when it's installed (python3-pychromecast).

A local file is handed to the TV through a small web server that serves only that one file,
under a random address, and only while casting.
"""
import mimetypes
import os
import secrets
import socket
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gi.repository import Gio, GLib, GObject

SSDP_ADDR = ('239.255.255.250', 1900)
RENDERER = 'urn:schemas-upnp-org:device:MediaRenderer:1'
AVT = 'urn:schemas-upnp-org:service:AVTransport:1'
RCS = 'urn:schemas-upnp-org:service:RenderingControl:1'
EXTRA_TYPES = {'.mkv': 'video/x-matroska', '.webm': 'video/webm', '.m4a': 'audio/mp4', '.flac': 'audio/flac',
               '.opus': 'audio/ogg', '.ogg': 'audio/ogg', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4',
               '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo', '.ts': 'video/mp2t'}


def mime_for(path):
    ext = os.path.splitext(path)[1].lower()
    return EXTRA_TYPES.get(ext) or mimetypes.guess_type(path)[0] or 'application/octet-stream'


def local_ip_towards(host):
    """The address of this computer on the network the receiver is on."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 9))  # UDP connect sends nothing; it only picks the route
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def hms(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def parse_hms(text):
    try:
        parts = [float(p) for p in (text or '').split(':')]
    except ValueError:
        return 0.0
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


# ---- the one-file web server ---------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = 'Fireside'

    def log_message(self, *_):
        pass

    def _target(self):
        files = self.server.files
        key = urllib.parse.urlparse(self.path).path.split('/')[1:2]
        return files.get(key[0]) if key else None

    def do_HEAD(self):
        self._send(head=True)

    def do_GET(self):
        self._send(head=False)

    def _send(self, head):
        path = self._target()
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes='):
            a, _, b = rng[6:].split(',')[0].partition('-')
            try:
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                else:
                    start = max(0, size - int(b))
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        mime = mime_for(path)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('transferMode.dlna.org', 'Streaming')
        self.send_header('contentFeatures.dlna.org', 'DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if head:
            return
        try:
            with open(path, 'rb') as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class FileServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(('0.0.0.0', 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.files = {}
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url_for(self, path, host):
        """Serve just this file (anything served before is dropped) and return its address."""
        token = secrets.token_urlsafe(12)
        self.httpd.files.clear()
        self.httpd.files[token] = path
        name = urllib.parse.quote(os.path.basename(path))
        return f'http://{local_ip_towards(host)}:{self.port}/{token}/{name}'

    def close(self):
        self.httpd.files.clear()
        self.httpd.shutdown()
        self.httpd.server_close()


# ---- receivers -----------------------------------------------------------------------------

class Receiver:
    kind = ''
    name = ''
    host = ''

    def load(self, url, mime, title, start): ...
    def play(self): ...
    def pause(self): ...
    def seek(self, seconds): ...
    def stop(self): ...
    def set_volume(self, value): ...
    def status(self):
        """(state, position, duration). state is playing, paused, loading or stopped."""
        return 'stopped', 0.0, 0.0
    def close(self): ...


class DlnaReceiver(Receiver):
    kind = 'dlna'

    def __init__(self, name, location, avt_url, rcs_url):
        self.name = name
        self.location = location
        self.host = urllib.parse.urlparse(location).hostname
        self.avt_url = avt_url
        self.rcs_url = rcs_url

    def _soap(self, url, service, action, args, timeout=6):
        body = ''.join(f'<{k}>{_xml_escape(str(v))}</{k}>' for k, v in args)
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(url, data=envelope.encode('utf-8'), method='POST', headers={
            'Content-Type': 'text/xml; charset="utf-8"', 'SOAPAction': f'"{service}#{action}"'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
        out = {}
        for el in root.iter():
            tag = el.tag.split('}')[-1]
            if el.text is not None and len(el) == 0:
                out[tag] = el.text
        return out

    def _avt(self, action, *args):
        return self._soap(self.avt_url, AVT, action, [('InstanceID', 0)] + list(args))

    def load(self, url, mime, title, start):
        cls = 'object.item.audioItem.musicTrack' if mime.startswith('audio') else 'object.item.videoItem'
        proto = f'http-get:*:{mime}:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000'
        didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                f'<item id="1" parentID="0" restricted="1"><dc:title>{_xml_escape(title)}</dc:title>'
                f'<upnp:class>{cls}</upnp:class><res protocolInfo="{proto}">{_xml_escape(url)}</res>'
                '</item></DIDL-Lite>')
        try:
            self._avt('Stop')
        except Exception:
            pass
        self._avt('SetAVTransportURI', ('CurrentURI', url), ('CurrentURIMetaData', didl))
        self._avt('Play', ('Speed', 1))
        if start and start > 1:
            # Many TVs only take a seek once playback has begun.
            for _ in range(10):
                state, _p, _d = self.status()
                if state == 'playing':
                    break
                threading.Event().wait(0.5)
            try:
                self.seek(start)
            except Exception:
                pass

    def play(self):
        self._avt('Play', ('Speed', 1))

    def pause(self):
        self._avt('Pause')

    def seek(self, seconds):
        self._avt('Seek', ('Unit', 'REL_TIME'), ('Target', hms(seconds)))

    def stop(self):
        self._avt('Stop')

    def set_volume(self, value):
        if self.rcs_url:
            self._soap(self.rcs_url, RCS, 'SetVolume', [('InstanceID', 0), ('Channel', 'Master'),
                                                        ('DesiredVolume', int(max(0, min(100, value * 100))))])

    def status(self):
        info = self._avt('GetTransportInfo')
        state = {'PLAYING': 'playing', 'PAUSED_PLAYBACK': 'paused', 'TRANSITIONING': 'loading'}.get(
            info.get('CurrentTransportState', ''), 'stopped')
        pos = self._avt('GetPositionInfo')
        return state, parse_hms(pos.get('RelTime')), parse_hms(pos.get('TrackDuration'))


class ChromecastReceiver(Receiver):
    kind = 'chromecast'

    def __init__(self, cast):
        self.cast = cast
        self.name = cast.cast_info.friendly_name if hasattr(cast, 'cast_info') else cast.name
        self.host = cast.cast_info.host if hasattr(cast, 'cast_info') else ''
        self._ready = False

    def _mc(self):
        if not self._ready:
            self.cast.wait(timeout=10)
            self._ready = True
        return self.cast.media_controller

    def load(self, url, mime, title, start):
        mc = self._mc()
        mc.play_media(url, mime, title=title, current_time=start or 0)
        mc.block_until_active(timeout=10)

    def play(self):
        self._mc().play()

    def pause(self):
        self._mc().pause()

    def seek(self, seconds):
        self._mc().seek(seconds)

    def stop(self):
        self._mc().stop()

    def set_volume(self, value):
        self.cast.set_volume(max(0.0, min(1.0, value)))

    def status(self):
        st = self._mc().status
        state = {'PLAYING': 'playing', 'PAUSED': 'paused', 'BUFFERING': 'loading'}.get(
            getattr(st, 'player_state', ''), 'stopped')
        return state, float(getattr(st, 'adjusted_current_time', None) or st.current_time or 0), \
            float(st.duration or 0)

    def close(self):
        try:
            self.cast.disconnect(timeout=2)
        except Exception:
            pass


def _xml_escape(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


# ---- discovery -----------------------------------------------------------------------------

def find_dlna(timeout=3.0):
    msg = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
           f'MX: 2\r\nST: {RENDERER}\r\n\r\n').encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    locations = set()
    try:
        for _ in range(2):
            sock.sendto(msg, SSDP_ADDR)
        end = GLib.get_monotonic_time() + int(timeout * 1e6)
        while GLib.get_monotonic_time() < end:
            try:
                data, _addr = sock.recvfrom(65507)
            except socket.timeout:
                continue
            for line in data.decode('utf-8', 'replace').split('\r\n'):
                if line.lower().startswith('location:'):
                    locations.add(line.split(':', 1)[1].strip())
    finally:
        sock.close()
    found = []
    for loc in locations:
        try:
            found.append(_describe(loc))
        except Exception:
            continue
    return [r for r in found if r]


def _describe(location):
    with urllib.request.urlopen(location, timeout=4) as resp:
        root = ET.fromstring(resp.read())
    ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
    base = root.findtext('d:URLBase', default='', namespaces=ns) or location
    name = None
    avt = rcs = None
    for dev in root.iter('{urn:schemas-upnp-org:device-1-0}device'):
        name = name or dev.findtext('d:friendlyName', namespaces=ns)
        for svc in dev.iter('{urn:schemas-upnp-org:device-1-0}service'):
            stype = svc.findtext('d:serviceType', default='', namespaces=ns)
            ctrl = urllib.parse.urljoin(base, svc.findtext('d:controlURL', default='', namespaces=ns))
            if stype.startswith('urn:schemas-upnp-org:service:AVTransport'):
                avt = avt or ctrl
            elif stype.startswith('urn:schemas-upnp-org:service:RenderingControl'):
                rcs = rcs or ctrl
    if not avt:
        return None
    return DlnaReceiver(name or urllib.parse.urlparse(location).hostname, location, avt, rcs)


def chromecast_available():
    try:
        import pychromecast  # noqa: F401
        return True
    except ImportError:
        return False


def find_chromecasts(timeout=4.0):
    try:
        import pychromecast
    except ImportError:
        return []
    try:
        casts, browser = pychromecast.get_chromecasts(timeout=timeout)
        browser.stop_discovery()
    except Exception:
        return []
    return [ChromecastReceiver(c) for c in casts]


# ---- a casting session ---------------------------------------------------------------------

class CastSession(GObject.Object):
    """Runs every network call on a worker thread and reports back on the main loop."""
    __gsignals__ = {
        'status': (GObject.SignalFlags.RUN_FIRST, None, (str, float, float)),
        'failed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'started': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'finished': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, receiver, server):
        super().__init__()
        self.receiver = receiver
        self.server = server
        self.state = 'loading'
        self.position = 0.0
        self.duration = 0.0
        self._was_playing = False
        self._alive = True
        self._lock = threading.Lock()
        self._poll = GLib.timeout_add(1000, self._tick)

    def _run(self, fn, *args, report=True):
        def work():
            with self._lock:
                try:
                    fn(*args)
                except Exception as e:  # network trouble: say so, don't crash
                    if report:
                        GLib.idle_add(self.emit, 'failed', str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()

    def load(self, uri, title, start):
        path = Gio.File.new_for_uri(uri).get_path()
        if path:
            url = self.server.url_for(path, self.receiver.host)
            mime = mime_for(path)
        else:
            url, mime = uri, mime_for(urllib.parse.urlparse(uri).path)
        self.state = 'loading'
        self._was_playing = False

        def go():
            self.receiver.load(url, mime, title, start)
            GLib.idle_add(self.emit, 'started')
        self._run(go)

    def toggle(self):
        if self.state == 'playing':
            self.state = 'paused'
            self._run(self.receiver.pause)
        else:
            self.state = 'playing'
            self._run(self.receiver.play)

    def seek(self, seconds):
        self.position = seconds
        self._run(self.receiver.seek, seconds)

    def set_volume(self, value):
        self._run(self.receiver.set_volume, value, report=False)

    def _tick(self):
        if not self._alive:
            return False

        def poll():
            try:
                st = self.receiver.status()
            except Exception:
                return
            GLib.idle_add(self._got, *st)
        threading.Thread(target=poll, daemon=True).start()
        return True

    def _got(self, state, pos, dur):
        if not self._alive:
            return False
        if self.state == 'loading' and state == 'stopped':
            return False  # still starting up
        if state == 'playing':
            self._was_playing = True
        elif state == 'stopped' and self._was_playing and dur and pos >= dur - 3 or \
                state == 'stopped' and self._was_playing and self.duration and self.position >= self.duration - 3:
            self._was_playing = False
            self.emit('finished')
        self.state = state
        self.position = pos or self.position
        self.duration = dur or self.duration
        self.emit('status', state, self.position, self.duration)
        return False

    def stop(self):
        self._alive = False
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0
        rec = self.receiver

        def bye():
            try:
                rec.stop()
            except Exception:
                pass
            rec.close()
        threading.Thread(target=bye, daemon=True).start()
