"""Softel Swift: its text exports (.txt, three layouts), its XML
interchange (.xif) and the Swift Interchange File (.sif), read and
written.

Softel never published them. What is here follows Subtitle Edit (libse,
SwiftText.cs, SwiftTextLineNumber.cs, SwiftTextLineNoAndDur.cs, Xif.cs and
SwiftInterchange2.cs, GPL-3 as Subtitld is), whose examples come from
files:

Text, a block a subtitle and a blank line after it, in one of three
layouts (LAYOUTS) told apart by the first line of a block:

    TIMEIN: 01:00:04:12	DURATION: 04:25	TIMEOUT: 01:00:09:07      'duration'
    SUBTITLE: 2	TIMEIN: 00:00:05:12	TIMEOUT: 00:00:10:02          'number'
    SUBTITLE: 1	TIMEIN: 00:00:07:01	DURATION: 03:11	TIMEOUT: ...  'number-duration'

then its lines. The times are HH:MM:SS:FF, DURATION SS:FF; any field can
be dashes ("--:--:--:--") for none. The 'duration' layout ends a subtitle
by its DURATION when it says one, else by TIMEOUT; the others by TIMEOUT
(and here, where it is dashes, by DURATION).

XIF, an XML document: a FileHeader of settings (written as Subtitle Edit
writes it, with the rate in its TimingStandard, SMPTE25 and the like) and
a FileBody with a ContentBlock a subtitle: ThreadedObject/TimingObject's
TimeIn and TimeOut (value="HH:MM:SS:FF"), and the Paragraph in
Content/SubtitleText, whose Text elements are the text and HardReturn the
line breaks. Its Row, ForegroundColour, StartState and Boxing elements are
written as Subtitle Edit writes them and not read.

SIF, text whose lines starting "#" are commands: a header (# SWIFT
INTERCHANGE FILE V2, # Originating Swift: Open 25 English (USA), the
dates ...), then a "# SUBTITLE n" a subtitle, its # TIMEIN, # DURATION SS:FF
AUTO and # TIMEOUT, # START ROW and # ALIGN, and its lines, each after a
"# ROW n" (with RETURN but for the last); "<fontstyle-italic>" before the
text makes the subtitle italic.

The frames count within the second at the rate the file says (XIF's
TimingStandard, SIF's Originating Swift), else the video's, else 25.
Italics are kept in SIF alone, as Subtitle Edit keeps them; the text
layouts and XIF hold the plain text. Subtitle Edit writes a duration's
whole seconds within the minute for SIF and the 'number-duration' layout;
Subtitld writes them all, as Subtitle Edit does for the 'duration'
layout, since a reader that takes the DURATION would lose the minutes.
"""

import datetime
import re
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import markup, srt

DEFAULT_FPS = 25.0
LAYOUTS = ('duration', 'number', 'number-duration')
_TC = r'[0-9-]+:[0-9-]+:[0-9-]+:[0-9-]+'
_DURATION = r'[0-9-]+:[0-9-]+'
_LINES = {
    'duration': re.compile(rf'^TIMEIN:\s*({_TC})\s*DURATION:\s*({_DURATION})\s*TIMEOUT:\s*({_TC})$'),
    'number': re.compile(rf'^SUBTITLE: \d+\s+TIMEIN:\s*({_TC})\s*TIMEOUT:\s*({_TC})$'),
    'number-duration': re.compile(rf'^SUBTITLE: \d+\s+TIMEIN:\s*({_TC})\s*DURATION:\s*({_DURATION})\s+TIMEOUT:\s*({_TC})$'),
}
_ITALIC_PREFIX = '<fontstyle-italic>'


# -- Times --------------------------------------------------------------------

def _seconds(text, fps):
    """Seconds for HH:MM:SS:FF, or None for dashes or anything else."""
    parts = (text or '').strip().replace('.', ':').split(':')
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, seconds, frames = (int(part) for part in parts)
    return round(hours * 3600 + minutes * 60 + seconds + min(frames / fps, 0.999), 3)


def _duration(text, fps):
    """Seconds for SS:FF, or None."""
    parts = (text or '').strip().split(':')
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return round(int(parts[0]) + min(int(parts[1]) / fps, 0.999), 3)


