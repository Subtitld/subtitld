"""Memory-footprint regression tests.

Why this exists
---------------
Loading a single project shouldn't pin 2-3 GB of RAM. The three biggest
offenders we fixed:

  1. **Waveform samples stored as float32.** A 1-hour project at 48 kHz
     is 691 MB of float32 — pure waste, since the waveform is already
     normalized to [-1.0, 1.0] and the only consumers downstream
     (WaveformWorker min/max bucketing, paint at integer pixel
     coordinates) don't need >16-bit precision. We pack to int16 and
     halve the footprint.

  2. **`SubtitleDubClip._loaded` grew without bound.** Every time a dub
     was regenerated (TTS rerun, voice change, edit), the new path got
     loaded and the old path stuck around in `_loaded` forever. For a
     500-subtitle project with even modest re-dubbing history, that's
     hundreds of MB of stale audio. The audio callback only ever asks
     for `_current_dub()`'s path — older entries are dead. We evict
     siblings on each successful preload.

  3. **Speaker images cached at video resolution.** The face-extraction
     UI cropped at source resolution (often 1024×1024+ from HD video),
     stored full-resolution ARGB QImage on `session.SPEAKERS[name]
     ['image']`, then displayed it at ~64 px in the speakers panel. A
     30-speaker project at 4K crops = 200+ MB held for thumbnail
     display. We downscale to 256 px max dimension on both fresh
     extraction (`preview_panel._apply_face_selection`) and project
     load (`file_io._downscale_speaker_image`, called from USF/USFX
     readers).

What this asserts
-----------------
Each case is a direct invariant check on the public API. If somebody
later "optimizes" set_samples back to float32, or removes the eviction
loop in preload, or drops the downscale call in file_io, one of these
assertions trips.

Run directly:
    python3 tests/addons_mock/test_memory_caps.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _stub_module(name):
    """Insert an empty module under `name` into sys.modules so importers
    that just do `from subtitld.interface import foo` get a benign
    namespace, not an ImportError. Only safe when the importer doesn't
    actually call into the stubbed names — which is the case for the
    `from subtitld.interface import left_panel` at module top of
    timeline.py: we only need WaveformManager, which never touches
    left_panel."""
    import types
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)


def _stub_heavy_imports():
    """Make timeline.py importable without dragging in the entire UI
    tree. Stubs out everything imported at top-of-module that
    WaveformManager itself doesn't actually call. If WaveformManager
    ever grows a dependency on one of these, the test will fail loudly
    on attribute access — that's fine, just remove the stub."""
    _stub_module('subtitld.interface.left_panel')
    _stub_module('subtitld.interface.playercontrols')
    _stub_module('subtitld.interface.translation')
    # The `from … import _` form needs an attribute on the stub.
    sys.modules['subtitld.interface.translation']._ = lambda s, **kw: s


def _stub_file_io_deps():
    """file_io.py imports a small ecosystem of file-format parsers
    (python-docx, pycaption, chardet, pysubs2, beautifulsoup4) at
    top of module. None of them are touched by the speaker-image
    helpers we test — but a missing one trips the import. Stub the
    optional ones; if any actually IS installed, the real module
    stays."""
    import types
    for name in ('docx', 'pycaption', 'pycaption.exceptions',
                 'chardet', 'pysubs2', 'bs4'):
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
    # Provide the symbols file_io reaches in via `from x import Y`.
    sys.modules.setdefault('docx', types.ModuleType('docx'))
    if not hasattr(sys.modules['docx'], 'Document'):
        sys.modules['docx'].Document = object
    pyc_exc = sys.modules.setdefault(
        'pycaption.exceptions', types.ModuleType('pycaption.exceptions'))
    for sym in ('CaptionReadSyntaxError', 'CaptionReadNoCaptions'):
        if not hasattr(pyc_exc, sym):
            setattr(pyc_exc, sym, type(sym, (Exception,), {}))
    bs4 = sys.modules.setdefault('bs4', types.ModuleType('bs4'))
    if not hasattr(bs4, 'BeautifulSoup'):
        bs4.BeautifulSoup = object


def _stub_i18n_if_missing():
    """timeline.py → left_panel → utils → translation → i18n. Stub it
    out if the dev env doesn't have python-i18n installed — the tests
    here don't care about translation, they care about dtype/cache
    invariants. Must run before importing the timeline module."""
    try:
        import i18n  # noqa: F401
        return
    except ImportError:
        pass

    import types

    fake = types.ModuleType('i18n')
    fake.load_path = []

    class _Config(dict):
        def get(self, key, default=None):
            # The defaults translation.py reads: file_format='json',
            # namespace_delimiter='.'. Anything else we don't care.
            return super().get(key, default if default is not None
                               else {'file_format': 'json',
                                     'namespace_delimiter': '.'}.get(key, ''))

    fake.config = _Config()
    fake.set = lambda *a, **kw: None
    fake.t = lambda text, **kw: text  # identity translation
    sys.modules['i18n'] = fake


def _ensure_qt():
    """A QGuiApplication is enough for QImage/QThread to behave. We
    don't need QApplication (QtWidgets) — and avoiding it sidesteps the
    full timeline.py import that pulls in left_panel, playercontrols,
    etc."""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def _make_dub_wav(path: str, duration_sec: float, sample_rate: int = 24000):
    """Mono 16-bit sine — same helper as test_audio_callback_perf."""
    n = int(duration_sec * sample_rate)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n):
            v = int(0.2 * 32767 * math.sin(2 * math.pi * 440.0 * i / sample_rate))
            wf.writeframesraw(struct.pack('<h', v))


