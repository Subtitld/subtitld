"""The subtitle tracks a video carries (Matroska's SRT and ASS, MP4's
mov_text, WebM's WebVTT ...): which of them are text, and one of them
written out as a subtitle file Subtitld reads.

`tracks` lists them from the ffprobe JSON Subtitld already reads when it
opens a video (file_io.process_video_file), so listing them runs nothing.
Each has its stream index, codec, language, title, whether it is the
default or a forced one, and its kind:

- 'text': subtitles FFmpeg decodes as text, which Subtitld can read, and
  a Blu-ray's TextST, which Subtitld decodes itself (textst);
- 'pictures': subtitles drawn as pictures (Blu-ray's PGS, DVD's VobSub,
  DVB's, DivX's XSUB), which only OCR would make text. A DVB subtitle
  stream may carry a service for each language (a composition page each),
  so `with_dvb_pages` makes a track of each, from the stream's subtitling
  descriptor in the file's PMT. SCTE 27's bitmaps (stream_type 0x82),
  which FFmpeg lists as a stream it does not know, are pictures too
  (codec 'scte_27'), which the scte27 module reads;
- 'text' too: Japan's and Brazil's ISDB captions (ARIB STD-B24,
  FFmpeg's arib_caption), which FFmpeg here has no decoder for: the
  aribb24 module reads them (`read_arib`);
- 'text' too: Matroska's USF tracks (S_TEXT/USF), a codec FFmpeg does not
  know, told by their CodecPrivate (the USF file's header, its
  <USFSubtitles> with its metadata and styles), which the probe gives
  when asked for its data (`with_extradata`); `extract` puts the USF
  file together again as mkvextract does (matroska.org's codec mapping:
  the header in the CodecPrivate, each block a <subtitle>'s contents,
  timed by the block) and the usf module reads it;
- 'captions': closed captions in a track of their own (QuickTime's
  CEA-608, and its c708 track of CEA-708 and CEA-608, which FFmpeg does
  not know: codec 'eia_708'), which the closed_captions module reads;
- 'teletext': a DVB teletext stream, whose subtitle pages the teletext
  module reads. One stream carries a page for each language (888, 777
  ...), so `with_teletext_pages` makes a track of each, from the stream's
  descriptor in the file's PMT.

Tracks of other codecs (TTML ...) are left out.

`extract` writes a text track to a file of its own: ASS and SSA tracks
as .ass, their styles kept; WebVTT as .vtt, cue settings kept; the
others as .srt. A track already in its file's format is copied as it is,
the others converted by FFmpeg (a timed text track without the pixel
sizes FFmpeg wraps its runs in). Times count from the video's start, as
FFmpeg counts them by default, the player shows them and the
closed_captions module reads them.
"""

import os
import re
import subprocess
import tempfile
from subtitld.modules.segment import text_of

TEXT_CODECS = frozenset({'subrip', 'srt', 'ass', 'ssa', 'webvtt', 'mov_text', 'text', 'microdvd', 'subviewer',
                         'subviewer1', 'mpl2', 'pjs', 'realtext', 'sami', 'stl', 'jacosub', 'vplayer',
                         'hdmv_text_subtitle'})
# Text tracks FFmpeg has no decoder for, which Subtitld decodes itself:
# a Blu-ray's TextST (textst).
TEXTST_CODECS = frozenset({'hdmv_text_subtitle'})
PICTURE_CODECS = frozenset({'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle', 'xsub', 'scte_27'})
CAPTION_CODECS = frozenset({'eia_608', 'eia_708'})
TELETEXT_CODECS = frozenset({'dvb_teletext'})
# Text Subtitld decodes itself rather than FFmpeg (modules.aribb24).
ARIB_CODECS = frozenset({'arib_caption'})
# Matroska's USF (S_TEXT/USF), which FFmpeg lists as a codec it does not
# know: Subtitld's name for it, told by its CodecPrivate.
USF_CODECS = frozenset({'usf'})


def _is_usf(stream):
    """Whether a subtitle stream FFmpeg does not know is a USF track: its
    extradata (the CodecPrivate, given with -show_data) a USF header."""
    if str(stream.get('codec_name') or '') not in ('', 'unknown') or not stream.get('extradata'):
        return False
    header = _hexdump(stream.get('extradata'))[:4096]
    return b'<USFSubtitles' in header


