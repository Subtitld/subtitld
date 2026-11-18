"""The JSON subtitles of video platforms, players and small tools, as
Subtitle Edit reads and writes them.

Each of these is a shape of JSON one site or program uses and nobody
documents; Subtitle Edit's readers and writers (src/libse/SubtitleFormats:
WistiaJson.cs, TwentyThreeJson*.cs, HollyStarJson.cs, JsonTed.cs,
GooglePlayJson.cs, PodcastIndexer.cs, SubUrbia.cs, Gremots.cs,
VideoIndexerJson.cs, JsonArchtime.cs, ClqttJson.cs, JsonType2.cs to
JsonType24.cs and JsonTypeOnlyLoad1.cs to JsonTypeOnlyLoad5.cs) are what
the shapes, units and names here follow, the writers what is written.
The table, DIALECTS, says for each its keys, what its times count in and
whether Subtitle Edit writes it (those are written here too); `kind`
tells which one a document is by the shape of its keys, `read` reads it,
`write` writes one.

The JSON this module leaves alone: Subtitld's own (Whisper's shape, read
by the plain-text editor), and every document modules.json_subtitles
knows (YouTube's json3, Bilibili's BCC, the speech-to-text services,
Amara, ...). `kind` is asked only after json_subtitles.kind has said no,
and it says no to Subtitld's own shape too, but for JSON Type 17, whose
text is a list of lines, which Subtitld's never is. Aeneas's sync maps
("fragments") are not one of these either. Google Play's json is
YouTube's json3 (events of tStartMs, dDurationMs and segs) and is read
there: it is here to be written. Of the shapes Subtitle Edit reads,
Google's Speech-to-Text word lists (JSON Type 20, 22), IBM Watson's
(Transcriptive) and AWS Transcribe's are json_subtitles' already; YouTube's
live-chat replay (JsonTypeOnlyLoad6) is one JSON document a line, not
JSON, and is left out.

Reading gives a subtitle its times and its text, lines broken where the
file breaks them (a list of lines, "\\n", or "<br />" as Subtitle Edit
writes a line break inside a string), and a speaker and a language where
the dialect has them. Times in frames (JSON Type 16, GTS Pro, CLQTT where
its header does not say) count at the frame rate given, the video's,
else 25. A dialect with start times only (JSON Type 11, 15) ends a
subtitle where the next begins, or after a reading time of 15 characters
a second (one to seven seconds), whichever is first: Subtitle Edit's
"optimal duration" for its settings, which a file cannot know.

Writing gives the dialect's keys and units as Subtitle Edit writes them,
the text plain (no formatting: the players these are for show none), its
lines a list where the dialect keeps lines in a list, else joined by what
Subtitle Edit joins them with. What is lost: formatting and places (CLQTT
and OOONA, read only, give a top place), and every field a dialect has
beyond times, text, speaker and language. JSON Type 7 and 13 are read but
not written: Subtitle Edit's writers for them write what is not JSON
(a stray brace; a trailing comma).
"""

import json
import re
import urllib.parse
import uuid

from subtitld.modules import markup

DEFAULT_FPS = 25.0
_CLOCK = re.compile(r'^(\d+):(\d{1,2}):(\d{1,2})[,.:;](\d+)$')


# Reading helpers

