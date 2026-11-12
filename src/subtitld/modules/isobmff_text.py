"""WebVTT and TTML in (fragmented) MP4, as ISO/IEC 14496-30 carries them:
the subtitle tracks of DASH, CMAF (.cmft), DVB-DASH (EBU-TT-D, as EBU
Tech 3381 puts it there) and HLS's IMSC.

An MP4 file is a run of boxes, each its size and four letters, some of
them holding boxes of their own. The movie box (moov) describes each track:
its clock (mdhd: so many units a second) and language, its kind (hdlr),
and in its sample description (stsd) what its samples are:

- 'wvtt': WebVTT. The description holds the file's header (vttC: "WEBVTT"
  and its STYLE and REGION blocks); a sample holds the cues shown for all
  of its time, each a box (vttc) of its text (payl), settings (sttg) and
  identifier (iden), or an empty-cue box (vtte) for a time with nothing to
  show. Cues that overlap are cut where each starts and ends, so that a
  sample follows the one before it with no gap and no overlap.
- 'stpp': XML subtitles, TTML (IMSC, EBU-TT-D). The description names the
  document's namespace, and a MIME type with the profile ('im1t' IMSC 1.0.1
  Text, 'im2t' IMSC 1.1 Text, 'etd1' EBU-TT-D); a sample is one whole TTML
  document, whose times count on the track's clock, not from the sample's
  start, and of which only what falls in the sample's time is shown.

Where a sample's bytes are, and when it starts and how long it lasts,
comes from the sample table (stts, stsz, stsc, stco) in a plain MP4, or,
in a fragmented one, from each movie fragment (moof): its track fragment's
header (tfhd: the defaults, where the data counts from), its decode time
(tfdt) and its runs of samples (trun), the defaults the movie's extends box
(mvex/trex) gives standing in for what they leave out. The bytes are in the
media data box (mdat) after it.

Reading takes a file with the movie and its fragments in it, an
initialization segment and the media segments after it, or a DASH
manifest (.mpd) that names them: its text adaptation set in the language
asked for, else the first, its segments by SegmentTemplate ($Number$ or
$Time$, a SegmentTimeline or a duration), a SegmentList or a single file,
relative to the manifest or its BaseURL. A WebVTT or TTML file a manifest
names by itself is read as it is. Each WebVTT sample's cues are read by
modules.webvtt, each TTML document by modules.ttml, cut to its sample's
time; a cue that goes on from one sample to the next, the same, is one
cue again. A document that counts its times from its sample's start,
as some packagers once wrote, is read so.

Writing gives a CMAF track file: a movie with the one text track and, for
each segment (6 seconds by default), a fragment of it. WebVTT as
modules.webvtt writes it, its header in vttC and each cue cut into the
samples it spans, an empty cue where nothing shows; TTML as modules.ttml writes it in
the profile asked for (IMSC 1.1 Text by default, or IMSC 1.0.1, or
EBU-TT-D), one document a fragment holding the cues shown during it, cut to
its time. The file's clock counts in milliseconds; it lasts as long as the
programme does. A DASH manifest can be written too, with its segments in
a folder beside it, the initialization segment and one file a fragment.
"""

import math
import os
import re
import struct
import urllib.parse
import xml.etree.ElementTree as ET

from subtitld.modules import export_options, hls, ttml, usf, webvtt
from subtitld.modules.srt import decode

SEGMENT_DURATION = 6.0
CODECS = ('wvtt', 'stpp')
STPP_PROFILES = ('imsc1.1', 'imsc1', 'ebu-tt-d')
# What the export dialog offers: WebVTT, or TTML in one of its profiles.
CARRIAGES = ('wvtt',) + STPP_PROFILES
# The profile a codecs string or MIME type names, as RFC 8216, DASH and
# CMAF write it ("stpp.ttml.im1t"; "application/ttml+xml;codecs=im1t").
_PROFILE_CODES = {'imsc1': 'im1t', 'imsc1.1': 'im2t', 'ebu-tt-d': 'etd1'}
_CODE_PROFILES = {code: profile for profile, code in _PROFILE_CODES.items()}
# CMAF's brand for each kind of text track (ISO/IEC 23000-19).
_CMAF_BRANDS = {'wvtt': b'cwvt', 'imsc1': b'im1t', 'imsc1.1': b'im2t'}
_TTML_NS = 'http://www.w3.org/ns/ttml'
_TIMESCALE = 1000
_CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'mvex', b'moof', b'traf', b'dinf', b'edts', b'udta'}
# ISO 639-2's terminologic codes, which MP4 writes, for the bibliographic ones.
_TERMINOLOGIC = {'cze': 'ces', 'ger': 'deu', 'gre': 'ell', 'baq': 'eus', 'per': 'fas', 'fre': 'fra', 'arm': 'hye',
                 'ice': 'isl', 'geo': 'kat', 'may': 'msa', 'dut': 'nld', 'rum': 'ron', 'slo': 'slk', 'chi': 'zho'}
_NEAR = 0.0015
_TEMPLATE = re.compile(r'\$(RepresentationID|Number|Time|Bandwidth|)(?:%0(\d+)d)?\$')
_DURATION = re.compile(r'^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$')


# Reading boxes

