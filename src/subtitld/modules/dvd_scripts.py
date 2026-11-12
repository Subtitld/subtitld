"""The DVD authoring tools' subtitle scripts: the text files DVD Studio Pro,
Adobe Encore, Sony (later Magix) DVD Architect, Sonic Scenarist, Sonic DVD
Producer and Avid's DVD tools import, each a subtitle to a line of times.

None of them is written down anywhere but in the tools' manuals, and every
one came in several layouts as the tools and the people feeding them
changed. Subtitle Edit has collected the layouts found in the wild, and it
is what files are exchanged with, so the readers here follow its readers
(src/libse/SubtitleFormats in its source) layout by layout.

What is read, and written:

- **DVD Studio Pro** (.stl): Spruce's script, which Apple bought with
  Spruce Technologies. "$" lines set the font, the place and the style for
  the subtitles after them ($VertAlign, $HorzAlign, $Italic, $Bold,
  $Underlined, $FontName ...), "//" lines are comments, and a subtitle is
  "00:00:01:00 , 00:00:03:00 , text|second line", "^I", "^B" and "^U"
  turning italic, bold and underline on and off inside it. The commas are
  written five ways: between tabs (DVD Studio Pro's own), between spaces,
  "," then ", ", and Spruce's bare "," and ", ". A ";" before the frames is
  drop-frame. Subtitles that are pictures ("<<Graphic>>file.tif") are left
  out. The legacy SPRUCE entry reads with this reader too: it is the same
  format, kept apart only for the layout it writes.
- **Adobe Encore** (.txt): "00:00:01:00 00:00:03:00 text", the next lines
  of text under it; or with a number before the times, or tabs between
  them, or "0001\\t...\\ttext//second line" with "@Italic@" around italics.
  "00;00;01;00", semicolons throughout, is Encore's drop-frame.
- **DVD Architect** (.sub, .txt): "00:00:01:00 - 00:00:03:00  text" and its
  tab-separated and numbered layouts, whose last field is HUNDREDTHS of a
  second, not frames; one with three clocks in milliseconds (the start, the
  end and the duration); and two with a header, a numbered line of start,
  end and duration in frames (and the text's length), and the text under it.
- **Scenarist** (.sst, .txt): "0001\\t00:00:01:00\\t00:00:03:00\\ttext", a
  tab between lines, under the header of Scenarist's subtitle script when
  it has one (st_format, TV_Type, Tape_Type, Display_Area, Contrast, the
  PA, E1, E2 and BG colours). That header's TV_Type and Tape_Type give the
  rate and the drop-frame. A script whose subtitles are pictures (a .tif
  or .bmp for every subtitle) is not text, and `kind` says so.
- **DVD Producer** (.txt): "1    00:00:01:00  00:00:03:00  text", padded
  into columns, the second line under the first.
- **Avid DVD** (.txt): "1\\t00:00:01:00\\t00:00:03:00\\ttext|second line",
  more lines under it, and "$Italic = TRUE" lines around italic subtitles.

The frame-counted ones are read at the rate the file names (DVD Studio
Pro's "// Format: NTSC - 29.97", Scenarist's TV_Type), else the video's,
else 25; drop-frame timecode at 29.97, or 59.94 for a video near 60. A
timecode counts frames, so at 29.97 non-drop "00:01:00:00" is 1800 frames,
60.06 seconds, as an authoring tool means it.

Every reader gives (segments, settings): the subtitles, and how the file
was written ({'fps', 'drop', 'layout', 'header'}), so that a file opened and
saved comes back in the layout it came in. A DVD Studio Pro or Scenarist
header is kept as it was, the settings Subtitld does not use with it.
"""

import datetime
import re
from fractions import Fraction

from subtitld.modules import avid_subcap
from subtitld.modules import markup
from subtitld.modules.srt import decode

DEFAULT_FPS = 25.0

# The session's name for each script, and back.
SESSION_FORMATS = {
    'DVDSTUDIOPRO': 'dvdstudiopro',
    'ENCORE': 'encore',
    'DVDARCHITECT': 'dvdarchitect',
    'SCENARIST': 'scenarist',
    'DVDPRODUCER': 'dvdproducer',
    'AVIDDVD': 'aviddvd',
}
SESSION_IDS = {fmt: name for name, fmt in SESSION_FORMATS.items()}

# The layouts each is read in, and can be written in; the first is the one
# written when nothing asks for another.
LAYOUTS = {
    'dvdstudiopro': ('tab', 'space', 'one', 'spruce', 'spruce-space'),
    'encore': ('numbered', 'plain', 'tabs', 'linetabs', 'linetabnewline'),
    'dvdarchitect': ('dash', 'tabs', 'numbered', 'explicit', 'duration', 'length'),
    'scenarist': ('tabs',),
    'dvdproducer': ('columns',),
    'aviddvd': ('tabs',),
}

# The formats that write drop-frame timecode when asked: the two whose
# tools have a way of writing it.
DROP_FRAME = ('dvdstudiopro', 'encore')

