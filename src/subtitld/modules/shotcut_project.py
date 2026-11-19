"""A Shotcut project (.mlt) of a project_export.Timeline, as Shotcut 24.04
and later write it (its source's models/multitrackmodel.cpp and
models/subtitlesmodel.cpp).

It is MLT XML, which melt plays as it is:

- the timeline is a <tractor> marked ``shotcut``, its tracks <playlist>s:
  first the "background" (a black colour Shotcut keeps there), then the
  video tracks from the bottom up, then the audio tracks; each named
  (``shotcut:name``) and marked ``shotcut:video`` or ``shotcut:audio``;
- every track is mixed into the sound by a ``mix`` transition, and each
  video track laid over the ones under it by a ``qtblend`` one (off for
  the bottom one, which has nothing under it);
- the video is on V1 without its sound, and its sound on A1, as
  Shotcut's Detach Audio leaves them;
- each subtitle track is a hidden ``subtitle_feed`` filter on the
  tractor, holding its name, its language (ISO 639-2) and its subtitles
  as SRT.
"""

import os
from math import gcd

from lxml import etree

from subtitld.modules import markup, project_export
from subtitld.modules.segment import text_of

SHOTCUT_VERSION = '25.10.31'


def iso639_2(language):
    """The three-letter code of `language` ('pt-br' -> 'por'), as Shotcut's
    subtitle tracks name theirs; '' when Qt does not know it."""
    from PySide6.QtCore import QLocale
    if not language:
        return ''
    locale = QLocale(language.replace('-', '_'))
    if locale.language() in (QLocale.C, QLocale.AnyLanguage):
        return ''
    return QLocale.languageToCode(locale.language(), QLocale.ISO639Part2)


def srt(cues):
    """`cues` as SRT text, their formatting dropped, as Shotcut reads it."""
    def stamp(seconds):
        millis = int(round(max(0.0, float(seconds)) * 1000))
        hours, rest = divmod(millis, 3600000)
        minutes, rest = divmod(rest, 60000)
        return f'{hours:02d}:{minutes:02d}:{rest // 1000:02d},{rest % 1000:03d}'
    blocks = []
    for number, cue in enumerate(sorted(cues, key=lambda c: float(c['start'])), start=1):
        text = markup.plain(text_of(cue)).strip()
        if text:
            blocks.append(f'{len(blocks) + 1}\n{stamp(cue["start"])} --> {stamp(cue["end"])}\n{text}\n')
    return '\n'.join(blocks)


