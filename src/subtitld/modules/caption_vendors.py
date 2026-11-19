"""The binary caption files of four line-21 captioning houses' tools: NCI's
(.cap), CapMaker Plus (.cap), Captions Inc.'s (.cin) and Ultech's (.ult,
.uld). CapMaker Plus is read and written; the others are read only.

None of them is published. What is here follows Subtitle Edit's readers
and writers (libse, NciCaption.cs, CapMakerPlus.cs, CaptionsInc.cs and
Ultech130.cs, GPL-3 as Subtitld is), worked out from files. All four time
in HH:MM:SS:FF whose frames count within the second at a rate the files do
not say: the video's, else 25 (Subtitle Edit's default too). A caption a
file gives no end ends where the next starts, the last when it has been on
long enough to read.

NCI ("CAPT" 00 "2.0" or "CAPT" 00 "1.2" at the start), read only, as
Subtitle Edit has no writer: the texts first, from 128, each 64 bytes
between FF FF markers (FF FF, two bytes, the text in Windows-1252 up to a
00, 0D and two bytes for a line break, 87 for "♪"); then the times, which
Subtitle Edit finds two ways:

    1.2  from 396, records between FF FF markers 28 bytes apart, the
         caption's number (from 1) at 14, its start (hours, minutes,
         seconds, frames, a byte each) at 18 and end at 22;
    2.0  from 230, runs of 14-byte records between FF FF FF FF, each its
         number (two bytes, low first, from 0) and its start at 6 and end
         at 10, the last of a run with a start only.

CapMaker Plus ("+" first): a header of 2638 bytes (written as Subtitle
Edit writes it: its CGridData, fonts and colours; the number of records at
1400, two bytes low first; a time at 1354 that every time in the file is
counted from), then a record a caption:

    0B, the start as "HH:MM:SS:FF", 12 bytes of style (at 2 and 3 the
    horizontal place, 80 BF centre, 00 00 left, 00 C0 right; at 7 the
    vertical, C0 bottom, 00 top; at 8 the justification; at 10 01 normal,
    03 italic), the length, the text in Windows-1252 (CRLF between lines,
    118 characters at most) and 74 zeros.

A caption has no end: a record with no text clears the screen, and
Subtitle Edit writes one wherever a caption ends more than 0.1 s before
the next starts. Records with the same start are the lines of one caption.
Text starting with "{\\rtf1" is RTF, read as its plain text. Writing gives
Subtitle Edit's header and records, the place from the subtitle's (top or
bottom; left, centre or right) and italics for a caption italic all
through.

Captions Inc. (.cin), read only: a header to 256, then records each 0D 0A
FE, three digits, the time as eight digits HHMMSSFF, codes (bytes up to 20,
14 2C ending the text) and the text in Windows-1252, a code byte between
lines; then FE, three digits and the end. Subtitle Edit's writer writes
its times with colons, which its own reader does not read, so there is no
source to write it from.

Ultech 1.30 (.ult, .uld for drop-frame), read only as everywhere: the
header "ULTECH" 00 "1.30" and 512 bytes, then records F1, the length (a
byte, then 00), the time (hours, minutes, seconds, frames) and CEA-608 data:
two-byte control codes (first byte below 1F) between lines, 11 2E turning
italics on for the line, the rest Windows-1252. A record of 22 bytes or
less ends the caption before it.
"""

import base64
import re
import zlib

from subtitld.modules import markup

DEFAULT_FPS = 25.0
NCI_MAGICS = (b'CAPT\x002.0', b'CAPT\x001.2')
ULTECH_MAGIC = b'ULTECH\x001.30'
_LONGEST = 8.0  # Subtitle Edit's longest a subtitle may be shown, in seconds


def _seconds(hours, minutes, seconds, frames, fps):
    return round(hours * 3600 + minutes * 60 + seconds + min(frames / fps, 0.999), 3)


def _reading_time(text):
    """How long a caption with no end stays: long enough to read it."""
    return max(1.0, round(len(markup.plain(text)) / 15, 3))


def _close_ends(segments):
    """Ends for the captions without one: the next start, else reading
    time; then nothing shorter than its start."""
    for index, segment in enumerate(segments):
        following = segments[index + 1]['start'] if index + 1 < len(segments) else None
        if segment.get('end') is None or segment['end'] <= segment['start']:
            if following is not None and following > segment['start']:
                segment['end'] = round(following - 0.001, 3)
            else:
                segment['end'] = round(segment['start'] + _reading_time(segment['text']), 3)
    return segments


