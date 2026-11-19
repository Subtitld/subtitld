"""Transcripts as speech research and speech recognition keep them: Praat's
TextGrid, CHAT (CLAN's, TalkBank's) and the CTM of NIST's scoring tools
and Kaldi.

Praat TextGrid (.TextGrid) follows Praat's manual page "TextGrid file
formats" (Boersma): a text file whose data are the free-standing numbers,
texts in double quotes (a quote in one doubled) and flags (<exists>) in
it, everything else a comment, as is the rest of a line after "!". The
long form ("Save as text file") and the short one ("Save as short text
file") are the same data with more or fewer comments, and both read the
same here; UTF-8, or UTF-16 with a byte order mark, as the page asks.

    File type = "ooTextFile"
    Object class = "TextGrid"
    xmin = 0
    xmax = 2.3
    tiers? <exists>
    size = 2
    item []:
        item [1]:
            class = "IntervalTier"
            name = "Mary"
            xmin = 0
            xmax = 2.3
            intervals: size = 1
            intervals [1]:
                xmin = 0
                xmax = 2.3
                text = "Hello"
        item [2]:
            class = "TextTier"
            ...

An interval tier is a run of intervals that meet end to start; a point
tier (TextTier) holds moments. Reading takes the interval tiers and
leaves the point tiers out: a moment is not a subtitle. Which tiers are
the subtitles goes by their names (`tiers` lists them, `parse` takes one
by name or number):

    a tier named as a level of a transcription ("sentence", "utterance",
    "transcription", "text"...) is the subtitles, and holds no speaker
    tiers named as people ("Mary", "spk1") are each a speaker's, the
    name the speaker (numbered ones as letters, as modules.json_subtitles
    names them)
    words ("words", "word", "ort", Montreal Forced Aligner's "Mary - words")
    become subtitles of the length asked for, as modules.json_subtitles
    makes them of a speech recogniser's words; phones and syllables
    ("phones", "MAU", "KAN"...), notes and glosses are left out

in that order of preference. Intervals with no text, or only a pause
("sil", "sp", "<p:>"), are no subtitle. A tier name ending " (2)" is
the second tier of the same name, which is how writing keeps subtitles
that overlap. Writing gives the long form in UTF-8, a tier for each
speaker when the export asks for speakers ('voice'; else one tier,
"transcription"), empty intervals between subtitles, as an interval tier
has no gaps, and a further tier of the same name for a subtitle that
overlaps another. The chronological TextGrid ("Praat chronological
TextGrid text file") and Praat's binary one are refused with a message
saying how to have Praat save a text file.

CHAT (.cha) follows TalkBank's CHAT manual (MacWhinney): headers in "@"
lines, an utterance a main line "*SPK:" and a tab, its time a bullet
U+0015 begin_end U+0015 in milliseconds at its end ("·0_1073·" on the
screen), dependent tiers ("%mor:") after it, continuation lines starting
with a tab. Reading gives a subtitle a main line with a bullet (one with
none has no time, and is left out); several bullets in one ("multiple")
give the first begin and the last end; the older "%snd:" bullets read as
well. Its speaker is the participant's name in @Participants ("CHI Ross
Target_Child": "Ross"), else the code. The text leaves out CHAT's codes
in square brackets, events (&=laughs), scope brackets and the "+"
linkers (+... is written "..."); dependent tiers are not read. The first
of @Languages (ISO 639-3) is the project's language. Writing gives
@UTF8, @Begin, @Languages, @Participants (an ID from the speaker's name,
the name, the role "Participant"; one "UNI ... Unidentified" when the
export leaves speakers out), an @ID each, @Media when there is a video,
a main line a subtitle with a terminator ("." when it has none) and its
bullet, and @End. A subtitle's lines are one utterance on one line.

CTM (.ctm) follows the NIST Scoring Toolkit's input formats page
(sclite, infmts.htm): a word a line, "<waveform> <channel> <begin>
<duration> <word> [<confidence>]", seconds, ";;" comments; Kaldi writes
the same. Reading groups the words into subtitles as
modules.json_subtitles does a speech recogniser's, by the limits given;
alternatives (<ALT_BEGIN> ... <ALT> ... <ALT_END>) give their first;
tokens in <> or [] (<eps>, <unk>, [noise]) and !SIL are left out. When a
file holds more than one waveform or channel, each is a speaker. Writing
gives a line a word, the waveform the video's name, channel A: the words'
times come from the karaoke codes of a subtitle that has them
(modules.karaoke), else its time is shared out by the words' length.
"""

