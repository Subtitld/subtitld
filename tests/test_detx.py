"""Cappella DetX: a detection laid out as Joker's description and test files
have it (header, roles with colours and pictures, loops and shots, lines
with their lipsyncs, a lipsync not linked, a line break, the video's
timestamp and rate) read into a dubbing session, written as Joker writes
one, round trips, telling it from other XML, and the file and the export
dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import base64, os, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import detx, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


picture = QImage(8, 8, QImage.Format_RGB32)
picture.fill(QColor('#00bb00'))
buffer = QBuffer()
buffer.open(QIODevice.WriteOnly)
picture.save(buffer, 'JPG')
JPEG = bytes(buffer.data())

SAMPLE = f'''<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<detx>
  <header>
    <cappella copyright="Cappella" version="3.6.0"/>
    <title>Le film</title>
    <title2>The film</title2>
    <episode number="3" season="1" title="Pilote" title2="Pilot"/>
    <production country="France" year="2026"/>
    <author firstname="Pierre" name="Dupont"/>
    <detector firstname="Jean" name="Dupond"/>
    <notes>Notes for the director</notes>
    <videofile timestamp="10:00:00:00" tctype="24">/films/film.mov</videofile>
    <last_position timecode="10:00:16:00" track="1"/>
  </header>
  <roles>
    <role color="#00BB00" description="the heroine" gender="female" id="jeanne" name="Jeanne">
      <image>{base64.b64encode(JPEG).decode()}</image>
    </role>
    <role color="#0000BB" gender="male" id="paul" name="Paul"/>
  </roles>
  <body>
    <loop timecode="10:00:00:00"/>
    <shot timecode="10:00:01:00"/>
    <line role="jeanne" track="1">
      <lipsync timecode="10:00:02:00" type="in_open"/>
      <text>Composed </text>
      <lipsync timecode="10:00:03:12" type="neutral"/>
      <text>sentence<br/>on two lines</text>
      <lipsync timecode="10:00:05:00" type="out_close"/>
    </line>
    <line role="paul" track="2" voice="off" type="reac">
      <lipsync timecode="10:00:06:00" type="in_close"/>
      <lipsync link="off" timecode="10:00:06:12" type="mpb"/>
      <text>Off the picture</text>
      <lipsync timecode="10:00:08:00" type="out_open"/>
    </line>
    <shot timecode="10:00:09:00"/>
    <line role="paul" track="0">
      <lipsync timecode="10:00:10:00" type="in_open"/>
      <text>Runs on</text>
    </line>
  </body>
</detx>
'''

print('reading')
segments, settings = detx.parse(SAMPLE)
check('lines from the timestamp at the tctype, words with their line break, roles as speakers',
      [(s['start'], s['end'], s['text'], s['speaker']) for s in segments],
      [(2.0, 5.0, 'Composed sentence\non two lines', 'Jeanne'), (6.0, 8.0, 'Off the picture', 'Paul'),
       (10.0, 17.0, 'Runs on', 'Paul')])
check('what a line has no room for kept: role, voice, track, type, the lipsyncs where the words are',
      segments[1]['detx'], {'role': 'paul', 'text': 'Off the picture', 'voice': 'off', 'track': '2', 'type': 'reac',
                            'marks': [{'type': 'in_close', 'at': 0.0, 'link': True, 'char': 0},
                                      {'type': 'mpb', 'at': 0.5, 'link': False, 'char': 0},
                                      {'type': 'out_open', 'at': 2.0, 'link': True, 'char': 15}]})
check('a lipsync inside the words', [(m['type'], m['at'], m['char']) for m in segments[0]['detx']['marks']],
      [('in_open', 0.0, 0), ('neutral', 1.5, 9), ('out_close', 3.0, 30)])
check("the roles' colours and pictures for the speakers",
      {name: (data.get('color'), data.get('image_bytes') == JPEG) for name, data in settings['speakers'].items()},
      {'Jeanne': ('#00bb00', True), 'Paul': ('#0000bb', False)})
check('the header, the roles, the loops and shots kept', (
    settings['fps'], settings['title'], settings['metadata']['header']['videofile'], settings['metadata']['header']['episode'],
    settings['metadata']['roles']['jeanne'], settings['metadata']['loops'], settings['metadata']['shots']),
    (24.0, 'Le film', {'timestamp': '10:00:00:00', 'tctype': '24', 'path': '/films/film.mov'},
     {'number': '3', 'season': '1', 'title': 'Pilote', 'title2': 'Pilot'},
     {'gender': 'female', 'description': 'the heroine', 'name': 'Jeanne'}, [0.0], [1.0, 9.0]))
check('Cappella\'s 25 where the videofile gives no tctype', detx.parse('<detx><body><line role="a">'
      '<lipsync timecode="00:00:01:05" type="in_open"/><text>x</text><lipsync timecode="00:00:02:00" type="out_open"/>'
      '</line></body></detx>')[0][0]['start'], 1.2)
for name, data in (('another XML: refused', '<tt xmlns="http://www.w3.org/ns/ttml"/>'), ('not XML: refused', 'detx')):
    try:
        detx.parse(data)
        check(name, None, 'ValueError')
    except ValueError:
        check(name, True, True)

print('writing')
written = detx.write(segments, metadata=settings['metadata'], speakers={'Jeanne': {'color': '#00bb00'}, 'Paul': {'color': '#0000bb'}})
root = ET.fromstring(written.encode())
check('the header as it was', [(child.tag, child.attrib, (child.text or '').strip()) for child in root.find('header')][:4],
      [('cappella', {'copyright': 'Cappella', 'version': '3.6.0'}, ''), ('title', {}, 'Le film'), ('title2', {}, 'The film'),
       ('episode', {'number': '3', 'season': '1', 'title': 'Pilote', 'title2': 'Pilot'}, '')])
check('roles: their ids, colours, genders', [(r.get('id'), r.get('name'), r.get('color'), r.get('gender')) for r in root.iter('role')],
      [('jeanne', 'Jeanne', '#00BB00', 'female'), ('paul', 'Paul', '#0000BB', 'male')])
check('the body in time order: loops, shots and lines', [(e.tag, e.get('timecode') or e.get('role')) for e in root.find('body')],
      [('loop', '10:00:00:00'), ('shot', '10:00:01:00'), ('line', 'jeanne'), ('line', 'paul'), ('shot', '10:00:09:00'),
       ('line', 'paul')])
first = root.find('body').findall('line')[0]
check('a line: its lipsyncs and words as read, the line break a <br/>',
      [(child.tag, child.get('timecode'), child.get('type'), ''.join(child.itertext()), len(child)) for child in first],
      [('lipsync', '10:00:02:00', 'in_open', '', 0), ('text', None, None, 'Composed ', 0),
       ('lipsync', '10:00:03:12', 'neutral', '', 0), ('text', None, None, 'sentenceon two lines', 1),
       ('lipsync', '10:00:05:00', 'out_close', '', 0)])
check('round trip', [(s['start'], s['end'], s['text'], s['speaker'], s['detx']) for s in detx.parse(written)[0]],
      [(s['start'], s['end'], s['text'], s['speaker'], s['detx']) for s in segments])
check('written again as it was read', detx.write(detx.parse(written)[0], metadata=detx.parse(written)[1]['metadata'],
                                                  speakers=detx.parse(written)[1]['speakers']), written)
edited = [dict(segments[0], text='Changed words')]
lipsyncs = [(child.get('type'), child.get('timecode')) for child in
            ET.fromstring(detx.write(edited, metadata=settings['metadata']).encode()).iter('lipsync')]
check('words changed: only the in and the out', lipsyncs, [('in_open', '10:00:02:00'), ('out_open', '10:00:05:00')])
new = ET.fromstring(detx.write([{'start': 1, 'end': 2, 'text': '<i>Hi</i>', 'speaker': 'Ève Noël'},
                                {'start': 3, 'end': 4, 'text': 'Nobody'}], fps=25).encode())
check('a new file: a role made of each name as Joker makes ids, "Unknown" for none, 25 fps from 00:00:00:00',
      ([(r.get('id'), r.get('name'), r.get('color')) for r in new.iter('role')], new.find('header/videofile').attrib,
       [t.text for t in new.iter('text')]),
      ([('evenoel', 'Ève Noël', '#000000'), ('unknown', 'Unknown', '#000000')], {'timestamp': '00:00:00:00', 'tctype': '25'},
       ['Hi', 'Nobody']))

print('detection')
check('a <detx> root, not another', [detx.looks_like(text) for text in (SAMPLE, SAMPLE.encode(), '<detxx/>',
                                                                        '<tt xmlns="http://www.w3.org/ns/ttml"/>')],
      [True, True, False, False])

print('through file_io: into a dubbing session')
folder = Path(tempfile.mkdtemp())
(folder / 'film.detx').write_text(SAMPLE, encoding='utf-8')
session.VIDEO = {}
session.FORMAT = {}
session.SPEAKERS = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'film.detx'))
file_io.stop_background_loaders()
check('opens as DETX: the speakers listed with their colours, the rate and metadata kept',
      (fmt, [s['speaker'] for s in opened], {name: data.get('color') for name, data in session.SPEAKERS.items()},
       session.FORMAT['options']['fps'], session.SUBTITLE['metadata']['title'], 'detx' in session.SUBTITLE['metadata']),
      ('DETX', ['Jeanne', 'Paul', 'Paul'], {'Jeanne': '#00bb00', 'Paul': '#0000bb'}, 24.0, 'Le film', True))
session.SUBTITLE.segments = opened
file_io.save_file(str(folder / 'out.detx'), 'DETX')
check('saved and read back as it was', [(s['start'], s['end'], s['text'], s['speaker']) for s in detx.read(str(folder / 'out.detx'))[0]],
      [(s['start'], s['end'], s['text'], s['speaker']) for s in segments])
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['DETX']['extensions'], ['detx'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DETX')
panel._refresh_options()
check('DETX in the export menu, with the text options and a frame rate',
      ('DETX' in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.sub_options.isHidden(), 'fps' in panel.export_choices()[2]),
      (True, False, False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
