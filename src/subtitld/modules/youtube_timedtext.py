"""YouTube's timed text in XML: srv3 (also YTT), the XML YouTube's player
reads and json3 holds as JSON; and srv1, the older <transcript>.

srv3 is a <timedtext format="3"> document: in <head>, pens (bold "b",
italics "i", underline "u", colour "fc"), window positions (anchor point
"ap", across "ah", down "av", in %) and window styles; in <body>, a <p> a
caption, its start "t" and duration "d" in milliseconds, its pen "p" and
position "wp", its text in <s> runs of their own pens, lines broken by a
newline. srv1 is <transcript> of <text start dur>, in seconds.

Reading turns srv3 into json3's events and reads them as modules.
json_subtitles does: tags for the pens, places for the positions,
automatic captions rolling a line at a time. Writing gives srv3: a pen
for each style the text uses, a position for each place but the bottom
centre, a <p> a subtitle.
"""

import html
import xml.etree.ElementTree as ET

from subtitld.modules import json_subtitles
from subtitld.modules import markup

# Places as srv3 positions: anchor point (0 top left ... 8 bottom right), %.
_POSITIONS = {7: (0, 0, 0), 8: (1, 50, 0), 9: (2, 100, 0), 4: (3, 0, 50), 5: (4, 50, 50), 6: (5, 100, 50),
              1: (6, 0, 100), 3: (8, 100, 100)}


def kind(root):
    """'srv3', 'srv1' or None for the root element of an XML document."""
    tag = root.tag.split('}')[-1]
    return 'srv3' if tag == 'timedtext' else 'srv1' if tag == 'transcript' else None


def _number(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _table(elements, convert):
    """A json3 table (a list, by id) from <pen>, <wp> or <ws> elements."""
    entries = {}
    for element in elements:
        index = _number(element.get('id'))
        if index is not None and 0 <= index < 10000:
            entries[index] = convert(element)
    return [entries.get(index, {}) for index in range(max(entries) + 1)] if entries else []


def _pen(element):
    pen = {}
    for attribute, key in (('b', 'bAttr'), ('i', 'iAttr'), ('u', 'uAttr')):
        if element.get(attribute) is not None:
            pen[key] = _number(element.get(attribute), 0)
    color = (element.get('fc') or '').lstrip('#')
    if len(color) == 6:
        try:
            pen['fcForeColor'] = int(color, 16)
        except ValueError:
            pass
    return pen


def _position(element):
    return {key: _number(element.get(attribute)) for attribute, key in (('ap', 'apPoint'), ('ah', 'ahHorPos'),
                                                                         ('av', 'avVerPos'))
            if _number(element.get(attribute)) is not None}


def _style(element):
    return {'mhModeHint': _number(element.get('mh'))} if element.get('mh') is not None else {}


def to_json3(root):
    """json3's document for an srv3 root."""
    head, body = root.find('head'), root.find('body')
    head = head if head is not None else ET.Element('head')
    body = body if body is not None else ET.Element('body')
    data = {'pens': _table(head.iter('pen'), _pen), 'wpWinPositions': _table(head.iter('wp'), _position),
            'wsWinStyles': _table(head.iter('ws'), _style), 'events': []}
    for element in body:
        tag = element.tag.split('}')[-1]
        event = {'tStartMs': _number(element.get('t'), 0)}
        if element.get('d') is not None:
            event['dDurationMs'] = _number(element.get('d'), 0)
        for attribute, key in (('p', 'pPenId'), ('wp', 'wpWinPosId'), ('ws', 'wsWinStyleId'), ('w', 'wWinId'),
                               ('a', 'aAppend')):
            if element.get(attribute) is not None:
                event[key] = _number(element.get(attribute))
        if tag == 'w':                                       # a window: its id, no text
            event['id'] = _number(element.get('id'))
        elif tag == 'p':
            segs = [{'utf8': element.text}] if element.text else []
            for child in element:
                if child.tag.split('}')[-1] == 's':
                    seg = {'utf8': child.text or ''}
                    if child.get('p') is not None:
                        seg['pPenId'] = _number(child.get('p'))
                    segs.append(seg)
                elif child.tag.split('}')[-1] == 'br':
                    segs.append({'utf8': '\n'})
                if child.tail:
                    segs.append({'utf8': child.tail})
            event['segs'] = segs
        else:
            continue
        data['events'].append(event)
    return data


def parse(data):
    """The subtitles in srv3 or srv1 bytes. ValueError for other XML."""
    root = ET.fromstring(data)
    which = kind(root)
    if which == 'srv3':
        segments, _language = json_subtitles._youtube(to_json3(root), json_subtitles.LIMITS, None)
        return segments
    if which == 'srv1':
        segments = []
        for element in root.iter('text'):
            start = float(element.get('start') or 0)
            duration = float(element.get('dur') or 5)
            # srv1 escapes its text once more: &amp;#39; for an apostrophe.
            text = html.unescape(''.join(element.itertext())).strip()
            if text:
                segments.append({'start': round(start, 3), 'end': round(start + duration, 3), 'text': text})
        return segments
    raise ValueError('Not YouTube timed text: its root is neither <timedtext> nor <transcript>')


def read(path):
    with open(path, 'rb') as xml_file:
        return parse(xml_file.read())


def write(segments):
    """srv3 for `segments`."""
    pens, positions, paragraphs = {}, {}, []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or ''))
        runs = [(text, style) for text, style in markup.spans(rest) if text]
        if not ''.join(text for text, _style in runs).strip():
            continue
        start = round(float(segment.get('start', 0)) * 1000)
        end = max(start + 1, round(float(segment.get('end', 0)) * 1000))
        paragraph = ET.Element('p', t=str(start), d=str(end - start))
        keypad = markup.alignment(lead)
        if keypad in _POSITIONS:
            paragraph.set('wp', str(positions.setdefault(keypad, len(positions) + 1)))
        last = None
        for text, style in runs:
            look = (style.bold, style.italic, style.underline, style.color)
            if look == (False, False, False, ''):
                if last is None:
                    paragraph.text = (paragraph.text or '') + text
                else:
                    last.tail = (last.tail or '') + text
                continue
            pen = pens.setdefault(look, len(pens) + 1)
            if last is not None and last.get('p') == str(pen) and not last.tail:
                last.text += text
                continue
            last = ET.SubElement(paragraph, 's', p=str(pen))
            last.text = text
        paragraphs.append(paragraph)

    root = ET.Element('timedtext', format='3')
    head = ET.SubElement(root, 'head')
    for (bold, italic, underline, color), pen in sorted(pens.items(), key=lambda item: item[1]):
        element = ET.SubElement(head, 'pen', id=str(pen))
        for attribute, on in (('b', bold), ('i', italic), ('u', underline)):
            if on:
                element.set(attribute, '1')
        if color:
            element.set('fc', color.upper())
    for keypad, position in sorted(positions.items(), key=lambda item: item[1]):
        point, across, down = _POSITIONS[keypad]
        ET.SubElement(head, 'wp', id=str(position), ap=str(point), ah=str(across), av=str(down))
    body = ET.SubElement(root, 'body')
    body.extend(paragraphs)
    # A line for each element of the head and body; no indentation, which
    # would go into the text of a <p> with runs.
    root.text = head.text = body.text = head.tail = '\n'
    for element in list(head) + paragraphs:
        element.tail = '\n'
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def save(path, segments):
    with open(path, 'w', encoding='utf-8', newline='\n') as xml_file:
        xml_file.write(write(segments))
