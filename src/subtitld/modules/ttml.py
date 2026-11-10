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
- a subtitle a player would draw nothing of — tts:display="none" on it
  or on what holds it, tts:visibility="hidden", a region at tts:opacity 0
  — is read like any other and marked hidden, not left out;
- the document's xml:lang is the subtitles' language, and its <metadata>'s
  ttm:title, ttm:desc and ttm:copyright what it says about itself;
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

from subtitld.modules import export_options, look as subtitld_look, markup, position
from subtitld.modules.srt import decode

_XML_NS = '{http://www.w3.org/XML/1998/namespace}'
_CLOCK = re.compile(r'^(\d+):(\d{2}):(\d{2})(?:\.(\d+)|:(\d+)(?:\.(\d+))?)?$')
_OFFSET = re.compile(r'^(\d+(?:\.\d+)?)(h|ms|m|s|f|t)$')
_SECONDS = {'h': 3600, 'm': 60, 's': 1, 'ms': 0.001}
_STYLE_FIELDS = ('fontStyle', 'fontWeight', 'textDecoration', 'color', 'textAlign', 'displayAlign', 'origin', 'extent',
                 'position', 'direction', 'forcedDisplay', 'display', 'visibility', 'opacity', 'shear', 'ruby',
                 'fontFamily', 'fontSize', 'lineHeight', 'backgroundColor', 'padding', 'textOutline', 'textShadow',
                 'wrapOption', 'linePadding', 'multiRowAlign', 'fillLineGap', 'unicodeBidi')

# tts:ruby says what part of a ruby a span is. Only the two that hold text
# are a run of their own; the containers around them hold no text, and a
# delimiter is punctuation a player draws, not the subtitle's words.
_RUBY_ROLES = {'base': 'base', 'text': 'text'}
# How a run of another direction sits inside the line: left as the
# characters' own directions make it, embedded as its own run, or forced
# whatever the characters say. Subtitld's words for TTML's.
_BIDI = {'embed': 'embed', 'bidiOverride': 'override', 'isolate': 'isolate'}
_BIDI_OUT = {value: key for key, value in _BIDI.items()}
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
_EBUTTS_NS = 'urn:ebu:tt:style'
# IMSC's own styling namespace, which itts:forcedDisplay belongs to.
_ITTS_NS = 'http://www.w3.org/ns/ttml/profile/imsc1#styling'

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


def _active_area(value):
    """IMSC's ittp:activeArea as (x, y, width, height) in % of the picture,
    or None where the file states none.

    It is the part of the picture the author means to stay visible, the way
    broadcast's AFD does: where a player crops, this is what it must keep.
    Its offsets are shares of the room left over, as tts:position's are, so
    "50% 50% 80% 80%" is an area 80% of the picture, centred.
    """
    parts = [_PERCENT.match(part) for part in (value or '').split()]
    if len(parts) != 4 or not all(parts):
        return None
    left, top, width, height = (float(part.group(1)) for part in parts)
    if not all(0 <= amount <= 100 for amount in (left, top, width, height)):
        return None
    return left * (1 - width / 100), top * (1 - height / 100), width, height


def _span_of(element, base, limit, clock):
    """(start, stop) for an element: its times count from `base`, where its
    container began, and it is over by `limit`, where that container ends.
    It ends at its own end, after its dur, or with what holds it."""
    begin = clock.seconds(element.get('begin'))
    end = clock.seconds(element.get('end'))
    duration = clock.seconds(element.get('dur'))
    start = base + (begin or 0)
    stop = base + end if end is not None else start + duration if duration is not None else limit
    if limit is not None and stop is not None:
        stop = min(stop, limit)
    return start, stop


