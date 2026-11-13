"""An Ardour session of a project_export.Timeline, in the format of Ardour
7 and later (session version 7003): a track for each sound track, its
clips as regions, and the subtitles as range markers.

A session is a folder: ``name/name.ardour`` and its sound files in
``name/interchange/name/audiofiles``, one channel each, at the session's
rate. Ardour adds what the session leaves out (the tracks' faders, panners
and the like) when it opens it; what it needs written is:

- the native file format (``<Config>``), by which it tells a session's
  sample format, and the sections it asks for (Bundles, RouteGroups...);
- each sound file a <Source>, with a whole-file <Region> of it;
- each track a <Route> with its inputs and outputs, the outputs wired to
  the Master's inputs, and a <Playlist> of its regions;
- times in superclock ticks ("a" and 282240000 to a second); a region's
  position is where its length is counted from: ``length="aN@aPOS"``.
"""

import os
import subprocess
import uuid as uuidlib
from xml.sax.saxutils import quoteattr

import soundfile

from subtitld.modules import markup, project_export, session

RATE = 48000
SUPERCLOCK = 282240000
SESSION_VERSION = '7003'


def session_paths(path):
    """(folder, name, file) of the session for the chosen `path`: a folder
    of its name beside it, holding it."""
    name = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(os.path.dirname(os.path.abspath(path)), name)
    return folder, name, os.path.join(folder, f'{name}.ardour')


