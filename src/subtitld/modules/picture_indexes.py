"""Picture subtitles kept as image files and an index that times them, read
and written: the scripts DVD and video tools import subtitles drawn as
pictures from.

None of these is written down by its maker. Subtitle Edit has collected
their layouts (src/libse/SubtitleFormats in its source, the readers named
below; src/libuilogic/Export for the writers it has), and the rest is
named where it is followed:

- **Scenarist bitmap script** (.sst, key 'SCENARISTBMP'; Subtitle Edit's
  SonicScenaristBitmaps): Scenarist's subtitle script, whose subtitles are
  image files (BMP, TIFF) where the text script (dvd_scripts) has words.
  A header of settings (st_format, Display_Start, TV_Type, Tape_Type,
  Pixel_Area, Display_Area, Color, Contrast, and PA, E1, E2 and BG, the
  colour in the images of each of a DVD subpicture's four pixel kinds:
  pattern, emphasis 1 and 2, background), "Directory" for where the images
  are, then "0001<tab>00:00:01:00<tab>00:00:03:00<tab>name.bmp" for each.
  Color and Contrast list the palette entries and opacities of the four
  kinds as an SPU's commands do, E2, E1, PA, BG: the headers people post
  have the background's contrast last and 0 (forum.doom9.org t-31906),
  which is the only source for the order.
- **DVD Maestro / Spruce SON** (.son, 'SON'; Subtitle Edit's Son): the
  same header, Display_Area and Color and Contrast also between the
  subtitles, and "0001<tab>00:00:19:13<tab>00:00:22:10<tab>a_0001.tif".
- **DOST** (.dost, 'DOST'; Subtitle Edit's Dost reader and
  ExportHandlerDost): "$FORMAT=480", "$VERSION=1.2", "$ULEAD=TRUE",
  "$DROP=" the frame rate times 1000, a column header, then a tab-separated
  line for each: number, in, out, x, y, the PNG's name, fade in and out.
- **spumux** (.xml, 'SPUMUX'; Subtitle Edit's "SPU Image", dvdauthor's
  spumux(1) and its subgen-parse-xml.c and spuunmux.c):
  <subpictures><stream><spu start="00:00:01.00" end="00:00:03.00"
  image="a.png" xoffset="x" yoffset="y" force="yes"/>. Times are
  [[hh:]mm:]ss[.fraction]; spuunmux writes hundredths, as Subtitld does.
- **Timed Images XML** (.xml, 'TIMEDIMAGES'; Subtitle Edit's
  TimedImagesXml, which reads it only): <TimedImages targetWidth
  targetHeight aspectRatio> and <I s="0.600" e="3.720" x y w h i="a.png"/>
  for each, times in seconds.
- **Final Cut Pro image XML** (.xml, 'FCPIMAGE'; Subtitle Edit's
  FinalCutProImage reader and ExportHandlerFcp): Final Cut Pro 7's xmeml,
  a sequence with a video track of still clips, each a PNG (<file> with
  <name> and <pathurl>) from <start> to <end> in frames of the sequence's
  <rate> (<timebase>, and <ntsc> TRUE for the 1000/1001 rates). A clip
  has no position, so the PNGs are as big as the frame (Subtitle Edit's
  "full frame"); one smaller is taken as Final Cut Pro places it, centred.
- **Rhozet Harmonic image** (.xml, 'RHOZET'; Subtitle Edit's
  RhozetHarmonicImage): <TitlerData><Data StartTimecode='00:00:55:02'
  EndTimecode='00:00:56:19' Image='name.png'/>, timecodes in frames at a
  rate the file does not give (the video's, else 25). Subtitle Edit takes
  "filepath\\" off an image's name; its PNGs are written as big as the
  frame here, nothing saying where a smaller one goes.

Times as HH:MM:SS:FF count frames at the nominal rate (24, 30 or 60 for
23.976, 29.97 and 59.94), as a timecode does and Subtitle Edit's writers
count them (bdn's timecodes); a ";" before the frames is drop-frame.

Reading gives the pictures for an OCR add-on (picture_ocr): `read` returns
picture_subtitles.Pictures. An image without an alpha channel (a BMP, a
TIFF) has the background shown as a colour: the header's BG where it
gives one, else the colour most of the image's border is, is taken as
transparent.

Writing takes the subtitles drawn as the preview draws them (dicts with
'rgba', as disc_pictures takes them), one on screen at a time
(disc_pictures.timeline), and writes the index and the images beside it,
named after it ("film_0001.png"); the images an export before left by
those names go first:

- Scenarist and SON: BMPs as big as the frame, in four colours, the same
  four for the whole film (cluster on the colours of every subtitle): the
  background white, the pattern (the colour covering most) black, the
  emphases red and blue, as the posted headers have them; Color the entries
  of BDSup2Sub's default DVD palette nearest each, Contrast their
  opacities. The frame is DVD's PAL or NTSC (vobsub_size), its rate PAL's
  25 or NTSC's 29.97.
- DOST and spumux: each picture cut to what shows, at its place; spumux's
  in the four colours a DVD subtitle has (as spumux takes them), with their
  opacities. On DVD's frame too.
- Timed Images, Final Cut Pro and Rhozet: PNGs in true colour, cut to what
  shows for Timed Images, as big as the frame for the other two, on the
  video's frame (else 1920x1080) at its rate (else 25).
"""

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import bdn, disc_pictures
from subtitld.modules.picture_subtitles import Picture

