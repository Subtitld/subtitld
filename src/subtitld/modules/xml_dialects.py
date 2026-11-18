"""The smaller XML subtitle dialects Subtitle Edit recognises: Flash's XML,
the Timed Text drafts of 2006 (and Ooyala's way of writing the April one),
Rhozet Harmonic's titler data, Ninsight's (Ayato's export), Subtitle Edit's
own two "Xml" layouts, XMP's markers (Rdf1) and Combined XML.

What each looks like and how it reads follow Subtitle Edit's readers and
writers (src/libse/SubtitleFormats: FlashXml.cs, TimedText200604.cs,
TimedText200604CData.cs, TimedText200604Ooyala.cs, RhozetHarmonic.cs,
NinsightXml.cs, TimeXml.cs, TimeXml2.cs, Rdf1.cs, CombinedXml.cs); there is
no other description of most of them.

    flash      <tt> with no namespace, <div>, and a <p begin end> a subtitle
               whose text is a CDATA section of "<sub>line<br />line</sub>"
    ttaf2006   TTML in the draft namespace http://www.w3.org/2006/04/ttaf1
               (Adobe's Flash captioning and Ooyala wrote it); Ooyala's
               files leave out the space before xml:lang after the tts
               namespace, and Subtitle Edit's CDATA variant puts a
               subtitle's text, tags and all, in a CDATA section
    rhozet     <TitlerData> of <Data StartTimecode EndTimecode Title>, the
               times HH:MM:SS:FF (or with a ';') in frames
    ninsight   <FILE xmlns="http://www.ninsight.com/Subtitle">: TEXT_CLIPs
               with TIMECODEIN and TIMECODEOUT in frames (at the MODEL's
               FramePerSec), a LIGNE a line of SECTION_TEXTs
    timexml    <Subtitle> of <Paragraph><Number/><StartMilliseconds/>
               <EndMilliseconds/><Text/></Paragraph>
    timexml2   <Subtitles> of <Subtitle><Number/><Start/><End/><Duration/>
               <Text/></Subtitle>, times "HH:MM:SS,mmm"
    rdf1       XMP (rdf:Description with xmpDM:startTime, xmpDM:duration
               and a name in xmpDM:name/rdf:Alt/rdf:li), times in
               milliseconds as Subtitle Edit takes them, or in the
               xmpDM:frameRate a Track around them states
    combined   several TTML documents one after the other in one file,
               each with its own XML declaration

The ones that are TTML (flash, ttaf2006, combined) are read by
modules.ttml, which matches elements by their local names whatever their
namespace: what is done here is only to turn their CDATA into the markup it
stands for (<sub> dropped, <br /> a line break, <i> <b> <u> as spans) and
mend Ooyala's attribute. A subtitle a CDATA section cannot hold is left as
the text it is.

Writing follows Subtitle Edit's writers, for the five it writes: flash,
ttaf2006 (the plain variant: lines and <br/>, italics as a span with
tts:fontStyle, which Subtitle Edit's reader takes), rhozet (the style
<Data> first, as Subtitle Edit writes it, frames at the rate given),
timexml and timexml2. Ninsight's, Rdf1 and Combined XML are read only:
Subtitle Edit writes none of Rdf1 or Combined XML, and its Ninsight writer
puts every subtitle's text in the first clip (an XPath from the document's
root), so there is no working layout to follow. Flash's times are written
with three digits of milliseconds; Subtitle Edit writes two when there are
fewer than 100 ("00:00:01.50" for 50 ms), which a TTML reader takes as
half a second, so that quirk is not copied.

What a round trip loses: everything but the times and the text in the
non-TTML dialects (and all formatting in timexml, timexml2, rhozet and
flash, which Subtitle Edit writes plain); places, in all.
"""

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import markup, ttml

DEFAULT_FPS = 25.0
DIALECTS = {
    'flash': 'Flash XML',
    'ttaf2006': 'Timed Text draft 2006-04',
    'rhozet': 'Rhozet Harmonic',
    'timexml': 'Subtitle Edit Xml',
    'timexml2': 'Subtitle Edit Xml 2',
    'ninsight': 'Ninsight XML',
    'rdf1': 'XMP markers (Rdf1)',
    'combined': 'Combined XML',
}
WRITABLE = ('flash', 'ttaf2006', 'rhozet', 'timexml', 'timexml2')
READ_ONLY = tuple(name for name in DIALECTS if name not in WRITABLE)