# DVD Studio Pro's header when the file had none of its own, as Subtitle
# Edit writes it.
DSP_HEADER = (
    '$VertAlign          =   Bottom',
    '$Bold               =   FALSE',
    '$Underlined         =   FALSE',
    '$Italic             =   FALSE',
    '$XOffset                =   0',
    '$YOffset                =   -5',
    '$TextContrast           =   15',
    '$Outline1Contrast           =   15',
    '$Outline2Contrast           =   13',
    '$BackgroundContrast     =   0',
    '$ForceDisplay           =   FALSE',
    '$FadeIn             =   0',
    '$FadeOut                =   0',
    '$HorzAlign          =   Center',
)

_TC = r'(\d{1,2})[:;](\d{2})[:;](\d{2})([:;.])(\d{1,3})'
_TC_STRICT = r'(\d{2})([:;])(\d{2})[:;](\d{2})([:;])(\d{2})'

# DVD Studio Pro: two timecodes and the text, a comma after each time, with
# or without the space or tab around it.
_DSP = re.compile(r'^\s*' + _TC + r'([ \t]*,[ \t]*)' + _TC + r'([ \t]*,[ \t]?)(.*)$')
_DSP_SEPARATORS = {'tab': ('\t,\t', '\t,\t'), 'space': (' , ', ' , '), 'one': (',', ', '),
                   'spruce': (',', ','), 'spruce-space': (', ', ', ')}
_DIRECTIVE = re.compile(r'^\s*\$\s*(\w+)\s*=\s*(.*?)\s*$')
_KEYPAD = {('top', 'left'): 7, ('top', 'center'): 8, ('top', 'right'): 9,
           ('center', 'left'): 4, ('center', 'center'): 5, ('center', 'right'): 6,
           ('bottom', 'left'): 1, ('bottom', 'center'): 2, ('bottom', 'right'): 3}
_PLACES = {keypad: place for place, keypad in _KEYPAD.items()}
_TOGGLES = {'^I': 'italic', '^B': 'bold', '^U': 'underline'}
# Directives the writer gives itself, so a kept header leaves them out.
_STATE_KEYS = ('vertalign', 'horzalign', 'italic', 'bold', 'underlined')

# Adobe Encore.
_ENCORE_PLAIN = re.compile(r'^' + _TC_STRICT + r' ' + _TC_STRICT + r'(?: (.*))?$')
_ENCORE_TABS = re.compile(r'^' + _TC_STRICT + r'\t' + _TC_STRICT + r'\t(.*)$')
_ENCORE_NUMBERED = re.compile(r'^(\d+) ' + _TC_STRICT + r' ' + _TC_STRICT + r'(?: (.*))?$')
_ENCORE_LINETABS = re.compile(r'^(\d{4})\t' + _TC_STRICT + r'\t' + _TC_STRICT + r'\t(.*)$')
_ENCORE_LINETABNEWLINE = re.compile(r'^(\d{4}) ' + _TC_STRICT + r' ' + _TC_STRICT + r'\t(.*)$')

# DVD Architect.
_SONY_DASH = re.compile(r'^(\d{2}):(\d{2}):(\d{2}):(\d{2}) +- +(\d{2}):(\d{2}):(\d{2}):(\d{2})[ \t]*(.*)$')
_SONY_TABS = re.compile(r'^(\d{2}):(\d{2}):(\d{2}):(\d{2})[ \t]+(\d{2}):(\d{2}):(\d{2}):(\d{2})[ \t]+(.*)$')
_SONY_NUMBERED = re.compile(r'^(\d{4})  (\d{2,3}):(\d{2}):(\d{2}):(\d{1,2})  (\d{2,3}):(\d{2}):(\d{2}):(\d{1,2})[ \t]*(.*)$')
_SONY_EXPLICIT = re.compile(r'^(\d{2}):(\d{2}):(\d{2})\.(\d{3})[ \t]+(\d{2}):(\d{2}):(\d{2})\.(\d{3})[ \t]+'
                            r'\d{2}:\d{2}:\d{2}\.\d{3}[ \t]+(.*)$')
_SONY_DURATION = re.compile(r'^(\d+)\t' + _TC_STRICT + r'\t' + _TC_STRICT + r'\t\d{2}:\d{2}:\d{2}:\d{2}(\t\d+)?\s*$')

# Scenarist, DVD Producer and Avid DVD.
_SCENARIST = re.compile(r'^(\d{1,4})[ \t]+' + _TC_STRICT + r'[ \t]+' + _TC_STRICT + r'(?:[ \t](.*))?$')
# The lines only a Scenarist script's header has.
_SCENARIST_HEAD = re.compile(r'^(st_format|Display_Start|TV_Type|Tape_Type|Pixel_Area|Display_Area|SP_NUMBER)\b')
_PICTURE = re.compile(r'\.(tif|tiff|png|bmp)\s*$', re.I)
_PRODUCER = re.compile(r'^[ \t]*(\d+)([ \t]+)' + _TC_STRICT + r'([ \t]+)' + _TC_STRICT + r'([ \t]*)(.*)$')
_AVID = re.compile(r'^(\d+)\t(\d{2})[:.,](\d{2})[:.,](\d{2})([:.,;])(\d{2})\t'
                   r'(\d{2})[:.,](\d{2})[:.,](\d{2})([:.,;])(\d{2})\t(.*)$')