import re

from subtitld.modules import json_subtitles, karaoke, markup

# A point label, or the last moment of a file, with nothing after it.
TAIL = 3.0


def plain_text(segment):
    """A subtitle's text as it reads: no place codes, no formatting."""
    return markup.plain(markup.split_lead(str(segment.get('text') or ''))[1]).strip()


def iso639_3(code):
    """The ISO 639-3 code (CHAT's, Aeneas's) for a language as Subtitld
    names it: "pt-br" is "por", "de" "deu"."""
    from subtitld.modules import usf
    primary = str(code or '').replace('_', '-').split('-')[0].lower()
    if primary == 'nb':
        return 'nob'
    terminologic = {two: three for three, two in usf._TERMINOLOGIC.items() if three not in ('nor', 'nno')}
    return terminologic.get(primary) or usf.ISO639_2.get(primary, primary)


def words_of(segment):
    """[(start, end, word)] of a subtitle: from its karaoke codes when it
    has them, a code's time shared by the words inside it; else its time
    shared out by the words' length."""
    start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
    # The whole text: its lead may hold the first karaoke code.
    text = str(segment.get('text') or '')
    pieces = karaoke.timed(text, start)
    out = []
    if pieces:
        joined = False
        for begin, finish, piece in pieces:
            words = piece.split()
            if not words:
                joined = False
                continue
            share = (finish - begin) / len(words)
            for index, word in enumerate(words):
                here = (round(begin + index * share, 3), round(begin + (index + 1) * share, 3))
                if index == 0 and joined and out and not piece[:1].isspace():
                    out[-1] = (out[-1][0], here[1], out[-1][2] + word)
                else:
                    out.append((here[0], here[1], word))
            joined = not piece[-1:].isspace()
        return out
    words = markup.plain(text).split()
    total = sum(len(word) + 1 for word in words)
    at = start
    for word in words:
        share = (end - start) * (len(word) + 1) / total
        out.append((round(at, 3), round(at + share, 3), word))
        at += share
    return out


def _number(value):
    text = ('%.6f' % value).rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


# --- Praat TextGrid -------------------------------------------------------

_NUMBER = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$')
# Names of the levels of a transcription, as Praat users, WebMAUS and the
# Montreal Forced Aligner name their tiers.
_UTTERANCES = {'sentence', 'sentences', 'utterance', 'utterances', 'transcription', 'transcript', 'text', 'trn',
               'trans', 'ipu', 'ipus', 'chunk', 'chunks', 'turn', 'turns', 'speech', 'tx', 'subtitles', 'orthographic'}
_WORDS = {'word', 'words', 'wrd', 'ort', 'orthography', 'tokens', 'token', 'lexical'}
_LEFT_OUT = {'phone', 'phones', 'phoneme', 'phonemes', 'phon', 'mau', 'kan', 'sampa', 'ipa', 'segment', 'segments',
             'syllable', 'syllables', 'syl', 'tone', 'tones', 'tobi', 'gloss', 'glosses', 'pos', 'tag', 'tags',
             'note', 'notes', 'comment', 'comments', 'event', 'events', 'silence', 'silences', 'misc', 'break',
             'breaks', 'translation', 'phrase'}
_PAUSES = {'', 'sil', 'sp', 'spn', '<sil>', '<sp>', '<p:>', '<p>', '#', '_', '<eps>', 'pau', '<pau>'}
_MFA = re.compile(r'(.+?)\s+-\s+(words|phones)$', re.IGNORECASE)
_AGAIN = re.compile(r'\s\(\d+\)$')


def textgrid_looks_like(head):
    """Whether decoded text (its start is enough) is a TextGrid of
    Praat's in text."""
    lines = [line for line in str(head or '').lstrip('﻿').split('\n')[:3] if line.strip()]
    return bool(lines) and 'ooTextFile' in lines[0] and 'TextGrid' in ' '.join(lines[:2])


