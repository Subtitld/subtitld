"""WebVTT (.vtt): read the files browsers and players read, write what they show.

Reading forgives, as for SRT (modules.srt):

- the bytes are decoded as SRT's are (a byte order mark, else UTF-8, else
  the detected encoding), with any line ends;
- the header ("WEBVTT", a title, header lines) and the NOTE, STYLE and
  REGION blocks are not subtitles; the colours a STYLE block gives classes
  (``::cue(.gold) { color: #ffd700 }``) are kept for the cues;
- cues are found by their timing lines: an identifier before one is
  skipped, hours may be left out, a comma reads as the dot; the cue settings
  give where the subtitle sits (``line:``, ``align:``, ``position:``);
- the cue text keeps <i>, <b> and <u>; <c.class> takes the colour its class
  has (WebVTT's own colour classes, or a STYLE block's); <v Name> is the
  subtitle's speaker; <lang>, ruby's readings (<rt>) and karaoke
  timestamps go; entities (&amp;, &lt;, &nbsp;, ...) are characters.

Writing gives "WEBVTT" and the cues in order of start: times as
"HH:MM:SS.mmm"; italic, bold and underline as tags (WebVTT has no
strikeout); a colour as a class, WebVTT's own where it is one of them,
else one a STYLE block defines; the position as cue settings; the speaker
as a voice tag when asked; &, < and > escaped; no blank line in a cue and
no "-->" in its text.
"""

import html
import re

from subtitld.modules import markup
from subtitld.modules.srt import decode

_STAMP = r'(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?'
_TIMING = re.compile(r'^\s*' + _STAMP + r'\s*-{1,2}>\s*' + _STAMP + r'(.*)$')
_BLOCK = re.compile(r'^(NOTE|STYLE|REGION)(?:[ \t]|$)')
_TAG = re.compile(r'<(/?)([a-zA-Z]*)((?:\.[\w-]+)*)(?:[ \t]+([^>]*))?>|<(\d[\d:.]*)>')
_CUE_RULE = re.compile(r'::cue\(\s*(?:c)?\.([\w-]+)\s*\)\s*\{([^}]*)\}')
_CSS_COLOR = re.compile(r'(?:^|;)\s*color\s*:\s*([^;]+)')

# WebVTT's own colour classes, which players colour without a STYLE block.
DEFAULT_CLASSES = {
    'white': '#ffffff', 'lime': '#00ff00', 'cyan': '#00ffff', 'red': '#ff0000',
    'yellow': '#ffff00', 'magenta': '#ff00ff', 'blue': '#0000ff', 'black': '#000000',
}


def _seconds(hours, minutes, seconds, fraction):
    value = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / 10 ** len(fraction)
    return round(value, 3)


def _style_classes(css):
    """{class: '#rrggbb'} for the ::cue(.class) rules of a STYLE block."""
    classes = {}
    for match in _CUE_RULE.finditer(css):
        color = _CSS_COLOR.search(match.group(2))
        if color and markup.color_hex(color.group(1).strip()):
            classes[match.group(1)] = markup.color_hex(color.group(1).strip())
    return classes


def _keypad(settings):
    """Where cue settings put a subtitle, as a keypad number (2: bottom centre)."""
    values = dict(token.split(':', 1) for token in settings.split() if ':' in token)
    row, column = 0, 1                                     # bottom, centre
    line = values.get('line', '').split(',')[0]
    if line.endswith('%'):
        try:
            percent = float(line[:-1])
        except ValueError:
            percent = 100
        row = 2 if percent <= 33 else 1 if percent < 66 else 0
    elif re.fullmatch(r'-?\d+', line):
        row = 2 if int(line) >= 0 else 0
    align = values.get('align', '')
    if align in ('start', 'left'):
        column = 0
    elif align in ('end', 'right'):
        column = 2
    elif not align and values.get('position', '').split(',')[0].endswith('%'):
        try:
            percent = float(values['position'].split(',')[0][:-1])
            column = 0 if percent < 33 else 2 if percent > 66 else 1
        except ValueError:
            pass
    return (1, 4, 7)[row] + column


def cue_text(text, classes=None):
    """(text with tags, speaker or None) for a cue's WebVTT text."""
    classes = dict(DEFAULT_CLASSES, **(classes or {}))
    out = []
    stack = []                                  # [(tag name, what closes what it opened)]
    speaker = None
    in_rt = 0
    position = 0
    for match in _TAG.finditer(text):
        if match.start() > position and not in_rt:
            out.append(html.unescape(text[position:match.start()]))
        position = match.end()
        if match.group(5):                      # a karaoke timestamp
            continue
        closing, name, dotted = match.group(1), match.group(2).lower(), match.group(3)
        if closing:
            if name == 'rt':
                in_rt = max(0, in_rt - 1)
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == name:
                    # Close down to it, then open again what was inside it
                    # and is still open, so the tags stay well nested.
                    for item in reversed(stack[index:]):
                        out.append(item[1])
                    reopen = stack[index + 1:]
                    del stack[index:]
                    for item in reopen:
                        out.append(item[2])
                        stack.append(item)
                    break
            continue
        if name == 'rt':
            in_rt += 1
            continue
        opened, closer = '', ''
        if name in ('i', 'b', 'u'):
            opened, closer = f'<{name}>', f'</{name}>'
        elif name == 'c':
            color = next((classes[c] for c in dotted.split('.') if c in classes), '')
            if color:
                opened, closer = f'<font color="{color}">', '</font>'
        elif name == 'v':
            voice = html.unescape((match.group(4) or '').strip())
            if voice and speaker is None:
                speaker = voice
            elif voice and voice != speaker:
                out.append(f'{voice}: ')
        out.append(opened)
        stack.append((name, closer, opened))
    if position < len(text) and not in_rt:
        out.append(html.unescape(text[position:]))
    for item in reversed(stack):
        out.append(item[1])
    return ''.join(out), speaker