def _lines_of(text):
    """The lines of a script. A carriage return alone is a line break too:
    DVD Architect and Encore write one between a subtitle's lines."""
    return str(text or '').lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').split('\n')


class _Clock:
    """Timecode at a rate: frames counted as the tools count them."""

    def __init__(self, fps, drop=False):
        self.drop = bool(drop)
        self.fps = avid_subcap.drop_rate(fps) if self.drop else float(fps or DEFAULT_FPS)
        self.nominal, self.frame_length = avid_subcap.rate(self.fps)
        # Only 29.97 and 59.94 have a drop-frame timecode.
        self.drop = self.drop and self.nominal in (30, 60) and self.frame_length.numerator == 1001

    def seconds(self, hours, minutes, seconds, frames):
        frame = avid_subcap.frame_of(hours, minutes, seconds, min(int(frames), self.nominal - 1), self.nominal, self.drop)
        return round(float(frame * self.frame_length), 3)

    def frame(self, seconds):
        return int(round(Fraction(max(0.0, float(seconds))).limit_denominator(1000000) / self.frame_length))

    def timecode(self, seconds):
        """HH:MM:SS:FF, ';' before the frames when drop-frame."""
        return avid_subcap.timecode(self.frame(seconds), self.nominal, self.drop)

    def span(self, start, end):
        """Timecodes for a subtitle, its end at least a frame after its start."""
        first = self.frame(start)
        last = max(first + 1, self.frame(end))
        return (avid_subcap.timecode(first, self.nominal, self.drop),
                avid_subcap.timecode(last, self.nominal, self.drop))


def _hundredths(hours, minutes, seconds, fraction):
    """A clock whose last field is hundredths, as DVD Architect writes it;
    one digit is tens of milliseconds, as Subtitle Edit reads it."""
    return round(int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) * 10 / 1000.0, 3)


def _stamp_hundredths(seconds):
    total = max(0, int(round(float(seconds) * 100)))
    hours, rest = divmod(total, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, hundredths = divmod(rest, 100)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}:{hundredths:02d}'


def _stamp_millis(seconds):
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def _tidy(text):
    """A subtitle's text: its lines trimmed, no empty ones."""
    return '\n'.join(line.strip() for line in str(text or '').split('\n') if line.strip())


def _plain_lines(segment):
    """A subtitle's lines as plain text, the tools showing tags as text."""
    text = markup.plain(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
    return [line.strip() for line in text.split('\n') if line.strip()]


def _ordered(segments):
    return sorted(segments or [], key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))


def _finished(found):
    """The subtitles with text, ends no earlier than starts, by start."""
    segments = []
    for start, end, text in found:
        text = _tidy(text)
        if not markup.plain(text).strip():
            continue
        segments.append({'start': start, 'end': round(max(start, end), 3), 'text': text})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments


def _toggled(text, toggles):
    """Text with tags for a subtitle's text whose styles `toggles` (code to
    Style field) turn on and off; a style left on runs to the end."""
    style = markup.PLAIN
    pattern = re.compile('|'.join(re.escape(code) for code in toggles))
    runs = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            runs.append((text[position:match.start()], style))
        field = toggles[match.group(0)]
        style = style._replace(**{field: not getattr(style, field)})
        position = match.end()
    if position < len(text):
        runs.append((text[position:], style))
    # A line break between two runs of one style is in that style: the
    # tools close every style at the end of a line and open it again.
    for index in range(1, len(runs) - 1):
        if not runs[index][0].strip() and runs[index - 1][1] == runs[index + 1][1]:
            runs[index] = (runs[index][0], runs[index - 1][1])
    merged = []
    for run, run_style in runs:
        if merged and merged[-1][1] == run_style:
            merged[-1] = (merged[-1][0] + run, run_style)
        else:
            merged.append((run, run_style))
    return markup.from_spans(merged)


def _encoded_toggles(text, toggles, separator):
    """`text` with its italic, bold and underline written as the toggles of
    `toggles` (Style field to code), each line closing what it opened, the
    lines joined by `separator`."""
    fields = list(toggles)
    out = []
    on = {field: False for field in fields}

    def switch(wanted):
        for field in fields:
            if on[field] != wanted.get(field, False):
                out.append(toggles[field])
                on[field] = not on[field]

    # A line closes what it opened; the next opens again what it needs.
    for run, style in markup.without_readings(markup.spans(text)):
        pieces = run.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        for index, piece in enumerate(pieces):
            if index:
                switch({})
                out.append('\n')
            if piece:
                switch({field: getattr(style, field) for field in fields})
                out.append(piece)
    switch({})
    lines = [line.strip() for line in ''.join(out).split('\n')]
    return separator.join(line for line in lines if line and line not in toggles.values())


# ------------------------------------------------------- DVD Studio Pro


