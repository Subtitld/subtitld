"""A Kdenlive project (.kdenlive) of a project_export.Timeline, in the
format of Kdenlive 23.04 and later (document version 1.1, the one with
sequences), as its source's dev-docs/fileformat.md describes it and
Kdenlive 25.12 writes it.

It is MLT XML, which melt plays as it is, with Kdenlive's own properties
(``kdenlive:``) beside MLT's:

- each file is a clip of the bin (a <chain> listed in the "main_bin"
  playlist), and has an instance of its own for each track it is used on,
  its picture or its sound turned off there (``set.test_image``,
  ``set.test_audio``); the same ``kdenlive:id`` ties them;
- a timeline track is a <tractor> of two <playlist>s (the second for the
  mixes between clips), its clips <entry>s with <blank>s between them;
- the timeline is a sequence: a <tractor> named by its uuid, the built-in
  black track first, then the audio tracks, then the video tracks, with
  the transitions Kdenlive adds itself (``internal_added`` 237) to mix the
  sound and lay the pictures over each other;
- the subtitles are an ``avfilter.subtitles`` filter on the sequence, and
  each subtitle track a file beside the project: ``name.kdenlive.ass``,
  then ``name.kdenlive-1.ass`` and on, listed in the sequence's
  ``subtitlesList``;
- the last <tractor> plays the sequence, for melt.

Kdenlive tells a clip's file by a hash of it (its first and last
megabyte), written as it computes it so that it does not ask to find it.
"""

import hashlib
import os
import time
import uuid as uuidlib
from math import gcd

from lxml import etree

from subtitld.modules import ass, project_export

KDENLIVE_VERSION = '25.12.3'
MLT_VERSION = '7.36.1'
# Kdenlive's own transitions and filters, which it adds and hides.
_INTERNAL = '237'
# Kdenlive's timeline zoom levels: about how many frames 30 pixels hold
# at each (timelinewidget.cpp's comboScale).
_ZOOMS = (1, 2, 4, 8, 15, 30, 50, 75, 100, 150, 200, 300, 500, 800, 1000, 1500, 2000, 3000, 6000, 15000, 30000)


def zoom_for(frames):
    """The zoom level at which `frames` fill about 900 pixels of the timeline."""
    return next((level for level, scale in enumerate(_ZOOMS) if scale >= frames / 30), len(_ZOOMS) - 1)


def file_hash(path):
    """Kdenlive's hash of a file: MD5 of its first and last megabyte, or of
    all of it when it is 2 MB or less."""
    size = os.path.getsize(path)
    with open(path, 'rb') as handle:
        if size > 2000000:
            data = handle.read(1000000)
            handle.seek(size - 1000000)
            data += handle.read()
        else:
            data = handle.read()
    return hashlib.md5(data).hexdigest()


def subtitle_path(project, index):
    """The file of subtitle track `index` of the project at `project`."""
    return f'{project}{f"-{index}" if index else ""}.ass'


