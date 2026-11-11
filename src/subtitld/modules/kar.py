"""KAR (.kar): the karaoke songs Tune 1000 made, Standard MIDI Files with
the words sung in them; and the lyrics of any MIDI file (.mid).

A Standard MIDI File is a header chunk, "MThd": its type (0, one track;
1, tracks played together), how many tracks, and its division, the ticks
a quarter note counts (or, with the top bit set, frames a second and
ticks a frame, SMPTE's). Then a chunk a track, "MTrk": events one after
another, each after a delta time in ticks (a variable-length number, 7
bits a byte, the top bit for "more"). An event is a channel message (a
status byte, or the last one again, "running status"), a system exclusive
message (F0 or F7, a length, its bytes) or a meta event (FF, its type, a
length, its bytes). The metas here: 01 text, 03 a track's name, 05 a
lyric, 51 a tempo (microseconds a quarter note, 500000 until one says
otherwise), 2F the end of the track.

A KAR puts the words in a track of their own (named "Words"), as text
events, one a syllable at the moment it is sung, the first ones "@"
headers: in the first track "@KMIDI KARAOKE FILE"; then "@L" the
language, "@T" the title, the artist and more, "@I" information, "@V" the
version. In the words, "/" starts a new line and "\\" a new paragraph,
which clears the screen. Other MIDI files with words carry them as lyric
events, in a track with the music, where "/" and "\\", or a carriage
return and a line feed, do the same.

Reading gives a subtitle a line: from its first syllable, each syllable a
karaoke code (modules.karaoke) until the next one starts, but no longer
than `HOLD` (MIDI says when a syllable is sung, not for how long: a
longer gap is a pause), the last one `HOLD` at most, to before the next
line. The words track, else the track with the most lyric events, is
read; times follow the tempo map. The first "@T" is the title, the second
the artist, as the subtitles' metadata; the language, the other headers
and a line that started a paragraph are kept ('kar' on the metadata and
on the subtitle) to be written back. The text is UTF-8 when it decodes
as such, else Windows-1252, what the players read.

Writing gives a type 1 file, 480 ticks a quarter note at 120 a minute: a
first track with "@KMIDI KARAOKE FILE", the tempo and 4/4, and the Words
track: the headers, then a text event a syllable, "\\" before the first
line of a paragraph, "/" before each other. Paragraphs are the ones the
file opened had; else one starts after a gap of `PARAGRAPH_GAP` or more,
or after two lines. A subtitle without karaoke codes is a syllable a
line, each sung in turn for its share of the letters; text before a
subtitle's first code is a syllable sung at its start that lasts no time,
which is how it reads back. In Windows-1252 when the words fit it, else
UTF-8.

A song opened from a MIDI file keeps its music: given the file's bytes as
`source`, writing keeps its division, tempo map and every track (the words
and "@" headers taken out of them), and puts the words track after them,
so the song plays as it did with the words edited.

What is lost on the way: formatting and places (KAR has none); a
subtitle's lines, each a subtitle of its own when read again; where a
syllable or a line ends, which reads back as the next one's start or
`HOLD` after its own; times to the tick (about a millisecond).
"""

import struct

from subtitld.modules import karaoke, markup

# The longest a syllable is held when the next is later, and the last of a
# line before the next line: MIDI does not say when a syllable ends.
HOLD = 2.0
# On writing subtitles with no paragraphs of their own, a gap this long or
# longer starts one; so does a third line.
PARAGRAPH_GAP = 1.0
PARAGRAPH_LINES = 2
PPQ = 480
TEMPO = 500000                                   # microseconds a quarter note: 120 a minute
KMIDI = '@KMIDI KARAOKE FILE'

_TEXT, _NAME, _LYRIC, _END, _SET_TEMPO = 0x01, 0x03, 0x05, 0x2F, 0x51


def is_midi(data):
    """Whether `data` begins as a Standard MIDI File (or a RIFF RMID
    holding one)."""
    data = bytes(data[:32])
    return data.startswith(b'MThd') or (data.startswith(b'RIFF') and data[8:12] == b'RMID')


def _number(data, position):
    """(value, position after it) for a variable-length number."""
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise ValueError('Not a MIDI file: a track ends inside a number')
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, position


