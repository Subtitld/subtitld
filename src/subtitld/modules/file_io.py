import os
import copy
import hashlib
import shutil
import threading
import zipfile
from docx import Document
import json
import pycaption
from pycaption.exceptions import CaptionReadSyntaxError, CaptionReadNoCaptions
import chardet
import pysubs2
import datetime
from bs4 import BeautifulSoup

from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import Qt, QThread, Signal, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from subtitld.modules import timecode
from subtitld.modules import session
from subtitld.modules import waveform
from subtitld.modules import usf
from subtitld.modules import utils


class CorruptedProjectFileError(Exception):
    """Raised by `process_subtitles_file` when the USFX zip on disk is
    structurally damaged (Bad CRC, truncated, missing required members).
    The UI catches this to show a friendly dialog instead of crashing."""
# `signals.SIGNALS` MUST be imported at module-load time, NOT lazily
# from inside the background extractor's `run()`. SIGNALS is a QObject
# singleton; its thread affinity is whichever thread imports the module
# first. If the worker thread is the first importer, the QObject is
# parented to a QThread that exits shortly afterwards — and every later
# `emit()` from the main thread silently goes to a dead receiver,
# breaking the progress bar, the Save-button gate, and the per-dub
# `usfx_member_ready` repaints. Importing here forces creation on the
# main thread the first time file_io is loaded (during app startup).
from subtitld.modules.signals import SIGNALS


def _safe_asset_name(name):
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in str(name))
    return safe or 'unnamed'


# Speaker thumbnails only render at ~64 px in the speakers panel. Original
# captures from `preview_panel._apply_face_selection` were the full face
# crop at video resolution — easily 400×400+ for HD source. A project with
# 30 speakers at 600×600 ARGB is ~40 MB held resident for nothing; at 4K
# face crops it can balloon past 200 MB. Cap on load so old projects
# benefit without touching their files. Kept in sync with the cap in
# `interface/preview_panel.py`.
SPEAKER_IMAGE_MAX_DIM = 256


def _downscale_speaker_image(qimg):
    if qimg is None or qimg.isNull():
        return qimg
    if max(qimg.width(), qimg.height()) <= SPEAKER_IMAGE_MAX_DIM:
        return qimg
    return qimg.scaled(
        SPEAKER_IMAGE_MAX_DIM, SPEAKER_IMAGE_MAX_DIM,
        Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )


def _usfx_extract_dir(usfx_path):
    project_hash = hashlib.md5(os.path.abspath(usfx_path).encode('utf-8')).hexdigest()
    path = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'usfx', project_hash)
    os.makedirs(path, exist_ok=True)
    return path


def _is_phase1_usfx_member(name):
    """Return True for zip members that must be on disk before the
    production screen can render. Everything else streams in via
    `_USFXBackgroundExtractor` while the UI is already responsive.

    Phase 1 covers:
      * `subtitles.usf` / `manifest.xml` / a bare-root `.usf` —
        parsed synchronously to populate the segment list, speakers,
        and video pointer the production screen needs to render.
      * `assets/speakers/*` — speaker thumbnails. Total payload is
        small (< 5 MB even on 30-speaker projects) and the
        downstream decode/downscale already runs off the main thread
        via `_SpeakerImageLoader`. Keeping them in Phase 1 means the
        speakers panel populates within the same paint as the
        timeline, without a visible "speakers are blank for a second"
        flash.

    Phase 2 (handled by `_USFXBackgroundExtractor`):
      * `assets/dubs/*` — TTS dub WAVs. Historically the biggest cost
        on big projects (hundreds of MB across hundreds of subtitles);
        moving these off the main thread is the single largest perceived
        win. The audio engine tolerates missing dub paths and the
        timeline paints a hatched placeholder until each file lands.
      * `assets/waveform.npy` — main timeline peaks cache.
      * `assets/audio/*.flac` — audio-separation stems.
      * `assets/video/*` — bundled source video, handled upstream by
        `peek_usfx_video()`.
    """
    if not name or name.endswith('/'):
        return False
    if name == 'subtitles.usf' or name == 'manifest.xml':
        return True
    if name.startswith('assets/speakers/'):
        return True
    # Tolerate `.usf` at the zip root with an unusual filename.
    if '/' not in name and name.lower().endswith('.usf'):
        return True
    return False


