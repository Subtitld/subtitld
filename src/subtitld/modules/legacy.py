"""The player-era subtitle formats (.mpl, .pjs, .aqt, .rt ...): read what the
players of the 1990s and 2000s left behind, write the ones worth writing.

Every one of these is a line of times and a line of text, and nearly every
one of them is a different idea of what a time is: tenths of a second in
MPL2 and PJS, frames in AQTitle and Spruce, seconds since the subtitle
before in MPSub, a clock with no end time at all in VPlayer. They are kept
together here because they are too small for a module each, and because
telling them apart is the harder half of reading them: five of them share
.txt and three share .sub, so the extension decides nothing and `kind`
decides everything.

What is read, and written:

- **MPL2** (.mpl, .txt): "[12][35]text", tenths of a second, "|" between
  lines. Read and written.
- **PJS** (.pjs): "12,35,\"text\"", tenths of a second as well, the text in
  quotes. Read and written. Its numbers look like frames and are not.
- **MPSub** (.sub): a FORMAT= header, then "<gap> <duration>" and the text.
  Both counted from the END of the subtitle before, in seconds when the
  header says TIME, in frames when it names a rate. Read and written, in
  time, which needs no rate to be right.
- **VPlayer** and **TMPlayer** (.txt): "00:00:12:text" and its variants
  ("=" or ";" for the colon, a comma inside the clock). No end times: a
  subtitle runs to the next one, and the last for `TAIL`. Read and written.
- **AQTitle** (.aqt): "-->> 25" and the text under it, counted in frames at
  the video's rate; the next marker ends the subtitle. Read and written.
- **SubViewer 1** (.sub): "[00:00:01]", the text, "[00:00:03]". Read and
  written. SubViewer 2 is another format, and Subtitld reads it as SBV.
- **Spruce** (.stl): "00:00:01:00 , 00:00:03:00 , text", frames after the
  seconds, "|" between lines. Read and written. **Not EBU STL**, which is
  binary and shares the extension: `kind` separates them, and so must
  anything else that opens a .stl.
- **RealText** (.rt): RealPlayer's HTML, "<time begin=...>" and the text
  until the next time or a <clear/>. Read and written, though nothing has
  played it in twenty years: a format Subtitld reads and cannot write back
  is a trap for anyone who opens one to fix a typo.
- **TurboTitle** (.tts): "0:00:00.80,0:00:02.28,Narrator The line", the
  Amiga laserdisc-era format, hundredths of a second, the name of whoever
  speaks between the times and the text. Read and written. Its "~" is a
  blank name, and a script still being timed carries "9:59:59.99" for
  both times: those lines are not subtitles yet and are left out.
- **ZeroG** (.zeg): "E 1 0:00:01.00 0:00:03.00 DEFAULT Narrator The line",
  a DOS subtitler's format, hundredths again, "\\n" between lines, with S
  lines for its styles. Read and written; the styles are not read, as
  nothing in Subtitld would show them.

The name field of TurboTitle and ZeroG becomes the subtitle's speaker,
which is what it meant, even though neither player drew it.

Times are seconds in Subtitld. Reading rounds to the millisecond, as the
other formats here do. The frame-counted formats take the video's rate, or
25 where there is no video — the rate they were written at is not in the
file, so a wrong guess shifts them whole, as MicroDVD does.
"""

import re

from subtitld.modules import markup
from subtitld.modules.srt import decode

# A subtitle with no end time, and no subtitle after it, lasts this long.
TAIL = 3.0
DEFAULT_FPS = 25.0

# The extensions each format is written to. A format may be read from any
# file: `kind` looks at the bytes, not at the name.
EXTENSIONS = {
    'mpl2': 'mpl',
    'pjs': 'pjs',
    'mpsub': 'sub',
    'vplayer': 'txt',
    'tmplayer': 'txt',
    'aqtitle': 'aqt',
    'subviewer1': 'sub',
    'spruce': 'stl',
    'realtext': 'rt',
    'turbotitle': 'tts',
    'zerog': 'zeg',
    'viplay': 'vsf',
    'sbt': 'sbt',
    'captions32': 'txt',
    'subsonic': 'sub',
    'oresme': 'txt',
    'jacosub': 'jss',
    'sasami': 's2k',
    'xombie': 'xss',
}

# The ones Subtitld writes; RealText is read only.
WRITABLE = ('mpl2', 'pjs', 'mpsub', 'vplayer', 'tmplayer', 'aqtitle', 'subviewer1', 'spruce',
            'realtext', 'turbotitle', 'zerog', 'viplay', 'sbt', 'captions32', 'subsonic', 'oresme',
            'jacosub', 'sasami', 'xombie')

# A TurboTitle script being timed carries this for both times.
_UNTIMED = 9 * 3600 + 59 * 60 + 59.99

# What the session calls each of these formats, and back. TMPlayer and
# VPlayer are one entry: they differ only in the character between the
# clock and the text, which is no difference to anyone choosing a format.
SESSION_FORMATS = {
    'MPL2': 'mpl2', 'PJS': 'pjs', 'MPSUB': 'mpsub', 'VPLAYER': 'vplayer',
    'AQTITLE': 'aqtitle', 'SUBVIEWER1': 'subviewer1', 'SPRUCE': 'spruce',
    'REALTEXT': 'realtext', 'TURBOTITLE': 'turbotitle', 'ZEROG': 'zerog',
    'VIPLAY': 'viplay', 'SBT': 'sbt', 'CAPTIONS32': 'captions32',
    'SUBSONIC': 'subsonic', 'ORESME': 'oresme', 'JACOSUB': 'jacosub',
    'SASAMI': 'sasami', 'XOMBIE': 'xombie',
}
SESSION_IDS = dict({fmt: name for name, fmt in SESSION_FORMATS.items()}, tmplayer='VPLAYER')

