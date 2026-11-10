"""Final Cut Pro's XML (.fcpxml, and the .fcpxmld bundle that holds one):
read the captions a project carries, write a project of captions Final
Cut Pro imports.

FCPXML describes a whole library, and subtitles are a small part of it.
Since version 1.8 (Final Cut Pro 10.4.1) a project's captions are
<caption> elements anchored above the clips of its timeline, each with a
role that names its format and language ("iTT?captionFormat=ITT.en",
"CEA-608?captionFormat=CEA608.fr-CA", and SRT in later versions), its
text in a <text> of <text-style> runs, and the runs' looks in
<text-style-def>s beside it. Titles, the text drawn into the picture, are
<title> elements with the same kind of <text>.

Times are rational numbers of seconds ("48048/24000s", "5s"), and an
element's offset is a time on its parent's timeline, which starts where
the parent's own `start` says: a caption anchored to a clip at "10s" whose
start is "3600s" and whose offset is "3602s" is two seconds into that clip.
A storyline's elements follow one another, so one with no offset starts
where the one before it ends. The project's timeline starts at its
sequence's tcStart, often 01:00:00:00, which is Subtitld's 0.

Reading:

- every caption of the first project that has any, where its parents put
  it, the disabled ones (and those inside a disabled clip) left out; a
  project with no captions gives its titles instead;
- the first role's captions are the subtitles, in the language its role
  names; those of other languages become their translations, each matched
  to the subtitle it overlaps most, as SAMI's are. Captions in the same
  language in another format (CEA-608 beside ITT) are a copy of them for
  another kind of player, and are left out;
- italic, bold, underline, the colour of the text and of the box behind it
  become formatting tags. White text and an opaque black box are what a
  caption is unless it says otherwise, so they are no formatting;
- an ITT caption's placement (top, bottom, left, right) and a CEA-608
  caption's position (its row of fifteen) and alignment become its place
  on the keypad; a run's alignment, the side for a title or ITT caption;
- whitespace between the runs, which Final Cut Pro writes to indent the
  file, is not text; a line separator (U+2028) is a line break.

Writing gives a library with one event and one project: a sequence at the
export's frame rate (the video's) and size, from 00:00:00:00 or
01:00:00:00, drop-frame at 29.97 and 59.94 unless asked otherwise, whose
storyline is one gap as long as the subtitles, with each subtitle an ITT
caption anchored to it, its times on the frames Final Cut Pro edits on.
Each language written is a role of its own, so a translation can go
beside the original as a second caption language. Captions that overlap
go on lanes of their own. FCPXML 1.10 is written (Final Cut Pro 10.6 and
later, DaVinci Resolve 17 and later); the elements used are the same
since 1.8.
"""

import os
import xml.etree.ElementTree as ET
from fractions import Fraction

from subtitld.modules import avid_subcap, export_options, markup
from subtitld.modules.srt import decode

VERSION = '1.10'
DEFAULT_FPS = 25.0
STARTS = avid_subcap.STARTS
# The bundle's file: an .fcpxmld is a folder with the document in it.
BUNDLE_DOCUMENT = 'Info.fcpxml'

# A frame as Final Cut Pro writes it, numerator and denominator.
_FRAME_DURATIONS = {
    23.976: (1001, 24000), 24.0: (100, 2400), 25.0: (100, 2500), 29.97: (1001, 30000),
    30.0: (100, 3000), 50.0: (100, 5000), 59.94: (1001, 60000), 60.0: (100, 6000),
}
_FORMAT_RATES = {23.976: '2398', 24.0: '24', 25.0: '25', 29.97: '2997', 30.0: '30', 50.0: '50', 59.94: '5994',
                 60.0: '60'}
_FORMAT_SIZES = {(1920, 1080): '1080p', (1280, 720): '720p', (3840, 2160): '3840x2160p', (4096, 2160): '4096x2160p'}
# Items with a place on a timeline, and those that hold others.
_TIMED = ('clip', 'asset-clip', 'ref-clip', 'sync-clip', 'mc-clip', 'audio', 'video', 'gap', 'title', 'caption',
          'audition', 'spine')