def _number(value):
    """A number a file writes as one or as text ("1.5", "1,5"), else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip('s').replace(',', '.'))
    except ValueError:
        return None


def _numbers(item, *keys):
    return isinstance(item, dict) and all(_number(item.get(key)) is not None for key in keys)


def _lines(value, br=True):
    """A file's text: a list of lines, or a string whose line breaks are
    "\\n", "\\r\\n" or (as Subtitle Edit writes them) "<br />"."""
    if isinstance(value, list):
        return '\n'.join(_lines(line, br) for line in value if line is not None and str(line).strip() != '').strip()
    text = '' if value is None else str(value)
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\\n', '\n')
    if br:
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    return '\n'.join(line.strip() for line in text.split('\n')).strip()


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _cue(start, end, text, **more):
    cue = {'start': round(max(0.0, start), 3), 'end': round(max(0.0, end), 3), 'text': text}
    cue.update({key: value for key, value in more.items() if value})
    return cue


def _clock(value, fps=None):
    """Seconds of "HH:MM:SS,mmm" (or with a '.'), or, with `fps`, of
    "HH:MM:SS:FF" frames; None for anything else."""
    match = _CLOCK.match(str(value or '').strip())
    if not match:
        return None
    hours, minutes, seconds, last = match.groups()
    whole = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fps:
        return whole + int(last) / float(fps)
    return whole + int(last.ljust(3, '0')[:3]) / 1000


def _reading_time(text):
    """Subtitle Edit's sort of optimal duration: 15 characters a second,
    one to seven seconds."""
    return min(7.0, max(1.0, len(text.replace('\n', '')) / 15.0))


def _ended(cues):
    """Cues with a start only ended at the next start or after their reading time."""
    cues.sort(key=lambda cue: cue['start'])
    for cue, after in zip(cues, cues[1:] + [None]):
        end = cue['start'] + _reading_time(cue['text'])
        if after is not None and after['start'] > cue['start']:
            end = min(end, after['start'])
        cue['end'] = round(end, 3)
    return cues


def _find(data, key):
    """The first list under `key`, anywhere in `data`."""
    if isinstance(data, dict):
        if isinstance(data.get(key), list):
            return data[key]
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        found = _find(value, key)
        if found is not None:
            return found
    return None


def _list_with(data, *keys):
    """The objects of a list root, or of a list one level down, that all
    have `keys`; [] when no list of them is there."""
    candidates = [data] if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
    for candidate in candidates:
        items = _objects(candidate)
        if items and all(all(key in item for key in keys) for item in items):
            return items
    return []


# Reading the dialects: each takes the document and the frame rate, and
# gives ([cues], language or None).

def _read_wistia(data, fps):
    tracks = _objects(data.get('captions'))
    track = tracks[0] if tracks else {}
    lines = _objects((track.get('hash') or {}).get('lines'))
    out = [_cue(_number(line['start']), _number(line['end']), _lines(line.get('text')))
           for line in lines if _numbers(line, 'start', 'end')]
    return out, track.get('bcp47LanguageTag') or track.get('language')


def _read_twentythree(data, fps):
    out = [_cue(_number(item['timestamp_begin']), _number(item['timestamp_end']), _lines(item.get('text')))
           for item in _objects(data.get('subtitles')) if _numbers(item, 'timestamp_begin', 'timestamp_end')]
    return out, None


def _read_twentythree_embed(data, fps):
    return _read_twentythree(json.loads(data['data'], strict=False), fps)


def _read_twentythree_websrt(data, fps):
    from subtitld.modules import srt
    return srt.parse(str(data['data']['websrt']).replace('\r\n', '\n')), None


def _read_holistar(data, fps):
    out, language = [], None
    for track in _objects(data.get('SubtitleLanguages'))[:1]:
        language = track.get('ClassName')
        for item in _objects(track.get('SubtitleItems')):
            if _numbers(item, 'ShowTime', 'HideTime'):
                out.append(_cue(_number(item['ShowTime']) / 1000, _number(item['HideTime']) / 1000,
                                _lines(item.get('TextLines'))))
    return out, language


def _ted_captions(data):
    if isinstance(data, dict) and isinstance(data.get('captions'), list):
        return data
    subtitle = _objects(data.get('subtitle')) if isinstance(data, dict) else []
    return subtitle[0] if subtitle and isinstance(subtitle[0].get('captions'), list) else None


def _read_ted(data, fps):
    holder = _ted_captions(data)
    out = [_cue(_number(item['startTime']) / 1000, (_number(item['startTime']) + _number(item['duration'])) / 1000,
                _lines(item.get('content')))
           for item in _objects(holder.get('captions')) if _numbers(item, 'startTime', 'duration')]
    return out, holder.get('lang')


def _read_ted_list(data, fps):
    out = [_cue(_number(item['startTime']) / 1000, (_number(item['startTime']) + _number(item['duration'])) / 1000,
                _lines(item.get('content')))
           for item in _objects(data) if _numbers(item, 'startTime', 'duration')]
    return out, None


def _pairs(start, end, text, scale=1.0, end_is_duration=False, br=True):
    """A reader for a list (or a list one level down) of objects of a start,
    an end (or a duration) and a text, the times `scale` to a second."""
    def reader(data, fps):
        out = []
        for item in _list_with(data, start, text):
            first = _number(item.get(start))
            last = _number(item.get(end))
            if first is None or last is None:
                continue
            last = first + last if end_is_duration else last
            out.append(_cue(first * scale, last * scale, _lines(item.get(text), br)))
        return out, None
    return reader


def _read_start_end_time(data, fps):
    """JSON Type 8 (seconds) and 8b (milliseconds): the same keys; the times
    are milliseconds when, read as seconds, a subtitle would last a minute
    on average (Subtitle Edit's own test)."""
    items = [item for item in _list_with(data, 'start_time', 'text') if _numbers(item, 'start_time', 'end_time')]
    lengths = [_number(item['end_time']) - _number(item['start_time']) for item in items]
    scale = 0.001 if lengths and sum(lengths) / len(lengths) >= 60 else 1.0
    return [_cue(_number(item['start_time']) * scale, _number(item['end_time']) * scale, _lines(item.get('text')))
            for item in items], None


def _read_type4(data, fps):
    out, language = [], None
    for item in _objects(data):
        meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        if _numbers(item, 'startTime', 'endTime'):
            out.append(_cue(_number(item['startTime']), _number(item['endTime']), _lines(meta.get('Text'))))
            language = language or meta.get('Language')
    return out, language


def _read_type5(data, fps):
    times = data.get('text_tees') or []
    texts = data.get('text_content') or []
    out = []
    # Subtitle Edit's pairing: each text with the time at its own index, to
    # the one after; the empty texts between fill the gaps and are dropped.
    for index in range(min(len(times), len(texts))):
        start = _number(times[index])
        end = _number(times[index + 1]) if index + 1 < len(times) else None
        content = texts[index]
        if isinstance(content, list):
            content = [' '.join(str(word) for word in line) if isinstance(line, list) else line for line in content]
        text = _lines(content)
        if start is not None and text:
            out.append(_cue(start / 1000, (end if end is not None else start) / 1000, text))
    return out, None


def _read_type6(data, fps):
    from subtitld.modules import json_subtitles
    stamps = []
    for word in data.get('words') or []:
        if isinstance(word, list) and len(word) == 2 and _number(word[0]) is not None:
            stamps.append((_number(word[0]) / 1000, str(word[1] or '').strip()))
    words = []
    for (start, text), after in zip(stamps, stamps[1:] + [None]):
        if text:
            end = after[0] if after is not None else start + _reading_time(text)
            words.append({'start': start, 'end': max(start, end), 'text': text})
    return json_subtitles.from_words(words), None


def _read_type7(data, fps):
    starts, ends, texts = data.get('start') or [], data.get('end') or [], data.get('text') or []
    out = []
    for start, end, text in zip(starts, ends, texts):
        if _number(start) is not None and _number(end) is not None:
            out.append(_cue(_number(start) / 1000, _number(end) / 1000, _lines(text)))
    return out, None


def _read_type9(data, fps):
    out = []
    for item in _objects(data):
        start, end = _clock(item.get('start')), _clock(item.get('end'))
        if start is not None and end is not None:
            out.append(_cue(start, end, _lines(item.get('text'))))
    return out, None


def _read_type13(data, fps):
    out = []
    for item in _objects(data.get('speakers')):
        if _numbers(item, 'time', 'duration'):
            text = _lines(item.get('name'))
            if text in ('.', '?', '!') and out and out[-1]['end'] + 1 > _number(item['time']):
                out[-1]['text'] += text
                out[-1]['end'] = round(_number(item['time']) + _number(item['duration']), 3)
                continue
            out.append(_cue(_number(item['time']), _number(item['time']) + _number(item['duration']), text))
    return out, (data.get('job') or {}).get('lang') if isinstance(data.get('job'), dict) else None


def _read_type14(data, fps):
    out = [_cue(_number(item['s']), _number(item['e']), _lines(item.get('t')))
           for item in _objects(data.get('captionGroups')) if _numbers(item, 's', 'e')]
    return out, None


def _read_type15(data, fps):
    out = [_cue(_number(item['displayTimeOffset']), 0, _lines(item.get('text')))
           for item in _objects(data) if _numbers(item, 'displayTimeOffset')]
    return _ended(out), None


def _read_type11(data, fps):
    out = [_cue(_number(item['milliseconds']) / 1000, 0, _lines(item.get('line')))
           for item in _objects(data) if _numbers(item, 'milliseconds')]
    return _ended(out), None


def _frames_time(value, fps):
    if not isinstance(value, dict) or not all(_number(value.get(key)) is not None for key in 'hmsf'):
        return None
    return _number(value['h']) * 3600 + _number(value['m']) * 60 + _number(value['s']) + _number(value['f']) / fps


def _read_type16(data, fps):
    items = data.get('subtitles') if isinstance(data, dict) else data
    out = []
    for item in _objects(items):
        start, end = _frames_time(item.get('startTime'), fps), _frames_time(item.get('endTime'), fps)
        if start is not None and end is not None:
            out.append(_cue(start, end, _lines(item.get('text'))))
    return out, None


def _read_type17(data, fps):
    out = [_cue(_number(item['start']) / 1000, _number(item['end']) / 1000, _lines(item.get('text')))
           for item in _objects(data) if _numbers(item, 'start', 'end')]
    return out, None


def _read_type19(data, fps):
    out = []
    for item in _objects(data):
        if _numbers(item, 'tStartMs', 'dDurationMs'):
            text = ' '.join(str(seg.get('utf8') or '') for seg in _objects(item.get('segs')))
            out.append(_cue(_number(item['tStartMs']) / 1000,
                            (_number(item['tStartMs']) + _number(item['dDurationMs'])) / 1000, _lines(text)))
    return out, None


def _read_type21(data, fps):
    out = []
    for item in _objects(data):
        stamp = item.get('timestamp')
        if isinstance(stamp, list) and len(stamp) == 2 and all(_number(value) is not None for value in stamp):
            out.append(_cue(_number(stamp[0]), _number(stamp[1]), _lines(item.get('text'))))
    return out, None


def _read_titulky(data, fps):
    return _pairs('s', 'd', 'n', end_is_duration=True)(data.get('titulky'), fps)


def _read_podcastindex(data, fps):
    out = []
    for item in _objects(data.get('segments')):
        if _numbers(item, 'startTime', 'endTime'):
            out.append(_cue(_number(item['startTime']), _number(item['endTime']), _lines(item.get('body')),
                            speaker=str(item.get('speaker') or '').strip()))
    return out, None


def _read_suburbia(data, fps):
    out = [_cue(_number(item['start']) / 1000, _number(item['end']) / 1000, _lines(item.get('text')))
           for item in _objects(data.get('subtitles')) if _numbers(item, 'start', 'end')]
    return out, None


def _read_videoindexer(data, fps):
    out = []
    for item in _objects(_find(data, 'transcript')):
        instances = _objects(item.get('instances'))
        if instances:
            start, end = _clock(instances[0].get('start')), _clock(instances[0].get('end'))
            if start is not None and end is not None:
                out.append(_cue(start, end, _lines(item.get('text'))))
    return out, None


def _read_archtime(data, fps):
    out = []
    for item in _objects(_find(data, 'BLOCKS')):
        if _numbers(item, 'time_start', 'time_end'):
            text = urllib.parse.unquote_plus(str(item.get('text') or ''))
            out.append(_cue(_number(item['time_start']) / 1000, _number(item['time_end']) / 1000, _lines(text)))
    return out, None


_CLQTT_RATES = {'FPS_2397': 24000 / 1001, 'FPS_2997': 30000 / 1001, 'FPS_5994': 60000 / 1001}


def _read_clqtt(data, fps):
    meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
    if meta and str(meta.get('timeFormat') or '').upper() not in ('', 'FRAMES'):
        raise ValueError(f'This CLQTT file counts its times in {meta.get("timeFormat")}, not in frames.')
    rate = str(meta.get('fps') or '').upper()
    if rate in _CLQTT_RATES:
        fps = _CLQTT_RATES[rate]
    elif re.fullmatch(r'FPS_\d+', rate):
        fps = int(rate[4:]) / 100.0
    out = []
    for item in _objects(data.get('events')):
        if str(item.get('eventType') or 'TIMED_TEXT_EVENT').upper() != 'TIMED_TEXT_EVENT' or not _numbers(item, 'start', 'end'):
            continue
        text = str(item.get('txt') or '').replace('\r\n', '\n')
        # Italics by character ranges, from the last so the earlier stay put.
        ranges = sorted(((int(style['from']), int(style['to'])) for style in _objects(item.get('styles'))
                         if str(style.get('type') or '').lower() == 'italic' and _numbers(style, 'from', 'to')),
                        reverse=True)
        for begin, finish in ranges:
            if 0 <= begin < finish <= len(text):
                text = text[:begin] + '<i>' + text[begin:finish] + '</i>' + text[finish:]
        text = _lines(text)
        if str(item.get('rgn') or '').lower() == 'top':
            text = '{\\an8}' + text
        out.append(_cue(_number(item['start']) / fps, _number(item['end']) / fps, text))
    return out, None


def _read_words_time_duration(data, fps):
    """Subtitle Edit's "Only load 1": words of a time, a duration and a
    name, each a subtitle, punctuation joined to the word before."""
    out = []
    for item in _objects(data.get('words')):
        if _numbers(item, 'time', 'duration'):
            text = _lines(item.get('name'))
            if text in ('.', '!', '?', ':') and out:
                out[-1]['text'] += text
                out[-1]['end'] = round(out[-1]['end'] + _number(item['duration']), 3)
                continue
            out.append(_cue(_number(item['time']), _number(item['time']) + _number(item['duration']), text))
    return out, None


def _capcut_text(content):
    """A text material's words: as it is, or (in newer drafts) the "text" of
    the JSON it holds."""
    text = str(content or '')
    if text.lstrip().startswith('{'):
        try:
            inner = json.loads(text)
            if isinstance(inner, dict) and isinstance(inner.get('text'), str):
                return inner['text']
        except ValueError:
            pass
    return text


def _read_capcut(data, fps):
    texts = {str(item.get('id')): _capcut_text(item.get('content'))
             for item in _objects(_find(data, 'texts')) if item.get('id') is not None}
    out = []
    for track in _objects(_find(data, 'tracks')):
        if track.get('type') != 'text':
            continue
        for segment in _objects(track.get('segments')):
            span = segment.get('target_timerange') or {}
            key = str(segment.get('material_id'))
            if key in texts and _numbers(span, 'start', 'duration'):
                start = _number(span['start']) / 1e6        # microseconds
                out.append(_cue(start, start + _number(span['duration']) / 1e6, _lines(texts[key])))
    return out, None


def _read_seriencamp(data, fps):
    out = []
    for block in _objects(_find(data, 'captiondata')):
        for item in _objects(block.get('data')):
            if _numbers(item, 'fromms', 'toms'):
                out.append(_cue(_number(item['fromms']) / 1000, _number(item['toms']) / 1000, _lines(item.get('caption'))))
    return out, None


def _read_gts(data, fps):
    out = []
    for item in _objects(data.get('subtitles')):
        words = []
        for content in _objects(item.get('content')):
            text = content.get('text')
            words += [str(piece) for piece in (text if isinstance(text, list) else [text]) if piece]
        start = _clock((item.get('tc_in') or {}).get('value'), fps)
        end = _clock((item.get('tc_out') or {}).get('value'), fps)
        if words and start is not None and end is not None:
            out.append(_cue(start, end, _lines(' '.join(words))))
    return out, None


_OOONA_ITALIC = re.compile(r'<span style="font-style:italic">(.*?)</span>', re.DOTALL)


def _read_ooona(data, fps):
    out = []
    for item in _objects(data.get('Paragraphs')):
        lines = [_OOONA_ITALIC.sub(r'<i>\1</i>', str(line.get('Text') or ''))
                 for line in _objects(item.get('Lines')) if line.get('Text')]
        if lines and _numbers(item, 'Start', 'End'):
            text = _lines(lines)
            if item.get('VAlign') == 'Top':
                text = '{\\an8}' + text
            out.append(_cue(_number(item['Start']), _number(item['End']), text))
    return out, None


def _read_gremots(data, fps):
    return _pairs('st', 'et', 'tt', 0.001)(data, fps)


# Telling them apart, by the shape of their keys.

def _items_have(data, *keys):
    items = _objects(data)
    return bool(items) and all(all(key in item for key in keys) for item in items)


def _is_twentythree_embed(data):
    if not isinstance(data.get('data'), str) or '"timestamp_begin"' not in data['data']:
        return False
    try:
        return isinstance(json.loads(data['data'], strict=False), dict)
    except ValueError:
        return False


def _type16(data):
    items = data.get('subtitles') if isinstance(data, dict) else data
    return _items_have(items, 'startTime', 'endTime', 'text') and isinstance(_objects(items)[0].get('startTime'), dict)


# id: (title, test, reader, writes): in the order they are tried, the most
# particular first. `writes` says Subtitle Edit writes it, and so does this
# module.
_DIALECTS = (
    ('wistia', 'Wistia', lambda d: isinstance(d, dict) and any(
        isinstance(t.get('hash'), dict) and isinstance(t['hash'].get('lines'), list) for t in _objects(d.get('captions'))),
     _read_wistia, True),
    ('ted', 'TED', lambda d: isinstance(d, dict) and _ted_captions(d) is not None
     and _items_have(_ted_captions(d).get('captions'), 'startTime', 'duration', 'content'), _read_ted, True),
    ('holistar', 'HoliStar', lambda d: isinstance(d, dict) and any(
        isinstance(t.get('SubtitleItems'), list) for t in _objects(d.get('SubtitleLanguages'))), _read_holistar, True),
    ('twentythree', 'TwentyThree', lambda d: isinstance(d, dict) and _items_have(
        d.get('subtitles'), 'timestamp_begin', 'timestamp_end'), _read_twentythree, True),
    ('twentythree-embed', 'TwentyThree (embedded)', lambda d: isinstance(d, dict) and _is_twentythree_embed(d),
     _read_twentythree_embed, True),
    ('twentythree-websrt', 'TwentyThree (embedded SRT)', lambda d: isinstance(d, dict) and isinstance(d.get('data'), dict)
     and isinstance(d['data'].get('websrt'), str), _read_twentythree_websrt, True),
    ('podcastindex', 'Podcasting 2.0 (PodcastIndex)', lambda d: isinstance(d, dict) and _items_have(
        d.get('segments'), 'startTime', 'endTime', 'body'), _read_podcastindex, True),
    ('suburbia', 'SubUrbia', lambda d: isinstance(d, dict) and isinstance(d.get('header'), dict)
     and _items_have(d.get('subtitles'), 'start', 'end', 'text'), _read_suburbia, True),
    ('videoindexer', 'Azure Video Indexer', lambda d: any(
        isinstance(item.get('instances'), list) and 'text' in item for item in _objects(_find(d, 'transcript'))),
     _read_videoindexer, True),
    ('archtime', 'Arctime project', lambda d: isinstance(d, dict) and 'atpjfile_info' in d, _read_archtime, False),
    ('clqtt', 'CLQTT', lambda d: isinstance(d, dict) and _items_have(d.get('events'), 'eventType', 'start', 'end'),
     _read_clqtt, False),
    ('capcut', 'CapCut draft', lambda d: isinstance(d, dict) and 'canvas_config' in d, _read_capcut, False),
    ('seriencamp', 'Seriencamp', lambda d: isinstance(d, dict) and _find(d, 'captiondata') is not None,
     _read_seriencamp, False),
    ('gts', 'GTS Pro', lambda d: isinstance(d, dict) and any(
        isinstance(item.get('tc_in'), dict) for item in _objects(d.get('subtitles'))), _read_gts, False),
    ('ooona', 'OOONA', lambda d: isinstance(d, dict) and _items_have(d.get('Paragraphs'), 'Start', 'End', 'Lines'),
     _read_ooona, False),
    ('type4', 'JSON Type 4', lambda d: _items_have(d, 'hitType', 'startTime', 'endTime', 'metadata'), _read_type4, True),
    ('type5', 'JSON Type 5', lambda d: isinstance(d, dict) and isinstance(d.get('text_tees'), list)
     and isinstance(d.get('text_content'), list), _read_type5, True),
    ('type6', 'JSON Type 6', lambda d: isinstance(d, dict) and isinstance(d.get('words'), list) and any(
        isinstance(word, list) and len(word) == 2 for word in d['words']), _read_type6, True),
    ('words-time-duration', 'JSON words (time, duration, name)', lambda d: isinstance(d, dict) and _items_have(
        d.get('words'), 'time', 'duration', 'name'), _read_words_time_duration, False),
    ('type7', 'JSON Type 7', lambda d: isinstance(d, dict) and all(isinstance(d.get(key), list) for key in ('start', 'end', 'text'))
     and 'segments' not in d, _read_type7, False),
    ('type10', 'JSON Type 10', lambda d: isinstance(d, dict) and _items_have(d.get('vostf'), 'startTime', 'endTime', 'text'),
     lambda d, fps: _pairs('startTime', 'endTime', 'text')(d['vostf'], fps), True),
    ('type13', 'JSON Type 13', lambda d: isinstance(d, dict) and isinstance(d.get('job'), dict)
     and _items_have(d.get('speakers'), 'time', 'duration', 'name'), _read_type13, False),
    ('type14', 'JSON Type 14', lambda d: isinstance(d, dict) and _items_have(d.get('captionGroups'), 't', 's', 'e'),
     _read_type14, True),
    ('type16', 'JSON Type 16', _type16, _read_type16, True),
    ('type23', 'JSON Type 23', lambda d: isinstance(d, dict) and _items_have(d.get('titulky'), 's', 'd', 'n'),
     _read_titulky, True),
    ('ted-list', 'JSON Type 3', lambda d: _items_have(d, 'startTime', 'duration', 'content'), _read_ted_list, True),
    ('type2', 'JSON Type 2', lambda d: _items_have(d, 'startMillis', 'endMillis', 'text'),
     _pairs('startMillis', 'endMillis', 'text', 0.001), True),
    ('type24', 'JSON Type 24', lambda d: _items_have(d, 'start_time', 'end_time', 'subtitle_content'),
     _pairs('start_time', 'end_time', 'subtitle_content', 0.001), True),
    ('type8', 'JSON Type 8', lambda d: bool(_list_with(d, 'start_time', 'end_time', 'text')), _read_start_end_time, True),
    ('type9', 'JSON Type 9', lambda d: _items_have(d, 'start', 'end', 'text') and all(
        isinstance(item['start'], str) and isinstance(item['text'], list) for item in _objects(d)), _read_type9, True),
    ('type11', 'JSON Type 11', lambda d: _items_have(d, 'milliseconds', 'line'), _read_type11, True),
    ('type12', 'JSON Type 12', lambda d: _items_have(d, 't1', 't2', 't'), _pairs('t1', 't2', 't', 0.001), True),
    ('type15', 'JSON Type 15', lambda d: _items_have(d, 'displayTimeOffset', 'text'), _read_type15, True),
    ('type17', 'JSON Type 17', lambda d: _items_have(d, 'start', 'end', 'text', 'index') and all(
        isinstance(item['text'], list) and _numbers(item, 'start', 'end') for item in _objects(d)), _read_type17, True),
    ('type18', 'JSON Type 18', lambda d: _items_have(d, 's', 'd', 'n'), _pairs('s', 'd', 'n', end_is_duration=True), True),
    ('type19', 'JSON Type 19', lambda d: _items_have(d, 'tStartMs', 'dDurationMs', 'segs'), _read_type19, True),
    ('type21', 'JSON Type 21', lambda d: _items_have(d, 'timestamp', 'text'), _read_type21, True),
    ('gremots', 'Gremots', lambda d: _items_have(d, 'st', 'et', 'tt'), _read_gremots, True),
)
# Written only: Google Play's json is YouTube's json3, read by json_subtitles;
# JSON Type 8b is 8 in milliseconds, which reading 8 tells apart.
_WRITE_ONLY = (('google-play', 'Google Play'), ('type8b', 'JSON Type 8b'))

DIALECTS = {name: title for name, title, _test, _reader, _writes in _DIALECTS}
DIALECTS.update(dict(_WRITE_ONLY))
READ_ONLY = tuple(name for name, _title, _test, _reader, writes in _DIALECTS if not writes)
WRITABLE = tuple(name for name in DIALECTS if name not in READ_ONLY)
_READERS = {name: reader for name, _title, _test, reader, _writes in _DIALECTS}


def kind(data):
    """Which dialect (a key of DIALECTS) the JSON document `data` is, or None.

    Ask json_subtitles.kind first: what it knows is never one of these.
    Subtitld's own shape is refused here too, but for JSON Type 17's."""
    from subtitld.modules import json_subtitles
    if json_subtitles.kind(data) is not None:
        return None
    for name, _title, test, _reader, _writes in _DIALECTS:
        try:
            found = test(data)
        except (AttributeError, TypeError, KeyError, IndexError):
            found = False
        if found:
            if name != 'type17' and json_subtitles.is_subtitlds(data):
                return None
            return name
    return None


def looks_like(text):
    """Which dialect a JSON file's decoded text is, or None (see kind)."""
    from subtitld.modules import json_subtitles
    try:
        return kind(json_subtitles.loads(text))
    except ValueError:
        return None


def read(data, dialect=None, fps=None):
    """(subtitles, language or None, dialect) of the JSON document `data`,
    in `dialect` or the one `kind` finds; frames count at `fps`, else 25.
    ValueError when it is none of them, or holds no subtitles."""
    dialect = dialect or kind(data)
    if dialect not in _READERS:
        raise ValueError('This JSON is none of the platform dialects Subtitld knows')
    try:
        cues, language = _READERS[dialect](data, float(fps or DEFAULT_FPS))
    except (AttributeError, TypeError, KeyError, IndexError, ZeroDivisionError) as error:
        raise ValueError(f'This {DIALECTS[dialect]} JSON is damaged: {error}') from error
    cues = [cue for cue in cues if cue['text'].strip() and cue['end'] >= cue['start']]
    cues.sort(key=lambda cue: (cue['start'], cue['end']))
    return cues, (str(language) if language else None), dialect


# Writing

def _plain(segment):
    return markup.plain(markup.split_lead(str(segment.get('text') or ''))[1]).strip()


def _seconds(value):
    value = round(float(value), 3)
    return int(value) if value == int(value) else value


def _ms(value):
    return int(round(float(value) * 1000))


def _frames(seconds, fps):
    """{"h", "m", "s", "f"} of `seconds` at `fps`, as JSON Type 16 writes it."""
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= round(fps):
        whole, frames = whole + 1, 0
    return {'f': frames, 'h': whole // 3600, 'm': whole // 60 % 60, 's': whole % 60}


def _clock_text(seconds, separator=','):
    millis = _ms(seconds)
    return '%02d:%02d:%02d%s%03d' % (millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, separator, millis % 1000)


def _short_clock(seconds):
    """Subtitle Edit's short time: "1,500", "1:02,500", "1:01:02,500"."""
    millis = _ms(seconds)
    hours, minutes, whole, part = millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, millis % 1000
    if hours:
        return '%d:%02d:%02d,%03d' % (hours, minutes, whole, part)
    if minutes:
        return '%d:%02d,%03d' % (minutes, whole, part)
    return '%d,%03d' % (whole, part)


def _br(text):
    """A text's lines joined as Subtitle Edit's EncodeJsonText joins them."""
    return text.replace('\n', '<br />')


def write(segments, dialect, language='en', fps=None):
    """The JSON text of `segments` in `dialect` (one of WRITABLE)."""
    if dialect not in WRITABLE:
        raise ValueError(f'Subtitld does not write {DIALECTS.get(dialect, dialect)} JSON')
    fps = float(fps or DEFAULT_FPS)
    language = (language or 'en').replace('_', '-')
    cues = [(float(s.get('start', 0)), float(s.get('end', 0)), _plain(s), str(s.get('speaker') or ''))
            for s in segments]
    cues = sorted((cue for cue in cues if cue[2]), key=lambda cue: cue[:2])
    if dialect == 'wistia':
        data = {'captions': [{'bcp47LanguageTag': language, 'hasCaptions': True, 'hash': {'lines': [
            {'start': _seconds(start), 'end': _seconds(end), 'text': text.split('\n')} for start, end, text, _s in cues]}}]}
    elif dialect in ('twentythree', 'twentythree-embed'):
        data = {'subtitles': [{'text': text.split('\n'), 'timestamp_begin': _seconds(start), 'timestamp_end': _seconds(end)}
                              for start, end, text, _s in cues]}
        if dialect == 'twentythree-embed':
            data = {'data': json.dumps(data, ensure_ascii=False)}
    elif dialect == 'twentythree-websrt':
        from subtitld.modules import srt
        data = {'data': {'websrt': srt.write([{'start': start, 'end': end, 'text': text} for start, end, text, _s in cues])}}
    elif dialect == 'holistar':
        data = {'SubtitleLanguages': [{'IsForced': False, 'ClassName': language, 'SubtitleItems': [
            {'TextLines': text.split('\n'), 'ClassName': language, 'ShowTime': _ms(start), 'HideTime': _ms(end)}
            for start, end, text, _s in cues]}], 'ReturnCode': {'Id': 1, 'Code': 'SUCCESS'}}
    elif dialect == 'ted':
        data = {'subtitle': [{'captions': [
            {'duration': _ms(end) - _ms(start), 'content': _br(text), 'startOfParagraph': True, 'startTime': _ms(start)}
            for start, end, text, _s in cues], 'lang': language}], 'introDuration': 0}
    elif dialect == 'ted-list':
        data = [{'duration': _ms(end) - _ms(start), 'content': _br(text), 'startOfParagraph': False, 'startTime': _ms(start)}
                for start, end, text, _s in cues]
    elif dialect == 'google-play':
        data = {'events': [{'tStartMs': _ms(start), 'dDurationMs': _ms(end) - _ms(start), 'segs': [{'utf8': text}]}
                           for start, end, text, _s in cues]}
    elif dialect == 'podcastindex':
        data = {'version': '1.0.0', 'segments': [
            dict(({'speaker': speaker} if speaker else {}), startTime=_seconds(start), endTime=_seconds(end), body=text)
            for start, end, text, speaker in cues]}
    elif dialect == 'suburbia':
        data = {'header': {'subtitleId': str(uuid.uuid4())},
                'subtitles': [{'start': _ms(start), 'end': _ms(end), 'text': _br(text)} for start, end, text, _s in cues]}
    elif dialect == 'videoindexer':
        data = {'transcript': [{'id': index, 'text': _br(text), 'instances': [
            {'start': _clock_text(start, '.'), 'end': _clock_text(end, '.')}]} for index, (start, end, text, _s) in enumerate(cues)]}
    elif dialect == 'type2':
        data = [{'startMillis': _ms(start), 'endMillis': _ms(end), 'text': _br(text)} for start, end, text, _s in cues]
    elif dialect == 'type4':
        track, kind_id = str(uuid.uuid4()), uuid.uuid4().hex[:24]
        data = [{'hitType': 'tag', 'subTrack': None, 'tags': [], 'track': 'Closed Captioning', 'startTime': _seconds(start),
                 'guid': track, 'segmentTypeId': kind_id, 'endTime': _seconds(end), 'id': uuid.uuid4().hex[:24],
                 'metadata': {'Text': _br(text), 'ID': '', 'Language': language}} for start, end, text, _s in cues]
    elif dialect == 'type5':
        times, contents = [], []
        for index, (start, end, text, _s) in enumerate(cues):
            times += [_ms(start), _ms(end)]
            contents.append(text.split('\n'))
            if index < len(cues) - 1:
                contents.append(['', ''])
        data = {'text_tees': times, 'text_target': [['w1', 'w3'], ['w1', 'w3']] * len(cues), 'text_content': contents,
                'text_styles': [['s1', 's2'], ['s1', 's2']] * len(cues),
                'timerange': [_ms(cues[0][0]) if cues else 0, _ms(cues[-1][1]) if cues else 0]}
    elif dialect == 'type6':
        words = []
        for index, (start, end, text, _s) in enumerate(cues):
            line = ' '.join(text.split())
            total = 0.0
            for word in line.split(' '):
                # Subtitle Edit's share of the time: by the word's length.
                words.append([str(_ms(start + total * (end - start) / 100.0)), word])
                total += (len(word) + 1.0) * 100 / len(line)
            following = cues[index + 1] if index + 1 < len(cues) else None
            if following is None or following[0] - 0.2 < end:
                words.append([str(_ms(end)), ''])
        data = {'words': words, 'paragraphs': [], 'speakers': {}}
    elif dialect in ('type8', 'type8b'):
        scale = _seconds if dialect == 'type8' else _ms
        data = [{'start_time': scale(start), 'end_time': scale(end), 'text': _br(text)} for start, end, text, _s in cues]
    elif dialect == 'type9':
        data = [{'index': index, 'start': _clock_text(start), 'end': _clock_text(end), 'text': text.split('\n')}
                for index, (start, end, text, _s) in enumerate(cues, 1)]
    elif dialect == 'type10':
        data = {'vostf': [{'startTime': _seconds(start), 'endTime': _seconds(end), 'positionAlign': 'middle',
                           'lineAlign': 'start', 'text': _br(text)} for start, end, text, _s in cues]}
    elif dialect == 'type11':
        data = [{'milliseconds': _ms(start), 'line': _br(text)} for start, _end, text, _s in cues]
    elif dialect == 'type12':
        data = [{'t1': _ms(start), 't2': _ms(end), 't': _br(text)} for start, end, text, _s in cues]
    elif dialect == 'type14':
        data = {'captionGroups': [{'t': text, 's': _seconds(start), 'e': _seconds(end)} for start, end, text, _s in cues]}
    elif dialect == 'type15':
        data = [{'displayTimeOffset': _seconds(start), 'text': _br(text)} for start, _end, text, _s in cues]
    elif dialect == 'type16':
        data = [{'endTime': _frames(end, fps), 'id': uuid.uuid4().hex[-10:], 'isSequelToNextSubtitle': False,
                 'isSequelToPrevSubtitle': False, 'isStartingNewSmartText': False, 'speechOptions': None,
                 'startTime': _frames(start, fps), 'text': _br(text)} for start, end, text, _s in cues]
    elif dialect == 'type17':
        data = [{'text': text.split('\n'), 'index': index, 'start': _ms(start), 'end': _ms(end)}
                for index, (start, end, text, _s) in enumerate(cues)]
    elif dialect in ('type18', 'type23'):
        data = [{'s': _seconds(start), 'd': _seconds(end - start), 'n': text} for start, end, text, _s in cues]
        if dialect == 'type23':
            data = {'titulky': data}
    elif dialect == 'type19':
        data = [{'tStartMs': _ms(start), 'dDurationMs': _ms(end) - _ms(start), 'segs': [{'utf8': text}]}
                for start, end, text, _s in cues]
    elif dialect == 'type21':
        data = [{'timestamp': [_seconds(start), _seconds(end)], 'text': text} for start, end, text, _s in cues]
    elif dialect == 'type24':
        data = [{'id': index, 'start_time': _ms(start), 'end_time': _ms(end), 'subtitle_id': index, 'subtitle_content': text}
                for index, (start, end, text, _s) in enumerate(cues, 1)]
    elif dialect == 'gremots':
        data = [{'st': _ms(start), 'et': _ms(end), 'tt': _br(text)} for start, end, text, _s in cues]
    else:                                                 # pragma: no cover - WRITABLE and this list agree
        raise ValueError(dialect)
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def save(path, segments, dialect, language='en', fps=None):
    with open(path, 'w', encoding='utf-8', newline='\n') as json_file:
        json_file.write(write(segments, dialect, language=language, fps=fps))
