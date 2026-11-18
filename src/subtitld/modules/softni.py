"""SoftNi (.sub): the text subtitle files of SoftNi's subtitling tools,
read and written, in both of their punctuations of the time code.

SoftNi documents neither in public. What is here follows Subtitle Edit
(libse, SoftNiSub.cs and SoftNiColonSub.cs, GPL-3 as Subtitld is, and
SoftNiSubTest.cs), whose two readers differ in the time code alone:

    *PART 1*
    00:00:00.00\\00:00:00.00
    }[Top and italic]
    01:00:01.12\\01:00:03.00
    —Peter.
    —[Estoy] de licencia.
    01:48:50.07\\01:48:52.01
    *END*
    01:48:53.00\\01:48:53.00
    *CODE*
    ...
    *TIMING*
    1 25 0
    ...

A subtitle's text comes before its times, "start\\end" as HH:MM:SS.FF (or
HH:MM:SS:FF in the colon variant); lines starting with "*" are headers and
sections, the first times (after *PART 1*) have no text, and the times
after *END* close the file. "[" opens italics and "]" closes them, each
line opening them again where they go on; "}" at the start puts the
subtitle at the top. The frames count within the second at the rate the
*TIMING* section's second number says (Subtitle Edit writes the video's
there, rounded), else the video's, else 25.

Writing gives what Subtitle Edit writes: the *PART 1* line and its empty
times, a subtitle a block, *END* a second after the last subtitle, and
the sections after it as Subtitle Edit fills them, *TIMING* with the rate;
CRLF, in UTF-8 with a byte order mark, as Subtitle Edit saves it (what
SoftNi's own tools expect of the encoding is not known; the *FORMAT
SAMPLE* line, ÅåÉéÌìÕõÛûÿ, is there for them to tell). Only the top of the
nine places is kept, and literal "[" and "]" in the text, which the
format has no way to write, come back as italics.
"""

import re

from subtitld.modules import markup, srt

DEFAULT_FPS = 25.0
PERIOD, COLON = '.', ':'
_TIMES = re.compile(r'^(\d\d):(\d\d):(\d\d)([.:])(\d\d)\\(\d\d):(\d\d):(\d\d)[.:](\d\d)$')
_TOP = '}'
_SECTIONS = ('*CODE*', '0000000000000000', '*CAST*', '*GENERATOR*', '*FONTS*', '*READ*',
             '0,300 15,000 130,000 100,000 25,000', '*TIMING*', '1 {fps} 0', '*TIMED BACKUP NAME*', 'C:\\',
             '*FORMAT SAMPLE ÅåÉéÌìÕõÛûÿ*', '*READ ADVANCED*', '< > 1 1 0,300', '*MARKERS*')