_MPL2 = re.compile(r'^\s*\[(\d+)\]\[(\d+)\](.*)$')
_PJS = re.compile(r'^\s*(\d+)\s*,\s*(\d+)\s*,\s*"(.*)"\s*$')
_AQT = re.compile(r'^\s*-->>\s*(\d+)\s*$')
_SUBVIEWER1 = re.compile(r'^\s*\[(\d{1,3}):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?\]\s*$')
_SPRUCE = re.compile(r'^\s*(\d{1,3}):(\d{2}):(\d{2})[:.](\d{1,3})\s*,\s*'
                     r'(\d{1,3}):(\d{2}):(\d{2})[:.](\d{1,3})\s*,\s*(.*)$')
# VPlayer and TMPlayer are the same shape: a clock, a separator, the text.
# The separator is what the players differed on, so all of them are taken.
_CLOCK = re.compile(r'^\s*(\d{1,3}):(\d{1,2})[:,.](\d{1,2})(?:[:,.](\d{1,3}))?\s*[:=;]\s?(.*)$')
_TTS = re.compile(r'^\s*(\d):(\d{2}):(\d{2})\.(\d{2})\s*,\s*(\d):(\d{2}):(\d{2})\.(\d{2})\s*,\s*(\S+)(?:[ \t](.*))?$')
_ZEG = re.compile(r'^\s*E\s+\d+\s+(\d):(\d{2}):(\d{2})\.(\d{2})\s+(\d):(\d{2}):(\d{2})\.(\d{2})\s+'
                  r'(\S+)(?:\s+(\S+))?(?:[ \t](.*))?$')
