"""Sound and picture together: the engine's heard position (the playhead less
the device's output latency, never before the last play or seek), play()
starting the picture that latency after the sound, WASAPI first on Windows
(MME understates what it holds), and the timeline zoom kept
in range (a zoom of 0, which the keys could reach, hid the timeline for good).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile, threading
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

print('the heard position')
engine = audioengine.SoundDeviceAudioEngine.__new__(audioengine.SoundDeviceAudioEngine)
engine._playhead_lock = threading.Lock()
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

print('play: the picture after the sound')
from subtitld.interface.preview_panel import PlayerWidget
widget = PlayerWidget()
started = []
device = widget._audio_device
device.play = lambda position=0.0: (started.append(position), setattr(device, 'playing', True))
device.pause = lambda: setattr(device, 'playing', False)
device.output_latency = 0.3
session.SUBTITLE['position'] = 4.0
widget.play()
check('the sound started where the cursor is', started, [4.0])
check('the picture waits the output latency', (widget._video_start_timer.isActive(),
                                               abs(widget._video_start_timer.remainingTime() - 300) <= 30), (True, True))
check('meanwhile, playing: a second Space pauses', widget.is_paused(), False)
widget.pause()
check('pausing first: the picture never starts', widget._video_start_timer.isActive(), False)
device.output_latency = 0.0
widget.play()
check('no latency known: the picture at once', widget._video_start_timer.isActive(), False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
