"""Without OpenCV, edge-tts and aiohttp, which some platforms do not package
(Haiku): Subtitld still starts. Edge is not offered, and asked anyway it
answers each line with an error instead of failing; face extraction says why
it cannot run, as it does without MediaPipe.

The three are hidden from the imports; the add-ons catalog fetch is a
stand-in, so nothing goes to the network.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import

MISSING = ('cv2', 'edge_tts', 'aiohttp')


class NotInstalled:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in MISSING:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return None


sys.meta_path.insert(0, NotInstalled())

from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session, subtitles
from subtitld.modules.addons import installer
from subtitld.modules.addons.builtin import edge_tts_provider
from subtitld.interface import left_panel_speakers
from subtitld import __main__ as subtitld_main

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}

print('the three are really missing')
check('hidden', [m for m in MISSING if m in sys.modules], [])

print('Edge: not offered, and asked anyway, an error per line')
check('available', edge_tts_provider.available(), False)
provider = edge_tts_provider.get_provider()
errors = []
provider.speech_error.connect(lambda uid, subtitle, message: errors.append((uid, message)))
provider.generate_speeches([{'uid': 'a', 'text': 'One'}, {'uid': 'b', 'text': 'Two'}])
check('one error per line, saying why', errors,
      [('a', 'edge-tts: not installed in this Subtitld'), ('b', 'edge-tts: not installed in this Subtitld')])
provider.refresh_voices()
check('no voice list fetch started', provider._voices_thread, None)

print('face extraction without OpenCV says why, as without MediaPipe')
session.VIDEO = {'filepath': '/nowhere/video.mp4', 'duration': 60}
subtitles.get_speaker_intervals = lambda name: [(1.0, 2.0)]
thread = left_panel_speakers.FaceExtractorThread()
thread.name = 'Speaker'
said = []
thread.error.connect(said.append)
thread.run()  # in this thread: the signal is delivered at once
check('the reason', said, ['Face detection is unavailable: OpenCV is not installed.'])

print('the main window builds')
window = subtitld_main.Window()
check('a window', type(window).__name__, 'Window')
window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
