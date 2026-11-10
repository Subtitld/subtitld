"""Subtitles written into a video as a track of their own, the "soft"
subtitles a player lists and turns on and off, rather than drawn into the
picture or kept in a file beside it.

Three kinds of file take them:

- **MP4 and QuickTime (.mp4, .m4v, .mov)**: 3GPP timed text (3GPP TS
  26.245), the `tx3g` track FFmpeg calls mov_text, which is what iTunes,
  QuickTime, Android and the browsers' MP4 players show. It carries the
  text with bold, italic, underline and colour over a run of it (its style
  records); a position, a background or an outline per subtitle it has
  no room for, so a track says them once, for all of it, in the style the
  subtitles are written in.
- **Matroska (.mkv)**: an SRT track, which every player reads, or an ASS
  one, which keeps a subtitle's position and the look the subtitles are
  drawn in.
- **MPEG transport stream (.ts)**: DVB teletext, a page for each language
  (888 and up), as broadcasters deliver subtitles; the teletext module
  writes it.

The picture and sound are copied as they are, so the export is quick and
loses nothing of them; the video's own subtitle tracks are left out, since
those it carries need not be ones the new file can hold. The subtitles go
in a track for each language: the project's, and when asked, one for each
translation it has. The first is the default track, and a forced one when
asked (shown even with subtitles turned off, as a film's foreign dialogue
is). Each names its language as ISO 639-2 has it ("por"), which is what
both containers write, and is titled with the language's name (in an MP4,
which has no track titles, as the name of its handler, which players show
instead).

FFmpeg writes the tracks from files Subtitld writes for them: ASS for
mov_text, since FFmpeg's mov_text encoder takes a run's formatting from
ASS override codes, and as SRT or ASS for Matroska, copied as they are.

`write_video` runs FFmpeg; `command` builds what it runs, which is what the
tests read.
"""

import os
import re
import subprocess
import tempfile

from subtitld.modules import ass
from subtitld.modules import srt
from subtitld.modules import usf

# What each kind of file is, and how its subtitles are written: as ASS for
# FFmpeg's mov_text encoder, or copied from an SRT or ASS file.
CONTAINERS = {
    'MP4': {'extension': 'mp4', 'codecs': ('mov_text',)},
    'MOV TEXT': {'extension': 'mov', 'codecs': ('mov_text',)},
    'MKV': {'extension': 'mkv', 'codecs': ('srt', 'ass')},
    'TS': {'extension': 'ts', 'codecs': ('teletext',)},
}
DEFAULT_CODEC = {'MP4': 'mov_text', 'MOV TEXT': 'mov_text', 'MKV': 'srt', 'TS': 'teletext'}

# FFmpeg's mov_text encoder changes the colour of a run for \1c alone; the
# \c Subtitld's ASS writes (ASS's own short form of it) it leaves out. A
# bare \c, back to the style's colour, is white: the clean look's.
_SHORT_COLOUR = re.compile(r'\\c(?=&H)')
_COLOUR_RESET = re.compile(r'\\1?c(?=[\\}])')


def is_soft(container):
    """Whether `container` is one of the kinds of file this module writes."""
    return str(container or '').upper() in CONTAINERS


def codec_for(container, codec=None):
    """The subtitle codec a track of `container` is written in: `codec` when
    the container takes it, else its default."""
    container = str(container or '').upper()
    holder = CONTAINERS.get(container, CONTAINERS['MP4'])
    return codec if codec in holder['codecs'] else DEFAULT_CODEC.get(container, 'mov_text')


def for_mov_text(text):
    """ASS text with its colour codes as FFmpeg's mov_text encoder reads
    them."""
    def block(match):
        codes = _SHORT_COLOUR.sub(r'\\1c', match.group(0))
        return _COLOUR_RESET.sub(r'\\1c&HFFFFFF&', codes)
    return re.sub(r'\{[^{}]*\}', block, text)


