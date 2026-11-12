"""WebVTT and TTML in fragmented MP4 (ISO/IEC 14496-30): the CMAF track
files written (boxes, fragments, samples cut where cues start and end,
empty cues, one TTML document a fragment in the profile asked for), read
back, other files' ways (a plain sample table, documents timed from their
sample, media segments beside their initialization), DASH manifests both
ways, HLS's fMP4 segments, what is not an MP4, file_io, the export dialog,
and FFmpeg's and GStreamer's view of the files when they are here.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, hls, isobmff_text, session, ttml

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(segments):
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ()) for s in segments]


def find(data, *path):
    """(start, end) of the payload down `path` of box types."""
    start, end = 0, len(data)
    for kind in path:
        start, end = next((payload, stop) for name, payload, stop in isobmff_text.boxes(data, start, end)
                          if name == kind)
    return start, end


def top_level(data):
    return [name.decode() for name, _start, _end in isobmff_text.boxes(data)]


folder = Path(tempfile.mkdtemp())
SUBTITLES = [
    {'start': 1.0, 'end': 3.0, 'text': 'Hello <i>there</i>'},
    {'start': 2.0, 'end': 8.5, 'text': 'Across\nthe edge', 'speaker': 'Ann'},
    {'start': 13.0, 'end': 14.0, 'text': '{\\an8}<font color="#ffd700">Gold</font> on top'},
]
# As written without the speakers.
UNSPOKEN = [{key: value for key, value in segment.items() if key != 'speaker'} for segment in SUBTITLES]

print('WebVTT written')
init, fragments = isobmff_text.write(SUBTITLES, codec='wvtt', total=20, voices=True, language='pt-br')
start, end = find(init, b'ftyp')
check('ftyp: CMAF, the WebVTT brand', [init[at:at + 4] for at in range(start + 8, end, 4)],
      [b'iso6', b'cmfc', b'cwvt', b'dash'])
check('the movie: one track, "text" handler, a null media header, a wvtt sample entry',
      (init[find(init, b'moov', b'trak', b'mdia', b'hdlr')[0] + 8:][:4],
       bool(find(init, b'moov', b'trak', b'mdia', b'minf', b'nmhd')),
       top_level(init[slice(*find(init, b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'))][8:])),
      (b'text', True, ['wvtt']))
tracks, defaults = isobmff_text.movie(init)
check('read back: its clock, language, header with the STYLE block', (tracks[1]['timescale'], tracks[1]['language'],
      tracks[1]['config'].split('\n')[:3]), (1000, 'pt-BR', ['WEBVTT', '', 'STYLE']))
check('a fragment each 6 s, for the 20 s of the programme', len(fragments), 4)
samples = []
for fragment in fragments:
    found, _clock = isobmff_text.fragment_samples(fragment, 1)
    samples += found
check('samples follow one another with no gap, cut where each cue starts and ends and at each fragment',
      [(time, duration) for time, duration, _data in samples],
      [(0, 1000), (1000, 1000), (2000, 1000), (3000, 3000), (6000, 2500), (8500, 3500), (12000, 1000),
       (13000, 1000), (14000, 4000), (18000, 2000)])
check('a time with nothing to show is an empty cue', top_level(samples[0][2]), ['vtte'])
check('two cues at once: two vttc', top_level(samples[2][2]), ['vttc', 'vttc'])
check('a cue: its settings and its text', [name.decode() for name, _start, _end in
                                            isobmff_text.boxes(samples[7][2], 8)], ['sttg', 'payl'])
check('the moof: tfdt and a trun with the data offset', all(
    find(fragment, b'moof', b'traf', b'tfdt') and find(fragment, b'moof', b'traf', b'trun') for fragment in fragments),
      True)
isobmff_text.save(str(folder / 'w.cmft'), SUBTITLES, codec='wvtt', total=20, voices=True, language='pt-br')
back, settings = isobmff_text.read(str(folder / 'w.cmft'))
check('read back: each cue once, whole again', cues(back), cues(SUBTITLES))
check('its settings', settings, {'codec': 'wvtt', 'language': 'pt-BR', 'profile': '', 'segment_duration': 6.0})
check('read back in the 4 s it was cut into', cues(isobmff_text.read_data(b''.join(
    (lambda written: [written[0]] + written[1])(isobmff_text.write(SUBTITLES, duration=4))))[0]), cues(SUBTITLES)[:1] +
    [(2.0, 8.5, 'Across\nthe edge')] + cues(SUBTITLES)[2:])

print('TTML written')
init, fragments = isobmff_text.write(SUBTITLES, codec='stpp', total=20, language='en')
entry = find(init, b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd')
check('a "subt" handler, the subtitle media header, a stpp sample entry',
      (init[find(init, b'moov', b'trak', b'mdia', b'hdlr')[0] + 8:][:4],
       bool(find(init, b'moov', b'trak', b'mdia', b'minf', b'sthd')), top_level(init[entry[0] + 8:entry[1]])),
      (b'subt', True, ['stpp']))
tracks, _defaults = isobmff_text.movie(init)
check("its namespace, and the MIME type with IMSC 1.1 Text's code", (tracks[1]['namespace'], tracks[1]['mime']),
      ('http://www.w3.org/ns/ttml', 'application/ttml+xml;codecs=im2t'))
documents = [isobmff_text.fragment_samples(fragment, 1)[0] for fragment in fragments]
check('one document a fragment, as long as it', [[(time, duration) for time, duration, _data in found]
                                                 for found in documents],
      [[(0, 6000)], [(6000, 6000)], [(12000, 6000)], [(18000, 2000)]])
texts = [found[0][2].decode('utf-8') for found in documents]
check('each a whole IMSC 1.1 document, its cues cut to its time',
      [(ttml.check(text, 'imsc1.1'), cues(ttml.parse(text))) for text in texts[:3]],
      [([], [(1.0, 3.0, 'Hello <i>there</i>'), (2.0, 6.0, 'Across\nthe edge')]),
       ([], [(6.0, 8.5, 'Across\nthe edge')]), ([], [(13.0, 14.0, '{\\an8}<font color="#ffd700">Gold</font> on top')])])
check('one with nothing to show has no cue', cues(ttml.parse(texts[3])), [])
isobmff_text.save(str(folder / 's.cmft'), SUBTITLES, codec='stpp', total=20, voices=True,
                  look='preview', preview={'font_color': '#00ff00'})
back, settings = isobmff_text.read(str(folder / 's.cmft'))
check('read back, styling and speakers kept, each once', cues(back), cues(SUBTITLES))
check('its settings', settings, {'codec': 'stpp', 'language': 'en', 'profile': 'imsc1.1', 'segment_duration': 6.0})
init, fragments = isobmff_text.write(SUBTITLES, codec='stpp', profile='ebu-tt-d')
tracks, _defaults = isobmff_text.movie(init)
document = isobmff_text.fragment_samples(fragments[0], 1)[0][0][2].decode()
check('EBU-TT-D: its code, and documents that conform to it',
      (tracks[1]['mime'], 'urn:ebu:tt:distribution:2018-04' in document, ttml.check(document, 'ebu-tt-d')),
      ('application/ttml+xml;codecs=etd1', True, []))
check('IMSC 1.0.1', isobmff_text.movie(isobmff_text.write(SUBTITLES, codec='stpp', profile='imsc1')[0])[0][1]['mime'],
      'application/ttml+xml;codecs=im1t')
check('the codecs strings', [isobmff_text.codecs_string('wvtt'), isobmff_text.codecs_string('stpp', 'imsc1'),
                             isobmff_text.codecs_string('stpp', 'ebu-tt-d')], ['wvtt', 'stpp.ttml.im1t', 'stpp.ttml.etd1'])

print("other files' ways")
# A document counting from its sample's start, as some packagers wrote them.
relative = '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="00:00:01.000" end="00:00:02.000">Later</p>' \
           '</div></body></tt>'
init, _fragments = isobmff_text.write([], codec='stpp')
fragment = isobmff_text._fragment(1, 60000, [(6000, relative.encode())])
check('a document timed from its sample: moved to it', cues(isobmff_text.read_data(init + fragment)[0]),
      [(61.0, 62.0, 'Later')])
absolute = relative.replace('00:00:01.000', '00:01:01.000').replace('00:00:02.000', '00:01:09.000')
check('one on the track clock: cut to its sample', cues(isobmff_text.read_data(
    init + isobmff_text._fragment(1, 60000, [(6000, absolute.encode())]))[0]), [(61.0, 66.0, 'Later')])
# Overlapping cues as another packager may cut them, the identifier kept.
init, _fragments = isobmff_text.write([], codec='wvtt')


def vttc(payload, identifier=''):
    return isobmff_text._box(b'vttc', *((isobmff_text._box(b'iden', identifier.encode()),) if identifier else ()),
                             isobmff_text._box(b'payl', payload.encode()))


fragment = isobmff_text._fragment(1, 0, [(500, isobmff_text._box(b'vtte')), (500, vttc('One', '1')),
                                         (1000, vttc('One', '1') + vttc('Two', '2')), (1000, vttc('Two', '2'))])
check("another packager's samples, identifiers and all", cues(isobmff_text.read_data(init + fragment)[0]),
      [(0.5, 2.0, 'One'), (1.0, 3.0, 'Two')])
dash = folder / 'dash'
dash.mkdir()
isobmff_text.save_dash(str(dash / 'show.mpd'), SUBTITLES, codec='wvtt', total=20)
segment = dash / 'show_segments' / '00002.m4s'
check('a media segment, read with the initialization segment beside it',
      cues(isobmff_text.read(str(segment))[0]), [(6.0, 8.5, 'Across\nthe edge')])
(folder / 'notes.cmft').write_bytes(b'Just some notes, no boxes in them at all.')
for name, data, said in (('not an MP4', b'Just some notes, no boxes in them at all.', 'not an MP4'),
                         ('cut short', (folder / 'w.cmft').read_bytes()[:300], 'cut short'),
                         ('a video track only', isobmff_text._box(b'ftyp', b'isom', b'\0\0\0\0') + isobmff_text._box(
                             b'moov', isobmff_text._box(b'trak', isobmff_text._full(b'tkhd', 0, 3, b'\0' * 80))),
                          'no WebVTT or TTML')):
    (folder / 'bad.cmft').write_bytes(data)
    try:
        isobmff_text.read(str(folder / 'bad.cmft'))
        check(name, 'read', 'ValueError')
    except ValueError as error:
        check(f'{name} says so', said in str(error), True)
check('the track in the language asked for, else the first',
      [isobmff_text._choose({1: {'id': 1, 'codec': 'wvtt', 'language': 'eng'},
                             2: {'id': 2, 'codec': 'stpp', 'language': 'por'},
                             3: {'id': 3, 'codec': 'avc1', 'language': 'fra'}}, language)['id']
       for language in ('pt-br', 'fr-fr', None)], [2, 1, 1])

print('DASH')
back, settings = isobmff_text.read(str(dash / 'show.mpd'))
check('the manifest written, read back', (cues(back), settings['segment_duration']), (cues(UNSPOKEN), 6.0))
manifest = (dash / 'show.mpd').read_text()
check('its template and codecs', ('media="show_segments/$Number%05d$.m4s"' in manifest, 'codecs="wvtt"' in manifest,
                                  sorted(os.listdir(dash / 'show_segments'))[:2]), (True, True, ['00001.m4s', '00002.m4s']))
isobmff_text.save_dash(str(dash / 'show.mpd'), SUBTITLES, codec='stpp', profile='ebu-tt-d', duration=10, total=20)
manifest = (dash / 'show.mpd').read_text()
check('EBU-TT-D for DVB-DASH: its profile, a second save taking the first one\'s segments',
      ('urn:dvb:dash:profile:dvb-dash:2014' in manifest, 'codecs="stpp.ttml.etd1"' in manifest,
       sorted(os.listdir(dash / 'show_segments'))), (True, True, ['00001.m4s', '00002.m4s', 'init.mp4']))
check('read back', cues(isobmff_text.read(str(dash / 'show.mpd'))[0]), cues(UNSPOKEN))
# A manifest of another packager: two languages, the template on the
# adaptation set, a SegmentTimeline and $Time$, a BaseURL.
os.makedirs(dash / 'media' / 'pt', exist_ok=True)
for number, fragment in enumerate(isobmff_text.write(SUBTITLES, codec='stpp', duration=10, total=20)[1]):
    (dash / 'media' / 'pt' / f'{number * 10000}.m4s').write_bytes(fragment)
(dash / 'media' / 'pt' / 'init.mp4').write_bytes(isobmff_text.write([], codec='stpp')[0])
(dash / 'media' / 'en.vtt').write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSidecar\n')
(dash / 'other.mpd').write_text(
    '<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT20S">\n'
    '<BaseURL>media/</BaseURL><Period start="PT0S">\n'
    '<AdaptationSet contentType="video" mimeType="video/mp4"><Representation id="v" codecs="avc1"/></AdaptationSet>\n'
    '<AdaptationSet contentType="text" mimeType="text/vtt" lang="en"><Representation id="en"><BaseURL>en.vtt</BaseURL>'
    '</Representation></AdaptationSet>\n'
    '<AdaptationSet contentType="text" mimeType="application/mp4" lang="pt">\n'
    '<SegmentTemplate timescale="1000" initialization="$RepresentationID$/init.mp4" media="$RepresentationID$/$Time$.m4s">'
    '<SegmentTimeline><S t="0" d="10000" r="1"/></SegmentTimeline></SegmentTemplate>\n'
    '<Representation id="pt" codecs="stpp.ttml.im2t" bandwidth="1000"/></AdaptationSet>\n</Period></MPD>\n')
back, settings = isobmff_text.read_dash(str(dash / 'other.mpd'), language='pt-br')
check("another packager's: the language asked, $Time$ from a SegmentTimeline, a BaseURL",
      (cues(back), settings['language']), (cues(UNSPOKEN), 'pt'))
back, settings = isobmff_text.read_dash(str(dash / 'other.mpd'))
check('the first text adaptation set: a WebVTT file by itself', (cues(back), settings['language']),
      ([(1.0, 2.0, 'Sidecar')], 'en'))
(dash / 'none.mpd').write_text('<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet mimeType="video/mp4">'
                               '<Representation id="v"/></AdaptationSet></Period></MPD>')
try:
    isobmff_text.read(str(dash / 'none.mpd'))
    check('a manifest with no subtitles says so', 'read', 'ValueError')
except ValueError as error:
    check('a manifest with no subtitles says so', 'no subtitles' in str(error), True)

print("HLS's fragmented MP4")
init, fragments = isobmff_text.write(SUBTITLES, codec='stpp', total=20)
(dash / 'imsc').mkdir()
(dash / 'imsc' / 'init.mp4').write_bytes(init)
for number, fragment in enumerate(fragments):
    (dash / 'imsc' / f'{number}.m4s').write_bytes(fragment)
(dash / 'imsc' / 'subs.m3u8').write_text('#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:6\n#EXT-X-MAP:URI="init.mp4"\n'
                                         + ''.join(f'#EXTINF:{6 if number < 3 else 2}.0,\n{number}.m4s\n'
                                                   for number in range(4)) + '#EXT-X-ENDLIST\n')
back, settings = hls.read(str(dash / 'imsc' / 'subs.m3u8'))
check('IMSC in fMP4 segments an HLS playlist lists', (len(back), back[1]['end'], settings['codec']), (3, 8.5, 'stpp'))

print('through file_io')
session.FORMAT = {}
session.SUBTITLE['language'] = 'en-us'
opened, fmt = file_io.process_subtitles_file(str(folder / 'w.cmft'))
check('a CMAF track opened: its language the project\'s, what it carries kept',
      (fmt, len(opened), session.SUBTITLE['language'],
       {key: session.FORMAT['options'].get(key) for key in ('mp4_text', 'segment_duration')}),
      ('CMFT', 3, 'pt-br', {'mp4_text': 'wvtt', 'segment_duration': 6.0}))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(dash / 'show.mpd'))
check('a DASH manifest opened', (fmt, len(opened), session.FORMAT['options'].get('mp4_text'),
                                 session.FORMAT['options'].get('segment_duration')), ('DASH', 3, 'ebu-tt-d', 10.0))
session.SUBTITLE['segments'] = [dict(segment) for segment in SUBTITLES]
session.SUBTITLE['language'] = 'en-us'
session.VIDEO = {'filepath': '/videos/show.mp4', 'duration': 30.0}
session.FORMAT = {'options': {'mp4_text': 'imsc1', 'segment_duration': 10.0}}
file_io.save_file(str(folder / 'saved.cmft'), 'CMFT')
back, settings = isobmff_text.read(str(folder / 'saved.cmft'))
check('saved as IMSC 1.0.1 in 10 s fragments, for the 30 s video, in English',
      (len(back), settings['profile'], settings['segment_duration'], settings['language'],
       isobmff_text.movie((folder / 'saved.cmft').read_bytes())[0][1]['language']),
      (3, 'imsc1', 10.0, 'en-US', 'en-US'))
session.FORMAT = {'options': {'mp4_text': 'wvtt', 'speakers': 'voice'}}
file_io.save_file(str(folder / 'saved.mpd'), 'DASH')
check('saved as DASH, WebVTT, voices', (cues(isobmff_text.read(str(folder / 'saved.mpd'))[0]),
                                        len(os.listdir(folder / 'saved_segments'))), (cues(SUBTITLES), 6))
try:
    file_io.process_subtitles_file(str(folder / 'notes.cmft'))
    check('a .cmft that is not one', 'opened', 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('a .cmft that is not one', 'CorruptedProjectFileError', 'CorruptedProjectFileError')
check('the extensions', [session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[name]['extensions'] for name in ('CMFT', 'DASH')],
      [['cmft'], ['mpd']])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('CMFT')
panel._refresh_options()
options = panel.get_config()['options']
check('CMFT: WebVTT in 6 s fragments by default, voice tags, no timestamp map',
      (panel.carriage_combo.isHidden(), panel.mpegts_combo.isHidden(), panel.ass_options.isHidden(),
       options.get('mp4_text'), options.get('segment_duration'), panel.speakers_combo.combobox.findData('voice') >= 0),
      (False, True, True, 'wvtt', 6.0, True))
panel.carriage_combo.combobox.setCurrentIndex(panel.carriage_combo.combobox.findData('ebu-tt-d'))
options = panel.get_config()['options']
check('TTML: its look offered, the profile chosen and remembered',
      (panel.ass_options.isHidden(), panel.profile_combo.isHidden(), options.get('mp4_text'), 'look' in options,
       _SubtitlesPanel().carriage_combo.combobox.currentData()), (False, True, 'ebu-tt-d', True, 'ebu-tt-d'))
panel.format_combo.setCurrentText('DASH')
panel._refresh_options()
check('DASH the same', (panel.carriage_combo.isHidden(), panel.get_config()['options'].get('mp4_text')),
      (False, 'ebu-tt-d'))
panel.format_combo.setCurrentText('HLS')
panel._refresh_options()
check('not for HLS', (panel.carriage_combo.isHidden(), panel.mpegts_combo.isHidden()), (True, False))

print('FFmpeg and GStreamer')
if shutil.which('ffprobe') and shutil.which('ffmpeg'):
    def probe(path):
        out = subprocess.run(['ffprobe', '-v', 'quiet', '-show_entries', 'stream=codec_name,codec_tag_string,duration'
                              ':stream_tags=language', '-of', 'compact=p=0:nk=1', str(path)],
                             capture_output=True, text=True).stdout.strip()
        return out
    check('FFmpeg: a wvtt track in Portuguese, 20 s long (FFmpeg has no WebVTT-in-MP4 decoder: "unknown")',
          probe(folder / 'w.cmft').split('|')[1:], ['wvtt', '20.000000', 'por'])
    check('FFmpeg: a stpp track, as TTML', probe(folder / 's.cmft').split('|'), ['ttml', 'stpp', '20.000000', 'eng'])
    packets = subprocess.run(['ffprobe', '-v', 'quiet', '-show_entries', 'packet=pts,duration', '-of', 'csv=p=0',
                              str(folder / 'w.cmft')], capture_output=True, text=True).stdout.split()
    check("FFmpeg: the samples' times and durations", packets[:5],
          ['0,1000', '1000,1000', '2000,1000', '3000,3000', '6000,2500'])
    dumped = folder / 'documents.bin'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'quiet', '-y', '-i', str(folder / 's.cmft'), '-map', '0', '-c', 'copy',
                    '-f', 'data', str(dumped)], check=True)
    check('FFmpeg: the samples it takes out are the four TTML documents', dumped.read_bytes().count(b'<?xml'), 4)
    made = folder / 'by_ffmpeg.mp4'
    (folder / 'in.vtt').write_text('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nOne\n\n00:00:05.000 --> 00:00:08.500\nTwo\n')
    if subprocess.run(['ffmpeg', '-nostdin', '-v', 'quiet', '-y', '-i', str(folder / 'in.vtt'), '-c:s', 'ttml',
                       str(made)]).returncode == 0:
        check("FFmpeg's own stpp MP4 (a plain sample table) read", cues(isobmff_text.read(str(made))[0]),
              [(1.0, 3.0, 'One'), (5.0, 8.5, 'Two')])
else:
    print('  [skip] no FFmpeg')
if shutil.which('gst-launch-1.0'):
    result = subprocess.run(['gst-launch-1.0', '-q', 'filesrc', f'location={folder / "w.cmft"}', '!', 'qtdemux', '!',
                             'fakesink', 'dump=true'], capture_output=True, text=True, timeout=60)
    # fakesink's dump: an offset, an address, then sixteen bytes in hex.
    text = bytes.fromhex(''.join(line.split('): ', 1)[1][:48] for line in result.stdout.split('\n')
                                 if line.startswith('0000') and '): ' in line)).decode('utf-8', 'replace')
    check("GStreamer's qtdemux makes WebVTT cues of the samples", ('00:00:02.000 -->' in text, 'Across' in text),
          (True, True))
else:
    print('  [skip] no GStreamer')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