def _dsp_layout(first, second):
    """The layout of a subtitle line from the separators after its times."""
    for layout, separators in _DSP_SEPARATORS.items():
        if (first, second) == separators:
            return layout
    if '\t' in first:
        return 'tab'
    if first.startswith(' '):
        return 'space'
    if first == ',':
        return 'one' if second.endswith(' ') else 'spruce'
    return 'spruce-space'


def stl_flavour(text):
    """'dvdstudiopro' for a .stl text script written the way DVD Studio Pro
    writes one (a header of settings, tabs around the commas or drop-frame
    times), 'spruce' for Spruce's bare lines of times and text, '' for no
    text script at all."""
    found = ''
    for line in _lines_of(text)[:400]:
        if _DIRECTIVE.match(line):
            return 'dvdstudiopro'
        match = _DSP.match(line)
        if match:
            groups = match.groups()
            if groups[3] == ';' or groups[9] == ';' or _dsp_layout(groups[5], groups[11]) == 'tab':
                return 'dvdstudiopro'
            found = 'spruce'
    return found


def _declared_rate(line):
    """The rate a DVD Studio Pro or Spruce comment names: "// Format: NTSC -
    29.97", "// Format: PAL", "//$SMPTEFormat = 25"; None for any other line."""
    lowered = line.strip().lower().replace(' ', '')
    if lowered.startswith('//format:ntsc-'):
        for prefix, rate in (('//format:ntsc-59.9', 59.94), ('//format:ntsc-29.9', 29.97), ('//format:ntsc-23.9', 23.976)):
            if lowered.startswith(prefix):
                return rate
        return 29.97
    if lowered.startswith('//format:pal'):
        return 25.0
    if lowered.startswith(('//$smpteformat=', '$smpteformat=')):
        try:
            rate = float(lowered.split('=', 1)[1])
        except ValueError:
            return None
        return rate if 20 < rate < 300 else None
    return None


# The comment a file is written with, so that it opens at its rate again.
_RATE_COMMENTS = {(24, 1001): '// Format: NTSC - 23.976', (30, 1001): '// Format: NTSC - 29.97',
                  (60, 1001): '// Format: NTSC - 59.94'}


def _parse_dvdstudiopro(text, fps):
    lines = _lines_of(text)
    declared = None
    drop = False
    for line in lines:
        declared = _declared_rate(line) or declared
        match = _DSP.match(line)
        if match and (match.group(4) == ';' or match.group(10) == ';'):
            drop = True
    clock = _Clock(declared or fps, drop)

    place = {'vertalign': 'bottom', 'horzalign': 'center'}
    flags = {'italic': False, 'bold': False, 'underlined': False}
    header = []
    layout = ''
    found = []
    for line in lines:
        match = _DSP.match(line)
        if not match:
            directive = _DIRECTIVE.match(line)
            if directive:
                key, value = directive.group(1).lower(), directive.group(2).strip().lower()
                if key in place:
                    place[key] = value if value in ('top', 'center', 'bottom', 'left', 'right') else place[key]
                elif key in flags:
                    flags[key] = value == 'true'
            if not found and line.strip() and (directive or line.lstrip().startswith('//')):
                header.append(line.rstrip())
            continue
        groups = match.groups()
        layout = layout or _dsp_layout(groups[5], groups[11])
        body = groups[12].strip()
        if body.startswith('<<Graphic>>'):
            continue                # a picture, not text
        body = body.replace(' | ', '\n').replace('|', '\n')
        has = {field: code in body for code, field in _TOGGLES.items()}
        content = _toggled(body, _TOGGLES)
        # A style the settings turn on wraps the subtitle, unless it says
        # where that style goes itself.
        for key, field, tag in (('italic', 'italic', 'i'), ('bold', 'bold', 'b'), ('underlined', 'underline', 'u')):
            if flags[key] and not has[field]:
                content = f'<{tag}>{content}</{tag}>'
        keypad = _KEYPAD.get((place['vertalign'], place['horzalign']), 2)
        start = clock.seconds(*groups[0:3], groups[4])
        end = clock.seconds(*groups[6:9], groups[10])
        found.append((start, end, markup.with_alignment('', keypad) + content))
    settings = {'fps': clock.fps, 'drop': clock.drop, 'layout': layout or 'tab', 'header': header}
    return _finished(found), settings


def _write_dvdstudiopro(segments, clock, layout, header, line_end):
    out = []
    if header is not False:
        kept = [line for line in (header or ())
                if not (_DIRECTIVE.match(line) and _DIRECTIVE.match(line).group(1).lower() in _STATE_KEYS)
                and _declared_rate(line) is None]
        if header:
            # The file's own settings, and the state the subtitles start in.
            out.extend(kept + ['$VertAlign = Bottom', '$HorzAlign = Center',
                               '$Bold = FALSE', '$Underlined = FALSE', '$Italic = FALSE'])
        else:
            out.extend(DSP_HEADER)
        out.append('')
    comment = _RATE_COMMENTS.get((clock.nominal, clock.frame_length.numerator))
    if comment:
        out.insert(0, comment + (' Drop Frame' if clock.drop else ''))
    first, second = _DSP_SEPARATORS.get(layout, _DSP_SEPARATORS['tab'])
    vertical, horizontal = 'bottom', 'center'
    for segment in _ordered(segments):
        text = str(segment.get('text') or '')
        body = _encoded_toggles(text, {'bold': '^B', 'italic': '^I', 'underline': '^U'}, '|')
        if not body:
            continue
        place_v, place_h = _PLACES.get(markup.alignment(text), ('bottom', 'center'))
        if place_v != vertical:
            out.append(f'$VertAlign = {place_v.capitalize()}')
            vertical = place_v
        if place_h != horizontal:
            out.append(f'$HorzAlign = {place_h.capitalize()}')
            horizontal = place_h
        start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
        out.append(f'{start}{first}{end}{second}{body}')
    return line_end.join(out) + line_end if out else ''


