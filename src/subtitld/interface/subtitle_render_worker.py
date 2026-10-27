"""The process that renders the preview's subtitles (see subtitle_renderer).

Subtitld starts it with --subtitle-render-worker and keeps QtWebEngine out of
its own process: there, any Python event filter on the whole application
(the frameless window has one) and a QtWebEngine view crash PySide, and
Chromium going away cannot take the editor with it.

A style is HTML that may come from anyone, so its page reaches nothing but
local files in the style's own folder and Subtitld's, and data: and blob:
URLs: no network, no other file. Chromium's sandbox is on too, where it can
start: where it cannot (Ubuntu 24.04 on keeps unprivileged user namespaces
from programs it has no AppArmor profile for, an AppImage among them), the
first page dies before it is ready, and the helper exits with
NO_SANDBOX_EXIT for Subtitld to start it again without the sandbox
(--no-sandbox: an argument, which reaches it through any wrapper, a
Flatpak's or a Snap's, where the environment may not). As root (a
container), Chromium will not start with it at all: off from the start.

It draws offscreen and in software: no window, no GPU, the same pixels
everywhere. The fonts Subtitld comes with are declared to every page, so a
style that names Montserrat gets it on a system that has not got it. A few views load the style's template with host.js in it; each
renders one job at a time, checks the capture is that job's (the number
host.js draws under the stage, and the view's size), crops it to the
subtitle and sends it back. An urgent job (a subtitle on screen with no
layer yet) goes before the others, and one view is kept for urgent jobs
alone, so rendering ahead never keeps one waiting.

Commands come on stdin, a JSON line each:

    {"cmd": "template", "path": "/…/style.html"}       load a style
    {"cmd": "render", "id": 7, "size": [w, h], "page": {…}, "urgent": false}
    {"cmd": "quit"}

Results go to stdout: a JSON line, then the pixels it announces (`bytes`
of premultiplied ARGB32, w × h, rows w × 4 bytes long):

    {"hello": {"views": 3}}
    {"ready": true}                                    a page is ready, once
    {"id": 7, "rect": [x, y, w, h] or null, "w": …, "h": …, "bytes": …}
"""

import json
import os
import sys
import threading

STRIP = 12                    # host.js: the strip under the stage with the job's number
VIEWS = 3
NO_SANDBOX_EXIT = 75          # Chromium's sandbox cannot start here: start the helper without it

# The fonts Subtitld comes with (graphics/), by file name: family, weight, italic.
_WEIGHTS = {'Thin': 100, 'ExtraLight': 200, 'Light': 300, 'Regular': 400, 'Medium': 500, 'SemiBold': 600,
            'Bold': 700, 'ExtraBold': 800, 'Black': 900}
_UBUNTU = {'Th': 100, 'L': 300, 'R': 400, 'M': 500, 'B': 700}


def _font_face(path):
    """(family, weight, italic) of a font Subtitld comes with, or None."""
    family, _, style = path.stem.partition('-')
    if family == 'Montserrat':
        italic = style.endswith('Italic')
        weight = _WEIGHTS.get(style[:-len('Italic')] if italic else style) or (400 if style == 'Italic' else None)
        return (family, weight, italic) if weight else None
    if family in ('Ubuntu', 'UbuntuMono'):
        if style == 'C':
            return 'Ubuntu Condensed', 400, False
        italic = style.endswith('I') and style != 'I'
        weight = _UBUNTU.get(style[:-1] if italic else style)
        return ('Ubuntu Mono' if family == 'UbuntuMono' else 'Ubuntu', weight, italic) if weight else None
    return None


def bundled_font_faces(folder):
    """@font-face rules for the fonts in `folder`: Chromium finds fonts
    through the system, and these are Subtitld's own (Qt has them; the
    system may not), so a style that names one gets it everywhere."""
    rules = []
    for path in sorted(folder.glob('*.ttf')):
        face = _font_face(path)
        if face is None:
            continue
        family, weight, italic = face
        rules.append(f'@font-face {{ font-family: "{family}"; src: url("{path.as_uri()}"); '
                     f'font-weight: {weight}; font-style: {"italic" if italic else "normal"}; }}')
    return '\n'.join(rules)


