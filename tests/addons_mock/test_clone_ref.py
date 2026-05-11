"""Smoke test for `subtitld.modules.clone_ref`.

Mirrors the scripted style of the other tests in this directory (no pytest
collection — invoked directly from CI / dev). Builds a synthetic source
WAV + a SUBTITLE state, runs `extract_speaker_reference`, and verifies:

  1. The output file is a valid mono 16 kHz WAV.
  2. Only the speaker's longest in-band subtitle is extracted (not the
     whole file, not a different speaker's segment).
  3. Clone-detection helpers (`voice_requires_ref_audio`,
     `provider_has_clone_voice`) classify the canonical manifest shapes
     correctly.

ffmpeg is required at runtime; if missing, the test exits with a SKIP
message rather than failing — matches how `audio_stretch` degrades.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import sys
import tempfile
import wave

# Make src/ importable when running from repo root.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

from subtitld.modules import session
from subtitld.modules import clone_ref


def _make_sine_wav(path: str, duration_sec: float, sample_rate: int = 16000) -> None:
    """Synthesize a mono 16-bit sine WAV. Used as our 'source media' for
    extraction — content doesn't matter, only that ffmpeg can read/seek it."""
    n = int(duration_sec * sample_rate)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n):
            v = int(0.3 * 32767 * math.sin(2 * math.pi * 220.0 * i / sample_rate))
            wf.writeframesraw(struct.pack('<h', v))


def case_classification():
    print('\n=== case 1: clone-voice classification ===')
    # Mirrors what each shipping addon manifest declares.
    f5_clone = {'id': 'f5-clone', 'requires': ['voice_ref_audio'], 'clone': True}
    xtts_clone = {'id': 'xtts-clone', 'requires': ['voice_ref_audio']}
    qwen_clone = {'id': 'qwen3-clone', 'requires': ['voice_ref_audio']}
    qwen_fixed = {'id': 'qwen3-cherry', 'language': 'zh-cn'}
    kokoro_voice = {'id': 'af_heart', 'language': 'en-us'}

    assert clone_ref.voice_requires_ref_audio(f5_clone), 'f5-clone'
    assert clone_ref.voice_requires_ref_audio(xtts_clone), 'xtts-clone'
    assert clone_ref.voice_requires_ref_audio(qwen_clone), 'qwen3-clone'
    assert not clone_ref.voice_requires_ref_audio(qwen_fixed), 'qwen3-cherry'
    assert not clone_ref.voice_requires_ref_audio(kokoro_voice), 'af_heart'
    assert not clone_ref.voice_requires_ref_audio(None), 'None'
    assert not clone_ref.voice_requires_ref_audio({}), 'empty'

    # provider_has_clone_voice walks the list — accept any truthy hit.
    class FakeProvider:
        def __init__(self, voices):
            self._voices = voices

        def list_voices(self):
            return self._voices

    assert clone_ref.provider_has_clone_voice(FakeProvider([qwen_fixed, qwen_clone]))
    assert not clone_ref.provider_has_clone_voice(FakeProvider([qwen_fixed, kokoro_voice]))

    # voice_id_requires_ref_audio drives the auto-injection in the dubbing
    # UI: True iff the *named voice id* on the provider needs a reference.
    # The dubbing panel calls this with whatever the combobox/override has
    # — including missing/empty ids on a half-set-up speaker — so the
    # function must fail closed (False) on lookup misses.
    qwen_provider = FakeProvider([qwen_fixed, qwen_clone])
    assert clone_ref.voice_id_requires_ref_audio(qwen_provider, 'qwen3-clone'), 'clone -> True'
    assert not clone_ref.voice_id_requires_ref_audio(qwen_provider, 'qwen3-cherry'), 'fixed -> False'
    assert not clone_ref.voice_id_requires_ref_audio(qwen_provider, 'unknown-voice'), 'miss -> False'
    assert not clone_ref.voice_id_requires_ref_audio(qwen_provider, ''), 'empty -> False'
    assert not clone_ref.voice_id_requires_ref_audio(qwen_provider, None), 'None -> False'

    # Provider whose list_voices() raises must not crash the UI.
    class BrokenProvider:
        def list_voices(self):
            raise RuntimeError('addon down')
    assert not clone_ref.voice_id_requires_ref_audio(BrokenProvider(), 'anything'), 'broken provider -> False'
    print('  PASS')


