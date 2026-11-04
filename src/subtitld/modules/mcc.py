"""MacCaption (.mcc): read the CEA-708 and CEA-608 captions MacCaption files
carry, write them as MacCaption does, a caption distribution packet a frame.

An MCC file is the ancillary data of each frame of a video, in hex after
its timecode: a SMPTE 291 packet (DID 61h, SDID 01h) holding a CEA-708
caption distribution packet (CDP), whose cc_data carries the frame's
CEA-608 bytes (the ones an SCC file holds) beside CEA-708's own. Common
runs of bytes are written as letters: G is FAh 00h 00h, Q FCh 80h 80h,
S 96h 69h, Z 00h ...

Reading takes the frame rate the header's "Time Code Rate" and the
packets' own rate say, drop-frame or not, and the letters. The captions
are the first CEA-708 service with any, decoded by the cea708 module;
in a file without, the first CEA-608 channel with any (CC1, CC2, then
field 2's CC3, CC4, as SMPTE 334's 608 packets, SDID 02h, carry them too),
decoded as the SCC module does: pop-on, roll-up and paint-on captions,
italics, underline, colours and places (see closed_captions).

Writing gives MCC V1.0 with the header MacCaption's licence asks for
whole, and a packet for each frame with caption data: the captions the SCC
module writes in CEA-608 (pop-on, roll-up or paint-on, in CC1, CC2, CC3 or
CC4 — the first two in the first field, the others in the second), at the
export's frame rate; and, unless only CEA-608 is asked for, the same
captions in CEA-708's service 1, shown on the same frames, with a service
of its own for each further language asked for. `frames` writes a packet
for every frame of a video rather than only those with captions, as the
ancillary data of an MXF or a QuickTime caption track needs; padding fills
the rest of each packet's count.
"""

import datetime
import re
import uuid

from subtitld.modules import cea708
from subtitld.modules import closed_captions
from subtitld.modules import scc
from subtitld.modules.srt import decode

# Rates as the export offers them: (Time Code Rate, timecode frames, frames
# a second, drop-frame, the CDP's frame rate code, cc_data count).
RATES = {
    '23.976': ('24', 24, 24000 / 1001, False, 1, 25),
    '24': ('24', 24, 24.0, False, 2, 25),
    '25': ('25', 25, 25.0, False, 3, 24),
    '29.97DF': ('30DF', 30, 30000 / 1001, True, 4, 20),
    '29.97': ('30', 30, 30000 / 1001, False, 4, 20),
    '30': ('30', 30, 30.0, False, 5, 20),
    '50': ('50', 50, 50.0, False, 6, 12),
    '59.94DF': ('60DF', 60, 60000 / 1001, True, 7, 10),
    '59.94': ('60', 60, 60000 / 1001, False, 7, 10),
    '60': ('60', 60, 60.0, False, 8, 10),
}
_HEADER_RATES = {'24': (24, 24000 / 1001, False), '25': (25, 25.0, False), '30': (30, 30000 / 1001, False),
                 '30DF': (30, 30000 / 1001, True), '50': (50, 50.0, False), '60': (60, 60000 / 1001, False),
                 '60DF': (60, 60000 / 1001, True)}
_PADDING = b'\xfa\x00\x00'
_ALIASES = {**{letter: _PADDING * count for count, letter in enumerate('GHIJKLMNO', start=1)},
            'P': b'\xfb\x80\x80', 'Q': b'\xfc\x80\x80', 'R': b'\xfd\x80\x80', 'S': b'\x96\x69', 'T': b'\x61\x01',
            'U': b'\xe1\x00\x00\x00', 'V': b'\xfc\x80\x80', 'W': b'\xfc\x80\x80', 'X': b'', 'Y': b'', 'Z': b'\x00'}
_LINE = re.compile(r'^(\d{1,2}):(\d{2}):(\d{2})[:;.,](\d{2})(?:\.\d(?:,\d+)?)?\s+(\S+)')

