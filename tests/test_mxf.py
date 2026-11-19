"""Subtitles in MXF: SMPTE ST 429-5 timed text track files (a SMPTE DCP's
subtitles, with PNGs as ancillary resources; IMF's IMSC 1.1) and EBU STL
in MXF (SMPTE ST 2075). The KLV structure written is checked against the
standards and asdcplib's layouts (partitions, primer, sets, descriptors,
index, RIP); an STL in MXF built by hand, another way than Subtitld
writes one, is read; files are opened and saved through file_io and the
dialog; and FFmpeg, where it is installed, probes what is written and
takes the XML out of it byte for byte.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, struct, subprocess, sys, tempfile, uuid
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import dcp, ebustl, file_io, mxf, session, ttml

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def layout(data):
    """The packets' keys, named."""
    names = {mxf.HEADER_CLOSED_COMPLETE: 'header', mxf.BODY_CLOSED_COMPLETE: 'body', mxf.GENERIC_STREAM_PARTITION: 'stream',
             mxf.FOOTER_COMPLETE: 'footer', mxf.PRIMER: 'primer', mxf.RIP: 'rip', mxf.INDEX_SEGMENT: 'index',
             mxf.TIMED_TEXT_ELEMENT: 'xml', mxf.GENERIC_STREAM_DATA: 'data'}
    return [names.get(key, 'set') for _offset, key, _value in mxf.packets(data)]


def squeeze(names):
    out = []
    for name in names:
        if not (out and out[-1] == name == 'set'):
            out.append(name)
    return out


def rip(data):
    count = (struct.unpack('>I', data[-4:])[0] - 16 - 4 - 4) // 12
    body = data[-4 - count * 12:-4]
    return [struct.unpack('>IQ', body[n * 12:n * 12 + 12]) for n in range(count)]


def sets_of(found, kind):
    return [items for set_kind, items in found['sets'] if set_kind == kind]


segments = [{'start': 1.0, 'end': 2.5, 'text': 'Hello'}, {'start': 3.0, 'end': 4.0, 'text': '<i>World</i>'}]
folder = Path(tempfile.mkdtemp())

print('a DCP track file')
reel = dcp.write(segments, fps=24, title='Film', identifier='0b0f3a6e-2f61-4a0e-9d55-6b6f3d1e0a11').encode()
png = b'\x89PNG\r\n\x1a\n' + bytes(40)
font = uuid.UUID('6f1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b')
picture = uuid.UUID('11111111-2222-4333-8444-555555555555')
track_file = mxf.write_timed_text(reel, [(font, 'application/x-font-opentype', b'OTTO' + bytes(20)), (picture, 'image/png', png)],
                                  (24, 1), 96, dcp.SMPTE_NAMESPACE, uuid.UUID('0b0f3a6e-2f61-4a0e-9d55-6b6f3d1e0a11'), mxf.DCP)
check("asdcplib's layout: header, the XML in a body partition, a stream partition a resource, the index in the footer, the RIP",
      squeeze(layout(track_file)), ['header', 'primer', 'set', 'body', 'xml', 'stream', 'data', 'stream', 'data', 'footer',
                                    'index', 'rip'])
found = mxf.structure(track_file)
offsets = [offset for offset, key, _value in mxf.packets(track_file) if mxf._is_partition(key)]
check('the RIP: every partition, the streams by their SIDs (10 and up, as asdcplib numbers them)', rip(track_file),
      list(zip([0, 1, 10, 11, 0], offsets)))
check('the partitions: OP-Atom, MXF 2004, the GC and timed text containers, the footer where they say',
      [(p['pattern'] == mxf.OP_ATOM, p['minor'], p['containers'] == [mxf.GC_MULTI, mxf.TIMED_TEXT_CONTAINER], p['footer'])
       for p in found['partitions']], [(True, 2, True, offsets[-1])] * 5)