def save(path, timeline):
    """Write `timeline` as the Shotcut project `path`."""
    fps = timeline.fps
    frames = lambda seconds: int(round(float(seconds) * fps))
    clock = lambda count: project_export.clock(count, fps)
    length = max(1, frames(timeline.duration))
    root = etree.Element('mlt', LC_NUMERIC='C', version='7.36.1', title=f'Shotcut version {SHOTCUT_VERSION}',
                         producer='main_bin', root=os.path.dirname(os.path.abspath(path)))

    def element(tag, attributes=None, properties=None, parent=root):
        node = etree.SubElement(parent, tag, {key: str(value) for key, value in (attributes or {}).items()})
        for name, value in (properties or {}).items():
            etree.SubElement(node, 'property', name=name).text = str(value)
        return node

    rate = timeline.rate
    divisor = gcd(timeline.width, timeline.height) or 1
    element('profile', {'description': 'automatic', 'width': timeline.width, 'height': timeline.height,
                        'progressive': 1, 'sample_aspect_num': 1, 'sample_aspect_den': 1,
                        'display_aspect_num': timeline.width // divisor,
                        'display_aspect_den': timeline.height // divisor,
                        'frame_rate_num': rate.numerator, 'frame_rate_den': rate.denominator, 'colorspace': 709})
    element('playlist', {'id': 'main_bin'}, {'xml_retain': 1})
    element('producer', {'id': 'black', 'in': clock(0), 'out': clock(length - 1)},
            {'length': clock(length), 'eof': 'pause', 'resource': '0', 'aspect_ratio': 1, 'mlt_service': 'color',
             'mlt_image_format': 'rgba', 'set.test_audio': 0})
    background = element('playlist', {'id': 'background'})
    element('entry', {'producer': 'black', 'in': clock(0), 'out': clock(length - 1)}, parent=background)

    # A producer for each file and kind of track: the video's picture on its
    # own, its sound on its own.
    producers = {}
    count = 0

    def producer_for(path, kind):
        nonlocal count
        if (path, kind) not in producers:
            count += 1
            whole = max(1, frames(project_export.media_length(path)))
            video = kind == 'video'
            properties = {'length': clock(whole), 'eof': 'pause', 'resource': path,
                          'mlt_service': 'avformat-novalidate', 'seekable': 1,
                          # A stream's index counts the file's streams of every kind:
                          # a video's sound is its second.
                          'audio_index': -1 if video else (1 if _has_video(path) else 0),
                          'video_index': 0 if video else -1, 'shotcut:caption': os.path.basename(path)}
            producers[(path, kind)] = f'chain{count}'
            element('chain', {'id': producers[(path, kind)], 'out': clock(whole - 1)}, properties)
        return producers[(path, kind)]

    ordered = [t for t in timeline.tracks if t.kind == 'video'] + [t for t in timeline.tracks if t.kind == 'audio']
    playlists = []
    numbers = {'video': 0, 'audio': 0}
    for number, track in enumerate(ordered):
        for clip in track.clips:
            producer_for(clip.path, track.kind)
        numbers[track.kind] += 1
        playlist = element('playlist', {'id': f'playlist{number}'},
                           {f'shotcut:{track.kind}': 1, 'shotcut:name': track.name})
        at = 0
        for clip in track.clips:
            start = max(at, frames(clip.at))
            if start > at:
                element('blank', {'length': clock(start - at)}, parent=playlist)
            first, size = frames(clip.source_in), max(1, frames(clip.length))
            element('entry', {'producer': producer_for(clip.path, track.kind), 'in': clock(first),
                              'out': clock(first + size - 1)}, parent=playlist)
            at = start + size
        playlists.append((playlist.get('id'), track.kind))

    tractor = element('tractor', {'id': 'tractor0', 'title': f'Shotcut version {SHOTCUT_VERSION}',
                                  'in': clock(0), 'out': clock(length - 1)},
                      {'shotcut': 1, 'shotcut:projectAudioChannels': 2, 'shotcut:projectFolder': 1})
    element('track', {'producer': 'background'}, parent=tractor)
    for playlist_id, kind in playlists:
        element('track', {'producer': playlist_id, **({'hide': 'video'} if kind == 'audio' else {})}, parent=tractor)
    under = 0
    for index, (_playlist_id, kind) in enumerate(playlists, start=1):
        element('transition', {'id': f'transition{index}a'}, {'a_track': 0, 'b_track': index, 'mlt_service': 'mix',
                                                             'always_active': 1, 'sum': 1}, parent=tractor)
        if kind == 'video':
            element('transition', {'id': f'transition{index}v'}, {
                'a_track': under, 'b_track': index, 'version': 0.1, 'mlt_service': 'qtblend', 'threads': 0,
                'disable': 1 if under == 0 else 0}, parent=tractor)
            under = index
    for number, (name, language, cues) in enumerate(timeline.subtitles):
        element('filter', {'id': f'filter{number}'}, {
            'mlt_service': 'subtitle_feed', 'feed': name, 'lang': iso639_2(language), 'text': srt(cues),
            'shotcut:hidden': 1}, parent=tractor)
    etree.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True, pretty_print=True)
    return path


def _has_video(path):
    import subprocess
    from subtitld.modules import session
    run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-select_streams', 'v',
                          '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, startupinfo=session.STARTUPINFO, check=False)
    return bool(run.stdout.strip())