_WHITE = (1.0, 1.0, 1.0)
_BLACK = (0.0, 0.0, 0.0, 1.0)


def is_fcpxml(data):
    """Whether `data` (bytes or text) is a Final Cut Pro XML document."""
    head = decode(data[:4096]) if isinstance(data, (bytes, bytearray)) else str(data or '')[:4096]
    return '<fcpxml' in head


def _time(value):
    """A Fraction of seconds for an FCPXML time ("1001/30000s", "5s"), or
    None for none."""
    value = (value or '').strip()
    if not value.endswith('s'):
        return None
    try:
        return Fraction(value[:-1])
    except (ValueError, ZeroDivisionError):
        return None


def _rate_of(frame_duration):
    """The frame rate a format's frameDuration gives: 29.97 for 1001/30000s."""
    length = _time(frame_duration)
    if not length:
        return None
    fps = float(1 / length)
    return min(_FRAME_DURATIONS, key=lambda known: abs(known - fps)) if any(
        abs(known - fps) < 0.01 for known in _FRAME_DURATIONS) else round(fps, 3)


def _color(value):
    """(r, g, b, a) in 0..1 for an FCPXML colour ("1 0.5 0 1"), or None."""
    try:
        parts = [float(part) for part in (value or '').split()]
    except ValueError:
        return None
    if len(parts) == 3:
        parts.append(1.0)
    return tuple(max(0.0, min(1.0, part)) for part in parts) if len(parts) == 4 else None


def _hex(color, alpha=False):
    digits = ''.join(f'{round(part * 255):02x}' for part in color[:3])
    if alpha and color[3] < 1:
        digits += f'{round(color[3] * 255):02x}'
    return '#' + digits


def _rgba(value, alpha=1.0):
    """FCPXML's "r g b a" for a markup colour ('#rrggbb' or '#rrggbbaa')."""
    digits = (value or '').lstrip('#')
    parts = [int(digits[index:index + 2], 16) / 255 for index in range(0, len(digits), 2)]
    if len(parts) == 3:
        parts.append(alpha)
    return ' '.join(f'{part:.6g}' for part in parts[:4])


def _style_of(attributes):
    """(markup.Style, alignment) for a text-style's attributes."""
    color = _color(attributes.get('fontColor'))
    background = _color(attributes.get('backgroundColor'))
    style = markup.Style(
        italic=attributes.get('italic') == '1', bold=attributes.get('bold') == '1',
        underline=attributes.get('underline') == '1',
        color=_hex(color) if color and color[:3] != _WHITE else '',
        background=_hex(background, alpha=True) if background and background != _BLACK else '')
    return style, attributes.get('alignment') or ''


def _text_of(element, definitions):
    """(text with tags, alignment) of a caption's or a title's <text>s."""
    runs = []
    alignment = ''
    for number, text in enumerate(element.findall('text')):
        if number and runs:
            runs.append(('\n', markup.PLAIN))
        # Whitespace alone between runs is the file's indentation.
        if text.text and text.text.strip():
            runs.append((text.text, markup.PLAIN))
        for run in text:
            if run.tag == 'text-style':
                attributes = {}
                defined = definitions.get(run.get('ref') or '')
                if defined is not None:
                    attributes.update(defined.attrib)
                attributes.update({key: value for key, value in run.attrib.items() if key != 'ref'})
                style, said = _style_of(attributes)
                alignment = alignment or said
                if run.text:
                    runs.append((run.text, style))
            if run.tail and run.tail.strip():
                runs.append((run.tail, markup.PLAIN))
    runs = [(text.replace(' ', '\n').replace('\r\n', '\n'), style) for text, style in runs]
    return markup.tidy(runs), alignment


def _keypad(element, alignment):
    """The keypad place of a caption: its ITT placement or CEA-608 row, its
    side from its alignment."""
    text = element.find('text')
    text = text if text is not None else ET.Element('text')
    column = {'left': 0, 'right': 2}.get(text.get('alignment') or alignment, 1)
    row = 0                                                 # bottom
    placement = text.get('placement')
    if placement == 'top':
        row = 2
    elif placement in ('left', 'right'):
        return 4 if placement == 'left' else 6
    elif text.get('position'):
        try:
            line = int(float(text.get('position').split()[1]))
        except (IndexError, ValueError):
            line = 15
        row = 2 if line <= 5 else (1 if line <= 10 else 0)
    return 1 + row * 3 + column


