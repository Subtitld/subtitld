"""A .usfx's paths stay in its cache, and a save reaches the disk.

Paths in a project come from the file: a dub or subclip path in
subtitles.usf, a member name in the zip. A crafted project saying
`../../somewhere/file.wav` must not have the audio engine read, nor the
background extractor write, outside the project's extract dir; the save's
dub rescue must not write where an absolute path in it says. The save
flushes the new file to disk before renaming it over the project, and the
folder after.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, zipfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
from subtitld.modules.signals import SIGNALS

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def inside(path, folder):
    real = os.path.realpath(folder)
    return os.path.commonpath([real, os.path.realpath(path)]) == real


def dub_paths(segments):
    """Every dub and subclip path in `segments`, by subtitle text."""
    found = []
    for segment in segments:
        for dub in segment.get('dubbing', []) or []:
            if dub.get('path'):
                found.append((segment['text'], 'path', dub['path']))
            for seg in dub.get('segments', []) or []:
                for key in ('path', 'raw_path'):
                    if seg.get(key):
                        found.append((segment['text'], f'subclip {key}', seg[key]))
    return found


def open_project(path):
    """Open as the app does, and let the background extractor finish."""
    session.SUBTITLE, session.SPEAKERS, session.FORMAT, session.VIDEO = {}, {}, {}, {}
    segments = file_io.process_subtitles_file(str(path))[0]
    if session.USFX_BACKGROUND_LOAD is not None:
        session.USFX_BACKGROUND_LOAD.wait(10000)
    session.SUBTITLE['segments'] = segments
    return segments


def recraft(source, target, replace=(), add=()):
    """A copy of the `source` bundle with text replaced in its
    subtitles.usf and members added, as someone crafting one would."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, 'w') as new:
        for name in old.namelist():
            data = old.read(name)
            if name == 'subtitles.usf':
                text = data.decode('utf-8')
                for old_text, new_text in replace:
                    text = text.replace(old_text, new_text)
                data = text.encode('utf-8')
            new.writestr(name, data)
        for name, data in add:
            new.writestr(name, data)


WAV = b'RIFF' + b'dub audio' * 50
folder = Path(tempfile.mkdtemp())
(folder / 'ok.wav').write_bytes(WAV)
(folder / 'bad.wav').write_bytes(WAV)
(folder / 'secret.wav').write_bytes(b'not for the project')


def dub(uid, path):
    return [{'uid': uid, 'path': str(path), 'start': 0.0, 'end': 1.0,
             'segments': [{'type': 'audio', 'path': str(path), 'raw_path': str(path),
                           'start': 0.0, 'end': 1.0, 'offset': 0.0}]}]


session.SUBTITLE = {'language': 'en-us', 'segments': [
    {'start': 1.0, 'end': 2.0, 'text': 'Kept', 'dubbing': dub('ok', folder / 'ok.wav')},
    {'start': 3.0, 'end': 4.0, 'text': 'Crafted', 'dubbing': dub('bad', folder / 'bad.wav')}]}
session.SPEAKERS, session.FORMAT, session.VIDEO = {}, {}, {}
file_io.save_file(str(folder / 'base.usfx'), 'USFX')

print('a crafted project: paths that climb out of its cache')
crafted = folder / 'crafted.usfx'
extract_dir = file_io._usfx_extract_dir(str(crafted))
# Relative to where the reader joins them, so each lands on a real file
# (or folder) outside the cache without the containment check.
secret = os.path.relpath(folder / 'secret.wav', extract_dir).replace(os.sep, '/')
planted = 'assets/dubs/' + os.path.relpath(folder / 'planted.wav',
                                           os.path.join(extract_dir, 'assets', 'dubs')).replace(os.sep, '/')
recraft(folder / 'base.usfx', crafted, replace=[('assets/dubs/bad.wav', secret)], add=[(planted, b'planted')])
with zipfile.ZipFile(crafted) as bundle:
    check('the crafted file says so', (f'path="{secret}"' in bundle.read('subtitles.usf').decode(), planted in bundle.namelist()),
          (True, True))

announced = []
SIGNALS.usfx_member_ready.connect(lambda arcname, target: announced.append((arcname, target)), Qt.DirectConnection)
opened = open_project(crafted)
check('every path read stays in the extract dir',
      [entry for entry in dub_paths(opened) if not inside(entry[2], extract_dir)], [])
dropped = next(entry for entry in opened if entry['text'] == 'Crafted')['dubbing'][0]
check('the climbing ones are dropped: the dub and its subclip have none',
      (dropped.get('path'), dropped['segments'][0].get('path'), dropped['segments'][0].get('raw_path')), (None, None, None))
kept = next(entry for entry in opened if entry['text'] == 'Kept')['dubbing'][0]
check('a good one lands in the extract dir, as before',
      kept['path'], os.path.join(extract_dir, 'assets', 'dubs', 'ok.wav'))
check('its subclip too', (kept['segments'][0]['path'], kept['segments'][0]['raw_path']), (kept['path'], kept['path']))
check('the extractor wrote the good one there', Path(kept['path']).read_bytes() == WAV, True)
check('and announced it under the very path the dub holds (the audio engine matches them as strings)',
      [target for arcname, target in announced if arcname == 'assets/dubs/ok.wav'], [kept['path']])
