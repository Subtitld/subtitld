import sys, types, os
sys.modules['mediapipe'] = types.ModuleType('mediapipe')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QObject, Signal
app = QApplication(sys.argv)
from subtitld.modules import session, recorder as recorder_mod
from subtitld.interface import record_controls
import soundfile as sf
recorder_mod.sd=None
recorder_mod.list_input_devices=lambda:[(0,'Mic')]; recorder_mod.default_input_device=lambda:0; recorder_mod.input_available=lambda:True
SR=recorder_mod.SAMPLE_RATE
def tone(d,a=0.3): t=np.arange(int(d*SR))/SR; return (a*np.sin(2*np.pi*220*t)).astype(np.float32)
def sil(d): return np.zeros(int(d*SR),np.float32)
class FakeASR(QObject):
    transcript_finished=Signal(list); error=Signal(str); id='fake'; tasks=['asr.transcribe']
    def supports_streaming(self): return False
    def transcribe(self,p,l,o):
        d=sf.info(p); self.transcript_finished.emit([{'start':0.0,'end':d.frames/d.samplerate,'text':'hi'}])
class Host(QWidget):
    def __init__(s):
        super().__init__()
        s.preview_panel_player=types.SimpleNamespace(is_paused=lambda:True,
            _audio_device=types.SimpleNamespace(sync_subtitle_dubs=lambda segs:None))
        s.timeline_widget=types.SimpleNamespace(update=lambda:None)
fails=[]

def drive_transcript(base, segments, selected=None):
    session.CONFIG.setdefault('record',{}); session.CONFIG['record'].update({'mode':'transcript','armed':True,'device':0})
    session.SPEAKERS={}; session.SUBTITLE={'segments':segments,'language':'en-us','position':base}
    if selected is not None: session.SUBTITLE['selected']=selected
    ctrl=record_controls.RecordController(Host()); fake=FakeASR(); ctrl._resolve_asr_provider=lambda:fake
    ctrl.on_play()
    stream=np.concatenate([sil(0.6),tone(1.5),sil(0.9),tone(1.2),sil(0.8)]); blk=int(0.1*SR)
    for i in range(0,len(stream),blk): ctrl._recorder.feed(stream[i:i+blk])
    ctrl.on_pause()
    for _ in range(120): app.processEvents()
    ctrl.shutdown()

# ===== TRANSCRIPT: no subtitle under cursor -> new subtitles from phrase boundaries =====
drive_transcript(10.0, [])
subs=session.SUBTITLE['segments']
print("[1] no-sub transcript (base=10):", [(round(s['start'],2),s['text']) for s in subs])
if not (len(subs)==2 and subs[0]['start']>=10.0 and (subs[1]['start']-subs[0]['start'])>2.0 and all(s['text']=='hi' for s in subs)):
    fails.append('transcript_new')

# ===== TRANSCRIPT: subtitle under cursor -> text REPLACED (old text discarded), no new subtitle =====
existing={'start':10.0,'end':20.0,'text':'old text to discard','speaker':'A'}   # spans both utterances
drive_transcript(10.0, [existing])
subs=session.SUBTITLE['segments']
print("[2] under-cursor replace:", len(subs), "sub(s); text=", repr(existing['text']))
if not (len(subs)==1 and existing['text']=='hi hi'):   # replaced on 1st utterance, appended on 2nd
    fails.append('transcript_replace')

# ===== TRANSCRIPT ISOLATION: cloned provider -> Import-panel handler must NOT wipe subtitles =====
shared=FakeASR(); wiped={'n':0}
def import_handler(segs):
    wiped['n']+=1
    session.SUBTITLE['segments']=list(segs)   # mimics left_panel_import._on_finished (full replace)
shared.transcript_finished.connect(import_handler)
session.CONFIG['record'].update({'mode':'transcript','armed':True,'device':0})
session.SPEAKERS={}; session.SUBTITLE={'segments':[{'start':100.0,'end':102.0,'text':'keep me','speaker':'A'}],'language':'en-us','position':10.0}
ctrl_iso=record_controls.RecordController(Host())
ctrl_iso._resolve_shared_asr_provider=lambda: shared   # real resolver clones this via type(base)()
ctrl_iso.on_play()
st=np.concatenate([sil(0.6),tone(1.5),sil(0.9)]); blk=int(0.1*SR)
for i in range(0,len(st),blk): ctrl_iso._recorder.feed(st[i:i+blk])
ctrl_iso.on_pause()
for _ in range(120): app.processEvents()
subs=session.SUBTITLE['segments']
kept=any(s['text']=='keep me' for s in subs)
print(f"[2b] isolation: import_handler_fired={wiped['n']} kept_existing={kept} total={len(subs)}")
if wiped['n']!=0 or not kept: fails.append('isolation')
ctrl_iso.shutdown()

