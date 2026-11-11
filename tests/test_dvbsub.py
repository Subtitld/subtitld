"""DVB subtitles (EN 300 743 in a transport stream): display sets built
segment by segment here (2-, 4- and 8-bit pixel code strings, top and
bottom fields and a top field alone, map tables, default and defined
palettes, full and reduced range, an ancillary page, a display window, a
page time-out, a page sent again as it was, characters for an object, PTS
wrapping) decoded into pictures; the pixel code strings Subtitld writes
read back line by line; colours brought into 256 and 16 entries; a
transport stream written (PAT, PMT with the subtitling descriptor, a
stream for each language, an object too large for one segment cut into
bands) read back, as 188- and 192-byte packets, its times, places,
colours, languages and types; a service of a stream that carries two
listed as a track of its own; the pictures through to an OCR add-on, from
a file and from a video's track; and, where FFmpeg is installed, what it
makes of the streams written (probed, decoded, drawn over a picture), a
video written out with them, opened, its track read back as it is and
from a Matroska file, and the export path and its place in the dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import json, os, random, shutil, subprocess, sys, tempfile
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
from subtitld.modules import config, session
session.set_unsaved = lambda *a, **k: None
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
from subtitld.modules import dvbsub, picture_ocr, picture_subtitles, teletext, video_subtitles
from subtitld.modules.addons.provider import OCRProvider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())


def seg(kind, page, data):
    return bytes((0x0F, kind, page >> 8, page & 0xFF, len(data) >> 8, len(data) & 0xFF)) + bytes(data)


def bits(text):
    """Bytes of a string of 0s and 1s, stuffed with 0s to a whole byte."""
    text = text.replace(' ', '')
    text += '0' * (-len(text) % 8)
    return bytes(int(text[i:i + 8], 2) for i in range(0, len(text), 8))


def pcs(page, timeout, version, state, regions):
    return seg(0x10, page, bytes((timeout, version << 4 | state << 2 | 3)) +
               b''.join(bytes((r, 0xFF)) + x.to_bytes(2, 'big') + y.to_bytes(2, 'big') for r, x, y in regions))


def rcs(page, region, version, width, height, depth, clut, objects, fill=True, background=0):
    level = {2: 1, 4: 2, 8: 3}[depth]
    return seg(0x11, page, bytes((region, version << 4 | (0x08 if fill else 0) | 7)) + width.to_bytes(2, 'big') +
               height.to_bytes(2, 'big') + bytes((level << 5 | level << 2 | 3, clut, background,
                                                  (background & 0xF) << 4 | (background & 3) << 2 | 3)) +
               b''.join(o.to_bytes(2, 'big') + bytes(((x >> 8) & 0x0F, x & 0xFF, 0xF0 | (y >> 8), y & 0xFF))
                        for o, x, y in objects))


def ods(page, number, top, bottom=b''):
    return seg(0x13, page, number.to_bytes(2, 'big') + b'\x01' + len(top).to_bytes(2, 'big') +
               len(bottom).to_bytes(2, 'big') + top + bottom)


def pes(*segments):
    return b'\x20\x00' + b''.join(segments) + b'\xff'


END = seg(0x80, 1, b'')

print('decoded segment by segment')
# A 2-bit line: five of 1 (a run of 3 to 10), two of 0, one of 3, the end.
two_bit = b'\x10' + bits('00 1 010 01' + '00 0 0 01' + '11' + '00 0 0 00') + b'\xf0'
# A 4-bit line: 2, then 9 to 24 of 5 (twelve), three of 0, 6, the end.
four_bit = b'\x11' + bits('0010' + '0000 1 1 10 0011 0101' + '0000 0 001' + '0110' + '0000 0 000') + b'\xf0'
# An 8-bit line: 200, four of 0, a run of 3 of 9, the end.
eight_bit = b'\x12' + bytes((200, 0, 4, 0, 0x83, 9, 0, 0)) + b'\xf0'
# A palette on the ancillary page: entry 3 of the 2-bit palette in reduced
# range (Y 6 bits, Cr and Cb 4, T 2), 9 of the 8-bit one in full range.
reduced = (60 << 10) | (8 << 6) | (8 << 2) | 0              # gray, opaque
clut = seg(0x12, 5, bytes((7, 0x0F, 3, 0x80 | 0x1E, reduced >> 8, reduced & 0xFF,
                           9, 0x20 | 0x1F, 81, 90, 240, 0)))   # pure blue-ish, opaque
display = seg(0x14, 1, bytes((0x0F,)) + (1919).to_bytes(2, 'big') + (1079).to_bytes(2, 'big') +
              (100).to_bytes(2, 'big') + (1819).to_bytes(2, 'big') + (50).to_bytes(2, 'big') + (1029).to_bytes(2, 'big'))
packets = [
    (90000, pes(display, pcs(1, 10, 0, 2, [(1, 10, 20), (2, 10, 40), (3, 10, 60)]),
                rcs(1, 1, 0, 8, 2, 2, 7, [(1, 0, 0)]), rcs(1, 2, 0, 18, 2, 4, 7, [(2, 0, 0)]),
                rcs(1, 3, 0, 8, 1, 8, 7, [(3, 0, 0)]), clut,
                ods(1, 1, two_bit), ods(1, 2, four_bit, two_bit), ods(1, 3, eight_bit), END)),
    # Sent again as it was (an acquisition point): the same subtitle, on.
    (90000 * 2, pes(display, pcs(1, 10, 1, 1, [(1, 10, 20), (2, 10, 40), (3, 10, 60)]),
                    rcs(1, 1, 0, 8, 2, 2, 7, [(1, 0, 0)]), rcs(1, 2, 0, 18, 2, 4, 7, [(2, 0, 0)]),
                    rcs(1, 3, 0, 8, 1, 8, 7, [(3, 0, 0)]), clut,
                    ods(1, 1, two_bit), ods(1, 2, four_bit, two_bit), ods(1, 3, eight_bit), END)),
    # A page of another service, which is not this one's.
    (90000 * 3, pes(seg(0x10, 9, bytes((5, 0x0B)) + bytes((1, 0xFF, 0, 0, 0, 0))), seg(0x80, 9, b''))),
    # The page taken away at 4 s; then one with a time-out of 2 s.
    (90000 * 4, pes(pcs(1, 0, 2, 0, []), END)),
    (90000 * 6, pes(pcs(1, 2, 3, 2, [(4, 0, 0)]), rcs(1, 4, 0, 4, 1, 4, 0, [(4, 0, 0)]),
                    ods(1, 4, b'\x11' + bits('0000 1 0 00 0001' + '0000 0 000') + b'\xf0'), END)),
    (90000 * 12, pes(pcs(1, 0, 4, 0, []), END)),
]
pictures = dvbsub.decode(packets, page=1, ancillary=5, origin=0)
check('three subtitles: the first sent again kept as one, the time-out ending the last',
      [(p.start, p.end) for p in pictures], [(1.0, 4.0), (6.0, 8.0)])
first = pictures[0]
check('the regions in one picture, placed in the display window, on a 1920x1080 display',
      (first.x, first.y, first.width, first.height, first.screen_width, first.screen_height),
      (110, 70, 18, 41, 1920, 1080))


def row(picture, y, x=0, width=None):
    width = picture.width if width is None else width
    return [picture.colours[picture.indices[(y - picture.y) * picture.width + x + k]] for k in range(width)]


WHITE, CLEAR = (255, 255, 255, 255), (0, 0, 0, 0)
GRAY = dvbsub.ycrcb_to_rgba(240, 128, 128, 0)
check('2-bit: runs, two of 0 and the defined palette\'s entry 3 (reduced range); no bottom field: the top\'s again',
      (row(first, 70, 0, 8), row(first, 71, 0, 8)), ([WHITE] * 5 + [CLEAR] * 2 + [GRAY],) * 2)
four_white = dvbsub.DEFAULT_CLUTS[4][7]
check('4-bit: the default palette, the runs of 9 to 24 and of 0; the bottom field a 2-bit line through the map table',
      (row(first, 90), row(first, 91, 0, 8)),
      ([dvbsub.DEFAULT_CLUTS[4][2]] + [dvbsub.DEFAULT_CLUTS[4][5]] * 12 + [CLEAR] * 3 + [dvbsub.DEFAULT_CLUTS[4][6]]
       + [CLEAR], [four_white] * 5 + [CLEAR] * 2 + [dvbsub.DEFAULT_CLUTS[4][15]]))
check('8-bit: a code, a run of 0, a run of a colour, the full-range entry',
      row(first, 110, 0, 8), [dvbsub.DEFAULT_CLUTS[8][200]] + [CLEAR] * 4 + [dvbsub.ycrcb_to_rgba(81, 90, 240, 0)] * 3)
check('a page with no display definition: 720x576', dvbsub.decode([(0, pes(
    pcs(1, 0, 0, 2, [(1, 5, 5)]), rcs(1, 1, 0, 2, 1, 4, 0, [(1, 0, 0)]), ods(1, 1, b'\x11' + bits('0001 0001 00000000')),
    END))])[0].screen_width, 720)
wrapped = dvbsub.decode([((1 << 33) - 90000, pes(pcs(1, 0, 0, 2, [(1, 0, 0)]), rcs(1, 1, 0, 1, 1, 4, 0, [(1, 0, 0)]),
                                                  ods(1, 1, b'\x11' + bits('0001 00000000')), END)),
                         (90000, pes(pcs(1, 0, 1, 0, []), END))])
check('the PTS wrapping round', [(p.start, p.end) for p in wrapped], [(0.0, 2.0)])
text = dvbsub.decode([(0, pes(pcs(1, 0, 0, 2, [(1, 0, 0)]), rcs(1, 1, 0, 4, 4, 4, 0, [(1, 0, 0)]),
                              seg(0x13, 1, b'\x00\x01\x05' + bytes((2, 0, 0x48, 0, 0x69)))))])
check('an object coded as characters: its words, nothing to read', [(p.text, p.end) for p in text], [('Hi', 5.0)])

print('pixel code strings written')
random.seed(7)
for depth, encode in ((8, dvbsub.encode_string8), (4, dvbsub.encode_string4)):
    lines = []
    for _ in range(200):
        line, top = [], (1 << depth) - 1
        while len(line) < 700:
            line += [random.choice([0, 0, random.randint(0, top)])] * random.choice([1, 1, 2, 3, 4, 7, 8, 9, 24, 25, 280, 300])
        lines.append(bytes(line[:700]))
    region = {'width': 700, 'height': 1, 'depth': depth, 'pixels': bytearray(700)}
    same = True
    for line in lines:
        region['pixels'][:] = bytes(700)
        dvbsub.draw_field(bytes((0x12 if depth == 8 else 0x11,)) + encode(line), region, 0, 0, 0)
        same = same and bytes(region['pixels']) == line
    check(f'{depth}-bit: every run read back as written', same, True)

print('colours')
gradient = numpy.zeros((64, 400, 4), numpy.uint8)
gradient[..., 0] = numpy.arange(400)[None, :] * 255 // 399
gradient[..., 1] = numpy.arange(64)[:, None] * 4
gradient[..., 2] = 40
gradient[..., 3] = 255
gradient[:, :20, 3] = 0
for entries in (256, 16):
    indices, palette = dvbsub.quantise(gradient, entries)
    flat = numpy.array([palette[i] for i in indices], numpy.int64).reshape(64, 400, 4)
    error = numpy.abs(flat[:, 20:, :3] - gradient[:, 20:, :3]).mean()
    check(f'{entries} entries: no more, entry 0 what does not show, near the picture',
          (len(palette) <= entries, palette[0], {indices[k] for k in range(20)}, bool(error < (6 if entries == 256 else 30))),
          (True, CLEAR, {0}, True))
check('Y, Cr, Cb and T and back', [dvbsub.ycrcb_to_rgba(*dvbsub.rgba_to_ycrcb(*c)) for c in
                                   ((255, 255, 0, 255), (0, 0, 255, 128), (16, 200, 90, 30))],
      [(255, 255, 0, 255), (0, 0, 255, 128), (16, 200, 90, 30)])

print('a transport stream written and read back')


def card(width, height, colour, alpha=255):
    picture = numpy.zeros((height, width, 4), numpy.uint8)
    picture[4:height - 4, 6:width - 6] = colour + (alpha,)
    picture[height // 2 - 2:height // 2 + 2, 10:width - 10] = (20, 20, 20, 255)
    return dvbsub.quantise(picture)


yellow, blue = card(300, 40, (255, 255, 0)), card(200, 30, (40, 80, 255), 160)
# A gradient of 256 grays across the picture: no runs to code at 8 bits.
ramp = numpy.zeros((200, 1920, 4), numpy.uint8)
ramp[..., :3] = (numpy.arange(1920) * 256 // 1920)[None, :, None]
ramp[..., 3] = 255
big = dvbsub.quantise(ramp)
tracks = [{'language': 'eng', 'pictures': [
    {'start': 1.0, 'end': 2.5, 'x': 810, 'y': 950, 'width': 300, 'height': 40, 'indices': yellow[0], 'palette': yellow[1]},
    {'start': 2.0, 'end': 3.0, 'x': 860, 'y': 60, 'width': 200, 'height': 30, 'indices': blue[0], 'palette': blue[1]}]},
    {'language': 'por', 'hard_of_hearing': True, 'pictures': [
        {'start': 1.5, 'end': 2.0, 'x': 0, 'y': 800, 'width': 1920, 'height': 200, 'indices': big[0], 'palette': big[1]}]}]
written = dvbsub.transport_stream(tracks)
(folder / 'subs.ts').write_bytes(written)
check('a stream for each language, each service named in the PMT',
      dvbsub.streams(folder / 'subs.ts'), {0x102: [{'language': 'eng', 'type': 0x14, 'page': 1, 'ancillary': 1}],
                                           0x103: [{'language': 'por', 'type': 0x24, 'page': 2, 'ancillary': 2}]})
check('a picture too large for a segment, at 8 bits, cut into bands',
      len(dvbsub.encode_picture(tracks[1]['pictures'][0])) > 1, True)
read, info = dvbsub.read(folder / 'subs.ts')
check('the first service: its language, type, page and PID', info, {'language': 'eng', 'type': 0x14, 'page': 1, 'pid': 0x102})
check('the times: changed when one starts or ends, the two shown together in one picture',
      [(p.start, p.end, p.x, p.y, p.width, p.height) for p in read],
      [(1.0, 2.0, 810, 950, 300, 40), (2.0, 2.5, 810, 60, 300, 930), (2.5, 3.0, 860, 60, 200, 30)])


def colours_of(picture):
    return sorted({picture.colours[i] for i in set(picture.indices)})


def close(found, want):
    """Colours the same but for a step of rounding in Y, Cr and Cb."""
    return len(found) == len(want) and all(max(abs(a - b) for a, b in zip(f, w)) <= 1 for f, w in zip(found, want))


check('the colours, after the palette and Y, Cr, Cb (within a step of rounding)',
      (close(colours_of(read[0]), [CLEAR, (20, 20, 20, 255), (255, 255, 0, 255)]),
       close(colours_of(read[2]), [CLEAR, (20, 20, 20, 255), (40, 80, 255, 160)])), (True, True))
m2ts = b''.join(b'\x00\x00\x00\x00' + written[i:i + 188] for i in range(0, len(written), 188))
(folder / 'subs.m2ts').write_bytes(m2ts)
second, info = dvbsub.read(folder / 'subs.m2ts', page='2')
grays = sorted({c[0] for c in colours_of(second[0])})
check('192-byte packets; the other service by its page, for the hard of hearing; too large for one PES packet in '
      '256 colours, it is in 16, still a gradient',
      ([(p.start, p.end, p.y, p.height) for p in second], info['type'], len(grays) <= 16 and grays[0] < 20 < 235 < grays[-1],
       all(a[0] <= b[0] for a, b in zip(row(second[0], 900), row(second[0], 900)[1:]))),
      ([(1.5, 2.0, 800, 200)], 0x24, True, True))
# Noise does not fit in one PES packet even in 16 colours: the display set
# takes several, read back as one.
noise = numpy.random.default_rng(3).integers(0, 255, (200, 1920, 4), dtype=numpy.uint8)
noise[..., 3] = 255
loud = dvbsub.quantise(noise)
(folder / 'noise.ts').write_bytes(dvbsub.transport_stream([{'language': 'eng', 'pictures': [
    {'start': 1.0, 'end': 2.0, 'x': 0, 'y': 0, 'width': 1920, 'height': 200, 'indices': loud[0], 'palette': loud[1]}]}]))
heard = dvbsub.read(folder / 'noise.ts')[0]
reduced = dvbsub.fewer_colours(dict(width=1920, height=200, indices=loud[0], palette=loud[1]))
check('a display set in several PES packets, in bands, read back as written',
      ([(p.start, p.end, p.width, p.height) for p in heard],
       [heard[0].colours[i] for i in heard[0].indices[:3000:7]] ==
       [dvbsub.ycrcb_to_rgba(*dvbsub.rgba_to_ycrcb(*reduced['palette'][i])) for i in reduced['indices'][:3000:7]]),
      ([(1.0, 2.0, 1920, 200)], True))
try:
    dvbsub.read(folder / 'subs.m2ts', pid=0x999, page='7')
    check('no such service: the first stream there is', True, True)
except ValueError:
    check('no such service: the first stream there is', False, True)
(folder / 'none.ts').write_bytes(teletext.transport_stream([{'page': '888', 'language': 'eng', 'segments': []}]))
try:
    dvbsub.read(folder / 'none.ts')
    check('no DVB subtitles: an error', None, 'ValueError')
except ValueError:
    check('no DVB subtitles: an error', True, True)

print('a stream with two services')
counters = {}
descriptor = b'ger\x10\x00\x01\x00\x01' + b'fre\x20\x00\x02\x00\x02'
pat = b'\x00' + teletext._section(0, 1, bytes((0, 1, 0xF0, 0x00)))
pmt = b'\x00' + teletext._section(2, 1, bytes((0xE1, 0x01, 0xF0, 0)) + bytes((6, 0xE1, 0x05, 0xF0, 2 + len(descriptor),
                                                                              0x59, len(descriptor))) + descriptor)
shared = []
for page, start in ((1, 1.0), (2, 2.0)):
    shared += dvbsub._pes(dvbsub.display_set(page, [dict(tracks[0]['pictures'][0], bands=dvbsub.encode_picture(
        tracks[0]['pictures'][0]))], dvbsub.HD_DISPLAY, 0, 3), int(start * 90000))
    shared += dvbsub._pes(dvbsub.display_set(page, [], dvbsub.HD_DISPLAY, 1, 0), int((start + 0.5) * 90000))
stream = teletext._ts_packets(0, pat, counters) + teletext._ts_packets(0x1000, pmt, counters)
for packet in shared:
    stream += teletext._ts_packets(0x105, packet, counters)
(folder / 'shared.ts').write_bytes(b''.join(stream))
listed = video_subtitles.with_dvb_pages([{'index': 0, 'codec': 'dvb_subtitle', 'kind': 'pictures', 'pid': 0x105,
                                          'language': '', 'language_tag': 'ger', 'title': '', 'default': False,
                                          'forced': False}], str(folder / 'shared.ts'),
                                        language_of=lambda tag: {'ger': 'de-de', 'fre': 'fr-fr'}[tag])
check('a track for each, its language, page and whether it is for the hard of hearing',
      [(t['page'], t['language_tag'], t['language'], t['hearing_impaired'], video_subtitles.key(t)) for t in listed],
      [('1', 'ger', 'de-de', False, '0/1'), ('2', 'fre', 'fr-fr', True, '0/2')])
check('each read on its own, timed from the file\'s start', [[(p.start, p.end) for p in video_subtitles.extract_pictures(
    str(folder / 'shared.ts'), 0, 'dvb_subtitle', pid=t['pid'], page=t['page'])] for t in listed],
      [[(0.0, 0.5)], [(1.0, 1.5)]])


print('the OCR path')


class FakeOCR(OCRProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append((len(image_paths), language))
        QTimer.singleShot(0, lambda: self.finished.emit(['Hello'] * len(image_paths), [0.95] * len(image_paths)))


def run(job):
    loop, out = QEventLoop(), {}
    job.finished.connect(lambda found, info: (out.update(segments=found, info=info), loop.quit()))
    job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
    job.start()
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    return out


session.PATH_TEMP = tempfile.mkdtemp()
engine = FakeOCR()
out = run(picture_ocr.OCRJob.for_file(str(folder / 'subs.ts'), engine))
check('a transport stream opened for its pictures: each read, in the language its descriptor gives, the top one marked',
      (engine.calls, [(s['start'], s['end'], s['text']) for s in out.get('segments', [])], out.get('info', {}).get('language')),
      ([(3, 'eng')], [(1.0, 2.5, 'Hello'), (2.5, 3.0, '{\\an8}Hello')], 'en-us'))
check('a DVB track is one an OCR add-on reads', 'dvb_subtitle' in video_subtitles.READABLE_PICTURE_CODECS, True)
check('the extensions a transport stream has', picture_ocr.TRANSPORT_STREAM_EXTENSIONS, ('.ts', '.m2ts', '.mts'))
check('pictures cropped for the OCR: what shows', picture_subtitles.crop(read[0]).width, 288)

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed: the streams are not checked against it')
else:
    print('FFmpeg')
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
                            'stream=codec_name,id:stream_tags=language:stream_disposition=hearing_impaired', '-of',
                            'csv=p=0', str(folder / 'subs.ts')], capture_output=True, text=True).stdout.split()
    # Each stream is listed under its programme too, without its tags.
    check('probed: DVB subtitles, the languages, the one for the hard of hearing',
          sorted({line for line in probe if line.count(',') == 3}),
          ['dvb_subtitle,0x102,0,eng', 'dvb_subtitle,0x103,1,por'])
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(folder / 'subs.ts'), '-map', '0:s', '-c:s', 'dvbsub',
                              str(folder / 'again.ts')], capture_output=True, text=True)
    # FFmpeg's parser hands over the first PES packet of a stream as it is,
    # which its decoder refuses: here, the page with nothing on it at 0.
    errors = [line for line in decoded.stderr.splitlines() if 'Invalid data' not in line]
    again = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 's:0', '-show_entries', 'packet=size', '-of',
                            'csv=p=0', str(folder / 'again.ts')], capture_output=True, text=True).stdout.split()
    check('decoded by FFmpeg (and encoded again): a subtitle for each display set with something on it',
          (decoded.returncode, errors, sum(1 for size in again if int(size.strip(',')) > 100)), (0, [], 3))

    def frames(path, times, canvas=(1920, 1080), stream='0:s:0'):
        """The colour FFmpeg draws at points of the picture, at `times`."""
        width, height = canvas
        made = subprocess.run([ffmpeg, '-v', 'error', '-canvas_size', f'{width}x{height}', '-i', str(path),
                               '-filter_complex', f'color=c=black:s={width}x{height}:r=4:d=5[b];[b][{stream}]overlay'
                               '=eof_action=pass,format=rgb24', '-f', 'rawvideo', '-'], capture_output=True).stdout
        size = width * height * 3
        return {when: made[int(when * 4) * size:(int(when * 4) + 1) * size] for when in times}

    def pixel(frame, x, y, width=1920):
        return tuple(frame[(y * width + x) * 3:(y * width + x) * 3 + 3])

    drawn = frames(folder / 'subs.ts', (0.5, 1.5, 2.25, 2.75, 3.5))
    want = [((0, 0, 0), (0, 0, 0)), ((253, 253, 0), (0, 0, 0)), ((253, 253, 0), (25, 50, 160)),
            ((0, 0, 0), (25, 50, 160)), ((0, 0, 0), (0, 0, 0))]
    got = [(pixel(f, 830, 960), pixel(f, 880, 64)) for f in drawn.values()]
    check('drawn by FFmpeg where and when it was written, in its colours (blue half seen through, over black; '
          'within its own rounding)', [[max(abs(a - b) for a, b in zip(g, w)) <= 10 for g, w in zip(pair, wanted)]
                                       for pair, wanted in zip(got, want)], [[True, True]] * 5)

    print('in a video')
    from subtitld.modules import file_io, soft_subtitles, waveform
    session.FFMPEG_EXECUTABLE, session.FFPROBE_EXECUTABLE = ffmpeg, ffprobe
    waveform.FFPROBE_EXECUTABLE = ffprobe
    source = folder / 'source.mkv'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1280x720:rate=25:duration=6',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '6', '-c:v', 'mpeg2video',
                    '-c:a', 'mp2', str(source)], check=True)
    segments = [{'start': 1.0, 'end': 2.0, 'text': 'Hello there'}, {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign'}]
    made = soft_subtitles.write_video(source, folder / 'out.ts', [
        {'segments': segments, 'language': 'en-us'},
        {'segments': [{'start': 2.0, 'end': 3.0, 'text': 'Olá'}], 'language': 'pt-br'}], container='TS DVB',
        width=1280, height=720, ffmpeg=ffmpeg)
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_name', '-of',
                            'default=noprint_wrappers=1:nokey=1', str(made)], capture_output=True, text=True).stdout.split()
    check('the picture and sound copied, beside a DVB subtitle stream for each language', list(dict.fromkeys(probe)),
          ['mpeg2video', 'mp2', 'dvb_subtitle'])
    opened = file_io.process_video_file(str(made))
    found = [t for t in opened['subtitle_tracks'] if t['codec'] == 'dvb_subtitle']
    check('opened: a picture track for each, its language', [(t['kind'], t['language_tag'], t['language'], t['page'])
                                                            for t in found],
          [('pictures', 'eng', 'en-us', None), ('pictures', 'por', 'pt-pt', None)])

    def near(found, want):
        return len(found) == len(want) and all(abs(a - b) <= 0.05 for a, b in zip(found, want))

    pictures = video_subtitles.extract_pictures(str(made), found[0]['index'], 'dvb_subtitle', pid=found[0]['pid'])
    check('read back: at the times of the picture they were written for, the sign at the top, the line at the bottom',
          (near([v for p in pictures for v in (p.start, p.end)], [1.0, 2.0, 3.0, 4.0]),
           [p.y + p.height / 2 < 1080 / 3 for p in pictures], (pictures[0].screen_width, pictures[0].screen_height)),
          (True, [False, True], (1920, 1080)))
    check('with the colours the preview draws in: white letters, a dark outline',
          any(c[3] == 255 and min(c[:3]) > 240 for c in colours_of(pictures[0])) and
          any(c[3] > 128 and max(c[:3]) < 60 for c in colours_of(pictures[0])), True)
    def video_frames(graph):
        out = subprocess.run([ffmpeg, '-v', 'error', '-canvas_size', '1920x1080', '-i', str(made), '-filter_complex',
                              graph, '-f', 'rawvideo', '-'], capture_output=True).stdout
        size = 1280 * 720 * 3
        return [numpy.frombuffer(out[int(when * 25) * size:(int(when * 25) + 1) * size], numpy.uint8).astype(int)
                for when in (0.5, 1.5, 2.5, 3.5, 4.5)]

    plain = video_frames('[0:v]format=rgb24')
    over = video_frames('[0:s:0]scale=1280:720[s];[0:v][s]overlay=eof_action=pass,format=rgb24')
    check('drawn by FFmpeg over the video, at the picture\'s own times, only while each is on',
          [int((numpy.abs(a - b) > 40).sum()) > 500 for a, b in zip(plain, over)], [False, True, False, True, False])
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(made), '-map', '0', '-c', 'copy', str(folder / 'out.mkv')],
                   check=True)
    mkv_probe = json.loads(subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json',
                                           str(folder / 'out.mkv')], capture_output=True, text=True).stdout)
    in_mkv = [t for t in video_subtitles.tracks(mkv_probe) if t['codec'] == 'dvb_subtitle']
    pictures = video_subtitles.extract_pictures(str(folder / 'out.mkv'), in_mkv[1]['index'], 'dvb_subtitle',
                                                ffprobe=ffprobe)
    check('in a Matroska file, read packet by packet through ffprobe', near([v for p in pictures for v in (p.start, p.end)],
                                                                           [2.0, 3.0]), True)

    print('the engine')
    from subtitld.modules.addons.builtin import subtitles_provider
    provider = subtitles_provider.get_provider()
    picture_ocr.ocr_provider = lambda: engine
    engine.calls.clear()
    loop, heard = QEventLoop(), {}
    provider.transcript_finished.connect(lambda found: (heard.setdefault('finished', found), loop.quit()))
    provider.error.connect(lambda message: (heard.setdefault('error', message), loop.quit()))
    provider.transcribe(str(made), 'en-us', {'track': found[1]['index'], 'codec': 'dvb_subtitle', 'pid': found[1]['pid'],
                                             'page': None, 'language': 'pt-br', 'duration': 6})
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    check('"Subtitles in the video" reads the track chosen through the OCR add-on',
          ([(round(s['start'], 1), s['text']) for s in heard.get('finished', [])], engine.calls), ([(2.0, 'Hello')], [(1, 'pt-br')]))

    print('the export path')
    from subtitld.modules import bounce
    session.VIDEO = {'filepath': str(source), 'framerate': 25.0, 'duration': 6.0, 'width': 720, 'height': 576}
    session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Um', 'translations': {'en-us': 'One'}}],
                        'language': 'pt-br'}
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.ts'), 'TS DVB', {'translations': True, 'forced': True})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    services = sorted(s['language'] for listed in dvbsub.streams(folder / 'wired.ts').values() for s in listed)
    check('what the export runs writes the file, a stream for each language, for a standard definition display',
          (said, services, dvbsub.read(folder / 'wired.ts')[0][0].screen_width), ([(True, '')], ['eng', 'por'], 720))

print('the dialog')
from subtitld.interface.export_dialog import _VideoPanel
from subtitld.modules import soft_subtitles
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.mp4'}
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'}
panel = _VideoPanel()
panel.video_tabwidget.setCurrentWidget(panel.tracks_panel)
panel.tracks_format_combo.combobox.setCurrentIndex(panel.tracks_format_combo.combobox.findData('TS DVB'))
panel._refresh_tracks()
check('a transport stream with DVB subtitles: no codec, nor forced, to choose',
      (panel.get_config()['format'], panel.tracks_codec_combo.isHidden(), panel.tracks_forced.isHidden(),
       soft_subtitles.is_soft('TS DVB')), ('TS DVB', True, True, True))
check('its file name', session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS['TS DVB']['extensions'], ['ts'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
