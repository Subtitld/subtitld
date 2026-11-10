"""UltraStar: reading the song files of the karaoke games (headers, notes of
every kind, line breaks, relative beats, duets), writing them back with no
note moved, writing a song from ordinary subtitles, and telling a song
apart from the other formats that share .txt when file_io opens one.

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
from subtitld.modules import karaoke, legacy, markup, ultrastar

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# 300 BPM: a beat is 60 / (300 * 4) = 0.05 s, from 1.2 s in.
SONG = """#TITLE:Tears of Steel
#ARTIST:The Blender Foundation
#LANGUAGE:English
#GENRE:Pop
#YEAR:2012
#MP3:Tears of Steel.mp3
#COVER:Tears of Steel [CO].jpg
#VIDEO:Tears of Steel.mp4
#VIDEOGAP:0
#BPM:300
#GAP:1200
: 0 4 5 Hold
: 6 3 7  on
: 10 2 7  to
* 14 6 9  the
: 22 8 4  light
- 34
F 40 4 0 We
R 46 2 0  re
G 50 3 0 mem
: 54 2 2 ~
: 57 6 0 ber
- 70
: 72 4 3 Last
E
this is after the end and is not read
"""

print('the song')
segments, metadata, headers = ultrastar.parse(SONG)
check('a subtitle a line', len(segments), 3)
check('the first: first note to the line break, which comes later',
      (segments[0]['start'], segments[0]['end']), (1.2, 2.9))
check('its syllables as karaoke codes, a pause between notes',
      segments[0]['text'], '{\\k20}Hold{\\k10}{\\k15} on{\\k5}{\\k10} to{\\k10}{\\k30} the{\\k10}{\\k40} light')
check('the words read as the line', markup.plain(segments[0]['text']), 'Hold on to the light')
check('the notes\' kinds and pitches kept', segments[0]['ultrastar'], {'notes': [[':', 5], [':', 7], [':', 7], ['*', 9], [':', 4]]})
check('freestyle, rap, golden rap and the ~ that holds a vowel',
      (segments[1]['ultrastar']['notes'], markup.plain(segments[1]['text'])),
      ([['F', 0], ['R', 0], ['G', 0], [':', 2], [':', 0]], 'We remem~ber'))
check('the last line ends at its last note', (segments[2]['start'], segments[2]['end']), (4.8, 5.0))
check('title and artist as metadata', metadata, {'title': 'Tears of Steel', 'artist': 'The Blender Foundation'})
check('the other headers, in order', [key for key, _value in headers],
      ['LANGUAGE', 'GENRE', 'YEAR', 'MP3', 'COVER', 'VIDEO', 'VIDEOGAP', 'BPM', 'GAP'])
check('karaoke.timed: each note at its beat', karaoke.timed(segments[0]['text'], segments[0]['start'])[:2],
      [(1.2, 1.4, 'Hold'), (1.4, 1.5, '')])

print('writing it back')
check('the same song, note for note', ultrastar.write(segments, metadata, headers),
      SONG.split('E\n')[0] + 'E\n')

print('a line break before the next line only')
early = ultrastar.parse('#BPM:300\n#GAP:0\n: 0 4 0 A\n- 2\n: 10 4 0 B\n- 40\n: 20 2 0 C\nE\n')[0]
check('a break before the last note\'s end, or after the next line starts, is not an end',
      [(s['start'], s['end']) for s in early], [(0.0, 0.2), (0.5, 0.7), (1.0, 1.1)])

print('decimal commas, relative beats, CRLF, a BOM')
check('#BPM:300,5 and #GAP:1200,5', ultrastar.parse('#BPM:150,5\n#GAP:500,5\n: 10 4 0 A\nE\n')[0][0]['start'],
      round(0.5005 + 10 * 60 / (150.5 * 4), 3))
RELATIVE = '﻿#TITLE:Rel\r\n#BPM:300\r\n#GAP:0\r\n#RELATIVE:yes\r\n: 0 4 0 One\r\n- 6 8\r\n: 0 4 0 Two\r\n- 6\r\n: 0 4 0 Three\r\nE\r\n'
check('each line counts from the start the break before it gives',
      [s['start'] for s in ultrastar.parse(RELATIVE)[0]], [0.0, 0.4, 0.7])
written = ultrastar.write(*ultrastar.parse(RELATIVE)[:3])
check('written in absolute beats, with no #RELATIVE',
      ('#RELATIVE' in written, [line for line in written.split('\n') if line[:1] == ':']),
      (False, [': 0 4 0 One', ': 8 4 0 Two', ': 14 4 0 Three']))

print('a duet')
DUET = ('#TITLE:Duet\n#ARTIST:Two\n#BPM:300\n#GAP:0\n#P1:Ana\n#P2:Bea\n'
        'P1\n: 0 4 0 Mine\n- 6\n: 10 4 0 Again\nP2\n: 4 4 2 Yours\n- 12\n: 16 4 2 Too\nE\n')
duet = ultrastar.parse(DUET)[0]
check('each singer\'s lines, by start', [(s['start'], markup.plain(s['text']), s['ultrastar'].get('player')) for s in duet],
      [(0.0, 'Mine', 'P1'), (0.2, 'Yours', 'P2'), (0.5, 'Again', 'P1'), (0.8, 'Too', 'P2')])
check('written back as it was', ultrastar.write(*ultrastar.parse(DUET)), DUET)

print('a song from ordinary subtitles')
plain = ultrastar.write([{'start': 2.0, 'end': 3.0, 'text': 'Hello <i>big</i>\nworld'},
                         {'start': 4.0, 'end': 4.5, 'text': '{\\k25}La{\\k25}la'}],
                        {'title': 'New'}, None, audio='clip.mp4')
check('freestyle words a share of the time by length, beats at BPM 600 from the first start',
      plain, '#TITLE:New\n#ARTIST:\n#BPM:600\n#GAP:2000\n#MP3:clip.mp4\n#VIDEO:clip.mp4\n'
             'F 0 15 0 Hello \nF 15 10 0 big \nF 25 15 0 world\n- 40\n'
             'F 80 10 0 La\nF 90 10 0 la\nE\n')
check('and read back with the words where they were',
      [(s['start'], s['end'], markup.plain(s['text'])) for s in ultrastar.parse(plain)[0]],
      [(2.0, 3.0, 'Hello big world'), (4.0, 4.5, 'Lala')])
edited = [dict(segment) for segment in segments]
edited[2] = dict(edited[2], text=edited[2]['text'] + '{\\k10} more')
check('a syllable added past the notes kept is freestyle at the pitch before',
      ultrastar.write(edited, metadata, headers).split('\n')[-3], 'F 76 2 3  more')
check('a song that starts before its GAP moves the GAP',
      '#GAP:500' in ultrastar.write([{'start': 0.5, 'end': 1.0, 'text': 'Early'}] + segments, metadata, headers), True)

print('telling it apart')
check('a song is UltraStar', ultrastar.kind(SONG), True)
check('and no player-era format', legacy.kind(SONG), '')
check('a VPlayer file is not UltraStar', ultrastar.kind('00:00:01:Hello\n00:00:05:There\n'), False)
check('a transcript with a hash is not', ultrastar.kind('#BPM: notes from the meeting\nWe talked.\n'), False)
check('headers with no notes are not', ultrastar.kind('#TITLE:x\n#BPM:300\n'), False)
try:
    ultrastar.parse('#BPM:0\n: 0 1 0 x\n')
    check('a BPM of 0 says so', None, 'ValueError')
except ValueError as error:
    check('a BPM of 0 says so', str(error), 'Not an UltraStar song: it has no #BPM.')

# --------------------------------------------------------------------------
print('through file_io')
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import export_options, file_io, session

folder = Path(tempfile.mkdtemp())
(folder / 'Tears of Steel.txt').write_text(SONG, encoding='utf-8')
session.SUBTITLE = {'segments': [], 'language': 'pt-br'}
session.FORMAT = {}
session.VIDEO = {}
opened, found = file_io.process_subtitles_file(str(folder / 'Tears of Steel.txt'))
check('a song in a .txt opens as UltraStar', (found, len(opened)), ('ULTRASTAR', 3))
check('its #LANGUAGE the project\'s', session.SUBTITLE['language'].split('-')[0], 'en')
check('its headers kept with its metadata', [key for key, _value in session.SUBTITLE['metadata']['ultrastar']][-2:],
      ['BPM', 'GAP'])
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.txt'), 'ULTRASTAR')
check('saved note for note', (folder / 'again.txt').read_text(encoding='utf-8'), SONG.split('E\n')[0] + 'E\n')
check('and opened again', file_io.process_subtitles_file(str(folder / 'again.txt'))[1], 'ULTRASTAR')

# Another export in between sets its own options: the song's headers are
# on the subtitles, not in them.
session.FORMAT = {'format': 'SRT', 'options': {'formatting': 'keep'}}
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'lyrics.srt'), 'SRT')
file_io.save_file(str(folder / 'third.txt'), 'ULTRASTAR')
check('headers survive another format\'s export', '#BPM:300\n#GAP:1200\n' in (folder / 'third.txt').read_text(encoding='utf-8'), True)

session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Hello'}]}
session.FORMAT = {}
session.VIDEO = {'filepath': '/videos/tears.mov', 'framerate': 25.0}
file_io.save_file(str(folder / 'new.txt'), 'ULTRASTAR')
check('a new song names the video as its audio and title',
      (folder / 'new.txt').read_text(encoding='utf-8').split('\n')[:6],
      ['#TITLE:tears', '#ARTIST:', '#BPM:600', '#GAP:1000', '#MP3:tears.mov', '#VIDEO:tears.mov'])

(folder / 'vplayer.txt').write_text('00:00:01:Hello\n00:00:05:There\n', encoding='utf-8')
check('a VPlayer .txt is still VPlayer', file_io.process_subtitles_file(str(folder / 'vplayer.txt'))[1], 'VPLAYER')
check('UltraStar takes the text options', 'ULTRASTAR' in export_options.TEXT_FORMATS, True)
check('.txt is UltraStar\'s as well', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['ULTRASTAR']['extensions'], ['txt'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
