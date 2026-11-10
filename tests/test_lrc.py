"""LRC and enhanced LRC: reading the lyrics files music players scroll (ID
tags, offsets, several moments on a line, word stamps), writing them back,
the word timing kept as karaoke codes through the editor and into ASS,
and opening and exporting through file_io; the reader checked against
FFmpeg's own LRC demuxer where ffmpeg is installed.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, re, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import karaoke, lrc, markup

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text):
    return [(s['start'], s['end'], s['text']) for s in lrc.parse(text)[0]]


SONG = """﻿[ti:Tears of Steel]
[ar:The Blender Foundation]
[al:Mango]
[au:Joram Letwory]
[by:Celia]
[re:Some Player 1.0]
[ve:1.0]
[#:made by hand]
[length: 03:25]

[00:12.00]Hold on to the light
[00:15.30][01:02.10]It was never ours to keep
[00:19.80]
[00:21.05]Under a broken sky
[00:24.50]We remember
"""

print('lines and tags')
check('a line runs to the next moment, a moment alone ends one, the chorus twice',
      cues(SONG),
      [(12.0, 15.3, 'Hold on to the light'), (15.3, 19.8, 'It was never ours to keep'),
       (21.05, 24.5, 'Under a broken sky'), (24.5, 62.1, 'We remember'),
       (62.1, 65.1, 'It was never ours to keep')])
check('the ID tags as metadata, the program and comments left out', lrc.parse(SONG)[1],
      {'title': 'Tears of Steel', 'artist': 'The Blender Foundation', 'album': 'Mango',
       'songwriter': 'Joram Letwory', 'author': 'Celia', 'length': '03:25'})
check('an offset shows every line sooner', cues('[offset:+500]\n[00:12.00]A\n[00:15.00]B\n'),
      [(11.5, 14.5, 'A'), (14.5, 17.5, 'B')])
check('a negative one later', cues('[offset:-250]\n[00:12.00]A\n')[0][:2], (12.25, 15.25))
check('thousandths, a colon before them, none at all, minutes past 99',
      [start for start, _end, _text in cues('[00:01.5]A\n[00:02.250]B\n[00:03:10]C\n[00:04]D\n[100:00.00]E\n')],
      [1.5, 2.25, 3.1, 4.0, 6000.0])
check('lines out of order are put in order',
      cues('[00:05.00]Second\n[00:01.00]First\n'), [(1.0, 5.0, 'First'), (5.0, 8.0, 'Second')])
check('CRLF line ends and blank lines', cues('[ti:x]\r\n\r\n[00:01.00]One\r\n[00:02.00]Two\r\n'),
      [(1.0, 2.0, 'One'), (2.0, 5.0, 'Two')])
check('the last line ends at the song\'s length? no: TAIL, as a length is the song\'s, not a line\'s',
      cues('[length:00:10]\n[00:01.00]Only\n'), [(1.0, 4.0, 'Only')])
check('is_lrc', (lrc.is_lrc(SONG), lrc.is_lrc('1\n00:00:01,000 --> 00:00:02,000\nSRT\n'),
                 lrc.is_lrc('[ti:only tags]\n')), (True, False, False))

print('enhanced LRC')
ENHANCED = ('[ti:Karaoke]\n'
            '[00:20.50]<00:20.50>Hel<00:20.90>lo <00:21.20>world <00:21.80>\n'
            '[00:23.00]<00:23.00>No <00:23.40>end <00:23.70>stamp\n'
            '[00:26.00]\n')
segments = lrc.parse(ENHANCED)[0]
check('word stamps as karaoke codes, the last word ended by the last stamp',
      segments[0]['text'], '{\\k40}Hel{\\k30}lo {\\k60}world')
check('the line still runs to the next moment', (segments[0]['start'], segments[0]['end']), (20.5, 23.0))
check('a last word with no stamp after it lasts to the line\'s end', segments[1]['text'],
      '{\\k40}No {\\k30}end {\\k230}stamp')
check('the editor shows the words alone', markup.plain(segments[0]['text']), 'Hello world')
check('karaoke.timed reads the moments back', karaoke.timed(segments[0]['text'], segments[0]['start']),
      [(20.5, 20.9, 'Hel'), (20.9, 21.2, 'lo '), (21.2, 21.8, 'world')])
check('A2: spaces around the stamps', lrc.parse('[00:01.00]<00:01.00> One <00:01.50> two <00:02.00>\n')[0][0]['text'],
      '{\\k50}One {\\k50}two')
check('a line sung twice moves its word stamps with it',
      [s['text'] for s in lrc.parse('[00:01.00][00:11.00]<00:01.00>La <00:01.50>la<00:02.00>\n[00:20.00]\n')[0]],
      ['{\\k50}La {\\k50}la', '{\\k50}La {\\k50}la'])
check('an offset moves word stamps too, the last line ending at its last',
      lrc.parse('[offset:1000]\n[00:05.00]<00:05.00>A <00:05.50>b<00:06.00>\n')[0][0],
      {'start': 4.0, 'end': 5.0, 'text': '{\\k50}A {\\k50}b'})
check('two stamps with nothing between them are a pause',
      lrc.parse('[00:01.00]<00:01.00>Wait<00:01.50><00:02.00>now<00:02.50>\n')[0][0]['text'],
      '{\\k50}Wait{\\k50}{\\k50}now')

print('karaoke codes')
check('\\K, \\kf and \\ko are lengths as well, other codes kept out of the words',
      karaoke.syllables('{\\an8}{\\K20}<i>Sha</i>{\\kf30\\i1}la{\\ko10}{\\k15}la'),
      [(20, 'Sha'), (30, 'la'), (10, ''), (15, 'la')])
check('text before the first code takes no time', karaoke.timed('Ah {\\k50}oh', 2.0),
      [(2.0, 2.0, 'Ah '), (2.0, 2.5, 'oh')])
check('no codes, no syllables', (karaoke.syllables('Plain <i>text</i>'), karaoke.timed('{\\an8}Plain', 0)),
      ([], None))
check('text_from rounds from the start, so a long line does not drift',
      karaoke.timed(karaoke.text_from(1.0, [(1.0 + i * 0.333, 1.0 + (i + 1) * 0.333, 'x') for i in range(30)]), 1.0)[-1][:2],
      (10.66, 10.99))

print('writing')
written = lrc.write(lrc.parse(SONG)[0], lrc.parse(SONG)[1])
check('the tags, the lines, an end where a pause follows and after the last', written,
      '[ti:Tears of Steel]\n[ar:The Blender Foundation]\n[al:Mango]\n[au:Joram Letwory]\n[by:Celia]\n'
      '[length:03:25]\n'
      '[00:12.00]Hold on to the light\n[00:15.30]It was never ours to keep\n[00:19.80]\n'
      '[00:21.05]Under a broken sky\n[00:24.50]We remember\n'
      '[01:02.10]It was never ours to keep\n[01:05.10]\n')
check('and read back the same', cues(written), cues(SONG))
check('word stamps written back', lrc.write(lrc.parse(ENHANCED)[0]),
      '[00:20.50]<00:20.50>Hel<00:20.90>lo <00:21.20>world<00:21.80>\n'
      '[00:23.00]<00:23.00>No <00:23.40>end <00:23.70>stamp<00:26.00>\n[00:26.00]\n')
check('enhanced round trip', cues(lrc.write(lrc.parse(ENHANCED)[0])), cues(ENHANCED))
check('two lines joined, formatting left out, hundredths',
      lrc.write([{'start': 1.234, 'end': 3.0, 'text': '<i>First</i> line\nsecond {\\an8}line'},
                 {'start': 3.0, 'end': 4.0, 'text': 'Next'}]),
      '[00:01.23]First line second line\n[00:03.00]Next\n[00:04.00]\n')
check('an empty subtitle is not written',
      lrc.write([{'start': 1.0, 'end': 2.0, 'text': '  '}, {'start': 3.0, 'end': 4.0, 'text': 'B'}]),
      '[00:03.00]B\n[00:04.00]\n')
check('overlapping subtitles: the first is cut by the second',
      cues(lrc.write([{'start': 1.0, 'end': 5.0, 'text': 'A'}, {'start': 3.0, 'end': 6.0, 'text': 'B'}])),
      [(1.0, 3.0, 'A'), (3.0, 6.0, 'B')])

# --------------------------------------------------------------------------
STAMP = re.compile(r'(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)')
if subprocess.run(['which', 'ffmpeg'], capture_output=True).returncode != 0:
    print('against FFmpeg: skipped, ffmpeg is not installed')
else:
    print('against FFmpeg, which demuxes LRC itself')
    path = Path(tempfile.mkdtemp()) / 'song.lrc'
    # No offset: FFmpeg's demuxer leaves it to the player.
    path.write_text(SONG, encoding='utf-8')
    out = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lrc', '-i', str(path), '-f', 'srt', '-'],
                         capture_output=True, text=True, timeout=60).stdout
    theirs = []
    heads = list(re.finditer(r'(?m)^\d+\n' + STAMP.pattern + r'\n', out))
    for index, head in enumerate(heads):
        text = out[head.end():heads[index + 1].start() if index + 1 < len(heads) else len(out)].strip()
        if text:
            numbers = [int(part) for part in head.groups()]
            theirs.append((round(numbers[0] * 3600 + numbers[1] * 60 + numbers[2] + numbers[3] / 1000, 3),
                           round(numbers[4] * 3600 + numbers[5] * 60 + numbers[6] + numbers[7] / 1000, 3), text))
    ours = cues(SONG)
    check('the same lines at the same moments', [(start, text) for start, _end, text in theirs],
          [(start, text) for start, _end, text in ours])
    # FFmpeg gives the last line no length; every other end agrees.
    check('the same ends but the last', [end for _start, end, _text in theirs][:-1],
          [end for _start, end, _text in ours][:-1])

# --------------------------------------------------------------------------
print('through file_io')
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import export_options, file_io, session
from subtitld.interface import export_dialog

folder = Path(tempfile.mkdtemp())
(folder / 'song.lrc').write_text(SONG + '[00:30.00]<00:30.00>Hel<00:30.40>lo<00:31.00>\n', encoding='utf-8')
session.SUBTITLE = {'segments': []}
session.FORMAT = {}
session.VIDEO = {}
opened, found = file_io.process_subtitles_file(str(folder / 'song.lrc'))
check('opened as LRC', (found, len(opened)), ('LRC', 6))
check('its tags kept on the subtitles', session.SUBTITLE['metadata']['title'], 'Tears of Steel')
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.lrc'), 'LRC')
again = (folder / 'again.lrc').read_text(encoding='utf-8')
check('saved with its tags and word stamps',
      (again.startswith('[ti:Tears of Steel]\n'), '[00:30.00]<00:30.00>Hel<00:30.40>lo<00:31.00>' in again), (True, True))
reopened, _found = file_io.process_subtitles_file(str(folder / 'again.lrc'))
check('and opened again the same', [(s['start'], s['end'], s['text']) for s in reopened],
      [(s['start'], s['end'], s['text']) for s in opened])

session.FORMAT = {'format': 'LRC', 'options': {'formatting': 'keep', 'offset': 1.0, 'encoding': 'utf-8-sig',
                                              'line_ends': 'crlf'}}
file_io.save_file(str(folder / 'shifted.lrc'), 'LRC')
raw = (folder / 'shifted.lrc').read_bytes()
check('the export\'s encoding, line ends and offset',
      (raw.startswith(b'\xef\xbb\xbf'), b'\r\n' in raw, b'[00:13.00]Hold on' in raw), (True, True, True))

print('karaoke into ASS')
session.FORMAT = {'format': 'ASS', 'options': {}}
session.SUBTITLE['segments'] = [dict(segment) for segment in opened]
file_io.save_file(str(folder / 'song.ass'), 'ASS')
check('the word timing is ASS karaoke', '{\\k40}Hel{\\k60}lo' in (folder / 'song.ass').read_text(encoding='utf-8'), True)

print('the export menu')
check('LRC is offered', 'LRC' in export_dialog.SUBTITLE_FORMATS, True)
check('and takes the text options', 'LRC' in export_options.TEXT_FORMATS, True)
check('.lrc is LRC', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['LRC']['extensions'], ['lrc'])
dialog = export_dialog.ExportDialog()
panel = dialog.findChild(export_dialog._SubtitlesPanel)
panel.format_combo.setCurrentText('LRC')
panel._refresh_options()
check('with an encoding and line ends to choose', panel.srt_options.isHidden(), False)

(folder / 'notlrc.lrc').write_text('just some words\n', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'notlrc.lrc'))
    check('a .lrc that is not LRC says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('a .lrc that is not LRC says so', str(error).startswith('Not an LRC file'), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
