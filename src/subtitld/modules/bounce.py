"""Bounce / render dub speeches to audio or video files."""

import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from subtitld.modules import session
from subtitld.modules import utils


def _safe_piece(name):
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in str(name)) or 'x'


def _separation_path(kind):
    """Path to the cached <kind>.flac (`background` or `vocals`) for the current video."""
    video_path = session.VIDEO.get('filepath') if isinstance(session.VIDEO, dict) else None
    if not video_path:
        return None
    key = utils.get_cache_key(video_path)
    if not key:
        return None
    path = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{key}_{kind}.flac')
    return path if os.path.exists(path) else None


def _background_audio_path():
    return _separation_path('background')


def _vocals_audio_path():
    return _separation_path('vocals')


def _load_audio(path, samplerate):
    """Load audio file, resample to `samplerate`, return (frames, 2) float32."""
    data, sr = sf.read(path, dtype='float32', always_2d=True)
    if data.shape[1] == 1:
        data = np.tile(data, (1, 2))
    if sr != samplerate:
        # Linear resample — cheap and sufficient for dub mixing.
        ratio = samplerate / sr
        out_len = int(round(data.shape[0] * ratio))
        src_idx = np.arange(out_len, dtype=np.float64) / ratio
        i0 = np.floor(src_idx).astype(np.int64)
        i1 = np.minimum(i0 + 1, data.shape[0] - 1)
        frac = (src_idx - i0).astype(np.float32)
        data = (1.0 - frac[:, None]) * data[i0] + frac[:, None] * data[i1]
    return data.astype(np.float32, copy=False)


def _write_audio(path, buffer, samplerate, audio_format):
    """Write `buffer` (frames, 2) to `path`.

    WAV / FLAC → direct soundfile write.
    MP3 → ffmpeg libmp3lame.
    """
    audio_format = audio_format.upper()
    if audio_format in ('WAV', 'FLAC'):
        sf.write(path, buffer, samplerate, subtype='PCM_16' if audio_format == 'WAV' else 'PCM_16')
        return

    if audio_format == 'MP3':
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            tmp_wav = tf.name
        try:
            sf.write(tmp_wav, buffer, samplerate, subtype='PCM_16')
            cmd = [
                session.FFMPEG_EXECUTABLE,
                '-hide_banner', '-loglevel', 'error',
                '-y', '-i', tmp_wav,
                '-codec:a', 'libmp3lame', '-qscale:a', '2',
                path,
            ]
            subprocess.run(cmd, startupinfo=session.STARTUPINFO, check=True)
        finally:
            try:
                os.unlink(tmp_wav)
            except Exception:
                pass
        return

    raise ValueError(f'Unsupported audio format: {audio_format}')