class _Walker:
    """Every caption and title of a timeline, where its parents put it."""

    def __init__(self, definitions):
        self.definitions = definitions
        self.captions = []          # (start, end, role, text)
        self.titles = []

    def storyline(self, spine, shift):
        """The items of a storyline whose own time `t` is `t + shift`."""
        running = Fraction(0)
        for child in spine:
            if child.tag not in _TIMED:
                continue
            offset = _time(child.get('offset'))
            offset = running if offset is None else offset
            self.item(child, offset + shift)
            duration = _time(child.get('duration'))
            running = offset + (duration or 0)

    def item(self, element, at):
        """An item that starts at `at`, and what is anchored to it or inside it."""
        if element.get('enabled') == '0':
            return
        duration = _time(element.get('duration')) or Fraction(0)
        if element.tag in ('caption', 'title'):
            text, alignment = _text_of(element, self.definitions)
            if markup.plain(text).strip():
                text = markup.with_alignment('', _keypad(element, alignment)) + text
                found = (at, at + duration, element.get('role') or '', text)
                (self.captions if element.tag == 'caption' else self.titles).append(found)
        if element.tag == 'audition':
            # Only the first of an audition's clips is in the timeline.
            first = next((child for child in element if child.tag in _TIMED), None)
            if first is not None:
                self.item(first, at)
            return
        # What is in it is on its own timeline, which begins at its start.
        shift = at - (_time(element.get('start')) or 0)
        for child in element:
            if child.tag == 'spine':
                # A storyline anchored here: where it sits, and its items in
                # turn from its first.
                first = next((_time(item.get('offset')) for item in child if item.tag in _TIMED), None)
                origin = (_time(child.get('offset')) or 0) + shift
                self.storyline(child, origin - (first or 0))
            elif child.tag in _TIMED:
                offset = _time(child.get('offset'))
                self.item(child, shift + (offset or 0))


def _role(role):
    """(format, language) a caption role names: ('ITT', 'en') for
    "iTT?captionFormat=ITT.en"."""
    said = role.split('captionFormat=', 1)[1] if 'captionFormat=' in role else ''
    kind, _dot, language = said.partition('.')
    return kind or 'ITT', language


def _overlap(segment, start, end):
    return min(segment['end'], end) - max(segment['start'], start)


def _segments(found):
    return [{'start': round(float(start), 3), 'end': round(float(max(start, end)), 3), 'text': text}
            for start, end, _role_name, text in sorted(found, key=lambda item: (item[0], item[1]))]


