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
import re
import time
import bisect
import logging
import secrets

from PySide6.QtCore import QObject, Qt, QTimer

from subtitld.modules import live_peaks
from subtitld.modules import session
from subtitld.modules import recorder as recorder_mod
from subtitld.modules import live_transcribe
from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE


log = logging.getLogger(__name__)

MODE_TRANSCRIPT = 'transcript'
MODE_WAVE = 'audio'

# How long, after Stop, a placeholder may be drawn as "waiting for text".
# When it expires the subtitle STAYS (empty); only the pending look goes.
_PENDING_TIMEOUT_MS = 30000
# Narrower than this, a cue squeezed between neighbours is not worth a subtitle.
_MIN_PLACEHOLDER = 0.1
# A sentence cannot have started after the last interim that preceded its
# final (plus this much latency).
_STREAM_CUTOFF_SLACK = 0.75
# The VAD's forced cut (a monologue split at max length) leaves no gap; a
# cut at a pause always does.
_FORCED_CUT_GAP = 0.05


def _config():
    return session.CONFIG.setdefault('record', {})


def _words(text):
    return re.findall(r"[\w']+", (text or '').lower())


def _extends(new, old):
    """Is interim `new` the sentence of interim `old`, grown by more words?
    Only its first few words are compared: the engine revises the rest."""
    a, b = _words(old), _words(new)
    k = min(len(a), 3)
    return k > 0 and len(b) > len(a) and b[:k] == a[:k]


def asr_providers():
    """ASR engines that are registered *and* usable right now.

    A provider that still needs configuration — a cloud key, say — reports
    `is_available()` False and is hidden until it is set up. The `import`
    pseudo-engine is excluded: its `transcribe()` is a no-op, so counting it
    would make "an engine is installed" true when nothing can actually
    transcribe.

    Lives here rather than in the Audio panel because the record button needs
    the same answer, and two copies of this filter would drift.
    """
    try:
        from subtitld.modules import addons
        from subtitld.modules.addons.provider import TASK_ASR_TRANSCRIBE
        out = []
        for p in addons.get_manager().providers_for_task(TASK_ASR_TRANSCRIBE):
            if getattr(p, 'id', '') == 'import':
                continue
            try:
                if not p.is_available():
                    continue
            except Exception:
                pass
            out.append(p)
        return out
    except Exception:
        return []


# Cached because `mode` is read on paint-adjacent paths; walking the provider
# registry there would cost a manager lock per frame. Invalidated whenever the
# registered set changes (AddonManager.providers_changed).
_ASR_ANY = None
_ASR_WATCHING = False


def any_asr_provider():
    """True when at least one engine could actually transcribe."""
    global _ASR_ANY
    if _ASR_ANY is None:
        _watch_providers()
        _ASR_ANY = bool(asr_providers())
    return _ASR_ANY


def _watch_providers():
    """Invalidate the cache whenever the registered provider set changes.

    Connected here, on first use, rather than left to whichever UI module
    happens to load first: anything that reads `mode` before that connection
    existed would otherwise cache a stale answer for the whole session.
    """
    global _ASR_WATCHING
    if _ASR_WATCHING:
        return
    try:
        from subtitld.modules import addons
        addons.get_manager().providers_changed.connect(invalidate_asr_cache)
        _ASR_WATCHING = True
    except Exception:
        log.debug('record: could not watch provider changes', exc_info=True)


def invalidate_asr_cache():
    """Forget the cached answer — call when add-ons are installed/removed."""
    global _ASR_ANY
    _ASR_ANY = None


