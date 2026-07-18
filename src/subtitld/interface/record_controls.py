"""Record mode, driven from the player's record button.

The record button (next to Play) *arms* recording and its two internal
switchers pick the mode; the input device is chosen in the Audio ▸ Recording
tab. Once armed, recording follows playback — pressing Play records the mic
from the current playhead:

  * **transcript** — live STT (whisper.cpp offline, or whatever ASR provider is
    active) writes subtitles as you narrate. If a subtitle sits under the
    playhead when an utterance is transcribed, its text is filled in; otherwise
    a new subtitle is created using the detected phrase boundaries.
  * **wave** — on Stop the recording becomes a *dub* on the subtitle under the
    cursor (or the selected one), added to that subtitle's clip list.

All state lives in ``session.CONFIG['record']`` so it persists. The heavy
lifting is the already-tested :mod:`recorder` + :mod:`live_transcribe` engines;
this class only wires them to playback and the project.
"""

import os
import time
import logging
import secrets

from PySide6.QtCore import QObject, QTimer

from subtitld.modules import session
from subtitld.modules import recorder as recorder_mod
from subtitld.modules import live_transcribe
from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE


log = logging.getLogger(__name__)

MODE_TRANSCRIPT = 'transcript'
MODE_WAVE = 'audio'


def _config():
    return session.CONFIG.setdefault('record', {})