def _vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _track(data):
    """[(tick, event)] for a track's bytes; an event is ('meta', type,
    bytes), ('sysex', status, bytes) or ('midi', status, bytes)."""
    events = []
    position, tick, running = 0, 0, None
    while position < len(data):
        delta, position = _number(data, position)
        tick += delta
        if position >= len(data):
            break
        status = data[position]
        if status == 0xFF:
            kind = data[position + 1] if position + 1 < len(data) else _END
            length, position = _number(data, position + 2)
            events.append((tick, ('meta', kind, bytes(data[position:position + length]))))
            position += length
            if kind == _END:
                break
            continue
        if status in (0xF0, 0xF7):
            length, position = _number(data, position + 1)
            events.append((tick, ('sysex', status, bytes(data[position:position + length]))))
            position += length
            continue
        if status & 0x80:
            running = status
            position += 1
        elif running is None:
            raise ValueError('Not a MIDI file: data before any status')
        # Program change and channel pressure carry one byte; the rest two.
        size = 1 if running & 0xF0 in (0xC0, 0xD0) else 2
        events.append((tick, ('midi', running, bytes(data[position:position + size]))))
        position += size
    return events


def parse_smf(data):
    """(type, division, [tracks]) for a Standard MIDI File's bytes. Raises
    ValueError for bytes that are not one."""
    data = bytes(data)
    if data.startswith(b'RIFF') and data[8:12] == b'RMID':
        # Microsoft's RMID wraps the file in a "data" chunk.
        at = data.find(b'MThd')
        data = data[at:] if at >= 0 else b''
    if not data.startswith(b'MThd') or len(data) < 14:
        raise ValueError('Not a MIDI file: it does not begin with MThd')
    length = struct.unpack('>I', data[4:8])[0]
    kind, count, division = struct.unpack('>HHH', data[8:14])
    tracks = []
    position = 8 + length
    while position + 8 <= len(data) and len(tracks) < count:
        name, size = data[position:position + 4], struct.unpack('>I', data[position + 4:position + 8])[0]
        body = data[position + 8:position + 8 + size]
        position += 8 + size
        if name == b'MTrk':
            tracks.append(_track(body))
    return kind, division, tracks


class _Clock:
    """Ticks to seconds and back, by the division and the tempo map."""

    def __init__(self, division, tracks):
        self.smpte = bool(division & 0x8000)
        if self.smpte:
            frames = 256 - (division >> 8)
            self.per_second = (29.97 if frames == 29 else frames) * (division & 0xFF)
            return
        self.ppq = division or PPQ
        changes = sorted((tick, int.from_bytes(event[2][:3], 'big')) for track in tracks for tick, event in track
                         if event[0] == 'meta' and event[1] == _SET_TEMPO and len(event[2]) >= 3)
        self.map = [(0, 0.0, TEMPO)]               # (tick, seconds at it, tempo from it)
        for tick, tempo in changes:
            if not tempo:
                continue
            at, seconds, before = self.map[-1]
            if tick == at:
                self.map[-1] = (at, seconds, tempo)
            else:
                self.map.append((tick, seconds + (tick - at) * before / 1e6 / self.ppq, tempo))

    def seconds(self, tick):
        if self.smpte:
            return tick / self.per_second
        at, seconds, tempo = next(entry for entry in reversed(self.map) if entry[0] <= tick)
        return seconds + (tick - at) * tempo / 1e6 / self.ppq

    def tick(self, seconds):
        if self.smpte:
            return int(round(seconds * self.per_second))
        at, start, tempo = next(entry for entry in reversed(self.map) if entry[1] <= seconds)
        return int(round(at + (seconds - start) * 1e6 * self.ppq / tempo))


def _decode(raw, utf8):
    return raw.decode('utf-8' if utf8 else 'cp1252', 'replace')


def _words(tracks):
    """(index of the track with the words, the meta type they are in): a
    KAR's words track (named "Words", or with the @L, @T or @I headers),
    else the track with the most lyric events, else the one with the most
    text events that break lines as KAR does."""
    def metas(track, kind):
        return [event[2] for _tick, event in track if event[0] == 'meta' and event[1] == kind]

    def best(scores):
        return scores.index(max(scores)) if scores and max(scores) else None

    words = [sum(1 for text in metas(track, _TEXT) if not text.startswith(b'@'))
             if any(name.strip().lower() == b'words' for name in metas(track, _NAME))
             or any(text[:2] in (b'@L', b'@T', b'@I') for text in metas(track, _TEXT)) else 0
             for track in tracks]
    if best(words) is not None:
        return best(words), _TEXT
    lyrics = [len(metas(track, _LYRIC)) for track in tracks]
    if best(lyrics) is not None:
        return best(lyrics), _LYRIC
    marked = [sum(1 for text in metas(track, _TEXT) if text[:1] in (b'/', b'\\')) for track in tracks]
    if best(marked) is not None:
        return best(marked), _TEXT
    return None, None


