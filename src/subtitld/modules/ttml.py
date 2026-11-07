"""TTML (.ttml, .dfxp, Apple's .itt): read the profiles in use, write one
they all take.

TTML is the W3C's XML for timed text. DFXP was its first profile's name,
and the files that carry it (Netflix's, broadcasters' EBU-TT-D, Apple's
iTunes Timed Text, IMSC for streaming) are TTML with their own habits.

Reading:

- the bytes are decoded by the encoding the XML declares, else as SRT's
  are; HTML's entities (&nbsp;) and a bare & read as what they mean;
- elements and attributes are matched by their local names, so the TTML
  namespace, the old DFXP one (http://www.w3.org/2006/10/ttaf1) and any
  prefix (tt:p) read alike;
- times as TTML writes them: clock times with a fraction or with frames
  (00:00:01:12), offsets in h, m, s, ms, f (frames) and t (ticks), by the
  document's ttp:frameRate, ttp:frameRateMultiplier, ttp:subFrameRate and
  ttp:tickRate; a paragraph's times count from its <div>'s and <body>'s
  begin (from the end of the one before it, in a timeContainer="seq");
  it ends at its end, after its dur, or with what holds it;
- styles as TTML applies them: the region's, then inherited down body,
  div, p and span, each element's being the styles its style="..." names
  (and the ones they name) and then its own tts:* attributes. Italic (or
  oblique), bold, underline, line-through and a colour other than white
  become formatting tags; so does a tts:shear, which is how IMSC 1.1
  writes italics for Japanese (Netflix's profile asks for it there, and a
  file written that way has no fontStyle to find);
- where the subtitle sits, from its region: the point tts:displayAlign
  and tts:textAlign put the text at in the region's tts:origin and
  tts:extent (in %, px or cells), or, without those, the two alone
  (with textAlign's default, start, as the middle: see _keypad). A region
  may say where it is as tts:position instead, which IMSC 1.1 permits and
  a document uses in place of tts:origin, never beside it;
- <br/> breaks a line; other whitespace collapses to a space, unless
  xml:space="preserve";
- the ttm:agent of a paragraph (or of a span in it, or of its div) is its
  speaker, named by the agent's ttm:name;
- the document's xml:lang is the subtitles' language;
- the subtitles come sorted by start.

Writing gives TTML 1 in the TTML namespace, ttp:timeBase="media", times as
"HH:MM:SS.mmm", a region for each place the subtitles sit, formatting as
spans with tts:fontStyle, tts:fontWeight, tts:textDecoration and
tts:color, line breaks as <br/>, and, when asked, each speaker as a
ttm:agent. Its one style is white with a black outline, or the look of
Subtitld's preview: its font, colour and box.

`profile` says which of TTML's profiles the file declares itself to be, so
a service that checks what it is given accepts it (PROFILES):

    'ttml1'     TTML 1 with nothing declared, as Subtitld has always
                written it
    'imsc1'     IMSC 1.0.1's Text Profile, declared in ttp:profile
    'imsc1.1'   IMSC 1.1's Text Profile, declared in ttp:contentProfiles,
                which is where 1.1 moved it
    'ebu-tt-d'  EBU-TT-D, declared in ebuttm:conformsToStandard, the way
                the EBU's own spec asks for and which forbids
                ttp:contentProfiles

A profile also sets what the file may hold, and `check` reads a file back
to say where it breaks the rules this module knows (it is a useful set of
checks, not a conformance certificate).
"""

import html.entities
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import export_options, markup, position
from subtitld.modules.srt import decode

_XML_NS = '{http://www.w3.org/XML/1998/namespace}'
_CLOCK = re.compile(r'^(\d+):(\d{2}):(\d{2})(?:\.(\d+)|:(\d+)(?:\.(\d+))?)?$')
_OFFSET = re.compile(r'^(\d+(?:\.\d+)?)(h|ms|m|s|f|t)$')
_SECONDS = {'h': 3600, 'm': 60, 's': 1, 'ms': 0.001}
_STYLE_FIELDS = ('fontStyle', 'fontWeight', 'textDecoration', 'color', 'textAlign', 'displayAlign', 'origin', 'extent',
                 'position', 'shear', 'ruby')

# tts:ruby says what part of a ruby a span is. Only the two that hold text
# are a run of their own; the containers around them hold no text, and a
# delimiter is punctuation a player draws, not the subtitle's words.
_RUBY_ROLES = {'base': 'base', 'text': 'text'}
_RGB = re.compile(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)')
_DECLARED = re.compile(rb'\s*<\?xml[^>]*?encoding\s*=\s*["\']([\w.:-]+)["\']')
_ENTITY = re.compile(r'&(?:(#\d+|#x[0-9a-fA-F]+)|(\w+));|&')
_LENGTH = re.compile(r'^(-?\d+(?:\.\d+)?)(%|px|c)$')

