"""EZTitles: the XML project export (.eztxml) EZTitles writes for other
tools, read and written, and its own binary project (.ezt), read.

EZTitles (Eclipse) documents neither in public. What is here follows
Subtitle Edit's readers and writer (libse, EZTSubtitlesProject.cs and
EZTitlesBinary.cs, GPL-3 as Subtitld is) and their tests, which were
worked out from EZTitles 5.3 files and their .eztxml exports.

The XML:

    <EZTSubtitlesProject version="1.3">
      <ProjectConfiguration>
        <VideoFrameRate>25 fps</VideoFrameRate>
        <TimeCodeStandard>25</TimeCodeStandard>   24 25 30 30drop 50 60 60drop
        ...                                       (safe area, fonts: kept by
      </ProjectConfiguration>                      Subtitle Edit's template)
      <Subtitles count="1">
        <Subtitle id="sub1" number="1" incue="10:00:01:12" outcue="10:00:03:00">
          <VisualAttributes row_position="center" row_justification="center"
                            vertical_align="bottom"/>
          <Rows>
            <Row italic="true" foreground_color="yellow"><Text>A row</Text></Row>
            <Row><Text>Only <span italic="true">this</span></Text></Row>
          </Rows>
        </Subtitle>
      </Subtitles>
    </EZTSubtitlesProject>

The cues are HH:MM:SS:FF in the time code standard, which need not be the
video's rate (a 23.976 project may time in "30drop"); without one, the
VideoFrameRate's number; "--:--:--:--" for a subtitle not timed yet, read
as 0. Subtitle Edit counts the frames within a second at the standard's
rate whether it drops frames or not; Subtitld counts a "drop" standard as
the drop-frame timecode it names (frames 0 and 1 of each minute but every
tenth skipped), which is the same to within a frame or two over an hour —
a choice of Subtitld's.

Reading takes italic and foreground_color from a row and from its spans
(white being the default), vertical_align (top, center, bottom) and
row_justification (left, center, right) as one of the nine places, as
Subtitle Edit does. Writing gives Subtitle Edit's template with the
standard of the rate asked (30drop at 29.97, 60drop at 59.94, else the
rate rounded) and, beyond what Subtitle Edit writes, what its reader
reads: the place as VisualAttributes, italics on a row or a span, and
colours by the names white, yellow, cyan, green, magenta, red, blue and
black (green being #00ff00 there); other colours are left out.

The binary, read only: "EZTZ" and a zlib stream holding a header of no
fixed size (fonts, safe area, media) and the subtitles, a record each:

    int32 size (itself not counted), int16 number, a flag byte,
    the in and out cues as frames, seconds, minutes, hours (a byte each;
        minutes and hours FF for no time), seven bytes of layout whose last
        is the vertical alignment (0 bottom, 1 centre, 2 top), the number
        of rows, then a row each:
    a small header ending in the justification (0 left, 1 right, 2 centre)
        and 03, int32 characters, the text in UTF-32LE, the count again,
        and an int32 attribute size and a blob that size a character:
        byte 0 flags (02 italic), bytes 1-3 the colour, byte 4 1F when it
        is the default colour.

The first record is found by its first row's "count, text, count" shape and
the records before it (subtitles with no text) by chaining back from it.
The file does not say the rate its frames count at: up to 24, the video's
(25 when it says less); 25 to 29, 29.97; 30 and up, 60 — Subtitle Edit's
guess, followed here.
"""

import struct
import xml.etree.ElementTree as ET
import zlib
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import markup
from subtitld.modules.avid_subcap import frame_of, timecode

DEFAULT_FPS = 25.0
_MAGIC = b'EZTZ'
_MAX_ROW = 500
# The colours EZTitles names, both ways.
_NAMES = {'white': '#ffffff', 'yellow': '#ffff00', 'cyan': '#00ffff', 'green': '#00ff00', 'magenta': '#ff00ff',
          'red': '#ff0000', 'blue': '#0000ff', 'black': '#000000'}
