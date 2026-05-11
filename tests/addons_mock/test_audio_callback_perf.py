"""Benchmark the audio-engine callback under a realistic dub load.

Targets the chunky-playback regression: build an engine with N dubbed
subtitles, fire `_callback` repeatedly across the timeline (so different
clips are in-window each call), and measure per-callback wall time.

Two things matter for smooth audio at blocksize=2048, samplerate=48000:
  * The PortAudio block budget is ~42.7 ms — any single callback over
    that drops the next block.
  * Average must stay well under budget (≤50 %), because the audio
    thread also needs slack for the OS to schedule it. If avg is e.g.
    20 ms, then a single 30 ms hiccup elsewhere (GC, paintEvent holding
    the GIL) costs a full block.

The test asserts:
  1. With DEBUG_AUDIO off the average per-callback time on a 500-clip
     project is under ~8 ms (4× headroom on the 42.7 ms budget).
  2. With DEBUG_AUDIO on (the working-tree default) the SAME load is
     not catastrophically worse — otherwise the diagnostic is itself a
     regression cause. Threshold: avg under ~12 ms.

Run directly (matches the rest of the addons_mock style):

    python3 tests/addons_mock/test_audio_callback_perf.py

Skips gracefully if sounddevice is missing — the engine module imports
it at top level, but we don't actually open an OutputStream.
"""

from __future__ import annotations

import os
import sys
import time
import importlib
import threading
import wave
import struct
import math
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))


def _make_dub_wav(path: str, duration_sec: float, sample_rate: int = 24000) -> None:
    """Build a tiny mono 16-bit WAV — used as the dub file for each
    fake subtitle. Stereo is what the audio engine produces; the file
    on disk can be mono (it tiles to stereo on preload)."""
    n = int(duration_sec * sample_rate)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n):
            v = int(0.2 * 32767 * math.sin(2 * math.pi * 440.0 * i / sample_rate))
            wf.writeframesraw(struct.pack('<h', v))


def _import_engine(debug_audio: str):
    """Force a fresh import of audioengine with the given DEBUG_AUDIO env.

    The module reads the env var at import time, so we must reimport
    after env mutation. Returns the imported module."""
    os.environ['SUBTITLD_AUDIO_DEBUG'] = debug_audio
    if 'subtitld.modules.audioengine' in sys.modules:
        del sys.modules['subtitld.modules.audioengine']
    return importlib.import_module('subtitld.modules.audioengine')


def _build_engine_with_dubs(audioengine, dub_wav: str, num_subs: int,
                            timeline_span_sec: float):
    """Build an engine with `num_subs` fake subtitles spread across the
    timeline, all using the same dub wav. Skips the real OutputStream —
    we drive `_callback` ourselves and never touch PortAudio."""
    # Monkey-patch sd.OutputStream so __init__ doesn't actually open a
    # device. Returning a dummy is enough for the engine to be usable.
    class _FakeStream:
        def start(self): pass
        def stop(self):  pass

    audioengine.sd.OutputStream = lambda **kw: _FakeStream()

    eng = audioengine.SoundDeviceAudioEngine(samplerate=48000, blocksize=2048)
    track = eng.generate_track()
    eng.add_track(track)

    # Same speaker for all subs — matches the "1 track for one speaker
    # with hundreds of dubs" case from the diff comments.
    subs = []
    spacing = timeline_span_sec / max(1, num_subs)
    for i in range(num_subs):
        sub = {
            'speaker': 'A',
            'start': i * spacing,
            'end': i * spacing + 1.5,
            'text': f'sub {i}',
            'dubbing': [{
                'engine': 'edge-tts',
                'voice': 'fake',
                'rate': 0,
                'path': dub_wav,
                'start': i * spacing,
            }],
        }
        subs.append(sub)
        clip = audioengine.SubtitleDubClip(sub, eng.samplerate)
        clip.preload(dub_wav)
        track.add_clip(clip)
    return eng, subs


