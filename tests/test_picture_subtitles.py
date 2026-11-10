"""Picture subtitles: DVD VobSub (.idx/.sub), Blu-ray PGS (.sup) and IMSC's Image Profile read
into pictures (time, place, palette), made dark-on-light grayscale for OCR,
and their text made subtitles by an OCR engine (picture_ocr.OCRJob): every
picture read, those read with little confidence read again the other way
round, a cancelled job never heard from again. Opening such a file names
it pictures (PictureSubtitlesError) for the OCR dialog; the add-on side of
`ocr.recognize` (AddonOCRProvider) passes its frames on, and leaves out
those of a request cancelled since. The PGS and VobSub tracks of a video
(Matroska, made with mkvmerge) are read too.

The sample files are written from scratch (tests/picture_samples.py) and
read by FFmpeg as well, so the readers are not only tested against their
own idea of the formats. The checks that need FFmpeg or mkvmerge are left
out (said so) where they are not installed.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, pgs, picture_ocr, picture_subtitles, session, video_subtitles, vobsub
from subtitld.modules.addons.provider import OCRProvider
import picture_samples

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())
CUES = [(1.0, 3.5, "I told you not to come back here.\nWhy didn't you listen?", False),
        (4.0, 6.0, 'Where were you last night?', True),
        (65.25, 68.0, 'Ação, coração e emoção', False)]
picture_samples.write_vobsub(str(folder / 'film'), CUES, language='pt')
picture_samples.write_pgs(str(folder / 'film.sup'), CUES)
FFPROBE = shutil.which('ffprobe')

print('VobSub')
pictures, track = vobsub.read(str(folder / 'film.idx'))
check('times, from the .idx', [(p.start, round(p.end, 2)) for p in pictures], [(1.0, 3.5), (4.0, 6.0), (65.25, 68.0)])
check('the track and its language', (track['index'], track['language']), (0, 'pt'))
check('the frame size', {(p.screen_width, p.screen_height) for p in pictures}, {(720, 480)})
check('the second at the top, the others at the bottom', [p.y < 240 for p in pictures], [False, True, False])
width, height, indices = picture_samples.draw(CUES[1][2])
check('its pixels, as drawn', (pictures[1].width, pictures[1].height, pictures[1].indices == indices), (width, height, True))
picture_samples.write_vobsub(str(folder / 'forced'), CUES, language='pt', forced=(0,))
check('the forced flag, read from the SPU that starts with it',
      ([p.forced for p in pictures], [p.forced for p in vobsub.read(str(folder / 'forced.idx'))[0]]),
      ([False, False, False], [True, False, False]))
check('four colours from the palette, the background clear',
      pictures[1].colours, [(0, 0, 0, 0), (240, 240, 240, 255), (128, 128, 128, 255), (16, 16, 16, 255)])
same, _track = vobsub.read(str(folder / 'film.sub'))
check('opened by its .sub just the same', [p.start for p in same], [1.0, 4.0, 65.25])
idx = (folder / 'film.idx').read_text()
(folder / 'film.idx').rename(folder / 'film.idx.bak')
alone, _track = vobsub.read(str(folder / 'film.sub'))
check('a .sub without its .idx: timed by its PTS', [p.start for p in alone], [1.0, 4.0, 65.25])
(folder / 'film.idx.bak').rename(folder / 'film.idx')
info = vobsub.parse_idx(idx + 'id: en, index: 1\ndelay: 00:00:01:500\ntimestamp: 00:00:02:000, filepos: 000000800\n')
check('an .idx: tracks, delay', [(t['index'], t['language'], t['entries'][-1][0]) for t in info['tracks']],
      [(0, 'pt', 65.25), (1, 'en', 3.5)])
if FFPROBE:
    probed = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'packet=pts_time', '-of', 'csv=p=0',
                             str(folder / 'film.idx')], capture_output=True, text=True).stdout.split()
    check('FFmpeg reads the sample at the same times', [float(t) for t in probed], [1.0, 4.0, 65.25])
else:
    print('  (no ffprobe: the sample is not checked against FFmpeg)')

print('PGS')
shown = pgs.read(str(folder / 'film.sup'))
check('times: each until the composition that clears it', [(p.start, p.end) for p in shown],
      [(1.0, 3.5), (4.0, 6.0), (65.25, 68.0)])
check('the frame size', {(p.screen_width, p.screen_height) for p in shown}, {(1920, 1080)})
width, height, indices = picture_samples.draw(CUES[0][2], size=54)
check('its pixels, as drawn', (shown[0].width, shown[0].height, shown[0].indices == indices), (width, height, True))
fill = shown[0].colours[1]
check('its palette from YCbCr', (fill[3], fill[0] > 230, abs(fill[0] - fill[2]) <= 2), (255, True, True))
try:
    pgs.read(str(folder / 'film.idx'))
    check('not a PGS: refused', None, 'PGSError')
except pgs.PGSError:
    check('not a PGS: refused', True, True)
if FFPROBE:
    probed = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'subtitle=pts_time,num_rects', '-of', 'csv=p=0',
                             str(folder / 'film.sup')], capture_output=True, text=True).stdout.split()
    check('FFmpeg shows and clears them at the same times', probed,
          ['1.000000,1', '3.500000,0', '4.000000,1', '6.000000,0', '65.250000,1', '68.000000,0'])

check('the forced flag, which a player shows with subtitles off',
      [p.forced for p in shown], [False, False, False])
picture_samples.write_pgs(str(folder / 'forced.sup'), CUES, forced=(1,))
marked = pgs.read(str(folder / 'forced.sup'))
check('set on the one that carries it', [p.forced for p in marked], [False, True, False])
check('and carried on to the subtitle',
      [s.get('forced') for s in picture_subtitles.segments(marked, ['one', 'two', 'three'])],
      [None, True, None])

print('for OCR')
picture = picture_subtitles.Picture(0, 1, 10, 20, 4, 3, bytes([0, 0, 0, 0,
                                                               0, 1, 2, 0,
                                                               0, 0, 0, 0]),
                                    [(0, 0, 0, 0), (255, 255, 255, 255), (0, 0, 0, 255)])
cropped = picture_subtitles.crop(picture)
check('cropped to what shows', (cropped.x, cropped.y, cropped.width, cropped.height, cropped.indices),
      (11, 21, 2, 1, bytes([1, 2])))
check('light letters: dark ink, the outline light', picture_subtitles.ink(cropped), bytes([0, 255]))
check('dark letters, read the other way', picture_subtitles.ink(cropped, 'dark'), bytes([255, 0]))
check('nothing showing: nothing to read', picture_subtitles.crop(
    picture_subtitles.Picture(0, 1, 0, 0, 2, 1, b'\x00\x00', [(0, 0, 0, 0)])), None)
data = picture_subtitles.pgm(cropped, margin=2)
check('a PGM with a margin of paper', (data.split(b'\n')[:3], len(data.split(b'\n', 3)[3])),
      ([b'P5', b'6 5', b'255'], 30))
top = picture_subtitles.Picture(5, 6, 0, 10, 1, 1, b'\x01', [], screen_height=480)
segments = picture_subtitles.segments([pictures[0], top, pictures[2]], ['One', 'Up', ''])
check('subtitles: the top one with {\\an8}, the empty one left out',
      [(s['start'], s['text']) for s in segments], [(1.0, 'One'), (5, '{\\an8}Up')])
fade = [picture_subtitles.Picture(1, 2, 0, 400, 1, 1, b'', [], screen_height=480),
        picture_subtitles.Picture(2, 3, 0, 400, 1, 1, b'', [], screen_height=480)]
check('a subtitle drawn twice in a row is one', picture_subtitles.segments(fade, ['Hi', 'Hi']),
      [{'start': 1, 'end': 3, 'text': 'Hi'}])

print('opening one')
for name, kind in (('film.idx', 'vobsub'), ('film.sub', 'vobsub'), ('film.sup', 'pgs')):
    try:
        file_io.process_subtitles_file(str(folder / name))
        check(f'{name}: pictures', None, 'PictureSubtitlesError')
    except file_io.PictureSubtitlesError as error:
        check(f'{name}: pictures, for the OCR dialog', (error.path, error.kind), (str(folder / name), kind))
check('picture files told from text', [picture_ocr.is_picture_file(folder / n) for n in ('film.idx', 'film.sub', 'film.sup')],
      [True, True, True])
(folder / 'text.sub').write_text('{1}{25}Hi\n')
check('a MicroDVD .sub is text', picture_ocr.is_picture_file(folder / 'text.sub'), False)


class FakeOCR(OCRProvider):
    """Reads a picture's file name back, with confidence by its number;
    'dark-' pictures (read the other way) come back sure."""

    def __init__(self, low=()):
        super().__init__()
        self.low = set(low)
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append(([os.path.basename(p) for p in image_paths], language))
        for path in image_paths:
            check(f'{os.path.basename(path)} is a PGM', Path(path).read_bytes()[:2], b'P5')
        texts, confidences = [], []
        for path in image_paths:
            name = os.path.basename(path)
            number = int(name.split('.')[0].split('-')[-1])
            texts.append(f'{"dark " if name.startswith("dark") else ""}text {number}')
            confidences.append(0.9 if name.startswith('dark') else 0.3 if number in self.low else 0.95)
        QTimer.singleShot(0, lambda: self.progress.emit(0.5, ''))
        QTimer.singleShot(0, lambda: self.finished.emit(texts, confidences))


def run(job, cancel=False):
    loop = QEventLoop()
    out = {}
    job.finished.connect(lambda segments, info: (out.update(segments=segments, info=info), loop.quit()))
    job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
    job.start()
    if cancel:
        job.cancel()
        QTimer.singleShot(500, loop.quit)
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    return out


print('read by OCR')
engine = FakeOCR(low={1})
out = run(picture_ocr.OCRJob.for_file(str(folder / 'film.idx'), engine))
check('every picture read, in the language the .idx gives', engine.calls[0], (['00000.pgm', '00001.pgm', '00002.pgm'], 'pt'))
check('the unsure one read again the other way', engine.calls[1], (['dark-00001.pgm'], 'pt'))
check('its better reading kept', [s['text'] for s in out['segments']], ['text 0', '{\\an8}dark text 1', 'text 2'])
check('the language, as Subtitld knows it', (out['info']['language_tag'], out['info']['language'][:2]), ('pt', 'pt'))
check('the pictures it wrote, removed', [p for p in Path(session.PATH_TEMP).glob('subtitld_ocr_*')], [])
engine = FakeOCR()
out = run(picture_ocr.OCRJob.for_file(str(folder / 'film.sup'), engine, language='en-us'))
check('a .sup, in the language chosen; none read again', (engine.calls, len(out['segments'])),
      ([(['00000.pgm', '00001.pgm', '00002.pgm'], 'en-us')], 3))
out = run(picture_ocr.OCRJob.for_file(str(folder / 'missing.sup'), FakeOCR()))
check('a file that is not there: failed', 'failed' in out, True)
engine = FakeOCR()
out = run(picture_ocr.OCRJob.for_file(str(folder / 'film.sup'), engine), cancel=True)
check('cancelled: nothing more heard', out, {})


print("IMSC's Image Profile")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from subtitld.modules import ttml


def png(width, height):
    """A white bar on nothing, as a PNG's bytes."""
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for x in range(4, width - 4):
        for y in range(4, height - 4):
            image.setPixelColor(x, y, QColor(255, 255, 255))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


