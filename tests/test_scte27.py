"""SCTE 27 (bitmap subtitles in a transport stream): run-length coded
bitmaps built code by code, simple bitmaps framed, outlined and shadowed,
messages in one section and in segments, a damaged section, the display
standards' times, pre_clear_display and immediate messages; the bitmaps
Subtitld codes read back; subtitles drawn by the preview's renderer in one
colour, written into a stream and read back; a video written with them,
its track listed and read; the OCR path and the dialog.

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
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, session
from subtitld.modules.segment import Subtitle
session.set_unsaved = lambda *a, **k: None
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
from subtitld.modules import picture_ocr, scte27, soft_subtitles, teletext, video_subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def bits(text):
    """Bytes from a string of 0s and 1s, padded with 0s."""
    text = text.replace(' ', '')
    text += '0' * (-len(text) % 8)
    return bytes(int(text[at:at + 8], 2) for at in range(0, len(text), 8))


print('the bitmap')
# 10 pixels a line: 3 on and 5 off ("1 011 00101"), 2 on (001 0010), the
# end of the line (00001); then 64 off ("01 000000") over the next lines,
# then 16 on (001 0000).
coded = bits('1 011 00101' '001 0010' '00001' '01 000000' '001 0000')
check('the four codes, a 0 for the longest run, the end of a line',
      scte27.decode_bitmap(coded, 10, 9), bytes([1, 1, 1, 0, 0, 0, 0, 0, 1, 1]) + bytes(10) + bytes(64) + bytes([1] * 6))
random.seed(7)
for width, height in ((37, 11), (300, 4), (5, 5), (100, 1)):
    pixels = bytearray(random.choice((0, 0, 1)) for _ in range(width * height))
    pixels[:width] = b'\x01' * width
    if height > 2:
        pixels[width:2 * width] = bytes(width)
    check(f'what Subtitld codes reads back, {width}x{height}',
          scte27.decode_bitmap(scte27.encode_bitmap(bytes(pixels), width, height), width, height), bytes(pixels))


def color(y, opaque, cr, cb):
    return f'{y:05b}{opaque:b}{cr:05b}{cb:05b}'


WHITE, BLACK = color(29, 1, 16, 16), color(2, 1, 16, 16)
corners = lambda *values: ''.join(f'{v:012b}' for v in values)  # noqa: E731
plain = bits('00000' '0' '00' + WHITE + corners(10, 20, 14, 22) + f'{2:016b}') + bits('001 0100' + '1 011 00001')
check('a simple bitmap: its place, its size, the characters\' colour, nothing else drawn',
      scte27.simple_bitmap(plain)[:5], (10, 20, 4, 2, bytes([1, 1, 1, 1, 1, 1, 1, 0])))
outlined = bits('00000' '0' '01' + WHITE + corners(10, 20, 13, 21) + '0000' '0001' + BLACK + f'{1:016b}') + bits('001 0011' '00001')
x, y, width, height, indices, colours = scte27.simple_bitmap(outlined)
check('an outline of 1 around the characters, the frame grown by it',
      (x, y, width, height, [indices[n * width:(n + 1) * width] for n in range(height)]),
      (9, 19, 5, 3, [bytes([0, 2, 2, 2, 0]), bytes([2, 1, 1, 1, 2]), bytes([0, 2, 2, 2, 0])]))
check('colours: frame, characters (BT.601), outline, shadow', [c[3] for c in colours], [0, 255, 255, 0])
framed = bits('00000' '1' '10' + WHITE + corners(10, 20, 12, 21) + corners(8, 18, 14, 23) + color(4, 0, 16, 16)
              + '0010' '0001' + BLACK + f'{1:016b}') + bits('001 0010' '00001')
x, y, width, height, indices, colours = scte27.simple_bitmap(framed)
check('framed, with a shadow two right and one down: the frame filled, half seen',
      (x, y, width, height, indices[(20 - y) * width + (10 - x):(20 - y) * width + (14 - x)],
       indices[(21 - y) * width + (10 - x):(21 - y) * width + (14 - x)], colours[0][3], colours[3][3]),
      (6, 17, 10, 7, bytes([1, 1, 0, 0]), bytes([0, 0, 3, 3]), 128, 255))


def message(data, standard=3, pts=0, duration=60, pre_clear=True, immediate=False):
    head = scte27.message_bytes('spa', standard, pts, duration, data, pre_clear)
    return head[:3] + bytes((head[3] | (0x40 if immediate else 0),)) + head[4:]


print('messages and sections')
whole = scte27._Messages()
one = scte27.sections_of(message(plain))
check('a message in one section, its CRC-32 checked', (len(one), whole.section(one[0]) == message(plain)), (1, True))
big = message(plain + bytes(9000))
segments = scte27.sections_of(big, ident=5)
got = [whole.section(section) for section in segments]
check('a long one in segments, put back together at the last', (len(segments), got[:-1], got[-1] == big),
      (3, [None, None], True))
damaged = bytearray(one[0])
damaged[10] ^= 0xFF
check('a damaged section is left out', whole.section(bytes(damaged)), None)
pictures = scte27.decode([(message(plain, 3, 90000 * 2, 120), None), (message(outlined, 0, 90000 * 3, 30), None),
                          (message(outlined, 1, 90000 * 10, 25, pre_clear=False), None),
                          (message(plain, 3, 0, 60, immediate=True), 90000 * 20)], origin=0)
check('times: display_in_PTS for the frames of its standard; cleared by the next; immediate at the time before it',
      [(p.start, p.end, p.screen_width, p.screen_height) for p in pictures],
      [(2.0, 3.0, 1920, 1080), (3.0, 4.001, 720, 480), (10.0, 11.0, 720, 576), (20.0, 21.001, 1920, 1080)])

print('writing')
folder = Path(tempfile.mkdtemp())
SEGMENTS = [{'start': 1.0, 'end': 3.0, 'text': 'Hello there'},
            {'start': 4.0, 'end': 6.0, 'text': '{\\an8}At the top'},
            {'start': 6.5, 'end': 60.0, 'text': 'A long one'}]
drawn = scte27.draw_tracks([{'segments': SEGMENTS, 'language': 'en'}])
check('each subtitle drawn and coded, in English', (len(drawn), drawn[0]['language'], len(drawn[0]['blocks'])), (1, 'eng', 3))
(folder / 'subs.ts').write_bytes(scte27.transport_stream(drawn, 3, start=1.4))
read, info = scte27.read(str(folder / 'subs.ts'))
check('read back: the stream, its language, the times (a long one sent again, one subtitle)',
      (info, [(p.start, round(p.end)) for p in read]),
      ({'language': 'eng', 'pid': 0x140}, [(1.0, 3), (4.0, 6), (6.5, 60)]))
check('where they are: the first at the bottom, the second at the top, on 1920x1080',
      (read[0].y > 700, read[1].y < 300, read[0].screen_width, read[0].screen_height), (True, True, 1920, 1080))
shown = numpy.frombuffer(read[0].indices, numpy.uint8)
check('the characters in one colour, outlined in another, as the preview draws them',
      (int((shown == 1).sum()) > 200, int((shown == 2).sum()) > 200, read[0].colours[1][:3] > (200, 200, 200),
       read[0].colours[2][:3] < (60, 60, 60)), (True, True, True, True))
check('the PMT lists the stream with its language', scte27.streams(str(folder / 'subs.ts')), {0x140: 'eng'})
check('the OCR path reads a transport stream without DVB subtitles as SCTE 27',
      [(p.start, round(p.end)) for p in picture_ocr.read_file(str(folder / 'subs.ts'))[0]], [(1.0, 3), (4.0, 6), (6.5, 60)])

print('FFmpeg')
ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed')
else:
    video = folder / 'video.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=25:duration=8',
                    '-f', 'lavfi', '-i', 'sine=duration=8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-shortest', str(video)], check=True)
    output = folder / 'out.ts'
    soft_subtitles.write_video(str(video), str(output), [{'segments': SEGMENTS[:2], 'language': 'es'}],
                               container='TS SCTE27', width=640, height=360)
    probe = json.loads(subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)],
                                      capture_output=True).stdout)
    check('FFmpeg still reads the picture and the sound, and lists the stream it does not know',
          sorted((s.get('codec_name', ''), s['codec_tag']) for s in probe['streams']),
          [('', '0x0082'), ('aac', '0x000f'), ('h264', '0x001b')])
    tracks = video_subtitles.tracks(probe)
    check('the video lists an SCTE 27 picture track, with its PID, read through an OCR add-on',
          [(t['codec'], t['kind'], t.get('pid')) for t in tracks] + ['scte_27' in video_subtitles.READABLE_PICTURE_CODECS],
          [('scte_27', 'pictures', 0x140), True])
    pictures = video_subtitles.extract_pictures(str(output), tracks[0]['index'], 'scte_27', pid=tracks[0]['pid'])
    check("its pictures at the video's times", [(round(p.start, 1), round(p.end, 1)) for p in pictures],
          [(1.0, 3.0), (4.0, 6.0)])
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-map', '0:v', '-f', 'null', '-'],
                             capture_output=True)
    check('and the video, sections laid in among its packets, plays through without a complaint',
          (decoded.returncode, decoded.stderr.decode().strip()), (0, ''))

print('the dialog')
from subtitld.interface import export_dialog
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.ts'}
session.SUBTITLE = Subtitle({'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'})
panel = export_dialog._VideoPanel()
panel.choose('TS SCTE27')
check('a transport stream\'s track as SCTE 27, with no forced track',
      ('TS SCTE27' in soft_subtitles.CONTAINERS, panel.get_config()['format'], panel.tracks_forced.isHidden(),
       panel.tracks_ts_combo.combobox.currentText()), (True, 'TS SCTE27', True, 'SCTE 27 subtitles (cable), drawn as pictures'))
check('a transport stream is still a video', teletext.is_transport_stream(str(folder / 'subs.ts')), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