HEADER = '''File Format=MacCaption_MCC V1.0

///////////////////////////////////////////////////////////////////////////////////
// Computer Prompting and Captioning Company
// Ancillary Data Packet Transfer File
//
// Permission to generate this format is granted provided that
//   1. This ANC Transfer file format is used on an as-is basis and no warranty is given, and
//   2. This entire descriptive information text is included in a generated .mcc file.
//
// General file format:
//   HH:MM:SS:FF(tab)[Hexadecimal ANC data in groups of 2 characters]
//     Hexadecimal data starts with the Ancillary Data Packet DID (Data ID defined in S291M)
//       and concludes with the Check Sum following the User Data Words.
//     Each time code line must contain at most one complete ancillary data packet.
//     To transfer additional ANC Data successive lines may contain identical time code.
//     Time Code Rate=[24, 25, 30, 30DF, 50, 60]
//
//   ANC data bytes may be represented by one ASCII character according to the following schema:
//     G  FAh 00h 00h
//     H  2 x (FAh 00h 00h)
//     I  3 x (FAh 00h 00h)
//     J  4 x (FAh 00h 00h)
//     K  5 x (FAh 00h 00h)
//     L  6 x (FAh 00h 00h)
//     M  7 x (FAh 00h 00h)
//     N  8 x (FAh 00h 00h)
//     O  9 x (FAh 00h 00h)
//     P  FBh 80h 80h
//     Q  FCh 80h 80h
//     R  FDh 80h 80h
//     S  96h 69h
//     T  61h 01h
//     U  E1h 00h 00h 00h
//     Z  00h
//
///////////////////////////////////////////////////////////////////////////////////
'''


def _expand(text):
    """The bytes an MCC line's hex and letters stand for."""
    out, nibble = bytearray(), None
    for char in text:
        if char in _ALIASES:
            out += _ALIASES[char]
        elif char in '0123456789abcdefABCDEF':
            if nibble is None:
                nibble = char
            else:
                out.append(int(nibble + char, 16))
                nibble = None
    return bytes(out)