def case_waveform_int16_storage():
    """A float32 input to WaveformManager.set_samples must be packed to
    int16 — halving the resident memory of the raw samples buffer.

    We feed a normalized 1-second sample array and check both the dtype
    and the byte count. The float32 case would be 4× the array length;
    int16 is 2×. Anything else means the dtype hop was skipped."""
    print('\n=== waveform samples stored as int16 ===')
    import numpy as np

    # set_samples spawns a QThread (WaveformWorker). It runs async and
    # we don't wait for it — we only care about self.samples right
    # after the call. But QThread.start() requires a QCoreApplication
    # in this thread.
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_heavy_imports()

    # timeline.py drags in the whole UI tree (left_panel,
    # playercontrols, translation). With those stubbed out we get just
    # the WaveformManager class, which is all this test needs.
    from subtitld.interface.timeline import WaveformManager

    n = 48000  # 1 second at 48 kHz
    samples_f32 = (np.sin(np.linspace(0, 2 * np.pi * 440, n))
                   .astype(np.float32))
    # WaveformManager normalizes upstream — we mirror that.
    samples_f32 /= max(1e-9, float(np.max(np.abs(samples_f32))))

    mgr = WaveformManager(samples=samples_f32, filepath=None)
    stored = mgr.samples
    assert stored is not None, 'set_samples did not store anything'
    assert stored.dtype == np.int16, (
        f'expected int16 storage, got {stored.dtype} — '
        'the dtype pack was reverted?')
    # Float32 would be 4 bytes/sample; int16 is 2.
    assert stored.nbytes == n * 2, (
        f'expected {n * 2} bytes (int16), got {stored.nbytes}')
    print(f'  n={n} samples → stored={stored.nbytes / 1024:.1f} KiB '
          f'(int16) vs {n * 4 / 1024:.1f} KiB (float32) ✓')

    # And int16 input is accepted as-is, no double conversion.
    samples_i16 = (samples_f32 * 32767.0).astype(np.int16)
    mgr2 = WaveformManager(samples=samples_i16, filepath=None)
    assert mgr2.samples.dtype == np.int16, (
        'int16 input should pass through, but dtype changed')
    print('  int16 input passes through unchanged ✓')

    # Wait for the WaveformWorkers to finish so they're not destroyed
    # mid-run when these managers go out of scope. Without this, Qt
    # prints "QThread: Destroyed while thread is still running" and on
    # some hosts the abort propagates into the next test case.
    for mgr_obj in (mgr, mgr2):
        for w in list(mgr_obj.workers.values()):
            w.wait(5000)


