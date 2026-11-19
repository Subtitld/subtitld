"""ARIB STD-B24 captions: decoding caption management and statement data
built byte by byte (the 8-unit code's sets and shifts, kanji, kana,
alphanumerics in middle size, ARIB's additional symbols, the macros,
colours, rubies, TIME, DRCS, the Latin sets of ABNT NBR 15606-1, UTF-8),
the transport stream Subtitld writes (as FFmpeg probes it, and read back
in each coding), a video written with captions and listed among its
tracks, and the export dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import aribb24, aribb24_tables, soft_subtitles, teletext, video_subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def pes_payload(group):
    """A PES packet's data: data_identifier, private_stream_id, header length 0."""
    return b'\x80\xff\xf0' + group


def management(languages=(('jpn', 0),), group=0x00, swf=7):
    body = bytes((0x3F, len(languages)))
    for number, (code, tcs) in enumerate(languages):
        body += bytes((number << 5 | 0x0F,)) + code.encode() + bytes((((swf + 1) << 4) | (tcs << 2),))
    body += b'\x00\x00\x00'
    return pes_payload(aribb24._data_group(group, body))


def statement(*units, number=1):
    data = b''.join(aribb24._unit(parameter, data) for parameter, data in units)
    return pes_payload(aribb24._data_group(number, b'\x3f' + len(data).to_bytes(3, 'big') + data))


def kanji(text):
    """JIS X 0208 codes, as GL bytes."""
    return b''.join(bytes((byte & 0x7F for byte in char.encode('euc_jis_2004'))) for char in text)


APS = lambda row, column: bytes((0x1C, 0x40 | row, 0x40 | column))  # noqa: E731

print('the 8-unit code')
# Profile A's sets at the start: G0 kanji, G1 alphanumerics, G2 hiragana
# (in GR), G3 the macros.
body = (b'\x0c' + APS(7, 4) + kanji('今日') + b'\xcf' + b'\x0e\x89ABC\x0f\x8a'
        + b'\x81' + kanji('赤') + b'\x87' + b'\x9a' + kanji('下線') + b'\x99' + b'\x0d'
        + kanji('二行目') + b'\x9b\x32\x20\x64' + kanji('斜') + b'\x9b\x30\x20\x64'
        + bytes((0x21 + 89, 0x21 + 0)) + b'\x9d\x20\x65')   # ARIB's row 90, the TIME of 3.7 s
packets = [(90000, management()), (90000 * 2, statement((0x20, body))), (90000 * 4, management())]
segments, language = aribb24.decode(packets, origin=0)
check('kanji, hiragana in GR, alphanumerics in middle size, a colour, underline, a new line, italic, '
      'an additional symbol; the TIME ending it; at the bottom left',
      (segments, language),
      ([{'start': 2.0, 'end': 5.7, 'text': '{\\an1}今日は' + 'ABC' + '<font color="#ff0000">赤</font><u>下線</u>\n二行目<i>斜</i>'
         + aribb24_tables.ADDITIONAL[5 * 94]}], 'jpn'))
check("ARIB's additional symbols: row 90 starts with U+26CC", aribb24_tables.ADDITIONAL[5 * 94], '\u26cc')

# The next statement ends the one before; a statement that only clears
# the screen ends it too; the last one gets TAIL.
packets = [(0, management()),
           (90000, statement((0x20, b'\x0c' + APS(1, 0) + kanji('上')))),
           (90000 * 2, statement((0x20, b'\x0c' + APS(4, 11) + kanji('中央')))),
           (90000 * 3, statement((0x20, b'\x0c'))),
           (90000 * 4, statement((0x20, b'\x0c' + APS(9, 20) + kanji('右下')))),
           (90000 * 5, statement((0x20, b'\x0c' + APS(9, 0) + kanji('二')), number=2))]
check("each until the next or a clear screen, the last TAIL; places from the rows; another language's skipped",
      [(s['start'], s['end'], s['text']) for s in aribb24.decode(packets, origin=0)[0]],
      [(1.0, 2.0, '{\\an7}上'), (2.0, 3.0, '{\\an5}中央'), (4.0, 9.0, '{\\an3}右下')])
check('a management data group sent again in the same group is not read again',
      aribb24.decode([(0, management((('jpn', 0),))), (1, management((('eng', 1),), group=0x00)),
                      (2, statement((0x20, b'\x0c' + b'ab')))], origin=0)[1], 'jpn')