def with_extradata(metadata, path, ffprobe='ffprobe', startupinfo=None):
    """`metadata` (ffprobe's -show_streams JSON) with the extradata of each
    subtitle stream FFmpeg does not know, which carries what it is (a
    Matroska USF track's header): read again with -show_data for those
    alone, when there are any."""
    unknown = [stream for stream in metadata.get('streams', []) if stream.get('codec_type') == 'subtitle'
               and str(stream.get('codec_name') or '') in ('', 'unknown') and int(stream.get('extradata_size') or 0)
               and 'extradata' not in stream]
    if not unknown or not path:
        return metadata
    import json
    try:
        result = subprocess.run([str(ffprobe), '-v', 'error', '-select_streams', 's', '-show_streams', '-show_data',
                                 '-show_entries', 'stream=index,extradata', '-of', 'json', str(path)],
                                capture_output=True, startupinfo=startupinfo, check=False)
        probed = json.loads(result.stdout.decode('utf-8', 'replace') or '{}')
    except (OSError, ValueError):
        return metadata
    extradata = {stream.get('index'): stream.get('extradata') for stream in probed.get('streams', [])}
    for stream in unknown:
        if extradata.get(stream.get('index')):
            stream['extradata'] = extradata[stream.get('index')]
    return metadata


def tracks(metadata, language_of=None):
    """The subtitle tracks in a video's ffprobe JSON (-show_streams):
    [{'index', 'codec', 'kind', 'language', 'language_tag', 'title',
    'default', 'forced'}], in the file's order. 'language_tag' is the
    language the track names ('eng'; '' for none or 'und'), 'language'
    Subtitld's code for it, `language_of(tag)`, or ''."""
    found = []
    for stream in metadata.get('streams', []):
        # SCTE 27's stream type, which FFmpeg does not know.
        scte27 = stream.get('codec_type') in (None, 'unknown', 'data') and str(stream.get('codec_tag')) == '0x0082'
        if stream.get('codec_type') != 'subtitle' and not scte27:
            continue
        codec = 'scte_27' if scte27 else str(stream.get('codec_name') or '')
        if codec in ('', 'unknown') and str(stream.get('codec_tag_string')) == 'c708':
            codec = 'eia_708'
        if codec in ('', 'unknown') and _is_usf(stream):
            codec = 'usf'
        kind = 'text' if codec in TEXT_CODECS | ARIB_CODECS | USF_CODECS else 'pictures' if codec in PICTURE_CODECS else \
            'captions' if codec in CAPTION_CODECS else 'teletext' if codec in TELETEXT_CODECS else None
        if kind is None:
            continue
        tags = {str(key).lower(): value for key, value in (stream.get('tags') or {}).items()}
        tag = str(tags.get('language') or '').strip()
        tag = '' if tag.lower() == 'und' else tag
        disposition = stream.get('disposition') or {}
        if kind == 'teletext' or codec == 'dvb_subtitle':
            # A stream of several pages names all their languages ("eng,ger").
            tag = tag.split(',')[0].strip()
        divx = re.fullmatch(r'Subtitle - ([a-z]{2,3})-xx;\d+', str(tags.get('title') or '').strip())
        if codec == 'xsub' and not tag and divx:
            # An AVI keeps an XSUB stream's language in its name, as DivX
            # writes it and FFmpeg's AVI muxer does: "Subtitle - pt-xx;02".
            tag = divx.group(1)
        found.append({
            'index': int(stream.get('index', 0)),
            'codec': codec,
            'kind': kind,
            'language': (language_of(tag) if language_of and tag else '') or '',
            'language_tag': tag,
            'title': str(tags.get('title') or '').strip(),
            'default': bool(disposition.get('default')),
            'forced': bool(disposition.get('forced')),
        })
        if kind == 'teletext' or codec in ('dvb_subtitle', 'arib_caption', 'scte_27'):
            # Its PID, in a transport stream ("0x102"), to find it there.
            try:
                found[-1]['pid'] = int(str(stream.get('id') or ''), 0)
            except ValueError:
                pass
    return found