TTAF_2006 = 'http://www.w3.org/2006/04/ttaf1'
NINSIGHT = 'http://www.ninsight.com/Subtitle'
_RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
_XMP_DM = 'http://ns.adobe.com/xmp/1.0/DynamicMedia/'
_DECLARATION = re.compile(r'<\?xml[^>]*\?>')
# The first element of a document: past the declaration, comments and a doctype.
_ROOT = re.compile(r'^(?:\s|<\?[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>)*<([\w:.-]+)([^>]*)>', re.DOTALL)
_CDATA = re.compile(r'<!\[CDATA\[(.*?)\]\]>', re.DOTALL)
_OOYALA = re.compile(r'(#styling")(?=xml:lang)')
_TAGS = {'i': 'fontStyle="italic"', 'b': 'fontWeight="bold"', 'u': 'textDecoration="underline"'}


def _root(text):
    """(the root element's name, its attributes as written) or ('', '')."""
    match = _ROOT.match((text or '').lstrip('﻿'))
    return (match.group(1), match.group(2)) if match else ('', '')


def looks_like(text):
    """Which dialect (a key of DIALECTS) the decoded text of an XML file is,
    or '' for none; by its root element and namespace, as Subtitle Edit
    tells them. `text` may be the start of a file, but Combined XML is told
    by a second declaration, which only the whole text has."""
    text = (text or '').lstrip('﻿')
    name, attributes = _root(text)
    local = name.rsplit(':', 1)[-1]
    if len(_DECLARATION.findall(text)) > 1 and '<tt' in text:
        return 'combined'
    if local == 'tt':
        if TTAF_2006 in attributes:
            return 'ttaf2006'
        if 'xmlns' not in attributes and '<sub>' in text and '<div' in text:
            return 'flash'
        return ''
    if local == 'TitlerData' and '<Data' in text:
        return 'rhozet'
    if '<TEXT_TRACK' in text and (NINSIGHT in attributes or local == 'FILE'):
        return 'ninsight'
    if local == 'Subtitle' and '<Paragraph>' in text and '<StartMilliseconds>' in text:
        return 'timexml'
    if local == 'Subtitles' and '<Duration>' in text and '<Text>' in text:
        return 'timexml2'
    if local in ('xmpmeta', 'RDF') and '<rdf:' in text and 'startTime' in text:
        return 'rdf1'
    return ''


# Reading

def _cdata_markup(content):
    """A CDATA section's text as TTML: <sub> dropped, <br/> kept, <i> <b>
    <u> as spans, the rest escaped."""
    content = re.sub(r'</?sub>', '', content, flags=re.IGNORECASE)
    out = []
    for piece in re.split(r'(<[^>]*>)', content):
        tag = re.fullmatch(r'<\s*(/?)\s*(br|i|b|u)\s*/?\s*>', piece, flags=re.IGNORECASE)
        if not tag:
            out.append(escape(piece))
        elif tag.group(2).lower() == 'br':
            out.append('<br/>')
        else:
            out.append('</span>' if tag.group(1) else f'<span {_TAGS[tag.group(2).lower()]}>')
    joined = ''.join(out)
    try:
        ET.fromstring(f'<p xmlns:tts="x">{joined}</p>')
    except ET.ParseError:
        # Tags that do not nest: the text alone.
        return escape(markup.plain(content))
    return joined


def _as_ttml(text):
    """TTML text that modules.ttml reads, of a TTML-like dialect."""
    text = _OOYALA.sub(r'\1 ', text)
    return _CDATA.sub(lambda match: _cdata_markup(match.group(1)), text)


def _frames_time(value, fps):
    parts = re.split(r'[:;.,]', str(value or '').strip())
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        raise ValueError(f'Not a timecode: {value!r}')
    hours, minutes, seconds, frames = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _children(element, name):
    return [child for child in element if _local(child.tag) == name]


def _child_text(element, name):
    found = _children(element, name)
    return (found[0].text or '') if found else None


def _xml(text):
    return ET.fromstring(_DECLARATION.sub('', text.lstrip('﻿'), count=1))