def boxes(data, start=0, end=None):
    """(type, payload start, payload end) of each box in `data` between
    `start` and `end`, in order; a box that runs past the end stops there."""
    end = len(data) if end is None else end
    at = start
    while at + 8 <= end:
        size, kind = struct.unpack_from('>I4s', data, at)
        header = 8
        if size == 1:
            if at + 16 > end:
                return
            size = struct.unpack_from('>Q', data, at + 8)[0]
            header = 16
        elif size == 0:
            size = end - at
        if kind == b'uuid':
            header += 16
        if size < header:
            return
        yield kind, at + header, min(end, at + size)
        at += size


def _children(data, start, end):
    """{type: [(payload start, payload end)]} of the boxes inside one."""
    found = {}
    for kind, payload, stop in boxes(data, start, end):
        found.setdefault(kind, []).append((payload, stop))
    return found


def _first(data, start, end, *path):
    """(start, end) of the first box down `path` (types) inside a box, or None."""
    for kind in path:
        found = _children(data, start, end).get(kind)
        if not found:
            return None
        start, end = found[0]
    return start, end


def _string(data, start, end):
    """A null-terminated UTF-8 string at `start` and where the next begins."""
    stop = data.find(b'\0', start, end)
    stop = end if stop < 0 else stop
    return data[start:stop].decode('utf-8', 'replace'), stop + 1


def _language(packed):
    """The ISO 639-2 code an mdhd packs into 15 bits ('und' for none)."""
    letters = ''.join(chr(((packed >> shift) & 0x1f) + 0x60) for shift in (10, 5, 0))
    return letters if letters.isalpha() and letters.islower() else 'und'


def _track(data, start, end):
    """What a trak box says of its track: {'id', 'timescale', 'language',
    'handler', 'codec', 'config' (wvtt's header), 'namespace', 'mime',
    'samples' (from a sample table), ...}."""
    found = {'id': 0, 'timescale': 1, 'language': '', 'handler': '', 'codec': '', 'config': '', 'namespace': '',
             'mime': '', 'samples': []}
    tkhd = _first(data, start, end, b'tkhd')
    if tkhd:
        version = data[tkhd[0]]
        found['id'] = struct.unpack_from('>I', data, tkhd[0] + (20 if version == 1 else 12))[0]
    mdia = _first(data, start, end, b'mdia')
    if not mdia:
        return found
    mdhd = _first(data, *mdia, b'mdhd')
    if mdhd:
        version = data[mdhd[0]]
        at = mdhd[0] + (20 if version == 1 else 12)
        found['timescale'] = struct.unpack_from('>I', data, at)[0] or 1
        at += 12 if version == 1 else 8
        language = _language(struct.unpack_from('>H', data, at)[0])
        found['language'] = '' if language == 'und' else language
    elng = _first(data, *mdia, b'elng')
    if elng:
        found['language'] = _string(data, elng[0] + 4, elng[1])[0] or found['language']
    hdlr = _first(data, *mdia, b'hdlr')
    if hdlr:
        found['handler'] = data[hdlr[0] + 8:hdlr[0] + 12].decode('latin-1')
    stbl = _first(data, *mdia, b'minf', b'stbl')
    if not stbl:
        return found
    stsd = _first(data, *stbl, b'stsd')
    if stsd and stsd[1] - stsd[0] >= 16:
        for kind, payload, stop in boxes(data, stsd[0] + 8, stsd[1]):
            found['codec'] = kind.decode('latin-1')
            entry = payload + 8                    # six reserved bytes and the data reference
            if kind == b'wvtt':
                config = _first(data, entry, stop, b'vttC')
                if config:
                    found['config'] = data[config[0]:config[1]].decode('utf-8', 'replace')
            elif kind == b'stpp':
                found['namespace'], at = _string(data, entry, stop)
                found['schema_location'], at = _string(data, at, stop)
                found['mime'], at = _string(data, at, stop)
                mime = _first(data, at, stop, b'mime')
                if mime:
                    found['mime'] = _string(data, mime[0] + 4, mime[1])[0]
            break
    found['samples'] = _table_samples(data, stbl)
    return found


def _table_samples(data, stbl):
    """[(decode time, duration, start, size)] of a plain MP4's sample table."""
    table = _children(data, *stbl)
    if b'stts' not in table or (b'stsz' not in table and b'stz2' not in table):
        return []
    times = []
    at = table[b'stts'][0][0]
    clock = 0
    for index in range(struct.unpack_from('>I', data, at + 4)[0]):
        count, delta = struct.unpack_from('>II', data, at + 8 + index * 8)
        for _number in range(count):
            times.append((clock, delta))
            clock += delta
    offsets = [0] * len(times)
    if b'ctts' in table:
        at = table[b'ctts'][0][0]
        signed = data[at] == 1
        sample = 0
        for index in range(struct.unpack_from('>I', data, at + 4)[0]):
            count, offset = struct.unpack_from('>Ii' if signed else '>II', data, at + 8 + index * 8)
            for _number in range(count):
                if sample < len(offsets):
                    offsets[sample] = offset
                sample += 1
    sizes = []
    if b'stsz' in table:
        at = table[b'stsz'][0][0]
        size, count = struct.unpack_from('>II', data, at + 4)
        sizes = [size] * count if size else list(struct.unpack_from(f'>{count}I', data, at + 12))
    chunks = []
    if b'stco' in table:
        at = table[b'stco'][0][0]
        count = struct.unpack_from('>I', data, at + 4)[0]
        chunks = list(struct.unpack_from(f'>{count}I', data, at + 8))
    elif b'co64' in table:
        at = table[b'co64'][0][0]
        count = struct.unpack_from('>I', data, at + 4)[0]
        chunks = list(struct.unpack_from(f'>{count}Q', data, at + 8))
    runs = []
    if b'stsc' in table:
        at = table[b'stsc'][0][0]
        runs = [struct.unpack_from('>III', data, at + 8 + index * 12)[:2]
                for index in range(struct.unpack_from('>I', data, at + 4)[0])]
    samples = []
    sample = 0
    for number, chunk_start in enumerate(chunks, 1):
        per_chunk = next((per for first, per in reversed(runs) if first <= number), 1)
        at = chunk_start
        for _index in range(per_chunk):
            if sample >= min(len(times), len(sizes)):
                break
            clock, delta = times[sample]
            samples.append((clock + offsets[sample], delta, at, sizes[sample]))
            at += sizes[sample]
            sample += 1
    return samples