def _frame(hours, minutes, seconds, frames, nominal, drop):
    count = (hours * 3600 + minutes * 60 + seconds) * nominal + frames
    if drop:
        all_minutes = hours * 60 + minutes
        count -= 2 * (nominal // 30) * (all_minutes - all_minutes // 10)
    return count


def _cc_data(packet):
    """([(cc_type, byte, byte)], the CDP's frame rate) of an ANC packet, as
    closed_captions reads it wherever it is carried."""
    return closed_captions.anc_triplets(packet)


def parse(text):
    """(subtitles, its settings: 'rate', the key of RATES for its frame rate
    or None for one the export does not offer, and 'captions', 'both' for a
    file with CEA-708, else '608') in MCC text. Raises ValueError for text
    that is not MCC, or holds no captions."""
    if not re.match(r'\s*File Format=MacCaption_MCC', text):
        raise ValueError('Not a MacCaption file: it does not begin "File Format=MacCaption_MCC"')
    label = (re.search(r'^Time Code Rate=(\S+)', text, re.M) or [None, '30DF'])[1].upper()
    nominal, rate, drop = _HEADER_RATES.get(label, _HEADER_RATES['30DF'])
    batches, packet_rate = [], None
    for line in text.split('\n'):
        match = _LINE.match(line)
        if not match:
            continue
        frame = _frame(*(int(part) for part in match.groups()[:4]), nominal, drop)
        triplets, cdp_rate = _cc_data(_expand(match.group(5)))
        packet_rate = packet_rate or cdp_rate
        if triplets:
            batches.append((frame, triplets))
    rate = packet_rate or rate
    segments, _track = closed_captions.decode(batches, rate)
    if not segments:
        raise ValueError('This MacCaption file holds no captions')
    dtvcc = any(cc_type >= 2 for _frame_, triplets in batches for cc_type, _a, _b in triplets)
    key = next((key for key, value in RATES.items()
                if value[0] == label and value[3] == drop and abs(value[2] - rate) < 0.01), None)
    return segments, {'rate': key, 'captions': 'both' if dtvcc else '608'}


def read(path):
    """(subtitles, its settings) in the MCC file at `path`."""
    with open(path, 'rb') as mcc_file:
        return parse(decode(mcc_file.read()))


def rate_for(fps):
    """The key of RATES for a video's frames a second, drop-frame at 29.97
    and 59.94 as broadcast has it; None for a rate not offered."""
    for key in ('23.976', '24', '25', '29.97DF', '30', '50', '59.94DF', '60'):
        if fps and abs(float(fps) - RATES[key][2]) < 0.01:
            return key
    return None


def _timecode(frame, nominal, drop):
    if drop:
        drops = 2 * (nominal // 30)
        per_minute = nominal * 60 - drops
        per_ten = per_minute * 10 + drops
        tens, rest = divmod(frame, per_ten)
        frame += drops * 9 * tens + (drops * ((rest - drops) // per_minute) if rest >= drops else 0)
    hours, rest = divmod(frame, nominal * 3600)
    minutes, rest = divmod(rest, nominal * 60)
    seconds, frames = divmod(rest, nominal)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{";" if drop else ":"}{frames:02d}'


def _compress(data):
    """MCC's hex for bytes, its letters where they fit."""
    out, index = [], 0
    while index < len(data):
        runs = 0
        while runs < 9 and data[index + 3 * runs:index + 3 * runs + 3] == _PADDING:
            runs += 1
        if runs:
            out.append('GHIJKLMNO'[runs - 1])
            index += 3 * runs
            continue
        for letter in 'PQRST':
            sequence = _ALIASES[letter]
            if data[index:index + len(sequence)] == sequence:
                out.append(letter)
                index += len(sequence)
                break
        else:
            out.append('Z' if data[index] == 0 else f'{data[index]:02X}')
            index += 1
    return ''.join(out)


def _packet(triplets, sequence, cdp_rate, count):
    """The ANC packet for a frame: a CDP with its caption data, padded."""
    cc_data = b''.join(bytes(triplet) for triplet in triplets) + _PADDING * (count - len(triplets))
    body = bytes([0x72, 0xE0 | count]) + cc_data + bytes([0x74, sequence >> 8, sequence & 0xFF])
    head = bytes([0x96, 0x69, 7 + len(body) + 1, (cdp_rate << 4) | 0x0F, 0x43, sequence >> 8, sequence & 0xFF])
    cdp = head + body
    cdp += bytes([(-sum(cdp)) & 0xFF])
    anc = bytes([0x61, 0x01, len(cdp)]) + cdp
    return anc + bytes([sum(anc) & 0xFF])


def write(segments, rate='29.97DF', speaker_changes=False, captions='both', now=None,
          mode='pop', channel=1, services=None, frames=0):
    """MCC text for `segments`, captions of `mode` in CEA-608's channel
    `channel` (1 to 4) and, with `captions` 'both', in CEA-708's service 1,
    at `rate` (a key of RATES); with `speaker_changes`, ">> " where a new
    speaker begins. `services` {number: segments} gives CEA-708 a service of
    its own for each further language. `frames` writes a packet for every
    frame up to it, as ancillary data needs."""
    label, nominal, frames_per_second, drop, cdp_rate, count = RATES.get(rate, RATES['29.97DF'])
    channel = channel if channel in (1, 2, 3, 4) else 1
    field = 1 if channel >= 3 else 0                   # CC3 and CC4 ride in the second field
    words, _breaks, shows = scc.frame_words(segments, frames_per_second, speaker_changes,
                                            mode=mode, channel=2 if channel in (2, 4) else 1)
    dtvcc = {}
    if captions != '608':
        by_service = {1: shows}
        for number, extra in (services or {}).items():
            if number != 1 and extra:
                _words_, _b_, extra_shows = scc.frame_words(extra, frames_per_second, speaker_changes, mode=mode)
                by_service[int(number)] = extra_shows
        dtvcc = cea708.frames_for(by_service, count - 2, mode)
    now = now or datetime.datetime.now()
    days = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
    months = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
              'November', 'December')
    lines = [HEADER, f'UUID={str(uuid.uuid4()).upper()}', 'Creation Program=Subtitld',
             f'Creation Date={days[now.weekday()]}, {months[now.month - 1]} {now.day}, {now.year}',
             f'Creation Time={now.hour}:{now.minute:02d}:{now.second:02d}', f'Time Code Rate={label}', '']
    span = range(max(frames, (max(set(words) | set(dtvcc)) + 1) if (words or dtvcc) else 0)) if frames \
        else sorted(set(words) | set(dtvcc))
    for sequence, frame in enumerate(span):
        # CEA-608's pair (or nothing) for each field, then CEA-708's.
        pair = bytes.fromhex(words[frame]) if frame in words else b'\x80\x80'
        triplets = [(0xFC, 0x80, 0x80), (0xFD, 0x80, 0x80)]
        triplets[field] = (0xFC if field == 0 else 0xFD, *pair)
        triplets += [(0xFF if cc_type == 3 else 0xFE, first, second) for cc_type, first, second in dtvcc.get(frame, [])]
        packet = _packet(triplets, sequence % 65536, cdp_rate, count)
        lines.append(f'{_timecode(frame, nominal, drop)}\t{_compress(packet)}')
    return '\n'.join(lines) + '\n'


def save(path, segments, rate='29.97DF', speaker_changes=False, captions='both',
         mode='pop', channel=1, services=None, frames=0):
    """Write `segments` to `path` as MCC (ASCII, Windows line ends)."""
    with open(path, 'w', encoding='ascii', newline='\r\n') as mcc_file:
        mcc_file.write(write(segments, rate, speaker_changes, captions, None, mode, channel, services, frames))
