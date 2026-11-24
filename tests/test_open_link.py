"""Opening a video from a link (modules/media_links.py, the `media.download` add-on task).

What a link is, what a server says is at it, the file downloaded (and nothing left behind when stopped), and
an add-on getting the video of a page through the real AddonManager / AddonMediaDownloadProvider stack (the
mock in addons_mock/). Everything is served from this computer.

Standalone script, like the other suites here; puts this checkout's src/ on the path itself.
"""
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
sys.path.insert(0, str(HERE / 'addons_mock'))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from subtitld.modules import media_links, session  # noqa: E402
from subtitld.modules.addons import installer  # noqa: E402
from subtitld.modules.addons.manager import ADDON_PROVIDER_CLASSES, get_manager  # noqa: E402
from subtitld.modules.addons.provider import TASK_MEDIA_DOWNLOAD  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f'\n     got  {got!r}\n     want {want!r}'))
    if not ok:
        fails.append(name)


# ---- What a link is ----
parse = media_links.parse
check('a scheme is added', parse('youtube.com/watch?v=x'), 'https://youtube.com/watch?v=x')
check('http stays http', parse(' http://a.example/v.mp4 '), 'http://a.example/v.mp4')
check('not a link', parse('hello'), None)
check('words are not a link', parse('a video.mp4'), None)
check('no files', parse('file:///etc/passwd'), None)
check('no other schemes', parse('ftp://a.example/v.mp4'), None)
check('an address', parse('http://127.0.0.1:8000/v.mp4'), 'http://127.0.0.1:8000/v.mp4')

# ---- A server ----
VIDEO = os.urandom(300_000)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, headers, head=False):
        self.send_response(200)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _route(self, head):
        path = self.path.split('?')[0]
        if path == '/clip.mp4':
            self._send(VIDEO, {'Content-Type': 'video/mp4'}, head)
        elif path == '/download':
            self._send(VIDEO, {'Content-Type': 'application/octet-stream',
                               'Content-Disposition': 'attachment; filename="../My film.webm"'}, head)
        elif path == '/blob':
            self._send(VIDEO, {'Content-Type': 'application/octet-stream'}, head)
        elif path == '/page':
            self._send(b'<html><body>a page</body></html>', {'Content-Type': 'text/html; charset=utf-8'}, head)
        elif path == '/list.m3u8':
            self._send(b'#EXTM3U\n', {'Content-Type': 'application/vnd.apple.mpegurl'}, head)
        elif path == '/nohead.mp4':
            if head:
                self.send_response(405)
                self.end_headers()
                return
            if self.headers.get('Range') == 'bytes=0-0':
                self.send_response(206)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Range', f'bytes 0-0/{len(VIDEO)}')
                self.send_header('Content-Length', '1')
                self.end_headers()
                self.wfile.write(VIDEO[:1])
                return
            self._send(VIDEO, {'Content-Type': 'video/mp4'})
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self._route(True)

    def do_GET(self):
        self._route(False)


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{server.server_address[1]}'

# ---- What is at a link ----
kind, info = media_links.probe(f'{BASE}/clip.mp4')
check('a video file', (kind, info['name'], info['size']), ('file', 'clip.mp4', len(VIDEO)))
kind, info = media_links.probe(f'{BASE}/download')
check("the server's name, without folders", (kind, info['name']), ('file', 'My film.webm'))
check('a file of any type is not a video by its look', media_links.probe(f'{BASE}/blob')[0], 'page')
check('a web page', media_links.probe(f'{BASE}/page')[0], 'page')
check('a playlist is for the add-on', media_links.probe(f'{BASE}/list.m3u8')[0], 'page')
kind, info = media_links.probe(f'{BASE}/nohead.mp4')
check('a server that refuses HEAD', (kind, info['size']), ('file', len(VIDEO)))
try:
    media_links.probe(f'{BASE}/missing')
    check('a missing page raises', False, True)
except OSError:
    check('a missing page raises', True, True)

# ---- The file ----
folder = tempfile.mkdtemp()
moved = []
path = media_links.download(media_links.probe(f'{BASE}/clip.mp4')[1], folder, lambda d, t: moved.append((d, t)))
check('downloaded', Path(path).read_bytes() == VIDEO, True)
check('progress to the end', moved[-1], (len(VIDEO), len(VIDEO)))
again = media_links.download(media_links.probe(f'{BASE}/clip.mp4')[1], folder)
check('nothing is replaced', os.path.basename(again), 'clip (2).mp4')
try:
    media_links.download(media_links.probe(f'{BASE}/clip.mp4')[1], folder, cancelled=lambda: True)
    check('stopped', False, True)
except media_links.Cancelled:
    check('stopped', True, True)
check('nothing left of a stopped one', sorted(os.listdir(folder)), ['clip (2).mp4', 'clip.mp4'])

# ---- The add-on ----
check('installable', TASK_MEDIA_DOWNLOAD in installer.supported_tasks(), True)
check('a provider class', ADDON_PROVIDER_CLASSES[TASK_MEDIA_DOWNLOAD].__name__, 'AddonMediaDownloadProvider')

import mock_media_addon  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication(sys.argv)
mock_media_addon.install(session.PATH_SUBTITLD_ADDONS)
manager = get_manager()
manager.discover()
providers = manager.providers_for_task(TASK_MEDIA_DOWNLOAD)
check('discovered', [p.id for p in providers], ['mock-media'])
provider = providers[0]


def run(url, cancel_after=None):
    outcome = {'progress': []}

    def done(path, info):
        outcome['result'] = (path, info)
        app.quit()

    def failed(code, message):
        outcome['error'] = (code, message)
        app.quit()

    provider.progress.connect(lambda value, message: outcome['progress'].append(value))
    provider.finished.connect(done)
    provider.error.connect(failed)
    QTimer.singleShot(20000, app.quit)
    if cancel_after is not None:
        QTimer.singleShot(cancel_after, provider.cancel)
    provider.download(url, os.path.join(folder, 'pages'))
    app.exec()
    for signal in (provider.progress, provider.finished, provider.error):
        signal.disconnect()
    return outcome


outcome = run(f'{BASE}/page')
check('a page: its video', os.path.basename(outcome.get('result', ('', {}))[0]), 'mock video [abc].mp4')
check('a page: its title', outcome.get('result', ('', {}))[1], {'title': 'mock video'})
check('a page: progress', outcome['progress'][-1:], [1.0])
outcome = run(f'{BASE}/nothing')
check('no video there', outcome.get('error', ('',))[0], 'unsupported_url')
outcome = run(f'{BASE}/slow', cancel_after=500)
check('cancelled', outcome.get('error', ('',))[0], 'cancelled')
QTimer.singleShot(1500, app.quit)
app.exec()  # the add-on removes its part file
check('nothing left of a cancelled one', sorted(os.listdir(os.path.join(folder, 'pages'))), ['mock video [abc].mp4'])
manager.shutdown_all()
server.shutdown()

print()
print(f'{len(fails)} failure(s)' if fails else 'all ok')
sys.exit(1 if fails else 0)