def _timed_regions(root, clock):
    """[(start, stop, region)] for every paragraph: what the checks need to
    ask how much is on screen at once."""
    found = []

    def walk(element, base, limit, region):
        region = element.get('region') or region
        start, stop = _span_of(element, base, limit, clock)
        if _local(element.tag) == 'p':
            found.append((start, stop, region))
            return stop
        sequential = element.get('timeContainer') == 'seq'
        for child in element:
            if _local(child.tag) in ('body', 'div', 'p'):
                child_stop = walk(child, start, stop, region)
                if sequential and child_stop is not None:
                    start = child_stop
        return stop

    walk(root, 0.0, None, None)
    return found


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
        # How wide the picture is for how tall: from its size in pixels,
        # else from what the file says it will be shown at, else widescreen.
        # ttp:displayAspectRatio, or ittp:aspectRatio, which is what IMSC
        # 1.0.1 files say and 1.1 deprecated in its favour. Same two
        # numbers either way.
        shape = ((_attr(root, 'displayAspectRatio') or _attr(root, 'aspectRatio') or '').split())
        self.aspect = 16 / 9
        if self.pixels and self.pixels[1]:
            self.aspect = self.pixels[0] / self.pixels[1]
        elif len(shape) == 2:
            try:
                self.aspect = float(shape[0]) / float(shape[1])
            except (ValueError, ZeroDivisionError):
                pass

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


# TTML names the generic families itself; CSS has its own names for them.
_GENERIC_FONTS = {'default': '', 'monospace': 'monospace', 'sansSerif': 'sans-serif', 'serif': 'serif',
                  'monospaceSansSerif': 'monospace', 'monospaceSerif': 'monospace',
                  'proportionalSansSerif': 'sans-serif', 'proportionalSerif': 'serif'}
_COLOUR_THEN = re.compile(r'^\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-zA-Z]+)\s+(?=[-\d.])')
_COLOUR_END = re.compile(r'\s+(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-zA-Z]+)\s*$')


def _argb(value):
    """A TTML colour as "#aarrggbb", the way a look and the styles write
    one, or '' when it is not a colour."""
    text = (value or '').strip()
    alpha = 'ff'
    rgba = _RGB.match(text)
    if rgba and text.lower().startswith('rgba('):
        last = text[:text.rfind(')')].rsplit(',', 1)[-1].strip()
        try:
            amount = float(last)
        except ValueError:
            amount = 255.0
        alpha = f'{max(0, min(255, round(amount if amount > 1 else amount * 255))):02x}'
    elif text.startswith('#') and len(text) == 9:
        alpha = text[7:9].lower()
    rgb = _color(text)
    return f'#{alpha}{rgb[1:]}' if rgb else ''


def _font_family(value):
    """A tts:fontFamily as CSS names families, TTML's generic names turned
    into CSS's and a name with spaces in it quoted."""
    families = []
    for part in (value or '').split(','):
        name = part.strip().strip('\'"').strip()
        if not name:
            continue
        if name in _GENERIC_FONTS:
            name = _GENERIC_FONTS[name]
        elif ' ' in name:
            name = f'"{name}"'
        if name and name not in families:
            families.append(name)
    return ', '.join(families)


def _alpha(value):
    """A tts:opacity as a number, or None when it is not one: TTML writes it
    as a real number, TTML2 also as a percentage."""
    value = (value or '').strip()
    try:
        return float(value[:-1]) / 100 if value.endswith('%') else float(value)
    except ValueError:
        return None


def _unshown(props, inherited=False):
    """Whether a player would draw nothing of this: tts:display="none" or
    a tts:visibility of "hidden".

    `inherited` carries display="none" from an element holding this one,
    which leaves everything inside it out of the layout however the inner
    elements style themselves; visibility is inherited the ordinary way,
    so an inner element saying "visible" does show.
    """
    return bool(inherited
                or str(props.get('display') or '').strip() == 'none'
                or str(props.get('visibility') or '').strip() == 'hidden')


def _of_size(frame, value, size, axis=1):
    """A length TTML measures against the font size where it is written as
    a percentage, and against the picture otherwise, in % of the picture's
    height either way."""
    percent = _PERCENT.match((value or '').strip())
    if percent:
        return float(percent.group(1)) / 100 * size if size is not None else None
    return frame.length(value, axis)


def _font_size(frame, stated):
    """The font size in % of the picture's height. TTML counts from the
    cell a region sits on — the initial size is 1c — and reads every
    percentage after that as a share of the size before it."""
    size = 100 / frame.cells[1]
    for value in stated:
        # "<horizontal> <vertical>?": the second is the height, and one
        # length alone is both. Two of them stretch the letters, which IMSC
        # and EBU-TT-D do not allow (check says so), so the height is kept.
        parts = (value or '').strip().split()
        resolved = _of_size(frame, parts[-1], size) if parts else None
        if resolved is not None:
            size = resolved
    return size