# The session's keys, and the extension each is written with.
FORMATS = {'SCENARISTBMP': 'sst', 'SON': 'son', 'DOST': 'dost', 'SPUMUX': 'xml', 'TIMEDIMAGES': 'xml',
           'FCPIMAGE': 'xml', 'RHOZET': 'xml'}
# Those placed on a DVD's frame.
DVD_FORMATS = ('SCENARISTBMP', 'SON', 'DOST', 'SPUMUX')
DEFAULT_DURATION = 5.0          # a spumux subtitle that never says when it ends
DEFAULT_FPS = 25.0
DEFAULT_SIZE = (1920, 1080)
# Frames a picture is laid on when the file does not say, the first that holds them all.
_FRAMES = ((720, 480), (720, 576), (1280, 720), (1920, 1080), (3840, 2160))
# The colours of the four pixel kinds in Subtitld's Scenarist and SON images.
KEYS = {'BG': (255, 255, 255), 'PA': (0, 0, 0), 'E1': (255, 0, 0), 'E2': (0, 0, 255)}

_TC = r'(\d{1,2}[:;.]\d\d[:;.]\d\d[:;.,]\d\d)'
_SCRIPT_LINE = re.compile(r'^\s*(\d{1,5})\s+' + _TC + r'\s+' + _TC + r'\s+(.+?\.(?:tif|tiff|png|bmp))\s*$', re.I)
_DOST_LINE = re.compile(r'^(\d{1,5})\t' + _TC + r'\t' + _TC + r'\t(-?\d+)\t(-?\d+)\t([^\t]+)')
_SETTING = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(.*?)\s*$')
_NUMBERS = re.compile(r'-?\d+')


class PictureIndexError(ValueError):
    """Not an index of pictures Subtitld can read."""


# Telling them apart --------------------------------------------------------

def _text(data):
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data)
        for encoding in ('utf-8-sig', 'cp1252'):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode('latin-1')
    return str(data or '')


def looks_like(head, extension=''):
    """Which of these `head` (the file's first bytes or text, 64 KB is
    plenty) is: a key of FORMATS, or ''. `extension` ('sst', 'son',
    'dost', 'xml') narrows it, as the text formats sharing .sst and .xml
    have their own sniffing; without it the content alone decides."""
    text = _text(head).lstrip('﻿')
    extension = str(extension or '').lower().lstrip('.')
    if extension in ('', 'xml'):
        start = text[:4096]
        if re.search(r'<subpictures\b', start) and re.search(r'<spu\b', text):
            return 'SPUMUX'
        if re.search(r'<TimedImages\b', start) and ' i="' in text:
            return 'TIMEDIMAGES'
        if re.search(r'<TitlerData\b', start) and re.search(r'<Data\b', text) and 'Image=' in text:
            return 'RHOZET'
        if '<xmeml' in start and '<pathurl>' in text and \
                re.search(r'<name>[^<]*\.(png|bmp|tif|tiff|jpg)</name>', text, re.I):
            return 'FCPIMAGE'
        if extension == 'xml':
            return ''
    if extension in ('', 'dost') and '$FORMAT' in text and re.search(r'(^|\n)NO\tINTIME', text) and \
            any(_DOST_LINE.match(line) for line in text.splitlines()):
        return 'DOST'
    if extension in ('', 'sst', 'son'):
        lines = [line for line in text.replace('\r', '\n').split('\n') if line.strip()]
        events = [line for line in lines if re.match(r'^\s*\d{1,5}\s+\d', line)]
        if events and all(_SCRIPT_LINE.match(line) for line in events):
            if extension == 'son':
                return 'SON'
            if extension == 'sst':
                return 'SCENARISTBMP'
            # By content alone: a SON's events are tab-separated, Display_Area may come between them.
            return 'SON' if all('\t' in line for line in events) and \
                any(line.lstrip().startswith('Display_Area') for line in lines[lines.index(events[0]):]) \
                else 'SCENARISTBMP'
    return ''


def kind(path):
    """Which of these the file at `path` is, by its extension and its
    bytes: a key of FORMATS, or ''."""
    extension = os.path.splitext(str(path))[1].lower().lstrip('.')
    if extension not in ('sst', 'son', 'dost', 'xml'):
        return ''
    try:
        with open(path, 'rb') as handle:
            return looks_like(handle.read(65536), extension)
    except OSError:
        return ''


# Times ---------------------------------------------------------------------

def _seconds(label, fps):
    """Seconds for HH:MM:SS:FF at `fps`, drop-frame for a ";"."""
    return bdn.seconds(label.replace(',', ':'), fps, drop=';' in label)


def _timecode(seconds, fps):
    return bdn.timecode(bdn.frames(seconds, fps), fps)


def spumux_seconds(value):
    """Seconds for a spumux time, [[hh:]mm:]ss[.fraction] (dvdauthor's
    parsetime)."""
    value = str(value or '').strip().replace(',', '.')
    whole, _dot, fraction = value.partition('.')
    total = 0
    for part in whole.split(':'):
        total = total * 60 + int(part or 0)
    return total + (int(fraction) / 10 ** len(fraction) if fraction.isdigit() else 0.0)


