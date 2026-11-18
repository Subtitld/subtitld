"""TMX (.tmx), the Translation Memory eXchange of LISA OSCAR, version 1.4b,
as Subtitle Edit keeps subtitles in it.

TMX itself (the 1.4b specification and its DTD, tmx14.dtd) is a memory of
translation units: a <header> (creationtool, creationtoolversion, segtype,
o-tmf, adminlang, srclang and datatype, all required), then a <body> of
<tu>s, each with <prop>s and a <tuv> a language (xml:lang; "lang" in
TMX 1.1), the text of each in a <seg>, whose inline codes (bpt, ept, it,
ph, ut) hold the source format's markup and <hi> highlighted text.

Times are not TMX's: Subtitle Edit (Tmx14.cs) gives a unit a subtitle and
keeps its times in two props, "start" and "end", as "hh:mm:ss,mmm", and
writes a line break as a <br /> in the seg.

    <tu tuid="0001" datatype="html">
      <prop type="start">00:00:01,000</prop>
      <prop type="end">00:00:03,500</prop>
      <tuv xml:lang="en"><seg>Hello<br />world</seg></tuv>
      <tuv xml:lang="pt-BR"><seg>Olá<br />mundo</seg></tuv>
    </tu>

Reading gives a subtitle a unit with both times: its text the <tuv> in the
header's srclang (the first one when none matches or srclang is
"*all*"), each of the others a translation in its language, as Subtitld
keeps them (a subtitle's 'translations'). The times read with ",", "." or
":" before the milliseconds, as Subtitle Edit's do; <br/> is a line break,
the inline codes' native markup is left out and a <hi>'s text kept. A
TMX with no times is a translation memory, not subtitles, and is refused
with a message saying so. Writing gives the header (Subtitld's name, the
original's language as srclang, plain text), a unit a subtitle with
Subtitle Edit's two props, the original in the project's language and a
<tuv> for each translation it has; line breaks as Subtitle Edit writes
them. Subtitle Edit writes only the original, and its srclang the language's
country for any but English ("BR"): such a code is read as written.
"""

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules.transcripts import plain_text

_XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
_TIME = re.compile(r'(\d+)[:.,;](\d{1,2})[:.,;](\d{1,2})[:.,;](\d{1,3})$')


def looks_like(head):
    """Whether bytes or decoded text open a TMX document (<tmx> root)."""
    if isinstance(head, bytes):
        head = head[:2048].decode('utf-16' if head.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8', 'replace')
    text = re.sub(r'<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>', '', str(head or ''), flags=re.S).lstrip('﻿ \t\r\n')
    return bool(re.match(r'<tmx[\s>]', text))


def _seconds(value):
    match = _TIME.match((value or '').strip())
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, '0')) / 1000


def _seg_text(seg):
    """A seg's text: line breaks for <br/>, a <hi>'s text, the inline
    codes' native markup left out."""
    out = [seg.text or '']
    for child in seg:
        tag = child.tag.rsplit('}', 1)[-1].lower()
        if tag == 'br':
            out.append('\n')
        elif tag == 'hi':
            out.append(_seg_text(child))
        out.append(child.tail or '')
    text = ''.join(out)
    # Files that carry the tags as escaped text (Subtitle Edit reads those too).
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    return '\n'.join(line.strip() for line in text.strip().split('\n'))


def _same_language(one, other):
    return str(one or '').replace('_', '-').split('-')[0].lower() == str(other or '').replace('_', '-').split('-')[0].lower()


def parse(data):
    """(subtitles, the originals' language or None) of a TMX document
    (bytes or text), translations under each subtitle's 'translations' by
    the file's language codes; ValueError when it is not one or holds no
    times."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f'Not well-formed XML: {error}') from error
    if root.tag != 'tmx':
        raise ValueError(f'Not a TMX document: its root is <{root.tag}>')
    header = root.find('header')
    source = header.get('srclang') if header is not None else None
    source = None if source in (None, '', '*all*') else source
    units = root.findall('body/tu')
    subtitles, language = [], None
    for unit in units:
        props = {prop.get('type'): prop.text for prop in unit.findall('prop')}
        start, end = _seconds(props.get('start')), _seconds(props.get('end'))
        variants = [(tuv.get(_XML_LANG) or tuv.get('lang') or '', _seg_text(tuv.find('seg')))
                    for tuv in unit.findall('tuv') if tuv.find('seg') is not None]
        if start is None or end is None or not variants:
            continue
        original = next((variant for variant in variants if source and _same_language(variant[0], source)), variants[0])
        if not original[1]:
            continue
        language = language or original[0] or source
        subtitle = {'start': round(start, 3), 'end': round(max(start, end), 3), 'text': original[1]}
        translations = {code: text for code, text in variants if (code, text) != original and code and text}
        if translations:
            subtitle['translations'] = translations
        subtitles.append(subtitle)
    if units and not subtitles:
        raise ValueError('This TMX has no times (no "start" and "end" props): it is a translation memory, not subtitles')
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return subtitles, language


def read(path):
    with open(path, 'rb') as tmx_file:
        return parse(tmx_file.read())


def _time(seconds):
    millis = round(max(0.0, float(seconds)) * 1000)
    return '%02d:%02d:%02d,%03d' % (millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, millis % 1000)


def _seg(text):
    return '<br />'.join(escape(line) for line in text.split('\n'))


def write(segments, language='en', version=''):
    """A TMX 1.4 document of `segments`: a unit a subtitle, the original in
    `language` and a <tuv> for each translation."""
    from subtitld.modules.export_options import language_tag
    source = language_tag(language or 'en')
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tmx version="1.4">',
           f'  <header creationtool="Subtitld" creationtoolversion={quoteattr(str(version or "1"))} datatype="plaintext" '
           f'segtype="block" adminlang="en-US" srclang={quoteattr(source)} o-tmf="Subtitld" o-encoding="UTF-8"/>',
           '  <body>']
    number = 0
    for segment in sorted(segments, key=lambda segment: float(segment.get('start', 0))):
        text = plain_text(segment)
        if not text:
            continue
        number += 1
        out += [f'    <tu tuid="{number:04d}">', f'      <prop type="start">{_time(segment["start"])}</prop>',
                f'      <prop type="end">{_time(segment["end"])}</prop>',
                f'      <tuv xml:lang={quoteattr(source)}><seg>{_seg(text)}</seg></tuv>']
        for code, translated in sorted((segment.get('translations') or {}).items()):
            translated = str(translated or '').strip()
            if translated:
                out.append(f'      <tuv xml:lang={quoteattr(language_tag(code))}><seg>{_seg(translated)}</seg></tuv>')
        out.append('    </tu>')
    out += ['  </body>', '</tmx>']
    return '\n'.join(out) + '\n'


def save(path, segments, language='en', version=''):
    with open(path, 'w', encoding='utf-8', newline='\n') as tmx_file:
        tmx_file.write(write(segments, language, version))
