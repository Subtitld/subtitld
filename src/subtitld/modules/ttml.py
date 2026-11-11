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
  file written that way has no fontStyle to find), and TTML2's emphasis
  marks (tts:textEmphasis), and where a ruby puts its reading and how it
  spreads it (tts:rubyPosition, tts:rubyAlign); the room a paragraph keeps
  for readings (tts:rubyReserve) goes in its look; a run set upright in
  one character's room down a column (tts:textCombine) is markup's too;
- a region written down the picture (tts:writingMode tbrl, tb or tblr)
  makes its subtitles written so, the subtitle's 'writing'; its
  displayAlign then says which side the columns go to and its textAlign
  how far down, so the keypad is the place on the picture either way. A
  region written right to left (rltb, rl) is the direction its subtitles
  run in, where they do not say one of their own;
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
  ttm:title, ttm:desc and ttm:copyright what it says about itself (in
  <head> itself too, where EBU-TT puts its copyright); the ebuttm
  elements EBU Tech 3360 maps an STL file's GSI to are kept by their STL
  names, as metadata['stl'], and an EBU-TT file timed in timecode counts
  from the documentStartOfProgramme it names, as an STL from its TCP;
- a subtitle that is a picture (IMSC's Image Profile, SMPTE-TT: a <div>
  with smpte:backgroundImage, a PNG beside the file or one the file holds
  in a smpte:image) is read as the text its ittm:altText gives, placed
  where its region is; `read_images` gives the pictures themselves, for
  the ones with no text to read (modules.picture_ocr reads them with an
  OCR add-on); SMPTE-TT's own example puts the picture's words in a <p>
  inside its <div>, and those are its words too;
- with ttp:timeBase="smpte", a clock time with frames is a timecode's
  label, counted at the nominal rate, the labels ttp:dropMode's dropNTSC
  or dropPAL skip left out (timecode_frames);
- what RP 2052-10 makes of CEA-608: a region with a tts:origin and no
  extent holds text starting there, and the <set> inside a region moves
  it from caption to caption; a document that holds no text but the
  CEA-608 or CEA-708 captions it was converted from, tunnelled in
  smpte:data (RP 2052-10, RP 2052-11), is read from those, decoded as an
  SCC or a video's captions are;
- a DAPT script is read as the dubbing session it describes (see the
  'dapt' profile below, and _dapt_document);
- the subtitles come sorted by start.

