"""DVD SUP: reading a .sup of bare subpicture units as BDSup2Sub and Subtitle
Edit write them ("SP", the PTS, the SPU), with the palette and frame of the
.ifo beside it or without one, telling it from a Blu-ray's PGS .sup by its
bytes, opening through file_io and on to OCR, writing subtitles drawn as
the preview draws them (the .sup and its .ifo) and reading them back, the
.ifo's palette checked against FFmpeg's reading of it, and the export
dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, dvdsup, export_options, file_io, picture_ocr, pgs, session, vobsub
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


def bar(width, height, value=1):
    """A bar of `value` on 0, a margin of 2 around it."""
    rows = numpy.zeros((height, width), numpy.uint8)
    rows[2:-2, 2:-2] = value
    return rows.tobytes()


def packet(seconds, spu):
    """An "SP" packet as BDSup2Sub's SupDvdWriter lays it out, by hand."""
    pts = round(seconds * 90000)
    return b'SP' + bytes([pts & 255, pts >> 8 & 255, pts >> 16 & 255, pts >> 24 & 255, 0, 0, 0, 0]) + spu


def ifo(colours, ntsc=False, language=b'fr'):
    """A VTS IFO cut down as BDSup2Sub's IfoWriter writes one, by hand: the
    palette entries (0, Y, Cr, Cb) at 0xA4 into the first program chain."""
    data = bytearray(0x1800)
    data[0:12] = b'DVDVIDEO-VTS'
    data[0xCC:0xD0] = struct.pack('>I', 2)
    data[0x200:0x202] = struct.pack('>H', 0x4f01 if ntsc else 0x5f01)
    data[0x254:0x256] = struct.pack('>H', 1)
    data[0x256] = 1
    data[0x258:0x25A] = language
    data[0x1000 + 0x0C:0x1000 + 0x10] = struct.pack('>I', 0x10)
    for number, (y, cr, cb) in enumerate(colours):
        data[0x1010 + 0xA4 + 4 * number:0x1010 + 0xA8 + 4 * number] = bytes([0, y, cr, cb])
    return bytes(data)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('read')
first = vobsub.encode_spu(bar(40, 10), 40, 10, 300, 500, 2.0, [0, 1, 2, 3], [0, 15, 15, 15])
second = vobsub.encode_spu(bar(60, 12), 60, 12, 100, 40, 1.5, [0, 3, 2, 1], [0, 15, 15, 15], forced=True)
data = packet(1.0, first) + packet(4.0, second)
(folder / 'film.sup').write_bytes(data)
check('told by its bytes: "SP" is a DVD\'s, "PG" a Blu-ray\'s',
      (dvdsup.is_dvdsup(data[:16]), dvdsup.is_dvdsup(b'PG' + data[2:16]), dvdsup.is_dvdsup(b'SP\x00')),
      (True, False, False))
check('the packets: where each is, its PTS, its SPU', [(o, p, len(s)) for o, p, s in dvdsup.units(data)],
      [(0, 90000, len(first)), (10 + len(first), 360000, len(second))])
check('a packet among bytes that are none is found again',
      [p for _o, p, _s in dvdsup.units(data[:10 + len(first)] + b'\x00junk' + data[10 + len(first):])], [90000, 360000])
pictures, language = dvdsup.read(str(folder / 'film.sup'))
check('without an .ifo: PAL\'s frame, no language, the times, places and sizes, forced where it says',
      (language, [(p.start, round(p.end, 3), p.x, p.y, p.width, p.height, p.forced, p.screen_height) for p in pictures]),
      ('', [(1.0, round(1.0 + round(2.0 / vobsub.SPU_TICK) * vobsub.SPU_TICK, 3), 300, 500, 40, 10, False, 576),
            (4.0, round(4.0 + round(1.5 / vobsub.SPU_TICK) * vobsub.SPU_TICK, 3), 100, 40, 60, 12, True, 576)]))