_VIPLAY_MAGIC = '{* viplay subtitle file *}'
_VIPLAY = re.compile(r'^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-\s*'
                     r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*=(.*)$')
_SBT_TIME = re.compile(r'^\s*(\d{1,3}):(\d{2}):(\d{2})(?:[.,:](\d{1,3}))?\s*$')
# Captions 32 writes the same times as Spruce, in the same order, with the
# same commas. What tells them apart is that its columns are fixed: the
# text is two fields of 33 characters with a "|" between them, and Spruce's
# text is free. The width is in the pattern so the two cannot be confused.
_CAPTIONS32 = re.compile(r'^(\d{2}):(\d{2}):(\d{2}):(\d{2}) , (\d{2}):(\d{2}):(\d{2}):(\d{2}) , (.{33})\|(.*)$')
_SUBSONIC = re.compile(r'^\s*\S+\s+(\d+(?:\.\d+)?)\s+\\\s+~:\\(.*)$')
_SUBSONIC_END = re.compile(r'^\s*\S+\s+(\d+(?:\.\d+)?)\s*$')
_ORESME = re.compile(r'^(\d{2}):(\d{2}):(\d{2}):(\d{2})((?:\{[^}]*\})*)(.*)$')
_TAG = re.compile(r'\{([^}]*)\}')
_JSS = re.compile(r'^\s*(\d{1,3}):(\d{2}):(\d{2})\.(\d{1,3})\s+(\d{1,3}):(\d{2}):(\d{2})\.(\d{1,3})\s+(\S+)\s+(.*)$')
_JSS_DIRECTIVE = re.compile(r'^\s*#\s*(\w+)\s*(.*)$')
_S2K_TIME = re.compile(r'^\s*;\s*Set\.Time\.Start\s*=\s*(\d+)\s*$', re.I)
_S2K_WIDTH = re.compile(r'^\s*;\s*Env\.Movie\.Width\s*=', re.I)
_XSS = re.compile(r'^\s*Line\s*=\s*([^,]*),([^,]*),([^,]*),'
                  r'(\d{1,3}):(\d{2}):(\d{2})\.(\d{1,3}),(\d{1,3}):(\d{2}):(\d{2})\.(\d{1,3}),'
                  r'([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),(.*)$', re.I)
_XSS_KEY = re.compile(r'^\s*(ScreenHorizontal|ScreenVertical|Style|FontName|version)\s*=', re.I)
_MPSUB_HEADER = re.compile(r'^\s*FORMAT\s*=\s*(\S+)\s*$', re.I)
_MPSUB_TIMES = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$')
_RT_TIME = re.compile(r'<time\b[^>]*\bbegin\s*=\s*["\']?([^"\'>\s]+)', re.I)
_RT_END = re.compile(r'<time\b[^>]*\bend\s*=\s*["\']?([^"\'>\s]+)', re.I)
_RT_CLEAR = re.compile(r'<clear\s*/?>', re.I)
# Bold, italic and underline are Subtitld's own tags and stay; every other
# tag goes. The dropping runs FIRST, with the three kept out of its reach,
# so it cannot eat what the keeping just wrote.
_RT_KEEP = re.compile(r'<(/?)([biu])\b[^>]*>', re.I)
_RT_DROP = re.compile(r'<(?!/?[biu]\b)[^>]*>', re.I)


def _rounded(seconds):
    return round(max(0.0, float(seconds)), 3)


def _clock_seconds(hours, minutes, seconds, fraction=None, frames=False, fps=DEFAULT_FPS):
    """Seconds for a clock, its last field centiseconds or frames."""
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / float(fps) if frames else int(fraction) / 10 ** len(fraction)
    return _rounded(value)


def _lines_of(text):
    """The text of a subtitle, its "|" and "[br]" as line breaks, trimmed."""
    body = text.replace('[br]', '\n').replace('|', '\n').replace('\\N', '\n')
    return '\n'.join(line.strip() for line in body.split('\n') if line.strip())


def _packed(segment, separator='|'):
    """A subtitle's text on one line, as the one-line formats write it."""
    text = markup.plain(str(segment.get('text') or '')).replace('\r\n', '\n').replace('\r', '\n')
    return separator.join(line.strip() for line in text.split('\n') if line.strip())


def _ordered(segments):
    """By start, and by end where there is one: the formats with no end
    time are sorted before `_ends_at_next` gives them one."""
    def key(segment):
        start = float(segment.get('start', 0))
        end = segment.get('end')
        return start, float(end) if end is not None else start
    return sorted(segments, key=key)


def _ends_at_next(segments):
    """Give every subtitle with no end the start of the one after it.

    VPlayer, TMPlayer and AQTitle only say when a subtitle begins. A
    decoder shows each until the next, and the last one for a moment.
    """
    for index, segment in enumerate(segments):
        if segment.get('end') is None:
            following = segments[index + 1]['start'] if index + 1 < len(segments) else None
            segment['end'] = following if following is not None else _rounded(segment['start'] + TAIL)
    return segments


# ---------------------------------------------------------------- MPL2


def _parse_mpl2(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _MPL2.match(line)
        if not match:
            continue
        body = _lines_of(match.group(3))
        if not body:
            continue
        start, end = int(match.group(1)) / 10.0, int(match.group(2)) / 10.0
        segments.append({'start': _rounded(start), 'end': _rounded(max(start, end)), 'text': body})
    return segments


def _write_mpl2(segments, fps=DEFAULT_FPS):
    lines = []
    for segment in _ordered(segments):
        body = _packed(segment)
        if not body:
            continue
        start = int(round(float(segment.get('start', 0)) * 10))
        end = max(start, int(round(float(segment.get('end', 0)) * 10)))
        lines.append(f'[{start}][{end}]{body}')
    return '\n'.join(lines) + '\n' if lines else ''


# ----------------------------------------------------------------- PJS


def _parse_pjs(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _PJS.match(line)
        if not match:
            continue
        body = _lines_of(match.group(3))
        if not body:
            continue
        start, end = int(match.group(1)) / 10.0, int(match.group(2)) / 10.0
        segments.append({'start': _rounded(start), 'end': _rounded(max(start, end)), 'text': body})
    return segments


def _write_pjs(segments, fps=DEFAULT_FPS):
    lines = []
    for segment in _ordered(segments):
        body = _packed(segment).replace('"', "'")
        if not body:
            continue
        start = int(round(float(segment.get('start', 0)) * 10))
        end = max(start, int(round(float(segment.get('end', 0)) * 10)))
        lines.append(f'{start},{end},"{body}"')
    return '\n'.join(lines) + '\n' if lines else ''


# --------------------------------------------------------------- MPSub


def _parse_mpsub(text, fps=DEFAULT_FPS):
    """MPSub counts from the end of the subtitle before, in seconds or frames."""
    rate = None                     # None while the header says TIME
    lines = text.split('\n')
    segments = []
    cursor = 0.0
    index = 0
    while index < len(lines):
        header = _MPSUB_HEADER.match(lines[index])
        if header:
            declared = header.group(1).strip().upper()
            if declared != 'TIME':
                try:
                    rate = float(declared)
                except ValueError:
                    rate = None
            index += 1
            continue
        times = _MPSUB_TIMES.match(lines[index])
        if not times:
            index += 1
            continue
        body = []
        index += 1
        while index < len(lines) and lines[index].strip():
            body.append(lines[index].strip())
            index += 1
        gap, length = float(times.group(1)), float(times.group(2))
        if rate:
            gap, length = gap / rate, length / rate
        start = cursor + gap
        cursor = start + length
        text_of = _lines_of('\n'.join(body))
        if text_of:
            segments.append({'start': _rounded(start), 'end': _rounded(cursor), 'text': text_of})
    return segments


def _write_mpsub(segments, fps=DEFAULT_FPS):
    """Written in seconds: a rate the file cannot be sure of is a rate that
    shifts it, and TIME is right at any rate."""
    blocks = []
    cursor = 0.0
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        blocks.append(f'{round(max(0.0, start - cursor), 2)} {round(end - start, 2)}\n'
                      + '\n'.join(line.strip() for line in text.split('\n') if line.strip()))
        cursor = end
    return 'FORMAT=TIME\n\n' + '\n\n'.join(blocks) + '\n' if blocks else 'FORMAT=TIME\n'


# ------------------------------------------------- VPlayer and TMPlayer


def _parse_clock(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _CLOCK.match(line)
        if not match:
            continue
        body = _lines_of(match.group(5))
        if not body:
            continue
        start = _clock_seconds(match.group(1), match.group(2), match.group(3), match.group(4))
        segments.append({'start': start, 'end': None, 'text': body})
    return _ends_at_next(_ordered(segments))


def _write_clock(segments, fps=DEFAULT_FPS, separator=':'):
    lines = []
    for segment in _ordered(segments):
        body = _packed(segment)
        if not body:
            continue
        total = int(float(segment.get('start', 0)))
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        lines.append(f'{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{body}')
    return '\n'.join(lines) + '\n' if lines else ''


# ------------------------------------------------------------- AQTitle


def _parse_aqtitle(text, fps=DEFAULT_FPS):
    rate = float(fps or DEFAULT_FPS)
    lines = text.split('\n')
    segments = []
    index = 0
    while index < len(lines):
        match = _AQT.match(lines[index])
        if not match:
            index += 1
            continue
        body = []
        index += 1
        while index < len(lines) and not _AQT.match(lines[index]):
            if lines[index].strip():
                body.append(lines[index].strip())
            index += 1
        text_of = _lines_of('\n'.join(body))
        if text_of:
            segments.append({'start': _rounded(int(match.group(1)) / rate), 'end': None, 'text': text_of})
    return _ends_at_next(_ordered(segments))


def _write_aqtitle(segments, fps=DEFAULT_FPS):
    rate = float(fps or DEFAULT_FPS)
    blocks = []
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        frame = int(round(float(segment.get('start', 0)) * rate))
        blocks.append(f'-->> {frame}\n' + '\n'.join(line.strip() for line in text.split('\n') if line.strip()))
    return '\n\n'.join(blocks) + '\n' if blocks else ''


# --------------------------------------------------------- SubViewer 1


def _parse_subviewer1(text, fps=DEFAULT_FPS):
    lines = text.split('\n')
    marks = [(index, match) for index, line in enumerate(lines) if (match := _SUBVIEWER1.match(line))]
    segments = []
    for position, (index, match) in enumerate(marks):
        if position + 1 >= len(marks):
            break
        following, next_match = marks[position + 1]
        body = _lines_of('\n'.join(lines[index + 1:following]))
        if not body:
            continue                # the closing mark of the subtitle before
        start = _clock_seconds(*match.groups())
        end = _clock_seconds(*next_match.groups())
        segments.append({'start': start, 'end': _rounded(max(start, end)), 'text': body})
    return segments


def _write_subviewer1(segments, fps=DEFAULT_FPS):
    def mark(seconds):
        total = int(float(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f'[{hours:02d}:{minutes:02d}:{secs:02d}]'

    blocks = []
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        body = '\n'.join(line.strip() for line in text.split('\n') if line.strip())
        blocks.append(f'{mark(segment.get("start", 0))}\n{body}\n{mark(segment.get("end", 0))}')
    return '\n\n'.join(blocks) + '\n' if blocks else ''


# ---------------------------------------------------------------- Spruce


def _parse_spruce(text, fps=DEFAULT_FPS):
    rate = float(fps or DEFAULT_FPS)
    segments = []
    for line in text.split('\n'):
        if line.lstrip().startswith(('$', '//')):
            continue                # Spruce's settings and comments
        match = _SPRUCE.match(line)
        if not match:
            continue
        body = _lines_of(match.group(9))
        if not body:
            continue
        start = _clock_seconds(*match.groups()[0:4], frames=True, fps=rate)
        end = _clock_seconds(*match.groups()[4:8], frames=True, fps=rate)
        segments.append({'start': start, 'end': _rounded(max(start, end)), 'text': body})
    return segments


def _write_spruce(segments, fps=DEFAULT_FPS):
    rate = float(fps or DEFAULT_FPS)

    def stamp(seconds):
        total = max(0.0, float(seconds))
        whole = int(total)
        frames = int(round((total - whole) * rate))
        if frames >= int(round(rate)):
            whole, frames = whole + 1, 0
        hours, rest = divmod(whole, 3600)
        minutes, secs = divmod(rest, 60)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}'

    lines = []
    for segment in _ordered(segments):
        body = _packed(segment)
        if not body:
            continue
        lines.append(f'{stamp(segment.get("start", 0))} , {stamp(segment.get("end", 0))} , {body}')
    return '\n'.join(lines) + '\n' if lines else ''


# -------------------------------------------------------------- RealText


def _rt_seconds(value):
    """RealText's time: "1.5", "00:00:01.5", "1:01.5" and the rest."""
    value = str(value or '').strip()
    if not value:
        return 0.0
    parts = value.split(':')
    try:
        numbers = [float(part.replace(',', '.')) for part in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return _rounded(total)


def _rt_text(chunk):
    """RealText's markup as Subtitld's: its b, i and u kept, the rest dropped."""
    body = re.sub(r'<br\s*/?>', '\n', chunk, flags=re.I)
    body = _RT_DROP.sub('', body)
    body = _RT_KEEP.sub(lambda m: f'<{m.group(1)}{m.group(2).lower()}>', body)
    body = (body.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&quot;', '"').replace('&amp;', '&'))
    return '\n'.join(line.strip() for line in body.split('\n') if line.strip())


def _parse_realtext(text, fps=DEFAULT_FPS):
    """Each <time> begins a subtitle; the next one, or a <clear/>, ends it."""
    pieces = re.split(r'(<time\b[^>]*>|<clear\s*/?>)', text, flags=re.I)
    segments = []
    pending = None
    for piece in pieces:
        if not piece:
            continue
        if _RT_CLEAR.match(piece):
            # <clear/> ends the text on screen but carries no time of its
            # own: the subtitle runs to the next <time>, as a player shows it.
            pending = None
            continue
        time_tag = _RT_TIME.search(piece) if piece.lstrip().lower().startswith('<time') else None
        if time_tag:
            start = _rt_seconds(time_tag.group(1))
            end_tag = _RT_END.search(piece)
            pending = {'start': start, 'end': _rt_seconds(end_tag.group(1)) if end_tag else None, 'text': ''}
            segments.append(pending)
            continue
        if pending is not None and not pending['text']:
            pending['text'] = _rt_text(piece)
    segments = [segment for segment in segments if segment['text']]
    return _ends_at_next(_ordered(segments))


def _write_realtext(segments, fps=DEFAULT_FPS):
    """A <window> of <time> tags, each subtitle cleared before the next."""
    def stamp(seconds):
        total = max(0.0, float(seconds))
        hours, rest = divmod(int(total), 3600)
        minutes, secs = divmod(rest, 60)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}.{int(round((total - int(total)) * 10))}'

    lines = ['<window type="generic">']
    for segment in _ordered(segments):
        text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        if not markup.plain(text).strip():
            continue
        # RealText's b, i and u are Subtitld's own, so the tags go through
        # as they are; a bare & would not, and becomes an entity.
        body = re.sub(r'&(?![A-Za-z#][A-Za-z0-9]*;)', '&amp;', text)
        body = '<br/>'.join(line.strip() for line in body.split('\n') if line.strip())
        lines.append(f'<time begin="{stamp(segment.get("start", 0))}"/>{body}'
                     f'<time end="{stamp(segment.get("end", 0))}"/><clear/>')
    lines.append('</window>')
    return '\n'.join(lines) + '\n' if len(lines) > 2 else ''


# -------------------------------------------------------------- JACOSub


def _parse_jacosub(text, fps=DEFAULT_FPS):
    """Times are H:MM:SS.FF, the FF in units of the script's own TIMERES
    (thirtieths unless it says otherwise), and #SHIFT moves them all."""
    timeres, shift = 30.0, 0.0
    segments = []
    for line in text.split('\n'):
        directive = _JSS_DIRECTIVE.match(line)
        if directive:
            name, value = directive.group(1).upper(), directive.group(2).strip().split()
            try:
                if name == 'TIMERES' and value:
                    timeres = float(value[0]) or 30.0
                elif name == 'SHIFT' and value:
                    shift = float(value[0])
            except ValueError:
                pass
            continue
        match = _JSS.match(line)
        if not match:
            continue
        body = _lines_of(match.group(10).replace('\\n', '\n').replace('\\N', '\n'))
        if not body:
            continue
        start = _clock_seconds(*match.groups()[0:3]) + (int(match.group(4)) + shift) / timeres
        end = _clock_seconds(*match.groups()[4:7]) + (int(match.group(8)) + shift) / timeres
        segments.append({'start': _rounded(start), 'end': _rounded(max(start, end)), 'text': body})
    return _ordered(segments)


def _write_jacosub(segments, fps=DEFAULT_FPS):
    """Written at a TIMERES of 100, so the times are hundredths and need no
    frame rate to be read back as they were written."""
    def stamp(seconds):
        total = max(0, int(round(float(seconds) * 100)))
        hours, rest = divmod(total, 360000)
        minutes, rest = divmod(rest, 6000)
        secs, hundredths = divmod(rest, 100)
        return f'{hours}:{minutes:02d}:{secs:02d}.{hundredths:02d}'

    lines = ['#TIMERES 100', '']
    for segment in _ordered(segments):
        body = _packed(segment, separator='\\n')
        if not body:
            continue
        lines.append(f'{stamp(segment.get("start", 0))} {stamp(segment.get("end", 0))} D {body}')
    return '\n'.join(lines) + '\n' if len(lines) > 2 else ''


# -------------------------------------------------------------- Sasami2k


def _parse_sasami(text, fps=DEFAULT_FPS):
    """A command script: ";Set.Time.Start=" in milliseconds begins a
    subtitle, every line that is not a command is its text, and the next
    marker ends it. A marker whose text is blank is there to clear the
    screen, which is how the format says when a subtitle stops."""
    marks = []
    for line in text.split('\n'):
        time_at = _S2K_TIME.match(line)
        if time_at:
            marks.append({'start': _rounded(int(time_at.group(1)) / 1000.0), 'lines': []})
        elif line.lstrip().startswith((';', '//')):
            continue
        elif marks and line.strip():
            marks[-1]['lines'].append(line.strip())
    segments = []
    for position, mark in enumerate(marks):
        body = _lines_of('\n'.join(mark['lines']))
        if not body:
            continue
        following = marks[position + 1]['start'] if position + 1 < len(marks) else None
        end = following if following is not None else _rounded(mark['start'] + 2.0)
        segments.append({'start': mark['start'], 'end': _rounded(max(mark['start'], end)), 'text': body})
    return _ordered(segments)


_S2K_HEAD = (';Env.Movie.Width=320', ';Env.Movie.Height=240', ';Set.Slot=1', ';Set.Time.Delay=15000',
             ';Set.Alpha.End=256', ';Set.Alpha.Start=256', ';Set.Alpha.Step=-300',
             ';Set.Font.Style.Bold=0', ';Set.Font.Color=#FFFFFF', ';Set.Font.Outline.Color=#00101010',
             ';Set.Font.Outline2.Color=#01101010', ';Set.Font.Size=20', ';Set.Font.Face=Tahoma',
             ';Buffer.Push=1')


def _write_sasami(segments, fps=DEFAULT_FPS):
    lines = list(_S2K_HEAD)
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        rows = [line.strip() for line in text.split('\n') if line.strip()]
        lines.append(';Buffer.Pop=1')
        lines.append(f';Set.Time.Start={int(round(float(segment.get("start", 0)) * 1000))}')
        # Each line is placed itself: the format has no line break, it has
        # coordinates, stacked upwards from the foot of a 320x240 frame.
        for number, row in enumerate(rows):
            height = 221 - 18 * (len(rows) - number)
            lines.append(';Set.Start.Position.x=160')
            lines.append(';Set.End.Position.x=160')
            lines.append(f';Set.Start.Position.y={height}')
            lines.append(f';Set.End.Position.y={height}')
            lines.append(row)
        # A marker with a blank line clears the screen, and so ends the one
        # above: without it the subtitle would stay until the next.
        lines.append(';Buffer.Pop=1')
        lines.append(f';Set.Time.Start={int(round(float(segment.get("end", 0)) * 1000))}')
        lines.append(';Set.Start.Position.x=160')
        lines.append(';Set.End.Position.x=160')
        lines.append(';Set.Start.Position.y=203')
        lines.append(';Set.End.Position.y=203')
        lines.append(' ')
    return '\n'.join(lines) + '\n' if len(lines) > len(_S2K_HEAD) else ''


# ---------------------------------------------------------------- Xombie


def _parse_xombie(text, fps=DEFAULT_FPS):
    """An ASS dialect: Line=D rows, times in milliseconds rather than
    ASS's hundredths, and the actor field where ASS keeps one."""
    segments = []
    for line in text.split('\n'):
        match = _XSS.match(line)
        if not match or match.group(1).strip().upper() != 'D':
            continue                # styles, fonts, and row types nothing reads
        body = _lines_of(match.group(18).replace('\\N', '\n').replace('\\n', '\n'))
        if not body:
            continue
        start = _clock_seconds(*match.groups()[3:6]) + int(match.group(7)) / 1000.0
        end = _clock_seconds(*match.groups()[7:10]) + int(match.group(11)) / 1000.0
        segments.append(_named({'start': _rounded(start), 'end': _rounded(max(start, end)), 'text': body},
                               match.group(14)))
    return _ordered(segments)


def _write_xombie(segments, fps=DEFAULT_FPS):
    def stamp(seconds):
        total = max(0, int(round(float(seconds) * 1000)))
        hours, rest = divmod(total, 3600000)
        minutes, rest = divmod(rest, 60000)
        secs, millis = divmod(rest, 1000)
        return f'{hours}:{minutes:02d}:{secs:02d}.{millis:03d}'

    lines = ['; XombieSub script', 'version=1.0', 'ScreenHorizontal=1280', 'ScreenVertical=1024',
             'Style=Default,Default,Arial,48,16777215,16777215,0,0,255,255,255,255,'
             '0,0,0,0,0,100,100,0,0,0,0,1,2,1,2,30,30,30,1']
    for number, segment in enumerate(_ordered(segments), start=1):
        body = _packed(segment, separator='\\N')
        if not body:
            continue
        speaker = str(segment.get('speaker') or '').strip()
        lines.append(f'Line=D,{number},0,{stamp(segment.get("start", 0))},{stamp(segment.get("end", 0))},'
                     f'Default,Default,{speaker},0,0,0,{body}')
    return '\n'.join(lines) + '\n' if len(lines) > 5 else ''


# --------------------------------------------------------------- ViPlay


def _parse_viplay(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _VIPLAY.match(line)
        if not match:
            continue                # the magic line, and anything else
        body = _lines_of(match.group(9))
        if not body:
            continue
        start = _clock_seconds(*match.groups()[0:4])
        end = _clock_seconds(*match.groups()[4:8])
        segments.append({'start': start, 'end': _rounded(max(start, end)), 'text': body})
    return _ordered(segments)


def _stamp_millis(seconds, separator=','):
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}'


def _write_viplay(segments, fps=DEFAULT_FPS):
    # ViPlay is by the author of Subtitle Workshop and takes its tags, which
    # are Subtitld's own, so the formatting goes through as it is.
    lines = ['{* VIPLAY SUBTITLE FILE *}']
    for segment in _ordered(segments):
        body = '|'.join(line.strip() for line in str(segment.get('text') or '')
                        .replace('\r\n', '\n').split('\n') if line.strip())
        if not markup.plain(body).strip():
            continue
        lines.append(f'{_stamp_millis(segment.get("start", 0))}-'
                     f'{_stamp_millis(segment.get("end", 0))}={body}')
    return '\n'.join(lines) + '\n' if len(lines) > 1 else ''


# ------------------------------------------------------------------ SBT


def _parse_sbt(text, fps=DEFAULT_FPS):
    """Start, end, then the text: always two lines of it, the first empty
    when the subtitle has only one."""
    lines = text.split('\n')
    segments = []
    index = 0
    while index + 1 < len(lines):
        start_at, end_at = _SBT_TIME.match(lines[index]), _SBT_TIME.match(lines[index + 1])
        if not (start_at and end_at):
            index += 1
            continue
        body = []
        index += 2
        while index < len(lines) and not (_SBT_TIME.match(lines[index])
                                          and index + 1 < len(lines) and _SBT_TIME.match(lines[index + 1])):
            body.append(lines[index])
            index += 1
        text_of = _lines_of('\n'.join(body))
        if not text_of:
            continue
        start = _clock_seconds(*start_at.groups())
        end = _clock_seconds(*end_at.groups())
        segments.append({'start': start, 'end': _rounded(max(start, end)), 'text': text_of})
    return _ordered(segments)


def _write_sbt(segments, fps=DEFAULT_FPS):
    def stamp(seconds):
        total = max(0, int(float(seconds)))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'

    blocks = []
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        rows = [line.strip() for line in text.split('\n') if line.strip()]
        # The format holds exactly two lines: one line of text is written
        # under an empty one, and more than two are folded into the second.
        rows = [''] + rows if len(rows) == 1 else [rows[0], ' '.join(rows[1:])]
        blocks.append(f'{stamp(segment.get("start", 0))}\n{stamp(segment.get("end", 0))}\n' + '\n'.join(rows))
    return '\n'.join(blocks) + '\n' if blocks else ''


# ---------------------------------------------------------- Captions 32


def _parse_captions32(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _CAPTIONS32.match(line.rstrip('\r'))
        if not match:
            continue
        body = _lines_of(f'{match.group(9)}\n{match.group(10)}')
        if not body:
            continue
        start = _hundredths(*match.groups()[0:4])
        end = _hundredths(*match.groups()[4:8])
        segments.append({'start': start, 'end': _rounded(max(start, end)), 'text': body})
    return _ordered(segments)


def _write_captions32(segments, fps=DEFAULT_FPS):
    """Fixed columns: two lines of 33 characters with a "|" between them."""
    def stamp(seconds):
        total = max(0, int(round(float(seconds) * 100)))
        hours, rest = divmod(total, 360000)
        minutes, rest = divmod(rest, 6000)
        secs, hundredths = divmod(rest, 100)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}:{hundredths:02d}'

    lines = []
    for segment in _ordered(segments):
        text = markup.plain(str(segment.get('text') or '')).strip()
        if not text:
            continue
        rows = [line.strip()[:33] for line in text.split('\n') if line.strip()][:2]
        first = rows[0].ljust(33)
        second = (rows[1] if len(rows) > 1 else '').ljust(33)
        lines.append(f'{stamp(segment.get("start", 0))} , {stamp(segment.get("end", 0))} , {first}|{second}')
    return '\n'.join(line.rstrip() + '\n' for line in lines) if lines else ''


# -------------------------------------------------------------- SubSonic


def _parse_subsonic(text, fps=DEFAULT_FPS):
    """Seconds with two decimals that wrap every 256, which is how the
    format counts and not a mistake in the file: the reader follows the
    wrap by watching the numbers fall."""
    times, wraps, previous = [], 0, None
    for line in text.split('\n'):
        match = _SUBSONIC.match(line) or _SUBSONIC_END.match(line)
        if not match:
            continue
        raw = float(match.group(1))
        if previous is not None and raw < previous:
            wraps += 1
        previous = raw
        body = _SUBSONIC.match(line)
        times.append((_rounded(raw + 256 * wraps), _lines_of(body.group(2)) if body else None))
    segments = []
    for position, (seconds, body) in enumerate(times):
        if body is None:
            continue                # a line that only ends the one before
        following = times[position + 1][0] if position + 1 < len(times) else None
        end = following if following is not None else _rounded(seconds + 2.0)
        segments.append({'start': seconds, 'end': _rounded(max(seconds, end)), 'text': body})
    return _ordered(segments)


def _write_subsonic(segments, fps=DEFAULT_FPS):
    lines = []
    for segment in _ordered(segments):
        body = _packed(segment, separator=' ')
        if not body:
            continue
        lines.append(f'1 {float(segment.get("start", 0)) % 256:.2f} \\ ~:\\{body}')
        lines.append(f'1 {float(segment.get("end", 0)) % 256:.2f}')
    return '\n'.join(lines) + '\n' if lines else ''


# ---------------------------------------------------------------- Oresme


def _parse_oresme(text, fps=DEFAULT_FPS):
    """A timecode in frames, a run of {tags}, the text; no end times."""
    rate = float(fps or DEFAULT_FPS)
    segments = []
    for line in text.split('\n'):
        match = _ORESME.match(line.rstrip('\r'))
        if not match:
            continue
        body = _lines_of(_TAG.sub(lambda tag: '\n' if tag.group(1).upper() == 'N' else '', match.group(6)))
        start = _clock_seconds(*match.groups()[0:4], frames=True, fps=rate)
        # A row with no text clears the screen, and so ends the one before.
        segments.append({'start': start, 'end': None, 'text': body})
    kept = []
    for position, segment in enumerate(segments):
        if not segment['text']:
            continue
        following = next((later['start'] for later in segments[position + 1:]), None)
        segment['end'] = following if following is not None else None
        kept.append(segment)
    return _ends_at_next(_ordered(kept))


def _write_oresme(segments, fps=DEFAULT_FPS):
    rate = float(fps or DEFAULT_FPS)
    tags = '{Bottom}{Open Caption}{Center}{White}{Font Arial GVP Bold}'

    def stamp(seconds):
        total = max(0.0, float(seconds))
        whole = int(total)
        frames = min(int(round((total - whole) * rate)), int(round(rate)) - 1)
        hours, rest = divmod(whole, 3600)
        minutes, secs = divmod(rest, 60)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}'

    lines = ['00:00:00:00{BC}{W2710}']
    for segment in _ordered(segments):
        body = _packed(segment, separator='{N}')
        if not body:
            continue
        lines.append(f'{stamp(segment.get("start", 0))}{tags}{body}')
        # The row that clears the screen is what gives the subtitle its end.
        lines.append(f'{stamp(segment.get("end", 0))}{tags}')
    return '\n'.join(lines) + '\n' if len(lines) > 1 else ''


# ----------------------------------------------------------- TurboTitle


def _hundredths(hours, minutes, seconds, fraction):
    return _rounded(int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / 100.0)


def _named(segment, name):
    """The name field as the subtitle's speaker; "~" is a blank field."""
    name = str(name or '').strip()
    if name and name != '~':
        segment['speaker'] = name
    return segment


def _parse_turbotitle(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _TTS.match(line)
        if not match:
            continue
        body = _lines_of(match.group(10) or '')
        if not body:
            continue                # a line kept only to hold its timing
        start = _hundredths(*match.groups()[0:4])
        end = _hundredths(*match.groups()[4:8])
        if start >= _UNTIMED and end >= _UNTIMED:
            continue                # a script still being timed
        segments.append(_named({'start': start, 'end': _rounded(max(start, end)), 'text': body}, match.group(9)))
    return _ordered(segments)


def _stamp_hundredths(seconds):
    total = max(0, int(round(float(seconds) * 100)))
    hours, rest = divmod(total, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, hundredths = divmod(rest, 100)
    return f'{min(hours, 9)}:{minutes:02d}:{secs:02d}.{hundredths:02d}'


def _write_turbotitle(segments, fps=DEFAULT_FPS):
    lines = []
    for segment in _ordered(segments):
        body = _packed(segment, separator=' ')
        if not body:
            continue
        name = str(segment.get('speaker') or '').strip().replace(' ', '_') or '~'
        lines.append(f'{_stamp_hundredths(segment.get("start", 0))},'
                     f'{_stamp_hundredths(segment.get("end", 0))},{name} {body}')
    return '\n'.join(lines) + '\n' if lines else ''


# --------------------------------------------------------------- ZeroG


def _parse_zerog(text, fps=DEFAULT_FPS):
    segments = []
    for line in text.split('\n'):
        match = _ZEG.match(line)
        if not match:
            continue                # S styles, T titles, % and # comments
        body = _lines_of((match.group(11) or '').replace('\\n', '\n'))
        if not body:
            continue
        start = _hundredths(*match.groups()[0:4])
        end = _hundredths(*match.groups()[4:8])
        segments.append(_named({'start': start, 'end': _rounded(max(start, end)), 'text': body}, match.group(10)))
    return _ordered(segments)


def _write_zerog(segments, fps=DEFAULT_FPS):
    lines = ['% Zero G 1.0']
    for segment in _ordered(segments):
        body = _packed(segment, separator='\\n')
        if not body:
            continue
        name = str(segment.get('speaker') or '').strip().replace(' ', '_') or '~'
        lines.append(f'E 1 {_stamp_hundredths(segment.get("start", 0))} '
                     f'{_stamp_hundredths(segment.get("end", 0))} DEFAULT {name} {body}')
    return '\n'.join(lines) + '\n' if len(lines) > 1 else ''


# ------------------------------------------------------------ the module


_PARSERS = {
    'mpl2': _parse_mpl2,
    'pjs': _parse_pjs,
    'mpsub': _parse_mpsub,
    'vplayer': _parse_clock,
    'tmplayer': _parse_clock,
    'aqtitle': _parse_aqtitle,
    'subviewer1': _parse_subviewer1,
    'spruce': _parse_spruce,
    'realtext': _parse_realtext,
    'turbotitle': _parse_turbotitle,
    'zerog': _parse_zerog,
    'viplay': _parse_viplay,
    'sbt': _parse_sbt,
    'captions32': _parse_captions32,
    'subsonic': _parse_subsonic,
    'oresme': _parse_oresme,
    'jacosub': _parse_jacosub,
    'sasami': _parse_sasami,
    'xombie': _parse_xombie,
}

_WRITERS = {
    'mpl2': _write_mpl2,
    'pjs': _write_pjs,
    'mpsub': _write_mpsub,
    'vplayer': _write_clock,
    'tmplayer': lambda segments, fps=DEFAULT_FPS: _write_clock(segments, fps, separator=':'),
    'aqtitle': _write_aqtitle,
    'subviewer1': _write_subviewer1,
    'spruce': _write_spruce,
    'realtext': _write_realtext,
    'turbotitle': _write_turbotitle,
    'zerog': _write_zerog,
    'viplay': _write_viplay,
    'sbt': _write_sbt,
    'captions32': _write_captions32,
    'subsonic': _write_subsonic,
    'oresme': _write_oresme,
    'jacosub': _write_jacosub,
    'sasami': _write_sasami,
    'xombie': _write_xombie,
}


def kind(data):
    """Which of these formats `data` is, '' when it is none of them.

    Takes bytes or text. The tests run from the most particular shape to
    the least: .txt is five of these formats and .sub is three, so a
    reader that trusted the extension would read the wrong one.
    """
    text = decode(data) if isinstance(data, (bytes, bytearray)) else str(data or '')
    head = text.lstrip('﻿')
    if '<time' in head.lower() and ('<window' in head.lower() or '<clear' in head.lower()):
        return 'realtext'
    if _S2K_WIDTH.match(head.lstrip().split('\n')[0] if head.strip() else ''):
        return 'sasami'
    if head.lstrip().lower().startswith(_VIPLAY_MAGIC):
        return 'viplay'
    if head.lstrip().lower().startswith('% zero g'):
        return 'zerog'
    lines = [line for line in head.split('\n') if line.strip()][:60]
    for line in lines:
        if _CAPTIONS32.match(line.rstrip('\r')):
            return 'captions32'
        if _VIPLAY.match(line):
            return 'viplay'
        if _SUBSONIC.match(line):
            return 'subsonic'
        if _ORESME.match(line.rstrip('\r')) and _ORESME.match(line.rstrip('\r')).group(5):
            return 'oresme'
        if _XSS.match(line):
            return 'xombie'
        if _JSS.match(line):
            return 'jacosub'
        if _S2K_TIME.match(line):
            return 'sasami'
        if _TTS.match(line):
            return 'turbotitle'
        if _ZEG.match(line):
            return 'zerog'
        if _SPRUCE.match(line):
            return 'spruce'
        if _MPL2.match(line):
            return 'mpl2'
        if _PJS.match(line):
            return 'pjs'
        if _AQT.match(line):
            return 'aqtitle'
    if any(_MPSUB_HEADER.match(line) for line in lines[:5]):
        return 'mpsub'
    # SubViewer 1 is marks alone on their lines, around their text.
    if sum(1 for line in lines if _SUBVIEWER1.match(line)) >= 2:
        return 'subviewer1'
    # SBT is times on their own lines, two of them, over the text.
    for position, line in enumerate(lines[:-2]):
        if _SBT_TIME.match(line) and _SBT_TIME.match(lines[position + 1]) \
                and not _SBT_TIME.match(lines[position + 2]):
            return 'sbt'
    clocks = sum(1 for line in lines if _CLOCK.match(line))
    if clocks and clocks >= max(1, len(lines) // 2):
        return 'vplayer'
    # MPSub without a header: "<gap> <duration>" over the text, twice.
    if sum(1 for line in lines if _MPSUB_TIMES.match(line)) >= 2 and len(lines) > 3:
        return 'mpsub'
    return ''


def parse(text, fmt, fps=DEFAULT_FPS):
    """The subtitles in `text`, read as `fmt`: [{'start', 'end', 'text'}]."""
    if fmt not in _PARSERS:
        raise ValueError(f'unknown subtitle format {fmt!r}')
    return _PARSERS[fmt](text, fps or DEFAULT_FPS)


def read(path, fps=None):
    """The subtitles in the file at `path`, and the format they were in."""
    with open(path, 'rb') as legacy_file:
        data = legacy_file.read()
    fmt = kind(data)
    if not fmt:
        raise ValueError('not a subtitle file of the player era')
    return parse(decode(data), fmt, fps or DEFAULT_FPS), fmt


def write(segments, fmt, fps=DEFAULT_FPS):
    """`segments` as `fmt` text."""
    if fmt not in _WRITERS:
        raise ValueError(f'{fmt!r} is read only' if fmt in _PARSERS else f'unknown subtitle format {fmt!r}')
    return _WRITERS[fmt](segments, fps or DEFAULT_FPS)


def save(path, segments, fmt, fps=DEFAULT_FPS, encoding='utf-8', line_end='\n'):
    """Write `segments` to `path` as `fmt`."""
    with open(path, 'w', encoding=encoding, newline=line_end) as legacy_file:
        legacy_file.write(write(segments, fmt, fps))
