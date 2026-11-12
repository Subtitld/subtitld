"""GPAC TTXT (.ttxt): the XML form of MP4 timed text (3GPP's tx3g), which
MP4Box writes when it extracts a text track (-ttxt) and reads to make one
(-add file.ttxt).

What is here follows GPAC's own code: the dump (src/isomedia/box_dump.c,
dump_ttxt_header and dump_ttxt_sample) for what files look like, the
importer (src/filters/load_text.c, txtin_setup_ttxt and
txtin_process_ttxt) for what MP4Box takes, and the TTXT documentation on
gpac.io for the defaults.

    <TextStream version="1.1">
    <TextStreamHeader width="1920" height="1080" layer="0" translation_x="0" translation_y="0">
    <TextSampleDescription horizontalJustification="center" verticalJustification="bottom"
        backColor="0 0 0 0" verticalText="no" fillTextRegion="no" continuousKaraoke="no" scroll="None">
    <FontTable><FontTableEntry fontName="Sans-Serif" fontID="1"/></FontTable>
    <TextBox top="0" left="0" bottom="1080" right="1920"/>
    <Style styles="Normal" fontID="1" fontSize="54" color="ff ff ff ff"/>
    </TextSampleDescription>
    </TextStreamHeader>
    <TextSample sampleTime="00:00:01.000" sampleDescriptionIndex="1" xml:space="preserve">Hello
    world<Style fromChar="6" toChar="11" styles="Italic" fontID="1" fontSize="54" color="ff ff ff ff"/>
    </TextSample>
    <TextSample sampleTime="00:00:03.000" text=""/>
    </TextStream>

A sample is shown from its time until the next sample's; an empty one
clears the screen. Its text is the first text inside the element (or, in
version 1.0 files, a `text` attribute of lines in single quotes). What
follows the text modifies a stretch of it, counted in UTF-16 units from 0,
`toChar` the first unit after it:

    Style      bold, italic, underlined (and strikethrough, which the
               importer reads), a colour as "r g b a" in hex, a font size
               and a font of the FontTable; a sample's Style replaces the
               description's for its stretch
    Blinking   text that blinks
    Karaoke    from `startTime` (seconds into the sample), KaraokeRange
               stretches lit one after the other, each until its `endTime`
    Highlight, HyperLink, TextBox  for one sample

A sample's description (sampleDescriptionIndex, from 1) says where the
text goes: horizontalJustification left, center or right,
verticalJustification top, center or bottom (left and bottom when it does
not say, as the importer has it), and verticalText "yes" for text written
down the picture.

Reading gives a subtitle a sample with text: bold, italic, underline,
strikethrough, a colour other than the description's and a size against
its size as Subtitld's markup; Blinking as flashing text; karaoke as
karaoke codes (modules.karaoke); the description's justification as one
of the nine places and verticalText as 'writing' 'vertical-rl'. The last
sample, with none after it, gets `TAIL`.

Writing gives a header sized to the video (GPAC's 400 by 80 without one),
a description for each place and writing the subtitles use, in the order
they come, a sample a subtitle with its Style, Blinking and Karaoke, an
empty sample wherever a subtitle ends before the next starts, and one at
the last end. Its text is the plain text in UTF-8, the readings of a ruby
left out.

What is lost on the way: a tx3g track shows one sample at a time, so a
subtitle overlapping the next ends where the next begins; position beyond
the nine places, typefaces, highlights, hyperlinks, a sample's own TextBox
and scrolling are not read; on writing, rubies, emphasis and the other
formatting tx3g has no record for are left out.

A .ttxt is also what CCExtractor calls its timed transcript, plain text
in "|"-parted columns. That one is told apart by its bytes and refused
with a message saying so.
"""

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import karaoke, markup

# The last sample, with no sample after it to end it, lasts this long.
TAIL = 3.0
# GPAC's defaults for a header and a style (gpac.io's TTXT documentation).
DEFAULT_WIDTH, DEFAULT_HEIGHT = 400, 80
DEFAULT_FONT_SIZE = 18

_JUSTIFY_ACROSS = {'left': 0, 'center': 1, 'right': 2}
_JUSTIFY_DOWN = {'bottom': 0, 'center': 1, 'top': 2}
_TIME = re.compile(r'^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?\s*$')
# CCExtractor's timed transcript: a time, "|", more columns, the text.
_TRANSCRIPT = re.compile(r'^\s*\d[\d:.,]*\|', re.MULTILINE)


