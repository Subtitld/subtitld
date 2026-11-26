"""The preview's subtitles, rendered as HTML and drawn with the video.

Each subtitle style is an HTML template (modules.subtitle_styles). A helper
process (subtitle_render_worker) loads the current style's template, with
subtitle_styles/host.js in it, and renders one subtitle at a time: the stage
the size the preview shows the video at, the subtitle on it, and, for an
animated style, its animations held at one moment. It sends back the capture
cropped to the subtitle, kept here as a layer. The preview draws the layer in
the video item's paint(), with the very frame it belongs with, so the two
never drift apart.

A title (modules.cue_kinds) is drawn by its title theme (modules.title_themes)
with its own settings: its jobs name the theme's template, which the
helper renders in views of its own.

A subtitle may be drawn with a style of its own (segment['theme']): its
job names that style's template, as a title's does. A speaker's own values
of a style's per-speaker fields make their subtitles' layers their own.

A style or title theme that is a theme file (modules.themes) is loaded from
the copy themes.served() keeps, its Content Security Policy in it; its jobs
say what host.js needs for it: what goes in its slots, whether it places
itself, how far its text is cut, and where its outro starts.

A layer is kept by a key of everything that changes its pixels: the style,
its template, its settings, the text, the size, the title-safe area and, for
an animated style, the subtitle's duration and the moment. Change any of them
and it is another layer; change one back and the old one is still there.

A subtitle timed word by word (modules.segment) lights up piece by piece:
each piece a span whose effect is a CSS animation (subtitle_styles/host.js),
from the piece's start, held at the moment like an animated style's. Its
layer is keyed by how many pieces have started, and a frame at a time only
while a piece wipes or pops in, so a song is a handful of layers a line.

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

A project just opened is rendered whole (render_all), to disk only, while
the helper has nothing else to do: every cue whose layers the disk does not
have yet, one job at a time, so playing it later reads them from disk
instead of racing the helper. Not an animated style's frames, which are a
layer each: those stay rendered ahead. A new size or style starts the pass
over, for that size or style.

The helper starts a second after Subtitld does (warm_up), or with the first
subtitle asked for if that is sooner, and again if it stops (a few times).
"""

import logging
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

from subtitld.interface.subtitle_render_worker import SCALING_VARIABLES
from subtitld.modules import cue_kinds, look as subtitld_look, markup, position, session, subtitle_styles, themes, title_themes
from subtitld.modules.segment import EFFECTS, text_of, timed_pieces

log = logging.getLogger(__name__)

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
POP = 0.15                   # seconds a piece popping in takes to appear
NO_SANDBOX_EXIT = 75         # subtitle_render_worker: Chromium's sandbox cannot start here


def available():
    """Whether this build has QtWebEngine (Haiku's has not)."""
    try:
        return importlib.util.find_spec('PySide6.QtWebEngineWidgets') is not None
    except (ImportError, ValueError):
        return False


def _effects(pieces, default):
    """How each piece lights up: its own effect, else the style's."""
    default = default if default in EFFECTS else 'fill'
    return [piece.get('effect') if piece.get('effect') in EFFECTS else default for piece in pieces]


def _lengths(pieces, effects):
    """How long each piece's effect runs, in seconds: a pop is quick."""
    return [min(POP, piece['end'] - piece['start']) if effect == 'pop' else piece['end'] - piece['start']
            for piece, effect in zip(pieces, effects)]


def karaoke_html(pieces, default_effect='fill'):
    """A timed subtitle as the cue's HTML: each piece a span lit up from
    its start by its effect's animation (subtitle_styles/host.js), its
    formatting closed at its end and opened again in the next. Words before
    the first piece that takes time are there from the start, unlit."""
    effects = _effects(pieces, default_effect)
    texts = [piece['text'] for piece in pieces]
    texts[0] = markup.split_lead(texts[0])[1]
    for number in range(len(texts) - 1):
        texts[number], texts[number + 1] = markup.rebalance(texts[number], texts[number + 1])
    out = []
    for piece, text, effect, length in zip(pieces, texts, effects, _lengths(pieces, effects)):
        html = markup.to_html(text)
        if piece['end'] > piece['start']:
            html = (f'<span class="subtitld-k subtitld-k-{effect}" style="--k-start:{round(piece["start"] * 1000)}ms;'
                    f'--k-length:{max(1, round(length * 1000))}ms">{html}</span>')
        out.append(html)
    return ''.join(out)