def _syllables(events, clock, utf8):
    """[[(seconds, text, paragraph)]] lines for a track's words."""
    lines = []
    paragraph = True
    for tick, raw in events:
        text = _decode(raw, utf8).replace('\r\n', '\r')
        at = round(clock.seconds(tick), 3)
        # A syllable may carry a break before it ("/", "\\", CR or LF), and
        # some files put the break after one ("lo\r"), for the next.
        while text and text[0] in '/\\\r\n':
            paragraph = paragraph or text[0] in '\\\n'
            lines.append([])
            text = text[1:]
        ending = ''
        while text and text[-1] in '\r\n':
            ending = text[-1] + ending
            text = text[:-1]
        if text:
            if not lines:
                lines.append([])
            lines[-1].append((at, text, paragraph))
            paragraph = False
        for char in ending:
            paragraph = paragraph or char == '\n'
            lines.append([])
    return [line for line in lines if line]


def parse(data):
    """(subtitles, metadata) in a KAR or MIDI file's bytes: [{'start',
    'end', 'text'}] a line, the syllables as karaoke codes, 'kar' on one
    that starts a paragraph; and {'title', 'artist', 'kar'}. Raises
    ValueError for a file that is not MIDI or has no words."""
    _kind, division, tracks = parse_smf(data)
    index, meta = _words(tracks)
    if index is None:
        raise ValueError('No words in this MIDI file: it has no karaoke text or lyric events')
    clock = _Clock(division, tracks)
    events = [(tick, event[2]) for tick, event in tracks[index] if event[0] == 'meta' and event[1] == meta]
    headers = [raw for raw in (event[2] for track in tracks for _tick, event in track
                               if event[0] == 'meta' and event[1] == _TEXT) if raw.startswith(b'@')]
    words = [(tick, raw) for tick, raw in events if not raw.startswith(b'@')]
    try:
        b''.join(raw for _tick, raw in words + [(0, raw) for raw in headers]).decode('utf-8')
        utf8 = True
    except UnicodeDecodeError:
        utf8 = False

    metadata, kept = {}, {'language': '', 'titles': [], 'info': []}
    for raw in headers:
        text = _decode(raw, utf8)
        code, value = text[1:2].upper(), text[2:].strip()
        if code == 'L':
            kept['language'] = value
        elif code == 'T':
            kept['titles'].append(value)
        elif code == 'I':
            kept['info'].append(value)
    if kept['titles']:
        metadata['title'] = kept['titles'][0]
    if len(kept['titles']) > 1:
        metadata['artist'] = kept['titles'][1]
    if any(kept.values()):
        metadata['kar'] = kept

    lines = _syllables(words, clock, utf8)
    segments = []
    for number, line in enumerate(lines):
        following = lines[number + 1][0][0] if number + 1 < len(lines) else None
        start = line[0][0]
        pieces = []
        for position, (at, text, _paragraph) in enumerate(line):
            after = line[position + 1][0] if position + 1 < len(line) else following
            end = at + HOLD if after is None else min(after, at + HOLD)
            if position == 0:
                text = text.lstrip()
            if position == len(line) - 1:
                text = text.rstrip()
            pieces.append((at, round(max(at, end), 3), text))
        # A first syllable sung no time, with the next at the same moment,
        # is text before the first code, as karaoke.text_from has it.
        lead = pieces.pop(0)[2] if len(pieces) > 1 and pieces[0][1] == pieces[0][0] == pieces[1][0] else ''
        text = lead + karaoke.text_from(start, pieces)
        if not markup.plain(text).strip():
            continue
        segment = {'start': start, 'end': pieces[-1][1], 'text': text}
        if line[0][2]:
            segment['kar'] = {'paragraph': True}
        segments.append(segment)
    return segments, metadata