descriptor = sets_of(found, mxf.TIMED_TEXT_DESCRIPTOR)[0]
check("the TimedTextDescriptor: the reel's Id, UTF-8, ST 428-7's namespace, the rate and duration",
      (descriptor['ResourceID'], descriptor['UCSEncoding'], descriptor['NamespaceURI'], descriptor['SampleRate'],
       descriptor['ContainerDuration'], descriptor['EssenceContainer'] == mxf.TIMED_TEXT_CONTAINER),
      (uuid.UUID('0b0f3a6e-2f61-4a0e-9d55-6b6f3d1e0a11'), 'UTF-8', dcp.SMPTE_NAMESPACE, (24, 1), 96, True))
check('a sub-descriptor a resource: its UUID, MIME type and SID, referred to by the descriptor',
      [(s['AncillaryResourceID'], s['MIMEMediaType'], s['BodySID'], s['InstanceUID'] in descriptor['SubDescriptors'])
       for s in sets_of(found, mxf.TIMED_TEXT_RESOURCE)],
      [(font, 'application/x-font-opentype', 10, True), (picture, 'image/png', 11, True)])
tracks = sets_of(found, mxf.TRACK)
check('tracks: a timecode track and the data track in each package, the file\'s numbered after the element key',
      sorted((t['TrackID'], t['TrackName'], t['TrackNumber'].hex()) for t in tracks),
      [(1, 'Timecode Track', '00000000'), (1, 'Timecode Track', '00000000'), (2, 'Data Track', '00000000'),
       (2, 'Data Track', '17010b01')])
check('the Preface first, the file package its primary package', (found['sets'][0][0], found['sets'][0][1]['PrimaryPackage'] ==
                                                                   sets_of(found, mxf.SOURCE_PACKAGE)[0]['InstanceUID'].bytes),
      (mxf.PREFACE, True))
read = mxf.read_timed_text(track_file)
check('read back: the reel and each resource', (read['document'] == reel, [(r['id'], r['type'], r['data']) for r in read['resources']][1]),
      (True, (picture, 'image/png', png)))
locked = track_file.replace(mxf.TIMED_TEXT_ELEMENT, mxf.ENCRYPTED_TRIPLET)
try:
    mxf.read_timed_text(locked)
    check('an encrypted track file: refused', None, 'ValueError')
except ValueError as error:
    check('an encrypted track file: refused', 'encrypted' in str(error), True)

print('an IMF track file')
document = ttml.write(segments, 'en', profile='imsc1.1').encode()
imf = mxf.write_timed_text(document, [], (24000, 1001), 96, 'http://www.w3.org/ns/ttml', None, mxf.IMF)
check("asdcplib's AS-02 layout: the index in a partition of its own after the XML, no timecode track",
      (squeeze(layout(imf)), sorted(t['TrackName'] for t in sets_of(mxf.structure(imf), mxf.TRACK))),
      (['header', 'primer', 'set', 'body', 'xml', 'body', 'index', 'footer', 'rip'], ['Data Track', 'Data Track']))
found = mxf.structure(imf)
check('OP1a, MXF 2011, the index partition IndexSID 129', [(p['pattern'] == mxf.OP_1A, p['minor'], p['index_sid'], p['body_sid'])
                                                          for p in found['partitions']],
      [(True, 3, 0, 0), (True, 3, 0, 1), (True, 3, 129, 0), (True, 3, 0, 0)])
check('read back', (mxf.read_timed_text(imf)['document'] == document, mxf.read_timed_text(imf)['rate']), (True, (24000, 1001)))

print('EBU STL in MXF')
stl = ebustl.write(segments, fps=25, start='10:00:00:00', language='fr', title='Film')
wrapped = mxf.write_stl(stl, language='fr', reference=900000, duration=100)
check('ST 2075: header, the STL in a Generic Stream partition, the footer, the RIP',
      squeeze(layout(wrapped)), ['header', 'primer', 'set', 'stream', 'data', 'footer', 'rip'])
found = mxf.structure(wrapped)
check('OP1a, the STL byte stream container (ST 2075 Table 2) in every partition pack',
      {(p['pattern'] == mxf.OP_1A, tuple(p['containers']) == (mxf.STL_CONTAINER,)) for p in found['partitions']}, {(True, True)})