def karaoke_moment(pieces, at, fps, default_effect='fill'):
    """(what sets this moment's picture apart, the moment in milliseconds)
    for a timed subtitle `at` seconds from its start: the pieces started,
    the moment the last of them did; while one wipes or pops in, the frame."""
    effects = _effects(pieces, default_effect)
    lengths = _lengths(pieces, effects)
    timed = [(piece, length) for piece, length in zip(pieces, lengths) if piece['end'] > piece['start']]
    started = [(piece, length) for piece, length in timed if piece['start'] <= at]
    for piece, effect, length in zip(pieces, effects, lengths):
        if effect in ('wipe', 'pop') and piece['start'] <= at < piece['start'] + length:
            frame = int(math.floor(at * fps + 1e-6))
            return ('frame', frame), int(round(frame / fps * 1000))
    if not started:
        # A moment before the first piece, which may start at 0.
        return ('started', 0), -1
    # Held where every piece started has finished lighting up, and before
    # the next starts; at least a millisecond after the last start
    # (Chromium holds an animation set exactly at its delay as not started).
    last = max(round(piece['start'] * 1000) for piece, _length in started)
    moment = max(round((piece['start'] + length) * 1000) for piece, length in started)
    following = [round(piece['start'] * 1000) for piece, _length in timed if piece['start'] > at]
    if following:
        moment = min(moment, min(following) - 1)
    return ('started', len(started)), max(moment, last + 1)


def theme_page(theme, segment=None):
    """What a job's page says for a theme file `theme` (modules.themes; None
    for a style that is not one): the slots besides "main" (a title's own,
    `segment`'s), their types, whether it places itself, how far its text is
    cut, how long its outro is."""
    if theme is None:
        return {'slots': None, 'slot_types': None, 'free': False, 'slice': 'none', 'outro': 0}
    slots, types = {}, {}
    is_title = isinstance(segment, dict) and theme.kind == 'title'
    settings = title_themes.values(segment) if is_title else {}
    for slot in theme.slots:
        key = slot['key']
        types[key] = slot['type']
        if key == 'main':
            continue
        # What the title has in it (modules.title_themes: fitted to the
        # slot, its default otherwise); nothing where a setting hides it.
        value = title_themes.slot_value(segment, key) if is_title else slot.get('default')
        if slot.get('visible_if') and not settings.get(slot['visible_if']):
            value = None
        if slot['type'] == 'image':
            value = value if isinstance(value, str) and value.startswith('data:image/') else None
        elif slot['type'] == 'list':
            value = [markup.to_html(item) for item in value if isinstance(item, str)] if isinstance(value, list) else None
        else:
            value = markup.to_html(value) if isinstance(value, str) and value.strip() else None
        slots[key] = value
    return {'slots': slots, 'slot_types': types, 'free': theme.ignores_position, 'slice': theme.slice,
            'outro': (theme.timing or {}).get('outro', 0)}


def template_of(style):
    """The file the helper loads for a style or title theme: a theme
    file's served copy, else the template in its folder."""
    if getattr(style, 'file', None) is not None:
        return themes.served(style.file)
    return style.path / style.template


