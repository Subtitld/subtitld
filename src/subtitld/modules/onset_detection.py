"""Audio onset detection for the timeline visualization layer.

Cheap, dependency-free (numpy only) spectral-flux detector. It catches
sharp broadband transients in speech — plosives, sibilants, and sharp
vowel onsets — which is what the eye actually uses to lock dubs to the
underlying video. We don't classify phonemes; the markers are surfaced
in the UI as "consonant onsets", not strictly plosives.

Algorithm: STFT → positive spectral flux → local-mean baseline subtract
→ peak picking with a minimum inter-onset gap. Chunked so the magnitude
matrix never grows past ~64 MB for arbitrarily long audio.
"""

from __future__ import annotations

import numpy as np


def detect_onsets(
    samples: np.ndarray,
    samplerate: int,
    hop_ms: float = 10.0,
    frame_ms: float = 40.0,
    threshold: float = 0.18,
    min_gap_ms: float = 50.0,
) -> np.ndarray:
    """Return onset times (seconds, float32) for a mono audio array.

    `threshold` is the minimum baseline-subtracted novelty value (in
    [0, 1] after global normalization) required for a peak to count.
    `min_gap_ms` is the minimum spacing between successive onsets — the
    higher of two collided peaks wins.

    Empty array on any error path (zero-length input, frame_size larger
    than the audio, all-silent input).
    """
    if samples is None or samplerate <= 0:
        return np.array([], dtype=np.float32)
    samples = np.ascontiguousarray(samples)
    if samples.size == 0:
        return np.array([], dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=tuple(range(1, samples.ndim)))
    samples = samples.astype(np.float32, copy=False)

    hop = max(1, int(samplerate * hop_ms / 1000.0))
    frame_size = max(hop, int(samplerate * frame_ms / 1000.0))
    if samples.size < frame_size + hop:
        return np.array([], dtype=np.float32)

    n_frames = (samples.size - frame_size) // hop + 1
    if n_frames < 3:
        return np.array([], dtype=np.float32)

    window = np.hanning(frame_size).astype(np.float32)
    n_bins = frame_size // 2 + 1

    # Cap memory at ~64 MB for the magnitude matrix (float32, n_bins).
    max_frames_per_chunk = max(64, int(64 * 1024 * 1024 / (n_bins * 4)))

    novelty = np.zeros(n_frames, dtype=np.float32)
    prev_last_mag: np.ndarray | None = None

    for cs in range(0, n_frames, max_frames_per_chunk):
        ce = min(n_frames, cs + max_frames_per_chunk)
        offsets = np.arange(cs, ce) * hop
        idx = np.arange(frame_size)[None, :] + offsets[:, None]
        frames = samples[idx] * window
        mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)

        # Bridge the chunk boundary: the first diff in this chunk
        # references the last mag of the previous chunk so we don't
        # silently zero out the novelty value at every chunk edge.
        if prev_last_mag is not None and mag.shape[0] >= 1:
            first_diff = mag[0] - prev_last_mag
            np.clip(first_diff, 0, None, out=first_diff)
            novelty[cs] = first_diff.sum()
        if mag.shape[0] > 1:
            diff = np.diff(mag, axis=0)
            np.clip(diff, 0, None, out=diff)
            novelty[cs + 1:ce] = diff.sum(axis=1)
        prev_last_mag = mag[-1]

    nmax = float(novelty.max())
    if nmax <= 0.0:
        return np.array([], dtype=np.float32)
    novelty /= nmax

    # Local-mean baseline (~150 ms) suppresses the slow envelope so the
    # threshold can pick out sharp peaks regardless of whether we're
    # inside a loud sentence or a quiet stretch.
    smooth_win = max(3, int(150 / hop_ms))
    if smooth_win < n_frames:
        kernel = np.ones(smooth_win, dtype=np.float32) / smooth_win
        baseline = np.convolve(novelty, kernel, mode='same')
        novelty = np.clip(novelty - baseline, 0, None)

    # Local maxima above threshold. The `>=` on the left and `>` on the
    # right makes a plateau resolve to its leftmost frame deterministically.
    above = novelty >= threshold
    is_peak = np.zeros(n_frames, dtype=bool)
    is_peak[1:-1] = (
        (novelty[1:-1] >= novelty[:-2])
        & (novelty[1:-1] > novelty[2:])
        & above[1:-1]
    )
    candidates = np.where(is_peak)[0]
    if candidates.size == 0:
        return np.array([], dtype=np.float32)

    min_gap_frames = max(1, int(min_gap_ms / hop_ms))
    keep: list[int] = [int(candidates[0])]
    for c in candidates[1:]:
        c = int(c)
        if c - keep[-1] >= min_gap_frames:
            keep.append(c)
        elif novelty[c] > novelty[keep[-1]]:
            keep[-1] = c

    times = np.asarray(keep, dtype=np.float64) * (hop / float(samplerate))
    return times.astype(np.float32)
