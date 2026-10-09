"""No audio output device (no sound card, PipeWire/PulseAudio not running, a
headless box): the audio engine, the preview panel and the main window still
build, playback is silent, and play() tries the device again. A window that
fails half-way through being built stops the add-ons catalog fetch it started
instead of letting Qt abort the process over the running QThread.

PortAudio is a stand-in: opening a stream raises what sounddevice raises with
no default output device, or hands back a fake stream. The add-ons catalog
fetch is a stand-in too, so nothing goes to the network.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import io, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import audioengine
from subtitld.modules.addons import installer
from subtitld.interface import preview_panel, addons_dialog
from subtitld import __main__ as subtitld_main

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def no_device(**_kwargs):
    raise audioengine.sd.PortAudioError('Error querying device -1')


class FakeStream:
    def __init__(self, **kwargs):
        self.calls = []

    def start(self):
        self.calls.append('start')

    def stop(self):
        self.calls.append('stop')

    def close(self):
        self.calls.append('close')


def slow_catalog(force_refresh=False, **_kwargs):
    time.sleep(1.0)
    return {}


installer.fetch_catalog = slow_catalog
audioengine.sd.OutputStream = no_device

print('the engine builds without an output device and logs it once')
stderr, sys.stderr = sys.stderr, io.StringIO()
try:
    engine = audioengine.SoundDeviceAudioEngine()
    engine.play(1.0)
    engine.seek(2.0)
    engine.pause()
    engine.stop()
    engine.play(0.5)
    log = sys.stderr.getvalue()
finally:
    sys.stderr = stderr
check('no stream', engine.stream, None)
check('one warning, naming the error', (log.count('WARNING'), 'Error querying device -1' in log), (1, True))
check('play() with no device does not claim to be playing', engine.playing, False)
check('the engine still mixes offline', engine.render_buffer(0.0, 0.1).shape, (4800, 2))

print('a device that appears later is picked up by the next play()')
audioengine.sd.OutputStream = FakeStream
engine.play(0.5)
check('stream opened and started', (type(engine.stream).__name__, engine.stream.calls, engine.playing),
      ('FakeStream', ['start'], True))
engine.shutdown()
check('pause and close on shutdown', engine.stream.calls, ['start', 'stop', 'close'])
audioengine.sd.OutputStream = no_device

print('shutdown with no device')
engine = audioengine.SoundDeviceAudioEngine()
engine.shutdown()
check('the mixer thread ends', engine._mixer.is_alive(), False)

print('the preview panel builds without an output device')
player = preview_panel.PlayerWidget()
check('its engine has no stream', player._audio_device.stream, None)
player.play()
player.pause()
check('play/pause leave the engine silent', player._audio_device.playing, False)
player._audio_device.shutdown()

print('the main window builds without an output device')
window = subtitld_main.Window()
check('a preview panel with a silent engine', window.preview_panel_player._audio_device.stream, None)
window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()

print('a window that fails half-way stops the catalog fetch it started')
fetches = []


class RecordedFetch(addons_dialog._CatalogFetchThread):
    def start(self, *args):
        fetches.append(self)
        super().start(*args)


def failing_preview_panel(_window):
    raise RuntimeError('a later panel failed')


addons_dialog._CatalogFetchThread, catalog_fetch = RecordedFetch, addons_dialog._CatalogFetchThread
preview_panel.load, load_preview_panel = failing_preview_panel, preview_panel.load
try:
    subtitld_main.Window()
    error = None
except RuntimeError as exc:
    error = str(exc)
finally:
    addons_dialog._CatalogFetchThread = catalog_fetch
    preview_panel.load = load_preview_panel
check('the error still reaches the caller', error, 'a later panel failed')
check('the fetch was started, and is no longer running', (len(fetches), any(t.isRunning() for t in fetches)), (1, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
