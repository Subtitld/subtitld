"""HLS subtitles (.m3u8): WebVTT cut into segments, as HTTP Live Streaming
serves them (RFC 8216, and Apple's HLS Authoring Specification).

A subtitle rendition is a media playlist: "#EXTM3U", the longest segment's
duration (#EXT-X-TARGETDURATION, a whole number of seconds no segment's
duration rounds above), and then each segment's duration (#EXTINF) and
address, to "#EXT-X-ENDLIST" when nothing more will come. Each segment is a
WebVTT file of its own, whose header maps its cue times onto the MPEG-2
timestamps of the video it goes with:

    WEBVTT
    X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000

says that a cue at 0 is shown with the frame stamped 900000 (in 90 kHz
ticks, so 10 seconds: where many encoders start a stream's clock, to stay
clear of its wrapping round). A segment holds every cue shown during it,
each with its whole time, so a cue that spans two segments is in both;
one with nothing to show has no cues, and the playlist goes on for as long
as the programme does.

Reading follows a playlist from a file, and the addresses in it from where
it is (relative ones beside it, http(s) ones fetched); a master playlist,
one that lists renditions (#EXT-X-MEDIA:TYPE=SUBTITLES), is followed to
its subtitles, the rendition in the language asked for, else the default
one, else the first. Each segment is read as WebVTT is (modules.webvtt)
and moved by its timestamp map against the others': a time counts from
the smallest map any segment gives, the stream's start as the maps tell
it, so that files whose segments all map 0 to 900000, and files whose
segments each start their cues at 0 and map that to where the segment
starts, read the same. A cue repeated in the segments it spans is one
cue again, as is one a segmenter cut in two at a segment's edge. A
segment in a part of a file (#EXT-X-BYTERANGE) is read from that part.
Segments in fragmented MP4 (an #EXT-X-MAP for their initialization, as
Apple asks of IMSC) are read by modules.isobmff_text.

Writing gives the playlist (a VOD one: version 3, for durations to the
millisecond) and its segments, beside it in a folder named after it, every
segment the same length but the last (6 seconds by default, as Apple
recommends), each with the same timestamp map (MPEGTS:900000 by default),
so that its cue times are the programme's.
"""

import math
import os
import re
import urllib.parse
import urllib.request

from subtitld.modules import net, webvtt
from subtitld.modules.srt import decode

SEGMENT_DURATION = 6.0
MPEGTS = 900000                              # 10 s at 90 kHz, as most encoders start
SEGMENT_DURATIONS = (2.0, 4.0, 6.0, 10.0)
MPEGTS_OFFSETS = (900000, 0)

_CLOCK = 90000
_WRAP = 1 << 33                              # a PES timestamp's 33 bits
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_LOCAL = re.compile(r'^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$')
_NEAR = 0.0015                               # a millisecond, with rounding to spare


def attributes(text):
    """{NAME: value} of an HLS attribute list (TYPE=SUBTITLES,URI="a b"),
    quotes taken off."""
    return {name: value[1:-1] if value.startswith('"') else value
            for name, value in _ATTRIBUTE.findall(text)}


def _is_url(address):
    return urllib.parse.urlsplit(str(address)).scheme in ('http', 'https')


def resolve(base, uri):
    """Where `uri`, as a playlist at `base` names it, is: a URL against a
    URL, a path beside a file."""
    if _is_url(uri):
        return uri
    if _is_url(base):
        return urllib.parse.urljoin(base, uri)
    if uri.startswith('file:'):
        return urllib.request.url2pathname(urllib.parse.urlsplit(uri).path)
    # A base ending in a slash (a DASH BaseURL) is a folder itself.
    where = os.path.abspath(base) if str(base).endswith(('/', os.sep)) else os.path.dirname(os.path.abspath(base))
    return os.path.join(where, urllib.parse.unquote(uri))


def fetch(address, byterange=None):
    """The bytes at `address`, a path or an http(s) URL; with `byterange`,
    (length, offset), those bytes only."""
    if _is_url(address):
        request = urllib.request.Request(address, headers={'User-Agent': 'Subtitld'})
        if byterange:
            request.add_header('Range', f'bytes={byterange[1]}-{byterange[1] + byterange[0] - 1}')
        with urllib.request.urlopen(request, timeout=30, context=net.https_context()) as response:
            data = response.read()
            if byterange and response.status != 206:
                data = data[byterange[1]:byterange[1] + byterange[0]]
            return data
    with open(address, 'rb') as source:
        if byterange:
            source.seek(byterange[1])
            return source.read(byterange[0])
        return source.read()


