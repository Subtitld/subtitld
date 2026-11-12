"""DCP subtitles (.xml): read the subtitles cinemas project, in both of the
Digital Cinema Package's generations, and write either.

A DCP carries its subtitles as XML a projector's server draws over the
picture. There are two kinds, and cinemas still play both:

- Interop, the first, written down by Texas Instruments as CineCanvas: a
  <DCSubtitle> root, no namespace, the film's title in <MovieTitle>, a
  <SubtitleID> UUID, and times as HH:MM:SS:TTT in ticks of 1/250 of a
  second (HH:MM:SS.mmm, and in files from before the ticks were settled,
  HH:MM:SS:mmm in milliseconds);
- SMPTE ST 428-7, the standard that followed: a <SubtitleReel> root in the
  namespace http://www.smpte-ra.org/schemas/428-7/2010/DCST (2007 before
  it, 2014 in some files after), an urn:uuid <Id>, <ContentTitleText>,
  <AnnotationText>, <IssueDate>, the reel's <EditRate> and
  <TimeCodeRate>, and times as HH:MM:SS:EE in frames of that rate, which
  may count from a <StartTime> other than zero.

The body is the same in both: <Subtitle> elements with a TimeIn and a
TimeOut, holding <Text> elements, one a line, each placed by its own
VAlign and VPosition (from the top, the middle or the bottom of the
picture, in % of its height) and HAlign and HPosition (likewise across).
<Font> elements wrap any of it — the whole reel, a subtitle, a line, a few
words — with its colour as AARRGGBB, Italic, Weight, Underlined (SMPTE:
Underline), Size in points of a picture eleven inches tall, and an Effect
(a border or a shadow) in an EffectColor. SMPTE spells the attributes
Halign, Hposition, ID ..., Interop HAlign, HPosition, Id; either is read in
either, as projectors do. A subtitle may be a picture instead, an <Image>
holding a PNG's file name (Interop) or its urn:uuid (SMPTE).

Reading:

- the root element says which generation it is (`kind`), and the
  subtitles are every <Subtitle>, wherever the <Font>s around it put it;
- Interop's times in ticks, or in milliseconds where a file counts past
  the 249th tick, which a tick cannot; SMPTE's in frames of the
  TimeCodeRate, from the StartTime where every subtitle comes after it;
- a subtitle's lines in the order they are on the picture, top to bottom
  (right to left, or left to right, for a subtitle written down it);
- where it sits as one of the nine places, from the VAlign and HAlign of
  its outermost line, and, where the file puts it elsewhere than Subtitld
  would (modules.position: the title-safe margins, the middle of a
  centred axis), the margins it is put at;
- italic, bold, underline, a colour other than white, Script's raised and
  lowered text and a size against the subtitle's as formatting tags
  (modules.markup); <Ruby> as a ruby, its reading after the text where its
  Position says so; <HGroup>, the characters set across a column, as a run
  set upright in one character's room; <Space> as a space;
- what a subtitle's own <Font>s say beyond those around it — its size, its
  border or shadow and their colour — as its look (modules.look), so the
  reel's own baseline does not take over the style the user chose. A font
  a file loads is a file of its own beside it, not a family a renderer
  knows by name, so which font it names is not kept; fades have no place
  in Subtitld's model and are not either;
- Direction "vertical" (Interop) and "ttb" or "btt" (SMPTE) as a subtitle
  written down the picture, "rtl" as its direction;
- the language as <Language> gives it, a code or a name ("English"); the
  title as the subtitles' metadata;
- an <Image> is not text: `read_images` gives the pictures, with where they
  sit, for an OCR add-on to read (modules.picture_ocr).

Writing gives either generation (`STANDARDS`). One <Font> around every
subtitle says the baseline: white letters with a black border at 42
points, the size Subtitle Edit and DCP-o-matic start from. A subtitle's own
look goes on a <Font> of its own inside it, and the formatting of a few
words on one around them. Each line is a <Text>, the lines stacked from
the place its keypad says, at its margins (the title-safe area where it
gives none), a line's height apart. Times are ticks (Interop) or frames at
the export's rate (SMPTE: 24, 25, 30, 48, 50 or 60, the video's nearest),
counted from 00:00:00:00, with no fade in or out: the subtitle shows when
its times say. No <LoadFont> is written, as Subtitld has no font file to
put beside the subtitles: the projector's own font draws them, and a
mastering tool that wraps the file into a package adds the one it ships.

Or the subtitles go as pictures (`write_images`, CONTENTS): each drawn as
Subtitld's preview draws it (interface.subtitle_pictures), a PNG beside the
file, an <Image> a subtitle, centred across where the picture's middle is
and placed down from the top or up from the bottom, whichever edge it is
nearer. Interop's <Image> names its PNG, which a package carries beside the
XML. SMPTE's names a PNG by urn:uuid, and ST 429-5 asks for the PNGs to be
in the MXF track file with the XML; Subtitld does not wrap MXF, so it
writes the XML with the PNGs loose beside it, each named <uuid>.png (as
`image_files` looks for them), for a mastering tool to wrap; a projector
does not play the loose files.
"""