def _textgrid_tokens(text):
    """The data of a Praat text file: ('number', float), ('text', str) and
    ('flag', str), the free-standing ones, in order."""
    out, at, size = [], 0, len(text)
    while at < size:
        char = text[at]
        if char.isspace():
            at += 1
        elif char == '!':
            line_end = text.find('\n', at)
            at = size if line_end < 0 else line_end
        elif char == '"':
            parts, at = [], at + 1
            while True:
                close = text.find('"', at)
                if close < 0:
                    raise ValueError('A text in this TextGrid has no closing quote')
                parts.append(text[at:close])
                if text.startswith('"', close + 1):
                    parts.append('"')
                    at = close + 2
                    continue
                at = close + 1
                break
            out.append(('text', ''.join(parts)))
        else:
            word_end = at
            while word_end < size and not text[word_end].isspace():
                word_end += 1
            word = text[at:word_end]
            at = word_end
            if _NUMBER.match(word):
                out.append(('number', float(word)))
            elif len(word) > 2 and word[0] == '<' and word[-1] == '>':
                out.append(('flag', word[1:-1]))
    return out


def parse_textgrid_tiers(data):
    """The tiers of a TextGrid (bytes or text): [{'name', 'class', 'xmin',
    'xmax', 'items'}], items [(start, end, text)] of an interval tier or
    [(time, text)] of a point tier. ValueError when it is not one."""
    from subtitld.modules import srt
    text = srt.decode(data) if isinstance(data, bytes) else str(data).lstrip('﻿')
    first = text.lstrip()[:60]
    if first.startswith('"Praat chronological TextGrid'):
        raise ValueError('This is a chronological TextGrid: open it in Praat and save it with "Save as text file"')
    if 'ooBinaryFile' in first:
        raise ValueError('This is a binary TextGrid: open it in Praat and save it with "Save as text file"')
    if not textgrid_looks_like(text):
        raise ValueError('Not a TextGrid: it does not start with "ooTextFile" and "TextGrid"')
    tokens = _textgrid_tokens(text)
    position = [0]

    def take(kind):
        if position[0] >= len(tokens):
            raise ValueError('This TextGrid ends before its last tier does')
        got_kind, value = tokens[position[0]]
        if got_kind != kind:
            raise ValueError(f'This TextGrid has a {got_kind} where a {kind} belongs (datum {position[0] + 1})')
        position[0] += 1
        return value

    # Older Praat wrote its short form as "ooTextFile short".
    if not take('text').startswith('ooTextFile') or take('text') != 'TextGrid':
        raise ValueError('Not a TextGrid: it does not start with "ooTextFile" and "TextGrid"')
    take('number')
    take('number')
    if take('flag') != 'exists':
        return []
    tiers = []
    for _tier in range(int(take('number'))):
        kind, name = take('text'), take('text')
        tier = {'class': kind, 'name': name, 'xmin': take('number'), 'xmax': take('number'), 'items': []}
        for _item in range(int(take('number'))):
            if kind == 'IntervalTier':
                tier['items'].append((take('number'), take('number'), take('text')))
            elif kind == 'TextTier':
                tier['items'].append((take('number'), take('text')))
            else:
                raise ValueError(f'This TextGrid has a tier of a class Praat does not have: {kind}')
        tiers.append(tier)
    return tiers


def _tier_kind(name):
    """(level, speaker) a tier's name says: level 'utterances', 'words',
    'left out' or 'speaker'."""
    name = _AGAIN.sub('', name.strip())
    match = _MFA.match(name)
    if match:
        return ('words' if match.group(2).lower() == 'words' else 'left out'), match.group(1)
    key = name.lower()
    if key in _UTTERANCES:
        return 'utterances', None
    if key in _WORDS:
        return 'words', None
    if key in _LEFT_OUT:
        return 'left out', None
    return 'speaker', name


def textgrid_tiers(data):
    """[(name, class, how many with text)] of a TextGrid's tiers, to choose
    one from."""
    return [(tier['name'], tier['class'], sum(1 for item in tier['items'] if item[-1].strip()))
            for tier in parse_textgrid_tiers(data)]


