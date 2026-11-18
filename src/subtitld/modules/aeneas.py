"""Aeneas's sync map in JSON (.json): what the aeneas forced aligner
writes when it times a text against its audio.

What is here follows aeneas's own code, its SyncMap.json_string (what it
writes) and SyncMapFormatJSON (what it reads, syncmap/smfjson.py), and
Subtitle Edit's JsonAeneas.cs:

    {"fragments": [
     {"begin": "0.000", "children": [], "end": "1.320", "id": "f000001",
      "language": "eng", "lines": ["First line", "second line"]}
    ]}

A fragment's begin and end are seconds as text ("SS.mmm", aeneas's
time_to_ssmmm), its lines the lines of its text, its language ISO 639-3.
A fragment of a multilevel map holds its finer ones (sentences, words) as
"children". Reading gives a subtitle a fragment of the top level with
text, its lines its lines, and a fragment with no lines of its own its
children instead; the first fragment's language is the file's. Writing
gives a fragment a subtitle, as aeneas writes them (keys in order, ids
f000001 on, no children), its plain text, a line a line.

.json is also every speech recogniser's: `looks_like` takes decoded text
(or the parsed document) and says yes to a top-level "fragments" list of
objects with begin, end and lines, which none of the JSONs that
modules.json_subtitles reads has.
"""

import json
import re

from subtitld.modules.transcripts import iso639_3, plain_text


def _document(head):
    if isinstance(head, (dict, list)):
        return head
    try:
        return json.loads(str(head or '').lstrip('﻿'), strict=False)
    except ValueError:
        return None


def looks_like(head):
    """Whether decoded text, or a parsed JSON document, is an aeneas sync
    map. Text cut short still counts when it opens as one."""
    data = _document(head)
    if data is None and isinstance(head, str):
        return bool(re.match(r'\s*\{\s*"fragments"\s*:\s*\[\s*(\{|\])', head.lstrip('﻿')))
    if not isinstance(data, dict) or not isinstance(data.get('fragments'), list):
        return False
    fragments = data['fragments']
    return all(isinstance(item, dict) and {'begin', 'end', 'lines'} <= set(item) for item in fragments)


def _seconds(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse(data):
    """(subtitles, the language the fragments are in or None) of an aeneas
    sync map (bytes, text or the parsed document); ValueError when it is
    not one."""
    from subtitld.modules import srt
    document = data if isinstance(data, dict) else _document(srt.decode(data) if isinstance(data, bytes) else data)
    if not looks_like(document):
        raise ValueError('Not an aeneas sync map: no "fragments" of begin, end and lines')
    subtitles, languages = [], []

    def take(fragments):
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            lines = [str(line).strip() for line in fragment.get('lines') or [] if str(line).strip()]
            start, end = _seconds(fragment.get('begin')), _seconds(fragment.get('end'))
            if not lines:
                take(fragment.get('children') or [])
                continue
            if start is None or end is None:
                continue
            subtitles.append({'start': round(start, 3), 'end': round(max(start, end), 3), 'text': '\n'.join(lines)})
            if fragment.get('language'):
                languages.append(str(fragment['language']))

    take(document['fragments'])
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return subtitles, (languages[0] if languages else None)


def read(path):
    with open(path, 'rb') as json_file:
        return parse(json_file.read())


def write(segments, language='en'):
    """An aeneas sync map of `segments`, the fragments in `language`."""
    lang = iso639_3(language or 'en')
    fragments = []
    for segment in sorted(segments, key=lambda segment: float(segment.get('start', 0))):
        lines = [line.strip() for line in plain_text(segment).split('\n') if line.strip()]
        if lines:
            fragments.append({'begin': '%.3f' % float(segment['start']), 'children': [],
                              'end': '%.3f' % float(segment['end']), 'id': 'f%06d' % (len(fragments) + 1),
                              'language': lang, 'lines': lines})
    return json.dumps({'fragments': fragments}, indent=1, sort_keys=True, ensure_ascii=False) + '\n'


def save(path, segments, language='en'):
    with open(path, 'w', encoding='utf-8', newline='\n') as json_file:
        json_file.write(write(segments, language))