_COLOR_NAMES = {value: name for name, value in _NAMES.items()}
_VERTICAL = {'top': 2, 'center': 1}
_JUSTIFIED = {'left': 0, 'right': 2}


# -- The time code standard ---------------------------------------------------

def parse_standard(text):
    """(frame rate, drop-frame) for a time code standard ("25", "30drop",
    "23.976 fps"), or None for one that is not."""
    value = str(text or '').strip().lower()
    drop = value.endswith('drop')
    if drop:
        value = value[:-4]
    try:
        rate = float(value.replace('fps', '').strip())
    except ValueError:
        return None
    if rate <= 0:
        return None
    if drop:
        rate = 59.94 if rate > 45 else 29.97
    return rate, drop


def standard_of(fps):
    """The time code standard for a frame rate, as Subtitle Edit writes it."""
    fps = float(fps or DEFAULT_FPS)
    if abs(fps - 29.97) < 0.01:
        return '30drop'
    if abs(fps - 59.94) < 0.01:
        return '60drop'
    return str(int(round(fps)))


def _nominal(fps):
    return int(round(fps))


def _cue_seconds(cue, fps, drop):
    """Seconds for an HH:MM:SS:FF cue; 0 for one not timed ("--")."""
    cue = (cue or '').strip()
    if not cue or cue.startswith('-'):
        return 0.0
    parts = cue.replace(';', ':').replace('.', ':').split(':')
    if len(parts) != 4:
        raise ValueError(f'Not a time code: {cue!r}')
    hours, minutes, seconds, frames = (int(part) for part in parts)
    if drop:
        nominal = _nominal(fps)
        return round(frame_of(hours, minutes, seconds, frames, nominal, True) * 1001 / (nominal * 1000), 3)
    return round(hours * 3600 + minutes * 60 + seconds + frames / fps, 3)


def _cue(seconds, fps, drop):
    """The HH:MM:SS:FF cue for a time."""
    seconds = max(0.0, float(seconds))
    if drop:
        nominal = _nominal(fps)
        return timecode(int(round(seconds * nominal * 1000 / 1001)), nominal, True).replace(';', ':')
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= _nominal(fps):
        whole, frames = whole + 1, 0
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{second:02d}:{frames:02d}'


def _keypad(vertical, justification):
    """The keypad number for a vertical alignment (0 bottom, 1 centre, 2
    top) and a justification (0 left, 1 centre, 2 right)."""
    return (1, 4, 7)[vertical] + justification


# -- The XML ------------------------------------------------------------------