LOOKS = ('clean', 'preview')

PROFILES = ('ttml1', 'imsc1', 'imsc1.1', 'ebu-tt-d')

# What each profile is called where it is declared. IMSC 1.0.1 names the
# profile it conforms to in ttp:profile; 1.1 moved that to
# ttp:contentProfiles (ttp:profile there says what a processor must do, not
# what the document is); EBU-TT-D has its own URN, in metadata, and
# forbids ttp:contentProfiles.
_IMSC1_TEXT = 'http://www.w3.org/ns/ttml/profile/imsc1/text'
_IMSC11_TEXT = 'http://www.w3.org/ns/ttml/profile/imsc1.1/text'
_EBUTTD_STANDARD = 'urn:ebu:tt:distribution:2018-04'
_EBUTTM_NS = 'urn:ebu:tt:metadata'

# Japanese subtitles write italics as a shear: IMSC 1.1 has tts:shear, and
# Netflix's Japanese profile asks for it in place of tts:fontStyle, which
# it prohibits. 16.67% is the slant their guide gives.
_SHEAR = '16.67%'
_PERCENT = re.compile(r'^(-?\d+(?:\.\d+)?)%$')


def _local(name):
    return name.rsplit('}', 1)[-1] if isinstance(name, str) else ''


def _attr(element, name):
    """An attribute by its local name, whatever its namespace."""
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _xml_id(element):
    return element.get(_XML_NS + 'id') or element.get('id')


def _entity(match):
    numeric, name = match.groups()
    if numeric or name in ('amp', 'lt', 'gt', 'quot', 'apos'):
        return match.group(0)
    if name in html.entities.name2codepoint:
        return f'&#{html.entities.name2codepoint[name]};'
    return '&amp;' + match.group(0)[1:]


