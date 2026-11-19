"""Blu-ray TextST: decoding hand-built dialog style and presentation
segments as libbluray reads them (region styles, inline font styles, line
breaks, two regions, forced, the PTS), and a hand-built .m2ts holding a
TextST stream: listed by ffprobe as a video's text track, read packet by
packet, and read through "Subtitles in the video". Then Subtitld's own
TextST written (textst.save, the export's 'TEXTST'): its segments laid out
as libbluray's textst_decode.c and graphics_processor.c read them, and the
.m2ts read by FFmpeg as a TextST stream and back.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, export_options, file_io, session, teletext, textst, video_subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('segments')
# Region 1 at the bottom in a plain font, region 2 at the top in italics.
STYLE = textst.style_segment([(1, 100, 900, 1720, 150, 0), (2, 100, 60, 1720, 150, 2)], 4)
DIALOGS = [
    textst.dialog_segment(1.0, 3.5, [(1, False, ['First line', '\n', 'and ', ('style', False, True), 'the second',
                                                'reset', '.'])]),
    textst.dialog_segment(4.0, 5.0, [(2, True, ['A sign'])]),
    textst.dialog_segment(6.0, 7.0, [(1, False, [('style', True, False), 'Bold', '\n', 'Ação'])]),
    textst.dialog_segment(8.0, 9.0, [(1, False, ['Two']), (1, False, ['regions'])]),
]
styles, count = textst.parse_style(STYLE[3:])
check('the dialog style: each region\'s place and font style, and how many dialogs follow',
      (styles, count), ({1: {'bold': False, 'italic': False, 'y': 900, 'height': 150},
                         2: {'bold': False, 'italic': True, 'y': 60, 'height': 150}}, 4))
dialog = textst.parse_dialog(DIALOGS[0][3:])
check('a dialog: its PTS, its region, the strings, a line break, a style and its reset',
      (dialog['start'], dialog['end'], dialog['regions'][0]['style'], dialog['regions'][0]['runs']),
      (90000, 315000, 1, [('First line', None, None), '\n', ('and ', None, None), ('the second', False, True),
                          ('.', None, None)]))
found = textst.decode([STYLE] + DIALOGS)
check('decoded: the styles as markup, the upper region {\\an8} and forced, two regions one under the other',
      found, [{'start': 1.0, 'end': 3.5, 'text': 'First line\nand <i>the second</i>.'},
              {'start': 4.0, 'end': 5.0, 'text': '{\\an8}<i>A sign</i>', 'forced': True},
              {'start': 6.0, 'end': 7.0, 'text': '<b>Bold</b>\n<b>Ação</b>'},
              {'start': 8.0, 'end': 9.0, 'text': 'Two\nregions'}])
check('segments back to back in one packet read alike', textst.decode([STYLE + b''.join(DIALOGS)]), found)
check('times count from the file\'s start, the PTS wrapping round',
      [s['start'] for s in textst.decode([STYLE, DIALOGS[0]], origin=(1 << 33) - 90000)], [2.0])
check('a dialog cut short is left out, the rest read',
      [s['start'] for s in textst.decode([STYLE, DIALOGS[0][:12], DIALOGS[1]])], [4.0])


def m2ts(payloads):
    """A Blu-ray transport stream of 192-byte packets: a PAT, a PMT with
    the HDMV registration and a TextST stream (type 0x92, PID 0x1800), and
    a private_stream_2 PES for each payload."""
    counters = {}
    pat = teletext._section(0x00, 1, bytes((0x00, 0x01, 0xE1, 0x00)))
    program = bytes((0x05, 4)) + b'HDMV'
    body = bytes((0xF8, 0x00, 0xF0 | len(program) >> 8, len(program) & 0xFF)) + program
    body += bytes((0x92, 0xF8, 0x00, 0xF0, 0x00))
    pmt = teletext._section(0x02, 1, body)
    packets = teletext._ts_packets(0, b'\x00' + pat, counters) + teletext._ts_packets(0x100, b'\x00' + pmt, counters)
    for payload in payloads:
        pes = b'\x00\x00\x01\xbf' + len(payload).to_bytes(2, 'big') + payload
        packets += teletext._ts_packets(0x1800, pes, counters)
    return b''.join(b'\x00\x00\x00\x00' + packet for packet in packets)


print('in a transport stream')
FFPROBE = shutil.which('ffprobe')
if not FFPROBE:
    print('  (no ffprobe: skipped)')
else:
    path = folder / '00001.m2ts'
    path.write_bytes(m2ts([STYLE] + DIALOGS))
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                      capture_output=True, check=True).stdout)
    tracks = video_subtitles.tracks(probe)
    check('FFmpeg lists the stream as TextST; Subtitld as a text track',
          [(t['codec'], t['kind']) for t in tracks], [('hdmv_text_subtitle', 'text')])
    target = video_subtitles.extract(str(path), tracks[0]['index'], 'hdmv_text_subtitle', ffprobe=FFPROBE,
                                     folder=str(folder))
    from subtitld.modules import srt
    check('extracted packet by packet as an SRT of its subtitles', [(s['start'], s['text']) for s in srt.read(target)],
          [(s['start'], s['text']) for s in found])

    print('"Subtitles in the video"')
    from subtitld.modules.addons.builtin import subtitles_provider
    session.FFPROBE_EXECUTABLE = FFPROBE
    provider = subtitles_provider.get_provider()
    loop, heard = QEventLoop(), {}
    provider.transcript_finished.connect(lambda segments: (heard.setdefault('finished', segments), loop.quit()))
    provider.error.connect(lambda message: (heard.setdefault('error', message), loop.quit()))
    provider.transcribe(str(path), 'en', {'track': tracks[0]['index'], 'codec': 'hdmv_text_subtitle', 'duration': 10})
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    check('the track read through the built-in engine', ([s['text'] for s in heard.get('finished', [])][:2],
                                                         heard.get('error')),
          (['First line\nand <i>the second</i>.', '{\\an8}<i>A sign</i>'], None))

print('written')
CUES = [{'start': 1.0, 'end': 3.0, 'text': 'First line\nand <i>the second</i>.'},
        {'start': 4.0, 'end': 5.5, 'text': '{\\an8}<b>A sign</b>', 'forced': True},
        {'start': 6.0, 'end': 7.0, 'text': 'In <font color="#ffff00">yellow</font>, Ação'},
        {'start': 8.0, 'end': 9.0, 'text': ('Long ' * 70).strip()}]
written = textst.stream(CUES)
check('a dialog style segment, then a dialog presentation segment a subtitle',
      [segment[0] for segment in written], [textst.DIALOG_STYLE] + [textst.DIALOG_PRESENTATION] * 4)
check('each segment as long as it says (type, 16-bit length, body: libbluray\'s graphics processor)',
      [len(segment) - 3 == int.from_bytes(segment[1:3], 'big') for segment in written], [True] * 5)
palette = {}
styles, count = textst.parse_style(written[0][3:], palette)
# libbluray's textst_decode reads the style segment through to its
# palette; graphics_processor.c then the number of dialogs that follow.
check('the style: nine region styles, the number of dialogs that follow it', (sorted(styles), count),
      (list(range(1, 10)), 4))
check('the top places in the upper part of the plane, the others not',
      [styles[k]['y'] + styles[k]['height'] / 2 < 1080 * 0.4 for k in range(1, 10)], [False] * 6 + [True] * 3)
check('a palette of transparent, white, black and the yellow (BT.709, a step off at most)',
      [palette[k][3] for k in range(4)] + [max(abs(a - b) for a, b in zip(palette[3][:3], (255, 255, 0))) <= 2],
      [0, 255, 255, 255, True])
dialogs = [textst.parse_dialog(segment[3:]) for segment in written[1:]]
check('each dialog at its times (90 kHz), in its place, forced where the subtitle is',
      [(d['start'], d['end'], d['regions'][0]['style'], d['regions'][0]['forced']) for d in dialogs],
      [(90000, 270000, 2, False), (360000, 495000, 8, True), (540000, 630000, 2, False), (720000, 810000, 2, False)])
long_strings = [len(s) for s in written[4].split(b'\x1b\x01')[1:]]
check('a string longer than an element holds (255 bytes) in two', len(long_strings), 2)
check('decoded again: the same subtitles', textst.decode(written), [
    {'start': 1.0, 'end': 3.0, 'text': 'First line\nand <i>the second</i>.'},
    {'start': 4.0, 'end': 5.5, 'text': '{\\an8}<b>A sign</b>', 'forced': True},
    {'start': 6.0, 'end': 7.0, 'text': 'In <font color="#feff00">yellow</font>, Ação'},
    {'start': 8.0, 'end': 9.0, 'text': ('Long ' * 70).strip()}])
overlapping = textst.stream([{'start': 1.0, 'end': 3.0, 'text': 'Below'},
                             {'start': 2.0, 'end': 4.0, 'text': '{\\an8}Above'}])
check('overlapping subtitles: a dialog for each stretch, the shared one of two regions, the top first',
      [(d['start'] / 90000, d['end'] / 90000, [r['style'] for r in d['regions']])
       for d in (textst.parse_dialog(segment[3:]) for segment in overlapping[1:])],
      [(1.0, 2.0, [2]), (2.0, 3.0, [8, 2]), (3.0, 4.0, [8])])

session.SUBTITLE = {'segments': CUES, 'language': 'pt-br'}
session.VIDEO = {}
session.FORMAT = {'format': 'TEXTST', 'options': {}}
file_io.save_file(str(folder / 'out.m2ts'), 'TEXTST')
data = (folder / 'out.m2ts').read_bytes()
check('saved: a Blu-ray transport stream, 192-byte packets, sync byte after each 4-byte arrival time',
      (len(data) % 192, {data[k + 4] for k in range(0, len(data), 192)}), (0, {0x47}))
check('registered: written alone, as a .m2ts, among the text formats',
      (session.LIST_OF_WRITE_ONLY_SUBTITLE_EXTENSIONS['TEXTST']['extensions'], 'TEXTST' in export_options.TEXT_FORMATS,
       'TEXTST' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS), (['m2ts'], True, False))
from subtitld.interface.export_dialog import SUBTITLE_FORMATS
check('offered by the export', 'TEXTST' in SUBTITLE_FORMATS, True)
if FFPROBE:
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-show_packets', '-show_format', '-of',
                                       'json', str(folder / 'out.m2ts')], capture_output=True, check=True).stdout)
    check('FFmpeg reads it: a TextST stream at PID 0x1800 in Portuguese, a packet a segment',
          ([(s['codec_name'], s['id'], s['tags'].get('language')) for s in probe['streams']], len(probe['packets'])),
          ([('hdmv_text_subtitle', '0x1800', 'por')], 5))
    target = video_subtitles.extract(str(folder / 'out.m2ts'), 0, 'hdmv_text_subtitle', ffprobe=FFPROBE,
                                     folder=str(folder))
    from subtitld.modules import srt
    check('and read back through FFmpeg as a video\'s track: the subtitles\' times and words',
          [(s['start'], s['end'], s['text']) for s in srt.read(target)],
          [(c['start'], c['end'], c['text'].replace('#ffff00', '#feff00').strip()) for c in CUES])
else:
    print('  (no ffprobe: FFmpeg\'s reading of it skipped)')

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