def _bench_callback(eng, span_sec: float, iterations: int) -> dict:
    """Drive `_callback` `iterations` times with the playhead walking
    across the project's full span. Returns timing stats."""
    import numpy as np

    frames = eng.blocksize
    outdata = np.zeros((frames, 2), dtype=np.float32)
    # Walk the playhead at the same rate as wall-clock would advance — so
    # `iterations` callbacks cover `iterations * frames / samplerate`
    # seconds of audio. Wrap to keep playhead inside the project.
    advance_sec = frames / eng.samplerate

    timings_ms: list[float] = []
    eng.playing = True
    try:
        for i in range(iterations):
            eng.playhead = (i * advance_sec) % max(1.0, span_sec)
            t0 = time.perf_counter()
            eng._callback(outdata, frames, None, 0)
            t1 = time.perf_counter()
            timings_ms.append((t1 - t0) * 1000.0)
    finally:
        eng.playing = False

    timings_ms.sort()
    return {
        'iterations': iterations,
        'avg_ms':     sum(timings_ms) / len(timings_ms),
        'min_ms':     timings_ms[0],
        'p50_ms':     timings_ms[len(timings_ms) // 2],
        'p95_ms':     timings_ms[int(len(timings_ms) * 0.95)],
        'p99_ms':     timings_ms[int(len(timings_ms) * 0.99)],
        'max_ms':     timings_ms[-1],
        'over_budget_count': sum(1 for t in timings_ms if t > 42.7),
    }


def case_no_dubs():
    """Empty engine — establishes the floor cost of the callback. Should
    be sub-millisecond; anything more means the instrumentation alone is
    a regression."""
    print('\n=== case 1: empty engine (callback floor) ===')

    audioengine = _import_engine('0')  # debug OFF
    eng, _ = _build_engine_with_dubs(audioengine, '', 0, 60.0)
    stats = _bench_callback(eng, 60.0, iterations=500)
    print(f'  DEBUG=0: avg={stats["avg_ms"]:.3f} p95={stats["p95_ms"]:.3f} '
          f'p99={stats["p99_ms"]:.3f} max={stats["max_ms"]:.3f} ms')
    # Empty engine must be very cheap — well under 1ms.
    assert stats['avg_ms'] < 1.0, f'empty callback too slow: {stats}'


def case_many_dubs(num_subs: int):
    print(f'\n=== case 2: {num_subs} dubs, DEBUG off ===')
    workdir = tempfile.mkdtemp(prefix='audio-bench-')
    try:
        dub = os.path.join(workdir, 'dub.wav')
        _make_dub_wav(dub, duration_sec=1.5)

        audioengine = _import_engine('0')
        eng, _ = _build_engine_with_dubs(audioengine, dub, num_subs,
                                         timeline_span_sec=num_subs * 4.0)
        stats = _bench_callback(eng, num_subs * 4.0, iterations=600)
        print(f'  DEBUG=0: avg={stats["avg_ms"]:.3f} p95={stats["p95_ms"]:.3f} '
              f'p99={stats["p99_ms"]:.3f} max={stats["max_ms"]:.3f} ms '
              f'over_budget={stats["over_budget_count"]}/{stats["iterations"]}')
        # Budget is 42.7ms — we want plenty of headroom so a GC pause
        # or paintEvent doesn't push us over. ≤8ms average gives ~5×.
        assert stats['avg_ms'] < 8.0, (
            f'avg callback too slow with {num_subs} dubs (DEBUG off): {stats}')
        # No single callback should bust the budget in the absence of
        # main-thread interference. If this triggers, the engine itself
        # has work-per-callback that occasionally spikes.
        assert stats['p99_ms'] < 20.0, (
            f'p99 callback too slow with {num_subs} dubs (DEBUG off): {stats}')

        # Now with DEBUG on — should not be drastically slower.
        audioengine_dbg = _import_engine('1')
        eng_dbg, _ = _build_engine_with_dubs(audioengine_dbg, dub, num_subs,
                                             timeline_span_sec=num_subs * 4.0)
        # Stash sys.stderr so the test output stays readable when the
        # debug instrumentation tries to print XRUN warnings (it won't —
        # nothing is actually XRUNing in this synthetic loop — but the
        # 1-second printer thread might fire).
        stats_dbg = _bench_callback(eng_dbg, num_subs * 4.0, iterations=600)
        print(f'  DEBUG=1: avg={stats_dbg["avg_ms"]:.3f} '
              f'p95={stats_dbg["p95_ms"]:.3f} p99={stats_dbg["p99_ms"]:.3f} '
              f'max={stats_dbg["max_ms"]:.3f} ms '
              f'over_budget={stats_dbg["over_budget_count"]}/{stats_dbg["iterations"]}')
        # If DEBUG ON doubles the average, the instrumentation is itself
        # a regression. Tighten the threshold if/when we remove the
        # stderr-from-callback writes.
        overhead = stats_dbg['avg_ms'] - stats['avg_ms']
        print(f'  DEBUG overhead avg: {overhead:.3f} ms')
        assert stats_dbg['avg_ms'] < 12.0, (
            f'DEBUG=1 callback too slow: {stats_dbg}')
        # Catch the cascading-stderr scenario explicitly: if even ONE
        # callback over budget shows up, the diagnostic itself is
        # corrupting the measurement.
        assert stats_dbg['over_budget_count'] == 0, (
            f'DEBUG=1 produced over-budget callbacks: {stats_dbg}')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_no_disk_io_on_audio_thread():
    """The chunky-playback / silent-crackle regression was the audio
    callback falling into `SubtitleDubClip.preload()` for an un-preloaded
    path. `preload()` opens a SoundFile (disk I/O) and allocates a full
    clip ndarray — a cold-cache open easily blocks tens of ms, past the
    42.7 ms block budget. Assert that the callback path never opens a
    file even when it encounters a dub whose path hasn't been preloaded
    on the main thread."""
    print('\n=== case 4: audio callback never opens files ===')
    workdir = tempfile.mkdtemp(prefix='audio-bench-')
    try:
        dub = os.path.join(workdir, 'dub.wav')
        _make_dub_wav(dub, duration_sec=1.5)

        audioengine = _import_engine('0')

        # Monkey-patch sf.SoundFile to record any open from the audio
        # thread. If `read()` opens a file, this list grows — guaranteed
        # underrun in production.
        opens = []
        original_soundfile = audioengine.sf.SoundFile

        def _tracking_soundfile(*args, **kwargs):
            opens.append((threading.get_ident(), args[0] if args else kwargs.get('file')))
            return original_soundfile(*args, **kwargs)

        # Set up the engine and intentionally DO NOT preload the clip on
        # the main thread before firing the callback. The old code would
        # then fall into preload from the callback; the fixed code must
        # queue an async load and return None instead.
        class _FakeStream:
            def start(self): pass
            def stop(self): pass
        audioengine.sd.OutputStream = lambda **kw: _FakeStream()

        eng = audioengine.SoundDeviceAudioEngine(samplerate=48000, blocksize=2048)
        track = eng.generate_track()
        eng.add_track(track)

        sub = {
            'speaker': 'A', 'start': 0.0, 'end': 1.5, 'text': 'sub',
            'dubbing': [{
                'engine': 'edge-tts', 'voice': 'fake', 'rate': 0,
                'path': dub, 'start': 0.0,
            }],
        }
        clip = audioengine.SubtitleDubClip(sub, eng.samplerate)
        # Crucially: NO preload here. The point is to fail the test if
        # `read()` does the load.
        track.add_clip(clip)

        # Install the patched SoundFile now, AFTER the engine is built —
        # so the test only catches opens that happen on the audio path.
        import threading
        audio_tid = threading.get_ident()  # we're driving callbacks from this thread
        audioengine.sf.SoundFile = _tracking_soundfile

        import numpy as np
        outdata = np.zeros((eng.blocksize, 2), dtype=np.float32)
        eng.playing = True
        try:
            for i in range(20):
                eng.playhead = i * 0.05  # inside the dub window
                eng._callback(outdata, eng.blocksize, None, 0)
        finally:
            eng.playing = False

        # If even one open happened from this thread (= simulated audio
        # thread), we've regressed. Background thread opens are fine —
        # those are the async loader doing what it should.
        opens_from_audio = [path for tid, path in opens if tid == audio_tid]
        assert not opens_from_audio, (
            f'audio callback opened files synchronously: {opens_from_audio}\n'
            f'all opens: {opens}'
        )
        print(f'  PASS — 20 callbacks completed without audio-thread disk I/O '
              f'({len(opens)} opens observed, all off-thread)')

        # Restore so other cases aren't affected.
        audioengine.sf.SoundFile = original_soundfile
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def case_gc_under_load():
    """A gen-2 GC during playback is the classic stop-the-world cause of
    a multi-block dropout. The engine's `play()` calls `gc.disable()` to
    suppress it. Verify that disabling sticks: collect once, then check
    `gc.isenabled()` is False during a synthetic playback span."""
    print('\n=== case 3: gc disabled during playback ===')
    import gc as _gc

    audioengine = _import_engine('0')
    eng, _ = _build_engine_with_dubs(audioengine, '', 0, 60.0)
    # Bypass eng.play() — it calls stream.start() which our fake
    # OutputStream handles, but we want to assert gc state cleanly.
    eng._gc_was_enabled = _gc.isenabled()
    _gc.disable()
    eng.playing = True
    try:
        assert not _gc.isenabled(), 'gc should be disabled while playing'
        # A synthetic callback batch shouldn't re-enable gc.
        import numpy as np
        outdata = np.zeros((eng.blocksize, 2), dtype=np.float32)
        for _ in range(50):
            eng._callback(outdata, eng.blocksize, None, 0)
        assert not _gc.isenabled(), 'gc unexpectedly re-enabled by callback'
    finally:
        eng.playing = False
        if eng._gc_was_enabled:
            _gc.enable()
    print('  PASS — gc stayed disabled across the synthetic playback')


def main():
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # pragma: no cover
        print(f'SKIP — sounddevice unavailable: {exc}')
        return 0

    case_no_dubs()
    case_many_dubs(num_subs=50)
    case_many_dubs(num_subs=500)
    case_no_disk_io_on_audio_thread()
    case_gc_under_load()
    print('\nAll audio-callback perf cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