def movie(data):
    """({track id: track} of the movie box in `data`, {track id: (default
    duration, default size)} of its mvex), or ({}, {}) without one."""
    moov = _first(data, 0, len(data), b'moov')
    if not moov:
        return {}, {}
    tracks = {}
    for start, end in _children(data, *moov).get(b'trak', []):
        found = _track(data, start, end)
        tracks[found['id']] = found
    defaults = {}
    mvex = _first(data, *moov, b'mvex')
    if mvex:
        for start, _end in _children(data, *mvex).get(b'trex', []):
            track_id, _index, duration, size = struct.unpack_from('>IIII', data, start + 4)
            defaults[track_id] = (duration, size)
    return tracks, defaults


def fragment_samples(data, track_id, defaults=(0, 0), clock=0):
    """([(decode time, duration, bytes)] of track `track_id` in the movie
    fragments in `data`, the decode time after the last). A fragment
    without a decode time follows on from `clock`."""
    samples = []
    for kind, moof_start, moof_end in boxes(data):
        if kind != b'moof':
            continue
        moof = moof_start - 8
        for traf_start, traf_end in _children(data, moof_start, moof_end).get(b'traf', []):
            parts = _children(data, traf_start, traf_end)
            if b'tfhd' not in parts:
                continue
            at = parts[b'tfhd'][0][0]
            flags = struct.unpack_from('>I', data, at)[0] & 0xffffff
            if struct.unpack_from('>I', data, at + 4)[0] != track_id:
                continue
            at += 8
            base = moof
            if flags & 0x01:
                base = struct.unpack_from('>Q', data, at)[0]
                at += 8
            if flags & 0x02:
                at += 4
            duration, size = defaults
            if flags & 0x08:
                duration = struct.unpack_from('>I', data, at)[0]
                at += 4
            if flags & 0x10:
                size = struct.unpack_from('>I', data, at)[0]
            if b'tfdt' in parts:
                at = parts[b'tfdt'][0][0]
                clock = struct.unpack_from('>Q' if data[at] == 1 else '>I', data, at + 4)[0]
            following = None
            for run_start, _run_end in parts.get(b'trun', []):
                head = struct.unpack_from('>I', data, run_start)[0]
                version, run_flags = head >> 24, head & 0xffffff
                count = struct.unpack_from('>I', data, run_start + 4)[0]
                at = run_start + 8
                where = following
                if run_flags & 0x01:
                    where = base + struct.unpack_from('>i', data, at)[0]
                    at += 4
                if where is None:
                    # No data offset: the media data box after the fragment.
                    where = next((payload for name, payload, _stop in boxes(data, moof_end)
                                  if name == b'mdat'), moof_end)
                if run_flags & 0x04:
                    at += 4
                for _index in range(count):
                    sample_duration, sample_size, offset = duration, size, 0
                    if run_flags & 0x100:
                        sample_duration = struct.unpack_from('>I', data, at)[0]
                        at += 4
                    if run_flags & 0x200:
                        sample_size = struct.unpack_from('>I', data, at)[0]
                        at += 4
                    if run_flags & 0x400:
                        at += 4
                    if run_flags & 0x800:
                        offset = struct.unpack_from('>i' if version else '>I', data, at)[0]
                        at += 4
                    samples.append((clock + offset, sample_duration, data[where:where + sample_size]))
                    where += sample_size
                    clock += sample_duration
                following = where
    return samples, clock


# The samples' subtitles

def _vtt_cues(sample):
    """[(identifier, settings, payload)] of a WebVTT sample; [] for an
    empty one."""
    cues = []
    for kind, start, end in boxes(sample):
        if kind != b'vttc':
            continue
        parts = {name: sample[payload:stop].decode('utf-8', 'replace')
                 for name, payload, stop in boxes(sample, start, end)}
        cues.append((parts.get(b'iden', ''), parts.get(b'sttg', ''), parts.get(b'payl', '')))
    return cues


def _key(segment):
    return repr(sorted((name, value) for name, value in segment.items() if name not in ('start', 'end')))


