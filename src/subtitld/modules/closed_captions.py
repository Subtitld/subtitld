"""Closed captions as videos and broadcast files carry them, CEA-608 and
CEA-708 in each frame's caption data: which of them to read, and those of
a video file.

A frame's caption data is a run of three-byte entries: one that says what
the two after it are, and whether they count. Type 0 is CEA-608's field 1
(its channels CC1 and CC2), type 1 its field 2 (CC3 and CC4), type 3 a
CEA-708 packet's start and type 2 the rest of it; CEA-708's packets carry
up to 63 services. Broadcasters put the main captions in CC1 and service
1, and often a second language in CC3 (or CC2) and service 2.

`decode` reads one of them: the one asked for, or, by default, the first
CEA-708 service with captions (decoded by the cea708 module), else the
first CEA-608 channel with captions (decoded as the SCC module does).

A video carries them in its frames (ATSC broadcasts and their
recordings: MPEG-2 user data, H.264 and HEVC SEI), which FFmpeg hands on
as it decodes each frame, so reading them takes as long as decoding the
video; as a track of their own, as QuickTime's c608; or as the ancillary
data of each frame, as broadcast MXF files carry them (SMPTE ST 436,
whose packets hold the same caption distribution packets an MCC file
does). `from_video` asks ffprobe for whichever it finds, the times
counted from the video's start; a track of its own and ancillary data
are read as they are, which is quick, the frames' own only by decoding
them.
"""

import json
import re
import subprocess
import tempfile

from subtitld.modules import cea708
from subtitld.modules import scc

TRACKS = ('auto', '708-1', '708-2', '708-3', '708-4', '708-5', '708-6', 'cc1', 'cc2', 'cc3', 'cc4')
_PAIR_RATE = 30000 / 1001                # CEA-608 pairs a second, whatever the video's rate
_HEX_ROW = re.compile(r'[0-9a-fA-F]{8}: ((?:[0-9a-fA-F]{2,4} ?)+)')