def _read_rhozet(text, fps):
    out = []
    for data in _children(_xml(text), 'Data'):
        if data.get('Title') is None or data.get('StartTimecode') is None:
            continue                                    # the style <Data>
        out.append({'start': _frames_time(data.get('StartTimecode'), fps),
                    'end': _frames_time(data.get('EndTimecode'), fps), 'text': data.get('Title').strip()})
    return out


def _read_ninsight(text, fps):
    # Subtitle Edit takes the doctype out before parsing: it names a DTD
    # that is not there.
    text = re.sub(r'<!DOCTYPE[^>]*>', '', text, count=1)
    root = _xml(text)
    model = next((element for element in root.iter() if _local(element.tag) == 'MODEL'), None)
    if model is not None and model.get('FramePerSec'):
        try:
            fps = float(model.get('FramePerSec')) / float(model.get('FramePerSecDivisor') or 1) or fps
        except ValueError:
            pass
    out = []
    for clip in (element for element in root.iter() if _local(element.tag) == 'TEXT_CLIP'):
        if clip.get('TIMECODEIN') is None or clip.get('TIMECODEOUT') is None:
            continue
        lines = []
        for line in (element for element in clip.iter() if _local(element.tag) == 'LIGNE'):
            pieces = []
            for section in (element for element in line.iter() if _local(element.tag) == 'SECTION_TEXT'):
                words = _child_text(section, 'TEXT') or ''
                style = next((element for element in section.iter() if _local(element.tag) == 'STYLE'), None)
                if words and style is not None and style.get('Italic') == 'true':
                    words = f'<i>{words}</i>'
                pieces.append(words)
            if ''.join(pieces).strip():
                lines.append(''.join(pieces).strip())
        if not lines:
            # Not laid out in lines: the clip's text, as Subtitle Edit reads it.
            lines = [''.join(clip.itertext()).strip()]
        out.append({'start': float(clip.get('TIMECODEIN')) / fps, 'end': float(clip.get('TIMECODEOUT')) / fps,
                    'text': '\n'.join(lines)})
    return out


def _read_timexml(text):
    out = []
    for paragraph in _children(_xml(text), 'Paragraph'):
        start, end = _child_text(paragraph, 'StartMilliseconds'), _child_text(paragraph, 'EndMilliseconds')
        if start is None or end is None:
            continue
        out.append({'start': float(start) / 1000, 'end': float(end) / 1000,
                    'text': (_child_text(paragraph, 'Text') or '').strip()})
    return out


def _clock(value):
    parts = re.split(r'[:;.,]', str(value or '').strip())
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        raise ValueError(f'Not a time: {value!r}')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 1000


def _read_timexml2(text):
    out = []
    for subtitle in _children(_xml(text), 'Subtitle'):
        start, end = _child_text(subtitle, 'Start'), _child_text(subtitle, 'End')
        if start is None or end is None:
            continue
        out.append({'start': _clock(start), 'end': _clock(end), 'text': (_child_text(subtitle, 'Text') or '').strip()})
    return out


def _xmp_rate(value):
    """Units a second of an xmpDM:frameRate ("f25", "f30000s1001", "f1000")."""
    match = re.fullmatch(r'f(\d+)(?:s(\d+))?', str(value or '').strip())
    return int(match.group(1)) / int(match.group(2) or 1) if match and int(match.group(1)) else None


def _read_rdf1(text):
    root = _xml(text)
    parents = {child: parent for parent in root.iter() for child in parent}
    out = []
    for description in root.iter('{%s}Description' % _RDF):
        start = description.get('{%s}startTime' % _XMP_DM)
        duration = description.get('{%s}duration' % _XMP_DM)
        name = description.find('{%s}name/{%s}Alt/{%s}li' % (_XMP_DM, _RDF, _RDF))
        if start is None or duration is None or name is None:
            continue
        # A Track around the markers may say what they count in.
        rate, at = None, description
        while at is not None and rate is None:
            rate = _xmp_rate(at.get('{%s}frameRate' % _XMP_DM))
            at = parents.get(at)
        rate = rate or 1000.0
        words = (name.text or '').strip()
        if words.startswith('{\\rtf1 '):
            # Not really RTF, as Subtitle Edit puts it.
            words = words[6:].replace('\\par', '\n').replace('\n ', '\n').rstrip('}').strip()
        out.append({'start': float(start) / rate, 'end': (float(start) + float(duration)) / rate, 'text': words})
    return out


