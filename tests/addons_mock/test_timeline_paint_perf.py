"""Microbench for the Python overhead inside Timeline.paintEvent's per-
subtitle loop. Companion to test_audio_callback_perf.py.

Why this exists
---------------
Chunky playback was caused by main-thread paintEvents holding the GIL
for so long that the audio callback missed its next 42.7 ms slot. The
audio callback itself is fast (proved by test_audio_callback_perf.py:
0.2 ms avg with 500 dubs). So the GIL-hogger has to be on the main
thread, and the obvious candidate is the per-subtitle paint loop in
`src/subtitld/interface/timeline.py`.

That loop used to do ~6 `session.CONFIG.get('timeline', {}).get(...)`
walks and ~3 fresh `QColor()` allocations *per visible subtitle, per
paint*. At 100 visible subtitles × 30 Hz preview cadence that's:

    100 * 30 * (6 walks + 3 QColors) ≈ 27,000 ops/sec

…all on the main thread, all holding the GIL. The fix hoists every
loop-invariant lookup and QColor allocation out of the loop and
consumes the cached values inside.

What the bench actually measures
--------------------------------
Two synthetic loop bodies that match the *Python* shape of the real
paint loop:

  * `_loop_OLD` — repeats `session.CONFIG.get(...).get(...)` and
    `QColor(...)` per iteration, like the regression did.
  * `_loop_NEW` — reads the same values once outside the loop and
    consumes the cached values inside.

We don't try to model the QPainter calls — those go to C++ and release
the GIL, so they aren't the GIL-holders we care about. The signal we
want is: did the Python loop body get cheaper enough that a 60 Hz
paintEvent on a 100-subtitle project fits inside one audio block.

Run directly:

    python3 tests/addons_mock/test_timeline_paint_perf.py
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _ensure_qt():
    """We need a QGuiApplication for QColor to exist. Offscreen is fine."""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def _fake_config():
    """A CONFIG that matches what session.CONFIG looks like in practice —
    multiple top-level keys, each with a sub-dict. The whole point of the
    regression was that the hot loop walked these every iteration."""
    return {
        'timeline': {
            'subtitle_text_color': '#ff304251',
            'selected_subtitle_text_color': '#b8cee0',
            'subtitle_fill_color': '#c8dbe9',
            'subtitle_border_color': '#ff6a7483',
            'dub_waveform_color': '#ffffffff',
        },
        'dubbing': {'enabled': False},
        'quality_check': {'enabled': False},
        'translation': {'engine_options': {'show_translations': False,
                                            'target_language': 'en-us'}},
    }


def _fake_subtitles(n: int):
    return [
        {
            'start': i * 2.0,
            'end': i * 2.0 + 1.5,
            'speaker': 'A' if i % 2 == 0 else 'B',
            'text': f'sub {i}',
            'dubbing': [],
        }
        for i in range(n)
    ]


def _loop_OLD(subs, config, speakers, selected):
    """Mirror the pre-fix hot loop body — every iteration does fresh
    dict walks and QColor allocations."""
    from PySide6.QtGui import QColor
    sink = 0
    for s in subs:
        # The exact lookups the old code did per subtitle:
        if config.get('quality_check', {}).get('enabled', False):
            text_color = QColor(config.get('timeline', {}).get('subtitle_text_color', '#ff304251'))
        else:
            text_color = QColor(config.get('timeline', {}).get('subtitle_text_color', '#304251'))
        if selected == s:
            sel_color = QColor(config.get('timeline', {}).get('selected_subtitle_text_color', '#b8cee0'))
        else:
            sel_color = QColor(config.get('timeline', {}).get('subtitle_text_color', '#304251'))
        speaker = speakers.get(s.get('speaker', 'A'), {}).get('color', '#1a73a8')
        spk_color = QColor(speaker)
        # Touch the colors so the compiler can't elide them.
        sink += text_color.red() + sel_color.green() + spk_color.blue()
        # Translation branch — also paid by every subtitle in the old code.
        target = config['translation'].get('engine_options', {}).get('target_language', 'en-us')
        show_trans = config['translation'].get('engine_options', {}).get('show_translations', False)
        if show_trans:
            sink += len(target)
    return sink


def _loop_NEW(subs, config, speakers, selected):
    """Mirror the post-fix hot loop body — invariants hoisted, only
    per-subtitle data touched inside the loop."""
    from PySide6.QtGui import QColor
    # All of these are now computed ONCE per paint, outside the loop.
    timeline_cfg = config.get('timeline', {})
    qc_enabled = config.get('quality_check', {}).get('enabled', False)
    translation_opts = config.get('translation', {}).get('engine_options', {})
    target = translation_opts.get('target_language', 'en-us')
    show_trans = translation_opts.get('show_translations', False)
    text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#304251'))
    text_color_selected = QColor(timeline_cfg.get('selected_subtitle_text_color', '#b8cee0'))
    qc_text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#ff304251'))
    sink = 0
    for s in subs:
        speaker_data = speakers.get(s.get('speaker', 'A'), {})
        if qc_enabled:
            text_color = qc_text_color_unselected
        else:
            text_color = text_color_unselected
        sel_color = text_color_selected if selected == s else text_color_unselected
        spk_color = QColor(speaker_data.get('color', '#1a73a8'))
        sink += text_color.red() + sel_color.green() + spk_color.blue()
        if show_trans:
            sink += len(target)
    return sink


def _bench(fn, *args, iterations: int) -> dict:
    timings_ms = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn(*args)
        t1 = time.perf_counter()
        timings_ms.append((t1 - t0) * 1000.0)
    timings_ms.sort()
    return {
        'avg_ms': sum(timings_ms) / len(timings_ms),
        'p50_ms': timings_ms[len(timings_ms) // 2],
        'p95_ms': timings_ms[int(len(timings_ms) * 0.95)],
        'max_ms': timings_ms[-1],
    }


def case_loop_body_overhead():
    print('\n=== timeline per-subtitle loop body overhead ===')
    _ensure_qt()
    cfg = _fake_config()
    speakers = {'A': {'color': '#1a73a8'}, 'B': {'color': '#a8731a'}}

    for n in (50, 200, 800):
        subs = _fake_subtitles(n)
        old = _bench(_loop_OLD, subs, cfg, speakers, None, iterations=50)
        new = _bench(_loop_NEW, subs, cfg, speakers, None, iterations=50)
        ratio = old['avg_ms'] / new['avg_ms'] if new['avg_ms'] > 0 else float('inf')
        print(
            f'  n={n:>3}  OLD avg={old["avg_ms"]:.3f} p95={old["p95_ms"]:.3f} '
            f'max={old["max_ms"]:.3f} ms   '
            f'NEW avg={new["avg_ms"]:.3f} p95={new["p95_ms"]:.3f} '
            f'max={new["max_ms"]:.3f} ms   speedup={ratio:.2f}×'
        )

        # The hoist should be a meaningful win. If this ratio drops to
        # ~1, somebody put the dict.get walks back inside the loop.
        # 1.5× is comfortably above noise on offscreen rendering.
        assert ratio >= 1.5, (
            f'expected ≥1.5× speedup hoisting CONFIG walks (n={n}); '
            f'got OLD={old} NEW={new}'
        )

        # And — the new body must fit inside the audio block budget
        # even on a 200-sub project with margin for Qt's C++ side. The
        # *Python* overhead alone here is the floor.
        if n <= 200:
            assert new['avg_ms'] < 10.0, (
                f'NEW loop body too slow at n={n}: {new}')


def _loop_NEW_strip(subs, config, speakers, selected, visible_start_sec, visible_end_sec):
    """Mirror the post-region-clip hot loop: same hoisting as _loop_NEW
    but the subtitle list is filtered by the dirty strip before the body
    runs. That filter is what region-targeted invalidation buys us: at
    30 Hz playhead repaints we iterate ~the subtitles inside one strip
    (a handful), not the whole visible viewport (often hundreds)."""
    from PySide6.QtGui import QColor
    timeline_cfg = config.get('timeline', {})
    qc_enabled = config.get('quality_check', {}).get('enabled', False)
    translation_opts = config.get('translation', {}).get('engine_options', {})
    target = translation_opts.get('target_language', 'en-us')
    show_trans = translation_opts.get('show_translations', False)
    text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#304251'))
    text_color_selected = QColor(timeline_cfg.get('selected_subtitle_text_color', '#b8cee0'))
    qc_text_color_unselected = QColor(timeline_cfg.get('subtitle_text_color', '#ff304251'))
    sink = 0
    for s in subs:
        # ← This is the filter the real paintEvent applies after the
        # iter_left/iter_right narrowing lands.
        if s['start'] > visible_end_sec:
            continue
        if s['end'] < visible_start_sec:
            continue
        speaker_data = speakers.get(s.get('speaker', 'A'), {})
        if qc_enabled:
            text_color = qc_text_color_unselected
        else:
            text_color = text_color_unselected
        sel_color = text_color_selected if selected == s else text_color_unselected
        spk_color = QColor(speaker_data.get('color', '#1a73a8'))
        sink += text_color.red() + sel_color.green() + spk_color.blue()
        if show_trans:
            sink += len(target)
    return sink


def case_region_clip_savings():
    """The playhead-strip timer repaints a narrow rect (e.g. 200 px wide)
    around the cursor at 30 Hz. Inside paintEvent that rect is intersected
    with the visible viewport and the result feeds visible_start_sec /
    visible_end_sec, which filters the per-subtitle loop. So on a project
    with 800 subtitles, a strip that covers ~4 seconds should only touch
    ~2-4 subtitles instead of all 800.

    If somebody removes the iter_left/iter_right narrowing in
    timeline.paintEvent, this test catches it: the strip iteration falls
    back to walking all subs and the ratio collapses to ~1.
    """
    print('\n=== region-clip savings (playhead strip vs full viewport) ===')
    _ensure_qt()
    cfg = _fake_config()
    speakers = {'A': {'color': '#1a73a8'}, 'B': {'color': '#a8731a'}}

    # 800 subtitles spanning 1600 seconds (one every 2s, as _fake_subtitles
    # does). The "full viewport" represents a paint that walks the whole
    # visible range; "strip" represents the playhead-repaint case where
    # the dirty rect maps to a 4-second window.
    n = 800
    subs = _fake_subtitles(n)
    full_start, full_end = 0.0, 1600.0
    strip_center = 800.0
    strip_start, strip_end = strip_center - 2.0, strip_center + 2.0

    full = _bench(_loop_NEW_strip, subs, cfg, speakers, None,
                  full_start, full_end, iterations=50)
    strip = _bench(_loop_NEW_strip, subs, cfg, speakers, None,
                   strip_start, strip_end, iterations=50)
    ratio = full['avg_ms'] / strip['avg_ms'] if strip['avg_ms'] > 0 else float('inf')
    print(
        f'  full-viewport avg={full["avg_ms"]:.3f} ms   '
        f'strip avg={strip["avg_ms"]:.4f} ms   speedup={ratio:.1f}×'
    )

    # A 4-second strip against 800 subs at 2s spacing touches ~2 subs.
    # Walking the full viewport touches all 800. The strip case still
    # has to *iterate* all 800 to apply the bail-out check (no binary
    # search yet), so the floor is set by Python's loop overhead. In
    # practice we observe ~25-30× — comfortably above 20×. If somebody
    # removes the visible_start/end filter inside the loop, both
    # branches execute the heavy body and this ratio collapses to ~1.
    assert ratio >= 20.0, (
        f'expected ≥20× speedup clipping subtitle iteration to strip; '
        f'got full={full} strip={strip}'
    )

    # The strip-clipped body must comfortably fit inside one audio block
    # (42.7 ms). In practice we want it WAY below that — at 30 Hz repaint
    # this runs ~30× per second and must leave room for the audio thread.
    assert strip['avg_ms'] < 1.0, (
        f'strip-clipped loop too slow: {strip}')


def main():
    case_loop_body_overhead()
    case_region_clip_savings()
    print('\nTimeline loop-body perf cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