def with_teletext_pages(found, path, language_of=None):
    """`found` (as `tracks` lists them) with each teletext stream of the
    transport stream at `path` as a track for each subtitle page its
    descriptor lists: 'page' ('888'), its own 'language_tag' and
    'language', and 'hearing_impaired' for a page for the hard of hearing.
    A stream that lists none, or one in another container, stays one track,
    whose first subtitle page is read ('page' None)."""
    if not any(track['kind'] == 'teletext' for track in found):
        return found
    from subtitld.modules import teletext
    try:
        listed = teletext.streams(path) if teletext.is_transport_stream(path) else {}
    except OSError:
        listed = {}
    out = []
    for track in found:
        if track['kind'] != 'teletext':
            out.append(track)
            continue
        pages = [page for page in listed.get(track.get('pid'), []) if page['type'] in teletext.SUBTITLE_TYPES]
        if not pages:
            out.append(dict(track, page=None, hearing_impaired=False))
            continue
        for page in pages:
            tag = '' if page['language'].lower() in ('', 'und') else page['language']
            out.append(dict(track, page=page['page'], language_tag=tag,
                            language=(language_of(tag) if language_of and tag else '') or '',
                            hearing_impaired=page['type'] == 0x05))
    return out


def with_dvb_pages(found, path, language_of=None):
    """`found` (as `tracks` lists them) with what the subtitling descriptor
    of each DVB subtitle stream of the transport stream at `path` says of
    it: 'hearing_impaired' for subtitles for the hard of hearing, and, for
    a stream that carries several services, a track for each, its 'page'
    (the composition page, '2') and its own 'language_tag' and 'language'.
    A stream that lists one service, or one in another container, stays one
    track ('page' None), which reads the first."""
    if not any(track['codec'] == 'dvb_subtitle' for track in found):
        return found
    from subtitld.modules import dvbsub, teletext
    try:
        listed = dvbsub.streams(path) if teletext.is_transport_stream(path) else {}
    except OSError:
        listed = {}
    out = []
    for track in found:
        if track['codec'] != 'dvb_subtitle':
            out.append(track)
            continue
        services = [service for service in listed.get(track.get('pid'), [])
                    if service['type'] in dvbsub.SUBTITLE_TYPES]
        if len(services) < 2:
            hearing = bool(services) and services[0]['type'] in dvbsub.HARD_OF_HEARING_TYPES
            out.append(dict(track, page=None, hearing_impaired=hearing))
            continue
        for service in services:
            tag = '' if service['language'].lower() in ('', 'und') else service['language']
            out.append(dict(track, page=str(service['page']), language_tag=tag,
                            language=(language_of(tag) if language_of and tag else '') or '',
                            hearing_impaired=service['type'] in dvbsub.HARD_OF_HEARING_TYPES))
    return out


def key(track):
    """What tells a track from the others: its stream index, and its page
    for one of a teletext stream's ('2/888') or a DVB subtitle stream's
    ('3/2')."""
    return f"{track['index']}/{track['page']}" if track.get('page') else track['index']


def read_teletext(path, index, page=None, pid=None, ffprobe='ffprobe', startupinfo=None, progress=None,
                  stopped=None, started=None):
    """The subtitles of teletext page `page` ('888'; the first subtitle
    page when None) of the teletext stream at `index` of the video at
    `path`, timed as FFmpeg counts from the video's start. A transport
    stream is read as it is (teletext.read), `progress(share)` hearing how
    far and `stopped()` asked whether to stop; another container's stream
    packet by packet through ffprobe, `started(process)` getting it."""
    from subtitld.modules import teletext
    if teletext.is_transport_stream(path):
        return teletext.read(path, page=page, pid=pid, progress=progress, stopped=stopped)[0]
    import json
    command = [str(ffprobe), '-v', 'error', '-select_streams', str(int(index)), '-show_packets', '-show_format',
               '-show_data', '-of', 'json', str(path)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               startupinfo=startupinfo)
    if started:
        started(process)
    out, errors = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'ffprobe could not read the track')
    probe = json.loads(out.decode('utf-8', 'replace') or '{}')
    try:
        offset = float((probe.get('format') or {}).get('start_time') or 0)
    except ValueError:
        offset = 0.0
    packets = []
    for packet in probe.get('packets') or []:
        try:
            when = float(packet.get('pts_time'))
        except (TypeError, ValueError):
            continue
        for field in teletext.units(_hexdump(packet.get('data'))):
            packets.append((round(when * 90000), field))
    if page is None:
        page = teletext.busiest_subtitle_page(packets)
    return teletext.decode(packets, page, origin=round(offset * 90000))