def _merged(segments, gap=0.25):
    """Neighbours with the same text, `gap` seconds apart or less, as one."""
    out = []
    for segment in sorted(segments, key=lambda s: (s['start'], s['end'])):
        if out and out[-1]['text'] == segment['text'] and segment['start'] - out[-1]['end'] <= gap:
            out[-1]['end'] = max(out[-1]['end'], segment['end'])
        else:
            out.append(dict(segment))
    return out


def _read_combined(text):
    starts = [match.start() for match in _DECLARATION.finditer(text)]
    found, language, metadata = [], '', {}
    for start, end in zip(starts, starts[1:] + [len(text)]):
        part = text[start:end].strip()
        if '<tt' not in part:
            continue
        segments, part_language, part_metadata = ttml.read_text(part)
        found += segments
        language = language or part_language
        metadata = metadata or part_metadata
    return _merged(found), language, metadata


def read_text(text, dialect=None, fps=None):
    """(subtitles, language, metadata) of the decoded text of an XML file in
    `dialect` (or the one looks_like finds); times in frames count at `fps`,
    else 25. ValueError (or ET.ParseError) when it cannot be read."""
    dialect = dialect or looks_like(text)
    fps = float(fps or DEFAULT_FPS)
    if dialect in ('flash', 'ttaf2006'):
        segments, language, metadata = ttml.read_text(_as_ttml(text))
    elif dialect == 'combined':
        segments, language, metadata = _read_combined(text)
    elif dialect in DIALECTS:
        reader = {'rhozet': lambda: _read_rhozet(text, fps), 'ninsight': lambda: _read_ninsight(text, fps),
                  'timexml': lambda: _read_timexml(text), 'timexml2': lambda: _read_timexml2(text),
                  'rdf1': lambda: _read_rdf1(text)}[dialect]
        segments, language, metadata = reader(), '', {}
    else:
        raise ValueError('This XML is none of the dialects Subtitld knows')
    for segment in segments:
        segment['start'], segment['end'] = round(segment['start'], 3), round(segment['end'], 3)
    segments = [segment for segment in segments if str(segment.get('text') or '').strip()]
    segments.sort(key=lambda s: (s['start'], s['end']))
    return segments, language, metadata


def read(path, fps=None):
    """(subtitles, language, metadata, dialect) of the XML file at `path`."""
    with open(path, 'rb') as xml_file:
        text = ttml._text_of(xml_file.read())
    dialect = looks_like(text)
    segments, language, metadata = read_text(text, dialect, fps)
    return segments, language, metadata, dialect


# Writing

def _plain(segment):
    return markup.plain(markup.split_lead(str(segment.get('text') or ''))[1]).strip()


def _millis(seconds):
    return int(round(float(seconds) * 1000))


def _clock_text(seconds, separator='.'):
    millis = _millis(seconds)
    return '%02d:%02d:%02d%s%03d' % (millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, separator, millis % 1000)


def _short_clock(seconds):
    """Subtitle Edit's short time: "1,500", "1:02,500", "1:01:02,500"."""
    millis = _millis(seconds)
    hours, minutes, whole, part = millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, millis % 1000
    if hours:
        return '%d:%02d:%02d,%03d' % (hours, minutes, whole, part)
    if minutes:
        return '%d:%02d,%03d' % (minutes, whole, part)
    return '%d,%03d' % (whole, part)


