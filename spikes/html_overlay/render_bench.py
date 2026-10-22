"""Spike: how fast can the overlay be rendered offscreen, one frame at a time?

Renders frames of overlay.html at fixed media times in a view that is never
shown, captures each one, reads the frame number back from the binary code the
page draws, and reports ms per frame.

    .venv/bin/python spikes/html_overlay/render_bench.py [--method grab|cdp] [--size 1920x1080]
                                                          [--frames 125] [--start 0] [--out DIR]

grab: QWidget.grab() of the hidden QWebEngineView.
cdp:  Chromium's Page.captureScreenshot over the DevTools protocol (what
      Puppeteer and Remotion use), through QtWebEngine's remote debugging port.
"""
import argparse
import base64
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication

HERE = Path(__file__).resolve().parent
FRAME_MS = 40.0
CDP_PORT = 9333


def read_code(img, scale):
    """Frame number from the 16 squares at the bottom left of the stage."""
    n = 0
    for k in range(16):
        x, y = int((30 + 20 * k + 10) * scale), int(1050 * scale)
        c = img.pixelColor(x, y)
        if c.lightness() > 128 and c.alpha() > 128:
            n |= 1 << k
    return n


def content_rect(img):
    """Bounding box of non-transparent pixels, ignoring the HUD and code strip."""
    a = img.convertToFormat(QImage.Format_ARGB32)
    w, h = a.width(), a.height()
    top_skip = int(h * 0.20)       # HUD
    bottom_skip = int(h * 0.05)    # frame code
    ptr = a.constBits()
    bpl = a.bytesPerLine()
    buf = memoryview(ptr).cast('B')
    xs, ys = [], []
    step = 2
    for y in range(top_skip, h - bottom_skip, step):
        row = buf[y * bpl:(y + 1) * bpl]
        alphas = row[3::4]
        nz = [i for i in range(0, w, step) if alphas[i]]
        if nz:
            ys.append(y)
            xs.append(nz[0])
            xs.append(nz[-1])
    if not ys:
        return QRect()
    return QRect(min(xs), min(ys), max(xs) - min(xs) + step, max(ys) - min(ys) + step)


class OverlayPage(QWebEnginePage):
    def __init__(self, bench, parent):
        super().__init__(parent)
        self.bench = bench

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith('RENDERED '):
            self.bench.on_rendered(int(message.split()[1]))
        elif not message.startswith('STATS'):
            print(f'[js] {message}', flush=True)