check('in BDSup2Sub\'s default palette: entry 1 near white, 3 a grey', pictures[0].colours[1:],
      [(0xf0, 0xf0, 0xf0, 255), (0xcc, 0xcc, 0xcc, 255), (0x99, 0x99, 0x99, 255)])
# White (235, 128, 128), black, pure red in BT.601 video range (Y 81, Cr 240, Cb 90).
(folder / 'film.ifo').write_bytes(ifo([(16, 128, 128), (235, 128, 128), (81, 240, 90)] + [(16, 128, 128)] * 13,
                                      ntsc=True))
pictures, language = dvdsup.read(str(folder / 'film.sup'))
check('with the .ifo: its frame (NTSC), its language, its palette read Y, Cr, Cb',
      (language, pictures[0].screen_height, pictures[0].colours[1:3]),
      ('fr', 480, [(255, 255, 255, 255), (254, 0, 0, 255)]))
check('an .ifo that is none is not read', dvdsup.parse_ifo(b'\x00' * 0x1800), None)

print('opened')
session.FORMAT = {}
session.SUBTITLE = {'language': 'en-us'}
for name, want in (('film.sup', 'dvdsup'),):
    try:
        file_io.process_subtitles_file(str(folder / name))
        check(f'{name} opens for OCR', None, want)
    except file_io.PictureSubtitlesError as error:
        check(f'{name} opens for OCR as a DVD\'s', error.kind, want)
pgs.save(str(folder / 'bluray.sup'), [{'start': 1.0, 'end': 2.0, 'x': 10, 'y': 10, 'width': 4, 'height': 4,
                                      'rgba': b'\xff' * 64}], (1920, 1080))
try:
    file_io.process_subtitles_file(str(folder / 'bluray.sup'))
except file_io.PictureSubtitlesError as error:
    check('a PGS .sup is still a Blu-ray\'s', error.kind, 'pgs')
check('picture_ocr reads each its own way',
      (len(picture_ocr.read_file(str(folder / 'bluray.sup'))[0]), picture_ocr.read_file(str(folder / 'film.sup'))[1]),
      (1, 'fr'))
engine, out = ocr(str(folder / 'film.sup'), ['Bonjour', 'Un panneau'])
check('read by OCR in the .ifo\'s language; the one at the top and forced',
      (engine.calls, [(s['start'], s['text'], s.get('forced', False)) for s in out.get('segments', [])]),
      ([(2, 'fr')], [(1.0, 'Bonjour', False), (4.0, '{\\an8}Un panneau', True)]))

print('written')
from subtitld.interface import subtitle_pictures
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''}]
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'pt-br'}
session.VIDEO = {}
session.FORMAT = {'format': 'DVDSUP', 'options': {'vobsub_size': 'ntsc'}}
out_folder = folder / 'out'
out_folder.mkdir()
target = out_folder / 'reel.sup'
file_io.save_file(str(target), 'DVDSUP')
check('the .sup and its .ifo, nothing else', sorted(os.listdir(out_folder)), ['reel.ifo', 'reel.sup'])
written = target.read_bytes()
check('it begins as a DVD SUP does', (written[:2], dvdsup.is_dvdsup(written[:16])), (b'SP', True))
info = dvdsup.parse_ifo((out_folder / 'reel.ifo').read_bytes())
check('the .ifo: NTSC, the language as a DVD names it', (info['size'], info['language']), ((720, 480), 'pt'))
drawn = subtitle_pictures.draw(export_options.prepare(SEGMENTS), (720, 480), str(folder / 'drawn'))
pictures, language = dvdsup.read(str(target))
check('read back: a picture each, at its time, ending within a tick, where and as big as drawn',
      [(p.start, abs(p.end - d['end']) <= vobsub.SPU_TICK / 2, p.x, p.y, p.width, p.height, p.forced)
       for p, d in zip(pictures, drawn)],
      [(d['start'], True, d['x'], d['y'], d['width'], d['height'], bool(d.get('forced'))) for d in drawn])
