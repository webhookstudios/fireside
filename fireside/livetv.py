"""Free live TV: channel lists from the public, community-kept iptv-org project.

Only channels that broadcasters stream openly are kept. Entries that relay another company's
streaming service through a third-party proxy are dropped.
"""
import hashlib
import json
import locale
import os
import re
import threading
import time
import urllib.request

from gi.repository import GLib, GObject

BASE = 'https://iptv-org.github.io'
CACHE = os.path.join(GLib.get_user_cache_dir(), 'fireside', 'tv')
LOGOS = os.path.join(CACHE, 'logos')
UA = {'User-Agent': 'Fireside media player'}
MAX_AGE = 12 * 3600
# Relays of other companies' services (not the broadcasters' own streams).
SKIP_HOSTS = ('jmp2.uk', 'pluto', 'samsung', 'plex.', 'roku', 'tubi', 'stirr', 'xumo', 'distro.tv',
              'amagi.tv/playlist', 'redbox', 'vizio', 'lg-channels', 'wurl')
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def _extinf(line):
    attrs = dict(ATTR.findall(line))
    if '"' in line:
        name = line[line.rfind('"') + 1:].lstrip(' ,')
    else:
        name = line.split(',', 1)[1] if ',' in line else ''
    return {'logo': attrs.get('tvg-logo', ''), 'group': attrs.get('group-title', ''), 'name': name}


class Channel(GObject.Object):
    def __init__(self, name, url, logo, groups):
        super().__init__()
        self.name = name
        self.url = url
        self.logo = logo
        self.groups = groups
        self.blocked = '[Geo-blocked]' in name
        clean = re.sub(r'\s*\[[^\]]*\]', '', name)
        self.quality = ''
        m = re.search(r'\((\d{3,4}p)\)', clean)
        if m:
            self.quality = m.group(1)
        self.title = re.sub(r'\s*\(\d{3,4}p\)', '', clean).strip() or name


def _get(url, path, max_age):
    """Download to a cache file unless a fresh enough copy is there. Returns the text."""
    try:
        if time.time() - os.path.getmtime(path) < max_age:
            with open(path, encoding='utf-8', errors='replace') as f:
                return f.read()
    except OSError:
        pass
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            data = r.read().decode('utf-8', 'replace')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
        return data
    except OSError:
        # Offline: an old copy is better than nothing.
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                return f.read()
        except OSError:
            return None


def parse(text):
    out, seen = [], set()
    info = None
    for line in (text or '').splitlines():
        line = line.strip()
        if line.startswith('#EXTINF'):
            info = _extinf(line)
            if not info['name']:
                info = None
        elif line and not line.startswith('#') and info:
            url = line
            low = url.lower()
            logo = (info.get('logo') or '')
            if not any(h in low or h in logo.lower() for h in SKIP_HOSTS) and url not in seen:
                seen.add(url)
                groups = [g for g in (info.get('group') or 'Other').split(';') if g and g != 'Undefined'] or ['Other']
                out.append(Channel(info['name'].strip(), url, logo, groups))
            info = None
    out.sort(key=lambda c: c.title.lower())
    return out


def default_country():
    for var in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        val = os.environ.get(var, '')
        m = re.match(r'[a-z]{2}_([A-Z]{2})', val)
        if m:
            return m.group(1).lower()
    try:
        loc = locale.getlocale()[0] or ''
        if '_' in loc:
            return loc.split('_')[1][:2].lower()
    except ValueError:
        pass
    return 'us'


class LiveTV(GObject.Object):
    __gsignals__ = {
        'countries': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'channels': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'failed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'logo': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self):
        super().__init__()
        self.country_list = []   # [(code, name)]
        self.channels = {}       # code -> [Channel]
        self._logo_q = []
        self._logo_lock = threading.Condition()
        self._logo_busy = set()
        for _ in range(4):
            threading.Thread(target=self._logo_worker, daemon=True).start()

    def load_countries(self):
        def work():
            text = _get(f'{BASE}/api/countries.json', os.path.join(CACHE, 'countries.json'), 7 * 86400)
            items = []
            try:
                for c in json.loads(text or '[]'):
                    if c.get('code') and c.get('name'):
                        items.append((c['code'].lower(), c['name']))
            except ValueError:
                pass
            items.sort(key=lambda x: x[1])
            GLib.idle_add(self._got_countries, items)
        threading.Thread(target=work, daemon=True).start()

    def _got_countries(self, items):
        self.country_list = items or [('us', 'United States')]
        self.emit('countries')
        return False

    def load(self, code, force=False):
        def work():
            path = os.path.join(CACHE, f'{code}.m3u')
            text = _get(f'{BASE}/iptv/countries/{code}.m3u', path, 0 if force else MAX_AGE)
            if text is None:
                GLib.idle_add(self.emit, 'failed', code)
                return
            chans = parse(text)
            GLib.idle_add(self._got_channels, code, chans)
        threading.Thread(target=work, daemon=True).start()

    def _got_channels(self, code, chans):
        self.channels[code] = chans
        self.emit('channels', code)
        return False

    # Logos: fetched a few at a time, kept on disk.
    def logo_path(self, url):
        if not url:
            return None
        path = os.path.join(LOGOS, hashlib.md5(url.encode()).hexdigest())
        if os.path.exists(path):
            return path if os.path.getsize(path) else None
        with self._logo_lock:
            if url not in self._logo_busy:
                self._logo_busy.add(url)
                self._logo_q.append(url)
                self._logo_lock.notify()
        return None

    def _logo_worker(self):
        while True:
            with self._logo_lock:
                while not self._logo_q:
                    self._logo_lock.wait()
                url = self._logo_q.pop()   # newest first: what's on screen now
            path = os.path.join(LOGOS, hashlib.md5(url.encode()).hexdigest())
            data = b''
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r:
                    data = r.read(2_000_000)
            except Exception:
                data = b''
            try:
                os.makedirs(LOGOS, exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(data)   # an empty file marks a logo that couldn't be fetched
            except OSError:
                continue
            if data:
                GLib.idle_add(self.emit, 'logo', url, path)
