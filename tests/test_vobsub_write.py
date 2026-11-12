"""VobSub written: subpicture units coded and decoded again (runs of every
length, both fields, the colours, opacities, area and forced start), the
program stream's 2048-byte packs (padding, stuffing, an SPU over several),
the pictures cut to four colours and one on screen at a time
(disc_pictures), and subtitles drawn by the preview's renderer, saved as
.idx and .sub through file_io and read back by vobsub.read, and by FFmpeg
where it is installed: the same times, places, sizes and forced flag, and
pixels close to the drawing's.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, disc_pictures, export_options, file_io, session, vobsub
from subtitld.interface import subtitle_pictures

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def over_grey(rgba, width, height):
    """Straight RGBA bytes laid on mid grey, as floats."""
    pixels = numpy.frombuffer(rgba, numpy.uint8).reshape(height, width, 4).astype(numpy.float64)
    alpha = pixels[..., 3:] / 255
    return pixels[..., :3] * alpha + 128 * (1 - alpha)


def picture_over_grey(picture):
    colours = numpy.array([c if len(c) == 4 else (0, 0, 0, 0) for c in picture.colours], numpy.float64)
    pixels = colours[numpy.frombuffer(picture.indices, numpy.uint8)].reshape(picture.height, picture.width, 4)
    alpha = pixels[..., 3:] / 255
    return pixels[..., :3] * alpha + 128 * (1 - alpha)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('subpicture units')
rng = numpy.random.default_rng(7)
width, height = 701, 37
rows = []
for y in range(height):
    # Runs of 1 to 3, 4 to 15, 16 to 63, 64 to 255, longer, and one to the end.
    row = []
    for length in (1, 2, 3, 4, 15, 16, 63, 64, 255, 256):
        row += [int(rng.integers(0, 4))] * length
    row += [int(rng.integers(0, 4))] * (width - len(row))
    rows.append(row)
indices = bytes(v for row in rows for v in row)
unit = vobsub.encode_spu(indices, width, height, 9, 500, 2.5, [0, 5, 6, 7], [0, 15, 12, 8], forced=True)
palette = [(n * 16, 255 - n * 16, n * 8) for n in range(16)]
picture, start, duration = vobsub.decode_spu(unit, palette)
check('its size in its first two bytes', int.from_bytes(unit[:2], 'big'), len(unit))
check('the pixels, both fields, every length of run', picture.indices == indices, True)
check('its area', (picture.x, picture.y, picture.width, picture.height), (9, 500, width, height))
check('its colours and their opacities, value 0 clear',
      picture.colours, [palette[0] + (0,), palette[5] + (255,), palette[6] + (204,), palette[7] + (136,)])
check('shown at once, for its duration, forced', (start, round(duration / vobsub.SPU_TICK), picture.forced),
      (0.0, round(2.5 / vobsub.SPU_TICK), True))
check('not forced: the ordinary start', vobsub.decode_spu(
    vobsub.encode_spu(indices, width, height, 0, 0, 1.0, [0, 1, 2, 3], [0, 15, 15, 15]), palette)[0].forced, False)
try:
    noise = rng.integers(0, 4, 720 * 576, dtype=numpy.uint8).tobytes()
    vobsub.encode_spu(noise, 720, 576, 0, 0, 1.0, [0, 1, 2, 3], [0, 15, 15, 15])
    check('a picture too big for an SPU says so', 'no error', 'VobSubError')
except vobsub.VobSubError as error:
    check('a picture too big for an SPU says so', 'more than a DVD subpicture holds' in str(error), True)

print('packs')
# The room in a first pack is 2048 - 14 (pack header) - 6 - 8 (PES header
# with its PTS) - 1 (substream): SPUs a few bytes short of it need stuffing.
for size in (100, 2013, 2014, 2016, 2018, 2019, 2020, 5000):
    spu = bytes([size >> 8, size & 255]) + bytes(rng.integers(0, 256, size - 2, dtype=numpy.uint8))
    stream = vobsub.packs(spu, 12.5, track=3)
    units = vobsub.parse_program_stream(stream)
    check(f'an SPU of {size} bytes: whole packs, read back whole, on track 3 at its PTS',
          (len(stream) % 2048, list(units), units[3][0][0], units[3][0][1], units[3][0][2] == spu),
          (0, [3], 0, 12.5 * 90000, True))

print('pictures cut to four colours')
solid = bytes([255, 255, 255, 255]) * 6 + bytes(4) * 2 + bytes([0, 0, 0, 255]) * 4
check('few colours kept as they are, 0 transparent', disc_pictures.reduce(solid, 4),
      (bytes([2] * 6 + [0] * 2 + [1] * 4), [(0, 0, 0, 0), (0, 0, 0, 255), (255, 255, 255, 255)]))
ramp = numpy.zeros((1, 256, 4), numpy.uint8)
ramp[0, :, :3] = numpy.arange(256)[:, None]
ramp[0, :, 3] = 255
ramp[0, :40, 3] = numpy.arange(40)          # some barely seen
cut, colours = disc_pictures.reduce(ramp.tobytes(), 4)
check('many: four at most, the first transparent', (len(colours) <= 4, colours[0], max(cut) < len(colours)),
      (True, (0, 0, 0, 0), True))
check('a pixel that barely shows goes to the transparent one', cut[0], 0)
first = {'start': 1.0, 'end': 3.0, 'x': 0, 'y': 50, 'width': 2, 'height': 1, 'rgba': bytes([255, 0, 0, 255]) * 2}
second = {'start': 2.0, 'end': 4.0, 'x': 1, 'y': 0, 'width': 1, 'height': 1, 'rgba': bytes([0, 0, 255, 255]),
          'forced': True}
shown = list(disc_pictures.timeline([first, second]))
check('overlapping: alone, then together, then the other alone',
      [(p['start'], p['end'], p['x'], p['y'], p['width'], p['height'], bool(p.get('forced'))) for p in shown],
      [(1.0, 2.0, 0, 50, 2, 1, False), (2.0, 3.0, 0, 0, 2, 51, True), (3.0, 4.0, 1, 0, 1, 1, True)])
both = numpy.frombuffer(shown[1]['rgba'], numpy.uint8).reshape(51, 2, 4).tolist()
check('together: each where it was', (both[0][1], both[50][0], both[20][0]),
      ([0, 0, 255, 255], [255, 0, 0, 255], [0, 0, 0, 0]))
check('cut to the frame', disc_pictures.fit({'x': -1, 'y': 0, 'width': 2, 'height': 1,
                                              'rgba': bytes([1, 2, 3, 4, 5, 6, 7, 8])}, (10, 10)),
      (0, 0, 1, 1, bytes([5, 6, 7, 8])))
check('wholly outside: nothing', disc_pictures.fit(first, (10, 10)), None)

print('frame sizes')
check('as asked, else the video\'s, else by the rate',
      [vobsub.frame_size('ntsc', 1920, 1080), vobsub.frame_size('video', 1280, 720), vobsub.frame_size(None, 0, 0, 29.97),
       vobsub.frame_size(None, 0, 0, 23.976), vobsub.frame_size(None, 0, 0, 25), vobsub.frame_size()],
      [(720, 480), (1280, 720), (720, 480), (720, 480), (720, 576), (720, 576)])

print('drawn, saved and read back')
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''},
            {'start': 7.0, 'end': 9.25, 'text': 'Ação, coração\ne emoção'}]
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'pt-br'}
session.VIDEO = {}
session.FORMAT = {'format': 'VOBSUB', 'options': {'vobsub_size': 'pal'}}
target = folder / 'film.idx'
file_io.save_file(str(target), 'VOBSUB')
check('the .idx and the .sub, and nothing else left', sorted(os.listdir(folder)), ['film.idx', 'film.sub'])
idx = target.read_text(encoding='utf-8')
info = vobsub.parse_idx(idx)
check('the .idx: its frame, its track in the language, a time for each',
      (info['size'], [(t['index'], t['language'], len(t['entries'])) for t in info['tracks']], idx.splitlines()[0]),
      ((720, 576), [(0, 'pt', 3)], '# VobSub index file, v7 (do not modify this line!)'))
drawn = subtitle_pictures.draw(export_options.prepare(SEGMENTS), (720, 576), str(folder / 'drawn'))
pictures, track = vobsub.read(str(target))
check('a picture each, at its time, ending within a tick of its end',
      [(p.start, abs(p.end - d['end']) <= vobsub.SPU_TICK / 2) for p, d in zip(pictures, drawn)],
      [(1.0, True), (3.0, True), (7.0, True)])
check('where the drawing was, as big', [(p.x, p.y, p.width, p.height) for p in pictures],
      [(d['x'], d['y'], d['width'], d['height']) for d in drawn])
check('the sign at the top and forced, the others not', [(p.y < 576 / 3, p.forced) for p in pictures],
      [(False, False), (True, True), (False, False)])
check('on the .idx\'s frame', {(p.screen_width, p.screen_height) for p in pictures}, {(720, 576)})
errors = []
for p, d in zip(pictures, drawn):
    want = over_grey(subtitle_pictures.rgba(d['path']), d['width'], d['height'])
    errors.append(float(numpy.abs(picture_over_grey(p) - want).mean()))
print('    mean error per channel over grey:', [round(e, 2) for e in errors])
check('pixels close to the drawing in four colours', all(e < 8 for e in errors), True)
check('four colours a subtitle, from the palette of sixteen', all(len(p.colours) == 4 for p in pictures), True)
check('the .sub read alone gives the same', [(p.start, p.x) for p in vobsub.read(str(folder / 'film.sub'))[0]],
      [(p.start, p.x) for p in pictures])

session.VIDEO = {'width': 1280, 'height': 720, 'framerate': 23.976}
session.FORMAT = {'format': 'VOBSUB', 'options': {}}
file_io.save_file(str(folder / 'video.idx'), 'VOBSUB')
check('no size asked: the video\'s', vobsub.parse_idx((folder / 'video.idx').read_text())['size'], (1280, 720))
session.VIDEO = {'framerate': 29.97}
file_io.save_file(str(folder / 'ntsc.idx'), 'VOBSUB')
check('no video size: the DVD frame of the rate', vobsub.parse_idx((folder / 'ntsc.idx').read_text())['size'],
      (720, 480))
session.FORMAT = {'format': 'VOBSUB', 'options': {'text': 'translation', 'language': 'fr'}}
session.SUBTITLE = {'segments': [dict(SEGMENTS[0], translations={'fr': 'Bonjour'})], 'language': 'en'}
file_io.save_file(str(folder / 'fr.idx'), 'VOBSUB')
check('a translation: in its language', vobsub.parse_idx((folder / 'fr.idx').read_text())['tracks'][0]['language'],
      'fr')
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'pt-br'}
session.VIDEO = {}

print('FFmpeg')
FFMPEG = shutil.which('ffmpeg')
if not FFMPEG:
    print('  (no ffmpeg: skipped)')
else:
    # The VobSub laid on a grey frame by FFmpeg's own decoder, at a time
    # within each subtitle, against the drawing laid on the same grey.
    for p, d in zip(pictures, drawn):
        moment = (d['start'] + d['end']) / 2
        result = subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x808080:s=720x576:r=25:d=10',
                                 '-i', str(target), '-filter_complex', '[0:v][1:s]overlay', '-ss', f'{moment:.3f}',
                                 '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                capture_output=True)
        if result.returncode or len(result.stdout) != 720 * 576 * 3:
            check(f'FFmpeg draws the subtitle at {moment}', result.stderr.decode()[-300:], '')
            continue
        frame = numpy.frombuffer(result.stdout, numpy.uint8).reshape(576, 720, 3).astype(numpy.float64)
        region = frame[d['y']:d['y'] + d['height'], d['x']:d['x'] + d['width']]
        want = over_grey(subtitle_pictures.rgba(d['path']), d['width'], d['height'])
        error = float(numpy.abs(region - want).mean())
        outside = float(numpy.abs(numpy.delete(frame, numpy.s_[d['y']:d['y'] + d['height']], axis=0) - 128).max())
        print(f'    at {moment}: mean error {error:.2f}, the rest of the frame off grey by {outside}')
        check(f'FFmpeg draws the subtitle at {moment} where it was drawn, nothing elsewhere',
              (error < 12, outside <= 2), (True, True))
    result = subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x808080:s=720x576:r=25:d=10',
                             '-i', str(target), '-filter_complex', '[0:v][1:s]overlay', '-ss', '6.5',
                             '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True)
    check('and none between them', set(result.stdout) <= {127, 128, 129}, True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel, SUBTITLE_FORMATS
check('offered, with the text options', ('VOBSUB' in SUBTITLE_FORMATS, 'VOBSUB' in export_options.TEXT_FORMATS),
      (True, True))
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'width': 1920, 'height': 1080, 'framerate': 25}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('VOBSUB')
panel._refresh_options()
check("the picture size, the video's first", (panel.vobsub_options.isHidden(), panel.text_options.isHidden(),
                                              panel.get_config()['options'].get('vobsub_size'),
                                              panel.vobsub_size_combo.combobox.count()), (False, False, 'video', 3))
check('what the pictures will be said', (panel.check_label.isHidden(), 'DVD' in panel.check_label.text()), (False, True))
panel.vobsub_size_combo.combobox.setCurrentIndex(panel.vobsub_size_combo.combobox.findData('ntsc'))
panel.get_config()
session.VIDEO = {}
check('remembered when there is no video', _SubtitlesPanel().vobsub_size_combo.combobox.currentData(), 'ntsc')
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 29.97}
check('else the DVD frame of the rate', _SubtitlesPanel().vobsub_size_combo.combobox.currentData(), 'ntsc')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.vobsub_options.isHidden(), 'vobsub_size' in panel.get_config()['options']), (True, False))
check('exported by its .idx, not a project format',
      (session.LIST_OF_PICTURE_SUBTITLE_EXTENSIONS['VOBSUB']['extensions'], 'VOBSUB' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS),
      (['idx'], False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
