"""Cappella DetX (.detx): the dubbing detection a French rythmo band is
drawn from, read into a dubbing session and written from one.

Cappella publishes no specification. What the code follows is the
description and the reader and writer of Joker, the free rythmo band
player that opens DetX (github.com/MartinDelille/Joker: DetX.md,
libs/PhStrip/PhStripDoc.cpp importDetXFile and exportDetXFile, and its
test files data/strip/*.detx):

    <detx>
      <header>
        <cappella version="..."/> <title>...</title> <title2>...</title2>
        <episode number="" season="" title="" title2=""/>
        <author name="" firstname=""/> <detector .../> <production .../>
        <notes>...</notes> <summary>...</summary>
        <videofile timestamp="01:00:00:00" tctype="25">path</videofile>
        <last_position timecode="..." track="..."/>
      </header>
      <roles><role id="jeanne" name="Jeanne" color="#00BB00" gender="female"
              description=""><image>base64 JPEG</image></role></roles>
      <body>
        <loop timecode="..."/> <shot timecode="..."/>
        <line role="jeanne" track="1" voice="off" type="reac">
          <lipsync timecode="01:00:02:00" type="in_open"/>
          <text>Composed </text>
          <lipsync timecode="01:00:03:00" type="neutral"/>
          <text>sentence</text>
          <lipsync timecode="01:00:04:00" type="out_open"/>
        </line>
      </body>
    </detx>

Timecodes are HH:MM:SS:FF in frames of the videofile's tctype (Cappella's
are 25, Joker says, where none is given), from the video's start, its
timestamp. A <line> is a sentence its role says on the band: it runs from
its first <lipsync> to its last (Joker gives a line whose text goes on past
its last lipsync a second a character), its <text>s are its words, a
<br/> in one a line break. The lipsyncs inside it are the detection's
marks; one with link="off" is not tied to the words around it.

Reading makes a subtitle of each <line>: its times from the video's
start, its words as its text, its role's name as its speaker, who is then
in the project's list with the role's colour and picture. What a subtitle
has no room for is kept in its 'detx' and written back: the role's id,
voice (on, off, semi_off), track, type, and every lipsync (its type, its
link, its time from the line's start and where in the words it falls); and
in the project's metadata['detx'] what the file says of itself: the
header's elements, the roles' genders and descriptions, the loops and the
shots.

Writing gives the same layout: the header kept (or the title alone), a
role for each speaker (its id the one read, else made of the name as
Joker makes one), the loops and shots kept, and a <line> a subtitle, an
in_open lipsync at its start and an out_open at its end; the lipsyncs
read inside it are written again where its words are still those it was
read with. A subtitle with no speaker is said by a role "Unknown".
Formatting is not written: the band has none; nor are the roles'
pictures, which Subtitld keeps as images of its own.
"""

import base64
import re
import unicodedata
import xml.etree.ElementTree as ET
from fractions import Fraction
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import markup
from subtitld.modules.avid_subcap import frame_of, rate, timecode

DEFAULT_FPS = 25.0
UNKNOWN_ROLE = 'Unknown'
_TC = re.compile(r'^\s*(\d{1,2})[:;.](\d{2})[:;.](\d{2})[:;.,](\d{2})\s*$')
# What the header holds, in Joker's order: elements of attributes, of text.
_HEADER_ATTRIBUTES = ('cappella', 'episode', 'production', 'author', 'detector')
_HEADER_TEXT = ('title', 'title2', 'notes', 'summary', 'audiofile', 'detectionfile')


def looks_like(head):
    """Whether `head` (bytes or text, the first of a file) is a DetX: XML
    whose root is <detx>."""
    text = head.decode('utf-8', 'replace') if isinstance(head, (bytes, bytearray)) else str(head or '')
    text = re.sub(r'<\?xml[^>]*\?>|<!--.*?-->', '', text.lstrip('﻿'), flags=re.S).lstrip()
    return text.startswith('<detx') and (len(text) == 5 or not text[5].isalnum())