ink = [numpy.frombuffer(p.indices, numpy.uint8) for p in pictures]
check('drawn in light colours that show (the fill), through the .ifo\'s palette',
      all(max(sum(p.colours[i][:3]) for i in set(k.tolist()) if p.colours[i][3]) > 600 for p, k in zip(pictures, ink)),
      True)

FFMPEG = shutil.which('ffmpeg')
if not FFMPEG:
    print('  (no ffmpeg: skipped)')
else:
    # The hand-built .ifo as FFmpeg's dvdsub decoder reads it (ifo_palette):
    # a bar of entry 2, which is red, comes out red, not blue.
    red = vobsub.encode_spu(bar(40, 10, 2), 40, 10, 300, 300, 2.0, [0, 1, 2, 3], [0, 15, 15, 15])
    (folder / 'red.sub').write_bytes(vobsub.packs(red, 1.0))
    result = subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=720x480:r=25:d=3',
                             '-ifo_palette', str(folder / 'film.ifo'), '-f', 'mpeg', '-i', str(folder / 'red.sub'),
                             '-filter_complex', '[0:v][1:s]overlay', '-ss', '1.5', '-frames:v', '1', '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-'], capture_output=True)
    if result.returncode or len(result.stdout) != 720 * 480 * 3:
        check('FFmpeg reads the hand-built .ifo', result.stderr.decode()[-300:], '')
    else:
        frame = numpy.frombuffer(result.stdout, numpy.uint8).reshape(480, 720, 3)
        r, g, b = (int(v) for v in frame[305, 320])
        check('FFmpeg reads the .ifo\'s entries as Subtitld does: Y, Cr, Cb (entry 2 red)', r > 200 and b < 60, True)
    # The .ifo Subtitld wrote, as FFmpeg's dvdsub decoder reads it:
    # the first SPU, in a program stream of its own, laid on black.
    spu = dvdsup.units(written)[0][2]
    (folder / 'one.sub').write_bytes(vobsub.packs(spu, 1.0))
    d = drawn[0]
    result = subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=720x480:r=25:d=3',
                             '-ifo_palette', str(out_folder / 'reel.ifo'), '-f', 'mpeg', '-i', str(folder / 'one.sub'),
                             '-filter_complex', '[0:v][1:s]overlay', '-ss', '1.5', '-frames:v', '1', '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-'], capture_output=True)
    if result.returncode or len(result.stdout) != 720 * 480 * 3:
        check('FFmpeg decodes it with the .ifo\'s palette', result.stderr.decode()[-300:], '')
    else:
        frame = numpy.frombuffer(result.stdout, numpy.uint8).reshape(480, 720, 3).astype(numpy.float64)
        region = frame[d['y']:d['y'] + d['height'], d['x']:d['x'] + d['width']]
        pixels = numpy.frombuffer(subtitle_pictures.rgba(d['path']), numpy.uint8)
        pixels = pixels.reshape(d['height'], d['width'], 4).astype(numpy.float64)
        want = pixels[..., :3] * pixels[..., 3:4] / 255
        error = float(numpy.abs(region - want).mean())
        print(f'    FFmpeg over black with the .ifo\'s palette: mean error {error:.2f}')
        check('FFmpeg draws it in the drawing\'s colours with the .ifo\'s palette', error < 12, True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel, SUBTITLE_FORMATS
check('offered, with the text options, exported as a .sup, not a project format',
      ('DVDSUP' in SUBTITLE_FORMATS, 'DVDSUP' in export_options.TEXT_FORMATS,
       session.LIST_OF_PICTURE_SUBTITLE_EXTENSIONS['DVDSUP']['extensions'],
       'DVDSUP' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS), (True, True, ['sup'], False))
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 25}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DVDSUP')
panel._refresh_options()
check('the picture size, as VobSub\'s', (panel.vobsub_options.isHidden(), panel.get_config()['options'].get('vobsub_size')),
      (False, 'pal'))
check('no note: the preview shows the pictures', panel.check_label.isHidden(), True)

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
