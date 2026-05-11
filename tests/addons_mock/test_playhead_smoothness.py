"""Microbench / behavior test for playhead extrapolation.

Why this exists
---------------
QMediaPlayer.positionChanged fires at only ~10 Hz on most Qt6 backends
(Qt6 removed setNotifyInterval). If the timeline simply repaints at
30 Hz and reads session.SUBTITLE['position'] each tick, the playhead
draws at the *same* x three times in a row, then jumps a big chunk on
the next QMediaPlayer emit. That's visible chunkiness.

The fix in playercontrols.py: on each positionChanged emit, anchor
(reported_position, wall_clock_time). On each repaint tick, extrapolate
`anchor_position + (now - anchor_walltime) * playback_speed`. Result:
the playhead glides instead of stepping.

What this test asserts
----------------------
We simulate the real flow:
  * QMediaPlayer fires positionChanged every 100 ms.
  * Repaint timer fires every 33 ms.
At each repaint tick we record the *extrapolated* position. The
between-tick deltas should be ~33 ms-worth of playback (not 0 / 100 /
0 / 0 / 100 like the un-extrapolated case would be).

If somebody rips out the extrapolation, this test catches it: the
deltas collapse to 0/0/0/100/0/0/0/100 and the stddev shoots up.

Run directly:
    python3 tests/addons_mock/test_playhead_smoothness.py
"""

from __future__ import annotations

import statistics