def _outline(frame, value, size):
    """tts:textOutline as a look's outline: a colour, a thickness and a
    blur, the last two measured against the font size. Without a colour
    the text's own is used, which the renderer already has."""
    text = (value or '').strip()
    if not text or text.lower() == 'none':
        return None
    colour = ''
    head = _COLOUR_THEN.match(text)
    if head:
        colour = _argb(head.group(1))
        text = text[head.end():]
    parts = text.split()
    width = _of_size(frame, parts[0], size) if parts else None
    if not width:
        return None
    outline = {'width': width}
    if colour:
        outline['color'] = colour
    blur = _of_size(frame, parts[1], size) if len(parts) > 1 else None
    if blur:
        outline['blur'] = blur
    return outline


def _shadows(frame, value, size):
    """tts:textShadow as a look's list of shadows, each measured against
    the font size: across, down, a blur and a colour."""
    text = (value or '').strip()
    if not text or text.lower() == 'none':
        return None
    shadows = []
    for one in re.split(r',(?![^()]*\))', text):
        colour = ''
        tail = _COLOUR_END.search(one)
        if tail:
            colour = _argb(tail.group(1))
            one = one[:tail.start()]
        lengths = [_of_size(frame, part, size) for part in one.split()[:3]]
        if len(lengths) < 2 or lengths[0] is None or lengths[1] is None:
            continue
        shadow = {'x': lengths[0], 'y': lengths[1]}
        if len(lengths) > 2 and lengths[2]:
            shadow['blur'] = lengths[2]
        if colour:
            shadow['color'] = colour
        shadows.append(shadow)
    return shadows or None


def _padding(frame, value, size):
    """tts:padding as a look's [top, right, bottom, left]. TTML writes the
    sides before, end, after, start, which is the order CSS writes padding
    in, with the same shorthand for fewer than four."""
    parts = (value or '').strip().split()
    if not parts or len(parts) > 4:
        return None
    sides = [_of_size(frame, part, size) for part in parts]
    if any(side is None for side in sides):
        return None
    if len(parts) == 1:
        return sides * 4
    if len(parts) == 2:
        return [sides[0], sides[1], sides[0], sides[1]]
    if len(parts) == 3:
        return [sides[0], sides[1], sides[2], sides[1]]
    return sides


def _across(frame, value, size):
    """A length across the picture, in % of its height: the stage is 1080
    tall and as wide as the aspect ratio makes it, so a look keeps one
    measure for both directions."""
    percent = _of_size(frame, value, size, axis=0)
    return None if percent is None else percent * frame.aspect