def kind(data):
    """'gpac' for GPAC's TTXT, 'transcript' for CCExtractor's timed
    transcript, None for neither, by the first bytes of a file."""
    head = bytes(data[:4096]).decode('utf-8', 'ignore')
    if re.search(r'<\s*TextStream\b', head):
        return 'gpac'
    if _TRANSCRIPT.search(head):
        return 'transcript'
    return None


def _seconds(value):
    """Seconds for a sampleTime: "hh:mm:ss.ms", or seconds as a number."""
    found = _TIME.match(str(value or ''))
    if found:
        hours, minutes, seconds, fraction = found.groups()
        # GPAC reads the digits after the point as milliseconds.
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + (int(fraction) / 1000 if fraction else 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _color(value):
    """('#rrggbb', alpha) for a colour as TTXT writes it, "r g b a" in hex."""
    try:
        red, green, blue, alpha = (int(part, 16) & 0xFF for part in str(value).split()[:4])
    except ValueError:
        return '#ffffff', 255
    return f'#{red:02x}{green:02x}{blue:02x}', alpha


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _quoted_lines(value):
    """The text of a version 1.0 `text` attribute: lines in single quotes,
    as the importer's ttxt_parse_string reads them; unquoted text as it is."""
    if not value.startswith("'"):
        return value
    lines, current, inside = [], [], False
    for index, char in enumerate(value):
        if char == "'":
            if not inside:
                inside = True
                current = []
            elif index + 1 == len(value) or value[index + 1] in " \n\r\t'":
                inside = False
                lines.append(''.join(current))
            else:
                current.append(char)
        elif inside:
            current.append(char)
    return '\n'.join(lines)


class _Description:
    """What a TextSampleDescription says that a subtitle can keep."""

    def __init__(self, element=None):
        get = element.get if element is not None else (lambda key, default=None: default)
        self.across = _JUSTIFY_ACROSS.get(str(get('horizontalJustification', 'left')).lower(), 0)
        self.down = _JUSTIFY_DOWN.get(str(get('verticalJustification', 'bottom')).lower(), 0)
        self.vertical = str(get('verticalText', 'no')).lower() == 'yes'
        style = element.find('Style') if element is not None else None
        self.style = _record(style, None)

    def keypad(self):
        return (1, 4, 7)[self.down] + self.across


def _record(element, default):
    """A style record: {'flags', 'size', 'color', 'font'}, from a Style
    element, what it does not say taken from `default`."""
    record = dict(default or {'flags': set(), 'size': DEFAULT_FONT_SIZE, 'color': '#ffffff', 'font': 1})
    if element is None:
        return record
    styles = str(element.get('styles', ''))
    # The importer looks for each word anywhere in the value.
    record['flags'] = {flag for flag in ('Bold', 'Italic', 'Underlined', 'Strikethrough') if flag in styles}
    record['size'] = _int(element.get('fontSize'), record['size']) or record['size']
    if element.get('color') is not None:
        record['color'] = _color(element.get('color'))[0]
    record['font'] = _int(element.get('fontID'), record['font'])
    return record


def _units(text):
    """For each UTF-16 unit of `text`, the index of its character: what
    TTXT's character offsets count."""
    out = []
    for index, char in enumerate(text):
        out.extend([index] * (2 if ord(char) > 0xFFFF else 1))
    out.append(len(text))
    return out


def _span(element, units):
    """(first, after) characters for an element's fromChar and toChar."""
    def at(unit):
        return units[max(0, min(len(units) - 1, unit))]
    return at(_int(element.get('fromChar'))), at(_int(element.get('toChar')))


def _sample_text(element):
    """A sample's text: its `text` attribute (version 1.0), else the first
    text inside it, as the importer takes it."""
    if element.get('text') is not None:
        return _quoted_lines(element.get('text').replace('\r\n', '\n'))
    if element.text:
        return element.text.replace('\r\n', '\n')
    for child in element:
        if child.tail and child.tail.strip():
            return child.tail.replace('\r\n', '\n')
    return ''


def _styled(text, element, description):
    """[(text, Style)] for a sample's text and its modifiers."""
    units = _units(text)
    records = [description.style] * len(text)
    flashing = [False] * len(text)
    for child in element:
        if child.tag == 'Style':
            first, after = _span(child, units)
            for index in range(first, after):
                records[index] = _record(child, description.style)
        elif child.tag == 'Blinking':
            first, after = _span(child, units)
            for index in range(first, after):
                flashing[index] = True
    runs = []
    base = description.style
    for index, char in enumerate(text):
        record = records[index]
        size = ''
        if record['size'] != base['size'] and base['size']:
            size = markup.size_percent(f'{record["size"] * 100 / base["size"]:g}%')
        style = markup.Style(italic='Italic' in record['flags'], bold='Bold' in record['flags'],
                             underline='Underlined' in record['flags'], strike='Strikethrough' in record['flags'],
                             color=record['color'] if record['color'] != base['color'] else '', size=size,
                             flash=flashing[index])
        if char == '\n':
            style = markup.PLAIN
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + char, style)
        else:
            runs.append((char, style))
    return runs