def joined(segments):
    """`segments` with each one that goes on, the same, where another ends
    joined to it: a cue cut into samples (or segments) as one again."""
    kept = []
    open_ends = {}                           # key: the kept cues it has, by where they end
    for segment in sorted(segments, key=lambda s: (s['start'], s['end'])):
        key = _key(segment)
        earlier = next((cue for cue in open_ends.get(key, []) if abs(cue['end'] - segment['start']) < _NEAR), None)
        if earlier is not None:
            earlier['end'] = max(earlier['end'], segment['end'])
            continue
        if any(cue['start'] == segment['start'] and cue['end'] == segment['end'] for cue in open_ends.get(key, [])):
            continue
        kept.append(segment)
        open_ends.setdefault(key, []).append(segment)
    return kept


def _ttml_cues(document, start, end):
    """The subtitles of one TTML sample shown from `start` to `end` (seconds),
    and its language."""
    segments, language, _metadata = ttml.read_text(decode(document))
    inside = [segment for segment in segments if segment['start'] < end and segment['end'] > start]
    if start > 0 and not inside:
        # A document that counts from its sample's start.
        moved = [dict(segment, start=segment['start'] + start, end=segment['end'] + start) for segment in segments]
        if any(segment['start'] < end and segment['end'] > start for segment in moved):
            inside = [segment for segment in moved if segment['start'] < end and segment['end'] > start]
    for segment in inside:
        segment['start'] = round(max(segment['start'], start), 3)
        segment['end'] = round(min(segment['end'], end), 3)
    return inside, language


def subtitles(track, samples, shift=0.0):
    """(subtitles, language) of `samples` [(decode time, duration, bytes)]
    of `track`, their times moved by `shift` seconds."""
    scale = float(track['timescale'] or 1)
    language = ''
    if track['codec'] == 'wvtt':
        config = track.get('config') or 'WEBVTT'
        if not config.lstrip('﻿').startswith('WEBVTT'):
            config = 'WEBVTT\n' + config
        blocks = [config.rstrip('\n')]
        for clock, duration, data in samples:
            start = webvtt.format_time(max(0.0, clock / scale + shift))
            end = webvtt.format_time(max(0.0, (clock + duration) / scale + shift))
            for _identifier, settings, payload in _vtt_cues(data):
                payload = '\n'.join(line for line in payload.replace('\r\n', '\n').split('\n') if line.strip())
                if payload:
                    blocks.append(f'{start} --> {end}' + (f' {settings.strip()}' if settings.strip() else '')
                                  + '\n' + payload.replace('-->', '->'))
        found = webvtt.parse('\n\n'.join(blocks) + '\n')
    elif track['codec'] == 'stpp':
        found = []
        for clock, duration, data in samples:
            start, end = clock / scale + shift, (clock + duration) / scale + shift
            cues, document_language = _ttml_cues(data, start, end)
            found += cues
            language = language or document_language
    else:
        raise ValueError(f'This MP4 track is not WebVTT or TTML ({track["codec"] or "no sample description"}).')
    return joined(found), language


def text_tracks(tracks):
    """The WebVTT and TTML tracks of {id: track}, in order of id."""
    return [tracks[number] for number in sorted(tracks) if tracks[number]['codec'] in CODECS]


def _choose(tracks, language=None, track_id=None):
    found = text_tracks(tracks)
    if track_id is not None:
        found = [track for track in found if track['id'] == track_id]
    if not found:
        raise ValueError('This MP4 has no WebVTT or TTML subtitle track.')
    if language:
        primary = str(language).lower().replace('_', '-').split('-')[0]
        for track in found:
            named = str(track['language'] or '').lower().replace('_', '-').split('-')[0]
            if named and (named == primary or usf.FROM_ISO639_2.get(named) == primary):
                return track
    return found[0]


def _settings(track, samples, language):
    lengths = sorted({round(duration / float(track['timescale'] or 1), 3) for _clock, duration, _data in samples})
    profile = ''
    code = (track.get('mime') or '').rpartition('codecs=')[2].split('.')[-1].strip(' "\'')
    if track['codec'] == 'stpp':
        profile = _CODE_PROFILES.get(code, '')
    return {'codec': track['codec'], 'language': language or track['language'] or '', 'profile': profile,
            'segment_duration': lengths[-1] if lengths else 0.0}


def read_data(init, segments=(), language=None, track_id=None, shift=0.0):
    """(subtitles, settings) of the MP4 bytes `init` (a whole file, or the
    initialization segment) and the media segments' bytes after it. The
    text track in `language` if there is one, else the first (or the one
    of `track_id`). The settings: {'codec' ('wvtt' or 'stpp'), 'language',
    'profile' (the TTML profile its MIME type names, or ''),
    'segment_duration' (the longest fragment, or sample)}."""
    tracks, defaults = movie(init)
    try:
        track = _choose(tracks, language, track_id)
    except ValueError:
        if _cut_short(init):
            raise ValueError('This MP4 is cut short: its movie box ends before the file says it does.') from None
        raise
    fragments = defaults.get(track['id'], (0, 0))
    found, clock = fragment_samples(init, track['id'], fragments)
    samples = []
    for clock_at, duration, start, size in track['samples']:
        samples.append((clock_at, duration, init[start:start + size]))
    samples += found
    lengths = []
    for data in segments:
        more, clock = fragment_samples(data, track['id'], fragments, clock)
        samples += more
        lengths.append(sum(duration for _time, duration, _bytes in more))
    segments_found, document_language = subtitles(track, samples, shift)
    settings = _settings(track, samples, document_language)
    if lengths:
        settings['segment_duration'] = round(max(lengths) / float(track['timescale'] or 1), 3)
    elif found:
        # One file: its fragments are its segments.
        settings['segment_duration'] = _fragment_length(init, track)
    return segments_found, settings