def _frames(seconds, fps):
    """(whole seconds, frames) for a time, the frames those of its second."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= int(round(fps)):
        whole, frames = whole + 1, 0
    return whole, frames


def _timecode(seconds, fps):
    whole, frames = _frames(seconds, fps)
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{second:02d}:{frames:02d}'


def _length(start, end, fps):
    """DURATION's SS:FF, from the times as written, so it adds up to them."""
    first, last = _frames(start, fps), _frames(end, fps)
    count = (last[0] * round(fps) + last[1]) - (first[0] * round(fps) + first[1])
    seconds, frames = divmod(max(0, count), round(fps))
    return f'{seconds:02d}:{frames:02d}'


def _ordered(segments):
    return sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))


def _plain_lines(text):
    """The lines of a subtitle's plain text, and whether all of it is
    italic."""
    _lead, rest = markup.split_lead(str(text or '').replace('\r\n', '\n').replace('\r', '\n'))
    runs = markup.without_readings(markup.spans(rest))
    letters = [style for run, style in runs if run.strip()]
    lines = [line.strip() for line in ''.join(run for run, _style in runs).split('\n') if line.strip()]
    return lines, bool(letters) and all(style.italic for style in letters)


# -- Text ---------------------------------------------------------------------

def looks_like(head):
    """The layout (one of LAYOUTS) of the Swift text file `head` (its
    decoded text or first bytes) is in, or None when it is not one."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:16384]).decode('latin-1')
    lines = str(head or '')[:16384].splitlines()
    if lines and '{QTtext}' in lines[0]:
        return None
    for line in lines:
        line = line.strip()
        for layout in ('number-duration', 'number', 'duration'):
            if _LINES[layout].match(line):
                return layout
    return None


def parse_text(text, fps=None):
    """(subtitles, {'fps', 'layout'}) in a Swift text file's text."""
    fps = float(fps or DEFAULT_FPS)
    layout = looks_like(text) or 'duration'
    pattern = _LINES[layout]
    segments, current, body = [], None, []

    def close():
        if current and current[1] is not None:
            lines = [line.rstrip() for line in body if line.strip()]
            if lines:
                segments.append({'start': current[0], 'end': max(current[0], current[1]), 'text': '\n'.join(lines)})

    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        match = pattern.match(line.strip())
        if match:
            close()
            start = _seconds(match.group(1), fps)
            if start is None:
                current, body = None, []
                continue
            out = _seconds(match.groups()[-1], fps)
            length = _duration(match.group(2), fps) if layout != 'number' else None
            if layout == 'duration' and length:
                end = start + length
            else:
                end = out if out is not None else (start + length if length else None)
            current, body = (start, round(end, 3) if end is not None else None), []
        elif not line.strip():
            close()
            current, body = None, []
        elif current:
            body.append(line)
    close()
    return segments, {'fps': fps, 'layout': layout}


def write_text(segments, fps=DEFAULT_FPS, layout='duration'):
    """Swift text in `layout` (one of LAYOUTS) for `segments`, frames at
    `fps`."""
    fps = float(fps or DEFAULT_FPS)
    layout = layout if layout in LAYOUTS else 'duration'
    blocks = []
    for segment in _ordered(segments):
        lines, _italic = _plain_lines(segment.get('text'))
        if not lines:
            continue
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        times = [f'TIMEIN: {_timecode(start, fps)}']
        if layout != 'number':
            times.append(f'DURATION: {_length(start, end, fps)}')
        times.append(f'TIMEOUT: {_timecode(end, fps)}')
        if layout != 'duration':
            times.insert(0, f'SUBTITLE: {len(blocks) + 1}')
        blocks.append('\t'.join(times) + '\r\n' + '\r\n'.join(lines) + '\r\n')
    return '\r\n'.join(blocks)


# -- XIF ----------------------------------------------------------------------

