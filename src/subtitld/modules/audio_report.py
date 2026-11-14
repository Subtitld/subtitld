"""What an audio export will be, for the export dialog's preview: its facts
(how long, in what format, how large, which files), its problems (speech
missing, sound files gone, clipping) and its loudness, measured as EBU
R128 / ITU-R BS.1770-4 measure it.

The facts and most problems are known at once from the subtitles. The
loudness needs the mix itself, rendered as the export renders it
(bounce): `measure_async` does that off the main thread.
"""

import os

import numpy as np

from subtitld.modules import session

# The loudness programs aim at, in LUFS, the quietest first: the points on
# the dashboard's scale.
TARGETS = (('atsc', -24.0), ('ebu', -23.0), ('podcast', -16.0), ('streaming', -14.0))
# The highest true peak most delivery specs allow before encoding to a
# lossy format.
PEAK_LIMIT = -1.0
# Bytes a second of each format, stereo at 48 kHz: 16-bit PCM; FLAC about
# half of it for speech with pauses; MP3 at LAME's -V2, about 190 kb/s.
_BYTES_PER_SECOND = {'WAV': 48000 * 2 * 2, 'FLAC': 48000 * 2 * 2 * 0.55, 'MP3': 190000 / 8}


def _playing(dubs):
    """The dub that plays: the first not muted (audioengine's rule)."""
    for index, dub in enumerate(dubs or []):
        if isinstance(dub, dict) and not dub.get('muted', index != 0):
            return dub
    return None


def _paths(dub):
    """The sound files a dub plays."""
    if dub.get('segments') is None:
        return [dub['path']] if dub.get('path') else []
    return [piece['path'] for piece in dub['segments'] if piece.get('path') and piece.get('type', 'audio') == 'audio']


def _safe(text):
    return ''.join(char if char.isalnum() or char in '-_' else '_' for char in str(text or 'A'))


def facts(mode, audio_format, include_background, segments, video, background=None, vocals=None, stem='export'):
    """What the export will write, known without rendering it: {'length':
    seconds, 'format', 'rate', 'channels', 'size': bytes (estimated, or
    None), 'files': [names]}."""
    segments = [s for s in segments or [] if isinstance(s, dict)]
    spoken = [(s, _playing(s.get('dubbing'))) for s in segments]
    spoken = [(s, dub) for s, dub in spoken if dub]
    ends = [float(dub.get('start', s.get('start', 0.0))) + max(0.0, float(s['end']) - float(s['start']))
            for s, dub in spoken]
    length = float((video or {}).get('duration') or 0.0) or max(ends + [0.0])
    extension = audio_format.lower()
    if mode in ('background_only', 'vocals_only'):
        source = background if mode == 'background_only' else vocals
        length = _length_of(source) or length
        files = [f'{stem}.{extension}']
    elif mode == 'stems':
        speakers = []
        for segment, _dub in spoken:
            speaker = str(segment.get('speaker') or 'A')
            if speaker not in speakers:
                speakers.append(speaker)
        files = [f'{stem}-{_safe(speaker)}.{extension}' for speaker in speakers] or [f'{stem}.{extension}']
    elif mode == 'clips':
        files = [f'{float(s["start"]):.3f}_{_safe(s.get("speaker", "A"))}.wav' for s, dub in spoken
                 if any(os.path.isfile(path) for path in _paths(dub))]
    else:
        files = [f'{stem}.{extension}']
    if mode == 'clips':
        size = sum(os.path.getsize(path) for _s, dub in spoken for path in _paths(dub)[:1] if os.path.isfile(path))
    elif audio_format == 'MP4':
        video_path = (video or {}).get('filepath')
        size = (os.path.getsize(video_path) if video_path and os.path.isfile(video_path) else 0) + \
            length * 192000 / 8
    else:
        size = length * _BYTES_PER_SECOND.get(audio_format, _BYTES_PER_SECOND['WAV']) * len(files)
    return {'length': length, 'format': 'WAV' if mode == 'clips' else audio_format, 'rate': 48000, 'channels': 2,
            'size': int(size), 'files': files}


def _length_of(path):
    if not path or not os.path.isfile(path):
        return 0.0
    try:
        import soundfile
        return float(soundfile.info(path).duration)
    except Exception:
        return 0.0


def problems(mode, segments):
    """The problems known without rendering: {'no_speech': subtitles with
    words and no dubbed speech, 'subtitles': how many have words,
    'files_gone': dubs whose sound files are gone}. Only for the modes that
    play the dub."""
    if mode not in ('mixdown', 'stems', 'clips'):
        return {}
    from subtitld.modules import markup
    worded = [s for s in segments or [] if isinstance(s, dict) and markup.plain(str(s.get('text') or '')).strip()]
    no_speech = sum(1 for s in worded if not _playing(s.get('dubbing')))
    gone = 0
    for segment in segments or []:
        dub = _playing(segment.get('dubbing')) if isinstance(segment, dict) else None
        if dub and any(not os.path.isfile(path) for path in _paths(dub)):
            gone += 1
    return {'no_speech': no_speech, 'subtitles': len(worded), 'files_gone': gone}


# Loudness (ITU-R BS.1770-4, EBU R128, EBU Tech 3342 for the range).

