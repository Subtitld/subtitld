"""SubViewer as YouTube writes it (.sbv): read what YouTube Studio exports
and people edit, write what it takes.

A subtitle is a timing line, "0:00:00.599,0:00:04.160", its text, and a
blank line. There is no number, no styling, and no place on the screen.

Reading forgives:

- the bytes are decoded as SRT's are;
- subtitles are found by their timing lines, so a missing or extra blank
  line reads right; hours of any number of digits, a comma or none for
  the milliseconds' dot, fewer or more than three decimals, spaces
  around the comma between the times;
- SubViewer 2's "[br]" breaks a line;
- a subtitle that begins ">> NAME: ", as YouTube asks speakers to be
  written, has NAME as its speaker and the rest as its text, and so do
  the ones after it until the next ">>" (one with no name ends it, and
  stays in the text);
- formatting tags written into the text stay as Subtitld's;
- the subtitles come sorted by start.

Writing gives YouTube's own layout: times as "H:MM:SS.mmm", the plain
text (SBV has no formatting), no empty subtitles and no blank line inside
one, and, when asked, ">> NAME: " where a new speaker begins, as YouTube
recommends.
"""

import re

from subtitld.modules import markup
from subtitld.modules.srt import decode

_STAMP = r'(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?'
_TIMING = re.compile(r'^\s*' + _STAMP + r'\s*,\s*' + _STAMP + r'\s*$')
_SPEAKER = re.compile(r'^>>\s*([^:<>]{1,40}?)\s*:\s+(\S.*)$', re.S)


def _seconds(hours, minutes, seconds, fraction):
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / 10 ** len(fraction)
    return round(value, 3)


def parse(text):
    """The subtitles in SBV text: [{'start', 'end', 'text'[, 'speaker']}], by start."""
    lines = text.split('\n')
    timings = [(index, match) for index, line in enumerate(lines) if (match := _TIMING.match(line))]
    segments = []
    for position, (index, match) in enumerate(timings):
        stop = timings[position + 1][0] if position + 1 < len(timings) else len(lines)
        body = '\n'.join(line.strip() for line in lines[index + 1:stop]).replace('[br]', '\n')
        body = '\n'.join(line.strip() for line in body.split('\n') if line.strip())
        if not markup.plain(body).strip():
            continue
        start = _seconds(*match.groups()[0:4])
        segment = {'start': start, 'end': max(start, _seconds(*match.groups()[4:8])), 'text': body}
        said = _SPEAKER.match(body)
        if said and len(said.group(1).split()) <= 4:
            segment['speaker'], segment['text'] = said.group(1).strip(), said.group(2).strip()
        elif body.startswith('>>'):
            segment['speaker'] = None           # a new speaker, not named
        segments.append(segment)
    segments.sort(key=lambda segment: segment['start'])
    # ">>" marks a change of speaker: until the next one, the same speaks.
    speaker = None
    for segment in segments:
        if 'speaker' in segment:
            speaker = segment.pop('speaker')
        if speaker:
            segment['speaker'] = speaker
    return segments


def read(path):
    """The subtitles in the SBV file at `path`."""
    with open(path, 'rb') as sbv_file:
        return parse(decode(sbv_file.read()))


def format_time(seconds):
    """SBV time for `seconds`: "H:MM:SS.mmm", to the nearest millisecond."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours}:{minutes:02d}:{secs:02d}.{millis:03d}'


def write(segments, speakers=False):
    """SBV text for `segments`; with `speakers`, ">> NAME: " where a new
    speaker begins."""
    blocks = []
    previous = ''
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        text = markup.plain(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            continue
        speaker = str(segment.get('speaker') or '').strip()
        if speakers and speaker and speaker != previous:
            lines[0] = f'>> {speaker}: {lines[0]}'
        previous = speaker
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        blocks.append(f'{format_time(start)},{format_time(end)}\n' + '\n'.join(lines))
    return '\n\n'.join(blocks) + '\n' if blocks else ''


def save(path, segments, speakers=False):
    """Write `segments` to `path` as SBV, in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as sbv_file:
        sbv_file.write(write(segments, speakers))