class RecordController(QObject):
    def __init__(self, host_window):
        super().__init__()
        self._host = host_window
        self._recorder = None
        self._live = None
        self._asr_provider = None
        self._draining_lives = []   # strong refs while queued subtitles drain
        self._base_position = 0.0
        self._recording = False
        self._status = 'idle'
        self._filled_targets = set()   # id()s of subtitles this take replaced
        # Live streaming (asr.stream) state.
        self._stream_provider = None
        self._stream_phrase_start = 0.0
        self._interim_text = ''

    # -- persisted state ---------------------------------------------------
    @property
    def armed(self):
        return bool(_config().get('armed', False))

    @armed.setter
    def armed(self, value):
        _config()['armed'] = bool(value)

    @property
    def mode(self):
        m = _config().get('mode', MODE_TRANSCRIPT)
        return m if m in (MODE_TRANSCRIPT, MODE_WAVE) else MODE_TRANSCRIPT

    @mode.setter
    def mode(self, value):
        _config()['mode'] = value if value in (MODE_TRANSCRIPT, MODE_WAVE) else MODE_TRANSCRIPT

    @property
    def device(self):
        return _config().get('device', None)

    @property
    def is_recording(self):
        return self._recording

    @property
    def status(self):
        return self._status

    @property
    def interim_text(self):
        """Un-committed live transcription text (shown separately from cues)."""
        return self._interim_text

    @property
    def level(self):
        return self._recorder.level if self._recorder is not None else 0.0

    @property
    def elapsed(self):
        return self._recorder.elapsed if self._recorder is not None else 0.0

    # -- lifecycle, tied to playback --------------------------------------
    def on_play(self):
        """Playback started — begin recording if armed and possible."""
        if not self.armed or self._recording:
            return
        if not recorder_mod.input_available():
            self._status = 'no-input'
            log.warning('Record: no audio input available; not recording')
            return
        base = float(session.SUBTITLE.get('position', 0) or 0)
        self._base_position = base
        self._filled_targets = set()

        chunk_cb = None
        if self.mode == MODE_TRANSCRIPT:
            # Prefer a live streaming engine (asr.stream) when the selected
            # engine offers one — it keeps its model warm and emits interim
            # text. The shared instance is used (not a clone) so the model
            # isn't reloaded per take; streaming uses its own signals so it
            # can't collide with the Import panel like the batch path can.
            shared = self._resolve_shared_asr_provider()
            if shared is not None and self._supports_streaming(shared):
                self._asr_provider = shared
                self._start_stream(shared, base)
                chunk_cb = self._stream_feed
            else:
                provider = self._resolve_asr_provider()
                self._asr_provider = provider
                if provider is None:
                    # No engine → recording the mic would just discard the audio.
                    # Surface why and stay idle so the user can configure one.
                    self._status = 'no-engine'
                    log.warning('Record: no ASR provider available for live transcription')
                    return
                self._live = live_transcribe.LiveTranscriber(
                    provider,
                    language=session.SUBTITLE.get('language', 'en-us'),
                    options=self._asr_options(provider),
                    samplerate=recorder_mod.SAMPLE_RATE,
                    base_offset=base,
                )
                self._live.subtitle_ready.connect(self._on_subtitle_ready)
                self._live.status_changed.connect(self._on_live_status)
                try:
                    provider.error.connect(self._on_provider_error)
                except Exception:
                    pass
                chunk_cb = self._live.push

        try:
            self._recorder = recorder_mod.AudioRecorder(
                self._output_path(), device=self.device, chunk_callback=chunk_cb)
            self._recorder.start()
            self._recording = True
            transcribing = self.mode == MODE_TRANSCRIPT and (self._live or self._stream_provider)
            self._status = 'transcribing' if transcribing else 'recording'
        except Exception as exc:
            self._status = 'error'
            log.exception('Record: failed to start recorder: %s', exc)
            self._recorder = None
            if self._live is not None:
                self._detach_provider_error()
                self._live.cleanup()
                self._live = None

    def on_pause(self):
        """Playback paused/stopped — finalize the recording."""
        if not self._recording:
            return
        rec = self._recorder
        self._recorder = None
        self._recording = False
        self._status = 'idle'
        wav = None
        if rec is not None:
            try:
                wav = rec.stop()   # stop capture first, then flush the engine
            except Exception:
                wav = None

        if self._stream_provider is not None:
            prov = self._stream_provider
            try:
                prov.stream_stop()   # add-on flushes → stream_finished cleans up
            except Exception:
                pass
            # Fallback: settle even if the terminal result never arrives.
            QTimer.singleShot(30000, lambda p=prov: self._on_stream_finished([])
                              if self._stream_provider is p else None)

        if self._live is not None:
            live = self._live
            self._live = None
            self._detach_provider_error()
            self._draining_lives.append(live)

            def _drain(_live=live):
                try:
                    _live.cleanup()
                except Exception:
                    pass
                try:
                    self._draining_lives.remove(_live)
                except ValueError:
                    pass

            live.status_changed.connect(
                lambda st, _live=live: _drain(_live) if st == 'done' else None)
            QTimer.singleShot(30000, lambda _live=live: _drain(_live))
            try:
                live.finish()
            except Exception:
                pass

        if self.mode == MODE_WAVE and wav and os.path.isfile(wav) and os.path.getsize(wav) > 0:
            self._attach_dub(wav, self._base_position)

    # -- transcript output -------------------------------------------------
    def _on_live_status(self, st):
        # Only reflect progress while we're actively recording; the drain-phase
        # 'done' shouldn't overwrite the idle state shown after Stop.
        if self._recording and st in ('transcribing', 'recording'):
            self._status = st

    def _on_provider_error(self, message):
        self._status = 'error'
        log.warning('Record: ASR provider error: %s', message)

    # -- live streaming (asr.stream) --------------------------------------
    def _start_stream(self, provider, base):
        self._stream_provider = provider
        self._stream_phrase_start = base
        self._interim_text = ''
        provider.stream_segment.connect(self._on_stream_segment)
        provider.stream_finished.connect(self._on_stream_finished)
        provider.stream_error.connect(self._on_provider_error)
        provider.stream_start(
            session.SUBTITLE.get('language', 'en-us'), self._asr_options(provider))

    def _stream_feed(self, chunk):
        """Recorder chunk callback (writer thread): float32 mono → int16 PCM →
        the streaming add-on. Writes are serialised inside the provider."""
        provider = self._stream_provider
        if provider is None:
            return
        try:
            import numpy as np
            pcm = (np.clip(np.asarray(chunk, dtype=np.float32), -1.0, 1.0)
                   * 32767.0).astype('<i2').tobytes()
            provider.stream_feed(pcm)
        except Exception:
            pass

    def _on_stream_segment(self, seg, final):
        """A streaming segment arrived (main thread). Interim text is shown
        separately; only committed (final) segments become subtitles."""
        text = str(seg.get('text', '')).strip()
        if not final:
            self._interim_text = text
            return
        self._interim_text = ''
        if not text:
            return
        # The add-on doesn't timestamp fed audio, so bound the cue by the
        # playhead: [previous phrase end, current playhead].
        pos = float(session.SUBTITLE.get('position', self._stream_phrase_start) or 0)
        start = self._stream_phrase_start
        end = max(pos, start + 0.3)
        self._stream_phrase_start = pos
        self._on_subtitle_ready({'start': start, 'end': end, 'text': text, 'speaker': 'A'})

    def _on_stream_finished(self, segments):
        # Committed segments were already placed as they streamed in; this just
        # settles the session (idempotent — also used as the timeout fallback).
        prov = self._stream_provider
        self._stream_provider = None
        self._interim_text = ''
        if prov is not None:
            self._disconnect_stream(prov)
        self._refresh_ui()

    def _disconnect_stream(self, provider):
        for sig, slot in (
            (provider.stream_segment, self._on_stream_segment),
            (provider.stream_finished, self._on_stream_finished),
            (provider.stream_error, self._on_provider_error),
        ):
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def _on_subtitle_ready(self, seg):
        """A live utterance was transcribed.

        If the utterance falls inside an existing subtitle, replace that
        subtitle's text and leave its timing alone (the first utterance of the
        take clears the old text; later ones for the same subtitle append). If
        it falls in a gap, create a new subtitle bounded by the detected speech
        (end = when the speaker paused), clamped so it can't overlap a
        neighbour."""
        text = str(seg.get('text', '')).strip()
        if not text:
            return
        start = float(seg.get('start', 0.0))
        end = float(seg.get('end', start))
        mid = (start + end) / 2.0

        target = self._subtitle_at(mid)
        if target is not None:
            tid = id(target)
            if tid in self._filled_targets:
                existing = str(target.get('text', '')).strip()
                target['text'] = (existing + ' ' + text).strip() if existing else text
            else:
                target['text'] = text          # replace pre-existing text
                self._filled_targets.add(tid)
        else:
            start, end = self._clamp_to_gap(start, end)
            if end <= start:
                return
            segments = session.SUBTITLE.setdefault('segments', [])
            speaker = seg.get('speaker', 'A')
            segments.append({
                'start': start, 'end': end, 'text': text, 'speaker': speaker,
            })
            segments.sort(key=lambda s: float(s.get('start', 0.0)))
            if speaker not in session.SPEAKERS:
                session.SPEAKERS[speaker] = {'image': None}

        self._refresh_ui()
        session.set_unsaved()

    # -- wave output: dub on the subtitle under the cursor -----------------
    def _attach_dub(self, wav, base):
        """Attach a recorded take as a dub.

        The *cue* (subtitle) must never overwrite a following subtitle: if the
        take runs past where the next subtitle begins, the cue ends exactly at
        that start. The *clip* keeps its natural length (never time-stretched)
        and is allowed to overflow past the cue into the next subtitle's time.
        """
        from subtitld.modules import dub_clip
        rec_dur = dub_clip._file_duration_seconds(wav) or 0.0
        natural_end = base + rec_dur if rec_dur > 0 else None

        # Where the next subtitle begins — the hard cap for the cue (not the clip).
        nxt = self._next_subtitle_start(base)
        cap = nxt if nxt is not None else float('inf')

        subtitle = self._target_subtitle(base)
        if subtitle is None:
            # Nothing under the cursor and nothing selected — don't lose the
            # take: make a subtitle spanning it, clamped to the next subtitle.
            from subtitld.modules import subtitles as subtitles_mod
            end = min(natural_end if natural_end is not None else base + 3.0, cap)
            subtitles_mod.add_subtitle(position=base, duration=max(0.1, end - base), text='')
            subtitle = next(
                (s for s in session.SUBTITLE.get('segments', [])
                 if abs(float(s.get('start', 0.0)) - base) < 0.05), None)
            if subtitle is None:
                return
            session.SUBTITLE['selected'] = subtitle
        elif natural_end is not None:
            # Grow the existing cue to cover the take, capped at the next
            # subtitle's start, but never shorten it.
            subtitle['end'] = max(float(subtitle.get('end', base)),
                                  min(natural_end, cap))

        dub_end = natural_end if natural_end is not None else float(subtitle.get('end', base))
        dubs = subtitle.setdefault('dubbing', [])
        dubs.insert(0, {
            'engine': 'recording',
            'path': wav,
            'raw_path': wav,
            'start': base,
            'end': dub_end,       # start + file duration → never stretched
            'uid': secrets.token_hex(4),
            'voice': '',
            'rate': 0,
            'pitch': 0,
        })
        subtitle['locked'] = False
        try:
            self._host.preview_panel_player._audio_device.sync_subtitle_dubs(
                session.SUBTITLE['segments'])
        except Exception:
            pass
        self._refresh_ui()
        session.set_unsaved()

    def _next_subtitle_start(self, base):
        """Start time of the earliest subtitle beginning after ``base``, or None."""
        starts = [float(s.get('start', 0.0))
                  for s in session.SUBTITLE.get('segments', [])
                  if isinstance(s, dict) and float(s.get('start', 0.0)) > base]
        return min(starts) if starts else None

    # -- helpers -----------------------------------------------------------
    def _subtitle_at(self, position):
        """The subtitle whose time range contains ``position`` (seconds), if any."""
        for s in session.SUBTITLE.get('segments', []):
            if isinstance(s, dict) and \
                    float(s.get('start', 0.0)) <= position <= float(s.get('end', 0.0)):
                return s
        return None

    def _clamp_to_gap(self, start, end):
        """Trim a new-subtitle range so it can't overlap existing subtitles:
        push the start past any subtitle it lands inside, and cap the end at the
        next subtitle's start."""
        segs = session.SUBTITLE.get('segments', [])
        for s in segs:
            s_start = float(s.get('start', 0.0))
            s_end = float(s.get('end', 0.0))
            if s_start <= start < s_end:
                start = s_end
        next_starts = [float(s.get('start', 0.0)) for s in segs
                       if float(s.get('start', 0.0)) >= start]
        if next_starts:
            end = min(end, min(next_starts))
        return start, end

    def _target_subtitle(self, position):
        """Wave-mode target: the subtitle under the cursor, else the selected one."""
        under = self._subtitle_at(position)
        if under is not None:
            return under
        sel = session.SUBTITLE.get('selected')
        return sel if isinstance(sel, dict) else None

    def _refresh_ui(self):
        """Rebuild the subtitle list + timeline the way the rest of the app does
        after a subtitle mutation (a bare widget.update() only repaints). The
        list is refreshed directly rather than via ``left_panel.update`` so it
        updates even when the Audio tab is the visible left panel."""
        host = self._host
        try:
            from subtitld.interface import timeline
            timeline.update(host)
        except Exception:
            pass
        try:
            host.timeline_widget.update()
        except Exception:
            pass
        try:
            from subtitld.interface import left_panel_subtitleslist
            left_panel_subtitleslist.update(host)
        except Exception:
            pass

    def _output_path(self):
        base = os.path.dirname(str(session.PATH_SUBTITLD_DATA_PROXY))
        rec_dir = os.path.join(base, 'recordings')
        os.makedirs(rec_dir, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        return os.path.join(rec_dir, f'recording_{stamp}_{secrets.token_hex(2)}.wav')

    def _resolve_asr_provider(self):
        """Return a PRIVATE provider instance for live transcription.

        The registered providers are singletons shared with the Import panel,
        whose ``transcript_finished`` handler replaces *all* subtitles with the
        finished segments (left_panel_import._on_finished). If we drove the
        shared instance, every live utterance would wipe the project and leave
        one cue. A fresh instance of the same class keeps our per-utterance
        signals private; config is read from ``session.CONFIG`` / passed via
        options, so the clone behaves identically."""
        base = self._resolve_shared_asr_provider()
        if base is None:
            return None
        try:
            return type(base)()
        except Exception:
            log.exception('Record: could not clone ASR provider; falling back to shared')
            return base

    def _resolve_shared_asr_provider(self):
        # 1. Explicit engine chosen in the Audio ▸ Recording tab (only if it's
        #    still usable — otherwise fall through to a working engine).
        try:
            chosen = _config().get('engine', None)
            if chosen:
                from subtitld.modules import addons
                provider = addons.get_manager().get(chosen)
                if provider is not None and TASK_ASR_TRANSCRIBE in getattr(provider, 'tasks', []) \
                        and getattr(provider, 'id', '') != 'import' \
                        and self._provider_available(provider):
                    return provider
        except Exception:
            pass
        # 2. Whatever the Import panel currently has selected.
        try:
            current = self._host.global_panel_import_tabwidget.currentWidget()
            provider = getattr(current, 'provider', None)
            if provider is not None and TASK_ASR_TRANSCRIBE in getattr(provider, 'tasks', []) \
                    and getattr(provider, 'id', '') != 'import' \
                    and self._provider_available(provider):
                return provider
        except Exception:
            pass
        # 3. First usable ASR provider.
        try:
            from subtitld.modules import addons
            for provider in addons.get_manager().providers_for_task(TASK_ASR_TRANSCRIBE):
                if getattr(provider, 'id', '') != 'import' and self._provider_available(provider):
                    return provider
        except Exception:
            pass
        return None

    @staticmethod
    def _provider_available(provider):
        try:
            return provider.is_available()
        except Exception:
            return True

    @staticmethod
    def _supports_streaming(provider):
        try:
            return bool(provider.supports_streaming())
        except Exception:
            return False

    def _detach_provider_error(self):
        if self._asr_provider is not None:
            try:
                self._asr_provider.error.disconnect(self._on_provider_error)
            except Exception:
                pass
            self._asr_provider = None

    def _asr_options(self, provider):
        try:
            return session.CONFIG.get('transcription', {}).get(
                'engine_options', {}).get(getattr(provider, 'id', ''), {})
        except Exception:
            return {}

    def shutdown(self):
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        if self._stream_provider is not None:
            prov = self._stream_provider
            self._stream_provider = None
            try:
                prov.stream_stop()
            except Exception:
                pass
            self._disconnect_stream(prov)
        self._detach_provider_error()
        if self._live is not None:
            try:
                self._live.cleanup()
            except Exception:
                pass
            self._live = None
        for live in list(self._draining_lives):
            try:
                live.cleanup()
            except Exception:
                pass
        self._draining_lives.clear()
