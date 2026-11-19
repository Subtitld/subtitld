"""Real picture subtitle files, made by other tools, read as Subtitld reads
them for OCR: FFmpeg's VobSub and PGS samples, Subtitle Edit's Blu-ray .sup
of two objects, its three odd VobSub streams (no .idx), its XSUB in an AVI
and its SMPTE DCP timed-text MXF made by asdcplib (with its XML).

Each is checked against what another tool says of it: FFmpeg's FATE
references (tests/ref/fate/sub2video: the sample VobSub's subtitles, their
times and durations; sub-pgs-remux: the PGS's segments), what FFmpeg 8.0
reads where no reference file has it, and Subtitle Edit's tests (at
80b7c034) where they state counts, times and sizes. The words are read by a
stand-in OCR engine (the file name of each picture back), as in
test_picture_subtitles. Then the pictures are written back as VobSub and
PGS and read again.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. It skips (passing) when the corpus has
not been fetched (tests/corpus/fetch.py).
"""
import os, shutil, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy
from subtitld.modules import file_io, pgs, picture_ocr, picture_subtitles, video_subtitles, vobsub
from subtitld.modules.addons.provider import OCRProvider
from corpus_common import need

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


(VOB_IDX, VOB_SUB, PGS_SUP, BDSUP, VOD_LENGTH, VOD_STUFFING, VOD_WINDOW, XSUB_AVI, MXF, MXF_XML,
 REF_SUB2VIDEO, REF_PGS) = need(
    'fate/sub/vobsub.idx', 'fate/sub/vobsub.sub', 'fate/sub/pgs_sub.sup',
    'subtitleedit/sample_BDSUP_multi_image.sup', 'subtitleedit/sample_VodSub_nonStandardLength.sub',
    'subtitleedit/sample_VodSub_stuffingBytes.sub', 'subtitleedit/sample_VodSub_windowStuffingWithoutPaddingStream.sub',
    'subtitleedit/sample_XSUB.avi', 'subtitleedit/sample_mxf_429-5_asdcplib.mxf',
    'subtitleedit/sample_mxf_429-5_asdcplib.xml', 'fate/ref/sub2video', 'fate/ref/sub-pgs-remux')
folder = Path(tempfile.mkdtemp())
FFPROBE = shutil.which('ffprobe')


def rgba(picture):
    """The picture's pixels as RGBA, what does not show as (0, 0, 0, 0)."""
    table = numpy.array([tuple(c) if c[3] else (0, 0, 0, 0) for c in picture.colours] +
                        [(0, 0, 0, 0)] * (256 - len(picture.colours)), numpy.uint8)
    return table[numpy.frombuffer(picture.indices, numpy.uint8)]


def as_drawn(pictures):
    """The pictures as disc_pictures' dicts, what vobsub.save and pgs.save write."""
    return [{'start': p.start, 'end': p.end, 'x': p.x, 'y': p.y, 'width': p.width, 'height': p.height,
             'rgba': rgba(p).tobytes(), 'forced': p.forced} for p in pictures]


def same_pixels(first, second, tolerance=0):
    """Whether two lists of pictures show the same thing in the same place:
    each cropped to what shows, its colours within `tolerance`, its
    opacity exact."""
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        a, b = picture_subtitles.crop(a), picture_subtitles.crop(b)
        if (a.x, a.y, a.width, a.height) != (b.x, b.y, b.width, b.height):
            return False
        pa, pb = rgba(a).astype(int), rgba(b).astype(int)
        if (pa[:, 3] != pb[:, 3]).any() or abs(pa[:, :3] - pb[:, :3]).max() > tolerance:
            return False
    return True


def drawn_per_row(picture):
    shows = numpy.array([c[3] > 0 for c in picture.colours] + [False] * (256 - len(picture.colours)))
    return shows[numpy.frombuffer(picture.indices, numpy.uint8)].reshape(picture.height, picture.width).sum(1).tolist()


