"""The thumbnail a .usfx carries: saved from the preview's picture (through
the save thread's snapshot, and saving on the main thread), listed in the
manifest, left alone by the reader; and subtitld-thumbnailer, which hands it
to file managers.

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
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
from subtitld import thumbnailer

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


PNG = b'\x89PNG\r\n\x1a\n' + b'picture' * 10
folder = Path(tempfile.mkdtemp())
video = folder / 'clip.mp4'
video.write_bytes(b'')
session.SUBTITLE = {'language': 'en-us', 'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Hello'}]}
session.VIDEO = {'filepath': str(video)}
session.SPEAKERS, session.FORMAT = {}, {}


def members(path):
    with zipfile.ZipFile(path) as bundle:
        return bundle.namelist(), bundle.read('manifest.xml').decode(), \
            bundle.read('thumbnail.png') if 'thumbnail.png' in bundle.namelist() else None


print('saving')
file_io.thumbnail_provider = lambda: PNG
snapshot = file_io._snapshot_for_save()
check('the snapshot takes the picture, on the main thread', snapshot['THUMBNAIL'], PNG)
file_io._active_save_snapshot = snapshot
file_io.save_file(str(folder / 'a.usfx'), 'USFX')
file_io._active_save_snapshot = None
names, manifest, data = members(folder / 'a.usfx')
check('saved from the snapshot (as a save thread does)', data, PNG)
check('in the manifest', '<entry path="thumbnail.png" type="image/png"/>' in manifest, True)
with zipfile.ZipFile(folder / 'a.usfx') as bundle:
    check('stored, not compressed again', bundle.getinfo('thumbnail.png').compress_type, zipfile.ZIP_STORED)
file_io.save_file(str(folder / 'b.usfx'), 'USFX')
check('saved on the main thread: taken then', members(folder / 'b.usfx')[2], PNG)
file_io.thumbnail_provider = lambda: None
file_io.save_file(str(folder / 'c.usfx'), 'USFX')
names, manifest, data = members(folder / 'c.usfx')
check('no picture (no video frame): no thumbnail, nor its entry', ('thumbnail.png' in names, 'thumbnail' in manifest),
      (False, False))
file_io.save_file(str(folder / 'a.usfx'), 'USFX')
check('saved again with no picture: the one it had is kept', members(folder / 'a.usfx')[2], PNG)
file_io.thumbnail_provider = lambda: 1 / 0
check('a provider that fails: no thumbnail, the save goes on', file_io._thumbnail_png(), None)
file_io.thumbnail_provider = None

print('reading')
session.SUBTITLE = {}
segments = file_io.process_subtitles_file(str(folder / 'a.usfx'))[0]
check('the project reads as before', [(s['start'], s['text']) for s in segments], [(1.0, 'Hello')])

print('the card')
from PySide6.QtGui import QColor, QFontDatabase, QImage
from subtitld.interface import project_thumbnail
for font in session.PATH_SUBTITLD_GRAPHICS.glob('Montserrat-*.ttf'):
    QFontDatabase.addApplicationFont(str(font))
frame = QImage(1280, 534, QImage.Format_RGB32)
frame.fill(QColor('#3a7a3a'))
cues = [{'start': 3.0, 'end': 4.5, 'text': "<i>You're</i> a jerk, Thom.", 'speaker': 'A'},
        {'start': 5.0, 'end': 7.0, 'text': 'Look Celia,\nwe have to follow our passions;', 'speaker': 'B'}]
colours = {'A': {'color': '#ef6c95'}, 'B': {'color': '#4e9cf0'}}
import numpy as np
sound = (np.sin(np.arange(48000 * 20) / 30.0) * 12000).astype(np.int16)
card = project_thumbnail.render(frame, cues, 6.0, speakers=colours, duration=40.0, samples=sound)
check('512 x 384, corners transparent', (card.width(), card.height(), card.pixelColor(1, 1).alpha()), (512, 384, 0))
check('the frame there', card.pixelColor(256, 60).name(), '#3a7a3a')


def near(colour, want, slack=40):
    a, b = QColor(colour), QColor(want)
    return all(abs(x - y) <= slack for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))


band_top = 14 + 272 + 10 + 8 + 2          # the timeline's blocks start here
x_cue_b = 14 + 10 + (5.0 + 0.5 - 0.0) / 12 * (512 - 28 - 84 - 20)
check('the current subtitle\'s block, its speaker\'s colour on top', near(card.pixelColor(int(x_cue_b), band_top + 2), '#4e9cf0'),
      True)
check('the playhead there', near(card.pixelColor(int(14 + 10 + 6.0 / 12 * 380), 300), '#ff3b3b', 60), True)
check('the mark in its badge', card.pixelColor(470, 333).alpha() == 255, True)
empty = project_thumbnail.render(None, cues, 0.0)
check('no video: the mark where the frame would be', empty.pixelColor(256, 60).name() != '#3a7a3a', True)
check('near the start and the end of the video, nothing out of place', all(
    not project_thumbnail.render(frame, cues, t, duration=8.0).isNull() for t in (0.0, 7.9, 30.0)), True)
check('a project with nothing yet: still a card', project_thumbnail.render(None, [], 0.0).isNull(), False)

print('subtitld-thumbnailer')
out = folder / 'out.png'
check('copies it out', (thumbnailer.main([str(folder / 'a.usfx'), str(out), '256']), out.read_bytes()), (0, PNG))
check('none in the file: 1, nothing written', thumbnailer.main([str(folder / 'c.usfx'), str(folder / 'none.png'), '256']) == 1
      and not (folder / 'none.png').exists(), True)
with zipfile.ZipFile(folder / 'bad.usfx', 'w') as bundle:
    bundle.writestr('thumbnail.png', b'<script>not a png')
check('not a PNG: refused', thumbnailer.thumbnail(folder / 'bad.usfx'), None)
with zipfile.ZipFile(folder / 'big.usfx', 'w', zipfile.ZIP_DEFLATED) as bundle:
    bundle.writestr('thumbnail.png', PNG + bytes(thumbnailer.LIMIT))
check('larger than a thumbnail: refused', thumbnailer.thumbnail(folder / 'big.usfx'), None)
(folder / 'notzip.usfx').write_bytes(b'hello')
check('not a zip: refused', thumbnailer.thumbnail(folder / 'notzip.usfx'), None)
check('missing file: refused', thumbnailer.thumbnail(folder / 'missing.usfx'), None)
check('no arguments: usage', thumbnailer.main([]), 2)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