# -- NCI ----------------------------------------------------------------------

def is_nci(data):
    """True when `data` begins as an NCI caption file."""
    return len(data) >= 640 and bytes(data[:8]) in NCI_MAGICS


def parse_nci(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in NCI caption bytes. Raises ValueError for
    bytes that are not NCI's."""
    fps = float(fps or DEFAULT_FPS)
    if not is_nci(data):
        raise ValueError('Not an NCI caption file: it does not begin "CAPT"')
    texts = []
    at = 128
    while at < len(data) - 66:
        if (data[at] == 0xFF and data[at + 1] == 0xFF and data[at + 3] != 0xFF and data[at - 1] != 0xFF
                and data[at + 64] == 0xFF and data[at + 65] == 0xFF):
            out, scan = '', at + 4
            while scan < at + 64 and data[scan]:
                if data[scan] == 0x0D:
                    out += '\n'
                    scan += 3
                    continue
                out += '♪' if data[scan] == 0x87 else bytes([data[scan]]).decode('cp1252', 'replace')
                scan += 1
            texts.append({'start': 0.0, 'end': None, 'text': '\n'.join(line.strip() for line in out.split('\n'))})
            at += 62
        else:
            at += 1

    def time_at(offset):
        return _seconds(*data[offset:offset + 4], fps)

    if bytes(data[:8]) == NCI_MAGICS[1]:
        at, number = 396, 0
        while at < len(data) - 66:
            if (data[at] == 0xFF and data[at + 1] == 0xFF and data[at + 2] != 0xFF and data[at + 28] == 0xFF
                    and data[at + 29] == 0xFF and data[at + 30] != 0xFF and data[at + 14] == number + 1):
                if number < len(texts):
                    texts[number]['start'], texts[number]['end'] = time_at(at + 18), time_at(at + 22)
                    number += 1
                at += 25
            at += 1
    else:
        at, run_start, last = 230, 230, -1
        while at < len(data) - 66:
            if data[at:at + 4] == b'\xff\xff\xff\xff':
                length = at - run_start - 2
                if length >= 10:
                    count = length // 14 + (1 if length % 14 in (8, 10) else 0)
                    for k in range(count):
                        index = run_start + 2 + 14 * k
                        number = data[index] | data[index + 1] << 8
                        if number != last + 1 and (data[index - 2] | data[index - 1] << 8) == last + 1:
                            index -= 2
                            number = last + 1
                        if number > last:
                            last = number
                            if number < len(texts):
                                texts[number]['start'] = time_at(index + 6)
                                if k < count - 1:
                                    texts[number]['end'] = time_at(index + 10)
                run_start = at + 2
                at += 5
            at += 1
    segments = [segment for segment in texts if segment['text'].strip()]
    if not segments:
        raise ValueError('An NCI caption file with no captions found in it')
    for segment in segments:
        if segment['end'] is not None and segment['end'] <= 0:
            segment['end'] = None
    _close_ends(segments)
    last_one = segments[-1]
    last_one['end'] = round(last_one['start'] + _reading_time(last_one['text']), 3)
    return segments, {'fps': fps}


# -- CapMaker Plus ------------------------------------------------------------

# Subtitle Edit's header and closing record, compressed (zlib, base64).
_CAPMAKER_HEADER = (
    'eNrTVue3cU4s8E3MTi1SCMgpLbZjZmJkgABFKM3OgBswQjErGgaJxQEJOSCtAMRMaHpggNvAwAqGGHHY8Z+BHFDL8P8/RGdDQwPc'
    'Tph7PYDYAIlmxGlOA15znKD6nSg0h3j3MEDNUcAwh4kEcwQEBOgSPsSb8x/DnP8EzMFmloODA05zmEgwB1v6HBUffuJUA/gt+P+f'
    'kYGTwdm9KDPFJbEkkZlJFSrB6ZOYl16amJ5qyAhMp9CU2LAfSBxgwmILZ1hmSmq+XnJiAYIl72IV41yQHJ6ZF2NuYBATnJhbkJNa'
    'HAORL6kosQEVyY5FmYk5oPzxACW1g+S4nfNLizKBxb9fajlIxRa8/mREw/wEQ+b3fxYGHgZQVYAd4gPMBLInUsYHKvuA5jYQEAFi'
    'LiQaJRAZBhLYMIxYMFr6DE9xABZmxYg=')
_CAPMAKER_TAIL = 'eNpjYGjYz8DAcICJgZGBisDBwYHh////YHZDQwPYcBAPRDMBsQcQGyDRVLV8FJAFAPjyCTQ='
_CAPMAKER_GAP = bytes([0, 0, 0, 0xC0, 0, 0, 0, 0, 0x01, 0, 0x01]) + bytes(76)
_CAPMAKER_TIME = re.compile(rb'\d\d:\d\d:\d\d:\d\d')
_CAPMAKER_TEXT = 118


def _capmaker_blob(encoded):
    return zlib.decompress(base64.b64decode(encoded))


def is_capmaker(data):
    """True when `data` begins as a CapMaker Plus file: "+" and, after the
    first 128 bytes, a 0B and a time."""
    if len(data) < 640 or data[0] != 0x2B:
        return False
    at = data.find(b'\x0b', 128)
    while 0 <= at < len(data) - 12:
        if _CAPMAKER_TIME.fullmatch(data[at + 1:at + 12]):
            return True
        at = data.find(b'\x0b', at + 1)
    return False


def _rtf_text(rtf):
    """The plain text of a little RTF: groups of control words dropped,
    \\par a line break."""
    text = re.sub(r'\\par[d]?\b ?', '\n', rtf)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode('cp1252', 'replace'), text)
    text = re.sub(r'\{\\\*[^{}]*\}|\{\\fonttbl[^{}]*(\{[^{}]*\})*[^{}]*\}|\{\\colortbl[^{}]*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+-?\d* ?', '', text)
    return re.sub(r'[{}]', '', text).strip()


