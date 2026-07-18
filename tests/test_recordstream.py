import sys, types, os
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

print("\n"+("FAIL: "+",".join(fails) if fails else "RECORD STREAMING TESTS PASS"))
sys.exit(1 if fails else 0)
