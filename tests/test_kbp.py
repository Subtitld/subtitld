"""KBP: reading a Karaoke Builder Studio project laid out as kbputils
writes one (its header, track information, pages of lines and syllables,
a fixed style, "{-}", a hundredth between syllables), writing it back,
round trips, a default template for subtitles that came from elsewhere,
and opening and saving through file_io.

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
from subtitld.modules import export_options, file_io, kar, kbp, karaoke, markup, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


D = '-' * 29
PROJECT = '\r\n'.join([
    D, 'KARAOKE BUILDER STUDIO', 'www.KaraokeBuilder.com', '', D, 'HEADERV2', '',
    "'--- Template Information ---", '', "'Palette Colours (0-15)",
    '  000,FFF,000,E70,940,CFF,033,0DD,077,FCF,303,F3F,818,000,FFF,000', '',
    "'Styles (00-19)", '  Style00,Default,1,2,3,4', '    Arial,12,B,0', '    2,2,2,2,0,0,0,L', '',
    '  StyleEnd', '', "'Margins : L,R,T,Line Spacing", '  2,2,7,12', '',
    "'Other: Border Colour,Detail Level", '  0,2', '',
    "'--- Track Information ---", '',
    'Status    1', 'Title     A Test Song', 'Artist    The Band', 'Audio     song.mp3', 'BuildFile song.cdg',
    'Intro     ', 'Outro     ', '', 'Comments  first line', '          second line', '',
    D, 'PAGEV2',
    'C/A/100/420/0/0/0',
    'Hel/           150/179/0',
    'lo /           180/229/0',
    'world/         230/300/0',
    '',
    'C/a/100/420/0/0/0',
    'Not wiped/     0/0/0',
    '',
    D, 'PAGEV2',
    'L/A/500/800/0/0/0',
    'and{-}or/      520/600/0',
    '',
    D, ''])

print('reading')
segments, metadata = kbp.parse(PROJECT)
check('a line a subtitle, its syllables as karaoke codes, a hundredth between them closed, fixed text plain', segments, [
    {'start': 1.0, 'end': 4.2, 'text': '{\\k50}{\\k30}Hel{\\k50}lo {\\k70}world', 'kar': {'paragraph': True}},
    {'start': 1.0, 'end': 4.2, 'text': 'Not wiped'},
    {'start': 5.0, 'end': 8.0, 'text': '{\\k20}{\\k80}and/or', 'kar': {'paragraph': True}}])
check('the words when sung', karaoke.timed(segments[0]['text'], 1.0)[1:],
      [(1.5, 1.8, 'Hel'), (1.8, 2.3, 'lo '), (2.3, 3.0, 'world')])
check('title and artist, the track information kept', (metadata['title'], metadata['artist'],
      metadata['kbp']['track']['Comments'], metadata['kbp']['track']['Audio']),
      ('A Test Song', 'The Band', 'first line\nsecond line', 'song.mp3'))
check('detection', (kbp.is_kbp(PROJECT), kbp.is_kbp('[00:01.00]LRC\n'), kbp.is_kbp('1\n00:00:01,000 --> 00:00:02,000\nx\n')),
      (True, False, False))
for name, text, start in (('an unsynced project: refused', f'{D}\nHEADERV2\n{D}\nLYRICSV2\nla la\n', 'This Karaoke'),
                          ('not a project: refused', 'nothing', 'Not a Karaoke')):
    try:
        kbp.parse(text)
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)

print('writing')
written = kbp.write(segments, metadata)
check('the header written back, its template and track information', (
    written.split('\n')[:6], '  000,FFF,000,E70' in written, 'Comments  first line\n          second line' in written,
    'Title     A Test Song\n' in written), ([D, 'KARAOKE BUILDER STUDIO', 'www.KaraokeBuilder.com', '', D, 'HEADERV2'],
                                           True, True, True))
check('the pages and lines as KBS has them', written[written.index('PAGEV2') - len(D) - 1:], '\n'.join([
    D, 'PAGEV2', 'C/A/100/420/0/0/0',
    'Hel/           150/179/0', 'lo /           180/229/0', 'world/         230/300/0', '',
    'C/A/100/420/0/0/0', 'Not wiped/     100/420/0', '',
    D, 'PAGEV2', 'C/A/500/800/0/0/0', 'and{-}or/      520/600/0', '', D, '', '']))
check('read back the same, but the fixed line is wiped now', kbp.parse(written)[0],
      [segments[0], {'start': 1.0, 'end': 4.2, 'text': '{\\k320}Not wiped'}, segments[2]])
plain = [{'start': 0.5, 'end': 2.5, 'text': 'First line\nand <i>second</i>'}, {'start': 2.6, 'end': 3.0, 'text': 'Next'},
         {'start': 9.0, 'end': 10.0, 'text': '{\\k50}La{\\k50}la'}]
fresh = kbp.write(plain, {'title': 'Fresh'}, audio='video.mp4')
check('subtitles from elsewhere: the default template, the video as the audio', (
    "'Palette Colours (0-15)\n  055,FFF" in fresh, 'Title     Fresh\n' in fresh, 'Audio     video.mp4\n' in fresh,
    'Status    1\n' in fresh), (True, True, True, True))
check('a subtitle\'s lines each a line, sung for their share of the letters; a page after a gap',
      [(s['start'], s['end'], markup.plain(s['text']), bool(s.get('kar'))) for s in kbp.parse(fresh)[0]], [
          (0.5, 2.5, 'First line', True), (0.5, 2.5, 'and second', False), (2.6, 3.0, 'Next', False),
          (9.0, 10.0, 'Lala', True)])
pages = [segment for segment in kbp.parse(fresh)[0] if markup.plain(segment['text']) != 'and second']
check('as KAR, a page is a paragraph', [bool(s.get('kar')) for s in kar.parse(kar.write(pages))[0]], [True, False, True])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'song.kbp').write_bytes(PROJECT.encode('utf-8'))
session.SUBTITLE = {'segments': []}
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'song.kbp'))
check('opens as KBP with its title', (fmt, len(opened), session.SUBTITLE['metadata']['title']), ('KBP', 3, 'A Test Song'))
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.kbp'), 'KBP')
raw = (folder / 'again.kbp').read_bytes()
check('saved with Windows line ends, the same subtitles', (b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''),
                                                          kbp.parse(raw.decode('utf-8'))[0][0] == {k: v for k, v in opened[0].items() if k != 'id'}), (True, True))
check('registered: .kbp, a text format', (session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['KBP']['extensions'],
                                          'KBP' in export_options.TEXT_FORMATS), (['kbp'], True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