class _Writer:
    def __init__(self, timeline):
        self.timeline = timeline
        self.fps = timeline.fps
        self.root = None
        self.ids = 0

    def frames(self, seconds):
        return int(round(float(seconds) * self.fps))

    def clock(self, frames):
        return project_export.clock(frames, self.fps)

    def element(self, tag, attributes=None, properties=None, parent=None):
        node = etree.SubElement(parent if parent is not None else self.root, tag,
                                {key: str(value) for key, value in (attributes or {}).items()})
        for name, value in (properties or {}).items():
            prop = etree.SubElement(node, 'property', name=name)
            prop.text = str(value)
        return node

    def write(self, project):
        timeline = self.timeline
        project = os.path.abspath(project)
        folder = os.path.dirname(project)
        rate = timeline.rate
        self.root = etree.Element('mlt', LC_NUMERIC='C', producer='main_bin', version=MLT_VERSION, root=folder)
        divisor = gcd(timeline.width, timeline.height) or 1
        self.element('profile', {
            'description': f'{timeline.width}x{timeline.height} {float(rate):.2f} fps', 'width': timeline.width,
            'height': timeline.height, 'progressive': 1, 'sample_aspect_num': 1, 'sample_aspect_den': 1,
            'display_aspect_num': timeline.width // divisor, 'display_aspect_den': timeline.height // divisor,
            'frame_rate_num': rate.numerator, 'frame_rate_den': rate.denominator, 'colorspace': 709})
        length = max(1, self.frames(timeline.duration))
        document_uuid = '{' + str(uuidlib.uuid4()) + '}'

        # The bin: a clip for each file.
        bin_clips, kinds = {}, {}
        for track in timeline.tracks:
            for clip in track.clips:
                kinds.setdefault(clip.path, set()).add(track.kind)
        for number, path in enumerate(timeline.media(), start=2):
            video = 'video' in kinds[path]
            audio = project_export.has_audio(path) if video else True
            frames = max(1, self.frames(project_export.media_length(path)))
            properties = {
                'length': frames, 'eof': 'pause', 'resource': path, 'mlt_service': 'avformat-novalidate',
                'seekable': 1, 'audio_index': (1 if video else 0) if audio else -1,
                'video_index': 0 if video else -1, 'kdenlive:folderid': -1, 'kdenlive:id': number,
                'kdenlive:clipname': os.path.basename(path),
                'kdenlive:control_uuid': '{' + str(uuidlib.uuid4()) + '}',
                'kdenlive:clip_type': (0 if audio else 2) if video else 1,
                'kdenlive:file_size': os.path.getsize(path), 'kdenlive:file_hash': file_hash(path)}
            bin_clips[path] = (self.new_id('chain'), properties, frames)
            self.element('chain', {'id': bin_clips[path][0], 'out': self.clock(frames - 1)}, properties)

        black = self.element('producer', {'id': 'black_track', 'in': self.clock(0), 'out': self.clock(length - 1)},
                             {'length': 2147483647, 'eof': 'continue', 'resource': 'black', 'aspect_ratio': 1,
                              'mlt_service': 'color', 'kdenlive:playlistid': 'black_track',
                              'mlt_image_format': 'rgba', 'set.test_audio': 0})

        # The tracks from the bottom up, as Kdenlive orders them: the sound,
        # the last first (A1 is the one under the video), then the pictures.
        tracks = [t for t in timeline.tracks if t.kind == 'audio'][::-1] + \
            [t for t in timeline.tracks if t.kind == 'video']
        track_ids = []
        for track in tracks:
            audio = track.kind == 'audio'
            instances = {}
            playlist = self.element('playlist', {'id': self.new_id('playlist')},
                                    {'kdenlive:audio_track': 1} if audio else {})
            at = 0
            for clip in track.clips:
                if clip.path not in instances:
                    _bin_id, properties, frames = bin_clips[clip.path]
                    instance = dict(properties)
                    instance['set.test_image' if audio else 'set.test_audio'] = 1
                    instance['set.test_audio' if audio else 'set.test_image'] = 0
                    instances[clip.path] = self.new_id('chain')
                    node = self.element('chain', {'id': instances[clip.path], 'out': self.clock(frames - 1)},
                                        instance)
                    self.root.remove(node)
                    playlist.addprevious(node)
                start = max(at, self.frames(clip.at))
                if start > at:
                    self.element('blank', {'length': self.clock(start - at)}, parent=playlist)
                first = self.frames(clip.source_in)
                count = max(1, self.frames(clip.length))
                self.element('entry', {'producer': instances[clip.path], 'in': self.clock(first),
                                       'out': self.clock(first + count - 1)},
                             {'kdenlive:id': bin_clips[clip.path][1]['kdenlive:id']}, parent=playlist)
                at = start + count
            mixes = self.element('playlist', {'id': self.new_id('playlist')}, {'kdenlive:audio_track': 1} if audio else {})
            properties = {'kdenlive:track_name': track.name}
            if audio:
                properties['kdenlive:audio_track'] = 1
            properties.update({'kdenlive:trackheight': 67 if audio else 69, 'kdenlive:timeline_active': 1,
                               'kdenlive:collapsed': 0, 'kdenlive:thumbs_format': '', 'kdenlive:audio_rec': ''})
            tractor = self.element('tractor', {'id': self.new_id('tractor'), 'in': self.clock(0),
                                               'out': self.clock(length - 1)}, properties)
            hide = 'video' if audio else 'audio'
            for part in (playlist, mixes):
                self.element('track', {'hide': hide, 'producer': part.get('id')}, parent=tractor)
            track_ids.append((tractor.get('id'), audio))

        # The subtitle tracks, each a file beside the project.
        listed = []
        for index, (name, _language, cues) in enumerate(timeline.subtitles):
            path = subtitle_path(project, index)
            ass.save(path, cues, timeline.width, timeline.height)
            listed.append({'file': os.path.basename(path), 'id': index, 'name': name})

        # The sequence.
        audio_tracks = sum(1 for _tid, audio in track_ids if audio)
        sequence_properties = {
            'kdenlive:uuid': document_uuid, 'kdenlive:clipname': timeline.title or 'Sequence 1',
            'kdenlive:sequenceproperties.hasAudio': 1 if audio_tracks else 0,
            'kdenlive:sequenceproperties.hasVideo': 1 if len(track_ids) > audio_tracks else 0,
            'kdenlive:sequenceproperties.activeTrack': 0,
            'kdenlive:sequenceproperties.tracksCount': len(track_ids),
            'kdenlive:sequenceproperties.documentuuid': document_uuid,
            'kdenlive:control_uuid': document_uuid, 'kdenlive:duration': self.clock(length),
            'kdenlive:maxduration': length, 'kdenlive:producer_type': 17, 'kdenlive:id': 1,
            'kdenlive:clip_type': 0, 'kdenlive:folderid': 1,
            'kdenlive:sequenceproperties.audioTarget': -1, 'kdenlive:sequenceproperties.videoTarget': -1,
            'kdenlive:sequenceproperties.disablepreview': 0, 'kdenlive:sequenceproperties.position': 0,
            'kdenlive:sequenceproperties.scrollPos': 0, 'kdenlive:sequenceproperties.zonein': 0,
            'kdenlive:sequenceproperties.zoneout': length, 'kdenlive:sequenceproperties.zoom': zoom_for(length),
            'kdenlive:sequenceproperties.groups': '[\n]\n', 'kdenlive:sequenceproperties.guides': '[\n]\n'}
        if listed:
            sequence_properties['kdenlive:sequenceproperties.subtitlesList'] = project_export.write_json(listed)
            sequence_properties['kdenlive:activeSubtitleIndex'] = 0
        sequence = self.element('tractor', {'id': document_uuid, 'in': self.clock(0), 'out': self.clock(length - 1)},
                                sequence_properties)
        self.element('track', {'producer': black.get('id')}, parent=sequence)
        for tractor_id, _audio in track_ids:
            self.element('track', {'producer': tractor_id}, parent=sequence)
        for index, (_tractor_id, audio) in enumerate(track_ids, start=1):
            if audio:
                properties = {'a_track': 0, 'b_track': index, 'mlt_service': 'mix', 'kdenlive_id': 'mix',
                              'internal_added': _INTERNAL, 'always_active': 1, 'accepts_blanks': 1, 'sum': 1}
            else:
                properties = {'a_track': 0, 'b_track': index, 'compositing': 0, 'distort': 0, 'rotate_center': 0,
                              'mlt_service': 'qtblend', 'kdenlive_id': 'qtblend', 'internal_added': _INTERNAL,
                              'always_active': 1}
            self.element('transition', {'id': self.new_id('transition')}, properties, parent=sequence)
        if listed:
            self.element('filter', {'id': self.new_id('filter')}, {
                'mlt_service': 'avfilter.subtitles', 'internal_added': _INTERNAL,
                'av.filename': subtitle_path(project, 0), 'av.alpha': 1}, parent=sequence)

        # The bin, and what the project is.
        main_bin = self.element('playlist', {'id': 'main_bin'}, {
            'kdenlive:folder.-1.1': 'Sequences', 'kdenlive:sequenceFolder': 1,
            'kdenlive:docproperties.audioChannels': 2, 'kdenlive:docproperties.compositing': 1,
            'kdenlive:docproperties.documentid': int(time.time() * 1000),
            'kdenlive:docproperties.kdenliveversion': KDENLIVE_VERSION,
            'kdenlive:docproperties.uuid': document_uuid, 'kdenlive:docproperties.version': '1.1',
            'kdenlive:docproperties.opensequences': document_uuid,
            'kdenlive:docproperties.activetimeline': document_uuid,
            'kdenlive:docproperties.seekOffset': 30000,
            'kdenlive:expandedFolders': '', 'kdenlive:documentnotes': '', 'xml_retain': 1})
        for path in timeline.media():
            chain_id, _properties, frames = bin_clips[path]
            self.element('entry', {'producer': chain_id, 'in': self.clock(0), 'out': self.clock(frames - 1)},
                         parent=main_bin)
        self.element('entry', {'producer': document_uuid, 'in': self.clock(0), 'out': self.clock(length - 1)},
                     parent=main_bin)

        # What melt plays.
        last = self.element('tractor', {'id': self.new_id('tractor'), 'in': self.clock(0),
                                        'out': self.clock(length - 1)}, {'kdenlive:projectTractor': 1})
        self.element('track', {'producer': document_uuid, 'in': self.clock(0), 'out': self.clock(length - 1)},
                     parent=last)
        etree.ElementTree(self.root).write(project, encoding='utf-8', xml_declaration=True, pretty_print=True)
        return project

    def new_id(self, kind):
        self.ids += 1
        return f'{kind}{self.ids}'


def save(path, timeline):
    """Write `timeline` as the Kdenlive project `path`, its subtitle tracks
    beside it."""
    return _Writer(timeline).write(path)
