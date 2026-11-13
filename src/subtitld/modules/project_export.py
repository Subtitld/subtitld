"""The subtitles and their dub as a project of another program, a video or
an audio editor, to go on working there: the export dialog's Project tab.

What every program gets is gathered here once, as a Timeline:

- the video, linked where it is (it is not copied), and its sound;
- or, in place of that sound, the background the voices were separated
  from (music and effects), when Subtitld has it;
- the dubbed speeches, each the clip that plays for its subtitle (the
  first not muted), cut as it is cut, on a track for each speaker; a
  speaker whose speeches overlap gets a second track;
- the subtitles, and each language they are translated into.

The speeches' sound files are copied into a folder beside the project
(`gather`), so that it opens on another computer too. Each program's
writer (kdenlive_project, ...) then writes the Timeline its own way.
"""

import json
import os
import shutil
import subprocess
from fractions import Fraction

from subtitld.modules import session

# The programs, by the key the export knows them by: the extension of
# their project, and the module that writes it.
FORMATS = {
    'KDENLIVE': {'extension': 'kdenlive', 'writer': 'kdenlive_project'},
    'SHOTCUT': {'extension': 'mlt', 'writer': 'shotcut_project'},
    'OPENSHOT': {'extension': 'osp', 'writer': 'openshot_project', 'one_subtitle_track': True},
    'AUDACITY': {'extension': 'aup', 'writer': 'audacity_project', 'audio_only': True},
}

# What goes in, when nothing is chosen.
DEFAULTS = {'video': True, 'audio': 'original', 'speeches': True, 'subtitles': True, 'translations': True}
# The sound under the speeches: the video's own, the background alone,
# or none.
AUDIO = ('original', 'background', 'none')
# The rates a float stands for, as the fractions programs count them in.
_RATES = {23.976: Fraction(24000, 1001), 29.97: Fraction(30000, 1001), 59.94: Fraction(60000, 1001),
          47.952: Fraction(48000, 1001), 119.88: Fraction(120000, 1001)}


def rate_of(fps):
    """`fps` (a float) as a Fraction: 30000/1001 for 29.97, 25 for 25."""
    fps = float(fps or 25.0)
    for known, fraction in _RATES.items():
        if abs(fps - known) < 0.01:
            return fraction
    return Fraction(fps).limit_denominator(1001)


def clock(frames, fps):
    """`frames` at `fps` as MLT's clock time, "HH:MM:SS.mmm"."""
    millis = int(round(frames * 1000 / float(fps)))
    hours, rest = divmod(millis, 3600000)
    minutes, rest = divmod(rest, 60000)
    return f'{hours:02d}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}'


class Clip:
    """A file's part on a track: from `source_in` to `source_out` seconds of
    `path`, starting `at` seconds into the timeline."""

    def __init__(self, path, at, source_in=0.0, source_out=None, name=''):
        self.path = path
        self.at = float(at)
        self.source_in = float(source_in)
        self.source_out = None if source_out is None else float(source_out)
        self.name = name

    @property
    def length(self):
        return max(0.0, self.source_out - self.source_in)

    @property
    def end(self):
        return self.at + self.length


class Track:
    """A track of `kind` ('video' or 'audio'), named `name`, its clips in
    the order they start."""

    def __init__(self, name, kind, clips=None):
        self.name = name
        self.kind = kind
        self.clips = sorted(clips or [], key=lambda clip: clip.at)


class Timeline:
    """What a project holds: its picture's size and rate, the tracks (the
    video's first, then the sound's, then a track a speaker), the subtitle
    tracks ([(name, language, segments)], the subtitles first), and how
    long it all is."""

    def __init__(self, width, height, rate, tracks, subtitles, duration, title=''):
        self.width = int(width)
        self.height = int(height)
        self.rate = rate
        self.tracks = tracks
        self.subtitles = subtitles
        self.duration = float(duration)
        self.title = title

    @property
    def fps(self):
        return float(self.rate)

    def media(self):
        """Every file the tracks use, once, in the order they first come."""
        out = []
        for track in self.tracks:
            for clip in track.clips:
                if clip.path not in out:
                    out.append(clip.path)
        return out


def media_length(path):
    """How long the file at `path` is, in seconds: a sound file read by
    soundfile, anything else asked of FFprobe; 0.0 when neither knows."""
    try:
        import soundfile
        return float(soundfile.info(path).duration)
    except Exception:
        pass
    try:
        run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'csv=p=0', str(path)], capture_output=True, text=True,
                             startupinfo=session.STARTUPINFO, check=False)
        return float(run.stdout.strip() or 0)
    except (OSError, ValueError):
        return 0.0


def has_audio(path):
    """Whether the file at `path` has a sound stream."""
    try:
        run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-select_streams', 'a',
                              '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)],
                             capture_output=True, text=True, startupinfo=session.STARTUPINFO, check=False)
        return bool(run.stdout.strip())
    except OSError:
        return False


def _playing_dub(dubs):
    """The dub that plays: the first not muted (audioengine's rule)."""
    for index, dub in enumerate(dubs or []):
        if isinstance(dub, dict) and not dub.get('muted', index != 0):
            return dub
    return None


def speech_clips(segment):
    """The clips of `segment`'s dub as it plays: one for the whole file, or
    one for each of the pieces it was cut into."""
    dub = _playing_dub(segment.get('dubbing'))
    if not dub:
        return []
    base = float(dub.get('start', segment.get('start', 0.0)))
    pieces = dub.get('segments')
    if pieces is None:
        path = dub.get('path')
        if not path or not os.path.isfile(path):
            return []
        length = media_length(path)
        return [Clip(path, base, 0.0, length)] if length > 0 else []
    out = []
    for piece in pieces:
        if piece.get('type', 'audio') != 'audio' or not piece.get('path') or piece.get('end') is None:
            continue
        if not os.path.isfile(piece['path']):
            continue
        source_in = float(piece.get('start', 0.0))
        out.append(Clip(piece['path'], base + float(piece.get('offset', 0.0)), source_in, float(piece['end'])))
    return [clip for clip in out if clip.length > 0]


