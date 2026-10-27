"""The preview's subtitles, rendered as HTML and drawn with the video.

Each subtitle style is an HTML template (modules.subtitle_styles). A helper
process (subtitle_render_worker) loads the current style's template, with
subtitle_styles/host.js in it, and renders one subtitle at a time: the stage
the size the preview shows the video at, the subtitle on it, and, for an
animated style, its animations held at one moment. It sends back the capture
cropped to the subtitle, kept here as a layer. The preview draws the layer in
the video item's paint(), with the very frame it belongs with, so the two
never drift apart.

A layer is kept by a key of everything that changes its pixels: the style,
its template, its settings, the text, the size, the title-safe area and, for
an animated style, the subtitle's duration and the moment. Change any of them
and it is another layer; change one back and the old one is still there.

Layers are kept on disk too (subtitle_disk_cache), so a project opened
again, or a setting set back days later, reads them instead of rendering.

Asked for a layer it does not have yet, the renderer queues it and answers
with the last layer it drew for that subtitle, if any (stretched to the new
size, or the text before an edit), so the subtitle does not blink while its
new layer renders. `layer_ready` says when to paint again. A subtitle on
screen with no layer is urgent: it goes to the helper at once, before what
is rendered ahead, and the helper keeps a view for urgent jobs alone.
Rendering ahead (the subtitles after the playhead, so each is ready when it
comes) starts again from wherever the playhead goes.

The helper starts a second after Subtitld does (warm_up), or with the first
subtitle asked for if that is sooner, and again if it stops (a few times).
"""

import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRectF, QTimer, Signal, qVersion
from PySide6.QtGui import QImage

from subtitld.modules import markup, session, subtitle_styles

HOST_SCRIPT = session.PATH_SUBTITLD / 'subtitle_styles' / 'host.js'
DISK_CACHE = session.PATH_SUBTITLD_USER_CACHE / 'subtitle_layers.sqlite'
DISK_LIMIT_MB = 512
DISK_LOADS = 32              # layers read from disk at once, at most
WORKER_FLAG = '--subtitle-render-worker'
PAD = 0.03                   # around the cue, of the stage's height: shadows and glows
RAM_LIMIT = 192 * 2**20      # bytes of layers kept
AHEAD = 30.0                 # seconds of subtitles rendered ahead of the playhead
AHEAD_ANIMATED = 2.0         # an animated subtitle: a layer a frame, this far ahead
SIZE_SETTLE_MS = 200         # a size has to hold this long before layers are rendered at it
RESTARTS = 3                 # times the helper is started again after it stops
NO_SANDBOX_EXIT = 75         # subtitle_render_worker: Chromium's sandbox cannot start here


def available():
    """Whether this build has QtWebEngine (Haiku's has not)."""
    try:
        return importlib.util.find_spec('PySide6.QtWebEngineWidgets') is not None
    except (ImportError, ValueError):
        return False