def parse_textgrid(data, tier=None, limits=None):
    """The subtitles of a TextGrid (bytes or text): the tier named or
    numbered (from 0) `tier`, else the ones its names say (see the module's
    docstring); ValueError when there are none."""
    tiers = [entry for entry in parse_textgrid_tiers(data) if entry['class'] == 'IntervalTier'
             and any(text.strip().lower() not in _PAUSES for _start, _end, text in entry['items'])]
    speakers = json_subtitles.Speakers()
    if tier is not None:
        # The one asked for, whatever its name says: words are still
        # grouped, and a speaker's tier still names its speaker.
        chosen = []
        for index, entry in enumerate(tiers):
            if tier in (entry['name'], index):
                level, speaker = _tier_kind(entry['name'])
                chosen.append((entry, 'words' if level == 'words' else 'utterances', speaker))
    else:
        kinds = [(entry,) + _tier_kind(entry['name']) for entry in tiers]
        chosen = []
        for level in ('utterances', 'speaker', 'words', 'left out'):
            chosen = [item for item in kinds if item[1] == level]
            if chosen:
                break
    if not chosen:
        raise ValueError('This TextGrid has no interval tier with text in it')
    subtitles, words = [], []
    for entry, level, speaker in chosen:
        name = speakers(_AGAIN.sub('', speaker)) if speaker else None
        for start, end, text in entry['items']:
            if text.strip().lower() in _PAUSES:
                continue
            if level == 'words':
                words.append({'start': start, 'end': end, 'text': text.strip(), 'speaker': name,
                              'attached': not re.search(r'\w', text)})
            else:
                subtitle = {'start': round(start, 3), 'end': round(end, 3), 'text': text.strip()}
                if name:
                    subtitle['speaker'] = name
                subtitles.append(subtitle)
    if words:
        words.sort(key=lambda word: word['start'])
        subtitles += [{key: value for key, value in subtitle.items() if value is not None}
                      for subtitle in json_subtitles.from_words(words, limits)]
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return subtitles


def read_textgrid(path, tier=None, limits=None):
    with open(path, 'rb') as grid_file:
        return parse_textgrid(grid_file.read(), tier, limits)


def layers(segments, by_speaker, default):
    """{tier name: [segments]} for formats whose tiers hold no overlaps: a
    tier a speaker's (or one, `default`), a subtitle that overlaps one
    already in it going to a further tier, "Name (2)"."""
    out = {}
    for segment in sorted(segments, key=lambda segment: (float(segment.get('start', 0)), float(segment.get('end', 0)))):
        name = str(segment.get('speaker') or '').strip() if by_speaker else ''
        name = name or default
        number = 1
        while True:
            key = name if number == 1 else f'{name} ({number})'
            held = out.setdefault(key, [])
            if not held or float(held[-1]['end']) <= float(segment['start']) + 1e-9:
                held.append(segment)
                break
            number += 1
    return out


def write_textgrid(segments, speakers=False):
    """A TextGrid, the long text form, of `segments`: a tier a speaker when
    `speakers`, else one; empty intervals between subtitles."""
    shown = [dict(segment, text=plain_text(segment)) for segment in segments
             if plain_text(segment) and float(segment.get('end', 0)) > float(segment.get('start', 0))]
    end = max([float(segment['end']) for segment in shown] or [0.0])
    tiers = layers(shown, speakers, 'transcription')

    def quoted(text):
        return '"' + text.replace('"', '""') + '"'

    out = ['File type = "ooTextFile"', 'Object class = "TextGrid"', '', 'xmin = 0', f'xmax = {_number(end)}']
    out += ['tiers? <exists>' if tiers else 'tiers? <absent>']
    if tiers:
        out += [f'size = {len(tiers)}', 'item []:']
    for number, (name, held) in enumerate(tiers.items(), 1):
        intervals, at = [], 0.0
        for segment in held:
            start, finish = float(segment['start']), float(segment['end'])
            if start > at + 1e-9:
                intervals.append((at, start, ''))
            intervals.append((max(start, at), finish, segment['text']))
            at = finish
        if end > at + 1e-9 or not intervals:
            intervals.append((at, end, ''))
        out += [f'    item [{number}]:', '        class = "IntervalTier"', f'        name = {quoted(name)}',
                '        xmin = 0', f'        xmax = {_number(end)}', f'        intervals: size = {len(intervals)}']
        for index, (start, finish, text) in enumerate(intervals, 1):
            out += [f'        intervals [{index}]:', f'            xmin = {_number(start)}',
                    f'            xmax = {_number(finish)}', f'            text = {quoted(text)}']
    return '\n'.join(out) + '\n'


def save_textgrid(path, segments, speakers=False):
    with open(path, 'w', encoding='utf-8', newline='\n') as grid_file:
        grid_file.write(write_textgrid(segments, speakers))


# --- CHAT -----------------------------------------------------------------

_BULLET = re.compile('\x15(?:%(?:snd|mov):"[^"]*"_)?-?(\\d+)_(\\d+)-?\x15')
_ANY_BULLET = re.compile('\x15[^\x15]*\x15')
_TERMINATOR = re.compile(r'[.!?]$')


def chat_looks_like(head):
    """Whether decoded text is CHAT: "@Begin" (after the hidden headers)
    and a header or main line after it."""
    lines = [line for line in str(head or '').lstrip('﻿').split('\n') if line.strip()][:12]
    return any(line.strip() == '@Begin' for line in lines) and any(line.startswith(('@Languages', '@Participants', '*'))
                                                                    for line in lines)