def _of_file(frame, props, sizes, opacity, size_own=True):
    """The look the file gives a subtitle: what it says about how the
    subtitle should be drawn, as modules.look holds it. Only what the file
    states — a look says nothing of its own.

    `sizes` is the whole chain of font sizes down to this subtitle, which
    every percentage here is measured against; `size_own` says whether any
    of it was stated below <body>, and so whether the size is the
    subtitle's own or the document's.
    """
    size = _font_size(frame, sizes)
    found = {}
    if props.get('fontFamily'):
        found['font'] = _font_family(props['fontFamily'])
    if sizes and size_own:
        found['size'] = size
    height = (props.get('lineHeight') or '').strip()
    if height:
        found['line_height'] = 'normal' if height == 'normal' else _of_size(frame, height, size)
    if props.get('backgroundColor'):
        found['background'] = _argb(props['backgroundColor'])
    found['padding'] = _padding(frame, props.get('padding'), size)
    found['outline'] = _outline(frame, props.get('textOutline'), size)
    found['shadow'] = _shadows(frame, props.get('textShadow'), size)
    wrap = (props.get('wrapOption') or '').strip()
    if wrap in ('wrap', 'noWrap'):
        found['wrap'] = 'wrap' if wrap == 'wrap' else 'nowrap'
    # A region a file only part shows. Nothing of it showing is the
    # subtitle being hidden, which is read as that instead.
    alpha = _alpha(opacity)
    if alpha is not None and 0 < alpha < 1:
        found['opacity'] = alpha
    # EBU-TT-D's two: space at both ends of every line, so a box does not
    # touch the letters, and where the shorter lines of a block sit.
    if props.get('linePadding'):
        found['line_padding'] = _across(frame, props['linePadding'], size)
    if (props.get('multiRowAlign') or '').strip() in ('start', 'center', 'end'):
        found['row_align'] = props['multiRowAlign'].strip()
    # IMSC's own: whether the box behind each line reaches the next one.
    gap = (props.get('fillLineGap') or '').strip().lower()
    if gap in ('true', 'false'):
        found['fill_line_gap'] = gap == 'true'
    return found


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
    # "start" and "end" are the near and the far edge of the writing, which
    # right-to-left swaps; "left" and "right" are themselves either way.
    near, far = (2, 0) if props.get('direction') == 'rtl' else (0, 2)
    horizontal = 0 if align == 'left' else 2 if align == 'right' else \
        near if align == 'start' else far if align == 'end' else 1
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
    # What the whole document looks like, from <body>: that is the look the
    # chosen style is for, and a subtitle's own look is what it says beyond
    # it. Without this a file's baseline would quietly take over the look
    # the user picked — Subtitld's own exported files among them, since
    # they state one style for the whole document.
    body = next((element for element in root.iter() if _local(element.tag) == 'body'), None)
    base_props = styles.of(body, {}) if body is not None else {}
    base_sizes = (base_props['fontSize'],) if base_props.get('fontSize') else ()

    def paragraph(element, start, stop, props, region, preserve, outer_agent, unshown=False, sizes=()):
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
        # Which way the writing runs, where the file says: Arabic and Hebrew
        # read right to left, and that decides where a line's punctuation
        # lands and which end "start" is.
        if p_props.get('direction') in ('ltr', 'rtl'):
            segment['direction'] = p_props['direction']
        # And how another direction sits inside the line. TTML does not
        # inherit this one, so it is read off the paragraph itself.
        bidi = _BIDI.get((styles.of(element, {}).get('unicodeBidi') or '').strip())
        if bidi:
            segment['bidi'] = bidi
        # Forced: shown even where a viewer asked for no subtitles, for the
        # line of another language a film translates on screen.
        if str(p_props.get('forcedDisplay') or '').strip().lower() == 'true':
            segment['forced'] = True
        # Only a region can be made transparent — tts:opacity applies to
        # nothing else — and a region that is not presented takes its
        # subtitles with it, whatever the body or the div says.
        region_unshown = (str(region_props.get('display') or '').strip() == 'none'
                          or _alpha(region_props.get('opacity')) == 0)
        # A subtitle a player would draw nothing of: a hidden alternate
        # track, a line an authoring tool left in, a region at opacity 0.
        # It is kept and marked, not dropped — it is part of the file, and
        # a round trip puts it back the way it came.
        if _unshown(p_props, unshown or region_unshown):
            segment['hidden'] = True
        # What the file says the subtitle should look like, which is drawn
        # over the chosen style. The region's size comes first in the chain
        # of font sizes, then the ones holding the paragraph, then its own.
        stated = tuple(value for value in
                       ((region_props.get('fontSize'),) + sizes + (styles.of(element, {}).get('fontSize'),)) if value)
        # What the subtitle says about itself is what it states beyond
        # the document: the attributes themselves, not the sizes they work
        # out to, or a paragraph that only sets its own size would inherit
        # the document's outline as an outline of its own.
        own_props = {key: value for key, value in p_props.items() if base_props.get(key) != value}
        # A box a file puts on a span hugs the words, which is how Subtitld
        # draws one; on the paragraph TTML paints a band the width of the
        # region. Both become the subtitle's one box, the span's first.
        for inner in element.iter():
            if inner is element or _local(inner.tag) != 'span':
                continue
            inner_props = styles.of(inner, {})
            for key in ('backgroundColor', 'padding'):
                if inner_props.get(key):
                    own_props.setdefault(key, inner_props[key])
        subtitld_look.store(segment, _of_file(frame, own_props, stated, region_props.get('opacity'),
                                              size_own=tuple(stated) != tuple(base_sizes)))
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

    def walk(element, base, limit, props, region, preserve, agent, unshown=False, sizes=()):
        """Read `element`, its times counted from `base`, over by `limit`;
        its end, when it has one."""
        name = _local(element.tag)
        region = element.get('region') or region
        start, stop = _span_of(element, base, limit, clock)
        if name == 'p':
            paragraph(element, start, stop, props, region, preserve, agent, unshown, sizes)
            return stop
        inner = styles.of(element, props) if name != 'tt' else props
        unshown = unshown or str(inner.get('display') or '').strip() == 'none'
        own_size = styles.of(element, {}).get('fontSize') if name != 'tt' else None
        if own_size:
            sizes = sizes + (own_size,)
        agent = (_attr(element, 'agent') or '').split()[0] if _attr(element, 'agent') else agent
        sequential = element.get('timeContainer') == 'seq'
        for child in element:
            if _local(child.tag) in ('body', 'div', 'p'):
                child_stop = walk(child, start, stop, inner, region, _preserving(element, preserve), agent,
                                  unshown, sizes)
                if sequential and child_stop is not None:
                    start = child_stop
        return stop

    walk(root, 0.0, None, {}, None, False, None)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, (root.get(_XML_NS + 'lang') or '').strip(), _metadata(root)


