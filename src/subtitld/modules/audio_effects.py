"""Real-time audio effects (EQ, compressor, gate) for the mixer.

Applied per-Track inside :meth:`audioengine.Track.read` (right before the track
gain), so the background-music track, the vocals track, and each speaker's dub
track can each carry their own effect chain. An effect can be limited to a
timeline range.

Effect spec — a JSON-serialisable dict stored in the per-project effects model:

    {
      'id': str,                     # stable uid
      'type': 'eq' | 'compressor' | 'gate',
      'enabled': bool,
      'target': {'kind': 'background'|'voice'|'speaker', 'speaker': str|None},
      'range': None | [start_sec, end_sec],   # timeline seconds; None = always
      'params': { ... type-specific ... },
    }

Processors are stateful — IIR filter / envelope state is carried across blocks —
and process one contiguous ``(frames, 2)`` float32 block **in place**. scipy
supplies the vectorised IIR; if it's ever unavailable the effects degrade to
no-ops (guarded import) so the app keeps running.
"""
from __future__ import annotations

import copy
import json
import math
import secrets

import numpy as np

try:
    from scipy import signal as _sig
except Exception:      # pragma: no cover - scipy is a declared dep; guard anyway
    _sig = None

SAMPLERATE_DEFAULT = 48000
EFFECT_TYPES = ('eq', 'compressor', 'gate')


def scipy_available() -> bool:
    return _sig is not None


# --------------------------------------------------------------------------- #
# Effect model helpers                                                         #
# --------------------------------------------------------------------------- #
def default_params(effect_type: str) -> dict:
    if effect_type == 'eq':
        return {'bands': [
            {'type': 'lowshelf', 'freq': 120.0, 'gain': 0.0, 'q': 0.707},
            {'type': 'peak', 'freq': 1000.0, 'gain': 0.0, 'q': 1.0},
            {'type': 'highshelf', 'freq': 8000.0, 'gain': 0.0, 'q': 0.707},
        ]}
    if effect_type == 'compressor':
        return {'threshold_db': -18.0, 'ratio': 3.0, 'attack_ms': 10.0,
                'release_ms': 120.0, 'makeup_db': 0.0}
    if effect_type == 'gate':
        return {'threshold_db': -45.0, 'attack_ms': 2.0, 'release_ms': 120.0}
    return {}


def new_effect(effect_type: str, target_kind: str = 'background', speaker=None) -> dict:
    return {
        'id': secrets.token_hex(4),
        'type': effect_type,
        'enabled': True,
        'target': {'kind': target_kind, 'speaker': speaker},
        'range': None,
        'params': default_params(effect_type),
    }


def specs_for_target(specs, kind: str, speaker=None) -> list:
    """The enabled-or-not specs whose target matches (kind[, speaker])."""
    out = []
    for s in specs or []:
        tgt = s.get('target') or {}
        if tgt.get('kind') != kind:
            continue
        if kind == 'speaker' and tgt.get('speaker') != speaker:
            continue
        out.append(s)
    return out


def _specs_key(specs) -> str:
    try:
        return json.dumps(specs, sort_keys=True, default=str)
    except Exception:
        return repr(specs)


# --------------------------------------------------------------------------- #
# EQ — RBJ biquads via stateful scipy sosfilt                                  #
# --------------------------------------------------------------------------- #
def _biquad_sos(band: dict, fs: float) -> list:
    """One RBJ cookbook biquad as a normalised SOS row [b0,b1,b2,1,a1,a2]."""
    btype = band.get('type', 'peak')
    f0 = min(max(float(band.get('freq', 1000.0)), 10.0), fs * 0.45)
    gain_db = float(band.get('gain', 0.0))
    q = float(band.get('q', 1.0)) or 1.0

    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * q)

    if btype == 'lowshelf':
        s = 2.0 * math.sqrt(A) * alpha
        b0 = A * ((A + 1) - (A - 1) * cw + s)
        b1 = 2.0 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - s)
        a0 = (A + 1) + (A - 1) * cw + s
        a1 = -2.0 * ((A - 1) + (A + 1) * cw)
        a2 = (A + 1) + (A - 1) * cw - s
    elif btype == 'highshelf':
        s = 2.0 * math.sqrt(A) * alpha
        b0 = A * ((A + 1) + (A - 1) * cw + s)
        b1 = -2.0 * A * ((A - 1) + (A + 1) * cw)
        b2 = A * ((A + 1) + (A - 1) * cw - s)
        a0 = (A + 1) - (A - 1) * cw + s
        a1 = 2.0 * ((A - 1) - (A + 1) * cw)
        a2 = (A + 1) - (A - 1) * cw - s
    else:  # peak
        b0 = 1 + alpha * A
        b1 = -2.0 * cw
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2.0 * cw
        a2 = 1 - alpha / A

    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