def _k_weighting(rate):
    """The K-weighting's two filters at `rate`, (b, a) each: the high shelf
    and the RLB high-pass, designed as libebur128 designs them, so that
    they match at every rate, not only 48 kHz."""
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = np.tan(np.pi * f0 / rate)
    vh = 10 ** (gain / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = (np.array([(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]),
             np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]))
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    highpass = (np.array([1.0, -2.0, 1.0]), np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]))
    return shelf, highpass


def _block_powers(weighted, rate, seconds, step):
    """The mean square of `weighted` (frames, channels), summed over the
    channels, over blocks `seconds` long every `step` seconds."""
    size, hop = int(round(seconds * rate)), max(1, int(round(step * rate)))
    if len(weighted) < size:
        return np.zeros(0)
    squares = np.concatenate([np.zeros((1, weighted.shape[1])), np.cumsum(weighted.astype(np.float64) ** 2, axis=0)])
    starts = np.arange(0, len(weighted) - size + 1, hop)
    return ((squares[starts + size] - squares[starts]) / size).sum(axis=1)


def _lufs(power):
    return -0.691 + 10 * np.log10(np.maximum(power, 1e-20))


def loudness(buffer, rate):
    """{'integrated': LUFS, 'peak': dBTP, 'range': LU} of `buffer`
    (frames, channels) at `rate`; None for a value silence has none of."""
    from scipy.signal import lfilter, resample_poly
    buffer = np.asarray(buffer, dtype=np.float32)
    if buffer.ndim == 1:
        buffer = buffer[:, None]
    weighted = buffer
    for b, a in _k_weighting(rate):
        weighted = lfilter(b, a, weighted, axis=0)
    # Integrated: 400 ms blocks, 75% overlap, gated at -70 LUFS and then
    # 10 LU under the loudness of what is left.
    blocks = _block_powers(weighted, rate, 0.4, 0.1)
    audible = blocks[_lufs(blocks) > -70.0]
    integrated = None
    if len(audible):
        gated = audible[_lufs(audible) > _lufs(audible.mean()) - 10.0]
        integrated = float(_lufs(gated.mean())) if len(gated) else None
    # Range: 3 s blocks, ten a second, gated at -70 LUFS and 20 LU under,
    # then the spread between the 10th and the 95th percentile.
    short = _block_powers(weighted, rate, 3.0, 0.1)
    short = short[_lufs(short) > -70.0]
    spread = None
    if len(short):
        kept = short[_lufs(short) > _lufs(short.mean()) - 20.0]
        if len(kept):
            values = _lufs(kept)
            spread = float(np.percentile(values, 95) - np.percentile(values, 10))
    # True peak: four times oversampled, ten seconds at a time, each piece
    # read with a margin either side and the margins' samples left out, where
    # the resampling filter rings against the piece's cut edges.
    peak = float(np.abs(buffer).max()) if len(buffer) else 0.0
    chunk, margin = rate * 10, 256
    for start in range(0, len(buffer), chunk):
        first = max(0, start - margin)
        piece = buffer[first:start + chunk + margin]
        if len(piece):
            up = np.abs(resample_poly(piece, 4, 1, axis=0))
            lead = (start - first) * 4
            keep = up[lead:lead + min(chunk, len(buffer) - start) * 4]
            if len(keep):
                peak = max(peak, float(keep.max()))
    true_peak = float(20 * np.log10(peak)) if peak > 0 else None
    return {'integrated': integrated, 'peak': true_peak, 'range': spread}


def render(mode, include_background, engine, video=None):
    """The sound the export will write, as one buffer, and its rate: the
    dubbed speeches mixed (with the background when included), or the
    background or the voices alone. The stems and the clips are measured
    together, as they would sound played at once."""
    from subtitld.modules import bounce
    if mode in ('background_only', 'vocals_only'):
        path = bounce._background_audio_path() if mode == 'background_only' else bounce._vocals_audio_path()
        if not path:
            return None, 0
        rate = engine.samplerate if engine else 48000
        return bounce._load_audio(path, rate), rate
    if engine is None:
        return None, 0
    rate = int(engine.samplerate)
    duration = float((video or session.VIDEO or {}).get('duration') or 0.0) or None
    tracks = list((getattr(engine, 'speaker_tracks', {}) or {}).values())
    buffer = engine.render_buffer(start=0.0, end=duration, samplerate=rate, tracks=tracks)
    if include_background:
        path = bounce._background_audio_path()
        if path:
            background = bounce._load_audio(path, rate)
            length = max(len(buffer), len(background))
            mixed = np.zeros((length, 2), dtype=np.float32)
            mixed[:len(buffer)] += buffer
            mixed[:len(background)] += background
            buffer = mixed
    return buffer, rate


# The threads measuring, kept until they are done: the dialog may close
# first.
_running = []


def measure_async(mode, include_background, engine, on_done):
    """`render` and `loudness` off the main thread; `on_done(result)` on the
    main thread, `result` None when there is nothing to measure."""
    from PySide6.QtCore import Qt, QThread, Signal

    class MeasureThread(QThread):
        done = Signal(object)

        def run(self):
            try:
                buffer, rate = render(mode, include_background, engine)
                self.done.emit(loudness(buffer, rate) if buffer is not None and len(buffer) else None)
            except Exception:
                self.done.emit(None)

    thread = MeasureThread()

    def _finished(result):
        if thread in _running:
            _running.remove(thread)
        thread.deleteLater()
        on_done(result)

    thread.done.connect(_finished, Qt.QueuedConnection)
    _running.append(thread)
    thread.start()
    return thread