def _frames_text(seconds, fps):
    """HH:MM:SS:FF, frames rounded and carried as Subtitle Edit does."""
    millis = _millis(seconds)
    whole, part = divmod(millis, 1000)
    frames = round(part / (1000.0 / fps))
    if frames >= fps - 0.001:
        whole, frames = whole + 1, 0
    return '%02d:%02d:%02d:%02d' % (whole // 3600, whole // 60 % 60, whole % 60, frames)


def _attribute(text):
    """`text` as an XML attribute's value, its line breaks kept."""
    return quoteattr(text, {'\n': '&#xA;', '\r': '&#xD;', '\t': '&#x9;'})


def _ttaf_content(segment):
    """A subtitle as the 2006 draft's paragraph content: lines and <br/>,
    italics as spans."""
    out = []
    for run, style in markup.without_readings(markup.spans(markup.split_lead(str(segment.get('text') or ''))[1])):
        chunk = '<br/>'.join(escape(line) for line in run.split('\n'))
        out.append(f'<span tts:fontStyle="italic">{chunk}</span>' if style.italic and run.strip() else chunk)
    return ''.join(out).strip()


def write(segments, dialect, fps=None, title=''):
    """The XML text of `segments` in `dialect` (one of WRITABLE)."""
    if dialect not in WRITABLE:
        raise ValueError(f'Subtitld does not write {DIALECTS.get(dialect, dialect)}')
    fps = float(fps or DEFAULT_FPS)
    shown = sorted((segment for segment in segments if _plain(segment)), key=lambda s: (s.get('start', 0), s.get('end', 0)))
    lines = []
    if dialect == 'flash':
        lines += ['<?xml version="1.0" encoding="utf-8"?>', '<tt>', '  <div>']
        for segment in shown:
            text = _plain(segment).replace('\n', '<br />')
            content = f'<![CDATA[<sub>{text}</sub>]]>' if ']]>' not in text else escape(text)
            lines.append(f'    <p begin="{_clock_text(segment["start"])}" end="{_clock_text(segment["end"])}">{content}</p>')
        lines += ['  </div>', '</tt>']
    elif dialect == 'ttaf2006':
        lines += ['<?xml version="1.0" encoding="utf-8"?>',
                  f'<tt xmlns="{TTAF_2006}" xmlns:tts="{TTAF_2006}#styling">',
                  '  <head>', '    <styling>',
                  '      <style id="defaultSpeaker" tts:fontSize="12px" tts:fontFamily="SansSerif" tts:fontWeight="normal" '
                  'tts:fontStyle="normal" tts:textDecoration="none" tts:color="white" tts:backgroundColor="black" '
                  'tts:textAlign="center" />',
                  '    </styling>', '  </head>', '  <body id="thebody" style="defaultSpeaker">', '    <div>']
        for number, segment in enumerate(shown):
            lines.append(f'      <p begin="{_clock_text(segment["start"])}" id="p{number}" '
                         f'end="{_clock_text(segment["end"])}">{_ttaf_content(segment)}</p>')
        lines += ['    </div>', '  </body>', '</tt>']
    elif dialect == 'rhozet':
        lines += ['<?xml version="1.0"?>', '<TitlerData>',
                  "<Data CharSize='0.2' PosX='0.5' PosY='0.75' ColorR='245' ColorG='245' ColorB='245' Transparency='0.0' "
                  "ShadowSize='0.5' BkgEnable='1' BkgExtraWidth='0.02' BkgExtraHeight='0.02'/>"]
        for segment in shown:
            title_value = _attribute(_plain(segment))
            if title_value.startswith('"') and "'" not in title_value:
                title_value = "'" + title_value[1:-1].replace('&quot;', '"') + "'"
            lines.append(f"<Data StartTimecode='{_frames_text(segment['start'], fps)}' "
                         f"EndTimecode='{_frames_text(segment['end'], fps)}' Title={title_value}/>")
        lines.append('</TitlerData>')
    elif dialect == 'timexml':
        lines += ['<?xml version="1.0" encoding="utf-8"?>', '<Subtitle>']
        for number, segment in enumerate(shown, 1):
            lines += ['  <Paragraph>', f'    <Number>{number}</Number>',
                      f'    <StartMilliseconds>{_millis(segment["start"])}</StartMilliseconds>',
                      f'    <EndMilliseconds>{_millis(segment["end"])}</EndMilliseconds>',
                      f'    <Text>{escape(_plain(segment))}</Text>', '  </Paragraph>']
        lines.append('</Subtitle>')
    elif dialect == 'timexml2':
        lines += ['<?xml version="1.0" encoding="utf-8"?>', '<Subtitles>']
        for number, segment in enumerate(shown, 1):
            lines += ['  <Subtitle>', f'    <Number>{number}</Number>',
                      f'    <Start>{_clock_text(segment["start"], ",")}</Start>',
                      f'    <End>{_clock_text(segment["end"], ",")}</End>',
                      f'    <Duration>{_short_clock(float(segment["end"]) - float(segment["start"]))}</Duration>',
                      f'    <Text>{escape(_plain(segment))}</Text>', '  </Subtitle>']
        lines.append('</Subtitles>')
    return '\n'.join(lines) + '\n'


def save(path, segments, dialect, fps=None):
    with open(path, 'w', encoding='utf-8', newline='\n') as xml_file:
        xml_file.write(write(segments, dialect, fps=fps))