import datetime
import re
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import export_options, look as subtitld_look, markup, position

INTEROP, SMPTE = 'interop', 'smpte'
STANDARDS = (INTEROP, SMPTE)
# What the subtitles are written as: their text, or pictures of it.
CONTENTS = ('text', 'pictures')
SMPTE_NAMESPACE = 'http://www.smpte-ra.org/schemas/428-7/2010/DCST'
# The frame rates a DCP plays at, which a SMPTE reel counts its times in.
RATES = (24, 25, 30, 48, 50, 60)
DEFAULT_RATE = 24
TICKS = 250                      # Interop's ticks in a second

# The size a <Font> has where none says one, in points of a picture 11
# inches (792 points) tall: a 72-point letter is an eleventh of the height.
DEFAULT_SIZE = 42
_POINTS_HIGH = 11 * 72
# The lines of a subtitle are this many times the letters' height apart.
_LINE_SPACING = 1.2
# A border or a shadow is drawn this much of the letters' height thick.
_EFFECT_WIDTH = 0.05

_TIME = re.compile(r'^\s*(\d+):(\d{1,2}):(\d{1,2})(?:([:.])(\d+))?\s*$')
_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
_COLOUR = re.compile(r'^\s*([0-9a-fA-F]{8})\s*$')

_VERTICAL = {'vertical': 'vertical-rl', 'ttb': 'vertical-rl', 'btt': 'vertical-rl'}
_ROWS = {'top': 2, 'center': 1, 'bottom': 0}
_COLUMNS = {'left': 0, 'center': 1, 'right': 2}
_ROW_NAMES = {row: name for name, row in _ROWS.items()}
_COLUMN_NAMES = {column: name for name, column in _COLUMNS.items()}


def _local(name):
    return name.rsplit('}', 1)[-1] if isinstance(name, str) else ''


def _attr(element, name):
    """An attribute by its name, whatever its case: Interop's HAlign and
    SMPTE's Halign alike, as projectors read them."""
    name = name.lower()
    for key, value in element.attrib.items():
        if _local(key).lower() == name:
            return value
    return None


def _number(value, default=None):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def kind(root):
    """INTEROP, SMPTE or None for the root element of an XML document."""
    tag = _local(root.tag)
    if tag == 'DCSubtitle':
        return INTEROP
    if tag == 'SubtitleReel' and (root.tag == tag or '428-7' in root.tag):
        return SMPTE
    return None


def is_dcp(path):
    """Whether the XML file at `path` is DCP subtitles, by its root: .xml is
    TTML's and Captionate's too, and only the root tells them apart."""
    try:
        for _event, element in ET.iterparse(path, events=('start',)):
            return kind(element) is not None
    except (ET.ParseError, OSError):
        return False
    return False


def rate_for(fps):
    """The DCP frame rate nearest a video's (23.976 is 24, 29.97 is 30),
    DEFAULT_RATE without one."""
    fps = _number(fps, 0) or 0
    if fps <= 0:
        return DEFAULT_RATE
    return min(RATES, key=lambda rate: abs(rate - fps))


# Reading.
class _Clock:
    """Seconds for a DCP's times, by its generation and its rate."""

    def __init__(self, root, standard):
        self.standard = standard
        self.rate = TICKS
        self.start = 0.0
        if standard == SMPTE:
            rate = _number(_child_text(root, 'TimeCodeRate'))
            if not rate:
                edit_rate = (_child_text(root, 'EditRate') or '').split()
                rate = _number(edit_rate[0]) if edit_rate else None
            self.rate = int(rate) if rate and rate > 0 else DEFAULT_RATE
        else:
            # Files from before the ticks were settled count milliseconds in
            # the same place, which shows where they pass the 249th tick.
            counts = [int(found.group(5)) for found in (
                _TIME.match(_attr(element, name) or '') for element in root.iter()
                if _local(element.tag) == 'Subtitle' for name in ('TimeIn', 'TimeOut'))
                if found and found.group(4) == ':' and found.group(5)]
            if any(count >= TICKS for count in counts):
                self.rate = 1000

    def seconds(self, value):
        found = _TIME.match(value or '')
        if not found:
            raise ValueError(f'Not a DCP time: "{value}"')
        hours, minutes, seconds, mark, fraction = found.groups()
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if fraction:
            # HH:MM:SS.mmm is a fraction of a second in either generation.
            total += int(fraction) / 10 ** len(fraction) if mark == '.' else int(fraction) / self.rate
        return total


