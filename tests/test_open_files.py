"""Opening what the command line passes, as the Open button does: a file
association passes the one file double-clicked. A .usfx opens with the video
it names, a subtitle and a video together, a video alone; a subtitle alone
asks for its video (but not when asked not to ask).

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
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
from subtitld.interface import startscreen

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())
video = folder / 'clip.mp4'
video.write_bytes(b'')
srt = folder / 'clip.srt'
srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nHello\n', encoding='utf-8')
session.SUBTITLE = {'language': 'en-us', 'segments': file_io.process_subtitles_file(str(srt))[0]}
session.VIDEO = {'filepath': str(video)}
session.FORMAT = {}
usfx = folder / 'project.usfx'
file_io.save_file(str(usfx), 'USFX')

loaded, asked = [], []
startscreen.load_productionscreen = lambda window: loaded.append(
    (session.SUBTITLE.get('filepath'), session.VIDEO.get('filepath')))
startscreen.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (asked.append(k.get('dir')) or str(video), ''))


def open_(paths, ask=True):
    loaded.clear(); asked.clear()
    session.SUBTITLE, session.VIDEO = {}, {}
    return startscreen.open_files(None, [str(p) for p in paths], ask=ask), list(loaded), list(asked)


check('a .usfx: opened with the video it names, nothing asked', open_([usfx]),
      (True, [(str(usfx), str(video))], []))
check('a subtitle and a video', open_([srt, video]), (True, [(str(srt), str(video))], []))
check('a video alone', open_([video]), (True, [(None, str(video))], []))
check('a subtitle alone, not asking: not opened', open_([srt], ask=False), (False, [], []))
check('a subtitle alone: its video asked for, from its folder', open_([srt]),
      (True, [(str(srt), str(video))], [str(folder)]))
check('nothing', open_([]), (False, [], []))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