def looks_like(head):
    """True when `head` (the first bytes or the decoded text of a file) is
    an EZTitles XML project."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:4096]).decode('utf-8', 'ignore')
    return '<EZTSubtitlesProject' in str(head or '')[:4096]


def _style_of(element, style):
    """`style` with the italic and colour an element says."""
    if element.get('italic') == 'true':
        style = style._replace(italic=True)
    color = (element.get('foreground_color') or '').strip().lower()
    if color and color != 'white':
        style = style._replace(color=markup.color_hex(_NAMES.get(color, color)) or style.color)
    return style


def _row_runs(row):
    """[(text, Style)] for a <Row>: its <Text>, spans and all."""
    base = _style_of(row, markup.PLAIN)
    text = row.find('Text')
    if text is None:
        return [(''.join(row.itertext()), base)]
    runs = [(text.text or '', base)]
    for child in text:
        runs.append((''.join(child.itertext()), _style_of(child, base)))
        runs.append((child.tail or '', base))
    return runs


def parse(text):
    """(subtitles, {'fps', 'drop'}) in an EZTitles XML project's text: the
    rate and drop-frame of its time code standard. Raises ValueError for
    text that is not one."""
    if not looks_like(text):
        raise ValueError('Not an EZTitles XML project: it has no EZTSubtitlesProject')
    try:
        root = ET.fromstring(text.encode('utf-8') if isinstance(text, str) else text)
    except ET.ParseError as error:
        raise ValueError(f'A damaged EZTitles XML project: {error}') from error
    standard = None
    for tag in ('TimeCodeStandard', 'VideoFrameRate'):
        found = root.find(f'.//{tag}')
        standard = parse_standard(found.text) if found is not None else None
        if standard:
            break
    fps, drop = standard or (DEFAULT_FPS, False)
    subtitles = root.find('Subtitles')
    segments = []
    for subtitle in (subtitles.findall('Subtitle') if subtitles is not None else []):
        rows = subtitle.find('Rows')
        if rows is None or subtitle.get('incue') is None or subtitle.get('outcue') is None:
            continue
        runs = []
        for number, row in enumerate(rows.findall('Row')):
            if number:
                runs.append(('\n', markup.PLAIN))
            runs.extend(_row_runs(row))
        body = markup.tidy(runs)
        visual = subtitle.find('VisualAttributes')
        keypad = 2
        if visual is not None:
            keypad = _keypad(_VERTICAL.get(visual.get('vertical_align'), 0),
                             _JUSTIFIED.get(visual.get('row_justification'), 1))
        start = _cue_seconds(subtitle.get('incue'), fps, drop)
        end = _cue_seconds(subtitle.get('outcue'), fps, drop)
        segments.append({'start': start, 'end': max(start, end),
                         'text': (markup.with_alignment('', keypad) if keypad != 2 and body else '') + body})
    return segments, {'fps': fps, 'drop': drop}


def read(path):
    """(subtitles, its settings) in the EZTitles XML project at `path`, as
    parse() gives them."""
    with open(path, 'rb') as xml_file:
        return parse(xml_file.read().decode('utf-8-sig', 'replace'))


_TEMPLATE = '''<?xml version="1.0" encoding="utf-8"?>
<EZTSubtitlesProject version="1.3">
  <ProjectConfiguration>
    <SubtitlesType>open</SubtitlesType>
    <SubtitlesMode>native</SubtitlesMode>
    <DvdSystem>None</DvdSystem>
    <VideoFormat>HD1080</VideoFormat>
    <VideoFrameRate>{rate} fps</VideoFrameRate>
    <AspectRatio>16x9</AspectRatio>
    <Letterbox>none</Letterbox>
    <TimeCodeStandard>{standard}</TimeCodeStandard>
    <SafeArea units="pixels" left_edge="192" center="960" right_edge="1728" top_edge="108" bottom_edge="1007" spacing_type="row_interval" spacing="-8" sa_override="true" max_chars="42" max_chars_vertical="36" max_chars_count_halfwidth_as_half="false">
      <BottomAlign type="above_bottom" base_line="810"/>
      <ClosedCaptions double_control_codes="true" interleave_23_976_CC_data="false" blank_between_popons="2" use_extended_char_set="true"/>
    </SafeArea>
    <Fonts number_of_fonts="2">
      <Font1 name="Arial" height="25" metric_language="" metric="" bold="false" spacing="0" scalex="100" rtl="false" asian_font_name=""/>
      <Font2 name="Arial Unicode MS" height="54" metric_language="" metric="" bold="false" spacing="0" scalex="100" rtl="false" asian_font_name=""/>
    </Fonts>
  </ProjectConfiguration>
  <Subtitles count="{count}">
{subtitles}  </Subtitles>
</EZTSubtitlesProject>
'''


def _attributes(style):
    out = ''
    if style.italic:
        out += ' italic="true"'
    name = _COLOR_NAMES.get(style.color)
    if name and name != 'white':
        out += f' foreground_color="{name}"'
    return out


def _row(runs):
    """A <Row> for a line's runs: what holds for the whole line on the row,
    the rest on spans."""
    letters = [style for run, style in runs if run.strip()]
    italic = bool(letters) and all(style.italic for style in letters)
    colors = {style.color for style in letters}
    color = colors.pop() if len(colors) == 1 else ''
    row_style = markup.PLAIN._replace(italic=italic, color=color if color in _COLOR_NAMES else '')
    inner = ''
    for run, style in runs:
        own = markup.PLAIN._replace(italic=style.italic and not italic,
                                    color=style.color if style.color != row_style.color else '')
        if own.italic or own.color in _COLOR_NAMES:
            inner += f'<span{_attributes(own)}>{escape(run)}</span>'
        else:
            inner += escape(run)
    return f'<Row{_attributes(row_style)}><Text>{inner}</Text></Row>'


def write(segments, fps=DEFAULT_FPS):
    """EZTitles XML project text for `segments`, cued in the time code
    standard of `fps`."""
    fps = float(fps or DEFAULT_FPS)
    standard = standard_of(fps)
    rate, drop = parse_standard(standard)
    blocks = []
    ordered = sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
    for number, segment in enumerate(ordered, 1):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        keypad = markup.alignment(lead) or 2
        lines = [[]]
        for run, style in markup.without_readings(markup.spans(rest)):
            for index, part in enumerate(run.split('\n')):
                if index:
                    lines.append([])
                if part:
                    lines[-1].append((part, style))
        vertical = ('bottom', 'center', 'top')[(keypad - 1) // 3]
        justification = ('left', 'center', 'right')[(keypad - 1) % 3]
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        blocks.append(
            f'    <Subtitle id="sub{number}" number="{number}" incue="{_cue(start, rate, drop)}" '
            f'outcue="{_cue(end, rate, drop)}">\n'
            f'      <VisualAttributes row_position={quoteattr(justification)} row_justification={quoteattr(justification)} '
            f'vertical_align={quoteattr(vertical)}/>\n'
            '      <Rows>\n' + ''.join(f'        {_row(line)}\n' for line in lines) + '      </Rows>\n'
            '    </Subtitle>\n')
    rate_text = f'{fps:.3f}'.rstrip('0').rstrip('.')
    return _TEMPLATE.format(rate=rate_text, standard=standard, count=len(blocks), subtitles=''.join(blocks))


def save(path, segments, fps=DEFAULT_FPS):
    """Write `segments` to `path` as an EZTitles XML project in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as xml_file:
        xml_file.write(write(segments, fps))