import base64
(folder / 'one.png').write_bytes(png(400, 60))
(folder / 'two.png').write_bytes(png(300, 60))
IMAGES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
    'xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt" '
    'xmlns:ittm="http://www.w3.org/ns/ttml/profile/imsc1#metadata" '
    'xmlns:itts="http://www.w3.org/ns/ttml/profile/imsc1#styling" '
    'ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/imsc1.1/image" tts:extent="1920px 1080px" xml:lang="fr">'
    '<head><metadata><smpte:image xml:id="embedded" imagetype="PNG" encoding="Base64">'
    + base64.b64encode(png(200, 40)).decode('ascii') +
    '</smpte:image></metadata><layout>'
    '<region xml:id="bottom" tts:origin="760px 900px" tts:extent="400px 60px"/>'
    '<region xml:id="top" tts:origin="810px 80px" tts:extent="300px 60px"/>'
    '</layout></head><body>'
    '<div begin="1s" end="2s" region="bottom" smpte:backgroundImage="one.png">'
    '<metadata><ittm:altText>Bonjour\ntout le monde</ittm:altText></metadata></div>'
    '<div begin="3s" end="4s" region="top" smpte:backgroundImage="two.png" itts:forcedDisplay="true"/>'
    '<div begin="5s" end="6s" region="bottom" smpte:backgroundImage="#embedded"/>'
    '</body></tt>')