def read_arib(path, index, pid=None, ffprobe='ffprobe', startupinfo=None, progress=None, stopped=None,
              started=None):
    """The subtitles of the ARIB caption stream at `index` of the video at
    `path` (its first language), timed as FFmpeg counts from the video's
    start. A transport stream is read as it is (aribb24.read, the stream
    `pid`); another container's stream packet by packet through ffprobe."""
    from subtitld.modules import aribb24, teletext
    if teletext.is_transport_stream(path):
        return aribb24.read(path, pid=pid, progress=progress, stopped=stopped)[0]
    import json
    command = [str(ffprobe), '-v', 'error', '-select_streams', str(int(index)), '-show_packets', '-show_format',
               '-show_data', '-of', 'json', str(path)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               startupinfo=startupinfo)
    if started:
        started(process)
    out, errors = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'ffprobe could not read the track')
    probe = json.loads(out.decode('utf-8', 'replace') or '{}')
    try:
        offset = float((probe.get('format') or {}).get('start_time') or 0)
    except ValueError:
        offset = 0.0
    packets = []
    for packet in probe.get('packets') or []:
        try:
            packets.append((round(float(packet.get('pts_time')) * 90000), _hexdump(packet.get('data'))))
        except (TypeError, ValueError):
            continue
    return aribb24.decode(packets, origin=round(offset * 90000))[0]


def output_of(codec):
    """(extension, FFmpeg encoder) a text track of `codec` is written with;
    'copy' when the track is already in that file's format."""
    if codec in ('ass', 'ssa'):
        return '.ass', 'copy' if codec == 'ass' else 'ass'
    if codec == 'webvtt':
        return '.vtt', 'copy'
    return '.srt', 'copy' if codec in ('subrip', 'srt') else 'srt'


def extract(path, index, codec, ffmpeg='ffmpeg', folder=None, duration=0.0, startupinfo=None, progress=None,
            started=None, ffprobe='ffprobe'):
    """Write the text track at stream `index` of the video at `path`, of
    `codec`, to a new file in `folder` (the system's temporary folder by
    default) and return its path; the caller removes it. `progress(share)`
    hears how far FFmpeg has got through the `duration` seconds, from 0 to
    1; `started(process)` gets the FFmpeg process, to stop it. Raises
    RuntimeError, the file removed, when FFmpeg fails or is stopped. A
    TextST track is read through `ffprobe` packet by packet and written
    as SRT."""
    if codec in TEXTST_CODECS:
        return _extract_textst(path, index, ffprobe, folder, startupinfo, started)
    if codec in USF_CODECS:
        return _extract_usf(path, index, ffprobe, folder, startupinfo, started)
    extension, encoder = output_of(codec)
    handle, target = tempfile.mkstemp(prefix='subtitld_track_', suffix=extension, dir=folder)
    os.close(handle)
    # -nostdin and -y: never a question on a terminal, and the file just
    # made is FFmpeg's to write.
    command = [str(ffmpeg), '-nostdin', '-y', '-v', 'error', '-i', str(path), '-map', f'0:{int(index)}',
               '-c:s', encoder, '-progress', 'pipe:1', '-nostats', target]
    errors = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                   startupinfo=startupinfo)
        if started:
            started(process)
        for line in process.stdout:
            key, _equals, value = line.decode('ascii', 'replace').strip().partition('=')
            if key == 'out_time_us' and progress and duration:
                try:
                    progress(min(1.0, max(0.0, int(value) / 1e6 / duration)))
                except ValueError:                   # N/A before the first packet
                    pass
        if process.wait() != 0:
            errors.seek(0)
            raise RuntimeError(errors.read().decode('utf-8', 'replace').strip()
                               or 'FFmpeg could not read the subtitle track')
        if codec == 'mov_text':
            _without_sizes(target)
    except BaseException:
        os.remove(target)
        raise
    finally:
        errors.close()
    return target


def _extract_textst(path, index, ffprobe, folder, startupinfo, started):
    """A TextST track's subtitles (textst.decode), as an SRT in `folder`."""
    import json
    from subtitld.modules import srt, textst
    command = [str(ffprobe), '-v', 'error', '-select_streams', str(int(index)), '-show_packets', '-show_format',
               '-show_data', '-of', 'json', str(path)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               startupinfo=startupinfo)
    if started:
        started(process)
    out, errors = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'ffprobe could not read the track')
    probe = json.loads(out.decode('utf-8', 'replace') or '{}')
    try:
        offset = float((probe.get('format') or {}).get('start_time') or 0)
    except ValueError:
        offset = 0.0
    segments = textst.decode([_hexdump(packet.get('data')) for packet in probe.get('packets') or []],
                             origin=round(offset * 90000))
    handle, target = tempfile.mkstemp(prefix='subtitld_track_', suffix='.srt', dir=folder)
    os.close(handle)
    srt.save(target, segments)
    return target


