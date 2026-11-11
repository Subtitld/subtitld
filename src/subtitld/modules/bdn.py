"""BDN XML (.xml): the pictures and times Blu-ray subtitles are authored
from, read and written.

A Blu-ray's subtitles are pictures (PGS, modules.pgs), and the studios'
authoring tools (Scenarist, EasySUP, BDSup2Sub, Subtitle Edit) hand them to
each other as BDN: an XML file named after the Blu-ray Disc Association's
spec, with a PNG beside it for each picture. The XML says what is on screen
when:

    <BDN Version="0.93">
      <Description>
        <Name Title="Film" Content=""/>
        <Language Code="eng"/>
        <Format VideoFormat="1080p" FrameRate="23.976" DropFrame="False"/>
        <Events Type="Graphic" FirstEventInTC="00:00:01:00"
                LastEventOutTC="00:00:04:12" NumberofEvents="2"/>
      </Description>
      <Events>
        <Event InTC="00:00:01:00" OutTC="00:00:02:12" Forced="False">
          <Graphic Width="640" Height="96" X="640" Y="920">Film_0001.png</Graphic>
        </Event>
        ...

- the video's size by name (480i, 480p, 576i, 576p, 720p, 1080i, 1080p;
  Subtitle Edit reads 2160p too, and BDSup2Sub writes 1440x1080 as
  "1440x1080", which Subtitle Edit does for any size without a name);
- its frame rate, one of a Blu-ray's: 23.976, 24, 25, 29.97, 50 or 59.94;
- the language as ISO 639-2's code ("eng", "fre");
- each event's times as HH:MM:SS:FF. At 23.976, 29.97 and 59.94 the frames
  are counted as if the rate were 24, 30 or 60, a label for each frame
  that runs 0.1 % slower than the clock: "00:01:00:00" at 23.976 is the
  1440th frame, a minute and 0.06 seconds in. BDSup2Sub and Subtitle Edit
  agree on it. Where DropFrame is "True" at 29.97 or 59.94, the labels
  skip numbers as drop-frame timecode does (Subtitle Edit reads it so;
  BDSup2Sub, and Subtitld, always write "False");
- each event's pictures, one or two (a Blu-ray shows two at once at
  most), with their size and where they are, in pixels from the top left;
  Forced="True" for the ones shown with subtitles off.

Reading gives the pictures, for an OCR add-on to read (modules.picture_ocr):
a BDN carries no words.

Writing takes pictures drawn as Subtitld's preview draws the subtitles
(interface.subtitle_pictures) and gives the XML that names them. An event
may not overlap another, so where subtitles do, the time is cut where what
is on screen changes and each piece is an event of its own, holding every
picture showing then (the same PNG named again); such an event is forced
when all it shows is. Times are counted at the rate the export asks for.
"""

import math
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

RATES = (23.976, 24.0, 25.0, 29.97, 50.0, 59.94)
DEFAULT_RATE = 23.976
# The video's size for each name VideoFormat may give.
VIDEO_FORMATS = {'480i': (720, 480), '480p': (720, 480), '576i': (720, 576), '576p': (720, 576),
                 '720p': (1280, 720), '1080i': (1920, 1080), '1080p': (1920, 1080), '2160p': (3840, 2160)}
# The names written, as BDSup2Sub writes them: a disc's standard definition
# is interlaced, its high definition progressive.
_WRITTEN_FORMATS = {(720, 480): '480i', (720, 576): '576i', (1280, 720): '720p', (1920, 1080): '1080p'}
DEFAULT_SIZE = (1920, 1080)

_TIMECODE = re.compile(r'^\s*(\d+)[:;.](\d{1,2})[:;.](\d{1,2})[:;.](\d+)\s*$')


def _local(name):
    return name.rsplit('}', 1)[-1] if isinstance(name, str) else ''


def is_bdn(path):
    """Whether the XML file at `path` is a BDN, by its root: .xml is TTML's,
    a DCP's, Captionate's and YouTube's too, and only the root tells them
    apart."""
    try:
        for _event, element in ET.iterparse(path, events=('start',)):
            return _local(element.tag) == 'BDN'
    except (ET.ParseError, OSError):
        return False
    return False