def spumux_time(seconds):
    """HH:MM:SS.cc, as spuunmux writes a time."""
    hundredths = max(0, int(round(seconds * 100)))
    return (f'{hundredths // 360000:02d}:{hundredths // 6000 % 60:02d}:{hundredths // 100 % 60:02d}'
            f'.{hundredths % 100:02d}')


# Reading -------------------------------------------------------------------

def _numbers(value):
    return [int(number) for number in _NUMBERS.findall(str(value or ''))]


def _fitting_frame(entries, fallback=None):
    """The first of the usual frames that holds every placed picture."""
    if fallback:
        return fallback
    right = max([e['x'] + e.get('width', 0) for e in entries if e.get('x') is not None] + [0])
    bottom = max([e['y'] + e.get('height', 0) for e in entries if e.get('y') is not None] + [0])
    return next((frame for frame in _FRAMES if frame[0] >= right and frame[1] >= bottom), (right, bottom))


def _parse_script(text, fps, which):
    """A Scenarist or SON script: (entries, settings)."""
    settings = {'tv': '', 'drop': False, 'display': None, 'pixel': None, 'keys': {}, 'directory': '',
                'forced': False}
    entries = []
    display = None
    shown = {}
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        event = _SCRIPT_LINE.match(line)
        if event:
            entries.append({'start': event.group(2), 'end': event.group(3), 'href': event.group(4).strip(),
                            'display': display, 'color': shown.get('color'), 'contrast': shown.get('contrast')})
            continue
        setting = _SETTING.match(line)
        if not setting:
            continue
        name, value = setting.group(1), setting.group(2)
        lowered = name.lower()
        if lowered == 'tv_type':
            settings['tv'] = value.split()[0].upper() if value.split() else ''
        elif lowered == 'tape_type':
            settings['drop'] = value.strip().upper() == 'DROP'
        elif lowered == 'display_start':
            settings['forced'] = value.strip().lower() == 'forced'
        elif lowered == 'display_area':
            found = _numbers(value)
            if len(found) == 4:
                display = found
                if settings['display'] is None:
                    settings['display'] = found
        elif lowered == 'pixel_area':
            found = _numbers(value)
            if len(found) == 2:
                settings['pixel'] = found
        elif lowered in ('color', 'contrast'):
            found = _numbers(value)
            if len(found) == 4:
                shown[lowered] = found
        elif name in KEYS:
            found = _numbers(value)
            if len(found) >= 3:
                settings['keys'][name] = tuple(max(0, min(255, v)) for v in found[:3])
        elif lowered == 'directory':
            settings['directory'] = value.strip()
    rate = 29.97 if settings['tv'].startswith('NTSC') else 25.0 if settings['tv'].startswith('PAL') else fps
    settings['fps'] = rate
    lines = settings['pixel'] or (settings['display'][1::2] if settings['display'] else None)
    if settings['tv'].startswith('NTSC') or (not settings['tv'] and lines and lines[1] < 480):
        settings['size'] = (720, 480)
    else:
        settings['size'] = (720, 576)
    found = []
    for entry in entries:
        try:
            start = _seconds(entry['start'], rate)
            end = _seconds(entry['end'], rate)
        except ValueError:
            continue
        area = entry['display']
        found.append({'start': round(start, 3), 'end': round(max(start, end), 3), 'href': entry['href'],
                      'x': area[0] if area else None, 'y': area[1] if area else None,
                      'forced': settings['forced'], 'shown': _shown_kinds(settings['keys'], entry)})
    return found, settings


def _shown_kinds(keys, entry):
    """{key colour: (r, g, b, a)}: what a DVD player shows for each of the
    image's four key colours, from the Color (palette entries) and Contrast
    (opacities, 0 to 15) the script gives them in the order E2, E1, PA, BG;
    None where it gives neither or a key is missing."""
    from subtitld.modules import dvdsup
    if not entry.get('color') or not entry.get('contrast') or any(name not in keys for name in ('E2', 'E1', 'PA', 'BG')):
        return None
    return {keys[name]: tuple(dvdsup.DEFAULT_PALETTE[colour % 16][:3]) + (min(15, max(0, contrast)) * 17,)
            for name, colour, contrast in zip(('E2', 'E1', 'PA', 'BG'), entry['color'], entry['contrast'])}


def _parse_dost(text, fps):
    rate = fps or DEFAULT_FPS
    declared = re.search(r'^\$DROP=(\d+)', text, re.M)
    if declared and 10 < int(declared.group(1)) / 1000 < 500:
        rate = int(declared.group(1)) / 1000
    found = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        event = _DOST_LINE.match(line)
        if not event:
            continue
        try:
            start, end = _seconds(event.group(2), rate), _seconds(event.group(3), rate)
        except ValueError:
            continue
        found.append({'start': round(start, 3), 'end': round(max(start, end), 3), 'href': event.group(6).strip(),
                      'x': int(event.group(4)), 'y': int(event.group(5)), 'forced': False})
    return found, {'fps': rate, 'size': None}