class FakeOCR(OCRProvider):
    """Reads a picture's file name back ("text 3"), sure of it."""

    def __init__(self):
        super().__init__()
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append(([os.path.basename(p) for p in image_paths], language))
        texts = [f'text {int(os.path.basename(p).split(".")[0].split("-")[-1])}' for p in image_paths]
        QTimer.singleShot(0, lambda: self.finished.emit(texts, [0.95] * len(texts)))


def ocr(path):
    """(engine, segments, info) of an OCR job on the file at `path`."""
    engine = FakeOCR()
    job = picture_ocr.OCRJob.for_file(str(path), engine)
    loop = QEventLoop()
    out = {}
    job.finished.connect(lambda segments, info: (out.update(segments=segments, info=info), loop.quit()))
    job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
    job.start()
    QTimer.singleShot(60000, loop.quit)
    loop.exec()
    return engine, out.get('segments'), out.get('info')


def opened_as(path):
    try:
        file_io.process_subtitles_file(str(path))
        return None
    except file_io.PictureSubtitlesError as error:
        return error


# FFmpeg's sub2video test decodes the sample from 132 s on (-ss 132): its
# subtitle packets ("1, pts, pts, duration") are every subtitle, their
# start less 132 s and their duration, in microseconds, whole milliseconds.
REFERENCE = []
for line in REF_SUB2VIDEO.read_text().splitlines():
    if line.startswith('1,'):
        fields = [field.strip() for field in line.split(',')]
        REFERENCE.append((132000 + int(fields[2]) // 1000, int(fields[3]) // 1000))

print("FFmpeg's VobSub sample (vobsub.idx + .sub, MPlayer's)")
error = opened_as(VOB_IDX)
check('opened: pictures, for the OCR dialog', (error.path, error.kind) if error else None, (str(VOB_IDX), 'vobsub'))
pictures, track = vobsub.read(str(VOB_IDX))
check('the track the .idx names (id: en, index: 0)', (track['index'], track['language']), (0, 'en'))
check('as many subtitles as FFmpeg decodes', len(pictures), len(REFERENCE))
check('which is 43', len(REFERENCE), 43)
# FFmpeg's decoder gives a subtitle's duration in whole milliseconds, cut
# down; the duration here is the SPU's own, in ticks of 1024/90000 s.
got = [(round(p.start * 1000), int((p.end - p.start) * 1000 + 1e-6)) for p in pictures]
# Three SPUs run on into a second pack that carries a PTS of its own (the
# .idx lists both packs). FFmpeg times each by the pack that finishes it;
# here by its first, as its .idx entry and its PES do: 185.919, not 185.910.
check('their times and durations, as FFmpeg has them, but for the three FFmpeg times by their last pack',
      [g for g, r in zip(got, REFERENCE) if g != r], [(185919, 2696), (302018, 1524), (328369, 1524)])
check('which FFmpeg has 9, 17 and 8 ms earlier', [r for g, r in zip(got, REFERENCE) if g != r],
      [(185910, 2696), (302035, 1524), (328361, 1524)])
check('the frame the .idx gives (size: 720x480)', {(p.screen_width, p.screen_height) for p in pictures}, {(720, 480)})
# The .idx palette: entry 6 is ebff0b, the yellow the words are in (the
# picture reads "CITY OF MEN", in yellow, outlined dark: entry 2, 101010).
first = picture_subtitles.crop(pictures[0])
check('the first in the yellow of palette entry 6, outlined in entry 2, the rest clear',
      sorted({tuple(c) for c in rgba(first).tolist()}), [(0, 0, 0, 0), (16, 16, 16, 255), (235, 255, 11, 255)])
# Each is drawn on the whole frame (720x478 from row 2); what shows is low
# on it, but for the second, a caption of the place at the top ("Dead End
# Hill / Rio de Janeiro").
check('all low on the frame but the second', [n for n, p in enumerate(pictures) if picture_subtitles.crop(p).y < 240], [1])
engine, segments, info = ocr(VOB_IDX)
check('read by OCR: every picture once, in English', (len(engine.calls[0][0]), engine.calls[0][1]), (43, 'en'))
check('a subtitle each, timed as the pictures', [(s['start'], s['end']) for s in segments],
      [(round(p.start, 3), round(p.end, 3)) for p in pictures])
check('the place caption at the top', [n for n, s in enumerate(segments) if s['text'].startswith('{\\an8}')], [1])

print("Subtitle Edit's odd VobSub streams (a .sub alone)")
# No .idx: timed by their PTS. FFmpeg 8.0 reads them (ffprobe -f mpeg) as
# 2, 1 and 2 subtitles at these times, each 329 ms long.
FFMPEG_TIMES = {VOD_LENGTH: [(2055219, 329), (2055553, 329)], VOD_STUFFING: [(2055219, 329)],
                VOD_WINDOW: [(5130, 329), (5463, 329)]}
odd = {}
for path, times in FFMPEG_TIMES.items():
    error = opened_as(path)
    check(f'{path.name}: pictures, for the OCR dialog', error.kind if error else None, 'vobsub')
    odd[path], _track = vobsub.read(str(path))
    check(f'{path.name}: its subtitles, as FFmpeg times them',
          [(round(p.start * 1000), int((p.end - p.start) * 1000 + 1e-6)) for p in odd[path]], times)
# nonStandardLength's and stuffingBytes' first SPU is the same one, packed
# differently (a pack header with a stuffing byte): the same picture.
check('the same picture through either packing',
      same_pixels(odd[VOD_LENGTH][:1], odd[VOD_STUFFING]), True)
check('a subtitle shown twice is one picture each time', same_pixels(odd[VOD_LENGTH][:1], odd[VOD_LENGTH][1:]), True)
# The pictures are a Blu-ray's, rewrapped (Japanese words in white, outlined
# black, centred on a 1920x1080 frame; windowStuffing's in two lines).
check('their place: centred on a 1920-wide frame, low on a 1080-high one',
      [(p.x + p.width // 2, p.y + p.height <= 1080, p.y > 540) for p in odd[VOD_LENGTH] + odd[VOD_WINDOW]],
      [(959, True, True)] * 2 + [(974, True, True)] * 2)
check('white words, outlined black', sorted({tuple(c) for c in rgba(odd[VOD_WINDOW][0]).tolist()}),
      [(0, 0, 0, 0), (0, 0, 0, 255), (255, 255, 255, 255)])

print("FFmpeg's PGS sample (pgs_sub.sup)")
# FATE's sub-pgs-remux lists the segments: the compositions (30 bytes, a
# PCS of two objects) at PTS 6072 and 36102 of 90 kHz. The second is the
# last display set, the file cut short before its END: Subtitle Edit reads
# such a one, FFmpeg (ffprobe) leaves it out.
compositions = [int(line.split(',')[2]) for line in REF_PGS.read_text().splitlines()
                if line.startswith('0,') and int(line.split(',')[4]) == 30]
check('the reference: two compositions', compositions, [6072, 36102])
error = opened_as(PGS_SUP)
check('opened: pictures, for the OCR dialog', error.kind if error else None, 'pgs')
shown = pgs.read(str(PGS_SUP))
check('a picture from each composition, until the next', [(round(p.start * 90000), round(p.end * 90000)) for p in shown],
      [(6072, 36102), (36102, 36102 + 5 * 90000)])
# Its two objects ("♪～" and the line, ffprobe: 2 rects) are drawn into one
# picture, as big as the two windows of its WDS: 383,956 1156x67 and
# 100,958 123x63.
check('two objects, one picture over both windows', [(p.x, p.y, p.width, p.height) for p in shown],
      [(100, 956, 1439, 67)] * 2)
check('the second the first again (an acquisition point, the other object kept)', same_pixels(shown[:1], shown[1:]), True)
check('on a 1920x1080 frame (its PCS)', {(p.screen_width, p.screen_height) for p in shown}, {(1920, 1080)})
check('white letters (BT.709), outlined black', (shown[0].colours[0], shown[0].colours[1], max(shown[0].colours)[:3]),
      ((0, 0, 0, 0), (0, 0, 0, 255), (255, 255, 255)))

print("Subtitle Edit's Blu-ray .sup of two objects (sample_BDSUP_multi_image.sup)")
# Two display sets: at 679.846 two objects, a sign at the top (571,81
# 777x45) and the line at the bottom (568,740 784x252); at 682.724 the
# bottom one alone, the file cut short before its END. Subtitle Edit's
# tests have more than one subtitle in it (TransportStreamReadingTest).
error = opened_as(BDSUP)
check('opened: pictures, for the OCR dialog', error.kind if error else None, 'pgs')
multi = pgs.read(str(BDSUP))
check('two pictures, the first until the second', [(round(p.start, 3), round(p.end, 3)) for p in multi],
      [(679.846, 682.724), (682.724, 687.724)])
check('the first both objects, the second the bottom one',
      [(p.x, p.y, p.width, p.height) for p in multi], [(568, 81, 784, 911), (568, 740, 784, 252)])
check('the bottom object drawn the same in both', numpy.array_equal(
    rgba(multi[0]).reshape(911, 784, 4)[740 - 81:], rgba(multi[1]).reshape(252, 784, 4)), True)
check('white letters', multi[0].colours[1], (255, 255, 255, 255))
engine, segments, info = ocr(BDSUP)
check('read by OCR: two pictures', [s['start'] for s in segments], [679.846, 682.724])

print("Subtitle Edit's XSUB (sample_XSUB.avi)")
if FFPROBE:
    import json, subprocess
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', str(XSUB_AVI)],
                                      capture_output=True, text=True).stdout)
    found = video_subtitles.tracks(probe)
    check('one picture track, stream 1, English (its DivX name)',
          [(t['index'], t['codec'], t['kind'], t['language_tag']) for t in found], [(1, 'xsub', 'pictures', 'en')])
    xsubs = video_subtitles.extract_pictures(str(XSUB_AVI), 1, 'xsub')
    # Subtitle Edit's XSubParserTest: three, at 1.000-2.991, 4.000 and
    # 7.000; a 640x480 frame; each left of the edge and inside the frame;
    # the first 246x42, these many pixels drawn on each of its rows.
    check('three subtitles, as Subtitle Edit times them', [p.start for p in xsubs], [1.0, 4.0, 7.0])
    check('the first until 2.991', xsubs[0].end, 2.991)
    check('on a 640x480 frame', {(p.screen_width, p.screen_height) for p in xsubs}, {(640, 480)})
    check('inside it, off its left edge', [p.x > 0 and p.y + p.height <= 480 for p in xsubs], [True] * 3)
    check('the first 246x42', (xsubs[0].width, xsubs[0].height), (246, 42))
    check('drawn row by row as Subtitle Edit has it (both fields, interlaced)', drawn_per_row(xsubs[0]), [
        44, 57, 62, 63, 63, 63, 63, 63, 63, 63, 145, 169, 178, 188, 195, 212, 219, 221, 226, 212, 204,
        202, 203, 201, 198, 184, 184, 184, 185, 186, 179, 182, 188, 202, 196, 193, 186, 180, 174, 163, 149, 119])
    check('every one draws something', [any(drawn_per_row(p)) for p in xsubs], [True] * 3)
    # Subtitle Edit's XSubInputTest: the last 7.000-8.991; its .sup of the
    # three has each below the middle of the frame.
    check('the last until 8.991', xsubs[2].end, 8.991)
    check('below the middle', [p.y > 240 for p in xsubs], [True] * 3)
else:
    print('  (no ffprobe: the XSUB track is not read)')

print("Subtitle Edit's DCP MXF made by asdcplib (sample_mxf_429-5_asdcplib.mxf + .xml)")
# The XML asdcplib wrapped: SMPTE 428-7 at 25 fps, StartTime 01:00:00:00,
# Danish; subtitle 1 text ("Hej verden", 01:00:01:00-01:00:03:00), subtitle
# 2 an <Image> (01:00:04:00-01:00:06:12, bottom, Vposition 8), whose PNG
# the MXF carries: Subtitle Edit's 10x4 test PNG (TimedTextMxfWriterTest).
# So it does hold a picture, and opens for OCR, its text kept as words.
error = opened_as(MXF)
check('the MXF: pictures, its reel and PNG taken out for OCR', (error.kind, Path(error.path).suffix) if error else None,
      ('dcp', '.xml'))
if error:
    pictures, language = picture_ocr.read_file(error.path)
    check('the text one as its words, the picture to read', [(p.start, p.end, p.text) for p in pictures],
          [(1.0, 3.0, 'Hej verden'), (4.0, 6.48, '')])
    check('the picture: 10x4, centred, its foot 8% above the bottom of 1920x1080',
          [(p.x, p.y, p.width, p.height) for p in pictures[1:]], [(955, 990, 10, 4)])
    check('in Danish', language[:2], 'da')
    engine, segments, info = ocr(error.path)
    check('read by OCR: only the picture', [len(call[0]) for call in engine.calls], [1])
    check('two subtitles', [(s['start'], s['end'], s['text']) for s in segments],
          [(1.0, 3.0, 'Hej verden'), (4.0, 6.48, 'text 1')])
error = opened_as(MXF_XML)
check('the XML alone: pictures as well', error.kind if error else None, 'dcp')
pictures, language = picture_ocr.read_file(str(MXF_XML))
check('but its PNG is not beside it (it is in the MXF): the text one alone',
      [(p.start, p.end, p.text) for p in pictures], [(1.0, 3.0, 'Hej verden')])

print('written back and read again')
size = (720, 480)
vob = vobsub.read(str(VOB_IDX))[0]
idx, _sub = vobsub.save(str(folder / 'fate.idx'), as_drawn(vob), size, 'en')
again, track = vobsub.read(idx)
check('VobSub: as many', len(again), len(vob))
check('the same starts (an .idx counts milliseconds)', [round(p.start, 3) for p in again], [round(p.start, 3) for p in vob])
check('the same ends, within an SPU tick', max(abs(a.end - b.end) for a, b in zip(again, vob)) <= 1024 / 90000, True)
check('the same pixels in the same places, the same colours', same_pixels(vob, again), True)
# The odd ones are on a 1920x1080 frame, though without an .idx they are
# taken for DVD PAL's: written on that, they would be cut away.
hd = odd[VOD_LENGTH] + odd[VOD_WINDOW]
hd.sort(key=lambda p: p.start)
vobsub.save(str(folder / 'hd.idx'), as_drawn(hd), (1920, 1080), 'ja')
again, _track = vobsub.read(str(folder / 'hd.idx'))
check('a .sub alone, on its HD frame: the same pixels', same_pixels(hd, again), True)
check('and times', [round(p.start, 3) for p in again], [round(p.start, 3) for p in hd])
for name, source in (('pgs_sub', shown), ('multi_image', multi)):
    pgs.save(str(folder / f'{name}.sup'), as_drawn(source), (1920, 1080), 23.976)
    again = pgs.read(str(folder / f'{name}.sup'))
    check(f'PGS {name}: the same times', [(round(p.start, 3), round(p.end, 3)) for p in again],
          [(round(p.start, 3), round(p.end, 3)) for p in source])
    # Through YCbCr and back: a colour may come back a step or two off.
    check(f'PGS {name}: the same pixels, colours within 2', same_pixels(source, again, tolerance=2), True)

print()
if fails:
    print(f'{len(fails)} FAILED: {fails}')
    sys.exit(1)
print('all passed')
