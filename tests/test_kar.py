"""KAR: reading karaoke MIDI built byte by byte in the test (a Tune 1000
song: @ headers, "\\" and "/" breaks, a tempo change, a melody in running
status; a type 0 MIDI file with lyric events and CR/LF breaks; SMPTE
time), writing a song with the words alone and one keeping the music of
the file it came from, checking both with a MIDI reader of the test's
own (and FluidSynth, where it is installed), round trips, and the file.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, shutil, struct, subprocess
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, kar, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# Building MIDI by hand, apart from the module's writer.
def vlq(value):
    out = [value & 0x7F]
    while value > 0x7F:
        value >>= 7
        out.append(0x80 | (value & 0x7F))
    return bytes(reversed(out))


def track(events):
    """An MTrk for [(tick, raw event bytes)], ended."""
    out, last = b'', 0
    for tick, raw in events:
        out += vlq(tick - last) + raw
        last = tick
    out += b'\x00\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(out)) + out


def meta(kind, data):
    data = data.encode('cp1252') if isinstance(data, str) else data
    return bytes([0xFF, kind]) + vlq(len(data)) + data


def smf(kind, division, tracks):
    return b'MThd' + struct.pack('>IHHH', 6, kind, len(tracks), division) + b''.join(tracks)


# A song as Tune 1000 laid them out, 96 ticks a quarter: 120 a minute, then
# 240 from tick 384 (two seconds in).
TEMPO_TRACK = track([(0, meta(0x01, '@KMIDI KARAOKE FILE')), (0, meta(0x01, '@V0100')),
                     (0, meta(0x51, (500000).to_bytes(3, 'big'))), (384, meta(0x51, (250000).to_bytes(3, 'big')))])
WORDS_TRACK = track([(0, meta(0x03, 'Words')), (0, meta(0x01, '@LENGL')), (0, meta(0x01, '@TMy Song')),
                     (0, meta(0x01, '@TThe Artist')), (0, meta(0x01, '@IMade in a test')),
                     (96, meta(0x01, '\\Hel')), (144, meta(0x01, 'lo ')), (192, meta(0x01, 'world')),
                     (288, meta(0x01, '/Sec')), (336, meta(0x01, 'ond')),
                     (480, meta(0x01, '\\Third ')), (1440, meta(0x01, 'line'))])
# The melody: note on, then running status (note on, velocity 0 for off).
MELODY = [(96, b'\x90\x3c\x64'), (144, b'\x3c\x00'), (144, b'\x3e\x64'), (192, b'\x3e\x00'), (192, b'\xc0\x05'),
          (200, b'\x90\x40\x64'), (288, b'\x40\x00')]
MELODY_TRACK = track([(0, meta(0x03, 'Melody'))] + MELODY)
SONG = smf(1, 96, [TEMPO_TRACK, WORDS_TRACK, MELODY_TRACK])

print('reading')
segments, metadata = kar.parse(SONG)
check('a line a subtitle, syllables as karaoke codes, the tempo map, a long gap a pause, paragraphs kept',
      segments, [
          {'start': 0.5, 'end': 1.5, 'text': '{\\k25}Hel{\\k25}lo {\\k50}world', 'kar': {'paragraph': True}},
          {'start': 1.5, 'end': 2.25, 'text': '{\\k25}Sec{\\k50}ond'},
          {'start': 2.25, 'end': 6.75, 'text': '{\\k200}Third {\\k50}{\\k200}line', 'kar': {'paragraph': True}}])
check('the headers: title and artist, the language and information kept', metadata, {
    'title': 'My Song', 'artist': 'The Artist',
    'kar': {'language': 'ENGL', 'titles': ['My Song', 'The Artist'], 'info': ['Made in a test']}})
LYRICS = smf(0, 96, [track([(0, meta(0x05, 'Twin')), (0, b'\x90\x3c\x64'), (48, meta(0x05, 'kle\r')), (48, b'\x3c\x00'),
                            (96, meta(0x05, 'Lit')), (144, meta(0x05, 'tle\n')), (192, meta(0x05, 'Star'))])])
check('a type 0 MIDI file: lyric events, a CR ending a line, an LF a paragraph', kar.parse(LYRICS)[0], [
    {'start': 0.0, 'end': 0.5, 'text': '{\\k25}Twin{\\k25}kle', 'kar': {'paragraph': True}},
    {'start': 0.5, 'end': 1.0, 'text': '{\\k25}Lit{\\k25}tle'},
    {'start': 1.0, 'end': 3.0, 'text': '{\\k200}Star', 'kar': {'paragraph': True}}])
SMPTE = smf(0, (0xE7 << 8) | 40, [track([(1500, meta(0x05, 'At one and a half'))])])
check('SMPTE time: 25 frames of 40 ticks', kar.parse(SMPTE)[0][0]['start'], 1.5)
check('UTF-8 words read as such, Windows-1252 otherwise', (
    kar.parse(smf(0, 96, [track([(0, meta(0x05, 'Canção'.encode('utf-8')))])]))[0][0]['text'],
    kar.parse(smf(0, 96, [track([(0, meta(0x05, 'Canção'.encode('cp1252')))])]))[0][0]['text']),
    ('{\\k200}Canção', '{\\k200}Canção'))
for name, data, start in (('not MIDI: refused', b'RIFF\x00\x00\x00\x00WAVE', 'Not a MIDI file'),
                          ('MIDI without words: refused', smf(1, 96, [MELODY_TRACK]), 'No words in this MIDI file')):
    try:
        kar.parse(data)
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)


# A reader of the test's own, to check what is written.
def read_smf(data):
    """(type, division, [[(tick, kind, bytes)]]) checking every length."""
    assert data[:4] == b'MThd' and struct.unpack('>I', data[4:8])[0] == 6
    kind, count, division = struct.unpack('>HHH', data[8:14])
    tracks, at = [], 14
    while at < len(data):
        assert data[at:at + 4] == b'MTrk', data[at:at + 4]
        size = struct.unpack('>I', data[at + 4:at + 8])[0]
        body, at = data[at + 8:at + 8 + size], at + 8 + size
        events, i, tick, running = [], 0, 0, None
        while i < len(body):
            delta = 0
            while True:
                byte = body[i]
                i += 1
                delta = (delta << 7) | (byte & 0x7F)
                if byte < 0x80:
                    break
            tick += delta
            if body[i] == 0xFF:
                kind_, i = body[i + 1], i + 2
                length = 0
                while True:
                    byte = body[i]
                    i += 1
                    length = (length << 7) | (byte & 0x7F)
                    if byte < 0x80:
                        break
                events.append((tick, ('meta', kind_), body[i:i + length]))
                i += length
            else:
                if body[i] & 0x80:
                    running, i = body[i], i + 1
                size_ = 1 if running & 0xF0 in (0xC0, 0xD0) else 2
                events.append((tick, ('midi', running), body[i:i + size_]))
                i += size_
        assert events[-1][1:] == (('meta', 0x2F), b''), 'a track not ended'
        assert sum(1 for e in events if e[1] == ('meta', 0x2F)) == 1
        tracks.append(events)
    assert len(tracks) == count, (len(tracks), count)
    return kind, division, tracks


def texts(events, kind=0x01):
    return [(tick, raw.decode('cp1252')) for tick, k, raw in events if k == ('meta', kind)]


print('writing')
plain = [{'start': 1.0, 'end': 2.0, 'text': 'Oh {\\k50}Hel{\\k30}lo {\\k20}{\\k60}world'},
         {'start': 2.5, 'end': 3.0, 'text': '<i>No codes</i>'},
         {'start': 5.0, 'end': 6.0, 'text': 'After a gap\nand a second line'}]
data = kar.write(plain, {'title': 'Title', 'artist': 'Singer'})
kind, division, tracks = read_smf(data)
check('type 1, 480 a quarter, a tempo track and a words track, each ended once', (kind, division, len(tracks)), (1, 480, 2))
check('the first track: @KMIDI, 120 a minute, 4/4', (texts(tracks[0])[0][1], [raw for _t, k, raw in tracks[0] if k == ('meta', 0x51)],
      [raw for _t, k, raw in tracks[0] if k == ('meta', 0x58)]), ('@KMIDI KARAOKE FILE', [b'\x07\xa1\x20'], [b'\x04\x02\x18\x08']))
check('the words track: its name, the headers, a syllable an event, "\\" for a paragraph and "/" for a line', (
    texts(tracks[1], 0x03), texts(tracks[1])), ([(0, 'Words')], [
        (0, '@LENGL'), (0, '@TTitle'), (0, '@TSinger'),
        (960, '\\Oh '), (960, 'Hel'), (1440, 'lo '), (1920, 'world'),
        (2400, '/No codes'),
        (4800, '\\After a gap'), (5177, '/and a second line')]))
check('no notes, no lyric events doubling the words', [e for track_ in tracks for e in track_ if e[1][0] == 'midi' or e[1] == ('meta', 0x05)], [])
sung = read_smf(kar.write([{'start': 0, 'end': 1, 'text': '歌'}]))[2][1]
check('words that Windows-1252 has not: UTF-8', [raw.decode('utf-8') for _t, k, raw in sung if k == ('meta', 0x01)][-1], '\\歌')

kept = kar.write(segments, metadata, source=SONG)
kind, division, tracks = read_smf(kept)
check("the music kept: the song's division and tracks, the words track replaced by one after them",
      (division, len(tracks), texts(tracks[1], 0x03), texts(tracks[2], 0x03)), (96, 3, [(0, 'Melody')], [(0, 'Words')]))
melody = [(t, raw) for t, k, raw in tracks[1] if k[0] == 'midi']
check('the melody note for note', melody, [(96, b'\x3c\x64'), (144, b'\x3c\x00'), (144, b'\x3e\x64'), (192, b'\x3e\x00'),
                                          (192, b'\x05'), (200, b'\x40\x64'), (288, b'\x40\x00')])
check('the tempo map kept, the old headers out, @KMIDI once', (
    [(t, raw) for t, k, raw in tracks[0] if k == ('meta', 0x51)], [text for _t, text in texts(tracks[0])]),
    ([(0, (500000).to_bytes(3, 'big')), (384, (250000).to_bytes(3, 'big'))], ['@KMIDI KARAOKE FILE']))
check('the words at their ticks in that tempo map', [(t, text) for t, text in texts(tracks[2]) if not text.startswith('@')],
      [(96, '\\Hel'), (144, 'lo '), (192, 'world'), (288, '/Sec'), (336, 'ond'), (480, '\\Third '), (1440, 'line')])
check('read back the same', kar.parse(kept), (segments, metadata))
from_lyrics = read_smf(kar.write(kar.parse(LYRICS)[0], {}, source=LYRICS))[2]
check('a type 0 lyric file: its track kept without its lyric events, the words a track of their own',
      (len(from_lyrics), [k for t, k, raw in from_lyrics[0] if k == ('meta', 0x05)],
       [t for t, k, raw in from_lyrics[0] if k[0] == 'midi'], texts(from_lyrics[1])),
      (2, [], [0, 48], [(0, '@LENGL'), (0, '\\Twin'), (48, 'kle'), (96, '/Lit'), (144, 'tle'), (192, '\\Star')]))

print('round trips')
trip = [
    {'start': 0.5, 'end': 1.5, 'text': '{\\k25}Hel{\\k25}lo {\\k50}world', 'kar': {'paragraph': True}},
    {'start': 1.5, 'end': 3.0, 'text': '{\\k33}Sec{\\k117}ond'},
    {'start': 3.0, 'end': 8.0, 'text': '{\\k200}Long {\\k100}{\\k200}pause', 'kar': {'paragraph': True}},
    {'start': 8.0, 'end': 10.0, 'text': '{\\k200}Canção'},
]
check('read back as written', kar.parse(kar.write(trip, {'title': 'T'}))[0], trip)
check('written again as it was read', kar.write(*kar.parse(kar.write(trip, {'title': 'T'}))), kar.write(trip, {'title': 'T', 'kar': {
    'language': 'ENGL', 'titles': ['T'], 'info': []}}))
check('what it loses: where a syllable ends (to the next, or HOLD), a subtitle\'s lines each sung in turn',
      [(s['start'], s['end'], s['text'], 'kar' in s) for s in kar.parse(kar.write(plain))[0]], [
          (1.0, 2.5, 'Oh {\\k50}Hel{\\k50}lo {\\k50}world', True),
          (2.5, 4.5, '{\\k200}No codes', False),
          (5.0, 5.393, '{\\k39}After a gap', True),
          (5.393, 7.393, '{\\k200}and a second line', False)])

fluidsynth = shutil.which('fluidsynth')
font = next((path for path in ('/usr/share/sounds/sf2/FluidR3_GM.sf2', '/usr/share/sounds/sf2/default-GM.sf2') if os.path.isfile(path)), None)
if fluidsynth and font:
    folder = Path(tempfile.mkdtemp())
    (folder / 'song.kar').write_bytes(kept)
    run = subprocess.run([fluidsynth, '-ni', '-F', str(folder / 'song.wav'), '-r', '22050', font, str(folder / 'song.kar')],
                         capture_output=True, text=True, timeout=60)
    check('FluidSynth plays the song kept, without a complaint', (run.returncode, 'error' in (run.stdout + run.stderr).lower(),
                                                                  (folder / 'song.wav').stat().st_size > 44), (0, False, True))
else:
    print('  [skip] FluidSynth is not installed')

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'song.kar').write_bytes(SONG)
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'song.kar'))
check('opens as KAR: its title, the source kept', (fmt, len(opened), session.SUBTITLE['metadata'].get('title'),
                                                   session.FORMAT['options'].get('kar_source')),
      ('KAR', 3, 'My Song', str(folder / 'song.kar')))
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.kar'), 'KAR')
again = read_smf((folder / 'again.kar').read_bytes())
check('saved with its music', (len(again[2]), [t for t, k, raw in again[2][1] if k[0] == 'midi'][:3]), (3, [96, 144, 144]))
(folder / 'song.kar').unlink()
file_io.save_file(str(folder / 'alone.kar'), 'KAR')
check('the source gone: the words alone', len(read_smf((folder / 'alone.kar').read_bytes())[2]), 2)
(folder / 'lyrics.mid').write_bytes(LYRICS)
opened, fmt = file_io.process_subtitles_file(str(folder / 'lyrics.mid'))
check("a .mid's lyrics open too", (fmt, len(opened)), ('KAR', 3))
(folder / 'no words.mid').write_bytes(smf(1, 96, [MELODY_TRACK]))
try:
    file_io.process_subtitles_file(str(folder / 'no words.mid'))
    check('a .mid without words: says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('a .mid without words: says so', str(error).startswith('No words'), True)
check('the extensions', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['KAR']['extensions'], ['kar', 'mid', 'midi'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