def _chat_text(text):
    """A main line's words, CHAT's codes left out."""
    text = _ANY_BULLET.sub(' ', text)
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    text = re.sub(r'(?<!\S)&=\S+', ' ', text)
    text = text.replace('<', ' ').replace('>', ' ')
    text = re.sub(r'(?<!\S)\+\.\.\.(?!\S)', '...', text)
    text = re.sub(r'(?<!\S)\+[^\w\s]*(?!\S)', ' ', text)
    text = ' '.join(text.split())
    return re.sub(r'\s+([.,!?;:])', r'\1', text).strip()


def parse_chat(data):
    """(subtitles, the first language of @Languages or None) of a CHAT
    transcript (bytes or text); ValueError when it is not one or has no
    time marks."""
    from subtitld.modules import srt
    text = srt.decode(data) if isinstance(data, bytes) else str(data).lstrip('﻿')
    if not chat_looks_like(text):
        raise ValueError('Not a CHAT transcript: it has no @Begin header')
    lines = []
    for line in text.split('\n'):
        if line.startswith('\t') and lines:
            lines[-1] += ' ' + line.strip()
        elif line.strip():
            lines.append(line.rstrip())
    names, language, subtitles, untimed = {}, None, [], 0
    speakers = json_subtitles.Speakers()
    for line in lines:
        if line.startswith('@Languages:'):
            codes = [code.strip() for code in line.split(':', 1)[1].split(',') if code.strip()]
            language = codes[0] if codes else None
        elif line.startswith('@Participants:'):
            for entry in line.split(':', 1)[1].split(','):
                parts = entry.split()
                if parts:
                    names[parts[0]] = parts[1].replace('_', ' ') if len(parts) >= 3 else parts[0]
        elif re.match(r'\*[^:\s]+:', line):
            code, said = line[1:].split(':', 1)
            times = _BULLET.findall(said)
            if not times:
                untimed += 1
                continue
            words = _chat_text(said)
            if not words:
                continue
            subtitles.append({'start': int(times[0][0]) / 1000, 'end': int(times[-1][1]) / 1000, 'text': words,
                              'speaker': speakers(names.get(code, code))})
    if not subtitles and untimed:
        raise ValueError('This CHAT transcript has no time marks (bullets): it is not linked to its media')
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return subtitles, language


def read_chat(path):
    with open(path, 'rb') as chat_file:
        return parse_chat(chat_file.read())


def _chat_codes(names):
    """{name: an ID}: up to three letters of the name, in capitals, made
    unique; "SP" before a shorter one ("A" is "SPA")."""
    codes = {}
    for name in names:
        letters = re.sub(r'[^A-Za-z0-9]', '', name).upper()[:3]
        letters = letters if len(letters) == 3 else ('SP' + letters)[-3:] if letters else 'SPK'
        code, number = letters, 1
        while code in codes.values():
            number += 1
            code = letters[:3 - len(str(number))] + str(number)
        codes[name] = code
    return codes


def write_chat(segments, language='en', speakers=False, media=None, video=True):
    """A CHAT transcript of `segments`: a main line a subtitle with its
    bullet, participants from the speakers (when `speakers`), `media` the
    name of the video (or audio) it is linked to."""
    shown = [segment for segment in sorted(segments, key=lambda segment: float(segment.get('start', 0)))
             if plain_text(segment)]
    names = []
    for segment in shown:
        name = str(segment.get('speaker') or '').strip() if speakers else ''
        if name and name not in names:
            names.append(name)
    codes = _chat_codes(names)
    lang = iso639_3(language or 'en')
    corpus = re.sub(r'[^a-z0-9]', '', (media or '').lower()) or 'corpus'
    participants = [f'{codes[name]} {name.replace(" ", "_")} Participant' for name in names]
    unnamed = any(not (str(segment.get('speaker') or '').strip() and speakers) for segment in shown) or not shown
    if unnamed:
        participants.append('UNI Unidentified')
    out = ['@UTF8', '@Begin', f'@Languages:\t{lang}', '@Participants:\t' + ', '.join(participants)]
    out += [f'@ID:\t{lang}|{corpus}|{codes[name]}|||||Participant|||' for name in names]
    if unnamed:
        out.append(f'@ID:\t{lang}|{corpus}|UNI|||||Unidentified|||')
    if media:
        out.append(f'@Media:\t{media.replace(",", " ")}, {"video" if video else "audio"}')
    for segment in shown:
        name = str(segment.get('speaker') or '').strip() if speakers else ''
        said = ' '.join(plain_text(segment).replace('\x15', '').split())
        if not _TERMINATOR.search(said):
            said += ' .'
        begin, end = round(float(segment['start']) * 1000), round(float(segment['end']) * 1000)
        out.append(f'*{codes.get(name, "UNI")}:\t{said} \x15{begin}_{max(begin, end)}\x15')
    out.append('@End')
    return '\n'.join(out) + '\n'