def decode(batches, rate, track='auto'):
    """(subtitles, the track they are from, or None when there are none) in
    caption data: `batches` [(frame, [(cc_type, byte, byte)])], the entries
    that count, at `rate` frames a second. CEA-608 pairs in a batch go out a
    frame each, from the batch's."""
    fields, dtvcc = {0: [], 1: []}, []
    for frame, triplets in batches:
        for field in (0, 1):
            words = [(first, second) for cc_type, first, second in triplets if cc_type == field]
            if words:
                fields[field].append((frame, words))
        channel = [triplet for triplet in triplets if triplet[0] >= 2]
        if channel:
            dtvcc.append((frame, channel))

    def cc(number):
        return scc.decode_pairs(fields[(number - 1) // 2], rate, channel=(number - 1) % 2 + 1)

    if track.startswith('708-'):
        return cea708.decode(dtvcc, rate, int(track[4:])), track
    if track.startswith('cc'):
        return cc(int(track[2:])), track
    for service in cea708.services(dtvcc):
        segments = cea708.decode(dtvcc, rate, service)
        if segments:
            return segments, f'708-{service}'
    for number in (1, 2, 3, 4):
        segments = cc(number)
        if segments:
            return segments, f'cc{number}'
    return [], None


def triplets(data):
    """[(cc_type, byte, byte)] of the entries that count in caption data."""
    return [(data[i] & 0x03, data[i + 1], data[i + 2]) for i in range(0, len(data) - 2, 3) if data[i] & 0x04]


_CDP_RATES = {1: 24000 / 1001, 2: 24.0, 3: 25.0, 4: 30000 / 1001, 5: 30.0, 6: 50.0, 7: 60000 / 1001, 8: 60.0}


def anc_triplets(packet):
    """([(cc_type, byte, byte)], the CDP's frame rate) of an ancillary data
    packet: a CEA-708 caption distribution packet's cc_data, or a SMPTE 334
    CEA-608 packet's. cc_type 0 and 1 are CEA-608's fields 1 and 2; 3 and 2
    a CEA-708 packet's start and the rest of it."""
    if len(packet) < 3 or packet[0] != 0x61:
        return [], None
    sdid, data = packet[1], packet[3:3 + packet[2]]
    if sdid == 0x02:
        # SMPTE 334: a byte with the field (set for field 1), then the pair.
        return [(0 if data[i] & 0x80 else 1, data[i + 1], data[i + 2]) for i in range(0, len(data) - 2, 3)], None
    if sdid != 0x01 or len(data) < 7 or data[:2] != b'\x96\x69':
        return [], None
    rate = _CDP_RATES.get(data[3] >> 4)
    pairs, index = [], 7
    while index < len(data):
        section = data[index]
        if section == 0x71:                        # time code
            index += 5
        elif section == 0x72:                      # cc_data
            count = data[index + 1] & 0x1F if index + 1 < len(data) else 0
            for offset in range(index + 2, min(len(data) - 2, index + 2 + 3 * count), 3):
                marker, first, second = data[offset:offset + 3]
                if marker & 0x04:
                    pairs.append((marker & 0x03, first, second))
            index += 2 + 3 * count
        elif section == 0x73:                      # service information
            index += 2 + 7 * ((data[index + 1] & 0x0F) if index + 1 < len(data) else 0)
        else:
            break                                  # the footer, or what is not known
    return pairs, rate


def _samples(payload, coding, count):
    """An ancillary packet's words as bytes: the 8-bit codings as they are,
    the 10-bit ones unpacked — their low eight bits are the data, the two
    above them parity."""
    if coding in (7, 8, 9):
        out = []
        for index in range(count):
            byte, offset = divmod(index * 10, 8)
            window = int.from_bytes(payload[byte:byte + 3].ljust(3, b'\x00'), 'big')
            out.append((window >> (14 - offset)) & 0xFF)
        return bytes(out)
    return bytes(payload[:count])


def anc_frame(data):
    """[(cc_type, byte, byte)] in a frame of ancillary data as SMPTE ST 436
    wraps it, which is how MXF files carry captions: a count of ancillary
    packets, then each one's line, how it is wrapped and coded, and its
    words."""
    if len(data) < 2:
        return []
    out, index = [], 2
    for _packet in range(int.from_bytes(data[:2], 'big')):
        if index + 14 > len(data):
            break
        coding = data[index + 3]
        count = int.from_bytes(data[index + 4:index + 6], 'big')
        size = int.from_bytes(data[index + 6:index + 10], 'big') * max(1, int.from_bytes(
            data[index + 10:index + 14], 'big'))
        index += 14
        out.extend(anc_triplets(_samples(data[index:index + size], coding, count))[0])
        index += size
    return out


def _filter_path(path):
    """`path` as a movie source's file name in a filtergraph: escaped for
    the option, then for the graph."""
    option = ''.join('\\' + char if char in "\\':" else char for char in str(path))
    return ''.join('\\' + char if char in "\\'[],;" else char for char in option)


def _rate(value):
    try:
        numerator, denominator = (float(part) for part in str(value).split('/'))
        return numerator / denominator if numerator and denominator else 0.0
    except ValueError:
        return 0.0


def probe(path, ffprobe='ffprobe', startupinfo=None):
    """What reading a video's captions needs: (the time it starts at, its
    frame rate, its duration, the index of a caption track or None, the
    index of an ancillary data track or None)."""
    result = subprocess.run([str(ffprobe), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
                             str(path)], capture_output=True, startupinfo=startupinfo, check=False)
    metadata = json.loads(result.stdout or b'{}')
    start = float(metadata.get('format', {}).get('start_time') or 0)
    duration = float(metadata.get('format', {}).get('duration') or 0)
    rate, track, ancillary = 0.0, None, None
    for stream in metadata.get('streams', []):
        if stream.get('codec_type') == 'video' and not rate and stream.get('codec_name') not in ('png', 'mjpeg'):
            rate = _rate(stream.get('avg_frame_rate')) or _rate(stream.get('r_frame_rate'))
        elif stream.get('codec_name') == 'eia_608' and track is None:
            track = int(stream.get('index', 0))
        elif stream.get('codec_name') == 'smpte_436m_anc' and ancillary is None:
            ancillary = int(stream.get('index', 0))
    return start, rate or _PAIR_RATE, duration, track, ancillary


def from_video(path, track='auto', ffprobe='ffprobe', startupinfo=None, progress=None, started=None):
    """(subtitles, the track they are from or None) in the closed captions a
    video carries: a caption track's, else its frames'. `progress(share)`
    hears how far it has read, from 0 to 1; `started(process)` gets the
    ffprobe process, to stop it."""
    start, video_rate, duration, caption_track, ancillary = probe(path, ffprobe, startupinfo)
    entries_of = triplets
    if caption_track is not None:
        rate = _PAIR_RATE
        command = [str(ffprobe), '-v', 'error', '-select_streams', str(caption_track), '-show_packets', '-show_data',
                   '-show_entries', 'packet=pts_time,data', '-of', 'compact=p=0:nk=1', str(path)]
    elif ancillary is not None:
        # An MXF's ancillary data: a frame of it a packet, SMPTE ST 436.
        rate, entries_of = video_rate, anc_frame
        command = [str(ffprobe), '-v', 'error', '-select_streams', str(ancillary), '-show_packets', '-show_data',
                   '-show_entries', 'packet=pts_time,data', '-of', 'compact=p=0:nk=1', str(path)]
    else:
        rate = video_rate
        command = [str(ffprobe), '-v', 'error', '-f', 'lavfi', '-i', f'movie={_filter_path(path)}[out0+subcc]',
                   '-select_streams', 's', '-show_packets', '-show_data', '-show_entries', 'packet=pts_time,data',
                   '-of', 'compact=p=0:nk=1']
    errors = tempfile.TemporaryFile()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, startupinfo=startupinfo)
    if started:
        started(process)
    batches = []
    for line in process.stdout:
        when, _bar, dump = line.decode('ascii', 'replace').strip().partition('|')
        try:
            seconds = float(when) - start
        except ValueError:
            continue
        data = bytes.fromhex(''.join(row.replace(' ', '') for row in _HEX_ROW.findall(dump.replace('\\n', '\n'))))
        entries = entries_of(data)
        if entries:
            batches.append((max(0, round(seconds * rate)), entries))
        if progress and duration:
            progress(min(1.0, max(0.0, seconds / duration)))
    if process.wait() != 0 and not batches:
        errors.seek(0)
        raise RuntimeError(errors.read().decode('utf-8', 'replace').strip() or 'ffprobe could not read the video')
    errors.close()
    batches.sort(key=lambda batch: batch[0])
    return decode(batches, rate, track)