def usf_document(header, blocks, language=''):
    """The USF file of a Matroska USF track, as mkvextract puts it
    together: its CodecPrivate `header` (the file's start, its closing
    </USFSubtitles> taken out), a <subtitles> block in the track's
    `language` (ISO 639-2), a <subtitle> for each of `blocks`, [(start,
    end, the block's contents)] in seconds, and the closing tag."""
    from subtitld.modules import usf
    header = header.decode('utf-8', 'replace') if isinstance(header, bytes) else str(header)
    header = header.replace('</USFSubtitles>', '', 1).rstrip()
    out = [header, '<subtitles>']
    if language:
        out.append(f'<language code="{language}"/>')
    for start, end, contents in blocks:
        contents = contents.decode('utf-8', 'replace') if isinstance(contents, bytes) else str(contents)
        out.append(f'<subtitle start="{usf.format_time(start)}" stop="{usf.format_time(end)}">'
                   f'{contents.strip()}</subtitle>')
    out += ['</subtitles>', '</USFSubtitles>', '']
    return '\n'.join(out)


def _extract_usf(path, index, ffprobe, folder, startupinfo, started):
    """A Matroska USF track (`usf_document`) as a .usf file in `folder`:
    its header and blocks as ffprobe hands them over, each block at its
    own time and for its duration (Matroska's, from the segment's start,
    as FFmpeg keeps a subtitle track's times)."""
    import json
    command = [str(ffprobe), '-v', 'error', '-select_streams', str(int(index)), '-show_streams', '-show_packets',
               '-show_data', '-of', 'json', str(path)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               startupinfo=startupinfo)
    if started:
        started(process)
    out, errors = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'ffprobe could not read the track')
    probe = json.loads(out.decode('utf-8', 'replace') or '{}')
    stream = next(iter(probe.get('streams') or []), {})
    tag = str({str(key).lower(): value for key, value in (stream.get('tags') or {}).items()}.get('language') or '')
    blocks = []
    for packet in probe.get('packets') or []:
        try:
            start = float(packet.get('pts_time'))
            end = start + float(packet.get('duration_time') or 0)
        except (TypeError, ValueError):
            continue
        blocks.append((start, end, _hexdump(packet.get('data'))))
    document = usf_document(_hexdump(stream.get('extradata')), blocks, '' if tag.lower() == 'und' else tag)
    handle, target = tempfile.mkstemp(prefix='subtitld_track_', suffix='.usf', dir=folder)
    with os.fdopen(handle, 'w', encoding='utf-8') as file:
        file.write(document)
    return target


_SIZED = re.compile(r'<font\b[^>]*\bsize=', re.IGNORECASE)
# The track's own font, which every cue starts with: <font face="Serif" size="18">.
_TRACK_FONT = re.compile(r'<font\s+face="([^"]*)"\s+size=', re.IGNORECASE)


def _without_sizes(path):
    """The SRT FFmpeg wrote of a timed text track without the sizes it
    wraps each run in: a style record says a size in pixels, which FFmpeg
    writes as an HTML <font size="13"> at every change of style. Such a
    size says nothing about a proportion, so Subtitld would only carry it
    along unread. A track whose sample description names a font gets that
    font and its size around every cue (<font face="Serif" size="18">),
    which is the track's look rather than a cue's, as FFmpeg's own ASS
    puts it in its Default style: when every cue starts with the same, it
    goes."""
    from subtitld.modules import markup, srt
    with open(path, encoding='utf-8', errors='replace') as track:
        if not _SIZED.search(track.read()):
            return
    segments = srt.read(path)
    parts = [markup.split_lead(text_of(segment)) for segment in segments]
    faces = {match.group(1) if match else None for match in (_TRACK_FONT.match(rest) for _lead, rest in parts)}
    track_font = faces.pop() if len(faces) == 1 else None
    for segment, (lead, rest) in zip(segments, parts):
        runs = [(text, style._replace(face='') if track_font and style.face == track_font else style)
                for text, style in markup.spans(rest)]
        segment['text'] = lead + markup.from_spans(runs)
    srt.save(path, segments)


# Picture tracks Subtitld can read through an OCR add-on (picture_ocr).
READABLE_PICTURE_CODECS = frozenset({'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle', 'xsub', 'scte_27'})


def _hexdump(text):
    """The bytes of an ffprobe -show_data dump ("00000000: 0000 01ba ...  ....")."""
    out = bytearray()
    for line in (text or '').splitlines():
        _offset, colon, rest = line.partition(': ')
        if colon:
            out += bytes.fromhex(rest.split('  ')[0].replace(' ', ''))
    return bytes(out)