(folder / 'pictures.ttml').write_text(IMAGES, encoding='utf-8')
check('a picture with words is read as them, placed where its region is',
      ttml.parse(IMAGES), [{'start': 1.0, 'end': 2.0, 'text': 'Bonjour\ntout le monde'}])
_images, _language, _size = ttml.read_images(str(folder / 'pictures.ttml'))
check('the pictures, the file\'s and the ones it holds',
      [(i['start'], i['href'], i['data'] is not None, i['forced']) for i in _images] + [_language, _size],
      [(1.0, 'one.png', False, False), (3.0, 'two.png', False, True), (5.0, '', True, False), 'fr', (1920.0, 1080.0)])
try:
    file_io.process_subtitles_file(str(folder / 'pictures.ttml'))
    check('pictures with no words: for the OCR dialog', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('pictures with no words: for the OCR dialog', (error.path, error.kind),
          (str(folder / 'pictures.ttml'), 'imsc'))
(folder / 'described.ttml').write_text(IMAGES.replace('itts:forcedDisplay="true"/>', 'itts:forcedDisplay="true">'
                                                      '<metadata><ittm:altText>En haut</ittm:altText></metadata></div>')
                                       .replace('smpte:backgroundImage="#embedded"/>', 'smpte:backgroundImage="#embedded">'
                                                '<metadata><ittm:altText>Encore</ittm:altText></metadata></div>'),
                                       encoding='utf-8')
_segments, _format = file_io.process_subtitles_file(str(folder / 'described.ttml'))
check('every picture with words: opened as text, the forced one forced, the top one at the top',
      ([(s['text'], s.get('forced', False)) for s in _segments], _format),
      ([('Bonjour\ntout le monde', False), ('{\\an8}En haut', True), ('Encore', False)], 'TTML'))
_pictures, _tag = picture_ocr.imsc_pictures(str(folder / 'pictures.ttml'))
check('made pictures where their regions put them, on the picture the file is',
      [(p.x, p.y, p.width, p.height, p.screen_height, p.text) for p in _pictures] + [_tag],
      [(760, 900, 400, 60, 1080, 'Bonjour\ntout le monde'), (810, 80, 300, 60, 1080, ''),
       (760, 900, 200, 40, 1080, ''), 'fr'])
_white, _clear = _pictures[0].indices[10 * 400 + 10], _pictures[0].indices[0]
check('a PNG\'s pixels as how light they are and how much of them shows',
      (_pictures[0].colours[_white], _pictures[0].colours[_clear]), ((255, 255, 255, 255), (0, 0, 0, 0)))
check('and drawn for OCR as dark ink on light paper, as a disc\'s are',
      (picture_subtitles.ink(_pictures[0])[10 * 400 + 10], picture_subtitles.ink(_pictures[0])[0]), (0, 255))
engine = FakeOCR()
out = run(picture_ocr.OCRJob.for_file(str(folder / 'pictures.ttml'), engine))
check('only the pictures with no words are read, in the file\'s language',
      engine.calls, [(['00001.pgm', '00002.pgm'], 'fr')])
check('and the words the file gives are kept as they are',
      [(s['text'], s.get('forced', False)) for s in out['segments']],
      [('Bonjour\ntout le monde', False), ('{\\an8}text 1', True), ('text 2', False)])


class Request(QObject):
    progress = Signal(float, str)
    partial = Signal(dict)
    result = Signal(dict)
    error = Signal(str, str)
    id = 'r1'


class Process:
    def __init__(self):
        self.requests, self.cancelled = [], []

    def is_running(self):
        return True

    def request(self, task, params):
        self.requests.append((task, params))
        request = Request()
        self.last = request
        return request

    def cancel(self, req_id):
        self.cancelled.append(req_id)


print('the add-on side')
from subtitld.modules.addons.addon_provider import AddonOCRProvider
provider = AddonOCRProvider('tesseract', {'id': 'tesseract', 'tasks': ['ocr.recognize']}, '/bin/true')
process = Process()
provider._process = process
heard = []
provider.partial.connect(lambda *a: heard.append(('partial',) + a))
provider.finished.connect(lambda *a: heard.append(('finished',) + a))
provider.error.connect(lambda *a: heard.append(('error',) + a))
provider.recognize(['/a.pgm', '/b.pgm'], 'pt-br')
check('the request', process.requests[0][0:1] + (process.requests[0][1]['images'], process.requests[0][1]['language']),
      ('ocr.recognize', [{'id': '0', 'path': '/a.pgm'}, {'id': '1', 'path': '/b.pgm'}], 'pt-br'))
first = process.last
first.partial.emit({'index': 1, 'text': 'B', 'confidence': 0.8})
first.result.emit({'texts': ['A', 'B'], 'confidences': [0.9, 0.8]})
app.processEvents()
check('its frames passed on', heard, [('partial', 1, 'B', 0.8), ('finished', ['A', 'B'], [0.9, 0.8])])
heard.clear()
provider.recognize(['/a.pgm'], 'en')
stale = process.last
provider.cancel()
stale.error.emit('cancelled', 'cancelled')
provider.recognize(['/a.pgm'], 'en')
process.last.result.emit({'texts': ['A', 'extra'], 'confidences': [1, 1]})
app.processEvents()
check('a cancelled request is not heard; a wrong count is an error',
      (process.cancelled, heard), (['r1'], [('error', 'tesseract returned 2 texts for 1 pictures')]))
heard.clear()
# PP-OCRv5's frames: ids, {id, text} entries, no confidence (taken as sure).
provider.recognize(['/a.pgm', '/b.pgm'], 'de')
process.last.partial.emit({'id': '1', 'text': 'Zwei'})
process.last.result.emit({'texts': [{'id': '0', 'text': 'Eins'}, {'id': '1', 'text': 'Zwei'}], 'script': 'latin'})
app.processEvents()
check('an add-on with ids and no confidences', heard,
      [('partial', 1, 'Zwei', 1.0), ('finished', ['Eins', 'Zwei'], [1.0, 1.0])])

print('in a video')
MKVMERGE = shutil.which('mkvmerge')
FFMPEG = shutil.which('ffmpeg')
if MKVMERGE and FFMPEG and FFPROBE:
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:s=320x240:d=70:r=5',
                    '-c:v', 'libx264', '-preset', 'ultrafast', str(folder / 'video.mkv')], check=True)
    subprocess.run([MKVMERGE, '-q', '-o', str(folder / 'film.mkv'), str(folder / 'video.mkv'),
                    str(folder / 'film.idx'), str(folder / 'film.sup')], check=True)
    tracks = {}
    for index, codec in ((1, 'dvd_subtitle'), (2, 'hdmv_pgs_subtitle')):
        found = tracks[codec] = video_subtitles.extract_pictures(str(folder / 'film.mkv'), index, codec, ffmpeg=FFMPEG, ffprobe=FFPROBE,
                                                 folder=str(folder))
        check(f'{codec}: its pictures and times',
              [(round(p.start, 2), round(p.end, 2)) for p in found], [(1.0, 3.5), (4.0, 6.0), (65.25, 68.0)])
        check(f'{codec}: its pixels', (found[1].width, found[1].height), (pictures[1].width, pictures[1].height)
              if codec == 'dvd_subtitle' else (shown[1].width, shown[1].height))
    check('VobSub in a video: the palette from its extradata', tracks['dvd_subtitle'][0].colours,
          pictures[0].colours)
else:
    print('  (no mkvmerge or FFmpeg: the tracks in a video are not checked)')

shutil.rmtree(folder, ignore_errors=True)
print()
print(f'{len(fails)} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