def _float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def rate_for(fps):
    """The Blu-ray frame rate nearest a video's, DEFAULT_RATE without one."""
    fps = _float(fps)
    if fps <= 0:
        return DEFAULT_RATE
    return min(RATES, key=lambda rate: abs(rate - fps))


def _rates(fps):
    """(the rate the labels count in, the true rate): (24, 23.976...) at
    23.976, (25, 25) at 25."""
    fps = _float(fps) or DEFAULT_RATE
    nominal = int(round(fps))
    return nominal, (nominal / 1.001 if abs(fps - nominal) > 0.001 else float(nominal))


def seconds(value, fps=DEFAULT_RATE, drop=False):
    """Seconds for an HH:MM:SS:FF label at `fps`, drop-frame where `drop`
    says so (at 29.97 and 59.94 only). Raises ValueError for a label that
    is none."""
    found = _TIMECODE.match(value or '')
    if not found:
        raise ValueError(f'Not a BDN timecode: "{value}"')
    hours, minutes, whole, frames = (int(part) for part in found.groups())
    nominal, true = _rates(fps)
    number = (hours * 3600 + minutes * 60 + whole) * nominal + frames
    if drop and nominal in (30, 60) and true != nominal:
        # Two (or four) labels skipped at each minute but every tenth.
        total_minutes = hours * 60 + minutes
        number -= nominal // 15 * (total_minutes - total_minutes // 10)
    return number / true


def frames(value, fps=DEFAULT_RATE):
    """The frame `value` seconds is on, at `fps`, the nearest (a half up)."""
    return max(0, math.floor(float(value) * _rates(fps)[1] + 0.5))


def timecode(number, fps=DEFAULT_RATE):
    """HH:MM:SS:FF for frame `number` at `fps`, never drop-frame."""
    nominal = _rates(fps)[0]
    whole, frame = divmod(int(number), nominal)
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{second:02d}:{frame:02d}'


def size_of(video_format):
    """(width, height) for a VideoFormat ('1080p', '1440x1080'), or None."""
    name = str(video_format or '').strip().lower()
    if name in VIDEO_FORMATS:
        return VIDEO_FORMATS[name]
    found = re.fullmatch(r'(\d+)\s*x\s*(\d+)', name)
    if found and int(found.group(1)) > 0 and int(found.group(2)) > 0:
        return int(found.group(1)), int(found.group(2))
    return None


def video_format(size):
    """The VideoFormat for a picture `size` (width, height) pixels: its name
    where it has one, else "WIDTHxHEIGHT"."""
    width, height = int(size[0]), int(size[1])
    return _WRITTEN_FORMATS.get((width, height), f'{width}x{height}')


# Reading.
def parse(data):
    """(pictures, settings) in a BDN given as bytes or text. Each picture is
    {'start', 'end', 'href', 'x', 'y', 'width', 'height', 'forced'}, its
    href the PNG's name as the file gives it (beside the file), its width
    and height what the file says (0 where it says nothing); the settings
    {'title', 'language', 'fps', 'drop', 'size'}: the language as ISO
    639-2's code, the picture's size from VideoFormat (None where it names
    none). Raises ValueError for anything that is not a BDN."""
    if isinstance(data, str):
        data = re.sub(r'^\s*<\?xml[^>]*\?>', '', data.lstrip('﻿')).encode('utf-8')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        line, column = error.position
        raise ValueError(f'Line {line}, column {column + 1}: {error}') from error
    if _local(root.tag) != 'BDN':
        raise ValueError(f'Not a BDN: the document is <{_local(root.tag)}>, not <BDN>')

    def first(parent, name):
        return next((child for child in parent if _local(child.tag) == name), None) if parent is not None else None

    description = first(root, 'Description')
    title = (first(description, 'Name').get('Title') or '').strip() if first(description, 'Name') is not None else ''
    language = first(description, 'Language')
    language = (language.get('Code') or '').strip() if language is not None else ''
    form = first(description, 'Format')
    form = form.attrib if form is not None else {}
    fps = _float(form.get('FrameRate')) or DEFAULT_RATE
    drop = str(form.get('DropFrame') or '').strip().lower() == 'true'
    settings = {'title': title, 'language': language, 'fps': fps, 'drop': drop,
                'size': size_of(form.get('VideoFormat'))}

    pictures = []
    events = first(root, 'Events')
    for event in (events if events is not None else []):
        if _local(event.tag) != 'Event':
            continue
        try:
            start = seconds(event.get('InTC'), fps, drop)
            end = seconds(event.get('OutTC'), fps, drop)
        except ValueError:
            continue
        forced = str(event.get('Forced') or '').strip().lower() == 'true'
        for graphic in event:
            if _local(graphic.tag) != 'Graphic' or not (graphic.text or '').strip():
                continue
            pictures.append({'start': round(start, 3), 'end': round(max(start, end), 3),
                             'href': graphic.text.strip(),
                             'x': int(_float(graphic.get('X'))), 'y': int(_float(graphic.get('Y'))),
                             'width': int(_float(graphic.get('Width'))), 'height': int(_float(graphic.get('Height'))),
                             'forced': forced})
    return pictures, settings