def parse_capmaker(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in CapMaker Plus bytes. Raises ValueError for
    bytes that are not CapMaker Plus."""
    fps = float(fps or DEFAULT_FPS)
    if not is_capmaker(data):
        raise ValueError('Not a CapMaker Plus file: it does not begin as one does')

    def seconds(stamp):
        hours, minutes, second, frames = (int(part) for part in stamp.split(b':'))
        return _seconds(hours, minutes, second, frames, fps)

    records = []
    for at in range(128, len(data) - 20):
        if data[at] != 0x0B or not _CAPMAKER_TIME.fullmatch(data[at + 1:at + 12]):
            continue
        stamp = data[at + 1:at + 12]
        style = data[at + 22] if at + 22 < len(data) else 0
        if stamp == b'00:00:00:00' and style not in (1, 3):
            continue
        end = data.find(b'\x00', at + 25)
        raw = data[at + 25:end if end >= 0 else len(data)].decode('cp1252', 'replace')
        if 0 <= raw.find('{\\rtf1') < 10:
            text = _rtf_text(raw[raw.find('{\\rtf1'):])
        else:
            text = '\n'.join(line.strip() for line in raw.replace('\r\n', '\n').replace('\r', '\n').split('\n'))
            if style == 3 and text.strip():
                text = f'<i>{text}</i>'
        across = data[at + 14:at + 16]
        column = 0 if across == b'\x00\x00' else 2 if across == b'\x00\xc0' else 1
        keypad = (7 if data[at + 19] == 0 else 1) + column
        records.append({'start': seconds(stamp), 'end': None, 'text': text, 'keypad': keypad})
    # Each caption lasts until the next record, a cleared screen included.
    for index, record in enumerate(records[:-1]):
        record['end'] = records[index + 1]['start']
    records = [record for record in records if record['text'].strip()]
    # The lines of a caption share its start.
    merged = []
    for record in records:
        if merged and merged[-1]['start'] == record['start']:
            merged[-1]['text'] += '\n' + record['text']
            merged[-1]['end'] = record['end']
        else:
            merged.append(dict(record))
    for index, record in enumerate(merged[:-1]):
        if record['end'] is not None and record['end'] == merged[index + 1]['start']:
            record['end'] = round(record['end'] - 0.001, 3)
    if merged and merged[-1]['end'] is None:
        merged[-1]['end'] = round(merged[-1]['start'] + _reading_time(merged[-1]['text']), 3)
    # Every time counts from the one the header gives.
    offset = 0.0
    if len(data) > 1365 and _CAPMAKER_TIME.fullmatch(data[1354:1365]):
        offset = seconds(data[1354:1365])
    segments = []
    for record in merged:
        start = round(max(0.0, record['start'] - offset), 3)
        end = round(max(start, (record['end'] if record['end'] is not None else record['start']) - offset), 3)
        keypad = record['keypad']
        segments.append({'start': start, 'end': end,
                         'text': (markup.with_alignment('', keypad) if keypad != 2 else '') + record['text']})
    return segments, {'fps': fps}


def _stamp(seconds, fps):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    frames = int(round((seconds - whole) * fps))
    if frames >= int(round(fps)):
        whole, frames = whole + 1, 0
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return b'\x0b' + f'{hours:02d}:{minute:02d}:{second:02d}:{frames:02d}'.encode('ascii')


def write_capmaker(segments, fps=DEFAULT_FPS):
    """CapMaker Plus bytes for `segments`, frames at `fps`."""
    fps = float(fps or DEFAULT_FPS)
    captions = []
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        runs = markup.without_readings(markup.spans(rest))
        lines = [line.strip() for line in ''.join(run for run, _style in runs).split('\n') if line.strip()]
        if not lines:
            continue
        letters = [style for run, style in runs if run.strip()]
        start = float(segment.get('start', 0))
        captions.append((start, max(start, float(segment.get('end', 0))), lines,
                         all(style.italic for style in letters), markup.alignment(lead) or 2))
    body = bytearray()
    count = len(captions)
    for index, (start, end, lines, italic, keypad) in enumerate(captions):
        style = bytearray([0, 0, 0x80, 0xBF, 0, 0, 0, 0xC0, 2, 0, 1, 0])
        if keypad in (7, 8, 9):
            style[7] = 0
        if keypad in (1, 7):
            style[2:4] = b'\x00\x00'
        elif keypad in (3, 9):
            style[2:4] = b'\x00\xc0'
        if italic:
            style[10] = 3
        text = '\r\n'.join(lines)[:_CAPMAKER_TEXT].encode('cp1252', 'replace')
        body += _stamp(start, fps) + style + bytes([len(text)]) + text + bytes(74)
        following = captions[index + 1][0] if index + 1 < len(captions) else None
        if following is not None and following - end > 0.1:
            body += _stamp(end, fps) + _CAPMAKER_GAP
            count += 1
    if captions:
        body += _stamp(captions[-1][1], fps) + _capmaker_blob(_CAPMAKER_TAIL)
    header = bytearray(_capmaker_blob(_CAPMAKER_HEADER))
    header[1400:1402] = (count & 0xFFFF).to_bytes(2, 'little')
    return bytes(header) + bytes(body)


def save_capmaker(path, segments, fps=DEFAULT_FPS):
    """Write `segments` to `path` as CapMaker Plus."""
    with open(path, 'wb') as cap_file:
        cap_file.write(write_capmaker(segments, fps))


# -- Captions Inc. --------------------------------------------------------------

def parse_captions_inc(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in Captions Inc. bytes. Raises ValueError when
    no caption is found."""
    fps = float(fps or DEFAULT_FPS)
    size = len(data)

    def stamp(at):
        digits = data[at:at + 8]
        if len(digits) == 8 and digits.isdigit():
            return _seconds(int(digits[0:2]), int(digits[2:4]), int(digits[4:6]), int(digits[6:8]), fps)
        return None

    segments, at = [], 256
    while at < size - 20:
        start, end = None, None
        while at < size - 20 and data[at] != 0xFE:
            at += 1
        if data[at] == 0xFE:
            at += 4
            start = stamp(at)
            at += 8
        found = ended = False
        while not found and not ended and at < size - 20:
            byte = data[at]
            if byte == 0x0D:
                at += 1
            elif byte == 0x14 and data[at + 1] == 0x2C:
                ended = True
            elif byte <= 0x20:
                at += 1
                if byte == 0x0A:
                    continue
            else:
                found = True
            at += 1
        at += 1
        text = ''
        if not ended:
            at -= 2
            while not ended and at < size - 20:
                byte = data[at]
                if (byte == 0x14 and data[at + 1] == 0x2C) or (byte == 0x0D and data[at + 1] == 0x0A):
                    ended = True
                elif byte <= 0x17:
                    if not text.endswith('\n'):
                        text += '\n'
                    at += 1
                else:
                    text += bytes([byte]).decode('cp1252', 'replace')
                at += 1
            at += 1
        if at < size and data[at] == 0xFE:
            end = stamp(at + 4)
            while at < size and data[at] != 0x0A:
                at += 1
            at += 1
        else:
            while at < size and data[at] != 0x0A:
                at += 1
            at += 1
            if at + 12 <= size and data[at] == 0xFE:
                end = stamp(at + 4)
        text = '\n'.join(line.strip() for line in text.strip().split('\n') if line.strip())
        if text and start is not None:
            segments.append({'start': start, 'end': end, 'text': text})
    if not segments:
        raise ValueError('Not a Captions Inc. file: no caption found in it')
    _close_ends(segments)
    last = segments[-1]
    if last['end'] - last['start'] > _LONGEST:
        last['end'] = round(last['start'] + _reading_time(last['text']), 3)
    return segments, {'fps': fps}


# -- Ultech ---------------------------------------------------------------------

def is_ultech(data):
    """True when `data` begins as an Ultech 1.30 caption file."""
    return len(data) >= 200 and bytes(data[:len(ULTECH_MAGIC)]).upper() == ULTECH_MAGIC


def parse_ultech(data, fps=DEFAULT_FPS):
    """(subtitles, {'fps'}) in Ultech 1.30 bytes. Raises ValueError for
    bytes that are not Ultech's."""
    fps = float(fps or DEFAULT_FPS)
    if not is_ultech(data):
        raise ValueError('Not an Ultech caption file: it does not begin "ULTECH"')
    segments, last, at = [], None, 512
    while at < len(data) - 25:
        length = data[at + 1]
        record = {'start': _seconds(*data[at + 3:at + 7], fps), 'end': None, 'text': ''}
        if last is not None and last['end'] is None:
            last['end'] = round(record['start'] - 0.001, 3)
        if length > 22:
            runs, italic, skip, line_has = [], False, 0, False
            for scan in range(at + 7, min(at + length, len(data) - 1)):
                byte = data[scan]
                if skip:
                    skip -= 1
                elif byte < 0x1F:
                    skip = 1
                    if line_has:
                        runs.append(('\n', markup.PLAIN))
                        italic, line_has = False, False
                    if byte == 0x11 and data[scan + 1] == 0x2E:
                        italic = True
                elif byte != 0x80:
                    runs.append((bytes([byte]).decode('cp1252', 'replace'), markup.Style(italic=italic)))
                    line_has = True
            record['text'] = markup.tidy(runs)
            if markup.plain(record['text']).strip():
                segments.append(record)
        elif last is not None:
            last['end'] = record['start']
        last = record
        at += length + 3
    if not segments:
        raise ValueError('An Ultech caption file with no captions found in it')
    final = segments[-1]
    if final['end'] is None:
        final['end'] = round(final['start'] + 2.5, 3)
    if final['end'] - final['start'] > _LONGEST:
        final['end'] = round(final['start'] + _reading_time(final['text']), 3)
    _close_ends(segments)
    return segments, {'fps': fps}


# -- Files ----------------------------------------------------------------------

def looks_like_cap(head):
    """'NCI' or 'CAPMAKER' for the .cap whose first bytes are `head` (2048
    of them at least for CapMaker Plus), or None for neither."""
    head = bytes(head or b'')
    if head[:8] in NCI_MAGICS:
        return 'NCI'
    if head[:1] == b'+' and (b'<CapMaker Plus>' in head[:64] or is_capmaker(head.ljust(640, b'\x00'))):
        return 'CAPMAKER'
    return None


def read(path, fps=DEFAULT_FPS):
    """(subtitles, its settings, the kind: 'NCI', 'CAPMAKER', 'CAPTIONSINC'
    or 'ULTECH') in the file at `path`, by its bytes and, for Captions Inc.,
    its .cin."""
    with open(path, 'rb') as caption_file:
        data = caption_file.read()
    if is_ultech(data):
        return (*parse_ultech(data, fps), 'ULTECH')
    kind = looks_like_cap(data[:4096])
    if kind == 'NCI':
        return (*parse_nci(data, fps), 'NCI')
    if kind == 'CAPMAKER':
        return (*parse_capmaker(data, fps), 'CAPMAKER')
    if path.lower().endswith('.cin'):
        return (*parse_captions_inc(data, fps), 'CAPTIONSINC')
    raise ValueError('Not a caption file Subtitld knows: NCI, CapMaker Plus, Captions Inc. or Ultech')