def _xml(data):
    try:
        return ET.fromstring(data if isinstance(data, (bytes, bytearray)) else str(data).encode('utf-8'))
    except ET.ParseError as error:
        line, column = error.position
        raise PictureIndexError(f'Line {line}, column {column + 1}: {error}') from error


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _children(parent, name):
    return [child for child in (parent if parent is not None else []) if _local(child.tag) == name]


def _child(parent, name):
    found = _children(parent, name)
    return found[0] if found else None


def _int(value, default=None):
    try:
        return int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _parse_spumux(data):
    root = _xml(data)
    found = []
    for stream in _children(root, 'stream'):
        for spu in _children(stream, 'spu'):
            if not spu.get('image') or spu.get('start') is None:
                continue
            start = spumux_seconds(spu.get('start'))
            end = spumux_seconds(spu.get('end')) if spu.get('end') else None
            found.append({'start': round(start, 3), 'end': None if end is None else round(max(start, end), 3),
                          'href': spu.get('image'), 'x': _int(spu.get('xoffset'), 0), 'y': _int(spu.get('yoffset'), 0),
                          'forced': str(spu.get('force') or '').strip().lower() in ('yes', 'on', '1', 'true'),
                          'transparent': spu.get('transparent')})
        break                                         # the first stream
    found.sort(key=lambda entry: entry['start'])
    for current, following in zip(found, found[1:] + [None]):
        if current['end'] is None:
            current['end'] = round(min(current['start'] + DEFAULT_DURATION, following['start']) if following
                                   else current['start'] + DEFAULT_DURATION, 3)
    return found, {'size': None}


def _parse_timed_images(data):
    root = _xml(data)
    if _local(root.tag) != 'TimedImages':
        raise PictureIndexError('Not a Timed Images XML: the document is not <TimedImages>')
    found = []
    for image in _children(root, 'I'):
        try:
            start, end = float(image.get('s')), float(image.get('e'))
        except (TypeError, ValueError):
            continue
        if not image.get('i'):
            continue
        found.append({'start': round(start, 3), 'end': round(max(start, end), 3), 'href': image.get('i').strip(),
                      'x': _int(image.get('x')), 'y': _int(image.get('y')), 'forced': False})
    width, height = _int(root.get('targetWidth'), 0), _int(root.get('targetHeight'), 0)
    return found, {'size': (width, height) if width > 0 and height > 0 else None}


def _fcp_rate(rate, fallback=DEFAULT_FPS):
    base = _int(_child(rate, 'timebase').text if _child(rate, 'timebase') is not None else None, 0)
    if not base:
        return fallback
    ntsc = (_child(rate, 'ntsc').text or '').strip().upper() == 'TRUE' if _child(rate, 'ntsc') is not None else False
    return base / 1.001 if ntsc else float(base)


def _parse_fcp(data):
    root = _xml(data)
    if _local(root.tag) != 'xmeml':
        raise PictureIndexError('Not a Final Cut Pro XML: the document is not <xmeml>')
    sequence = _child(root, 'sequence')
    if sequence is None:
        return [], {'size': None}
    fps = _fcp_rate(_child(sequence, 'rate'))
    video = _child(_child(sequence, 'media'), 'video')
    characteristics = _child(_child(video, 'format'), 'samplecharacteristics')
    width = _int(_child(characteristics, 'width').text, 0) if _child(characteristics, 'width') is not None else 0
    height = _int(_child(characteristics, 'height').text, 0) if _child(characteristics, 'height') is not None else 0
    files = {}
    found = []
    for track in _children(video, 'track'):
        for clip in _children(track, 'clipitem'):
            source = _child(clip, 'file')
            if source is None:
                continue
            name, url = _child(source, 'name'), _child(source, 'pathurl')
            if name is not None or url is not None:
                files[source.get('id')] = ((name.text or '').strip() if name is not None else '',
                                           (url.text or '').strip() if url is not None else '')
            name, url = files.get(source.get('id'), ('', ''))
            if not name and not url:
                continue
            first = _child(clip, 'start') if _child(clip, 'start') is not None else _child(clip, 'in')
            last = _child(clip, 'end') if _child(clip, 'end') is not None else _child(clip, 'out')
            first, last = _int(first.text if first is not None else None), _int(last.text if last is not None else None)
            if first is None or last is None or first < 0:
                continue
            found.append({'start': round(first / fps, 3), 'end': round(max(first, last) / fps, 3),
                          'href': url or name, 'name': name, 'x': None, 'y': None, 'forced': False})
    found.sort(key=lambda entry: entry['start'])
    return found, {'size': (width, height) if width > 0 and height > 0 else None, 'fps': fps}


def _parse_rhozet(data, fps):
    root = _xml(data)
    if _local(root.tag) != 'TitlerData':
        raise PictureIndexError('Not a Rhozet Harmonic image file: the document is not <TitlerData>')
    rate = fps or DEFAULT_FPS
    found = []
    for item in _children(root, 'Data'):
        image, first, last = item.get('Image'), item.get('StartTimecode'), item.get('EndTimecode')
        if not image or not first or not last:
            continue
        image = image.strip()
        if image.startswith('filepath\\'):
            image = image[len('filepath\\'):]
        try:
            start, end = _seconds(first, rate), _seconds(last, rate)
        except ValueError:
            continue
        found.append({'start': round(start, 3), 'end': round(max(start, end), 3), 'href': image,
                      'x': None, 'y': None, 'forced': False})
    return found, {'size': None, 'fps': rate}


