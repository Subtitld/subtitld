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
video; or as a track of their own, as QuickTime's c608, read as it is.
`from_video` asks ffprobe for either, the times counted from the video's
start.
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
    frame rate, its duration, the index of a caption track or None)."""
    result = subprocess.run([str(ffprobe), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
                             str(path)], capture_output=True, startupinfo=startupinfo, check=False)
    metadata = json.loads(result.stdout or b'{}')
    start = float(metadata.get('format', {}).get('start_time') or 0)
    duration = float(metadata.get('format', {}).get('duration') or 0)
    rate, track = 0.0, None
    for stream in metadata.get('streams', []):
        if stream.get('codec_type') == 'video' and not rate and stream.get('codec_name') not in ('png', 'mjpeg'):
            rate = _rate(stream.get('avg_frame_rate')) or _rate(stream.get('r_frame_rate'))
        elif stream.get('codec_name') == 'eia_608' and track is None:
            track = int(stream.get('index', 0))
    return start, rate or _PAIR_RATE, duration, track


def from_video(path, track='auto', ffprobe='ffprobe', startupinfo=None, progress=None, started=None):
    """(subtitles, the track they are from or None) in the closed captions a
    video carries: a caption track's, else its frames'. `progress(share)`
    hears how far it has read, from 0 to 1; `started(process)` gets the
    ffprobe process, to stop it."""
    start, video_rate, duration, caption_track = probe(path, ffprobe, startupinfo)
    if caption_track is not None:
        rate = _PAIR_RATE
        command = [str(ffprobe), '-v', 'error', '-select_streams', str(caption_track), '-show_packets', '-show_data',
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
        entries = triplets(data)
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