Writing gives TTML 1 in the TTML namespace, ttp:timeBase="media", times as
"HH:MM:SS.mmm", a region for each place the subtitles sit, formatting as
spans with tts:fontStyle, tts:fontWeight, tts:textDecoration and
tts:color, line breaks as <br/>, and, when asked, each speaker as a
ttm:agent. Its one style is white with a black outline, or the look of
Subtitld's preview: its font, colour and box. A subtitle written down the
picture gets a region of its own, with tts:writingMode; every profile here
has vertical writing, IMSC 1.0.1's and EBU-TT-D's included.

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
    'smpte-tt'  SMPTE-TT (ST 2052-1), declared in ttp:profile: TTML 1 with
                ttp:timeBase="media" and clock times, the form Premiere
                and the broadcast tools import; the video's frame rate in
                ttp:frameRate where it is known, and, when asked
                (`tunnel`), the subtitles as CEA-608 captions in CC1,
                tunnelled in smpte:data as RP 2052-10 stores them
    'ebu-tt'    EBU-TT Part 1 (EBU Tech 3350 v1.2), the production and
                archive profile, declared in ebuttm:conformsToStandard:
                styled by reference only (what a paragraph or a span says
                goes into a <style> it names, a region's textAlign too), in
                SMPTE timecode where a frame rate is given (ttp:timeBase,
                frameRate, frameRateMultiplier, markerMode, dropMode; from
                the programme start in ebuttm:documentStartOfProgramme),
                ttm:copyright in <head>, and the GSI fields EBU Tech 3360
                carries over from an STL file (metadata['stl'], by their STL
                names) as Part M's ebuttm elements
    'dapt'      DAPT, W3C's Dubbing and Audio description Profiles of TTML2
                (a Candidate Recommendation), declared in
                ttp:contentProfiles: a script, not subtitles to show. Each
                subtitle is a Script Event, a <div> with an xml:id; its
                speaker a character (a ttm:agent of type character, named
                by its alias, its talent a person agent); its text a <p>
                in the subtitle's language and each translation a <p>
                translated from it (xml:lang and daptm:langSrc); the dub
                heard for it an <audio> for each clip of a file. Written
                by _write_dapt, read by _dapt_document, which says what a
                subtitle has no room for and keeps it

The Image Profiles of IMSC 1.0.1 and 1.1 carry each subtitle as a picture
instead, a PNG beside the file drawn as Subtitld's preview draws it, with
its words as the picture's alt text: `write_images` writes one
(IMAGE_PROFILES), for a player or a service that takes pictures only.

A profile also sets what the file may hold, and `check` reads a file back
to say where it breaks the rules this module knows (it is a useful set of
checks, not a conformance certificate).
"""

import base64
import binascii
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
                 'wrapOption', 'linePadding', 'multiRowAlign', 'fillLineGap', 'unicodeBidi',
                 'textEmphasis', 'rubyPosition', 'rubyAlign', 'rubyReserve', 'writingMode', 'textCombine')

# What tts:writingMode says, as the subtitle's 'writing' (modules.position):
# down the picture, in columns from the right or from the left. Across, it
# is no 'writing' at all; across right to left it is a direction.
_WRITING = {'tbrl': 'vertical-rl', 'tb': 'vertical-rl', 'tblr': 'vertical-lr'}
_WRITING_OUT = {'vertical-rl': 'tbrl', 'vertical-lr': 'tblr'}
_RIGHT_TO_LEFT = ('rltb', 'rl')

# TTML's words for how a reading is spread, from markup's (RUBY_ALIGNS).
_RUBY_ALIGN_OUT = {'start': 'start', 'end': 'end', 'space-around': 'spaceAround',
                   'space-between': 'spaceBetween', 'with-base': 'withBase'}
# What TTML2 says that TTML 1 does not, which IMSC 1.0.1 and EBU-TT-D are
# built on: emphasis marks, where a ruby puts its reading and how, and a
# run set upright down a column.
_TTML2_ANNOTATIONS = ('textEmphasis', 'rubyAlign', 'rubyPosition', 'rubyReserve', 'textCombine')
# How much of those each profile takes: none in the two built on TTML 1,
# IMSC 1.1's minimal sets (#textEmphasis-minimal, #rubyAlign-minimal), or
# all of it in TTML itself.
_ANNOTATIONS = {'ttml1': 'full', 'imsc1.1': 'minimal', 'imsc1': 'none', 'ebu-tt-d': 'none', 'smpte-tt': 'none',
                'ebu-tt': 'flat', 'dapt': 'full'}
# The profiles built on TTML 1, which has none of TTML2's styling.
_TTML1_PROFILES = ('imsc1', 'ebu-tt-d', 'smpte-tt', 'ebu-tt')
# The EBU's two, which leave out what EBU-TT does not use: an outline, a
# region's opacity, tts:display.
_EBU_PROFILES = ('ebu-tt-d', 'ebu-tt')
# The words a tts:textEmphasis may hold that are neither a colour nor a mark
# of its own: what IMSC 1.1's #textEmphasis-minimal allows.
_EMPHASIS_WORDS = frozenset(('none', 'auto', 'filled', 'open', 'circle', 'dot', 'sesame',
                             'before', 'after', 'outside'))

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
# The same, with the opacity TTML writes as a fourth part (0-255, or 0-1).
_RGBA = re.compile(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)')
_DECLARED = re.compile(rb'\s*<\?xml[^>]*?encoding\s*=\s*["\']([\w.:-]+)["\']')
_ENTITY = re.compile(r'&(?:(#\d+|#x[0-9a-fA-F]+)|(\w+));|&')
_LENGTH = re.compile(r'^(-?\d+(?:\.\d+)?)(%|px|c)$')

LOOKS = ('clean', 'preview')

PROFILES = ('ttml1', 'imsc1', 'imsc1.1', 'ebu-tt-d', 'smpte-tt', 'ebu-tt', 'dapt')
# IMSC's profiles of subtitles that are pictures (`write_images`).
IMAGE_PROFILES = ('imsc1-image', 'imsc1.1-image')

# What each profile is called where it is declared. IMSC 1.0.1 names the
# profile it conforms to in ttp:profile; 1.1 moved that to
# ttp:contentProfiles (ttp:profile there says what a processor must do, not
# what the document is); EBU-TT-D has its own URN, in metadata, and
# forbids ttp:contentProfiles.
_IMSC1_TEXT = 'http://www.w3.org/ns/ttml/profile/imsc1/text'
_IMSC11_TEXT = 'http://www.w3.org/ns/ttml/profile/imsc1.1/text'
_IMSC1_IMAGE = 'http://www.w3.org/ns/ttml/profile/imsc1/image'
_IMSC11_IMAGE = 'http://www.w3.org/ns/ttml/profile/imsc1.1/image'
# Where a picture's file is named (SMPTE-TT) and its words kept (IMSC).
_SMPTE_NS = 'http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt'
# SMPTE-TT (ST 2052-1). The 2013 edition moved its vocabulary to a 2013
# namespace; IMSC, and the tools that read SMPTE-TT, name the 2010 one, which
# is what Subtitld writes. Both are read. The profile is declared in
# ttp:profile; RP 2052-10 and -11 add CEA-608's and CEA-708's metadata
# attributes, in namespaces of their own, and name the data they tunnel.
_SMPTE_NAMESPACES = (_SMPTE_NS, 'http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt')
_SMPTE_PROFILE = 'http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full'
_SMPTE_PROFILES = (_SMPTE_PROFILE, 'http://www.smpte-ra.org/schemas/2052-1/2013/profiles/smpte-tt-full')
_M608_NS = _SMPTE_NS + '#cea608'
_CEA608_DATA = _SMPTE_NS + '#cea608'
# CEA-608's pairs come at NTSC's rate, whatever the document's (RP 2052-10).
_CEA608_RATE = 30000 / 1001
_ITTM_NS = 'http://www.w3.org/ns/ttml/profile/imsc1#metadata'
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


def _dropped(minutes, drop):
    """How many frame labels a drop-frame count has skipped by the start of
    minute `minutes` (counted from 00:00): TTML's ttp:dropMode. dropNTSC
    skips 00 and 01 at each minute but every tenth; dropPAL 00 to 03 at
    each even minute but 00, 20 and 40."""
    if drop == 'dropNTSC':
        return 2 * (minutes - minutes // 10)
    if drop == 'dropPAL':
        return 4 * (minutes // 2 - minutes // 20)
    return 0


def timecode_frames(hours, minutes, seconds, frames, nominal, drop=''):
    """The frame a SMPTE timecode label names, counted from 00:00:00:00, at
    `nominal` frames a second, the labels `drop` ('dropNTSC', 'dropPAL' or
    '') skips left out."""
    return int(round(((hours * 60 + minutes) * 60 + seconds) * nominal + frames)) - \
        _dropped(hours * 60 + minutes, drop)


def timecode_label(count, nominal, drop=''):
    """'HH:MM:SS:FF' for the frame `count`, the inverse of timecode_frames."""
    nominal = int(round(nominal))
    count = max(0, int(count))
    if drop in ('dropNTSC', 'dropPAL'):
        skip = 2 if drop == 'dropNTSC' else 4

        def drops(minute):
            return minute % 10 != 0 if drop == 'dropNTSC' else minute % 2 == 0 and minute % 20 != 0
        # Walk to the minute the frame falls in: each minute is a full one,
        # less the labels it skips at its start.
        minute = 0
        while count >= nominal * 60 - (skip if drops(minute) else 0):
            count -= nominal * 60 - (skip if drops(minute) else 0)
            minute += 1
        if drops(minute):
            count += skip                       # this minute's first labels are not used
        hours, minutes = divmod(minute, 60)
        seconds, frames = divmod(count, nominal)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}'
    seconds, frames = divmod(count, nominal)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}'


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
        self.nominal = number('frameRate', 30)
        self.fps = self.nominal * (ratio or 1.0)
        self.subframes = number('subFrameRate', 1)
        if _attr(root, 'tickRate'):
            self.tick_rate = number('tickRate', 1)
        else:
            self.tick_rate = self.fps * self.subframes if _attr(root, 'frameRate') else 1
        # A time base of "smpte" makes a clock time a timecode's label: its
        # hours, minutes and seconds count frames at the nominal rate (30
        # for 29.97), with the labels a drop-frame count skips, and the
        # frame it names comes at the real rate.
        self.smpte = (_attr(root, 'timeBase') or '').strip() == 'smpte'
        self.drop = (_attr(root, 'dropMode') or '').strip() if self.smpte else ''

    def seconds(self, text):
        """Seconds for a time expression, or None for none."""
        text = (text or '').strip()
        clock = _CLOCK.match(text)
        if clock:
            hours, minutes, seconds, fraction, frames, subframes = clock.groups()
            if self.smpte and fraction is None:
                count = timecode_frames(int(hours), int(minutes), int(seconds), int(frames or 0),
                                        self.nominal, self.drop)
                return (count + (int(subframes) / self.subframes if subframes else 0)) / self.fps
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
    """[(start, stop, region)] for every paragraph and every picture: what
    the checks need to ask how much is on screen at once."""
    found = []

    def walk(element, base, limit, region):
        region = element.get('region') or region
        start, stop = _span_of(element, base, limit, clock)
        if _local(element.tag) == 'p' or (_local(element.tag) == 'div' and _attr(element, 'backgroundImage')):
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


def _most_regions_at_once(shown):
    """The most regions any one moment has on screen, for [(start, stop,
    region)]. Swept in time order, so a film's worth of subtitles costs a
    sort rather than every subtitle against every other."""
    events = []
    for start, stop, region in shown:
        if region and start is not None and stop is not None and stop > start:
            events.append((start, 1, region))
            events.append((stop, -1, region))
    # At one instant what ends goes before what begins: one subtitle
    # following another is not two on screen at once.
    events.sort(key=lambda event: (event[0], event[1]))
    showing, most = {}, 0
    for _moment, change, region in events:
        showing[region] = showing.get(region, 0) + change
        if not showing[region]:
            del showing[region]
        most = max(most, len(showing))
    return most


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


def _one_size(value):
    """A tts:fontSize as one measure: it may give two, across and down, and
    a run's size here is one proportion."""
    return (str(value or '').strip().split() or [''])[-1]


def _span_outline(value):
    """(the edge a tts:textOutline draws, its colour): TTML says a colour
    and a thickness, which is the outline CEA-708 calls uniform."""
    parts = str(value or '').strip().split()
    if not parts or parts[0] == 'none':
        return '', ''
    color = markup.color_hex(parts[0])
    return ('uniform', color) if color or len(parts) > 1 else ('', '')


def _background(value):
    """'#rrggbb', or '#rrggbbaa' where it is not opaque, for a TTML
    backgroundColor: the alpha a run's background keeps, unlike a colour's,
    since a caption's box is often only partly opaque."""
    value = (value or '').strip()
    rgb = _RGBA.match(value)
    if rgb:
        parts = [min(255, int(part)) for part in rgb.groups()[:3]]
        alpha = rgb.group(4)
        opacity = 255 if alpha is None else min(255, int(round(float(alpha) * (1 if '.' in alpha else 1 / 255) * 255)))
        return '#' + ''.join(f'{part:02x}' for part in parts) + ('' if opacity == 255 else f'{opacity:02x}')
    return markup.background_hex(value)
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
    # TTML2's: room kept on every line for a reading.
    found['ruby_reserve'] = _ruby_reserve(frame, props.get('rubyReserve'), size)
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
    role = _RUBY_ROLES.get((props.get('ruby') or '').strip(), '')
    return markup.Style(
        (props.get('fontStyle') or '') in ('italic', 'oblique') or _sheared(props.get('shear')),
        (props.get('fontWeight') or '') == 'bold',
        'underline' in decoration,
        'lineThrough' in decoration,
        '' if color == '#ffffff' else color,
        role,
        _background(props.get('backgroundColor')),
        markup.size_percent(_one_size(props.get('fontSize'))),
        '', *_span_outline(props.get('textOutline')),
        emphasis=markup.emphasis_value(props.get('textEmphasis')),
        # A reading's place and spread are the ruby's: they mean something
        # only on the base and the reading, which carry them for it.
        ruby_position=markup.annotation_position(props.get('rubyPosition')) if role else '',
        ruby_align=markup.ruby_align_value(props.get('rubyAlign')) if role else '',
        combine=markup.combine_value(props.get('textCombine')))


def _ruby_reserve(frame, value, size):
    """tts:rubyReserve as a look's reserve: where the room goes and, when
    the file gives one, how much, measured against the font size."""
    parts = (value or '').strip().split()
    if not parts or parts[0] not in subtitld_look.RESERVE_POSITIONS:
        return None
    reserve = {'position': parts[0]}
    length = _of_size(frame, parts[1], size) if len(parts) > 1 else None
    if length:
        reserve['length'] = length
    return reserve


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
    origin, extent = frame.percent(props.get('origin')), frame.percent(props.get('extent'))
    writing = _WRITING.get((props.get('writingMode') or '').strip())
    if writing:
        return _keypad_down(props, frame, writing, origin, extent, geometry)
    display = props.get('displayAlign') or 'after'
    align = props.get('textAlign') or 'center'
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


def _keypad_down(props, frame, writing, origin, extent, geometry):
    """_keypad for a region written down the picture. Its displayAlign says
    which side the columns go to — before is where they start, the right
    for columns from the right — and its textAlign how far down: start at
    the top, as left is, end at the bottom, as right is. With no
    displayAlign the columns start where the writing does, TTML's own
    default: the bottom a line across is put at is a habit of players, and
    a column has no such habit."""
    display = props.get('displayAlign') or 'before'
    align = props.get('textAlign') or 'center'
    if align == 'start' and not (origin and extent):
        align = 'center'
    first, last = (2, 0) if writing == 'vertical-rl' else (0, 2)
    column = {'before': first, 'center': 1}.get(display, last)
    row = 2 if align in ('start', 'left') else 0 if align in ('end', 'right') else 1
    if origin and extent and geometry:
        # The middle of the column nearest the side the text is put
        # against, half a cell in from it, a cell being as wide as a line
        # across is tall.
        half_column = min(50 / frame.cells[1] / frame.aspect, extent[0] / 2)
        x = origin[0] + (half_column, extent[0] / 2, extent[0] - half_column)[column]
        y = origin[1] + extent[1] * (2 - row) / 2
        column = 0 if x < 34 else 1 if x < 67 else 2
        row = 2 if y < 34 else 1 if y < 67 else 0
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


def _whole_look(element, styles, props):
    """What a whole paragraph is drawn in, as {field: value}: the box behind
    a subtitle and the edge around its letters are a style template's
    settings, not a run's own (modules.markup). It is what the paragraph
    itself sets, and for the box what a lone span wrapping all of it does,
    as this module writes the box; one on only some of the runs is theirs.
    """
    whole = _look(props)
    background = whole.background
    children = list(element)
    if len(children) == 1 and _local(children[0].tag) == 'span' and \
            not (element.text or '').strip() and not (children[0].tail or '').strip():
        background = _background(styles.of(children[0], props).get('backgroundColor')) or background
    return background, (whole.edge, whole.edge_color)


def _own_look(style, box, outline):
    """`style` with what the whole paragraph is drawn in taken out: the box
    behind it and the edge around its letters. The edge goes with the colour
    it is drawn in, so a run outlined in another colour keeps both, and an
    edge colour never outlives the edge it was for.
    """
    if box and style.background == box:
        style = style._replace(background='')
    if outline[0] and (style.edge, style.edge_color) == outline:
        style = style._replace(edge='', edge_color='')
    return style if style.edge else style._replace(edge_color='')


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
        elif name and name not in ('metadata', 'set', 'animate', 'style', 'region', 'audio', 'source', 'data', 'desc'):
            _runs(child, styles, props, preserve, out, agent)
        text(child.tail)


def _embedded_images(root):
    """{xml:id: PNG bytes} for the pictures a SMPTE-TT file holds itself,
    as smpte:image elements in Base64."""
    found = {}
    for element in root.iter():
        if _local(element.tag) != 'image' or not _xml_id(element):
            continue
        if (element.get('encoding') or 'Base64').strip().lower() != 'base64':
            continue
        try:
            found[_xml_id(element)] = base64.b64decode(''.join((element.text or '').split()), validate=True)
        except (binascii.Error, ValueError):
            continue
    return found


def _alt_text(element):
    """What a picture says in words: the ittm:altText in its <metadata>,
    its lines kept apart."""
    lines = []
    for block in element:
        if _local(block.tag) != 'metadata':
            continue
        for inner in block:
            if _local(inner.tag) == 'altText':
                lines += [line.strip() for line in ''.join(inner.itertext()).split('\n') if line.strip()]
    return '\n'.join(lines)


def _region_props(element, styles, clock, at):
    """A region's properties at `at` seconds: its own, then those of a
    <set> inside it that is active then, which is how RP 2052-10 moves the
    one region a CEA-608 conversion uses from caption to caption."""
    if element is None:
        return {}
    props = styles.of(element, {})
    base = clock.seconds(element.get('begin')) or 0.0
    for child in element:
        if _local(child.tag) != 'set':
            continue
        start, stop = _span_of(child, base, None, clock)
        if start <= at + 0.0005 and (stop is None or at < stop - 0.0005):
            props.update(_properties(child))
    return props


def _plain_paragraph(element, styles, props):
    """A paragraph's words, its lines kept apart and its formatting not."""
    runs = []
    _runs(element, styles, styles.of(element, props), False, runs, [None])
    return '\n'.join(line.strip() for line in markup.plain(markup.tidy(runs)).split('\n') if line.strip())


def _data_kind(datatype):
    """'608' or '708' for the captions a smpte:data tunnels, by the datatype
    RP 2052-10 and -11 give them (in either edition's namespace, or as the
    private x-cea608 some tools write), else ''."""
    datatype = (datatype or '').strip().lower()
    for kind in ('608', '708'):
        if datatype.endswith('#cea' + kind) or datatype == 'x-cea' + kind:
            return kind
    return ''


def _tunnelled(root, clock):
    """[(seconds, '608' or '708', bytes)] for the captions the document
    tunnels in smpte:data, each from when the element whose metadata holds
    it begins (the document's start, for the head's)."""
    found = []

    def walk(element, base, limit):
        start, stop = (_span_of(element, base, limit, clock)
                       if _local(element.tag) in ('body', 'div', 'p', 'span') else (base, limit))
        for child in element:
            name = _local(child.tag)
            if name == 'metadata':
                for data in child:
                    kind = _data_kind(data.get('datatype')) if _local(data.tag) == 'data' else ''
                    if not kind:
                        continue
                    try:
                        found.append((start, kind, base64.b64decode(''.join((data.text or '').split()), validate=True)))
                    except (binascii.Error, ValueError):
                        continue
            elif name in ('head', 'body', 'div', 'p', 'span'):
                walk(child, start, stop)

    walk(root, 0.0, None)
    return found


# The frame rate a cc_data() holding this many entries is sent at: CEA-708
# carries 9600 bits a second, 20 entries a frame at 29.97.
_CC_COUNT_RATES = {10: 60000 / 1001, 20: 30000 / 1001, 25: 24000 / 1001}


def _captions_of(root, clock, tunnels):
    """The subtitles in the CEA-608 or CEA-708 data a document tunnels,
    decoded as an SCC or a video's captions are (modules.closed_captions):
    CEA-708's first service with captions, else the CEA-608 channel the
    smpte:information names, else the first with captions.

    RP 2052-10 stores CEA-608 as its byte pairs, field 1's and field 2's
    by turns, m608:fieldStart saying which comes first, at NTSC's rate.
    RP 2052-11 stores CEA-708 as its cc_data() structures, one a frame."""
    from subtitld.modules import closed_captions
    info = next((element for element in root.iter() if _local(element.tag) == 'information'), None)
    field_start = 2 if info is not None and (_attr(info, 'fieldStart') or '').strip() == '2' else 1
    channel = (_attr(info, 'channel') or '').strip().lower() if info is not None else ''
    pairs, frames, rate = {}, {}, None
    for start, kind, raw in tunnels:
        if kind == '608':
            first = round(start * _CEA608_RATE)
            for index in range(len(raw) // 2):
                slot = index + field_start - 1
                pairs.setdefault(first + slot // 2, []).append((slot % 2, raw[2 * index], raw[2 * index + 1]))
            continue
        # One cc_data() a frame, from the frame the element begins on.
        offset, frame = 0, None
        while offset + 2 <= len(raw):
            count = raw[offset] & 0x1F
            if rate is None:
                rate = clock.fps if _attr(root, 'frameRate') else _CC_COUNT_RATES.get(count, 30000 / 1001)
            frame = round(start * rate) if frame is None else frame + 1
            entries = closed_captions.triplets(raw[offset + 2:offset + 2 + 3 * count])
            if entries:
                frames.setdefault(frame, []).extend(entries)
            offset += 2 + 3 * count
    dtvcc = sorted(frames.items())
    if dtvcc:
        segments, _track = closed_captions.decode(dtvcc, rate, 'auto')
        if segments:
            return segments
    batches = sorted(pairs.items())
    if channel in ('cc1', 'cc2', 'cc3', 'cc4'):
        segments, _track = closed_captions.decode(batches, _CEA608_RATE, channel)
        if segments:
            return segments
    return closed_captions.decode(batches, _CEA608_RATE, 'auto')[0]


# DAPT (W3C's Dubbing and Audio description Profiles of TTML2, a Candidate
# Recommendation): a script for dubbing or audio description, whose
# events are <div>s with an xml:id, each holding a <p> for each language its
# text is in, the characters ttm:agents in <head>, recordings <audio>.
_DAPT_CONTENT = 'http://www.w3.org/ns/ttml/profile/dapt1.0/content'
_DAPTM_NS = 'http://www.w3.org/ns/ttml/profile/dapt#metadata'
_TTA_NS = 'http://www.w3.org/ns/ttml#audio'
_TTM_NS = 'http://www.w3.org/ns/ttml#metadata'
DAPT_SCRIPT_TYPES = ('originalTranscript', 'translatedTranscript', 'preRecording', 'asRecorded')
# The registry of what a script and its events represent (DAPT, H.2.2). A
# value of one's own begins "x-", or extends one of these with an "x-" part.
DAPT_REPRESENTS = ('audio', 'audio.dialogue', 'audio.nonDialogueSounds', 'visual', 'visual.dialogue',
                   'visual.nonText', 'visual.text', 'visual.text.title', 'visual.text.credit', 'visual.text.location')
_DAPT_ON_SCREEN = ('ON', 'OFF', 'ON_OFF', 'OFF_ON')
# What a text's source language says when it names no language: content
# with none (zxx), or one not yet determined ('' or und). Such text is
# original, as is text whose source is its own language.
_NO_LANGUAGE = ('', 'zxx', 'und')
_BCP47 = re.compile(r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$')
_DESCRIPTOR = re.compile(r'^[^\s.]+(?:\.[^\s.]+)*$')
_AUDIO_TYPES = {'.wav': 'audio/wave', '.wave': 'audio/wave', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg',
                '.oga': 'audio/ogg', '.opus': 'audio/ogg', '.flac': 'audio/flac', '.m4a': 'audio/mp4',
                '.aac': 'audio/aac', '.aif': 'audio/aiff', '.aiff': 'audio/aiff'}


def is_dapt(root_or_text):
    """Whether a document (its root element, or its text) is a DAPT script:
    it says so in ttp:contentProfiles, or carries DAPT's script type."""
    root = root_or_text
    if isinstance(root_or_text, str):
        try:
            root = ET.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', _ENTITY.sub(_entity, root_or_text.lstrip('﻿'))))
        except ET.ParseError:
            return False
    return any('/ttml/profile/dapt' in value for value in (_attr(root, 'contentProfiles') or '').split()) \
        or root.get(f'{{{_DAPTM_NS}}}scriptType') is not None


def _dapt(element, name):
    return element.get(f'{{{_DAPTM_NS}}}{name}')


def _original(language, source):
    """Whether text in `language` from `source` (its daptm:langSrc) is
    original rather than a translation (DAPT 4.5)."""
    return source.strip().lower() in _NO_LANGUAGE or source.strip().lower() == language.strip().lower()


def _dapt_agents(root):
    """({character id: name}, {character id: the talent's name}): DAPT's
    characters are ttm:agents of type character named by an alias, the
    actor who voices one a person agent its ttm:actor names."""
    names, persons, actors = {}, {}, {}
    for element in root.iter():
        if _local(element.tag) != 'agent' or not _xml_id(element):
            continue
        found = {child.get('type'): (child.text or '').strip() for child in element
                 if _local(child.tag) == 'name' and (child.text or '').strip()}
        if element.get('type') == 'person':
            persons[_xml_id(element)] = found.get('full') or next(iter(found.values()), '')
        else:
            names[_xml_id(element)] = found.get('alias') or found.get('full') or next(iter(found.values()), '') \
                or _xml_id(element)
            actor = next((child.get('agent') for child in element if _local(child.tag) == 'actor'), None)
            if actor:
                actors[_xml_id(element)] = actor
    return names, {key: persons[actor] for key, actor in actors.items() if persons.get(actor)}


def _dapt_document(root, clock, styles, folder=None):
    """(subtitles, language, metadata) of a DAPT script.

    Each Script Event is a subtitle: its times, its character as its
    speaker, its text in the subtitle's language and the rest as its
    translations, its recordings, where the files are there to be had, as
    its dub. The subtitle's language is the one most of the events' original
    text is in (the text whose daptm:langSrc is its own language, or none),
    else the document's. What has no home in a subtitle — what an event
    represents, whether its subject is on screen, its descriptions, the
    source language of its text, a second character, the mixing of its
    audio, a recording that is not there — is kept in its 'dapt', and what
    the script says of itself in metadata['dapt'], for a DAPT written again.
    """
    characters, talents = _dapt_agents(root)
    language = (root.get(_XML_NS + 'lang') or '').strip()
    events = []

    def lang_of(element, inherited):
        return (element.get(_XML_NS + 'lang') if element.get(_XML_NS + 'lang') is not None else inherited).strip()

    def source_of(element, inherited):
        return _dapt(element, 'langSrc') if _dapt(element, 'langSrc') is not None else inherited

    def audio_in(element, base, limit, found, mixing):
        """The recordings in a text, each timed from the text's start, and
        the mixing instructions on it."""
        for child in element:
            name = _local(child.tag)
            if name == 'audio':
                start, stop = _span_of(child, base, limit, clock)
                sources = [child.get('src')] if child.get('src') else \
                    [source.get('src') for source in child if _local(source.tag) == 'source' and source.get('src')]
                found.append({'sources': [source for source in sources if source], 'start': start, 'end': stop,
                              'type': child.get('type') or '',
                              'clip_begin': clock.seconds(child.get('clipBegin')),
                              'clip_end': clock.seconds(child.get('clipEnd'))})
            elif name == 'animate':
                said = {_local(key): value for key, value in child.attrib.items()
                        if _local(key) in ('begin', 'end', 'dur', 'fill', 'gain', 'pan')}
                if 'gain' in said or 'pan' in said:
                    mixing.append(said)
            elif name == 'span':
                start, stop = _span_of(child, base, limit, clock)
                audio_in(child, start, stop, found, mixing)

    def walk(element, base, limit, lang, source, represents, agent):
        name = _local(element.tag)
        start, stop = _span_of(element, base, limit, clock) if name in ('body', 'div') else (base, limit)
        lang, source = lang_of(element, lang), source_of(element, source)
        represents = _dapt(element, 'represents') or represents
        agent = element.get(f'{{{_TTM_NS}}}agent') or agent
        children = [child for child in element if _local(child.tag) in ('body', 'div')]
        if name == 'div' and _xml_id(element) and not children:
            texts, mixing = [], []
            for p in (child for child in element if _local(child.tag) == 'p'):
                p_lang, p_source = lang_of(p, lang), source_of(p, source)
                runs = []
                _runs(p, styles, styles.of(p, {}), False, runs, [None])
                found = []
                p_start, p_stop = _span_of(p, start, stop, clock)
                audio_in(p, p_start, p_stop, found, mixing)
                speak = next((value for inner in p.iter() for key, value in inner.attrib.items()
                              if _namespace(key) == _TTA_NS and _local(key) == 'speak' and value != 'none'), '')
                texts.append({'lang': p_lang, 'source': p_source, 'text': markup.tidy(runs).strip(),
                              'audio': found, 'speak': speak, 'represents': _dapt(p, 'represents') or '',
                              'agent': p.get(f'{{{_TTM_NS}}}agent') or ''})
            audio_in(element, start, stop, [], mixing)
            descriptions = [{'text': (child.text or '').strip(),
                             **({'type': _dapt(child, 'descType')} if _dapt(child, 'descType') else {})}
                            for child in element if _local(child.tag) == 'desc' and (child.text or '').strip()]
            events.append({'id': _xml_id(element), 'start': start, 'end': stop, 'texts': texts,
                           'represents': represents, 'said': _dapt(element, 'represents') is not None, 'on_screen': (_dapt(element, 'onScreen') or '').strip(),
                           'agents': (agent or next((text['agent'] for text in texts if text['agent']), '')).split(),
                           'descriptions': descriptions, 'mixing': mixing})
            return
        for child in children:
            walk(child, start, stop, lang, source, represents, agent)

    body = next((element for element in root if _local(element.tag) == 'body'), None)
    root_source = _dapt(root, 'langSrc') or ''
    root_represents = _dapt(root, 'represents') or ''
    if body is not None:
        walk(body, 0.0, None, language, root_source, root_represents, root.get(f'{{{_TTM_NS}}}agent'))

    counted = {}
    for event in events:
        for text in event['texts']:
            if text['text'] and _original(text['lang'], text['source']):
                counted[text['lang']] = counted.get(text['lang'], 0) + 1
    main = max(counted, key=lambda key: (counted[key], key == language)) if counted else language
    body_represents = (_dapt(body, 'represents') if body is not None else None) or root_represents

    segments = []
    for event in events:
        if event['end'] is None:
            continue
        mine = [text for text in event['texts'] if text['lang'].lower() == main.lower()]
        chosen = next((text for text in mine if _original(text['lang'], text['source'])), mine[0] if mine else None)
        segment = {'start': round(event['start'], 3), 'end': round(max(event['start'], event['end']), 3),
                   'text': chosen['text'] if chosen else ''}
        names = [characters.get(agent, agent) for agent in event['agents']]
        if names:
            segment['speaker'] = names[0]
        translations = {}
        for text in event['texts']:
            if text is not chosen and text['text']:
                translations[text['lang']] = '\n'.join(part for part in (translations.get(text['lang']), text['text'])
                                                       if part)
        if translations:
            segment['translations'] = translations
        kept = {'id': event['id']}
        # What it represents, where the event says or it differs from what
        # the script's body says for all of them.
        if event['represents'] and (event['said'] or event['represents'] != body_represents):
            kept['represents'] = event['represents']
        if event['on_screen'] and event['on_screen'] != 'ON':
            kept['on_screen'] = event['on_screen']
        if chosen and chosen['source'] and chosen['source'] != root_source:
            kept['lang_src'] = chosen['source']
        if len(names) > 1:
            kept['characters'] = names
        if event['descriptions']:
            kept['descriptions'] = event['descriptions']
        if event['mixing']:
            kept['mixing'] = event['mixing']
        if chosen and chosen['speak']:
            kept['speak'] = chosen['speak']
        # The recordings of the subtitle's own text, as its dub, where the
        # files are there: each a clip of its file, placed where it plays.
        clips, missing = [], []
        for audio in (chosen['audio'] if chosen else []):
            path = _audio_path(audio['sources'], folder)
            if path is None:
                missing.append({key: value for key, value in audio.items() if value not in (None, '', [])})
                continue
            clips.append((path, audio))
        if clips:
            segment['dubbing'] = [_dub_of(clips)]
        if missing:
            kept['audio'] = missing
        segment['dapt'] = kept
        segments.append(segment)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))

    about = _metadata(root)
    script = {'script_type': (_dapt(root, 'scriptType') or '').strip(),
              'script_represents': ' '.join((_dapt(root, 'scriptRepresents') or '').split()),
              'lang_src': root_source, 'represents': body_represents}
    if talents:
        script['talents'] = {characters.get(key, key): name for key, name in talents.items()}
    about['dapt'] = {key: value for key, value in script.items() if value}
    return segments, main, about


def _audio_path(sources, folder):
    """The local file one of an <audio>'s sources names, or None: a path
    or a file: URL, relative to the script's folder; an embedded or a
    remote recording is not one."""
    import os
    import urllib.parse
    for source in sources:
        if source.startswith('#'):
            continue
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme not in ('', 'file') and len(parsed.scheme) > 1:
            continue
        path = urllib.parse.unquote(parsed.path if parsed.scheme else source)
        if folder and not os.path.isabs(path):
            path = os.path.join(folder, path)
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _dub_of(clips):
    """A dub (subtitle['dubbing'][0], modules.dub_clip) of a text's
    recordings [(path, audio)]: one file played whole is the dub's path, as
    a recorded take is; more, or a clip of one, are its subclips."""
    first = min(audio['start'] for _path, audio in clips)
    if len(clips) == 1 and clips[0][1]['clip_begin'] is None and clips[0][1]['clip_end'] is None:
        return {'path': clips[0][0], 'start': round(first, 3)}
    subclips = []
    for path, audio in clips:
        begin = audio['clip_begin'] or 0.0
        end = audio['clip_end']
        if end is None and audio['end'] is not None:
            end = begin + (audio['end'] - audio['start'])
        if end is None or end <= begin:
            continue
        subclips.append({'type': 'audio', 'path': path, 'start': round(begin, 3), 'end': round(end, 3),
                         'offset': round(audio['start'] - first, 3)})
    dub = {'path': clips[0][0], 'start': round(first, 3)}
    if subclips:
        dub['segments'] = subclips
    return dub


def _dapt_problems(root, declared_profile, content_profiles):
    """What breaks DAPT in a document: its profile and what the script
    must say of itself, its characters, its events, the languages and what
    they represent, its timing, its recordings."""
    problems = []
    if _DAPT_CONTENT not in content_profiles:
        problems.append(f'ttp:contentProfiles does not name DAPT\'s content profile ({_DAPT_CONTENT})')
    if declared_profile:
        problems.append('ttp:profile is present: DAPT, built on TTML2, prohibits it')
    script_represents = (_dapt(root, 'scriptRepresents') or '').split()
    if not script_represents:
        problems.append('<tt> has no daptm:scriptRepresents: a script says what it is an alternative for')

    def valid(descriptor):
        if not _DESCRIPTOR.match(descriptor):
            return False
        if descriptor in DAPT_REPRESENTS or descriptor.startswith('x-'):
            return True
        parts = descriptor.split('.')
        return any(parts[index].startswith('x-') and '.'.join(parts[:index]) in DAPT_REPRESENTS
                   for index in range(1, len(parts)))
    unknown = sorted({value for value in script_represents if not valid(value)})
    if unknown:
        problems.append(f'daptm:scriptRepresents names what DAPT\'s registry has not ({", ".join(unknown)}): '
                        'one of its own begins "x-"')
    script_type = _dapt(root, 'scriptType')
    if script_type is None:
        problems.append('<tt> has no daptm:scriptType')
    elif script_type.strip() not in DAPT_SCRIPT_TYPES:
        problems.append(f'daptm:scriptType="{script_type}": it is {", ".join(DAPT_SCRIPT_TYPES)}')
    if (_attr(root, 'timeBase') or 'media').strip() != 'media':
        problems.append('DAPT is timed in media time: ttp:timeBase is "media" or not said')
    if any((element.get('timeContainer') or 'par') != 'par' for element in root.iter()):
        problems.append('timeContainer other than "par": DAPT has none')
    framed = any(re.fullmatch(r'\d+(?:\.\d+)?f|\d+:\d{2}:\d{2}:\d+(?:\.\d+)?', (element.get(name) or '').strip())
                 for element in root.iter() for name in ('begin', 'end', 'dur', 'clipBegin', 'clipEnd'))
    if framed and _attr(root, 'frameRate') is None:
        problems.append('Times in frames, but <tt> has no ttp:frameRate')

    parents = {child: parent for parent in root.iter() for child in parent}
    ids = [_xml_id(element) for element in root.iter() if _xml_id(element)]
    twice = sorted({value for value in ids if ids.count(value) > 1})
    if twice:
        problems.append(f'xml:ids used twice ({", ".join(twice)}): each identifier is the script\'s once')
    agents = {_xml_id(element): element for element in root.iter() if _local(element.tag) == 'agent'}
    persons = {key for key, element in agents.items() if element.get('type') == 'person'}
    for key, element in agents.items():
        holder = parents.get(element)
        if holder is None or _local(holder.tag) != 'metadata' or _local(parents.get(holder, holder).tag) != 'head':
            problems.append(f'ttm:agent {key or "(no xml:id)"} outside <head>\'s <metadata>')
        if element.get('type') == 'character':
            if not key:
                problems.append('A character with no xml:id: events name characters by it')
            if not any(_local(child.tag) == 'name' and child.get('type') == 'alias' for child in element):
                problems.append(f'Character {key} has no ttm:name of type alias')
            for actor in (child for child in element if _local(child.tag) == 'actor'):
                if actor.get('agent') not in persons:
                    problems.append(f'Character {key}\'s ttm:actor names no person agent the script has')
        if element.get('type') == 'person' and not any(_local(child.tag) == 'name' and child.get('type') == 'full'
                                                       for child in element):
            problems.append(f'Person {key} has no ttm:name of type full')
    named = sorted({value for element in root.iter() for value in (element.get(f'{{{_TTM_NS}}}agent') or '').split()
                    if value not in agents})
    if named:
        problems.append(f'Events name characters the script does not have: {", ".join(named)}')

    homeless, unrepresented, wide, sources, screens = 0, 0, set(), set(), set()

    def walk(element, represents, inside_event):
        nonlocal homeless, unrepresented
        represents = _dapt(element, 'represents') or represents
        value = _dapt(element, 'represents')
        if value is not None and not (valid(value) and any(value == top or value.startswith(top + '.')
                                                          for top in script_represents)):
            wide.add(value)
        source = _dapt(element, 'langSrc')
        if source is not None and source.strip() and not _BCP47.match(source.strip()):
            sources.add(source)
        name = _local(element.tag)
        if name == 'div' and _dapt(element, 'onScreen') is not None and \
                _dapt(element, 'onScreen').strip() not in _DAPT_ON_SCREEN:
            screens.add(_dapt(element, 'onScreen'))
        event = inside_event or (name == 'div' and bool(_xml_id(element)))
        if name == 'p':
            if not inside_event:
                homeless += 1
            elif not represents:
                unrepresented += 1
        for child in element:
            walk(child, represents, event)
    walk(root, '', False)
    if homeless:
        problems.append(f'{homeless} <p> element(s) in no <div> with an xml:id: text belongs to a Script Event')
    if unrepresented:
        problems.append(f'{unrepresented} text(s) with no daptm:represents, said or inherited')
    if wide:
        problems.append(f'daptm:represents that is not one of the script\'s daptm:scriptRepresents, or below one '
                        f'({", ".join(sorted(wide))})')
    if sources:
        problems.append(f'daptm:langSrc that is not a language tag ({", ".join(sorted(sources))})')
    if screens:
        problems.append(f'daptm:onScreen="{", ".join(sorted(screens))}": it is ON, OFF, ON_OFF or OFF_ON')
    untyped = 0
    for element in root.iter():
        if _local(element.tag) != 'audio':
            continue
        for holder in [element] + [child for child in element if _local(child.tag) == 'source']:
            if holder.get('src') and not holder.get('src').startswith('#') and not holder.get('type'):
                untyped += 1
    if untyped:
        problems.append(f'{untyped} recording(s) linked with no type: DAPT asks for the audio\'s MIME type')
    return problems


def _dapt_text(text):
    """A subtitle's text as a DAPT <p> holds it: its formatting as TTML's
    spans, without the place a subtitle sits in, which a script has not."""
    _lead, rest = markup.split_lead(str(text or '').replace('\r\n', '\n').replace('\r', '\n'))
    rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
    return _content(rest) if markup.plain(rest).strip() else ''


def _dapt_audio(segment, start, audio_base):
    """<audio> elements for the dub of `segment` that is heard (its first
    un-muted take), each clip of a file placed where it plays, from the
    text's start, and linked relative to `audio_base`."""
    import os
    import urllib.parse
    dubs = segment.get('dubbing') or []
    dub = next((take for index, take in enumerate(dubs) if not take.get('muted', index != 0)), dubs[0] if dubs else None)
    if not isinstance(dub, dict):
        return []
    begin = float(dub.get('start', start) or start)
    clips = [clip for clip in (dub.get('segments') or []) if isinstance(clip, dict) and clip.get('path')]
    if not clips and dub.get('path'):
        clips = [{'path': dub['path'], 'offset': 0.0}]
    out = []
    for clip in clips:
        path = str(clip['path'])
        if audio_base:
            try:
                path = os.path.relpath(path, audio_base)
            except ValueError:
                pass
        source = urllib.parse.quote(path.replace(os.sep, '/'))
        kind = _AUDIO_TYPES.get(os.path.splitext(path)[1].lower(), 'audio/wave')
        attrs = [f'src={quoteattr(source)}', f'type="{kind}"']
        at = begin + float(clip.get('offset') or 0) - start
        if at > 0.0005:
            attrs.append(f'begin="{format_time(at)}"')
        if clip.get('start') is not None and 'end' in clip:
            attrs.append(f'clipBegin="{format_time(float(clip["start"]))}"')
            if clip.get('end') is not None:
                attrs.append(f'clipEnd="{format_time(float(clip["end"]))}"')
        out.append(f'<audio {" ".join(attrs)}/>')
    return out


def _write_dapt(segments, language, metadata=None, audio_base=None):
    """A DAPT script of `segments`: each subtitle a Script Event, its
    speaker its character, its text the original <p> in `language` and its
    translations a <p> each, translated from it; the dub heard for it, an
    <audio> for each clip. What the script said of itself when it was read
    (metadata['dapt']) and what each event said (its 'dapt') is written
    again; a new script is a dubbing script of dialogue, as recorded where
    there are dubs, translated where there are translations."""
    tag = export_options.language_tag(language)
    script = dict((metadata or {}).get('dapt') or {})
    ordered = sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
    translated = sorted({code for segment in ordered for code, text in (segment.get('translations') or {}).items()
                         if isinstance(segment.get('translations'), dict) and str(text or '').strip()})
    dubbed = any(segment.get('dubbing') for segment in ordered)
    script_type = script.get('script_type') if script.get('script_type') in DAPT_SCRIPT_TYPES else \
        'asRecorded' if dubbed else 'translatedTranscript' if translated else 'originalTranscript'
    script_represents = ' '.join(str(script.get('script_represents') or 'audio.dialogue').split())
    represents = script.get('represents') or script_represents.split()[0]
    source = script.get('lang_src') if script.get('lang_src') is not None and 'lang_src' in script else tag

    characters, persons = {}, {}
    talents = script.get('talents') or {}
    for segment in ordered:
        for name in [str(segment.get('speaker') or '').strip()] + list((segment.get('dapt') or {}).get('characters') or [])[1:]:
            if name and name not in characters:
                characters[name] = f'character_{len(characters) + 1}'
                if talents.get(name) and talents[name] not in persons:
                    persons[talents[name]] = f'actor_{len(persons) + 1}'
    head = []
    for key, name in (('title', 'ttm:title'), ('description', 'ttm:desc'), ('copyright', 'ttm:copyright')):
        said = str((metadata or {}).get(key) or '').strip()
        if said:
            head.append(f'      <{name}>{escape(said)}</{name}>')
    # The talents before the characters they voice, all in the first
    # <metadata>, as DAPT recommends.
    for name, agent_id in persons.items():
        head.append(f'      <ttm:agent type="person" xml:id="{agent_id}"><ttm:name type="full">{escape(name)}'
                    f'</ttm:name></ttm:agent>')
    for name, agent_id in characters.items():
        actor = f'<ttm:actor agent="{persons[talents[name]]}"/>' if talents.get(name) in persons else ''
        head.append(f'      <ttm:agent type="character" xml:id="{agent_id}"><ttm:name type="alias">{escape(name)}'
                    f'</ttm:name>{actor}</ttm:agent>')

    used = set(characters.values()) | set(persons.values())
    events, audio_used = [], False
    for number, segment in enumerate(ordered, start=1):
        kept = segment.get('dapt') if isinstance(segment.get('dapt'), dict) else {}
        event_id = str(kept.get('id') or '')
        if not re.fullmatch(r'[A-Za-z_][\w.-]*', event_id) or event_id in used:
            event_id = next(f'e{n}' for n in range(number, number + len(ordered) + 2) if f'e{n}' not in used)
        used.add(event_id)
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        attrs = [f'xml:id="{event_id}"', f'begin="{format_time(start)}"', f'end="{format_time(end)}"']
        names = list(kept.get('characters') or []) or [str(segment.get('speaker') or '').strip()]
        agents = [characters[name] for name in names if name in characters]
        if agents:
            attrs.append(f'ttm:agent="{" ".join(agents)}"')
        if kept.get('represents'):
            attrs.append(f'daptm:represents={quoteattr(str(kept["represents"]))}')
        if kept.get('on_screen') in _DAPT_ON_SCREEN and kept['on_screen'] != 'ON':
            attrs.append(f'daptm:onScreen="{kept["on_screen"]}"')
        lines = [f'    <div {" ".join(attrs)}>']
        for description in kept.get('descriptions') or []:
            if isinstance(description, dict) and str(description.get('text') or '').strip():
                kind = f' daptm:descType={quoteattr(str(description["type"]))}' if description.get('type') else ''
                lines.append(f'      <ttm:desc{kind}>{escape(str(description["text"]).strip())}</ttm:desc>')
        inner = []
        for mix in kept.get('mixing') or []:
            if isinstance(mix, dict):
                said = ' '.join(f'{"tta:" if key in ("gain", "pan") else ""}{key}={quoteattr(str(value))}'
                                for key, value in mix.items() if key in ('begin', 'end', 'dur', 'fill', 'gain', 'pan'))
                inner.append(f'<animate {said}/>')
        recordings = _dapt_audio(segment, start, audio_base)
        audio_used = audio_used or bool(recordings or inner or kept.get('speak'))
        speak = f' tta:speak={quoteattr(str(kept["speak"]))}' if kept.get('speak') else ''
        own_source = kept.get('lang_src') if kept.get('lang_src') is not None else source
        text = _dapt_text(segment.get('text'))
        lines.append(f'      <p xml:lang="{tag}" daptm:langSrc={quoteattr(str(own_source))}{speak}>'
                     f'{"".join(inner + recordings)}{text}</p>')
        for code in translated:
            other = _dapt_text((segment.get('translations') or {}).get(code))
            if other:
                # Translated from the subtitle's own language: DAPT names the
                # source by the language of the text it came from.
                lines.append(f'      <p xml:lang="{export_options.language_tag(code)}" daptm:langSrc="{tag}">'
                             f'{other}</p>')
        lines.append('    </div>')
        events += lines

    namespaces = ['xmlns="http://www.w3.org/ns/ttml"', 'xmlns:tts="http://www.w3.org/ns/ttml#styling"',
                  'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"', f'xmlns:ttm="{_TTM_NS}"',
                  f'xmlns:daptm="{_DAPTM_NS}"']
    if audio_used:
        namespaces.append(f'xmlns:tta="{_TTA_NS}"')
    root = ' '.join(namespaces + [f'ttp:contentProfiles="{_DAPT_CONTENT}"', f'xml:lang="{tag}"',
                                  f'daptm:langSrc={quoteattr(str(source))}',
                                  f'daptm:scriptRepresents={quoteattr(script_represents)}',
                                  f'daptm:scriptType="{script_type}"'])
    return '\n'.join(['<?xml version="1.0" encoding="UTF-8"?>', f'<tt {root}>', '  <head>', '    <metadata>', *head,
                      '    </metadata>', '  </head>', f'  <body daptm:represents={quoteattr(represents)}>', *events,
                      '  </body>', '</tt>', ''])


def _document(text, images=None, folder=None):
    """(subtitles, language, metadata) in TTML text; with `images`, a dict,
    the pictures go in it as 'pictures' and the picture's size as 'size',
    as `read_images` gives them. A DAPT script's recordings are found in
    `folder`, where the script is."""
    text = _ENTITY.sub(_entity, text.lstrip('﻿'))
    # The text is decoded already: the declaration's encoding no longer applies.
    text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text)
    root = ET.fromstring(text)
    if _local(root.tag) != 'tt':
        raise ValueError(f'Not a TTML document: it is <{_local(root.tag)}>, not <tt>')
    clock = _Clock(root)
    frame = _Frame(root)
    styles = _Styles(root)
    if images is None and is_dapt(root):
        return _dapt_document(root, clock, styles, folder)
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
    embedded = _embedded_images(root)

    def picture(element, start, stop, props, region):
        """A subtitle that is a picture: the words its alt text gives it, where
        its region puts it, and the picture itself for `images`."""
        if stop is None:
            return
        region_props = _region_props(regions.get(region), styles, clock, start)
        own = styles.of(element, dict(region_props, **props))
        # Its words: IMSC's alt text, else the text SMPTE-TT may put in the
        # picture's <div> beside it, which is read and not drawn.
        alt = _alt_text(element) or '\n'.join(
            text for text in (_plain_paragraph(inner, styles, own) for inner in element.iter()
                              if _local(inner.tag) == 'p') if text)
        keypad = _keypad(own, frame)
        forced = str(own.get('forcedDisplay') or '').strip().lower() == 'true'
        if alt:
            segment = {'start': round(start, 3), 'end': round(max(start, stop), 3),
                       'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + alt}
            if forced:
                segment['forced'] = True
            segments.append(segment)
        if images is not None:
            source = _attr(element, 'backgroundImage').strip()
            images.setdefault('pictures', []).append({'start': round(start, 3), 'end': round(max(start, stop), 3), 'alt': alt,
                           'href': '' if source.startswith('#') else source,
                           'data': embedded.get(source[1:]) if source.startswith('#') else None,
                           'origin': frame.percent(own.get('origin')), 'extent': frame.percent(own.get('extent')),
                           'forced': forced})
    base_sizes = (base_props['fontSize'],) if base_props.get('fontSize') else ()

    def paragraph(element, start, stop, props, region, preserve, outer_agent, unshown=False, sizes=()):
        # The region as it is when the subtitle begins: a region converted
        # from CEA-608 is moved about by <set> (RP 2052-10).
        region_props = _region_props(regions.get(region), styles, clock, start)
        # The region's styles first: what the body, div or paragraph says
        # goes over them.
        p_props = styles.of(element, dict(region_props, **props))
        runs, agent = [], [None]
        _runs(element, styles, p_props, preserve, runs, agent)
        box, outline = _whole_look(element, styles, p_props)
        runs = [(run, _own_look(style, box, outline)) for run, style in runs]
        body = markup.tidy(runs)
        if stop is None or not markup.plain(body).strip():
            return
        # A file that states both alignments means them; one that leaves
        # them out is read by where its region sits, as before.
        stated = p_props.get('displayAlign') is not None and p_props.get('textAlign') is not None
        at = frame.percent(p_props.get('origin')) \
            if not p_props.get('extent') and not p_props.get('position') else None
        if at:
            # A region with a place and no size, as RP 2052-10 converts
            # CEA-608's rows and columns: the text starts there, as tall as
            # its lines and reaching to the right edge. Its column is the
            # one its textAlign says, so a line indented from the left stays
            # a left-aligned line with a margin, as an SCC is read.
            lines = markup.plain(body).count('\n') + 1
            height = max(1.0, min(100.0 - at[1], lines * 100.0 / frame.cells[1]))
            p_props = dict(p_props, extent=f'{100.0 - at[0]:.4g}% {height:.4g}%',
                           displayAlign=p_props.get('displayAlign') or 'before')
            align = p_props.get('textAlign') or 'start'
            near, far = (2, 0) if p_props.get('direction') == 'rtl' else (0, 2)
            column = {'left': 0, 'right': 2, 'start': near, 'end': far}.get(align, 1)
            keypad = (_keypad(dict(p_props, textAlign='left'), frame) - 1) // 3 * 3 + 1 + column
        else:
            keypad = _keypad(p_props, frame, geometry=not stated)
        segment = {'start': round(start, 3), 'end': round(max(start, stop), 3),
                   'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + body}
        speaker = agent[0] or outer_agent
        if speaker:
            segment['speaker'] = agents.get(speaker, speaker)
        # Which way the writing runs, where the file says: Arabic and Hebrew
        # read right to left, and that decides where a line's punctuation
        # lands and which end "start" is.
        writing_mode = (p_props.get('writingMode') or '').strip()
        if p_props.get('direction') in ('ltr', 'rtl'):
            segment['direction'] = p_props['direction']
        elif writing_mode in _RIGHT_TO_LEFT:
            segment['direction'] = 'rtl'
        # Written down the picture, in columns.
        if writing_mode in _WRITING:
            segment['writing'] = _WRITING[writing_mode]
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
        if name == 'div' and (_attr(element, 'backgroundImage') or '').strip():
            picture(element, start, stop, props, region)
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
    if not segments:
        # SMPTE-TT may carry the captions it was converted from, CEA-608 or
        # CEA-708, tunnelled in smpte:data; a document that holds them and
        # no text is read from them.
        tunnels = _tunnelled(root, clock)
        if tunnels:
            segments.extend(_captions_of(root, clock, tunnels))
    about = _metadata(root)
    # Timecodes from the start of the programme an EBU-TT file names
    # (ebuttm:documentStartOfProgramme, 10:00:00:00 as often as not): its
    # subtitles count from there, as an STL's from its TCP, when they all
    # come after it.
    programme = (about.get('stl') or {}).get('TCP', '')
    shift = clock.seconds(':'.join(programme[i:i + 2] for i in range(0, 8, 2))) if clock.smpte and programme else 0
    found = segments + (images.get('pictures', []) if images is not None else [])
    if shift and found and min(float(item['start']) for item in found) >= shift - 0.0005:
        for item in found:
            item['start'] = round(max(0.0, item['start'] - shift), 3)
            item['end'] = round(max(0.0, item['end'] - shift), 3)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    if images is not None:
        images.setdefault('pictures', []).sort(key=lambda image: (image['start'], image['end']))
        images['size'] = frame.pixels
    return segments, (root.get(_XML_NS + 'lang') or '').strip(), about


def _metadata(root):
    """What the file says about itself, from <head>'s <metadata>: its
    ttm:title, ttm:desc and ttm:copyright, as modules.session keeps them
    on the subtitle. A file that says none of it gives {}."""
    into = {'title': 'title', 'desc': 'description', 'copyright': 'copyright'}
    found = {}
    # Inside a <metadata> block, so a ttm:desc describing one subtitle is
    # not read as what the whole document is — or in <head> itself, where
    # TTML 1 permits them and EBU-TT Part 1 puts its ttm:copyright.
    head = next((element for element in root if _local(element.tag) == 'head'), None)
    for block in [head] + [element for element in root.iter() if _local(element.tag) == 'metadata']:
        if block is None:
            continue
        for element in block:
            key = into.get(_local(element.tag))
            if key and (element.text or '').strip():
                found.setdefault(key, (element.text or '').strip())
    # What EBU Tech 3360 carries over from an STL file's GSI, kept by the
    # STL's names for it (modules.ebustl), in <head>'s metadata or in the
    # ebuttm:documentMetadata that EBU-TT 1.0 kept it in.
    stl = {}
    for element in (head.iter() if head is not None else ()):
        key = _STL_READ.get(_local(element.tag)) if _namespace(element.tag) == _EBUTTM_NS else None
        value = (element.text or '').strip()
        if not key or not value or key in stl:
            continue
        if key in ('CD', 'RD'):
            date = re.fullmatch(r'\d\d(\d\d)-(\d\d)-(\d\d)', value)
            value = ''.join(date.groups()) if date else ''
        elif key == 'TCP':
            label = _CLOCK.match(value)
            value = ''.join(f'{int(part or 0):02d}' for part in label.group(1, 2, 3, 5)) if label else ''
        if value:
            stl[key] = value
    if stl:
        found['stl'] = stl
        if stl.get('OPT'):
            found.setdefault('title', stl['OPT'])
    copyright_old = next((element.text.strip() for element in (head.iter() if head is not None else ())
                          if _local(element.tag) == 'documentCopyright' and (element.text or '').strip()), '')
    if copyright_old:
        found.setdefault('copyright', copyright_old)
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
    import os
    with open(path, 'rb') as ttml_file:
        return _document(_text_of(ttml_file.read()), folder=os.path.dirname(os.path.abspath(path)))


def read_images(path):
    """(pictures, language, picture size) of the TTML file at `path`.

    A picture is {'start', 'end', 'alt', 'href', 'data', 'origin', 'extent',
    'forced'}: its words where the file gives them ('' where it does not),
    the PNG as a path relative to the file ('href') or as the bytes the
    file holds ('data'), and its region's place and size in % of the
    picture. The size is the file's tts:extent in pixels, or None."""
    images = {}
    with open(path, 'rb') as ttml_file:
        _segments, language, _metadata_found = _document(_text_of(ttml_file.read()), images)
    return images['pictures'], language, images['size']


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
    profile = profile if profile in PROFILES + IMAGE_PROFILES else 'ttml1'
    try:
        root = ET.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', _ENTITY.sub(_entity, (text or '').lstrip('﻿'))))
    except ET.ParseError as error:
        return [f'Not XML that can be read: {error}']
    if _local(root.tag) != 'tt':
        return [f'The document is <{_local(root.tag)}>, not <tt>']

    problems = []
    if profile == 'smpte-tt':
        # TTML 1 asks for the attribute, and RP 2052-10 for it empty where a
        # conversion's language is not known.
        if root.get(_XML_NS + 'lang') is None:
            problems.append('<tt> has no xml:lang: TTML 1 asks a file to say what language it is in, '
                            'if only as ""')
    elif not (root.get(_XML_NS + 'lang') or '').strip():
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
    if profile == 'smpte-tt':
        problems += _smpte_problems(root, declared_profile)
    if profile == 'ebu-tt':
        problems += _ebu_tt_problems(root, standards)
    if profile == 'dapt':
        problems += _dapt_problems(root, declared_profile, content_profiles)

    cells = (_attr(root, 'cellResolution') or '').split()
    if cells:
        try:
            if len(cells) != 2 or not all(int(cell) > 0 for cell in cells):
                raise ValueError
        except ValueError:
            problems.append(f'ttp:cellResolution is {" ".join(cells)!r}: it is two whole numbers above zero')

    if profile in IMAGE_PROFILES:
        problems += _image_problems(root, profile, declared_profile, content_profiles)
        return problems

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
        crowded = _most_regions_at_once(_timed_regions(root, _Clock(root)))
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

    # TTML2's emphasis marks, what a ruby says about its reading and text
    # set upright down a column. IMSC 1.0.1 and EBU-TT-D are built on TTML 1
    # and have none of it; IMSC 1.1
    # has the minimal sets: no colour or mark of its own for an emphasis
    # (#textEmphasis-minimal), no spreading a reading with its base
    # (#rubyAlign-minimal).
    if profile in _TTML1_PROFILES:
        annotated = sorted({name for element in root.iter() for name in _TTML2_ANNOTATIONS
                            if _attr(element, name) is not None})
        if annotated:
            problems.append(f'{", ".join("tts:" + name for name in annotated)}: TTML2 styling, '
                            'which this profile, built on TTML 1, does not have')
    if profile == 'imsc1.1':
        marks = sorted({value for element in root.iter() for value in [_attr(element, 'textEmphasis') or '']
                        if re.search(r'["\']', value)
                        or set(value.lower().replace(',', ' ').split()) - _EMPHASIS_WORDS})
        if marks:
            problems.append(f'Emphasis with a colour or a mark of its own ({", ".join(marks)}): '
                            'IMSC 1.1 allows a fill, a shape and a position only')
        if any((_attr(element, 'rubyAlign') or '').strip() == 'withBase' for element in root.iter()):
            problems.append('tts:rubyAlign="withBase": IMSC 1.1 allows start, center, end, '
                            'spaceAround and spaceBetween')
        combined = sorted({value for element in root.iter() for value in [(_attr(element, 'textCombine') or '').strip()]
                           if value and value not in ('none', 'all')})
        if combined:
            problems.append(f'tts:textCombine="{", ".join(combined)}": IMSC 1.1 allows none and all')

    regions = {_xml_id(element) for element in root.iter()
               if _local(element.tag) == 'region' and _xml_id(element)}
    missing = sorted({element.get('region') for element in root.iter()
                      if element.get('region') and element.get('region') not in regions})
    if missing:
        problems.append(f'Subtitles name regions the file does not have: {", ".join(missing)}')
    return problems


def _namespace(name):
    return name[1:].split('}', 1)[0] if isinstance(name, str) and name.startswith('{') else ''


def _smpte_problems(root, declared_profile):
    """What breaks SMPTE-TT (ST 2052-1) in a document: the profile it
    declares, its time base and offsets, and its own vocabulary — the data
    it tunnels, the pictures it holds, the one smpte:information — as the
    standard and RP 2052-10 lay them down."""
    problems = []
    if declared_profile not in _SMPTE_PROFILES:
        problems.append(f'ttp:profile does not name SMPTE-TT\'s profile ({_SMPTE_PROFILE})')
    if (_attr(root, 'timeBase') or 'media').strip() != 'media':
        problems.append('SMPTE-TT asks for ttp:timeBase="media": "smpte" and "clock" should not be used')
    units = sorted({offset.group(2) for element in root.iter() for name in ('begin', 'end', 'dur')
                    for offset in [_OFFSET.match((element.get(name) or '').strip())] if offset and offset.group(2) != 't'})
    if units:
        problems.append(f'Offset times in {", ".join(units)}: an offset time in SMPTE-TT is counted in ticks (t)')
    parents = {child: parent for parent in root.iter() for child in parent}
    ours = [element for element in root.iter() if 'smpte-ra.org' in _namespace(element.tag)]
    stray = sorted({_namespace(element.tag) for element in ours if _namespace(element.tag) not in _SMPTE_NAMESPACES})
    if stray:
        problems.append(f'SMPTE vocabulary in a namespace SMPTE-TT does not have: {", ".join(stray)}')
    images = {}
    tunnels_608 = 0
    for element in ours:
        name = _local(element.tag)
        if name not in ('data', 'image', 'information'):
            continue
        parent = parents.get(element)
        if parent is None or _local(parent.tag) != 'metadata':
            problems.append(f'smpte:{name} outside a <metadata>: it is metadata\'s, and nowhere else')
        if name == 'information':
            continue
        if (element.get('encoding') or '').strip() != 'Base64':
            problems.append(f'smpte:{name} with encoding="{element.get("encoding") or ""}": it is Base64, and says so')
        try:
            payload = base64.b64decode(''.join((element.text or '').split()), validate=True)
        except (binascii.Error, ValueError):
            problems.append(f'smpte:{name} {_xml_id(element) or ""} holds what is not Base64'.replace('  ', ' '))
            payload = None
        if name == 'image':
            images[_xml_id(element)] = element
            if (element.get('imageType') or '').strip() != 'PNG':
                problems.append(f'smpte:image with imageType="{element.get("imageType") or ""}": SMPTE-TT\'s pictures are PNG')
            elif payload is not None and not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                problems.append(f'smpte:image {_xml_id(element) or ""} is not a PNG'.replace('  ', ' '))
            continue
        datatype = (element.get('datatype') or '').strip()
        if not (datatype.startswith('x-') and len(datatype) > 2) and datatype not in \
                [namespace + suffix for namespace in _SMPTE_NAMESPACES for suffix in ('#cea608', '#cea708')]:
            problems.append(f'smpte:data with datatype="{element.get("datatype") or ""}": the datatype is one '
                            'SMPTE defines (CEA-608\'s, CEA-708\'s), or a private one beginning "x-"')
        tunnels_608 += _data_kind(datatype) == '608'
    informations = [element for element in ours if _local(element.tag) == 'information']
    if len(informations) > 1:
        problems.append(f'{len(informations)} smpte:information elements: a document has one, in <head>')
    for info in informations:
        holder = parents.get(parents.get(info))
        if holder is None or _local(holder.tag) != 'head':
            problems.append('smpte:information outside <head>\'s <metadata>')
        mode = info.get('mode')
        if mode is not None and mode.strip() not in ('Enhanced', 'Preserved'):
            problems.append(f'smpte:information mode="{mode}": it is Enhanced or Preserved')
    if tunnels_608 and not any(_attr(info, 'fieldStart') for info in informations):
        problems.append('CEA-608 data tunnelled, but no m608:fieldStart says which field its first pair is from')
    pictures = [element for element in root.iter() if _attr(element, 'backgroundImage')]
    for element in pictures:
        if _local(element.tag) != 'div':
            problems.append(f'smpte:backgroundImage on a <{_local(element.tag)}>: it applies to a <div>')
        source = _attr(element, 'backgroundImage').strip()
        if source.startswith('#') and source[1:] not in images:
            problems.append(f'smpte:backgroundImage="{source}" names no smpte:image the file holds')
    origin = next((info.get('origin') or '' for info in informations), '')
    if _data_kind(origin) == '608' and (images or pictures):
        problems.append('Pictures in a document converted from CEA-608: RP 2052-10 has no smpte:image or '
                        'smpte:backgroundImage in one')
    return problems


# What EBU-TT Part 1 lets a region say itself (Tech 3350, Annex G), and the
# words some of its style attributes are limited to.
_EBU_TT_REGION = ('tts:displayAlign', 'tts:extent', 'tts:origin', 'tts:overflow', 'tts:padding',
                  'tts:showBackground', 'tts:writingMode')
_EBU_TT_WORDS = {'fontStyle': ('normal', 'italic'), 'textDecoration': ('none', 'underline'),
                 'unicodeBidi': ('normal', 'embed', 'bidiOverride'), 'fontWeight': ('normal', 'bold'),
                 'multiRowAlign': ('start', 'center', 'end', 'auto'), 'wrapOption': ('wrap', 'noWrap'),
                 'direction': ('ltr', 'rtl')}
_TTS_NS = 'http://www.w3.org/ns/ttml#styling'


def _ebu_tt_problems(root, standards):
    """What breaks EBU-TT Part 1 (EBU Tech 3350 v1.2) in a document: the
    conformance it declares, the time base and the parameters timecode
    needs, styling by reference only, what a <style> and a <region> may
    say and in which words, a <head> with its styling and layout."""
    problems = []
    if not any(standard.startswith('urn:ebu:tt:exchange') for standard in standards) and \
            not any(_local(element.tag) == 'documentEbuttVersion' for element in root.iter()):
        problems.append(f'No ebuttm:conformsToStandard naming EBU-TT Part 1 ({_EBU_TT_STANDARD})')
    base = _attr(root, 'timeBase')
    if base is None:
        problems.append('<tt> has no ttp:timeBase: EBU-TT asks a file to say it')
    elif base.strip() not in ('media', 'smpte', 'clock'):
        problems.append(f'ttp:timeBase="{base}": it is media, smpte or clock')
    if (base or '').strip() == 'smpte':
        missing = [name for name in ('frameRate', 'frameRateMultiplier', 'markerMode', 'dropMode') if _attr(root, name) is None]
        if missing:
            problems.append(f'Timed in timecode, without {", ".join("ttp:" + name for name in missing)}: '
                            'EBU-TT asks for each with ttp:timeBase="smpte"')
        if (_attr(root, 'frameRateMultiplier') or '1 1').split() in (['1', '1'], ['1000', '1000']) and \
                (_attr(root, 'dropMode') or 'nonDrop').strip() != 'nonDrop':
            problems.append(f'ttp:dropMode="{_attr(root, "dropMode")}" at a whole frame rate: '
                            'EBU-TT asks for nonDrop there')
        unlabelled = sorted({value for element in root.iter() for name in ('begin', 'end')
                             for value in [(element.get(name) or '').strip()]
                             if value and not re.fullmatch(r'\d{2,}:\d{2}:\d{2}:\d{2}', value)})
        if unlabelled:
            problems.append(f'Times that are not timecodes ({", ".join(unlabelled[:3])}): with ttp:timeBase="smpte" '
                            'EBU-TT writes HH:MM:SS:FF')
    extent = (_attr(root, 'extent') or '').split()
    if extent and not all(part.endswith('px') for part in extent):
        problems.append('tts:extent on <tt> in what is not px: EBU-TT gives the picture\'s size in pixels')
    head = next((element for element in root if _local(element.tag) == 'head'), None)
    for name, inside in (('styling', 'style'), ('layout', 'region')):
        holder = next((element for element in (head if head is not None else ()) if _local(element.tag) == name), None)
        if holder is None or not any(_local(element.tag) == inside for element in holder):
            problems.append(f'<head> has no <{name}> with a <{inside}> in it: EBU-TT asks for one')
    inline, worded = set(), set()
    for element in root.iter():
        name = _local(element.tag)
        styled = [key for key in element.attrib if _namespace(key) == _TTS_NS]
        if name in ('body', 'div', 'p', 'span', 'br') and styled:
            inline.add(name)
        if name == 'region':
            if not (_attr(element, 'origin') and _attr(element, 'extent')):
                problems.append(f'Region {_xml_id(element) or "(unnamed)"} without a tts:origin and a tts:extent: '
                                'EBU-TT asks every region for both')
            others = sorted({'tts:' + _local(key) for key in styled} - set(_EBU_TT_REGION))
            if others:
                problems.append(f'Region {_xml_id(element) or "(unnamed)"} says {", ".join(others)}: '
                                'EBU-TT puts that in a <style> the region names')
        if name == 'style':
            others = sorted({'tts:' + _local(key) for key in styled} - set(_EBU_TT_STYLE))
            if others:
                problems.append(f'Style {_xml_id(element) or "(unnamed)"} says {", ".join(others)}, '
                                'which EBU-TT does not have')
        for key, value in element.attrib.items():
            words = _EBU_TT_WORDS.get(_local(key)) if _namespace(key) in (_TTS_NS, _EBUTTS_NS) else None
            if words and value.strip() not in words:
                worded.add(f'{"ebutts" if _namespace(key) == _EBUTTS_NS else "tts"}:{_local(key)}="{value}"')
        padding = _attr(element, 'linePadding')
        if padding is not None and not re.fullmatch(r'\d+(?:\.\d+)?c', padding.strip()):
            worded.add(f'ebutts:linePadding="{padding}"')
    if inline:
        problems.append(f'Styles stated on {", ".join(f"<{name}>" for name in sorted(inline))} itself: EBU-TT styles '
                        'content by naming a <style>, never inline')
    if worded:
        problems.append(f'{", ".join(sorted(worded))}: not a value EBU-TT has')
    return problems


def _image_problems(root, profile, declared_profile, content_profiles):
    """What breaks one of IMSC's Image Profiles in a document: the
    declaration, the picture's size in pixels, text where only pictures
    may be, a picture held in the file, a picture's region missing."""
    problems = []
    if profile == 'imsc1-image' and declared_profile != _IMSC1_IMAGE:
        problems.append(f'ttp:profile does not name IMSC 1.0.1\'s Image Profile ({_IMSC1_IMAGE})')
    if profile == 'imsc1.1-image' and _IMSC11_IMAGE not in content_profiles:
        problems.append(f'ttp:contentProfiles does not name IMSC 1.1\'s Image Profile ({_IMSC11_IMAGE})')
    extent = [_LENGTH.match(part) for part in (_attr(root, 'extent') or '').split()]
    if not (len(extent) == 2 and all(part and part.group(2) == 'px' for part in extent)):
        problems.append('<tt> has no tts:extent in pixels: the Image Profile asks for the picture\'s size')
    texts = [element for element in root.iter() if _local(element.tag) in ('p', 'span')]
    if texts:
        problems.append(f'{len(texts)} <p> or <span> element(s): the Image Profile holds pictures, not text')
    if any(_local(element.tag) == 'image' for element in root.iter()):
        problems.append('smpte:image: IMSC asks for pictures beside the file, not held in it')
    regions = {_xml_id(element) for element in root.iter() if _local(element.tag) == 'region'}
    homeless = [element for element in root.iter()
                if _attr(element, 'backgroundImage') and element.get('region') not in regions]
    if homeless:
        problems.append(f'{len(homeless)} picture(s) in no region the file has')
    if _most_regions_at_once(_timed_regions(root, _Clock(root))) > 4:
        problems.append('More than four regions on screen at once: IMSC allows at most four')
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


def _open_span(tags, style, shear=False, annotations='full'):
    """A <span> for the formatting `tags` ({(name, value)}) open together;
    its textDecoration names all the run's, so none an outer span set is
    lost, whatever a player makes of a nested one. With `shear`, italics
    are written as a slant instead (see _SHEAR). With `annotations` 'none',
    what only TTML2 can say (emphasis marks, text set upright) is left out
    (see _ANNOTATIONS)."""
    names = {name: value for name, value in tags}
    attrs = []
    if 'font' in names:
        font = names['font']
        if font.color:
            attrs.append(f'tts:color="{font.color}"')
        if font.background:
            attrs.append(f'tts:backgroundColor="{font.background}"')
        if font.size:
            attrs.append(f'tts:fontSize="{font.size}"')
        if font.edge:
            # TTML draws an edge as an outline; the kinds it has no word for
            # are written as the outline nearest them.
            attrs.append(f'tts:textOutline="{font.edge_color or "#000000"} 4%"')
        if font.emphasis and annotations not in ('none', 'flat'):
            # markup's words are TTML's: a fill and a shape, or auto, and
            # before or after.
            attrs.append(f'tts:textEmphasis="{font.emphasis}"')
        if font.combine and annotations not in ('none', 'flat'):
            attrs.append(f'tts:textCombine="{font.combine}"')
    if 'i' in names:
        attrs.append(f'tts:shear="{_SHEAR}"' if shear else 'tts:fontStyle="italic"')
    if 'b' in names:
        attrs.append('tts:fontWeight="bold"')
    if 'u' in names or 's' in names:
        decoration = ' '.join(word for word, on in (('underline', style.underline), ('lineThrough', style.strike)) if on)
        attrs.append(f'tts:textDecoration="{decoration}"')
    return f'<span {" ".join(attrs)}>'


def _nested_content(runs, shear=False, annotations='full'):
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
            out.append(_open_span(new, style, shear, annotations))
            stack.append(frozenset(new))
        out.append('<br/>'.join(escape(part) for part in run.split('\n')))
    out.extend('</span>' for _span in stack)
    return ''.join(out)


def _content(text, shear=False, annotations='full'):
    """A subtitle's text as TTML. A ruby is a container span holding the
    base and its text, which is how IMSC 1.1 writes one: how its reading is
    spread on the container, where it sits on the reading, which is where
    TTML2 applies each. `annotations` says how much of that a profile
    takes (see _ANNOTATIONS)."""
    out = []
    for group in markup.ruby_groups(markup.spans(text)):
        if group[0] == 'plain':
            out.append(_nested_content(group[1], shear, annotations))
            continue
        base = _nested_content(group[1], shear, annotations)
        reading = _nested_content(group[2], shear, annotations)
        if not (base and reading):
            out.append(base + reading)
            continue
        if annotations == 'flat':
            # A profile with no ruby at all: the reading after its base, in
            # brackets, as a line of text gives one.
            out.append(f'{base}({reading})')
            continue
        said = markup.ruby_attributes(group) if annotations != 'none' else markup.Ruby()
        # Spreading a reading with its base is beyond IMSC 1.1's minimal set.
        spread = f' tts:rubyAlign="{_RUBY_ALIGN_OUT[said.align]}"' \
            if said.align in _RUBY_ALIGN_OUT and not (said.align == 'with-base' and annotations == 'minimal') else ''
        placed = f' tts:rubyPosition="{said.position}"' if said.position else ''
        out.append(f'<span tts:ruby="container"{spread}><span tts:ruby="base">{base}</span>'
                   f'<span tts:ruby="text"{placed}>{reading}</span></span>')
    return ''.join(out)


def _styles(look, preview, profile='ttml1'):
    """(<style> elements, the id of the box's or None) for `look`. The box
    goes on a span around each subtitle's text: a span's background is
    behind its text, a paragraph's across its region.

    EBU-TT-D does not use tts:textOutline, so its files say the rest of the
    style and leave the outline out.
    """
    if look != 'preview':
        outline = '' if profile in _EBU_PROFILES else ' tts:textOutline="black 5%"'
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
    ebu = profile in _EBU_PROFILES
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
    # Room on every line for a reading: TTML2's, so not in IMSC 1.0.1 or
    # EBU-TT-D, which are built on TTML 1.
    reserve = styling.get('ruby_reserve') or {}
    if isinstance(reserve, dict) and reserve.get('position') in subtitld_look.RESERVE_POSITIONS \
            and profile not in _TTML1_PROFILES:
        length = f' {_cells(reserve["length"])}' if reserve.get('length') else ''
        out.append(f'tts:rubyReserve="{reserve["position"]}{length}"')
    return ' '.join(out)


def _japanese(language):
    return str(language or '').replace('_', '-').split('-')[0].lower() == 'ja'


def _frame_rate(fps):
    """(ttp:frameRate, ttp:frameRateMultiplier or None) for a frame rate:
    29.97 is 30 frames at 1000/1001 of the speed, as TTML writes it."""
    fps = float(fps)
    nominal = int(round(fps))
    if abs(fps - nominal) < 0.005:
        return nominal, None
    return int(round(fps * 1.001)), '1000 1001'


def _root_attributes(profile, language, imsc_styling=False, ebu_styling=False, fps=None, tunnel=False, smpte=None):
    """What <tt> carries: the namespaces and parameters, and the profile
    the file declares itself to be. `imsc_styling` brings IMSC's styling
    namespace in, for a forced subtitle to mark or a box that reaches from
    one line to the next, and `ebu_styling` the EBU's, for a look that
    states one of its two. `fps` states the video's frame rate, and
    `tunnel` the namespaces of the CEA-608 data a SMPTE-TT file carries.
    `smpte` (nominal rate, multiplier or None, drop mode) times the file in
    SMPTE timecode, with the parameters EBU-TT asks of it."""
    namespaces = ['xmlns="http://www.w3.org/ns/ttml"',
                  'xmlns:tts="http://www.w3.org/ns/ttml#styling"',
                  'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"',
                  'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"']
    if imsc_styling:
        namespaces.append(f'xmlns:itts="{_ITTS_NS}"')
    if profile in _EBU_PROFILES:
        namespaces.append(f'xmlns:ebuttm="{_EBUTTM_NS}"')
    if ebu_styling:
        namespaces.append(f'xmlns:ebutts="{_EBUTTS_NS}"')
    if profile == 'smpte-tt' and tunnel:
        namespaces += [f'xmlns:smpte="{_SMPTE_NS}"', f'xmlns:m608="{_M608_NS}"']
    attrs = ['ttp:timeBase="media"']
    if smpte:
        nominal, multiplier, drop = smpte[:3]
        # EBU-TT Part 1 asks a file timed in timecode for all four. The
        # times run on as the subtitles do, so the markers are continuous.
        attrs = ['ttp:timeBase="smpte"', f'ttp:frameRate="{nominal}"',
                 f'ttp:frameRateMultiplier="{multiplier or "1 1"}"', 'ttp:markerMode="continuous"',
                 f'ttp:dropMode="{drop}"']
    elif fps:
        rate, multiplier = _frame_rate(fps)
        attrs.append(f'ttp:frameRate="{rate}"')
        if multiplier:
            attrs.append(f'ttp:frameRateMultiplier="{multiplier}"')
    if profile == 'imsc1':
        attrs.append(f'ttp:profile="{_IMSC1_TEXT}"')
    elif profile == 'imsc1.1':
        attrs.append(f'ttp:contentProfiles="{_IMSC11_TEXT}"')
    elif profile == 'smpte-tt':
        # SMPTE-TT names its profile the way TTML 1 does, in ttp:profile.
        attrs.append(f'ttp:profile="{_SMPTE_PROFILE}"')
    if profile != 'ttml1':
        # What the percentages in this file are measured against. It is
        # TTML's default, but EBU-TT-D asks for it in writing and a file
        # that says so cannot be read against another grid by mistake.
        attrs.append('ttp:cellResolution="32 15"')
    attrs.append(f'xml:lang={quoteattr(export_options.language_tag(language))}')
    return ' '.join(namespaces + attrs)


def _stamps(fps, start=None, drop_frame=False):
    """(seconds -> a TTML time, the SMPTE timing or None). Without `fps`,
    clock times in media time; with it, SMPTE timecode labels counted from
    `start`: (nominal rate, multiplier, drop mode, the start's label)."""
    if not fps:
        return format_time, None
    nominal, multiplier = _frame_rate(fps)
    real = nominal * (1000 / 1001 if multiplier else 1)
    drop = 'dropNTSC' if drop_frame and multiplier and nominal == 30 else 'nonDrop'
    label = _CLOCK.match(str(start or '').strip().replace(';', ':'))
    offset = timecode_frames(int(label.group(1)), int(label.group(2)), int(label.group(3)), int(label.group(5) or 0),
                             nominal, drop) if label else 0
    # Half a frame up, whatever Python's rounding does with halves.
    return (lambda seconds: timecode_label(int(float(seconds) * real + 0.5) + offset, nominal, drop),
            (nominal, multiplier, drop, timecode_label(offset, nominal, drop)))


# What EBU-TT Part 1 lets a <style> say (Tech 3350, Annex G).
_EBU_TT_STYLE = ('tts:direction', 'tts:fontFamily', 'tts:fontSize', 'tts:lineHeight', 'tts:textAlign', 'tts:color',
                 'tts:backgroundColor', 'tts:fontStyle', 'tts:fontWeight', 'tts:textDecoration', 'tts:unicodeBidi',
                 'tts:wrapOption', 'tts:padding', 'ebutts:multiRowAlign', 'ebutts:linePadding')
_START_TAG = re.compile(r'<(p|span)((?:\s+[\w:.-]+=(?:"[^"]*"|\'[^\']*\'))*)\s*>')
_ATTRIBUTE = re.compile(r'([\w:.-]+)=("[^"]*"|\'[^\']*\')')


def _referenced(fragment, hoisted):
    """`fragment` (TTML this module wrote) with the styles its <p> and
    <span> elements state inline moved into styles they name, as EBU-TT
    Part 1 asks: `hoisted` {the attributes: the id of their <style>} gathers
    them. What EBU-TT has no word for goes: line-through (its
    textDecoration is none or underline), an outline, an isolate."""
    def tag(match):
        kept, styled, named = [], [], []
        for name, value in _ATTRIBUTE.findall(match.group(2)):
            if name == 'style':
                named += value[1:-1].split()
            elif name.startswith('tts:'):
                inner = value[1:-1]
                if name == 'tts:textDecoration':
                    inner = 'underline' if 'underline' in inner.split() else ''
                if name in _EBU_TT_STYLE and inner and not (name == 'tts:unicodeBidi' and inner == 'isolate'):
                    styled.append(f'{name}={value[0]}{inner}{value[0]}')
            else:
                kept.append(f'{name}={value}')
        if styled:
            named.append(hoisted.setdefault(' '.join(styled), f'inline{len(hoisted) + 1}'))
        if named:
            kept.append(f'style="{" ".join(named)}"')
        return f'<{match.group(1)}{"".join(" " + attribute for attribute in kept)}>'
    return _START_TAG.sub(tag, fragment)


# The GSI fields of an EBU STL file (modules.ebustl) and the metadata EBU
# Tech 3360 carries them in, as EBU-TT Part M (Tech 3390) names it.
_STL_METADATA = (('OPT', 'documentOriginalProgrammeTitle'), ('OET', 'documentOriginalEpisodeTitle'),
                 ('TPT', 'documentTranslatedProgrammeTitle'), ('TET', 'documentTranslatedEpisodeTitle'),
                 ('TN', 'documentTranslatorsName'), ('TCD', 'documentTranslatorsContactDetails'),
                 ('SLR', 'documentSubtitleListReferenceCode'), ('CD', 'stlCreationDate'),
                 ('RD', 'stlRevisionDate'), ('RN', 'stlRevisionNumber'), ('TNS', 'documentTotalNumberOfSubtitles'),
                 ('MNC', 'documentMaximumNumberOfDisplayableCharacterInAnyRow'),
                 ('TCP', 'documentStartOfProgramme'), ('CO', 'documentCountryOfOrigin'),
                 ('PUB', 'documentPublisher'), ('EN', 'documentEditorsName'),
                 ('ECD', 'documentEditorsContactDetails'), ('UDA', 'documentUserDefinedArea'))
# The names read as well: Tech 3360's own spelling of one, and the element
# Part 1 version 1.0 had before ttm:copyright.
_STL_READ = dict({name: key for key, name in _STL_METADATA}, documentTotalNumbersOfSubtitles='TNS')
_EBU_TT_STANDARD = 'urn:ebu:tt:exchange:2017-05'


def _stl_date(value):
    """An STL date (YYMMDD) as xs:date, or '' for one that is not."""
    found = re.fullmatch(r'(\d\d)(\d\d)(\d\d)', str(value or '').strip())
    if not found or not ('01' <= found.group(2) <= '12' and '01' <= found.group(3) <= '31'):
        return ''
    year = int(found.group(1))
    return f'{2000 + year if year < 70 else 1900 + year}-{found.group(2)}-{found.group(3)}'


def _ebu_tt_metadata(metadata, count, widest, smpte):
    """The ebuttm elements of an EBU-TT Part 1 document's <head>: the
    standard it conforms to, and what EBU Tech 3360 carries over from an STL
    file's GSI (`metadata['stl']`, as modules.ebustl reads it), the number
    of subtitles and, without the STL's, the widest row this file's own.
    Each directly in <metadata>, as Part M has them since
    ebuttm:documentMetadata was deprecated."""
    stl = dict((metadata or {}).get('stl') or {})
    stl['TNS'] = str(count)
    stl.setdefault('MNC', str(widest))
    if smpte:
        stl['TCP'] = smpte[3]
    else:
        stl.pop('TCP', None)                    # a start of programme is a timecode's, and this file has none
    lines = [f'      <ebuttm:conformsToStandard>{_EBU_TT_STANDARD}</ebuttm:conformsToStandard>']
    for key, name in _STL_METADATA:
        value = str(stl.get(key) or '').strip()
        if key in ('CD', 'RD'):
            value = _stl_date(value)
        elif key in ('RN', 'TNS', 'MNC'):
            value = str(int(value)) if value.isdigit() else ''
        elif key == 'TCP' and re.fullmatch(r'\d{8}', value):
            value = ':'.join(value[i:i + 2] for i in range(0, 8, 2))
        if value:
            lines.append(f'      <ebuttm:{name}>{escape(value)}</ebuttm:{name}>')
    return lines


def _tunnel_608(segments):
    """[(first frame, bytes)] for `segments` as CEA-608 pop-on captions in
    CC1 (modules.scc), stored as RP 2052-10 stores them: field 1's pair and
    field 2's (empty) by turns, a frame each, at NTSC's rate. A run of
    frames is broken where a second or more goes by with nothing sent, so
    the file holds the captions and not the silence between them."""
    from subtitld.modules import scc
    words, _breaks, _shows = scc.frame_words(segments, _CEA608_RATE, mode='pop', channel=1)
    runs = []
    for frame in sorted(words):
        if runs and frame - runs[-1][1] <= 30:
            runs[-1][1] = frame
        else:
            runs.append([frame, frame])
    return [(first, b''.join(bytes.fromhex(words.get(frame, '8080')) + b'\x80\x80'
                             for frame in range(first, last + 1)))
            for first, last in runs]


def write(segments, language='en', agents=False, look='clean', preview=None, profile='ttml1', metadata=None,
          *, fps=None, tunnel=False, start=None, drop_frame=False, audio_base=None):
    """TTML text for `segments`: with `agents`, each speaker as a ttm:agent;
    in `look` ('clean' or 'preview', Subtitld's preview settings in
    `preview`, CONFIG['videoplayer']); declaring `profile` (PROFILES).
    `fps`, the video's frame rate, is stated where it is given; with
    `tunnel`, a SMPTE-TT file carries the subtitles as CEA-608 captions too.
    EBU-TT with a frame rate is timed in SMPTE timecode, from a programme
    start of `start` ("HH:MM:SS:FF", 00:00:00:00 without one), drop-frame
    at 29.97 with `drop_frame`. A DAPT script links its recordings relative
    to `audio_base`, the folder it is written in.

    A Japanese file in an IMSC profile writes its italics as tts:shear:
    that is what IMSC 1.1 is for in Japanese, and Netflix's Japanese
    profile prohibits tts:fontStyle outright."""
    profile = profile if profile in PROFILES else 'ttml1'
    if profile == 'dapt':
        return _write_dapt(segments, language, metadata, audio_base)
    tunnel = bool(tunnel) and profile == 'smpte-tt'
    shear = profile in ('imsc1', 'imsc1.1') and _japanese(language)
    annotations = _ANNOTATIONS[profile]
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
    stamp, smpte = _stamps(fps if profile == 'ebu-tt' else None, start, drop_frame)
    widest = 0
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
        if not markup.plain(rest).strip():
            continue
        keypad = markup.alignment(lead)
        placed = position.of(segment)
        styling = subtitld_look.of(segment)
        widest = max([widest] + [len(line) for line in markup.plain(rest).split('\n')])
        # Opacity belongs to a region, so a subtitle only part shown takes
        # a region of its own rather than fading the ones beside it.
        # And writing down the picture is a region's, so a subtitle written
        # in columns takes one of its own too.
        spot = (keypad, position.as_percent(placed.h), position.as_percent(placed.v),
                None if profile in _EBU_PROFILES else styling.get('opacity'), position.writing(segment))
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
        begin = float(segment.get('start', 0))
        end = max(begin, float(segment.get('end', begin)))
        attrs = f'begin="{stamp(begin)}" end="{stamp(end)}" region="{region}"'
        speaker = str(segment.get('speaker') or '').strip()
        if agents and speaker:
            attrs += f' ttm:agent="{speakers.setdefault(speaker, f"speaker{len(speakers) + 1}")}"'
        if segment.get('direction') in ('ltr', 'rtl'):
            attrs += f' tts:direction="{segment["direction"]}"'
        # EBU-TT-D's schema has normal, embed and bidiOverride, and no
        # isolate, so a file written for it says nothing of an isolate.
        if segment.get('bidi') in _BIDI_OUT and not (profile in _EBU_PROFILES and segment['bidi'] == 'isolate'):
            attrs += f' tts:unicodeBidi="{_BIDI_OUT[segment["bidi"]]}"'
        if segment.get('forced') and profile != 'ebu-tt-d':
            attrs += ' itts:forcedDisplay="true"'
        # EBU-TT-D's spec lists tts:display among the attributes it does not
        # use, so there a hidden subtitle is written as an ordinary one.
        if segment.get('hidden') and profile not in _EBU_PROFILES:
            attrs += ' tts:display="none"'
        looking = _look_attributes(styling, profile)
        if looking:
            attrs += f' style="{lookings.setdefault(looking, f"look{len(lookings) + 1}")}"'
        content = _content(rest, shear, annotations)
        # A box the file brought stands in for the style's own, and goes on
        # a span: on the paragraph TTML paints a band the width of the
        # region instead of a box around the words.
        boxing = _look_attributes(styling, profile, inline=True)
        around = boxings.setdefault(boxing, f'lookbox{len(boxings) + 1}') if boxing else box
        if around:
            content = f'<span style="{around}">{content}</span>'
        cues.append(f'      <p {attrs}>{content}</p>')

    if profile == 'ebu-tt':
        # EBU-TT Part 1 styles by reference only: what a paragraph or a span
        # says inline goes into a <style> of its own, which it names.
        hoisted = {}
        cues = [_referenced(cue, hoisted) for cue in cues]
        lookings.update(hoisted)
    head = ['  <head>']
    meta_lines = []
    if profile == 'ebu-tt':
        meta_lines += _ebu_tt_metadata(metadata, len(cues), widest, smpte)
    if profile == 'ebu-tt-d':
        meta_lines.append(f'      <ebuttm:documentMetadata>'
                          f'<ebuttm:conformsToStandard>{_EBUTTD_STANDARD}</ebuttm:conformsToStandard>'
                          f'</ebuttm:documentMetadata>')
    # What the file says about itself, in TTML's own words for it.
    for key, name in (('title', 'ttm:title'), ('description', 'ttm:desc'), ('copyright', 'ttm:copyright')):
        said = str((metadata or {}).get(key) or '').strip()
        if said and not (profile == 'ebu-tt' and key == 'copyright'):
            meta_lines.append(f'      <{name}>{escape(said)}</{name}>')
    for name, agent_id in speakers.items():
        meta_lines.append(f'      <ttm:agent xml:id="{agent_id}" type="character">'
                          f'<ttm:name type="full">{escape(name)}</ttm:name></ttm:agent>')
    tunnels = _tunnel_608(segments) if tunnel else []
    if tunnels:
        # What the tunnelled data is (RP 2052-10): CEA-608's CC1, its first
        # pair field 1's. The text was not translated from it, so the mode
        # is Enhanced: the captions are the text's, in what 608 can say.
        meta_lines.append(f'      <smpte:information origin="{_CEA608_DATA}" mode="Enhanced" '
                          f'm608:channel="CC1" m608:fieldStart="1"/>')
    if meta_lines:
        head += ['    <metadata>', *meta_lines, '    </metadata>']
    copyright_said = str((metadata or {}).get('copyright') or '').strip()
    if profile == 'ebu-tt' and copyright_said:
        # EBU-TT Part 1 has ttm:copyright in <head> itself, after its
        # <metadata>, and nowhere else.
        head.append(f'    <ttm:copyright>{escape(copyright_said)}</ttm:copyright>')
    style_lines = style_lines + [f'<style xml:id="{name}" {looking}/>'
                                 for looking, name in list(lookings.items()) + list(boxings.items())]
    aligned = []
    region_lines = []
    for spot in sorted(places, key=lambda spot: (-((spot[0] - 1) // 3), spot[0], spot[1] or 0, spot[2] or 0,
                                                 spot[3] if spot[3] is not None else 1, spot[4])):
        keypad, across, down, alpha, writing = spot
        column, row = (keypad - 1) % 3, (keypad - 1) // 3
        if writing:
            # Down the picture the two swap axes: displayAlign says which
            # side the columns go to, before being the side they start
            # from, and textAlign how far down (see _keypad_down).
            first = 2 if writing == 'vertical-rl' else 0
            display = 'before' if column == first else 'center' if column == 1 else 'after'
            align = ('end', 'center', 'start')[row]
        else:
            display = ('after', 'center', 'before')[row]
            align = ('left', 'center', 'right')[column]
        (x, y), (width, height) = _box(keypad, across, down)
        faded = f' tts:opacity="{alpha:.4g}"' if alpha is not None else ''
        written = f' tts:writingMode="{_WRITING_OUT[writing]}"' if writing else ''
        if profile == 'ebu-tt':
            # A region in EBU-TT Part 1 takes no textAlign of its own: it
            # names a style that holds it, which its text inherits.
            if align not in aligned:
                aligned.append(align)
            aligning = f'tts:displayAlign="{display}" style="align-{align}"'
        else:
            aligning = f'tts:displayAlign="{display}" tts:textAlign="{align}"'
        region_lines.append(f'      <region xml:id="{places[spot]}" '
                            f'tts:origin="{_percent(x)} {_percent(y)}" '
                            f'tts:extent="{_percent(width)} {_percent(height)}" {aligning}{written}{faded}/>')
    style_lines += [f'<style xml:id="align-{align}" tts:textAlign="{align}"/>' for align in aligned]
    head += ['    <styling>', *('      ' + line for line in style_lines), '    </styling>', '    <layout>',
             *region_lines, '    </layout>', '  </head>']
    forced = any(segment.get('forced') for segment in segments) and profile != 'ebu-tt-d'
    imsc_styling = forced or any('itts:' in looking for looking in lookings)
    ebu_styling = any('ebutts:' in looking for looking in lookings)
    root = _root_attributes(profile, language, imsc_styling=imsc_styling, ebu_styling=ebu_styling,
                            fps=fps if not smpte else None, tunnel=bool(tunnels), smpte=smpte)
    # The tunnelled captions, each run in a <div> of its own that begins on
    # its first frame: RP 2052-11's way, which RP 2052-10 shares, of saying
    # where data in the body belongs on the timeline.
    carried = []
    for first, data in tunnels:
        encoded = base64.b64encode(data).decode('ascii')
        lines = '\n'.join('          ' + encoded[i:i + 76] for i in range(0, len(encoded), 76))
        carried += [f'    <div begin="{format_time(first / _CEA608_RATE)}" '
                    f'end="{format_time((first + len(data) // 4) / _CEA608_RATE)}">',
                    '      <metadata>',
                    f'        <smpte:data encoding="Base64" datatype="{_CEA608_DATA}">',
                    lines,
                    '        </smpte:data>',
                    '      </metadata>',
                    '    </div>']
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<tt {root}>',
        *head,
        '  <body style="text">',
        '    <div>',
        *cues,
        '    </div>',
        *carried,
        '  </body>',
        '</tt>',
        ''])


def write_images(pictures, size, language='en', profile='imsc1.1-image', metadata=None):
    """TTML text in one of IMSC's Image Profiles (IMAGE_PROFILES) for
    `pictures`: [{'start', 'end', 'href', 'x', 'y', 'width', 'height', 'alt'
    [, 'forced']}], each a PNG named by `href` relative to the file, at x, y
    on a picture `size` (width, height) pixels, as big as it is, with the
    words `alt` says it shows.

    The profile asks for the picture's size in pixels on <tt>, a region as
    big as the picture it holds and the picture as a <div>'s
    smpte:backgroundImage, never held in the file. A region is shown only
    while it has a picture in it, so the ones not in use count against
    none of the four IMSC allows at once."""
    profile = profile if profile in IMAGE_PROFILES else 'imsc1.1-image'
    width, height = size
    regions = {}
    divs = []
    for picture in sorted(pictures, key=lambda p: (float(p['start']), float(p['end']))):
        box = (int(picture['x']), int(picture['y']), int(picture['width']), int(picture['height']))
        region = regions.setdefault(box, f'picture{len(regions) + 1}')
        start = float(picture['start'])
        end = max(start, float(picture['end']))
        attrs = (f'begin="{format_time(start)}" end="{format_time(end)}" region="{region}" '
                 f'smpte:backgroundImage={quoteattr(picture["href"])}')
        if picture.get('forced'):
            attrs += ' itts:forcedDisplay="true"'
        alt = str(picture.get('alt') or '').strip()
        said = f'<metadata><ittm:altText>{escape(alt)}</ittm:altText></metadata>' if alt else ''
        divs.append(f'    <div {attrs}>{said}</div>' if said else f'    <div {attrs}/>')
    head = ['  <head>']
    meta_lines = []
    for key, name in (('title', 'ttm:title'), ('description', 'ttm:desc'), ('copyright', 'ttm:copyright')):
        said = str((metadata or {}).get(key) or '').strip()
        if said:
            meta_lines.append(f'      <{name}>{escape(said)}</{name}>')
    if meta_lines:
        head += ['    <metadata>', *meta_lines, '    </metadata>']
    head.append('    <layout>')
    for (x, y, w, h), name in regions.items():
        head.append(f'      <region xml:id="{name}" tts:origin="{x}px {y}px" tts:extent="{w}px {h}px" '
                    f'tts:showBackground="whenActive"/>')
    head += ['    </layout>', '  </head>']
    declared = f'ttp:profile="{_IMSC1_IMAGE}"' if profile == 'imsc1-image' else f'ttp:contentProfiles="{_IMSC11_IMAGE}"'
    namespaces = ['xmlns="http://www.w3.org/ns/ttml"', 'xmlns:tts="http://www.w3.org/ns/ttml#styling"',
                  'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"', 'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"',
                  f'xmlns:smpte="{_SMPTE_NS}"', f'xmlns:ittm="{_ITTM_NS}"']
    if any(picture.get('forced') for picture in pictures):
        namespaces.append(f'xmlns:itts="{_ITTS_NS}"')
    root = ' '.join(namespaces + ['ttp:timeBase="media"', declared, f'tts:extent="{int(width)}px {int(height)}px"',
                                  f'xml:lang={quoteattr(export_options.language_tag(language))}'])
    return '\n'.join(['<?xml version="1.0" encoding="UTF-8"?>', f'<tt {root}>', *head,
                      '  <body>', *divs, '  </body>', '</tt>', ''])


def save(path, segments, language='en', agents=False, look='clean', preview=None, profile='ttml1',
         metadata=None, **options):
    """Write `segments` to `path` as TTML, in UTF-8; `options` as write()
    takes them. A DAPT script's recordings are linked from where it is."""
    import os
    if profile == 'dapt':
        options.setdefault('audio_base', os.path.dirname(os.path.abspath(path)))
    with open(path, 'w', encoding='utf-8', newline='\n') as ttml_file:
        ttml_file.write(write(segments, language, agents, look, preview, profile, metadata, **options))