def playlist(text):
    """What an HLS playlist says: {'segments': [{'duration', 'uri',
    'byterange', 'map'}], 'renditions': [{NAME: value} of each subtitles
    rendition], 'target': its target duration}. Raises ValueError when the
    text is not a playlist."""
    lines = [line.strip() for line in text.lstrip('﻿').split('\n')]
    if not lines or lines[0] != '#EXTM3U':
        raise ValueError('This is not an HLS playlist: it does not begin with #EXTM3U.')
    found = {'segments': [], 'renditions': [], 'target': 0.0}
    duration = None
    byterange = None
    following = 0                            # where a byte range with no offset starts
    media_map = None
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            try:
                duration = float(line[8:].split(',')[0])
            except ValueError:
                duration = 0.0
        elif line.startswith('#EXT-X-TARGETDURATION:'):
            try:
                found['target'] = float(line.split(':', 1)[1])
            except ValueError:
                pass
        elif line.startswith('#EXT-X-BYTERANGE:'):
            length, _at, offset = line.split(':', 1)[1].partition('@')
            byterange = (int(length), int(offset) if offset else following)
        elif line.startswith('#EXT-X-MAP:'):
            media_map = attributes(line.split(':', 1)[1])
        elif line.startswith('#EXT-X-MEDIA:'):
            rendition = attributes(line.split(':', 1)[1])
            if rendition.get('TYPE') == 'SUBTITLES' and rendition.get('URI'):
                found['renditions'].append(rendition)
        elif not line.startswith('#'):
            if duration is not None:
                found['segments'].append({'duration': duration, 'uri': line, 'byterange': byterange,
                                          'map': media_map})
                if byterange:
                    following = byterange[0] + byterange[1]
            duration = None
            byterange = None
    return found


def _rendition(renditions, language=None):
    """The subtitles rendition to follow: in `language` (its first part
    will do), else the default one, else the first."""
    if language:
        wanted = str(language).lower().replace('_', '-')
        for rendition in renditions:
            if rendition.get('LANGUAGE', '').lower() == wanted:
                return rendition
        for rendition in renditions:
            if rendition.get('LANGUAGE', '').lower().split('-')[0] == wanted.split('-')[0]:
                return rendition
    return next((rendition for rendition in renditions if rendition.get('DEFAULT') == 'YES'), renditions[0])


