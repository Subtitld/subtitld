"""UltraStar (.txt): the song files of UltraStar, Vocaluxe, Performous and
the other karaoke games scored on how well each syllable is sung.

A song file is headers, then notes. A header is "#KEY:value": #TITLE and
#ARTIST, the audio (#MP3, or #AUDIO in format 1.1), #VIDEO, #COVER and
#BACKGROUND, #LANGUAGE, #GENRE, #YEAR, and the two the times hang on:
#BPM and #GAP. Times are beats: beat b is GAP milliseconds into the
song plus b * 60 / (BPM * 4) seconds (the BPM counts quarter beats). A
comma may stand for the decimal point in both.

A note is ": start length pitch syllable", in beats, the pitch in
semitones from middle C, the syllable after one space and carrying its
own spaces ("Hel", "lo ", "world"). Its first character says what kind
of note it is: ":" sung, "*" golden (worth more), "F" freestyle (not
scored), "R" rap and "G" golden rap (the words scored, not the pitch).
"- 120" ends a line of lyrics at beat 120; with #RELATIVE:yes the beats
in each line count from the line's start, and the break's second number,
where there is one, says where the next line's count starts. "E" ends the
song. A duet has "P1" and "P2" before each singer's lines.

Reading gives a subtitle a line, from its first note's start to its last
note's end, or to the line break when that comes later and before the
next line; each note a syllable as karaoke codes (modules.karaoke), a
pause between notes as a pause. Each subtitle keeps the kind and pitch of
its notes in its 'ultrastar' key, {'notes': [[kind, pitch], ...]}, and in
a duet the singer ('player': 'P1' or 'P2'), so a song written back sings
as it did. #TITLE and #ARTIST are the subtitles' metadata; the other
headers are the file's settings, in order, written back as they were.

Writing gives the headers, then each subtitle's syllables as notes, in
beats at the file's own BPM and GAP when it was opened from one (so no
note moves), else at `BPM` from the first subtitle's start. A syllable
takes the kind and pitch of the note in its place; one past the notes a
subtitle kept is freestyle, at the pitch before it, as is every word of a
subtitle with no karaoke codes, each word given a share of its time by
its length. Absolute beats are
always written: #RELATIVE is not.

What does not come back: the file's encoding (it is written in UTF-8,
and an #ENCODING header says so), a line break's beat where it is not
after the line's last note and before the next line, and notes whose
syllable is empty.
"""

import re

from subtitld.modules import karaoke, markup
from subtitld.modules.srt import decode

# The BPM a song with none of its own is written at: a beat is 25 ms.
BPM = 600.0
KINDS = (':', '*', 'F', 'R', 'G')

_HEADER = re.compile(r'^\s*#\s*([A-Za-z0-9_-]+)\s*:(.*)$')
_NOTE = re.compile(r'^([:*FRG])[ \t]+(-?\d+)[ \t]+(-?\d+)[ \t]+(-?\d+)(?:[ \t](.*))?$')
_BREAK = re.compile(r'^-[ \t]*(-?\d+)(?:[ \t]+(-?\d+))?\s*$')
_PLAYER = re.compile(r'^P[ \t]*(\d)\s*$')


def _number(value):
    try:
        return float(str(value).strip().replace(',', '.'))
    except ValueError:
        return None


def kind(text):
    """Whether `text` is an UltraStar song: a #BPM header and notes."""
    lines = str(text or '').lstrip('﻿').split('\n')
    headers = {match.group(1).upper() for match in map(_HEADER.match, lines[:80]) if match}
    return 'BPM' in headers and any(_NOTE.match(line.rstrip('\r')) for line in lines)


def parse(text):
    """(subtitles, metadata, headers) in an UltraStar song: [{'start',
    'end', 'text', 'ultrastar'}] by start; {'title', 'artist'}; and the
    other headers, [[key, value]] in order."""
    lines = str(text or '').lstrip('﻿').split('\n')
    metadata, headers = {}, []
    bpm, gap, relative = None, 0.0, False
    for line in lines:
        header = _HEADER.match(line)
        if not header:
            if line.strip() and not line.lstrip().startswith('#'):
                break
            continue
        key, value = header.group(1).upper(), header.group(2).strip()
        if key == 'TITLE':
            metadata['title'] = value
        elif key == 'ARTIST':
            metadata['artist'] = value
        else:
            headers.append([key, value])
        if key == 'BPM':
            bpm = _number(value)
        elif key == 'GAP':
            gap = (_number(value) or 0.0) / 1000
        elif key == 'RELATIVE':
            relative = value.lower() in ('yes', 'true', '1')
    if not bpm or bpm <= 0:
        raise ValueError('Not an UltraStar song: it has no #BPM.')

    def seconds(beat):
        return round(gap + beat * 60 / (bpm * 4), 3)

    # [(player, [(start, length, kind, pitch, syllable)], break beat or None)]
    lines_of_notes = []
    player, notes, base = None, [], 0
    for line in lines:
        line = line.rstrip('\r')
        note = _NOTE.match(line)
        if note:
            notes.append((base + int(note.group(2)), int(note.group(3)), note.group(1),
                          int(note.group(4)), note.group(5) or ''))
            continue
        stripped = line.strip()
        line_break = _BREAK.match(stripped)
        singer = _PLAYER.match(stripped)
        if line_break or singer or stripped.upper() == 'E':
            if notes:
                lines_of_notes.append((player, notes, base + int(line_break.group(1)) if line_break else None))
            notes = []
            if line_break and relative:
                base += int(line_break.group(2) if line_break.group(2) is not None else line_break.group(1))
            if singer:
                player, base = 'P' + singer.group(1), 0
            if stripped.upper() == 'E':
                break
    if notes:
        lines_of_notes.append((player, notes, None))

    segments = []
    for index, (singer, notes, line_break) in enumerate(lines_of_notes):
        start = seconds(notes[0][0])
        last = seconds(max(beat + length for beat, length, _kind, _pitch, _syllable in notes))
        end = last
        if line_break is not None:
            following = next((seconds(other[1][0][0]) for other in lines_of_notes[index + 1:]
                              if other[0] == singer), None)
            ends = seconds(line_break)
            if ends > last and (following is None or ends <= following):
                end = ends
        pieces = [(seconds(beat), seconds(beat + length), syllable) for beat, length, _kind, _pitch, syllable in notes]
        segment = {'start': start, 'end': end, 'text': karaoke.text_from(start, pieces),
                   'ultrastar': {'notes': [[note_kind, pitch] for _beat, _length, note_kind, pitch, _syllable in notes]}}
        if singer:
            segment['ultrastar']['player'] = singer
        segments.append(segment)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, metadata, headers


