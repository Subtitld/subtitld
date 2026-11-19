"""Editors' markers as subtitles: EDIUS's marker list (CSV versions 2 and 3,
in frames and in milliseconds, a comment over lines, and version 4's XML)
and Premiere's marker CSV (drop-frame, point markers), as Subtitle Edit
reads and writes them; round trips, telling them apart from each other and
from the formats that share .csv and .xml, and the file and the export
dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import dcp, file_io, markers, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# As Subtitle Edit's EdiusMarkerList2Frames writes one, with a quote and a
# comment over two lines inside its quotes.
EDIUS2 = ('# EDIUS Marker list\r\n# Format Version 2\r\n# Created Date : Wed Nov 18 10:00:00 2026\r\n#\r\n'
          '# No, Position, Duration, Comment\r\n'
          '1,"00:00:01:00","00:00:02:12","Hello \\"you\\""\r\n'
          '2,"00:00:05:00","00:00:00:00","Two\r\nlines"\r\n'
          '3,"00:00:06:00","00:00:01:00","Last"\r\n')
EDIUS3_MS = ('# EDIUS Marker list\r\n# Format Version 3\r\n# Created Date : Wed Nov 18 10:00:00 2026\r\n#\r\n'
             '# No, Anchor, Position, Duration, Comment\r\n'
             '1,"ON","00:00:01:500","00:00:01:250","In milliseconds"\r\n')
EDIUS4 = ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
          '<edius:markerInfo xmlns:edius="http://www.grassvalley.com/ns/edius/markerListInfo">\n'
          '\t<edius:formatVersion>4</edius:formatVersion>\n\t<edius:CreateDate>Wed Nov 18 10:00:00 2026</edius:CreateDate>\n'
          '\t<edius:markerLists>\n\t\t<edius:marker>\n\t\t\t<edius:no>1</edius:no>\n\t\t\t<edius:anchor>1</edius:anchor>\n'
          '\t\t\t<edius:position>00:00:02:00</edius:position>\n\t\t\t<edius:duration>00:00:01:12</edius:duration>\n'
          '\t\t\t<edius:comment>A &amp; B</edius:comment>\n\t\t\t<edius:color>0xffff0000</edius:color>\n'
          '\t\t</edius:marker>\n\t</edius:markerLists>\n</edius:markerInfo>\n')
PREMIERE = ('Marker Name\tDescription\tIn\tOut\tDuration\tMarker Type\n'
            'Intro\tWelcome to the show\t00;00;01;00\t00;00;03;00\t00;00;02;00\tComment\n'
            'Point\t\t00;00;10;00\t00;00;10;00\t00;00;00;00\tComment\n')

print('reading')
segments, settings = markers.parse(EDIUS2, 25)
check('EDIUS 2: frames, \\" for a quote, a comment over lines, a marker of no length a point',
      segments, [{'start': 1.0, 'end': 3.48, 'text': 'Hello "you"'}, {'start': 5.0, 'end': 6.0, 'text': 'Two\nlines'},
                 {'start': 6.0, 'end': 7.0, 'text': 'Last'}])
check('EDIUS 2: the settings', settings, {'kind': 'edius', 'fps': 25.0, 'version': 2, 'units': 'frames', 'drop': False})
segments, settings = markers.parse(EDIUS3_MS, 25)
check('EDIUS 3 in milliseconds', (segments, settings['version'], settings['units']),
      ([{'start': 1.5, 'end': 2.75, 'text': 'In milliseconds'}], 3, 'ms'))
segments, settings = markers.parse(EDIUS4, 25)
check("EDIUS 4's XML, its colour kept", (segments, settings['version']),
      ([{'start': 2.0, 'end': 3.48, 'text': 'A & B', 'markers': {'color': '0xffff0000'}}], 4))
segments, settings = markers.parse(PREMIERE, 25)
check("Premiere: drop-frame at 29.97, the description, else the name; a point until POINT_SECONDS",
      ([(s['start'], s['end'], s['text']) for s in segments], settings['fps'], settings['drop']),
      ([(1.001, 3.003, 'Welcome to the show'), (10.01, 13.01, 'Point')], 29.97, True))

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': '<i>Hello</i> "you"\nthere'}, {'start': 4.0, 'end': 5.0, 'text': 'Bye'}]
written = markers.write_edius(trip, fps=25, created=__import__('datetime').datetime(2026, 11, 18, 10, 0, 0))
check('EDIUS 3 as Subtitle Edit writes it', written.split('\r\n'), [
    '# EDIUS Marker list', '# Format Version 3', '# Created Date : Wed Nov 18 10:00:00 2026', '#',
    '# No, Anchor, Position, Duration, Comment',
    '1,"ON","00:00:01:00","00:00:01:12","Hello \\"you\\"', 'there"', '2,"ON","00:00:04:00","00:00:01:00","Bye"', ''])
check('read back', markers.parse(written, 25)[0], [{'start': 1.0, 'end': 2.48, 'text': 'Hello "you"\nthere'},
                                                   {'start': 4.0, 'end': 5.0, 'text': 'Bye'}])
check('version 2 and milliseconds read back', markers.parse(markers.write_edius(trip, version=2, units='ms'), 25)[0][0],
      {'start': 1.0, 'end': 2.5, 'text': 'Hello "you"\nthere'})
xml = markers.write_edius(trip + [{'start': 6, 'end': 7, 'text': 'Red', 'markers': {'color': '0xffff0000'}}], fps=25, version=4)
root = ET.fromstring(xml.encode())
check("EDIUS 4: Subtitle Edit's elements, the colour kept", [
    [child.tag.split('}')[1] for child in root.find('{%s}markerLists' % markers.EDIUS_NAMESPACE)[0]],
    [marker.find('{%s}color' % markers.EDIUS_NAMESPACE).text for marker in root.iter('{%s}marker' % markers.EDIUS_NAMESPACE)]],
    [['no', 'anchor', 'position', 'duration', 'comment', 'color'], ['0xffffffff', '0xffffffff', '0xffff0000']])
check('EDIUS 4 read back', [(s['start'], s['end'], s['text']) for s in markers.parse(xml, 25)[0]],
      [(1.0, 2.48, 'Hello "you"\nthere'), (4.0, 5.0, 'Bye'), (6.0, 7.0, 'Red')])
premiere = markers.write_premiere(trip, fps=29.97, drop=True)
check("Premiere's columns, drop-frame, one line", premiere.split('\r\n')[:2], [
    'Marker Name\tDescription\tIn\tOut\tDuration\tMarker Type',
    'Hello "you" there\tHello "you" there\t00;00;01;00\t00;00;02;15\t00;00;01;15\tComment'])
check('Premiere read back', [(s['start'], s['end'], s['text']) for s in markers.parse(premiere, 29.97)[0]],
      [(1.001, 2.502, 'Hello "you" there'), (4.004, 5.005, 'Bye')])

print('detection')
check("each told by its header, the others' not", [markers.kind(text) for text in (
    EDIUS2, EDIUS4.encode(), PREMIERE, '﻿' + PREMIERE, 'a,b,c\n1,2,3\n', dcp.write([{'start': 0, 'end': 1, 'text': 'x'}]),
    '<tt xmlns="http://www.w3.org/ns/ttml"/>')], ['edius', 'edius', 'premiere', 'premiere', None, None, None])

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.VIDEO = {}
session.FORMAT = {}
(folder / 'edius.xml').write_text(EDIUS4, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'edius.xml'))
check("EDIUS's XML opens as EDIUS, not as TTML", (fmt, opened[0]['text'], session.FORMAT['options']['marker_version']),
      ('EDIUS', 'A & B', 4))
session.FORMAT = {}
(folder / 'markers.csv').write_bytes(PREMIERE.encode('utf-16'))
opened, fmt = file_io.process_subtitles_file(str(folder / 'markers.csv'))
check("Premiere's CSV (in UTF-16) opens as PREMIERE, its drop-frame kept", (fmt, len(opened), session.FORMAT['options']['timecode']),
      ('PREMIERE', 2, 'drop'))
session.FORMAT = {}
(folder / 'edius.csv').write_text(EDIUS2, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'edius.csv'))
check("EDIUS's CSV opens as EDIUS, version 2 kept", (fmt, len(opened), session.FORMAT['options']['marker_version']),
      ('EDIUS', 3, 2))
(folder / 'table.csv').write_text('a,b,c\n1,2,3\n', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'table.csv'))
    check('another CSV: says it is not a marker list', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('another CSV: says it is not a marker list', 'Not a marker list' in str(error), True)
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 25.0}
file_io.save_file(str(folder / 'out.csv'), 'EDIUS')
check('saved as the version opened, with a byte order mark', (folder / 'out.csv').read_bytes()[:44],
      '﻿# EDIUS Marker list\r\n# Format Version 2\r\n'.encode('utf-8'))
file_io.save_file(str(folder / 'out.xml'), 'EDIUS')
check('a .xml saved as version 4', markers.read(str(folder / 'out.xml'), 25)[1]['version'], 4)
session.FORMAT = {'options': {'timecode': 'non-drop'}}
file_io.save_file(str(folder / 'out_premiere.csv'), 'PREMIERE')
check("Premiere's saved and read back", markers.read(str(folder / 'out_premiere.csv'), 25)[0][1],
      {'start': 4.0, 'end': 5.0, 'text': 'Bye'})
check('the extensions', (session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['EDIUS']['extensions'],
                         session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['PREMIERE']['extensions']), (['csv', 'xml'], ['csv']))

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
for name in ('EDIUS', 'PREMIERE'):
    panel.format_combo.setCurrentText(name)
    panel._refresh_options()
    check(f'{name} in the export menu, with the text options and a frame rate',
          (name in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.sub_options.isHidden(), 'fps' in panel.export_choices()[2]),
          (True, False, False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
