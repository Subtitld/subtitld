"""DCP subtitles written as pictures: each subtitle drawn as the preview
draws it, a PNG beside the file, an <Image> a subtitle; Interop naming its
PNG, SMPTE naming it by urn:uuid with the PNG loose beside the XML as
<uuid>.png. Read back by Subtitld (pictures, places, times; no words, so to
the OCR dialog), a second export taking away the pictures of the one
before, and the export dialog's option.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, re, shutil, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, dcp, file_io, picture_ocr, session
from subtitld.modules.addons.provider import OCRProvider
from subtitld.interface import subtitle_pictures

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())
SEGMENTS = [{'start': 1.0, 'end': 2.6, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign'},
            {'start': 5.0, 'end': 6.0, 'text': ''},
            {'start': 7.0, 'end': 8.0, 'text': '{\\an1}Bottom left'}]
SCREEN = (1920, 1080)

print('placed')
check('a picture centred at the bottom', dcp.image_place(760, 912, 400, 60, SCREEN), ('bottom', 10.0, 'center', 0.0))
check('one at the top, left of the middle', dcp.image_place(200, 54, 300, 60, SCREEN), ('top', 5.0, 'center', -31.77))
for box in ((760, 912, 400, 60), (200, 54, 300, 60), (1500, 500, 301, 41), (0, 0, 1920, 1080)):
    valign, down, halign, across = dcp.image_place(*box, SCREEN)
    image = {'valign': valign, 'vposition': down, 'halign': halign, 'hposition': across}
    check(f'{box}: read back where it was put', dcp.place_picture(image, box[2], box[3], SCREEN), box[:2])

drawn = subtitle_pictures.draw(SEGMENTS, SCREEN, str(folder / 'drawn'), stem='cue')

print('written')
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'fr-fr', 'metadata': {'title': 'Pictures'}}
session.VIDEO = {'width': SCREEN[0], 'height': SCREEN[1], 'framerate': 25.0}
for standard in dcp.STANDARDS:
    out = folder / standard
    out.mkdir()
    target = out / 'reel.xml'
    session.FORMAT = {'format': 'DCP', 'options': {'dcp_standard': standard, 'dcp_fps': 25, 'dcp_content': 'pictures'}}
    file_io.save_file(str(target), 'DCP')
    written = target.read_text(encoding='utf-8')
    names = sorted(name for name in os.listdir(out) if name.endswith('.png'))
    images, texts, settings = dcp.read_images(str(target))
    if standard == dcp.INTEROP:
        check('Interop: the PNGs beside it, named after it, named by the <Image>s', (
            names, [image['href'] for image in images]),
              (['reel_00001.png', 'reel_00002.png', 'reel_00003.png'],
               ['reel_00001.png', 'reel_00002.png', 'reel_00003.png']))
        check('Interop: its spelling, no <Font>, ticks', ('VAlign="bottom"' in written, '<Font' in written,
                                                          'TimeIn="00:00:01:000" TimeOut="00:00:02:150"' in written),
              (True, False, True))
    else:
        uuids = [re.fullmatch(r'urn:uuid:([0-9a-f-]{36})', image['href']) for image in images]
        check('SMPTE: each <Image> a urn:uuid, its PNG <uuid>.png beside it, nothing else', (
            all(uuids), names == sorted(f'{found.group(1)}.png' for found in uuids if found)), (True, True))
        check('SMPTE: its spelling, no <Font>, frames at 25', ('Valign="bottom"' in written, '<Font' in written,
                                                              'TimeIn="00:00:01:00" TimeOut="00:00:02:15"' in written,
                                                              '<TimeCodeRate>25</TimeCodeRate>' in written),
              (True, False, True, True))
    check(f'{standard}: no text, the language and the title', (texts, settings['language'], settings['metadata']),
          ([], 'fr-FR', {'title': 'Pictures'}))
    check(f'{standard}: each picture where the preview drew it, at its times', [
        (image['start'], image['end']) + dcp.place_picture(image, picture['width'], picture['height'], SCREEN)
        for image, picture in zip(images, drawn)],
          [(picture['start'], picture['end'], picture['x'], picture['y']) for picture in drawn])
    check(f'{standard}: the sign at the top, the line at the bottom left of the middle', [
        (image['valign'], image['hposition'] < 0) for image in images],
          [('bottom', False), ('top', False), ('bottom', True)])
    session.FORMAT = {}
    try:
        file_io.process_subtitles_file(str(target))
        check(f'{standard}: no words in it, so to the OCR dialog', None, 'PictureSubtitlesError')
    except file_io.PictureSubtitlesError as error:
        check(f'{standard}: no words in it, so to the OCR dialog, as DCP', (error.kind, session.FORMAT['options']
                                                                             .get('dcp_standard')), ('dcp', standard))
    pictures, language = picture_ocr.read_file(str(target))
    check(f'{standard}: for OCR, the PNGs as drawn, where they were drawn', [
        (p.start, p.x, p.y, p.width, p.height, p.text) for p in pictures],
          [(d['start'], d['x'], d['y'], d['width'], d['height'], '') for d in drawn])
    check(f'{standard}: in its language', language, 'fr-fr')

print('written again')
out = folder / dcp.SMPTE
before = sorted(os.listdir(out))
(out / 'notes.txt').write_text('mine')
(out / '5c7a1b2e-0000-4000-8000-00000000abcd.png').write_bytes(b'some other reel\'s')
session.FORMAT = {'format': 'DCP', 'options': {'dcp_standard': dcp.SMPTE, 'dcp_content': 'pictures'}}
session.SUBTITLE = {'segments': SEGMENTS[:1], 'language': 'fr-fr'}
file_io.save_file(str(out / 'reel.xml'), 'DCP')
after = sorted(os.listdir(out))
check('SMPTE: the PNGs the reel there named are gone, what it did not name stays', (
    len(after), [name for name in before if name in after], '5c7a1b2e-0000-4000-8000-00000000abcd.png' in after),
      (4, ['reel.xml'], True))
session.FORMAT = {'format': 'DCP', 'options': {'dcp_standard': dcp.SMPTE, 'dcp_content': 'text'}}
file_io.save_file(str(out / 'reel.xml'), 'DCP')
check('as text again: the <Text> back, no <Image>', ('<Text' in (out / 'reel.xml').read_text(encoding='utf-8'),
                                                    dcp.has_images(str(out / 'reel.xml'))), (True, False))
images = {name: QImage(str(out / name)) for name in after if name.endswith('.png') and not name.startswith('5c7a')}
check('the picture written is the drawing\'s', [(i.width(), i.height()) for i in images.values()],
      [(drawn[0]['width'], drawn[0]['height'])])


class FakeOCR(OCRProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append((len(image_paths), language))
        QTimer.singleShot(0, lambda: self.finished.emit(['Lu'] * len(image_paths), [0.95] * len(image_paths)))


engine = FakeOCR()
job = picture_ocr.OCRJob.for_file(str(folder / dcp.INTEROP / 'reel.xml'), engine)
loop = QEventLoop()
found = {}
job.finished.connect(lambda segments, info: (found.update(segments=segments), loop.quit()))
job.failed.connect(lambda message: (found.update(failed=message), loop.quit()))
job.start()
QTimer.singleShot(20000, loop.quit)
loop.exec()
check('every picture read by OCR, the sign at the top', (engine.calls, [s['text'] for s in found.get('segments', [])]),
      ([(3, 'fr-fr')], ['Lu', '{\\an8}Lu', 'Lu']))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'en-us'}
session.VIDEO = {'framerate': 24.0}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DCP')
panel._refresh_options()
check('text by default, and nothing said', (panel.get_config()['options'].get('dcp_content'), panel.check_label.isHidden()),
      ('text', True))
panel.dcp_content_combo.combobox.setCurrentIndex(1)
check('pictures: chosen, and SMPTE\'s MXF said', (panel.get_config()['options'].get('dcp_content'),
                                                 panel.check_label.isHidden(), 'MXF' in panel.check_label.text()),
      ('pictures', False, True))
panel.dcp_standard_combo.combobox.setCurrentIndex(1)
check('Interop: no MXF to say', (panel.get_config()['options']['dcp_standard'], 'MXF' in panel.check_label.text()),
      ('interop', False))
again = _SubtitlesPanel()
check('remembered', again.dcp_content_combo.combobox.currentData(), 'pictures')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', ('dcp_content' in panel.get_config()['options'], panel.check_label.isHidden()), (False, True))

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