def parse(text):
    """The subtitles in WebVTT text: [{'start', 'end', 'text'[, 'speaker']}], by start."""
    lines = text.split('\n')
    index = 0
    classes = {}
    if lines and lines[0].lstrip('﻿').startswith('WEBVTT'):
        while index < len(lines) and lines[index].strip():
            index += 1
    segments = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        block = _BLOCK.match(line)
        if block and not _TIMING.match(line):
            body = []
            index += 1
            while index < len(lines) and lines[index].strip():
                body.append(lines[index])
                index += 1
            if block.group(1) == 'STYLE':
                classes.update(_style_classes(' '.join([line[5:]] + body)))
            continue
        timing = _TIMING.match(line)
        if not timing:
            # A cue's identifier, or a stray line: either way not text.
            index += 1
            continue
        body = []
        index += 1
        while index < len(lines) and lines[index].strip() and not _TIMING.match(lines[index]):
            body.append(lines[index].strip())
            index += 1
        groups = timing.groups()
        start, end = _seconds(*groups[0:4]), _seconds(*groups[4:8])
        text_, speaker = cue_text('\n'.join(body), classes)
        text_ = '\n'.join(part.strip() for part in text_.split('\n') if part.strip())
        keypad = _keypad(groups[8] or '')
        if keypad != 2:
            text_ = markup.with_alignment('', keypad) + text_
        segment = {'start': start, 'end': max(start, end), 'text': text_}
        if speaker:
            segment['speaker'] = speaker
        segments.append(segment)
    segments.sort(key=lambda segment: segment['start'])
    return segments


def read(path):
    """The subtitles in the WebVTT file at `path`."""
    with open(path, 'rb') as vtt_file:
        return parse(decode(vtt_file.read()))


def format_time(seconds):
    """WebVTT time for `seconds`: "HH:MM:SS.mmm", to the nearest millisecond."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def _settings(keypad):
    """Cue settings that put a subtitle at `keypad`."""
    settings = []
    if keypad >= 7:
        settings.append('line:0')
    elif keypad >= 4:
        settings.append('line:50%,center')
    if keypad % 3 == 1:
        settings.append('position:10% align:start')
    elif keypad % 3 == 0:
        settings.append('position:90% align:end')
    return ' '.join(settings)


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _class_for(color, classes):
    for name, value in DEFAULT_CLASSES.items():
        if value == color:
            return name
    return classes.setdefault(color, 'color_' + color.lstrip('#'))


def _cue_body(text, classes):
    """The text's runs as WebVTT cue text: tags well nested, a colour as a class."""
    out = []
    stack = []
    for run, style in markup.spans(text):
        if not run:
            continue
        wanted = ([('c', _class_for(style.color, classes))] if style.color else []) + \
                 [(name, True) for name, field in (('b', 'bold'), ('i', 'italic'), ('u', 'underline')) if getattr(style, field)]
        keep = 0
        while keep < len(stack) and stack[keep] in wanted:
            keep += 1
        for name, _value in reversed(stack[keep:]):
            out.append(f'</{name}>')
        del stack[keep:]
        for name, value in wanted:
            if (name, value) not in stack:
                out.append(f'<c.{value}>' if name == 'c' else f'<{name}>')
                stack.append((name, value))
        out.append(_escape(run))
    for name, _value in reversed(stack):
        out.append(f'</{name}>')
    return ''.join(out)


def write(segments, voices=False):
    """WebVTT text for `segments`; with `voices`, each subtitle's speaker as
    a voice tag."""
    classes = {}                                # '#rrggbb': class, for the STYLE block
    cues = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        lines = [line.strip() for line in rest.split('\n')]
        rest = '\n'.join(line for line in lines if line)
        if not markup.plain(rest).strip():
            continue
        body = _cue_body(rest, classes)
        speaker = str(segment.get('speaker') or '').strip()
        if voices and speaker:
            body = f'<v {_escape(speaker)}>' + body
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        settings = _settings(markup.alignment(lead))
        cues.append(f'{format_time(start)} --> {format_time(end)}' + (f' {settings}' if settings else '') + '\n' + body)
    head = 'WEBVTT\n'
    if classes:
        head += '\nSTYLE\n' + '\n'.join(f'::cue(.{name}) {{ color: {color}; }}' for color, name in classes.items()) + '\n'
    return head + ('\n' + '\n\n'.join(cues) + '\n' if cues else '')


def save(path, segments, voices=False):
    """Write `segments` to `path` as WebVTT (always UTF-8, as WebVTT is)."""
    with open(path, 'w', encoding='utf-8', newline='\n') as vtt_file:
        vtt_file.write(write(segments, voices))