def _cut_short(data):
    """Whether the last box in `data` says it is longer than what is left."""
    at = 0
    while at + 8 <= len(data):
        size = struct.unpack_from('>I', data, at)[0]
        if size == 1 and at + 16 <= len(data):
            size = struct.unpack_from('>Q', data, at + 8)[0]
        if size < 8:
            return False
        at += size
    return at > len(data)


def _fragment_length(data, track):
    """The longest fragment of the track in `data`, in seconds."""
    longest = 0
    for kind, start, end in boxes(data):
        if kind == b'moof':
            samples, _clock = fragment_samples(data[start - 8:end], track['id'])
            longest = max(longest, sum(duration for _time, duration, _bytes in samples))
    return round(longest / float(track['timescale'] or 1), 3)


def read(path, language=None):
    """(subtitles, settings) of the MP4 (.mp4, .cmft) or DASH manifest
    (.mpd) at `path`; see read_data. An MP4 with no movie in it is a media
    segment, read with the initialization segment beside it. Raises
    ValueError for one with no WebVTT or TTML track, or cut short."""
    try:
        return _read(path, language)
    except struct.error as error:
        raise ValueError(f'This MP4 is cut short or damaged: {error}') from error


def _read(path, language=None):
    if str(path).lower().endswith('.mpd'):
        return read_dash(path, language)
    data = hls.fetch(path)
    if data[4:8] not in (b'ftyp', b'styp', b'moov', b'moof', b'free', b'sidx', b'emsg', b'mdat'):
        raise ValueError('This is not an MP4 file.')
    if not _first(data, 0, len(data), b'moov'):
        init = _beside(path)
        if init is None:
            raise ValueError('This is a media segment without its initialization segment beside it.')
        return read_data(hls.fetch(init), [data], language)
    return read_data(data, (), language)


def _beside(path):
    """The initialization segment in the same folder as a media segment."""
    folder = os.path.dirname(os.path.abspath(path))
    for name in sorted(os.listdir(folder)):
        if 'init' in name.lower() and name.lower().endswith(('.mp4', '.m4s', '.cmfi', '.cmft', '.m4i', '.ismt')):
            return os.path.join(folder, name)
    return None


def read_hls(path, found):
    """(subtitles, settings) of an HLS media playlist at `path` whose segments
    are fragmented MP4 (hls.playlist's `found`): the initialization
    segment its EXT-X-MAP names, then each segment."""
    media_map = found['segments'][0]['map']
    byterange = None
    if media_map.get('BYTERANGE'):
        length, _at, offset = media_map['BYTERANGE'].partition('@')
        byterange = (int(length), int(offset or 0))
    init = hls.fetch(hls.resolve(path, media_map['URI']), byterange)
    segments = [hls.fetch(hls.resolve(path, entry['uri']), entry['byterange']) for entry in found['segments']]
    try:
        segments_found, settings = read_data(init, segments)
    except struct.error as error:
        raise ValueError(f'This MP4 is cut short or damaged: {error}') from error
    settings['segment_duration'] = found['target'] or settings['segment_duration']
    return segments_found, settings


# DASH

def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _child(element, name):
    return next((child for child in element if _local(child.tag) == name), None)


def _all(element, name):
    return [child for child in element if _local(child.tag) == name]


def _seconds(value):
    """Seconds of an xs:duration ('PT1H2M3.5S'), 0 when there is none."""
    match = _DURATION.match(str(value or '').strip())
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(part) if part else 0.0 for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _filled(template, representation, number=None, time=None):
    def value(match):
        name, width = match.group(1), match.group(2)
        if name == '':
            return '$'
        if name == 'RepresentationID':
            return representation.get('id', '')
        if name == 'Bandwidth':
            number_value = int(representation.get('bandwidth') or 0)
        else:
            number_value = number if name == 'Number' else time
        return f'{int(number_value):0{int(width)}d}' if width else str(int(number_value))
    return _TEMPLATE.sub(value, template)


def _based(base, elements):
    """Where a manifest at `base` puts its files, its BaseURLs (the MPD's,
    the Period's ...) followed down to `elements`."""
    for element in elements:
        url = _child(element, 'BaseURL')
        if url is not None and (url.text or '').strip():
            base = hls.resolve(base, url.text.strip())
    return base


def _segment_addresses(template, representation, presentation_duration, base):
    """[(address, time)], the initialization's first, of a SegmentTemplate."""
    timescale = float(template.get('timescale') or 1)
    number = int(template.get('startNumber') or 1)
    addresses = []
    if template.get('initialization'):
        addresses.append((hls.resolve(base, _filled(template.get('initialization'), representation)), None))
    media = template.get('media') or ''
    timeline = _child(template, 'SegmentTimeline')
    if timeline is not None:
        time = 0
        for entry in _all(timeline, 'S'):
            time = int(entry.get('t', time))
            duration = int(entry.get('d') or 0)
            repeat = int(entry.get('r') or 0)
            if repeat < 0:
                repeat = max(0, math.ceil((presentation_duration * timescale - time) / duration) - 1) if duration else 0
            for _index in range(repeat + 1):
                addresses.append((hls.resolve(base, _filled(media, representation, number, time)), time))
                number += 1
                time += duration
    elif template.get('duration'):
        duration = float(template.get('duration'))
        count = math.ceil(round(presentation_duration * timescale / duration, 6)) if presentation_duration else None
        index = 0
        while count is None or index < count:
            address = hls.resolve(base, _filled(media, representation, number + index, int(index * duration)))
            if count is None and not hls._is_url(address) and not os.path.isfile(address):
                break
            addresses.append((address, int(index * duration)))
            index += 1
            if index > 100000:
                break
    return addresses