def _metadata(root):
    """What the file says about itself, from <head>'s <metadata>: its
    ttm:title, ttm:desc and ttm:copyright, as modules.session keeps them
    on the subtitle. A file that says none of it gives {}."""
    into = {'title': 'title', 'desc': 'description', 'copyright': 'copyright'}
    found = {}
    # Inside a <metadata> block, so a ttm:desc describing one subtitle is
    # not read as what the whole document is.
    for block in root.iter():
        if _local(block.tag) != 'metadata':
            continue
        for element in block:
            key = into.get(_local(element.tag))
            if key and (element.text or '').strip():
                found.setdefault(key, (element.text or '').strip())
    return found


def parse(text):
    """The subtitles in TTML text: [{'start', 'end', 'text'[, 'speaker']}], by start."""
    return _document(text)[0]


def read_text(text):
    """(subtitles, language, metadata) in TTML text, as `read` gives them
    for a file."""
    return _document(text)


def read(path):
    """(subtitles, language, metadata) in the TTML file at `path`: the
    language as the file's xml:lang gives it, or '', and what the file says
    about itself (its title, description and copyright)."""
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

    boxes = []
    for element in root.iter():
        if _local(element.tag) != 'region':
            continue
        box = [_LENGTH.match(part) for part in
               ((_attr(element, 'origin') or '') + ' ' + (_attr(element, 'extent') or '')).split()]
        if len(box) == 4 and all(part and part.group(2) == '%' for part in box):
            boxes.append((_xml_id(element) or '(unnamed)',
                          [float(box[axis].group(1)) for axis in (0, 1)],
                          [float(box[2 + axis].group(1)) for axis in (0, 1)]))
    outside = [name for name, start, size in boxes
               if any(start[axis] < 0 or start[axis] + size[axis] > 100 for axis in (0, 1))]
    if outside:
        problems.append(f'Regions reach outside the picture: {", ".join(outside)}')

    # The area the file says must stay visible should hold the subtitles:
    # a region outside it is one a player may crop away.
    active = _active_area(_attr(root, 'activeArea'))
    if active:
        at = (active[0], active[1])
        span = (active[2], active[3])
        strayed = [name for name, start, size in boxes
                   if any(start[axis] < at[axis] - 0.5
                          or start[axis] + size[axis] > at[axis] + span[axis] + 0.5 for axis in (0, 1))]
        if strayed:
            problems.append(f'Regions fall outside the active area the file states '
                            f'({_attr(root, "activeArea")}): {", ".join(strayed)}')

    # IMSC asks a file that measures anything in pixels to say how big its
    # picture is, without which the length cannot be resolved at all — this
    # module drops such a length, so the file loses what it said.
    if profile != 'ttml1':
        pixels = any(length and length.group(2) == 'px'
                     for element in root.iter() for value in element.attrib.values()
                     for length in (_LENGTH.match(part) for part in str(value).split()))
        if pixels and not (_attr(root, 'extent') or '').strip():
            problems.append('Lengths in px, but <tt> has no tts:extent: a file that measures in pixels '
                            'has to say how big its picture is')

    # No more than four regions on screen at once, and a file states its
    # shape once: either the 1.1 way or the 1.0.1 way, never both.
    if profile in ('imsc1', 'imsc1.1'):
        shown = _timed_regions(root, _Clock(root))
        crowded = 0
        for moment in sorted({start for start, _stop, _region in shown if start is not None}):
            crowded = max(crowded, len({region for start, stop, region in shown
                                        if region and start is not None and stop is not None
                                        and start <= moment < stop}))
        if crowded > 4:
            problems.append(f'{crowded} regions are on screen at once: IMSC allows at most four')
        if _attr(root, 'aspectRatio') and _attr(root, 'displayAspectRatio'):
            problems.append('ittp:aspectRatio and ttp:displayAspectRatio are both present: '
                            'a file says its shape one way or the other, not both')

    # Two font sizes, one across and one down, stretch the letters. Both
    # IMSC versions allow one size for both directions, and EBU-TT-D "only
    # one percentage value".
    if profile != 'ttml1':
        stretched = sorted({value for element in root.iter() for value in [_attr(element, 'fontSize') or '']
                            if len(value.split()) > 1})
        if stretched:
            problems.append(f'Font sizes with a width and a height of their own ({", ".join(stretched)}): '
                            'the profile allows one size for both, and the height is what is read')

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