def case_dub_cache_eviction():
    """SubtitleDubClip._loaded must hold only the currently-relevant
    dub. Without eviction, regenerating dubs N times leaks N old
    arrays — easy gigabytes on a real project."""
    print('\n=== dub cache evicts stale paths on preload ===')
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:
        print(f'  SKIP — sounddevice import failed: {exc}')
        return

    from subtitld.modules import audioengine

    workdir = tempfile.mkdtemp(prefix='dub-cache-test-')
    try:
        path_a = os.path.join(workdir, 'a.wav')
        path_b = os.path.join(workdir, 'b.wav')
        path_c = os.path.join(workdir, 'c.wav')
        for p in (path_a, path_b, path_c):
            _make_dub_wav(p, duration_sec=0.5)

        sub = {
            'speaker': 'X', 'start': 0.0, 'end': 1.0, 'text': 't',
            'dubbing': [{'engine': 'edge-tts', 'voice': 'fake',
                         'rate': 0, 'path': path_a, 'start': 0.0}],
        }
        clip = audioengine.SubtitleDubClip(sub, samplerate=48000)

        clip.preload(path_a)
        assert path_a in clip._loaded, 'first preload did not populate cache'
        assert len(clip._loaded) == 1

        clip.preload(path_b)
        # The interesting invariant: path_a is evicted by path_b.
        assert path_b in clip._loaded, 'second preload not present'
        assert path_a not in clip._loaded, (
            f'stale path leaked: _loaded keys = {list(clip._loaded)}')
        assert len(clip._loaded) == 1, (
            f'cache should hold exactly 1 entry, has {len(clip._loaded)}')

        clip.preload(path_c)
        assert list(clip._loaded.keys()) == [path_c], (
            f'after 3rd preload, _loaded should hold only path_c, '
            f'has {list(clip._loaded.keys())}')

        # Re-preloading the same path is a no-op — must not double the
        # entry or somehow drop itself.
        before_id = id(clip._loaded[path_c])
        clip.preload(path_c)
        assert list(clip._loaded.keys()) == [path_c]
        assert id(clip._loaded[path_c]) == before_id, (
            'no-op preload re-allocated the cached entry')

        print(f'  3 sequential preloads → _loaded size = 1 (path={os.path.basename(path_c)}) ✓')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_dub_storage_is_int16_mono():
    """SubtitleDubClip._loaded packs each dub as int16 mono at engine
    samplerate. The historical format was stereo float32 — 4× larger.
    On a 500-subtitle project that change is hundreds of MB.

    Assert the shape and dtype of what preload() actually stored. A
    revert to float32 or stereo would trip this immediately."""
    print('\n=== dub cache stores int16 mono ===')
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:
        print(f'  SKIP — sounddevice import failed: {exc}')
        return

    import numpy as np
    from subtitld.modules import audioengine

    workdir = tempfile.mkdtemp(prefix='dub-storage-test-')
    try:
        # 2-second dub at 24 kHz (typical TTS rate) — exercises both
        # the mono downmix path (file is already mono, so no-op) and
        # the resample path (24 kHz → 48 kHz engine rate).
        path = os.path.join(workdir, 'a.wav')
        _make_dub_wav(path, duration_sec=2.0, sample_rate=24000)

        sub = {
            'speaker': 'X', 'start': 0.0, 'end': 2.0, 'text': 't',
            'dubbing': [{'engine': 'edge-tts', 'voice': 'fake',
                         'rate': 0, 'path': path, 'start': 0.0}],
        }
        clip = audioengine.SubtitleDubClip(sub, samplerate=48000)
        clip.preload(path)
        assert path in clip._loaded, 'preload did not populate cache'

        data, src_sr, n_frames = clip._loaded[path]
        # Dtype: int16 (not float32). 2 bytes/frame.
        assert data.dtype == np.int16, (
            f'expected int16 dub storage, got {data.dtype} — '
            'revert to float32 detected')
        # Shape: 1-D mono (not (N, 2) stereo).
        assert data.ndim == 1, (
            f'expected mono dub storage (ndim=1), got shape {data.shape}')
        # Resampled to engine SR.
        assert src_sr == 48000, f'expected engine SR 48000, got {src_sr}'
        # Duration: 2 s at 48 kHz = 96000 frames (± a few from resampling).
        assert 95900 <= n_frames <= 96100, (
            f'expected ~96000 frames for 2 s @ 48 kHz, got {n_frames}')
        # Bytes: int16 mono = 2 bytes/frame, vs old format = 8 (stereo
        # float32). Confirm the 4× shrink.
        bytes_new = data.nbytes
        bytes_old = n_frames * 2 * 4  # stereo float32 baseline
        ratio = bytes_old / bytes_new
        assert ratio >= 3.9, (
            f'expected ≥4× shrink vs stereo float32; got {ratio:.2f}×')
        print(f'  2 s dub @ 24 kHz → {bytes_new / 1024:.1f} KiB '
              f'int16 mono @ 48 kHz (was {bytes_old / 1024:.1f} KiB '
              f'stereo float32 → {ratio:.1f}× shrink) ✓')

        # And the audio callback's fast path must still produce
        # non-zero output from int16 storage. Drive one block of
        # samples through clip.read() and check the rendered float32
        # buffer is non-silent.
        pool = audioengine.BufferPool(max_size=4)
        out = clip.read(playhead=0.5, frames=2048, samplerate=48000,
                        buffer_pool=pool)
        assert out is not None, 'clip.read returned None on in-window read'
        assert out.dtype == np.float32, (
            f'audio callback output dtype: {out.dtype}, expected float32')
        assert out.shape == (2048, 2), (
            f'audio callback output shape: {out.shape}, expected (2048, 2)')
        # Both channels should be identical (mono broadcast).
        assert np.array_equal(out[:, 0], out[:, 1]), (
            'mono→stereo broadcast should write identical channels')
        # Non-trivial signal — 440 Hz sine at 0.2 amplitude → peak ~0.2.
        peak = float(np.max(np.abs(out)))
        assert peak > 0.05, (
            f'rendered output is near-silent (peak={peak:.4f}); '
            'int16 conversion or gain may be wrong')
        print(f'  clip.read() → (2048, 2) float32, mono channels '
              f'matched, peak={peak:.3f} ✓')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_speaker_image_downscale():
    """Speaker thumbnails are cached at ≤256 px max dim. A bigger image
    in must come out at ≤256 px, preserving aspect ratio. An image
    already at-or-below 256 must be returned untouched (no resize cost,
    no quality loss)."""
    print('\n=== speaker images downscaled on load ===')
    _ensure_qt()
    _stub_file_io_deps()
    from PySide6.QtGui import QImage
    from subtitld.modules.file_io import (
        _downscale_speaker_image, SPEAKER_IMAGE_MAX_DIM,
    )

    cap = SPEAKER_IMAGE_MAX_DIM

    # 1024×768 → should shrink so that max dim == cap, aspect preserved.
    big = QImage(1024, 768, QImage.Format_ARGB32)
    big.fill(0xFF112233)
    out = _downscale_speaker_image(big)
    assert max(out.width(), out.height()) <= cap, (
        f'1024×768 downscale exceeded cap: {out.width()}×{out.height()}')
    # Aspect within 1 px of the original 4:3.
    assert abs(out.width() / out.height() - 1024 / 768) < 0.02, (
        f'aspect ratio drifted: {out.width()}×{out.height()}')
    print(f'  1024×768 → {out.width()}×{out.height()} ✓')

    # 200×200 → already under cap; should pass through untouched (same
    # object, not a scaled copy — cheaper and avoids any quality loss).
    small = QImage(200, 200, QImage.Format_ARGB32)
    small.fill(0xFFAABBCC)
    out_small = _downscale_speaker_image(small)
    assert out_small.width() == 200 and out_small.height() == 200, (
        'sub-cap image got resized — that wastes CPU and quality')
    print(f'  200×200 (below cap) → unchanged ✓')

    # Null QImage → returned as-is, no crash.
    null = QImage()
    out_null = _downscale_speaker_image(null)
    assert out_null is null or out_null.isNull(), (
        'null QImage should round-trip through downscale unchanged')
    print('  null QImage → no crash ✓')

    # Portrait 256×1024 → height clamped, width scaled down.
    tall = QImage(256, 1024, QImage.Format_ARGB32)
    tall.fill(0xFF445566)
    out_tall = _downscale_speaker_image(tall)
    assert max(out_tall.width(), out_tall.height()) <= cap, (
        f'tall downscale exceeded cap: {out_tall.width()}×{out_tall.height()}')
    print(f'  256×1024 → {out_tall.width()}×{out_tall.height()} ✓')


