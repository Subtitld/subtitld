"""BDN XML: reading a Blu-ray's authoring files as BDSup2Sub and Subtitle
Edit write them (the video formats, the frame rates counted at 24, 30 and
60, drop-frame, forced events, two pictures in one), writing them with each
subtitle drawn as the preview draws it and reading them back (pictures,
places, times, forced; overlapping subtitles cut into events that do not
overlap), opening through file_io beside the other .xml formats and on to
OCR, and the export dialog's options.

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
from PySide6.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import bdn, config, file_io, picture_ocr, session
from subtitld.modules.addons.provider import OCRProvider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


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


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('timecodes')
check('23.976 counts its labels at 24: a minute\'s label is 1440 frames, 60.06 s',
      round(bdn.seconds('00:01:00:00', 23.976), 3), 60.06)
check('25 is the clock', bdn.seconds('00:00:02:12', 25), 2.48)
check('29.97 drop-frame skips two labels a minute: 00:01:00;02 is frame 1800',
      round(bdn.seconds('00:01:00;02', 29.97, drop=True), 4), round(1800 / (30 / 1.001), 4))
check('and not on the tenth minute', round(bdn.seconds('00:10:00:00', 29.97, drop=True), 3), round(17982 * 1.001 / 30, 3))
check('written without drop-frame, at the rate\'s labels', [bdn.timecode(bdn.frames(t, r), r) for t, r in
                                                         ((60.06, 23.976), (2.48, 25), (1.0, 59.94), (3599.9, 24))],
      ['00:01:00:00', '00:00:02:12', '00:00:01:00', '00:59:59:22'])
check('the rate for a video', [bdn.rate_for(f) for f in (23.976, 24, 30, 29.97, 60, 48, 0, None)],
      [23.976, 24.0, 29.97, 29.97, 59.94, 50.0, 23.976, 23.976])
check('the video formats', [bdn.size_of(name) for name in ('480i', '576p', '720p', '1080i', '1080p', '2160p',
                                                           '1440x1080', 'hd')],
      [(720, 480), (720, 576), (1280, 720), (1920, 1080), (1920, 1080), (3840, 2160), (1440, 1080), None])
check('and their names, as BDSup2Sub writes them', [bdn.video_format(size) for size in
                                                     ((720, 480), (720, 576), (1280, 720), (1920, 1080), (1440, 1080))],
      ['480i', '576i', '720p', '1080p', '1440x1080'])

print('read')
# As BDSup2Sub writes one (its PNGs named <title>_0001.png), with a second
# picture in an event as Scenarist allows.
BDSUP2SUB = '''<?xml version="1.0" encoding="UTF-8"?>
<BDN Version="0.93" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="BD-03-006-0093b BDN File Format.xsd">
  <Description>
    <Name Title="film" Content=""/>
    <Language Code="fre"/>
    <Format VideoFormat="720p" FrameRate="23.976" DropFrame="False"/>
    <Events Type="Graphic" FirstEventInTC="00:00:01:00" LastEventOutTC="00:01:02:00" NumberofEvents="2"/>
  </Description>
  <Events>
    <Event InTC="00:00:01:00" OutTC="00:00:02:12" Forced="False">
      <Graphic Width="400" Height="60" X="440" Y="620">film_0001.png</Graphic>
    </Event>
    <Event InTC="00:01:00:00" OutTC="00:01:02:00" Forced="True">
      <Graphic Width="300" Height="60" X="490" Y="40">film_0002.png</Graphic>
      <Graphic Width="200" Height="40" X="540" Y="620">film_0003.png</Graphic>
    </Event>
  </Events>
</BDN>
'''
(folder / 'film.xml').write_text(BDSUP2SUB, encoding='utf-8')
for name, size in (('film_0001.png', (400, 60)), ('film_0002.png', (300, 60)), ('film_0003.png', (200, 40))):
    (folder / name).write_bytes(png(*size))
graphics, settings = bdn.read(str(folder / 'film.xml'))
check('the description', settings, {'title': 'film', 'language': 'fre', 'fps': 23.976, 'drop': False, 'size': (1280, 720)})
check('each picture with its times, its place and whether it is forced',
      [(g['start'], g['end'], g['href'], g['x'], g['y'], g['forced']) for g in graphics],
      [(1.001, 2.502, 'film_0001.png', 440, 620, False), (60.06, 62.062, 'film_0002.png', 490, 40, True),
       (60.06, 62.062, 'film_0003.png', 540, 620, True)])
try:
    bdn.parse('<tt xmlns="http://www.w3.org/ns/ttml"/>')
    check('TTML is no BDN', None, 'ValueError')
except ValueError as error:
    check('TTML is no BDN, and says so', str(error).startswith('Not a BDN'), True)

pictures, language = picture_ocr.read_file(str(folder / 'film.xml'))
check('for OCR: the PNGs where the file puts them, on a 720p picture, forced, no words',
      [(p.start, p.x, p.y, p.width, p.height, p.screen_width, p.screen_height, p.forced, p.text) for p in pictures],
      [(1.001, 440, 620, 400, 60, 1280, 720, False, ''), (60.06, 490, 40, 300, 60, 1280, 720, True, ''),
       (60.06, 540, 620, 200, 40, 1280, 720, True, '')])
check('in the language the file gives', language, 'fr-fr')

print('through file_io, beside the other .xml')
session.FORMAT = {}
session.SUBTITLE = {'language': 'en-us'}
try:
    file_io.process_subtitles_file(str(folder / 'film.xml'))
    check('a BDN goes to the OCR dialog', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('a BDN goes to the OCR dialog, its rate kept', (error.kind, session.FORMAT['options'].get('bdn_fps')),
          ('bdn', 23.976))
OTHERS = {
    'ttml.xml': ('<?xml version="1.0" encoding="UTF-8"?><tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><body><div>'
                 '<p begin="00:00:01.000" end="00:00:02.000">TTML</p></div></body></tt>', 'TTML'),
    'dcp.xml': ('<?xml version="1.0" encoding="UTF-8"?><DCSubtitle Version="1.0"><SubtitleID>40950d85-63eb-4ee2-b1e8-'
                '45c126601b94</SubtitleID><MovieTitle>T</MovieTitle><ReelNumber>1</ReelNumber><Language>en</Language>'
                '<Subtitle SpotNumber="1" TimeIn="00:00:01:000" TimeOut="00:00:02:000"><Text VAlign="bottom" '
                'VPosition="10">DCP</Text></Subtitle></DCSubtitle>', 'DCP'),
    'captionate.xml': ('<captionate><timeformat>ms</timeformat><captioninfo><trackinfo><track><displayname>en'
                       '</displayname></track></trackinfo></captioninfo><captions><caption time="1000"><tracks>'
                       '<track0>Flash</track0></tracks></caption><caption time="2000"><tracks><track0></track0>'
                       '</tracks></caption></captions></captionate>', 'CAPTIONATE'),
    'youtube.xml': ('<?xml version="1.0" encoding="utf-8"?><transcript><text start="1" dur="1">YouTube</text>'
                    '</transcript>', 'XML'),
}
for name, (text, want) in OTHERS.items():
    (folder / name).write_text(text, encoding='utf-8')
    session.FORMAT = {}
    check(f'{name} is still {want}', file_io.process_subtitles_file(str(folder / name))[1], want)
check('none of them is a BDN', [bdn.is_bdn(str(folder / name)) for name in OTHERS] + [bdn.is_bdn(str(folder / 'film.xml'))],
      [False, False, False, False, True])


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


engine, out = ocr(str(folder / 'film.xml'), ['Bonjour', 'Un panneau', 'Dessous'])
check('every picture read by OCR, in the file\'s language; the sign at the top and forced',
      (engine.calls, [(s['start'], s['text'], s.get('forced', False)) for s in out.get('segments', [])]),
      ([(3, 'fr-fr')], [(1.001, 'Bonjour', False), (60.06, '{\\an8}Un panneau', True), (60.06, 'Dessous', True)]))

print('written')
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''},
            # Two at once, the second overlapping the first's end.
            {'start': 7.0, 'end': 9.0, 'text': 'First'},
            {'start': 8.0, 'end': 10.0, 'text': '{\\an8}Second'}]
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'en-us', 'metadata': {'title': 'Pictures'}}
session.VIDEO = {'width': 1280, 'height': 720, 'framerate': 25.0}
session.FORMAT = {'format': 'BDN', 'options': {'bdn_fps': 25.0}}
target = folder / 'out' / 'reel.xml'
target.parent.mkdir()
(target.parent / 'reel_00009.png').write_bytes(b'left from a longer export')
(target.parent / 'notes.txt').write_text('mine')
file_io.save_file(str(target), 'BDN')
written = target.read_text(encoding='utf-8')
check('the PNGs beside it, named after it; those of an export before gone, the rest kept',
      sorted(os.listdir(target.parent)),
      ['notes.txt', 'reel.xml', 'reel_00001.png', 'reel_00002.png', 'reel_00003.png', 'reel_00004.png'])
graphics, settings = bdn.read(str(target))
check('its description: the title, ISO 639-2, the video\'s size and the rate asked for',
      settings, {'title': 'Pictures', 'language': 'eng', 'fps': 25.0, 'drop': False, 'size': (1280, 720)})
check('the head as BDSup2Sub writes it', ('<BDN Version="0.93"' in written,
                                          'FirstEventInTC="00:00:01:00" LastEventOutTC="00:00:10:00" NumberofEvents="5"'
                                          in written, 'DropFrame="False"' in written), (True, True, True))
check('overlapping subtitles cut where what shows changes: no two events overlap, both pictures in the middle',
      [(g['start'], g['end'], g['href'], g['forced']) for g in graphics],
      [(1.0, 2.52, 'reel_00001.png', False), (3.0, 4.0, 'reel_00002.png', True),
       (7.0, 8.0, 'reel_00003.png', False), (8.0, 9.0, 'reel_00003.png', False), (8.0, 9.0, 'reel_00004.png', False),
       (9.0, 10.0, 'reel_00004.png', False)])
images = {g['href']: QImage(str(target.parent / g['href'])) for g in graphics}
check('each <Graphic> as big as its PNG', all((images[g['href']].width(), images[g['href']].height())
                                             == (g['width'], g['height']) for g in graphics), True)
check('where the preview puts them: the sign at the top, the line at the bottom',
      (graphics[1]['y'] < 720 / 3, graphics[0]['y'] + graphics[0]['height'] > 720 * 2 / 3), (True, True))

session.FORMAT = {}
session.SUBTITLE = {'language': 'en-us'}
try:
    file_io.process_subtitles_file(str(target))
    check('what Subtitld writes opens for OCR', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('what Subtitld writes opens for OCR', (error.kind, session.FORMAT['options'].get('bdn_fps')), ('bdn', 25.0))
engine, out = ocr(str(target), ['Hello there', 'A sign', 'First', 'First', 'Second', 'Second'])
check('read back: the pieces of a subtitle cut in two joined again, the sign forced and at the top',
      [(s['start'], s['end'], s['text'], s.get('forced', False)) for s in out.get('segments', [])],
      [(1.0, 2.52, 'Hello there', False), (3.0, 4.0, '{\\an8}A sign', True), (7.0, 9.0, 'First', False),
       (8.0, 10.0, '{\\an8}Second', False)])

check('at 23.976, as a Blu-ray counts it', bdn.write([{'start': 60.06, 'end': 61.0, 'href': 'a.png', 'x': 0, 'y': 0,
                                                      'width': 8, 'height': 8}], (1920, 1080), fps=23.976)
      .count('InTC="00:01:00:00" OutTC="00:01:00:23"'), 1)

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
from subtitld.modules import export_options
check('offered, a timed-text format, an .xml', ('BDN' in SUBTITLE_FORMATS, 'BDN' in export_options.TEXT_FORMATS,
                                              session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['BDN']['extensions']),
      (True, True, ['xml']))
from subtitld.modules import utils
check('.xml still a DCP\'s by its extension', utils.get_format_from_extension('xml'), 'DCP')
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'en-us'}
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('BDN')
panel._refresh_options()
check('BDN: its rate, the video\'s (29.97), and where the pictures go', (
    panel.bdn_options.isHidden(), panel.text_options.isHidden(), panel.get_config()['options'].get('bdn_fps'),
    panel.check_label.isHidden(), 'PNG' in panel.check_label.text()), (False, False, 29.97, False, True))
session.VIDEO = {}
panel.bdn_rate_combo.combobox.setCurrentIndex(bdn.RATES.index(50.0))
panel.get_config()
again = _SubtitlesPanel()
check('remembered, without a video', again.bdn_rate_combo.combobox.currentData(), 50.0)
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.bdn_options.isHidden(), 'bdn_fps' in panel.get_config()['options']), (True, False))

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