def _styles(look, preview, profile='ttml1'):
    """(<style> elements, the id of the box's or None) for `look`. The box
    goes on a span around each subtitle's text: a span's background is
    behind its text, a paragraph's across its region.

    EBU-TT-D does not use tts:textOutline, so its files say the rest of the
    style and leave the outline out.
    """
    if look != 'preview':
        outline = '' if profile == 'ebu-tt-d' else ' tts:textOutline="black 5%"'
        return [f'<style xml:id="text" tts:fontFamily="proportionalSansSerif" tts:fontSize="100%" '
                f'tts:color="white"{outline}/>'], None
    preview = preview or {}
    styles = [f'<style xml:id="text" tts:fontFamily={quoteattr(str(preview.get("font_family") or "proportionalSansSerif"))} '
              f'tts:fontSize="100%" tts:color="{_rgba(preview.get("color"), "#ffffffff")}"/>']
    if not preview.get('backgroundbox_enabled', True):
        return styles, None
    styles.append(f'<style xml:id="box" tts:backgroundColor="{_rgba(preview.get("backgroundbox_color"), "#55000000")}"/>')
    return styles, 'box'


# The grid _root_attributes states, which is also TTML's own default: a
# look is written in cells of it, so the file does not depend on the size of
# anybody's picture.
_WRITE_CELLS = (32.0, 15.0)
_WRITE_ASPECT = 16 / 9
# CSS's generic families back to the names TTML has for them.
_TTML_FAMILIES = {'sans-serif': 'proportionalSansSerif', 'serif': 'proportionalSerif', 'monospace': 'monospace'}


def _cells(value):
    """A look's length (% of the picture's height) in cells down."""
    return f'{value * _WRITE_CELLS[1] / 100:.4g}c'


def _cells_across(value):
    """The same length counted in cells across, which is what EBU-TT-D's
    line padding is written in."""
    return f'{value / _WRITE_ASPECT * _WRITE_CELLS[0] / 100:.4g}c'


def _ttml_family(value):
    """A look's CSS font families as tts:fontFamily writes them."""
    families = []
    for part in str(value or '').split(','):
        name = part.strip().strip('\'"').strip()
        if name:
            families.append(_TTML_FAMILIES.get(name, name))
    return ', '.join(families)