def read(path):
    """(pictures, settings) in the BDN at `path`, as parse() gives them."""
    with open(path, 'rb') as bdn_file:
        return parse(bdn_file.read())


# Writing.
def _events(pictures, fps):
    """[(first frame, frame after the last, [pictures])]: the time cut where
    what is on screen changes, so no two events overlap."""
    timed = []
    for picture in pictures:
        first, last = frames(picture['start'], fps), frames(picture['end'], fps)
        if last > first:
            timed.append((first, last, picture))
    cuts = sorted({first for first, _last, _picture in timed} | {last for _first, last, _picture in timed})
    events = []
    for begin, finish in zip(cuts, cuts[1:]):
        showing = [picture for first, last, picture in timed if first <= begin and last >= finish]
        if showing:
            events.append((begin, finish, showing))
    return events


def write(pictures, size, fps=DEFAULT_RATE, language='en', title=''):
    """BDN XML for `pictures`: [{'start', 'end', 'href', 'x', 'y', 'width',
    'height'[, 'forced']}], each a PNG named by `href` relative to the file,
    at x, y on a picture `size` (width, height) pixels, in `language` (a
    code as Subtitld names it), for the film called `title`, its times at
    `fps` (one of RATES, else the nearest)."""
    from subtitld.modules import usf
    fps = fps if fps in RATES else rate_for(fps)
    events = _events(sorted(pictures, key=lambda p: (float(p['start']), float(p['end']))), fps)
    lines = []
    for begin, finish, showing in events:
        forced = 'True' if all(picture.get('forced') for picture in showing) else 'False'
        lines.append(f'    <Event InTC="{timecode(begin, fps)}" OutTC="{timecode(finish, fps)}" Forced="{forced}">')
        for picture in showing:
            lines.append(f'      <Graphic Width="{int(picture["width"])}" Height="{int(picture["height"])}" '
                         f'X="{max(0, int(picture["x"]))}" Y="{max(0, int(picture["y"]))}">'
                         f'{escape(str(picture["href"]))}</Graphic>')
        lines.append('    </Event>')
    first_in = timecode(events[0][0], fps) if events else timecode(0, fps)
    last_out = timecode(events[-1][1], fps) if events else timecode(0, fps)
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<BDN Version="0.93" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="BD-03-006-0093b BDN File Format.xsd">',
        '  <Description>',
        f'    <Name Title={quoteattr(title or "")} Content=""/>',
        f'    <Language Code={quoteattr(usf.iso639_2(language) or "eng")}/>',
        f'    <Format VideoFormat="{video_format(size)}" FrameRate="{fps:g}" DropFrame="False"/>',
        f'    <Events Type="Graphic" FirstEventInTC="{first_in}" LastEventOutTC="{last_out}" '
        f'NumberofEvents="{len(events)}"/>',
        '  </Description>',
        '  <Events>', *lines, '  </Events>',
        '</BDN>', ''])


def save(path, pictures, size, **options):
    """Write a BDN for `pictures` to `path`, in UTF-8; `options` as write()
    takes them."""
    with open(path, 'w', encoding='utf-8', newline='\n') as bdn_file:
        bdn_file.write(write(pictures, size, **options))
