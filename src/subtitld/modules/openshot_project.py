"""An OpenShot project (.osp) of a project_export.Timeline, as OpenShot 3
writes it: JSON, its clips' properties as libopenshot's Clip writes them,
its files as libopenshot's FFmpegReader describes them.

- each track is a layer of its own, named after it, the video's on top;
- the video is a clip without its sound, its sound a clip without its
  picture, as OpenShot's Separate Audio leaves them;
- the subtitles are a Caption effect on the video's clip, its text a
  block for each subtitle ("HH:MM:SS:mmm --> HH:MM:SS:mmm", then the
  text). A Caption is drawn on the clip it is on, so there is one: the
  subtitles, not their translations; and none without a video.
"""

import json
import os
import random
import string
import subprocess
from fractions import Fraction

from subtitld.modules import markup, project_export, session

OPENSHOT_VERSION = '3.5.1'
LIBOPENSHOT_VERSION = '0.5.0'
# How far apart OpenShot numbers its layers.
LAYER_STEP = 1000000


# OpenShot's profiles by picture size (its profiles folder), the start of
# their names; the ones it names apart.
_PROFILE_SIZES = {(1280, 720): 'HD 720p', (1920, 1080): 'FHD 1080p', (2560, 1440): '2.5K WQHD 1440p',
                  (3840, 2160): '4K UHD 2160p', (1600, 900): 'HD+ 900p', (3200, 1800): '3K QHD+ 1800p',
                  (5120, 2880): '5K UHD 2880p', (7680, 4320): '8K UHD 4320p', (720, 1280): 'HD Vertical 720p',
                  (1080, 1920): 'FHD Vertical 1080p', (1080, 1080): 'HD Square 1080p'}
_PROFILE_NAMES = {(1280, 720, 50): 'PAL HD 720p 50 fps', (1920, 1080, 25): 'FHD PAL 1080p 25 fps',
                  (1920, 1080, 50): 'FHD PAL 1080p 50 fps'}


def profile_name(width, height, rate):
    """The name of OpenShot's profile for a picture `width` x `height` at
    `rate` (a Fraction), which it finds a project's by; '' when it has
    none of that size."""
    label = {Fraction(24000, 1001): '23.98', Fraction(30000, 1001): '29.97',
             Fraction(60000, 1001): '59.94'}.get(rate, f'{float(rate):g}')
    if rate.denominator == 1 and (width, height, rate.numerator) in _PROFILE_NAMES:
        return _PROFILE_NAMES[(width, height, rate.numerator)]
    size = _PROFILE_SIZES.get((width, height))
    return f'{size} {label} fps' if size else ''


def _id():
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))


def _keyframe(value):
    """A keyframe holding `value`, as libopenshot writes one."""
    return {'Points': [{'co': {'X': 1.0, 'Y': float(value)}, 'handle_left': {'X': 0.5, 'Y': 1.0},
                        'handle_right': {'X': 0.5, 'Y': 0.0}, 'handle_type': 0, 'interpolation': 0}]}


def _color(red, green, blue, alpha=255):
    return {'red': _keyframe(red), 'green': _keyframe(green), 'blue': _keyframe(blue), 'alpha': _keyframe(alpha)}


def _fraction(text, default):
    try:
        value = Fraction(text)
        return {'num': value.numerator, 'den': value.denominator}
    except (ValueError, ZeroDivisionError, TypeError):
        return default