def _ticks(seconds):
    """`seconds` in superclock ticks, on the session's samples."""
    return int(round(float(seconds) * RATE)) * (SUPERCLOCK // RATE)


def _split(path, folder, stem, done):
    """The channels of `path`, each a mono WAV at RATE in `folder`: their
    paths, written once."""
    if path in done:
        return done[path]
    channels = max(1, soundfile.info(path).channels) if path.lower().endswith(('.wav', '.flac')) else None
    if channels is None:
        run = subprocess.run([str(session.FFPROBE_EXECUTABLE), '-v', 'error', '-select_streams', 'a:0',
                              '-show_entries', 'stream=channels', '-of', 'csv=p=0', str(path)],
                             capture_output=True, text=True, startupinfo=session.STARTUPINFO, check=False)
        channels = max(1, min(2, int((run.stdout.strip() or '1').split()[0])))
    channels = min(channels, 2)
    outputs = []
    for channel in range(channels):
        target = os.path.join(folder, f'{stem}{"" if channels == 1 else "-LR"[channel + 1]}.wav')
        run = subprocess.run([str(session.FFMPEG_EXECUTABLE), '-v', 'error', '-y', '-nostdin', '-i', path, '-vn',
                              '-af', f'pan=mono|c0=c{channel}', '-ar', str(RATE), '-c:a', 'pcm_s16le', target],
                             capture_output=True, startupinfo=session.STARTUPINFO, check=False)
        if run.returncode != 0 or not os.path.isfile(target):
            raise RuntimeError(f'Could not convert {os.path.basename(path)} for Ardour')
        outputs.append(target)
    done[path] = outputs
    return outputs


def save(path, timeline):
    """Write `timeline` as an Ardour session for the chosen `path`: the
    folder named after it, beside it. Its path."""
    folder, name, session_file = session_paths(path)
    audio = os.path.join(folder, 'interchange', name, 'audiofiles')
    os.makedirs(audio, exist_ok=True)
    for empty in ('analysis', 'dead', 'export', 'externals', 'peaks', 'plugins',
                  os.path.join('interchange', name, 'midifiles')):
        os.makedirs(os.path.join(folder, empty), exist_ok=True)

    ids = iter(range(100, 10 ** 9))
    lines_sources, lines_regions, lines_playlists, lines_routes = [], [], [], []
    done, sources = {}, {}
    gathered = set()

    def sources_of(path_in, label):
        if path_in not in sources:
            stem = f'{len(sources) + 1:04d} {label}'
            files = _split(path_in, audio, stem, done)
            entries = []
            for file in files:
                source_id = next(ids)
                frames = soundfile.info(file).frames
                lines_sources.append(f'    <Source name={quoteattr(os.path.basename(file))} type="audio" flags="" '
                                     f'id="{source_id}" captured-for="" channel="0" origin="" gain="1"/>')
                entries.append((source_id, frames))
            whole = next(ids)
            lines_regions.append(_region(os.path.splitext(os.path.basename(files[0]))[0], whole, 0,
                                         entries[0][1] * (SUPERCLOCK // RATE), 0, [s for s, _f in entries], True))
            sources[path_in] = entries
            if os.path.dirname(os.path.abspath(path_in)) == os.path.abspath(project_export.media_folder(path)):
                gathered.add(path_in)
        return sources[path_in]

    tracks = [t for t in timeline.tracks if t.kind == 'audio']
    outputs = []
    for order, track in enumerate(tracks, start=1):
        route_id, playlist_id = next(ids), next(ids)
        channels = 1
        regions = []
        for number, clip in enumerate(track.clips, start=1):
            entries = sources_of(clip.path, track.name)
            channels = max(channels, len(entries))
            regions.append((clip, entries, number))
        route_name = track.name
        lines_playlists.append(f'    <Playlist id="{playlist_id}" name={quoteattr(route_name + ".1")} type="audio" '
                               f'orig-track-id="{route_id}" shared-with-ids="" frozen="0" combine-ops="0">')
        for clip, entries, number in regions:
            source_ids = [entries[min(channel, len(entries) - 1)][0] for channel in range(channels)]
            lines_playlists.append('  ' + _region(f'{route_name}.{number}', next(ids), _ticks(clip.source_in),
                                                  _ticks(clip.length), _ticks(clip.at), source_ids, False))
        lines_playlists.append('    </Playlist>')
        ports_in = ''.join(f'<Port name={quoteattr(f"{route_name}/audio_in {n}")} type="audio" direction="Input"/>'
                           for n in range(1, channels + 1))
        ports_out = ''.join(
            f'<Port name={quoteattr(f"{route_name}/audio_out {n}")} type="audio" direction="Output">'
            f'<Connection other="Master/audio_in {n}"/></Port>' for n in (1, 2))
        outputs.append(route_name)
        lines_routes.append(
            f'    <Route version="{SESSION_VERSION}" id="{route_id}" name={quoteattr(route_name)} default-type="audio" '
            f'strict-io="0" active="1" meter-point="MeterPostFader" disk-io-point="DiskIOPreFader" '
            f'meter-type="MeterPeak" audio-playlist="{playlist_id}" alignment-choice="Automatic" mode="Normal">\n'
            f'      <PresentationInfo order="{order}" flags="AudioTrack,OrderSet" color="0"/>\n'
            f'      <IO name={quoteattr(route_name)} id="{next(ids)}" direction="Input" default-type="audio">'
            f'{ports_in}</IO>\n'
            f'      <IO name={quoteattr(route_name)} id="{next(ids)}" direction="Output" default-type="audio">'
            f'{ports_out}</IO>\n'
            f'    </Route>')
    master_in = ''.join(
        f'<Port name="Master/audio_in {n}" type="audio" direction="Input">'
        + ''.join(f'<Connection other={quoteattr(f"{route}/audio_out {n}")}/>' for route in outputs) + '</Port>'
        for n in (1, 2))
    master = (f'    <Route version="{SESSION_VERSION}" id="{next(ids)}" name="Master" default-type="audio" strict-io="1" '
              f'active="1" meter-point="MeterOutput" disk-io-point="DiskIOPreFader" meter-type="MeterK14">\n'
              f'      <PresentationInfo order="0" flags="MasterOut,OrderSet" color="0"/>\n'
              f'      <IO name="Master" id="{next(ids)}" direction="Input" default-type="audio">{master_in}</IO>\n'
              f'      <IO name="Master" id="{next(ids)}" direction="Output" default-type="audio">'
              '<Port name="Master/audio_out 1" type="audio" direction="Output">'
              '<Connection other="system:playback_1"/></Port>'
              '<Port name="Master/audio_out 2" type="audio" direction="Output">'
              '<Connection other="system:playback_2"/></Port></IO>\n'
              f'    </Route>')

    locations = [f'    <Location id="{next(ids)}" name="session" start="a0" end="a{_ticks(timeline.duration)}" '
                 f'flags="IsSessionRange" locked="0" timestamp="0" cue="0"/>']
    if timeline.subtitles:
        _name, _language, cues = timeline.subtitles[0]
        for cue in sorted(cues, key=lambda c: float(c['start'])):
            text = ' '.join(markup.plain(str(cue.get('text') or '')).split())
            if text:
                locations.append(f'    <Location id="{next(ids)}" name={quoteattr(text)} start="a{_ticks(cue["start"])}" '
                                 f'end="a{_ticks(cue["end"])}" flags="IsRangeMarker" locked="0" timestamp="0" cue="0"/>')

    counter = next(ids)
    document = '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Session version="{SESSION_VERSION}" uuid="{uuidlib.uuid4()}" name={quoteattr(name)} sample-rate="{RATE}" '
        f'session-range-is-free="1" id-counter="{counter}" rg-counter="0" name-counter="1" event-counter="0" '
        'vca-counter="1">',
        f'  <ProgramVersion created-with="Subtitld" modified-with="Subtitld"/>',
        '  <Config>',
        '    <Option name="native-file-data-format" value="FormatFloat"/>',
        '    <Option name="native-file-header-format" value="WAVE"/>',
        '  </Config>',
        '  <Metadata/>',
        '  <Sources>', *lines_sources, '  </Sources>',
        '  <Regions>', *lines_regions, '  </Regions>',
        '  <Locations>', *locations, '  </Locations>',
        '  <Bundles/>',
        '  <VCAManager/>',
        '  <Routes>', master, *lines_routes, '  </Routes>',
        '  <Playlists>', *lines_playlists, '  </Playlists>',
        '  <UnusedPlaylists/>',
        '  <RouteGroups/>',
        f'  <TempoMap superclocks-per-second="{SUPERCLOCK}">',
        '    <Tempos><Tempo npm="120" enpm="120" note-type="4" type="Constant" locked-to-meter="0" continuing="0" '
        'active="1" sclock="0" quarters="0:0" bbt="1|1|0" omega="0"/></Tempos>',
        '    <Meters><Meter note-value="4" divisions-per-bar="4" sclock="0" quarters="0:0" bbt="1|1|0"/></Meters>',
        '    <MusicTimes/>',
        '  </TempoMap>',
        '</Session>', ''])
    with open(session_file, 'w', encoding='utf-8') as handle:
        handle.write(document)
    # The speeches were copied beside the chosen path; the session holds
    # its own.
    for copied in gathered:
        if os.path.isfile(copied):
            os.remove(copied)
    media = project_export.media_folder(path)
    if os.path.isdir(media) and not os.listdir(media):
        os.rmdir(media)
    return session_file


def _region(name, region_id, start, length, position, source_ids, whole):
    """A <Region> of `length` ticks from `start` ticks into its sources,
    at `position` ticks."""
    sources = ' '.join(f'source-{n}="{s}" master-source-{n}="{s}"' for n, s in enumerate(source_ids))
    return (f'    <Region name={quoteattr(name)} muted="0" opaque="1" locked="0" video-locked="0" automatic="0" '
            f'whole-file="{1 if whole else 0}" import="0" external="0" sync-marked="0" left-of-split="0" '
            f'right-of-split="0" hidden="0" position-locked="0" valid-transients="0" start="a{start}" '
            f'length="a{length}@a{position}" sync-position="a0" ancestral-start="a0" ancestral-length="a0@a0" '
            f'stretch="1" shift="1" layering-index="0" tags="" contents="0" envelope-active="0" default-fade-in="0" '
            f'default-fade-out="0" fade-in-active="1" fade-out-active="1" fade-before-fx="0" scale-amplitude="1" '
            f'id="{region_id}" type="audio" first-edit="nothing" {sources} channels="{len(source_ids)}"/>')
