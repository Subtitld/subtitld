"""The preview draws the subtitle again after an edit: merging two
subtitles left it showing the ones before until something else repainted
the video. Any change to the document now repaints it.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, session, subtitles
from subtitld.interface import preview_panel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
session.SUBTITLE['segments'] = [{'start': 0.0, 'end': 1.0, 'text': 'First'},
                                {'start': 1.0, 'end': 2.0, 'text': 'second'},
                                {'start': 2.0, 'end': 3.0, 'text': 'third'}]
player = preview_panel.PlayerWidget()
player.resize(640, 360)
player.show()
item = player._graphics_video_item
paints = []
paint = type(item).paint
type(item).paint = lambda self, *args: (paints.append(1), paint(self, *args))
spin(300)

print('an edit repaints the subtitle')
before = len(paints)
session.SUBTITLE['selected'] = subtitles.merge_next_subtitle(selected_subtitle=session.SUBTITLE['segments'][0])
session.set_unsaved()
spin(200)
check('merged', [s['text'] for s in session.SUBTITLE['segments']], ['First second', 'third'])
check('the preview painted again', len(paints) > before, True)
before = len(paints)
session.SUBTITLE['selected'] = subtitles.merge_back_subtitle(selected_subtitle=session.SUBTITLE['segments'][1])
session.set_unsaved()
spin(200)
check('merged back, painted again', len(paints) > before, True)

print('a preview gone')
if player._subtitle_renderer is not None:
    player._subtitle_renderer.close()
player.deleteLater()
spin(100)
try:
    session.set_unsaved()
    check('an edit after it: no error', True, True)
except RuntimeError as error:
    check('an edit after it: no error', str(error), True)

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
