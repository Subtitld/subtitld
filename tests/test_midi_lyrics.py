"""MIDI lyrics beyond KAR: a Tune 1000 song in a type 0 file, Yamaha XF
lyrics built byte by byte as the XF spec's examples lay them out (the
Lyrics Header, an XFKM chunk, Shift-JIS, its control characters, rubies
and readings, vocal parts, the information header), and writing the words
as standard lyric events (RP-026), alone and keeping the music of the file
opened; then open and save through file_io as the MIDI format.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, struct
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import export_options, file_io, kar, markup, session
from subtitld.modules.segment import Subtitle, text_of

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def vlq(value):
    out = [value & 0x7F]
    while value > 0x7F:
        value >>= 7
        out.append(0x80 | (value & 0x7F))
    return bytes(reversed(out))


def chunk(name, events, end=True):
    out, last = b'', 0
    for tick, raw in events:
        out += vlq(tick - last) + raw
        last = tick
    if end:
        out += b'\x00\xff\x2f\x00'
    return name + struct.pack('>I', len(out)) + out


def meta(kind, data, codec='cp1252'):
    data = data.encode(codec) if isinstance(data, str) else data
    return bytes([0xFF, kind]) + vlq(len(data)) + data


def smf(kind, division, chunks, count=None):
    tracks = sum(1 for c in chunks if c[:4] == b'MTrk') if count is None else count
    return b'MThd' + struct.pack('>IHHH', 6, kind, tracks, division) + b''.join(chunks)


def lyric_events(data, kind=0x05):
    """[(tick, bytes)] of the meta events of `kind` in every track, by tick."""
    _type, _division, tracks = kar.parse_smf(data)
    return sorted(((tick, event[2]) for track in tracks for tick, event in track
                   if event[0] == 'meta' and event[1] == kind), key=lambda item: item[0])


def plain(segments):
    return [(s['start'], s['end'], markup.plain(text_of(s))) for s in segments]


print('Tune 1000 in a type 0 file')
# One track with the tempo, the headers, the words and the melody, 96 a quarter.
TYPE0 = smf(0, 96, [chunk(b'MTrk', [
    (0, meta(0x01, '@KMIDI KARAOKE FILE')), (0, meta(0x01, '@LENGL')), (0, meta(0x01, '@TType Zero')),
    (0, meta(0x51, (500000).to_bytes(3, 'big'))), (0, b'\xc0\x05'),
    (96, meta(0x01, '\\Row ')), (96, b'\x90\x3c\x64'), (144, meta(0x01, 'row')), (144, b'\x3c\x00'),
    (192, meta(0x01, '/your ')), (240, meta(0x01, 'boat'))])])
segments, metadata = kar.parse(TYPE0)
check('the words found in the one track with the music', segments, [
    {'start': 0.5, 'end': 1.0, 'text': '{\\k25}Row {\\k25}row', 'kar': {'paragraph': True}},
    {'start': 1.0, 'end': 3.25, 'text': '{\\k25}your {\\k200}boat'}])
check('its title', (metadata['title'], kar.words_kind(TYPE0)), ('Type Zero', 'kar'))
again = kar.write(segments, metadata, source=TYPE0)
check('saved as KAR keeping its music, read back the same', (kar.parse(again)[0], kar.parse_smf(again)[0]), (segments, 1))

print('Yamaha XF')
# The spec's page 15 example ("さ" closing the ruby its example leaves
# open): "<" a page, rubies across events, "/" lines,
# a reading after a kanji, 480 a quarter, the Lyrics Header saying JP and an
# offset of 240 ticks (a quarter second at 120), the words in an XFKM chunk.
WORDS = ['<宵[よ', 'い]', '待[ま', 'ち]', '草[ぐ', 'さ]', 'の/', '待(ま)', 'て', 'ど', '暮(く)', 'ら', 'せ', 'ど/',
         '来(こ)', 'ぬ', '人', 'を']
XFKM = chunk(b'XFKM', [(0, meta(0x07, '$Lyrc:1:240:JP'))] + [
    (480 + 240 * number, meta(0x05, word, 'cp932')) for number, word in enumerate(WORDS)])
FIRST = chunk(b'MTrk', [(0, meta(0x03, '待宵草', 'cp932')), (0, meta(0x7F, b'\x43\x7b\x00XF02\x00\x1b')),
                        (0, meta(0x51, (500000).to_bytes(3, 'big'))), (0, meta(0x01, 'XFhd:1994/09/28:JP:Pops:8Beat:65:f1:'
                                                                              'Taro Yamaha:Hanako Hamamatsu::Machiko Nakazawa:Jiro'))])
MELODY = chunk(b'MTrk', [(0, b'\xc0\x41'), (480, b'\x90\x48\x64'), (720, b'\x48\x00')])
XF = smf(1, 480, [FIRST, MELODY, XFKM])
segments, metadata = kar.parse(XF)
check('XF found, in its karaoke chunk', kar.words_kind(XF), 'xf')
check('a line a subtitle, the offset taken off each moment, readings left out, rubies kept',
      [(s['start'], s['end'], s['text']) for s in segments], [
          (0.25, 2.0, '{\\k50}<ruby>宵<rt>よい</rt></ruby>{\\k50}<ruby>待<rt>まち</rt></ruby>'
                      '{\\k50}<ruby>草<rt>ぐさ</rt></ruby>{\\k25}の'),
          (2.0, 3.75, '{\\k25}待{\\k25}て{\\k25}ど{\\k25}暮{\\k25}ら{\\k25}せ{\\k25}ど'),
          (3.75, 6.5, '{\\k25}来{\\k25}ぬ{\\k25}人{\\k200}を')])
check('the page as a paragraph, the lines in it not', [bool(s.get('kar')) for s in segments], [True, False, False])
check('what shows: the words, a ruby\'s reading above them', markup.plain(segments[1]['text']), '待てど暮らせど')
check('the song\'s name from the first track, the performer and lyricist from XFhd', metadata,
      {'title': '待宵草', 'artist': 'Machiko Nakazawa', 'songwriter': 'Hanako Hamamatsu'})

# In a track, in Latin-1: "^" a space, "%" and ">" left out, "\\" before a
# control character, vocal parts, a {#Title=} tag, "{@LATIN}"; RP-026's CR
# and LF as events.
INTRACK = smf(0, 96, [chunk(b'MTrk', [
    (0, meta(0x07, '$Lyrc:1:0:L1')), (0, meta(0x07, '&f')),
    (96, meta(0x05, '{@LATIN}{#Title=Café}>Ça^va')), (144, meta(0x05, '%bien\\/mal ')), (192, meta(0x05, b'\r')),
    (192, meta(0x07, '&m')), (192, meta(0x05, 'Oui')), (240, meta(0x05, b'\n')), (288, meta(0x05, 'Fin\\^'))])])
segments, metadata = kar.parse(INTRACK)
check('control characters, Latin-1, parts as speakers, a page by LF', [
    (s['start'], markup.plain(s['text']), s.get('speaker'), bool(s.get('kar'))) for s in segments], [
    (0.5, 'Ça vabien/mal', 'Female', True), (1.0, 'Oui', 'Male', False), (1.5, 'Fin^', 'Male', True)])
check('{#Title=} gives the title', metadata.get('title'), 'Café')
check('a ruby left open ends with its line', kar._xf_texts(
    [(0, ('meta', 5, b'Ka[ka')), (0, ('meta', 5, b'ra/')), (9, ('meta', 5, b'Next'))], 'latin-1')[0],
    [(0, '<ruby>Ka<rt>kara</rt></ruby>', ''), (0, '\r', ''), (9, 'Next', '')])
check('a ruby with no text before it in its event reads the event before\'s',
      kar._xf_texts([(0, ('meta', 5, '一瞬'.encode('cp932'))), (0, ('meta', 5, '[いっしゅん]'.encode('cp932')))],
                    'cp932')[0], [(0, '<ruby>一瞬<rt>いっしゅん</rt></ruby>', '')])
check('plain lyrics are not XF', kar.words_kind(smf(0, 96, [chunk(b'MTrk', [(0, meta(0x05, 'La'))])])), 'lyrics')

print('writing lyric events')
song = [{'start': 1.0, 'end': 2.0, 'text': '{\\k50}Hel{\\k50}lo ', 'kar': {'paragraph': True}},
        {'start': 2.0, 'end': 3.0, 'text': '{\\k100}world'},
        {'start': 4.0, 'end': 5.0, 'text': '{\\k50}A{\\k50}gain', 'kar': {'paragraph': True}}]
data = kar.write(song, {'title': 'Hello'}, lyrics=True)
kind, division, tracks = kar.parse_smf(data)
check('a type 0 file of one track named after the song, the tempo, no KAR text', (
    kind, division, len(tracks), [e[2] for _t, e in tracks[0] if e[1] == 0x03], lyric_events(data, 0x01)),
    (0, 480, 1, [b'Hello'], []))
check('a syllable an event, CR before a line, LF before a paragraph, at their ticks', lyric_events(data), [
    (960, b'Hel'), (1440, b'lo '), (1920, b'\r'), (1920, b'world'), (3840, b'\n'), (3840, b'A'), (4320, b'gain')])
check('read back as written', kar.parse(data)[0], [
    {'start': 1.0, 'end': 2.0, 'text': '{\\k50}Hel{\\k50}lo', 'kar': {'paragraph': True}},
    {'start': 2.0, 'end': 4.0, 'text': '{\\k200}world'},
    {'start': 4.0, 'end': 6.5, 'text': '{\\k50}A{\\k200}gain', 'kar': {'paragraph': True}}])
kept = kar.write(kar.parse(XF)[0], kar.parse(XF)[1], source=XF, lyrics=True)
kind, division, tracks = kar.parse_smf(kept)
check('an XF song saved as lyrics: its tracks kept, the words a track of their own, its header cue gone', (
    kind, division, len(tracks), [e[2] for _t, e in tracks[2] if e[1] == 0x03], lyric_events(kept, 0x07),
    b'XFKM' in kept), (1, 480, 3, [b'Words'], [], False))
check('in UTF-8, the offset as read', plain(kar.parse(kept)[0])[0], (0.25, 2.0, '宵待草の'))
check('a type 0 file: the words go back in its one track', (
    kar.parse_smf(kar.write(kar.parse(INTRACK)[0], {}, source=INTRACK, lyrics=True))[0],
    len(kar.parse_smf(kar.write(kar.parse(INTRACK)[0], {}, source=INTRACK, lyrics=True))[2])), (0, 1))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'xf.mid').write_bytes(XF)
session.SUBTITLE = Subtitle({'segments': []})
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'xf.mid'))
check('an XF .mid opens as MIDI lyrics, its title kept', (fmt, len(opened), session.SUBTITLE.metadata['title']),
      ('MIDI', 3, '待宵草'))
session.SUBTITLE.segments = opened
file_io.save_file(str(folder / 'again.mid'), 'MIDI')
written = (folder / 'again.mid').read_bytes()
check('saved as lyric events with its music', (len(kar.parse_smf(written)[2]), kar.words_kind(written),
                                              plain(kar.parse(written)[0]) == plain(opened)), (3, 'lyrics', True))
(folder / 'type0.mid').write_bytes(TYPE0)
check('a KAR in a .mid stays KAR', file_io.process_subtitles_file(str(folder / 'type0.mid'))[1], 'KAR')
check('MIDI registered for .mid, after KAR, and a text format', (
    session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['MIDI']['extensions'], list(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS).index('MIDI')
    > list(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS).index('KAR'), 'MIDI' in export_options.TEXT_FORMATS),
    (['mid', 'midi'], True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
