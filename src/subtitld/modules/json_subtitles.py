"""JSON subtitles and transcripts as other tools write them.

Subtitld's own JSON (Whisper's shape: "segments" of "start", "end" and
"text", in seconds) is read by the plain-text module, which says where a
file goes wrong. This module reads the rest: what speech-to-text engines
and services, video sites and subtitle sites put in JSON. `kind` tells
which one a document is; `read` turns it into subtitles.

Transcripts often come as turns or paragraphs, or as words with their
times: those become subtitles of the length asked for (characters a line,
lines, seconds), broken where the speaker changes, where speech pauses,
at the end of a sentence when the subtitle is already long enough, and
otherwise between words; a turn with no word times is shared out by the
length of its parts. Speakers a file numbers (0, "spk_1", "SPEAKER_02")
become "A", "B" ... in the order they first speak; speakers a file names
keep their names.
"""

import re

LIMITS = {'characters': 42, 'lines': 2, 'seconds': 7.0}
_PAUSE = 1.0                              # seconds of silence that end a subtitle
_SENTENCE_END = re.compile(r'[.!?…。！？]["”’»)\]]*$')
_ANONYMOUS = re.compile(r'(?:speaker|spk|guest|s)?[ _-]?\d+', re.IGNORECASE)


class Speakers:
    """Names for a file's speakers: its own names kept, numbers and ids
    as letters in the order they first speak."""

    def __init__(self):
        self.letters = {}

    def __call__(self, value):
        if value is None or value == '':
            return None
        text = str(value).strip()
        if not isinstance(value, bool) and (isinstance(value, int) or _ANONYMOUS.fullmatch(text)):
            if text not in self.letters:
                count = len(self.letters)
                self.letters[text] = chr(ord('A') + count) if count < 26 else f'S{count + 1}'
            return self.letters[text]
        return text


def _join(words):
    """The text of words, each attached or after a space as it says."""
    out = ''
    for word in words:
        text = word['text']
        if out and not word.get('attached') and not out.endswith((' ', '\n')):
            out += ' '
        out += text.strip() if not word.get('attached') else text.lstrip()
    return out.strip()


def _lines(text, limits):
    """`text` in at most limits['lines'] lines: as it is when it fits on one,
    else broken at the space that best balances the lines."""
    text = ' '.join(text.split())
    width = limits['characters']
    if len(text) <= width or limits['lines'] < 2:
        return text
    spaces = [index for index, char in enumerate(text) if char == ' ']
    if not spaces:
        return text
    middle = len(text) / 2
    fitting = [index for index in spaces if index <= width and len(text) - index - 1 <= width]
    cut = min(fitting or spaces, key=lambda index: abs(index - middle))
    return text[:cut] + '\n' + text[cut + 1:]


def from_words(words, limits=None):
    """Subtitles from words [{'start', 'end', 'text', 'speaker', 'attached'}]
    (attached: no space before it, as punctuation has)."""
    limits = {**LIMITS, **(limits or {})}
    room = limits['characters'] * limits['lines']
    subtitles, current = [], []

    def flush(count=None):
        """The first `count` words (all by default) are a subtitle."""
        taken = current[:count] if count else list(current)
        del current[:len(taken)]
        if taken:
            subtitle = {'start': round(taken[0]['start'], 3), 'end': round(max(word['end'] for word in taken), 3),
                        'text': _lines(_join(taken), limits)}
            if taken[0].get('speaker'):
                subtitle['speaker'] = taken[0]['speaker']
            subtitles.append(subtitle)

    def full():
        """Out of room: back to the last punctuation that leaves the
        subtitle fairly full, else all of it."""
        for count in range(len(current) - 1, 0, -1):
            if len(_join(current[:count])) < room * 0.4:
                break
            if re.search(r'[,;:.!?…，、。！？]["”’»)\]]*$', current[count - 1]['text'].strip()):
                flush(count)
                return
        flush()

    for word in words:
        if not str(word.get('text') or '').strip():
            continue
        if current and not word.get('attached'):
            last = current[-1]
            longer = _join(current + [word])
            if word.get('speaker') != current[0].get('speaker') or word['start'] - last['end'] > _PAUSE:
                flush()
            elif word['end'] - current[0]['start'] > limits['seconds'] or len(longer) > room:
                full()
            elif _SENTENCE_END.search(last['text'].strip()) and len(longer) > limits['characters']:
                flush()
        current.append(word)
    flush()
    return subtitles