# --------------------------------------------------------- Adobe Encore


def _encore_drop(lines):
    """Whether Encore's times are drop-frame: semicolons in them."""
    return any((parsed := _encore_line(line.rstrip())) and ';' in ''.join(parsed[1] + parsed[2]) for line in lines)


def _encore_line(line):
    """(layout, start groups, end groups, text) for an Encore subtitle line."""
    for layout, pattern, numbered in (('linetabs', _ENCORE_LINETABS, True),
                                      ('linetabnewline', _ENCORE_LINETABNEWLINE, True),
                                      ('numbered', _ENCORE_NUMBERED, True),
                                      ('tabs', _ENCORE_TABS, False),
                                      ('plain', _ENCORE_PLAIN, False)):
        match = pattern.match(line.replace('       ', '\t') if layout == 'linetabs' else line)
        if match:
            groups = match.groups()[1:] if numbered else match.groups()
            return layout, groups[0:6], groups[6:12], groups[12] or ''
    return None


def _italic_marks(text):
    """Encore's "@Italic@", which turns italic on and off, as tags."""
    return _toggled(text, {'@Italic@': 'italic'}) if '@Italic@' in text else text


def _parse_encore(text, fps):
    lines = _lines_of(text)
    clock = _Clock(fps, _encore_drop(lines))
    layout = ''
    found = []
    for line in lines:
        parsed = _encore_line(line.rstrip())
        if parsed:
            kind, start, end, body = parsed
            layout = layout or kind
            if kind in ('linetabs', 'linetabnewline'):
                body = body.replace('//', '\n')
            found.append([clock.seconds(start[0], start[2], start[3], start[5]),
                          clock.seconds(end[0], end[2], end[3], end[5]), body.strip()])
        elif line.strip() and found and layout != 'linetabs':
            found[-1][2] = (found[-1][2] + '\n' + line.strip()).strip()
    for cue in found:
        cue[2] = _italic_marks(cue[2])
    return _finished(found), {'fps': clock.fps, 'drop': clock.drop, 'layout': layout or 'numbered', 'header': []}


def _write_encore(segments, clock, layout, line_end):
    out = []
    for index, segment in enumerate((s for s in _ordered(segments) if _plain_lines(s)), start=1):
        start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
        if clock.drop:
            # Encore's drop-frame is semicolons throughout: "00;00;01;00".
            start, end = start.replace(':', ';'), end.replace(':', ';')
        if layout in ('linetabs', 'linetabnewline'):
            text = _encoded_toggles(str(segment.get('text') or ''), {'italic': '@Italic@'}, '\n')
            lines = text.split('\n')
            if layout == 'linetabs':
                out.append(f'{index:04d}\t{start}\t{end}\t' + '//'.join(lines))
            else:
                out.append(f'{index:04d} {start} {end}\t{lines[0]}')
                out.extend('\t\t\t\t' + line for line in lines[1:])
            continue
        lines = _plain_lines(segment)
        if layout == 'plain':
            out.append(f'{start} {end} {lines[0]}')
        elif layout == 'tabs':
            out.append(f'{start}\t{end}\t{lines[0]}')
        else:
            out.append(f'{index} {start} {end} {lines[0]}')
        out.extend(lines[1:])
    return line_end.join(out) + line_end if out else ''


# --------------------------------------------------------- DVD Architect


def _parse_dvdarchitect(text, fps):
    lines = _lines_of(text)
    clock = _Clock(fps, False)
    layout = ''
    found = []
    for line in lines:
        if not line.strip():
            continue
        match = _SONY_DURATION.match(line)
        if match:
            layout = layout or ('length' if match.group(14) else 'duration')
            groups = match.groups()[1:13]
            found.append([clock.seconds(groups[0], groups[2], groups[3], groups[5]),
                          clock.seconds(groups[6], groups[8], groups[9], groups[11]), ''])
            continue
        hit = None
        for kind, pattern in (('numbered', _SONY_NUMBERED), ('explicit', _SONY_EXPLICIT),
                              ('dash', _SONY_DASH), ('tabs', _SONY_TABS)):
            match = pattern.match(line)
            if match:
                hit = kind, match
                break
        # Under a numbered line of times, every line is its text.
        if hit and layout not in ('duration', 'length'):
            kind, match = hit
            layout = layout or kind
            groups = match.groups()
            if kind == 'numbered':
                groups = groups[1:]
            if kind == 'explicit':
                start = round(int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2]) + int(groups[3]) / 1000.0, 3)
                end = round(int(groups[4]) * 3600 + int(groups[5]) * 60 + int(groups[6]) + int(groups[7]) / 1000.0, 3)
            else:
                start, end = _hundredths(*groups[0:4]), _hundredths(*groups[4:8])
            found.append([start, end, groups[8].replace('|', '\n').replace('\x00', '').strip()])
        elif found:
            found[-1][2] = (found[-1][2] + '\n' + line.strip()).strip()
    return _finished(found), {'fps': clock.fps, 'drop': False, 'layout': layout or 'dash', 'header': []}