class _USFXBackgroundExtractor(QThread):
    """Stream the deferred USFX members to their target paths after the
    production screen is already visible. Phase 1 extracts only what's
    needed for first paint (subtitles.usf, manifest.xml, speaker thumbs);
    this thread handles the rest in the background:

      * `assets/dubs/<uid>.<fmt>` → `<extract_dir>/assets/dubs/<uid>.<fmt>`
        — the per-subtitle TTS WAVs. On a project with hundreds of dubs
        this is the single biggest item by total bytes; deferring it is
        what makes the production screen show up "instantly" on big
        projects.
      * `assets/waveform.npy` → `<cache>/waveform/<key>_waveform.npy`
      * `assets/audio/<kind>.flac` → `<audiosep>/<key>_<kind>.flac`

    If a downstream feature (audio separation, zoom-out, dub playback) is
    invoked before this finishes, the affected code paths fall back to
    silence or a hatched placeholder — no user-visible error. Writes are
    atomic via `.tmp` + `os.replace`, so a crash mid-extract leaves clean
    state, not a half-written cache.

    There used to be a global progress signal driving a thin bar at the
    bottom of the window; it was removed because the per-dub
    `usfx_member_ready` repaints already give the user concrete visual
    feedback (the matching clip un-hatches the instant its bytes land,
    far more legible than an abstract percent). The per-instance
    `progress` signal is kept for tests that want to assert the chunked
    loop actually iterates through to 100%.

    Per-member completion (`usfx_member_ready`) lets the timeline repaint
    each dub clip and the audio engine preload it the instant its file
    lands — no need to wait for the whole batch.
    """
    # Per-instance progress signal — observable by tests for chunked
    # iteration verification. No production listener; the UI relies on
    # `usfx_member_ready` for visual feedback instead.
    progress = Signal(int)

    # 1 MiB chunks balance "few progress emits" against responsiveness
    # for the 50–500 MB items typical here.
    _CHUNK = 1 << 20

    def __init__(self, usfx_path, cache_key, extract_dir=None, parent=None):
        super().__init__(parent)
        self._usfx_path = usfx_path
        self._cache_key = cache_key
        # extract_dir is required for dub coverage; legacy callers that
        # only wanted the waveform/FLAC streaming still work without it
        # (dubs are simply skipped from the work list).
        self._extract_dir = extract_dir
        self._extracted_bytes = 0
        self._total_bytes = 0
        self._last_pct = -1

    def _emit_progress(self):
        if self._total_bytes <= 0:
            return
        pct = int(self._extracted_bytes * 100 / self._total_bytes)
        if pct == self._last_pct:
            return
        self._last_pct = pct
        # Per-instance signal only — the hub variant is gone with the
        # progress bar. Best-effort: never let a test-only signal raise
        # into the extractor's hot loop.
        try:
            self.progress.emit(pct)
        except Exception:
            pass

    def _emit_member_ready(self, arcname, target_path):
        """Tell listeners (timeline, audio engine) that `arcname` is now
        on disk at `target_path`. Best-effort — never raises."""
        try:
            SIGNALS.usfx_member_ready.emit(arcname, target_path)
        except Exception:
            pass

    def _stream(self, zf, arcname, target_path, file_size):
        # Reuse the cache only when the on-disk file is byte-for-byte the
        # same size as the archived member. A pure existence check served
        # STALE assets after a crash: autosave overwrites the original
        # .usfx in place, but its extract dir (keyed by the unchanged
        # path) still held the pre-edit dub — so we'd play the old audio
        # while the waveform/peaks were recomputed from the new file. The
        # extracted file is a raw copy, so its size equals the member's
        # uncompressed size exactly; any difference means a different
        # version and forces a re-extract (which also bumps mtime, so the
        # content-aware peaks/onset caches refresh in lockstep).
        if os.path.exists(target_path) and os.path.getsize(target_path) == file_size:
            # Skip but still account the bytes — otherwise the percent
            # never reaches 100 when a cache already exists.
            self._extracted_bytes += file_size
            self._emit_progress()
            # The file is already there — listeners that connected late
            # still need to know it's available.
            self._emit_member_ready(arcname, target_path)
            return
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        tmp = target_path + '.tmp'
        try:
            with zf.open(arcname) as src, open(tmp, 'wb') as dst:
                while True:
                    chunk = src.read(self._CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    self._extracted_bytes += len(chunk)
                    self._emit_progress()
            os.replace(tmp, target_path)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            # Don't emit member_ready if the rename never landed —
            # downstream listeners would try to read a path that doesn't
            # exist (the audio engine tolerates it but the timeline's
            # peaks worker would just bounce off `os.path.exists`).
            return
        self._emit_member_ready(arcname, target_path)

    def _heavy_targets(self, names):
        """Yield (arcname, target_path) for each zip member this thread
        is responsible for. Called twice: once to sum total bytes for the
        progress denominator, then again during the actual extract.

        Order matters for perceived speed: dubs first so playback can
        start as early as possible; waveform next so the main timeline
        peak band fills in; FLAC stems last because nothing immediate
        depends on them (audio separation is a deliberate user action).
        """
        if self._extract_dir:
            # zipfile.namelist() returns members in archive order; filter
            # to the dubs directory. Skip directory entries (trailing /).
            for name in sorted(names):
                if not name.startswith('assets/dubs/') or name.endswith('/'):
                    continue
                # Map directly: arcname `assets/dubs/uid.wav` lands at
                # `<extract_dir>/assets/dubs/uid.wav` — same path the
                # main-thread USFX parse stored in `dub['path']`.
                target = os.path.join(self._extract_dir, name.replace('/', os.sep))
                yield name, target
        if 'assets/waveform.npy' in names:
            target = os.path.join(
                session.PATH_SUBTITLD_USER_CACHE, 'waveform',
                f'{self._cache_key}_waveform.npy',
            )
            yield 'assets/waveform.npy', target
        for kind in ('original', 'vocals', 'background'):
            arc = f'assets/audio/{kind}.flac'
            if arc in names:
                target = os.path.join(
                    session.PATH_SUBTITLD_DATA_AUDIOSEPARATION,
                    f'{self._cache_key}_{kind}.flac',
                )
                yield arc, target

    def run(self):
        # `cache_key` is required for the FLAC/waveform targets; the dub
        # branch only needs `extract_dir`. If we have neither, nothing to
        # do — but bail only if BOTH are missing so dub-only zips still
        # work.
        if not os.path.isfile(self._usfx_path):
            return
        if not self._cache_key and not self._extract_dir:
            return
        try:
            with zipfile.ZipFile(self._usfx_path, 'r') as zf:
                names = set(zf.namelist())
                # First pass: sum uncompressed bytes across the members
                # we'll actually handle, so progress is a true percent.
                pairs = list(self._heavy_targets(names))
                for arc, _target in pairs:
                    try:
                        self._total_bytes += zf.getinfo(arc).file_size
                    except KeyError:
                        pass
                if self._total_bytes <= 0:
                    return
                # Second pass: stream each member with progress per chunk.
                for arc, target in pairs:
                    try:
                        size = zf.getinfo(arc).file_size
                    except KeyError:
                        size = 0
                    self._stream(zf, arc, target, size)
                # If we skipped everything (all caches already present),
                # the first byte accounting in _stream already emitted
                # the final 100%. If for some reason _emit_progress
                # didn't fire 100, force it now.
                if self._last_pct < 100:
                    self._extracted_bytes = self._total_bytes
                    self._emit_progress()
        except Exception:
            # Background pre-cache is best-effort; any failure just means
            # the affected feature will recompute on demand later.
            pass


def _start_speaker_image_loader(items):
    """Spawn a `_SpeakerImageLoader` for `items` if there is any work to
    do, and park the reference on `session` so the QThread isn't GC'd
    mid-run. A subsequent project open replaces it; the prior thread
    either finishes naturally or is gracefully orphaned (its writes go
    into `session.SPEAKERS` regardless of which project is loaded — but
    since `session.SPEAKERS` is reset on each open, stale writes from a
    previous load would be no-ops for absent names)."""
    if not items:
        return
    loader = _SpeakerImageLoader(items)
    session.SPEAKER_IMAGE_LOAD = loader
    loader.start()


class _SpeakerImageLoader(QThread):
    """Decode + downscale speaker thumbnails off the main thread.

    QImage is a value class and its `loadFromData` / `load` / `scaled`
    methods are reentrant — safe to call on a non-GUI thread. Doing the
    work here removes ~10–50 ms per speaker (HD face crops at 4K source)
    from the main-thread USF/USFX load.

    Each item is a `(speaker_name, image_bytes_or_None, fallback_path_or_None)`
    tuple. The first non-empty source that decodes wins. Results are
    published via `signals.SIGNALS.speaker_image_ready` and written into
    `session.SPEAKERS[name]['image']` from this thread — dict mutation in
    CPython is GIL-protected, and the UI re-renders on the queued signal
    delivery (i.e., after the write has landed).
    """
    def __init__(self, items, parent=None):
        super().__init__(parent)
        # Copy so the caller can keep mutating the original.
        self._items = list(items)

    def run(self):
        # SIGNALS comes from the module-level import — see the long note
        # at the top of file_io.py. The previous lazy import here was
        # the bug that broke `speaker_image_ready` deliveries.
        for name, image_bytes, fallback_path in self._items:
            qimg = None
            try:
                if image_bytes:
                    qi = QImage()
                    if qi.loadFromData(image_bytes):
                        qimg = _downscale_speaker_image(qi)
                elif fallback_path and os.path.isfile(fallback_path):
                    qi = QImage()
                    if qi.load(fallback_path):
                        qimg = _downscale_speaker_image(qi)
            except Exception:
                qimg = None
            if qimg is None or qimg.isNull():
                continue
            # Write into the session dict from the worker — atomic per
            # CPython's GIL — then signal the UI to redraw. The receive
            # slot is on the main thread, so Qt queues the delivery; by
            # the time it fires, the dict already reflects the new image.
            try:
                bucket = session.SPEAKERS.setdefault(name, {})
                bucket['image'] = qimg
            except Exception:
                continue
            try:
                SIGNALS.speaker_image_ready.emit(name)
            except Exception:
                pass


USFX_OPTION_DEFAULTS = {
    'include_speaker_images': True,
    'include_waveform_cache': False,
    'include_original_audio': False,
    'include_processed_audio': False,
    'include_original_video': False,
}


def _get_usfx_options():
    """Merge stored defaults with fallback values."""
    stored = {}
    if isinstance(session.CONFIG, dict):
        stored = session.CONFIG.get('default_values', {}).get('usfx_options') or {}
    return {**USFX_OPTION_DEFAULTS, **stored}

from subtitld.interface.translation import _


class ThreadGenerateHashOfVideo(QThread):
    """Thread to extract time positions of scenes"""
    response = Signal(list)
    filepath = ''

    def run(self):
        if self.filepath and os.path.isfile(self.filepath):
            md5_object = hashlib.md5()
            block_size = 128 * md5_object.block_size
            file_object = open(self.filepath, 'rb')
            chunk = file_object.read(block_size)
            while chunk:
                md5_object.update(chunk)
                chunk = file_object.read(block_size)

            md5_hash = md5_object.hexdigest()

            self.response.emit([self.filepath, md5_hash])


def process_subtitles_file(subtitle_file=False, subtitle_format='SRT'):
    """Definition to process subtitle file. It returns a dict with the subtitles."""
    segments_list = []

    if subtitle_file and os.path.isfile(subtitle_file):
        if subtitle_file.lower().endswith(('.srt')):
            enc = chardet.detect(open(subtitle_file, 'rb').read())['encoding']
            with open(subtitle_file, mode='rb') as srt_file:
                srt_content = srt_file.read().decode(enc, 'ignore')

                if ' -> ' in srt_content:
                    srt_content = srt_content.replace(' -> ', ' --> ')

                srt_reader = pycaption.SRTReader().read(srt_content)
                languages = srt_reader.get_languages()
                language = languages[0]
                captions = srt_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.vtt', '.webvtt')):
            subtitle_format = 'VTT'
            with open(subtitle_file, encoding='utf-8') as vtt_file:
                try:
                    vtt_reader = pycaption.WebVTTReader().read(vtt_file.read())
                    languages = vtt_reader.get_languages()
                    language = languages[0]
                    captions = vtt_reader.get_captions(language)
                    for caption in captions:
                        segments_list.append({
                            'start': caption.start / 1000000,
                            'end': caption.end / 1000000,
                            'text': caption.get_text()
                        })
                except CaptionReadSyntaxError:
                    with open(subtitle_file, encoding='utf-8') as fileobj:
                        subfile = pysubs2.SSAFile.from_string(fileobj.read())

                    for event in subfile.events:
                        segments_list.append({
                            'start': event.start / 1000.0,
                            'end': (event.start / 1000.0) + (event.duration / 1000.0),
                            'text': event.text
                        })
                except CaptionReadNoCaptions:
                    pass

                    # error_message = QMessageBox()
                    # error_message.setWindowTitle('There is a problem with this file and can not be opened.')

        elif subtitle_file.lower().endswith(('.ttml', '.dfxp', '.xml', '.itt')):
            if '<tt ' in open(subtitle_file).read():
                subtitle_format = 'TTML'
            else:
                subtitle_format = 'DFXP'

            with open(subtitle_file, encoding='utf-8') as dfxp_file:
                dfxp_reader = pycaption.DFXPReader().read(dfxp_file.read())
                languages = dfxp_reader.get_languages()
                language = languages[0]
                captions = dfxp_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.smi', '.sami')):
            subtitle_format = 'SAMI'
            with open(subtitle_file, encoding='utf-8') as sami_file:
                sami_reader = pycaption.SAMIReader().read(sami_file.read())
                languages = sami_reader.get_languages()
                language = languages[0]
                captions = sami_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        # elif subtitle_file.lower().endswith(('.sbv')):
        #     subtitle_format = 'SBV'
        #     with open(subtitle_file, encoding='utf-8') as sbv_file:
        #         from captionstransformer.sbv import Reader
        #         captions = Reader(sbv_file).read()
        #         for caption in captions:
        #             segments_list.append([(caption.start - datetime.datetime(1900, 1, 1)).total_seconds(), caption.duration.total_seconds(), caption.text.strip()])

        elif subtitle_file.lower().endswith(('.ass', '.ssa', '.sub')):
            if subtitle_file.lower().endswith(('.ass', '.ssa')):
                subtitle_format = 'ASS'
                with open(subtitle_file, encoding='utf-8') as fileobj:
                    subfile = pysubs2.SSAFile.from_string(fileobj.read())
            elif subtitle_file.lower().endswith(('.sub')):
                subtitle_format = 'SUB'
                enc = chardet.detect(open(subtitle_file, 'rb').read())['encoding']
                subfile = pysubs2.SSAFile.from_string(open(subtitle_file, mode='rb').read().decode(enc, 'ignore'))

            for event in subfile.events:
                segments_list.append({
                    'start': event.start / 1000.0,
                    'end': (event.start / 1000.0) + (event.duration / 1000.0),
                    'text': event.plaintext
                })

        elif subtitle_file.lower().endswith(('.scc')):
            subtitle_format = 'SCC'

            with open(subtitle_file, encoding='utf-8') as scc_file:
                scc_reader = pycaption.SCCReader().read(scc_file.read())
                languages = scc_reader.get_languages()
                language = languages[0]
                captions = scc_reader.get_captions(language)
                for caption in captions:
                    segments_list.append({
                        'start': caption.start / 1000000,
                        'end': caption.end / 1000000,
                        'text': caption.get_text()
                    })

        elif subtitle_file.lower().endswith(('.usf')):
            subtitle_format = 'USF'
            reader = usf.USFReader()
            segments_list = reader.read(open(subtitle_file).read())
            if reader.language:
                session.SUBTITLE['language'] = reader.language
            # Speaker color/dubbing metadata is cheap — set synchronously.
            # Image decode + downscale moves to `_SpeakerImageLoader` so
            # 20 HD face crops don't add 0.5–2 s to the main-thread open.
            image_jobs = []
            for speaker_name, speaker_data in reader.speakers.items():
                existing = session.SPEAKERS.get(speaker_name, {})
                if 'color' in speaker_data:
                    existing['color'] = speaker_data['color']
                if isinstance(speaker_data.get('dubbing'), dict):
                    existing['dubbing'] = speaker_data['dubbing']
                session.SPEAKERS[speaker_name] = existing
                image_bytes = speaker_data.get('image_bytes')
                if image_bytes:
                    image_jobs.append((speaker_name, bytes(image_bytes), None))
            _start_speaker_image_loader(image_jobs)

            if not isinstance(session.FORMAT, dict):
                session.FORMAT = {}
            session.FORMAT['format'] = 'USF'
            session.FORMAT['options'] = {
                'embed_speaker_images': reader.has_embedded_images,
                'embed_audio_clips': reader.has_embedded_audio_clips,
            }

            dub_cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform')
            os.makedirs(dub_cache_dir, exist_ok=True)
            for segment in segments_list:
                for dub in segment.get('dubbing', []) or []:
                    raw = dub.pop('_embedded_bytes', None)
                    fmt = dub.pop('_embedded_format', 'wav')
                    if raw and dub.get('uid'):
                        target = os.path.join(dub_cache_dir, f'{dub["uid"]}.{fmt}')
                        if not os.path.exists(target):
                            try:
                                with open(target, 'wb') as wf:
                                    wf.write(raw)
                            except Exception:
                                pass
                        dub['path'] = target

        elif subtitle_file.lower().endswith(('.usfx')):
            subtitle_format = 'USFX'
            extract_dir = _usfx_extract_dir(subtitle_file)

            # Phase 1: extract only what the production screen needs to
            # render — USF/manifest/speakers/dubs. Heavy assets (FLAC
            # stems, waveform.npy) stream in afterwards via
            # _USFXBackgroundExtractor; bundled video is handled upstream
            # by peek_usfx_video().
            #
            # A corrupted USFX (Bad CRC, truncated, not a zip) raises
            # `zipfile.BadZipFile`. Re-raise as `CorruptedProjectFileError`
            # so the UI layer (startscreen) can catch a single typed
            # exception and show a friendly dialog instead of crashing
            # the open flow.
            try:
                with zipfile.ZipFile(subtitle_file, 'r') as zf:
                    for member in zf.namelist():
                        if _is_phase1_usfx_member(member):
                            zf.extract(member, extract_dir)
            except zipfile.BadZipFile as exc:
                raise CorruptedProjectFileError(str(exc)) from exc

            inner_usf = os.path.join(extract_dir, 'subtitles.usf')
            if not os.path.exists(inner_usf):
                for fname in os.listdir(extract_dir):
                    if fname.lower().endswith('.usf'):
                        inner_usf = os.path.join(extract_dir, fname)
                        break

            reader = usf.USFReader()
            with open(inner_usf, encoding='utf-8') as fh:
                segments_list = reader.read(fh.read())

            if reader.language:
                session.SUBTITLE['language'] = reader.language

            # Speaker color/dubbing metadata stays inline; image decode
            # is dispatched to `_SpeakerImageLoader` (same rationale as
            # the USF branch above). The fallback file-path resolution
            # also moves into the loader so its disk reads come off main.
            speakers_dir = os.path.join(extract_dir, 'assets', 'speakers')
            image_jobs = []
            for speaker_name, speaker_data in reader.speakers.items():
                existing = session.SPEAKERS.get(speaker_name, {})
                if 'color' in speaker_data:
                    existing['color'] = speaker_data['color']
                if isinstance(speaker_data.get('dubbing'), dict):
                    existing['dubbing'] = speaker_data['dubbing']
                session.SPEAKERS[speaker_name] = existing
                image_bytes = speaker_data.get('image_bytes')
                if image_bytes:
                    image_jobs.append((speaker_name, bytes(image_bytes), None))
                else:
                    # Try the first extension that exists on disk; the
                    # loader will load it. Cheap path-exists checks stay
                    # on main — they're a handful of stat() calls.
                    safe_name = _safe_asset_name(speaker_name)
                    fallback = None
                    for ext in ('png', 'jpg', 'jpeg'):
                        candidate = os.path.join(speakers_dir, f'{safe_name}.{ext}')
                        if os.path.exists(candidate):
                            fallback = candidate
                            break
                    if fallback:
                        image_jobs.append((speaker_name, None, fallback))
            _start_speaker_image_loader(image_jobs)

            for segment in segments_list:
                for dub in segment.get('dubbing', []) or []:
                    path = dub.get('path', '')
                    if path and not os.path.isabs(path):
                        dub['path'] = os.path.join(extract_dir, path)
                    # Mirror the rewrite for subclip paths: the saver
                    # bundled each unique source under `assets/dubs/`
                    # and stored the arcname; map back to the extracted
                    # absolute path so the audio engine / timeline can
                    # open the file. Skip entries already absolute (came
                    # from a legacy project or an in-place edit since load).
                    for seg in dub.get('segments', []) or []:
                        for key in ('path', 'raw_path'):
                            p = seg.get(key, '')
                            if p and not os.path.isabs(p):
                                seg[key] = os.path.join(extract_dir, p)

            # Resolve the project's source video. Priority: bundled video file →
            # original-path entry recorded in the manifest → same-folder match
            # against the manifest basename. Fall back is leaving session.VIDEO
            # empty so the start screen prompts the user.
            if not isinstance(session.VIDEO, dict):
                session.VIDEO = {}

            bundled_video_dir = os.path.join(extract_dir, 'assets', 'video')
            if os.path.isdir(bundled_video_dir):
                for fname in sorted(os.listdir(bundled_video_dir)):
                    candidate = os.path.join(bundled_video_dir, fname)
                    if os.path.isfile(candidate):
                        session.VIDEO['filepath'] = candidate
                        break

            manifest_path = os.path.join(extract_dir, 'manifest.xml')
            manifest_soup = None
            if os.path.isfile(manifest_path):
                try:
                    manifest_soup = BeautifulSoup(open(manifest_path, encoding='utf-8').read(), 'lxml-xml')
                except Exception:
                    manifest_soup = None

            if not session.VIDEO.get('filepath') and manifest_soup is not None:
                source_tag = manifest_soup.find('source')
                if source_tag is not None:
                    recorded_path = source_tag.get('path', '') or ''
                    recorded_basename = source_tag.get('basename', '') or os.path.basename(recorded_path)
                    usfx_dir = os.path.dirname(os.path.abspath(subtitle_file))
                    for candidate in (recorded_path, os.path.join(usfx_dir, recorded_basename) if recorded_basename else ''):
                        if candidate and os.path.isfile(candidate):
                            session.VIDEO['filepath'] = candidate
                            break

            if manifest_soup is not None:
                voicemix_tag = manifest_soup.find('voicemix')
                if voicemix_tag is not None and voicemix_tag.get('volume') is not None:
                    try:
                        session.VIDEO['music_voice_separation_volume_pending'] = float(voicemix_tag.get('volume'))
                    except (TypeError, ValueError):
                        pass

            # Spawn the Phase 2 extractor to stream heavy assets (FLAC
            # stems, waveform.npy) into their final cache locations in
            # the background. The reference is parked on the session
            # module so the QThread isn't garbage-collected mid-run; a
            # subsequent project open replaces it (the old thread either
            # finishes or its remaining writes no-op against existing
            # cache files via the `os.path.exists` guard in `_stream`).
            #
            # The `finished` signal is re-emitted on the global session
            # signal hub so the UI's progress-bar overlay can connect
            # once at startup and never has to track per-load instances.
            current_video = session.VIDEO.get('filepath') if isinstance(session.VIDEO, dict) else None
            current_key = utils.get_cache_key(current_video) if current_video else None
            # Spawn the extractor whenever there's *anything* deferred to
            # do: a cache_key for FLAC/waveform targets, or just the
            # extract_dir for dubs. Most USFX zips have at least dubs, so
            # the cache_key absence (e.g. video not yet resolved) is no
            # longer a reason to skip the whole background pass.
            if current_key or extract_dir:
                extractor = _USFXBackgroundExtractor(
                    subtitle_file, current_key, extract_dir=extract_dir,
                )
                try:
                    # Signal-to-signal forwarding (not signal-to-`.emit`
                    # bound method). Both forms work now that SIGNALS is
                    # imported at module load and so lives on the main
                    # thread, but signal-to-signal is the idiomatic Qt
                    # pattern and avoids relying on PySide6's tracking
                    # of a bound-method receiver.
                    extractor.finished.connect(SIGNALS.usfx_background_load_finished)
                    # Emit synchronously *before* start() so listeners
                    # (e.g. Save-button gate) flip to "loading" state
                    # without a race against the first emitted frame from
                    # the worker thread.
                    SIGNALS.usfx_background_load_started.emit()
                except Exception:
                    pass
                session.USFX_BACKGROUND_LOAD = extractor
                extractor.start()

            if not isinstance(session.FORMAT, dict):
                session.FORMAT = {}
            session.FORMAT['format'] = 'USFX'
            session.FORMAT['options'] = {}

        elif subtitle_file.lower().endswith(('.json')):
            subtitle_format = 'JSON'

            with open(subtitle_file, encoding='utf-8') as json_file:
                source_content = json.load(json_file)

                for segment in source_content['segments']:
                    segments_list.append({
                        'start': segment['start'],
                        'end': segment['end'],
                        'text': segment['text'],
                        'speaker': segment.get('speaker', 'A'),
                        'translations': segment.get('translations', {})
                    })
            if not 'config' in session.FORMAT:
                session.FORMAT['config'] = {
                    'standard': 'Whisper'
                }

    if not 'format' in session.FORMAT:
        session.FORMAT['format'] = subtitle_format

    return segments_list, subtitle_format


def process_video_file(video_file=False):
    """Function to process video file. It returns a dict with video properties."""
    video_metadata = {}
    json_result = waveform.ffmpeg_load_metadata(video_file)
    video_metadata['audio'] = False
    video_metadata['audio_is_present'] = False
    video_metadata['waveform'] = {}
    video_metadata['duration'] = float(json_result.get('format', {}).get('duration', '0.01'))
    for stream in json_result.get('streams', []):
        if stream.get('codec_type', '') == 'video' and not stream.get('codec_name', 'png') in ['png', 'mjpeg']:
            video_metadata['width'] = int(stream.get('width', 640))
            video_metadata['height'] = int(stream.get('height', 480))
            video_metadata['framerate'] = int(stream.get('r_frame_rate', '1/30').split('/', 1)[0]) / int(stream.get('r_frame_rate', '1/30').split('/', 1)[-1])
        elif stream.get('codec_type', '') in ['subtitle'] and not video_metadata.get('subtitles', False):
            video_metadata['subtitles'] = waveform.ffmpeg_extract_subtitle(video_file, stream.get('index', 2))
            # TODO: select what language if multiple embedded subtitles
        elif stream.get('codec_type', '') in ['audio']:
            video_metadata['audio_is_present'] = True
    video_metadata['filepath'] = video_file
    video_metadata['scenes'] = []

    return video_metadata


def import_file(filename=False, subtitle_format=False):  # , fit_to_length=False, length=.01, distribute_fixed_duration=False):
    """Function to import file into the subtitle project."""
    session.SUBTITLE['segments'] = []
    if filename:
        if filename.lower().endswith(('.txt')):
            subtitle_format = 'TXT'
            with open(filename) as txt_file:
                # clean("some input",
                #     fix_unicode=True,               # fix various unicode errors
                #     to_ascii=True,                  # transliterate to closest ASCII representation
                #     lower=True,                     # lowercase text
                #     no_line_breaks=False,           # fully strip line breaks as opposed to only normalizing them
                #     no_urls=False,                  # replace all URLs with a special token
                #     no_emails=False,                # replace all email addresses with a special token
                #     no_phone_numbers=False,         # replace all phone numbers with a special token
                #     no_numbers=False,               # replace all numbers with a special token
                #     no_digits=False,                # replace all digits with a special token
                #     no_currency_symbols=False,      # replace all currency symbols with a special token
                #     no_punct=False,                 # fully remove punctuation
                #     replace_with_url="<URL>",
                #     replace_with_email="<EMAIL>",
                #     replace_with_phone_number="<PHONE>",
                #     replace_with_number="<NUMBER>",
                # replace_with_digit="0",
                # replace_with_currency_symbol="<CUR>",
                # lang="en"                       # set to 'de' for German special handling
                # )

                # txt_content = clean(txt_file.read())
                txt_content = txt_file.read()
                pos = 0.0
                for phrase in txt_content.split('. '):
                    session.SUBTITLE['segments'].append({
                        'start': pos,
                        'end': pos + 5.0, 
                        'text': phrase + '.'
                    })
                    pos += 5.0

        elif filename.lower().endswith(('.srt')):
            subtitle_format = 'SRT'
            session.SUBTITLE['segments'] += process_subtitles_file(subtitle_file=filename, subtitle_format=subtitle_format)[0]

        elif filename.lower().endswith(('.docx')):
            subtitle_format = 'DOCX'
            txt_content = ''

            doc = Document(filename)
            for par in doc.paragraphs:
                txt_content += par.text

            pos = 0.0
            for phrase in txt_content.split('. '):
                session.SUBTITLE['segments'].append({
                    'start': pos,
                    'end': pos + 5.0,
                    'text': phrase + '.'
                })
                pos += 5.0

            session.SUBTITLE['segments'] += process_subtitles_file(subtitle_file=filename, subtitle_format=subtitle_format)[0]

    return session.SUBTITLE['segments'], subtitle_format


def export_file(filename=False, export_format='TXT', options=False):
    """Function to export file. A filepath and a subtitle dict is given."""
    if session.SUBTITLE['segments'] and filename:
        if export_format in ['.txt']:
            final_txt = ''
            for sub in session.SUBTITLE['segments']:
                final_txt += sub[2].replace('\n', ' ') + ' '
            if options:
                if options.get('new_line', False):
                    final_txt = final_txt.replace('. ', '.\n')
                    final_txt = final_txt.replace('! ', '!\n')
                    final_txt = final_txt.replace('? ', '?\n')
            with open(filename, mode='w', encoding='utf-8') as txt_file:
                txt_file.write(final_txt)
        elif export_format in ['.kdenlive']:
            final_xml = '''<?xml version='1.0' encoding='utf-8'?><mlt LC_NUMERIC="C" producer="main_bin" version="6.26.1" root="/home/jonata"><profile frame_rate_num="25" sample_aspect_num="1" display_aspect_den="9" colorspace="709" progressive="1" description="HD 1080p 25 fps" display_aspect_num="16" frame_rate_den="1" width="1920" height="1080" sample_aspect_den="1"/>'''

            i = 0
            for sub in session.SUBTITLE['segments']:
                final_xml += '''<producer id="producer{i}" in="{zerotime}" out="{out}">
                                <property name="length">{length}</property>
                                <property name="eof">pause</property>
                                <property name="resource"/>
                                <property name="progressive">1</property>
                                <property name="aspect_ratio">1</property>
                                <property name="seekable">1</property>
                                <property name="mlt_service">kdenlivetitle</property>
                                <property name="kdenlive:duration">125</property>
                                <property name="kdenlive:clipname">{clipname}</property>
                                <property name="xmldata">&lt;kdenlivetitle duration="125" LC_NUMERIC="C" width="1920" height="1080" out="124"> &lt;item type="QGraphicsTextItem" z-index="0"> &lt;position x="784" y="910"> &lt;transform>1,0,0,0,1,0,0,0,1&lt;/transform> &lt;/position>
                                &lt;content shadow="0;#64000000;3;3;3" font-underline="0" box-height="62" font-outline-color="0,0,0,255" font="Ubuntu" letter-spacing="0" font-pixel-size="54" font-italic="0" typewriter="0;2;1;0;0" alignment="1" font-weight="50" font-outline="0"
                                box-width="351.719" font-color="255,255,255,255">{content}&lt;/content> &lt;/item> &lt;startviewport rect="0,0,1920,1080"/> &lt;endviewport rect="0,0,1920,1080"/> &lt;background color="0,0,0,0"/> &lt;/kdenlivetitle>
                                </property>
                                <property name="kdenlive:folderid">-1</property>
                                <property name="kdenlive:id">{id}</property>
                                <property name="force_reload">0</property>
                                <property name="meta.media.width">1920</property>
                                <property name="meta.media.height">1080</property>
                            </producer>'''.format(i=i, id=i + 2, length=int(sub[1] * 25), clipname='Subtitle {i}'.format(i=i), content=sub[2], zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                i += 1

            final_xml += '''<playlist id="main_bin">
                            <property name="kdenlive:docproperties.activeTrack">2</property>
                            <property name="kdenlive:docproperties.audioChannels">2</property>
                            <property name="kdenlive:docproperties.audioTarget">-1</property>
                            <property name="kdenlive:docproperties.disablepreview">0</property>
                            <property name="kdenlive:docproperties.documentid">1621801540856</property>
                            <property name="kdenlive:docproperties.enableTimelineZone">0</property>
                            <property name="kdenlive:docproperties.enableexternalproxy">0</property>
                            <property name="kdenlive:docproperties.enableproxy">0</property>
                            <property name="kdenlive:docproperties.externalproxyparams">../Sub;;S03.MP4;../Clip;;.MXF</property>
                            <property name="kdenlive:docproperties.generateimageproxy">0</property>
                            <property name="kdenlive:docproperties.generateproxy">0</property>
                            <property name="kdenlive:docproperties.groups">[ ]
                            </property>
                            <property name="kdenlive:docproperties.kdenliveversion">21.04.0</property>
                            <property name="kdenlive:docproperties.position">372</property>
                            <property name="kdenlive:docproperties.previewextension"/>
                            <property name="kdenlive:docproperties.previewparameters"/>
                            <property name="kdenlive:docproperties.profile">atsc_1080p_25</property>
                            <property name="kdenlive:docproperties.proxyextension"/>
                            <property name="kdenlive:docproperties.proxyimageminsize">2000</property>
                            <property name="kdenlive:docproperties.proxyimagesize">800</property>
                            <property name="kdenlive:docproperties.proxyminsize">1000</property>
                            <property name="kdenlive:docproperties.proxyparams"/>
                            <property name="kdenlive:docproperties.scrollPos">0</property>
                            <property name="kdenlive:docproperties.seekOffset">30000</property>
                            <property name="kdenlive:docproperties.version">1</property>
                            <property name="kdenlive:docproperties.verticalzoom">1</property>
                            <property name="kdenlive:docproperties.videoTarget">-1</property>
                            <property name="kdenlive:docproperties.zonein">0</property>
                            <property name="kdenlive:docproperties.zoneout">75</property>
                            <property name="kdenlive:docproperties.zoom">8</property>
                            <property name="kdenlive:expandedFolders"/>
                            <property name="kdenlive:documentnotes"/>
                            <property name="xml_retain">1</property>\n'''

            i = 0
            for sub in session.SUBTITLE['segments']:
                final_xml += '''<entry producer="producer{i}" in="{zerotime}" out="{out}"/>\n'''.format(i=i, zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                i += 1

            final_xml += '''</playlist>
                            <producer id="black_track" in="00:00:00.000" out="00:20:12.120">
                                <property name="length">2147483647</property>
                                <property name="eof">continue</property>
                                <property name="resource">black</property>
                                <property name="aspect_ratio">1</property>
                                <property name="mlt_service">color</property>
                                <property name="mlt_image_format">rgb24a</property>
                                <property name="set.test_audio">0</property>
                            </producer>
                            <playlist id="playlist0">
                                <property name="kdenlive:audio_track">1</property>
                            </playlist>
                            <playlist id="playlist1"/>
                            <tractor id="tractor0" in="00:00:00.000">
                                <property name="kdenlive:audio_track">1</property>
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="video" producer="playlist0"/>
                                <track hide="video" producer="playlist1"/>
                            </tractor>
                            <playlist id="playlist2">
                                <property name="kdenlive:audio_track">1</property>
                            </playlist>
                            <playlist id="playlist3"/>
                            <tractor id="tractor1" in="00:00:00.000">
                                <property name="kdenlive:audio_track">1</property>
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="video" producer="playlist2"/>
                                <track hide="video" producer="playlist3"/>
                            </tractor>
                            <playlist id="playlist4"/>
                            <playlist id="playlist5"/>
                            <tractor id="tractor2" in="00:00:00.000" out="00:00:12.080">
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="audio" producer="playlist4"/>
                                <track producer="playlist5"/>
                            </tractor>
                            <playlist id="playlist6">'''
            i = 0
            last_intime = 0
            for sub in session.SUBTITLE['segments']:
                last_intime = sub['start'] - last_intime
                if last_intime:
                    final_xml += '''
                                    <blank length="{intime}"/>'''.format(intime=str(timecode.Timecode('1000', start_seconds=last_intime, fractional=True)))
                final_xml += '''
                                <entry producer="producer{i}" in="{zerotime}" out="{out}">
                                    <property name="kdenlive:id">{id}</property>
                                </entry>'''.format(i=i, id=i + 2, zerotime=str(timecode.Timecode('1000', start_seconds=0.001, fractional=True)), out=str(timecode.Timecode('1000', start_seconds=sub[1], fractional=True)))
                last_intime = sub['end']
                i += 1

            final_xml += '''
                            </playlist>
                            <playlist id="playlist7"/>
                            <tractor id="tractor3" in="00:00:00.000">
                                <property name="kdenlive:trackheight">69</property>
                                <property name="kdenlive:timeline_active">1</property>
                                <property name="kdenlive:collapsed">0</property>
                                <property name="kdenlive:thumbs_format"/>
                                <property name="kdenlive:audio_rec"/>
                                <track hide="audio" producer="playlist6"/>
                                <track producer="playlist7"/>
                            </tractor>
                            <tractor id="tractor4" global_feed="1" in="00:00:00.000" out="00:20:12.120">
                                <track producer="black_track"/>
                                <track producer="tractor0"/>
                                <track producer="tractor1"/>
                                <track producer="tractor2"/>
                                <track producer="tractor3"/>
                            </tractor>
                            </mlt>'''

            with open(filename, mode='w', encoding='utf-8') as txt_file:
                txt_file.write(final_xml)


def _ensure_dub_files_present(segments, existing_usfx_path):
    """Re-materialise any dub files referenced by `segments` whose
    absolute paths don't currently exist on disk, by copying their
    bytes out of `existing_usfx_path` (the USFX we're about to
    overwrite). No-op when `existing_usfx_path` doesn't exist or
    isn't a valid zip — first-ever save still works the old way.

    Without this rescue, a save run while the background extractor
    is still streaming dubs (or after the user cleared the cache)
    would silently produce a USFX missing those files. See the
    long comment in `save_file` for the full motivation."""
    if not existing_usfx_path or not os.path.isfile(existing_usfx_path):
        return

    missing = []  # list of (absolute_path, expected_arcname)

    def _consider(p):
        if not p or not isinstance(p, str):
            return
        if os.path.isfile(p):
            return
        # Derive the arcname by finding the `assets/dubs/...` substring
        # of the absolute path. `process_subtitles_file` builds the
        # session's dub paths as `<extract_dir>/assets/dubs/<file>`,
        # so the suffix is stable across machines.
        marker = '/assets/dubs/'
        idx = p.find(marker)
        if idx < 0:
            return
        arcname = p[idx + 1:].replace(os.sep, '/')
        missing.append((p, arcname))

    for segment in segments:
        for dub in segment.get('dubbing', []) or []:
            _consider(dub.get('path'))
            for seg in dub.get('segments', []) or []:
                _consider(seg.get('path'))
                _consider(seg.get('raw_path'))

    if not missing:
        return

    try:
        with zipfile.ZipFile(existing_usfx_path, 'r') as old_zf:
            namelist = set(old_zf.namelist())
            for abs_path, arcname in missing:
                if arcname not in namelist:
                    continue
                try:
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    with old_zf.open(arcname) as src, open(abs_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                except Exception:
                    # One bad member shouldn't block the others — the
                    # bundling loop below will still silently skip
                    # whatever we couldn't rescue, same as before.
                    pass
    except zipfile.BadZipFile:
        # Existing USFX is corrupted; nothing to rescue from. The
        # bundling loop will skip missing files as before.
        return


def save_file(final_file, subtitle_format='USFX', language='en'):
    """Function to save the subtitle project. A subtitles dict and the format is given.

    The `language` argument is treated as a fallback — the document's own
    `session.SUBTITLE['language']` (set on import / transcription / by the
    user) wins when present so reloading a USFX restores the language the
    user actually picked, not the UI-default.

    Reads document state through local bindings (`SUBTITLE` / `SPEAKERS`
    / `VIDEO` / `FORMAT`) that come from `_active_save_snapshot` when
    one is set (background save threads), falling back to live session
    when called synchronously on the main thread. The snapshot path
    avoids racing the UI thread's edits during the write."""
    snap = _active_save_snapshot
    if snap is not None:
        SUBTITLE = snap['SUBTITLE']
        SPEAKERS = snap['SPEAKERS']
        VIDEO = snap['VIDEO']
        FORMAT = snap['FORMAT']
    else:
        SUBTITLE = session.SUBTITLE
        SPEAKERS = session.SPEAKERS
        VIDEO = session.VIDEO
        FORMAT = session.FORMAT
    document_language = (SUBTITLE.get('language') or '').strip()
    if document_language:
        language = document_language
    # `segments` may legitimately be empty — e.g. the user loaded a
    # video and clicked Save before transcribing or adding any
    # subtitles. We still want to persist the project file (video
    # reference, speakers, manifest). The old truthiness check made the
    # whole body a no-op, and the caller saw `success=True` because
    # nothing raised — UI showed "saved" while no file landed.
    if SUBTITLE.get('segments') is not None:
        # if not final_file.lower().endswith('.' + format.lower()):
        #     final_file += '.' + format.lower()

        if 'format' not in FORMAT:
            # Write back to the live session as well so the next save
            # from the main thread sees the chosen format. Harmless when
            # FORMAT *is* the live session dict.
            FORMAT['format'] = subtitle_format
            if FORMAT is not session.FORMAT:
                session.FORMAT['format'] = subtitle_format

        if subtitle_format in ['SRT', 'DFXP', 'TTML', 'SAMI', 'SCC', 'VTT']:
            captions = pycaption.CaptionList()
            for sub in SUBTITLE['segments']:
                # skip extra blank lines
                nodes = [pycaption.CaptionNode.create_text(sub['text'])]
                caption = pycaption.Caption(start=sub['start'] * 1000000, end=(sub['end']) * 1000000, nodes=nodes)
                captions.append(caption)
            caption_set = pycaption.CaptionSet({language: captions})

            if subtitle_format == 'SRT':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SRTWriter().write(caption_set))
            elif subtitle_format in ['DFXP', 'TTML']:
                open(final_file, mode='w', encoding='utf-8').write(pycaption.DFXPWriter().write(caption_set))
            elif subtitle_format == 'SAMI':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SAMIWriter().write(caption_set))
            elif subtitle_format == 'SCC':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.SCCWriter().write(caption_set))
            elif subtitle_format == 'VTT':
                open(final_file, mode='w', encoding='utf-8').write(pycaption.WebVTTWriter().write(caption_set))

        elif subtitle_format in ['ASS', 'SBV', 'XML', 'SUB']:
            if subtitle_format in ['ASS', 'SUB']:
                assfile = pysubs2.SSAFile()
                index = 0
                for sub in reversed(sorted(SUBTITLE['segments'])):
                    assfile.insert(
                        index,
                        pysubs2.SSAEvent(
                            start=int(sub['start'] * 1000),
                            end=int(sub['end'] * 1000),
                            text=sub['text'].replace('\n', ' ')
                        )
                    )
                if subtitle_format == 'SUB':
                    assfile.save(final_file, subtitle_format='microdvd')
                else:
                    assfile.save(final_file)
            # else:
            #     if subtitle_format == 'SBV':
            #         from captionstransformer.sbv import Writer
            #     elif subtitle_format == 'XML':
            #         from captionstransformer.transcript import Writer
            #     writer = Writer(open(final_file, mode='w', encoding='utf-8'))
            #     captions = []
            #     for cap in session.SUBTITLE['segments']:
            #         caption = captionstransformer.core.Caption()
            #         caption.start = captionstransformer.core.get_date(second=int(cap[0] // 1), millisecond=int((cap[0] % 1) * 1000))
            #         caption.duration = captionstransformer.core.get_date(second=int(cap[1] // 1), millisecond=int((cap[1] % 1) * 1000)) - captionstransformer.core.get_date()
            #         caption.text = cap[2]
            #         captions.append(caption)
            #     writer.set_captions(captions)
            #     writer.write()

        elif subtitle_format in ['JSON']:
            if FORMAT.get('options', {}).get('standard', 'Whisper') == 'Whisper':
                open(final_file, mode='w', encoding='utf-8').write(json.dumps(SUBTITLE, indent=4))
            elif FORMAT['options'].get('standard', 'Whisper') == 'AD':
                new_json_dict = {
                    'metadata': {
                        'framerate': VIDEO.get('framerate', 25),
                        'video_name': os.path.basename(VIDEO.get('filepath', ''))
                    },
                    'description_cues': [],
                }
                for i, segment in enumerate(SUBTITLE['segments']):
                    new_json_dict['description_cues'].append({
                        'cue_number': i,
                        'text': segment['text'],
                        'start_frame': int(segment['start'] * VIDEO.get('framerate', 25)),
                        'end_frame': int(segment['end'] * VIDEO.get('framerate', 25)),
                        'start_time_smpte': str(timecode.Timecode(VIDEO.get('framerate', 25), start_seconds=segment['start'], fractional=False)),
                        'end_time_smpte': str(timecode.Timecode(VIDEO.get('framerate', 25), start_seconds=segment['end'], fractional=False)),
                        'start_time': str(timecode.Timecode(VIDEO.get('framerate', 25), start_seconds=segment['start'], fractional=True)),
                        'end_time': str(timecode.Timecode(VIDEO.get('framerate', 25), start_seconds=segment['end'], fractional=True)),
                        'speaker': segment.get('speaker', 'A')
                    })

                open(final_file, mode='w', encoding='utf-8').write(json.dumps(new_json_dict, indent=4))


        elif subtitle_format in ['USF']:
            options = FORMAT.get('options', {}) if isinstance(FORMAT, dict) else {}
            embed_speaker_images = bool(options.get('embed_speaker_images', False))
            embed_audio_clips = bool(options.get('embed_audio_clips', False))

            writer_speakers = {}
            for name, data in SPEAKERS.items():
                if not isinstance(data, dict):
                    continue
                entry = {}
                if data.get('color'):
                    entry['color'] = data['color']
                if isinstance(data.get('dubbing'), dict):
                    entry['dubbing'] = data['dubbing']
                if embed_speaker_images and isinstance(data.get('image'), QImage):
                    buf = QByteArray()
                    qbuf = QBuffer(buf)
                    qbuf.open(QIODevice.WriteOnly)
                    if data['image'].save(qbuf, 'PNG'):
                        entry['image_bytes'] = bytes(buf)
                    qbuf.close()
                if entry:
                    writer_speakers[name] = entry

            open(final_file, mode='w', encoding='utf-8').write(usf.USFWriter().write(
                SUBTITLE['segments'],
                speakers=writer_speakers,
                language=language,
                embed_audio_clips=embed_audio_clips,
            ))

        elif subtitle_format in ['USFX']:
            usfx_options = _get_usfx_options()

            writer_speakers = {}
            speaker_image_bytes = {}
            for name, data in SPEAKERS.items():
                if not isinstance(data, dict):
                    continue
                entry = {}
                if data.get('color'):
                    entry['color'] = data['color']
                if isinstance(data.get('dubbing'), dict):
                    entry['dubbing'] = data['dubbing']
                if usfx_options['include_speaker_images'] and isinstance(data.get('image'), QImage):
                    buf = QByteArray()
                    qbuf = QBuffer(buf)
                    qbuf.open(QIODevice.WriteOnly)
                    if data['image'].save(qbuf, 'PNG'):
                        speaker_image_bytes[name] = bytes(buf)
                    qbuf.close()
                if entry:
                    writer_speakers[name] = entry

            # When `_active_save_snapshot` populated SUBTITLE, this
            # deepcopy is on a stable snapshot taken under the main
            # thread — safe from the iteration race that took the
            # whole app down before the snapshot path landed.
            segments_copy = copy.deepcopy(SUBTITLE['segments'])

            # Before bundling: rescue any dub files we're about to lose.
            #
            # The session's dub paths are absolute paths into the USFX
            # extract dir (set by `process_subtitles_file` on open). If
            # the user cleared the cache, or the background extractor
            # hasn't gotten to a particular `assets/dubs/...` member
            # yet, the file won't exist on disk RIGHT NOW — and the
            # bundling loop below would silently skip it (line 1227's
            # `os.path.isfile` check). On the *next* open the dub would
            # have no audio, even though the previous USFX had it.
            #
            # We re-extract any missing dub members from the existing
            # USFX (the one we're about to overwrite) so they're back
            # on disk before the bundling loop runs. This is also why
            # save is non-destructive even when the cache is empty.
            _ensure_dub_files_present(segments_copy, final_file)
            dub_files_to_include = {}
            for segment in segments_copy:
                for dub in segment.get('dubbing', []) or []:
                    uid = dub.get('uid')
                    if not uid:
                        continue
                    source_path = dub.get('path')
                    if source_path and os.path.isfile(source_path):
                        ext = (os.path.splitext(source_path)[1].lstrip('.').lower() or 'wav')
                        arcname = f'assets/dubs/{_safe_asset_name(uid)}.{ext}'
                        dub_files_to_include[source_path] = arcname
                        dub['path'] = arcname
                    # Per-subclip paths (the split/stretch list). Each
                    # unique source file gets one zip entry; subclips
                    # referencing the same source (the common case after
                    # plain splits) collapse onto the same arcname.
                    # Stretched subclips have a different `path` than the
                    # dub-level one — they get a uid-namespaced arcname.
                    # `raw_path` (immutable raw source for re-stretching)
                    # gets bundled too so stretch-after-reload still works.
                    counter = [0]
                    def _register_subclip_path(src):
                        if not src or not os.path.isfile(src):
                            return None
                        if src in dub_files_to_include:
                            return dub_files_to_include[src]
                        base = os.path.basename(src)
                        safe_base = _safe_asset_name(base)
                        arc = f'assets/dubs/{_safe_asset_name(uid)}__{counter[0]}__{safe_base}'
                        counter[0] += 1
                        dub_files_to_include[src] = arc
                        return arc
                    for seg in dub.get('segments', []) or []:
                        for key in ('path', 'raw_path'):
                            new_arc = _register_subclip_path(seg.get(key))
                            if new_arc:
                                seg[key] = new_arc

            # Collect optional bundled assets (video + separation caches + waveform cache).
            video_path = VIDEO.get('filepath', '') if isinstance(VIDEO, dict) else ''
            video_cache_key = utils.get_cache_key(video_path) if video_path else None
            extra_files_to_include = {}  # source path -> arcname

            if usfx_options['include_original_video'] and video_path and os.path.isfile(video_path):
                extra_files_to_include[video_path] = f'assets/video/{_safe_asset_name(os.path.basename(video_path))}'

            if video_cache_key:
                if usfx_options['include_waveform_cache']:
                    wf_source = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{video_cache_key}_waveform.npy')
                    if os.path.isfile(wf_source):
                        extra_files_to_include[wf_source] = 'assets/waveform.npy'
                if usfx_options['include_original_audio']:
                    src = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{video_cache_key}_original.flac')
                    if os.path.isfile(src):
                        extra_files_to_include[src] = 'assets/audio/original.flac'
                if usfx_options['include_processed_audio']:
                    for kind in ('vocals', 'background'):
                        src = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{video_cache_key}_{kind}.flac')
                        if os.path.isfile(src):
                            extra_files_to_include[src] = f'assets/audio/{kind}.flac'

            xml_content = usf.USFWriter().write(
                segments_copy,
                speakers=writer_speakers,
                language=language,
                embed_audio_clips=False,
            )

            def _mime_for(path_in_zip):
                ext = path_in_zip.rsplit('.', 1)[-1].lower()
                return {
                    'usf': 'application/x-usf+xml',
                    'png': 'image/png',
                    'wav': 'audio/wav',
                    'flac': 'audio/flac',
                    'mp3': 'audio/mpeg',
                    'mp4': 'video/mp4',
                    'mkv': 'video/x-matroska',
                    'mov': 'video/quicktime',
                    'webm': 'video/webm',
                    'ogv': 'video/ogg',
                    'npy': 'application/octet-stream',
                }.get(ext, 'application/octet-stream')

            manifest_entries = [('subtitles.usf', 'application/x-usf+xml')]
            for name in speaker_image_bytes:
                manifest_entries.append((f'assets/speakers/{_safe_asset_name(name)}.png', 'image/png'))
            for arc in dub_files_to_include.values():
                manifest_entries.append((arc, _mime_for(arc)))
            for arc in extra_files_to_include.values():
                manifest_entries.append((arc, _mime_for(arc)))

            manifest_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                              '<manifest version="1.0">',
                              '  <format>USFX</format>',
                              '  <generator>Subtitld</generator>']
            if video_path:
                escaped_path = video_path.replace('&', '&amp;').replace('"', '&quot;')
                escaped_basename = os.path.basename(video_path).replace('&', '&amp;').replace('"', '&quot;')
                manifest_lines.append(f'  <source path="{escaped_path}" basename="{escaped_basename}"/>')
            mvs = VIDEO.get('music_voice_separation') if isinstance(VIDEO, dict) else None
            if isinstance(mvs, dict) and 'volume' in mvs:
                try:
                    manifest_lines.append(f'  <voicemix volume="{float(mvs["volume"]):.4f}"/>')
                except (TypeError, ValueError):
                    pass
            for path, mime in manifest_entries:
                manifest_lines.append(f'  <entry path="{path}" type="{mime}"/>')
            manifest_lines.append('</manifest>')
            manifest_xml = '\n'.join(manifest_lines)

            tmp_path = final_file + '.tmp'
            try:
                with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr('subtitles.usf', xml_content)
                    zf.writestr('manifest.xml', manifest_xml)
                    for source, arc in dub_files_to_include.items():
                        zf.write(source, arc)
                    for name, img_bytes in speaker_image_bytes.items():
                        zf.writestr(f'assets/speakers/{_safe_asset_name(name)}.png', img_bytes)
                    for source, arc in extra_files_to_include.items():
                        zf.write(source, arc)
                # Verify the tmp file is a structurally valid zip with
                # intact CRCs for every member BEFORE atomic rename —
                # otherwise a partial / I/O-corrupted write would
                # overwrite the (still valid) destination, and the next
                # open would crash on `Bad CRC-32`. testzip() returns
                # the name of the first bad member, or None on success.
                with zipfile.ZipFile(tmp_path, 'r') as zf_check:
                    bad = zf_check.testzip()
                    if bad is not None:
                        raise zipfile.BadZipFile(
                            f'CRC mismatch on member {bad!r} — refusing to overwrite destination',
                        )
                os.replace(tmp_path, final_file)
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                raise