def from_text(start, end, text, speaker=None, limits=None):
    """Subtitles from a turn with no word times: as it is when it fits, else
    in parts of whole words, the time shared out by their length."""
    limits = {**LIMITS, **(limits or {})}
    pieces = text.split()
    if not pieces:
        return []
    duration = max(0.0, end - start)
    total = sum(len(piece) + 1 for piece in pieces)
    words, at = [], start
    for piece in pieces:
        share = duration * (len(piece) + 1) / total
        words.append({'start': at, 'end': at + share, 'text': piece, 'speaker': speaker})
        at += share
    return from_words(words, limits)


def _numbers(item, *keys):
    return all(isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool) for key in keys)


def is_subtitlds(data):
    """Whether `data` is Subtitld's own JSON, Whisper's shape: "segments" (or
    a list) of objects with "start", "end" (seconds) and "text". Those the
    plain-text module reads, saying where they go wrong."""
    items = data.get('segments') if isinstance(data, dict) else data if isinstance(data, list) else None
    if not isinstance(items, list):
        return False
    objects = [item for item in items if isinstance(item, dict)]
    return not objects or any(_numbers(item, 'start', 'end') and 'text' in item for item in objects)


# Times, as files write them: seconds; "1.400s"; "4.87"; ISO 8601 "PT1.52S";
# {"seconds", "nanos"}.
_ISO = re.compile(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$')
_CLOCK = re.compile(r'(\d+):(\d{2}):(\d{2})[,.](\d{1,3})$')


def _seconds(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return float(value.get('seconds') or 0) + float(value.get('nanos') or 0) / 1e9
    text = str(value).strip()
    match = _ISO.match(text)
    if match and text != 'PT':
        hours, minutes, seconds = (float(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    match = _CLOCK.match(text)
    if match:
        hours, minutes, seconds, millis = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, '0')) / 1000
    try:
        return float(text.rstrip('s'))
    except ValueError:
        return None


def _first(*values):
    return next((value for value in values if value is not None), None)


def _place(text, keypad):
    """`text` at a keypad place (2, the bottom centre, needs no tag)."""
    return text if keypad in (None, 2) else '{\\an%d}%s' % (keypad, text)


def _whisperx(data, limits, speakers):
    out = []
    for segment in data.get('segments') or []:
        if not isinstance(segment, dict):
            continue
        start, end = _seconds(segment.get('start')), _seconds(segment.get('end'))
        text = str(segment.get('text') or '').strip()
        if start is None or end is None or not text:
            continue
        subtitle = {'start': round(start, 3), 'end': round(end, 3), 'text': text}
        if speakers(segment.get('speaker')):
            subtitle['speaker'] = speakers(segment.get('speaker'))
        out.append(subtitle)
    return out, data.get('language')


def _whisper_cpp(data, limits, speakers):
    out = []
    for item in data.get('transcription') or []:
        offsets, stamps = item.get('offsets') or {}, item.get('timestamps') or {}
        start = _first(_seconds(offsets.get('from')) / 1000 if offsets.get('from') is not None else None,
                       _seconds(stamps.get('from')))
        end = _first(_seconds(offsets.get('to')) / 1000 if offsets.get('to') is not None else None,
                     _seconds(stamps.get('to')))
        text = str(item.get('text') or '').strip()
        if start is None or end is None or not text:
            continue
        subtitle = {'start': round(start, 3), 'end': round(end, 3), 'text': text}
        speaker = speakers(item.get('speaker') if item.get('speaker') != '?' else None)
        if speaker:
            subtitle['speaker'] = speaker
        out.append(subtitle)
    language = (data.get('result') or {}).get('language') or (data.get('params') or {}).get('language')
    return out, language if language != 'auto' else None


def _youtube(data, limits, speakers):
    """YouTube's json3: events of text segments, each event a subtitle;
    pens give bold, italics, underline and colours, window positions the
    place. Automatic captions roll: their lines, which overlap, end where
    the next begins."""
    from subtitld.modules import markup
    pens = data.get('pens') or []
    positions = data.get('wpWinPositions') or []
    styles = data.get('wsWinStyles') or []
    events = [event for event in data.get('events') or [] if isinstance(event, dict)]
    windows = {event.get('id'): event for event in events if event.get('id') is not None and not event.get('segs')}

    def entry(table, index):
        return table[index] if isinstance(index, int) and 0 <= index < len(table) and isinstance(table[index], dict) else {}

    def style(pen):
        color = pen.get('fcForeColor')
        color = '#%06x' % color if isinstance(color, int) and color not in (0xFFFFFF, 0xFEFEFE) else ''
        return markup.Style(bool(pen.get('iAttr')), bool(pen.get('bAttr')), bool(pen.get('uAttr')), False, color)

    rolling = any(event.get('aAppend') for event in events) or any(
        entry(styles, window.get('wsWinStyleId')).get('mhModeHint') == 2 for window in windows.values())
    shown = []
    for event in events:
        segs = event.get('segs')
        if not segs:
            continue
        text_runs = []
        for seg in segs:
            # Zero-width spaces, which creators use to shape lines, show nothing.
            piece = str(seg.get('utf8') or '').replace('\u200b', '').replace('\ufeff', '')
            if '&lt;' in piece:
                import html
                piece = html.unescape(piece)
            text_runs.append((piece, style(entry(pens, seg.get('pPenId') or event.get('pPenId')))))
        if not ''.join(piece for piece, _style in text_runs).strip():
            continue
        start = (event.get('tStartMs') or 0) / 1000
        end = start + (event.get('dDurationMs') if event.get('dDurationMs') is not None else 5000) / 1000
        window = windows.get(event.get('wWinId'), {})
        keypad = None
        if not rolling:
            position = entry(positions, _first(event.get('wpWinPosId'), window.get('wpWinPosId')))
            if position:
                point = position.get('apPoint', 7)
                vertical = position.get('avVerPos', 100)
                horizontal = position.get('ahHorPos', 50)
                row = 0 if vertical <= 33 else 2 if vertical >= 67 else 1
                column = point % 3 if isinstance(point, int) and 0 <= point <= 8 else 1
                if column == 1:
                    column = 0 if horizontal < 33 else 2 if horizontal > 67 else 1
                keypad = (7, 4, 1)[row] + column
        text = markup.tidy(text_runs).strip()
        shown.append({'start': start, 'end': end, 'text': _place(text, keypad)})
    if rolling:
        for current, following in zip(shown, shown[1:]):
            if following['start'] < current['end']:
                current['end'] = following['start']
    return [{**subtitle, 'start': round(subtitle['start'], 3), 'end': round(max(subtitle['end'], subtitle['start']), 3)}
            for subtitle in shown], None


def _bilibili(data, limits, speakers):
    out = []
    for item in data.get('body') or []:
        start, end = _seconds(item.get('from')), _seconds(item.get('to'))
        text = str(item.get('content') or '').strip()
        if start is None or end is None or not text:
            continue
        location = item.get('location')
        keypad = location if isinstance(location, int) and 1 <= location <= 9 else None
        out.append({'start': round(start, 3), 'end': round(end, 3), 'text': _place(text, keypad)})
    return out, data.get('lang')


def _assemblyai(data, limits, speakers):
    utterances = data.get('utterances') or []
    words = data.get('words') or [word for utterance in utterances for word in utterance.get('words') or []]
    timed = [{'start': word['start'] / 1000, 'end': word['end'] / 1000, 'text': str(word.get('text') or ''),
              'speaker': speakers(word.get('speaker'))}
             for word in words if isinstance(word.get('start'), (int, float)) and isinstance(word.get('end'), (int, float))]
    if timed:
        return from_words(timed, limits), data.get('language_code')
    out = []
    for utterance in utterances:
        out += from_text(utterance['start'] / 1000, utterance['end'] / 1000, str(utterance.get('text') or ''),
                         speakers(utterance.get('speaker')), limits)
    return out, data.get('language_code')


def _deepgram(data, limits, speakers):
    results = data.get('results') or {}
    channels = results.get('channels') or []
    words, language = [], None
    for index, channel in enumerate(channels):
        alternative = (channel.get('alternatives') or [{}])[0]
        language = language or channel.get('detected_language') or (alternative.get('languages') or [None])[0]
        for word in alternative.get('words') or []:
            speaker = word.get('speaker', index if len(channels) > 1 else None)
            words.append({'start': word['start'], 'end': word['end'], 'speaker': speakers(speaker),
                          'text': str(word.get('punctuated_word') or word.get('word') or '')})
    if words:
        words.sort(key=lambda word: word['start'])
        return from_words(words, limits), language
    out = []
    for channel in channels:
        alternative = (channel.get('alternatives') or [{}])[0]
        for paragraph in (alternative.get('paragraphs') or {}).get('paragraphs') or []:
            for sentence in paragraph.get('sentences') or []:
                out += from_text(sentence['start'], sentence['end'], sentence.get('text') or '',
                                 speakers(paragraph.get('speaker')), limits)
    if not out:
        for utterance in results.get('utterances') or []:
            out += from_text(utterance['start'], utterance['end'], utterance.get('transcript') or '',
                             speakers(utterance.get('speaker')), limits)
    return out, language


def _aws(data, limits, speakers):
    results = data.get('results') or {}
    by_time = {}
    for segment in (results.get('speaker_labels') or {}).get('segments') or []:
        for item in segment.get('items') or []:
            by_time[item.get('start_time')] = item.get('speaker_label') or segment.get('speaker_label')
    for channel in (results.get('channel_labels') or {}).get('channels') or []:
        for item in channel.get('items') or []:
            by_time.setdefault(item.get('start_time'), channel.get('channel_label'))
    words = []
    for item in results.get('items') or []:
        content = str(((item.get('alternatives') or [{}])[0]).get('content') or '')
        if item.get('type') == 'punctuation':
            if words:
                words.append({**words[-1], 'text': content, 'attached': True})
            continue
        start, end = _seconds(item.get('start_time')), _seconds(item.get('end_time'))
        if start is None or end is None:
            continue
        speaker = item.get('speaker_label') or by_time.get(item.get('start_time'))
        words.append({'start': start, 'end': end, 'text': content, 'speaker': speakers(speaker)})
    language = results.get('language_code') or next(
        (entry.get('language_code') for entry in results.get('language_codes') or []), None) or next(
        (entry.get('code') for entry in results.get('language_identification') or []), None)
    return from_words(words, limits), language


def _rev(data, limits, speakers):
    words = []
    for monologue in data.get('monologues') or []:
        speaker = speakers(((monologue.get('speaker_info') or {}).get('display_name')) or monologue.get('speaker'))
        for element in monologue.get('elements') or []:
            value = str(element.get('value') or '')
            if element.get('type') == 'punct':
                if value.strip() and words:
                    words.append({**words[-1], 'text': value.strip(), 'attached': True})
                continue
            start, end = _seconds(element.get('ts')), _seconds(element.get('end_ts'))
            if start is not None and end is not None and value.strip():
                words.append({'start': start, 'end': end, 'text': value, 'speaker': speaker})
    return from_words(words, limits), None


def _google(data, limits, speakers):
    root = data.get('response') if isinstance(data.get('response'), dict) else data
    results = [result for result in root.get('results') or [] if isinstance(result, dict)]
    language = next((result.get('languageCode') or result.get('language_code') for result in results
                     if result.get('languageCode') or result.get('language_code')), None)
    words, seen = [], set()
    for result in results:
        for word in ((result.get('alternatives') or [{}])[0]).get('words') or []:
            start = _seconds(_first(word.get('startTime'), word.get('startOffset'), word.get('start_time'),
                                    word.get('start_offset'))) or 0.0
            end = _seconds(_first(word.get('endTime'), word.get('endOffset'), word.get('end_time'), word.get('end_offset')))
            text = str(word.get('word') or '')
            # Speaker diarization repeats every word in the last result.
            if (start, text) in seen or end is None:
                continue
            seen.add((start, text))
            words.append({'start': start, 'end': end, 'text': text, 'speaker': speakers(_first(
                word.get('speakerLabel'), word.get('speaker_label'), word.get('speakerTag') or None,
                word.get('speaker_tag') or None))})
    if words:
        words.sort(key=lambda word: word['start'])
        return from_words(words, limits), language
    out, previous = [], 0.0
    for result in results:
        end = _seconds(_first(result.get('resultEndTime'), result.get('resultEndOffset'), result.get('result_end_time')))
        text = ((result.get('alternatives') or [{}])[0]).get('transcript') or ''
        if end is not None:
            out += from_text(previous, end, text, None, limits)
            previous = end
    return out, language


def _azure_time(item, name):
    if item.get(name + 'InTicks') is not None:
        return float(item[name + 'InTicks']) / 1e7
    if item.get(name + 'Milliseconds') is not None:
        return float(item[name + 'Milliseconds']) / 1000
    return _seconds(item.get(name))


def _azure(data, limits, speakers):
    phrases = [phrase for phrase in data.get('recognizedPhrases') or []
               if isinstance(phrase, dict) and phrase.get('recognitionStatus', 'Success') == 'Success']
    channels = {phrase.get('channel') for phrase in phrases}
    words, out, language = [], [], None
    for phrase in phrases:
        best = (phrase.get('nBest') or [{}])[0]
        language = language or phrase.get('locale')
        speaker = speakers(_first(phrase.get('speaker'), phrase.get('channel') if len(channels) > 1 else None))
        if best.get('displayWords'):
            for word in best['displayWords']:
                start, duration = _azure_time(word, 'offset'), _azure_time(word, 'duration') or 0.0
                if start is not None:
                    words.append({'start': start, 'end': start + duration, 'text': str(word.get('displayText') or ''),
                                  'speaker': speaker})
        else:
            start, duration = _azure_time(phrase, 'offset'), _azure_time(phrase, 'duration') or 0.0
            if start is not None:
                out += from_text(start, start + duration, best.get('display') or best.get('lexical') or '', speaker,
                                 limits)
    if words:
        words.sort(key=lambda word: word['start'])
        out = sorted(out + from_words(words, limits), key=lambda subtitle: subtitle['start'])
    return out, language or data.get('locale')


def _speechmatics(data, limits, speakers):
    metadata = data.get('metadata') or {}
    delimiter = (metadata.get('language_pack_info') or {}).get('word_delimiter', ' ')
    words, attach_next = [], False
    for result in data.get('results') or []:
        alternative = (result.get('alternatives') or [{}])[0]
        content = str(alternative.get('content') or '')
        speaker = speakers(alternative.get('speaker') if alternative.get('speaker') != 'UU' else None)
        start, end = _seconds(result.get('start_time')), _seconds(result.get('end_time'))
        if start is None or end is None or not content:
            continue
        word = {'start': start, 'end': end, 'text': content, 'speaker': speaker}
        if result.get('type') == 'punctuation':
            attaches = result.get('attaches_to', 'previous')
            word['attached'] = attaches in ('previous', 'both') and bool(words)
            attach_next = attaches in ('next', 'both')
            if word['attached']:
                word['speaker'] = words[-1].get('speaker')
            words.append(word)
            continue
        word['attached'] = attach_next or (delimiter == '' and bool(words))
        attach_next = False
        words.append(word)
    language = (metadata.get('transcription_config') or {}).get('language')
    return from_words(words, limits), language


def _amara(data, limits, speakers):
    import html
    items = data if isinstance(data, list) else data.get('subtitles') or []
    items = sorted((item for item in items if isinstance(item, dict)), key=lambda item: item.get('position') or 0)
    out = []
    for item in items:
        start, end = item.get('start'), item.get('end')
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue                              # not synced yet
        text = re.sub(r'<br\s*/?>', '\n', str(item.get('text') or ''), flags=re.IGNORECASE)
        text = re.sub(r'<(?!/?[biu]>)[^>]*>', '', text)
        text = html.unescape(text).strip()
        if not text:
            continue
        top = (item.get('meta') or {}).get('region') == 'top'
        out.append({'start': round(start / 1000, 3), 'end': round(end / 1000, 3), 'text': _place(text, 8 if top else None)})
    language = (data.get('language') or {}).get('code') if isinstance(data, dict) else None
    return out, language


READERS = {'whisperx': _whisperx, 'whisper.cpp': _whisper_cpp, 'youtube': _youtube, 'bilibili': _bilibili,
           'assemblyai': _assemblyai, 'deepgram': _deepgram, 'aws': _aws, 'rev': _rev, 'google': _google,
           'azure': _azure, 'speechmatics': _speechmatics, 'amara': _amara}


def kind(data):
    """Which tool's JSON `data` is (a key of READERS), or None: Subtitld's
    own (Whisper's shape, see is_subtitlds), or nothing known."""
    if isinstance(data, list):
        objects = [item for item in data if isinstance(item, dict)]
        if any('text' in item and 'start' in item and ('meta' in item or 'position' in item) for item in objects):
            return 'amara'
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get('subtitles'), list) and ('sub_format' in data or 'version_number' in data):
        return 'amara'
    if 'wireMagic' in data or (isinstance(data.get('events'), list) and any(
            isinstance(event, dict) and ('tStartMs' in event or 'segs' in event) for event in data['events'])):
        return 'youtube'
    if isinstance(data.get('body'), list) and any(isinstance(item, dict) and 'from' in item and 'content' in item
                                                  for item in data['body']):
        return 'bilibili'
    if isinstance(data.get('transcription'), list):
        return 'whisper.cpp'
    segments = data.get('segments')
    if isinstance(segments, list) and ('word_segments' in data or any(
            isinstance(segment, dict) and isinstance(segment.get('words'), list)
            and re.fullmatch(r'SPEAKER_\d+', str(segment.get('speaker') or '')) for segment in segments)):
        return 'whisperx'
    if 'utterances' in data and not isinstance(data.get('results'), dict) or (
            isinstance(data.get('words'), list) and ('language_code' in data or 'audio_url' in data)):
        return 'assemblyai'
    if isinstance(data.get('monologues'), list):
        return 'rev'
    if isinstance(data.get('recognizedPhrases'), list):
        return 'azure'
    results = data.get('results')
    if isinstance(data.get('response'), dict) and isinstance(data['response'].get('results'), list):
        results = data['response']['results']
    if isinstance(results, dict):
        if isinstance(results.get('channels'), list):
            return 'deepgram'
        if isinstance(results.get('items'), list):
            return 'aws'
    if isinstance(results, list) and results and isinstance(results[0], dict):
        if 'type' in results[0] and isinstance(results[0].get('alternatives'), list):
            return 'speechmatics'
        if isinstance(results[0].get('alternatives'), list):
            return 'google'
    return None


def read(data, limits=None):
    """(subtitles, the language the file names or None, which tool's) for a
    JSON document of a kind this module knows; ValueError otherwise."""
    which = kind(data)
    if which is None:
        raise ValueError('This JSON holds no subtitles or transcript Subtitld knows')
    segments, language = READERS[which](data, {**LIMITS, **(limits or {})}, Speakers())
    segments = [segment for segment in segments if segment['text'].strip()]
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, (str(language) if language else None), which


def loads(text):
    """The JSON in `text`, as tools write it: control characters left raw in
    strings (whisper.cpp), and whisper.cpp's missing comma after "speaker"
    when it marks both channels and speaker turns."""
    import json
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        repaired = re.sub(r'("speaker"\s*:\s*"[^"]*")(\s*"speaker_turn_next")', r'\1,\2', text)
        if repaired == text:
            raise
        return json.loads(repaired, strict=False)


# Writing Bilibili's BCC.
BILIBILI_STYLE = {'font_size': 0.4, 'font_color': '#FFFFFF', 'background_alpha': 0.5, 'background_color': '#9C27B0',
                  'Stroke': 'none'}


def bilibili(segments):
    """Bilibili's BCC for `segments`: the player's default look, as
    Subtitle Edit writes it, and a body of plain text, seconds from and to.
    Subtitles shown at once are one entry, their texts a line each, for a
    timeline with no overlaps; "location" is 2, the bottom centre, which is
    all Bilibili's player is known to show."""
    import json
    from subtitld.modules import markup
    shown = []
    for segment in segments:
        text = markup.plain(markup.split_lead(str(segment.get('text') or ''))[1]).strip()
        start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
        if text and end > start:
            shown.append((start, end, text))
    shown.sort()
    moments = sorted({time for start, end, _text in shown for time in (start, end)})
    body = []
    for begin, finish in zip(moments, moments[1:]):
        content = '\n'.join(text for start, end, text in shown if start <= begin and end >= finish)
        if not content:
            continue
        if body and body[-1]['content'] == content and abs(body[-1]['to'] - round(begin, 3)) < 1e-6:
            body[-1]['to'] = round(finish, 3)
        else:
            body.append({'from': round(begin, 3), 'to': round(finish, 3), 'location': 2, 'content': content})
    return json.dumps({**BILIBILI_STYLE, 'body': body}, ensure_ascii=False, indent=2) + '\n'


def save_bilibili(path, segments):
    with open(path, 'w', encoding='utf-8', newline='\n') as bcc_file:
        bcc_file.write(bilibili(segments))