def _digest(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def worker_command():
    """How to start the helper: this executable, with the worker's flag."""
    if getattr(sys, 'frozen', False):
        return sys.executable, [WORKER_FLAG]
    return sys.executable, ['-m', 'subtitld', WORKER_FLAG]


class SubtitleRenderer(QObject):
    """Layers of the preview's subtitles. Ask with `layers`; paint again on
    `layer_ready`."""

    layer_ready = Signal()

    def __init__(self, parent=None, disk_cache=DISK_CACHE):
        """`disk_cache`: the file layers are kept in, or None for none."""
        super().__init__(parent)
        # What every page gets besides its template: host.js and Subtitld's fonts.
        fonts = sorted(path.name for path in session.PATH_SUBTITLD_GRAPHICS.glob('*.ttf'))
        self._host_digest = hashlib.sha1(HOST_SCRIPT.read_bytes() + json.dumps(fonts).encode()).hexdigest()
        self.disk = None
        if disk_cache is not None:
            from subtitld.interface.subtitle_disk_cache import SubtitleDiskCache
            try:
                disk_cache.parent.mkdir(parents=True, exist_ok=True)
                self.disk = SubtitleDiskCache(disk_cache, DISK_LIMIT_MB)
                self.disk.loaded.connect(self._loaded)
            except Exception as error:      # no disk cache is slower, not broken
                print(f'[subtitles] no layers on disk: {error}', flush=True)
                self.disk = None
        self.loading = set()            # keys being read from disk
        self.disk_timer = QTimer(self)
        self.disk_timer.setInterval(10_000)
        self.disk_timer.timeout.connect(lambda: self.disk and self.disk.maintain())
        if self.disk is not None:
            self.disk_timer.start()
        self.cache = OrderedDict()      # key -> (QRectF of the stage, 0..1, or None; QImage or None)
        self.bytes = 0
        self.last = {}                  # id(segment) -> key of the layer last drawn for it
        self.queue = OrderedDict()      # key -> job, waiting
        self.jobs = {}                  # key -> job, of the keys asked for lately (a read from disk may fail)
        self.sent = {}                  # job id -> job, with the helper
        self.next_id = 1
        self.size = None                # (width, height) in device pixels layers are rendered at
        self.pending_size = None
        self.size_timer = QTimer(self)
        self.size_timer.setSingleShot(True)
        self.size_timer.timeout.connect(self._settle_size)
        self.stats = {'rendered': 0, 'from_disk': 0, 'hits': 0, 'stand_ins': 0, 'misses': 0, 'restarts': 0}
        self.urgent = set()             # keys of queued or sent jobs a subtitle on screen waits for
        self.waiting = {}               # key -> when a subtitle on screen first lacked it
        self.gaps = []                  # ms subtitles on screen waited for their layer
        self._style_key = None
        self._template = None
        self.process = None
        self.no_sandbox = False         # Chromium's sandbox could not start here
        self.helper_ready = False       # the helper ever had a page ready
        self.slots = 0                  # jobs the helper takes at once
        self.buffer = b''
        self.header = None

    # --- the helper --------------------------------------------------------
    def _start(self):
        if self.process is not None or self.stats['restarts'] > RESTARTS:
            return
        self.process = QProcess(self)
        program, arguments = worker_command()
        environment = QProcessEnvironment.systemEnvironment()
        if self.no_sandbox:
            arguments = [*arguments, '--no-sandbox']
        if not getattr(sys, 'frozen', False):
            # The helper imports this very copy of Subtitld.
            path = str(session.PATH_SUBTITLD.parent)
            known = environment.value('PYTHONPATH')
            environment.insert('PYTHONPATH', path + (os.pathsep + known if known else ''))
        self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QProcess.ForwardedErrorChannel)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.start(program, arguments)
        self.slots = 0
        self.buffer, self.header = b'', None
        if self._template is not None:
            self._send({'cmd': 'template', 'path': str(self._template)})

    def _send(self, message):
        if self.process is not None:
            self.process.write(json.dumps(message).encode() + b'\n')

    def _finished(self, code, status):
        process, self.process = self.process, None
        if process is not None:
            process.deleteLater()
        self.slots = 0
        # What the helper had is asked for again.
        for job in reversed(list(self.sent.values())):
            self.queue[job['key']] = job
            self.queue.move_to_end(job['key'], last=False)
        self.sent.clear()
        if (code == NO_SANDBOX_EXIT or not self.helper_ready) and not self.no_sandbox:
            # The sandbox could not start (the helper said so, or it never
            # got as far as a page): once more, without it.
            # Its pages still reach nothing but local files (subtitle_render_worker).
            print('[subtitles] Chromium\'s sandbox cannot start here: rendering without it', flush=True)
            self.no_sandbox = True
            self._start_and_schedule()
            return
        self.stats['restarts'] += 1
        print(f'[subtitles] the subtitle renderer stopped (exit {code})', flush=True)
        if self.queue:
            QTimer.singleShot(500, self._start_and_schedule)

    def _start_and_schedule(self):
        self._start()
        self.schedule()

    def _read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while True:
            if self.header is None:
                end = self.buffer.find(b'\n')
                if end < 0:
                    return
                try:
                    self.header = json.loads(self.buffer[:end])
                except ValueError:
                    self.header = {}
                self.buffer = self.buffer[end + 1:]
            size = self.header.get('bytes', 0)
            if len(self.buffer) < size:
                return
            header, data = self.header, self.buffer[:size]
            self.buffer, self.header = self.buffer[size:], None
            self._answer(header, data)

    def _answer(self, header, data):
        if header.get('ready'):
            self.helper_ready = True
            return
        if 'hello' in header:
            # Rendering ahead: two jobs a view, but for the one kept for urgent jobs.
            self.slots = max(1, int(header['hello'].get('views', 1)) - 1) * 2
            self.schedule()
            return
        job = self.sent.pop(header.get('id'), None)
        if job is not None and job['style'] == self._style_key and job['size'] == self.size:
            rect = header.get('rect')
            if rect is None:
                layer = (None, None)
            else:
                width, height = job['size']
                x, y, w, h = rect
                image = QImage(data, header['w'], header['h'], header['w'] * 4,
                               QImage.Format_ARGB32_Premultiplied).copy()
                layer = (QRectF(x / width, y / height, w / width, h / height), image)
            self.stats['rendered'] += 1
            if self.disk is not None:
                self.disk.put(job['key'], *layer)
            self._store(job['key'], layer)
        self.schedule()

    def _loaded(self, key, layer):
        """A layer read from disk, or None when it was not there after all."""
        self.loading.discard(key)
        if layer is None:
            if key in self.urgent or key in self.waiting:
                self.urgent.add(key)
                self._enqueue_render(key, first=True)
        else:
            self.stats['from_disk'] += 1
            self._store(key, layer)
        self.schedule()

    def _store(self, key, layer):
        if key in self.cache:
            return
        if layer[1] is not None:
            self.bytes += layer[1].sizeInBytes()
        self.cache[key] = layer
        self.jobs.pop(key, None)
        self.urgent.discard(key)
        asked = self.waiting.pop(key, None)
        if asked is not None:
            self.gaps.append((time.perf_counter() - asked) * 1000)
            if os.environ.get('SUBTITLD_DEBUG_RENDER'):
                print(f'[subtitles] waited {self.gaps[-1]:.0f} ms for a layer', flush=True)
        while self.bytes > RAM_LIMIT and len(self.cache) > 1:
            _key, (_rect, old) = self.cache.popitem(last=False)
            if old is not None:
                self.bytes -= old.sizeInBytes()
        self.layer_ready.emit()

    def warm_up(self):
        """Start the helper and load the style now, before the first
        subtitle (Chromium takes most of a second to start)."""
        self._style()
        self._start()

    def close(self):
        if os.environ.get('SUBTITLD_DEBUG_RENDER'):
            print(f'[subtitles] {self.stats}', flush=True)
        self.disk_timer.stop()
        if self.disk is not None:
            self.disk.close()
        if self.process is not None:
            process, self.process = self.process, None
            process.finished.disconnect(self._finished)
            process.write(b'{"cmd": "quit"}\n')
            process.closeWriteChannel()
            if not process.waitForFinished(2000):
                process.kill()
                process.waitForFinished(1000)

    # --- the style ---------------------------------------------------------
    def _style(self):
        """The current style and the part of every key it gives; the helper
        is told when the template is another."""
        style = subtitle_styles.get()
        template = style.path / style.template
        settings = subtitle_styles.values(style.id)
        variables = subtitle_styles.css_variables(style.id, settings)
        attributes = subtitle_styles.html_attributes(style.id, settings)
        try:
            source = template.read_bytes()
        except OSError:
            source = b''
        key = _digest(style.id, hashlib.sha1(source).hexdigest(), self._host_digest, qVersion(),
                      variables, attributes, session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10),
                      session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10))
        if key != self._style_key:
            self._style_key = key
            self.queue.clear()
            self.urgent.clear()
            self.waiting.clear()
            self.jobs.clear()
            if template != self._template:
                self._template = template
                self.sent.clear()
                self._send({'cmd': 'template', 'path': str(template)})
        return style, variables, attributes

    # --- size --------------------------------------------------------------
    def set_size(self, width, height):
        """The size the preview shows the video at, in device pixels. Layers
        follow it once it holds still (a window being resized, a panel
        sliding in)."""
        size = (max(16, int(round(width))), max(16, int(round(height))))
        if self.size is None:
            self.size = size
        elif size != self.size and size != self.pending_size:
            self.pending_size = size
            self.size_timer.start(SIZE_SETTLE_MS)
        elif size == self.size:
            self.pending_size = None
            self.size_timer.stop()

    def _settle_size(self):
        if self.pending_size is not None:
            self.size, self.pending_size = self.pending_size, None
            self.queue.clear()
            self.urgent.clear()
            self.waiting.clear()
            self.jobs.clear()
            self.layer_ready.emit()

    # --- layers ------------------------------------------------------------
    def _job(self, segment, at, style, variables, attributes):
        """What renders `segment` at `at` seconds, keyed."""
        text = segment.get('text') or ''
        start, end = float(segment['start']), float(segment['end'])
        offset = 0
        if style.animated:
            fps = float(session.VIDEO.get('framerate') or 0) or 25.0
            offset = int(round(math.floor((at - start) * fps + 1e-6) / fps * 1000))
        safe = (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100,
                session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100)
        width, height = self.size
        duration = int(round((end - start) * 1000))
        key = _digest(self._style_key, text, self.size, (duration, offset) if style.animated else None)
        page = {'html': markup.to_html(text) if text.strip() else None, 'align': markup.alignment(text),
                'vars': variables, 'attrs': attributes, 'aspect': width / height, 'safe': safe,
                'offset': offset, 'duration': duration, 'pad': PAD}
        return key, {'key': key, 'size': self.size, 'page': page, 'style': self._style_key}

    def layers(self, segments, at):
        """[(QRectF of the stage, 0..1; QImage)] to draw for `segments`
        (those showing) at `at` seconds. Layers not rendered yet are queued,
        and stood in for by the last one drawn for that segment."""
        if self.size is None or not segments:
            return []
        style, variables, attributes = self._style()
        out = []
        for segment in segments:
            key, job = self._job(segment, at, style, variables, attributes)
            layer = self.cache.get(key)
            if layer is not None:
                self.cache.move_to_end(key)
                self.last[id(segment)] = key
                self.stats['hits'] += 1
            else:
                self._enqueue(key, job, first=True)
                self.waiting.setdefault(key, time.perf_counter())
                stand_in = self.cache.get(self.last.get(id(segment)))
                self.stats['stand_ins' if stand_in else 'misses'] += 1
                layer = stand_in
            if layer is not None and layer[1] is not None:
                out.append(layer)
        self.schedule()
        return out

    def prefetch(self, segments, at):
        """Render ahead: the subtitles from `at` on (sorted by start), so each
        is ready when it shows."""
        if self.size is None or not segments:
            return
        style, variables, attributes = self._style()
        horizon = AHEAD_ANIMATED if style.animated else AHEAD
        # From the playhead on: what was queued for elsewhere goes behind.
        for key in [k for k in self.queue if k not in self.urgent]:
            del self.queue[key]
            self.jobs.pop(key, None)
        for segment in segments:
            start, end = float(segment['start']), float(segment['end'])
            if end <= at:
                continue
            if start > at + horizon:
                break
            if style.animated:
                fps = float(session.VIDEO.get('framerate') or 0) or 25.0
                moment = max(start, at)
                while moment < min(end, at + horizon):
                    key, job = self._job(segment, moment, style, variables, attributes)
                    self._enqueue(key, job)
                    moment += 1 / fps
            else:
                key, job = self._job(segment, start, style, variables, attributes)
                self._enqueue(key, job)
        self.schedule()

    def _enqueue(self, key, job, first=False):
        if first:
            self.urgent.add(key)
        if key in self.cache or key in self.loading:
            return
        self.jobs[key] = job
        if self.disk is not None and key in self.disk.index and (first or len(self.loading) < DISK_LOADS):
            # On disk: read, not rendered (a millisecond, not tens).
            self.loading.add(key)
            self.disk.get(key)
            return
        self._enqueue_render(key, first)

    def _enqueue_render(self, key, first=False):
        job = self.jobs.get(key)
        sent = next((s for s in self.sent.values() if s['key'] == key), None)
        if job is None or key in self.cache or (sent is not None and (sent['urgent'] or not first)):
            return
        self.queue[key] = job
        if first:
            self.queue.move_to_end(key, last=False)

    def schedule(self):
        if self.queue and self.process is None:
            self._start()
        if not self.slots:
            return                      # the helper has not said hello yet
        # Urgent jobs at once; the rest while the helper has room for them.
        for key in [k for k in self.queue if k in self.urgent]:
            self._dispatch(key, urgent=True)
        ahead = sum(1 for job in self.sent.values() if not job['urgent'])
        while self.queue and ahead < self.slots:
            self._dispatch(next(iter(self.queue)), urgent=False)
            ahead += 1

    def _dispatch(self, key, urgent):
        job = dict(self.queue.pop(key), id=self.next_id, urgent=urgent)
        self.next_id += 1
        self.sent[job['id']] = job
        self._send({'cmd': 'render', 'id': job['id'], 'size': job['size'], 'page': job['page'], 'urgent': urgent})

    def forget(self, segments):
        """Stand-ins only for these segments (the subtitles now loaded)."""
        alive = {id(segment) for segment in segments}
        self.last = {k: v for k, v in self.last.items() if k in alive}