# Rubies: small characters on the row above stand over their base.
ruby = (b'\x0c' + b'\x9b960;540\x20\x56'[:0] + b'\x9b100;100\x20\x61' + b'\x88' + bytes((0xAB, 0xF3, 0xB8)) + b'\x8a'
        + b'\x9b100;160\x20\x61' + kanji('漢字') + kanji('は') + b'\x9b500;100\x20\x61' + b'\x88\xca\xa4' + b'\x8a')
check('the reading in small size above its base is its ruby; small text over nothing is small text',
      aribb24.decode([(0, management()), (0, statement((0x20, ruby)))], origin=0)[0][0]['text'],
      '{\\an7}<font size="50%">ない</font><ruby>漢字<rt>かんじ</rt></ruby>は')
check('the macros: SS3 and 0x60 designates the sets anew (katakana in G1 by macro 0x61)',
      aribb24.decode([(0, management()), (0, statement((0x20, b'\x0c' + b'\x1d\x61' + b'\x0e\x2b\x2b\x0f' + kanji('字'))))],
                     origin=0)[0][0]['text'], '{\\an7}カカ字')
pattern = bytes(range(16 * 18 // 8))                  # 16x18, two shades: a bit a pixel
aribb24_tables.DRCS[hashlib.md5(pattern).hexdigest()] = 0x2665
drcs = b'\x02' + b'\x41\x21\x01' + b'\x00\x00\x10\x12' + pattern + b'\x41\x22\x01' + b'\x00\x00\x10\x12' + pattern[::-1]
check('DRCS: a pattern libaribcaption knows (by its MD5) is its character, one it does not the geta mark',
      aribb24.decode([(0, management()), (0, statement((0x30, drcs), (0x20, b'\x0c\x1b\x28\x20\x41\x21\x22')))],
                     origin=0)[0][0]['text'], '{\\an7}\u2665\u3013')

print('Latin and UTF-8')
latin = b'\x0c' + APS(9, 2) + b'Ol' + bytes((0xA1 + 0x40,)) + b' a' + bytes((0xA1 + 0x46,)) + b'\x1d\x21'
check("Portuguese (ABNT NBR 15606-1): the Latin extension in GR, the special set by SS3, spaces half wide",
      aribb24.decode([(0, management((('por', 0),))), (0, statement((0x20, latin)))], origin=0)[0][0]['text'],
      '{\\an1}Olá aç♪')
utf8 = b'\x0c' + APS(9, 2) + 'Hello '.encode() + b'\xc2\x81' + 'wörld'.encode()
check('UTF-8 (TCS 1): the text as it is, C1 codes as U+0080 to U+009F',
      aribb24.decode([(0, management((('eng', 1),))), (0, statement((0x20, utf8)))], origin=0)[0][0]['text'],
      '{\\an1}Hello <font color="#ff0000">wörld</font>')

print('writing')
SEGMENTS = [
    {'start': 1.0, 'end': 3.0, 'text': '<ruby>漢字<rt>かんじ</rt></ruby>を<font color="#ffff00">読む</font> ABC'},
    {'start': 3.0, 'end': 5.0, 'text': '{\\an8}二行目\n<i>テスト</i><b>①</b>'},
    {'start': 6.0, 'end': 7.5, 'text': '{\\an3}<u>右下</u>'},
    {'start': 8.0, 'end': 9.0, 'text': '{\\an7}<blink>左上</blink> ~'},
]
folder = Path(tempfile.mkdtemp())
(folder / 'ja.ts').write_bytes(aribb24.transport_stream(SEGMENTS))
check('read back as written, in the 8-unit code (a tilde as the fullwidth one, which the set has)',
      aribb24.read(str(folder / 'ja.ts')),
      (SEGMENTS[:3] + [dict(SEGMENTS[3], text='{\\an7}<blink>左上</blink> \uff5e')], {'language': 'jpn', 'pid': 0x130}))
check('the PMT as FFmpeg finds a profile A caption stream', aribb24.streams(str(folder / 'ja.ts')),
      {0x130: {'component_tag': 0x30, 'data_component_id': 0x0008}})
TRIP = [{'start': 0.5, 'end': 2.0, 'text': 'Olá, <b>ação</b> ♪ <i>ü</i>'}, {'start': 2.0, 'end': 3.0, 'text': '{\\an8}Topo'}]
(folder / 'pt.ts').write_bytes(aribb24.transport_stream(TRIP, 'por', 'pt-br'))
check('Portuguese in the Latin sets, read back', aribb24.read(str(folder / 'pt.ts')), (TRIP, {'language': 'por', 'pid': 0x130}))
(folder / 'en.ts').write_bytes(aribb24.transport_stream(TRIP + [{'start': 4, 'end': 5, 'text': '<ruby>東<rt>ひがし</rt></ruby>'}],
                                                         'eng', 'en'))
check('English in UTF-8, read back; a ruby in brackets, which only the 8-unit code can make',
      aribb24.read(str(folder / 'en.ts'))[0], TRIP + [{'start': 4.0, 'end': 5.0, 'text': '東(ひがし)'}])
check('a data group\'s CRC-16 (ITU-T from 0): "123456789"', aribb24.crc16(b'123456789'), 0x31C3)
good = statement((0x20, b'\x0c' + APS(1, 1) + kanji('左')))
damaged = good[:-4] + bytes((good[-4] ^ 0x01,)) + good[-3:]
check('a data group whose CRC-16 holds read; one damaged on the way left out; one cut short of its CRC taken',
      (aribb24.data_group(good) is not None, aribb24.data_group(damaged), aribb24.data_group(good[:-2]) is not None),
      (True, None, True))
before = statement((0x20, b'\x0c' + APS(1, 1) + kanji('上')))
check('the damaged statement shows nothing; the one before it stays on',
      [(s['start'], s['text']) for s in aribb24.decode([(0, management()), (90000, before), (180000, damaged)],
                                                       origin=0)[0]],
      [(1.0, '{\\an7}上')])

print('FFmpeg')
ffprobe = shutil.which('ffprobe')
ffmpeg = shutil.which('ffmpeg')
if not (ffprobe and ffmpeg):
    print('  [SKIP] FFmpeg is not installed')
else:
    probe = json.loads(subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(folder / 'ja.ts')],
                                      capture_output=True).stdout)
    check('ffprobe: an arib_caption stream, profile A', [(s['codec_name'], s.get('profile')) for s in probe['streams']],
          [('arib_caption', 'Profile A')])
    video = folder / 'video.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=10',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
    output = folder / 'captioned.ts'
    soft_subtitles.write_video(str(video), str(output), [{'segments': SEGMENTS, 'language': 'ja'}], container='TS ARIB')
    probe = json.loads(subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)],
                                      capture_output=True).stdout)
    listed = video_subtitles.tracks(probe)
    check('the video written with them lists a text track of ARIB captions, with its PID',
          [(t['codec'], t['kind'], t.get('pid') is not None) for t in listed], [('arib_caption', 'text', True)])
    read = video_subtitles.read_arib(str(output), listed[0]['index'], pid=listed[0]['pid'])
    check("read back from the video at the video's times", [(s['start'], s['text']) for s in read],
          [(s['start'], s['text']) for s in SEGMENTS[:3]] + [(8.0, '{\\an7}<blink>左上</blink> \uff5e')])
    mkv = folder / 'captioned.mkv'
    copied = subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(output), '-map', '0', '-c', 'copy', str(mkv)],
                            capture_output=True)
    if copied.returncode != 0:
        print('  [SKIP] this FFmpeg does not copy ARIB captions into Matroska')
    else:
        probe = json.loads(subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(mkv)],
                                          capture_output=True).stdout)
        index = video_subtitles.tracks(probe)[0]['index']
        check('from a Matroska copy, packet by packet through ffprobe',
              [s['text'] for s in video_subtitles.read_arib(str(mkv), index)], [s['text'] for s in read])

print('the dialog')
from subtitld.interface import export_dialog
from subtitld.modules import session
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.ts'}
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'}
panel = export_dialog._VideoPanel()
panel.choose('TS ARIB')
check('a transport stream\'s track as ARIB captions, with no forced track',
      ('TS ARIB' in soft_subtitles.CONTAINERS, panel.get_config()['format'], panel.tracks_forced.isHidden(),
       panel.tracks_ts_combo.combobox.currentText()), (True, 'TS ARIB', True, 'ARIB captions (ISDB)'))
check('a transport stream is still a video', teletext.is_transport_stream(str(folder / 'ja.ts')), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