def looks_like_xif(head):
    """True when `head` (decoded text or first bytes) is a XIF document."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:4096]).decode('utf-8', 'ignore')
    return '<XIF' in str(head or '')[:4096]


def parse_xif(text, fps=None):
    """(subtitles, {'fps'}) in a XIF document's text. Raises ValueError for
    text that is not one."""
    if not looks_like_xif(text):
        raise ValueError('Not a XIF file: it has no XIF element')
    try:
        root = ET.fromstring(text.encode('utf-8') if isinstance(text, str) else text)
    except ET.ParseError as error:
        raise ValueError(f'A damaged XIF file: {error}') from error
    standard = root.find('FileHeader/TimingStandards/TimingStandard')
    said = re.search(r'(\d+(?:\.\d+)?)', standard.get('Type', '')) if standard is not None else None
    fps = float(said.group(1)) if said and float(said.group(1)) > 0 else float(fps or DEFAULT_FPS)
    segments = []
    for block in root.findall('FileBody/ContentBlock'):
        time_in = block.find('ThreadedObject/TimingObject/TimeIn')
        time_out = block.find('ThreadedObject/TimingObject/TimeOut')
        paragraph = block.find('ThreadedObject/Content/SubtitleText/Paragraph')
        if time_in is None or time_out is None or paragraph is None:
            continue
        start, end = _seconds(time_in.get('value'), fps), _seconds(time_out.get('value'), fps)
        if start is None or end is None:
            continue
        lines = ['']
        for child in paragraph:
            if child.tag == 'Text':
                lines[-1] += ' ' + (''.join(child.itertext()))
            elif child.tag == 'HardReturn':
                lines.append('')
        lines = [re.sub(r'\s+', ' ', line).strip() for line in lines]
        body = '\n'.join(line for line in lines if line)
        if body:
            segments.append({'start': start, 'end': max(start, end), 'text': body})
    return segments, {'fps': fps}


_XIF_HEADER = '''<?xml version="1.0" encoding="utf-8"?>
<XIF version="1.0" filename="file:///filename.xif">
  <FileHeader>
    <OriginatorSoftware Name="FileConverter" Version="1.01"/>
    <GlobalFileInfo ProgrammeName={title} Format="Open" Language="English" StartTime="00:00:00:00" StopTime="{stop}" TotalLength="00:00:00:00" ProgrammeTitle={title} NumberOfCaptions="{count}" RevisionNumber="0" CountryIndex="0" CharacterCodeTable="0" MaxColumns="0" EpisodeNumber="0" BlinkOption="yes" CaptionService="0" CurrentVideo="2" Videosize="0" BaseOffset="0" TopOffset="0" DefaultSave="0" DisplaySafwWidth="576" SafeAreaWidthPercent="80" DisplaySafeHoriz="0" SafeAreaHorizPos="0" CurrentStyle="None" StyleDate="None" StyleTime="None" SAOLeft="72" SAORight="0" VideoStartTC="00:00:00:00" SpellLanguage="English" CGConfig="0"/>
    <ReadingSpeeds>
      <ReadingSpeed ID="0" WordsPerMinute="180" CharsPerMinute="720" CharsPerSecond="12" PTS="WPM"/>
    </ReadingSpeeds>
    <Canvases>
      <Canvas ID="0" Width="720" Height="576" SAWidth="576" SAHeight="460" SATop="57" SALeft="72"/>
    </Canvases>
    <FormattingInfo TabWidth="3"/>
    <TimingStandards>
      <TimingStandard ID="0" Type="SMPTE{rate}"/>
    </TimingStandards>
    <Fonts>
      <Font ID="0" Type="True Type" Typeface="Courier New" Height="0" AstonFountSize="185" Ascent="268" Descent="97" IntLead="43"/>
      <Font ID="1" Type="True Type" Typeface="Courier New" Height="0" AstonFountSize="185" Ascent="268" Descent="97" IntLead="43"/>
      <Font ID="2" Type="True Type" Typeface="Hairy Helix" Height="0" AstonFountSize="12" Ascent="15" Descent="3" IntLead="2"/>
      <Font ID="3" Type="True Type" Typeface="Hairy Helix Italics" Height="0" AstonFountSize="12" Ascent="15" Descent="3" IntLead="2"/>
      <Font ID="4" Type="True Type" Typeface="Arial" Height="35" AstonFountSize="22" Ascent="28" Descent="7" IntLead="5"/>
      <Font ID="5" Type="True Type" Typeface="Arial Narrow" Height="35" AstonFountSize="25" Ascent="30" Descent="5" IntLead="0"/>
      <Font/>
    </Fonts>
    <Colours>
      <Colour ID="0" value="0x000000"/>
      <Colour ID="1" value="0xFF0000"/>
      <Colour ID="2" value="0x00FF00"/>
      <Colour ID="3" value="0xFFFF00"/>
      <Colour ID="4" value="0x0000FF"/>
      <Colour ID="5" value="0xFF00FF"/>
      <Colour ID="6" value="0x00FFFF"/>
      <Colour ID="7" value="0xFFFFFF"/>
    </Colours>
    <Threads>
      <Thread ID="0" CanvasID="0" TimingStandardID="0" ReadingSpeedID="0" Medium="Open" Name="Default"/>
    </Threads>
    <WordLists/>
    <TxStreams>
      <TxStream ID="0" Active="yes"/>
    </TxStreams>
  </FileHeader>
  <FileBody>
    <ProgrammeBlock NoOfAssociatedIDs="0">
      <SceneBaseBlock NewsID="0" Position="0" NewsRoomInternalID="0"/>
    </ProgrammeBlock>
    <StoryBlock TimingType="Manual">
      <SceneBaseBlock NewsID="0" Position="0" NewsRoomInternalID="0"/>
    </StoryBlock>
'''

_XIF_BLOCK = '''    <ContentBlock TXStreamID="0" uid="{uid}">
      <ThreadedObject ID="0" Type="BasicSubtitle" VPos="448">
        <Note/>
        <TimingObject>
          <TimeIn value="{time_in}"/>
          <TimeOut value="{time_out}"/>
        </TimingObject>
        <Content Override="0" RaisedRowUp="0" RaisedRowDown="0" DirectionState="0" PositionState="1" ConfidenceValue="0">
          <SubtitleText>
            <Paragraph Type="Open" Justification="Centre" LineLimit="15" CharactersPerRow="99" Alignment="None" Language="ENG" VideoLanguage="ENG" Xpercent="0.0" Ypercent="0.0">
              <StartState Foreground="7" Background="0" Flash="No" Underline="No" Italic="No" Bold="No" Indent="0" SBOnOff="Yes" FontID="4"/>
              <Boxing Colour="0" Transparency="0" Type="None"/>
              <AntiAliasing Style="Default"/>
{rows}{text}            </Paragraph>
          </SubtitleText>
        </Content>
      </ThreadedObject>
    </ContentBlock>
'''


def write_xif(segments, fps=DEFAULT_FPS, title=''):
    """A XIF document for `segments`, frames at `fps`."""
    fps = float(fps or DEFAULT_FPS)
    blocks, last_start = [], 0.0
    for segment in _ordered(segments):
        lines, _italic = _plain_lines(segment.get('text'))
        if not lines:
            continue
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        last_start = start
        rows = ''.join(f'              <Row Number="{number}" JustificationOverride="Centre" Highlight="0"/>\n'
                       for number in range(1, len(lines) + 1))
        text = ''.join(('              <HardReturn/>\n' if number else '')
                       + '              <ForegroundColour Colour="7"/>\n'
                       + f'              <Text>{escape(line)}</Text>\n' for number, line in enumerate(lines))
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'subtitld:xif:{len(blocks) + 1}')).upper()
        blocks.append(_XIF_BLOCK.format(uid=uid, time_in=_timecode(start, fps), time_out=_timecode(end, fps),
                                        rows=rows, text=text))
    header = _XIF_HEADER.format(title=quoteattr(title or 'Unknown'), stop=_timecode(last_start, fps),
                                count=len(blocks), rate=int(round(fps)))
    return header + ''.join(blocks) + '  </FileBody>\n</XIF>\n'


# -- SIF ----------------------------------------------------------------------

def looks_like_sif(head):
    """True when `head` (decoded text or first bytes) is a Swift
    Interchange File."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:16384]).decode('latin-1')
    text = str(head or '')[:16384]
    return not text.lstrip().startswith('{\\rtf1') and ('# SWIFT INTERCHANGE FILE' in text
                                                        or re.search(r'^# SUBTITLE\b', text, re.MULTILINE) is not None)