# ===== WAVE: dub on the subtitle UNDER the cursor; cue grows to cover take, clip not stretched =====
session.CONFIG['record']['mode']='audio'
under={'start':5.0,'end':8.0,'text':'under'}
other={'start':30.0,'end':33.0,'text':'other'}
session.SUBTITLE={'segments':[under,other],'selected':other,'language':'en-us','position':6.0}  # cursor at 6 -> inside `under`
session.SPEAKERS={}
ctrl2=record_controls.RecordController(Host())
ctrl2.on_play()
for _ in range(3): ctrl2._recorder.feed(tone(1.0))   # 3s take, base=6 -> take ends ~9
ctrl2.on_pause()
for _ in range(20): app.processEvents()
print("[3] wave under-cursor: under=",round(under['start'],1),"-",round(under['end'],2)," under.dub=",len(under.get('dubbing',[]))," other.dub=",len(other.get('dubbing',[])))
ok=False
if under.get('dubbing') and not other.get('dubbing'):
    d=under['dubbing'][0]; wav=d.get('path')
    dur=sf.info(wav).frames/sf.info(wav).samplerate if (wav and os.path.isfile(wav)) else 0
    clip_span=d.get('end')-d.get('start')
    print(f"    dub start={d.get('start')} end={round(d.get('end'),2)} span={round(clip_span,2)} filedur={round(dur,2)} cue.end={round(under['end'],2)}")
    ok=(d.get('engine')=='recording' and abs(d.get('start')-6.0)<0.01
        and abs(clip_span-dur)<0.05           # not stretched: span == file duration
        and abs(under['end']-(6.0+dur))<0.05)  # cue grew to cover the take (next is far)
if not ok: fails.append('wave_under_cursor')
ctrl2.shutdown()

# ===== WAVE: take PASSES the next subtitle -> cue stops at next.start, clip overflows, no stretch =====
under2={'start':5.0,'end':7.0,'text':'here'}
nextsub={'start':9.0,'end':12.0,'text':'do not touch'}
session.SUBTITLE={'segments':[under2,nextsub],'language':'en-us','position':6.0}  # base=6 inside under2
session.SPEAKERS={}
ctrl2b=record_controls.RecordController(Host())
ctrl2b.on_play()
for _ in range(5): ctrl2b._recorder.feed(tone(1.0))   # 5s take, base=6 -> would reach ~11, past next.start=9
ctrl2b.on_pause()
for _ in range(20): app.processEvents()
okb=False
if under2.get('dubbing') and not nextsub.get('dubbing'):
    d=under2['dubbing'][0]; wav=d.get('path')
    dur=sf.info(wav).frames/sf.info(wav).samplerate if (wav and os.path.isfile(wav)) else 0
    span=d.get('end')-d.get('start')
    print(f"[3b] passes-next: cue.end={round(under2['end'],2)} (next.start=9) clip=[{d.get('start')},{round(d.get('end'),2)}] span={round(span,2)} filedur={round(dur,2)}")
    okb=(abs(under2['end']-9.0)<0.05                # cue finishes exactly at next.start
         and d.get('end')-9.0>0.3                   # clip overflows past next.start
         and abs(span-dur)<0.05)                    # clip not stretched
else:
    print("[3b] passes-next: FAILED to attach or touched next subtitle")
if not okb: fails.append('wave_passes_next')
ctrl2b.shutdown()

# ===== WAVE: no subtitle under cursor, none selected -> creates one =====
session.SUBTITLE={'segments':[],'language':'en-us','position':2.0}; session.SPEAKERS={}
ctrl3=record_controls.RecordController(Host())
ctrl3.on_play()
for _ in range(2): ctrl3._recorder.feed(tone(1.0))
ctrl3.on_pause()
for _ in range(20): app.processEvents()
segs=session.SUBTITLE['segments']
print("[4] wave no-target -> created", len(segs), "subtitle(s)")
if not (len(segs)==1 and segs[0].get('dubbing')): fails.append('wave_create')
ctrl3.shutdown()

# ===== no ASR engine -> status reflects it, audio still captured =====
session.CONFIG['record'].update({'mode':'transcript','armed':True})
session.SUBTITLE={'segments':[],'language':'en-us','position':0.0}; session.SPEAKERS={}
ctrl4=record_controls.RecordController(Host()); ctrl4._resolve_asr_provider=lambda:None
ctrl4.on_play()
print("[5] no-engine status:", ctrl4.status, " recording=", ctrl4.is_recording)
if ctrl4.status!='no-engine' or ctrl4.is_recording: fails.append('no_engine_status')
ctrl4.shutdown()

# ===== state via config =====
session.CONFIG['record']['mode']='transcript'; session.CONFIG['record']['armed']=False
c=record_controls.RecordController(Host())
print("[6] state: armed=",c.armed," mode=",c.mode)
if c.armed or c.mode!='transcript': fails.append('state')

print("\n"+("FAIL: "+",".join(fails) if fails else "ALL RECORDCONTROLLER TESTS PASS"))
sys.exit(1 if fails else 0)
