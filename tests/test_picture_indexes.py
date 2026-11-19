"""Indexes of picture subtitles: reading hand-built Scenarist bitmap and SON
scripts (BMP and TIFF without alpha, the background colour taken out), DOST,
spumux, Timed Images, Final Cut Pro and Rhozet indexes as Subtitle Edit and
dvdauthor lay them out; telling them from the text formats that share .sst
and .xml; opening them through file_io and on to OCR; writing subtitles drawn
as the preview draws them in each and reading them back; and the export
dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, shutil, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, export_options, file_io, picture_indexes, picture_ocr, picture_subtitles, session
from subtitld.modules.segment import Subtitle
from subtitld.modules.addons.provider import OCRProvider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


class FakeOCR(OCRProvider):
    def __init__(self, texts):
        super().__init__()
        self.texts = texts
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append((len(image_paths), language))
        QTimer.singleShot(0, lambda: self.finished.emit(self.texts[:len(image_paths)], [0.95] * len(image_paths)))


def ocr(path, texts):
    engine = FakeOCR(texts)
    job = picture_ocr.OCRJob.for_file(path, engine)
    loop = QEventLoop()
    out = {}
    job.finished.connect(lambda found, info: (out.update(segments=found, info=info), loop.quit()))
    job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
    job.start()
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    return engine, out


def image(path, width, height, bar, background=None, ink=QColor(0, 0, 0)):
    """An image with a bar of `ink` at `bar` (x, y, w, h): on `background`
    when given (no alpha), else on nothing."""
    picture = QImage(width, height, QImage.Format_RGB888 if background else QImage.Format_ARGB32)
    picture.fill(background or QColor(0, 0, 0, 0))
    x, y, w, h = bar
    for row in range(y, y + h):
        for column in range(x, x + w):
            picture.setPixelColor(column, row, ink)
    assert picture.save(str(path)), path


def shown(pictures):
    """Each picture cut to what shows: (start, end, x, y, width, height, forced)."""
    out = []
    for picture in pictures:
        cropped = picture_subtitles.crop(picture)
        out.append((picture.start, picture.end, cropped.x, cropped.y, cropped.width, cropped.height, picture.forced)
                   if cropped else None)
    return out


def inked(drawn):
    """A drawing cut to what shows of it, as `shown` cuts a picture."""
    import numpy
    pixels = numpy.frombuffer(subtitle_pictures.rgba(drawn['path']), numpy.uint8)
    alpha = pixels.reshape(drawn['height'], drawn['width'], 4)[..., 3] > 0
    rows, columns = numpy.flatnonzero(alpha.any(1)), numpy.flatnonzero(alpha.any(0))
    return dict(drawn, x=drawn['x'] + int(columns[0]), y=drawn['y'] + int(rows[0]),
                width=int(columns[-1] - columns[0] + 1), height=int(rows[-1] - rows[0] + 1))


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('Scenarist bitmap script')
SST = ('st_format 2\r\nDisplay_Start non_forced\r\nTV_Type PAL\r\nTape_Type NON_DROP\r\nPixel_Area (2 574)\r\n'
       'Directory C:\\subs\r\nSubtitle film\r\nDisplay_Area (0 2 719 574)\r\nColor (9 3 4 3)\r\nContrast (15 15 15 0)\r\n'
       '#\r\nE2 (0 0 255 ===)\r\nE1 (255 0 0 ===)\r\nPA (0 0 0 ===)\r\nBG (255 255 255 ===)\r\n#\r\n'
       'SP_NUMBER\tSTART\tEND\tFILE_NAME\r\n'
       '0001\t00:00:01:00\t00:00:03:12\tfilm_0001.bmp\r\n0002\t00:00:04:00\t00:00:06:00\tfilm_0002.bmp\r\n')
(folder / 'film.sst').write_text(SST, encoding='utf-8')
image(folder / 'film_0001.bmp', 720, 576, (300, 500, 120, 30), QColor(255, 255, 255))
image(folder / 'film_0002.bmp', 720, 576, (200, 40, 80, 20), QColor(255, 255, 255), QColor(255, 0, 0))
check('told by its content', (picture_indexes.kind(folder / 'film.sst'), picture_indexes.looks_like(SST, 'sst'),
                              picture_indexes.looks_like(SST.replace('film_0001.bmp', 'Words'), 'sst')),
      ('SCENARISTBMP', 'SCENARISTBMP', ''))
pictures, which = picture_indexes.read(str(folder / 'film.sst'))
check('read at PAL\'s 25, the BG colour transparent: each bar where it is, frame-sized images at 0, 0',
      (which, shown(pictures), {(p.screen_width, p.screen_height) for p in pictures}),
      ('SCENARISTBMP', [(1.0, 3.48, 300, 500, 120, 30, False), (4.0, 6.0, 200, 40, 80, 20, False)], {(720, 576)}))

print('SON')
SON = ('st_format\t2\r\nDisplay_Start\tforced\r\nTV_Type\t\tNTSC\r\nTape_Type\tNON_DROP\r\nPixel_Area\t(0 477)\r\n'
       'Directory\t.\r\nSubtitle\tfilm\r\nDisplay_Area\t(0 2 719 477)\r\nContrast\t(15 15 15 0)\r\n\r\n'
       'SP_NUMBER\tSTART\tEND\tFILE_NAME\r\nColor\t(15 15 0 0)\r\nContrast\t(15 15 0 15)\r\n'
       'Display_Area\t(000 400 719 439)\r\n0001\t00:00:19:13\t00:00:22:10\ta_0001.tif\r\n'
       'Display_Area\t(000 010 719 049)\r\n0002\t00:00:30:00\t00:00:31:00\ta_0002.tif\r\n')
(folder / 'film.son').write_text(SON, encoding='utf-8')
image(folder / 'a_0001.tif', 720, 40, (10, 5, 60, 20), QColor(0, 0, 255))
image(folder / 'a_0002.tif', 720, 40, (10, 5, 60, 20), QColor(0, 0, 255))
check('told by its content', (picture_indexes.kind(folder / 'film.son'), picture_indexes.looks_like(SON)),
      ('SON', 'SON'))
pictures, which = picture_indexes.read(str(folder / 'film.son'))
check('NTSC\'s 29.97 counted as 30, the border colour transparent, at each Display_Area, forced as Display_Start says',
      ([(round(p.start, 3), round(p.end, 3)) for p in pictures], shown(pictures)[0][2:], shown(pictures)[1][3],
       pictures[0].screen_height),
      ([(round((19 * 30 + 13) / (30 / 1.001), 3), round((22 * 30 + 10) / (30 / 1.001), 3)),
        (round(900 / (30 / 1.001), 3), round(930 / (30 / 1.001), 3))], (10, 405, 60, 20, True), 15, 480))

print('DOST')
DOST = ('$FORMAT=480\r\n$VERSION=1.2\r\n$ULEAD=TRUE\r\n$DROP=25000\r\n\r\n'
        'NO\tINTIME\t\tOUTTIME\t\tXPOS\tYPOS\tFILENAME\tFADEIN\tFADEOUT\r\n'
        '0001\t00:00:01:00\t00:00:02:12\t100\t400\tbk_0001.png\t0\t0\r\n')
(folder / 'index.dost').write_text(DOST, encoding='utf-8')
image(folder / 'bk_0001.png', 200, 40, (0, 0, 200, 40))
pictures, which = picture_indexes.read(str(folder / 'index.dost'))
check('at its $DROP rate, at XPOS, YPOS, on the DVD frame that holds it',
      (which, shown(pictures), (pictures[0].screen_width, pictures[0].screen_height)),
      ('DOST', [(1.0, 2.48, 100, 400, 200, 40, False)], (720, 480)))

print('spumux')
SPUMUX = ('<subpictures>\n <stream>\n'
          '  <spu start="00:00:01.50" end="00:00:03.25" image="s1.png" xoffset="100" yoffset="420" force="yes"/>\n'
          '  <spu start="4.5" image="s2.png" xoffset="50" yoffset="30"/>\n'
          '  <spu start="1:00" end="1:01.5" image="s1.png" xoffset="100" yoffset="420"/>\n'
          ' </stream>\n</subpictures>\n')
(folder / 'menu.xml').write_text(SPUMUX, encoding='utf-8')
image(folder / 's1.png', 100, 20, (0, 0, 100, 20))
image(folder / 's2.png', 100, 20, (0, 0, 100, 20))
pictures, which = picture_indexes.read(str(folder / 'menu.xml'))
check('dvdauthor\'s times, hh:mm:ss.cc and shorter; one with no end lasts to the next, forced where it says',
      (which, [(p.start, p.end, p.x, p.y, p.forced) for p in pictures]),
      ('SPUMUX', [(1.5, 3.25, 100, 420, True), (4.5, 9.5, 50, 30, False), (60.0, 61.5, 100, 420, False)]))
check('spumux times written as spuunmux writes them', (picture_indexes.spumux_time(3661.257),
                                                       picture_indexes.spumux_seconds('01:01:01.26')),
      ('01:01:01.26', 3661.26))

print('Timed Images XML')
TIMED = ('<TimedImages targetWidth="720" targetHeight="576" aspectRatio="16:9">\n'
         '<I s="0.600" e="3.720" x="268" y="458" w="100" h="20" i="AYZ-1.png" />\n</TimedImages>\n')
(folder / 'timed.xml').write_text(TIMED, encoding='utf-8')
image(folder / 'AYZ-1.png', 100, 20, (0, 0, 100, 20))
pictures, which = picture_indexes.read(str(folder / 'timed.xml'))
check('its times in seconds, its place, its target size',
      (which, [(p.start, p.end, p.x, p.y, p.screen_width, p.screen_height) for p in pictures]),
      ('TIMEDIMAGES', [(0.6, 3.72, 268, 458, 720, 576)]))

print('Final Cut Pro image XML')
(folder / 'pictures').mkdir()
image(folder / 'pictures' / 'clip0001.png', 1280, 720, (500, 600, 200, 40))
FCP = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE xmeml[]>
<xmeml version="4"><sequence id="x"><rate><ntsc>TRUE</ntsc><timebase>24</timebase></rate>
<media><video><format><samplecharacteristics><width>1280</width><height>720</height></samplecharacteristics></format>
<track><clipitem id="clip0001.png"><name>clip0001.png</name><in>24</in><out>48</out><start>24</start><end>48</end>
<file id="clip0001"><name>clip0001.png</name><pathurl>file://localhost{(folder / 'pictures' / 'clip0001.png').as_posix().replace(' ', '%20')}</pathurl></file>
</clipitem>
<clipitem id="again"><start>96</start><end>120</end><file id="clip0001"/></clipitem>
</track></video></media></sequence></xmeml>
'''
(folder / 'fcp.xml').write_text(FCP, encoding='utf-8')
pictures, which = picture_indexes.read(str(folder / 'fcp.xml'))
check('frames of 24 at NTSC (1.001 s), the file by its pathurl, named again by its id; frame-sized at 0, 0',
      (which, [(p.start, p.end) for p in pictures], shown(pictures)[0][2:6], pictures[0].screen_width),
      ('FCPIMAGE', [(1.001, 2.002), (4.004, 5.005)], (500, 600, 200, 40), 1280))

print('Rhozet Harmonic image')
RHOZET = ("<?xml version=\"1.0\"?>\n<TitlerData>\n<Data StartTimecode='00:00:55:02' EndTimecode='00:00:56:19' "
          "Image='filepath\\shHD_jpn0001.png'/>\n</TitlerData>\n")
(folder / 'rhozet.xml').write_text(RHOZET, encoding='utf-8')
image(folder / 'shHD_jpn0001.png', 1920, 1080, (800, 950, 300, 50))
pictures, which = picture_indexes.read(str(folder / 'rhozet.xml'), fps=25)
check('frames at the rate given, "filepath\\" taken off, the frame the image\'s size',
      (which, [(p.start, p.end) for p in pictures], shown(pictures)[0][2:6], pictures[0].screen_height),
      ('RHOZET', [(55.08, 56.76)], (800, 950, 300, 50), 1080))

print('told from the formats they share .sst and .xml with')
OTHERS = {
    'text.sst': SST.replace('film_0001.bmp', 'Hello').replace('film_0002.bmp', 'there'),
    'bdn.xml': '<?xml version="1.0"?><BDN Version="0.93"><Events/></BDN>',
    'tt.xml': '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1s" end="2s">Hi</p></div></body></tt>',
    'fcptext.xml': '<xmeml version="4"><sequence><name>Titles</name></sequence></xmeml>',
}
for name, text in OTHERS.items():
    (folder / name).write_text(text, encoding='utf-8')
check('none of the others is one', [picture_indexes.kind(folder / name) for name in OTHERS], ['', '', '', ''])
session.FORMAT = {}
session.SUBTITLE = Subtitle({'language': 'en-us'})
check('the Scenarist text script still opens as one', file_io.process_subtitles_file(str(folder / 'text.sst'))[1],
      'SCENARIST')
kinds = []
for name in ('film.sst', 'film.son', 'index.dost', 'menu.xml', 'timed.xml', 'fcp.xml', 'rhozet.xml'):
    try:
        file_io.process_subtitles_file(str(folder / name))
        kinds.append(None)
    except file_io.PictureSubtitlesError as error:
        kinds.append(error.kind)
check('each opens for OCR', kinds, ['scenaristbmp', 'son', 'dost', 'spumux', 'timedimages', 'fcpimage', 'rhozet'])
engine, out = ocr(str(folder / 'film.sst'), ['Hello', 'A sign'])
check('read by OCR: the one at the top', [(s['start'], s['text']) for s in out.get('segments', [])],
      [(1.0, 'Hello'), (4.0, '{\\an8}A sign')])

print('written')
from subtitld.interface import subtitle_pictures
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''}]
out_folder = folder / 'out'
out_folder.mkdir()
for which, extension in picture_indexes.FORMATS.items():
    session.SUBTITLE = Subtitle({'segments': SEGMENTS, 'language': 'en'})
    session.VIDEO = {'width': 1280, 'height': 720, 'framerate': 25.0}
    session.FORMAT = {'format': which, 'options': {'vobsub_size': 'pal'}}
    target = out_folder / which.lower() / f'reel.{extension}'
    target.parent.mkdir()
    image_extension = 'bmp' if which in ('SCENARISTBMP', 'SON') else 'png'
    (target.parent / f'reel_0009.{image_extension}').write_bytes(b'left from a longer export')
    file_io.save_file(str(target), which)
    check(f'{which}: the index and an image a subtitle beside it, those of an export before gone',
          sorted(os.listdir(target.parent)),
          sorted([target.name, f'reel_0001.{image_extension}', f'reel_0002.{image_extension}']))
    check(f'{which}: told as itself', picture_indexes.kind(target), which)
    frame = (720, 576) if which in picture_indexes.DVD_FORMATS else (1280, 720)
    drawn = [inked(d) for d in subtitle_pictures.draw(export_options.prepare(SEGMENTS), frame,
                                                       str(folder / 'drawn' / which.lower()))]
    pictures, _which = picture_indexes.read(str(target), fps=25.0)
    got = shown(pictures)
    check(f'{which}: read back at its times (to a frame), where it was drawn (within 2 px)',
          [(abs(g[0] - d['start']) <= 0.04, abs(g[1] - d['end']) <= 0.04,
            abs(g[2] - d['x']) <= 2 and abs(g[3] - d['y']) <= 2 and abs(g[4] - d['width']) <= 4
            and abs(g[5] - d['height']) <= 4) for g, d in zip(got, drawn)] + [len(got)],
          [(True, True, True), (True, True, True), 2])
    if which == 'SPUMUX':
        check('spumux: forced where the subtitle is', [p.forced for p in pictures], [False, True])
    if which in ('SCENARISTBMP', 'SON'):
        written = QImage(str(target.parent / 'reel_0001.bmp'))
        colours = {written.pixelColor(x, y).rgb() & 0xFFFFFF for x in range(0, 720, 3) for y in range(0, 576, 3)}
        check(f'{which}: a BMP the frame\'s size in the four colours the header names',
              ((written.width(), written.height()), colours <= {0xFFFFFF, 0x000000, 0xFF0000, 0x0000FF}),
              ((720, 576), True))
text = (out_folder / 'scenaristbmp' / 'reel.sst').read_bytes().decode('utf-8')
check('Scenarist: PAL, its BG white, the colours\' contrast as they show',
      ('TV_Type PAL' in text, 'BG (255 255 255 ===)' in text, 'Contrast (' in text and text.split('Contrast (')[1][-1:] != ''),
      (True, True, True))
dost = (out_folder / 'dost' / 'reel.dost').read_bytes().decode('utf-8')
check('DOST: Subtitle Edit\'s header, the video\'s rate', (dost.startswith('$FORMAT=480\r\n$VERSION=1.2'), '$DROP=25000' in dost),
      (True, True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel, SUBTITLE_FORMATS
check('offered, with the text options, exported by their extension, not project formats',
      [(which in SUBTITLE_FORMATS, which in export_options.TEXT_FORMATS,
        session.LIST_OF_PICTURE_SUBTITLE_EXTENSIONS[which]['extensions'][0],
        which in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS) for which in picture_indexes.FORMATS],
      [(True, True, extension, False) for extension in picture_indexes.FORMATS.values()])
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 25}
panel = _SubtitlesPanel()
for which in picture_indexes.FORMATS:
    panel.format_combo.setCurrentText(which)
    panel._refresh_options()
    check(f'{which}: the DVD frame for the DVD tools\', no note as the preview shows the pictures',
          (panel.vobsub_options.isHidden(), panel.check_label.isHidden()),
          (which not in picture_indexes.DVD_FORMATS, True))

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
