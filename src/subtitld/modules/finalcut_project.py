"""A Final Cut Pro project (FCPXML) of a project_export.Timeline, which
DaVinci Resolve imports too: the video as the storyline, the captions
(the subtitles and each translation, a caption language each) anchored to
it, and a lane under it for each speaker's speeches (fcpxml.write).

The video's own sound stays in its clip, turned down when another sound
is chosen; the separated background, when chosen, is a lane of its own.
"""

from subtitld.modules import fcpxml


def media_of(timeline):
    """The `media` of fcpxml.write for `timeline`."""
    video = next((t.clips[0] for t in timeline.tracks if t.kind == 'video' and t.clips), None)
    own_sound = False
    lanes = []
    for track in timeline.tracks:
        if track.kind != 'audio':
            continue
        if video and [c.path for c in track.clips] == [video.path]:
            own_sound = True                        # in the video's clip already
            continue
        lanes.append([(c.path, c.at, c.source_in, c.length, False, True) for c in track.clips])
    picture = (video.path, video.at, video.source_in, video.length, True, True) if video else None
    return {'video': picture, 'muted': bool(video) and not own_sound, 'lanes': lanes}


def save(path, timeline):
    """Write `timeline` as the FCPXML project `path`."""
    tracks = [(language or name, cues) for name, language, cues in timeline.subtitles]
    fcpxml.save(path, tracks, fps=timeline.fps, title=timeline.title or 'Subtitles', width=timeline.width,
                height=timeline.height, media=media_of(timeline))
    return path