def role_id(name, taken=()):
    """A role's id made of its name as Joker's computeDetXId makes one:
    lower case, no accents, letters and digits only; a "_" more where it
    is taken."""
    plain = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode('ascii').lower()
    identifier = re.sub(r'[^a-z0-9]', '', plain) or 'role'
    while identifier in taken:
        identifier += '_'
    return identifier


def _frames(value, nominal):
    match = _TC.match(value or '')
    if not match:
        return None
    return frame_of(*match.groups(), nominal)


def _text_of(element):
    """A <text>'s words, a <br/> a line break."""
    parts = [element.text or '']
    for child in element:
        if child.tag == 'br':
            parts.append('\n')
        parts.append(child.tail or '')
    return ''.join(parts)


def parse(data, fps=None):
    """(subtitles, settings) in DetX bytes or text: [{'start', 'end',
    'text', 'speaker', 'detx'}] by start, and {'fps', 'metadata' (what
    metadata['detx'] keeps), 'speakers' {name: {'color', 'image_bytes'}},
    'title'}. Raises ValueError for anything that is not DetX."""
    if isinstance(data, str):
        data = re.sub(r'^\s*<\?xml[^>]*\?>', '', data.lstrip('﻿')).encode('utf-8')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        line, column = error.position
        raise ValueError(f'Line {line}, column {column + 1}: {error}') from error
    if root.tag != 'detx':
        raise ValueError(f'Not a DetX file: its root is <{root.tag}>, not <detx>')
    header = root.find('header')
    kept = {'header': {}, 'roles': {}, 'loops': [], 'shots': []}
    video = header.find('videofile') if header is not None else None
    stated = float(video.get('tctype') or 0) if video is not None and re.fullmatch(
        r'\d+(\.\d+)?', (video.get('tctype') or '').strip()) else 0
    fps = stated or float(fps or DEFAULT_FPS)
    nominal, frame_length = rate(fps)
    origin = 0
    if header is not None:
        for name in _HEADER_ATTRIBUTES:
            element = header.find(name)
            if element is not None:
                kept['header'][name] = dict(element.attrib)
        for name in _HEADER_TEXT:
            element = header.find(name)
            if element is not None and element.text is not None:
                kept['header'][name] = element.text
        if video is not None:
            kept['header']['videofile'] = dict(video.attrib, path=(video.text or '').strip())
            origin = _frames(video.get('timestamp'), nominal) or 0
        position = header.find('last_position')
        if position is not None:
            kept['header']['last_position'] = dict(position.attrib)

    def seconds(value):
        count = _frames(value, nominal)
        return None if count is None else round(float((count - origin) * frame_length), 3)

    names, speakers = {}, {}
    roles = root.find('roles')
    for role in (roles.findall('role') if roles is not None else []):
        identifier = role.get('id') or role_id(role.get('name') or '')
        name = (role.get('name') or identifier).strip() or identifier
        names[identifier] = name
        speaker = {}
        if re.fullmatch(r'#[0-9a-fA-F]{6}', (role.get('color') or '').strip()):
            speaker['color'] = role.get('color').strip().lower()
        image = role.find('image')
        if image is not None and (image.text or '').strip():
            try:
                speaker['image_bytes'] = base64.b64decode(''.join(image.text.split()), validate=True)
            except (ValueError, base64.binascii.Error):
                pass
        speakers[name] = speaker
        kept['roles'][identifier] = {key: value for key, value in role.attrib.items() if key in ('gender', 'description')}
        kept['roles'][identifier]['name'] = name

    segments = []
    body = root.find('body')
    for element in (list(body) if body is not None else []):
        if element.tag in ('loop', 'shot'):
            at = seconds(element.get('timecode') or element.get('tc'))
            if at is not None:
                kept['loops' if element.tag == 'loop' else 'shots'].append(at)
            continue
        if element.tag != 'line':
            continue
        words, marks = '', []
        for child in element:
            if child.tag == 'lipsync':
                at = seconds(child.get('timecode') or child.get('tc'))
                if at is not None:
                    marks.append({'type': child.get('type') or 'neutral', 'at': at,
                                  'link': child.get('link') != 'off', 'char': len(words)})
            elif child.tag == 'text':
                words += _text_of(child)
        if not marks:
            continue
        start = marks[0]['at']
        end = max(mark['at'] for mark in marks)
        # Joker: words that run on past the last lipsync last a second a
        # character from the last mark tied to them.
        if marks[-1]['char'] < len(words.rstrip()):
            linked = [mark['at'] for mark in marks if mark['link']] or [start]
            end = max(end, round(linked[-1] + len(words[marks[-1]['char']:].strip()), 3))
        text = '\n'.join(line.strip() for line in words.strip().split('\n'))
        lead = len(words) - len(words.lstrip())
        identifier = element.get('role') or ''
        detx = {'role': identifier, 'text': text,
                'marks': [dict(mark, at=round(mark['at'] - start, 3), char=max(0, min(len(text), mark['char'] - lead)))
                          for mark in marks]}
        for attribute in ('voice', 'track', 'type'):
            if element.get(attribute) is not None:
                detx[attribute] = element.get(attribute)
        segment = {'start': start, 'end': max(start, end), 'text': text, 'detx': detx}
        if identifier:
            segment['speaker'] = names.get(identifier, identifier)
            speakers.setdefault(segment['speaker'], {})
        segments.append(segment)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    title = (kept['header'].get('title') or '').strip()
    return segments, {'fps': fps, 'metadata': kept, 'speakers': speakers, 'title': title}