descriptor = sets_of(found, mxf.STL_DESCRIPTOR)[0]
check("the STL Descriptor: subtitle essence, the language, Table B.1's reference 900,000 for a TCP of 10:00:00:00",
      (descriptor['EventTextKind'] == mxf.STL_SUBTITLE_KIND, descriptor['EventTextLanguageCode'],
       descriptor['STLReferencePointTimecode'], descriptor['SampleRate']), (True, 'fr', 900000, (25, 1)))
timecodes = sets_of(found, mxf.TIMECODE)
data_track = [t for t in sets_of(found, mxf.TRACK) if t['TrackNumber'] != bytes(4)]
check("a timecode track from the reference point; the data track numbered after the data element key (5.1.1)",
      ({t['StartTimecode'] for t in timecodes}, [t['TrackNumber'].hex() for t in data_track]), ({900000}, ['01000000']))
check('the Essence Container Data links the package to the stream (9)', [(c['BodySID'], c['IndexSID'])
                                                                          for c in sets_of(found, mxf.CONTAINER_DATA)], [(1, 0)])
check('read back as it was', mxf.read_stl(wrapped), {'stl': stl, 'language': 'fr', 'reference': 900000, 'captions': False})
check('29.97 STL: 30000/1001', mxf.structure(mxf.write_stl(ebustl.write(segments, fps=30)))['sets'] and
      sets_of(mxf.structure(mxf.write_stl(ebustl.write(segments, fps=30))), mxf.STL_DESCRIPTOR)[0]['SampleRate'], (30000, 1001))


def by_hand(stl_bytes):
    """An MXF with STL built another way: one-byte BER lengths, dynamic tags
    from 0x8000 up, labels of an older version byte, a video's body
    partition first with essence the reader is to go over, then the GS
    partition, no footer."""
    def klv(key, value, short=True):
        if short and len(value) < 128:
            return key + bytes([len(value)]) + value
        return key + bytes([0x82]) + len(value).to_bytes(2, 'big') + value

    def pack(key, body_sid, containers):
        return klv(key, struct.pack('>HHIQQQQQIQI', 1, 3, 512, 0, 0, 0, 0, 0, 0, 0, body_sid) + mxf.OP_1A
                   + struct.pack('>II', len(containers), 16) + b''.join(containers), short=False)
    older = lambda label: label[:7] + b'\x0d' + label[8:]
    primer_entries = [(0x3c0a, mxf._UL_OF['InstanceUID']), (0x8000, older(mxf._UL_OF['EventTextLanguageCode'])),
                      (0x8001, mxf._UL_OF['STLReferencePointTimecode']), (0x8002, mxf._UL_OF['EventTextKind'])]
    primer = klv(mxf.PRIMER, struct.pack('>II', len(primer_entries), 18) + b''.join(
        struct.pack('>H', tag) + label for tag, label in primer_entries), short=False)
    items = [(0x3c0a, uuid.uuid4().bytes), (0x8000, 'de-DE'.encode('utf-16-be')), (0x8001, struct.pack('>q', 90000)),
             (0x8002, mxf.STL_CAPTIONS_KIND)]
    descriptor = klv(mxf._SET + bytes([mxf.STL_DESCRIPTOR, 0]),
                     b''.join(struct.pack('>HH', tag, len(value)) + value for tag, value in items), short=False)
    video_essence = bytes.fromhex('060e2b34010201010d01030115010501')
    return (pack(mxf.HEADER_CLOSED_COMPLETE, 0, [mxf.GC_MULTI, mxf.STL_CONTAINER]) + primer + descriptor
            + pack(mxf.BODY_CLOSED_COMPLETE, 1, [mxf.GC_MULTI]) + klv(video_essence, bytes(300), short=False)
            + pack(mxf.GENERIC_STREAM_PARTITION, 2, [mxf.STL_CONTAINER])
            + mxf.GENERIC_STREAM_DATA + bytes([0x83]) + len(stl_bytes).to_bytes(3, 'big') + stl_bytes)