def _look_attributes(styling, profile='ttml1', inline=False):
    """A look as the TTML attributes that state it, for one <style> of its
    own. Lengths go out in cells, the measure they were read in, so a look
    that came from a file goes back as it came.

    Two styles, because TTML paints a background where it is stated: the
    box and its padding go on a span around the text, where they hug the
    words as the preview draws them (`inline`), and everything else on the
    paragraph, where it is inherited by the text inside it.

    EBU-TT-D's spec names tts:textOutline and tts:opacity among the
    attributes it does not use, so a file written for it says neither.
    """
    ebu = profile == 'ebu-tt-d'
    out = []
    if inline:
        if styling.get('background'):
            out.append(f'tts:backgroundColor="{_rgba(styling["background"], "#ff000000")}"')
        if styling.get('padding'):
            out.append('tts:padding="' + ' '.join(_cells(side) for side in styling['padding']) + '"')
        return ' '.join(out)
    if styling.get('font'):
        out.append(f'tts:fontFamily={quoteattr(_ttml_family(styling["font"]))}')
    if styling.get('size') is not None:
        out.append(f'tts:fontSize="{_cells(styling["size"])}"')
    height = styling.get('line_height')
    if height is not None:
        out.append(f'tts:lineHeight="{"normal" if height == "normal" else _cells(height)}"')
    outline = styling.get('outline') or {}
    if outline.get('width') and not ebu:
        parts = [_rgba(outline['color'], '#ff000000')] if outline.get('color') else []
        parts.append(_cells(outline['width']))
        if outline.get('blur'):
            parts.append(_cells(outline['blur']))
        out.append(f'tts:textOutline="{" ".join(parts)}"')
    shadows = styling.get('shadow') or []
    if shadows and not ebu:
        drawn = []
        for one in shadows:
            parts = [_cells(one.get('x') or 0), _cells(one.get('y') or 0)]
            if one.get('blur'):
                parts.append(_cells(one['blur']))
            if one.get('color'):
                parts.append(_rgba(one['color'], '#ff000000'))
            drawn.append(' '.join(parts))
        out.append(f'tts:textShadow="{", ".join(drawn)}"')
    if styling.get('wrap'):
        out.append(f'tts:wrapOption="{"wrap" if styling["wrap"] == "wrap" else "noWrap"}"')
    if styling.get('line_padding'):
        out.append(f'ebutts:linePadding="{_cells_across(styling["line_padding"])}"')
    if styling.get('row_align'):
        out.append(f'ebutts:multiRowAlign="{styling["row_align"]}"')
    # Whether the box behind each line reaches the next. A look that says
    # nothing but brings a box asks for it, because that is how the box was
    # drawn: behind the whole subtitle, lines and the gaps between them.
    gap = styling.get('fill_line_gap')
    if gap is None and styling.get('background'):
        gap = True
    if gap is not None and not ebu:
        out.append(f'itts:fillLineGap="{"true" if gap else "false"}"')
    return ' '.join(out)


def _japanese(language):
    return str(language or '').replace('_', '-').split('-')[0].lower() == 'ja'


def _root_attributes(profile, language, imsc_styling=False, ebu_styling=False):
    """What <tt> carries: the namespaces and parameters, and the profile
    the file declares itself to be. `imsc_styling` brings IMSC's styling
    namespace in, for a forced subtitle to mark or a box that reaches from
    one line to the next, and `ebu_styling` the EBU's, for a look that
    states one of its two."""
    namespaces = ['xmlns="http://www.w3.org/ns/ttml"',
                  'xmlns:tts="http://www.w3.org/ns/ttml#styling"',
                  'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"',
                  'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"']
    if imsc_styling:
        namespaces.append(f'xmlns:itts="{_ITTS_NS}"')
    if profile == 'ebu-tt-d':
        namespaces.append(f'xmlns:ebuttm="{_EBUTTM_NS}"')
    if ebu_styling:
        namespaces.append(f'xmlns:ebutts="{_EBUTTS_NS}"')
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