def track_file(path, segments, codec, width=None, height=None, look='clean', preview=None):
    """Write `segments` to `path` as the file a track of `codec` is made
    from: SRT for an SRT track, ASS for the others."""
    if codec == 'srt':
        srt.save(path, segments)
        return
    text = ass.write(segments, width, height, 'ass', look if codec == 'ass' else 'clean', preview)
    if codec == 'mov_text':
        text = for_mov_text(text)
    with open(path, 'w', encoding='utf-8', newline='\n') as track:
        track.write(text)


def command(video, output, tracks, container='MP4', codec=None, forced=False, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as `output` with a
    subtitle track for each of `tracks`: [{'path' (the file it is made
    from), 'language' (Subtitld's code, '' for none), 'title'}]."""
    container = str(container or 'MP4').upper()
    codec = codec_for(container, codec)
    out = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video)]
    for track in tracks:
        out += ['-i', str(track['path'])]
    # The picture and sound (an MP4's cover art is a picture stream too),
    # and a Matroska's fonts, which its ASS may use.
    out += ['-map', '0:v?', '-map', '0:a?']
    if container == 'MKV':
        out += ['-map', '0:t?']
    for number in range(len(tracks)):
        out += ['-map', f'{number + 1}:0']
    out += ['-c:v', 'copy', '-c:a', 'copy', '-c:t', 'copy', '-c:s', 'mov_text' if codec == 'mov_text' else 'copy']
    for number, track in enumerate(tracks):
        language = usf.iso639_2(track.get('language')) if track.get('language') else 'und'
        out += [f'-metadata:s:s:{number}', f'language={language}']
        if track.get('title'):
            # An MP4 track has no title of its own: players show the name
            # of its handler instead.
            key = 'title' if container == 'MKV' else 'handler_name'
            out += [f'-metadata:s:s:{number}', f'{key}={track["title"]}']
        disposition = ('default+forced' if forced else 'default') if number == 0 else '0'
        out += [f'-disposition:s:{number}', disposition]
    if container in ('MP4', 'MOV TEXT'):
        # The index at the front, so a player starts before it has it all.
        out += ['-movflags', '+faststart']
    return out + [str(output)]


def write_video(video, output, tracks, container='MP4', codec=None, forced=False, width=None, height=None,
                look='clean', preview=None, ffmpeg='ffmpeg', startupinfo=None):
    """Write `video` out as `output` with a subtitle track for each of
    `tracks`: [{'segments', 'language' (Subtitld's code), 'title'}], the
    first the default one (and forced, when `forced`). An ASS track is
    sized to the video (`width`, `height`) and drawn in `look` ('clean', or
    'preview' with `preview`'s settings, as modules.ass draws them).
    Raises ValueError for no tracks, and RuntimeError with what FFmpeg said
    when it cannot write the file."""
    if not tracks:
        raise ValueError('There are no subtitles to write into the video')
    container = str(container or 'MP4').upper()
    if container == 'TS':
        from subtitld.modules import teletext
        return teletext.write_video(video, output, tracks, ffmpeg=ffmpeg, startupinfo=startupinfo)
    codec = codec_for(container, codec)
    extension = '.srt' if codec == 'srt' else '.ass'
    written = []
    try:
        for track in tracks:
            handle, path = tempfile.mkstemp(prefix='subtitld_track_', suffix=extension)
            os.close(handle)
            written.append(path)
            track_file(path, track.get('segments') or [], codec, width, height, look, preview)
        listed = [{'path': path, 'language': track.get('language') or '', 'title': track.get('title') or ''}
                  for path, track in zip(written, tracks)]
        run = subprocess.run(command(video, output, listed, container, codec, forced, ffmpeg),
                             capture_output=True, startupinfo=startupinfo, check=False)
        if run.returncode != 0 or not os.path.isfile(output) or not os.path.getsize(output):
            # The first line FFmpeg writes says what went wrong (a codec the
            # container cannot hold, as an MP4 cannot hold PCM sound).
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
    finally:
        for path in written:
            try:
                os.unlink(path)
            except OSError:
                pass
    return output