hand = by_hand(stl)
check('read from one built by hand: the STL past a video essence, the tags and an older label version as its primer says',
      mxf.read_stl(hand), {'stl': stl, 'language': 'de-DE', 'reference': 90000, 'captions': True})
check('told apart by the containers its header names', [mxf.kind(data[:300]) for data in (track_file, imf, wrapped, hand, b'abc')],
      ['timed-text', 'timed-text', 'stl', 'stl', None])

print('through file_io')
session.VIDEO = {'framerate': 24.0, 'filepath': '/films/film.mov'}
session.SUBTITLE.segments = segments
session.SUBTITLE.metadata = None
session.FORMAT = {'options': {'dcp_fps': 24, 'dcp_content': 'text'}}
file_io.save_file(str(folder / 'dcp.mxf'), 'DCPMXF')
written = mxf.read_timed_text(str(folder / 'dcp.mxf'))
check('DCPMXF: a SMPTE reel at 24, its Id the ResourceID, to the last end', (
    dcp.parse(written['document'])[1]['standard'], dcp.parse(written['document'])[1]['fps'],
    f'urn:uuid:{written["resource_id"]}' in written['document'].decode(), written['duration']), ('smpte', 24, True, 96))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'dcp.mxf'))
check('opened again as DCPMXF', (fmt, [(s['start'], s['end']) for s in opened], session.FORMAT['options'].get('dcp_fps')),
      ('DCPMXF', [(1.0, 2.5), (3.0, 4.0)], 24))
pictures = dcp.write_images([{'start': 1, 'end': 2, 'href': f'urn:uuid:{picture}', 'x': 100, 'y': 900, 'width': 200,
                              'height': 50}], (1920, 1080)).encode()
(folder / 'pictures.mxf').write_bytes(mxf.write_timed_text(pictures, [(picture, 'image/png', png)], (24, 1), 48,
                                                           dcp.SMPTE_NAMESPACE, None, mxf.DCP))
try:
    file_io.process_subtitles_file(str(folder / 'pictures.mxf'))
    check('a reel of pictures: for an OCR add-on, its PNGs taken out beside it', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('a reel of pictures: for an OCR add-on, its PNGs taken out beside it',
          (error.kind, (Path(error.path).parent / f'{picture}.png').read_bytes() == png), ('dcp', True))
session.VIDEO = {'framerate': 23.976}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'imf.mxf'), 'IMSCMXF')
written = mxf.read_timed_text(str(folder / 'imf.mxf'))
check('IMSCMXF: IMSC 1.1 at 24000/1001, nothing it breaks', (written['rate'], written['namespace'],
                                                             ttml.check(written['document'].decode(), 'imsc1.1')),
      ((24000, 1001), 'http://www.w3.org/ns/ttml', []))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'imf.mxf'))
check('opened again as IMSCMXF', (fmt, [(s['start'], s['end'], s['text']) for s in opened]),
      ('IMSCMXF', [(1.0, 2.5, 'Hello'), (3.0, 4.0, '<i>World</i>')]))
session.VIDEO = {'framerate': 25.0}
session.SUBTITLE.language = 'fr-fr'
session.FORMAT = {'options': {'stl_fps': 25, 'start': '10:00:00:00', 'display': 'open'}}
file_io.save_file(str(folder / 'stl.mxf'), 'STLMXF')
carried = mxf.read_stl(str(folder / 'stl.mxf'))
check('STLMXF: the STL as STL saves it, French, its programme start the reference point',
      (ebustl.parse(carried['stl'])[1], carried['language'], carried['reference']),
      ({'language': 'fr', 'fps': 25, 'display': 'open', 'start': '10:00:00:00'}, 'fr-FR', 900000))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'stl.mxf'))
check('opened again as STLMXF, its rate, display and start kept', (fmt, [(s['start'], s['end']) for s in opened],
                                                                   session.FORMAT['options']),
      ('STLMXF', [(1.0, 2.48), (3.0, 4.0)], {'stl_fps': 25, 'display': 'open', 'start': '10:00:00:00'}))