class Bench:
    def __init__(self, args, index=0, views=1, done=None):
        self.args = args
        self.index, self.views, self.done = index, views, done
        self.w, self.h = map(int, args.size.split('x'))
        self.scale = self.w / 1920

        self.view = QWebEngineView()
        self.page = OverlayPage(self, self.view)
        self.view.setPage(self.page)
        self.page.setBackgroundColor(Qt.transparent)
        self.view.setAttribute(Qt.WA_DontShowOnScreen)
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.resize(self.w, self.h)
        self.view.show()
        self.view.loadFinished.connect(self.on_loaded)
        self.view.load(QUrl.fromLocalFile(str(HERE / 'overlay.html')))

        self.n = 0
        self.times, self.wrong, self.alpha_ok = [], [], 0
        self.areas, self.png_bytes, self.hashes = [], [], []
        self.out = Path(args.out) if args.out else None
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)

    def frame_no(self):
        return self.args.start + self.index + self.n * self.views

    def count(self):
        return len(range(self.index, self.args.frames, self.views))

    def on_loaded(self, ok):
        if self.index == 0:
            print(f'loaded {ok}, method {self.args.method}, {self.w}x{self.h}', flush=True)
        # Let the first layout and font load settle before timing.
        QTimer.singleShot(1500, self.begin)

    def begin(self):
        if self.args.method == 'cdp':
            self.start_cdp()
        else:
            self.t0 = time.perf_counter()
            self.next_grab()

    # --- grab --------------------------------------------------------------
    def next_grab(self):
        self.t_frame = time.perf_counter()
        f = self.frame_no()
        self.page.runJavaScript(f'renderAt({f * FRAME_MS}, {f}, {self.args.rafs})')

    def on_rendered(self, f):
        img = self.view.grab().toImage()
        self.record(img, time.perf_counter() - self.t_frame)
        self.advance(self.next_grab)

    # --- cdp ---------------------------------------------------------------
    def start_cdp(self):
        # The DevTools server lives in this process and answers on the GUI
        # thread, so a blocking request from here would never return.
        self.nam = QNetworkAccessManager()
        reply = self.nam.get(QNetworkRequest(QUrl(f'http://127.0.0.1:{CDP_PORT}/json')))
        reply.finished.connect(lambda: self.on_targets(reply))

    def on_targets(self, reply):
        targets = json.loads(bytes(reply.readAll()))
        target = next(t for t in targets if t.get('type') == 'page')
        self.ws = QWebSocket()
        self.ws.textMessageReceived.connect(self.on_cdp)
        self.ws.connected.connect(self.on_cdp_connected)
        self.ws.open(QUrl(target['webSocketDebuggerUrl']))
        self.msg_id = 0
        self.pending = {}

    def cdp(self, method, params, then):
        self.msg_id += 1
        self.pending[self.msg_id] = then
        self.ws.sendTextMessage(json.dumps({'id': self.msg_id, 'method': method, 'params': params}))

    def on_cdp(self, text):
        msg = json.loads(text)
        if os.environ.get("CDP_DEBUG"): print("cdp <-", text[:160], flush=True)
        then = self.pending.pop(msg.get('id'), None)
        if then:
            if 'error' in msg:
                print('cdp error', msg['error'], flush=True)
                QApplication.quit()
                return
            then(msg.get('result', {}))

    def on_cdp_connected(self):
        self.cdp('Emulation.setDefaultBackgroundColorOverride',
                 {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0}},
                 lambda _r: self.start_cdp_frames())

    def start_cdp_frames(self):
        self.t0 = time.perf_counter()
        self.next_cdp()

    def next_cdp(self):
        self.t_frame = time.perf_counter()
        f = self.frame_no()
        self.cdp('Runtime.evaluate', {'expression': f'renderAtAsync({f * FRAME_MS})', 'awaitPromise': True},
                 lambda _r: self.cdp('Page.captureScreenshot',
                                     {'format': self.args.cdp_format, 'optimizeForSpeed': True},
                                     self.on_cdp_shot))

    def on_cdp_shot(self, result):
        img = QImage.fromData(base64.b64decode(result['data']))
        self.record(img, time.perf_counter() - self.t_frame)
        self.advance(self.next_cdp)

    # --- bookkeeping -------------------------------------------------------
    def record(self, img, seconds):
        f = self.frame_no()
        self.times.append(seconds * 1000)
        got = read_code(img, self.scale)
        if got != f:
            self.wrong.append((f, got))
        if img.hasAlphaChannel() and img.pixelColor(self.w // 2, self.h // 3).alpha() == 0:
            self.alpha_ok += 1
        if self.args.no_analysis:
            return
        # What a cache entry would cost: the cue's own pixels only.
        rect = content_rect(img)
        self.areas.append(rect.width() * rect.height())
        crop = img.copy(rect) if not rect.isEmpty() else QImage()
        if not crop.isNull():
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            crop.save(buf, 'PNG', 50)
            self.png_bytes.append(buf.size())
            self.hashes.append(hashlib.md5(bytes(crop.constBits())).hexdigest())
        else:
            self.png_bytes.append(0)
            self.hashes.append('empty')
        if self.out and self.n % 10 == 0:
            img.save(str(self.out / f'{self.args.method}_{f:05d}.png'))

    def advance(self, next_fn):
        self.n += 1
        if self.n < self.count():
            next_fn()
        else:
            self.t_end = time.perf_counter()
            if self.done:
                self.done(self)
            else:
                self.report()
                QApplication.quit()

    def report(self):
        total = time.perf_counter() - self.t0
        t = sorted(self.times)
        print(f'frames {len(t)} in {total:.2f} s -> {len(t) / total:.1f} fps '
              f'({len(t) / total / 25:.2f}x real time at 25 fps)')
        print(f'ms/frame mean {statistics.mean(t):.1f} p50 {t[len(t) // 2]:.1f} '
              f'p95 {t[int(len(t) * .95)]:.1f} max {t[-1]:.1f}')
        print(f'wrong frame in image: {len(self.wrong)}' + (f'  e.g. {self.wrong[:5]}' if self.wrong else ''))
        print(f'transparent background: {self.alpha_ok}/{len(t)}')
        full = self.w * self.h
        print(f'cue area mean {statistics.mean(self.areas) / full * 100:.1f}% of frame, '
              f'cropped PNG mean {statistics.mean(self.png_bytes) / 1024:.1f} KB, '
              f'unique images {len(set(self.hashes))}/{len(self.hashes)}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--method', choices=['grab', 'cdp'], default='grab')
    parser.add_argument('--size', default='1920x1080')
    parser.add_argument('--frames', type=int, default=125)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--cdp-format', choices=['png', 'webp', 'jpeg'], default='png')
    parser.add_argument('--out')
    parser.add_argument('--views', type=int, default=1, help='hidden views rendering in parallel (grab only)')
    parser.add_argument('--rafs', type=int, default=2, help='animation frames to wait before capturing (grab only)')
    parser.add_argument('--no-analysis', action='store_true', help='skip the slow per-frame size analysis')
    args = parser.parse_args()
    if args.method == 'cdp':
        os.environ['QTWEBENGINE_REMOTE_DEBUGGING'] = f'127.0.0.1:{CDP_PORT}'
    app = QApplication(sys.argv)
    if args.views == 1:
        bench = Bench(args)
        sys.exit(app.exec())

    finished = []
    t0 = time.perf_counter()

    def done(b):
        finished.append(b)
        if len(finished) < args.views:
            return
        # Wall time from the first view starting to the last one finishing.
        start = min(b.t0 for b in finished)
        total = max(b.t_end for b in finished) - start
        times = sorted(t for b in finished for t in b.times)
        wrong = [w for b in finished for w in b.wrong]
        print(f'{args.views} views: frames {len(times)} in {total:.2f} s -> {len(times) / total:.1f} fps '
              f'({len(times) / total / 25:.2f}x real time at 25 fps)')
        print(f'ms/frame per view mean {statistics.mean(times):.1f} p95 {times[int(len(times) * .95)]:.1f}')
        print(f'wrong frame in image: {len(wrong)}' + (f'  e.g. {wrong[:5]}' if wrong else ''))
        QApplication.quit()

    benches = [Bench(args, i, args.views, done) for i in range(args.views)]
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