def read(path):
    """(subtitles, metadata, headers) in the UltraStar song at `path`."""
    with open(path, 'rb') as song_file:
        text = decode(song_file.read())
    if not kind(text):
        raise ValueError('Not an UltraStar song: it needs a #BPM header and notes.')
    return parse(text)


def _syllables(segment):
    """[(start, end, syllable)] for a subtitle: its karaoke codes' syllables
    with text, else its words, each a share of its time by its length."""
    start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
    text = str(segment.get('text') or '')
    timed = karaoke.timed(text, start)
    if timed is not None:
        return [(begin, finish, ' '.join(syllable.split('\n'))) for begin, finish, syllable in timed if syllable]
    words = ' '.join(line.strip() for line in markup.plain(text).split('\n') if line.strip()).split(' ')
    words = [word for word in words if word]
    total = sum(len(word) + 1 for word in words)
    out, at = [], start
    for index, word in enumerate(words):
        share = (end - start) * (len(word) + 1) / total
        out.append((at, at + share, word + (' ' if index + 1 < len(words) else '')))
        at += share
    return out


def write(segments, metadata=None, headers=None, audio=''):
    """`segments` as an UltraStar song: the `headers` a song was opened with
    ([[key, value]]) or new ones naming `audio` as its #MP3 and #VIDEO."""
    headers = [[str(key).upper(), str(value)] for key, value in (headers or [])
               if str(key).upper() not in ('TITLE', 'ARTIST', 'RELATIVE')]
    known = {key for key, _value in headers}
    bpm = _number(dict(headers).get('BPM')) if 'BPM' in known else None
    gap = (_number(dict(headers).get('GAP')) or 0.0) / 1000 if 'GAP' in known else None
    ordered = sorted((segment for segment in segments if _syllables(segment)),
                     key=lambda segment: float(segment.get('start', 0)))
    if not bpm or bpm <= 0:
        bpm = BPM
        headers = [[key, value] for key, value in headers if key != 'BPM'] + [['BPM', f'{BPM:g}']]
    first = min((_syllables(segment)[0][0] for segment in ordered), default=0.0)
    if gap is None or gap > first + 1e-6:
        gap = round(first, 3)
        headers = [[key, value] for key, value in headers if key != 'GAP'] + [['GAP', f'{gap * 1000:g}']]
    if audio and not known & {'MP3', 'AUDIO'}:
        headers.append(['MP3', audio])
        if 'VIDEO' not in known:
            headers.append(['VIDEO', audio])
    headers = [['ENCODING', 'UTF8'] if key == 'ENCODING' else [key, value] for key, value in headers]

    def beat(seconds):
        return round((seconds - gap) * bpm * 4 / 60)

    out = []
    for key in ('title', 'artist'):
        out.append(f'#{key.upper()}:{" ".join(str((metadata or {}).get(key) or "").split())}')
    out.extend(f'#{key}:{value}' for key, value in headers)

    singers = []
    for segment in ordered:
        singer = (segment.get('ultrastar') or {}).get('player') if isinstance(segment.get('ultrastar'), dict) else None
        if singer not in singers:
            singers.append(singer)
    duet = any(singers)
    for singer in singers:
        if duet:
            out.append(singer or 'P1')
        lines = [segment for segment in ordered
                 if ((segment.get('ultrastar') or {}).get('player') if isinstance(segment.get('ultrastar'), dict)
                     else None) == singer]
        for index, segment in enumerate(lines):
            syllables = _syllables(segment)
            kept = (segment.get('ultrastar') or {}).get('notes') if isinstance(segment.get('ultrastar'), dict) else None
            kept = [note for note in kept or [] if isinstance(note, (list, tuple)) and len(note) == 2
                    and note[0] in KINDS and isinstance(note[1], int)]
            pitch = 0
            last = None
            for number, (begin, finish, syllable) in enumerate(syllables):
                if number < len(kept):
                    note_kind, pitch = kept[number]
                else:
                    note_kind = 'F'
                start = beat(begin) if last is None else max(last, beat(begin))
                length = max(1, beat(finish) - start)
                out.append(f'{note_kind} {start} {length} {pitch} {syllable}')
                last = start + length
            if index + 1 < len(lines):
                following = beat(_syllables(lines[index + 1])[0][0])
                out.append(f'- {max(last, min(beat(float(segment.get("end", 0))), following))}')
    out.append('E')
    return '\n'.join(out) + '\n'


def save(path, segments, metadata=None, headers=None, audio=''):
    """Write `segments` to `path` as an UltraStar song, in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as song_file:
        song_file.write(write(segments, metadata, headers, audio))