def _mux_mp4(video_path, audio_buffer, samplerate, output_path):
    """Copy the video stream from `video_path`, replace audio with AAC-encoded
    version of `audio_buffer`. Original audio/subtitle/data streams are dropped."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
        tmp_wav = tf.name
    try:
        sf.write(tmp_wav, audio_buffer, samplerate, subtype='PCM_16')
        cmd = [
            session.FFMPEG_EXECUTABLE,
            '-hide_banner', '-loglevel', 'error',
            '-y',
            '-an',                       # drop audio from the video input entirely
            '-i', video_path,
            '-i', tmp_wav,
            '-map', '0:v:0',             # only video stream from input 0
            '-map', '1:a:0',             # only audio from rendered WAV
            '-sn', '-dn',                # no subtitle, no data streams
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            output_path,
        ]
        subprocess.run(cmd, startupinfo=session.STARTUPINFO, check=True)
    finally:
        try:
            os.unlink(tmp_wav)
        except Exception:
            pass


def bounce(output_path, audio_format, mode, audio_engine=None,
           include_background=False):
    """Bounce dubs to disk.

    mode: 'mixdown' | 'stems' | 'clips'
    audio_format: 'WAV' | 'FLAC' | 'MP3' | 'MP4'
    """
    audio_format = audio_format.upper()
    samplerate = audio_engine.samplerate if audio_engine else 48000

    if mode == 'clips':
        folder = os.path.splitext(output_path)[0] + '_clips'
        os.makedirs(folder, exist_ok=True)
        for segment in session.SUBTITLE.get('segments', []):
            dubs = segment.get('dubbing') or []
            if not dubs:
                continue
            src = dubs[0].get('path')
            if not src or not os.path.isfile(src):
                continue
            base = f'{segment["start"]:.3f}_{_safe_piece(segment.get("speaker", "A"))}.wav'
            shutil.copy(src, os.path.join(folder, base))
        return

    if audio_engine is None:
        raise ValueError('audio_engine is required for non-clip bounce modes')

    duration = float(session.VIDEO.get('duration', 0.0))
    if duration <= 0.0:
        # Fall back to the latest dub endpoint
        for segment in session.SUBTITLE.get('segments', []):
            for dub in segment.get('dubbing') or []:
                try:
                    duration = max(duration, float(dub.get('start', 0.0)) + float(dub.get('end', 0.0)) - float(dub.get('start', 0.0)))
                except Exception:
                    pass

    bg_buffer = None
    if include_background:
        bg_path = _background_audio_path()
        if bg_path:
            bg_buffer = _load_audio(bg_path, samplerate)
            print(f'[bounce] background source: {bg_path} (frames={len(bg_buffer)}, peak={float(np.max(np.abs(bg_buffer))):.3f})')
        else:
            print('[bounce] include_background requested but no _background.flac found in cache')
    else:
        print('[bounce] background NOT included (checkbox unchecked)')

    def _apply_background(buffer):
        if bg_buffer is None:
            return buffer
        length = max(len(buffer), len(bg_buffer))
        padded = np.zeros((length, 2), dtype=np.float32)
        padded[:len(buffer)] += buffer
        padded[:len(bg_buffer)] += bg_buffer
        np.clip(padded, -1.0, 1.0, out=padded)
        return padded

    dub_tracks = list((getattr(audio_engine, 'speaker_tracks', {}) or {}).values())

    if mode in ('background_only', 'vocals_only'):
        src = _background_audio_path() if mode == 'background_only' else _vocals_audio_path()
        if not src:
            raise ValueError(f'No cached audio available for mode {mode}')
        buffer = _load_audio(src, samplerate)
        if audio_format == 'MP4':
            video = session.VIDEO.get('filepath')
            if not video:
                raise ValueError('No video loaded for MP4 mux')
            _mux_mp4(video, buffer, samplerate, output_path)
        else:
            _write_audio(output_path, buffer, samplerate, audio_format)
        return

    if mode == 'mixdown':
        buffer = audio_engine.render_buffer(start=0.0, end=duration, samplerate=samplerate, tracks=dub_tracks)
        buffer = _apply_background(buffer)
        if audio_format == 'MP4':
            video = session.VIDEO.get('filepath')
            if not video:
                raise ValueError('No video loaded for MP4 mux')
            _mux_mp4(video, buffer, samplerate, output_path)
        else:
            _write_audio(output_path, buffer, samplerate, audio_format)
        return

    if mode == 'stems':
        stem, ext = os.path.splitext(output_path)
        stem_tracks = getattr(audio_engine, 'speaker_tracks', {}) or {}
        if not stem_tracks:
            # No per-speaker tracks — just write a single mixdown under the stem name.
            buffer = audio_engine.render_buffer(start=0.0, end=duration, samplerate=samplerate, tracks=dub_tracks)
            buffer = _apply_background(buffer)
            _write_audio(output_path, buffer, samplerate, audio_format)
            return
        for speaker_name, track in stem_tracks.items():
            buffer = audio_engine.render_buffer(start=0.0, end=duration, samplerate=samplerate, tracks=[track])
            if include_background:
                buffer = _apply_background(buffer)
            speaker_path = f'{stem}-{_safe_piece(speaker_name)}{ext}'
            if audio_format == 'MP4':
                video = session.VIDEO.get('filepath')
                if not video:
                    raise ValueError('No video loaded for MP4 mux')
                _mux_mp4(video, buffer, samplerate, speaker_path)
            else:
                _write_audio(speaker_path, buffer, samplerate, audio_format)
        return

    raise ValueError(f'Unknown bounce mode: {mode}')
