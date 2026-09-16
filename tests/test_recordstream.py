import sys, types, os
# Import THIS checkout's code. The venv holds a non-editable install, and
# without this the suite silently tests that stale copy instead.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'src'))
sys.modules['mediapipe'] = types.ModuleType('mediapipe')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QObject, Signal
app = QApplication(sys.argv)
from subtitld.modules import session, recorder as recorder_mod
from subtitld.interface import record_controls
recorder_mod.sd=None
recorder_mod.list_input_devices=lambda:[(0,'Mic')]; recorder_mod.default_input_device=lambda:0; recorder_mod.input_available=lambda:True
SR=recorder_mod.SAMPLE_RATE
def tone(d): t=np.arange(int(d*SR))/SR; return (0.3*np.sin(2*np.pi*220*t)).astype(np.float32)

# Fake engines are injected by stubbing controller methods rather than
# registered with the add-on manager, so tell the availability check an
# engine exists; otherwise the effective record mode is audio and the
# streaming path is never exercised.
record_controls.any_asr_provider=lambda:True


class FakeStream(QObject):
    stream_segment=Signal(dict,bool); stream_finished=Signal(list); stream_error=Signal(str)
    id='org.subtitld.realtimestt'; tasks=['asr.transcribe']
    def __init__(s): super().__init__(); s.started=None; s.fed=0; s.stopped=False
    def supports_streaming(s): return True
    def is_available(s): return True
    def stream_start(s,lang,opts=None): s.started=(lang,opts)
    def stream_feed(s,pcm): s.fed+=1
    def stream_stop(s): s.stopped=True

class Host(QWidget):
    def __init__(s):
        super().__init__()
        s.preview_panel_player=types.SimpleNamespace(is_paused=lambda:True,
            _audio_device=types.SimpleNamespace(sync_subtitle_dubs=lambda segs:None))
        s.timeline_widget=types.SimpleNamespace(update=lambda:None)
fails=[]

# ===== Handlers: interim shown separately; finals placed as subtitles at advancing playhead =====
session.CONFIG.setdefault('record',{}).update({'mode':'transcript','armed':True,'device':0})
session.SPEAKERS={}; session.SUBTITLE={'segments':[],'language':'en-us','position':5.0}
fake=FakeStream()
ctrl=record_controls.RecordController(Host())
ctrl._start_stream(fake, 5.0)
print("stream_start called with:", fake.started)
if fake.started is None: fails.append('stream_start_not_called')

fake.stream_segment.emit({'start':0,'end':0,'text':'hel'}, False); app.processEvents()
fake.stream_segment.emit({'start':0,'end':0,'text':'hello world'}, False); app.processEvents()
print("interim_text:", repr(ctrl.interim_text), " segments so far:", len(session.SUBTITLE['segments']))
if ctrl.interim_text!='hello world' or session.SUBTITLE['segments']: fails.append('interim_not_separated')

session.SUBTITLE['position']=8.0
fake.stream_segment.emit({'start':0,'end':0,'text':'hello world'}, True); app.processEvents()
session.SUBTITLE['position']=11.0
fake.stream_segment.emit({'start':0,'end':0,'text':'second phrase'}, True); app.processEvents()
subs=session.SUBTITLE['segments']
print("after 2 finals:", [(round(s['start'],1),round(s['end'],1),s['text']) for s in subs], " interim:", repr(ctrl.interim_text))
if not (len(subs)==2 and subs[0]['text']=='hello world' and subs[1]['text']=='second phrase'
        and abs(subs[0]['start']-5.0)<0.1 and abs(subs[1]['start']-8.0)<0.1):
    fails.append('finals_not_placed')
if ctrl.interim_text!='': fails.append('interim_not_cleared')

fake.stream_finished.emit(list(subs)); app.processEvents()
if ctrl._stream_provider is not None: fails.append('not_settled')

# ===== on_play chooses streaming when supported; feeds chunks; on_pause stops =====
session.SUBTITLE={'segments':[],'language':'en-us','position':0.0}; session.SPEAKERS={}
fake2=FakeStream()
ctrl2=record_controls.RecordController(Host())
ctrl2._resolve_shared_asr_provider=lambda: fake2
ctrl2.on_play()
print("streaming on_play -> stream_provider set:", ctrl2._stream_provider is fake2, " started:", fake2.started is not None)
if ctrl2._stream_provider is not fake2 or fake2.started is None: fails.append('onplay_no_stream')
import time
for _ in range(4): ctrl2._recorder.feed(tone(0.2))
for _ in range(200):          # wait for the recorder's writer thread to drain
    app.processEvents(); time.sleep(0.005)
    if fake2.fed >= 1: break
