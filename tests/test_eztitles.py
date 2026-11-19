"""EZTitles: the XML project read as Subtitle Edit's tests lay it out (the
time code standard over the video's rate, dashes for untimed cues, italics
and colours on rows and spans, the places), written and read back at 25,
29.97 drop-frame and 23.976, told from TTML's .xml; the binary .ezt built
as Subtitle Edit's tests build it (records, empty ones before the first
text, per-character attributes); and both through file_io, the XML saved
again, and the export dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, struct, sys, tempfile, zlib
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import eztitles, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def project(rate, standard, cue_in, cue_out):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<EZTSubtitlesProject version="1.3">\n<ProjectConfiguration>'
            f'<VideoFrameRate>{rate}</VideoFrameRate><TimeCodeStandard>{standard}</TimeCodeStandard></ProjectConfiguration>\n'
            f'<Subtitles count="1"><Subtitle id="sub1" number="1" incue="{cue_in}" outcue="{cue_out}">'
            '<Rows><Row><Text>Hello</Text></Row></Rows></Subtitle></Subtitles>\n</EZTSubtitlesProject>\n')


FORMATTED = '''<?xml version="1.0" encoding="UTF-8"?>
<EZTSubtitlesProject version="1.3">
  <ProjectConfiguration><TimeCodeStandard>24</TimeCodeStandard></ProjectConfiguration>
  <Subtitles count="4">
    <Subtitle id="sub1" number="1" incue="--:--:--:--" outcue="--:--:--:--">
      <VisualAttributes row_position="center" row_justification="center" vertical_align="bottom"/>
      <Rows><Row foreground_color="red" italic="true"><Text>Red italic text</Text></Row></Rows>
    </Subtitle>
    <Subtitle id="sub2" number="1" index="a" incue="00:00:01:12" outcue="00:00:02:00">
      <VisualAttributes row_position="left" row_justification="left" vertical_align="top"/>
      <Rows><Row foreground_color="white"><Text>Top left</Text></Row></Rows>
    </Subtitle>
    <Subtitle id="sub3" number="1" index="b" incue="00:00:03:00" outcue="00:00:04:00">
      <VisualAttributes row_position="right" row_justification="right" vertical_align="bottom"/>
      <Rows><Row foreground_color="yellow"><Text>Bottom right</Text><Style foreground_color="yellow"/></Row></Rows>
    </Subtitle>
    <Subtitle id="sub4" number="1" index="c" incue="00:00:05:00" outcue="00:00:06:00">
      <VisualAttributes row_position="center" row_justification="center" vertical_align="bottom"/>
      <Rows><Row foreground_color="white"><Text>Only <span italic="true">THIS</span> in italic</Text></Row>
            <Row><Text>&amp; a second row</Text></Row></Rows>
    </Subtitle>
  </Subtitles>
</EZTSubtitlesProject>'''

print('reading the XML')
segments, settings = eztitles.parse(project('23.976 fps', '30drop', '00:00:01:29', '00:00:04:15'))
check('30drop over a 23.976 video: drop-frame at 29.97', (settings, segments[0]['start'], segments[0]['end']),
      ({'fps': 29.97, 'drop': True}, round(59 * 1001 / 30000, 3), round(135 * 1001 / 30000, 3)))
segments, settings = eztitles.parse(project('25 fps', '', '00:00:01:24', '00:00:02:00'))
check('no standard: the video rate it names', (settings['fps'], segments[0]['start']), (25.0, 1.96))
check('the standards', [eztitles.parse_standard(text) for text in ('30drop', '60drop', '25', '24', '23.976 fps', 'x')],
      [(29.97, True), (59.94, True), (25.0, False), (24.0, False), (23.976, False), None])
check('written standards', [eztitles.standard_of(rate) for rate in (29.97, 59.94, 23.976, 25)], ['30drop', '60drop', '24', '25'])
check('italics, colours, places, dashes', [(s['start'], s['end'], s['text']) for s in eztitles.parse(FORMATTED)[0]], [
    (0.0, 0.0, '<font color="#ff0000"><i>Red italic text</i></font>'),
    (1.5, 2.0, '{\\an7}Top left'),
    (3.0, 4.0, '{\\an3}<font color="#ffff00">Bottom right</font>'),
    (5.0, 6.0, 'Only <i>THIS</i> in italic\n& a second row')])
try:
    eztitles.parse('<tt xmlns="http://www.w3.org/ns/ttml"/>')
    check('TTML is not EZTitles', None, 'ValueError')
except ValueError:
    check('TTML is not EZTitles', True, True)

print('writing the XML')
trip = [{'start': 1.0, 'end': 3.0, 'text': '{\\an8}<i>Hello</i>\nOnly <font color="#ffff00">this</font> & that'},
        {'start': 4.0, 'end': 6.5, 'text': '{\\an1}Plain <i>part</i>'},
        {'start': 60.0, 'end': 62.0, 'text': 'A minute'}]
for rate in (25, 29.97, 23.976):
    got = eztitles.parse(eztitles.write(trip, rate))[0]
    check(f'read back at {rate}', [s['text'] for s in got], [s['text'] for s in trip])
    check(f'times at {rate} within a frame', all(abs(a['start'] - b['start']) <= 1 / rate + 0.001 and
                                               abs(a['end'] - b['end']) <= 1 / rate + 0.001 for a, b in zip(got, trip)), True)
check('written again as it was read', eztitles.write(eztitles.parse(eztitles.write(trip, 25))[0], 25) == eztitles.write(trip, 25), True)
written = eztitles.write(trip, 29.97)
check('30drop and drop-frame cues', ('<TimeCodeStandard>30drop</TimeCodeStandard>' in written, 'incue="00:00:59:28"' in written),
      (True, True))


print('the binary')


def timecode_bytes(frames):
    seconds = frames // 30
    return bytes([frames % 30, seconds % 60, seconds // 60 % 60, seconds // 3600])


def build(subtitles, attributes=None):
    body = bytearray(64) + struct.pack('<i', len(subtitles))
    for number, (start, end, rows, vertical, justification) in enumerate(subtitles, 1):
        record = struct.pack('<h', number) + b'\x00' + timecode_bytes(start) + timecode_bytes(end)
        record += bytes([0x0a, 0, 0x80, 1, 0, 0x80, vertical, len(rows)])
        for row in rows:
            record += bytes([0xaf, 1, 0, 0, 0, 0, 0, 0, justification, 3]) + struct.pack('<i', len(row))
            record += row.encode('utf-32-le') + struct.pack('<i', len(row))
            if attributes:
                record += struct.pack('<i', 13) + b''.join(attributes(index) for index in range(len(row)))
            else:
                record += b''.join(b'\xff\xff\xff\x1f\xff\xff\xff\x1f\x09' for _ in row)
        body += struct.pack('<i', len(record)) + record
    return b'EZTZ' + zlib.compress(bytes(body))


data = build([(0, 50, ['My name is Carey Newman.'], 0, 2),
              (53, 149, ['My traditional name', 'is Hayalthkin’geme.'], 0, 2)])
check('is_ezt', (eztitles.is_ezt(data), eztitles.is_ezt(b'EZTX' + data[4:])), (True, False))
segments, settings = eztitles.parse_binary(data)
check('rows and times, frames up to 29 at 29.97', [(s['start'], s['end'], s['text']) for s in segments], [
    (0.0, round(1 + 20 / 29.97, 3), 'My name is Carey Newman.'),
    (round(1 + 23 / 29.97, 3), round(4 + 29 / 29.97, 3), 'My traditional name\nis Hayalthkin’geme.')])
segments, _ = eztitles.parse_binary(build([(30, 60, [], 0, 2), (90, 120, [], 0, 2), (150, 180, ['ON-SCREEN'], 0, 2),
                                           (210, 240, [], 0, 2)]))
check('records without text before the first are found', [(s['start'], s['text']) for s in segments],
      [(1.0, ''), (3.0, ''), (5.0, 'ON-SCREEN'), (7.0, '')])


def attributes(italic_from):
    def blob(index):
        italic = index >= italic_from
        return bytes([2 if italic else 0, 0xff, 0, 0, 0 if italic else 0x1f, 0xff, 0xff, 0xff, 0x1f, 9, 8, 0, 0])
    return blob


segments, _ = eztitles.parse_binary(build([(0, 30, ['Red italic'], 2, 0)], attributes(4)))
check('per-character italics and colour, top left', segments[0]['text'],
      '{\\an7}Red <font color="#ff0000"><i>italic</i></font>')

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.eztxml').write_text(FORMATTED, encoding='utf-8')
session.VIDEO = {}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.eztxml'))
check('.eztxml opens as EZTXML', (fmt, len(opened), session.FORMAT['options']['fps']), ('EZTXML', 4, 24.0))
(folder / 'export.xml').write_text(FORMATTED, encoding='utf-8')
session.FORMAT = {}
check('.xml with an EZTSubtitlesProject opens as EZTXML', file_io.process_subtitles_file(str(folder / 'export.xml'))[1], 'EZTXML')
(folder / 'tt.xml').write_text('<?xml version="1.0"?>\n<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
                               '<p begin="00:00:01.000" end="00:00:02.000">TTML</p></div></body></tt>', encoding='utf-8')
session.FORMAT = {}
check('TTML .xml still TTML', file_io.process_subtitles_file(str(folder / 'tt.xml'))[1], 'TTML')
(folder / 'in.ezt').write_bytes(data)
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.ezt'))
check('.ezt opens, read only', (fmt, len(opened), 'EZT' in session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS,
                                'EZT' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS), ('EZT', 2, True, False))
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 29.97}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.eztxml'), 'EZTXML')
check('saved at the video rate, read back', (eztitles.read(str(folder / 'out.eztxml'))[1]['fps'],
                                             [s['text'] for s in eztitles.read(str(folder / 'out.eztxml'))[0]]),
      (29.97, [s['text'] for s in trip]))

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('EZTXML')
panel._refresh_options()
check('EZTXML in the menu with a frame rate', ('EZTXML' in SUBTITLE_FORMATS, 'EZT' in SUBTITLE_FORMATS,
                                               panel.sub_options.isHidden()), (True, False, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