def read_dash(path, language=None):
    """(subtitles, settings) of the subtitles a DASH manifest at `path` names:
    the text adaptation set in `language`, else the first, and its first
    representation."""
    try:
        root = ET.fromstring(hls.fetch(path))
    except ET.ParseError as error:
        raise ValueError(f'This is not a DASH manifest: {error}') from error
    if _local(root.tag) != 'MPD':
        raise ValueError('This is not a DASH manifest: it has no <MPD>.')
    presentation_duration = _seconds(root.get('mediaPresentationDuration'))
    chosen = []
    for period in _all(root, 'Period'):
        for adaptation in _all(period, 'AdaptationSet'):
            for representation in _all(adaptation, 'Representation'):
                mime = representation.get('mimeType') or adaptation.get('mimeType') or ''
                codecs = representation.get('codecs') or adaptation.get('codecs') or ''
                kind = adaptation.get('contentType') or ''
                if (mime in ('text/vtt', 'application/ttml+xml') or codecs.startswith(('wvtt', 'stpp'))
                        or (kind == 'text' and mime == 'application/mp4')):
                    chosen.append((period, adaptation, representation, mime,
                                   representation.get('lang') or adaptation.get('lang') or ''))
    if not chosen:
        raise ValueError('This DASH manifest has no subtitles: no text adaptation set.')
    pick = chosen[0]
    if language:
        primary = str(language).lower().split('-')[0]
        pick = next((entry for entry in chosen if entry[4].lower().split('-')[0] == primary), pick)
    period, adaptation, representation, mime, lang = pick
    period_start = _seconds(period.get('start'))
    duration = _seconds(period.get('duration')) or presentation_duration
    base = _based(path, (root, period, adaptation, representation))
    template = _child(representation, 'SegmentTemplate')
    outer = _child(adaptation, 'SegmentTemplate')
    if outer is not None:
        merged = ET.Element('SegmentTemplate', dict(outer.attrib, **(template.attrib if template is not None else {})))
        for child in (template if template is not None and len(template) else outer):
            merged.append(child)
        template = merged
    offset = 0.0
    if template is not None:
        offset = float(template.get('presentationTimeOffset') or 0) / float(template.get('timescale') or 1)
        addresses = _segment_addresses(template, representation, duration, base)
    elif _child(representation, 'SegmentList') is not None or _child(adaptation, 'SegmentList') is not None:
        listed = _child(representation, 'SegmentList')
        listed = listed if listed is not None else _child(adaptation, 'SegmentList')
        initialization = _child(listed, 'Initialization')
        addresses = [(hls.resolve(base, initialization.get('sourceURL')), None)] if initialization is not None else []
        addresses += [(hls.resolve(base, entry.get('media')), 0) for entry in _all(listed, 'SegmentURL')]
    else:
        addresses = [(base, None)]
    if mime in ('text/vtt', 'application/ttml+xml'):
        # A WebVTT or TTML file by itself.
        text = decode(hls.fetch(addresses[0][0]))
        if mime == 'text/vtt':
            segments, found_language = webvtt.parse(text), ''
        else:
            segments, found_language, _metadata = ttml.read_text(text)
        for segment in segments:
            segment['start'] = round(segment['start'] + period_start, 3)
            segment['end'] = round(segment['end'] + period_start, 3)
        return segments, {'codec': 'wvtt' if mime == 'text/vtt' else 'stpp', 'language': lang or found_language,
                          'profile': '', 'segment_duration': 0.0}
    if not addresses:
        raise ValueError('This DASH manifest names no segments for its subtitles.')
    # The initialization segment, or a first segment with the movie in it.
    init = hls.fetch(addresses[0][0])
    media = [hls.fetch(address) for address, _time in addresses[1:]]
    segments, settings = read_data(init, media, lang or language, shift=period_start - offset)
    settings['language'] = lang or settings['language']
    if template is not None and template.get('duration'):
        settings['segment_duration'] = round(float(template.get('duration')) / float(template.get('timescale') or 1), 3)
    return segments, settings


# Writing boxes

def _box(kind, *parts):
    payload = b''.join(parts)
    return struct.pack('>I4s', 8 + len(payload), kind) + payload


def _full(kind, version, flags, *parts):
    return _box(kind, struct.pack('>I', (version << 24) | flags), *parts)


