"""SubRip: reading the SRT files people have, writing ones players read,
and the formatting tags carried into the other formats.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, srt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data):
    return [(s['start'], s['end'], s['text']) for s in srt.parse(srt.decode(data))]


BASE = '1\n00:00:01,000 --> 00:00:02,500\nHello there.\nSecond line.\n\n2\n00:00:03,000 --> 00:00:04,000\n<i>Italic</i> and <b>bold</b>\n\n'
BASE_CUES = [(1.0, 2.5, 'Hello there.\nSecond line.'), (3.0, 4.0, '<i>Italic</i> and <b>bold</b>')]

print('encodings and line ends')
check('UTF-8', cues(BASE.encode('utf-8')), BASE_CUES)
check('UTF-8 with a byte order mark', cues(b'\xef\xbb\xbf' + BASE.encode('utf-8')), BASE_CUES)
check('UTF-16 with a byte order mark', cues(BASE.encode('utf-16')), BASE_CUES)
check('UTF-16 big-endian', cues(b'\xfe\xff' + BASE.encode('utf-16-be')), BASE_CUES)
check('Windows line ends', cues(BASE.replace('\n', '\r\n').encode()), BASE_CUES)
check('old Mac line ends', cues(BASE.replace('\n', '\r').encode()), BASE_CUES)
check('Windows-1252', cues('1\n00:00:01,000 --> 00:00:02,000\nAção, você é já\n'.encode('cp1252')), [(1.0, 2.0, 'Ação, você é já')])
check('Windows-1252 quotes and dashes',
      cues('1\n00:00:01,000 --> 00:00:02,000\n“Quoted” — dash …\n'.encode('cp1252')),
      [(1.0, 2.0, '“Quoted” — dash …')])
check('an empty file', cues(b''), [])
check('only a byte order mark', cues(b'\xef\xbb\xbf'), [])

print('found by timing lines, not blank lines')
check('no blank line between subtitles',
      cues(b'1\n00:00:01,000 --> 00:00:02,000\nFirst\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n'),
      [(1.0, 2.0, 'First'), (3.0, 4.0, 'Second')])
check('leading and extra blank lines, trailing spaces',
      cues(b'\n\n1\n00:00:01,000 --> 00:00:02,000\nFirst   \n\n\n\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n\n\n'),
      [(1.0, 2.0, 'First'), (3.0, 4.0, 'Second')])
check('no numbers', cues(b'00:00:01,000 --> 00:00:02,000\nFirst\n\n00:00:03,000 --> 00:00:04,000\nSecond\n'),
      [(1.0, 2.0, 'First'), (3.0, 4.0, 'Second')])
check('a blank line inside the text keeps the rest',
      cues(b'1\n00:00:01,000 --> 00:00:02,000\nLine one\n\nLine two\n\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n'),
      [(1.0, 2.0, 'Line one\nLine two'), (3.0, 4.0, 'Second')])
check('a number as the text, then the next number',
      cues(b'1\n00:00:01,000 --> 00:00:02,000\n42\n\n2\n00:00:03,000 --> 00:00:04,000\nAfter\n'),
      [(1.0, 2.0, '42'), (3.0, 4.0, 'After')])
check('a number as the text, no numbers and no blank lines',
      cues(b'00:00:01,000 --> 00:00:02,000\n2000\n00:00:03,000 --> 00:00:04,000\nAfter\n'),
      [(1.0, 2.0, '2000'), (3.0, 4.0, 'After')])
check('numbers that do not start at 1, without blank lines',
      cues(b'7\n00:00:01,000 --> 00:00:02,000\nFirst\n8\n00:00:03,000 --> 00:00:04,000\nSecond\n'),
      [(1.0, 2.0, 'First'), (3.0, 4.0, 'Second')])
check('an empty subtitle', cues(b'1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nAfter\n'),
      [(1.0, 2.0, ''), (3.0, 4.0, 'After')])
check('out of order, sorted', cues(b'1\n00:00:05,000 --> 00:00:06,000\nLater\n\n2\n00:00:01,000 --> 00:00:02,000\nEarlier\n'),
      [(1.0, 2.0, 'Earlier'), (5.0, 6.0, 'Later')])
check('a time in the text is not a timing', cues(b'1\n00:00:01,000 --> 00:00:02,000\nMeet at 10:30 -> 11:00\n'),
      [(1.0, 2.0, 'Meet at 10:30 -> 11:00')])

print('timings')
check('a dot for the comma', cues(b'1\n00:00:01.500 --> 00:00:02.000\nx\n'), [(1.5, 2.0, 'x')])
check('one-digit hours', cues(b'1\n0:00:01,000 --> 0:00:02,000\nx\n'), [(1.0, 2.0, 'x')])
check('fewer decimals are a fraction', cues(b'1\n00:00:01,50 --> 00:00:02,5\nx\n'), [(1.5, 2.5, 'x')])
check('more decimals are rounded', cues(b'1\n00:00:01,2345 --> 00:00:02,0006\nx\n'), [(1.234, 2.001, 'x')])
check('no decimals', cues(b'1\n00:00:01 --> 00:00:02\nx\n'), [(1.0, 2.0, 'x')])
check('a short arrow', cues(b'1\n00:00:01,000 -> 00:00:02,000\nx\n'), [(1.0, 2.0, 'x')])
check('coordinates after the end', cues(b'1\n00:00:01,000 --> 00:00:02,000 X1:100 X2:500 Y1:10 Y2:50\nx\n'), [(1.0, 2.0, 'x')])
check('an end before the start', cues(b'1\n00:00:05,000 --> 00:00:04,000\nx\n'), [(5.0, 5.0, 'x')])
check('past 99 hours', cues(b'1\n100:00:00,500 --> 100:00:01,000\nx\n'), [(360000.5, 360001.0, 'x')])

print('the text keeps its formatting as written')
check('font colour', cues(b'1\n00:00:01,000 --> 00:00:02,000\n<font color="#ff0000">Red</font> text\n'),
      [(1.0, 2.0, '<font color="#ff0000">Red</font> text')])
check('a position code', cues(b'1\n00:00:01,000 --> 00:00:02,000\n{\\an8}Top\n'), [(1.0, 2.0, '{\\an8}Top')])
check('& and < that are text', cues('1\n00:00:01,000 --> 00:00:02,000\nTom & Jerry <3\n'.encode()),
      [(1.0, 2.0, 'Tom & Jerry <3')])

print('writing')
written = srt.write([
    {'start': 5.0, 'end': 6.0, 'text': 'Later'},
    {'start': 1.001, 'end': 2.999, 'text': 'Rounding'},
    {'start': 0.2999999, 'end': 0.5704999, 'text': 'Float noise'},
    {'start': 3.0, 'end': 4.0, 'text': '<i>Italic</i> & <3'},
    {'start': 4.1, 'end': 4.2, 'text': ''},
    {'start': 4.21, 'end': 4.25, 'text': '<i></i>'},
    {'start': 4.3, 'end': 4.9, 'text': 'Line one\n\nLine three\r\n'},
    {'start': 7.0, 'end': 8.0, 'text': '  padded  \nsecond  '},
    {'start': 9.0, 'end': 8.5, 'text': 'End before start'},
    {'start': 360000.5, 'end': 360001.0, 'text': '100 hours in'},
])
check('the whole file', written, (
    '1\n00:00:00,300 --> 00:00:00,570\nFloat noise\n\n'
    '2\n00:00:01,001 --> 00:00:02,999\nRounding\n\n'
    '3\n00:00:03,000 --> 00:00:04,000\n<i>Italic</i> & <3\n\n'
    '4\n00:00:04,300 --> 00:00:04,900\nLine one\nLine three\n\n'
    '5\n00:00:05,000 --> 00:00:06,000\nLater\n\n'
    '6\n00:00:07,000 --> 00:00:08,000\npadded\nsecond\n\n'
    '7\n00:00:09,000 --> 00:00:09,000\nEnd before start\n\n'
    '8\n100:00:00,500 --> 100:00:01,000\n100 hours in\n'))
check('nothing to write', srt.write([{'start': 1, 'end': 2, 'text': ' '}]), '')
check('reads back as written', [(s['start'], s['end'], s['text']) for s in srt.parse(written)][:3],
      [(0.3, 0.57, 'Float noise'), (1.001, 2.999, 'Rounding'), (3.0, 4.0, '<i>Italic</i> & <3')])
check('format_time', [srt.format_time(t) for t in (0, 0.0006, 59.9996, 3661.25, -1)],
      ['00:00:00,000', '00:00:00,001', '00:01:00,000', '01:01:01,250', '00:00:00,000'])

print('opening and saving through file_io')
folder = Path(tempfile.mkdtemp())
path = folder / 'broken.srt'
path.write_bytes(b'\xef\xbb\xbf\r\n1\r\n00:00:01.000 -> 00:00:02,000\r\n<i>First</i>\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nSecond\r\n')
segments, _format = file_io.process_subtitles_file(str(path))
check('opens', [(s['start'], s['end'], s['text']) for s in segments], [(1.0, 2.0, '<i>First</i>'), (3.0, 4.0, 'Second')])
session.SUBTITLE['segments'] = segments
session.SUBTITLE['language'] = 'en'
out = folder / 'saved.srt'
file_io.save_file(str(out), 'SRT')
check('saves', out.read_bytes(), b'1\n00:00:01,000 --> 00:00:02,000\n<i>First</i>\n\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n')
empty = folder / 'empty.srt'
empty.write_bytes(b'')
check('an empty file opens with no subtitles', file_io.process_subtitles_file(str(empty))[0], [])

print('the formatting, carried into the other formats')
session.SUBTITLE['segments'] = [
    {'start': 3.0, 'end': 4.0, 'text': 'Second'},
    {'start': 1.0, 'end': 2.0, 'text': '<i>Italic</i> & <b>bold</b>\n<font color="#ff0000">red</font>'},
]


def saved(fmt, ext):
    target = folder / f'out.{ext}'
    file_io.save_file(str(target), fmt)
    return target.read_text(encoding='utf-8')


vtt = saved('VTT', 'vtt')
check('VTT: italic and bold as tags, & escaped', '<i>Italic</i> &amp; <b>bold</b>' in vtt, True)
check('VTT: no tag written as text', '&lt;' in vtt, False)
check('VTT: in order', vtt.index('Italic') < vtt.index('Second'), True)
dfxp = saved('DFXP', 'dfxp')
check('DFXP: italic and colour as styles', ('tts:fontStyle="italic"' in dfxp, 'tts:color="#ff0000"' in dfxp), (True, True))
check('DFXP: the line break kept', '<br/>' in dfxp, True)
sami = saved('SAMI', 'smi')
check('SAMI: italic', '<i>Italic</i>' in sami, True)
ass = saved('ASS', 'ass')
check('ASS: override codes, line break as \\N, two events in order',
      [line.split(',', 9)[-1] for line in ass.splitlines() if line.startswith('Dialogue:')],
      ['{\\i1}Italic{\\i0} & {\\b1}bold{\\b0}\\N{\\c&H0000FF&}red{\\c}', 'Second'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
