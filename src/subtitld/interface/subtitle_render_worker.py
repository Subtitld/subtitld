"""The process that renders the preview's subtitles (see subtitle_renderer).

Subtitld starts it with --subtitle-render-worker and keeps QtWebEngine out of
its own process: there, any Python event filter on the whole application
(the frameless window has one) and a QtWebEngine view crash PySide, and
Chromium going away cannot take the editor with it.

It draws offscreen and in software: no window, no GPU, the same pixels
everywhere. A few views load the style's template with host.js in it; each
renders one job at a time, checks the capture is that job's (the number
host.js draws under the stage, and the view's size), crops it to the
subtitle and sends it back.

Commands come on stdin, a JSON line each:

    {"cmd": "template", "path": "/…/style.html"}       load a style
    {"cmd": "render", "id": 7, "size": [w, h], "page": {…}}
    {"cmd": "quit"}

Results go to stdout: a JSON line, then the pixels it announces (`bytes`
of premultiplied ARGB32, w × h, rows w × 4 bytes long):

    {"hello": {"views": 3}}
    {"id": 7, "rect": [x, y, w, h] or null, "w": …, "h": …, "bytes": …}
"""

import json
import os
import sys
import threading

STRIP = 12                    # host.js: the strip under the stage with the job's number
VIEWS = 3


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
    os.environ['QT_QUICK_BACKEND'] = 'software'
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--disable-gpu' not in flags:
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (flags + ' --disable-gpu').strip()

    from PySide6 import QtWebEngineWidgets  # noqa: F401  (before the application)
    from PySide6.QtCore import QObject, QRect, QTimer, QUrl, Qt, Signal
    from PySide6.QtGui import QImage
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    from subtitld.modules import session

    host_script = (session.PATH_SUBTITLD / 'subtitle_styles' / 'host.js').read_text(encoding='utf-8')
    out = sys.stdout.buffer

    def send(header, payload=b''):
        out.write(json.dumps(header).encode() + b'\n')
        if payload:
            out.write(payload)
        out.flush()

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
            script.setSourceCode(host_script)
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
            self.worker.schedule()

        def on_terminated(self, status, code):
            # Chromium's renderer went away: load again, a few times.
            self.crashes += 1
            print(f'[subtitles] a renderer stopped ({status}, {code})', file=sys.stderr, flush=True)
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
            self.views = [View(self) for _ in range(VIEWS)]
            self.command.connect(self.on_command)

        def on_command(self, message):
            if message is None or message.get('cmd') == 'quit':
                QApplication.quit()
            elif message.get('cmd') == 'template':
                # Jobs for the style before are of no use now.
                self.pending.clear()
                for view in self.views:
                    view.job = None
                    view.load(message['path'])
            elif message.get('cmd') == 'render':
                self.pending.append(message)
                self.schedule()

        def schedule(self):
            for view in self.views:
                if not self.pending:
                    return
                if view.ready and view.job is None:
                    view.start(self.pending.pop(0))

    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
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
    app.exec()
    return 0


if __name__ == '__main__':
    sys.exit(main())