def peek_usfx_video(usfx_path):
    """Inspect a USFX bundle and return the resolved video path without
    loading the full project. Used by the start screen so it can skip the
    'select video' prompt when the project remembers (or bundles) it.
    Returns the absolute path on success, otherwise None."""
    if not usfx_path or not os.path.isfile(usfx_path):
        return None
    try:
        with zipfile.ZipFile(usfx_path, 'r') as zf:
            usfx_dir = os.path.dirname(os.path.abspath(usfx_path))

            # Bundled video first.
            for name in zf.namelist():
                if name.startswith('assets/video/') and not name.endswith('/'):
                    extract_dir = _usfx_extract_dir(usfx_path)
                    target = os.path.join(extract_dir, name)
                    if not os.path.isfile(target):
                        zf.extract(name, extract_dir)
                    if os.path.isfile(target):
                        return target

            try:
                manifest_data = zf.read('manifest.xml').decode('utf-8')
            except KeyError:
                return None

        soup = BeautifulSoup(manifest_data, 'lxml-xml')
        source = soup.find('source')
        if source is None:
            return None

        recorded_path = source.get('path', '') or ''
        recorded_basename = source.get('basename', '') or os.path.basename(recorded_path)

        for candidate in (recorded_path, os.path.join(usfx_dir, recorded_basename) if recorded_basename else ''):
            if candidate and os.path.isfile(candidate):
                return candidate
    except Exception:
        return None
    return None