def _write_dvdarchitect(segments, clock, layout, line_end, title=''):
    cues = [s for s in _ordered(segments) if _plain_lines(s)]
    out = []
    if layout in ('duration', 'length'):
        last = max((float(s.get('end', 0)) for s in cues), default=0.0)
        out.extend([f'Title: {title}', 'Translator: No Author',
                    'Date: ' + datetime.date.today().strftime('%d.%m.%Y'),
                    f'Duration: {clock.timecode(last)}', 'Program start: 00:00:00:00',
                    f'Title count: {len(cues)}', '',
                    '#\tIn\tOut\tDuration' + ('\tLength' if layout == 'length' else ''), ''])
    for index, segment in enumerate(cues, start=1):
        lines = _plain_lines(segment)
        if layout in ('duration', 'length'):
            first = clock.frame(segment.get('start', 0))
            last = max(first + 1, clock.frame(segment.get('end', 0)))
            start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
            length = avid_subcap.timecode(last - first, clock.nominal, False)
            text = '\n'.join(lines)
            out.append(f'{index}\t{start}\t{end}\t{length}' + (f'\t{len(text)}' if layout == 'length' else ''))
            out.extend(lines)
            out.append('')
            continue
        # Its own line break inside a subtitle is a carriage return alone.
        text = '\r'.join(lines)
        start, end = float(segment.get('start', 0)), max(float(segment.get('start', 0)), float(segment.get('end', 0)))
        if layout == 'explicit':
            out.append(f'{_stamp_millis(start)}\t{_stamp_millis(end)}\t{_stamp_millis(end - start)}\t{text}')
        elif layout == 'tabs':
            out.append(f'{_stamp_hundredths(start)}\t{_stamp_hundredths(end)}\t{text}')
        elif layout == 'numbered':
            out.append(f'{index:04d}  {_stamp_hundredths(start)}  {_stamp_hundredths(end)}    \t{text}')
        else:
            out.append(f'{_stamp_hundredths(start)} - {_stamp_hundredths(end)}  \t{text}')
    return line_end.join(out) + line_end if out else ''


# -------------------------------------------------------------- Scenarist


def _scenarist_rate(lines):
    """(rate, drop-frame) as a Scenarist header gives them, or (None, False)."""
    declared, drop = None, False
    for line in lines[:60]:
        words = line.split()
        if len(words) >= 2 and words[0].lower() == 'tv_type':
            declared = 29.97 if words[1].upper().startswith('NTSC') else 25.0 if words[1].upper().startswith('PAL') else None
        elif len(words) >= 2 and words[0].lower() == 'tape_type':
            drop = words[1].upper() == 'DROP'
    return declared, drop


def _parse_scenarist(text, fps):
    lines = _lines_of(text)
    declared, drop = _scenarist_rate(lines)
    drop = drop or any((m := _SCENARIST.match(line)) and ';' in (m.group(3) + m.group(6) + m.group(9) + m.group(12))
                       for line in lines)
    clock = _Clock(declared or fps, drop)
    header = []
    found = []
    for line in lines:
        match = _SCENARIST.match(line.rstrip())
        if match:
            groups = match.groups()
            body = (groups[13] or '').strip('\r\n')
            if not body.strip() or _PICTURE.search(body):
                continue
            found.append((clock.seconds(groups[1], groups[3], groups[4], groups[6]),
                          clock.seconds(groups[7], groups[9], groups[10], groups[12]),
                          '\n'.join(part.strip() for part in body.split('\t'))))
        elif not found and line.strip():
            header.append(line.rstrip())
    return _finished(found), {'fps': clock.fps, 'drop': clock.drop, 'layout': 'tabs', 'header': header}


def _write_scenarist(segments, clock, header, line_end):
    out = list(header or [])
    for index, segment in enumerate((s for s in _ordered(segments) if _plain_lines(s)), start=1):
        start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
        out.append(f'{index:04d}\t{start}\t{end}\t' + '\t'.join(_plain_lines(segment)))
    return line_end.join(out) + line_end if out else ''


# ----------------------------------------------------------- DVD Producer


def _parse_dvdproducer(text, fps):
    clock = _Clock(fps, False)
    found = []
    for line in _lines_of(text):
        match = _PRODUCER.match(line)
        if match:
            groups = match.groups()
            found.append([clock.seconds(groups[2], groups[4], groups[5], groups[7]),
                          clock.seconds(groups[9], groups[11], groups[12], groups[14]), groups[16].strip()])
        elif line.strip() and found:
            found[-1][2] = (found[-1][2] + '\n' + line.strip()).strip()
    return _finished(found), {'fps': clock.fps, 'drop': False, 'layout': 'columns', 'header': []}


