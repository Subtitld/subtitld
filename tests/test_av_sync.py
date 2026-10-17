"""Sound and picture together: the engine's heard position (the playhead less
the device's output latency, never before the last play or seek), play()
starting the picture that latency after the sound, WASAPI first on Windows
(MME understates what it holds), and the timeline zoom kept
in range (a zoom of 0, which the keys could reach, hid the timeline for good).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import audioengine, config, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('the zoom, as saved')
folder = Path(tempfile.mkdtemp())
for saved, want in ((0.0, 100.0), (-20.0, 100.0), ('x', 100.0), (True, 100.0), (5.0, 10.0), (1000, 490.0),
                    (55.5, 55.5)):
    path = folder / 'subtitld.config'
    path.write_text(json.dumps({'timeline_zoom': saved}), encoding='utf-8')
    check(f'{saved!r} read as', config.Config(path)['timeline_zoom'], want)
path.unlink()
check('none saved: the default', config.Config(path)['timeline_zoom'], 100.0)

print('the heard position, without the device\'s times')
engine = audioengine.SoundDeviceAudioEngine.__new__(audioengine.SoundDeviceAudioEngine)
engine._playhead_lock = threading.Lock()
engine._dac_anchor, engine.stream = None, None
engine._playhead, engine._anchor, engine.output_latency, engine.speed, engine.playing = 10.4, 10.0, 0.4, 1.0, False
check('paused: the playhead', engine.heard_position, 10.4)
engine.playing = True
check('playing: the playhead less the latency', round(engine.heard_position, 6), 10.0)
engine._playhead = 12.0
check('later on', round(engine.heard_position, 6), 11.6)
engine._playhead = 10.2
check('the latency not yet out: where play started', engine.heard_position, 10.0)
engine.speed, engine._playhead = 2.0, 12.0
check('at twice the speed, twice the content in the buffer', round(engine.heard_position, 6), 11.2)
check('the picture waits the latency', engine.seconds_until_heard(), 0.4)

print('the heard position, from the device\'s times')
clock = [500.0]
real_perf_counter = time.perf_counter
audioengine.time.perf_counter = lambda: clock[0]
callback_time = type('TimeInfo', (), {})()
engine.speed, engine._playhead, engine._anchor = 1.0, 10.0, 10.0
engine._ring = type('Ring', (), {'pop_into': lambda self, out, frames: frames})()
engine.samplerate = 48000
# A callback (perf_counter 500.0; PortAudio's clock reads 100.0 there) hands
# over 10.0 s - 10.085 s, heard from 40 ms on.
callback_time.currentTime, callback_time.outputBufferDacTime = 100.0, 100.040
engine._callback(None, 4096, callback_time, None)
check('the callback notes its block and when it is heard, on perf_counter\'s clock',
      (engine._dac_anchor[0], round(engine._dac_anchor[1], 6)), (10.0, 500.04))
check('the picture waits until then', round(engine.seconds_until_heard(), 6), 0.04)
clock[0] = 500.020
check('before it is heard: where play started', engine.heard_position, 10.0)
clock[0] = 500.070
check('then the block, as it plays: 30 ms in', round(engine.heard_position, 6), 10.03)
clock[0] = 500.200
check('never past what was handed over', round(engine.heard_position, 6), round(10.0 + 4096 / 48000, 6))
callback_time.outputBufferDacTime = 0.0
engine._callback(None, 4096, callback_time, None)
check('a host API without the times: the last ones kept', round(engine._dac_anchor[1], 6), 500.04)
engine._mixer, engine.tracks = type('Mixer', (), {'reseek': lambda self, seconds: None})(), []
engine.seek(30.0)
check('after a seek, no times until the next callback', (engine._dac_anchor, engine.heard_position), (None, 30.0))
audioengine.time.perf_counter = real_perf_counter

print('the output: WASAPI first on Windows')
real_platform, real_hostapis = audioengine.sys.platform, audioengine.sd.query_hostapis
audioengine.sd.query_hostapis = lambda: ({'name': 'MME', 'default_output_device': 3},
                                         {'name': 'Windows DirectSound', 'default_output_device': 5},
                                         {'name': 'Windows WASAPI', 'default_output_device': 7})
try:
    audioengine.sys.platform = 'win32'
    choices = audioengine.SoundDeviceAudioEngine._output_choices()
    check('Windows: WASAPI\'s default output, converting, then the default',
          [(device, type(extra).__name__) for device, extra in choices], [(7, 'WasapiSettings'), (None, 'NoneType')])
    audioengine.sd.query_hostapis = lambda: ({'name': 'MME', 'default_output_device': 3},
                                             {'name': 'Windows WASAPI', 'default_output_device': -1})
    check('Windows, no WASAPI output: the default', audioengine.SoundDeviceAudioEngine._output_choices(), [(None, None)])
    audioengine.sys.platform = 'linux'
    check('elsewhere: the default', audioengine.SoundDeviceAudioEngine._output_choices(), [(None, None)])
finally:
    audioengine.sys.platform, audioengine.sd.query_hostapis = real_platform, real_hostapis

print('play: the picture when the sound is heard')
from subtitld.interface.preview_panel import PlayerWidget
widget = PlayerWidget()
started, pictures = [], []
device = widget._audio_device
device.play = lambda position=0.0: (started.append(position), setattr(device, 'playing', True),
                                    setattr(device, '_anchor', position), setattr(device, '_dac_anchor', None))
device.pause = lambda: setattr(device, 'playing', False)
widget._media_player.play = lambda: pictures.append(time.perf_counter())
device.output_latency = 0.3
session.SUBTITLE['position'] = 4.0
widget.play()
check('the sound started where the cursor is', started, [4.0])
check('no callback yet: it looks again in 10 ms', (widget._video_start_timer.isActive(),
                                                   widget._video_start_timer.interval(), pictures), (True, 10, []))
check('meanwhile, playing: a second Space pauses', widget.is_paused(), False)
# The first callback: 4.0 s is heard 120 ms from now.
device._dac_anchor = (4.0, time.perf_counter() + 0.12)
t0 = time.perf_counter()
QTest.qWait(400)
check('the picture starts when the sound is heard (within 20 ms)', len(pictures) == 1 and abs(pictures[0] - t0 - 0.12) < 0.02,
      True)
widget.play()
widget.pause()
QTest.qWait(400)
check('pausing first: the picture never starts', (widget._video_start_timer.isActive(), len(pictures)), (False, 1))
widget.play()
QTest.qWait(500)
check('a host API that never gives the times: after 300 ms of looking, the stream\'s latency', len(pictures), 1)
QTest.qWait(700)
check('then it starts', len(pictures), 2)
device.play = lambda position=0.0: (started.append(position), setattr(device, 'playing', False))
widget.play()
check('no sound device: the picture at once', len(pictures), 3)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