def _autosave_filename_stem():
    if session.SUBTITLE.get('filepath'):
        stem = os.path.basename(session.SUBTITLE['filepath']).rsplit('.', 1)[0]
        if stem:
            return stem
    if session.VIDEO.get('filepath'):
        return os.path.basename(session.VIDEO['filepath']).rsplit('.', 1)[0]
    return ''


def _prune_backups(stem):
    max_count = int(session.CONFIG.get('autosave', {}).get('backup_max_count', 20))
    if max_count <= 0:
        return
    backup_dir = str(session.PATH_SUBTITLD_DATA_BACKUP)
    try:
        candidates = [
            os.path.join(backup_dir, name)
            for name in os.listdir(backup_dir)
            if name.startswith(stem + '_') and name.endswith('.usfx')
        ]
    except OSError:
        return
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for stale in candidates[max_count:]:
        try:
            os.unlink(stale)
        except OSError:
            pass


# Module-level save coordination. Two crashes were tracked to this area:
#   1. The autosave backup timer + the autosave original timer can fire
#      within milliseconds and spawn two `SaveFileThread`s that run their
#      USFX writes concurrently. `_save_lock` serialises them so at most
#      one save runs at a time — the second blocks until the first
#      releases.
#   2. The save thread used to deepcopy `session.SUBTITLE['segments']`
#      directly while the UI thread could still be editing the list
#      (add/remove subtitle). That can raise `RuntimeError: list changed
#      size during iteration` deep inside CPython's deepcopy, taking the
#      whole app down. `_snapshot_for_save()` runs on the main thread
#      BEFORE the worker starts, producing a stable copy that the worker
#      reads through `_active_save_snapshot`. The lock keeps the
#      snapshot single-writer.
_save_lock = threading.Lock()
_active_save_snapshot = None
_save_in_progress = False