def read(path):
    """(subtitles, metadata) in the KAR or MIDI file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as midi_file:
        return parse(midi_file.read())


# Writing.
def _meta(kind, data):
    return ('meta', kind, data)


def _chunk(events):
    """An MTrk chunk for [(tick, event)], in order, ended once."""
    out = bytearray()
    last = 0
    for tick, event in sorted(events, key=lambda item: item[0]):
        if event[0] == 'meta' and event[1] == _END:
            continue
        out += _vlq(max(0, tick - last))
        last = max(last, tick)
        if event[0] == 'meta':
            out += bytes([0xFF, event[1]]) + _vlq(len(event[2])) + event[2]
        elif event[0] == 'sysex':
            out += bytes([event[1]]) + _vlq(len(event[2])) + event[2]
        else:
            out += bytes([event[1]]) + event[2]
    out += b'\x00\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(out)) + bytes(out)


def _lines(segments):
    """[(start, [(seconds, text)], paragraph)] a line for the subtitles: a
    subtitle's lines each a line of its own, its syllables by its karaoke
    codes, or the line one syllable."""
    out = []
    previous_end = None
    in_paragraph = 0
    known = any(isinstance(segment.get('kar'), dict) for segment in segments)
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        # All of the text: a karaoke code may come first, where a place does.
        rest = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        pieces = karaoke.timed(rest, start)
        if not pieces:
            # No syllables: the lines one after the other, each for its
            # share of the letters, as a karaoke player shows them sung.
            parts = [part for part in markup.plain(rest).split('\n') if part.strip()]
            total = sum(len(part) for part in parts) or 1
            pieces, at = [], start
            for part in parts:
                pieces.append((round(at, 3), 0, part + '\n'))
                at += (end - start) * len(part) / total
        lines = [[]]
        for begin, _end, text in pieces:
            for number, part in enumerate(text.split('\n')):
                if number:
                    lines.append([])
                if part:
                    lines[-1].append((begin, part))
        lines = [line for line in lines if ''.join(text for _at, text in line).strip()]
        if not lines:
            continue
        if known:
            paragraph = bool((segment.get('kar') or {}).get('paragraph')) or not out
        else:
            paragraph = (not out or start - previous_end >= PARAGRAPH_GAP or in_paragraph >= PARAGRAPH_LINES)
        for number, line in enumerate(lines):
            first = paragraph and number == 0
            in_paragraph = 1 if first else in_paragraph + 1
            out.append((line[0][0], line, first))
        previous_end = max(start, float(segment.get('end', start)))
    return out


def write(segments, metadata=None, source=None):
    """KAR bytes for `segments`, with the title and artist of `metadata`
    (and the headers it kept from a KAR); keeping the music of `source`,
    the bytes of the MIDI file they were opened from, when given."""
    metadata = metadata or {}
    kept = metadata.get('kar') if isinstance(metadata.get('kar'), dict) else {}
    lines = _lines(segments)
    texts = [text for _start, line, _paragraph in lines for _at, text in line]
    titles = list(kept.get('titles') or [])
    for number, key in enumerate(('title', 'artist')):
        value = str(metadata.get(key) or '').strip()
        if value:
            titles[number:number + 1] = [value]
    headers = (['@LENGL' if not kept.get('language') else '@L' + kept['language']]
               + ['@T' + title for title in titles] + ['@I' + info for info in kept.get('info') or []])
    try:
        ''.join(texts + headers).encode('cp1252')
        encoding = 'cp1252'
    except UnicodeEncodeError:
        encoding = 'utf-8'

    if source:
        _kind, division, tracks = parse_smf(source)
        index, meta = _words(tracks)
        kept_tracks = []
        for number, track in enumerate(tracks):
            # The words and the headers are written anew, so they go.
            events = [(tick, event) for tick, event in track if not (
                event[0] == 'meta' and (event[1] == _LYRIC or (event[1] == _TEXT and (
                    event[2].startswith(b'@') or (number == index and meta == _TEXT)))))]
            if number == index and meta == _TEXT and not any(event[0] != 'meta' or event[1] not in (_NAME, _END)
                                                             for _tick, event in events):
                continue                            # the words track: replaced
            kept_tracks.append(events)
        if not kept_tracks:
            kept_tracks = [[]]
    else:
        division, tracks = PPQ, []
        kept_tracks = [[(0, _meta(_NAME, b'Tempo')), (0, _meta(_SET_TEMPO, TEMPO.to_bytes(3, 'big'))),
                        (0, _meta(0x58, bytes([4, 2, 24, 8])))]]
    clock = _Clock(division, tracks)
    kept_tracks[0] = [(0, _meta(_TEXT, KMIDI.encode('ascii')))] + kept_tracks[0]

    words = [(0, _meta(_NAME, b'Words'))] + [(0, _meta(_TEXT, header.encode(encoding, 'replace'))) for header in headers]
    for _start, line, paragraph in lines:
        for number, (at, text) in enumerate(line):
            if number == 0:
                text = ('\\' if paragraph else '/') + text.lstrip()
            words.append((clock.tick(at), _meta(_TEXT, text.encode(encoding, 'replace'))))
    chunks = [_chunk(track) for track in kept_tracks + [words]]
    return b'MThd' + struct.pack('>IHHH', 6, 1, len(chunks), division) + b''.join(chunks)


def save(path, segments, metadata=None, source=None):
    """Write `segments` to `path` as KAR; `metadata` and `source` as
    write() takes them."""
    data = write(segments, metadata, source)
    with open(path, 'wb') as kar_file:
        kar_file.write(data)