def parse(data, which, fps=None):
    """(entries, settings) of an index of `which` kind (a key of FORMATS)
    given as bytes: each entry {'start', 'end', 'href', 'x', 'y',
    'forced'}, x and y None where the file places the picture nowhere;
    settings {'size' (None where the file gives none), ...}. `fps` is the
    rate for those that do not say theirs (the video's)."""
    if which in ('SCENARISTBMP', 'SON'):
        return _parse_script(_text(data), fps or DEFAULT_FPS, which)
    if which == 'DOST':
        return _parse_dost(_text(data), fps)
    if which == 'SPUMUX':
        return _parse_spumux(data)
    if which == 'TIMEDIMAGES':
        return _parse_timed_images(data)
    if which == 'FCPIMAGE':
        return _parse_fcp(data)
    if which == 'RHOZET':
        return _parse_rhozet(data, fps)
    raise PictureIndexError(f'Not an index of pictures: {which}')


def _image_path(folder, href, directory='', name=''):
    """The image file an entry names: as given (a file:// URL, a path
    relative to the index or to its Directory), else its name beside the
    index."""
    candidates = []
    if href.lower().startswith('file:'):
        path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
        if re.match(r'^/[A-Za-z]:/', path):
            path = path[1:]
        candidates.append(path)
    else:
        candidates.append(os.path.join(folder, href.replace('\\', os.sep)))
        if directory:
            candidates.append(os.path.join(folder, directory.replace('\\', os.sep), href))
    base = re.split(r'[\\/]', urllib.parse.unquote(href))[-1]
    candidates += [os.path.join(folder, base)] + ([os.path.join(folder, name)] if name else [])
    return next((path for path in candidates if os.path.isfile(path)), None)


def image_picture(path, key=None, shown=None):
    """(width, height, indices, colours) of an image file, as
    picture_ocr.png_picture gives a PNG's, or None. An image without
    alpha has `key` (else the colour most of its border is) taken as
    transparent; `shown` ({colour: (r, g, b, a)}) gives a Scenarist or
    SON image's key colours as the player shows them instead."""
    import numpy
    from PySide6.QtGui import QImage
    image = QImage(str(path))
    if image.isNull():
        return None
    has_alpha = image.hasAlphaChannel()
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    rows = numpy.frombuffer(image.constBits(), numpy.uint8, count=image.sizeInBytes())
    rgba = rows.reshape(height, image.bytesPerLine())[:, :width * 4].reshape(height, width, 4).copy()
    if not has_alpha and shown:
        keyed = rgba.copy()
        for colour, replacement in shown.items():
            keyed[(rgba[..., :3] == numpy.array(colour[:3], numpy.uint8)).all(axis=2)] = replacement
        rgba = keyed
    elif not has_alpha:
        if key is None:
            border = numpy.concatenate([rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]])
            colours, counts = numpy.unique(border, axis=0, return_counts=True)
            key = tuple(int(v) for v in colours[int(numpy.argmax(counts))])
        rgba[(rgba[..., :3] == numpy.array(key[:3], numpy.uint8)).all(axis=2), 3] = 0
    rgba = rgba.astype(numpy.float32)
    luma = 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
    indices = (numpy.rint(luma / 17) * 16 + numpy.rint(rgba[..., 3] / 17)).astype(numpy.uint8)
    colours = [(level * 17, level * 17, level * 17, alpha * 17) for level in range(16) for alpha in range(16)]
    return width, height, indices.tobytes(), colours