def _snapshot_for_save():
    """Main thread only. Deep-copy the mutable session collections so
    the save thread sees a stable view regardless of user edits during
    the write. Returns None if the live state itself is mid-mutation —
    callers (especially autosave) can use that to retry later."""
    try:
        return {
            'SUBTITLE': copy.deepcopy(session.SUBTITLE),
            'SPEAKERS': copy.deepcopy(session.SPEAKERS),
            'VIDEO': copy.deepcopy(session.VIDEO),
            'FORMAT': copy.deepcopy(session.FORMAT) if isinstance(session.FORMAT, dict) else session.FORMAT,
        }
    except Exception:
        return None


class SaveFileThread(QThread):
    """Run `save_file()` on a background thread so the UI stays responsive
    while USFX bundles are zipped and assets are written to disk."""
    save_finished = Signal(str, bool, str)  # (filepath, success, error)

    def __init__(self, final_file, subtitle_format, language, snapshot=None, parent=None):
        super().__init__(parent)
        self._final_file = final_file
        self._subtitle_format = subtitle_format
        self._language = language
        self._snapshot = snapshot

    def run(self):
        global _active_save_snapshot
        success = False
        error = ''
        try:
            # Serialise: a previous save thread (autosave-backup,
            # autosave-original, manual save) holding `_save_lock`
            # forces this one to wait rather than writing to the same
            # paths concurrently.
            with _save_lock:
                _active_save_snapshot = self._snapshot
                try:
                    save_file(self._final_file, self._subtitle_format, self._language)
                    success = True
                except Exception as exc:
                    error = str(exc)
                finally:
                    _active_save_snapshot = None
        except Exception as exc:
            error = str(exc)
        self.save_finished.emit(self._final_file, success, error)