def _text_of(data):
    """The document's text: in the encoding its declaration names, else
    decoded as SRT's are."""
    declared = _DECLARED.match(data)
    if declared and not data.startswith((b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff')):
        try:
            return data.decode(declared.group(1).decode('ascii'))
        except (LookupError, UnicodeDecodeError):
            pass
    return decode(data)


class _Clock:
    """Turns TTML times into seconds, by the document's parameters."""

    def __init__(self, root):
        def number(name, default):
            try:
                return float(_attr(root, name) or default) or default
            except ValueError:
                return default
        multiplier = (_attr(root, 'frameRateMultiplier') or '1 1').split()
        try:
            ratio = float(multiplier[0]) / float(multiplier[1]) if len(multiplier) == 2 else 1.0
        except (ValueError, ZeroDivisionError):
            ratio = 1.0
        self.fps = number('frameRate', 30) * (ratio or 1.0)
        self.subframes = number('subFrameRate', 1)
        if _attr(root, 'tickRate'):
            self.tick_rate = number('tickRate', 1)
        else:
            self.tick_rate = self.fps * self.subframes if _attr(root, 'frameRate') else 1

    def seconds(self, text):
        """Seconds for a time expression, or None for none."""
        text = (text or '').strip()
        clock = _CLOCK.match(text)
        if clock:
            hours, minutes, seconds, fraction, frames, subframes = clock.groups()
            value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            if fraction:
                value += int(fraction) / 10 ** len(fraction)
            if frames:
                value += int(frames) / self.fps
            if subframes:
                value += int(subframes) / (self.fps * self.subframes)
            return value
        offset = _OFFSET.match(text)
        if offset:
            amount, unit = float(offset.group(1)), offset.group(2)
            if unit in _SECONDS:
                return amount * _SECONDS[unit]
            return amount / (self.fps if unit == 'f' else self.tick_rate)
        return None


class _Frame:
    """Turns a region's lengths into percentages of the picture."""

    def __init__(self, root):
        extent = [_LENGTH.match(part) for part in (_attr(root, 'extent') or '').split()]
        self.pixels = tuple(float(m.group(1)) for m in extent) \
            if len(extent) == 2 and all(m and m.group(2) == 'px' for m in extent) else None
        try:
            cells = tuple(float(part) for part in (_attr(root, 'cellResolution') or '').split())
        except ValueError:
            cells = ()
        self.cells = cells if len(cells) == 2 and all(cells) else (32.0, 15.0)

    def length(self, value, axis):
        """One length in % of the picture along `axis` (0 across, 1 down),
        or None when it is not a length this can measure."""
        length = _LENGTH.match((value or '').strip())
        if not length:
            return None
        amount, unit = float(length.group(1)), length.group(2)
        if unit == '%':
            return amount
        if unit == 'c':
            return amount * 100 / self.cells[axis]
        if self.pixels and self.pixels[axis]:
            return amount * 100 / self.pixels[axis]
        return None

    def percent(self, value):
        """(x, y) in % of the picture for a pair of lengths, or None."""
        parts = (value or '').split()
        if len(parts) != 2:
            return None
        result = [self.length(part, axis) for axis, part in enumerate(parts)]
        return tuple(result) if all(part is not None for part in result) else None


def _color(value):
    """'#rrggbb' for a TTML colour (a name, #rrggbb, #rrggbbaa, rgb(), rgba()),
    or '' when it is none of them."""
    value = (value or '').strip()
    rgb = _RGB.match(value)
    if rgb:
        return '#' + ''.join(f'{min(255, int(part)):02x}' for part in rgb.groups())
    if value.startswith('#') and len(value) == 9:
        value = value[:7]
    return markup.color_hex(value)


def _properties(element):
    """The element's own tts:* properties this module reads, after those of
    the <style> elements inside it (a region's)."""
    props = {}
    for child in element:
        if _local(child.tag) == 'style' and not _xml_id(child):
            props.update(_properties(child))
    props.update({field: _attr(element, field) for field in _STYLE_FIELDS if _attr(element, field) is not None})
    return props


class _Styles:
    """The document's named styles, each resolved through the ones it names."""

    def __init__(self, root):
        self.raw = {_xml_id(element): element for element in root.iter()
                    if _local(element.tag) == 'style' and _xml_id(element)}
        self.resolved = {}

    def named(self, ids, seen=()):
        props = {}
        for style_id in (ids or '').split():
            if style_id in seen or style_id not in self.raw:
                continue
            if style_id not in self.resolved:
                element = self.raw[style_id]
                resolved = self.named(element.get('style'), seen + (style_id,))
                resolved.update(_properties(element))
                self.resolved[style_id] = resolved
            props.update(self.resolved[style_id])
        return props

    def of(self, element, inherited):
        """The properties `element` has: inherited, then the styles it names,
        then its own."""
        props = dict(inherited)
        props.update(self.named(element.get('style')))
        props.update(_properties(element))
        return props


def _sheared(value):
    """Whether a tts:shear slants the text: a percentage other than zero.
    A span that sets it back to 0% stands the text up again, as one with
    tts:fontStyle="normal" does."""
    percent = _PERCENT.match((value or '').strip())
    return bool(percent) and float(percent.group(1)) != 0


def _look(props):
    decoration = (props.get('textDecoration') or '').split()
    color = _color(props.get('color'))
    return markup.Style(
        (props.get('fontStyle') or '') in ('italic', 'oblique') or _sheared(props.get('shear')),
        (props.get('fontWeight') or '') == 'bold',
        'underline' in decoration,
        'lineThrough' in decoration,
        '' if color == '#ffffff' else color,
        _RUBY_ROLES.get((props.get('ruby') or '').strip(), ''))


def _keypad(props, frame, geometry=True):
    """Where a subtitle sits (7 8 9 top ... 1 2 3 bottom), from its region.

    Without `geometry` the region's own displayAlign and textAlign say it
    and where the region sits does not: a file that says both means them,
    and how far in the region stands is the subtitle's margins (_margins),
    not another place. Reading a lifted subtitle as "the middle, lifted
    less" would put it in the same spot on the picture but change what the
    subtitle says about itself.
    With no displayAlign the text goes to the bottom of the region, and
    with no textAlign to its middle, as players show them. In a region
    with no place of its own, "start", textAlign's default, is taken as
    saying nothing too: pycaption writes it on every region, and so did
    Subtitld when it wrote TTML with pycaption."""
    display = props.get('displayAlign') or 'after'
    align = props.get('textAlign') or 'center'
    origin, extent = frame.percent(props.get('origin')), frame.percent(props.get('extent'))
    if align == 'start' and not (origin and extent):
        align = 'center'
    horizontal = 0 if align in ('left', 'start') else 2 if align in ('right', 'end') else 1
    if origin and extent and geometry:
        # The middle of the line nearest the edge the text is put against:
        # half a cell in from it.
        half_line = min(50 / frame.cells[1], extent[1] / 2)
        y = origin[1] + {'before': half_line, 'center': extent[1] / 2}.get(display, extent[1] - half_line)
        x = origin[0] + extent[0] * horizontal / 2
        row = 2 if y < 34 else 1 if y < 67 else 0
        column = 0 if x < 34 else 1 if x < 67 else 2
    else:
        row = {'before': 2, 'center': 1}.get(display, 0)
        column = horizontal
    return (1, 4, 7)[row] + column


# Where a subtitle sits when it says nothing: the title-safe area Subtitld
# has always written, a tenth of the picture in from each edge, and no
# offset from the middle for a subtitle aligned to it.
_SAFE = 10.0
_NUDGE = 0.25                                   # % a margin must differ by to be one


def _default_margin(place):
    """The margin an alignment has when a subtitle gives none: from an edge
    it is the safe area, from the middle it is no offset at all."""
    return 0.0 if place == 1 else _SAFE


def _box(keypad, horizontal, vertical):
    """(origin, extent) in % for the nine places and a subtitle's margins
    (None where it gives none).

    The region's aligned edge stands the margin in from the picture's, and
    its other edge stays at the safe area; a middle alignment is moved off
    the middle by it, right and down. With no margins this is the box
    Subtitld has always written: 10% in on every side.
    """
    column, row = (keypad - 1) % 3, (keypad - 1) // 3   # 0 left/bottom, 1 centre/middle, 2 right/top
    across = _default_margin(column) if horizontal is None else horizontal
    down = _default_margin(row) if vertical is None else vertical
    if column == 0:                                     # left
        x, width = across, 100.0 - _SAFE - across
    elif column == 2:                                   # right
        x, width = _SAFE, 100.0 - across - _SAFE
    else:
        x, width = _SAFE + across, 100.0 - 2 * _SAFE
    if row == 0:                                        # bottom
        y, height = _SAFE, 100.0 - down - _SAFE
    elif row == 2:                                      # top
        y, height = down, 100.0 - _SAFE - down
    else:
        y, height = _SAFE + down, 100.0 - 2 * _SAFE
    x, width = _inside(x, width)
    y, height = _inside(y, height)
    return (x, y), (width, height)


def _inside(start, size):
    """(start, size) kept inside the picture.

    A margin can be negative: CEA-608 keeps a position but no alignment, so
    a line indented from the left comes back centred and nudged the other
    way. Off the middle that moves the band, which is what it means; on an
    edge it would put the region outside the root container, which no
    profile allows and no player draws, so it sits against the edge.
    """
    size = min(max(1.0, size), 100.0)
    return max(0.0, min(start, 100.0 - size)), size


# A tts:position names an edge and how far from it a region sits.
_EDGES = {'left': (0, 'start'), 'right': (0, 'end'), 'top': (1, 'start'), 'bottom': (1, 'end')}


def _position(value):
    """{axis: (edge, offset)} for a tts:position, or None when it is not one.

    TTML2 writes it as CSS writes a background's place: an edge keyword, a
    length or percentage, or both, for each axis, in any order. An edge
    takes a distance of its own only in the three- and four-part forms,
    since "left 10%" is the two-part one, the left edge and a tenth down.
    What one part leaves unsaid is centred.
    """
    tokens = [token.lower() for token in (value or '').split()]
    if not 1 <= len(tokens) <= 4:
        return None
    parts, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in _EDGES:
            axis, edge = _EDGES[token]
            offset = '0%'
            if len(tokens) > 2 and index < len(tokens) and _LENGTH.match(tokens[index]):
                offset, index = tokens[index], index + 1
            parts.append((axis, edge, offset))
        elif token == 'center':
            parts.append((None, 'start', '50%'))
        elif _LENGTH.match(token):
            parts.append((None, 'start', token))
        else:
            return None
    found = {axis: (edge, offset) for axis, edge, offset in parts if axis is not None}
    for axis, edge, offset in parts:
        if axis is not None:
            continue
        free = next((free for free in (0, 1) if free not in found), None)
        if free is None:
            return None
        found[free] = (edge, offset)
    for axis in (0, 1):
        found.setdefault(axis, ('start', '50%'))
    return found


def _position_origin(frame, value, extent):
    """(x, y) in % of the picture for a region placed by tts:position, given
    its extent in %, or None.

    A percentage is of the room left over, the picture less the region, so
    "center" is the middle whatever the region measures and "bottom 10%"
    leaves a tenth of that room below it. A length is itself.
    """
    found = _position(value)
    if not found or not extent:
        return None
    origin = []
    for axis, size in enumerate(extent):
        edge, offset = found[axis]
        room = 100.0 - size
        length = _LENGTH.match(offset)
        if length and length.group(2) == '%':
            away = room * float(length.group(1)) / 100.0
        else:
            away = frame.length(offset, axis)
            if away is None:
                return None
        origin.append(away if edge == 'start' else room - away)
    return tuple(origin)


def _margins(keypad, origin, extent):
    """(horizontal, vertical) in % for a region, None where it sits where a
    subtitle that says nothing would. The inverse of _box."""
    column, row = (keypad - 1) % 3, (keypad - 1) // 3
    (x, y), (width, height) = origin, extent
    across = x if column == 0 else 100.0 - (x + width) if column == 2 else x + width / 2 - 50.0
    down = 100.0 - (y + height) if row == 0 else y if row == 2 else y + height / 2 - 50.0
    return tuple(None if abs(margin - _default_margin(place)) < _NUDGE else round(margin, 3)
                 for margin, place in ((across, column), (down, row)))


def _percent(value):
    """A margin as USF writes it: '12.5%', or '' for none."""
    if value is None:
        return ''
    return f'{int(value)}%' if float(value).is_integer() else f'{round(float(value), 3)}%'


def _agents(root):
    """{agent id: name} for the ttm:agent elements: the full name, else the
    first one, else the id."""
    names = {}
    for element in root.iter():
        if _local(element.tag) == 'agent' and _xml_id(element):
            found = [(child.get('type'), (child.text or '').strip()) for child in element
                     if _local(child.tag) == 'name' and (child.text or '').strip()]
            full = [name for kind, name in found if kind == 'full']
            names[_xml_id(element)] = (full or [name for _kind, name in found] or [_xml_id(element)])[0]
    return names


def _preserving(element, preserve):
    return (element.get(_XML_NS + 'space') or ('preserve' if preserve else 'default')) == 'preserve'


def _runs(element, styles, props, preserve, out, agent):
    """Collect `element`'s text into `out` as [(text, Style)]; the first
    ttm:agent met goes in `agent`."""
    if (props.get('ruby') or '').strip() == 'delimiter':
        # Brackets a player draws around a reading. They are not words of
        # the subtitle, and taking them for words would cut the ruby in two.
        return
    preserve = _preserving(element, preserve)
    if agent[0] is None and _attr(element, 'agent'):
        agent[0] = _attr(element, 'agent').split()[0]

    def text(value):
        if value:
            # XML's whitespace, which a no-break space is not.
            out.append((value if preserve else re.sub(r'[ \t\r\n]+', ' ', value), _look(props)))

    text(element.text)
    for child in element:
        name = _local(child.tag)
        if name == 'br':
            out.append(('\n', _look(props)))
        elif name == 'span':
            _runs(child, styles, styles.of(child, props), preserve, out, agent)
        elif name and name not in ('metadata', 'set', 'animate', 'style', 'region'):
            _runs(child, styles, props, preserve, out, agent)
        text(child.tail)


def _document(text):
    """(subtitles, language) in TTML text."""
    text = _ENTITY.sub(_entity, text.lstrip('﻿'))
    # The text is decoded already: the declaration's encoding no longer applies.
    text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text)
    root = ET.fromstring(text)
    if _local(root.tag) != 'tt':
        raise ValueError(f'Not a TTML document: it is <{_local(root.tag)}>, not <tt>')
    clock = _Clock(root)
    frame = _Frame(root)
    styles = _Styles(root)
    regions = {_xml_id(element): element for element in root.iter()
               if _local(element.tag) == 'region' and _xml_id(element)}
    agents = _agents(root)
    segments = []

    def paragraph(element, start, stop, props, region, preserve, outer_agent):
        region_props = styles.of(regions[region], {}) if region in regions else {}
        # The region's styles first: what the body, div or paragraph says
        # goes over them.
        p_props = styles.of(element, dict(region_props, **props))
        runs, agent = [], [None]
        _runs(element, styles, p_props, preserve, runs, agent)
        body = markup.tidy(runs)
        if stop is None or not markup.plain(body).strip():
            return
        # A file that states both alignments means them; one that leaves
        # them out is read by where its region sits, as before.
        stated = p_props.get('displayAlign') is not None and p_props.get('textAlign') is not None
        keypad = _keypad(p_props, frame, geometry=not stated)
        segment = {'start': round(start, 3), 'end': round(max(start, stop), 3),
                   'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body}
        speaker = agent[0] or outer_agent
        if speaker:
            segment['speaker'] = agents.get(speaker, speaker)
        # Where the region puts it, beyond the nine places: a file that
        # places its subtitles itself keeps that through to the project.
        region_origin = frame.percent(p_props.get('origin'))
        region_extent = frame.percent(p_props.get('extent'))
        if region_extent and p_props.get('position'):
            # A region placed the other way: TTML2 ignores tts:origin where
            # a tts:position is given, and IMSC asks a document for one of
            # the two, never both.
            region_origin = _position_origin(frame, p_props.get('position'), region_extent) or region_origin
        if region_origin and region_extent:
            across, down = _margins(keypad, region_origin, region_extent)
            if across is not None or down is not None:
                position.store(segment, position.Place(h=_percent(across), v=_percent(down)))
        segments.append(segment)

    def walk(element, base, limit, props, region, preserve, agent):
        """Read `element`, its times counted from `base`, over by `limit`;
        its end, when it has one."""
        name = _local(element.tag)
        region = element.get('region') or region
        begin = clock.seconds(element.get('begin'))
        end = clock.seconds(element.get('end'))
        duration = clock.seconds(element.get('dur'))
        start = base + (begin or 0)
        stop = base + end if end is not None else start + duration if duration is not None else limit
        if limit is not None and stop is not None:
            stop = min(stop, limit)
        if name == 'p':
            paragraph(element, start, stop, props, region, preserve, agent)
            return stop
        inner = styles.of(element, props) if name != 'tt' else props
        agent = (_attr(element, 'agent') or '').split()[0] if _attr(element, 'agent') else agent
        sequential = element.get('timeContainer') == 'seq'
        for child in element:
            if _local(child.tag) in ('body', 'div', 'p'):
                child_stop = walk(child, start, stop, inner, region, _preserving(element, preserve), agent)
                if sequential and child_stop is not None:
                    start = child_stop
        return stop

    walk(root, 0.0, None, {}, None, False, None)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, (root.get(_XML_NS + 'lang') or '').strip()


def parse(text):
    """The subtitles in TTML text: [{'start', 'end', 'text'[, 'speaker']}], by start."""
    return _document(text)[0]


def read(path):
    """(subtitles, language) in the TTML file at `path`; the language as
    the file's xml:lang gives it, or ''."""
    with open(path, 'rb') as ttml_file:
        return _document(_text_of(ttml_file.read()))


def check(text, profile='imsc1.1'):
    """What breaks `profile`'s rules in TTML text: ["what is wrong", ...],
    empty when this finds nothing.

    A useful set of checks, not a conformance certificate: it holds the
    rules that are written down plainly and that a file Subtitld wrote or
    read can actually break — the declaration itself, the language, a
    region a subtitle names but the file does not have, images in a text
    profile, and what EBU-TT-D asks for. A service's own validator still
    has the last word.
    """
    profile = profile if profile in PROFILES else 'ttml1'
    try:
        root = ET.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', _ENTITY.sub(_entity, (text or '').lstrip('﻿'))))
    except ET.ParseError as error:
        return [f'Not XML that can be read: {error}']
    if _local(root.tag) != 'tt':
        return [f'The document is <{_local(root.tag)}>, not <tt>']

    problems = []
    if not (root.get(_XML_NS + 'lang') or '').strip():
        problems.append('<tt> has no xml:lang: every profile asks a file to say what language it is in')

    declared_profile = _attr(root, 'profile') or ''
    content_profiles = (_attr(root, 'contentProfiles') or '').split()
    standards = [(element.text or '').strip() for element in root.iter()
                 if _local(element.tag) == 'conformsToStandard']
    if profile == 'imsc1' and declared_profile != _IMSC1_TEXT:
        problems.append(f'ttp:profile does not name IMSC 1.0.1\'s Text Profile ({_IMSC1_TEXT})')
    if profile == 'imsc1.1' and _IMSC11_TEXT not in content_profiles:
        problems.append(f'ttp:contentProfiles does not name IMSC 1.1\'s Text Profile ({_IMSC11_TEXT})')
    if profile == 'ebu-tt-d':
        if not any(standard.startswith('urn:ebu:tt:distribution') for standard in standards):
            problems.append('No ebuttm:conformsToStandard: EBU-TT-D is declared in metadata, not in a ttp: attribute')
        if content_profiles:
            problems.append('ttp:contentProfiles is prohibited in EBU-TT-D')
        if (_attr(root, 'timeBase') or 'media') != 'media':
            problems.append('EBU-TT-D asks for ttp:timeBase="media"')

    cells = (_attr(root, 'cellResolution') or '').split()
    if cells:
        try:
            if len(cells) != 2 or not all(int(cell) > 0 for cell in cells):
                raise ValueError
        except ValueError:
            problems.append(f'ttp:cellResolution is {" ".join(cells)!r}: it is two whole numbers above zero')

    if profile in ('imsc1', 'imsc1.1'):
        images = [element for element in root.iter() if _attr(element, 'backgroundImage')]
        if images:
            problems.append(f'{len(images)} element(s) carry smpte:backgroundImage: '
                            'pictures belong to the Image Profile, not the Text Profile')

    outside = []
    for element in root.iter():
        if _local(element.tag) != 'region':
            continue
        box = [_LENGTH.match(part) for part in
               ((_attr(element, 'origin') or '') + ' ' + (_attr(element, 'extent') or '')).split()]
        if len(box) == 4 and all(part and part.group(2) == '%' for part in box):
            start = [float(box[axis].group(1)) for axis in (0, 1)]
            size = [float(box[2 + axis].group(1)) for axis in (0, 1)]
            if any(start[axis] < 0 or start[axis] + size[axis] > 100 for axis in (0, 1)):
                outside.append(_xml_id(element) or '(unnamed)')
    if outside:
        problems.append(f'Regions reach outside the picture: {", ".join(outside)}')

    regions = {_xml_id(element) for element in root.iter()
               if _local(element.tag) == 'region' and _xml_id(element)}
    missing = sorted({element.get('region') for element in root.iter()
                      if element.get('region') and element.get('region') not in regions})
    if missing:
        problems.append(f'Subtitles name regions the file does not have: {", ".join(missing)}')
    return problems


def format_time(seconds):
    """TTML clock time for `seconds`: "HH:MM:SS.mmm"."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def _rgba(value, default):
    """TTML's '#rrggbb' or '#rrggbbaa' for Qt's '#aarrggbb' or '#rrggbb'."""
    value = str(value or default).lstrip('#').lower()
    if len(value) == 8:
        alpha, value = value[:2], value[2:]
        return '#' + value + ('' if alpha == 'ff' else alpha)
    return '#' + value


def _open_span(tags, style, shear=False):
    """A <span> for the formatting `tags` ({(name, value)}) open together;
    its textDecoration names all the run's, so none an outer span set is
    lost, whatever a player makes of a nested one. With `shear`, italics
    are written as a slant instead (see _SHEAR)."""
    names = {name: value for name, value in tags}
    attrs = []
    if 'font' in names:
        attrs.append(f'tts:color="{names["font"]}"')
    if 'i' in names:
        attrs.append(f'tts:shear="{_SHEAR}"' if shear else 'tts:fontStyle="italic"')
    if 'b' in names:
        attrs.append('tts:fontWeight="bold"')
    if 'u' in names or 's' in names:
        decoration = ' '.join(word for word, on in (('underline', style.underline), ('lineThrough', style.strike)) if on)
        attrs.append(f'tts:textDecoration="{decoration}"')
    return f'<span {" ".join(attrs)}>'


def _nested_content(runs, shear=False):
    """Runs of one stretch as TTML: spans for their formatting, nested as
    the tags are, <br/> between lines."""
    out = []
    stack = []                                  # [frozenset of tags], one per open span
    for run, style in runs:
        if not run:
            continue
        wanted = set(markup.tags_for(style))
        keep = 0
        while keep < len(stack) and stack[keep] <= wanted:
            keep += 1
        out.extend('</span>' for _span in stack[keep:])
        del stack[keep:]
        new = wanted.difference(*stack)
        if new:
            out.append(_open_span(new, style, shear))
            stack.append(frozenset(new))
        out.append('<br/>'.join(escape(part) for part in run.split('\n')))
    out.extend('</span>' for _span in stack)
    return ''.join(out)


def _content(text, shear=False):
    """A subtitle's text as TTML. A ruby is a container span holding the
    base and its text, which is how IMSC 1.1 writes one."""
    out = []
    for group in markup.ruby_groups(markup.spans(text)):
        if group[0] == 'plain':
            out.append(_nested_content(group[1], shear))
            continue
        base = _nested_content(group[1], shear)
        reading = _nested_content(group[2], shear)
        if not (base and reading):
            out.append(base + reading)
            continue
        out.append(f'<span tts:ruby="container"><span tts:ruby="base">{base}</span>'
                   f'<span tts:ruby="text">{reading}</span></span>')
    return ''.join(out)


def _styles(look, preview):
    """(<style> elements, the id of the box's or None) for `look`. The box
    goes on a span around each subtitle's text: a span's background is
    behind its text, a paragraph's across its region."""
    if look != 'preview':
        return ['<style xml:id="text" tts:fontFamily="proportionalSansSerif" tts:fontSize="100%" '
                'tts:color="white" tts:textOutline="black 5%"/>'], None
    preview = preview or {}
    styles = [f'<style xml:id="text" tts:fontFamily={quoteattr(str(preview.get("font_family") or "proportionalSansSerif"))} '
              f'tts:fontSize="100%" tts:color="{_rgba(preview.get("color"), "#ffffffff")}"/>']
    if not preview.get('backgroundbox_enabled', True):
        return styles, None
    styles.append(f'<style xml:id="box" tts:backgroundColor="{_rgba(preview.get("backgroundbox_color"), "#55000000")}"/>')
    return styles, 'box'


def _japanese(language):
    return str(language or '').replace('_', '-').split('-')[0].lower() == 'ja'


def _root_attributes(profile, language):
    """What <tt> carries: the namespaces and parameters, and the profile
    the file declares itself to be."""
    namespaces = ['xmlns="http://www.w3.org/ns/ttml"',
                  'xmlns:tts="http://www.w3.org/ns/ttml#styling"',
                  'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"',
                  'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"']
    if profile == 'ebu-tt-d':
        namespaces.append(f'xmlns:ebuttm="{_EBUTTM_NS}"')
    attrs = ['ttp:timeBase="media"']
    if profile == 'imsc1':
        attrs.append(f'ttp:profile="{_IMSC1_TEXT}"')
    elif profile == 'imsc1.1':
        attrs.append(f'ttp:contentProfiles="{_IMSC11_TEXT}"')
    if profile != 'ttml1':
        # What the percentages in this file are measured against. It is
        # TTML's default, but EBU-TT-D asks for it in writing and a file
        # that says so cannot be read against another grid by mistake.
        attrs.append('ttp:cellResolution="32 15"')
    attrs.append(f'xml:lang={quoteattr(export_options.language_tag(language))}')
    return ' '.join(namespaces + attrs)


def write(segments, language='en', agents=False, look='clean', preview=None, profile='ttml1'):
    """TTML text for `segments`: with `agents`, each speaker as a ttm:agent;
    in `look` ('clean' or 'preview', Subtitld's preview settings in
    `preview`, CONFIG['videoplayer']); declaring `profile` (PROFILES).

    A Japanese file in an IMSC profile writes its italics as tts:shear:
    that is what IMSC 1.1 is for in Japanese, and Netflix's Japanese
    profile prohibits tts:fontStyle outright."""
    profile = profile if profile in PROFILES else 'ttml1'
    shear = profile in ('imsc1', 'imsc1.1') and _japanese(language)
    cues = []
    places = {}
    speakers = {}
    style_lines, box = _styles(look, preview)
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
        if not markup.plain(rest).strip():
            continue
        keypad = markup.alignment(lead)
        placed = position.of(segment)
        spot = (keypad, position.as_percent(placed.h), position.as_percent(placed.v))
        if spot not in places:
            # One region for each place used. A subtitle placed more finely
            # than the nine takes a region of its own, named after the place
            # it starts from, numbered only where that name is taken.
            name = markup.POSITION_NAMES[keypad]
            taken = set(places.values())
            if name in taken:
                name = next(f'{name}-{number}' for number in range(2, len(taken) + 3)
                            if f'{name}-{number}' not in taken)
            places[spot] = name
        region = places[spot]
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        attrs = f'begin="{format_time(start)}" end="{format_time(end)}" region="{region}"'
        speaker = str(segment.get('speaker') or '').strip()
        if agents and speaker:
            attrs += f' ttm:agent="{speakers.setdefault(speaker, f"speaker{len(speakers) + 1}")}"'
        content = _content(rest, shear)
        if box:
            content = f'<span style="{box}">{content}</span>'
        cues.append(f'      <p {attrs}>{content}</p>')

    head = ['  <head>']
    metadata = []
    if profile == 'ebu-tt-d':
        metadata.append(f'      <ebuttm:documentMetadata>'
                        f'<ebuttm:conformsToStandard>{_EBUTTD_STANDARD}</ebuttm:conformsToStandard>'
                        f'</ebuttm:documentMetadata>')
    for name, agent_id in speakers.items():
        metadata.append(f'      <ttm:agent xml:id="{agent_id}" type="character">'
                        f'<ttm:name type="full">{escape(name)}</ttm:name></ttm:agent>')
    if metadata:
        head += ['    <metadata>', *metadata, '    </metadata>']
    head += ['    <styling>', *('      ' + line for line in style_lines), '    </styling>', '    <layout>']
    for spot in sorted(places, key=lambda spot: (-((spot[0] - 1) // 3), spot[0], spot[1] or 0, spot[2] or 0)):
        keypad, across, down = spot
        display = ('after', 'center', 'before')[(keypad - 1) // 3]
        align = ('left', 'center', 'right')[(keypad - 1) % 3]
        (x, y), (width, height) = _box(keypad, across, down)
        head.append(f'      <region xml:id="{places[spot]}" '
                    f'tts:origin="{_percent(x)} {_percent(y)}" tts:extent="{_percent(width)} {_percent(height)}" '
                    f'tts:displayAlign="{display}" tts:textAlign="{align}"/>')
    head += ['    </layout>', '  </head>']
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<tt {_root_attributes(profile, language)}>',
        *head,
        '  <body style="text">',
        '    <div>',
        *cues,
        '    </div>',
        '  </body>',
        '</tt>',
        ''])


def save(path, segments, language='en', agents=False, look='clean', preview=None, profile='ttml1'):
    """Write `segments` to `path` as TTML, in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as ttml_file:
        ttml_file.write(write(segments, language, agents, look, preview, profile))
