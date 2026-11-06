"""The subtitle tracks a video carries (Matroska's SRT and ASS, MP4's
mov_text, WebM's WebVTT ...): which of them are text, and one of them
written out as a subtitle file Subtitld reads.

`tracks` lists them from the ffprobe JSON Subtitld already reads when it
opens a video (file_io.process_video_file), so listing them runs nothing.
Each has its stream index, codec, language, title, whether it is the
default or a forced one, and its kind:

- 'text': subtitles FFmpeg decodes as text, which Subtitld can read;
- 'pictures': subtitles drawn as pictures (Blu-ray's PGS, DVD's VobSub,
  DVB's, DivX's XSUB), which only OCR would make text;
- 'captions': closed captions in a track of their own (QuickTime's
  CEA-608), which the closed_captions module reads.

Tracks of other codecs (TTML, teletext ...) are left out.

`extract` writes a text track to a file of its own: ASS and SSA tracks
as .ass, their styles kept; WebVTT as .vtt, cue settings kept; the
others as .srt. A track already in its file's format is copied as it is,
the others converted by FFmpeg. Times count from the video's start, as
FFmpeg counts them by default, the player shows them and the
closed_captions module reads them.
"""

import os
import subprocess
import tempfile

TEXT_CODECS = frozenset({'subrip', 'srt', 'ass', 'ssa', 'webvtt', 'mov_text', 'text', 'microdvd', 'subviewer',
                         'subviewer1', 'mpl2', 'pjs', 'realtext', 'sami', 'stl', 'jacosub', 'vplayer'})
PICTURE_CODECS = frozenset({'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle', 'xsub'})
CAPTION_CODECS = frozenset({'eia_608'})


def tracks(metadata, language_of=None):
    """The subtitle tracks in a video's ffprobe JSON (-show_streams):
    [{'index', 'codec', 'kind', 'language', 'language_tag', 'title',
    'default', 'forced'}], in the file's order. 'language_tag' is the
    language the track names ('eng'; '' for none or 'und'), 'language'
    Subtitld's code for it, `language_of(tag)`, or ''."""
    found = []
    for stream in metadata.get('streams', []):
        if stream.get('codec_type') != 'subtitle':
            continue
        codec = str(stream.get('codec_name') or '')
        kind = 'text' if codec in TEXT_CODECS else 'pictures' if codec in PICTURE_CODECS else \
            'captions' if codec in CAPTION_CODECS else None
        if kind is None:
            continue
        tags = {str(key).lower(): value for key, value in (stream.get('tags') or {}).items()}
        tag = str(tags.get('language') or '').strip()
        tag = '' if tag.lower() == 'und' else tag
        disposition = stream.get('disposition') or {}
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
    return found


def output_of(codec):
    """(extension, FFmpeg encoder) a text track of `codec` is written with;
    'copy' when the track is already in that file's format."""
    if codec in ('ass', 'ssa'):
        return '.ass', 'copy' if codec == 'ass' else 'ass'
    if codec == 'webvtt':
        return '.vtt', 'copy'
    return '.srt', 'copy' if codec in ('subrip', 'srt') else 'srt'


def extract(path, index, codec, ffmpeg='ffmpeg', folder=None, duration=0.0, startupinfo=None, progress=None,
            started=None):
    """Write the text track at stream `index` of the video at `path`, of
    `codec`, to a new file in `folder` (the system's temporary folder by
    default) and return its path; the caller removes it. `progress(share)`
    hears how far FFmpeg has got through the `duration` seconds, from 0 to
    1; `started(process)` gets the FFmpeg process, to stop it. Raises
    RuntimeError, the file removed, when FFmpeg fails or is stopped."""
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
    except BaseException:
        os.remove(target)
        raise
    finally:
        errors.close()
    return target


# Picture tracks Subtitld can read through an OCR add-on (picture_ocr).
READABLE_PICTURE_CODECS = frozenset({'hdmv_pgs_subtitle', 'dvd_subtitle'})


def _hexdump(text):
    """The bytes of an ffprobe -show_data dump ("00000000: 0000 01ba ...  ....")."""
    out = bytearray()
    for line in (text or '').splitlines():
        _offset, colon, rest = line.partition(': ')
        if colon:
            out += bytes.fromhex(rest.split('  ')[0].replace(' ', ''))
    return bytes(out)


def extract_pictures(path, index, codec, ffmpeg='ffmpeg', ffprobe='ffprobe', folder=None, startupinfo=None,
                     started=None):
    """The pictures of the picture track at stream `index` (PGS or VobSub)
    of the video at `path`, timed as FFmpeg counts from the video's start:
    [picture_subtitles.Picture]. A PGS track is written out by FFmpeg as a
    .sup; a VobSub track is read packet by packet through ffprobe, with the
    palette and frame size its extradata (an .idx header) gives."""
    from subtitld.modules import pgs, vobsub
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
    if codec == 'dvd_subtitle':
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