def parse_sif(text, fps=None):
    """(subtitles, {'fps'}) in a Swift Interchange File's text."""
    said = re.search(r'^# Originating Swift\s*:\s*\S+\s+(\d+(?:\.\d+)?)', text, re.MULTILINE)
    fps = float(said.group(1)) if said and float(said.group(1)) > 0 else float(fps or DEFAULT_FPS)
    segments, current = [], None

    def close():
        if current and current['start'] is not None and current['lines']:
            body = '\n'.join(current['lines'])
            if body.startswith(_ITALIC_PREFIX):
                body = '<i>' + body[len(_ITALIC_PREFIX):].lstrip() + '</i>'
            end = current['end'] if current['end'] is not None else current['start']
            segments.append({'start': current['start'], 'end': max(current['start'], end), 'text': body})

    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith('# SUBTITLE'):
            close()
            current = {'start': None, 'end': None, 'lines': []}
        elif current is None:
            continue
        elif line.startswith('# TIMEIN'):
            current['start'] = _seconds(line[8:], fps)
        elif line.startswith('# DURATION'):
            length = _duration(line[10:].replace('AUTO', ''), fps)
            if length is not None and current['start'] is not None:
                current['end'] = round(current['start'] + length, 3)
        elif line.startswith('# TIMEOUT'):
            out = _seconds(line[9:], fps)
            if out is not None:
                current['end'] = out
        elif not line.startswith('#') and line.strip():
            current['lines'].append(line.strip())
    close()
    return segments, {'fps': fps}