def extract_pictures(path, index, codec, ffmpeg='ffmpeg', ffprobe='ffprobe', folder=None, startupinfo=None,
                     started=None, pid=None, page=None):
    """The pictures of the picture track at stream `index` (PGS, VobSub,
    DVB or XSUB) of the video at `path`, timed as FFmpeg counts from the
    video's start: [picture_subtitles.Picture]. A PGS track is written out
    by FFmpeg as a .sup; a VobSub track is read packet by packet through
    ffprobe, with the palette and frame size its extradata (an .idx header)
    gives; an XSUB track packet by packet too, each packet its own times
    and colours (xsub). A DVB track of a transport stream is read as it is (dvbsub.read:
    the stream `pid`, its composition page `page`); in another container,
    packet by packet through ffprobe, the pages its extradata names."""
    from subtitld.modules import dvbsub, pgs, teletext, vobsub
    if codec == 'dvb_subtitle' and teletext.is_transport_stream(path):
        return dvbsub.read(path, pid=pid, page=page)[0]
    if codec == 'scte_27':
        # Sections only a transport stream carries: read as it is.
        from subtitld.modules import scte27
        return scte27.read(path, pid=pid)[0]
    if codec == 'hdmv_pgs_subtitle':
        handle, target = tempfile.mkstemp(prefix='subtitld_track_', suffix='.sup', dir=folder)
        os.close(handle)
        command = [str(ffmpeg), '-nostdin', '-y', '-v', 'error', '-i', str(path), '-map', f'0:{int(index)}',
                   '-c:s', 'copy', '-f', 'sup', target]
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, startupinfo=startupinfo)
            if started:
                started(process)
            _out, errors = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'FFmpeg could not read the track')
            return pgs.read(target)
        finally:
            os.remove(target)
    if codec in ('dvd_subtitle', 'dvb_subtitle', 'xsub'):
        import json
        command = [str(ffprobe), '-v', 'error', '-select_streams', str(int(index)), '-show_streams',
                   '-show_packets', '-show_format', '-show_data', '-of', 'json', str(path)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   startupinfo=startupinfo)
        if started:
            started(process)
        out, errors = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(errors.decode('utf-8', 'replace').strip() or 'ffprobe could not read the track')
        probe = json.loads(out.decode('utf-8', 'replace') or '{}')
        stream = (probe.get('streams') or [{}])[0]
        if codec == 'xsub':
            from subtitld.modules import xsub
            try:
                offset = float((probe.get('format') or {}).get('start_time') or 0)
            except ValueError:
                offset = 0.0
            screen = (int(stream.get('width') or 0), int(stream.get('height') or 0))
            return xsub.pictures([_hexdump(packet.get('data')) for packet in probe.get('packets') or []],
                                 has_alpha=str(stream.get('codec_tag_string') or '').upper() == 'DXSA',
                                 screen=screen if all(screen) else xsub.DEFAULT_SIZE, offset=offset)
        if codec == 'dvb_subtitle':
            # The extradata: the composition and ancillary pages.
            extradata = _hexdump(stream.get('extradata'))
            composition = int(page) if page else (extradata[0] << 8 | extradata[1] if len(extradata) >= 4 else None)
            ancillary = extradata[2] << 8 | extradata[3] if len(extradata) >= 4 and not page else None
            packets = []
            for packet in probe.get('packets') or []:
                try:
                    when = float(packet.get('pts_time'))
                except (TypeError, ValueError):
                    continue
                packets.append((round(when * 90000), _hexdump(packet.get('data'))))
            try:
                offset = float((probe.get('format') or {}).get('start_time') or 0)
            except ValueError:
                offset = 0.0
            return dvbsub.decode(packets, composition, ancillary, origin=round(offset * 90000))
        info = vobsub.parse_idx(_hexdump(stream.get('extradata')).decode('utf-8', 'replace'))
        if stream.get('width') and stream.get('height') and info['size'] == (720, 576):
            info['size'] = (int(stream['width']), int(stream['height']))
        try:
            offset = float((probe.get('format') or {}).get('start_time') or 0)
        except ValueError:
            offset = 0.0
        units = []
        for packet in probe.get('packets') or []:
            try:
                when = float(packet.get('pts_time')) - offset
            except (TypeError, ValueError):
                continue
            units.append((0, round(when * 90000), _hexdump(packet.get('data'))))
        return vobsub.pictures(units, info)
    raise RuntimeError(f'{codec} tracks cannot be read')