class EQProcessor:
    def __init__(self, params: dict, fs: float):
        self.fs = fs
        rows = []
        for band in params.get('bands', []):
            # A 0 dB peak/shelf is a no-op — skip it to save a section.
            if band.get('type', 'peak') in ('peak', 'lowshelf', 'highshelf') \
                    and abs(float(band.get('gain', 0.0))) < 1e-3:
                continue
            rows.append(_biquad_sos(band, fs))
        if not rows or _sig is None:
            self.sos = None
            self.zi = None
            return
        self.sos = np.array(rows, dtype=np.float64)
        zi = _sig.sosfilt_zi(self.sos)                 # (n_sections, 2)
        self.zi = np.repeat(zi[:, :, None], 2, axis=2)  # (n_sections, 2, 2) stereo

    def process(self, block: np.ndarray) -> None:
        if self.sos is None or _sig is None or block.shape[0] == 0:
            return
        y, self.zi = _sig.sosfilt(self.sos, block, axis=0, zi=self.zi)
        block[:] = y.astype(block.dtype, copy=False)


# --------------------------------------------------------------------------- #
# Dynamics — decimated peak envelope follower (compressor + gate share it)     #
# --------------------------------------------------------------------------- #
def _smooth_coeff(time_ms: float, fs: float) -> float:
    t = max(float(time_ms), 0.05) / 1000.0
    return math.exp(-1.0 / (t * fs))


class _Envelope:
    """Peak follower with separate attack/release, decimated for speed. Returns
    a per-sample linear envelope the size of the block; state carries across
    blocks so there's no discontinuity at block boundaries."""

    def __init__(self, fs: float, decim: int = 16):
        self.fs = fs
        self.decim = max(1, int(decim))
        self.env = 0.0

    def follow(self, detector: np.ndarray, attack_ms: float, release_ms: float) -> np.ndarray:
        n = detector.shape[0]
        if n == 0:
            return detector
        dec = self.decim
        pad = (-n) % dec
        det = np.concatenate([detector, np.zeros(pad, detector.dtype)]) if pad else detector
        # Peak within each decimation window (don't miss transients).
        d = det.reshape(-1, dec).max(axis=1)
        fs_dec = self.fs / dec
        att = _smooth_coeff(attack_ms, fs_dec)
        rel = _smooth_coeff(release_ms, fs_dec)

        env = self.env
        out = np.empty(d.shape[0], dtype=np.float64)
        for i in range(d.shape[0]):
            x = float(d[i])
            c = att if x > env else rel
            env = c * env + (1.0 - c) * x
            out[i] = env
        self.env = env

        # Upsample the decimated envelope back to per-sample (linear interp).
        base = np.arange(d.shape[0]) * dec
        return np.interp(np.arange(n), base, out)


def _peak_detector(block: np.ndarray) -> np.ndarray:
    return np.abs(block).max(axis=1)   # (frames,) peak across channels


class CompressorProcessor:
    def __init__(self, params: dict, fs: float):
        self.fs = fs
        self.threshold_db = float(params.get('threshold_db', -18.0))
        self.ratio = max(float(params.get('ratio', 3.0)), 1.0)
        self.attack_ms = float(params.get('attack_ms', 10.0))
        self.release_ms = float(params.get('release_ms', 120.0))
        self.makeup_db = float(params.get('makeup_db', 0.0))
        self._env = _Envelope(fs)

    def process(self, block: np.ndarray) -> None:
        if block.shape[0] == 0:
            return
        env = self._env.follow(_peak_detector(block), self.attack_ms, self.release_ms)
        env_db = 20.0 * np.log10(env + 1e-9)
        over = env_db - self.threshold_db
        gain_db = np.where(over > 0.0, -over * (1.0 - 1.0 / self.ratio), 0.0) + self.makeup_db
        gain = np.power(10.0, gain_db / 20.0).astype(block.dtype)
        block *= gain[:, None]