def write_sif(segments, fps=DEFAULT_FPS, title='', today=None):
    """A Swift Interchange File for `segments`, frames at `fps`; its dates
    `today` (a datetime.date), else today."""
    fps = float(fps or DEFAULT_FPS)
    today = today or datetime.date.today()
    date = f'{today.day:02d}/{today.month:02d}/{today.year}'
    out = ['# SWIFT INTERCHANGE FILE V2', "# DO NOT EDIT LINES BEGINNING WITH '#' SIGN",
           f'# Originating Swift: Open {int(round(fps))} English (USA)',
           f'# VIDEO CLIP : {(title or "Unknown")}.mpg', f'# BROADCAST DATE : {date}', f'# REVISION DATE : {date}',
           f'# CREATION DATE : {date}', '# COUNTRY OF ORIGIN : ENG', '# EPISODE NUMBER : 0',
           f'# DEADLINE DATE : {date}', '# AUTO TX : false', '# CURRENT STYLE : None', '# STYLE DATE : None',
           '# STYLE Time : None', '', '']
    number = 0
    for segment in _ordered(segments):
        lines, italic = _plain_lines(segment.get('text'))
        if not lines:
            continue
        number += 1
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', 0)))
        out += [f'# SUBTITLE {number}', f'# TIMEIN {_timecode(start, fps)}', f'# DURATION {_length(start, end, fps)} AUTO',
                f'# TIMEOUT {_timecode(end, fps)}', '# START ROW BOTTOM', '# ALIGN CENTRE JUSTIFY CENTER']
        if italic:
            lines[0] = _ITALIC_PREFIX + lines[0]
        for row, line in enumerate(lines):
            out += [f'# ROW {row}' + ('' if row == len(lines) - 1 else ' RETURN'), line]
        out.append('')
    return '\r\n'.join(out).rstrip() + '\r\n'


# -- Files --------------------------------------------------------------------

def read(path, fps=None):
    """(subtitles, its settings) in the Swift file at `path`: XIF by its
    .xif, SIF by its .sif, text otherwise."""
    with open(path, 'rb') as swift_file:
        text = srt.decode(swift_file.read())
    if path.lower().endswith('.xif'):
        return parse_xif(text, fps)
    if path.lower().endswith('.sif'):
        return parse_sif(text, fps)
    return parse_text(text, fps)


def save(path, segments, kind='duration', fps=DEFAULT_FPS, title=''):
    """Write `segments` to `path` as Swift: `kind` 'xif', 'sif' or one of
    LAYOUTS for text; UTF-8 (with a byte order mark for the text files, as
    Subtitle Edit saves them)."""
    if kind == 'xif':
        content, encoding = write_xif(segments, fps, title), 'utf-8'
    elif kind == 'sif':
        content, encoding = write_sif(segments, fps, title), 'utf-8-sig'
    else:
        content, encoding = write_text(segments, fps, kind), 'utf-8-sig'
    with open(path, 'w', encoding=encoding, newline='') as swift_file:
        swift_file.write(content)
