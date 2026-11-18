"""Audacity's labels (.txt): a label track exported as text, as the
Audacity manual's "Importing and Exporting Labels" describes it.

A line a label, its start and end in seconds and its text, parted by
tabs; a point label has the same start and end. In the extended form a
label's line is followed by one of its frequency range, a backslash first:

    2.150000	2.150000	a point label
    3.400000	6.100000	a region label
    \\	1484.669312	2969.338523

Reading gives a subtitle a label with text: a point label lasts until the
next label, at most TAIL seconds; a label of no text and the frequency
lines are left out. Decimal commas, which some Audacity builds wrote, read
too. Writing gives a region label a subtitle, as Audacity writes them (six
decimals) and as Subtitle Edit does (AudacityLabels.cs): plain text, its
lines and tabs as spaces, as a label holds one line.

.txt is every plain text's extension: `looks_like` takes decoded text and
says yes only when every line is a label (or a frequency line).
"""

import re

from subtitld.modules.transcripts import TAIL, plain_text

_NUMBER = r'-?\d+(?:[.,]\d+)?'
_LABEL = re.compile(rf'({_NUMBER})\t({_NUMBER})(?:\t(.*))?$')
_FREQUENCIES = re.compile(rf'\\\t{_NUMBER}\t{_NUMBER}$')


def looks_like(head):
    """Whether decoded text (its start is enough; a last line cut short
    is not held against it) is Audacity's labels."""
    lines = [line.rstrip('\r') for line in str(head or '').lstrip('﻿').split('\n') if line.strip()]
    if len(lines) > 1:
        lines = lines[:-1] if not str(head).endswith('\n') else lines
    return bool(lines) and all(_LABEL.match(line) or _FREQUENCIES.match(line) for line in lines) and any(
        _LABEL.match(line) for line in lines)


def _seconds(value):
    return float(value.replace(',', '.'))


def parse(data):
    """The subtitles of Audacity's labels (bytes or text); ValueError when
    a line is not a label."""
    from subtitld.modules import srt
    text = srt.decode(data) if isinstance(data, bytes) else str(data).lstrip('﻿')
    labels = []
    for number, line in enumerate(text.split('\n'), 1):
        line = line.rstrip('\r')
        if not line.strip() or _FREQUENCIES.match(line):
            continue
        match = _LABEL.match(line)
        if not match:
            raise ValueError(f'Line {number} is not an Audacity label: {line[:60]}')
        labels.append((_seconds(match.group(1)), _seconds(match.group(2)), (match.group(3) or '').strip()))
    labels.sort(key=lambda label: label[0])
    subtitles = []
    for index, (start, end, label) in enumerate(labels):
        if not label:
            continue
        if end <= start:
            following = next((later for later, _end, _text in labels[index + 1:] if later > start), None)
            end = min(following, start + TAIL) if following is not None else start + TAIL
        subtitles.append({'start': round(start, 3), 'end': round(end, 3), 'text': label})
    return subtitles


def read(path):
    with open(path, 'rb') as label_file:
        return parse(label_file.read())


def write(segments):
    """Audacity's labels of `segments`: a region label each."""
    out = []
    for segment in sorted(segments, key=lambda segment: float(segment.get('start', 0))):
        text = ' '.join(plain_text(segment).replace('\t', ' ').split())
        if text:
            out.append(f"{float(segment['start']):.6f}\t{float(segment['end']):.6f}\t{text}")
    return '\n'.join(out) + ('\n' if out else '')


def save(path, segments):
    with open(path, 'w', encoding='utf-8', newline='\n') as label_file:
        label_file.write(write(segments))