session.SUBTITLE.language = None
check('the extensions, a kind each', [session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[name]['extensions']
                                      for name in ('DCPMXF', 'IMSCMXF', 'STLMXF')], [['mxf']] * 3)

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [skip] FFmpeg is not installed: the files are not probed')
else:
    print('FFmpeg')
    for name, base, rate in (('dcp.mxf', '1/24', 96), ('imf.mxf', '1001/24000', 96)):
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,time_base,duration_ts',
                                '-of', 'csv=p=0', str(folder / name)], capture_output=True, text=True)
        check(f'{name}: probed as a TTML stream of its rate and duration', (probe.stdout.strip(), probe.returncode),
              (f'ttml,subtitle,{base},{rate}', 0))
        copied = folder / (name + '.xml')
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(folder / name), '-map', '0:0', '-c', 'copy', '-f', 'data',
                        str(copied)], capture_output=True)
        check(f'{name}: the XML taken out by its index, byte for byte', copied.read_bytes() == mxf.read_timed_text(
            str(folder / name))['document'], True)
    # FFmpeg reads no Generic Stream: it goes over the whole file, every
    # set but the three it has no reader for parsed, and stops at "no
    # essence".
    probe = subprocess.run([ffprobe, '-v', 'debug', str(folder / 'stl.mxf')], capture_output=True, text=True)
    dark = sorted({line.split('Dark key ')[1].strip() for line in probe.stderr.splitlines() if 'Dark key' in line})
    errors = [line for line in subprocess.run([ffprobe, '-v', 'error', str(folder / 'stl.mxf')], capture_output=True,
                                              text=True).stderr.splitlines() if line.startswith('[mxf')]
    check("stl.mxf: walked by FFmpeg to the end, unknown to it only the STL Descriptor, the GS partition and its data",
          (dark, [line.split('] ')[1] for line in errors]),
          (['06.0e.2b.34.01.01.01.0c.0d.01.05.09.01.00.00.00', '06.0e.2b.34.02.05.01.01.0d.01.02.01.01.03.11.00',
            '06.0e.2b.34.02.53.01.01.0d.01.01.01.01.01.70.00'], ['no essence']))
    video = folder / 'video.mxf'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x48:rate=25', '-t', '0.2',
                    '-c:v', 'mpeg2video', str(video)], capture_output=True)
    with open(video, 'rb') as video_file:
        check("a video's MXF: no subtitles in it", mxf.kind(video_file.read(4096)), None)
    try:
        file_io.process_subtitles_file(str(video))
        check("a video's MXF: says it carries none", None, 'CorruptedProjectFileError')
    except file_io.CorruptedProjectFileError as error:
        check("a video's MXF: says it carries none", 'no timed text' in str(error), True)

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 24.0}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DCPMXF')
panel._refresh_options()
check("DCPMXF: the DCP options, SMPTE's alone (no generation to choose), the rate",
      (panel.dcp_options.isHidden(), panel.dcp_standard_combo.isHidden(), panel.dcp_rate_combo.isHidden(),
       sorted(key for key in panel.export_choices()[2] if key.startswith('dcp'))),
      (False, True, False, ['dcp_content', 'dcp_fps']))
panel.format_combo.setCurrentText('STLMXF')
panel._refresh_options()
check("STLMXF: STL's options", (panel.stl_options.isHidden(), sorted(key for key in panel.export_choices()[2]
                                                                    if key in ('stl_fps', 'display', 'start'))),
      (False, ['display', 'start', 'stl_fps']))
session.SUBTITLE.segments = segments
panel.format_combo.setCurrentText('IMSCMXF')
panel._refresh_options()
check('IMSCMXF: the look, the check against IMSC 1.1', (panel.ass_options.isHidden(), panel.check_label.isHidden(),
                                                        'IMSC 1.1' in panel.check_label.text()), (False, False, True))
check('in the export menu', all(name in SUBTITLE_FORMATS for name in ('DCPMXF', 'IMSCMXF', 'STLMXF')), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