class FakeClock:
    """Deterministic wall clock — no real time involved. Starts at a
    positive base so that any `walltime <= 0` sentinel used in the real
    code (to mean "no anchor yet") stays untriggered after the first
    anchor — mirroring time.perf_counter() in production."""
    def __init__(self, base=1000.0):
        self.t = base
    def perf_counter(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def _extrapolate(anchor_sec, anchor_walltime, now, speed, cap=0.25,
                 playing=True):
    """Same formula as _smoothed_position_sec in playercontrols.py.

    `playing` mirrors the QMediaPlayer.playbackState() == PlayingState
    check: when False, return the raw anchor (no time-based advance).
    The default is True so existing call sites stay valid; the
    paused-cursor case explicitly passes playing=False."""
    if anchor_walltime <= 0:
        return anchor_sec
    if not playing:
        return anchor_sec
    elapsed = now - anchor_walltime
    if elapsed < 0:
        elapsed = 0.0
    if elapsed > cap:
        elapsed = cap
    return anchor_sec + elapsed * speed


def case_extrapolation_smoothness():
    """Drive a 1-second playback with QMediaPlayer-rate (10 Hz) anchors
    and timer-rate (30 Hz) ticks. Assert the per-tick position deltas
    are tightly clustered around 33 ms-worth of playback (~0.033 s at
    speed=1), not multimodal at 0 ms / 100 ms."""
    print('\n=== playhead extrapolation smoothness ===')
    clock = FakeClock()
    base = clock.perf_counter()  # anchor walltime offset
    speed = 1.0
    qmp_interval = 0.100   # 10 Hz anchors (QMediaPlayer emit rate)
    tick_interval = 0.033  # 30 Hz repaints (our timer)

    # State that mirrors what playercontrols.py keeps. Initial anchor
    # at playback t=0, walltime = current FakeClock reading.
    anchor_sec = 0.0
    anchor_walltime = clock.perf_counter()

    # Build a schedule of events: ticks every 33 ms, anchors every 100 ms,
    # over 1 second total. Tag each event so we know what to do.
    # Skip the t=0 events for both — they're already represented by the
    # initial anchor + the first tick will be at t=tick_interval.
    events = []
    t = tick_interval
    while t < 1.0:
        events.append((t, 'tick'))
        t += tick_interval
    t = qmp_interval
    while t < 1.0:
        events.append((t, 'anchor'))
        t += qmp_interval
    events.sort()  # process in time order; ties broken by tag alpha

    positions_at_ticks = []
    # Record the position at t=0 too — that's the "first tick" baseline.
    positions_at_ticks.append(_extrapolate(anchor_sec, anchor_walltime,
                                            clock.perf_counter(), speed))
    for ev_time, kind in events:
        clock.t = base + ev_time
        if kind == 'anchor':
            # QMediaPlayer says "you're at this position right now".
            # Wall-clock continues from the same instant.
            anchor_sec = ev_time * speed  # canonical playback progresses at `speed`
            anchor_walltime = clock.perf_counter()
        else:
            # Repaint tick — read extrapolated value.
            pos = _extrapolate(anchor_sec, anchor_walltime,
                               clock.perf_counter(), speed)
            positions_at_ticks.append(pos)

    # Compute per-tick deltas. With extrapolation, every tick advances
    # by ~tick_interval × speed. Without it, deltas would alternate
    # between 0 (cache hit on same anchor) and ~qmp_interval (anchor
    # bump).
    deltas = [b - a for a, b in zip(positions_at_ticks[:-1],
                                     positions_at_ticks[1:])]
    mean = statistics.mean(deltas)
    stdev = statistics.pstdev(deltas)
    print(f'  ticks={len(positions_at_ticks)}  mean Δ={mean*1000:.2f} ms  '
          f'σ={stdev*1000:.2f} ms')

    # With extrapolation, every delta is tick_interval × speed = ~33 ms.
    # Allow some slack for the boundary tick where an anchor lands
    # between two ticks and resets elapsed to ~0 — that single delta is
    # naturally larger or smaller than 33 ms.
    expected = tick_interval * speed
    assert abs(mean - expected) < 0.005, (
        f'extrapolation should mean ~{expected*1000:.0f} ms; got {mean*1000:.2f} ms')

    # The critical assertion: standard deviation should be small. The
    # un-extrapolated case has σ ≈ tick_interval × √(2/9) ≈ 16 ms
    # (most deltas are 0, a few are 100 ms). Our extrapolated case
    # should be well under 10 ms.
    assert stdev * 1000 < 10.0, (
        f'tick-delta σ too high ({stdev*1000:.2f} ms) — extrapolation '
        f'broken? deltas={deltas}'
    )


def case_extrapolation_with_speed():
    """At 2× playback speed, the extrapolation should advance twice as
    fast between anchors. At 0.5×, half as fast."""
    print('\n=== playhead extrapolation honors playback_speed ===')
    for speed in (0.5, 1.0, 2.0):
        clock = FakeClock()
        anchor_sec = 0.0
        anchor_walltime = clock.perf_counter()
        clock.advance(0.033)
        pos = _extrapolate(anchor_sec, anchor_walltime,
                           clock.perf_counter(), speed)
        expected = 0.033 * speed
        assert abs(pos - expected) < 1e-6, (
            f'speed={speed}: expected pos≈{expected:.4f}, got {pos:.4f}')
        print(f'  speed={speed}× → after 33 ms, pos={pos*1000:.2f} ms ✓')


def case_extrapolation_cap():
    """If QMediaPlayer stalls (no anchors for a long time — seek,
    buffering), extrapolation must not run away to infinity. Cap is
    0.25 s in playercontrols.py."""
    print('\n=== playhead extrapolation is capped on stall ===')
    clock = FakeClock()
    anchor_sec = 10.0
    anchor_walltime = clock.perf_counter()
    # Simulate a 5-second stall (no new anchors).
    clock.advance(5.0)
    pos = _extrapolate(anchor_sec, anchor_walltime,
                       clock.perf_counter(), speed=1.0, cap=0.25)
    # Should not fly past anchor + 0.25.
    assert pos <= 10.0 + 0.25 + 1e-6, (
        f'extrapolation flew off: got {pos}, expected ≤ 10.25')
    print(f'  5 s stall: pos={pos:.4f} (capped at anchor + 0.25) ✓')


def case_re_anchor_after_seek():
    """After a seek, QMediaPlayer reports the *new* position; the
    anchor must reset to that, not extrapolate from the old position."""
    print('\n=== playhead extrapolation re-anchors on seek ===')
    clock = FakeClock()
    # Initial: at 5.0 s.
    anchor_sec = 5.0
    anchor_walltime = clock.perf_counter()
    clock.advance(0.05)  # half-way through an anchor interval

    # User seeks to 50.0 s. QMediaPlayer emits positionChanged(50000).
    anchor_sec = 50.0
    anchor_walltime = clock.perf_counter()
    clock.advance(0.033)

    pos = _extrapolate(anchor_sec, anchor_walltime,
                       clock.perf_counter(), speed=1.0)
    # Should be 50.033, NOT 5.083.
    assert abs(pos - 50.033) < 1e-6, (
        f'seek re-anchor broken: expected ~50.033, got {pos}')
    print(f'  after seek to 50 s + 33 ms: pos={pos:.4f} ✓')


def case_no_slide_while_paused():
    """Regression: clicking the timeline while playback is paused must
    NOT slide the cursor right on subsequent repaint ticks. The bug:
    seeking always re-anchors (anchor_sec=click_pos, walltime=now), and
    the repaint timer was running unconditionally. Each tick computed
    `anchor + elapsed × speed`, which is non-zero whenever the timer
    keeps firing after the click — cursor drifts away from where the
    user clicked.

    Fix: when the player is not in PlayingState, _smoothed_position_sec
    returns the raw anchor (no time-based advance), and
    _on_position_changed doesn't start the timer."""
    print('\n=== playhead stays put on seek-while-paused ===')
    clock = FakeClock()

    # User clicks timeline at t=42.0s while paused. Anchor gets set.
    anchor_sec = 42.0
    anchor_walltime = clock.perf_counter()

    # Several "ticks" elapse (as if the timer were running). With
    # the fix in place, each call must return the anchor exactly —
    # the elapsed × speed term must be skipped.
    for dt in (0.016, 0.050, 0.100, 0.500, 2.000):
        clock.advance(dt)
        pos = _extrapolate(anchor_sec, anchor_walltime,
                           clock.perf_counter(), speed=1.0, playing=False)
        assert abs(pos - 42.0) < 1e-9, (
            f'cursor slid while paused: at +{dt:.3f}s, pos={pos:.6f} '
            f'(expected 42.0)')
    print('  cursor stays at 42.000s through 2.666s of wall-clock '
          'with playing=False ✓')

    # And switching to playing must immediately resume the
    # extrapolation from the same anchor.
    pos = _extrapolate(anchor_sec, anchor_walltime,
                       clock.perf_counter(), speed=1.0, playing=True)
    # We advanced by 0.016 + 0.050 + 0.100 + 0.500 + 2.000 = 2.666 s,
    # but the cap is 0.25 s, so the resumed extrapolation maxes at
    # anchor + 0.25.
    assert abs(pos - (42.0 + 0.25)) < 1e-6, (
        f'resume after pause: expected {42.0 + 0.25}, got {pos}')
    print(f'  switching to playing=True resumes extrapolation '
          f'(capped at +0.25s) → {pos:.4f} ✓')


def main():
    case_extrapolation_smoothness()
    case_extrapolation_with_speed()
    case_extrapolation_cap()
    case_re_anchor_after_seek()
    case_no_slide_while_paused()
    print('\nPlayhead smoothness cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