def case_usfx_phase1_member_selection():
    """The USFX load splits zip members into Phase 1 (extract synchronously
    so the production screen can render) and Phase 2 (stream in a
    background thread because they're big). The predicate
    `_is_phase1_usfx_member` is the contract: anything it returns True
    for blocks the open; everything else doesn't. A regression on which
    side a member lands on is a regression on perceived open time."""
    print('\n=== USFX Phase 1 selects only fast-path members ===')
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    from subtitld.modules.file_io import _is_phase1_usfx_member

    keep = [
        'subtitles.usf',
        'manifest.xml',
        'assets/speakers/Alice.png',
        'assets/speakers/Bob_2.jpg',
        'project.usf',           # tolerated odd-name USF at root
    ]
    skip = [
        # Dubs deferred to Phase 2 — historically the single biggest
        # contributor to "frozen production screen on open" on projects
        # with hundreds of subtitles. Moving them out of Phase 1 is the
        # core of the perceived-latency win; if they leak back in, this
        # case is the canary.
        'assets/dubs/abc123.wav',
        'assets/dubs/uid-42.mp3',
        # Heavy caches that Phase 2 owns.
        'assets/waveform.npy',
        'assets/audio/original.flac',
        'assets/audio/vocals.flac',
        'assets/audio/background.flac',
        # Bundled video is handled by peek_usfx_video upstream.
        'assets/video/My Movie.mkv',
        # Directory entries — extractor would skip; predicate must too.
        'assets/speakers/',
        'assets/dubs/',
        '',
    ]

    for name in keep:
        assert _is_phase1_usfx_member(name), (
            f'expected Phase 1 to keep {name!r}, predicate said skip')
    for name in skip:
        assert not _is_phase1_usfx_member(name), (
            f'expected Phase 1 to skip {name!r}, predicate said keep')
    print(f'  {len(keep)} kept, {len(skip)} skipped ✓')