def read(path, fps=None):
    """(subtitles, settings) in the DetX file at `path`, as parse() gives them."""
    with open(path, 'rb') as detx_file:
        return parse(detx_file.read(), fps)


def _frame(seconds, frame_length):
    return int(round(Fraction(float(seconds)).limit_denominator(1000000) / frame_length))


def _attributes(values):
    return ''.join(f' {name}={quoteattr(str(value))}' for name, value in values.items())


def write(segments, fps=DEFAULT_FPS, metadata=None, speakers=None, title=''):
    """DetX text for `segments`: times in frames at `fps` from the video's
    timestamp, the header, roles, loops and shots `metadata` (what parse()
    kept) says, `speakers` {name: {'color'}} for the roles' colours, and
    `title` where the header has none."""
    kept = metadata if isinstance(metadata, dict) else {}
    header = dict(kept.get('header') or {})
    known_roles = dict(kept.get('roles') or {})
    speakers = speakers or {}
    video = dict(header.get('videofile') or {})
    stated = float(video['tctype']) if re.fullmatch(r'\d+(\.\d+)?', str(video.get('tctype') or '')) else 0
    nominal, frame_length = rate(stated or fps)
    origin = _frames(video.get('timestamp'), nominal) or 0

    def label(seconds):
        return timecode(origin + _frame(seconds, frame_length), nominal)

    out = ['<?xml version="1.0" encoding="UTF-8" standalone="no" ?>', '<detx>', '  <header>']
    if 'cappella' in header:
        out.append(f'    <cappella{_attributes(header["cappella"])}/>')
    out.append(f'    <title>{escape(header.get("title") or title or "")}</title>')
    if header.get('title2') is not None:
        out.append(f'    <title2>{escape(header["title2"])}</title2>')
    for name in ('episode', 'production', 'author', 'detector'):
        if name in header:
            out.append(f'    <{name}{_attributes(header[name])}/>')
    for name in ('notes', 'summary'):
        if header.get(name) is not None:
            out.append(f'    <{name}>{escape(header[name])}</{name}>')
    path = video.pop('path', '')
    video.setdefault('timestamp', label(0) if origin else '00:00:00:00')
    video['tctype'] = video.get('tctype') or f'{float(stated or fps):g}'
    out.append(f'    <videofile{_attributes(video)}>{escape(path)}</videofile>')
    for name in ('audiofile', 'detectionfile'):
        if header.get(name) is not None:
            out.append(f'    <{name}>{escape(header[name])}</{name}>')
    if 'last_position' in header:
        out.append(f'    <last_position{_attributes(header["last_position"])}/>')
    out.append('  </header>')

    ordered = [segment for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
               if markup.plain(str(segment.get('text') or '')).strip()]
    ids = {}
    for identifier, role in known_roles.items():
        ids.setdefault(role.get('name') or identifier, identifier)
    used = []
    for segment in ordered:
        name = str(segment.get('speaker') or '').strip() or UNKNOWN_ROLE
        if name not in used:
            used.append(name)
        if name not in ids:
            wanted = str((segment.get('detx') or {}).get('role') or '')
            ids[name] = wanted if wanted and wanted not in ids.values() else role_id(name, ids.values())
    out.append('  <roles>')
    for name in used:
        identifier = ids[name]
        role = known_roles.get(identifier) or {}
        color = (speakers.get(name) or {}).get('color') or '#000000'
        attributes = {'color': color.upper() if re.fullmatch(r'#[0-9a-fA-F]{6}', str(color)) else '#000000'}
        if role.get('description') is not None:
            attributes['description'] = role['description']
        if role.get('gender'):
            attributes['gender'] = role['gender']
        attributes.update(id=identifier, name=name)
        out.append(f'    <role{_attributes(attributes)}/>')
    out.append('  </roles>')

    entries = [(float(at), 0, f'    <loop timecode="{label(at)}"/>') for at in kept.get('loops') or []]
    entries += [(float(at), 1, f'    <shot timecode="{label(at)}"/>') for at in kept.get('shots') or []]
    for segment in ordered:
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        name = str(segment.get('speaker') or '').strip() or UNKNOWN_ROLE
        detx = segment.get('detx') if isinstance(segment.get('detx'), dict) else {}
        text = markup.plain(str(segment.get('text') or '').replace('\r\n', '\n'))
        attributes = {'role': ids[name], 'track': detx.get('track', '0')}
        for attribute in ('voice', 'type'):
            if detx.get(attribute):
                attributes[attribute] = detx[attribute]
        marks = detx.get('marks') if detx.get('text') == text and detx.get('marks') else None
        if not marks:
            marks = [{'type': 'in_open', 'at': 0.0, 'link': True, 'char': 0},
                     {'type': 'out_open', 'at': round(end - start, 3), 'link': True, 'char': len(text)}]
        lines = [f'    <line{_attributes(attributes)}>']
        written = 0
        for mark in sorted(marks, key=lambda mark: (int(mark.get('char', 0)), float(mark.get('at', 0)))):
            at = int(mark.get('char', 0))
            if at > written:
                lines.append(f'      <text>{escape(text[written:at]).replace(chr(10), "<br/>")}</text>')
                written = at
            link = '' if mark.get('link', True) else ' link="off"'
            lines.append(f'      <lipsync timecode="{label(start + float(mark.get("at", 0)))}" '
                         f'type={quoteattr(str(mark.get("type") or "neutral"))}{link}/>')
        if written < len(text):
            lines.append(f'      <text>{escape(text[written:]).replace(chr(10), "<br/>")}</text>')
        lines.append('    </line>')
        entries.append((start, 2, '\n'.join(lines)))
    out.append('  <body>')
    out += [entry[2] for entry in sorted(entries, key=lambda entry: (entry[0], entry[1]))]
    out += ['  </body>', '</detx>', '']
    return '\n'.join(out)


def save(path, segments, **options):
    """Write `segments` to `path` as DetX, in UTF-8; `options` as write()
    takes them."""
    with open(path, 'w', encoding='utf-8', newline='\n') as detx_file:
        detx_file.write(write(segments, **options))
