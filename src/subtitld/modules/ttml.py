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
  become formatting tags;
- where the subtitle sits, from its region: the point tts:displayAlign
  and tts:textAlign put the text at in the region's tts:origin and
  tts:extent (in %, px or cells), or, without those, the two alone
  (with textAlign's default, start, as the middle: see _keypad);
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
"""

import html.entities
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import markup
from subtitld.modules.srt import decode

_XML_NS = '{http://www.w3.org/XML/1998/namespace}'
_CLOCK = re.compile(r'^(\d+):(\d{2}):(\d{2})(?:\.(\d+)|:(\d+)(?:\.(\d+))?)?$')
_OFFSET = re.compile(r'^(\d+(?:\.\d+)?)(h|ms|m|s|f|t)$')
_SECONDS = {'h': 3600, 'm': 60, 's': 1, 'ms': 0.001}
_STYLE_FIELDS = ('fontStyle', 'fontWeight', 'textDecoration', 'color', 'textAlign', 'displayAlign', 'origin', 'extent')
_RGB = re.compile(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)')
_DECLARED = re.compile(rb'\s*<\?xml[^>]*?encoding\s*=\s*["\']([\w.:-]+)["\']')
_ENTITY = re.compile(r'&(?:(#\d+|#x[0-9a-fA-F]+)|(\w+));|&')
_LENGTH = re.compile(r'^(-?\d+(?:\.\d+)?)(%|px|c)$')

LOOKS = ('clean', 'preview')


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

    def percent(self, value):
        """(x, y) in % of the picture for a pair of lengths, or None."""
        parts = (value or '').split()
        if len(parts) != 2:
            return None
        result = []
        for axis, part in enumerate(parts):
            length = _LENGTH.match(part)
            if not length:
                return None
            amount, unit = float(length.group(1)), length.group(2)
            if unit == '%':
                result.append(amount)
            elif unit == 'c':
                result.append(amount * 100 / self.cells[axis])
            elif self.pixels and self.pixels[axis]:
                result.append(amount * 100 / self.pixels[axis])
            else:
                return None
        return tuple(result)


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


def _look(props):
    decoration = (props.get('textDecoration') or '').split()
    color = _color(props.get('color'))
    return markup.Style(
        (props.get('fontStyle') or '') in ('italic', 'oblique'),
        (props.get('fontWeight') or '') == 'bold',
        'underline' in decoration,
        'lineThrough' in decoration,
        '' if color == '#ffffff' else color)


def _keypad(props, frame):
    """Where a subtitle sits (7 8 9 top ... 1 2 3 bottom), from its region.
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
    if origin and extent:
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


def _tidy(runs):
    """Text with tags for [(text, Style)]: spaces single and none at the
    ends of a line, no empty line."""
    out = []
    for text, style in runs:
        for char in text:
            if char in ' \t\r':
                if out and out[-1][0] not in ' \n':
                    out.append((' ', style))
            elif char == '\n':
                while out and out[-1][0] == ' ':
                    out.pop()
                if out and out[-1][0] != '\n':
                    out.append(('\n', style))
            else:
                out.append((char, style))
    while out and out[-1][0] in ' \n':
        out.pop()
    grouped = []
    for char, style in out:
        if grouped and grouped[-1][1] == style:
            grouped[-1][0].append(char)
        else:
            grouped.append(([char], style))
    return markup.from_spans([(''.join(chars), style) for chars, style in grouped])


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
        body = _tidy(runs)
        if stop is None or not markup.plain(body).strip():
            return
        keypad = _keypad(p_props, frame)
        segment = {'start': round(start, 3), 'end': round(max(start, stop), 3),
                   'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body}
        speaker = agent[0] or outer_agent
        if speaker:
            segment['speaker'] = agents.get(speaker, speaker)
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


def _open_span(tags, style):
    """A <span> for the formatting `tags` ({(name, value)}) open together;
    its textDecoration names all the run's, so none an outer span set is
    lost, whatever a player makes of a nested one."""
    names = {name: value for name, value in tags}
    attrs = []
    if 'font' in names:
        attrs.append(f'tts:color="{names["font"]}"')
    if 'i' in names:
        attrs.append('tts:fontStyle="italic"')
    if 'b' in names:
        attrs.append('tts:fontWeight="bold"')
    if 'u' in names or 's' in names:
        decoration = ' '.join(word for word, on in (('underline', style.underline), ('lineThrough', style.strike)) if on)
        attrs.append(f'tts:textDecoration="{decoration}"')
    return f'<span {" ".join(attrs)}>'


def _content(text):
    """A subtitle's text as TTML: spans for its formatting, nested as its
    tags are, <br/> between lines."""
    out = []
    stack = []                                  # [frozenset of tags], one per open span
    for run, style in markup.spans(text):
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
            out.append(_open_span(new, style))
            stack.append(frozenset(new))
        out.append('<br/>'.join(escape(part) for part in run.split('\n')))
    out.extend('</span>' for _span in stack)
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


def _language_tag(language):
    """BCP 47's way of writing a language: 'pt-BR' for 'pt-br' or 'pt_BR'."""
    parts = str(language or 'en').replace('_', '-').split('-')
    return '-'.join([parts[0].lower()] + [part.upper() if len(part) == 2 else part for part in parts[1:]])


def write(segments, language='en', agents=False, look='clean', preview=None):
    """TTML text for `segments`: with `agents`, each speaker as a ttm:agent;
    in `look` ('clean' or 'preview', Subtitld's preview settings in
    `preview`, CONFIG['videoplayer'])."""
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
        region = places.setdefault(keypad, markup.POSITION_NAMES[keypad])
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        attrs = f'begin="{format_time(start)}" end="{format_time(end)}" region="{region}"'
        speaker = str(segment.get('speaker') or '').strip()
        if agents and speaker:
            attrs += f' ttm:agent="{speakers.setdefault(speaker, f"speaker{len(speakers) + 1}")}"'
        content = _content(rest)
        if box:
            content = f'<span style="{box}">{content}</span>'
        cues.append(f'      <p {attrs}>{content}</p>')

    head = ['  <head>']
    if speakers:
        head.append('    <metadata>')
        for name, agent_id in speakers.items():
            head.append(f'      <ttm:agent xml:id="{agent_id}" type="character">'
                        f'<ttm:name type="full">{escape(name)}</ttm:name></ttm:agent>')
        head.append('    </metadata>')
    head += ['    <styling>', *('      ' + line for line in style_lines), '    </styling>', '    <layout>']
    for keypad in sorted(places, key=lambda keypad: (-((keypad - 1) // 3), keypad)):
        display = ('after', 'center', 'before')[(keypad - 1) // 3]
        align = ('left', 'center', 'right')[(keypad - 1) % 3]
        head.append(f'      <region xml:id="{places[keypad]}" tts:origin="10% 10%" tts:extent="80% 80%" '
                    f'tts:displayAlign="{display}" tts:textAlign="{align}"/>')
    head += ['    </layout>', '  </head>']
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" '
        'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
        f'ttp:timeBase="media" xml:lang={quoteattr(_language_tag(language))}>',
        *head,
        '  <body style="text">',
        '    <div>',
        *cues,
        '    </div>',
        '  </body>',
        '</tt>',
        ''])


def save(path, segments, language='en', agents=False, look='clean', preview=None):
    """Write `segments` to `path` as TTML, in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as ttml_file:
        ttml_file.write(write(segments, language, agents, look, preview))
