"""Final Cut Pro XML: reading the captions of a project as Final Cut Pro
exports it (rational times, offsets on the parents' timelines, a timeline
an hour in, several languages and formats, styles by definition), its
titles where it has no captions, the .fcpxmld bundle, and writing a
project Final Cut Pro imports.

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
from subtitld.modules import fcpxml, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data):
    segments, settings = fcpxml.parse(data)
    return [(s['start'], s['end'], s['text']) + ((s['translations'],) if 'translations' in s else ())
            for s in segments], settings


# A 23.976 project as Final Cut Pro exports it: its timeline at 01:00:00:00
# (86400 frames of 1001/24000s), a clip with captions anchored to it in
# English and French, the English again as CEA-608, a disabled caption, a
# secondary storyline, and a gap after the clip whose own time starts at
# 3600s, with a caption anchored to it.
PROJECT = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>

<fcpxml version="1.10">
    <resources>
        <format id="r1" name="FFVideoFormat1080p2398" frameDuration="1001/24000s" width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)"/>
        <asset id="r2" name="Interview" start="0s" duration="240240/24000s" hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">
            <media-rep kind="original-media" src="file:///Volumes/Media/Interview.mov"/>
        </asset>
    </resources>
    <library location="file:///Users/editor/Movies/Untitled.fcpbundle/">
        <event name="Day 1" uid="0A1B2C3D-0000-0000-0000-000000000001">
            <project name="Interview Captions" uid="0A1B2C3D-0000-0000-0000-000000000002" modDate="2024-03-01 10:00:00 +0000">
                <sequence format="r1" duration="480480/24000s" tcStart="86486400/24000s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
                    <spine>
                        <asset-clip ref="r2" offset="86486400/24000s" name="Interview" start="0s" duration="240240/24000s" tcFormat="NDF" audioRole="dialogue">
                            <caption lane="1" offset="24024/24000s" name="Hello there" duration="48048/24000s" role="iTT?captionFormat=ITT.en">
                                <text placement="bottom">
                                    <text-style ref="ts1">Hello </text-style>
                                    <text-style ref="ts2">there</text-style>
                                </text>
                                <text-style-def id="ts1">
                                    <text-style font=".AppleSystemUIFont" fontSize="13" fontFace="Regular" fontColor="1 1 1 1" backgroundColor="0 0 0 1"/>
                                </text-style-def>
                                <text-style-def id="ts2">
                                    <text-style font=".AppleSystemUIFont" fontSize="13" fontFace="Regular" fontColor="1 1 0 1" backgroundColor="0 0 0 1" italic="1"/>
                                </text-style-def>
                            </caption>
                            <caption lane="2" offset="24024/24000s" name="Bonjour" duration="48048/24000s" role="iTT?captionFormat=ITT.fr">
                                <text placement="top">
                                    <text-style ref="ts1">Bonjour</text-style>
                                </text>
                            </caption>
                            <caption lane="3" offset="24024/24000s" name="Hello there" duration="48048/24000s" role="CEA-608?captionFormat=CEA608.en">
                                <text position="4 15" display-style="pop-on" alignment="left">
                                    <text-style ref="ts1">Hello there</text-style>
                                </text>
                            </caption>
                            <caption lane="1" offset="96096/24000s" name="Not shown" duration="24024/24000s" role="iTT?captionFormat=ITT.en" enabled="0">
                                <text placement="bottom"><text-style ref="ts1">Not shown</text-style></text>
                            </caption>
                            <spine lane="1" offset="120120/24000s">
                                <gap name="Gap" offset="120120/24000s" start="0s" duration="72072/24000s">
                                    <caption lane="1" offset="24024/24000s" name="In a storyline" duration="24024/24000s" role="iTT?captionFormat=ITT.en">
                                        <text placement="top"><text-style ref="ts3">In a storyline,</text-style><text-style ref="ts3" bold="1">&#x2028;bold</text-style></text>
                                        <text-style-def id="ts3">
                                            <text-style fontFace="Regular" fontColor="1 1 1 1" backgroundColor="0 0 0 0.5" alignment="left"/>
                                        </text-style-def>
                                    </caption>
                                </gap>
                            </spine>
                        </asset-clip>
                        <gap name="Gap" offset="86726640/24000s" start="3600s" duration="240240/24000s">
                            <caption lane="1" offset="86448048/24000s" name="After the clip" duration="72072/24000s" role="iTT?captionFormat=ITT.en">
                                <text placement="right"><text-style ref="ts1">After the clip</text-style></text>
                            </caption>
                            <caption lane="2" offset="86448048/24000s" name="Après" duration="72072/24000s" role="iTT?captionFormat=ITT.fr">
                                <text placement="bottom"><text-style ref="ts1">Après le clip</text-style></text>
                            </caption>
                        </gap>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>
'''

print('a project as Final Cut Pro exports it')
read, settings = cues(PROJECT)
check('every English caption, where its parents put it, from the timeline start; French as translations', read, [
    (1.001, 3.003, 'Hello <font color="#ffff00"><i>there</i></font>', {'fr': '{\\an8}Bonjour'}),
    (6.006, 7.007, '{\\an7}<font background="#00000080">In a storyline,<b>\nbold</b></font>'),
    (12.012, 15.015, '{\\an6}After the clip', {'fr': 'Après le clip'})])
check('its rate, drop-frame, start and languages', settings,
      {'fps': 23.976, 'drop': False, 'start': '01:00:00:00', 'languages': ['en', 'fr']})
check('a storyline whose items count from 0', cues(PROJECT.replace('<gap name="Gap" offset="120120/24000s"', '<gap name="Gap" offset="0s"'))[0][1][0:2],
      (6.006, 7.007))
check('a storyline item with no offset follows the one before', cues(PROJECT.replace(
    '<gap name="Gap" offset="86726640/24000s"', '<gap name="Gap"'))[0][2][0:2], (12.012, 15.015))
CEA608 = ('<fcpxml version="1.10"><resources><format id="r1" frameDuration="1001/30000s"/></resources><project name="p">'
          '<sequence format="r1" tcStart="0s" tcFormat="DF"><spine><gap offset="0s" duration="10s">'
          '<caption lane="1" offset="1s" duration="2s" role="CEA-608?captionFormat=CEA608.en-US">'
          '<text position="{position}" display-style="pop-on" alignment="{alignment}">'
          '<text-style fontColor="1 1 1 1" backgroundColor="0 0 0 1">Roll</text-style></text></caption>'
          '</gap></spine></sequence></project></fcpxml>')
check('CEA-608: its row of fifteen and its alignment are its place', [
    cues(CEA608.format(position=position, alignment=alignment))[0][0][2]
    for position, alignment in (('4 15', 'left'), ('0 1', 'right'), ('0 8', 'center'))],
      ['{\\an1}Roll', '{\\an9}Roll', '{\\an5}Roll'])
check('CEA-608: its settings', cues(CEA608.format(position='0 15', alignment='center'))[1],
      {'fps': 29.97, 'drop': True, 'start': '00:00:00:00', 'languages': ['en-US']})
check('a timeline at 00:00:00:00', cues(PROJECT.replace('tcStart="86486400/24000s"', 'tcStart="0s"')
                                        .replace('offset="86486400/24000s"', 'offset="0s"')
                                        .replace('offset="86726640/24000s"', 'offset="240240/24000s"'))[1]['start'],
      '00:00:00:00')
check('a disabled clip hides its captions', cues(PROJECT.replace(
    'name="Interview" start="0s"', 'name="Interview" enabled="0" start="0s"'))[0][0][0], 12.012)

TITLES = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
    <resources>
        <format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s" width="1920" height="1080"/>
        <effect id="r2" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"/>
    </resources>
    <library>
        <event name="Titles">
            <project name="Titled">
                <sequence format="r1" duration="10s" tcStart="0s" tcFormat="NDF">
                    <spine>
                        <gap name="Gap" offset="0s" start="3600s" duration="10s">
                            <title ref="r2" lane="1" offset="3601s" name="Basic Title" start="3600s" duration="2s">
                                <param name="Position" key="9999/999166631/999166633/1/100/101" value="0 -400"/>
                                <text>
                                    <text-style ref="ts1">A title</text-style>
                                </text>
                                <text-style-def id="ts1">
                                    <text-style font="Helvetica" fontSize="63" fontFace="Regular" fontColor="1 1 1 1" alignment="center"/>
                                </text-style-def>
                            </title>
                            <title ref="r2" lane="1" offset="3604s" name="Basic Title" start="3600s" duration="150/25s">
                                <text><text-style ref="ts2">Second, </text-style><text-style ref="ts2" italic="1">leaning</text-style></text>
                                <text-style-def id="ts2">
                                    <text-style font="Helvetica" fontSize="63" fontColor="1 1 1 1"/>
                                </text-style-def>
                            </title>
                        </gap>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>
'''
print('titles, where there are no captions')
check('a project of titles', cues(TITLES), ([(1.0, 3.0, 'A title'), (4.0, 10.0, 'Second, <i>leaning</i>')],
                                           {'fps': 25.0, 'drop': False, 'start': '00:00:00:00', 'languages': []}))
check('captions win over titles', len(cues(PROJECT.replace('</gap>\n                    </spine>', TITLES.split('<gap name="Gap" offset="0s" start="3600s" duration="10s">')[1].split('</gap>')[0] + '</gap>\n                    </spine>', 1))[0]), 3)
for name, data in (('another XML', '<?xml version="1.0"?><tt xmlns="http://www.w3.org/ns/ttml"/>'), ('not XML', 'Hello')):
    try:
        fcpxml.parse(data)
        check(f'{name}: says it is not FCPXML', None, 'ValueError')
    except ValueError as error:
        check(f'{name}: says it is not FCPXML', str(error).startswith('Not a Final Cut Pro XML file'), True)
check('an empty project', fcpxml.parse('<fcpxml version="1.10"><resources/></fcpxml>')[0], [])

print('writing')
SEGMENTS = [{'start': 1.0, 'end': 3.6, 'text': '{\\an8}Hello <i>there</i>\nsecond <font color="#ff0000">red</font>'},
            {'start': 3.0, 'end': 5.0, 'text': 'Overlap & <b>bold</b> <u>under</u>'},
            {'start': 6.0, 'end': 6.0, 'text': '{\\an1}<font background="#00000080">A box</font>'},
            {'start': 7.0, 'end': 8.0, 'text': '<i></i>'}]
written = fcpxml.write([('en', SEGMENTS), ('pt-br', [{'start': 1.0, 'end': 3.5, 'text': 'Olá'}])], 25)
root = ET.fromstring(written.split('\n', 3)[3])
captions = list(root.iter('caption'))
check('a declaration, a doctype, version 1.10', (written.splitlines()[:2], root.get('version')),
      (['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE fcpxml>'], '1.10'))
check('a library, an event, a project, a sequence of one gap', (
    [element.tag for element in root.find('library').iter() if element.tag in ('event', 'project', 'sequence', 'spine', 'gap')],
    root.find('.//sequence').attrib, root.find('.//gap').attrib, root.find('.//format').attrib), (
    ['event', 'project', 'sequence', 'spine', 'gap'],
    {'format': 'r1', 'duration': '15100/2500s', 'tcStart': '0s', 'tcFormat': 'NDF', 'audioLayout': 'stereo', 'audioRate': '48k'},
    {'name': 'Gap', 'offset': '0s', 'start': '0s', 'duration': '15100/2500s'},
    {'id': 'r1', 'name': 'FFVideoFormat1080p25', 'frameDuration': '100/2500s', 'width': '1920', 'height': '1080',
     'colorSpace': '1-1-1 (Rec. 709)'}))
check('a caption a subtitle, a role a language, overlapping ones on lanes of their own, a frame at least', [
    (c.get('offset'), c.get('duration'), c.get('lane'), c.get('role'), c.get('name')) for c in captions], [
    ('1s', '6500/2500s', '1', 'iTT?captionFormat=ITT.en', 'Hello there second red'),
    ('1s', '6300/2500s', '2', 'iTT?captionFormat=ITT.pt-BR', 'Olá'),
    ('3s', '2s', '3', 'iTT?captionFormat=ITT.en', 'Overlap & bold under'),
    ('6s', '100/2500s', '1', 'iTT?captionFormat=ITT.en', 'A box')])
check('runs and their definitions', [(run.get('ref'), run.text) for run in captions[0].find('text')],
      [('ts1', 'Hello '), ('ts2', 'there'), ('ts1', '\nsecond '), ('ts3', 'red')])
check('the looks', [dict(definition.find('text-style').attrib) for definition in captions[0].findall('text-style-def')][1:], [
    {'fontFace': 'Regular', 'fontColor': '1 1 1 1', 'backgroundColor': '0 0 0 1', 'italic': '1'},
    {'fontFace': 'Regular', 'fontColor': '1 0 0 1', 'backgroundColor': '0 0 0 1'}])
check('the place', [(c.find('text').get('placement'), c.find('text-style-def/text-style').get('alignment')) for c in captions],
      [('top', None), ('bottom', None), ('bottom', None), ('bottom', 'left')])
check('ids unique in the file', len({d.get('id') for d in root.iter('text-style-def')}), len(list(root.iter('text-style-def'))))
check('read back', cues(written), ([
    (1.0, 3.6, '{\\an8}Hello <i>there</i>\nsecond <font color="#ff0000">red</font>', {'pt-BR': 'Olá'}),
    (3.0, 5.0, 'Overlap & <b>bold</b> <u>under</u>'),
    (6.0, 6.04, '{\\an1}<font background="#00000080">A box</font>')],
    {'fps': 25.0, 'drop': False, 'start': '00:00:00:00', 'languages': ['en', 'pt-BR']}))
at_2997 = fcpxml.write([('en', SEGMENTS[:1])], 29.97, start='01:00:00:00', width=1280, height=720)
check('29.97 drop-frame from 01:00:00;00 (107892 frames), on frames', (
    ET.fromstring(at_2997.split('\n', 3)[3]).find('.//sequence').get('tcStart'),
    ET.fromstring(at_2997.split('\n', 3)[3]).find('.//sequence').get('tcFormat'),
    ET.fromstring(at_2997.split('\n', 3)[3]).find('.//format').get('name'),
    ET.fromstring(at_2997.split('\n', 3)[3]).find('.//caption').get('offset')),
      ('107999892/30000s', 'DF', 'FFVideoFormat720p2997', '30030/30000s'))
check('read back: the hour taken off, times on frames', cues(at_2997), (
    [(1.001, 3.604, '{\\an8}Hello <i>there</i>\nsecond <font color="#ff0000">red</font>')],
    {'fps': 29.97, 'drop': True, 'start': '01:00:00:00', 'languages': ['en']}))
check('23.976 not drop-frame, an odd size unnamed', (
    lambda r: (r.find('.//sequence').get('tcFormat'), r.find('.//format').get('name'), r.find('.//format').get('frameDuration')))(
    ET.fromstring(fcpxml.write([('en', SEGMENTS[:1])], 23.976, width=1000, height=500).split('\n', 3)[3])),
      ('NDF', None, '1001/24000s'))
check('nothing to write: still a project', len(list(ET.fromstring(fcpxml.write([('en', [])]).split('\n', 3)[3]).iter('gap'))), 1)

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.fcpxml').write_text(PROJECT, encoding='utf-8')
session.FORMAT = {}
session.VIDEO = {}
session.SUBTITLE['language'] = 'pt-br'
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.fcpxml'))
check("opens; its language the project's, French translations; its settings kept", (
    fmt, len(opened), session.SUBTITLE['language'], opened[0].get('translations'),
    {key: session.FORMAT['options'].get(key) for key in ('fps', 'timecode', 'timeline_start')}),
      ('FCPXML', 3, 'en-us', {'fr-fr': '{\\an8}Bonjour'}, {'fps': 23.976, 'timecode': 'non-drop', 'timeline_start': '01:00:00:00'}))
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.fcpxml'), 'FCPXML')
again = ET.fromstring((folder / 'again.fcpxml').read_text(encoding='utf-8').split('\n', 3)[3])
check('saved again as it was', (again.find('.//sequence').get('tcStart'), again.find('.//format').get('frameDuration'),
                                [c.get('offset') for c in again.iter('caption')]),
      ('86486400/24000s', '1001/24000s', ['24024/24000s', '144144/24000s', '288288/24000s']))
session.SUBTITLE['segments'] = opened
session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/Interview.mov', 'width': 1920, 'height': 1080}
session.FORMAT = {'options': {'text': 'both', 'language': 'fr-fr', 'speakers': 'none', 'formatting': 'keep', 'offset': 0}}
file_io.save_file(str(folder / 'both.fcpxml'), 'FCPXML')
both = ET.fromstring((folder / 'both.fcpxml').read_text(encoding='utf-8').split('\n', 3)[3])
check("both, as two caption languages; the video's rate and name", (
    sorted({c.get('role') for c in both.iter('caption')}), len(list(both.iter('caption'))),
    both.find('.//project').get('name'), both.find('.//format').get('frameDuration')),
      (['iTT?captionFormat=ITT.en-US', 'iTT?captionFormat=ITT.fr-FR'], 5, 'Interview', '100/2500s'))
bundle = folder / 'Captions.fcpxmld'
session.FORMAT = {'options': {}}
file_io.save_file(str(bundle), 'FCPXML')
check('a bundle: a folder with Info.fcpxml in it', (bundle / 'Info.fcpxml').is_file(), True)
session.SUBTITLE['segments'] = []
check('the bundle opens', (lambda r: (r[1], len(r[0])))(file_io.process_subtitles_file(str(bundle))), ('FCPXML', 3))
(folder / 'broken.fcpxml').write_text('<fcpxml version="1.10"><resources>', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'broken.fcpxml'))
    check('a broken file says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('a broken file says so', str(error).startswith('Not a Final Cut Pro XML file'), True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE['segments'] = opened
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('FCPXML')
panel._refresh_options()
options = panel.get_config()['options']
check("FCPXML: the rate (the video's), drop-frame, the timeline start; formatting; no encoding", (
    panel.sub_options.isHidden(), panel.scc_options.isHidden(), panel.timeline_options.isHidden(),
    panel.formatting_combo.isHidden(), panel.srt_options.isHidden(),
    {key: options.get(key) for key in ('fps', 'timecode', 'timeline_start', 'encoding')}),
      (False, False, False, False, True, {'fps': 29.97, 'timecode': 'drop', 'timeline_start': '00:00:00:00', 'encoding': None}))
check('both, as two languages', [panel.text_combo.combobox.itemText(index) for index in range(panel.text_combo.combobox.count())
                                 if panel.text_combo.combobox.itemData(index)[0] == 'both'][0].startswith('Original and translation, as two'),
      True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