# -- The binary ---------------------------------------------------------------

def is_ezt(data):
    """True when `data` begins as an EZTitles binary project: "EZTZ" and a
    zlib stream."""
    return len(data) >= 6 and data[:4] == _MAGIC and data[4] == 0x78


def _int32(data, at):
    return struct.unpack_from('<i', data, at)[0]


def _find_row(data, start, end):
    """(where a row's first character count is, its text, where its
    attributes start) for the next row at or after `start`, or None."""
    for at in range(max(start, 4), end - 9):
        if data[at - 2] > 2 or data[at - 1] != 3 or data[at - 3] or data[at - 4]:
            continue
        length = _int32(data, at)
        if length <= 0 or length > _MAX_ROW:
            continue
        text_end = at + 4 + length * 4
        if text_end + 4 > end or _int32(data, text_end) != length:
            continue
        codes = struct.unpack_from(f'<{length}I', data, at + 4)
        if any(code < 0x20 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF for code in codes):
            continue
        return at, ''.join(chr(code) for code in codes), text_end + 4
    return None


def _row_runs_binary(data, at, text, attributes_at, end):
    """([(text, Style)], where the row ends) for a row and the attribute
    blobs after it."""
    length = len(text)
    if attributes_at + 4 > end:
        return [(text, markup.PLAIN)], attributes_at
    size = _int32(data, attributes_at)
    if size < 5 or size > 64 or attributes_at + 4 + size * length > end:
        return [(text, markup.PLAIN)], attributes_at
    runs, blob = [], attributes_at + 4
    for char in text:
        style = markup.PLAIN._replace(italic=bool(data[blob] & 0x02))
        if data[blob + 4] != 0x1F:
            color = '#%02x%02x%02x' % tuple(data[blob + 1:blob + 4])
            style = style._replace(color='' if color == '#ffffff' else color)
        runs.append((char, style))
        blob += size
    return runs, blob