def _local_seconds(value):
    match = _LOCAL.match(value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, fraction = match.groups()
    total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    return total + (int(fraction) / 10 ** len(fraction) if fraction else 0.0)


def timestamp_map(text):
    """(MPEGTS, LOCAL in seconds) a WebVTT segment's header maps; (0, 0)
    without one, as RFC 8216 has a player assume."""
    for line in text.split('\n')[1:]:
        if not line.strip():
            break
        if line.startswith('X-TIMESTAMP-MAP='):
            values = dict(part.split(':', 1) for part in line[16:].split(',') if ':' in part)
            try:
                mpegts = int(values.get('MPEGTS', '0').strip())
            except ValueError:
                mpegts = 0
            return mpegts, _local_seconds(values.get('LOCAL', '0:00:00.000'))
    return 0, 0.0


def _merged(segments, edges):
    """`segments` from every segment, each once: a cue repeated in the
    segments it spans as one, and one cut in two at a segment's edge (a
    time in `edges`) as one again."""
    def same(one, other):
        return all(one.get(key) == other.get(key) for key in ('text', 'speaker', 'writing'))

    kept = []
    seen = set()
    for segment in sorted(segments, key=lambda s: (s['start'], s['end'])):
        key = (round(segment['start'], 3), round(segment['end'], 3), segment['text'],
               segment.get('speaker'), segment.get('writing'))
        if key in seen:
            continue
        seen.add(key)
        joined = False
        for earlier in reversed(kept):
            if earlier['end'] < segment['start'] - _NEAR:
                break
            if abs(earlier['end'] - segment['start']) < _NEAR and same(earlier, segment) \
                    and any(abs(edge - segment['start']) < _NEAR for edge in edges):
                earlier['end'] = segment['end']
                joined = True
                break
        if not joined:
            kept.append(segment)
    return kept


def read(path, language=None):
    """(subtitles, settings) of the HLS playlist at `path` (a file, or an
    http(s) URL): following a master playlist to its subtitles rendition in
    `language` if it has one. The settings: {'language' (the rendition's,
    or ''), 'segment_duration' (the target duration), 'mpegts' (the
    timestamp the stream's start is given)}. Raises ValueError for what is
    not an HLS subtitles playlist."""
    found = playlist(decode(fetch(path)))
    rendition = {}
    if not found['segments'] and found['renditions']:
        rendition = _rendition(found['renditions'], language)
        path = resolve(path, rendition['URI'])
        found = playlist(decode(fetch(path)))
    if not found['segments']:
        raise ValueError('This playlist lists no subtitles: neither segments nor a subtitles rendition.')
    if found['segments'][0]['map']:
        # IMSC (or WebVTT) in fragmented MP4, as DASH carries it too.
        from subtitld.modules import isobmff_text
        segments, settings = isobmff_text.read_hls(path, found)
        settings['language'] = rendition.get('LANGUAGE') or settings['language']
        return segments, settings
    pieces = []
    first = None
    starts = []
    clock = 0.0
    for entry in found['segments']:
        text = decode(fetch(resolve(path, entry['uri']), entry['byterange']))
        mpegts, local = timestamp_map(text)
        if first is None:
            first = mpegts
        # Timestamps that wrapped round past 2^33 while the cue times went on.
        while mpegts - first > _WRAP // 2:
            mpegts -= _WRAP
        while first - mpegts > _WRAP // 2:
            mpegts += _WRAP
        pieces.append((mpegts / _CLOCK - local, webvtt.parse(text)))
        starts.append(clock)
        clock += entry['duration']
    base = min(mapped for mapped, _cues in pieces)
    segments = []
    for mapped, cues in pieces:
        shift = mapped - base
        for cue in cues:
            cue['start'] = round(cue['start'] + shift, 3)
            cue['end'] = round(cue['end'] + shift, 3)
            segments.append(cue)
    settings = {'language': rendition.get('LANGUAGE', ''),
                'segment_duration': found['target'] or (found['segments'][0]['duration'] if found['segments'] else 0),
                'mpegts': int(round(base * _CLOCK)) % _WRAP}
    return _merged(segments, starts[1:] + [clock]), settings


def _target(duration):
    """A segment's duration rounded to the nearest second, halves up."""
    return int(math.floor(duration + 0.5))


def write(segments, duration=SEGMENT_DURATION, mpegts=MPEGTS, voices=False, total=0.0, folder=''):
    """(playlist, [(name, WebVTT text)]) for `segments` cut into segments of
    `duration` seconds, each mapping cue time 0 to `mpegts`, for as long as
    the subtitles last or `total` seconds (the programme's), whichever is
    longer; the segments' addresses in the playlist inside `folder`. With
    `voices`, speakers as voice tags."""
    duration = max(1.0, float(duration or SEGMENT_DURATION))
    mpegts = int(mpegts) % _WRAP
    segments = [segment for segment in segments if str(segment.get('text') or '').strip()]
    last = max([float(segment.get('end', 0)) for segment in segments] + [float(total or 0), 0.001])
    count = max(1, math.ceil(round(last / duration, 6)))
    header = f'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{mpegts}\n'
    files = []
    lengths = []
    for index in range(count):
        start = index * duration
        end = min(last, start + duration) if index == count - 1 else start + duration
        shown = [segment for segment in segments
                 if float(segment.get('start', 0)) < end and
                 (float(segment.get('end', 0)) > start or float(segment.get('start', 0)) >= start)]
        text = webvtt.write(shown, voices)
        files.append((f'segment{index:05d}.vtt', header + text[len('WEBVTT\n'):]))
        lengths.append(end - start)
    prefix = urllib.parse.quote(folder) + '/' if folder else ''
    lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{max(1, max(_target(length) for length in lengths))}',
             '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:VOD']
    for (name, _text), length in zip(files, lengths):
        lines += [f'#EXTINF:{length:.3f},', prefix + name]
    lines.append('#EXT-X-ENDLIST')
    return '\n'.join(lines) + '\n', files


def folder_for(path, suffix='segments'):
    """The folder a playlist (or manifest) at `path` keeps its segments in:
    beside it, named after it."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(path)), f'{stem}_{suffix}')


def save(path, segments, duration=SEGMENT_DURATION, mpegts=MPEGTS, voices=False, total=0.0):
    """Write `segments` as the HLS playlist at `path` and its WebVTT segments
    in a folder beside it; the segments a save before left there go."""
    folder = folder_for(path)
    playlist_text, files = write(segments, duration, mpegts, voices, total, os.path.basename(folder))
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if re.fullmatch(r'segment\d{5}\.vtt', name):
            os.remove(os.path.join(folder, name))
    for name, text in files:
        with open(os.path.join(folder, name), 'w', encoding='utf-8', newline='\n') as vtt_file:
            vtt_file.write(text)
    with open(path, 'w', encoding='utf-8', newline='\n') as playlist_file:
        playlist_file.write(playlist_text)
