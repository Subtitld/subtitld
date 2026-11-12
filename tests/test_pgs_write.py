"""PGS written: objects run-length coded and decoded again (single pixels,
short and long runs, of colour 0 and others), an object split over several
ODS segments, the palette in YCbCr for HD and SD, the frame rate codes,
the display sets of an epoch and the composition that clears it (or none,
when the next subtitle takes its place), and subtitles drawn by the
preview's renderer, saved as .sup through file_io and read back by
pgs.read, and by FFmpeg where it is installed: the same times, places,
sizes and forced flag, and pixels close to the drawing's.

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
from subtitld.modules import config, export_options, file_io, pgs, session
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
    colours = numpy.array(picture.colours, numpy.float64)
    pixels = colours[numpy.frombuffer(picture.indices, numpy.uint8)].reshape(picture.height, picture.width, 4)
    alpha = pixels[..., 3:] / 255
    return pixels[..., :3] * alpha + 128 * (1 - alpha)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())
rng = numpy.random.default_rng(11)

print('objects')
width, height = 5000 // 4, 9
rows = []
for y in range(height):
    row = []
    for length in (1, 2, 3, 63, 64, 200):
        row += [0] * length + [int(rng.integers(1, 256))] * length
    row += [int(rng.integers(0, 256))] * (width - len(row))
    rows.append(row)
indices = bytes(v for row in rows for v in row)
check('runs of every length, of 0 and of other colours, read back',
      pgs.decode_rle(pgs.encode_rle(indices, width, height), width, height) == indices, True)
noise = rng.integers(0, 256, 1920 * 120, dtype=numpy.uint8).tobytes()
colours = [(0, 0, 0, 0)] + [(int(v), 255 - int(v), 128, 255) for v in range(1, 256)]
data = pgs.display_sets(0, 900, 1920, 120, noise, colours, 2.0, 3.0, (1920, 1080), pgs.rate_code(25), 0)
kinds = [kind for _pts, kind, _payload in pgs.segments(data)]
check('a large object over several ODS, then the composition that clears it',
      (kinds[:3], kinds.count(pgs.ODS) > 3, kinds[-4:]),
      ([pgs.PCS, pgs.WDS, pgs.PDS], True, [pgs.END, pgs.PCS, pgs.WDS, pgs.END]))
check('no segment longer than its 16 bits hold', all(len(payload) <= 0xFFFF for _p, _k, payload in pgs.segments(data)),
      True)
shown = pgs.parse(data)
check('read back whole, where it was, when it was', [(p.start, p.end, p.x, p.y, p.width, p.height, p.indices == noise)
                                                    for p in shown], [(2.0, 3.0, 0, 900, 1920, 120, True)])
check('the palette, HD (BT.709), within a step of each colour',
      max(abs(a - b) for got, want in zip(shown[0].colours[1:256], colours[1:]) for a, b in zip(got, want)) <= 2, True)
small = pgs.parse(pgs.display_sets(0, 0, 2, 1, b'\x01\x02', [(0, 0, 0, 0), (255, 255, 0, 255), (20, 40, 200, 128)],
                                   0.0, 1.0, (720, 480), pgs.rate_code(29.97), 0))
check('SD (BT.601) as well, the opacity as it was', [tuple(c) for c in small[0].colours[1:3]],
      [(255, 255, 0, 255), (20, 40, 200, 128)])
check('the frame rate codes', [pgs.rate_code(r) for r in (23.976, 24, 25, 29.97, 50, 59.94, None, 30)],
      [0x10, 0x20, 0x30, 0x40, 0x60, 0x70, 0x10, 0x40])
follows = pgs.display_sets(0, 0, 2, 1, b'\x01\x01', small[0].colours[:2], 1.0, None, (720, 480), 0x10, 0)
check('followed at once by the next: no composition to clear it',
      [kind for _p, kind, _d in pgs.segments(follows)].count(pgs.PCS), 1)

print('drawn, saved and read back')
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 2.5, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''},
            {'start': 7.0, 'end': 9.25, 'text': 'Ação, coração\ne emoção'}]
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'pt-br'}
session.VIDEO = {'width': 1280, 'height': 720, 'framerate': 25}
session.FORMAT = {'format': 'PGS', 'options': {}}
target = folder / 'film.sup'
file_io.save_file(str(target), 'PGS')
check('the .sup, and nothing else left', sorted(os.listdir(folder)), ['film.sup'])
raw = target.read_bytes()
sets = [(round(pts, 3), payload) for pts, kind, payload in pgs.segments(raw) if kind == pgs.PCS]
check('a composition at each start; cleared at each end but where the next starts',
      [(pts, payload[7], payload[10]) for pts, payload in sets],
      [(1.0, 0x80, 1), (2.5, 0x80, 1), (4.0, 0, 0), (7.0, 0x80, 1), (9.25, 0, 0)])
check("the video's frame and rate", {(payload[:4], payload[4]) for _pts, payload in sets},
      {(bytes([5, 0, 2, 208]), 0x30)})
check('composition numbers counting up', [int.from_bytes(payload[5:7], 'big') for _pts, payload in sets],
      [0, 2, 3, 4, 5])
check('each DTS at or before its PTS', all(int.from_bytes(raw[i + 6:i + 10], 'big') <= int.from_bytes(raw[i + 2:i + 6], 'big')
                                           for i in range(len(raw)) if raw[i:i + 2] == b'PG' and raw[i + 10:i + 11] in
                                           (b'\x16', b'\x17', b'\x14', b'\x15', b'\x80')), True)
drawn = subtitle_pictures.draw(export_options.prepare(SEGMENTS), (1280, 720), str(folder / 'drawn'))
pictures = pgs.read(str(target))
check('a picture each, at its times', [(p.start, p.end) for p in pictures], [(1.0, 2.5), (2.5, 4.0), (7.0, 9.25)])
check('where the drawing was, as big', [(p.x, p.y, p.width, p.height) for p in pictures],
      [(d['x'], d['y'], d['width'], d['height']) for d in drawn])
check('the sign at the top and forced, the others not', [(p.y < 720 / 3, p.forced) for p in pictures],
      [(False, False), (True, True), (False, False)])
check("on the video's frame", {(p.screen_width, p.screen_height) for p in pictures}, {(1280, 720)})
errors = []
for p, d in zip(pictures, drawn):
    want = over_grey(subtitle_pictures.rgba(d['path']), d['width'], d['height'])
    errors.append(float(numpy.abs(picture_over_grey(p) - want).mean()))
print('    mean error per channel over grey:', [round(e, 2) for e in errors])
check('pixels close to the drawing', all(e < 2 for e in errors), True)
session.VIDEO = {}
file_io.save_file(str(folder / 'hd.sup'), 'PGS')
check('no video: 1920x1080 at 23.976', {(p.screen_width, p.screen_height) for p in pgs.read(str(folder / 'hd.sup'))},
      {(1920, 1080)})

print('FFmpeg')
FFMPEG = shutil.which('ffmpeg')
if not FFMPEG:
    print('  (no ffmpeg: skipped)')
else:
    # FFmpeg's decoder: a subtitle at each composition's time, and nothing
    # at each clearing one.
    result = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error', '-show_frames', '-show_entries',
                             'subtitle=pts_time,num_rects', '-of', 'csv=p=0', str(target)], capture_output=True, text=True)
    check('FFmpeg decodes each display set, at its time',
          [tuple(line.split(',')) for line in result.stdout.split()],
          [('1.000000', '1'), ('2.500000', '1'), ('4.000000', '0'), ('7.000000', '1'), ('9.250000', '0')])
    # Each subtitle laid on a grey frame by FFmpeg, against the drawing
    # laid on the same grey. A .sup of its own each, shown for long: FFmpeg
    # lays a subtitle that lasts until the next one some way before its
    # time (a .sup written by tests/picture_samples does the same).
    for number, d in enumerate(drawn):
        alone = folder / f'alone{number}.sup'
        pgs.save(str(alone), [dict(d, start=1.0, end=9.0, rgba=subtitle_pictures.rgba(d['path']))], (1280, 720), 25)
        result = subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x808080:s=1280x720:r=25:d=10',
                                 '-i', str(alone), '-filter_complex', '[0:v][1:s]overlay', '-ss', '5',
                                 '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                capture_output=True)
        if result.returncode or len(result.stdout) != 1280 * 720 * 3:
            check(f'FFmpeg draws subtitle {number + 1}', result.stderr.decode()[-300:], '')
            continue
        frame = numpy.frombuffer(result.stdout, numpy.uint8).reshape(720, 1280, 3).astype(numpy.float64)
        region = frame[d['y']:d['y'] + d['height'], d['x']:d['x'] + d['width']]
        want = over_grey(subtitle_pictures.rgba(d['path']), d['width'], d['height'])
        error = float(numpy.abs(region - want).mean())
        outside = float(numpy.abs(numpy.delete(frame, numpy.s_[d['y']:d['y'] + d['height']], axis=0) - 128).max())
        print(f'    subtitle {number + 1}: mean error {error:.2f}, the rest of the frame off grey by {outside}')
        check(f'FFmpeg draws subtitle {number + 1} where it was drawn, nothing elsewhere',
              (error < 8, outside <= 2), (True, True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel, SUBTITLE_FORMATS
check('offered, with the text options', ('PGS' in SUBTITLE_FORMATS, 'PGS' in export_options.TEXT_FORMATS),
      (True, True))
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('PGS')
panel._refresh_options()
check('the text options, no picture size to choose, what the pictures will be said',
      (panel.text_options.isHidden(), panel.vobsub_options.isHidden(), panel.check_label.isHidden(),
       '1920x1080' in panel.check_label.text()), (False, True, False, True))
check('exported by its .sup, not a project format',
      (session.LIST_OF_PICTURE_SUBTITLE_EXTENSIONS['PGS']['extensions'], 'PGS' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS),
      (['sup'], False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