def _colour(value):
    """(r, g, b) of a spumux colour-spec ("#rrggbb", "rrggbb"), or None."""
    found = re.fullmatch(r'#?([0-9a-fA-F]{6})', str(value or '').strip())
    return tuple(int(found.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if found else None


def read(path, fps=None):
    """(pictures, which) of the index at `path`: picture_subtitles.Pictures
    in order of start, and the key of FORMATS it is. `fps` is the rate for
    an index that does not say its own (the video's). Raises
    PictureIndexError for a file that is none of them."""
    which = kind(path)
    if not which:
        raise PictureIndexError('Not an index of picture subtitles Subtitld knows')
    with open(path, 'rb') as handle:
        entries, settings = parse(handle.read(), which, fps)
    folder = os.path.dirname(os.path.abspath(path))
    key = settings.get('keys', {}).get('BG')
    decoded = []
    for entry in entries:
        found = _image_path(folder, entry['href'], settings.get('directory', ''), entry.get('name', ''))
        image = image_picture(found, _colour(entry.get('transparent')) or key, entry.get('shown')) if found else None
        if image is not None:
            decoded.append((entry, image))
    sized = [dict(entry, width=image[0], height=image[1]) for entry, image in decoded]
    screen = settings.get('size') or _fitting_frame(sized, max(((image[0], image[1]) for _e, image in decoded),
                                                                default=None) if which == 'RHOZET' else None)
    pictures = []
    for entry, (width, height, indices, colours) in decoded:
        x, y = entry['x'], entry['y']
        if (width, height) == tuple(screen) and which in ('SCENARISTBMP', 'SON'):
            # A frame-sized image is the frame; Display_Area is where on it subtitles may show.
            x, y = 0, 0
        if x is None or y is None:
            # Nowhere said: a frame-sized picture on the frame, a smaller one centred.
            x, y = max(0, (screen[0] - width) // 2), max(0, (screen[1] - height) // 2)
        pictures.append(Picture(entry['start'], entry['end'], x, y, width, height, indices, colours,
                                bool(entry.get('forced')), screen[0], screen[1], ''))
    pictures.sort(key=lambda picture: (picture.start, picture.end))
    return pictures, which


# Writing -------------------------------------------------------------------

def _qimage(rgba, width, height, alpha=True):
    from PySide6.QtGui import QImage
    image = QImage(rgba, width, height, width * 4, QImage.Format_RGBA8888).copy()
    return image if alpha else image.convertToFormat(QImage.Format_RGB888)


def _save_image(image, path):
    if not image.save(path, os.path.splitext(path)[1].lstrip('.').upper()):
        raise OSError(f'Could not write {path}')


def _full_frame(picture, size):
    """The picture's pixels laid on a transparent frame of `size`."""
    import numpy
    canvas = numpy.zeros((int(size[1]), int(size[0]), 4), numpy.uint8)
    fitted = disc_pictures.fit(picture, size)
    if fitted is not None:
        x, y, width, height, rgba = fitted
        canvas[y:y + height, x:x + width] = numpy.frombuffer(rgba, numpy.uint8).reshape(height, width, 4)
    return canvas.tobytes()


def _shown(pictures, size):
    """The pictures one on screen at a time, cut to the frame: [(picture, x,
    y, width, height, rgba)]."""
    found = []
    for picture in disc_pictures.timeline(pictures):
        fitted = disc_pictures.fit(picture, size)
        if fitted is not None and picture['end'] > picture['start']:
            found.append((picture,) + tuple(fitted))
    return found


def _clear(folder, stem, extension):
    """The images an export before left by this index's names."""
    pattern = re.compile(re.escape(stem) + r'_\d{4,}\.' + re.escape(extension) + '$')
    for name in os.listdir(folder):
        if pattern.match(name):
            os.remove(os.path.join(folder, name))


def four_colours(shown):
    """For Scenarist's and SON's images: the same four colours for every
    picture, as [(r, g, b, a)] by kind 'PA', 'E1', 'E2' (the pattern the one
    covering most), and each picture's pixels as kinds 0 (BG) to 3 (PA, E1,
    E2): [bytes]."""
    import numpy
    reduced = [disc_pictures.reduce(rgba, 4) for _p, _x, _y, _w, _h, rgba in shown]
    weights = {}
    for indices, colours in reduced:
        counts = numpy.bincount(numpy.frombuffer(indices, numpy.uint8), minlength=len(colours))
        for colour, count in zip(colours[1:], counts[1:]):
            weights[colour] = weights.get(colour, 0) + int(count)
    colours = [colour for colour, weight in weights.items() if weight > 0]
    if not colours:
        return [(0, 0, 0, 0)] * 3, [bytes(len(indices)) for indices, _c in reduced]
    points = numpy.array([[r * a / 255, g * a / 255, b * a / 255, a] for r, g, b, a in colours], numpy.float64)
    centres, labels = disc_pictures.cluster(points, [weights[c] for c in colours], 3, pinned=(0, 0, 0, 0))
    totals = numpy.bincount(labels, [weights[c] for c in colours], len(centres))
    order = [int(n) for n in numpy.argsort(-totals[1:]) + 1 if totals[n] > 0][:3]
    kinds = {}
    for rank, centre in enumerate(order, start=1):
        kinds[centre] = rank
    palette = []
    for centre in order:
        a = max(1.0, centres[centre][3])
        palette.append(tuple(max(0, min(255, int(round(v * 255 / a)))) for v in centres[centre][:3]) +
                       (min(255, int(round(centres[centre][3]))),))
    palette += [(0, 0, 0, 0)] * (3 - len(palette))
    lookup = {colour: kinds.get(int(label), 0) for colour, label in zip(colours, labels)}
    pixels = []
    for indices, picture_colours in reduced:
        table = bytes([0] + [lookup.get(colour, 0) for colour in picture_colours[1:]]).ljust(256, b'\x00')
        pixels.append(indices.translate(table))
    return palette, pixels


def _palette_entry(colour):
    """The entry of BDSup2Sub's default DVD palette nearest `colour`."""
    from subtitld.modules import dvdsup
    return min(range(16), key=lambda n: sum((a - b) ** 2 for a, b in zip(dvdsup.DEFAULT_PALETTE[n], colour[:3])))


def _script_header(which, stem, size, palette):
    ntsc = int(size[1]) == 480
    last = int(size[1]) - 2 if int(size[1]) in (480, 576) else int(size[1]) - 1
    kinds = dict(zip(('PA', 'E1', 'E2'), palette))
    colour = ' '.join(str(_palette_entry(kinds[name])) for name in ('E2', 'E1', 'PA')) + ' 0'
    contrast = ' '.join(str(int(round(kinds[name][3] / 17))) for name in ('E2', 'E1', 'PA')) + ' 0'
    lines = ['st_format 2', 'Display_Start non_forced', f'TV_Type {"NTSC" if ntsc else "PAL"}',
             'Tape_Type NON_DROP', f'Pixel_Area (2 {last})', 'Directory .', f'Subtitle {stem}',
             f'Display_Area (0 2 {int(size[0]) - 1} {last})', f'Color ({colour})', f'Contrast ({contrast})',
             '#', '# Palette entries:', '#']
    lines += [f'{name} ({" ".join(str(v) for v in KEYS[name])} ===)' for name in ('PA', 'E1', 'E2', 'BG')]
    return lines + ['#', 'SP_NUMBER\tSTART\tEND\tFILE_NAME']


def save(path, which, pictures, size, fps=None, language='en', title=''):
    """Write the index of `which` kind (a key of FORMATS) at `path` and its
    images beside it, of `pictures` (disc_pictures' dicts with 'rgba', in
    order of start) on a frame of `size`, times at `fps` (the video's;
    Scenarist's and SON's the rate of their frame). Returns the paths of
    the images."""
    import numpy
    folder = os.path.dirname(os.path.abspath(path))
    stem = re.sub(r'\s', '_', os.path.splitext(os.path.basename(path))[0]) or 'subtitles'
    width, height = int(size[0]), int(size[1])
    extension = 'bmp' if which in ('SCENARISTBMP', 'SON') else 'png'
    _clear(folder, stem, extension)
    shown = _shown(pictures, (width, height))
    written = []

    def name_of(number):
        return f'{stem}_{number:04d}.{extension}'

    if which in ('SCENARISTBMP', 'SON'):
        rate = 29.97 if height == 480 else 25.0
        palette, pixels = four_colours(shown)
        table = numpy.array([KEYS['BG'], KEYS['PA'], KEYS['E1'], KEYS['E2']], numpy.uint8)
        lines = _script_header(which, stem, (width, height), palette)
        for number, ((picture, x, y, w, h, _rgba), kinds) in enumerate(zip(shown, pixels), start=1):
            frame = numpy.empty((height, width, 3), numpy.uint8)
            frame[...] = KEYS['BG']
            frame[y:y + h, x:x + w] = table[numpy.frombuffer(kinds, numpy.uint8).reshape(h, w)]
            from PySide6.QtGui import QImage
            image = QImage(frame.tobytes(), width, height, width * 3, QImage.Format_RGB888).copy()
            _save_image(image, os.path.join(folder, name_of(number)))
            written.append(os.path.join(folder, name_of(number)))
            separator = '\t'
            lines.append(separator.join([f'{number:04d}', _timecode(picture['start'], rate),
                                         _timecode(picture['end'], rate), name_of(number)]))
        text = '\r\n'.join(lines) + '\r\n'
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        return written

    rate = fps or DEFAULT_FPS
    entries = []
    for number, (picture, x, y, w, h, rgba) in enumerate(shown, start=1):
        target = os.path.join(folder, name_of(number))
        if which == 'SPUMUX':
            indices, colours = disc_pictures.reduce(rgba, 4)
            table = numpy.array(colours + [(0, 0, 0, 0)] * (256 - len(colours)), numpy.uint8)
            image = _qimage(table[numpy.frombuffer(indices, numpy.uint8)].tobytes(), w, h)
        elif which in ('FCPIMAGE', 'RHOZET'):
            image = _qimage(_full_frame(picture, (width, height)), width, height)
        else:
            image = _qimage(rgba, w, h)
        _save_image(image, target)
        written.append(target)
        entries.append({'number': number, 'start': picture['start'], 'end': picture['end'], 'x': x, 'y': y,
                        'width': w, 'height': h, 'name': name_of(number), 'forced': bool(picture.get('forced'))})
    writer = {'DOST': _write_dost, 'SPUMUX': _write_spumux, 'TIMEDIMAGES': _write_timed_images,
              'FCPIMAGE': _write_fcp, 'RHOZET': _write_rhozet}[which]
    text = writer(entries, (width, height), rate, stem)
    with open(path, 'w', encoding='utf-8', newline='\n' if which != 'DOST' else '') as handle:
        handle.write(text)
    return written


def _write_dost(entries, size, fps, stem):
    lines = ['$FORMAT=480', '$VERSION=1.2', '$ULEAD=TRUE', f'$DROP={int(round(fps * 1000))}', '',
             'NO\tINTIME\t\tOUTTIME\t\tXPOS\tYPOS\tFILENAME\tFADEIN\tFADEOUT']
    for entry in entries:
        lines.append('\t'.join([f'{entry["number"]:04d}', _timecode(entry['start'], fps), _timecode(entry['end'], fps),
                                str(entry['x']), str(entry['y']), entry['name'], '0', '0']))
    return '\r\n'.join(lines) + '\r\n'


def _write_spumux(entries, size, fps, stem):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<subpictures>', '\t<stream>']
    for entry in entries:
        lines.append(f'\t\t<spu image={quoteattr(entry["name"])} start="{spumux_time(entry["start"])}" '
                     f'end="{spumux_time(entry["end"])}" xoffset="{entry["x"]}" yoffset="{entry["y"]}"'
                     + (' force="yes"' if entry['forced'] else '') + ' />')
    return '\n'.join(lines + ['\t</stream>', '</subpictures>', ''])


def _ratio(size):
    import math
    divisor = math.gcd(int(size[0]), int(size[1])) or 1
    return f'{int(size[0]) // divisor}:{int(size[1]) // divisor}'


def _write_timed_images(entries, size, fps, stem):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<TimedImages targetWidth="{size[0]}" targetHeight="{size[1]}" aspectRatio="{_ratio(size)}">']
    for entry in entries:
        lines.append(f'  <I s="{entry["start"]:.3f}" e="{entry["end"]:.3f}" x="{entry["x"]}" y="{entry["y"]}" '
                     f'w="{entry["width"]}" h="{entry["height"]}" i={quoteattr(entry["name"])} />')
    return '\n'.join(lines + ['</TimedImages>', ''])


def _fcp_timebase(fps):
    """(timebase, 'TRUE'/'FALSE') of a rate, as Subtitle Edit's FCP export
    gives them."""
    for rate, base in ((29.97, 30), (23.976, 24), (59.94, 60)):
        if abs(fps - rate) < 0.01:
            return base, 'TRUE'
    return int(round(fps)), 'FALSE'


def _write_fcp(entries, size, fps, stem):
    base, ntsc = _fcp_timebase(fps)
    rate = base / 1.001 if ntsc == 'TRUE' else float(base)

    def frames(seconds):
        return int(round(seconds * rate))

    def rate_block(indent):
        return [f'{indent}<rate>', f'{indent}  <timebase>{base}</timebase>', f'{indent}  <ntsc>{ntsc}</ntsc>',
                f'{indent}</rate>']

    clips = []
    for entry in entries:
        name = escape(entry['name'])
        identifier = escape(os.path.splitext(entry['name'])[0])
        start, end = frames(entry['start']), frames(entry['end'])
        clips += [f'          <clipitem id={quoteattr(entry["name"])}>', f'            <name>{name}</name>',
                  f'            <duration>{end - start}</duration>', *rate_block('            '),
                  f'            <in>{start}</in>', f'            <out>{end}</out>',
                  f'            <start>{start}</start>', f'            <end>{end}</end>',
                  '            <pixelaspectratio>square</pixelaspectratio>', '            <stillframe>TRUE</stillframe>',
                  '            <anamorphic>FALSE</anamorphic>', '            <alphatype>straight</alphatype>',
                  f'            <masterclipid>{name}1</masterclipid>', f'            <file id="{identifier}">',
                  f'              <name>{name}</name>', f'              <pathurl>{escape(urllib.parse.quote(entry["name"]))}</pathurl>',
                  *rate_block('              '), f'              <duration>{end - start}</duration>',
                  f'              <width>{size[0]}</width>', f'              <height>{size[1]}</height>',
                  '              <media>', '                <video>', f'                  <duration>{end - start}</duration>',
                  '                  <stillframe>TRUE</stillframe>', '                  <samplecharacteristics>',
                  f'                    <width>{size[0]}</width>', f'                    <height>{size[1]}</height>',
                  '                  </samplecharacteristics>', '                </video>', '              </media>',
                  '            </file>', '            <sourcetrack>', '              <mediatype>video</mediatype>',
                  '            </sourcetrack>', '            <fielddominance>none</fielddominance>',
                  '          </clipitem>']
    last = frames(entries[-1]['end']) if entries else 0
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<!DOCTYPE xmeml>', '<xmeml version="4">',
             f'  <sequence id={quoteattr(stem)}>', '    <updatebehavior>add</updatebehavior>',
             f'    <name>{escape(stem)}</name>', f'    <duration>{last}</duration>', *rate_block('    '),
             '    <timecode>', *rate_block('      '), '      <string>00:00:00:00</string>', '      <frame>0</frame>',
             '      <source>source</source>', f'      <displayformat>{"DF" if ntsc == "TRUE" else "NDF"}</displayformat>',
             '    </timecode>', '    <in>0</in>', f'    <out>{last + 1 if entries else 0}</out>', '    <media>',
             '      <video>', '        <format>', '          <samplecharacteristics>', *rate_block('            '),
             f'            <width>{size[0]}</width>', f'            <height>{size[1]}</height>',
             '            <anamorphic>FALSE</anamorphic>', '            <pixelaspectratio>square</pixelaspectratio>',
             '            <fielddominance>none</fielddominance>', '            <colordepth>32</colordepth>',
             '          </samplecharacteristics>', '        </format>', '        <track>', *clips,
             '          <enabled>TRUE</enabled>', '          <locked>FALSE</locked>', '        </track>',
             '      </video>', '    </media>', '    <ismasterclip>FALSE</ismasterclip>', '  </sequence>',
             '</xmeml>', '']
    return '\n'.join(lines)


def _write_rhozet(entries, size, fps, stem):
    lines = ['<?xml version="1.0"?>', '<TitlerData>']
    for entry in entries:
        # In single quotes, as Subtitle Edit writes them.
        name = escape(entry['name'], {"'": '&apos;'})
        lines.append(f"<Data StartTimecode='{_timecode(entry['start'], fps)}' "
                     f"EndTimecode='{_timecode(entry['end'], fps)}' Image='{name}'/>")
    return '\n'.join(lines + ['</TitlerData>', ''])