def write(segments, language='en', agents=False, look='clean', preview=None, profile='ttml1', metadata=None):
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
    # One <style> for each look the subtitles use, the way one region is
    # made for each place they sit in: the file keeps the structure even
    # though the project holds a look per subtitle.
    lookings = {}
    # And one for each box, which goes on a span rather than the paragraph.
    boxings = {}
    style_lines, box = _styles(look, preview, profile)
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
        if not markup.plain(rest).strip():
            continue
        keypad = markup.alignment(lead)
        placed = position.of(segment)
        styling = subtitld_look.of(segment)
        # Opacity belongs to a region, so a subtitle only part shown takes
        # a region of its own rather than fading the ones beside it.
        spot = (keypad, position.as_percent(placed.h), position.as_percent(placed.v),
                None if profile == 'ebu-tt-d' else styling.get('opacity'))
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
        if segment.get('direction') in ('ltr', 'rtl'):
            attrs += f' tts:direction="{segment["direction"]}"'
        # EBU-TT-D's schema has normal, embed and bidiOverride, and no
        # isolate, so a file written for it says nothing of an isolate.
        if segment.get('bidi') in _BIDI_OUT and not (profile == 'ebu-tt-d' and segment['bidi'] == 'isolate'):
            attrs += f' tts:unicodeBidi="{_BIDI_OUT[segment["bidi"]]}"'
        if segment.get('forced') and profile != 'ebu-tt-d':
            attrs += ' itts:forcedDisplay="true"'
        # EBU-TT-D's spec lists tts:display among the attributes it does not
        # use, so there a hidden subtitle is written as an ordinary one.
        if segment.get('hidden') and profile != 'ebu-tt-d':
            attrs += ' tts:display="none"'
        looking = _look_attributes(styling, profile)
        if looking:
            attrs += f' style="{lookings.setdefault(looking, f"look{len(lookings) + 1}")}"'
        content = _content(rest, shear)
        # A box the file brought stands in for the style's own, and goes on
        # a span: on the paragraph TTML paints a band the width of the
        # region instead of a box around the words.
        boxing = _look_attributes(styling, profile, inline=True)
        around = boxings.setdefault(boxing, f'lookbox{len(boxings) + 1}') if boxing else box
        if around:
            content = f'<span style="{around}">{content}</span>'
        cues.append(f'      <p {attrs}>{content}</p>')

    head = ['  <head>']
    meta_lines = []
    if profile == 'ebu-tt-d':
        meta_lines.append(f'      <ebuttm:documentMetadata>'
                          f'<ebuttm:conformsToStandard>{_EBUTTD_STANDARD}</ebuttm:conformsToStandard>'
                          f'</ebuttm:documentMetadata>')
    # What the file says about itself, in TTML's own words for it.
    for key, name in (('title', 'ttm:title'), ('description', 'ttm:desc'), ('copyright', 'ttm:copyright')):
        said = str((metadata or {}).get(key) or '').strip()
        if said:
            meta_lines.append(f'      <{name}>{escape(said)}</{name}>')
    for name, agent_id in speakers.items():
        meta_lines.append(f'      <ttm:agent xml:id="{agent_id}" type="character">'
                          f'<ttm:name type="full">{escape(name)}</ttm:name></ttm:agent>')
    if meta_lines:
        head += ['    <metadata>', *meta_lines, '    </metadata>']
    style_lines = style_lines + [f'<style xml:id="{name}" {looking}/>'
                                 for looking, name in list(lookings.items()) + list(boxings.items())]
    head += ['    <styling>', *('      ' + line for line in style_lines), '    </styling>', '    <layout>']
    for spot in sorted(places, key=lambda spot: (-((spot[0] - 1) // 3), spot[0], spot[1] or 0, spot[2] or 0,
                                                 spot[3] if spot[3] is not None else 1)):
        keypad, across, down, alpha = spot
        display = ('after', 'center', 'before')[(keypad - 1) // 3]
        align = ('left', 'center', 'right')[(keypad - 1) % 3]
        (x, y), (width, height) = _box(keypad, across, down)
        faded = f' tts:opacity="{alpha:.4g}"' if alpha is not None else ''
        head.append(f'      <region xml:id="{places[spot]}" '
                    f'tts:origin="{_percent(x)} {_percent(y)}" tts:extent="{_percent(width)} {_percent(height)}" '
                    f'tts:displayAlign="{display}" tts:textAlign="{align}"{faded}/>')
    head += ['    </layout>', '  </head>']
    forced = any(segment.get('forced') for segment in segments) and profile != 'ebu-tt-d'
    imsc_styling = forced or any('itts:' in looking for looking in lookings)
    ebu_styling = any('ebutts:' in looking for looking in lookings)
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<tt {_root_attributes(profile, language, imsc_styling=imsc_styling, ebu_styling=ebu_styling)}>',
        *head,
        '  <body style="text">',
        '    <div>',
        *cues,
        '    </div>',
        '  </body>',
        '</tt>',
        ''])


def save(path, segments, language='en', agents=False, look='clean', preview=None, profile='ttml1',
         metadata=None):
    """Write `segments` to `path` as TTML, in UTF-8."""
    with open(path, 'w', encoding='utf-8', newline='\n') as ttml_file:
        ttml_file.write(write(segments, language, agents, look, preview, profile, metadata))
