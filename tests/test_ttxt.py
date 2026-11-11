"""GPAC TTXT: reading a stream laid out as MP4Box dumps one (descriptions
and their places, styles counted in UTF-16 units, blinking, karaoke, an
empty sample clearing the screen, the final empty sample), a version 1.0
file with its quoted lines, writing what GPAC's importer takes, round
trips, telling it from CCExtractor's timed transcript, and the file and the
export dialog.

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
from subtitld.modules import file_io, session, ttxt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def description(across, down, vertical='no', size=18, color='ff ff ff ff'):
    # As dump_ttxt_header writes one.
    return (f'<TextSampleDescription horizontalJustification="{across}" verticalJustification="{down}" '
            f'backColor="0 0 0 0" verticalText="{vertical}" fillTextRegion="no" continuousKaraoke="no" scroll="None">\n'
            '<FontTable>\n<FontTableEntry fontName="Serif" fontID="1"/>\n</FontTable>\n'
            '<TextBox top="0" left="0" bottom="60" right="400"/>\n'
            f'<Style styles="Normal" fontID="1" fontSize="{size}" color="{color}"/>\n'
            '</TextSampleDescription>\n')


# A stream as MP4Box -ttxt writes one: the samples one after the other, an
# empty one between two subtitles, styles after the text, an emoji (two
# UTF-16 units) before a styled word, a sample at the top right, one down
# the picture, karaoke, and the empty sample at the track's end.
SAMPLE = ('<?xml version="1.0" encoding="UTF-8" ?>\n<!-- GPAC 3GPP Text Stream -->\n<TextStream version="1.1">\n'
          '<TextStreamHeader width="400" height="60" layer="0" translation_x="0" translation_y="0">\n'
          + description('center', 'bottom') + description('right', 'top') + description('center', 'center', 'yes')
          + '</TextStreamHeader>\n'
          '<TextSample sampleTime="00:00:00.000" sampleDescriptionIndex="1" xml:space="preserve"></TextSample>\n'
          '<TextSample sampleTime="00:00:01.000" sampleDescriptionIndex="1" xml:space="preserve">Hello &amp; \n'
          'welcome<Style fromChar="0" toChar="5" styles="Bold Italic " fontID="1" fontSize="18" color="ff 0 0 ff"/>\n'
          '<Style fromChar="9" toChar="16" styles="Underlined " fontID="1" fontSize="27" color="ff ff ff ff"/>\n'
          '</TextSample>\n'
          '<TextSample sampleTime="00:00:02.500" sampleDescriptionIndex="1" xml:space="preserve"></TextSample>\n'
          '<TextSample sampleTime="00:00:03.000" sampleDescriptionIndex="2" xml:space="preserve">&#128512; it '
          '&apos;blinks&apos;<Style fromChar="7" toChar="13" styles="Strikethrough" fontID="1" fontSize="18" color="ff ff ff ff"/>\n'
          '<Blinking fromChar="7" toChar="13"/>\n</TextSample>\n'
          '<TextSample sampleTime="00:00:04.000" sampleDescriptionIndex="3" xml:space="preserve">縦書き</TextSample>\n'
          '<TextSample sampleTime="00:00:05.000" sampleDescriptionIndex="1" xml:space="preserve">Sing a song'
          '<Karaoke startTime="0.2">\n<KaraokeRange fromChar="0" toChar="5" endTime="0.5"/>\n'
          '<KaraokeRange fromChar="5" toChar="7" endTime="0.9"/>\n<KaraokeRange fromChar="7" toChar="7" endTime="1.1"/>\n'
          '<KaraokeRange fromChar="7" toChar="11" endTime="1.6"/>\n</Karaoke>\n</TextSample>\n'
          '<TextSample sampleTime="00:00:07.000" text="" />\n'
          '</TextStream>\n')

print('reading')
check('samples until the next, styles by UTF-16 units, the places, blinking, karaoke', [
    (s['start'], s['end'], s['text'], s.get('writing', '')) for s in ttxt.parse(SAMPLE)], [
    (1.0, 2.5, '<font color="#ff0000"><i><b>Hello</b></i></font> & \n<font size="150%"><u>welcome</u></font>', ''),
    (3.0, 4.0, '{\\an9}😀 it \'<blink><s>blinks</s></blink>\'', ''),
    (4.0, 5.0, '{\\an5}縦書き', 'vertical-rl'),
    (5.0, 7.0, '{\\k20}{\\k30}Sing {\\k40}a {\\k20}{\\k50}song', '')])
VERSION_1_0 = ('<?xml version="1.0" encoding="UTF-8" ?>\n<TextStream version="1.0">\n<TextStreamHeader>\n'
               '<TextSampleDescription horizontalJustification="center"/>\n</TextStreamHeader>\n'
               '<TextSample sampleTime="00:00:01.000" text="\'First line\' \'it\'s the second\'"/>\n'
               '<TextSample sampleTime="2.5" text=""/>\n'
               '<TextSample sampleTime="4" text="\'Last\'"/>\n</TextStream>\n')
check("version 1.0: quoted lines, times in seconds, the importer's bottom by default, the last sample's tail",
      ttxt.parse(VERSION_1_0), [{'start': 1.0, 'end': 2.5, 'text': "First line\nit's the second"},
                                {'start': 4.0, 'end': 7.0, 'text': 'Last'}])
TRANSCRIPT = b'00:00:01,001|00:00:03,003|POP|Hello there\r\n00:00:04,004|00:00:05,005|POP|Bye\r\n'
check("CCExtractor's transcript and GPAC's told apart", (ttxt.kind(TRANSCRIPT), ttxt.kind(SAMPLE.encode())), ('transcript', 'gpac'))
for name, data, start in (("CCExtractor's transcript: refused, saying what it is", TRANSCRIPT, "Not GPAC's TTXT: this .ttxt is CCExtractor's"),
                          ('another XML: refused', b'<tt xmlns="http://www.w3.org/ns/ttml"/>', "Not GPAC's TTXT: its root is <")):
    try:
        ttxt.parse(data)
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)

print('writing')
written = ttxt.write([{'start': 1.0, 'end': 2.0, 'text': '<i>Hello</i> "you"'},
                      {'start': 3.0, 'end': 4.0, 'text': '{\\an8}Top <font color="#00ff00">green</font>'}], 1920, 1080)
root = ET.fromstring(written.encode())
check("a header the video's size, a description for each place, as GPAC dumps them",
      (root.get('version'), root.find('TextStreamHeader').get('width'), root.find('TextStreamHeader').get('height'),
       [(d.get('horizontalJustification'), d.get('verticalJustification'), d.find('Style').get('fontSize'))
        for d in root.iter('TextSampleDescription')]),
      ('1.1', '1920', '1080', [('center', 'bottom', '54'), ('center', 'top', '54')]))
check('samples: times, description, text, Style records; an empty sample between and at the end',
      [(s.get('sampleTime'), s.get('sampleDescriptionIndex'), s.text if s.get('text') is None else s.get('text'),
        [(c.get('fromChar'), c.get('toChar'), c.get('styles'), c.get('color')) for c in s]) for s in root.iter('TextSample')],
      [('00:00:01.000', '1', 'Hello "you"', [('0', '5', 'Italic', 'ff ff ff ff')]),
       ('00:00:02.000', None, '', []),
       ('00:00:03.000', '2', 'Top green', [('4', '9', 'Normal', '0 ff 0 ff')]),
       ('00:00:04.000', None, '', [])])
check('the text escaped as GPAC escapes it', '&quot;you&quot;' in written, True)
lit = ET.fromstring(ttxt.write([{'start': 0, 'end': 2, 'text': 'Oh {\\k50}Hel{\\k30}lo {\\k20}{\\k60}world'}]).encode())
check('karaoke: a range a syllable, a pause lighting nothing, the text before the first not lit',
      [(r.get('fromChar'), r.get('toChar'), r.get('endTime')) for r in lit.iter('KaraokeRange')],
      [('3', '6', '0.5'), ('6', '9', '0.8'), ('9', '9', '1'), ('9', '14', '1.6')])
check('blinking and sizes; an emoji counts two units',
      [(c.tag, c.get('fromChar'), c.get('toChar'), c.get('fontSize')) for c in ET.fromstring(ttxt.write(
          [{'start': 0, 'end': 1, 'text': '😀<blink>on</blink> <font size="200%">big</font>'}]).encode()).find('TextSample')],
      [('Blinking', '2', '4', None), ('Style', '5', '8', '36')])
check('readings and what tx3g has no record for left out',
      ET.fromstring(ttxt.write([{'start': 0, 'end': 1, 'text': '<ruby>漢字<rt>かんじ</rt></ruby><font emphasis="filled dot">!</font>'}])
                    .encode()).find('TextSample').text, '漢字!')

print('round trips')
trip = [
    {'start': 0.5, 'end': 2.0, 'text': '<b>Bold</b>, <i>italic</i>, <u>under</u>, <s>struck</s>\nand <font color="#ffff00">yellow</font>'},
    {'start': 2.0, 'end': 3.25, 'text': '{\\an7}Top left <font size="150%">large</font> <blink>flash</blink>'},
    {'start': 4.0, 'end': 6.0, 'text': '{\\an6}右の列', 'writing': 'vertical-rl'},
    {'start': 6.0, 'end': 8.0, 'text': 'Oh {\\k50}Hel{\\k30}lo {\\k20}{\\k60}world'},
    {'start': 8.0, 'end': 9.0, 'text': '{\\an8}{\\k40}Sung {\\k60}first'},
    {'start': 9.0, 'end': 10.0, 'text': '😀 <i>after an emoji</i> & <less> than'},
]
check('read back as written', ttxt.parse(ttxt.write(trip, 1280, 720)), trip)
check('written again as it was read', ttxt.write(ttxt.parse(ttxt.write(trip))), ttxt.write(trip))
check('what it loses: an overlap ends where the next begins',
      [(s['start'], s['end']) for s in ttxt.parse(ttxt.write([{'start': 0, 'end': 3, 'text': 'a'}, {'start': 2, 'end': 4, 'text': 'b'}]))],
      [(0.0, 2.0), (2.0, 4.0)])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.ttxt').write_text(SAMPLE, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.ttxt'))
check('opens as TTXT', (fmt, len(opened), opened[2].get('writing')), ('TTXT', 4, 'vertical-rl'))
(folder / 'transcript.ttxt').write_bytes(TRANSCRIPT)
try:
    file_io.process_subtitles_file(str(folder / 'transcript.ttxt'))
    check("a CCExtractor transcript's .ttxt: says what it is", None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check("a CCExtractor transcript's .ttxt: says what it is", 'CCExtractor' in str(error), True)
session.SUBTITLE['segments'] = trip
session.VIDEO = {'width': 1280, 'height': 720, 'filepath': '/videos/clip.mp4'}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.ttxt'), 'TTXT')
check("saved at the video's size, and read back", (ET.parse(folder / 'out.ttxt').getroot().find('TextStreamHeader').get('height'),
                                                    ttxt.read(str(folder / 'out.ttxt')) == trip), ('720', True))
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['TTXT']['extensions'], ['ttxt'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTXT')
panel._refresh_options()
check('TTXT in the export menu, with the text options, no frame rate',
      ('TTXT' in SUBTITLE_FORMATS, panel.format_combo.currentText(), panel.text_options.isHidden(), panel.sub_options.isHidden()),
      (True, 'TTXT', False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