def _child_text(root, name):
    for element in root:
        if _local(element.tag) == name:
            return (element.text or '').strip()
    return ''


# What a <Font> says, by the names it says it with.
_FONT_FIELDS = {'color': 'color', 'effect': 'effect', 'effectcolor': 'effect_color', 'italic': 'italic',
                'weight': 'weight', 'underlined': 'underline', 'underline': 'underline', 'script': 'script',
                'size': 'size'}


def _font(element, state, own):
    """(state, own): what a <Font> sets over `state`, and over `own`, what
    the subtitle's own fonts have said so far."""
    said = {}
    for key, value in element.attrib.items():
        field = _FONT_FIELDS.get(_local(key).lower())
        if field and value is not None and str(value).strip():
            said[field] = str(value).strip()
    if not said:
        return state, own
    return dict(state, **said), (dict(own, **said) if own is not None else None)


def _argb(value):
    """'#aarrggbb' for a DCP colour (AARRGGBB), or ''."""
    found = _COLOUR.match(value or '')
    return '#' + found.group(1).lower() if found else ''


def _size(state):
    size = _number(state.get('size'))
    return size if size and size > 0 else DEFAULT_SIZE


def _style(state, base_size, ruby='', ruby_position='', combine=''):
    """The markup Style of a run in `state`, in a subtitle drawn at
    `base_size` points."""
    colour = _argb(state.get('color'))[3:]
    size = _size(state)
    return markup.Style(
        italic=str(state.get('italic') or '').lower() in ('yes', 'left', 'right'),
        bold=str(state.get('weight') or '').lower() == 'bold',
        underline=str(state.get('underline') or '').lower() == 'yes',
        color='' if not colour or colour == 'ffffff' else '#' + colour,
        offset={'super': 'super', 'sub': 'sub'}.get(str(state.get('script') or '').lower(), ''),
        size='' if abs(size - base_size) < 0.01 else f'{round(size / base_size * 100, 1):g}%',
        ruby=ruby, ruby_position=ruby_position if ruby else '', combine=combine)


def _runs(element, state, own, out):
    """The runs of a <Text> or of a <Font> inside one, in `out` as
    [(text, state, own, extra)]: what the fonts around it say, what the
    subtitle's own fonts among them say, and in `extra` what part of a ruby
    a run is, or that it is set upright."""
    if element.text:
        out.append((element.text, state, own, {}))
    for child in element:
        name = _local(child.tag)
        if name == 'Font':
            _runs(child, *_font(child, state, own), out)
        elif name == 'Ruby':
            base = next((part for part in child if _local(part.tag) == 'Rb'), None)
            reading = next((part for part in child if _local(part.tag) == 'Rt'), None)
            # A reading goes before its text unless it says after.
            where = 'after' if reading is not None and \
                str(_attr(reading, 'Position') or '').strip().lower() == 'after' else ''
            if base is not None:
                out.append((''.join(base.itertext()), state, own, {'ruby': 'base', 'ruby_position': where}))
            if reading is not None:
                out.append((''.join(reading.itertext()), state, own, {'ruby': 'text', 'ruby_position': where}))
        elif name == 'Space':
            out.append((' ', state, own, {}))
        elif name == 'HGroup':
            out.append((''.join(child.itertext()), state, own, {'combine': markup.COMBINE}))
        elif name == 'Rotate':
            out.append((''.join(child.itertext()), state, own, {}))
        if child.tail:
            out.append((child.tail, state, own, {}))
    return out


def _cross(line, writing):
    """Where a line sits across the way the lines stack, in % from the
    edge they start at: down from the top for lines written across; from
    the right (columns from the right) or the left for ones written down."""
    if writing:
        align, offset = line['halign'], line['hposition']
        across = offset if align == 'left' else 50 + offset if align == 'center' else 100 - offset
        return 100 - across if writing == 'vertical-rl' else across
    align, offset = line['valign'], line['vposition']
    return offset if align == 'top' else 50 + offset if align == 'center' else 100 - offset