def _write_dvdproducer(segments, clock, line_end):
    out = []
    for index, segment in enumerate((s for s in _ordered(segments) if _plain_lines(s)), start=1):
        start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
        prefix = f'{index:<4} {start}  {end}  '
        lines = _plain_lines(segment)
        out.append(prefix + lines[0])
        out.extend(' ' * len(prefix) + line for line in lines[1:])
    return line_end.join(out) + line_end if out else ''


# --------------------------------------------------------------- Avid DVD


def _parse_aviddvd(text, fps):
    lines = _lines_of(text)
    drop = any((m := _AVID.match(line.rstrip())) and ';' in (m.group(5) + m.group(10)) for line in lines)
    clock = _Clock(fps, drop)
    italic = False
    found = []
    for line in lines:
        line = line.rstrip()
        match = _AVID.match(line)
        if match:
            groups = match.groups()
            found.append([clock.seconds(*groups[1:4], groups[5]), clock.seconds(*groups[6:9], groups[10]),
                          groups[11].strip(), italic])
        elif line.startswith('$'):
            setting = line.replace(' ', '').lower()
            if setting == '$italic=true':
                italic = True
            elif setting == '$italic=false':
                italic = False
        elif line.strip() and found:
            found[-1][2] = (found[-1][2] + '\n' + line.strip()).strip()
    cues = []
    for start, end, body, slanted in found:
        body = _tidy(body.replace('|', '\n'))
        cues.append((start, end, f'<i>{body}</i>' if slanted and body else body))
    return _finished(cues), {'fps': clock.fps, 'drop': clock.drop, 'layout': 'tabs', 'header': []}


def _write_aviddvd(segments, clock, line_end):
    out = []
    italic = False
    for index, segment in enumerate((s for s in _ordered(segments) if _plain_lines(s)), start=1):
        runs = [(run, style) for run, style in markup.without_readings(markup.spans(str(segment.get('text') or '')))
                if run.strip()]
        slanted = bool(runs) and all(style.italic for _run, style in runs)
        if slanted != italic:
            out.append('$Italic = TRUE' if slanted else '$Italic = FALSE')
            italic = slanted
        start, end = clock.span(segment.get('start', 0), segment.get('end', 0))
        out.append(f'{index}\t{start}\t{end}\t' + '|'.join(_plain_lines(segment)))
        out.append('')
    return line_end.join(out) + line_end if out else ''


# ------------------------------------------------------------ the module


def is_picture_script(data):
    """Whether `data` is a Scenarist subtitle script whose subtitles are
    pictures (a .tif, .bmp or .png named for each), which is no text."""
    text = decode(data[:65536]) if isinstance(data, (bytes, bytearray)) else str(data or '')[:65536]
    entries = [match for line in _lines_of(text) if (match := _SCENARIST.match(line.rstrip()))]
    return bool(entries) and all(_PICTURE.search(match.group(14) or '') for match in entries)


def kind(data, extension=''):
    """Which of these scripts `data` (bytes or text) is, '' when none.

    `extension` ('txt', 'sub', 'sst') narrows it where the tools disagree on
    a layout: DVD Architect's tab-separated .sub counts hundredths where
    Encore's .txt counts frames. DVD Studio Pro's .stl is told by
    stl_flavour, not here: its lines are Spruce's, which legacy reads.
    The tests run from the most particular layout to the least.
    """
    text = decode(data) if isinstance(data, (bytes, bytearray)) else str(data or '')
    extension = extension.lower().lstrip('.')
    lines = [line.rstrip() for line in _lines_of(text)]
    filled = [line for line in lines if line.strip()][:400]
    head = text[:8192]
    # Pinnacle's, RTF and Avid SubCap's, whose lines look like these.
    if not filled or '#INPOINT OUTPOINT PATH' in head or head.lstrip('\ufeff').startswith('{\\rtf1') or \
            avid_subcap.is_subcap(head):
        return ''
    if any(_SCENARIST_HEAD.match(line) for line in filled[:30]) and any(_SCENARIST.match(line) for line in filled):
        return 'scenarist'
    if any(_SONY_DURATION.match(line) for line in filled):
        return 'dvdarchitect'
    # Number, tab, two timecodes, tab, text: Encore, Scenarist or Avid DVD.
    tabbed = [line for line in filled if _AVID.match(line)]
    if tabbed:
        bodies = [_AVID.match(line).group(12) for line in tabbed]
        if any('@Italic@' in body or '//' in body for body in bodies) and \
                all(_ENCORE_LINETABS.match(line) for line in tabbed):
            return 'encore'
        # Avid DVD puts the next lines under the times, or "|" between
        # them, and "$Italic" lines around them; Scenarist numbers in four
        # digits and puts a tab between lines.
        others = [line for line in filled if not _AVID.match(line)]
        if not others and not any('|' in body for body in bodies) and \
                all(re.match(r'^\d{4}\t', line) for line in tabbed):
            return 'scenarist'
        return 'aviddvd'
    if any(_ENCORE_LINETABNEWLINE.match(line) for line in filled):
        return 'encore'
    if any(_SONY_NUMBERED.match(line) for line in filled):
        return 'dvdarchitect'
    numbered = [match for line in filled if (match := _PRODUCER.match(line))]
    if numbered:
        # DVD Producer pads its columns; Encore writes one space.
        if any(len(match.group(2)) > 1 or len(match.group(9)) > 1 or len(match.group(16)) > 1
               for match in numbered):
            return 'dvdproducer'
        if any(_ENCORE_NUMBERED.match(line) for line in filled):
            return 'encore'
    if any(_SONY_DASH.match(line) for line in filled) or any(_SONY_EXPLICIT.match(line) for line in filled):
        return 'dvdarchitect'
    if any(_ENCORE_TABS.match(line) for line in filled):
        # Encore counts frames, DVD Architect hundredths: a last field of
        # 30 or more cannot be a frame, and a .sub is DVD Architect's.
        fractions = [int(match.group(number)) for line in filled if (match := _SONY_TABS.match(line))
                     for number in (4, 8)]
        return 'dvdarchitect' if extension == 'sub' or max(fractions, default=0) >= 30 else 'encore'
    if extension == 'sub' and any(_SONY_TABS.match(line) for line in filled):
        return 'dvdarchitect'
    plain = [line for line in filled if _ENCORE_PLAIN.match(line)]
    if plain:
        # "Times, a space, times, text" is DVDSS too (modules.legacy), which
        # writes "//" between lines: Encore is the one with its next lines
        # under the times, as Subtitle Edit tells them apart.
        under = any(not _ENCORE_PLAIN.match(line) for line in filled[filled.index(plain[0]):])
        if under and not any('//' in line for line in plain):
            return 'encore'
    return ''


