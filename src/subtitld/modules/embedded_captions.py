"""Closed captions written into a video file, the way a broadcast master
carries them rather than as a subtitle file beside it.

Two containers take them, and they carry different things:

- **QuickTime (.mov)**: a caption track of its own, `c608`, holding
  CEA-608. The picture and sound are copied as they are, so the export is
  quick and loses nothing of them. CEA-708 has no place in a QuickTime
  caption track, so a .mov carries CEA-608 alone.
- **MXF (OP1a, .mxf)**: the ancillary data of each frame, SMPTE ST 436,
  holding the caption distribution packets that carry **both** CEA-608 and
  CEA-708 — what Premiere and Avid write, and what a station asks for. MXF
  takes none of the usual delivery codecs as they are, so the picture is
  re-encoded: DNxHR (Avid's), MPEG-2 (Sony's XDCAM) or ProRes (Apple's),
  and the sound as PCM.

The captions themselves are written by the mcc module, with a packet for
every frame of the video: ancillary data is per-frame essence, and FFmpeg
stops writing where the caption stream runs out, so a file of only the
frames that hold captions would end at the last one.

`write_video` runs FFmpeg; `command` builds what it runs, which is what
the tests read.
"""

import math
import os
import subprocess
import tempfile

from subtitld.modules import mcc

# What each container holds, and what it does to the picture.
CONTAINERS = {
    'MOV': {'extension': 'mov', 'captions': '608', 'copies': True},
    'MXF': {'extension': 'mxf', 'captions': 'both', 'copies': False},
}

# The picture codecs MXF takes, with what FFmpeg needs for each. MXF holds
# no long-GOP H.264, so one of these is always encoded.
VIDEO_CODECS = {
    'dnxhr': ['-c:v', 'dnxhd', '-profile:v', 'dnxhr_hq', '-pix_fmt', 'yuv422p'],
    'mpeg2': ['-c:v', 'mpeg2video', '-b:v', '50M', '-pix_fmt', 'yuv422p'],
    'prores': ['-c:v', 'prores', '-profile:v', '3', '-pix_fmt', 'yuv422p10le'],
}
DEFAULT_CODEC = 'dnxhr'


def rate_of(fps):
    """The key of mcc.RATES for a video's frames a second, else None: the
    rates captions can be written at, since a caption packet goes with a
    frame."""
    return mcc.rate_for(fps)


def caption_file(path, segments, rate, frames, mode='pop', channel=1, services=None,
                 speaker_changes=False, captions='both'):
    """Write the captions for a video to `path` as MacCaption, a packet for
    every one of its `frames` frames."""
    mcc.save(path, segments, rate=rate, speaker_changes=speaker_changes, captions=captions,
             mode=mode, channel=channel, services=services, frames=frames)


def command(video, output, captions, container='MOV', codec=DEFAULT_CODEC, ffmpeg='ffmpeg'):
    """The FFmpeg command that writes `video` out as `output` with the
    captions in the MacCaption file `captions` inside it."""
    holder = CONTAINERS.get(str(container).upper(), CONTAINERS['MOV'])
    out = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y',
           '-i', str(video), '-f', 'mcc', '-i', str(captions),
           '-map', '0:v:0', '-map', '0:a?', '-map', '1:0']
    if holder['copies']:
        out += ['-c:v', 'copy', '-c:a', 'copy']
    else:
        # MXF takes PCM at 48 kHz alone, which is what broadcast delivers.
        out += VIDEO_CODECS.get(codec, VIDEO_CODECS[DEFAULT_CODEC]) + ['-c:a', 'pcm_s16le', '-ar', '48000']
    return out + ['-c:s', 'copy', str(output)]


def write_video(video, output, segments, fps, duration, container='MOV', codec=DEFAULT_CODEC,
                mode='pop', channel=1, services=None, speaker_changes=False,
                ffmpeg='ffmpeg', startupinfo=None):
    """Write `video` out as `output` with `segments` in it as closed
    captions. Raises ValueError when the video's frame rate is not one
    captions can be written at, and RuntimeError with what FFmpeg said when
    it cannot write the file."""
    rate = rate_of(fps)
    if not rate:
        raise ValueError(f'Closed captions cannot be written at {float(fps or 0):g} frames a second')
    holder = CONTAINERS.get(str(container).upper(), CONTAINERS['MOV'])
    frames = max(1, int(math.ceil(float(duration or 0) * mcc.RATES[rate][2])) + 1)
    handle, captions = tempfile.mkstemp(suffix='.mcc')
    os.close(handle)
    try:
        caption_file(captions, segments, rate, frames, mode=mode, channel=channel, services=services,
                     speaker_changes=speaker_changes, captions=holder['captions'])
        run = subprocess.run(command(video, output, captions, container, codec, ffmpeg),
                             capture_output=True, startupinfo=startupinfo, check=False)
        if run.returncode != 0 or not os.path.isfile(output) or not os.path.getsize(output):
            # The first line FFmpeg writes says what went wrong; the ones
            # after it are what that stopped (a codec that will not open,
            # then every stream that got nothing).
            said = (run.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
            raise RuntimeError(said[0] if said else 'FFmpeg could not write the video')
    finally:
        try:
            os.unlink(captions)
        except OSError:
            pass
    return output