def _subtitle(element, state, clock, start_offset):
    """{'start', 'end', 'text'[, 'position', 'look', 'writing', 'direction']}
    for a <Subtitle>, or None when it has no text."""
    start = clock.seconds(_attr(element, 'TimeIn')) - start_offset
    end = clock.seconds(_attr(element, 'TimeOut')) - start_offset
    lines = []

    def walk(node, node_state, own):
        for child in node:
            name = _local(child.tag)
            if name == 'Font':
                walk(child, *_font(child, node_state, own))
            elif name == 'Text':
                runs = _runs(child, node_state, own, [])
                direction = str(_attr(child, 'Direction') or '').strip().lower()
                lines.append({
                    'runs': runs,
                    'valign': str(_attr(child, 'VAlign') or 'center').strip().lower(),
                    'vposition': _number(_attr(child, 'VPosition'), 0.0),
                    'halign': str(_attr(child, 'HAlign') or 'center').strip().lower(),
                    'hposition': _number(_attr(child, 'HPosition'), 0.0),
                    'direction': direction})

    walk(element, state, {})
    lines = [line for line in lines if ''.join(run[0] for run in line['runs']).strip()]
    if not lines:
        return None
    for line in lines:
        line['valign'] = line['valign'] if line['valign'] in _ROWS else 'center'
        line['halign'] = line['halign'] if line['halign'] in _COLUMNS else 'center'
    writing = _VERTICAL.get(lines[0]['direction'], '')
    lines.sort(key=lambda line: _cross(line, writing))

    # The subtitle's own size, which its runs are measured against: its
    # first words'.
    _text, first_state, own, _extra = next(run for run in lines[0]['runs'] if run[0].strip())
    base_size = _size(first_state)
    runs = []
    for number, line in enumerate(lines):
        if number:
            runs.append(('\n', markup.PLAIN))
        for text, run_state, _own, extra in line['runs']:
            runs.append((re.sub(r'\s+', ' ', text), _style(run_state, base_size, **extra)))
    text = markup.tidy(runs)

    # Where it sits: the nine places, from the line nearest the edge it is
    # aligned to, and its margins where they are not Subtitld's own.
    if writing:
        aligned = [line for line in lines if line['halign'] == lines[0]['halign']] or lines
        column = _COLUMNS[aligned[0]['halign']]
        outermost = min(aligned, key=lambda line: line['hposition']) if column != 1 else None
        across = outermost['hposition'] if outermost else sum(line['hposition'] for line in aligned) / len(aligned)
        row = _ROWS[aligned[0]['valign']]
        down = aligned[0]['vposition']
    else:
        aligned = [line for line in lines if line['valign'] == lines[-1]['valign']] or lines
        row = _ROWS[aligned[0]['valign']]
        outermost = min(aligned, key=lambda line: line['vposition']) if row != 1 else None
        down = outermost['vposition'] if outermost else sum(line['vposition'] for line in aligned) / len(aligned)
        column = _COLUMNS[(outermost or aligned[0])['halign']]
        across = (outermost or aligned[0])['hposition']
    keypad = row * 3 + column + 1
    segment = {'start': round(start, 3), 'end': round(max(start, end), 3),
               'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + text}
    default_across, default_down = position.margins(position.NOWHERE, keypad)
    place = position.Place(h=position.percent(across) if abs(across - default_across) >= 0.05 else '',
                           v=position.percent(down) if abs(down - default_down) >= 0.05 else '')
    position.store(segment, place)
    if writing:
        segment['writing'] = writing
    elif lines[0]['direction'] == 'rtl':
        segment['direction'] = 'rtl'

    # What the subtitle's own fonts say beyond the reel's: its look.
    look = {}
    if own.get('size'):
        look['size'] = round(_size(own) * 100 / _POINTS_HIGH, 2)
    if own.get('effect') or own.get('effect_color'):
        effect = str(first_state.get('effect') or '').strip().lower()
        colour = _argb(first_state.get('effect_color')) or '#ff000000'
        width = round(base_size * 100 / _POINTS_HIGH * _EFFECT_WIDTH, 3)
        if effect == 'border':
            look['outline'] = {'color': colour, 'width': width}
        elif effect == 'shadow':
            look['shadow'] = [{'x': width, 'y': width, 'blur': 0, 'color': colour}]
    subtitld_look.store(segment, look)
    return segment


def _document(root, images=None):
    standard = kind(root)
    if standard is None:
        raise ValueError(f'Not DCP subtitles: the document is <{_local(root.tag)}>, '
                         'not <DCSubtitle> (Interop) or <SubtitleReel> (SMPTE)')
    clock = _Clock(root, standard)
    found = []

    def walk(node, state):
        for child in node:
            name = _local(child.tag)
            if name == 'Font':
                walk(child, _font(child, state, None)[0])
            elif name == 'SubtitleList':
                walk(child, state)
            elif name == 'Subtitle':
                found.append((child, state))

    walk(root, {})
    # Times from the reel's start, when the subtitles come after it.
    start_offset = 0.0
    if standard == SMPTE and _child_text(root, 'StartTime'):
        try:
            start_offset = clock.seconds(_child_text(root, 'StartTime'))
        except ValueError:
            start_offset = 0.0
        if any(clock.seconds(_attr(element, 'TimeIn')) < start_offset for element, _state in found):
            start_offset = 0.0
    segments = []
    for element, state in found:
        try:
            segment = _subtitle(element, state, clock, start_offset)
        except (TypeError, ValueError):
            continue
        if segment is not None:
            segments.append(segment)
        if images is not None:
            for picture in element.iter():
                if _local(picture.tag) != 'Image' or not (picture.text or '').strip():
                    continue
                start = clock.seconds(_attr(element, 'TimeIn')) - start_offset
                end = clock.seconds(_attr(element, 'TimeOut')) - start_offset
                images.append({
                    'start': round(start, 3), 'end': round(max(start, end), 3), 'href': picture.text.strip(),
                    'valign': str(_attr(picture, 'VAlign') or 'center').strip().lower(),
                    'vposition': _number(_attr(picture, 'VPosition'), 0.0),
                    'halign': str(_attr(picture, 'HAlign') or 'center').strip().lower(),
                    'hposition': _number(_attr(picture, 'HPosition'), 0.0)})
    segments.sort(key=lambda segment: (segment['start'], segment['end']))

    title = _child_text(root, 'MovieTitle' if standard == INTEROP else 'ContentTitleText')
    metadata = {'title': title} if title else {}
    annotation = _child_text(root, 'AnnotationText')
    if annotation and annotation != title:
        metadata['description'] = annotation
    settings = {'standard': standard, 'language': _child_text(root, 'Language'), 'metadata': metadata,
                'fps': clock.rate if standard == SMPTE else None,
                'size': None}
    resolution = re.fullmatch(r'\s*(\d+)\s*x\s*(\d+)\s*', _attr(root, 'IntrinsicPictureResolution') or '')
    if resolution:
        settings['size'] = (int(resolution.group(1)), int(resolution.group(2)))
    return segments, settings


def _root(data):
    """The root element of DCP subtitles given as bytes or text."""
    if isinstance(data, str):
        data = re.sub(r'^\s*<\?xml[^>]*\?>', '', data.lstrip('﻿')).encode('utf-8')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        line, column = error.position
        raise ValueError(f'Line {line}, column {column + 1}: {error}') from error


def parse(data):
    """(subtitles, settings) in DCP subtitles given as bytes or text.
    The settings are {'standard', 'language', 'metadata', 'fps', 'size'}:
    the generation, the language as <Language> says it, the title, the
    SMPTE TimeCodeRate (None for Interop) and the picture's size where the
    file gives it. Raises ValueError for anything that is not them."""
    return _document(_root(data))


def read(path):
    """(subtitles, settings) in the DCP subtitle file at `path`, as parse()
    gives them."""
    with open(path, 'rb') as dcp_file:
        return parse(dcp_file.read())


def read_images(path):
    """(pictures, subtitles, settings) of the DCP subtitle file at `path`:
    each <Image> as {'start', 'end', 'href', 'valign', 'vposition',
    'halign', 'hposition'}, its href as the file says it (a PNG's name
    beside the file, or SMPTE's urn:uuid); the subtitles in text beside
    them and the settings, as parse() gives them."""
    images = []
    with open(path, 'rb') as dcp_file:
        segments, settings = _document(_root(dcp_file.read()), images)
    return images, segments, settings


def has_images(path):
    """Whether the DCP subtitle file at `path` holds any picture subtitle."""
    try:
        with open(path, 'rb') as dcp_file:
            if b'Image' not in dcp_file.read():
                return False
        return bool(read_images(path)[0])
    except (OSError, ValueError):
        return False


def image_files(href):
    """The file names an <Image>'s reference may be beside its XML: Interop
    names the PNG; SMPTE gives its urn:uuid, which a reel written outside
    an MXF has as <uuid>.png."""
    href = href.strip()
    found = _UUID.search(href)
    if href.lower().startswith('urn:uuid:') and found:
        return [found.group(0) + '.png', found.group(0).lower() + '.png', found.group(0)]
    return [href] + ([href + '.png'] if not href.lower().endswith('.png') else [])


def place_picture(image, width, height, screen):
    """(x, y) in pixels of a `width` x `height` picture an <Image> places
    on a picture `screen` (width, height) pixels, as projectors place it."""
    screen_width, screen_height = screen
    across = image['hposition'] / 100
    down = image['vposition'] / 100
    if image['halign'] == 'left':
        x = across * screen_width
    elif image['halign'] == 'right':
        x = (1 - across) * screen_width - width
    else:
        x = (0.5 + across) * screen_width - width / 2
    if image['valign'] == 'top':
        y = down * screen_height
    elif image['valign'] == 'bottom':
        y = (1 - down) * screen_height - height
    else:
        y = (0.5 + down) * screen_height - height / 2
    return int(round(x)), int(round(y))


# Writing.
def _time(seconds, rate, standard):
    """HH:MM:SS:TTT (Interop, ticks) or HH:MM:SS:EE (SMPTE, frames)."""
    units = max(0, int(round(float(seconds) * rate)))
    whole, part = divmod(units, rate)
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{second:02d}:' + (f'{part:03d}' if standard == INTEROP else f'{part:02d}')


def _dcp_colour(value, alpha='ff'):
    """AARRGGBB for a markup colour ('#rrggbb') or a look's ('#aarrggbb')."""
    digits = str(value or '').lstrip('#')
    if len(digits) == 6:
        digits = alpha + digits
    return digits.upper() if re.fullmatch(r'[0-9a-fA-F]{8}', digits) else ''


def _fixed(value):
    """A position as the file writes it: 10, 16.4."""
    value = round(float(value), 1)
    return f'{0.0 if value == 0 else value:g}'


def _font_attributes(style, size, standard):
    """The attributes of the <Font> a run in `style` sits in, in a subtitle
    drawn at `size` points; '' for an ordinary run."""
    attributes = []
    if style.italic:
        attributes.append('Italic="yes"')
    if style.bold:
        attributes.append('Weight="bold"')
    if style.underline:
        attributes.append(('Underlined' if standard == INTEROP else 'Underline') + '="yes"')
    if style.color:
        attributes.append(f'Color="{_dcp_colour(style.color)}"')
    if style.offset in ('super', 'sub'):
        attributes.append(f'Script="{style.offset}"')
    percent = markup.size_percent(style.size)
    if percent:
        attributes.append(f'Size="{max(1, int(round(size * float(percent[:-1]) / 100)))}"')
    return ' '.join(attributes)


def _line_xml(runs, size, standard):
    """The contents of a <Text> for one line's runs."""
    out = []
    for group in markup.ruby_groups(runs):
        if group[0] == 'ruby':
            base = ''.join(run for run, _style in group[1])
            reading = ''.join(run for run, _style in group[2])
            if base and reading:
                said = markup.ruby_attributes(group)
                where = ' Position="after"' if said.position == 'after' else ''
                out.append(f'<Ruby><Rb>{escape(base)}</Rb><Rt{where}>{escape(reading)}</Rt></Ruby>')
                continue
            group = ['plain', group[1] + group[2]]
        for run, style in group[1]:
            if not run:
                continue
            body = escape(run)
            if style.combine == markup.COMBINE:
                # An <HGroup> holds text alone: no <Font> may go around it.
                out.append(f'<HGroup>{body}</HGroup>')
                continue
            attributes = _font_attributes(style, size, standard)
            out.append(f'<Font {attributes}>{body}</Font>' if attributes else body)
    return ''.join(out)


def _lines(text):
    """[[(run, Style)]] for each line of a subtitle's text, without the
    lines that hold nothing."""
    lines = [[]]
    for run, style in markup.spans(text):
        for index, part in enumerate(run.split('\n')):
            if index:
                lines.append([])
            if part:
                lines[-1].append((part, style))
    out = []
    for line in lines:
        while line and not line[0][0].strip():
            line.pop(0)
        while line and not line[-1][0].strip():
            line.pop()
        if line:
            line[0] = (line[0][0].lstrip(), line[0][1])
            line[-1] = (line[-1][0].rstrip(), line[-1][1])
            out.append(line)
    return out


def _stack(count, anchor, margin, step):
    """Where each of `count` lines goes, from the edge the lines start at:
    `anchor` 'start' (that edge), 'end' (the other) or 'center', the margin
    from the edge they are aligned to, or from the middle."""
    if anchor == 'start':
        return [margin + number * step for number in range(count)]
    if anchor == 'end':
        return [margin + (count - 1 - number) * step for number in range(count)]
    return [margin + (number - (count - 1) / 2) * step for number in range(count)]


def _subtitle_xml(segment, number, rate, standard):
    """The <Subtitle> for a subtitle, or None when it has no text."""
    lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
    lines = _lines(rest)
    if not lines:
        return None
    keypad = markup.alignment(lead)
    across, down = position.margins(position.of(segment), keypad)
    styling = subtitld_look.of(segment)
    size = int(round(float(styling['size']) * _POINTS_HIGH / 100)) if styling.get('size') else DEFAULT_SIZE
    step = size * 100 / _POINTS_HIGH * _LINE_SPACING
    writing = position.writing(segment)
    row, column = (keypad - 1) // 3, (keypad - 1) % 3
    valign, halign = _ROW_NAMES[row], _COLUMN_NAMES[column]
    names = ('VAlign', 'VPosition', 'HAlign', 'HPosition') if standard == INTEROP else \
        ('Valign', 'Vposition', 'Halign', 'Hposition')

    if writing:
        # Columns: the first against the side they start from.
        first = 'right' if writing == 'vertical-rl' else 'left'
        anchor = 'start' if halign == first else 'center' if halign == 'center' else 'end'
        places = _stack(len(lines), anchor, across, step)
        if halign == 'center' and writing == 'vertical-rl':
            places = [2 * across - place for place in places]
        spots = [(down, place) for place in places]
        direction = ' Direction="vertical"' if standard == INTEROP else ' Direction="ttb"'
    else:
        anchor = 'start' if valign == 'top' else 'center' if valign == 'center' else 'end'
        spots = [(place, across) for place in _stack(len(lines), anchor, down, step)]
        direction = ' Direction="rtl"' if standard == SMPTE and segment.get('direction') == 'rtl' else ''

    texts = []
    for line, (vertical, horizontal) in zip(lines, spots):
        attributes = f'{names[0]}="{valign}" {names[1]}="{_fixed(vertical)}" {names[2]}="{halign}"'
        if round(horizontal, 1):
            attributes += f' {names[3]}="{_fixed(horizontal)}"'
        texts.append(f'<Text {attributes}{direction}>{_line_xml(line, size, standard)}</Text>')

    # The subtitle's own look: its size, its border or shadow.
    own = []
    if styling.get('size'):
        own.append(f'Size="{size}"')
    outline = styling.get('outline') if isinstance(styling.get('outline'), dict) else {}
    shadows = styling.get('shadow') if isinstance(styling.get('shadow'), list) else []
    if outline.get('width') and _dcp_colour(outline.get('color')):
        own += ['Effect="border"', f'EffectColor="{_dcp_colour(outline.get("color"))}"']
    elif shadows and isinstance(shadows[0], dict) and _dcp_colour(shadows[0].get('color')):
        own += ['Effect="shadow"', f'EffectColor="{_dcp_colour(shadows[0].get("color"))}"']
    start = float(segment.get('start', 0))
    end = max(start, float(segment.get('end', start)))
    fade = '0' if standard == INTEROP else '00:00:00:00'
    opening = (f'<Subtitle SpotNumber="{number}" TimeIn="{_time(start, rate, standard)}" '
               f'TimeOut="{_time(end, rate, standard)}" FadeUpTime="{fade}" FadeDownTime="{fade}">')
    if own:
        body = [f'      <Font {" ".join(own)}>', *(f'        {text}' for text in texts), '      </Font>']
    else:
        body = [f'      {text}' for text in texts]
    return '\n'.join([f'    {opening}', *body, '    </Subtitle>'])


def write(segments, standard=SMPTE, fps=DEFAULT_RATE, language='en', title='', reel=1, identifier=None,
          issued=None):
    """DCP subtitle XML for `segments`: Interop or SMPTE (STANDARDS), a
    SMPTE reel's times in frames at `fps` (one of RATES), in `language`,
    for the film called `title`. `identifier` (a UUID) and `issued` (a
    datetime) are new ones where not given."""
    standard = standard if standard in STANDARDS else SMPTE
    rate = _rate(standard, fps)
    subtitles = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        written = _subtitle_xml(segment, len(subtitles) + 1, rate, standard)
        if written:
            subtitles.append(written)
    return _reel(subtitles, standard, rate, language, title, reel, identifier, issued)


def _rate(standard, fps):
    """What a reel counts its times in: Interop's ticks, or a SMPTE reel's
    frames at `fps` (one of RATES, else the nearest)."""
    return TICKS if standard == INTEROP else (int(fps) if int(_number(fps, 0) or 0) in RATES else rate_for(fps))


def _reel(subtitles, standard, rate, language, title, reel, identifier, issued, baseline=True):
    """The whole file around its <Subtitle>s (written four spaces in): the
    reel's head, and where `baseline` asks, the one <Font> around them
    that says the baseline."""
    identifier = str(identifier or uuid.uuid4())
    tag = export_options.language_tag(language)
    font = (f'Color="FFFFFFFF" Effect="border" EffectColor="FF000000" Italic="no" Size="{DEFAULT_SIZE}" '
            f'Weight="normal" ' + ('Underlined' if standard == INTEROP else 'Underline') + '="no"')
    if standard == INTEROP:
        head = ['<?xml version="1.0" encoding="UTF-8"?>', '<DCSubtitle Version="1.0">',
                f'  <SubtitleID>{identifier}</SubtitleID>', f'  <MovieTitle>{escape(title or "")}</MovieTitle>',
                f'  <ReelNumber>{int(reel)}</ReelNumber>', f'  <Language>{escape(tag)}</Language>']
        if not baseline:
            # Straight under the root: one level of indentation less.
            subtitles = ['\n'.join(line[2:] for line in subtitle.split('\n')) for subtitle in subtitles]
            return '\n'.join(head + subtitles + ['</DCSubtitle>', ''])
        return '\n'.join(head + [f'  <Font {font}>'] + subtitles + ['  </Font>', '</DCSubtitle>', ''])
    issued = (issued or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0)
    head = ['<?xml version="1.0" encoding="UTF-8"?>', f'<SubtitleReel xmlns={quoteattr(SMPTE_NAMESPACE)}>',
            f'  <Id>urn:uuid:{identifier}</Id>', f'  <ContentTitleText>{escape(title or "")}</ContentTitleText>',
            f'  <IssueDate>{issued.isoformat()}</IssueDate>', f'  <ReelNumber>{int(reel)}</ReelNumber>',
            f'  <Language>{escape(tag)}</Language>', f'  <EditRate>{rate} 1</EditRate>',
            f'  <TimeCodeRate>{rate}</TimeCodeRate>', '  <StartTime>00:00:00:00</StartTime>',
            '  <SubtitleList>']
    if not baseline:
        return '\n'.join(head + subtitles + ['  </SubtitleList>', '</SubtitleReel>', ''])
    # One more level of indentation inside <SubtitleList>.
    subtitles = ['\n'.join('  ' + line for line in subtitle.split('\n')) for subtitle in subtitles]
    return '\n'.join(head + [f'    <Font {font}>'] + subtitles + ['    </Font>', '  </SubtitleList>', '</SubtitleReel>', ''])


def image_place(x, y, width, height, screen):
    """(valign, vposition, halign, hposition) of the <Image> that puts a
    `width` x `height` picture at x, y on a picture `screen` (width,
    height) pixels, as place_picture reads it back: across from the
    middle, and down from the top or up from the bottom, whichever is
    nearer the picture's middle; in % to the hundredth."""
    screen_width, screen_height = screen
    across = ((x + width / 2) / screen_width - 0.5) * 100
    if y + height / 2 < screen_height / 2:
        valign, down = 'top', y / screen_height * 100
    else:
        valign, down = 'bottom', (screen_height - y - height) / screen_height * 100

    def clamp(value):
        value = max(-100.0, min(100.0, round(value, 2)))
        return 0.0 if value == 0 else value
    return valign, clamp(down), 'center', clamp(across)


def write_images(pictures, size, standard=SMPTE, fps=DEFAULT_RATE, language='en', title='', reel=1,
                 identifier=None, issued=None):
    """DCP subtitle XML for subtitles that are pictures: [{'start', 'end',
    'href', 'x', 'y', 'width', 'height'}], each a PNG at x, y on a picture
    `size` (width, height) pixels, as big as it is. `href` is what its
    <Image> holds: the PNG's file name beside the file (Interop), or
    "urn:uuid:" and the UUID the PNG is known by (SMPTE). The rest as
    write() takes it. No <Font> is written: there is no text to style."""
    standard = standard if standard in STANDARDS else SMPTE
    rate = _rate(standard, fps)
    names = ('VAlign', 'VPosition', 'HAlign', 'HPosition') if standard == INTEROP else \
        ('Valign', 'Vposition', 'Halign', 'Hposition')
    fade = '0' if standard == INTEROP else '00:00:00:00'
    subtitles = []
    for picture in sorted(pictures, key=lambda p: (float(p['start']), float(p['end']))):
        valign, down, halign, across = image_place(int(picture['x']), int(picture['y']), int(picture['width']),
                                                   int(picture['height']), size)
        start = float(picture['start'])
        end = max(start, float(picture['end']))
        attributes = f'{names[0]}="{valign}" {names[1]}="{down:g}" {names[2]}="{halign}"'
        if across:
            attributes += f' {names[3]}="{across:g}"'
        subtitles.append('\n'.join([
            f'    <Subtitle SpotNumber="{len(subtitles) + 1}" TimeIn="{_time(start, rate, standard)}" '
            f'TimeOut="{_time(end, rate, standard)}" FadeUpTime="{fade}" FadeDownTime="{fade}">',
            f'      <Image {attributes}>{escape(str(picture["href"]))}</Image>',
            '    </Subtitle>']))
    return _reel(subtitles, standard, rate, language, title, reel, identifier, issued, baseline=False)


def save(path, segments, **options):
    """Write `segments` to `path` as DCP subtitles, in UTF-8; `options` as
    write() takes them."""
    with open(path, 'w', encoding='utf-8', newline='\n') as dcp_file:
        dcp_file.write(write(segments, **options))


def save_images(path, pictures, size, **options):
    """Write picture subtitles to `path` as DCP subtitles, in UTF-8;
    `pictures`, `size` and `options` as write_images() takes them."""
    with open(path, 'w', encoding='utf-8', newline='\n') as dcp_file:
        dcp_file.write(write_images(pictures, size, **options))
