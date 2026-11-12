"""A still of a video export, for the export dialog's preview: a few
seconds of the video around one subtitle written out as the export would
write the whole of it (bounce.write_captioned_video), then a frame of that
file with its subtitles read back out of it and drawn on it, by FFmpeg, as
a player would: a caption decoder's text, a subtitle track's text or
pictures, a teletext page.

Run it off the main thread: it encodes a few seconds of video, and an MXF
re-encodes them again."""

import os
import shutil
import subprocess
import tempfile

from subtitld.modules import session

# Seconds of the video before the subtitle: pop-on captions are sent ahead
# of the moment they show, and a decoder starts on a picture of its own.
LEAD = 2.0
# Seconds after the still, so its frame is not the clip's last.
TAIL = 0.5


class StillError(RuntimeError):
    """FFmpeg could not write the clip, or could not draw its subtitles."""


def moment(cue, at):
    """When the still is taken: the cursor, when it is on `cue`; else the
    middle of the cue, where roll-up and paint-on captions are whole."""
    start, end = float(cue['start']), float(cue['end'])
    return at if start <= at < end else (start + end) / 2.0


def shifted(segments, start, length):
    """The subtitles of the clip from `start`, `length` seconds long, with
    their times counted from its start."""
    out = []
    for segment in segments or []:
        if float(segment['end']) <= start or float(segment['start']) >= start + length:
            continue
        out.append(dict(segment, start=max(0.0, float(segment['start']) - start),
                        end=min(length, float(segment['end']) - start)))
    return out


def still(container, options, segments, cue, at, output, video=None, info=None, language=''):
    """Write to `output` (a PNG) the frame of the `container` export at the
    subtitle `cue` (the cursor `at`, when it is on it), with what that file
    shows drawn on it; for the subtitles alone, with its clear parts kept.
    Raises ValueError for what the export would refuse too, and StillError
    when FFmpeg cannot do its part."""
    from subtitld.modules import a53, bounce, burned_video, embedded_captions
    info = dict(info if info is not None else (session.VIDEO or {}))
    video = video or info.get('filepath')
    alpha = burned_video.is_alpha(container)
    if not video and not alpha:
        raise ValueError('No video loaded')
    if container in ('MP4 A53', 'TS A53'):
        codec = a53.probe(video, session.FFPROBE_EXECUTABLE, session.STARTUPINFO)[0]
        if codec not in a53.CODECS:
            raise ValueError(f'Captions go in the frames of H.264, HEVC or MPEG-2 video, not {codec or "this video"}')
    when = moment(cue, at)
    # Drawn subtitles need nothing sent ahead of them.
    lead = 0.2 if burned_video.is_burned(container) else LEAD
    start = max(0.0, min(when, float(cue['start'])) - lead)
    length = when - start + TAIL
    folder = tempfile.mkdtemp(prefix='subtitld-still-')
    try:
        source = None
        if not alpha:
            source = os.path.join(folder, 'source.mp4')
            _ffmpeg(['-ss', f'{start:.3f}', '-i', str(video), '-t', f'{length:.3f}', '-map', '0:v:0',
                     '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                     source])
        holder = embedded_captions.CONTAINERS.get(container) or burned_video.CONTAINERS.get(container)
        extension = holder['extension'] if holder else _track_extension(container)
        clip = os.path.join(folder, 'clip.' + extension)
        bounce.write_captioned_video(source, clip, container, options, shifted(segments, start, length),
                                     dict(info, duration=length), language)
        _draw(clip, container, when - start, output, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return output


def _track_extension(container):
    from subtitld.modules import soft_subtitles
    return soft_subtitles.CONTAINERS[container]['extension']


def _draw(clip, container, when, output, folder):
    """The frame of `clip` at `when` seconds, its subtitles drawn on it."""
    from subtitld.modules import burned_video, embedded_captions
    at = ['-ss', f'{when:.3f}', '-frames:v', '1', '-update', '1', output]
    if burned_video.is_burned(container):
        # In the picture already; alone, its clear parts kept (VP9's alpha
        # is read by libvpx alone).
        decoder = ['-c:v', 'libvpx-vp9'] if container == 'ALPHA WEBM' else []
        rgba = ['-pix_fmt', 'rgba'] if burned_video.is_alpha(container) else []
        _ffmpeg(decoder + ['-i', clip, '-map', '0:v:0'] + rgba + at)
    elif container in embedded_captions.CONTAINERS:
        # The captions a decoder reads, as the text it shows, drawn by libass.
        captions = os.path.join(folder, 'captions.ass')
        if container in ('MP4 A53', 'TS A53'):
            _ffmpeg(['-f', 'lavfi', '-i', f'movie={_escaped(clip)}[out0+subcc]', '-map', '0:s:0', captions])
        else:
            # An MXF's captions are in its ancillary data, which FFmpeg 7.1
            # and later read as captions when asked.
            extract = ['-eia608_extract', '1'] if container == 'MXF' else []
            _ffmpeg(extract + ['-i', clip, '-map', '0:s:0', captions])
        _ffmpeg(['-i', clip, '-vf', f'subtitles={_escaped(captions)}'] + at)
    elif container in ('TS', 'TS DVB'):
        # Pictures: a DVB subtitle, or the teletext page, over the frame.
        decoder = ['-txt_page', '888', '-txt_format', 'bitmap'] if container == 'TS' else []
        _ffmpeg(decoder + ['-i', clip, '-filter_complex',
                           '[0:s:0][0:v:0]scale2ref[sub][video];[video][sub]overlay'] + at)
    else:
        _ffmpeg(['-i', clip, '-vf', f'subtitles={_escaped(clip)}:si=0'] + at)
    if not os.path.isfile(output) or not os.path.getsize(output):
        raise StillError('FFmpeg drew no frame')


def _escaped(path):
    """A path as a filter's argument: its colons, quotes and backslashes
    escaped, twice, once for the option and once for the filter graph."""
    for char in ('\\', ':', "'"):
        path = path.replace(char, '\\' + char)
    for char in ('\\', "'", '[', ']', ',', ';'):
        path = path.replace(char, '\\' + char)
    return path


def _ffmpeg(arguments):
    run = subprocess.run([str(session.FFMPEG_EXECUTABLE), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin']
                         + arguments, capture_output=True, startupinfo=session.STARTUPINFO, check=False)
    if run.returncode != 0:
        said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        raise StillError(said[0] if said else 'FFmpeg failed')


# The threads drawing a still, kept until they are done: the dialog that
# asked for one may close first.
_running = []


def still_async(container, options, segments, cue, at, output, on_done, language=''):
    """`still` off the main thread; `on_done(output, error)` is called on the
    main thread when it is done, `error` empty when the still was drawn."""
    from PySide6.QtCore import Qt, QThread, Signal

    class StillThread(QThread):
        done = Signal(str, str)

        def run(self):
            try:
                still(container, options, segments, cue, at, output, language=language)
                self.done.emit(output, '')
            except Exception as exc:
                self.done.emit(output, str(exc) or exc.__class__.__name__)

    thread = StillThread()

    def _finished(path, error):
        if thread in _running:
            _running.remove(thread)
        thread.deleteLater()
        on_done(path, error)

    thread.done.connect(_finished, Qt.QueuedConnection)
    _running.append(thread)
    thread.start()
    return thread