def parse(data):
    """(subtitles, settings) in an FCPXML document: [{'start', 'end', 'text'
    [, 'translations']}] by start, and {'fps', 'drop', 'start', 'languages'},
    the frame rate of its sequence, whether its timecode is drop-frame, the
    timecode it starts at (one of STARTS, else the first) and the languages
    read, the subtitles' first."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f'Not a Final Cut Pro XML file: {error}') from error
    if root.tag != 'fcpxml':
        raise ValueError('Not a Final Cut Pro XML file')
    definitions = {}
    for definition in root.iter('text-style-def'):
        style = definition.find('text-style')
        if definition.get('id') and style is not None:
            definitions[definition.get('id')] = style
    formats = {element.get('id'): element for element in root.iter('format')}

    chosen = None
    sequences = [project.find('sequence') for project in root.iter('project')]
    for sequence in [sequence for sequence in sequences if sequence is not None]:
        walker = _Walker(definitions)
        tc_start = _time(sequence.get('tcStart')) or Fraction(0)
        spine = sequence.find('spine')
        if spine is not None:
            walker.storyline(spine, -tc_start)
        if walker.captions or (walker.titles and chosen is None):
            chosen = (sequence, walker)
        if walker.captions:
            break
    if chosen is None:
        return [], {'fps': None, 'drop': False, 'start': STARTS[0], 'languages': []}
    sequence, walker = chosen

    found_format = formats.get(sequence.get('format'))
    fps = _rate_of(found_format.get('frameDuration')) if found_format is not None else None
    drop = sequence.get('tcFormat') == 'DF'
    nominal, frame_length = avid_subcap.rate(fps or DEFAULT_FPS)
    tc_start = _time(sequence.get('tcStart')) or Fraction(0)
    hour = avid_subcap.frame_of(1, 0, 0, 0, nominal, drop and nominal in (30, 60)) * frame_length
    settings = {'fps': fps, 'drop': drop, 'start': STARTS[1] if abs(tc_start - hour) < frame_length / 2 else STARTS[0]}

    if not walker.captions:
        settings['languages'] = []
        return _segments(walker.titles), settings
    roles = []
    for _start, _end, role, _text in walker.captions:
        if role not in roles:
            roles.append(role)
    _kind, first_language = _role(roles[0])
    segments = _segments([caption for caption in walker.captions if caption[2] == roles[0]])
    languages = [first_language]
    for role in roles[1:]:
        _kind, language = _role(role)
        if language.lower() == first_language.lower() or language in languages[1:]:
            continue                # the same words for another kind of player
        languages.append(language)
        for start, end, _role_name, text in [caption for caption in walker.captions if caption[2] == role]:
            start, end = float(start), float(end)
            best = max(segments, key=lambda segment: _overlap(segment, start, end), default=None)
            if best is None or _overlap(best, start, end) <= 0:
                continue
            translations = best.setdefault('translations', {})
            translations[language] = (translations[language] + '\n' + text) if language in translations else text
    settings['languages'] = languages
    return segments, settings


def read(path):
    """(subtitles, settings) in the FCPXML document at `path`, or in the
    bundle (.fcpxmld) that is `path`, as parse() gives them."""
    if os.path.isdir(path):
        path = os.path.join(path, BUNDLE_DOCUMENT)
    with open(path, 'rb') as fcpxml_file:
        return parse(fcpxml_file.read())


def _frame_duration(fps):
    fps = float(fps or DEFAULT_FPS)
    known = min(_FRAME_DURATIONS, key=lambda rate: abs(rate - fps))
    if abs(known - fps) < 0.01:
        return known, _FRAME_DURATIONS[known]
    whole = max(1, round(fps))
    return float(whole), (100, 100 * whole)


def _plain_name(text):
    return ' '.join(line.strip() for line in markup.plain(text).split('\n') if line.strip())


def _style_attributes(style):
    attributes = {'fontFace': 'Regular',
                  'fontColor': _rgba(markup.color_hex(style.color)) if markup.color_hex(style.color) else '1 1 1 1',
                  'backgroundColor': _rgba(style.background) if style.background else '0 0 0 1'}
    for name in ('italic', 'bold', 'underline'):
        if getattr(style, name):
            attributes[name] = '1'
    return attributes


def write(tracks, fps=DEFAULT_FPS, drop=True, start=STARTS[0], title='Subtitles', width=1920, height=1080):
    """FCPXML text for `tracks`, [(language, segments)]: each language a
    caption role of its own, on the frames of `fps` (drop-frame timecode
    when `drop` and the rate has it), the timeline starting at `start`."""
    fps, (numerator, denominator) = _frame_duration(fps)
    nominal, _length = avid_subcap.rate(fps)
    drop = bool(drop) and numerator == 1001 and nominal in (30, 60)

    def frames(seconds):
        return max(0, int(round(Fraction(float(seconds)).limit_denominator(10 ** 6) * denominator / numerator)))

    def when(count):
        value = count * numerator
        return f'{value // denominator}s' if value % denominator == 0 else f'{value}/{denominator}s'

    origin = avid_subcap.frame_of(1, 0, 0, 0, nominal, drop) if start == STARTS[1] else 0
    captions = []                                   # (first, track, last, language, segment)
    for track, (language, segments) in enumerate(tracks):
        for segment in segments:
            first = frames(segment.get('start', 0))
            last = max(first + 1, frames(segment.get('end', 0)))
            if markup.plain(str(segment.get('text') or '')).strip():
                captions.append((first, track, last, language, segment))
    # The first language's caption first where two start together: a
    # reader takes the first role it meets for the subtitles' language.
    captions.sort(key=lambda caption: caption[:3])
    length = max([caption[2] for caption in captions] + [1])

    root = ET.Element('fcpxml', version=VERSION)
    resources = ET.SubElement(root, 'resources')
    width, height = int(width or 1920), int(height or 1080)
    name = _FORMAT_SIZES.get((width, height))
    format_attributes = {'id': 'r1'}
    if name and fps in _FORMAT_RATES:
        format_attributes['name'] = f'FFVideoFormat{name}{_FORMAT_RATES[fps]}'
    format_attributes.update(frameDuration=f'{numerator}/{denominator}s', width=str(width), height=str(height),
                             colorSpace='1-1-1 (Rec. 709)')
    ET.SubElement(resources, 'format', format_attributes)
    library = ET.SubElement(root, 'library')
    event = ET.SubElement(library, 'event', name='Subtitld')
    project = ET.SubElement(event, 'project', name=title or 'Subtitles')
    sequence = ET.SubElement(project, 'sequence', {
        'format': 'r1', 'duration': when(length), 'tcStart': when(origin), 'tcFormat': 'DF' if drop else 'NDF',
        'audioLayout': 'stereo', 'audioRate': '48k'})
    spine = ET.SubElement(sequence, 'spine')
    gap = ET.SubElement(spine, 'gap', {'name': 'Gap', 'offset': when(origin), 'start': '0s', 'duration': when(length)})

    lanes = []                                      # the frame each lane is free from
    style_number = 0
    for first, _track, last, language, segment in captions:
        lane = next((number for number, free in enumerate(lanes) if free <= first), None)
        if lane is None:
            lanes.append(0)
            lane = len(lanes) - 1
        lanes[lane] = last
        lead, body = markup.split_lead(str(segment.get('text') or ''))
        keypad = markup.alignment(lead)
        runs = [(text, style) for text, style in markup.without_readings(markup.spans(body)) if text]
        caption = ET.SubElement(gap, 'caption', {
            'name': _plain_name(body), 'lane': str(lane + 1), 'offset': when(first), 'duration': when(last - first),
            'role': f'iTT?captionFormat=ITT.{export_options.language_tag(language)}'})
        text = ET.SubElement(caption, 'text', placement={7: 'top', 8: 'top', 9: 'top', 4: 'left', 6: 'right'}.get(
            keypad, 'bottom'))
        alignment = {1: 'left', 7: 'left', 3: 'right', 9: 'right'}.get(keypad)
        ids = {}
        definitions = []
        for content, style in runs:
            attributes = _style_attributes(style)
            if alignment:
                attributes['alignment'] = alignment
            key = tuple(sorted(attributes.items()))
            if key not in ids:
                style_number += 1
                ids[key] = f'ts{style_number}'
                definition = ET.Element('text-style-def', id=ids[key])
                ET.SubElement(definition, 'text-style', attributes)
                definitions.append(definition)
            ET.SubElement(text, 'text-style', ref=ids[key]).text = content.replace('\r\n', '\n')
        caption.extend(definitions)

    ET.indent(root, space='    ')
    # Indenting would put whitespace between the runs of a text, which is
    # where its words are.
    for text in root.iter('text'):
        text.text = None
        for run in text:
            run.tail = None
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n'
            + ET.tostring(root, encoding='unicode') + '\n')


def save(path, tracks, fps=DEFAULT_FPS, drop=True, start=STARTS[0], title='Subtitles', width=1920, height=1080):
    """Write `tracks` to `path` as FCPXML; a path ending in .fcpxmld is a
    bundle, a folder with the document in it."""
    if path.lower().rstrip('/\\').endswith('.fcpxmld'):
        os.makedirs(path, exist_ok=True)
        path = os.path.join(path, BUNDLE_DOCUMENT)
    with open(path, 'w', encoding='utf-8', newline='\n') as fcpxml_file:
        fcpxml_file.write(write(tracks, fps, drop, start, title, width, height))