def _read_stamp(image, height):
    number = 0
    for k in range(16):
        x, y = 4 + 10 * k + 4, height + 6
        if 0 <= x < image.width() and 0 <= y < image.height():
            colour = image.pixelColor(x, y)
            if colour.alpha() > 128 and colour.lightness() > 128:
                number |= 1 << k
    return number


def main():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    # No display at all: Qt's offscreen platform still reaches for GLX when
    # there is an X display, and aborts where it has none (Xvfb, some VMs).
    os.environ['QT_QPA_OFFSCREEN_NO_GLX'] = '1'
    for name in ('DISPLAY', 'WAYLAND_DISPLAY'):
        os.environ.pop(name, None)
    if '--no-sandbox' in sys.argv[1:] or (hasattr(os, 'geteuid') and os.geteuid() == 0):
        os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
    os.environ['QT_QUICK_BACKEND'] = 'software'
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--disable-gpu' not in flags:
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (flags + ' --disable-gpu').strip()

    from PySide6 import QtWebEngineWidgets  # noqa: F401  (before the application)
    from PySide6.QtCore import QObject, QRect, QTimer, QUrl, Qt, Signal
    from PySide6.QtGui import QImage
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript,
                                         QWebEngineUrlRequestInterceptor)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    from subtitld.modules import session

    host_script = (session.PATH_SUBTITLD / 'subtitle_styles' / 'host.js').read_text(encoding='utf-8')
    fonts_script = ('(() => { const css = document.createElement("style"); css.id = "subtitld-fonts"; '
                    f'css.textContent = {json.dumps(bundled_font_faces(session.PATH_SUBTITLD_GRAPHICS))}; '
                    'document.head.prepend(css); })();')
    out = sys.stdout.buffer

    def send(header, payload=b''):
        out.write(json.dumps(header).encode() + b'\n')
        if payload:
            out.write(payload)
        out.flush()

    class Interceptor(QWebEngineUrlRequestInterceptor):
        """Local files of the style and of Subtitld, data: and blob:;
        nothing else."""

        def __init__(self):
            super().__init__()
            self.roots = (os.path.realpath(session.PATH_SUBTITLD),)

        def allow(self, folder):
            self.roots = (os.path.realpath(session.PATH_SUBTITLD), os.path.realpath(folder))

        def interceptRequest(self, info):
            url = info.requestUrl()
            scheme = url.scheme()
            if scheme in ('data', 'blob', 'about'):
                return
            if scheme == 'file':
                path = os.path.realpath(url.toLocalFile())
                if any(path == root or path.startswith(root + os.sep) for root in self.roots):
                    return
            print(f'[subtitles] a style asked for {url.toDisplayString()[:200]}: refused', file=sys.stderr, flush=True)
            info.block(True)

    class Page(QWebEnginePage):
        def __init__(self, on_message, parent):
            super().__init__(parent)
            self.on_message = on_message

        def javaScriptConsoleMessage(self, level, message, line, source):
            self.on_message(message)

    class View(QObject):
        def __init__(self, worker):
            super().__init__()
            self.worker = worker
            self.job = None
            self.tries = 0
            self.ready = False
            self.template = None
            self.crashes = 0
            self.view = QWebEngineView()
            self.page = Page(self.on_message, self.view)
            self.view.setPage(self.page)
            self.page.setBackgroundColor(Qt.transparent)
            self.view.setAttribute(Qt.WA_DontShowOnScreen)
            self.view.setAttribute(Qt.WA_TranslucentBackground)
            self.view.resize(16, 16 + STRIP)
            self.view.show()
            self.page.renderProcessTerminated.connect(self.on_terminated)
            script = QWebEngineScript()
            script.setName('subtitld-host')
            script.setSourceCode(fonts_script + '\n' + host_script)
            script.setInjectionPoint(QWebEngineScript.DocumentReady)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(False)
            self.page.scripts().insert(script)

        def load(self, template):
            if self.job is not None:
                self.worker.pending.insert(0, self.job)
            self.template, self.ready, self.job = template, False, None
            self.view.load(QUrl.fromLocalFile(template))

        def on_message(self, message):
            if message == 'SUBTITLD_READY':
                # The first layout and the fonts settle before the first capture.
                QTimer.singleShot(300, self.on_ready)
            elif message.startswith('SUBTITLD_RENDERED ') and self.job is not None:
                self.on_rendered(json.loads(message.split(' ', 1)[1]))

        def on_ready(self):
            self.ready = True
            if not self.worker.ever_ready:
                self.worker.ever_ready = True
                send({'ready': True})
            self.worker.schedule()

        def on_terminated(self, status, code):
            # Chromium's renderer went away: load again, a few times.
            self.crashes += 1
            print(f'[subtitles] a renderer stopped ({status}, {code})', file=sys.stderr, flush=True)
            if not self.worker.ever_ready and not os.environ.get('QTWEBENGINE_DISABLE_SANDBOX'):
                # Before any page was ready: the sandbox cannot start here.
                QApplication.exit(NO_SANDBOX_EXIT)
                return
            if self.job is not None:
                self.worker.pending.insert(0, self.job)
                self.job = None
            if self.template is not None and self.crashes <= 3:
                self.load(self.template)

        def start(self, job):
            self.job, self.tries = job, 0
            self.run()

        def run(self):
            width, height = self.job['size']
            if self.view.size().toTuple() != (width, height + STRIP):
                self.view.resize(width, height + STRIP)
            page = dict(self.job['page'], id=self.job['id'] & 0xffff, view=[width, height + STRIP])
            self.page.runJavaScript(f'window.subtitld && subtitld.render({json.dumps(page)})')

        def on_rendered(self, answer):
            job = self.job
            width, height = job['size']
            if answer.get('id') != job['id'] & 0xffff:
                return                     # an answer to a job before (one dropped by a reload)
            image = self.view.grab().toImage()
            if (answer.get('view') != [width, height + STRIP] or _read_stamp(image, height) != job['id'] & 0xffff) \
                    and self.tries < 5:
                # Captured before Chromium drew the job, or at another size: again.
                self.tries += 1
                self.run()
                return
            self.job = None
            rect = answer.get('rect')
            if rect is None:
                send({'id': job['id'], 'rect': None})
            else:
                x, y, w, h = rect
                crop = image.copy(QRect(x, y, w, h)).convertToFormat(QImage.Format_ARGB32_Premultiplied)
                data = bytes(crop.constBits())[:crop.width() * crop.height() * 4]
                send({'id': job['id'], 'rect': rect, 'w': crop.width(), 'h': crop.height(), 'bytes': len(data)},
                     data)
            self.worker.schedule()

    class Worker(QObject):
        command = Signal(object)

        def __init__(self):
            super().__init__()
            self.pending = []
            self.ever_ready = False
            self.views = [View(self) for _ in range(VIEWS)]
            self.command.connect(self.on_command)

        def on_command(self, message):
            if message is None or message.get('cmd') == 'quit':
                QApplication.quit()
            elif message.get('cmd') == 'template':
                # Jobs for the style before are of no use now.
                self.pending.clear()
                interceptor.allow(os.path.dirname(message['path']))
                for view in self.views:
                    view.job = None
                    view.load(message['path'])
            elif message.get('cmd') == 'render':
                if message.get('urgent'):
                    # After the urgent ones already waiting, before the rest.
                    at = next((i for i, job in enumerate(self.pending) if not job.get('urgent')), len(self.pending))
                    self.pending.insert(at, message)
                else:
                    self.pending.append(message)
                self.schedule()

        def schedule(self):
            for index, view in enumerate(self.views):
                if not self.pending:
                    return
                if not view.ready or view.job is not None:
                    continue
                if index == 0:
                    # Kept for urgent jobs.
                    if self.pending[0].get('urgent'):
                        view.start(self.pending.pop(0))
                else:
                    view.start(self.pending.pop(0))

    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    interceptor = Interceptor()
    QWebEngineProfile.defaultProfile().setUrlRequestInterceptor(interceptor)
    worker = Worker()

    def read():
        for line in sys.stdin.buffer:
            try:
                worker.command.emit(json.loads(line))
            except ValueError:
                continue
        worker.command.emit(None)     # Subtitld is gone

    threading.Thread(target=read, daemon=True, name='subtitle-render-commands').start()
    send({'hello': {'views': VIEWS}})
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