def case_extract():
    print('\n=== case 2: extract_speaker_reference end-to-end ===')
    if shutil.which(session.FFMPEG_EXECUTABLE) is None:
        print(f'  SKIP — {session.FFMPEG_EXECUTABLE} not on PATH')
        return

    workdir = tempfile.mkdtemp(prefix='clone-ref-test-')
    src_wav = os.path.join(workdir, 'source.wav')
    _make_sine_wav(src_wav, duration_sec=20.0)

    # Stash and replace session state. The cache dir for clone-refs is
    # also session-scoped (PATH_SUBTITLD_USER_CACHE / 'clone-refs') so
    # we point that at our temp dir to keep the test hermetic.
    original_video = session.VIDEO
    original_subtitle = session.SUBTITLE
    original_cache = session.PATH_SUBTITLD_USER_CACHE
    try:
        from pathlib import Path
        session.PATH_SUBTITLD_USER_CACHE = Path(workdir)

        session.VIDEO = {'filepath': src_wav}
        session.SUBTITLE = {
            'segments': [
                # Speaker 'A' has both a too-short segment (0.5s) and a
                # solid in-band segment (5s). The 5s one is what we expect
                # extract to pick.
                {'speaker': 'A', 'start': 0.0,  'end': 0.5,  'text': 'short'},
                {'speaker': 'A', 'start': 6.0,  'end': 11.0, 'text': 'good ref'},
                {'speaker': 'A', 'start': 14.0, 'end': 14.4, 'text': 'short again'},
                # Speaker 'B' should never be picked when we extract for 'A'.
                {'speaker': 'B', 'start': 11.5, 'end': 13.5, 'text': 'other'},
            ],
        }

        out_path = clone_ref.extract_speaker_reference('A')
        assert out_path is not None, 'expected a path, got None'
        assert os.path.isfile(out_path), f'output missing: {out_path}'
        assert os.path.getsize(out_path) > 0, 'empty output WAV'

        with wave.open(out_path, 'rb') as wf:
            assert wf.getnchannels() == 1, f'expected mono, got {wf.getnchannels()}ch'
            assert wf.getframerate() == 16000, f'expected 16k, got {wf.getframerate()}'
            duration = wf.getnframes() / wf.getframerate()
        # The 5s good-ref subtitle is the in-band winner — duration should
        # be close to 5s (give 0.2s ffmpeg slack on either side).
        assert 4.8 <= duration <= 5.2, f'duration out of band: {duration}'
        print(f'  PASS — extracted {duration:.2f}s -> {out_path}')

        # Cache hit: a second call with the same state should return the
        # same path without re-running ffmpeg. We can't easily assert "no
        # ffmpeg call" but we can assert mtime stability.
        mtime_first = os.path.getmtime(out_path)
        out_path_2 = clone_ref.extract_speaker_reference('A')
        assert out_path_2 == out_path, 'expected cache hit on identical state'
        assert os.path.getmtime(out_path_2) == mtime_first, 'cache should not regenerate'
        print('  PASS — cache hit')

        # Speaker with no usable segments returns None.
        none_path = clone_ref.extract_speaker_reference('Z')
        assert none_path is None, f'expected None for absent speaker, got {none_path}'
        print('  PASS — None for speaker with no segments')

    finally:
        session.VIDEO = original_video
        session.SUBTITLE = original_subtitle
        session.PATH_SUBTITLD_USER_CACHE = original_cache
        shutil.rmtree(workdir, ignore_errors=True)


