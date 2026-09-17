"""Transcript placeholders: a cut phrase is a REAL (empty) subtitle at once,
and its transcript fills that subtitle. Standalone; puts its own src/ on the path."""
import sys, types, os, time, json, tempfile
# Import THIS checkout's code. The venv holds a non-editable install, and
# without this the suite silently tests that stale copy instead.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'src'))
sys.modules['mediapipe'] = types.ModuleType('mediapipe')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QObject, Signal, QTimer as _RealQTimer
from PySide6.QtGui import QImage, QPainter
app = QApplication(sys.argv)
from subtitld.modules import session, history, recorder as recorder_mod
from subtitld.interface import record_controls, timeline
import soundfile as sf

recorder_mod.sd = None
record_controls.any_asr_provider = lambda: True
recorder_mod.list_input_devices = lambda: [(0, 'Mic')]
recorder_mod.default_input_device = lambda: 0
recorder_mod.input_available = lambda: True
SR = recorder_mod.SAMPLE_RATE

# Backstop timers are captured, not waited for: a test fires them explicitly.
TIMERS = []
class _TimerShim(_RealQTimer):
    @staticmethod
    def singleShot(ms, *args):
        TIMERS.append((ms, args[-1]))
record_controls.QTimer = _TimerShim

def fire_timers(ms):
    for delay, fn in [t for t in TIMERS if t[0] == ms]:
        TIMERS.remove((delay, fn))
        fn()

