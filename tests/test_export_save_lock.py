"""An export to a format other than .usfx does not wait for a .usfx save
(an autosave of a large project zips its dubs and checks them: seconds),
and does not draw the thumbnail only a .usfx carries; a .usfx save still
waits its turn. Text previews go on while a save runs.

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
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())
session.SUBTITLE = Subtitle({'language': 'en-us', 'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Hello'}]})
session.VIDEO = {'filepath': ''}
session.SPEAKERS, session.FORMAT = {}, {}
drawn = []
file_io.thumbnail_provider = lambda: drawn.append(1) or b'\x89PNG'


def save(name, fmt, seconds):
    """Save on a save thread; whether it finished within `seconds`."""
    result = []
    file_io.save_file_async(str(folder / name), fmt, on_done=lambda path, ok, error: result.append((ok, error)))
    started = time.time()
    while not result and time.time() - started < seconds:
        app.processEvents()
        time.sleep(0.02)
    return result


print('while a .usfx save holds the lock')
file_io._save_lock.acquire()
try:
    check('an SRT export finishes', save('a.srt', 'SRT', 5), [(True, '')])
    check('its file written', (folder / 'a.srt').read_text(encoding='utf-8-sig').split('\n')[2], 'Hello')
    check('no thumbnail drawn for it', drawn, [])
    check('a text preview is not busy', 'Hello' in file_io.preview_file('SRT', {}), True)
    check('no thumbnail drawn for the preview', drawn, [])
    try:
        file_io.preview_file('USFX', {})
        busy = False
    except file_io.PreviewBusy:
        busy = True
    check('a .usfx preview is busy', busy, True)
    usfx = []
    file_io.save_file_async(str(folder / 'b.usfx'), 'USFX', on_done=lambda path, ok, error: usfx.append(ok))
    started = time.time()
    while time.time() - started < 0.5:
        app.processEvents()
        time.sleep(0.02)
    check('a .usfx save waits', usfx, [])
    check('its thumbnail drawn, on the main thread', drawn, [1])
finally:
    file_io._save_lock.release()
started = time.time()
while not usfx and time.time() - started < 10:
    app.processEvents()
    time.sleep(0.02)
check('the .usfx save goes on once the lock is free', usfx, [True])
file_io.wait_for_save_threads()

print(f'\n{len(fails)} failure(s)' + (': ' + ', '.join(fails) if fails else ''))
sys.exit(1 if fails else 0)