_active_save_threads = []


def wait_for_save_threads():
    """Block until every in-flight save thread finishes. Call before exit so
    autosave/manual saves still flush even when the user closes the window
    while a write is queued or running."""
    for thread in list(_active_save_threads):
        try:
            thread.wait()
        except RuntimeError:
            pass


def save_file_async(final_file, subtitle_format='USFX', language='en', on_done=None, parent=None):
    """Spawn a `SaveFileThread`, optionally wire `on_done(path, ok, err)`,
    keep a reference so the QThread isn't garbage-collected mid-save.

    Takes a deep-copy snapshot of the mutable session state on the MAIN
    thread before starting the worker — without this, the worker's own
    deepcopy could race a UI-thread edit and crash."""
    global _save_in_progress
    snapshot = _snapshot_for_save()
    thread = SaveFileThread(final_file, subtitle_format, language, snapshot=snapshot, parent=parent)

    def _cleanup(path, success, error):
        global _save_in_progress
        _save_in_progress = False
        if on_done is not None:
            on_done(path, success, error)
        if thread in _active_save_threads:
            _active_save_threads.remove(thread)
        thread.deleteLater()

    thread.save_finished.connect(_cleanup, Qt.QueuedConnection)
    _active_save_threads.append(thread)
    _save_in_progress = True
    thread.start()
    return thread