_PARSERS = {
    'dvdstudiopro': _parse_dvdstudiopro,
    'encore': _parse_encore,
    'dvdarchitect': _parse_dvdarchitect,
    'scenarist': _parse_scenarist,
    'dvdproducer': _parse_dvdproducer,
    'aviddvd': _parse_aviddvd,
}


def parse(text, fmt, fps=None):
    """(subtitles, settings) in `text` read as `fmt`: [{'start', 'end',
    'text'}] by start, and {'fps', 'drop', 'layout', 'header'}."""
    if fmt not in _PARSERS:
        raise ValueError(f'unknown subtitle script {fmt!r}')
    return _PARSERS[fmt](str(text or ''), float(fps or DEFAULT_FPS))


def read(path, fps=None, extension=''):
    """(subtitles, settings, fmt) for the script at `path`."""
    with open(path, 'rb') as script_file:
        data = script_file.read()
    text = decode(data)
    fmt = kind(text, extension or path.rsplit('.', 1)[-1]) or (stl_flavour(text) and 'dvdstudiopro')
    if not fmt:
        raise ValueError('not a DVD authoring subtitle script')
    segments, settings = parse(text, fmt, fps)
    return segments, settings, fmt


def write(segments, fmt, fps=DEFAULT_FPS, drop=False, layout=None, header=None, line_end='\n', title=''):
    """`segments` as `fmt` text: frames at `fps`, drop-frame when `drop` and
    the format and rate have it, in `layout` (one of LAYOUTS[fmt], else the
    first), under `header` (DVD Studio Pro and Scenarist: the lines a file
    opened had; False for none at all, as Spruce writes)."""
    if fmt not in LAYOUTS:
        raise ValueError(f'unknown subtitle script {fmt!r}')
    layout = layout if layout in LAYOUTS[fmt] else LAYOUTS[fmt][0]
    # Drop-frame where the format has it and the rate is 29.97 or 59.94:
    # asked at any other rate, it is not there to give.
    nominal, frame_length = avid_subcap.rate(fps)
    drop = bool(drop) and fmt in DROP_FRAME and nominal in (30, 60) and frame_length.numerator == 1001
    clock = _Clock(fps, drop)
    if fmt == 'dvdstudiopro':
        return _write_dvdstudiopro(segments, clock, layout, header, line_end)
    if fmt == 'encore':
        return _write_encore(segments, clock, layout, line_end)
    if fmt == 'dvdarchitect':
        return _write_dvdarchitect(segments, clock, layout, line_end, title)
    if fmt == 'scenarist':
        return _write_scenarist(segments, clock, header, line_end)
    if fmt == 'dvdproducer':
        return _write_dvdproducer(segments, clock, line_end)
    return _write_aviddvd(segments, clock, line_end)


def save(path, segments, fmt, fps=DEFAULT_FPS, drop=False, layout=None, header=None, encoding='utf-8',
         line_end='\n', title=''):
    """Write `segments` to `path` as `fmt`. DVD Studio Pro's text in UTF-16
    is what it reads best; the encoding is the caller's to choose."""
    with open(path, 'w', encoding=encoding, errors='replace', newline='') as script_file:
        script_file.write(write(segments, fmt, fps, drop, layout, header, line_end, title))