class RecordController(QObject):
    def __init__(self, host_window):
        super().__init__()
        self._host = host_window
        self._recorder = None
        self._live = None
        # In-progress take preview (see modules/live_peaks + timeline).
        self.live_take = None
        self._live_buf = None
        self._live_timer = None
        self._live_seq = 0        # identity for backstop timers
        self._asr_provider = None
        self._draining_lives = []   # strong refs while queued subtitles drain
        self._drain_backstops = {}  # id(live) -> generation of its live backstop
        self._drain_gen = 0
        self._base_position = 0.0
        self._recording = False
        self._status = 'idle'
        # Transcript placeholders (see "placeholders" below).
        self._take_token = None       # prefixes placeholder keys of the current take
        self._take_history = None     # undo snapshot the current run of edits belongs to
        self._ph = {}                 # key -> placeholder record
        self._ph_seq = 0
        self._uid_rec = {}            # batch utterance uid -> placeholder record
        self.pending_keys = set()     # keys drawn as "waiting for text" (timeline reads it)
        # Live streaming (asr.stream) state, see _start_stream.
        self._streams = {}            # n -> session, until it settles
        self._stream = None           # the session being recorded
        self._stream_provider = None  # where recorded audio is fed
        self._stream_seq = 0
        self._by_sid = {}             # provider stream id -> session
        self._legacy_stream = None    # the session untagged events belong to
        self._tagged_providers = []
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
        """The EFFECTIVE record mode.

        Falls back to audio when no transcription engine is installed, because
        transcript mode would otherwise arm, play, and produce nothing at all.
        Deliberately computed rather than written back: the stored preference
        stays 'transcript', so installing an engine restores the user's choice
        without them having to set it again.
        """
        m = _config().get('mode', MODE_TRANSCRIPT)
        if m not in (MODE_TRANSCRIPT, MODE_WAVE):
            m = MODE_TRANSCRIPT
        if m == MODE_TRANSCRIPT and not any_asr_provider():
            return MODE_WAVE
        return m

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
        self._take_token = secrets.token_hex(3)
        self._take_history = None     # each take is its own undo step
        # Records are remembered per take, and for as long as anything may
        # still answer for them.
        alive = {st['token'] for st in self._streams.values()}
        self._ph = {k: r for k, r in self._ph.items()
                    if r['outstanding'] or r['take'] in alive}

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
                live, token = self._live, self._take_token
                live.utterance_sealed.connect(
                    lambda info, _t=token: self._on_utterance_sealed(info, _t))
                live.utterance_done.connect(
                    lambda uid, segs, _l=live: self._on_utterance_done(uid, segs, _l))
                live.utterance_failed.connect(
                    lambda uid, msg, _l=live: self._on_utterance_failed(uid, msg, _l))
                self._live.status_changed.connect(self._on_live_status)
                try:
                    provider.error.connect(self._on_provider_error)
                except Exception:
                    pass
                chunk_cb = self._live.push

        # Preview buffer is fanned in ahead of whatever ASR sink was chosen.
        # AudioRecorder wraps the whole callback in one try/except, so each
        # sink gets its own guard — otherwise a preview bug would silently
        # kill live transcription for the rest of the take.
        _preview_vad = None
        if self.mode == MODE_TRANSCRIPT:
            # Same class, same defaults, same audio as the real segmenter, so
            # the preview's cue boundaries are the ones that will be emitted.
            # The streaming path has no segmenter of its own, so this is also
            # what gives it previews at all.
            _preview_vad = live_transcribe.VadSegmenter(
                samplerate=recorder_mod.SAMPLE_RATE)
        self._live_buf = live_peaks.LiveTakeBuffer(
            samplerate=recorder_mod.SAMPLE_RATE, vad=_preview_vad)
        _asr_sink = chunk_cb

        def _fanout(mono, _sink=_asr_sink, _buf=self._live_buf):
            try:
                _buf.append(mono)
            except Exception:
                log.exception('Record: live preview buffer failed')
            if _sink is not None:
                _sink(mono)

        try:
            self._recorder = recorder_mod.AudioRecorder(
                self._output_path(), device=self.device, chunk_callback=_fanout)
            self._recorder.start()
            self._recording = True
            self._begin_live_take(base)
            transcribing = self.mode == MODE_TRANSCRIPT and (self._live or self._stream_provider)
            if self._stream is not None and self._stream['dead']:
                self._status = 'error'    # the engine failed while starting
            else:
                self._status = 'transcribing' if transcribing else 'recording'
        except Exception as exc:
            self._status = 'error'
            log.exception('Record: failed to start recorder: %s', exc)
            self._recorder = None
            self._clear_live_take()
            st = self._stream
            if st is not None:
                # Nothing will be recorded: close the engine's session too.
                try:
                    st['provider'].stream_stop()
                except Exception:
                    pass
                self._settle_stream(st['n'], 'error')
            if self._live is not None:
                self._detach_provider_error()
                self._live.cleanup()
                self._live = None

    def on_pause(self):
        """Playback paused/stopped — finalize the recording.

        Transcript mode: every phrase already cut, plus the one in progress,
        is a real (empty) subtitle by the time this returns — or, for the batch
        path, one event-loop turn later, when the flushed cut is delivered.
        Text fills those subtitles as it arrives; nothing is drawn as a
        stand-in any more, so there is nothing left on screen to retire.
        """
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
        stop_pos = float(session.SUBTITLE.get('position', 0) or 0)

        # AFTER rec.stop(): that joins the writer thread, so the preview VAD
        # can be flushed from here without racing an append.
        st = self._stream
        if st is not None and self._live_buf is not None:
            try:
                self._live_buf.flush_vad()
            except Exception:
                log.exception('Record: could not flush the preview VAD')
            self._drain_seals(self.live_take)
        self._clear_live_take()

        if st is not None:
            # The session keeps answering for this take while the next one
            # may already be recording; it is just no longer the current one.
            self._stream = None
            self._stream_provider = None
            self._interim_text = ''
            st['stop_pos'] = stop_pos
            if st['dead']:
                self._settle_stream(st['n'], 'error')
            else:
                try:
                    st['provider'].stream_stop()   # add-on flushes → stream_finished
                except Exception:
                    pass
                # Fallback: settle even if the terminal result never arrives.
                # Guarded by the session, NOT the provider: the provider is
                # shared, so a later take on it must not be torn down here.
                self._arm_stream_backstop(st)

        if self._live is not None:
            live = self._live
            self._live = None
            self._detach_provider_error()
            self._draining_lives.append(live)
            # Connected BEFORE finish(): with nothing outstanding it reports
            # done synchronously.
            live.status_changed.connect(
                lambda s, _live=live: self._finish_drain(_live) if s == 'done' else None)
            self._rearm_drain(live)
            try:
                live.finish()
            except Exception:
                pass

        if self.mode == MODE_TRANSCRIPT:
            self._refresh_ui()
        if self.mode == MODE_WAVE and wav and os.path.isfile(wav) and os.path.getsize(wav) > 0:
            self._attach_dub(wav, self._base_position)

    def _rearm_drain(self, live):
        """(Re)start the backstop for a draining take. Counted from the last
        answer, not from Stop: a slow engine working through a backlog is
        still answering, and must not be cut off mid-way."""
        if live is None or live not in self._draining_lives:
            return
        self._drain_gen += 1
        self._drain_backstops[id(live)] = gen = self._drain_gen
        QTimer.singleShot(_PENDING_TIMEOUT_MS,
                          lambda _live=live, _gen=gen: self._drain_expired(_live, _gen))

    def _drain_expired(self, live, gen):
        if self._drain_backstops.get(id(live)) == gen:
            self._finish_drain(live, abandoned=True)

    def _finish_drain(self, live, abandoned=False):
        if live not in self._draining_lives:
            return
        self._draining_lives.remove(live)
        self._drain_backstops.pop(id(live), None)
        if abandoned:
            # The engine never answered these. Keep their subtitles — the
            # timing is still useful and the user can type — but stop
            # drawing them as "waiting for text".
            for uid in live.outstanding_uids():
                self._on_utterance_failed(uid, 'no answer')
        try:
            live.cleanup()
        except Exception:
            pass

    # -- transcript output -------------------------------------------------
    def _on_live_status(self, st):
        # Only reflect progress while we're actively recording; the drain-phase
        # 'done' shouldn't overwrite the idle state shown after Stop.
        if self._recording and st in ('transcribing', 'recording'):
            self._status = st

    def _on_provider_error(self, message):
        self._status = 'error'
        log.warning('Record: ASR provider error: %s', message)

    # -- placeholders: real subtitles that wait for their text ------------
    #
    # A phrase becomes a real, empty subtitle the moment it is cut (VAD
    # silence, max length, or pause). Its dict carries a runtime-only
    # `_rec` key; every underscore key is stripped on save. `pending_keys`
    # (shared with the timeline) lists the keys still waiting for text, so a
    # stale `_rec` restored by undo is inert. Text is placed by KEY, looked
    # up in the live document each time, so an undo/redo round-trip (which
    # replaces the dicts) still finds its subtitle — and a subtitle the user
    # deleted is never recreated. A record stays known for its whole take,
    # so a cue the user typed into is never adopted afresh by a later answer.
    def _share_pending(self):
        widget = getattr(self._host, 'timeline_widget', None)
        if widget is not None:
            try:
                widget.record_pending = self.pending_keys
            except Exception:
                pass

    def _new_key(self, token=None):
        self._ph_seq += 1
        return f'{token or self._take_token}.{self._ph_seq}'

    @staticmethod
    def _find_by_key(key):
        if not key:
            return None
        for s in session.SUBTITLE.get('segments', []) or []:
            if isinstance(s, dict) and s.get('_rec') == key:
                return s
        return None

    def _history_step(self):
        """One undo step per uninterrupted run of recorder edits.

        history.py only has whole-document snapshots (no grouping), so the
        run is delimited by identity: if the newest snapshot is still the one
        we pushed, nothing else happened since and this edit belongs to it.
        """
        from subtitld.modules import history
        top = history.top_snapshot()
        if top is None or top is not self._take_history:
            history.history_append()
            self._take_history = history.top_snapshot()

    @staticmethod
    def _insert_segment(seg):
        segs = session.SUBTITLE.setdefault('segments', [])
        starts = [float(s.get('start', 0.0)) for s in segs]
        segs.insert(bisect.bisect_right(starts, float(seg['start'])), seg)

    @staticmethod
    def _remove_segment(seg):
        segs = session.SUBTITLE.get('segments', [])
        for i, s in enumerate(segs):
            if s is seg:
                del segs[i]
                break
        if session.SUBTITLE.get('selected') is seg:
            session.SUBTITLE['selected'] = None

    @staticmethod
    def _untouched(seg, rec):
        return (abs(float(seg.get('start', 0.0)) - rec['start']) < 1e-6
                and abs(float(seg.get('end', 0.0)) - rec['end']) < 1e-6
                and (seg.get('text') or '').strip() == (rec['written'] or rec['orig']))

    def _amend(self, rec, fn):
        """Apply `fn(segments, index)` to every stored undo/redo snapshot that
        holds `rec`'s subtitle.

        For changes that COMPLETE the recorder's own creation (text arriving,
        a split, a merge, noise removed): undoing an unrelated later edit
        must not roll them back, and redoing the take brings them along.
        Adopted subtitles are not amended — replacing their text is an edit
        of its own, with its own undo step.
        """
        if rec['kind'] != 'new':
            return
        from subtitld.modules import history
        key = rec['key']

        def update(segs):
            for i, s in enumerate(segs):
                if isinstance(s, dict) and s.get('_rec') == key:
                    fn(segs, i)
                    return True
            return False
        history.amend_snapshots(update, stop_at=rec.get('born'))

    @staticmethod
    def _stamp_snapshots(target, key):
        """An adopted subtitle's key has to survive undo/redo, or its answer
        has nowhere to go: stamp the stored copies of it too."""
        from subtitld.modules import history
        start = float(target.get('start', 0.0))
        end = float(target.get('end', 0.0))
        text = target.get('text') or ''

        def update(segs):
            for s in segs:
                if (isinstance(s, dict)
                        and abs(float(s.get('start', 0.0)) - start) < 1e-6
                        and abs(float(s.get('end', 0.0)) - end) < 1e-6
                        and (s.get('text') or '') == text):
                    s['_rec'] = key
                    return True
            return False
        history.amend_snapshots(update)

    def _create_placeholder(self, start, end, speaker='A', pending=True,
                            token=None, adopt=True):
        """Make the cue [start, end] a real subtitle, or adopt the subtitle
        it falls inside. Returns the placeholder record, or None if there is
        no room for it (or, with `adopt` False, the spot is someone else's)."""
        token = token or self._take_token
        start = float(start)
        end = float(end)
        target = self._subtitle_at((start + end) / 2.0)
        if target is not None:
            key = target.get('_rec')
            rec = self._ph.get(key) if key else None
            if rec is not None and rec['take'] == token:
                # This take's own cue: its text rules still apply, so a cue
                # the user typed into stays theirs.
                pass
            elif not adopt:
                return None
            else:
                if rec is not None:
                    # Recording the line again: the new take owns it now, and
                    # an answer still due for the old take is dropped.
                    rec['superseded'] = True
                    self.pending_keys.discard(rec['key'])
                # Narrating over an existing subtitle re-transcribes it: its
                # text is replaced by the first answer, later answers append,
                # and its timing is left alone (the historic behaviour).
                key = self._new_key(token)
                target['_rec'] = key
                self._stamp_snapshots(target, key)
                rec = {'key': key, 'kind': 'retarget', 'take': token,
                       'orig': (target.get('text') or '').strip(), 'written': None,
                       'start': float(target.get('start', 0.0)),
                       'end': float(target.get('end', 0.0)),
                       'outstanding': 0, 'claimed': False, 'born': None,
                       'superseded': False}
                self._ph[key] = rec
        else:
            start, end = self._clamp_to_gap(start, end)
            if end - start < _MIN_PLACEHOLDER:
                return None
            key = self._new_key(token)
            self._history_step()
            self._insert_segment({'start': start, 'end': end, 'text': '',
                                  'speaker': speaker, '_rec': key})
            if speaker not in session.SPEAKERS:
                session.SPEAKERS[speaker] = {'image': None}
            # `born` is the snapshot taken just before this insert: stored
            # snapshots older than it cannot hold this subtitle.
            rec = {'key': key, 'kind': 'new', 'take': token,
                   'orig': '', 'written': None, 'start': start, 'end': end,
                   'outstanding': 0, 'claimed': False, 'born': self._take_history,
                   'superseded': False}
            self._ph[key] = rec
            session.set_unsaved()
        rec['outstanding'] += 1
        if pending:
            self.pending_keys.add(key)
        return rec

    def _release(self, rec):
        """One reason to wait for text is gone (answered, failed, abandoned)."""
        rec['outstanding'] = max(0, rec['outstanding'] - 1)
        if rec['outstanding'] == 0:
            self.pending_keys.discard(rec['key'])

    def _mergeable(self, rec):
        """An untouched, still empty cue this take made."""
        if rec['kind'] != 'new' or rec.get('superseded') or rec['written'] is not None:
            return False
        seg = self._find_by_key(rec['key'])
        return seg is not None and self._untouched(seg, rec)

    @staticmethod
    def _next_text(written, orig, cur, text):
        """What a subtitle's text becomes when `text` arrives, or None when
        the user has changed it (their text always wins)."""
        if written is not None:
            return (cur + ' ' + text).strip() if cur == written else None
        return text if cur == orig else None

    def _fill(self, rec, text):
        text = (text or '').strip()
        if not text:
            return False
        if rec.get('superseded'):
            log.info('Record: %s was recorded again; transcript %r dropped', rec['key'], text)
            return False
        key = rec['key']
        seg = self._find_by_key(key)
        written, orig = rec['written'], rec['orig']
        new_text = None
        if seg is not None:
            new_text = self._next_text(written, orig, (seg.get('text') or '').strip(), text)
            if new_text is None:
                log.info('Record: %s was edited; transcript %r not applied', key, text)
            else:
                if rec['kind'] == 'retarget' and written is None:
                    self._history_step()   # replacing existing text is a real edit
                seg['text'] = new_text
                session.set_unsaved()

        def _apply(segs, i):
            nt = self._next_text(written, orig, (segs[i].get('text') or '').strip(), text)
            if nt is not None:
                segs[i]['text'] = nt
        self._amend(rec, _apply)
        if new_text is not None:
            rec['written'] = new_text
        elif seg is None and rec['kind'] == 'new':
            # Deleted or undone: only the stored copies took the text. Track
            # it so a later append still matches them.
            rec['written'] = (written + ' ' + text).strip() if written is not None else text
        return new_text is not None

    def _discard_if_untouched(self, rec):
        """The engine positively heard nothing here: a VAD false positive."""
        # Only an empty cue: one that already holds a transcript is never
        # taken away by another answer's silence.
        if rec['kind'] != 'new' or rec['written'] is not None or rec.get('superseded'):
            return
        seg = self._find_by_key(rec['key'])
        if seg is not None and self._untouched(seg, rec):
            self._remove_segment(seg)
            session.set_unsaved()

            def _drop(segs, i):
                if self._untouched(segs[i], rec):
                    del segs[i]
            self._amend(rec, _drop)

    # -- batch path (LiveTranscriber) --------------------------------------
    def _on_utterance_sealed(self, info, token=None):
        rec = self._create_placeholder(info['start'], info['end'],
                                       info.get('speaker', 'A'), token=token)
        self._uid_rec[info['uid']] = rec
        self._refresh_ui()

    def _on_utterance_done(self, uid, segments, live=None):
        self._rearm_drain(live)
        rec = self._uid_rec.pop(uid, None)
        if rec is None:
            return   # no room for a subtitle when it was cut
        pieces = [s for s in (segments or [])
                  if isinstance(s, dict) and str(s.get('text', '')).strip()]
        if not pieces:
            if rec['outstanding'] == 1:
                self._discard_if_untouched(rec)
        elif not self._split_fill(rec, pieces):
            self._fill(rec, ' '.join(str(s['text']).strip() for s in pieces))
        self._release(rec)
        self._refresh_ui()

    def _on_utterance_failed(self, uid, message, live=None):
        self._rearm_drain(live)
        rec = self._uid_rec.pop(uid, None)
        if rec is not None:
            log.warning('Record: no transcript for %s (%s); keeping the empty subtitle',
                        rec['key'], message)
            self._release(rec)
            self._refresh_ui()

    def _split_fill(self, rec, pieces):
        """Several ASR segments for one untouched placeholder: split it at the
        segment boundaries so cues stay subtitle-sized. False = not split."""
        if len(pieces) < 2 or rec['kind'] != 'new' or rec['written'] is not None:
            return False
        seg = self._find_by_key(rec['key'])
        if seg is None or not self._untouched(seg, rec):
            return False
        lo, hi = rec['start'], rec['end']
        cuts = [lo]
        for a, b in zip(pieces, pieces[1:]):
            c = (float(a['end']) + float(b['start'])) / 2.0
            if not (cuts[-1] + _MIN_PLACEHOLDER <= c <= hi - _MIN_PLACEHOLDER):
                return False
            cuts.append(c)
        cuts.append(hi)
        speaker = seg.get('speaker', 'A')
        tails = [{'start': cuts[i], 'end': cuts[i + 1],
                  'text': str(piece['text']).strip(), 'speaker': speaker}
                 for i, piece in enumerate(pieces[1:], start=1)]

        # The split completes the creation too: stored copies get it as well.
        def _split(segs, i):
            if not self._untouched(segs[i], rec):
                return
            segs[i]['end'] = cuts[1]
            segs[i + 1:i + 1] = [dict(t) for t in tails]
        self._amend(rec, _split)
        seg['end'] = rec['end'] = cuts[1]
        self._fill(rec, str(pieces[0]['text']).strip())
        for tail in tails:
            self._insert_segment(tail)
        return True

    # -- live streaming (asr.stream) --------------------------------------
    #
    # A session outlives its take: after pause the engine is still working
    # on the last phrase, and the user may already be recording the next
    # take. Sessions stay in `_streams` until they settle; only `_stream`
    # (the one being recorded) is fed audio and gets new cues.
    #
    # Providers that return an id from `stream_start` also emit tagged
    # events, so a finishing session's late text still reaches its own cues.
    # A provider that returns None cannot tell its sessions apart: starting a
    # new one settles the previous one first.
    #
    # Finals that carry a time span are placed by overlap. Untimed ones are
    # matched in arrival order (an engine answers its sentences in order),
    # bounded by the playhead of the last interim before them.
    def _start_stream(self, provider, base):
        legacy = self._legacy_stream
        if legacy is not None:
            self._settle_stream(legacy['n'], 'superseded')
        if self._take_token is None:
            self._take_token = secrets.token_hex(3)
        self._stream_seq += 1
        st = {'n': self._stream_seq, 'sid': None, 'token': self._take_token,
              'provider': provider, 'base': float(base), 'order': [],
              'last': None, 'carry': [], 'finals': 0, 'interim_t': None,
              'dead': False, 'stop_pos': None, 'phrase_start': float(base),
              'timed': False, 'timed_carry': [], 'cut_interim': '',
              'continues': False, 'backstop': 0}
        self._streams[st['n']] = st
        self._stream = st
        self._stream_provider = provider
        self._interim_text = ''
        # Untagged first: a provider may report failure from inside
        # stream_start, before there is any id to tag it with.
        self._legacy_stream = st
        self._connect_untagged(provider)
        sid = None
        try:
            sid = provider.stream_start(
                session.SUBTITLE.get('language', 'en-us'), self._asr_options(provider))
        except Exception as exc:
            self._on_session_error(st, f'could not start stream: {exc}')
        if isinstance(sid, str) and sid and st['n'] in self._streams:
            st['sid'] = sid
            self._by_sid[sid] = st
            self._legacy_stream = None
            self._disconnect_untagged(provider)
            self._connect_tagged(provider)

    def _connect_untagged(self, provider):
        provider.stream_segment.connect(self._on_untagged_segment)
        provider.stream_finished.connect(self._on_untagged_finished)
        provider.stream_error.connect(self._on_untagged_error)

    def _disconnect_untagged(self, provider):
        for sig, slot in (
            (provider.stream_segment, self._on_untagged_segment),
            (provider.stream_finished, self._on_untagged_finished),
            (provider.stream_error, self._on_untagged_error),
        ):
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def _connect_tagged(self, provider):
        if any(p is provider for p in self._tagged_providers):
            return
        self._tagged_providers.append(provider)
        provider.stream_segment_tagged.connect(self._on_tagged_segment)
        provider.stream_finished_tagged.connect(self._on_tagged_finished)
        provider.stream_error_tagged.connect(self._on_tagged_error)

    def _disconnect_tagged(self):
        for provider in self._tagged_providers:
            for sig, slot in (
                (provider.stream_segment_tagged, self._on_tagged_segment),
                (provider.stream_finished_tagged, self._on_tagged_finished),
                (provider.stream_error_tagged, self._on_tagged_error),
            ):
                try:
                    sig.disconnect(slot)
                except Exception:
                    pass
        self._tagged_providers = []

    def _on_untagged_segment(self, seg, final):
        if self._legacy_stream is not None:
            self._on_session_segment(self._legacy_stream, seg, final)

    def _on_untagged_finished(self, segments):
        if self._legacy_stream is not None:
            self._on_session_finished(self._legacy_stream, segments)

    def _on_untagged_error(self, message):
        if self._legacy_stream is not None:
            self._on_session_error(self._legacy_stream, message)

    def _on_tagged_segment(self, sid, seg, final):
        st = self._by_sid.get(sid)
        if st is not None:
            self._on_session_segment(st, seg, final)

    def _on_tagged_finished(self, sid, segments):
        st = self._by_sid.get(sid)
        if st is not None:
            self._on_session_finished(st, segments)

    def _on_tagged_error(self, sid, message):
        st = self._by_sid.get(sid)
        if st is not None:
            self._on_session_error(st, message)

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

    def _on_session_error(self, st, message):
        log.warning('Record: ASR stream error: %s', message)
        if st is self._stream:
            self._status = 'error'
        st['dead'] = True
        # Nothing more is coming: what is waiting stops waiting (and stays).
        # Cues cut from now on are still made — timing only, not pending.
        for rec in st['order']:
            if not rec['claimed']:
                rec['claimed'] = True
                self._release(rec)
        self._refresh_ui()
        if st['stop_pos'] is not None:     # already paused: nothing will settle it
            self._settle_stream(st['n'], 'error')

    def _stream_pos(self, st):
        """The playhead as far as this session is concerned: frozen at pause,
        since after that the user may be anywhere."""
        if st['stop_pos'] is not None:
            return st['stop_pos']
        return float(session.SUBTITLE.get('position', 0) or 0)

    def _on_session_segment(self, st, seg, final):
        """A streaming segment arrived (main thread). Interim text is shown
        separately; committed (final) segments fill placeholders."""
        text = str(seg.get('text', '')).strip()
        current = st is self._stream
        if st['stop_pos'] is not None:
            self._arm_stream_backstop(st)   # still answering: keep waiting
        if not final:
            if current:
                self._interim_text = text
            if text:
                # Interims since the previous final belong to the NEXT final;
                # the latest one bounds where that sentence can have started.
                st['interim_t'] = self._stream_pos(st)
                # The sentence in progress at the last cut is still growing:
                # the cue being spoken now continues it.
                if st['cut_interim'] and _extends(text, st['cut_interim']):
                    st['continues'] = True
            return
        if current:
            self._interim_text = ''
        st['finals'] += 1
        cutoff = st['interim_t']
        st['interim_t'] = None
        st['cut_interim'] = ''
        st['continues'] = False
        if text:
            self._stream_commit(st, seg, text, cutoff)
            self._refresh_ui()

    def _arm_stream_backstop(self, st):
        """Settle the session if it stays silent for a while. Counted from its
        last word, not from pause: a slow engine still answering is not stuck."""
        st['backstop'] += 1
        QTimer.singleShot(_PENDING_TIMEOUT_MS,
                          lambda n=st['n'], gen=st['backstop']: self._stream_expired(n, gen))

    def _stream_expired(self, n, gen):
        st = self._streams.get(n)
        if st is not None and st['backstop'] == gen:
            self._settle_stream(n, 'timeout')

    def _stream_commit(self, st, seg, text, cutoff):
        try:
            t0, t1 = float(seg.get('start', 0) or 0), float(seg.get('end', 0) or 0)
        except (TypeError, ValueError):
            t0 = t1 = 0.0
        if t1 > t0:
            st['timed'] = True
            self._commit_timed(st, st['base'] + t0, st['base'] + t1, text)
        else:
            self._commit_untimed(st, text, cutoff)

    @staticmethod
    def _overlap(rec, a, b):
        """Overlap of [a, b] with where the cue was CUT. Engine timestamps say
        where the phrase was spoken, which moving the subtitle does not change."""
        return min(rec['end'], b) - max(rec['start'], a)

    @staticmethod
    def _significant(overlap, rec, a, b):
        """Enough overlap to say the sentence is this cue's. The engine's span
        starts early (pre-roll) and ends late (trailing silence), so a sliver
        reaching into a neighbour means nothing."""
        return overlap > 0 and (overlap >= 0.5 * (rec['end'] - rec['start'])
                                or overlap >= 0.5 * (b - a))

    def _commit_timed(self, st, a, b, text):
        hits = []
        for rec in st['order']:
            overlap = self._overlap(rec, a, b)
            if self._significant(overlap, rec, a, b):
                hits.append((overlap, rec))
        if not hits:
            last_cut = max((r['end'] for r in st['order']), default=st['base'])
            if st is self._stream and self._recording and b > last_cut:
                # Spoken after the last cut: its cue has not been cut yet.
                st['timed_carry'].append((a, b, text))
            else:
                self._stream_place_new(st, a, b, text, adopt=True)
            return
        best = max(hits, key=lambda h: h[0])[1]
        if best['claimed']:
            # The engine split a cue that already has its text: add to it.
            self._fill(best, text)
            st['last'] = best
            return
        # Other waiting cues the sentence covers most of were a pause the VAD
        # cut at mid-sentence.
        run = [r for o, r in hits if r is best or
               (not r['claimed'] and o >= 0.5 * (r['end'] - r['start']))]
        run.sort(key=st['order'].index)
        self._claim_run(st, run, text, primary=best)

    def _commit_untimed(self, st, text, cutoff):
        waiting = [r for r in st['order'] if not r['claimed']]
        if cutoff is not None:
            eligible = [r for r in waiting if r['start'] <= cutoff + _STREAM_CUTOFF_SLACK]
        else:
            eligible = waiting
        if not eligible:
            live = self.live_take if (st is self._stream and self._recording) else None
            growing = live.get('cue_start') if live is not None else None
            last = st['last']
            if growing is not None and (cutoff is None or
                                        growing <= cutoff + _STREAM_CUTOFF_SLACK):
                st['carry'].append(text)   # its cue has not been cut yet
                return
            if (last is not None and cutoff is not None
                    and cutoff <= last['end'] + _STREAM_CUTOFF_SLACK):
                self._fill(last, text)     # the engine split one cue
                return
            if waiting:
                eligible = waiting         # answers come in order
            else:
                start = last['end'] if last is not None else st['phrase_start']
                end = max(self._stream_pos(st), start + 0.3)
                # Never over a subtitle someone else wrote: only a gap will do.
                if not self._stream_place_new(st, start, end, text, adopt=False):
                    if last is not None:
                        self._fill(last, text)
                    else:
                        log.info('Record: no room for transcript %r; dropped', text)
                return
        # Without timestamps the oldest waiting cue is this sentence's. It
        # runs on into the next cue only on evidence: the VAD force-cut a
        # monologue there (no gap), or the text in progress at the cut kept
        # growing afterwards.
        run = [eligible[0]]
        for rec in eligible[1:]:
            prev = run[-1]
            if st['order'].index(rec) != st['order'].index(prev) + 1:
                break
            if not (rec.get('joins_prev') or rec['start'] - prev['end'] <= _FORCED_CUT_GAP):
                break
            run.append(rec)
        self._claim_run(st, run, text)

    def _claim_run(self, st, run, text, primary=None):
        """Give `text` to the sentence's main cue (`primary`, else the first
        of `run`, cues in cut order) and merge its untouched neighbours in the
        run into it. If the user deleted or typed into that main cue, the
        sentence is theirs: _fill drops it (a deleted cue's text only reaches
        its stored undo copies) rather than moving it into a leftover piece."""
        head = primary if primary is not None else run[0]
        if self._mergeable(head):
            i = run.index(head)
            lo = hi = i
            while lo > 0 and self._mergeable(run[lo - 1]):
                lo -= 1
            while hi < len(run) - 1 and self._mergeable(run[hi + 1]):
                hi += 1
            self._merge_into(head, run[lo:hi + 1])
        for rec in run:
            if rec is not head:
                rec['claimed'] = True
                self._release(rec)
        self._fill(head, text)
        head['claimed'] = True
        st['last'] = head
        st['phrase_start'] = max(st['phrase_start'], head['end'])
        self._release(head)

    def _merge_into(self, head, group):
        """Stretch `head` over `group` (untouched cues, in cut order) and
        remove the others, in the document and in its stored undo copies."""
        start = min(r['start'] for r in group)
        end = max(r['end'] for r in group)
        if start == head['start'] and end == head['end']:
            return
        old_start, old_end = head['start'], head['end']

        def _stretch(segs, i):
            if (abs(float(segs[i].get('start', 0.0)) - old_start) < 1e-6
                    and abs(float(segs[i].get('end', 0.0)) - old_end) < 1e-6):
                segs[i]['start'], segs[i]['end'] = start, end
        self._amend(head, _stretch)
        for rec in group:
            if rec is head:
                continue
            seg = self._find_by_key(rec['key'])

            def _drop(segs, i, rec=rec):
                if self._untouched(segs[i], rec):
                    del segs[i]
            self._amend(rec, _drop)
            if seg is not None:
                self._remove_segment(seg)
        head_seg = self._find_by_key(head['key'])
        if head_seg is not None:
            head_seg['start'], head_seg['end'] = start, end
        head['start'], head['end'] = start, end
        session.set_unsaved()

    def _stream_place_new(self, st, start, end, text, adopt):
        rec = self._create_placeholder(start, end, pending=False,
                                       token=st['token'], adopt=adopt)
        if rec is None:
            return False
        if rec not in st['order']:
            st['order'].append(rec)
        self._fill(rec, text)
        rec['claimed'] = True
        st['last'] = rec
        st['phrase_start'] = max(st['phrase_start'], rec['end'])
        self._release(rec)
        return True

    def _drain_seals(self, live):
        """Cues the preview VAD has cut since the last tick."""
        buf = live.get('buffer') if live else None
        if buf is None:
            return
        try:
            sealed = buf.take_sealed()
        except Exception:
            return
        if not sealed:
            return
        base = live['base']
        st = self._stream
        vad = buf.vad_state()
        changed = False
        for a, b in sealed:
            start, end = base + a, base + b
            # The growing cue can only be speech AFTER this cut (a force-cut
            # at max length, or a new phrase that began between two ticks).
            if live.get('cue_start') is not None and live['cue_start'] < end:
                later = base + max(0.0, vad[1] - vad[3]) if vad and vad[0] else end
                live['cue_start'] = max(end, later)
                live['cue_end'] = max(live.get('cue_end') or 0.0, live['cue_start'] + 0.15)
            if st is None:
                continue   # batch: the transcriber announces its own cuts
            joins_prev = st['continues']
            st['cut_interim'] = self._interim_text
            st['continues'] = False
            rec = self._create_placeholder(start, end, pending=not st['dead'],
                                           token=st['token'])
            if rec is None:
                continue
            changed = True
            if st['dead'] or rec in st['order']:
                # Nothing will answer (dead), or a second cut over the same
                # subtitle: its answer appends to the cue already waiting.
                self._release(rec)
                continue
            rec['joins_prev'] = joins_prev
            st['order'].append(rec)
            early = [c for c in st['timed_carry']
                     if self._significant(self._overlap(rec, c[0], c[1]), rec, c[0], c[1])]
            if early:
                # Timed sentences that arrived before this cut.
                for c in early:
                    st['timed_carry'].remove(c)
                pieces = [{'start': c[0], 'end': c[1], 'text': c[2]} for c in early]
                if not self._split_fill(rec, pieces):
                    self._fill(rec, ' '.join(c[2] for c in early))
                rec['claimed'] = True
                st['last'] = rec
                self._release(rec)
            elif st['carry']:
                self._fill(rec, ' '.join(st['carry']))
                st['carry'] = []
                rec['claimed'] = True
                st['last'] = rec
                self._release(rec)
        if changed:
            self._refresh_ui()

    def _on_session_finished(self, st, segments):
        # Engines that only commit at the end: place what never streamed in.
        extra = list(segments or [])[st['finals']:]
        for seg in extra:
            text = str(seg.get('text', '')).strip() if isinstance(seg, dict) else ''
            if text:
                st['finals'] += 1
                self._stream_commit(st, seg, text, None)
        self._settle_stream(st['n'], 'finished')

    def _settle_stream(self, n, reason):
        """End a streaming session (idempotent)."""
        st = self._streams.pop(n, None)
        if st is None:
            return
        if st['sid']:
            self._by_sid.pop(st['sid'], None)
        if self._legacy_stream is st:
            self._legacy_stream = None
            self._disconnect_untagged(st['provider'])
        if self._stream is st:
            self._stream = None
            self._stream_provider = None
            self._interim_text = ''
        for a, b, text in st['timed_carry']:
            # Timed text whose cue was never cut: the engine says where.
            self._stream_place_new(st, a, b, text, adopt=True)
        st['timed_carry'] = []
        if st['carry']:
            # Text whose cue was never cut (VAD missed it): bound it by the
            # playhead at pause, in a gap.
            last = st['last']
            start = last['end'] if last is not None else st['phrase_start']
            end = st['stop_pos'] if st['stop_pos'] is not None else start + 0.3
            text = ' '.join(st['carry'])
            st['carry'] = []
            if not self._stream_place_new(st, start, max(end, start + 0.3), text,
                                          adopt=False) and last is not None:
                self._fill(last, text)
        for rec in st['order']:
            if rec['claimed']:
                continue
            rec['claimed'] = True
            if reason == 'finished' and st['timed'] and rec['outstanding'] == 1:
                # The engine placed every sentence by time, none of them
                # here: the VAD cut noise.
                self._discard_if_untouched(rec)
            # Otherwise keep it (timing is useful, the user can type); just
            # stop marking it pending.
            self._release(rec)
        if reason != 'finished':
            log.info('Record: stream settled (%s)', reason)
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Live take preview
    #
    # Only the part of the take that is still GROWING is kept out of
    # session.SUBTITLE and painted as an overlay:
    #   * history snapshots deep-copy the whole segment list and are capped at
    #     100 entries, so growing a cue in-document at 15 Hz would either
    #     evict the user's real undo history or leave the stack describing a
    #     half-take;
    #   * (wave) autosave can fire mid-take and would bundle a WAV that
    #     AudioRecorder still holds open, whose RIFF sizes are only fixed on
    #     stop();
    #   * (wave) audioengine.sync_subtitle_dubs would hand the mixer thread a
    #     file that is still growing, and the user would hear their own take.
    # A transcript cue stops growing when it is cut, and becomes a real
    # subtitle at that moment (see "placeholders" above).
    # ------------------------------------------------------------------
    def _begin_live_take(self, base):
        host = self._host
        target = self._target_subtitle(base) if self.mode == MODE_WAVE else None
        self._live_seq += 1
        self.live_take = {
            'seq': self._live_seq,
            'mode': self.mode,
            'base': float(base),
            'end': float(base),
            'subtitle': target,
            'buffer': self._live_buf,
            'buckets_drawn': 0,
            'pixmap': None,
            'pm_wpp': None,
            'peak': 0.0,
            'last_edge_x': None,
            # Transcript mode: the cue currently being spoken.
            'cue_start': None,
            'cue_end': None,
            'cue_cap': None,
        }
        widget = getattr(host, 'timeline_widget', None)
        if widget is not None:
            widget.live_take = self.live_take
        self._share_pending()
        if self._live_timer is None:
            self._live_timer = QTimer(self)
            # ~15Hz, half the playhead repaint rate. Coarse because none of
            # this needs millisecond accuracy and coarse timers coalesce.
            self._live_timer.setInterval(66)
            self._live_timer.setTimerType(Qt.CoarseTimer)
            self._live_timer.timeout.connect(self._live_tick)
        self._live_timer.start()

    def _live_tick(self):
        live = self.live_take
        rec = self._recorder
        if live is None or rec is None or not self._recording:
            return
        try:
            live['end'] = live['base'] + float(rec.elapsed)
            buf = live['buffer']
            live['peak'] = buf.peak
            self._live_track_cue(live, buf)
            if live.get('mode') == MODE_TRANSCRIPT:
                self._drain_seals(live)
            widget = getattr(self._host, 'timeline_widget', None)
            if widget is not None:
                from subtitld.interface import timeline as _tl
                _tl.live_take_tick(widget, live)
        except Exception:
            log.exception('Record: live take tick failed')

    def _live_track_cue(self, live, buf):
        """Open and grow the provisional cue from the VAD's own state. Cutting
        it is the VAD's call (see _drain_seals), not this poll's."""
        state = buf.vad_state()
        if state is None:
            return
        in_speech, speech_start, last_speech, pad = state
        base = live['base']

        if not in_speech:
            live['cue_start'] = None
            live['cue_end'] = None
            live['cue_cap'] = None
            return

        if live.get('cue_start') is None:
            start = base + max(0.0, speech_start - pad)
            live['cue_start'] = start
            # Computed ONCE per cue: an O(len(segments)) scan at 15Hz is
            # exactly the sort of work that starved the mixer before.
            live['cue_cap'] = self._next_subtitle_start(start)

        end = base + last_speech + pad
        cap = live.get('cue_cap')
        if cap:
            end = min(end, float(cap))
        live['cue_end'] = max(end, live['cue_start'] + 0.15)

    def _clear_live_take(self, seq=None):
        """Tear down the preview.

        `seq` guards deferred callers: a stale callback from a previous take
        must not clear the take the user just started.
        """
        if seq is not None and (self.live_take is None
                                or self.live_take.get('seq') != seq):
            return
        if self._live_timer is not None:
            self._live_timer.stop()
        self.live_take = None
        self._live_buf = None
        widget = getattr(self._host, 'timeline_widget', None)
        if widget is not None:
            widget.live_take = None
            try:
                widget.update()
            except Exception:
                pass

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
        self._share_pending()
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
            # Provider subclasses implement clone(). Anything else that merely
            # quacks like an engine gets the historic no-argument construction;
            # if that raises too, we fail closed below rather than fall back.
            maker = getattr(base, 'clone', None)
            clone = maker() if callable(maker) else type(base)()
        except Exception:
            clone = None
            log.exception('Record: could not clone ASR provider %r',
                          getattr(base, 'id', base))
        if clone is None or clone is base:
            # FAIL CLOSED. Falling back to the shared instance was worse than
            # having no engine: the Import panel's transcript_finished handler
            # replaces every subtitle in the project with the finished
            # segments, so each recorded utterance would silently wipe the
            # track down to one cue. Refusing to record is recoverable; that
            # was not.
            log.error('Record: no private ASR instance available; refusing to '
                      'drive the shared provider (it would overwrite the project)')
            return None
        return clone

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
        # After the recorder, never before — see on_pause.
        self._clear_live_take()
        st = self._stream
        if st is not None:
            try:
                st['provider'].stream_stop()
            except Exception:
                pass
        for n in list(self._streams):
            self._settle_stream(n, 'shutdown')
        self._disconnect_tagged()
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
        self._drain_backstops.clear()
        self._uid_rec.clear()
        self._ph.clear()
        self.pending_keys.clear()
