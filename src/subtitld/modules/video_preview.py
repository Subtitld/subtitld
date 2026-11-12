"""A still of a video export, for the export dialog's preview: a few
seconds of the video around one subtitle written out as the export would
write the whole of it (bounce.write_captioned_video), then a frame of that
file with its subtitles read back out of it and drawn on it, by FFmpeg, as
a player would: a caption decoder's text, a subtitle track's text or
pictures, a teletext page.

Run it off the main thread: it encodes a few seconds of video, and an MXF
re-encodes them again."""

import os
import re
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
    shows drawn on it, and return where on it the subtitles are: (x, y,
    width, height), of the frame, 0..1; None when nothing is drawn. Raises
    ValueError for what the export would refuse too, and StillError when
    FFmpeg cannot do its part."""
    from subtitld.modules import a53, bounce, embedded_captions
    info = dict(info if info is not None else (session.VIDEO or {}))
    video = video or info.get('filepath')
    if not video:
        raise ValueError('No video loaded')
    if container in ('MP4 A53', 'TS A53'):
        codec = a53.probe(video, session.FFPROBE_EXECUTABLE, session.STARTUPINFO)[0]
        if codec not in a53.CODECS:
            raise ValueError(f'Captions go in the frames of H.264, HEVC or MPEG-2 video, not {codec or "this video"}')
    when = moment(cue, at)
    start = max(0.0, min(when, float(cue['start'])) - LEAD)
    length = when - start + TAIL
    if container == 'TS':
        # A teletext decoder shows a page once the next one's header ends
        # it, at the subtitle's end: the clip runs past it.
        length = max(length, float(cue['end']) - start + TAIL)
    folder = tempfile.mkdtemp(prefix='subtitld-still-')
    try:
        source = os.path.join(folder, 'source.mp4')
        _ffmpeg(['-ss', f'{start:.3f}', '-i', str(video), '-t', f'{length:.3f}', '-map', '0:v:0', '-map', '0:a:0?',
                 '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', source])
        holder = embedded_captions.CONTAINERS.get(container)
        extension = holder['extension'] if holder else _track_extension(container)
        clip = os.path.join(folder, 'clip.' + extension)
        bounce.write_captioned_video(source, clip, container, options, shifted(segments, start, length),
                                     dict(info, duration=length), language)
        shown = _draw(clip, container, when - start, output, folder, float(cue['start']) - start)
        bare = os.path.join(folder, 'bare.png')
        _ffmpeg(['-i', clip, '-map', '0:v:0', '-ss', f'{shown:.3f}', '-frames:v', '1', '-update', '1', bare])
        return drawn_area(output, bare)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def drawn_area(still_path, bare_path, threshold=48):
    """Where `still_path` differs from `bare_path`, the same frame with
    nothing drawn on it: (x, y, width, height), 0..1, around what changed
    by more than `threshold` (an MXF's picture is encoded again, and
    differs from its source a little everywhere, and the same frame
    decoded twice may not be); None when nothing did."""
    import numpy as np
    from PySide6.QtGui import QImage
    images = [QImage(path).convertToFormat(QImage.Format_RGB888) for path in (still_path, bare_path)]
    if any(image.isNull() for image in images) or images[0].size() != images[1].size():
        return None
    width, height = images[0].width(), images[0].height()
    arrays = [np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())[:, :width * 3]
              .reshape(height, width, 3).astype(np.int16) for image in images]
    changed = (np.abs(arrays[0] - arrays[1]).max(axis=2) > threshold)
    rows, columns = np.where(changed.sum(axis=1) >= 2)[0], np.where(changed.sum(axis=0) >= 2)[0]
    if not len(rows) or not len(columns):
        return None
    pad = 6
    left, top = max(0, columns[0] - pad), max(0, rows[0] - pad)
    right, bottom = min(width, columns[-1] + 1 + pad), min(height, rows[-1] + 1 + pad)
    return (float(left / width), float(top / height), float((right - left) / width), float((bottom - top) / height))


def _track_extension(container):
    from subtitld.modules import soft_subtitles
    return soft_subtitles.CONTAINERS[container]['extension']


def _draw(clip, container, when, output, folder, cue_start=0.0):
    """The frame of `clip` at `when` seconds, its subtitles drawn on it; a
    teletext page, at the first moment after `cue_start` it is shown.
    Returns when the frame is."""
    from subtitld.modules import embedded_captions
    if container == 'TS':
        when = _page_shown(clip, cue_start, folder, when)
    at = ['-ss', f'{when:.3f}', '-frames:v', '1', '-update', '1', output]
    if container in embedded_captions.CONTAINERS:
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
    return when


def _page_shown(clip, after, folder, default):
    """When the teletext decoder first shows a page with text on it, from
    `after` seconds into `clip` on; `default` if it shows none."""
    pages = os.path.join(folder, 'pages.ass')
    _ffmpeg(['-txt_page', '888', '-txt_format', 'ass', '-i', clip, '-map', '0:s:0', pages])
    with open(pages, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if not line.startswith('Dialogue:'):
                continue
            fields = line.split(',', 9)
            text = re.sub(r'{[^}]*}|\\[Nnh]', '', fields[9]).strip()
            hours, minutes, seconds = fields[1].split(':')
            shown = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            if text and shown >= after - 0.01:
                return shown + 0.05
    return default


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
    """`still` off the main thread; `on_done(output, area, error)` is called
    on the main thread when it is done, `error` empty when the still was
    drawn."""
    from PySide6.QtCore import Qt, QThread, Signal

    class StillThread(QThread):
        done = Signal(str, object, str)

        def run(self):
            try:
                area = still(container, options, segments, cue, at, output, language=language)
                self.done.emit(output, area, '')
            except Exception as exc:
                self.done.emit(output, None, str(exc) or exc.__class__.__name__)

    thread = StillThread()

    def _finished(path, area, error):
        if thread in _running:
            _running.remove(thread)
        thread.deleteLater()
        on_done(path, area, error)

    thread.done.connect(_finished, Qt.QueuedConnection)
    _running.append(thread)
    thread.start()
    return thread