def save_chat(path, segments, language='en', speakers=False, media=None, video=True):
    with open(path, 'w', encoding='utf-8', newline='\n') as chat_file:
        chat_file.write(write_chat(segments, language, speakers, media, video))


# --- CTM ------------------------------------------------------------------

_CTM_LINE = re.compile(r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s*$')


def ctm_looks_like(head):
    """Whether decoded text is CTM: its lines (but ";;" comments) of a
    waveform, a channel, two numbers and a word."""
    lines = [line for line in str(head or '').lstrip('﻿').split('\n') if line.strip() and not line.startswith(';;')]
    lines = lines[:40] if len(lines) > 40 else lines[:-1] or lines
    for line in lines:
        match = _CTM_LINE.match(line.strip())
        if not match or not (_NUMBER.match(match.group(3)) or match.group(3) == '*') or not (
                _NUMBER.match(match.group(4)) or match.group(4) == '*'):
            return False
    return bool(lines)


def parse_ctm(data, limits=None):
    """The subtitles of a CTM file's words (bytes or text), grouped as
    modules.json_subtitles groups a speech recogniser's; ValueError when it
    has none."""
    from subtitld.modules import srt
    text = srt.decode(data) if isinstance(data, bytes) else str(data).lstrip('﻿')
    found, alternative = [], None
    for number, line in enumerate(text.split('\n'), 1):
        if not line.strip() or line.startswith(';;'):
            continue
        match = _CTM_LINE.match(line.strip())
        if not match:
            raise ValueError(f'Line {number} is not a CTM record: {line.strip()[:60]}')
        waveform, channel, begin, duration, word = match.groups()[:5]
        if word == '<ALT_BEGIN>':
            alternative = 'first'
            continue
        if word == '<ALT>':
            alternative = 'others'
            continue
        if word == '<ALT_END>':
            alternative = None
            continue
        if alternative == 'others' or re.fullmatch(r'<.*>|\[.*\]|!SIL', word):
            continue
        if not _NUMBER.match(begin) or not _NUMBER.match(duration):
            raise ValueError(f'Line {number} has no time: {line.strip()[:60]}')
        found.append(((waveform, channel), float(begin), float(begin) + float(duration), word))
    sources = list(dict.fromkeys(source for source, *_rest in found))
    waveforms = {waveform for waveform, _channel in sources}
    speakers = json_subtitles.Speakers()
    words = []
    for (waveform, channel), start, end, word in found:
        speaker = None
        if len(sources) > 1:
            speaker = speakers(channel if len(waveforms) == 1 else f'{waveform} {channel}')
        words.append({'start': start, 'end': end, 'text': word, 'speaker': speaker,
                      'attached': not re.search(r'\w', word)})
    if not words:
        raise ValueError('This CTM file holds no words')
    words.sort(key=lambda word: word['start'])
    return [{key: value for key, value in subtitle.items() if value is not None}
            for subtitle in json_subtitles.from_words(words, limits)]


def read_ctm(path, limits=None):
    with open(path, 'rb') as ctm_file:
        return parse_ctm(ctm_file.read(), limits)


def write_ctm(segments, waveform='subtitles'):
    """A CTM of the words of `segments`, channel A, `waveform` the name of
    the recording (no spaces, no extension)."""
    waveform = re.sub(r'\s+', '_', waveform or '') or 'subtitles'
    out = []
    for segment in sorted(segments, key=lambda segment: float(segment.get('start', 0))):
        for start, end, word in words_of(segment):
            out.append(f'{waveform} A {start:.3f} {max(0.0, end - start):.3f} {word}')
    return '\n'.join(out) + ('\n' if out else '')


def save_ctm(path, segments, waveform='subtitles'):
    with open(path, 'w', encoding='utf-8', newline='\n') as ctm_file:
        ctm_file.write(write_ctm(segments, waveform))