_MATRIX = struct.pack('>9I', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def _packed_language(language):
    code = usf.iso639_2(language) if language else 'und'
    code = _TERMINOLOGIC.get(code, code)
    if not re.fullmatch(r'[a-z]{3}', code):
        code = 'und'
    return ((ord(code[0]) - 0x60) << 10) | ((ord(code[1]) - 0x60) << 5) | (ord(code[2]) - 0x60)


def _sample_entry(codec, config='WEBVTT', profile='imsc1.1'):
    reserved = b'\0' * 6 + struct.pack('>H', 1)
    if codec == 'wvtt':
        return _box(b'wvtt', reserved, _box(b'vttC', config.encode('utf-8')))
    code = _PROFILE_CODES.get(profile)
    mime = 'application/ttml+xml' + (f';codecs={code}' if code else '')
    return _box(b'stpp', reserved, _TTML_NS.encode() + b'\0', b'\0', b'\0',
                _full(b'mime', 0, 0, mime.encode() + b'\0'))


def _init(codec, config, profile, language):
    """The initialization: ftyp and a movie with one text track."""
    brands = [b'iso6', b'cmfc', _CMAF_BRANDS.get('wvtt' if codec == 'wvtt' else profile, b'cmfc'), b'dash']
    ftyp = _box(b'ftyp', b'cmfc', struct.pack('>I', 0), *dict.fromkeys(brands))
    mvhd = _full(b'mvhd', 0, 0, struct.pack('>IIII', 0, 0, _TIMESCALE, 0), struct.pack('>IH', 0x10000, 0x100),
                 b'\0' * 10, _MATRIX, b'\0' * 24, struct.pack('>I', 2))
    tkhd = _full(b'tkhd', 0, 3, struct.pack('>IIIII', 0, 0, 1, 0, 0), b'\0' * 8, struct.pack('>hhhH', 0, 0, 0, 0),
                 _MATRIX, struct.pack('>II', 0, 0))
    mdhd = _full(b'mdhd', 0, 0, struct.pack('>IIIIHH', 0, 0, _TIMESCALE, 0, _packed_language(language), 0))
    handler, name, header = (b'text', b'WebVTT', _full(b'nmhd', 0, 0)) if codec == 'wvtt' else \
        (b'subt', b'TTML', _full(b'sthd', 0, 0))
    hdlr = _full(b'hdlr', 0, 0, struct.pack('>I', 0), handler, b'\0' * 12, name + b'\0')
    elng = _full(b'elng', 0, 0, export_options.language_tag(language).encode() + b'\0') if language else b''
    dinf = _box(b'dinf', _full(b'dref', 0, 0, struct.pack('>I', 1), _full(b'url ', 0, 1)))
    stbl = _box(b'stbl', _full(b'stsd', 0, 0, struct.pack('>I', 1), _sample_entry(codec, config, profile)),
                _full(b'stts', 0, 0, struct.pack('>I', 0)), _full(b'stsc', 0, 0, struct.pack('>I', 0)),
                _full(b'stsz', 0, 0, struct.pack('>II', 0, 0)), _full(b'stco', 0, 0, struct.pack('>I', 0)))
    trak = _box(b'trak', tkhd, _box(b'mdia', mdhd, hdlr, elng, _box(b'minf', header, dinf, stbl)))
    mvex = _box(b'mvex', _full(b'trex', 0, 0, struct.pack('>IIIII', 1, 1, 0, 0, 0)))
    return ftyp + _box(b'moov', mvhd, trak, mvex)


def _fragment(sequence, decode_time, samples):
    """A moof and mdat of `samples` [(duration, bytes)] from `decode_time`."""
    entries = b''.join(struct.pack('>II', duration, len(data)) for duration, data in samples)

    def moof(offset):
        trun = _full(b'trun', 0, 0x000301, struct.pack('>Ii', len(samples), offset), entries)
        traf = _box(b'traf', _full(b'tfhd', 0, 0x020000, struct.pack('>I', 1)),
                    _full(b'tfdt', 1, 0, struct.pack('>Q', decode_time)), trun)
        return _box(b'moof', _full(b'mfhd', 0, 0, struct.pack('>I', sequence)), traf)

    head = moof(0)
    head = moof(len(head) + 8)
    return head + _box(b'mdat', *(data for _duration, data in samples))


def _vtt_parts(segments, voices):
    """(header, [(start ms, end ms, settings, payload)]) of `segments` as
    modules.webvtt writes them."""
    text = webvtt.write(segments, voices)
    blocks = text.split('\n\n')
    header = []
    cues = []
    timing = re.compile(r'^(\d+):(\d{2}):(\d{2})\.(\d{3}) --> (\d+):(\d{2}):(\d{2})\.(\d{3})(?: (.*))?$')
    for block in blocks:
        lines = block.strip('\n').split('\n')
        match = timing.match(lines[0]) if lines else None
        if not match:
            if block.strip():
                header.append(block.strip('\n'))
            continue
        values = [int(value) for value in match.groups()[:8]]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        cues.append((start, end, match.group(9) or '', '\n'.join(lines[1:])))
    return '\n\n'.join(header), cues


def _vtt_sample(cues):
    if not cues:
        return _box(b'vtte')
    return b''.join(_box(b'vttc', *((_box(b'sttg', settings.encode()),) if settings else ()),
                         _box(b'payl', payload.encode('utf-8'))) for _start, _end, settings, payload in cues)


def write(segments, codec='wvtt', duration=SEGMENT_DURATION, language='en', voices=False, profile='imsc1.1',
          look='clean', preview=None, metadata=None, total=0.0):
    """(initialization, [fragments]) of a CMAF text track of `segments`:
    WebVTT ('wvtt') or TTML ('stpp', in `profile`, its look `look` and
    `preview`, its speakers as agents with `voices`), a fragment each
    `duration` seconds for as long as the subtitles or `total` last."""
    codec = codec if codec in CODECS else 'wvtt'
    profile = profile if profile in ttml.PROFILES else 'imsc1.1'
    step = int(round(max(1.0, float(duration or SEGMENT_DURATION)) * _TIMESCALE))
    segments = [segment for segment in segments if str(segment.get('text') or '').strip()]
    last = max([float(segment.get('end', 0)) for segment in segments] + [float(total or 0), 0.001])
    span = max(1, int(round(last * _TIMESCALE)))
    edges = list(range(0, span, step)) + [span]
    fragments = []
    if codec == 'wvtt':
        config, cues = _vtt_parts(segments, voices)
        cues = [cue for cue in cues if cue[1] > cue[0]]
        cuts = sorted(set(edges) | {cue[0] for cue in cues if cue[0] < span} | {cue[1] for cue in cues if cue[1] < span})
        for number, (start, end) in enumerate(zip(edges, edges[1:]), 1):
            times = [cut for cut in cuts if start <= cut <= end]
            samples = [(stop - begin, _vtt_sample([cue for cue in cues if cue[0] <= begin and cue[1] >= stop]))
                       for begin, stop in zip(times, times[1:]) if stop > begin]
            fragments.append(_fragment(number, start, samples))
    else:
        config = ''
        for number, (start, end) in enumerate(zip(edges, edges[1:]), 1):
            low, high = start / _TIMESCALE, end / _TIMESCALE
            shown = [dict(segment, start=max(low, float(segment.get('start', 0))),
                          end=min(high, float(segment.get('end', 0))))
                     for segment in segments
                     if float(segment.get('start', 0)) < high and float(segment.get('end', 0)) > low]
            document = ttml.write(shown, language=language, agents=voices, look=look, preview=preview,
                                  profile=profile, metadata=metadata)
            fragments.append(_fragment(number, start, [(end - start, document.encode('utf-8'))]))
    return _init(codec, config or 'WEBVTT', profile, language), fragments


def save(path, segments, **options):
    """Write `segments` to `path` as a CMAF text track file (.cmft, or
    .mp4): the initialization and every fragment; see write."""
    init, fragments = write(segments, **options)
    with open(path, 'wb') as mp4_file:
        mp4_file.write(init + b''.join(fragments))


def codecs_string(codec, profile='imsc1.1'):
    """The track's codecs as DASH and HLS name it: 'wvtt', 'stpp.ttml.im2t'."""
    if codec == 'wvtt':
        return 'wvtt'
    code = _PROFILE_CODES.get(profile)
    return f'stpp.ttml.{code}' if code else 'stpp'


def _xs_duration(seconds):
    return f'PT{seconds:.3f}S'


def save_dash(path, segments, codec='wvtt', duration=SEGMENT_DURATION, language='en', total=0.0, **options):
    """Write `segments` as the DASH manifest at `path` and its segments
    (init.mp4, then 00001.m4s ...) in a folder beside it; the segments a
    save before left there go."""
    init, fragments = write(segments, codec=codec, duration=duration, language=language, total=total, **options)
    profile = options.get('profile') if options.get('profile') in ttml.PROFILES else 'imsc1.1'
    folder = hls.folder_for(path)
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if re.fullmatch(r'\d{5}\.m4s|init\.mp4', name):
            os.remove(os.path.join(folder, name))
    with open(os.path.join(folder, 'init.mp4'), 'wb') as init_file:
        init_file.write(init)
    styp = _box(b'styp', b'msdh', struct.pack('>I', 0), b'msdh', b'msix')
    for number, fragment in enumerate(fragments, 1):
        with open(os.path.join(folder, f'{number:05d}.m4s'), 'wb') as segment_file:
            segment_file.write(styp + fragment)
    step = max(1.0, float(duration or SEGMENT_DURATION))
    length = max([float(segment.get('end', 0)) for segment in segments] + [float(total or 0), 0.001])
    bandwidth = max(1, int(max(len(fragment) for fragment in fragments) * 8 / step))
    where = urllib.parse.quote(os.path.basename(folder))
    profiles = 'urn:mpeg:dash:profile:isoff-live:2011'
    if codec == 'stpp' and profile == 'ebu-tt-d':
        profiles += ',urn:dvb:dash:profile:dvb-dash:2014'
    tag = export_options.language_tag(language)
    manifest = '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="{profiles}" type="static" '
        f'mediaPresentationDuration="{_xs_duration(length)}" minBufferTime="PT{step:g}S">',
        '  <Period id="1" start="PT0S">',
        f'    <AdaptationSet id="1" contentType="text" mimeType="application/mp4" lang="{tag}" segmentAlignment="true">',
        '      <Role schemeIdUri="urn:mpeg:dash:role:2011" value="subtitle"/>',
        f'      <SegmentTemplate timescale="{_TIMESCALE}" duration="{int(round(step * _TIMESCALE))}" startNumber="1" '
        f'initialization="{where}/init.mp4" media="{where}/$Number%05d$.m4s"/>',
        f'      <Representation id="{codec}" codecs="{codecs_string(codec, profile)}" bandwidth="{bandwidth}"/>',
        '    </AdaptationSet>',
        '  </Period>',
        '</MPD>',
        ''])
    with open(path, 'w', encoding='utf-8', newline='\n') as mpd_file:
        mpd_file.write(manifest)