def _karaoke(runs, element, units):
    """The text with karaoke codes for a sample whose Karaoke lights its
    stretches; None without one."""
    lit = element.find('Karaoke')
    if lit is None:
        return None
    ranges = [(_span(child, units), float(child.get('endTime') or 0)) for child in lit if child.tag == 'KaraokeRange']
    if not ranges:
        return None
    chars = [(char, style) for run, style in runs for char in run]

    def text_of(first, after):
        return markup.from_spans([(char, style) for char, style in chars[first:after]])

    begin = float(lit.get('startTime') or 0)
    pieces = []
    for index, ((first, _after), end) in enumerate(ranges):
        # A stretch runs to the next one's start, so no text falls between.
        following = ranges[index + 1][0][0] if index + 1 < len(ranges) else len(chars)
        pieces.append((begin, max(begin, end), text_of(first, max(first, following))))
        begin = max(begin, end)
    return text_of(0, ranges[0][0][0]) + karaoke.text_from(0, pieces)


def parse(data):
    """(subtitles) in TTXT bytes or text. Raises ValueError for one that is
    not GPAC's TTXT."""
    raw = data.encode('utf-8') if isinstance(data, str) else bytes(data)
    if kind(raw) == 'transcript':
        raise ValueError("Not GPAC's TTXT: this .ttxt is CCExtractor's timed transcript")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError(f"Not GPAC's TTXT: {error}") from error
    if root.tag != 'TextStream':
        raise ValueError(f"Not GPAC's TTXT: its root is <{root.tag}>, not <TextStream>")
    header = root.find('TextStreamHeader')
    descriptions = [_Description(element) for element in (header.findall('TextSampleDescription') if header is not None else [])]
    if not descriptions:
        descriptions = [_Description()]

    samples = []                                   # (time, text or None)
    for element in root.iter('TextSample'):
        start = round(_seconds(element.get('sampleTime')), 3)
        text = _sample_text(element)
        if not text.strip():
            samples.append((start, None, None))
            continue
        index = _int(element.get('sampleDescriptionIndex'), 1)
        description = descriptions[index - 1] if 1 <= index <= len(descriptions) else descriptions[0]
        runs = _styled(text, element, description)
        content = _karaoke(runs, element, _units(text))
        if content is None:
            content = markup.from_spans(runs)
        samples.append((start, content, description))

    segments = []
    for index, (start, content, description) in enumerate(samples):
        if content is None:
            continue
        end = samples[index + 1][0] if index + 1 < len(samples) else start + TAIL
        segment = {'start': start, 'end': round(max(start, end), 3),
                   'text': markup.with_alignment('', description.keypad()) + content}
        if description.vertical:
            segment['writing'] = 'vertical-rl'
        segments.append(segment)
    return segments


def read(path):
    """The subtitles in the TTXT file at `path`, as parse() gives them."""
    with open(path, 'rb') as ttxt_file:
        return parse(ttxt_file.read())


# Writing.
def _time(seconds):
    milliseconds = int(round(max(0.0, float(seconds)) * 1000))
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    return f'{hours:02d}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}'


def _hex_color(color, alpha=255):
    value = markup.color_hex(color) or '#ffffff'
    return ' '.join(f'{int(value[at:at + 2], 16):x}' for at in (1, 3, 5)) + f' {alpha:x}'


def _style_element(first, after, style, font_size):
    flags = [name for name, on in (('Bold', style.bold), ('Italic', style.italic), ('Underlined', style.underline),
                                   ('Strikethrough', style.strike)) if on]
    size = font_size
    if style.size:
        size = max(1, round(font_size * float(style.size.rstrip('%')) / 100))
    return (f'<Style fromChar="{first}" toChar="{after}" styles="{" ".join(flags) or "Normal"}" fontID="1" '
            f'fontSize="{size}" color="{_hex_color(style.color)}"/>')