def looks_like(head):
    """The punctuation ('.' or ':') of the SoftNi file `head` (its decoded
    text, or its first bytes) begins as, or None when it is not one: a
    *PART line or a section, and a line of SoftNi times."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:16384]).decode('latin-1')
    lines = [line.strip() for line in str(head or '')[:16384].splitlines()]
    times = next((match for match in map(_TIMES.match, lines) if match), None)
    if times and any(line.startswith('*PART') or line in ('*END*', '*CODE*', '*TIMING*') for line in lines):
        return times.group(4)
    return None


def _runs(lines):
    """[(text, Style)] for a subtitle's lines: "[" to "]" italic, each line
    on its own."""
    parsed = []
    for line in lines:
        italic, out = False, []
        for char in line:
            if char == '[':
                italic = True
            elif char == ']':
                italic = False
            else:
                out.append((char, markup.Style(italic=italic)))
        parsed.append(out)
    runs = []
    for number, line in enumerate(parsed):
        if number:
            # Italics that end a line and open the next are one stretch, as
            # Subtitle Edit joins "</i>" and "<i>" around a line break.
            joined = runs and line and runs[-1][1].italic and line[0][1].italic
            runs.append(('\n', markup.Style(italic=True) if joined else markup.PLAIN))
        runs.extend(line)
    return runs


def parse(text, fps=None):
    """(subtitles, {'fps', 'separator'}) in a SoftNi file's text: the rate
    its frames were read at and the punctuation of its times."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    for index, line in enumerate(lines[:-1]):
        if line.strip() == '*TIMING*':
            numbers = lines[index + 1].split()
            try:
                fps = float(numbers[1]) if len(numbers) > 1 and float(numbers[1]) > 0 else fps
            except ValueError:
                pass
            break
    fps = float(fps or DEFAULT_FPS)
    segments, pending, timed, separator = [], [], False, None
    for line in lines:
        match = _TIMES.match(line.strip())
        if match:
            separator = separator or match.group(4)
            numbers = [int(match.group(group)) for group in (1, 2, 3, 5, 6, 7, 8, 9)]
            start = round(numbers[0] * 3600 + numbers[1] * 60 + numbers[2] + numbers[3] / fps, 3)
            end = round(numbers[4] * 3600 + numbers[5] * 60 + numbers[6] + numbers[7] / fps, 3)
            body_lines = [part.strip() for part in pending if part.strip()]
            top = bool(body_lines) and body_lines[0].startswith(_TOP)
            if top:
                body_lines[0] = body_lines[0][1:]
            body = markup.tidy(_runs(body_lines))
            if markup.plain(body).strip():
                segments.append({'start': start, 'end': max(start, end),
                                 'text': (markup.with_alignment('', 8) if top else '') + body})
            pending, timed = [], True
        elif not line.strip() or line.startswith('*'):
            continue
        elif timed:
            pending.append(line)
    return segments, {'fps': fps, 'separator': separator or PERIOD}


def read(path, fps=None):
    """(subtitles, its settings) in the SoftNi file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as sub_file:
        return parse(srt.decode(sub_file.read()), fps)


def _time(seconds, fps, separator):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= int(round(fps)):
        whole, frames = whole + 1, 0
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{second:02d}{separator}{frames:02d}'


def _line(runs):
    """A line in SoftNi's text: italics between "[" and "]"."""
    out, italic = '', False
    for run, style in runs:
        if style.italic and not italic and run.strip():
            out += '['
            italic = True
        elif not style.italic and italic:
            out += ']'
            italic = False
        out += run
    if italic:
        out = out.rstrip() + ']'
    return out.strip()


def write(segments, fps=DEFAULT_FPS, separator=PERIOD):
    """SoftNi text for `segments`, frames at `fps`, the times punctuated
    with `separator` ('.' or ':')."""
    fps = float(fps or DEFAULT_FPS)
    separator = COLON if separator == COLON else PERIOD
    zero = _time(0, fps, separator)
    out = ['*PART 1*', f'{zero}\\{zero}']
    last_end = None
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        lines = [[]]
        for run, style in markup.without_readings(markup.spans(rest)):
            for index, part in enumerate(run.split('\n')):
                if index:
                    lines.append([])
                if part:
                    lines[-1].append((part, style))
        written = [line for line in map(_line, lines) if line]
        if not written:
            continue
        if markup.alignment(lead) in (7, 8, 9):
            written[0] = _TOP + written[0]
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        out.extend(written)
        out.append(f'{_time(start, fps, separator)}\\{_time(end, fps, separator)}')
        last_end = end if last_end is None else max(last_end, end)
    out.append('*END*')
    if last_end is None:
        out.append('...........\\...........')
    else:
        closing = _time(int(last_end + 1), fps, separator)
        out.append(f'{closing}\\{closing}')
    out.extend(line.format(fps=int(round(fps))) for line in _SECTIONS)
    return '\r\n'.join(out) + '\r\n'


def save(path, segments, fps=DEFAULT_FPS, separator=PERIOD):
    """Write `segments` to `path` as SoftNi, in UTF-8 with a byte order
    mark."""
    with open(path, 'w', encoding='utf-8-sig', newline='') as sub_file:
        sub_file.write(write(segments, fps, separator))