def autosave_backup_timer_timeout(force=False):
    if not session.SUBTITLE:
        return
    if not force and not session.AUTOSAVE_BACKUP_DIRTY:
        return
    # If another save is already in flight (the autosave-original
    # timer, a manual save, or this same timer's previous run still
    # finishing) skip this tick. It fires every few minutes; the next
    # one will catch up. Without this, two save threads can spawn in
    # the same event-loop pass and crash on a shared deepcopy of
    # `session.SUBTITLE['segments']`.
    if _save_in_progress:
        return
    stem = _autosave_filename_stem()
    if not stem:
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    def _on_done(path, success, _error):
        if not success:
            return
        _prune_backups(stem)
        session.AUTOSAVE_BACKUP_DIRTY = False
        session.AUTOSAVE_LAST_BACKUP = datetime.datetime.now()
        for callback in session._autosave_status_callbacks:
            callback()

    save_file_async(
        os.path.join(str(session.PATH_SUBTITLD_DATA_BACKUP), f'{stem}_{timestamp}.usfx'),
        'USFX',
        session.CONFIG.get('selected_language', 'en'),
        on_done=_on_done,
    )


def autosave_original_timer_timeout():
    if not session.SUBTITLE:
        return
    if not session.UNSAVED:
        return
    if not session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'):
        return
    # See `autosave_backup_timer_timeout` for why we skip when a save
    # is already in flight.
    if _save_in_progress:
        return

    def _on_done(_path, success, _error):
        if not success:
            return
        session.set_unsaved(False)
        session.AUTOSAVE_BACKUP_DIRTY = False
        session.AUTOSAVE_LAST_ORIGINAL = datetime.datetime.now()
        session.notify_save_success()
        for callback in session._autosave_status_callbacks:
            callback()

    save_file_async(
        session.SUBTITLE['filepath'],
        'USFX',
        session.CONFIG['selected_language'],
        on_done=_on_done,
    )