class GateProcessor:
    def __init__(self, params: dict, fs: float):
        self.fs = fs
        self.threshold_db = float(params.get('threshold_db', -45.0))
        self.attack_ms = float(params.get('attack_ms', 2.0))
        self.release_ms = float(params.get('release_ms', 120.0))
        self._level = _Envelope(fs)     # fast level detector
        self._gain = _Envelope(fs)      # smooth the open/close so it doesn't click

    def process(self, block: np.ndarray) -> None:
        if block.shape[0] == 0:
            return
        level = self._level.follow(_peak_detector(block), 1.0, 5.0)
        level_db = 20.0 * np.log10(level + 1e-9)
        target = (level_db > self.threshold_db).astype(np.float64)   # 1 open / 0 closed
        gain = self._gain.follow(target, self.attack_ms, self.release_ms).astype(block.dtype)
        block *= gain[:, None]


# --------------------------------------------------------------------------- #
# Chain                                                                        #
# --------------------------------------------------------------------------- #
def build_processor(spec: dict, fs: float):
    t = spec.get('type')
    params = spec.get('params') or {}
    if t == 'eq':
        p = EQProcessor(params, fs)
        return p if p.sos is not None else None
    if t == 'compressor':
        return CompressorProcessor(params, fs)
    if t == 'gate':
        return GateProcessor(params, fs)
    return None


class EffectChain:
    """Ordered stateful processors for one Track. Rebuilt (fresh state) only
    when the spec list actually changes, so steady-state playback keeps its
    filter/envelope state."""

    def __init__(self, specs, fs: float = SAMPLERATE_DEFAULT):
        self.fs = fs
        self._specs_key = object()   # sentinel != any real key
        self._entries = []           # list[(spec, processor)]
        self.rebuild(specs)

    def rebuild(self, specs) -> None:
        key = _specs_key(specs)
        if key == self._specs_key:
            return
        self._specs_key = key
        entries = []
        for spec in specs or []:
            if not spec.get('enabled', True):
                continue
            proc = build_processor(spec, self.fs)
            if proc is not None:
                entries.append((spec, proc))
        self._entries = entries

    def __bool__(self):
        return bool(self._entries)

    def process(self, block: np.ndarray, playhead: float, samplerate: float) -> None:
        if not self._entries or block is None or block.shape[0] == 0:
            return
        frames = block.shape[0]
        block_start = float(playhead)
        block_end = block_start + frames / float(samplerate)
        for spec, proc in self._entries:
            rng = spec.get('range')
            if not rng:
                proc.process(block)
                continue
            s, e = float(rng[0]), float(rng[1])
            if e <= block_start or s >= block_end:
                continue   # block entirely outside the effect's range
            i0 = max(0, int(round((s - block_start) * samplerate)))
            i1 = min(frames, int(round((e - block_start) * samplerate)))
            if i1 > i0:
                proc.process(block[i0:i1])   # numpy view → mutates block in place


# --------------------------------------------------------------------------- #
# Per-project persistence + engine sync                                        #
# --------------------------------------------------------------------------- #
def _project_key() -> str:
    from subtitld.modules import session
    from subtitld.modules.utils import get_cache_key
    path = session.VIDEO.get('filepath') or session.SUBTITLE.get('filepath') or ''
    return get_cache_key(path) if path else '_no_project'


def load_effects() -> list:
    """The current project's saved effect specs, as independent copies the UI
    can edit freely."""
    from subtitld.modules import session
    store = session.CONFIG.get('audio_effects', {}) or {}
    return copy.deepcopy(list(store.get(_project_key(), [])))


def save_effects(specs) -> None:
    """Persist the effect specs for the current project into the global config
    (keyed by the project's cache key, since USF/USFX don't carry arbitrary
    per-project blobs)."""
    from subtitld.modules import session
    store = session.CONFIG.setdefault('audio_effects', {})
    store[_project_key()] = copy.deepcopy(list(specs or []))


def apply_to_engine(engine, specs) -> None:
    """Push specs into the audio engine (rebuilds every track's chain).
    Tolerates a missing/None engine."""
    if engine is None:
        return
    try:
        engine.set_audio_effects(specs)
    except Exception:
        pass