def _sample(segment, index, font_size):
    """A TextSample element for a subtitle, at description `index`."""
    # All of the text: a karaoke code may come first, where a place does;
    # the place ({\an8}) draws nothing and has no syllable.
    rest = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
    runs = markup.without_readings(markup.spans(rest))
    text = ''.join(run for run, _style in runs)
    modifiers = []
    at = 0                                         # in UTF-16 units
    for run, style in runs:
        length = len(run.encode('utf-16-le')) // 2
        if style.bold or style.italic or style.underline or style.strike or style.color or style.size:
            modifiers.append(_style_element(at, at + length, style, font_size))
        if style.flash:
            modifiers.append(f'<Blinking fromChar="{at}" toChar="{at + length}"/>')
        at += length
    pieces = karaoke.timed(rest, 0)
    if pieces:
        ranges = []
        at = 0
        # Text before the first code is not lit: no range of its own.
        unlit = karaoke.syllables(rest)[0][0] is None
        for number, (begin, end, piece) in enumerate(pieces):
            length = len(piece.encode('utf-16-le')) // 2
            if not (number == 0 and unlit):
                # A pause lights nothing: a range of no characters.
                ranges.append(f'<KaraokeRange fromChar="{at}" toChar="{at + length}" endTime="{end:g}"/>')
            at += length
        modifiers.append('<Karaoke startTime="0">\n' + '\n'.join(ranges) + '\n</Karaoke>')
    return (f'<TextSample sampleTime="{_time(segment["start"])}" sampleDescriptionIndex="{index}" xml:space="preserve">'
            + escape(text, {'"': '&quot;', "'": '&apos;'}) + ''.join(modifier + '\n' for modifier in modifiers)
            + '</TextSample>')


def _description(keypad, vertical, width, height, font_size):
    row, column = (keypad - 1) // 3, (keypad - 1) % 3
    return (f'<TextSampleDescription horizontalJustification="{("left", "center", "right")[column]}" '
            f'verticalJustification="{("bottom", "center", "top")[row]}" backColor="0 0 0 0" '
            f'verticalText="{"yes" if vertical else "no"}" fillTextRegion="no" continuousKaraoke="no" scroll="None">\n'
            '<FontTable>\n<FontTableEntry fontName="Sans-Serif" fontID="1"/>\n</FontTable>\n'
            f'<TextBox top="0" left="0" bottom="{height}" right="{width}"/>\n'
            f'<Style styles="Normal" fontID="1" fontSize="{font_size}" color="ff ff ff ff"/>\n'
            '</TextSampleDescription>')


def write(segments, width=None, height=None):
    """TTXT text for `segments`, its stream `width` by `height` pixels
    (the video's; GPAC's 400 by 80 without them)."""
    width, height = int(width or DEFAULT_WIDTH), int(height or DEFAULT_HEIGHT)
    # A size that reads on the picture: a twentieth of its height, never
    # under GPAC's default.
    font_size = max(DEFAULT_FONT_SIZE, round(height / 20))
    places = []                                    # [(keypad, vertical)]
    samples = []
    last_end = None
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        if not markup.plain(str(segment.get('text') or '')).strip():
            continue
        start, end = float(segment.get('start', 0)), max(float(segment.get('start', 0)), float(segment.get('end', 0)))
        if last_end is not None and start > last_end:
            samples.append(f'<TextSample sampleTime="{_time(last_end)}" text=""/>')
        place = (markup.alignment(str(segment.get('text') or '')), segment.get('writing') in ('vertical-rl', 'vertical-lr'))
        if place not in places:
            places.append(place)
        samples.append(_sample(dict(segment, start=start), places.index(place) + 1, font_size))
        last_end = end
    if last_end is not None:
        samples.append(f'<TextSample sampleTime="{_time(last_end)}" text=""/>')
    if not places:
        places.append((2, False))
    out = ['<?xml version="1.0" encoding="UTF-8" ?>', '<!-- GPAC 3GPP Text Stream -->', '<TextStream version="1.1">',
           f'<TextStreamHeader width={quoteattr(str(width))} height={quoteattr(str(height))} layer="0" '
           'translation_x="0" translation_y="0">']
    out.extend(_description(keypad, vertical, width, height, font_size) for keypad, vertical in places)
    out.append('</TextStreamHeader>')
    out.extend(samples)
    out.append('</TextStream>')
    return '\n'.join(out) + '\n'


def save(path, segments, width=None, height=None):
    """Write `segments` to `path` as TTXT, UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as ttxt_file:
        ttxt_file.write(write(segments, width, height))