def reader(path):
    """What libopenshot's FFmpegReader says of the file at `path`, as
    OpenShot keeps it in its project, from FFprobe."""
    run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json',
                          str(path)], capture_output=True, text=True, startupinfo=session.STARTUPINFO, check=False)
    probe = json.loads(run.stdout or '{}')
    streams = probe.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    duration = float((probe.get('format') or {}).get('duration') or project_export.media_length(path) or 0.0)
    fps = _fraction(video.get('r_frame_rate'), {'num': 30, 'den': 1}) if video else {'num': 30, 'den': 1}
    width, height = (int(video.get('width', 0)), int(video.get('height', 0))) if video else (720, 480)
    divisor = Fraction(width, height) if video and height else Fraction(1)
    return {
        'acodec': audio.get('codec_name', '') if audio else '',
        'audio_bit_rate': int(audio.get('bit_rate', 0) or 0) if audio else 0,
        'audio_stream_index': int(audio['index']) if audio else -1,
        # The streams' own time bases, which libopenshot seeks by.
        'audio_timebase': _fraction(audio.get('time_base'), {'num': 1, 'den': int(audio.get('sample_rate', 48000))})
        if audio else {'num': 1, 'den': 1},
        'channel_layout': 4 if audio and int(audio.get('channels', 1)) == 1 else 3,
        'channels': int(audio.get('channels', 0)) if audio else 0,
        'display_ratio': {'num': divisor.numerator, 'den': divisor.denominator},
        'duration': duration, 'duration_strategy': 'VideoPreferred',
        'file_size': os.path.getsize(path), 'fps': fps, 'has_audio': bool(audio), 'has_single_image': False,
        'has_video': bool(video), 'height': height, 'width': width, 'interlaced_frame': False, 'metadata': {},
        'path': path, 'pixel_format': 0 if video else -1, 'pixel_ratio': {'num': 1, 'den': 1},
        'sample_rate': int(audio.get('sample_rate', 0)) if audio else 0, 'top_field_first': True,
        'type': 'FFmpegReader', 'vcodec': video.get('codec_name', '') if video else '',
        'video_bit_rate': int(video.get('bit_rate', 0) or 0) if video else 0,
        'video_length': int(round(duration * fps['num'] / fps['den'])),
        'video_stream_index': int(video['index']) if video else -1,
        'video_timebase': _fraction(video.get('time_base'), {'num': fps['den'], 'den': fps['num']})
        if video else {'num': fps['den'], 'den': fps['num']},
    }


def _clip(file, layer, at, start, end, picture=True, sound=True):
    """A clip of `file` (a files entry) on `layer`: from `start` to `end`
    seconds of it, `at` seconds into the timeline."""
    clip = {'id': _id(), 'layer': layer, 'position': float(at), 'start': float(start), 'end': float(end),
            'duration': file['duration'], 'file_id': file['id'], 'title': os.path.basename(file['path']),
            'reader': file, 'effects': [], 'anchor': 0, 'composite': 0, 'display': 0, 'gravity': 4,
            'mixing': 0, 'parentObjectId': '', 'scale': 1, 'waveform': False,
            'wave_color': _color(0, 123, 255)}
    for name, value in (('alpha', 1.0), ('location_x', 0.0), ('location_y', 0.0), ('origin_x', 0.5),
                        ('origin_y', 0.5), ('rotation', 0.0), ('scale_x', 1.0), ('scale_y', 1.0), ('shear_x', 0.0),
                        ('shear_y', 0.0), ('time', 1.0), ('volume', 1.0), ('channel_filter', -1.0),
                        ('channel_mapping', -1.0), ('has_audio', -1.0 if sound else 0.0),
                        ('has_video', -1.0 if picture else 0.0)):
        clip[name] = _keyframe(value)
    for corner in ('c1', 'c2', 'c3', 'c4'):
        clip[f'perspective_{corner}_x'] = _keyframe(-1.0)
        clip[f'perspective_{corner}_y'] = _keyframe(-1.0)
    return clip


def caption_text(cues):
    """`cues` as a Caption effect's text."""
    def stamp(seconds):
        millis = int(round(max(0.0, float(seconds)) * 1000))
        hours, rest = divmod(millis, 3600000)
        minutes, rest = divmod(rest, 60000)
        return f'{hours:02d}:{minutes:02d}:{rest // 1000:02d}:{rest % 1000:03d}'
    blocks = []
    for cue in sorted(cues, key=lambda c: float(c['start'])):
        text = markup.plain(str(cue.get('text') or '')).strip()
        if text:
            blocks.append(f'{stamp(cue["start"])} --> {stamp(cue["end"])}\n{text}')
    return '\n\n'.join(blocks)


