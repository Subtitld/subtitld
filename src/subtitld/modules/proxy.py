"""Downscaled, all-intra H.264 **proxy** videos for large (4K/8K) sources.

Editing an 8K file is painful because every seek decodes a huge frame. A proxy
is a small stand-in the Qt video surface plays instead — the timeline/waveform
and Subtitld's own audio engine keep using the *original* file, so timing stays
sample-accurate.

Design choices:

* **Video-only** (`-an`): Subtitld plays audio through its own engine from the
  original, so the proxy never needs an audio stream — smaller, faster, and no
  A/V-sync concern.
* **All-intra H.264** (`-g 1`, keyframe every frame): instant frame-accurate
  scrubbing, which is the whole point for subtitle timing; and H.264 plays
  everywhere Qt/FFmpeg does.
* **Hardware fast-path**: a GPU encoder (NVENC / QSV / VAAPI) is used when it is
  actually present AND passes a 1-frame validation encode (a listed encoder can
  still fail at runtime with no driver/device), falling back to libx264 (CPU).

Files are cached under ``PATH_SUBTITLD_DATA_PROXY`` keyed to the source's
path/size/mtime, so re-selecting a scale is instant and a changed source
invalidates automatically.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal, Qt

from subtitld.modules import session, utils


SCALES = (25, 50, 75, 100)

# Preference order: fastest/most-common hardware first, CPU last. libx264 is the
# always-present fallback and never fails validation.
_ENCODER_ORDER = ('h264_nvenc', 'h264_qsv', 'h264_vaapi', 'libx264')

# Session-cached detection result (None = not yet probed).
_detected_encoder = None


# ---------------------------------------------------------------------------
# Cache paths
# ---------------------------------------------------------------------------
def proxy_path_for(source_path: str, scale: int) -> str:
    """Deterministic cache path for a (source, scale) proxy."""
    key = utils.get_cache_key(source_path)
    return os.path.join(str(session.PATH_SUBTITLD_DATA_PROXY), f'{key}_{int(scale)}.mp4')


def existing_proxy(source_path: str, scale: int) -> str | None:
    """Return the cached proxy path if it exists and is non-empty, else None."""
    path = proxy_path_for(source_path, scale)
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    except OSError:
        pass
    return None


def scaled_dimensions(width: int, height: int, scale: int) -> tuple[int, int]:
    """Target (w, h) for a scale %, forced even (H.264 requires even dims)."""
    w = max(2, int(round(width * scale / 100.0)) // 2 * 2)
    h = max(2, int(round(height * scale / 100.0)) // 2 * 2)
    return w, h


# ---------------------------------------------------------------------------
# ffmpeg command construction (per encoder)
# ---------------------------------------------------------------------------
def _scale_filter(scale: int) -> str:
    # trunc(.../2)*2 forces even width AND height at any scale (100% included).
    f = scale / 100.0
    return f'scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2'


def _build_command(encoder: str, source: str, scale: int, output: str,
                   *, one_frame: bool = False, progress: bool = False) -> list[str]:
    """Full ffmpeg argv for `encoder`. `one_frame` builds the validation
    encode; `progress` adds machine-readable progress on stdout."""
    ff = str(session.FFMPEG_EXECUTABLE)
    vf = _scale_filter(scale)

    pre: list[str] = [ff, '-hide_banner', '-loglevel', 'error', '-y']
    inp: list[str] = ['-i', source]
    # Video-only, even pixel format, faststart so Qt can play while/after write.
    out: list[str] = ['-an', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']

    if encoder == 'libx264':
        vargs = ['-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast',
                 '-g', '1', '-x264-params', 'keyint=1:scenecut=0', '-crf', '23']
    elif encoder == 'h264_nvenc':
        # NVENC accepts software frames from `-vf scale` and uploads internally.
        vargs = ['-vf', vf, '-c:v', 'h264_nvenc', '-preset', 'p4',
                 '-g', '1', '-rc', 'vbr', '-cq', '23']
    elif encoder == 'h264_qsv':
        vargs = ['-vf', vf + ',format=nv12', '-c:v', 'h264_qsv',
                 '-g', '1', '-global_quality', '23']
    elif encoder == 'h264_vaapi':
        # VAAPI needs an explicit device + hwupload before the encoder.
        pre = [ff, '-hide_banner', '-loglevel', 'error', '-y',
               '-init_hw_device', 'vaapi=va:/dev/dri/renderD128', '-filter_hw_device', 'va']
        vargs = ['-vf', vf + ',format=nv12,hwupload', '-c:v', 'h264_vaapi',
                 '-g', '1', '-qp', '23']
    else:
        raise ValueError(f'unknown encoder {encoder!r}')

    cmd = pre + inp + vargs + out
    if one_frame:
        cmd += ['-frames:v', '1']
    if progress:
        cmd += ['-progress', 'pipe:1', '-nostats']
    cmd += [output]
    return cmd


# ---------------------------------------------------------------------------
# Encoder detection (validated)
# ---------------------------------------------------------------------------
def _ffmpeg_encoder_names() -> set[str]:
    """Names ffmpeg reports it was *built* with (necessary, not sufficient)."""
    try:
        out = subprocess.run(
            [str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-encoders'],
            startupinfo=session.STARTUPINFO,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        ).stdout.decode('utf-8', 'replace')
    except Exception:
        return set()
    return {m for m in ('h264_nvenc', 'h264_qsv', 'h264_vaapi', 'libx264') if m in out}


def _validate_encoder(encoder: str, source: str) -> bool:
    """True if a 1-frame encode with this encoder+pipeline actually succeeds
    against the real source — catches drivers/devices missing at runtime."""
    tmp = os.path.join(tempfile.gettempdir(), f'subtitld_proxy_probe_{encoder}.mp4')
    try:
        cmd = _build_command(encoder, source, 50, tmp, one_frame=True)
        rc = subprocess.run(
            cmd, startupinfo=session.STARTUPINFO,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
        )
        return rc.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0
    except Exception:
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def detect_encoder(source_path: str, force: bool = False) -> str:
    """Pick the best working encoder for this ffmpeg build + machine, validated
    against `source_path`. Cached for the session (and in CONFIG for display)."""
    global _detected_encoder
    if _detected_encoder is not None and not force:
        return _detected_encoder

    built = _ffmpeg_encoder_names()
    chosen = 'libx264'
    for name in _ENCODER_ORDER:
        if name == 'libx264':
            chosen = 'libx264'
            break
        if name in built and _validate_encoder(name, source_path):
            chosen = name
            break

    _detected_encoder = chosen
    try:
        session.CONFIG.setdefault('proxy', {})['encoder'] = chosen
    except Exception:
        pass
    return chosen


def encoder_label(encoder: str) -> str:
    return {
        'h264_nvenc': 'H.264 (NVIDIA NVENC)',
        'h264_qsv': 'H.264 (Intel Quick Sync)',
        'h264_vaapi': 'H.264 (VAAPI)',
        'libx264': 'H.264 (CPU / libx264)',
    }.get(encoder, encoder)


# ---------------------------------------------------------------------------
# Encoder worker (background, cancellable, real progress)
# ---------------------------------------------------------------------------
class ProxyEncodeThread(QThread):
    """Encode one proxy on a background thread.

    Signals:
      progress(float 0..1)              — parsed from ffmpeg -progress
      finished_ok(str output_path)      — success
      finished_err(str message)         — failure / cancel
    """
    progress = Signal(float)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, source_path, scale, output_path, encoder,
                 duration=0.0, parent=None):
        super().__init__(parent)
        self._source = source_path
        self._scale = scale
        self._output = output_path
        self._encoder = encoder
        self._duration = float(duration or 0.0)
        self._proc = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self):
        # Detect the best encoder here (off the UI thread) if the caller didn't
        # pre-resolve one — validation runs real 1-frame encodes and can take a
        # couple of seconds the first time.
        if not self._encoder:
            self._encoder = detect_encoder(self._source)
        # Encode to a temp sibling then atomically rename, so a cancelled or
        # crashed run never leaves a half-written file that the cache check
        # would later treat as valid.
        tmp = self._output + '.tmp.mp4'
        cmd = _build_command(self._encoder, self._source, self._scale, tmp, progress=True)
        try:
            self._proc = subprocess.Popen(
                cmd, startupinfo=session.STARTUPINFO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
            )
        except Exception as exc:
            self.finished_err.emit(str(exc))
            return

        # Parse `out_time_us=` lines against the known duration for a real %.
        total_us = self._duration * 1_000_000
        for line in self._proc.stdout:
            if self._cancelled:
                break
            m = re.match(r'out_time_us=(\d+)', line.strip())
            if m and total_us > 0:
                frac = min(0.999, int(m.group(1)) / total_us)
                self.progress.emit(frac)
        rc = self._proc.wait()

        if self._cancelled:
            self._cleanup(tmp)
            self.finished_err.emit('cancelled')
            return
        if rc != 0:
            err = ''
            try:
                err = (self._proc.stderr.read() or '')[:400]
            except Exception:
                pass
            self._cleanup(tmp)
            self.finished_err.emit(f'ffmpeg failed (rc={rc}): {err.strip()}')
            return
        try:
            os.replace(tmp, self._output)
        except OSError as exc:
            self._cleanup(tmp)
            self.finished_err.emit(str(exc))
            return
        self.progress.emit(1.0)
        self.finished_ok.emit(self._output)

    @staticmethod
    def _cleanup(path):
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass


# Keep references so QThreads aren't GC'd mid-run (mirrors bounce.py).
_active_threads: list[ProxyEncodeThread] = []


def encode_proxy_async(source_path, scale, encoder=None, duration=0.0,
                       on_progress=None, on_done=None, parent=None) -> ProxyEncodeThread:
    """Spawn a :class:`ProxyEncodeThread`. `encoder=None` → detect in-thread.
    `on_done(ok, path, error)`."""
    output = proxy_path_for(source_path, scale)
    thread = ProxyEncodeThread(source_path, scale, output, encoder,
                               duration=duration, parent=parent)

    def _ok(path):
        if on_done is not None:
            on_done(True, path, '')
        _drop(thread)

    def _err(msg):
        if on_done is not None:
            on_done(False, output, msg)
        _drop(thread)

    if on_progress is not None:
        thread.progress.connect(on_progress, Qt.QueuedConnection)
    thread.finished_ok.connect(_ok, Qt.QueuedConnection)
    thread.finished_err.connect(_err, Qt.QueuedConnection)
    _active_threads.append(thread)
    thread.start()
    return thread


def _drop(thread):
    if thread in _active_threads:
        _active_threads.remove(thread)
    thread.deleteLater()


def cancel_all_proxy_encodes():
    """Signal every in-flight encode to stop. Proxies are regenerable, so on
    app exit we cancel rather than block for minutes on a 4K/8K encode."""
    for thread in list(_active_threads):
        try:
            thread.cancel()
        except RuntimeError:
            pass


def wait_for_proxy_threads():
    """Block until in-flight encodes finish (they cancel fast). Call after
    `cancel_all_proxy_encodes()` on exit so the ffmpeg child is reaped."""
    for thread in list(_active_threads):
        try:
            thread.wait()
        except RuntimeError:
            pass
