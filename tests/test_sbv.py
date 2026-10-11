"""SBV, YouTube's SubViewer: reading what YouTube exports and people edit
(missing and extra blank lines, loose times, [br], ">> NAME: " speakers,
encodings), writing what YouTube takes, and the export's options.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, sbv, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text):
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ()) for s in sbv.parse(text)]


# YouTube's own example, as its help shows SBV.
YOUTUBE = """0:00:00.599,0:00:04.160
>> ALICE: Hi, my name is Alice Miller and this is John Brown

0:00:04.160,0:00:06.770
>> JOHN: and we're the owners of Miller Bakery.

0:00:06.770,0:00:10.880
>> ALICE: Today we'll be teaching you how to make
our famous chocolate chip cookies!
"""

print('reading')
check("YouTube's example: >> NAME: is the speaker", cues(YOUTUBE), [
    (0.599, 4.16, 'Hi, my name is Alice Miller and this is John Brown', 'ALICE'),
    (4.16, 6.77, "and we're the owners of Miller Bakery.", 'JOHN'),
    (6.77, 10.88, "Today we'll be teaching you how to make\nour famous chocolate chip cookies!", 'ALICE')])
check('missing and extra blank lines, loose times, [br], sorted', cues(
    '\n\n0:00:05.000,0:00:06.000\nLater\n0:00:01.5, 0:00:02,25\nOne[br]two\n\n\n\n00:00:03.1234,0:00:04.000\nThree\n\n'), [
    (1.5, 2.25, 'One\ntwo'), (3.123, 4.0, 'Three'), (5.0, 6.0, 'Later')])
check('the speaker goes on until the next >>; >> with no name ends it and stays; a long "name" or a colon later on is '
      'no speaker', cues(
    '0:00:00.000,0:00:00.500\n>> THOM: First\n\n0:00:00.500,0:00:00.900\nStill Thom\n\n'
    '0:00:01.000,0:00:02.000\n>> Hello there\n\n0:00:03.000,0:00:04.000\n>> This is far too long to be a name: hi\n\n'
    '0:00:05.000,0:00:06.000\nNote: no chevrons\n'), [
    (0.0, 0.5, 'First', 'THOM'), (0.5, 0.9, 'Still Thom', 'THOM'),
    (1.0, 2.0, '>> Hello there'), (3.0, 4.0, '>> This is far too long to be a name: hi'), (5.0, 6.0, 'Note: no chevrons')])
check('formatting tags written into it are kept; an empty subtitle is none', cues(
    '0:00:01.000,0:00:02.000\n<i>Italic</i>\n\n0:00:03.000,0:00:04.000\n\n0:00:05.000,0:00:06.000\nLast\n'),
      [(1.0, 2.0, '<i>Italic</i>'), (5.0, 6.0, 'Last')])
check('Windows-1252 and a byte order mark', (
    sbv.parse(sbv.decode('0:00:01.000,0:00:02.000\nAção é café\n'.encode('cp1252') * 3))[0]['text'],
    len(sbv.parse(sbv.decode(b'\xef\xbb\xbf0:00:01.000,0:00:02.000\r\nHi\r\n')))), ('Ação é café', 1))

print('writing')
segments = [
    {'start': 4.16, 'end': 6.77, 'text': "and we're the owners of <i>Miller Bakery</i>.", 'speaker': 'JOHN'},
    {'start': 0.599, 'end': 4.16, 'text': '{\\an8}Hi, my name is Alice\n\nand this is John', 'speaker': 'ALICE'},
    {'start': 6.77, 'end': 10.88, 'text': 'Same speaker, no mark', 'speaker': 'JOHN'},
    {'start': 3661.5, 'end': 3662.0, 'text': 'After an hour', 'speaker': 'ALICE'},
    {'start': 11.0, 'end': 12.0, 'text': ' <i> </i> '},
]
check('times as H:MM:SS.mmm, in order, plain text, no blank line inside, empty ones left out', sbv.write(segments),
      "0:00:00.599,0:00:04.160\nHi, my name is Alice\nand this is John\n\n"
      "0:00:04.160,0:00:06.770\nand we're the owners of Miller Bakery.\n\n"
      "0:00:06.770,0:00:10.880\nSame speaker, no mark\n\n"
      "1:01:01.500,1:01:02.000\nAfter an hour\n")
with_speakers = sbv.write(segments, speakers=True)
check('>> NAME: where a new speaker begins', [block.split('\n')[1] for block in with_speakers.strip().split('\n\n')],
      ['>> ALICE: Hi, my name is Alice', ">> JOHN: and we're the owners of Miller Bakery.", 'Same speaker, no mark',
       '>> ALICE: After an hour'])
check('read back: the speakers, the unmarked one too', [s.get('speaker') for s in sbv.parse(with_speakers)],
      ['ALICE', 'JOHN', 'JOHN', 'ALICE'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.sbv').write_text(YOUTUBE, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.sbv'))
check('opens', (fmt, len(opened), opened[0]['speaker']), ('SBV', 3, 'ALICE'))
session.SUBTITLE['segments'] = opened
for options, want in (({'speakers': 'voice'}, '>> ALICE: Hi, my name'), ({'speakers': 'prefix'}, 'ALICE: Hi, my name is'),
                      ({'speakers': 'none', 'offset': 1.0}, 'Hi, my name is Alice ')):
    session.FORMAT['options'] = options
    file_io.save_file(str(folder / 'out.sbv'), 'SBV')
    written = (folder / 'out.sbv').read_text(encoding='utf-8')
    check(f'exported, {options}', written.split('\n')[1][:21], want)
check('shifted', written.split('\n')[0], '0:00:01.599,0:00:05.160')

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SBV')
panel._refresh_options()
combo = panel.speakers_combo.combobox
check("SBV: the text options, YouTube's >> NAME:, no formatting to choose", (
    panel.text_options.isHidden(), combo.itemText(combo.count() - 1), panel.formatting_combo.isHidden()),
      (False, "As >> NAME: (YouTube's way)", True))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('SRT: formatting again', panel.formatting_combo.isHidden(), False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