def _caption(cues, layer):
    effect = {'id': _id(), 'type': 'Caption', 'class_name': 'Caption', 'name': 'Caption',
              'description': 'Add text captions on top of your video.', 'caption_text': caption_text(cues),
              'caption_font': 'sans', 'apply_before_clip': True, 'has_audio': False, 'has_video': True,
              'has_tracked_object': False, 'layer': layer, 'order': 0, 'position': 0.0, 'start': 0.0, 'end': 0.0,
              'duration': 0.0, 'mask_invert': False, 'mask_loop_mode': 0, 'mask_reader': {}, 'mask_time_mode': 1,
              'parent_effect_id': '', 'background': _color(0, 0, 0), 'color': _color(255, 255, 255),
              'stroke': _color(169, 169, 169)}
    for name, value in (('background_alpha', 0.0), ('background_corner', 10.0), ('background_padding', 20.0),
                        ('fade_in', 0.35), ('fade_out', 0.35), ('font_alpha', 1.0), ('font_size', 30.0),
                        ('left', 0.1), ('right', 0.1), ('line_spacing', 1.0), ('stroke_width', 0.5),
                        ('top', 0.75)):
        effect[name] = _keyframe(value)
    return effect


def save(path, timeline):
    """Write `timeline` as the OpenShot project `path`."""
    files, by_path = [], {}

    def file_for(source):
        if source not in by_path:
            entry = dict(reader(source), id=_id(), media_type='video' if _has_picture(source) else 'audio',
                         name=os.path.basename(source))
            by_path[source] = entry
            files.append(entry)
        return by_path[source]

    # The layers from the bottom up: the sound tracks, the last lowest, then
    # the pictures.
    ordered = [t for t in timeline.tracks if t.kind == 'audio'][::-1] + \
        [t for t in timeline.tracks if t.kind == 'video']
    layers, clips = [], []
    for number, track in enumerate(ordered, start=1):
        layer = number * LAYER_STEP
        layers.append({'id': f'L{number}', 'label': track.name, 'number': layer, 'y': 0, 'lock': False})
        for clip in track.clips:
            video = track.kind == 'video'
            clips.append(_clip(file_for(clip.path), layer, clip.at, clip.source_in, clip.source_out,
                               picture=video, sound=not video))
    pictures = [c for c in clips if c['has_audio']['Points'][0]['co']['Y'] == 0.0]
    if pictures and timeline.subtitles:
        _name, _language, cues = timeline.subtitles[0]
        pictures[-1]['effects'].append(_caption(cues, pictures[-1]['layer']))
    rate = timeline.rate
    ratio = Fraction(timeline.width, timeline.height)
    project = {
        'id': 'T0', 'fps': {'num': rate.numerator, 'den': rate.denominator},
        'display_ratio': {'num': ratio.numerator, 'den': ratio.denominator}, 'pixel_ratio': {'num': 1, 'den': 1},
        'width': timeline.width, 'height': timeline.height, 'sample_rate': 48000, 'channels': 2,
        'channel_layout': 3, 'settings': {}, 'clips': clips, 'effects': [], 'files': files,
        'duration': max(1.0, timeline.duration), 'scale': 15.0, 'tick_pixels': 100, 'playhead_position': 0,
        'profile': profile_name(timeline.width, timeline.height, rate), 'profile_settings': {},
        'layers': layers, 'markers': [], 'progress': [], 'history': {'undo': [], 'redo': []},
        'version': {'openshot-qt': OPENSHOT_VERSION, 'libopenshot': LIBOPENSHOT_VERSION}}
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(project, handle, indent=1, ensure_ascii=False)
    return path


def _has_picture(path):
    run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-select_streams', 'v',
                          '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, startupinfo=session.STARTUPINFO, check=False)
    return bool(run.stdout.strip())