def tone(d, a=0.3):
    t = np.arange(int(d * SR)) / SR
    return (a * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
def sil(d): return np.zeros(int(d * SR), np.float32)

class Host(QWidget):
    def __init__(s):
        super().__init__()
        s.preview_panel_player = types.SimpleNamespace(is_paused=lambda: True,
            _audio_device=types.SimpleNamespace(sync_subtitle_dubs=lambda segs: None))
        s.timeline_widget = types.SimpleNamespace(update=lambda *a: None, live_take=None,
                                                  width_proportion=0, record_pending=set())

def pump(n=30):
    for _ in range(n):
        app.processEvents()

def feed(ctrl, audio):
    """Push audio through the real recorder and wait for the writer thread."""
    rec = ctrl._recorder
    target = rec.frames_written + len(audio)
    blk = int(0.1 * SR)
    for i in range(0, len(audio), blk):
        rec.feed(audio[i:i + blk])
    deadline = time.time() + 5
    while rec.frames_written < target and time.time() < deadline:
        time.sleep(0.002)
    ctrl._live_tick()
    pump()

def reset(segments=None, pos=0.0):
    session.CONFIG.setdefault('record', {}).update({'mode': 'transcript', 'armed': True, 'device': 0})
    session.SPEAKERS = {}
    session.SUBTITLE = {'segments': list(segments or []), 'language': 'en-us', 'position': pos}
    history.history_clear()
    TIMERS.clear()

def subs():
    return session.SUBTITLE['segments']

def texts():
    return [s['text'] for s in subs()]

fails = []
def check(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (('  ' + str(detail)) if detail else ''))
    if not cond:
        fails.append(name)

# --------------------------------------------------------------------------
# Batch engine that answers only when told to.
class HeldASR(QObject):
    transcript_finished = Signal(list); error = Signal(str)
    id = 'held'; tasks = ['asr.transcribe']
    def __init__(s):
        super().__init__(); s.inflight = []
    def supports_streaming(s): return False
    def is_available(s): return True
    def transcribe(s, path, lang, opts):
        d = sf.info(path); s.inflight.append(d.frames / d.samplerate)
    def answer(s, segs=None, text='hi'):
        dur = s.inflight.pop(0)
        s.transcript_finished.emit(segs if segs is not None else
                                   [{'start': 0.0, 'end': dur, 'text': text}])
        pump()
    def fail(s, msg='boom'):
        s.inflight.pop(0); s.error.emit(msg); pump()

def batch_take(segments=None, pos=10.0):
    reset(segments, pos)
    ctrl = record_controls.RecordController(Host())
    eng = HeldASR()
    ctrl._resolve_asr_provider = lambda: eng
    ctrl.on_play()
    return ctrl, eng

UTT = np.concatenate([sil(0.5), tone(1.2), sil(0.9)])

# B1: the phrase is a real subtitle while still recording, before any text.
ctrl, eng = batch_take()
feed(ctrl, UTT)
check('B1 placeholder exists during recording', ctrl.is_recording and len(subs()) == 1
      and subs()[0]['text'] == '' and subs()[0].get('_rec') in ctrl.pending_keys, texts())
ph = subs()[0]
eng.answer(text='hello')
check('B1 text fills THAT subtitle, not a new one', len(subs()) == 1 and subs()[0] is ph
      and ph['text'] == 'hello' and not ctrl.pending_keys, texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B2: pause mid-phrase -> the phrase in progress becomes a subtitle right away;
# nothing is drawn as a stand-in; text arrives later.
ctrl, eng = batch_take()
feed(ctrl, np.concatenate([sil(0.5), tone(1.2), sil(0.9), tone(0.8)]))
eng.answer(text='one')
ctrl.on_pause(); pump()
check('B2 no overlay left after pause', ctrl.live_take is None)
check('B2 in-progress phrase is a pending subtitle', len(subs()) == 2 and subs()[1]['text'] == ''
      and subs()[1].get('_rec') in ctrl.pending_keys, texts())
eng.answer(text='two')
check('B2 filled after pause', texts() == ['one', 'two'] and not ctrl.pending_keys, texts())
check('B2 drained without waiting for a backstop', not ctrl._draining_lives)
ctrl.shutdown()

# B3: engine hears nothing -> untouched placeholder removed; a moved one is kept.
ctrl, eng = batch_take()
feed(ctrl, UTT); feed(ctrl, UTT)
subs()[1]['start'] += 0.2          # the user nudged the second one
eng.answer(segs=[]); eng.answer(segs=[])
check('B3 empty answer removes untouched, keeps moved', len(subs()) == 1
      and abs(subs()[0]['start'] - 13.18) < 0.3 and not ctrl.pending_keys,
      [(round(s['start'], 2), s['text']) for s in subs()])
ctrl.on_pause(); pump(); ctrl.shutdown()

# B4: engine errors -> keep the empty subtitle, stop marking it pending.
ctrl, eng = batch_take()
feed(ctrl, UTT)
eng.fail('[internal] boom')
check('B4 error keeps empty subtitle', len(subs()) == 1 and subs()[0]['text'] == ''
      and not ctrl.pending_keys, texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B5: engine never answers -> the drain timeout un-pends; subtitle stays.
ctrl, eng = batch_take()
feed(ctrl, UTT)
ctrl.on_pause(); pump()
check('B5 still pending right after pause', len(ctrl.pending_keys) == 1)
check('B5 only one (30 s) backstop, no 45 s phantom timer',
      sorted(ms for ms, _ in TIMERS) == [30000], sorted(ms for ms, _ in TIMERS))
fire_timers(30000)
check('B5 timeout keeps subtitle, clears pending', len(subs()) == 1 and not ctrl.pending_keys)
ctrl.shutdown()

# B6: the user types before the text arrives -> their text wins.
ctrl, eng = batch_take()
feed(ctrl, UTT)
subs()[0]['text'] = 'typed by me'
eng.answer(text='asr text')
check('B6 user text never overwritten', texts() == ['typed by me'] and not ctrl.pending_keys, texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B7: the user deletes it before the text arrives -> never resurrected.
ctrl, eng = batch_take()
feed(ctrl, UTT)
from subtitld.modules import subtitles as subtitles_mod
subtitles_mod.remove_subtitle(subs()[0])
eng.answer(text='late')
check('B7 deleted placeholder not recreated', subs() == [] and not ctrl.pending_keys, texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B8: the user moves it -> text still lands in it.
ctrl, eng = batch_take()
feed(ctrl, UTT)
subtitles_mod.move_subtitle(subs()[0], amount=5.0)
eng.answer(text='moved')
check('B8 moved placeholder still filled', texts() == ['moved'] and subs()[0]['start'] > 15, texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B9: undo. Creation + fill is ONE step; an unrelated edit in between does not
# take the fill with it.
keep = {'start': 1.0, 'end': 2.0, 'text': 'mine'}
ctrl, eng = batch_take([keep])
feed(ctrl, UTT); feed(ctrl, UTT)
eng.answer(text='a')
history.history_append(); subs()[0]['text'] = 'mine, edited'      # user edit
eng.answer(text='b')
check('B9 before undo', texts() == ['mine, edited', 'a', 'b'], texts())
history.history_undo()
check('B9 undoing the user edit keeps both fills', texts() == ['mine', 'a', 'b'], texts())
history.history_undo()
check('B9 next undo removes the whole take', texts() == ['mine'], texts())
history.history_redo()
check('B9 redo brings the take back with its text', texts() == ['mine', 'a', 'b'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B10: undo BEFORE the text arrives -> nothing recreated; redo -> with text.
ctrl, eng = batch_take()
feed(ctrl, UTT)
history.history_undo()
eng.answer(text='after undo')
check('B10 text after undo does not recreate', subs() == [], texts())
history.history_redo()
check('B10 redo restores it filled', texts() == ['after undo'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# B11: several ASR segments for one phrase -> cue-sized subtitles.
ctrl, eng = batch_take()
feed(ctrl, np.concatenate([sil(0.5), tone(3.0), sil(0.9)]))
eng.answer(segs=[{'start': 0.1, 'end': 1.4, 'text': 'first half'},
                 {'start': 1.6, 'end': 3.0, 'text': 'second half'}])
check('B11 split at ASR boundary', texts() == ['first half', 'second half']
      and abs(subs()[0]['end'] - subs()[1]['start']) < 1e-6, [(round(s['start'], 2), round(s['end'], 2)) for s in subs()])
ctrl.on_pause(); pump(); ctrl.shutdown()

# B12: saving never persists the marker.
ctrl, eng = batch_take()
feed(ctrl, UTT)
from subtitld.modules import file_io
tmp = tempfile.mkdtemp()
session.FORMAT = {'format': 'JSON', 'options': {'standard': 'Whisper'}}
file_io.save_file(os.path.join(tmp, 'x.json'), 'JSON')
session.FORMAT = {}
file_io.save_file(os.path.join(tmp, 'x.usf'), 'USF')
blob = open(os.path.join(tmp, 'x.json')).read() + open(os.path.join(tmp, 'x.usf')).read()
check('B12 _rec never saved', '_rec' not in blob and '"segments"' in blob)
eng.answer(text='x'); ctrl.on_pause(); pump(); ctrl.shutdown()

# B13: overlay draws nothing for a transcript take in silence (no take-wide box).
w = types.SimpleNamespace(width_proportion=10.0, subtitle_y=10, subtitle_height=40)
img = QImage(500, 60, QImage.Format_ARGB32); img.fill(0)
p = QPainter(img)
timeline._paint_live_take(w, p, {'mode': 'transcript', 'base': 10.0, 'end': 40.0,
                                 'subtitle': None, 'cue_start': None, 'cue_end': None,
                                 'buffer': None, 'peak': 0.0}, None)
p.end()
painted = any(img.pixelColor(x, y).alpha() for x in range(0, 500, 3) for y in range(60))
check('B13 no take-wide box in silence', not painted)

# --------------------------------------------------------------------------
# Streaming engine driven by hand.
class FakeStream(QObject):
    stream_segment = Signal(dict, bool); stream_finished = Signal(list); stream_error = Signal(str)
    id = 'realtimestt'; tasks = ['asr.transcribe']
    def __init__(s, fail_start=False):
        super().__init__(); s.fail_start = fail_start; s.stopped = 0
    def supports_streaming(s): return True
    def is_available(s): return True
    def stream_start(s, lang, opts=None):
        if s.fail_start:
            s.stream_error.emit('[internal] could not start stream: Silero VAD failed')
    def stream_feed(s, pcm): pass
    def stream_stop(s): s.stopped += 1
    def interim(s, text): s.stream_segment.emit({'start': 0, 'end': 0, 'text': text}, False); pump()
    def final(s, text, start=0, end=0):
        s.stream_segment.emit({'start': start, 'end': end, 'text': text}, True); pump()

def stream_take(eng, segments=None, pos=0.0):
    reset(segments, pos)
    ctrl = record_controls.RecordController(Host())
    ctrl._resolve_shared_asr_provider = lambda: eng
    ctrl.on_play()
    return ctrl

def at(t):
    session.SUBTITLE['position'] = t

# S1: cues become pending subtitles while recording; finals fill them in order.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); at(2.6)
check('S1 placeholder while recording', len(subs()) == 1 and subs()[0]['text'] == ''
      and subs()[0]['_rec'] in ctrl.pending_keys, texts())
eng.interim('hel'); eng.final('hello')
feed(ctrl, UTT); at(5.2)
eng.interim('wor'); eng.final('world')
check('S1 finals fill in order', texts() == ['hello', 'world'] and not ctrl.pending_keys, texts())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# S2: pause mid-speech -> in-progress cue is a subtitle synchronously; text later.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(1.0)])); at(1.5)
eng.interim('almost')
ctrl.on_pause()
check('S2 cue sealed as subtitle inside on_pause', len(subs()) == 1 and subs()[0]['text'] == ''
      and ctrl.live_take is None and ctrl.pending_keys, texts())
eng.final('almost done')
check('S2 late final fills it', texts() == ['almost done'], texts())
eng.stream_finished.emit(['almost done']); pump()
check('S2 settled', not ctrl._streams and not ctrl.pending_keys)
ctrl.shutdown()

# S3: THE REPORTED CASE - stream_start failed (Silero VAD). Nothing waits.
eng = FakeStream(fail_start=True); ctrl = stream_take(eng)
check('S3 status error', ctrl.status == 'error', ctrl.status)
feed(ctrl, UTT); feed(ctrl, np.concatenate([tone(0.8)]))
check('S3 cues still become (non-pending) subtitles', len(subs()) == 1 and not ctrl.pending_keys, texts())
ctrl.on_pause()
check('S3 settled immediately on pause, no timer, both cues kept',
      ctrl._stream is None and not TIMERS and len(subs()) == 2 and not ctrl.pending_keys,
      (len(subs()), TIMERS))
ctrl.shutdown()

# S4: engine never sends stream_finished -> timeout settles, subtitles stay.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); ctrl.on_pause()
check('S4 pending after pause', len(ctrl.pending_keys) == 1)
fire_timers(30000)
check('S4 timeout keeps subtitle, clears pending', len(subs()) == 1 and not ctrl.pending_keys
      and not ctrl._streams)
ctrl.shutdown()

# S5: play again before the previous stream settled (a provider that cannot
# tell its sessions apart: the old one is settled first).
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); ctrl.on_pause()
old = list(TIMERS); TIMERS.clear()
ctrl.on_play()
at(8.0); eng.final('only once')
check('S5 final placed once', texts().count('only once') == 1, texts())
for ms, fn in old: fn()
check('S5 old timeout does not kill the new take', ctrl._stream is not None
      and ctrl._stream_provider is eng)
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# S6: a timed sentence across a short mid-sentence pause -> one subtitle; a
# sentence that only grazes the next cue (pre-roll / trailing silence) does not.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(1.0), sil(0.8)]))
feed(ctrl, np.concatenate([tone(1.0), sil(0.9)]))
check('S6 two cues cut', len(subs()) == 2, texts())
eng.final('the quick brown fox', start=0.4, end=3.4)
check('S6 merged into one', texts() == ['the quick brown fox'] and subs()[0]['start'] < 1.0 and subs()[0]['end'] > 3.3,
      [(round(s['start'], 2), round(s['end'], 2), s['text']) for s in subs()])
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); feed(ctrl, UTT)
eng.final('one', start=0.0, end=3.2)             # trailing silence reaches cue 2
eng.final('two', start=1.6, end=5.0)             # pre-roll reaches back into cue 1
check('S6 a sliver of overlap does not merge', texts() == ['one', 'two'], texts())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# S7: a timed engine never answers for a noise blip -> the blip goes at the
# end, the sentence fills its own cue.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(0.4), sil(3.0)]))       # noise at 0.5
feed(ctrl, np.concatenate([tone(1.2), sil(0.9)])); at(6.0)
eng.final('hi there', start=3.9, end=5.1)
check('S7 sentence placed by time', [s['text'] for s in subs()] == ['', 'hi there'], texts())
ctrl.on_pause(); eng.stream_finished.emit([]); pump()
check('S7 noise removed once the engine finished', texts() == ['hi there'] and subs()[0]['start'] > 3.5,
      [(round(s['start'], 2), s['text']) for s in subs()])
ctrl.shutdown()

# S8: the engine splits one cue into two finals -> both land in that cue.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(2.0)])); at(2.0)
eng.interim('first'); eng.final('first.')        # cue still growing -> carried
eng.interim('second')                            # still inside the same cue
feed(ctrl, sil(0.9)); at(3.4)
eng.final('second.')
check('S8 both sentences in the one cue', texts() == ['first. second.'], texts())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# S9: an engine that timestamps its finals is matched by overlap.
eng = FakeStream(); ctrl = stream_take(eng, pos=10.0)
feed(ctrl, UTT); feed(ctrl, UTT)
eng.final('second', start=3.0, end=4.5)          # stream-relative seconds
eng.final('first', start=0.4, end=1.8)
check('S9 timed finals matched by time', texts() == ['first', 'second'], texts())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# S10: an engine that only commits at the end.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); ctrl.on_pause()
eng.stream_finished.emit([{'start': 0, 'end': 0, 'text': 'at the end'}]); pump()
check('S10 end-only commit fills the cue', texts() == ['at the end'] and not ctrl.pending_keys, texts())
ctrl.shutdown()

# --------------------------------------------------------------------------
# Review findings.

def spans():
    return [(round(s['start'], 2), round(s['end'], 2), s['text']) for s in subs()]

# R1: the user types into a pending cue -> a late final never replaces it.
for timed in (False, True):
    eng = FakeStream(); ctrl = stream_take(eng)
    feed(ctrl, UTT); at(2.6)
    ctrl.on_pause()
    subs()[0]['text'] = 'my own words'
    eng.final('hello asr', *((0.4, 1.8) if timed else ()))
    check(f'R1 typed text kept ({"timed" if timed else "untimed"})',
          spans() == [(0.38, 1.82, 'my own words')], spans())
    eng.stream_finished.emit([]); pump()
    check(f'R1 nothing pending ({"timed" if timed else "untimed"})', not ctrl.pending_keys)
    ctrl.shutdown()

# R2: a late final for a cue the user deleted is dropped, and does not shift
# the next answer.
for timed in (False, True):
    eng = FakeStream(); ctrl = stream_take(eng)
    feed(ctrl, UTT); feed(ctrl, UTT); at(5.2)
    ctrl.on_pause()
    subtitles_mod.remove_subtitle(subs()[0])
    eng.final('hello', *((0.4, 1.8) if timed else ()))
    eng.final('world', *((3.0, 4.4) if timed else ()))
    check(f'R2 deleted cue not recreated, next answer in place ({"timed" if timed else "untimed"})',
          spans() == [(2.98, 4.42, 'world')], spans())
    eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# R3: a lagging untimed engine: its first final arrives after the second cut.
for gap, label in ((1.1, '2 s'), (0.1, '1 s')):
    eng = FakeStream(); ctrl = stream_take(eng)
    feed(ctrl, UTT)
    feed(ctrl, np.concatenate([sil(gap), tone(1.2), sil(0.9)]))
    at(5.2); eng.interim('phrase one'); eng.final('Phrase one.')
    at(5.4); eng.interim('phrase two'); eng.final('Phrase two.')
    got = spans()
    check(f'R3 lagging finals stay in their own cues ({label} gap)',
          [t for _, _, t in got] == ['Phrase one.', 'Phrase two.'] and got[0][:2] == (0.38, 1.82), got)
    ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); feed(ctrl, UTT); feed(ctrl, UTT); ctrl.on_pause()
eng.stream_finished.emit([{'start': 0, 'end': 0, 'text': t} for t in ('One.', 'Two.', 'Three.')]); pump()
check('R3 end-only engine pairs its segments with the cues in order',
      [t for _, _, t in spans()] == ['One.', 'Two.', 'Three.'] and len(spans()) == 3, spans())
ctrl.shutdown()

# R3b: a forced cut (monologue at max length) is the one untimed merge.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(16.0), sil(0.9)])); at(17.0)
check('R3b forced cut made two touching cues', len(subs()) == 2
      and abs(subs()[0]['end'] - subs()[1]['start']) < 1e-6, spans())
eng.interim('a long monologue'); eng.final('a long monologue')
check('R3b one sentence over a forced cut is one subtitle',
      len(subs()) == 1 and subs()[0]['text'] == 'a long monologue' and subs()[0]['end'] > 16.0, spans())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# R4: after pause the user clicks into a hand-written subtitle; a late final
# with no cue left never lands on it.
mine = {'start': 40.0, 'end': 60.0, 'text': 'my hand-written line'}
eng = FakeStream(); ctrl = stream_take(eng, [mine])
feed(ctrl, UTT); at(2.6)
eng.interim('first'); eng.final('first sentence.')
ctrl.on_pause()
at(50.0); eng.final('second sentence.')
check('R4 hand-written subtitle untouched', mine['text'] == 'my hand-written line'
      and mine in subs(), spans())
check('R4 late text kept next to the take, sized by the pause position',
      (1.82, 2.6, 'second sentence.') in spans(), spans())
eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# R4b: with no gap there, the late text joins the take's last cue instead of
# adopting the subtitle next to it.
mine = {'start': 2.0, 'end': 5.0, 'text': 'mine'}
eng = FakeStream(); ctrl = stream_take(eng, [mine])
feed(ctrl, UTT); at(2.6)
eng.interim('first'); eng.final('first sentence.')
ctrl.on_pause()
eng.final('second sentence.')
check('R4b neighbour untouched, text appended to the take',
      spans() == [(0.38, 1.82, 'first sentence. second sentence.'), (2.0, 5.0, 'mine')], spans())
eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# R5: a provider that tags its sessions: take 1 is still finishing when take 2
# starts; each take's text lands in its own cues and take 2 keeps running.
class TaggedStream(FakeStream):
    stream_segment_tagged = Signal(str, dict, bool)
    stream_finished_tagged = Signal(str, list)
    stream_error_tagged = Signal(str, str)
    def __init__(s):
        super().__init__(); s.sessions = []; s.fed = []
    def stream_start(s, lang, opts=None):
        s.sessions.append(f'r{len(s.sessions) + 1}')
        return s.sessions[-1]
    def stream_feed(s, pcm): s.fed.append(s.sessions[-1])
    def interim(s, text, sid=None):
        sid = sid or s.sessions[-1]
        seg = {'start': 0, 'end': 0, 'text': text}
        s.stream_segment_tagged.emit(sid, seg, False)
        if sid == s.sessions[-1]:
            s.stream_segment.emit(seg, False)
        pump()
    def final_for(s, sid, text, start=0, end=0):
        seg = {'start': start, 'end': end, 'text': text}
        s.stream_segment_tagged.emit(sid, seg, True)
        if sid == s.sessions[-1]:
            s.stream_segment.emit(seg, True)
        pump()
    def finish_for(s, sid, segs=()):
        s.stream_finished_tagged.emit(sid, list(segs))
        if sid == s.sessions[-1]:
            s.stream_finished.emit(list(segs))
        pump()

eng = TaggedStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); ctrl.on_pause()
at(10.0); ctrl.on_play()
check('R5 take 2 streams while take 1 finishes', ctrl._stream is not None
      and ctrl._stream['sid'] == 'r2' and len(ctrl._streams) == 2)
eng.final_for('r1', 'take one sentence')
eng.finish_for('r1', [{'text': 'take one sentence'}])
check('R5 take 1 text in take 1 cue', spans() == [(0.38, 1.82, 'take one sentence')], spans())
check('R5 take 2 not torn down', ctrl._stream is not None and ctrl._stream_provider is eng
      and list(ctrl._streams) == [ctrl._stream['n']])
eng.fed.clear()
feed(ctrl, UTT)
check('R5 take 2 audio still fed to its own session', eng.fed and set(eng.fed) == {'r2'})
eng.final_for('r2', 'take two sentence')
check('R5 take 2 text in take 2 cue', spans() == [(0.38, 1.82, 'take one sentence'),
                                                   (10.38, 11.82, 'take two sentence')], spans())
ctrl.on_pause(); eng.finish_for('r2'); ctrl.shutdown()
check('R5 all settled', not ctrl._streams and not ctrl.pending_keys)

# R6: an adopted subtitle keeps its key through undo, so its answer lands.
other = {'start': 5.0, 'end': 6.0, 'text': 'other'}
old = {'start': 10.2, 'end': 12.0, 'text': 'old words'}
reset([other, old], 10.0)
history.history_append(); other['text'] = 'other, edited'
ctrl = record_controls.RecordController(Host())
eng = HeldASR(); ctrl._resolve_asr_provider = lambda: eng
ctrl.on_play()
feed(ctrl, UTT)
history.history_undo()
eng.answer(text='new words')
check('R6 adopted subtitle re-transcribed after an undo',
      texts() == ['other', 'new words'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# R7 (U2): two cuts over one existing subtitle -> nothing stays pending.
eng = FakeStream(); ctrl = stream_take(eng, [{'start': 0.2, 'end': 6.0, 'text': 'long existing'}])
feed(ctrl, UTT); feed(ctrl, UTT); at(5.2)
eng.interim('x'); eng.final('x')
eng.interim('y'); eng.final('y')
check('R7 both answers in the adopted subtitle', texts() == ['x y'], texts())
check('R7 nothing pending', not ctrl.pending_keys, ctrl.pending_keys)
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# R8 (U7): the JSON save strips runtime keys from `current` as well.
ctrl, eng = batch_take()
feed(ctrl, UTT)
session.SUBTITLE['current'] = subs()[0]
session.SUBTITLE['selected'] = subs()[0]
session.FORMAT = {'format': 'JSON', 'options': {'standard': 'Whisper'}}
file_io.save_file(os.path.join(tmp, 'y.json'), 'JSON')
session.FORMAT = {}
check('R8 _rec not saved through current/selected', '_rec' not in open(os.path.join(tmp, 'y.json')).read())

# R9 (U9): removing a selected noise cue clears the selection to None.
eng.answer(segs=[])
check('R9 selection cleared to None', subs() == [] and session.SUBTITLE.get('selected') is None,
      session.SUBTITLE.get('selected'))
session.SUBTITLE.pop('current', None)
ctrl.on_pause(); pump(); ctrl.shutdown()

# R10 (U6): the drain backstop counts from the last answer, not from Stop.
ctrl, eng = batch_take()
feed(ctrl, UTT); feed(ctrl, UTT)
ctrl.on_pause(); pump()
check('R10 a backstop is armed at pause', len(TIMERS) == 1, TIMERS)
first = TIMERS.pop(0) if TIMERS else (0, lambda: None)
eng.answer(text='a')
first[1]()
check('R10 an answer restarts the backstop', ctrl._draining_lives and len(ctrl.pending_keys) == 1)
fire_timers(30000)
check('R10 the latest backstop still abandons', not ctrl._draining_lives and not ctrl.pending_keys
      and texts() == ['a', ''], texts())
ctrl.shutdown()

# R11 (U1): a split survives undoing an unrelated later edit.
later = {'start': 30.0, 'end': 31.0, 'text': 'mine'}
ctrl, eng = batch_take([later])
feed(ctrl, np.concatenate([sil(0.5), tone(3.0), sil(0.9)]))
history.history_append(); later['text'] = 'mine, edited'
eng.answer(segs=[{'start': 0.1, 'end': 1.4, 'text': 'first half'},
                 {'start': 1.6, 'end': 3.0, 'text': 'second half'}])
history.history_undo()
check('R11 split kept after undoing the unrelated edit',
      texts() == ['first half', 'second half', 'mine'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# R12 (U10): a noise cue removed after an unrelated edit stays removed on undo.
later = {'start': 30.0, 'end': 31.0, 'text': 'mine'}
ctrl, eng = batch_take([later])
feed(ctrl, UTT)
history.history_append(); later['text'] = 'mine, edited'
eng.answer(segs=[])
history.history_undo()
check('R12 noise cue does not come back with the undo', texts() == ['mine'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# R15: text for a cue the user deleted reaches the stored copies from before
# the deletion, even with other edits after it.
later = {'start': 30.0, 'end': 31.0, 'text': 'mine'}
ctrl, eng = batch_take([later])
feed(ctrl, UTT)
subtitles_mod.remove_subtitle(subs()[0])        # pushes its own undo step
history.history_append(); later['text'] = 'mine, edited'
eng.answer(text='late words')
check('R15 not recreated', texts() == ['mine, edited'], texts())
history.history_undo(); history.history_undo()
check('R15 undoing the deletion brings it back with its text',
      texts() == ['late words', 'mine'], texts())
ctrl.on_pause(); pump(); ctrl.shutdown()

# R13: a batch take started while a stream is still finishing is not
# attached to that stream.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); ctrl.on_pause()
held = HeldASR()
ctrl._resolve_shared_asr_provider = lambda: None
ctrl._resolve_asr_provider = lambda: held
at(20.0); ctrl.on_play()
feed(ctrl, UTT)
stream_orders = [len(st['order']) for st in ctrl._streams.values()]
check('R13 batch cue not added to the finishing stream', stream_orders == [1], stream_orders)
held.answer(text='batch text')
eng.final('stream text')
check('R13 each text in its own take', texts() == ['stream text', 'batch text'], spans())
ctrl.on_pause(); eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# --------------------------------------------------------------------------
# Second review.

# Q1: a timed final that arrives before its cue is cut waits for the cut,
# instead of joining the previous subtitle.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); at(2.6)
eng.final('Phrase one.', start=0.1, end=1.9)
feed(ctrl, np.concatenate([tone(1.2), sil(0.3)]))              # still in speech
eng.final('Phrase two.', start=1.8, end=4.0)                   # pre-roll reaches cue 1
check('Q1 early final not glued to the previous subtitle',
      spans() == [(0.38, 1.82, 'Phrase one.')], spans())
feed(ctrl, sil(0.6))                                            # now the cut
check('Q1 it fills its own cue once cut',
      spans() == [(0.38, 1.82, 'Phrase one.'), (2.48, 3.92, 'Phrase two.')], spans())
ctrl.on_pause(); eng.stream_finished.emit([]); pump()
check('Q1 nothing removed as noise', len(subs()) == 2, spans())
ctrl.shutdown()

# Q1b: noisy room, the VAD only cuts at max length: the timed sentences that
# arrived meanwhile split that cue.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(9.0)])); at(9.5)
for text, a, b in (('Sentence one.', 0.4, 3.2), ('Sentence two.', 3.3, 6.1), ('Sentence three.', 6.2, 9.3)):
    eng.final(text, start=a, end=b)
check('Q1b nothing placed before a cut', subs() == [], spans())
ctrl.on_pause(); eng.stream_finished.emit([]); pump()
check('Q1b one subtitle per sentence', [t for _, _, t in spans()] ==
      ['Sentence one.', 'Sentence two.', 'Sentence three.'], spans())
ctrl.shutdown()

# Q2: untimed engine keeps a sentence across a pause the VAD cut at; the
# interim text kept growing across the cut, so the cues are merged and later
# answers stay in place.
eng = TaggedStream(); ctrl = stream_take(eng)
feed(ctrl, np.concatenate([sil(0.5), tone(1.2)])); at(1.7); eng.interim('the quick')
feed(ctrl, sil(0.7)); at(2.4)
feed(ctrl, tone(1.2)); at(3.6); eng.interim('the quick brown fox')
feed(ctrl, sil(0.9)); at(4.5); eng.final_for('r1', 'The quick brown fox.')
feed(ctrl, tone(1.2)); at(5.7); eng.interim('jumps over')
feed(ctrl, sil(0.9)); at(6.6); eng.final_for('r1', 'Jumps over.')
check('Q2 continued sentence merged, next answer in its own cue',
      spans() == [(0.38, 3.72, 'The quick brown fox.'), (4.38, 5.82, 'Jumps over.')], spans())
ctrl.on_pause(); eng.finish_for('r1'); ctrl.shutdown()
check('Q2 a restarted interim is not a continuation',
      not record_controls._extends('jumps over', 'the quick brown fox')
      and not record_controls._extends('the quick', 'the quick brown fox')
      and record_controls._extends('The quick brown, fox', 'the quick'))

# Q3: recording a line again while the first take is still finishing: the
# new take owns it; the old answer is dropped and nothing stays pending.
for timed in (False, True):
    eng = TaggedStream(); ctrl = stream_take(eng)
    feed(ctrl, UTT); ctrl.on_pause()
    at(0.0); ctrl.on_play()
    feed(ctrl, UTT); ctrl.on_pause()
    span = (0.4, 1.8) if timed else (0, 0)
    eng.final_for('r1', 'first try', *span); eng.finish_for('r1')
    eng.final_for('r2', 'second try', *span); eng.finish_for('r2')
    for ms, fn in list(TIMERS):
        fn()
    check(f'Q3 re-take wins, nothing pending ({"timed" if timed else "untimed"})',
          spans() == [(0.38, 1.82, 'second try')] and not ctrl.pending_keys and not ctrl._streams,
          (spans(), ctrl.pending_keys))
    ctrl.shutdown()

# Q4: an empty answer never takes away a subtitle that already has its text
# (a cue shared by two answers: the first filled it, the second heard nothing).
reset(pos=0.0)
ctrl = record_controls.RecordController(Host())
ctrl._take_token = 'q4'
rec = ctrl._create_placeholder(1.0, 2.0)
assert ctrl._create_placeholder(1.2, 1.8) is rec and rec['outstanding'] == 2
ctrl._fill(rec, 'hello world'); ctrl._release(rec)
ctrl._discard_if_untouched(rec)
check('Q4 filled cue kept after an empty answer', texts() == ['hello world'], texts())
ctrl.shutdown()

# Q5: the stream backstop counts from the engine's last word.
eng = TaggedStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); feed(ctrl, UTT); feed(ctrl, UTT); at(7.8)
ctrl.on_pause()
at_pause = TIMERS.pop(0)
eng.final_for('r1', 'one', 0.0, 2.3)
eng.final_for('r1', 'two', 1.9, 4.6)
at_pause[1]()
check('Q5 still listening after the pause backstop', len(ctrl._streams) == 1)
eng.final_for('r1', 'three', 4.5, 7.2)
check('Q5 slow engine keeps its last answer', [t for _, _, t in spans()] == ['one', 'two', 'three'], spans())
fire_timers(30000)
check('Q5 a silent engine is still settled', not ctrl._streams and not ctrl.pending_keys)
ctrl.shutdown()

# Q6: a timed sentence over two cues is not lost when the user deleted or
# typed into the first, short one.
for action in ('deleted', 'typed'):
    eng = FakeStream(); ctrl = stream_take(eng)
    feed(ctrl, np.concatenate([sil(0.5), tone(0.5), sil(0.7)]))
    feed(ctrl, np.concatenate([tone(2.5), sil(0.9)])); at(5.0)
    ctrl.on_pause()
    first = subs()[0]
    if action == 'deleted':
        subtitles_mod.remove_subtitle(first)
    else:
        first['text'] = 'So,'
    eng.final('so the quick brown fox jumps', start=0.0, end=4.6)
    want = [(1.58, 4.32, 'so the quick brown fox jumps')]
    if action == 'typed':
        want = [(0.38, 1.12, 'So,')] + want
    check(f'Q6 sentence lands in the cue that is still free ({action})', spans() == want, spans())
    eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# Q6b: ...but if the user deleted or typed into the MAIN cue of the sentence,
# the sentence is theirs: it is not moved into the leftover piece.
for action in ('deleted', 'typed'):
    eng = FakeStream(); ctrl = stream_take(eng)
    feed(ctrl, np.concatenate([sil(0.5), tone(2.5), sil(0.7)]))
    feed(ctrl, np.concatenate([tone(0.5), sil(0.9)])); at(6.0)
    ctrl.on_pause()
    main = subs()[0]
    if action == 'deleted':
        subtitles_mod.remove_subtitle(main)
    else:
        main['text'] = 'my own sentence'
    eng.final('the quick brown fox jumps', start=0.2, end=4.7)
    kept = [] if action == 'deleted' else [(0.38, 3.12, 'my own sentence')]
    check(f'Q6b main cue {action}: sentence not moved into the leftover',
          spans() == kept + [(3.58, 4.32, '')], spans())
    eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# Q7: a pending cue the user moved still gets its timed answer.
eng = FakeStream(); ctrl = stream_take(eng)
feed(ctrl, UTT); at(2.6); ctrl.on_pause()
subtitles_mod.move_subtitle(subs()[0], amount=5.0)
eng.final('moved', start=0.4, end=1.8)
check('Q7 moved cue filled, no duplicate', spans() == [(5.38, 6.82, 'moved')], spans())
eng.stream_finished.emit([]); pump(); ctrl.shutdown()

# Q8: the input device fails after the engine session opened: it is closed.
eng = TaggedStream(); eng.stops = 0
eng.stream_stop = lambda: setattr(eng, 'stops', eng.stops + 1)
reset(); ctrl = record_controls.RecordController(Host())
ctrl._resolve_shared_asr_provider = lambda: eng
real_recorder = recorder_mod.AudioRecorder
class _Boom:
    def __init__(s, *a, **k): raise RuntimeError('device unavailable')
recorder_mod.AudioRecorder = _Boom
try:
    ctrl.on_play()
finally:
    recorder_mod.AudioRecorder = real_recorder
check('Q8 failed start closes its session', ctrl.status == 'error' and eng.stops == 1
      and not ctrl._streams and ctrl._stream is None, (ctrl.status, eng.stops, ctrl._streams))
ctrl.shutdown()

# --------------------------------------------------------------------------
# Private engines (batch path): one per take used to leak, each an add-on
# process holding its model.
class PoolASR(HeldASR):
    made = []
    def __init__(s, engine='held'):
        super().__init__(); s.engine = engine; s.shut = 0
    def clone(s):
        c = PoolASR(s.engine); PoolASR.made.append(c); return c
    def shutdown(s): s.shut += 1

def pool_take(ctrl, base, pos):
    session.SUBTITLE['position'] = pos
    ctrl._resolve_shared_asr_provider = lambda: base
    ctrl.on_play()
    return ctrl._live_clone[id(ctrl._live)]

reset(pos=0.0)
PoolASR.made.clear()
ctrl = record_controls.RecordController(Host())
base = PoolASR()
c1 = pool_take(ctrl, base, 0.0)
feed(ctrl, UTT); ctrl.on_pause(); c1.answer(text='one')
check('P1 a clean take keeps its engine warm', c1.shut == 0 and not ctrl._draining_lives
      and [e['busy'] for e in ctrl._clones] == [False])
c2 = pool_take(ctrl, base, 20.0)
check('P1 the next take reuses it', c2 is c1 and len(PoolASR.made) == 1)
feed(ctrl, UTT)
ctrl.on_pause()                                  # take 2 still waiting for its answer
c3 = pool_take(ctrl, base, 40.0)                 # take 3 while take 2 drains
check('P2 overlapping takes get separate engines', c3 is not c1 and len(PoolASR.made) == 2)
feed(ctrl, UTT); ctrl.on_pause(); c3.answer(text='three')
c1.answer(text='two')
check('P2 both drained: one engine kept warm, the spare shut down',
      sorted(e['clone'].shut for e in ctrl._clones) == [0] and (c1.shut + c3.shut) == 1,
      (c1.shut, c3.shut, [e['busy'] for e in ctrl._clones]))
check('P2 texts in place', texts() == ['one', 'two', 'three'], texts())

warm = ctrl._clones[0]['clone']
c4 = pool_take(ctrl, base, 60.0)
feed(ctrl, UTT); ctrl.on_pause()
check('P3 reused again', c4 is warm)
TIMERS.clear()
ctrl._rearm_drain(ctrl._draining_lives[0])
fire_timers(30000)                               # the engine never answered
check('P3 an abandoned engine is shut down, not reused', c4.shut == 1 and ctrl._clones == [])

other = PoolASR('other')
c5 = pool_take(ctrl, base, 80.0)
feed(ctrl, UTT); ctrl.on_pause(); c5.answer(text='five')
c6 = pool_take(ctrl, other, 100.0)
check('P4 switching engines lets the idle one go', c5.shut == 1 and c6.engine == 'other'
      and [e['base'] for e in ctrl._clones] == [other])
feed(ctrl, UTT); ctrl.on_pause()
ctrl.shutdown()
check('P5 shutdown stops every private engine', c6.shut == 1 and ctrl._clones == [])

# P6: starting a private engine stops an idle shared one (one model copy).
class ReleasingASR(PoolASR):
    def __init__(s, engine='held'):
        super().__init__(engine); s.released = 0
    def clone(s):
        c = ReleasingASR(s.engine); PoolASR.made.append(c); return c
    def release_if_idle(s):
        s.released += 1; return True

reset(pos=0.0)
ctrl = record_controls.RecordController(Host())
shared = ReleasingASR()
c = pool_take(ctrl, shared, 0.0)
check('P6 the shared engine is released before the private one starts', shared.released == 1)
feed(ctrl, UTT); ctrl.on_pause(); c.answer(text='x')
pool_take(ctrl, shared, 20.0)
check('P6 ...and again when the warm private engine is reused', shared.released == 2)
ctrl.on_pause(); ctrl.shutdown()

# P7: the saved record engine is not installed: the take uses what the
# Recording tab shows (the first usable engine), and the choice is kept.
first, second = PoolASR('first'), PoolASR('second')
first.id, second.id = 'first', 'second'
class _Mgr:
    def providers_for_task(s, task): return [first, second]
    def get(s, pid): return {'first': first, 'second': second}.get(pid)
from subtitld.modules import addons as _addons
_real_get_manager = _addons.get_manager
_addons.get_manager = lambda: _Mgr()
try:
    reset(pos=0.0)
    session.CONFIG['record']['engine'] = 'whispercpp'
    ctrl = record_controls.RecordController(Host())
    ctrl._host.global_panel_import_tabwidget = types.SimpleNamespace(
        currentWidget=lambda: types.SimpleNamespace(provider=second))
    check('P7 the take uses the engine the tab shows', ctrl._resolve_shared_asr_provider() is first)
    check('P7 the saved choice is kept', session.CONFIG['record']['engine'] == 'whispercpp')
    session.CONFIG['record']['engine'] = None
    check('P7 with no saved choice the Import panel\'s still counts',
          ctrl._resolve_shared_asr_provider() is second)
    ctrl.shutdown()
finally:
    _addons.get_manager = _real_get_manager

print('\n' + ('FAIL: ' + ','.join(fails) if fails else 'ALL PLACEHOLDER TESTS PASS'))
sys.exit(1 if fails else 0)