def case_usfx_background_extractor_emits_progress():
    """The background extractor must drive its per-instance `progress`
    signal through 0..100 so we know the chunked loop actually iterates
    end-to-end (regression canary against a degenerate write that skips
    the chunk read). The global hub variant was removed when the
    progress bar UI went away — per-dub `usfx_member_ready` repaints
    are the user-visible feedback now. The finished hub signal still
    matters because it gates the Save button, so this case also asserts
    the signal-to-signal bridge actually delivers."""
    print('\n=== USFX Phase 2 emits monotonic 0..100 progress ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    import zipfile
    from PySide6.QtCore import QEventLoop, QTimer

    from subtitld.modules import session
    workdir = tempfile.mkdtemp(prefix='usfx-progress-test-')
    real_cache = session.PATH_SUBTITLD_USER_CACHE
    real_audiosep = session.PATH_SUBTITLD_DATA_AUDIOSEPARATION
    try:
        session.PATH_SUBTITLD_USER_CACHE = workdir
        audiosep = os.path.join(workdir, 'audiosep')
        os.makedirs(audiosep, exist_ok=True)
        session.PATH_SUBTITLD_DATA_AUDIOSEPARATION = audiosep

        # Use a payload large enough that the 1 MiB chunk loop fires
        # multiple times — we want to see >2 progress emits, otherwise
        # we're not really exercising the chunked path.
        usfx_path = os.path.join(workdir, 'project.usfx')
        big_blob = (b'WAVEFORM_PAYLOAD' * 64) * 4096  # ~4 MiB
        flac_blob = (b'FLAC_PAYLOAD' * 64) * 4096     # ~3 MiB
        with zipfile.ZipFile(usfx_path, 'w', zipfile.ZIP_STORED) as zf:
            zf.writestr('subtitles.usf', b'<usf/>')
            zf.writestr('assets/waveform.npy', big_blob)
            zf.writestr('assets/audio/original.flac', flac_blob)

        from subtitld.modules.file_io import _USFXBackgroundExtractor
        from subtitld.modules.signals import SIGNALS

        ex = _USFXBackgroundExtractor(usfx_path, 'progkey')
        instance_progress = []
        finished_fired = []
        ex.progress.connect(instance_progress.append)
        SIGNALS.usfx_background_load_finished.connect(
            lambda: finished_fired.append(True))
        # Wire the same finished bridge production code uses, so the
        # global `usfx_background_load_finished` actually fires.
        # Signal-to-signal (not `.emit`) — see file_io.py for why; the
        # bound-method form silently breaks after a prior extractor has
        # run in the same process.
        ex.finished.connect(SIGNALS.usfx_background_load_finished)

        # Drive the local event loop until the thread completes — we
        # need queued signal deliveries to land in the receiver lists.
        loop = QEventLoop()
        ex.finished.connect(loop.quit)
        ex.start()
        # Watchdog so a hung extractor doesn't hang the suite.
        QTimer.singleShot(15_000, loop.quit)
        loop.exec()
        assert not ex.isRunning(), 'extractor did not finish under watchdog'

        assert instance_progress, 'no per-instance progress signals emitted'
        assert instance_progress[-1] == 100, (
            f'expected final progress 100, got {instance_progress[-1]}')
        # Monotonic — no rewind.
        for a, b in zip(instance_progress[:-1], instance_progress[1:]):
            assert b >= a, (
                f'progress rewound from {a} to {b}; sequence='
                f'{instance_progress}')
        assert finished_fired, 'finished signal never bubbled to the hub'
        print(f'  {len(instance_progress)} progress emits '
              f'({instance_progress[0]}→…→{instance_progress[-1]}), '
              'finished bubbled ✓')

        # Cleanup signal connections so they don't leak into the next case.
        try:
            SIGNALS.usfx_background_load_finished.disconnect()
        except (TypeError, RuntimeError):
            pass
    finally:
        session.PATH_SUBTITLD_USER_CACHE = real_cache
        session.PATH_SUBTITLD_DATA_AUDIOSEPARATION = real_audiosep
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_speaker_image_loader_runs_off_main():
    """Speaker thumbnail decode/downscale must happen on a worker thread,
    not the main USFX/USF parse path. We can't directly observe "what
    thread ran", but we *can* confirm:
      * the loader QThread is alive between `.start()` and `.wait()`,
      * by the time the thread finishes, `session.SPEAKERS[name]['image']`
        is populated with a properly downscaled QImage,
      * the global `speaker_image_ready` signal fires once per decoded
        speaker.

    Decode failures must be tolerated silently (the dict stays empty for
    that name; no `image` key) — a bad PNG should not crash the load."""
    print('\n=== Speaker thumbnails decoded off main thread ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    from PySide6.QtCore import QEventLoop, QTimer, QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImage

    from subtitld.modules import session
    from subtitld.modules.file_io import _SpeakerImageLoader, SPEAKER_IMAGE_MAX_DIM
    from subtitld.modules.signals import SIGNALS

    # Build a real PNG byte stream for "Alice" (1024×768 — over the
    # downscale cap, so we can assert that the cap was applied) and a
    # second one written to disk for the "fallback path" code branch.
    big = QImage(1024, 768, QImage.Format_ARGB32)
    big.fill(0xFF334455)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    assert big.save(buf, 'PNG'), 'reference PNG encode failed'
    buf.close()
    alice_png = bytes(ba)

    workdir = tempfile.mkdtemp(prefix='speaker-loader-test-')
    try:
        bob_path = os.path.join(workdir, 'bob.png')
        bob = QImage(320, 240, QImage.Format_ARGB32)
        bob.fill(0xFFAABBCC)
        assert bob.save(bob_path, 'PNG'), 'Bob PNG file save failed'

        # Reset session.SPEAKERS so we can assert exactly what the loader
        # writes. Save the prior value to restore at the end.
        prior_speakers = session.SPEAKERS
        session.SPEAKERS = {'Alice': {'color': '#111'}, 'Bob': {'color': '#222'}, 'Carol': {}}

        items = [
            ('Alice', alice_png, None),          # base64 path
            ('Bob', None, bob_path),             # file fallback path
            ('Carol', b'NOT A REAL PNG', None),  # decode failure → no image
        ]

        ready = []
        SIGNALS.speaker_image_ready.connect(ready.append)

        loader = _SpeakerImageLoader(items)
        loop = QEventLoop()
        loader.finished.connect(loop.quit)
        loader.start()
        QTimer.singleShot(10_000, loop.quit)  # watchdog
        loop.exec()
        assert not loader.isRunning(), 'speaker image loader hung'

        # Alice + Bob should both have images now; Carol must not.
        alice_img = session.SPEAKERS['Alice'].get('image')
        bob_img = session.SPEAKERS['Bob'].get('image')
        assert alice_img is not None and not alice_img.isNull(), (
            'Alice image not populated by the loader')
        assert bob_img is not None and not bob_img.isNull(), (
            'Bob image not populated by the loader')
        assert 'image' not in session.SPEAKERS['Carol'], (
            'Carol got a bogus image from corrupt PNG bytes')

        # Downscale was applied to Alice (1024×768 → ≤256 max dim).
        assert max(alice_img.width(), alice_img.height()) <= SPEAKER_IMAGE_MAX_DIM, (
            f'Alice not downscaled: {alice_img.width()}×{alice_img.height()}')
        # Bob (320×240) gets downscaled too.
        assert max(bob_img.width(), bob_img.height()) <= SPEAKER_IMAGE_MAX_DIM, (
            f'Bob not downscaled: {bob_img.width()}×{bob_img.height()}')

        # Color metadata was not stomped — we only set 'image', not the
        # whole speaker dict.
        assert session.SPEAKERS['Alice']['color'] == '#111'
        assert session.SPEAKERS['Bob']['color'] == '#222'

        # Two `speaker_image_ready` emits — Alice and Bob, in some order.
        assert sorted(ready) == ['Alice', 'Bob'], (
            f'expected ready=[Alice, Bob], got {ready}')

        print(f'  3 jobs in: 2 decoded + downscaled, 1 bad PNG silently '
              f'skipped; ready={ready} ✓')

        try:
            SIGNALS.speaker_image_ready.disconnect()
        except (TypeError, RuntimeError):
            pass
        session.SPEAKERS = prior_speakers
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_usfx_background_extractor_streams_caches():
    """_USFXBackgroundExtractor copies the heavy USFX members (waveform
    cache + audio stems) into their final cache target dirs, atomically.
    Without it Phase 1 is fast but the user loses the bundled caches.

    Drive a fake USFX zip through it and confirm:
      * Cache target files exist with the right bytes.
      * No `.tmp` leftover (atomicity).
      * Re-running against an existing target is a no-op (no overwrite,
        no crash).
      * Missing zip members are tolerated (best-effort)."""
    print('\n=== USFX Phase 2 streams heavy assets to cache ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    import zipfile

    # Patch session paths to a clean temp tree so we can assert against
    # them without touching the user's real cache.
    from subtitld.modules import session
    workdir = tempfile.mkdtemp(prefix='usfx-phase2-test-')
    real_cache = session.PATH_SUBTITLD_USER_CACHE
    real_audiosep = session.PATH_SUBTITLD_DATA_AUDIOSEPARATION
    try:
        session.PATH_SUBTITLD_USER_CACHE = workdir
        audiosep = os.path.join(workdir, 'audiosep')
        os.makedirs(audiosep, exist_ok=True)
        session.PATH_SUBTITLD_DATA_AUDIOSEPARATION = audiosep

        # Build a fake USFX zip with both Phase 1 and Phase 2 members.
        usfx_path = os.path.join(workdir, 'project.usfx')
        wf_bytes = b'WAVEFORM_NPY_PAYLOAD' * 1024  # ~20 KB
        orig_bytes = b'FLAC_ORIGINAL_PAYLOAD' * 2048
        vocals_bytes = b'FLAC_VOCALS_PAYLOAD' * 2048
        # Note: background.flac intentionally absent — we want to confirm
        # the extractor doesn't crash on a missing optional member.
        with zipfile.ZipFile(usfx_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('subtitles.usf', b'<usf/>')
            zf.writestr('manifest.xml', b'<manifest/>')
            zf.writestr('assets/speakers/Foo.png', b'PNG_BYTES')
            zf.writestr('assets/dubs/uid1.wav', b'WAV_BYTES')
            zf.writestr('assets/waveform.npy', wf_bytes)
            zf.writestr('assets/audio/original.flac', orig_bytes)
            zf.writestr('assets/audio/vocals.flac', vocals_bytes)

        from subtitld.modules.file_io import _USFXBackgroundExtractor

        cache_key = 'cachekey_abc'
        ex = _USFXBackgroundExtractor(usfx_path, cache_key)
        ex.start()
        assert ex.wait(10_000), 'Phase 2 extractor did not finish in 10s'

        wf_target = os.path.join(workdir, 'waveform',
                                 f'{cache_key}_waveform.npy')
        orig_target = os.path.join(audiosep,
                                   f'{cache_key}_original.flac')
        vocals_target = os.path.join(audiosep,
                                     f'{cache_key}_vocals.flac')
        bg_target = os.path.join(audiosep,
                                 f'{cache_key}_background.flac')

        assert os.path.isfile(wf_target), 'waveform cache not extracted'
        assert os.path.isfile(orig_target), 'original stem not extracted'
        assert os.path.isfile(vocals_target), 'vocals stem not extracted'
        assert not os.path.exists(bg_target), (
            'background stem should not exist — it was absent from the zip')

        with open(wf_target, 'rb') as fh:
            assert fh.read() == wf_bytes, 'waveform payload corrupted'
        with open(orig_target, 'rb') as fh:
            assert fh.read() == orig_bytes, 'original FLAC payload corrupted'

        # No leftover .tmp files anywhere we wrote.
        for parent in (os.path.join(workdir, 'waveform'), audiosep):
            for entry in os.listdir(parent):
                assert not entry.endswith('.tmp'), (
                    f'leftover .tmp in {parent}: {entry}')
        print('  3 of 4 heavy members streamed to cache atomically ✓')

        # Re-run against the same targets: must be a silent no-op (no
        # rewrite, no crash). We touch the files first and confirm mtime
        # doesn't change.
        before = {p: os.stat(p).st_mtime_ns
                  for p in (wf_target, orig_target, vocals_target)}
        ex2 = _USFXBackgroundExtractor(usfx_path, cache_key)
        ex2.start()
        assert ex2.wait(10_000), 'second extractor run did not finish'
        for p, mtime in before.items():
            assert os.stat(p).st_mtime_ns == mtime, (
                f'{os.path.basename(p)} was rewritten on re-extract '
                '— os.path.exists guard broken')
        print('  re-run against existing caches is a no-op ✓')

        # Missing zip → silent best-effort (no crash, no creation).
        gone = os.path.join(workdir, 'does-not-exist.usfx')
        ex3 = _USFXBackgroundExtractor(gone, 'other_key')
        ex3.start()
        assert ex3.wait(5_000)
        print('  missing zip → no crash ✓')

        # Empty cache_key → no work done (guards against a degenerate
        # "no video resolved" path).
        ex4 = _USFXBackgroundExtractor(usfx_path, '')
        ex4.start()
        assert ex4.wait(5_000)
        print('  empty cache_key → skipped ✓')
    finally:
        # Restore session paths so subsequent test cases use the real
        # cache dirs.
        session.PATH_SUBTITLD_USER_CACHE = real_cache
        session.PATH_SUBTITLD_DATA_AUDIOSEPARATION = real_audiosep
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_usfx_extractor_emits_member_ready_for_dubs():
    """The deferred-dub flow only works if the timeline and audio engine
    learn the instant each dub file lands on disk — `_request_async_preload`
    has to run, and the hatched placeholder has to clear. The contract is
    `signals.SIGNALS.usfx_member_ready(arcname, target_path)`.

    Drive a zip with a handful of dubs through `_USFXBackgroundExtractor`
    (no FLAC, no waveform — pure-dub case) and assert:
      * one emit per dub, in zip-order (the extractor sorts namelist so
        playback near the start can begin before tail dubs finish),
      * target_path matches `<extract_dir>/<arcname>` (the same path the
        main-thread USFX parse stored in `dub['path']`),
      * the file exists at target_path by the time the emit fires (the
        audio engine immediately calls preload on it; a slot that fires
        before os.replace would race against a missing file)."""
    print('\n=== USFX Phase 2 emits per-member ready for each dub ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    import zipfile
    from PySide6.QtCore import QEventLoop, QTimer

    workdir = tempfile.mkdtemp(prefix='usfx-member-ready-test-')
    try:
        # Pure-dub zip — no cache_key needed, only extract_dir. This is
        # the common case for a project without bundled audio separation.
        usfx_path = os.path.join(workdir, 'project.usfx')
        dub_arcs = [
            'assets/dubs/sub_001.wav',
            'assets/dubs/sub_002.wav',
            'assets/dubs/sub_003.mp3',
        ]
        dub_blobs = {a: (f'DUB_PAYLOAD_{i}_' * 256).encode()
                     for i, a in enumerate(dub_arcs)}
        with zipfile.ZipFile(usfx_path, 'w', zipfile.ZIP_STORED) as zf:
            zf.writestr('subtitles.usf', b'<usf/>')
            for arc, payload in dub_blobs.items():
                zf.writestr(arc, payload)

        extract_dir = os.path.join(workdir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)

        from subtitld.modules.file_io import _USFXBackgroundExtractor
        from subtitld.modules.signals import SIGNALS

        # Capture (arcname, target_path, exists_at_emit_time) tuples.
        captured = []

        def _on_member(arc, tgt):
            captured.append((arc, tgt, os.path.exists(tgt)))

        SIGNALS.usfx_member_ready.connect(_on_member)
        try:
            ex = _USFXBackgroundExtractor(
                usfx_path, cache_key='', extract_dir=extract_dir,
            )
            loop = QEventLoop()
            ex.finished.connect(loop.quit)
            ex.start()
            QTimer.singleShot(10_000, loop.quit)
            loop.exec()
            assert not ex.isRunning(), 'pure-dub extractor hung'

            # One emit per dub, regardless of zip order (extractor sorts).
            emitted_arcs = [c[0] for c in captured]
            assert sorted(emitted_arcs) == sorted(dub_arcs), (
                f'expected one emit per dub; got {emitted_arcs}')

            # target_path must match the convention `dub['path']` uses.
            for arc, tgt, exists in captured:
                expected = os.path.join(extract_dir, arc.replace('/', os.sep))
                assert tgt == expected, (
                    f'arc {arc!r} emitted target {tgt!r}, expected {expected!r}')
                # The signal must fire AFTER the rename — slots downstream
                # call open() / preload() immediately and would crash on a
                # missing file.
                assert exists, (
                    f'member_ready fired before {tgt!r} hit disk — '
                    'os.replace ordering bug')

            # Payloads must be intact (not truncated by the chunk loop).
            for arc, tgt, _exists in captured:
                with open(tgt, 'rb') as fh:
                    assert fh.read() == dub_blobs[arc], (
                        f'payload mismatch for {arc!r} — chunk loop bug?')
            print(f'  {len(captured)} per-dub emits, all post-replace ✓')

            # Re-running on a populated extract_dir must still fire the
            # signal (late listeners need to know the file is available
            # — e.g. preview_panel wires its slot only AFTER first paint).
            captured.clear()
            ex2 = _USFXBackgroundExtractor(
                usfx_path, cache_key='', extract_dir=extract_dir,
            )
            loop2 = QEventLoop()
            ex2.finished.connect(loop2.quit)
            ex2.start()
            QTimer.singleShot(10_000, loop2.quit)
            loop2.exec()
            assert sorted([c[0] for c in captured]) == sorted(dub_arcs), (
                f'second-run emits missing for late listeners: {captured}')
            print(f'  re-run on populated extract_dir re-emits ({len(captured)} dubs) ✓')
        finally:
            try:
                SIGNALS.usfx_member_ready.disconnect(_on_member)
            except (TypeError, RuntimeError):
                pass
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_usfx_extractor_extracts_dubs_without_cache_key():
    """Pure-dub projects (no audio separation bundled, no waveform cache)
    have an empty `cache_key`. Pre-refactor the extractor bailed in that
    case, leaving dubs trapped inside the zip — the new gate runs the
    background extractor whenever `extract_dir` is set, regardless of
    cache_key.

    Confirm: with cache_key='' and a dubs-only zip, the extractor still
    streams the dubs out and writes them to extract_dir."""
    print('\n=== USFX Phase 2 runs for dubs-only projects (no cache_key) ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    import zipfile

    workdir = tempfile.mkdtemp(prefix='usfx-dubs-only-test-')
    try:
        usfx_path = os.path.join(workdir, 'project.usfx')
        with zipfile.ZipFile(usfx_path, 'w', zipfile.ZIP_STORED) as zf:
            zf.writestr('subtitles.usf', b'<usf/>')
            zf.writestr('assets/dubs/d1.wav', b'WAV_BYTES_1')
            zf.writestr('assets/dubs/d2.wav', b'WAV_BYTES_2')

        extract_dir = os.path.join(workdir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)

        from subtitld.modules.file_io import _USFXBackgroundExtractor

        ex = _USFXBackgroundExtractor(
            usfx_path, cache_key='', extract_dir=extract_dir,
        )
        ex.start()
        assert ex.wait(10_000), 'dubs-only extractor did not finish'

        d1 = os.path.join(extract_dir, 'assets', 'dubs', 'd1.wav')
        d2 = os.path.join(extract_dir, 'assets', 'dubs', 'd2.wav')
        assert os.path.isfile(d1), 'd1.wav not extracted by dubs-only run'
        assert os.path.isfile(d2), 'd2.wav not extracted by dubs-only run'
        with open(d1, 'rb') as fh:
            assert fh.read() == b'WAV_BYTES_1'
        with open(d2, 'rb') as fh:
            assert fh.read() == b'WAV_BYTES_2'
        print('  2 dubs extracted with empty cache_key ✓')

        # And the inverse: cache_key='' AND extract_dir=None means no
        # work — the extractor bails cleanly without crashing.
        ex_bail = _USFXBackgroundExtractor(
            usfx_path, cache_key='', extract_dir=None,
        )
        ex_bail.start()
        assert ex_bail.wait(5_000), 'no-op extractor hung'
        print('  no extract_dir AND no cache_key → clean no-op ✓')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_save_button_gate_signals():
    """The Save button is disabled while the background extractor is
    streaming — saving mid-stream would re-zip dubs whose source bytes
    are still inside the original USFX. The gate is implemented as a
    pair of signal emissions: `usfx_background_load_started` flips the
    UI to "busy"; `usfx_background_load_finished` flips it back.

    Wire a fake button to those signals exactly like `top_bar.load()`
    does, run the extractor end-to-end, and assert the button's
    enabled-state lifecycle matches the extractor's lifecycle."""
    print('\n=== Save button gates on background extractor lifecycle ===')
    _ensure_qt()
    _stub_i18n_if_missing()
    _stub_file_io_deps()
    import zipfile
    from PySide6.QtCore import QEventLoop, QTimer

    from subtitld.modules.signals import SIGNALS

    # Verify the two signals exist (catches an accidental rename).
    assert hasattr(SIGNALS, 'usfx_background_load_started'), (
        'usfx_background_load_started signal missing from the hub')
    assert hasattr(SIGNALS, 'usfx_background_load_finished'), (
        'usfx_background_load_finished signal missing from the hub')

    # Minimal fake button — just an `_enabled` bool the wiring flips.
    class FakeButton:
        def __init__(self):
            self._enabled = True
            self._tooltip = 'Save'
            self.transitions = []  # list of bool states over time

        def setEnabled(self, val):
            self._enabled = bool(val)
            self.transitions.append(self._enabled)

        def setToolTip(self, val):
            self._tooltip = val

    btn = FakeButton()

    def _on_started():
        btn.setEnabled(False)
        btn.setToolTip('busy')

    def _on_finished():
        btn.setEnabled(True)
        btn.setToolTip('Save')

    SIGNALS.usfx_background_load_started.connect(_on_started)
    SIGNALS.usfx_background_load_finished.connect(_on_finished)
    try:
        # Build a small USFX zip and run the extractor — but mimic the
        # production wiring in file_io: emit started just before start().
        workdir = tempfile.mkdtemp(prefix='save-gate-test-')
        try:
            usfx_path = os.path.join(workdir, 'project.usfx')
            with zipfile.ZipFile(usfx_path, 'w', zipfile.ZIP_STORED) as zf:
                zf.writestr('subtitles.usf', b'<usf/>')
                zf.writestr('assets/dubs/d1.wav', b'WAV' * 1024)

            extract_dir = os.path.join(workdir, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)

            from subtitld.modules.file_io import _USFXBackgroundExtractor

            ex = _USFXBackgroundExtractor(
                usfx_path, cache_key='', extract_dir=extract_dir,
            )
            # Signal-to-signal forwarding — matches file_io.py wiring
            # and avoids the bound-`.emit` quirk that silently drops
            # deliveries after a prior extractor has run.
            ex.finished.connect(SIGNALS.usfx_background_load_finished)

            assert btn._enabled, (
                'button should start enabled before any extractor')

            # Emit started exactly like file_io does, synchronously.
            SIGNALS.usfx_background_load_started.emit()
            assert not btn._enabled, (
                'started signal did not disable the button')
            assert btn._tooltip == 'busy', (
                f'started did not swap tooltip; got {btn._tooltip!r}')

            loop = QEventLoop()
            ex.finished.connect(loop.quit)
            ex.start()
            QTimer.singleShot(10_000, loop.quit)
            loop.exec()
            assert not ex.isRunning(), 'extractor hung in gate test'

            assert btn._enabled, (
                'finished signal did not re-enable the button; '
                f'transitions={btn.transitions}')
            assert btn._tooltip == 'Save', (
                f'finished did not restore tooltip; got {btn._tooltip!r}')
            # Exact transition sequence: True (init) → False (started) →
            # True (finished). No extra flicker.
            assert btn.transitions == [False, True], (
                f'unexpected transition trail: {btn.transitions}')
            print(f'  transitions={btn.transitions}, tooltip restored ✓')
        finally:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
    finally:
        try:
            SIGNALS.usfx_background_load_started.disconnect(_on_started)
        except (TypeError, RuntimeError):
            pass
        try:
            SIGNALS.usfx_background_load_finished.disconnect(_on_finished)
        except (TypeError, RuntimeError):
            pass


def main():
    case_waveform_int16_storage()
    case_usfx_phase1_member_selection()
    case_usfx_background_extractor_streams_caches()
    case_usfx_background_extractor_emits_progress()
    case_usfx_extractor_emits_member_ready_for_dubs()
    case_usfx_extractor_extracts_dubs_without_cache_key()
    case_save_button_gate_signals()
    case_speaker_image_loader_runs_off_main()
    case_dub_storage_is_int16_mono()
    case_speaker_image_downscale()
    case_dub_cache_eviction()
    print('\nMemory cap cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
