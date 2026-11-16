"""An Audacity project of a project_export.Timeline: the sound tracks, a
track for each, and the subtitles as label tracks.

Audacity 3's own projects (.aup3) are SQLite databases of its sample
blocks; it still opens the projects of Audacity 2 (.aup), XML, through
its importer (modules/import-export/mod-aup), and that is what this
writes. Their clips can be read from sound files beside them rather
than from blocks of their own (``pcmaliasblockfile``), so the project
is this one XML file and the folder of sound files beside it. Audacity
reads those with libsndfile and does not resample them, so every file
is written there as WAV at the project's rate, the video's sound too.

- each track is a <wavetrack> (two, linked, when it is stereo) and each
  of its clips a <waveclip> at its offset, whose <sequence> is made of
  <waveblock>s of at most MAX_SAMPLES samples, each read from its part
  of the file; every block has a summary file name of its own, as the
  importer tells blocks apart by it (they are not written: Audacity
  makes its own);
- each subtitle track is a <labeltrack>, a <label> for each subtitle.
"""

import os
import subprocess

import soundfile

from subtitld.modules import markup, project_export, session

RATE = 48000
# The longest a block may be (Audacity 2's own limit).
MAX_SAMPLES = 262144
# Audacity's sampleFormat for 32-bit float.
FLOAT_SAMPLE = 262159


def _to_wav(path, folder, done):
    """`path` as a WAV at RATE in `folder` (its sound, for a video): the
    path of that file, written once."""
    if path in done:
        return done[path]
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    target = os.path.join(folder, f'{stem}.wav')
    written = target + '.part.wav'
    run = subprocess.run([str(session.FFMPEG_EXECUTABLE), '-v', 'error', '-y', '-nostdin', '-i', path, '-vn',
                          '-ar', str(RATE), '-c:a', 'pcm_s16le', written], capture_output=True,
                         startupinfo=session.STARTUPINFO, check=False)
    if run.returncode != 0 or not os.path.isfile(written):
        raise RuntimeError(f'Could not convert {os.path.basename(path)} for Audacity')
    # A copy made for the project is replaced by its WAV.
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(folder):
        os.remove(path)
    os.replace(written, target)
    done[path] = target
    return target


def save(path, timeline):
    """Write `timeline`'s sound and subtitles as the Audacity project `path`
    (.aup), its WAV files in the media folder beside it."""
    from xml.sax.saxutils import quoteattr
    folder = project_export.media_folder(path)
    done = {}
    channels = {}
    for track in timeline.tracks:
        if track.kind != 'audio':
            continue
        for clip in track.clips:
            clip.path = _to_wav(clip.path, folder, done)
            if clip.path not in channels:
                channels[clip.path] = soundfile.info(clip.path).channels

    # The project's data folder, which the importer asks to find: the
    # folder of its sound files.
    os.makedirs(folder, exist_ok=True)
    lines = ['<?xml version="1.0" standalone="no" ?>',
             '<!DOCTYPE project PUBLIC "-//audacityproject-1.3.0//DTD//EN" '
             '"http://audacity.sourceforge.net/xml/audacityproject-1.3.0.dtd" >',
             f'<project xmlns="http://audacity.sourceforge.net/xml/" projname={quoteattr(os.path.basename(folder))} '
             f'version="1.3.0" audacityversion="2.4.2" sel0="0.0" sel1="0.0" vpos="0" h="0.0" zoom="86.1328125" '
             f'rate="{RATE}.0" snapto="off" selectionformat="hh:mm:ss + milliseconds">']
    blocks = 0
    for track in timeline.tracks:
        if track.kind != 'audio':
            continue
        stereo = any(channels[clip.path] > 1 for clip in track.clips)
        for channel in (0, 1) if stereo else (0,):
            lines.append(f'\t<wavetrack name={quoteattr(track.name)} channel="{channel if stereo else 2}" '
                         f'linked="{1 if stereo and channel == 0 else 0}" mute="0" solo="0" height="150" '
                         f'minimized="0" isSelected="0" rate="{RATE}" gain="1.0" pan="0.0" '
                         f'sampleformat="{FLOAT_SAMPLE}">')
            for clip in track.clips:
                first = int(round(clip.source_in * RATE))
                total = soundfile.info(clip.path).frames
                count = max(0, min(int(round(clip.length * RATE)), total - first))
                if count <= 0:
                    continue
                source_channel = channel if channels[clip.path] > 1 else 0
                lines.append(f'\t\t<waveclip offset="{clip.at:.8f}" colorindex="0">')
                lines.append(f'\t\t\t<sequence maxsamples="{MAX_SAMPLES}" sampleformat="{FLOAT_SAMPLE}" '
                             f'numsamples="{count}">')
                for start in range(0, count, MAX_SAMPLES):
                    size = min(MAX_SAMPLES, count - start)
                    blocks += 1
                    lines.append(f'\t\t\t\t<waveblock start="{start}">')
                    lines.append(f'\t\t\t\t\t<pcmaliasblockfile summaryfile="e{blocks:07x}.auf" '
                                 f'aliasfile={quoteattr(clip.path)} aliasstart="{first + start}" '
                                 f'aliaslen="{size}" aliaschannel="{source_channel}" min="-1.0" max="1.0" '
                                 f'rms="0.1"/>')
                    lines.append('\t\t\t\t</waveblock>')
                lines.append('\t\t\t</sequence>')
                lines.append('\t\t\t<envelope numpoints="0"/>')
                lines.append('\t\t</waveclip>')
            lines.append('\t</wavetrack>')
    for name, _language, cues in timeline.subtitles:
        labels = [cue for cue in sorted(cues, key=lambda c: float(c['start']))
                  if markup.plain(str(cue.get('text') or '')).strip()]
        lines.append(f'\t<labeltrack name={quoteattr(name)} numlabels="{len(labels)}" height="73" minimized="0" '
                     f'isSelected="0">')
        for cue in labels:
            title = ' '.join(markup.plain(str(cue['text'])).split())
            lines.append(f'\t\t<label t="{float(cue["start"]):.8f}" t1="{float(cue["end"]):.8f}" '
                         f'title={quoteattr(title)}/>')
        lines.append('\t</labeltrack>')
    lines.append('</project>')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return path