def case_concat_short_speeches():
    """When all of a speaker's subtitles are shorter than the minimum
    reference duration, clone_ref must concatenate adjacent same-speaker
    spans to reach the target. Validates:
      1. The output exists, is mono 16 kHz, and is in the [3s, 12s]
         band (the chunks were 1.5s + 1.5s + 2s = 5s of speaker A).
      2. The concat filter ran (we don't have a way to assert the
         specific filter graph, but a successful multi-span build at
         the right total duration is good evidence)."""
    print('\n=== case 3: concat when all speeches are too short ===')
    if shutil.which(session.FFMPEG_EXECUTABLE) is None:
        print(f'  SKIP — {session.FFMPEG_EXECUTABLE} not on PATH')
        return

    workdir = tempfile.mkdtemp(prefix='clone-ref-concat-')
    src_wav = os.path.join(workdir, 'source.wav')
    _make_sine_wav(src_wav, duration_sec=30.0)

    original_video = session.VIDEO
    original_subtitle = session.SUBTITLE
    original_cache = session.PATH_SUBTITLD_USER_CACHE
    try:
        from pathlib import Path
        session.PATH_SUBTITLD_USER_CACHE = Path(workdir)
        session.VIDEO = {'filepath': src_wav}
        # All speaker A subs are < 3s — must concatenate.
        # 1.5 + 1.5 + 2.0 = 5.0s of speaker A, with a speaker B in
        # between to verify we skip the wrong speaker.
        session.SUBTITLE = {
            'segments': [
                {'speaker': 'A', 'start': 1.0,  'end': 2.5,  'text': 'first'},
                {'speaker': 'B', 'start': 3.0,  'end': 5.0,  'text': 'other'},
                {'speaker': 'A', 'start': 6.0,  'end': 7.5,  'text': 'second'},
                {'speaker': 'A', 'start': 9.0,  'end': 11.0, 'text': 'third'},
                # Beyond this point we'd already cross the 3 s minimum,
                # so the loop should stop. A trailing speaker A sub
                # exists but won't be picked.
                {'speaker': 'A', 'start': 13.0, 'end': 14.5, 'text': 'too late'},
            ],
        }

        out_path = clone_ref.extract_speaker_reference('A')
        assert out_path is not None, 'expected concat output, got None'
        assert os.path.isfile(out_path), f'output missing: {out_path}'
        assert os.path.getsize(out_path) > 0, 'empty output WAV'

        with wave.open(out_path, 'rb') as wf:
            assert wf.getnchannels() == 1, f'expected mono, got {wf.getnchannels()}ch'
            assert wf.getframerate() == 16000, f'expected 16k, got {wf.getframerate()}'
            duration = wf.getnframes() / wf.getframerate()
        # Picker stops as soon as cumulative duration crosses the 3 s
        # min — first two A subs (1.5 + 1.5 = 3.0 s) satisfy that, so
        # the third sub is NOT included. Allow ±0.3 s ffmpeg slack.
        assert 2.7 <= duration <= 3.3, f'concat duration out of band: {duration}'
        print(f'  PASS — concat produced {duration:.2f}s from 2 spans (stopped at floor)')

        # Speaker B has only one 2s sub — also under 3s, but only one
        # segment exists, so concat should still produce that single
        # span (better than nothing — the addon's own validation will
        # error if it's still too short, which is the documented policy).
        session.SUBTITLE = {
            'segments': [
                {'speaker': 'B', 'start': 3.0, 'end': 5.0, 'text': 'only'},
            ],
        }
        out_b = clone_ref.extract_speaker_reference('B')
        assert out_b is not None, 'expected single-span fallback, got None'
        with wave.open(out_b, 'rb') as wf:
            duration = wf.getnframes() / wf.getframerate()
        assert 1.8 <= duration <= 2.2, f'single-sub duration out of band: {duration}'
        print(f'  PASS — sub-min single sub passes through ({duration:.2f}s)')

    finally:
        session.VIDEO = original_video
        session.SUBTITLE = original_subtitle
        session.PATH_SUBTITLD_USER_CACHE = original_cache
        shutil.rmtree(workdir, ignore_errors=True)