def _digest(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def worker_command():
    """How to start the helper: this executable, with the worker's flag."""
    if getattr(sys, 'frozen', False):
        return sys.executable, [WORKER_FLAG]
    return sys.executable, ['-m', 'subtitld', WORKER_FLAG]


def worker_environment():
    """The helper's environment: Subtitld's, at scale 1, with this copy of
    Subtitld on the path when it runs from source."""
    environment = QProcessEnvironment.systemEnvironment()
    for name in SCALING_VARIABLES:
        environment.remove(name)
    if not getattr(sys, 'frozen', False):
        # The helper imports this very copy of Subtitld.
        path = str(session.PATH_SUBTITLD.parent)
        known = environment.value('PYTHONPATH')
        environment.insert('PYTHONPATH', path + (os.pathsep + known if known else ''))
    return environment


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
                log.warning('no subtitle layers on disk: %s', error)
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
        self.style_id = None            # the style drawn with: the current one when None (an export may ask another)
        self.fps = None                 # frames a second animations are drawn at: the video's when None
        self.one_style = False          # style_id for every subtitle, its own theme or not (an export's choice)
        self.pending_size = None
        self.size_timer = QTimer(self)
        self.size_timer.setSingleShot(True)
        self.size_timer.timeout.connect(self._settle_size)
        self.stats = {'rendered': 0, 'from_disk': 0, 'hits': 0, 'stand_ins': 0, 'misses': 0, 'restarts': 0}
        self.urgent = set()             # keys of queued or sent jobs a subtitle on screen waits for
        self.backlog = []               # the project's cues, rendered to disk while nothing else waits
        self.backlog_at = 0             # the next of them
        self.backlog_mark = None        # (style key, size) the pass renders for; another starts it over
        self.backlog_jobs = []          # (key, job) of the cue being gone through
        self.waiting = {}               # key -> when a subtitle on screen first lacked it
        self.gaps = []                  # ms subtitles on screen waited for their layer
        self._style_key = None
        self._template = None
        self._templates = {}            # a title theme's template -> (mtime, digest)
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
        if self.no_sandbox:
            arguments = [*arguments, '--no-sandbox']
        self.process.setProcessEnvironment(worker_environment())
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
            log.info("Chromium's sandbox cannot start here: rendering subtitles without it")
            self.no_sandbox = True
            self._start_and_schedule()
            return
        self.stats['restarts'] += 1
        log.warning('the subtitle renderer stopped (exit %s)', code)
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
            # One of the whole project's goes to disk alone, unless it was
            # asked for meanwhile: in memory it would push out what is needed now.
            if not job.get('background') or job['key'] in self.jobs:
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
                log.debug('waited %.0f ms for a subtitle layer', self.gaps[-1])
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
            log.debug('subtitle renderer: %s', self.stats)
        self.disk_timer.stop()
        if self.disk is not None:
            self.disk.close()
        if self.process is not None:
            process, self.process = self.process, None
            process.finished.disconnect(self._finished)
            # What it still says while it quits is for no one: waiting for
            # it delivers that output, and _read has no process to read it.
            process.readyReadStandardOutput.disconnect(self._read)
            process.write(b'{"cmd": "quit"}\n')
            process.closeWriteChannel()
            if not process.waitForFinished(2000):
                process.kill()
                process.waitForFinished(1000)

    # --- the style ---------------------------------------------------------
    def _style(self):
        """The current style and the part of every key it gives; the helper
        is told when the template is another."""
        style = subtitle_styles.get(self.style_id)
        template = template_of(style)
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

    def frame_rate(self):
        """Frames a second an animation is drawn at: the one asked for (an
        export's), else the video's, else 25."""
        return float(self.fps or 0) or float(session.VIDEO.get('framerate') or 0) or 25.0

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

    # --- titles, and subtitles with a style of their own ---------------------
    def _template_digest(self, path):
        """A template a job names, by its content, read once a change."""
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        cached = self._templates.get(path)
        if cached is None or cached[0] != stamp:
            try:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                digest = ''
            cached = self._templates[path] = (stamp, digest)
        return cached[1]

    def _title_job(self, segment, at):
        """What renders a title at `at` seconds, keyed: its theme's
        template, with its settings."""
        theme = title_themes.of(segment)
        settings = title_themes.values(segment)
        variables = title_themes.css_variables(theme, settings)
        attributes = title_themes.html_attributes(theme, settings)
        template = template_of(theme)
        extra = theme_page(theme.file, segment)
        text = text_of(segment)
        start, end = float(segment['start']), float(segment['end'])
        fps = self.frame_rate()
        offset = moment = 0
        if theme.animated:
            offset = moment = int(round(math.floor((at - start) * fps + 1e-6) / fps * 1000))
        safe = (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100,
                session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100)
        width, height = self.size
        duration = int(round((end - start) * 1000))
        placed = position.of(segment)
        place = {'h': position.as_percent(placed.h), 'v': position.as_percent(placed.v),
                 'rotate': list(placed.rotate)} if placed.any() else None
        # A title's text is its theme's "main" part; its own place on the
        # keypad, its theme's placement where it has none.
        lead, body = markup.split_lead(text)
        align = markup.alignment(text) if lead else title_themes.alignment(theme, settings)
        html = markup.to_html(body) if body.strip() else None
        key = _digest('title', theme.id, self._template_digest(template), self._host_digest, qVersion(), variables,
                      attributes, safe, body, self.size, (duration, moment) if theme.animated else None, place, align,
                      extra)
        page = {'html': html, 'align': align, 'vars': variables, 'attrs': attributes, 'aspect': width / height,
                'safe': safe, 'offset': offset, 'duration': duration, 'pad': PAD, 'place': place, 'direction': None,
                'bidi': None, 'look': None, 'writing': None, **extra}
        return key, {'key': key, 'size': self.size, 'page': page, 'style': self._style_key, 'template': str(template)}

    # --- layers ------------------------------------------------------------
    def _own_style(self, segment, style):
        """The style `segment` is drawn with and what tells its job apart:
        its own (segment['theme']) where it names one there is, else
        `style`; with its speaker's own values of the per-speaker fields.
        (style, variables, attributes, template or None, key part); all but
        the style None when it is `style` as it is."""
        own_id = None if self.one_style else segment.get('theme')
        own = subtitle_styles.get(own_id) if own_id and own_id != style.id and own_id in subtitle_styles.available() \
            else style
        speaker = segment.get('speaker') if any(f.get('per_speaker') for f in own.fields) else None
        if own is style and not (speaker and subtitle_styles.speaker_values(own.id, speaker)):
            return own, None, None, None, None
        settings = subtitle_styles.values(own.id, speaker)
        variables = subtitle_styles.css_variables(own.id, settings)
        attributes = subtitle_styles.html_attributes(own.id, settings)
        template = template_of(own) if own is not style else None
        part = (own.id, self._template_digest(template) if template else None, variables, attributes)
        return own, variables, attributes, template, part

    def _job(self, segment, at, style, variables, attributes):
        """What renders `segment` at `at` seconds, keyed."""
        if cue_kinds.kind_of(segment) == cue_kinds.TITLE:
            return self._title_job(segment, at)
        style, own_variables, own_attributes, template, own_part = self._own_style(segment, style)
        if own_variables is not None:
            variables, attributes = own_variables, own_attributes
        text = text_of(segment)
        start, end = float(segment['start']), float(segment['end'])
        fps = self.frame_rate()
        offset, moment = 0, None
        pieces = timed_pieces(segment)
        # The style's way for a piece that has none of its own.
        default_effect = attributes.get('data-karaoke-effect')
        if style.animated:
            offset = int(round(math.floor((at - start) * fps + 1e-6) / fps * 1000))
            moment = offset
        elif pieces:
            moment, offset = karaoke_moment(pieces, at - start, fps, default_effect)
        safe = (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100,
                session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100)
        width, height = self.size
        duration = int(round((end - start) * 1000))
        # Where it sits beyond the nine places: margins in from the edge it
        # is aligned to, and the degrees it is turned by (modules.position).
        placed = position.of(segment)
        place = {'h': position.as_percent(placed.h), 'v': position.as_percent(placed.v),
                 'rotate': list(placed.rotate)} if placed.any() else None
        # Which way the writing runs: a subtitle read from a file that says
        # so (TTML's tts:direction), else the page's own.
        direction = segment.get('direction') if segment.get('direction') in ('ltr', 'rtl') else None
        # And how a run of the other direction sits inside the line, where
        # the file forces it (TTML's tts:unicodeBidi).
        bidi = segment.get('bidi') if segment.get('bidi') in ('embed', 'override', 'isolate') else None
        # Written down the picture, in columns, as Japanese and Chinese may be
        # (TTML's tts:writingMode, WebVTT's vertical setting).
        writing = position.writing(segment) or None
        # What the file it came from says it should look like, as CSS over
        # the style's own rules (modules.look). What the file is silent
        # about the style still draws.
        styling = subtitld_look.css(subtitld_look.of(segment)) or None
        html = (karaoke_html(pieces, default_effect) if pieces else markup.to_html(text)) if text.strip() else None
        # The text, and for a timed one its pieces as drawn (their times and effects).
        key = _digest(self._style_key, text, html if pieces else None, self.size,
                      (duration, moment) if moment is not None else None,
                      place, direction, bidi, styling, writing, own_part)
        page = {'html': html, 'align': markup.alignment(text),
                'vars': variables, 'attrs': attributes, 'aspect': width / height, 'safe': safe,
                'offset': offset, 'duration': duration, 'pad': PAD, 'place': place, 'direction': direction,
                'bidi': bidi, 'look': styling, 'writing': writing, **theme_page(style.file)}
        job = {'key': key, 'size': self.size, 'page': page, 'style': self._style_key}
        if template is not None:
            job['template'] = str(template)
        return key, job

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
            is_title = cue_kinds.kind_of(segment) == cue_kinds.TITLE
            if self._animated(segment, style):
                fps = self.frame_rate()
                moment = max(start, at)
                while moment < min(end, at + AHEAD_ANIMATED):
                    key, job = self._job(segment, moment, style, variables, attributes)
                    self._enqueue(key, job)
                    moment += 1 / fps
            elif timed_pieces(segment) and not is_title:
                # A timed subtitle: a layer as each piece starts, and frames
                # through a wipe or a pop close to the playhead.
                fps = self.frame_rate()
                pieces = timed_pieces(segment)
                own_attributes = self._own_style(segment, style)[2] or attributes
                effects = _effects(pieces, own_attributes.get('data-karaoke-effect'))
                moments = self._still_moments(segment)
                for piece, effect, length in zip(pieces, effects, _lengths(pieces, effects)):
                    moment = max(start + piece['start'], at)
                    while effect in ('wipe', 'pop') and moment < min(start + piece['start'] + length, at + AHEAD_ANIMATED):
                        moments.append(moment)
                        moment += 1 / fps
                for moment in sorted(set(moments)):
                    if moment < end:
                        key, job = self._job(segment, moment, style, variables, attributes)
                        self._enqueue(key, job)
            else:
                key, job = self._job(segment, start, style, variables, attributes)
                self._enqueue(key, job)
        self.schedule()

    def _animated(self, segment, style):
        is_title = cue_kinds.kind_of(segment) == cue_kinds.TITLE
        return title_themes.of(segment).animated if is_title else self._own_style(segment, style)[0].animated

    def _still_moments(self, segment):
        """The moments a cue not animated changes at: its start and, timed,
        each piece's start (a wipe's or a pop's frames aside)."""
        start = float(segment['start'])
        pieces = timed_pieces(segment) if cue_kinds.kind_of(segment) != cue_kinds.TITLE else None
        return [start] + [start + piece['start'] for piece in pieces or ()]

    def render_all(self, segments):
        """Render the project's cues to disk while nothing else waits.
        `segments` is the live list: a cue added before the pass reaches it
        is rendered too."""
        if self.disk is None:
            return                      # nowhere to keep them
        self.backlog = segments
        self.backlog_at = 0
        self.backlog_mark = None
        self.backlog_jobs = []
        self._start()                   # it says hello, and the pass begins
        self.schedule()

    def _next_background(self):
        """(key, job) of the next layer of the project the disk lacks, or None."""
        if self.size is None or (self.backlog_mark == (self._style_key, self.size)
                                 and self.backlog_at >= len(self.backlog) and not self.backlog_jobs):
            return None                 # gone through already
        style, variables, attributes = self._style()
        mark = (self._style_key, self.size)
        if mark != self.backlog_mark:
            self.backlog_mark, self.backlog_at, self.backlog_jobs = mark, 0, []
        sent = {job['key'] for job in self.sent.values()}
        while True:
            while self.backlog_jobs:
                key, job = self.backlog_jobs.pop(0)
                if not (key in self.cache or key in self.disk.index or key in self.queue or key in self.loading
                        or key in sent):
                    return key, job
            if self.backlog_at >= len(self.backlog):
                return None
            segment = self.backlog[self.backlog_at]
            self.backlog_at += 1
            if cue_kinds.kind_of(segment) == cue_kinds.MARK or self._animated(segment, style):
                continue
            end = float(segment['end'])
            self.backlog_jobs = [self._job(segment, moment, style, variables, attributes)
                                 for moment in sorted(set(self._still_moments(segment))) if moment < end]

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
        # Then the whole project, a job at a time: the helper shares the
        # computer with the video playing.
        if not self.queue and ahead == 0 and self.backlog:
            found = self._next_background()
            if found is not None:
                key, job = found
                self.queue[key] = dict(job, background=True)
                self._dispatch(key, urgent=False)

    def _dispatch(self, key, urgent):
        job = dict(self.queue.pop(key), id=self.next_id, urgent=urgent)
        self.next_id += 1
        self.sent[job['id']] = job
        message = {'cmd': 'render', 'id': job['id'], 'size': job['size'], 'page': job['page'], 'urgent': urgent}
        if job.get('template'):
            message['template'] = job['template']
        self._send(message)

    def forget(self, segments):
        """Stand-ins only for these segments (the subtitles now loaded)."""
        alive = {id(segment) for segment in segments}
        self.last = {k: v for k, v in self.last.items() if k in alive}