check('the climbing member: nothing written outside, nothing announced',
      ((folder / 'planted.wav').exists(), [arcname for arcname, _ in announced if arcname == planted]), (False, []))

print('saving it again')
events = []
real_fsync_file, real_fsync_dir, real_replace = file_io._fsync_file, file_io._fsync_dir, os.replace
file_io._fsync_file = lambda path: (events.append(('flush file', os.path.basename(path))), real_fsync_file(path))
file_io._fsync_dir = lambda path: (events.append(('flush folder', path)), real_fsync_dir(path))
os.replace = lambda src, dst: (events.append(('rename', os.path.basename(src), os.path.basename(dst))), real_replace(src, dst))
try:
    file_io.save_file(str(crafted), 'USFX')
finally:
    file_io._fsync_file, file_io._fsync_dir, os.replace = real_fsync_file, real_fsync_dir, real_replace
check('written, flushed to disk, renamed over the project, the folder flushed', events,
      [('flush file', 'crafted.usfx.tmp'), ('rename', 'crafted.usfx.tmp', 'crafted.usfx'), ('flush folder', str(folder))])
check('no .tmp left', (folder / 'crafted.usfx.tmp').exists(), False)
with zipfile.ZipFile(crafted) as bundle:
    names = bundle.namelist()
    check('a sound zip', bundle.testzip(), None)
    check('the good dub bundled, nothing climbing', ('assets/dubs/ok.wav' in names, [n for n in names if '..' in n]),
          (True, []))
    check('its bytes', bundle.read('assets/dubs/ok.wav') == WAV, True)
reopened = open_project(crafted)
check('reopened: the subtitles as they were', [(s['start'], s['text']) for s in reopened], [(1.0, 'Kept'), (3.0, 'Crafted')])
check('the good dub plays from the extract dir, the dropped one stays dropped',
      [(text, key, os.path.relpath(path, extract_dir)) for text, key, path in dub_paths(reopened)],
      [('Kept', 'path', os.path.join('assets', 'dubs', 'ok.wav')),
       ('Kept', 'subclip path', os.path.join('assets', 'dubs', 'ok.wav')),
       ('Kept', 'subclip raw_path', os.path.join('assets', 'dubs', 'ok.wav'))])

print('a cleared cache: the dub comes back from the file being replaced')
os.unlink(os.path.join(extract_dir, 'assets', 'dubs', 'ok.wav'))
file_io.save_file(str(crafted), 'USFX')
with zipfile.ZipFile(crafted) as bundle:
    check('still bundled, its bytes', bundle.read('assets/dubs/ok.wav') == WAV, True)

print('a save that fails to reach the disk')
before = crafted.read_bytes()


def refuse(path):
    raise OSError(5, 'Input/output error')


file_io._fsync_file = refuse
try:
    file_io.save_file(str(crafted), 'USFX')
    check('raises', None, 'OSError')
except OSError as error:
    check('raises', error.errno, 5)
finally:
    file_io._fsync_file = real_fsync_file
check('the project untouched, no .tmp left', (crafted.read_bytes() == before, (folder / 'crafted.usfx.tmp').exists()),
      (True, False))

print('an absolute path: the dub rescue writes nowhere but the cache')
elsewhere = folder / 'elsewhere' / 'assets' / 'dubs' / 'bad.wav'
absolute = folder / 'absolute.usfx'
recraft(folder / 'base.usfx', absolute, replace=[('assets/dubs/bad.wav', str(elsewhere))])
opened = open_project(absolute)
check('read as is (a legacy project may hold one)',
      next(entry for entry in opened if entry['text'] == 'Crafted')['dubbing'][0]['path'], str(elsewhere))
file_io.save_file(str(absolute), 'USFX')
check('saving over it writes nothing there', (folder / 'elsewhere').exists(), False)

print('peek_usfx_video: a climbing video member')
(folder / 'private.mp4').write_bytes(b'not a video of the project')
peeked = folder / 'peeked.usfx'
peek_dir = file_io._usfx_extract_dir(str(peeked))
with zipfile.ZipFile(peeked, 'w') as bundle:
    bundle.writestr('assets/video/' + os.path.relpath(folder / 'private.mp4', os.path.join(peek_dir, 'assets', 'video'))
                    .replace(os.sep, '/'), b'video')
check('not handed back as the video', file_io.peek_usfx_video(str(peeked)), None)

print('the helper')
base = os.path.join(_xdg, 'base')
check('inside', file_io._path_inside(base, 'assets/dubs/a.wav'), os.path.join(base, 'assets', 'dubs', 'a.wav'))
check('a .. that stays inside: normalised', file_io._path_inside(base, 'assets/x/../dubs/a.wav'),
      os.path.join(base, 'assets', 'dubs', 'a.wav'))
check('out by ..', file_io._path_inside(base, '../a.wav'), None)
check('out by an absolute path', file_io._path_inside(base, '/etc/passwd'), None)
os.makedirs(base, exist_ok=True)
os.symlink(folder, os.path.join(base, 'link'))
check('out by a symlink', file_io._path_inside(base, 'link/secret.wav'), None)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