print("chunks fed to provider:", fake2.fed)
if fake2.fed < 1: fails.append('no_feed')
ctrl2.on_pause()
print("stream_stop called on pause:", fake2.stopped)
if not fake2.stopped: fails.append('no_stop')
ctrl2.shutdown()

# ===== AddonASRProvider: a finishing session never touches the newer one =====
from subtitld.modules.addons.addon_provider import AddonASRProvider

class FakeReq(QObject):
    partial=Signal(dict); result=Signal(dict); error=Signal(str,str); progress=Signal(float,str)
    def __init__(s, rid): super().__init__(); s.id=rid

class FakeProc:
    def __init__(s): s.reqs=[]; s.sent=[]
    def is_running(s): return True
    def request(s, task, params, timeout=None):
        s.reqs.append(FakeReq(f'q{len(s.reqs)+1}')); return s.reqs[-1]
    def stream_send(s, rid, frame): s.sent.append(rid)

prov=AddonASRProvider('realtimestt', {'id':'realtimestt','tasks':['asr.transcribe','asr.stream']}, '/nonexistent')
proc=FakeProc(); prov._process=proc; prov._ensure_process=lambda: proc
seen={'tagged':[], 'plain':[], 'done_tagged':[], 'done_plain':[]}
prov.stream_segment_tagged.connect(lambda sid,seg,final: seen['tagged'].append((sid,seg['text'])))
prov.stream_segment.connect(lambda seg,final: seen['plain'].append(seg['text']))
prov.stream_finished_tagged.connect(lambda sid,segs: seen['done_tagged'].append(sid))
prov.stream_finished.connect(lambda segs: seen['done_plain'].append(len(segs)))
sid1=prov.stream_start('en'); prov.stream_stop()
sid2=prov.stream_start('en')
r1,r2=proc.reqs
r1.partial.emit({'text':'late one','final':True}); r1.result.emit({'segments':[{'text':'late one'}]}); app.processEvents()
print("provider:", sid1, sid2, seen)
if (sid1, sid2) != ('q1','q2'): fails.append('provider_no_ids')
if seen['tagged']!=[('q1','late one')] or seen['plain']: fails.append('provider_late_leaked_untagged')
if seen['done_tagged']!=['q1'] or seen['done_plain']: fails.append('provider_late_finish_leaked')
if prov._stream_request is not r2: fails.append('provider_late_result_cleared_newer_request')
prov.stream_feed(b'\x00\x00')
if proc.sent[-1]!='q2': fails.append('provider_feeds_wrong_session')
r2.partial.emit({'text':'current','final':False}); app.processEvents()
if seen['plain']!=['current'] or seen['tagged'][-1]!=('q2','current'): fails.append('provider_current_not_both')
r2.result.emit({'segments':[]}); app.processEvents()
if prov._stream_request is not None or seen['done_plain']!=[0]: fails.append('provider_current_finish')

# ===== Host paths and saved options reach the add-on =====
from subtitld.modules.addons import addon_provider as ap, registry
import os as _os
env=ap._build_addon_env('whispercpp', {'model':'large-v3'}, {'id':'whispercpp'})
if env.get('SUBTITLD_MODELS_DIR')!=str(session.PATH_SUBTITLD_DATA_MODELS): fails.append('env_models_dir')
if env.get('SUBTITLD_TEMP_DIR')!=str(session.PATH_TEMP): fails.append('env_temp_dir')
if env.get('WHISPERCPP_MODEL')!='large-v3': fails.append('env_option')
ff=env.get('SUBTITLD_FFMPEG_EXECUTABLE')
if ff and not _os.path.isfile(ff): fails.append('env_ffmpeg_not_a_file')
_os.environ['SUBTITLD_MODELS_DIR']='/elsewhere'
if ap._build_addon_env('x', None).get('SUBTITLD_MODELS_DIR')!='/elsewhere': fails.append('env_override_lost')
del _os.environ['SUBTITLD_MODELS_DIR']

session.CONFIG.setdefault('addons',{})
registry.set_options_for('realtimestt', {'model':'medium','device':'cpu'})
captured=[]
proc.request=lambda task, params, timeout=None: (captured.append(params), FakeReq('q9'))[1]
prov.stream_start('en', {'model':'tiny'})
prov.transcribe('/tmp/x.wav', 'en', None)
print("request options:", [c['options'] for c in captured])
if captured[0]['options']!={'model':'tiny','device':'cpu'}: fails.append('stream_options_not_merged')
if captured[1]['options']!={'model':'medium','device':'cpu'}: fails.append('transcribe_options_not_merged')

print("\n"+("FAIL: "+",".join(fails) if fails else "RECORD STREAMING TESTS PASS"))
sys.exit(1 if fails else 0)