def _lanes(clips):
    """`clips` laid on as few lanes as they fit on without overlapping."""
    lanes = []
    for clip in sorted(clips, key=lambda clip: clip.at):
        for lane in lanes:
            if lane[-1].end <= clip.at + 1e-6:
                lane.append(clip)
                break
        else:
            lanes.append([clip])
    return lanes


def collect(subtitle, video, options=None, background=None, names=None):
    """The Timeline of the project as `options` ask (DEFAULTS for those not
    given): of `subtitle` (session.SUBTITLE's shape) over `video`
    (session.VIDEO's), with the separated `background` (a path, or None).
    `names` gives the tracks' names: 'video', 'audio', 'background',
    'subtitles' (English when not given)."""
    options = dict(DEFAULTS, **{key: value for key, value in (options or {}).items() if value is not None})
    names = dict({'video': 'Video', 'audio': 'Audio', 'background': 'Background', 'subtitles': 'Subtitles'},
                 **(names or {}))
    video = video or {}
    segments = [s for s in (subtitle or {}).get('segments') or [] if isinstance(s, dict)]
    path = video.get('filepath') if video.get('filepath') and os.path.isfile(video.get('filepath')) else None
    tracks = []
    duration = float(video.get('duration') or 0.0)
    if path:
        duration = duration or media_length(path)
        if options['video']:
            tracks.append(Track(names['video'], 'video', [Clip(path, 0.0, 0.0, duration, os.path.basename(path))]))
        if options['audio'] == 'original' and has_audio(path):
            tracks.append(Track(names['audio'], 'audio', [Clip(path, 0.0, 0.0, duration, os.path.basename(path))]))
    if options['audio'] == 'background' and background and os.path.isfile(background):
        length = media_length(background)
        tracks.append(Track(names['background'], 'audio', [Clip(background, 0.0, 0.0, length)]))
    if options['speeches']:
        speakers = {}
        for segment in segments:
            clips = speech_clips(segment)
            if clips:
                speakers.setdefault(str(segment.get('speaker') or 'A'), []).extend(clips)
        for speaker, clips in speakers.items():
            lanes = _lanes(clips)
            for number, lane in enumerate(lanes, start=1):
                tracks.append(Track(speaker if len(lanes) == 1 else f'{speaker} {number}', 'audio', lane))
    for track in tracks:
        for clip in track.clips:
            duration = max(duration, clip.end)
    subtitles = []
    if options['subtitles']:
        subtitles.append((names['subtitles'], (subtitle or {}).get('language') or '', segments))
        if options['translations']:
            for language in _languages(segments):
                translated = [dict(s, text=s['translations'][language]) for s in segments
                              if isinstance((s.get('translations') or {}).get(language), str)]
                subtitles.append((language, language, translated))
        for _name, _language, cues in subtitles:
            for cue in cues:
                duration = max(duration, float(cue.get('end', 0.0)))
    from subtitld.modules import documents
    return Timeline(video.get('width') or 1920, video.get('height') or 1080, rate_of(video.get('framerate')),
                    tracks, subtitles, duration, documents.title_for(subtitle, video))


def _languages(segments):
    out = []
    for segment in segments:
        for language, text in (segment.get('translations') or {}).items():
            if isinstance(text, str) and language not in out:
                out.append(language)
    return out


def media_folder(project_path):
    """The folder the project's sound files are copied into: beside it,
    named after it."""
    stem = os.path.splitext(os.path.basename(project_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(project_path)), f'{stem} media')


def gather(timeline, folder, keep=()):
    """Copy every file `timeline` uses into `folder`, but those in `keep`
    (the video, linked where it is), and point its clips at the copies.
    The copies are named by their order and their track; a file used more
    than once is copied once."""
    copies = {}
    for track in timeline.tracks:
        for clip in track.clips:
            if clip.path in keep:
                continue
            if clip.path not in copies:
                os.makedirs(folder, exist_ok=True)
                safe = ''.join(char if char.isalnum() or char in ' -_' else '_' for char in track.name).strip() or 'audio'
                name = f'{len(copies) + 1:04d} {safe}{os.path.splitext(clip.path)[1].lower() or ".wav"}'
                target = os.path.join(folder, name)
                shutil.copyfile(clip.path, target)
                copies[clip.path] = target
            clip.path = copies[clip.path]
    return copies


def write_json(value):
    """`value` as the JSON Kdenlive and others keep in properties."""
    return json.dumps(value, indent=4, ensure_ascii=False)


def background_path(video):
    """The background Subtitld separated from `video`'s voices (music and
    effects), when it has one."""
    from subtitld.modules import bounce
    if not (video or {}).get('filepath'):
        return None
    return bounce._separation_path('background')


def save(path, fmt, subtitle, video, options=None, names=None):
    """Write the project `path` for the program `fmt` (FORMATS): the
    Timeline of `subtitle` over `video` as `options` ask, the sound files
    copied beside it."""
    import importlib
    options = dict(options or {})
    if FORMATS[fmt].get('audio_only'):
        options['video'] = False
    if FORMATS[fmt].get('one_subtitle_track'):
        options['translations'] = False
    timeline = collect(subtitle, video, options, background_path(video), names)
    keep = {(video or {}).get('filepath')}
    gather(timeline, media_folder(path), keep=keep)
    writer = importlib.import_module(f'subtitld.modules.{FORMATS[fmt]["writer"]}')
    writer.save(path, timeline)
    return path