def _plausible(data, at):
    return (all(data[at + offset] < 60 for offset in (3, 4, 5, 7, 8, 9)) and data[at + 18] <= 8)


def _records(data):
    """[(number, start cue bytes, end cue bytes, vertical, [rows of runs],
    justification)] in a decompressed project."""
    found = _find_row(data, 0, len(data))
    if not found:
        return []
    # The row header before the first text is of fixed size: back from it
    # to the record's size field.
    at = found[0] - 2 - 8 - 19 - 4
    if at < 0 or _int32(data, at) < 19 or at + 4 + _int32(data, at) > len(data):
        return []
    # Records without text come before the first one with: chain back
    # through records that end where the known one starts.
    number = struct.unpack_from('<h', data, at + 4)[0]
    chained = True
    while chained and at > 0:
        chained = False
        for back in range(at - 4 - 19, -1, -1):
            if back + 4 + _int32(data, back) != at or not _plausible(data, back + 4):
                continue
            earlier = struct.unpack_from('<h', data, back + 4)[0]
            if 1 <= earlier <= number:
                at, number, chained = back, earlier, True
                break
    records = []
    while at + 4 <= len(data):
        size = _int32(data, at)
        if size < 19 or at + 4 + size > len(data):
            break
        start, end = at + 4, at + 4 + size
        rows, justification, scan = [], 1, start + 19
        while scan < end:
            row = _find_row(data, scan, end)
            if not row:
                break
            if not rows:
                justification = {0: 0, 1: 2}.get(data[row[0] - 2], 1)
            runs, scan = _row_runs_binary(data, row[0], row[1], row[2], end)
            rows.append(runs)
        records.append((data[start + 3:start + 7], data[start + 7:start + 11], {1: 1, 2: 2}.get(data[start + 17], 0), rows,
                        justification))
        at = end
    return records


def parse_binary(data, fps=None):
    """(subtitles, {'fps'}) in an EZTitles binary project's bytes. Raises
    ValueError for bytes that are not one."""
    if not is_ezt(data):
        raise ValueError('Not an EZTitles project: it does not begin as one does')
    try:
        body = zlib.decompress(data[4:])
    except zlib.error:
        try:
            body = zlib.decompressobj().decompress(data[4:])
        except zlib.error as error:
            raise ValueError(f'A damaged EZTitles project: {error}') from error
    records = _records(body)
    if not records:
        raise ValueError('An EZTitles project with no subtitles found in it')

    def unset(cue):
        return cue[2] == 0xFF and cue[3] == 0xFF

    highest = max(max(0 if unset(cue) else cue[0] for cue in (start, end)) for start, end, *_ in records)
    rate = float(fps or DEFAULT_FPS)
    if highest >= 30:
        rate = 60.0
    elif highest >= 25:
        rate = 29.97
    elif highest == 24 and rate < 25:
        rate = 25.0

    def seconds(cue):
        if unset(cue):
            return 0.0
        return round(cue[3] * 3600 + cue[2] * 60 + cue[1] + cue[0] / rate, 3)

    segments = []
    for start, end, vertical, rows, justification in records:
        runs = []
        for index, row in enumerate(rows):
            if index:
                runs.append(('\n', markup.PLAIN))
            runs.extend(row)
        body_text = markup.tidy(runs)
        keypad = _keypad(vertical, justification)
        first, last = seconds(start), seconds(end)
        segments.append({'start': first, 'end': max(first, last),
                         'text': (markup.with_alignment('', keypad) if keypad != 2 and body_text else '') + body_text})
    return segments, {'fps': rate}


def read_binary(path, fps=None):
    """(subtitles, its settings) in the EZTitles binary project at `path`,
    as parse_binary() gives them."""
    with open(path, 'rb') as ezt_file:
        return parse_binary(ezt_file.read(), fps)