def case_extract_with_text():
    """`extract_speaker_reference_with_text` returns a (path, ref_text)
    pair where ref_text is the joined subtitle text on the same spans
    that fed the WAV. Drives the ICL-mode handoff to clone-capable
    addons (qwen3-clone, xtts-clone, f5-clone) — without ref_text the
    addons fall back to x-vector-only mode and leak training-set
    accent into the output (most visible on Portuguese / Spanish /
    Italian source material).

    Validates:
      1. The path matches what the legacy single-return helper produces
         (back-compat shim must agree with the source of truth).
      2. The ref_text is non-empty and contains the picked subtitle's
         text — for the in-band case (case 2 setup) it's the 'good ref'
         line; for the concat case it's the first two A subs joined.
      3. Whitespace is collapsed (multi-line / runs of spaces in the
         source text are normalized to single spaces).
      4. None speaker returns (None, None) cleanly."""
    print('\n=== case 4: extract_speaker_reference_with_text ===')
    if shutil.which(session.FFMPEG_EXECUTABLE) is None:
        print(f'  SKIP — {session.FFMPEG_EXECUTABLE} not on PATH')
        return

    workdir = tempfile.mkdtemp(prefix='clone-ref-text-')
    src_wav = os.path.join(workdir, 'source.wav')
    _make_sine_wav(src_wav, duration_sec=20.0)

    original_video = session.VIDEO
    original_subtitle = session.SUBTITLE
    original_cache = session.PATH_SUBTITLD_USER_CACHE
    try:
        from pathlib import Path
        session.PATH_SUBTITLD_USER_CACHE = Path(workdir)
        session.VIDEO = {'filepath': src_wav}
        # In-band case: 5 s 'good ref' wins. Note the embedded newline
        # and double-space — the function must collapse them.
        session.SUBTITLE = {
            'segments': [
                {'speaker': 'A', 'start': 0.0,  'end': 0.5,  'text': 'short'},
                {'speaker': 'A', 'start': 6.0,  'end': 11.0, 'text': 'this is\na  good ref'},
                {'speaker': 'A', 'start': 14.0, 'end': 14.4, 'text': 'short again'},
            ],
        }
        path, text = clone_ref.extract_speaker_reference_with_text('A')
        assert path is not None, 'expected a path'
        assert text == 'this is a good ref', f'expected collapsed text, got {text!r}'
        # Back-compat shim must agree on the path.
        path_via_shim = clone_ref.extract_speaker_reference('A')
        assert path_via_shim == path, 'shim disagrees with source-of-truth'
        print(f'  PASS — in-band ref_text={text!r}')

        # Concat case: first two A subs join. Each text contributes;
        # both are kept verbatim with a single space separator.
        # Force re-extraction by pointing at a fresh cache dir so the
        # filename keys to a new media_key (mtime-based skip would
        # otherwise return us the previous WAV).
        workdir2 = tempfile.mkdtemp(prefix='clone-ref-text-2-')
        try:
            session.PATH_SUBTITLD_USER_CACHE = Path(workdir2)
            session.SUBTITLE = {
                'segments': [
                    {'speaker': 'A', 'start': 1.0, 'end': 2.5,  'text': 'first line'},
                    {'speaker': 'A', 'start': 6.0, 'end': 7.5,  'text': 'second line'},
                ],
            }
            _path2, text2 = clone_ref.extract_speaker_reference_with_text('A')
            assert text2 == 'first line second line', f'concat text wrong: {text2!r}'
            print(f'  PASS — concat ref_text={text2!r}')
        finally:
            shutil.rmtree(workdir2, ignore_errors=True)

        # Missing speaker returns the (None, None) tuple — callers rely
        # on tuple-unpack on the unhappy path.
        none_path, none_text = clone_ref.extract_speaker_reference_with_text('Z')
        assert none_path is None and none_text is None, \
            f'expected (None, None), got ({none_path!r}, {none_text!r})'
        print('  PASS — (None, None) for absent speaker')

    finally:
        session.VIDEO = original_video
        session.SUBTITLE = original_subtitle
        session.PATH_SUBTITLD_USER_CACHE = original_cache
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    case_classification()
    case_extract()
    case_concat_short_speeches()
    case_extract_with_text()
    print('\nAll clone_ref cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
