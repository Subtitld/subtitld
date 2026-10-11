"""Scenarist SCC: timecodes (drop-frame or not), decoding CEA-608 as a
caption decoder does (pop-on, roll-up and paint-on captions, PAC and
mid-row styles, special and extended characters, codes sent twice, the
second channel, backspace and delete to end of row), writing pop-on
captions shown on their frames, and the export's options.

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
from subtitld.modules import file_io, scc, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def chars(text):
    return ' '.join(scc._words([('b', scc._TO_BASIC[c]) for c in text]))


def raw(*codes):
    return ' '.join(scc._words([('b', byte) for byte in codes]))


def code(first, second, twice=True):
    return ' '.join([scc._word(first, second)] * (2 if twice else 1))


def doc(*lines):
    return 'Scenarist_SCC V1.0\n\n' + '\n\n'.join(f'{tc}\t{words}' for tc, words in lines) + '\n'


def cues(text):
    return [(s['start'], s['end'], s['text']) for s in scc.parse(text)]


RCL, ENM, EOC, EDM = code(0x14, 0x20), code(0x14, 0x2E), code(0x14, 0x2F), code(0x14, 0x2C)
RU2, CR, RDC, BS, DER = code(0x14, 0x25), code(0x14, 0x2D), code(0x14, 0x29), code(0x14, 0x21), code(0x14, 0x24)


def pac(row, indent=0, look=None, underline=False):
    first, base = scc._PAC_ROWS[row]
    return code(first, base + (look * 2 if look is not None else 0x10 + indent // 4 * 2) + int(underline))


print('timecodes')
check('drop-frame: frames 0 and 1 dropped each minute but every tenth',
      [scc.frame_of(tc) for tc in ('00:00:00;00', '00:01:00;02', '00:10:00;00', '01:00:00;00')], [0, 1800, 17982, 107892])
check('non-drop-frame counts every frame', [scc.frame_of(tc) for tc in ('00:00:10:00', '01:00:00:00')], [300, 108000])
check('and back', [scc.timecode(frame, drop) for frame, drop in ((1800, True), (17982, True), (107892, True), (300, False))],
      ['00:01:00;02', '00:10:00;00', '01:00:00;00', '00:00:10:00'])
check('29.97 frames a second', (scc._seconds(30), scc._seconds(107892)), (1.001, 3599.996))

print('pop-on captions')
POP = doc(
    ('00:00:00;20', f'{RCL} {ENM} {pac(14, 12)} {chars("You")} {code(0x11, 0x2E)} {chars("jerk,")} {pac(15, 12)} {chars("Celia.")}'),
    ('00:00:01;00', EOC),
    ('00:00:01;20', f'{RCL} {ENM} {pac(1, 8)} {code(0x11, 0x2A)} {chars("Yellow at the top")}'),
    ('00:00:02;15', EOC),
    ('00:00:03;00', EDM),
    ('00:00:04;00', f'{RCL} {ENM} {pac(15, 0, look=7)} {chars("Italic by PAC")} {code(0x11, 0x20)} {chars("plain")}'),
    ('00:00:04;20', EOC),
    ('00:00:05;00', f'{RCL} {ENM} {pac(15, 20)} {chars("Right ")} {code(0x11, 0x29)} {chars("red")}'),
    ('00:00:05;20', EOC),
    ('00:00:06;00', f'{RCL} {ENM} {pac(15, 0)} {chars("Lone")}'),
    ('00:00:06;20', EOC),
    ('00:00:07;00', EDM),
)
check('shown at EOC, cleared at EDM or by the next; italics by mid-row code, its space not before a comma; rows and '
      'columns as places', cues(POP), [
          (1.001, 2.502, 'You <i>jerk,</i>\nCelia.'),
          (2.502, 3.003, '{\\an8}<font color="#ffff00">Yellow at the top</font>'),
          (4.671, 5.672, '{\\an1}<i>Italic by PAC</i> plain'),
          (5.672, 6.673, '{\\an3}Right <font color="#ff0000"><u>red</u></font>'),
          (6.673, 7.007, '{\\an1}Lone')])
CHARACTERS = doc(
    ('00:00:00;00', f'{RCL} {ENM} {pac(15, 0)} {chars("A")} {code(0x11, 0x38)} {chars(" la caf")} '
                    f'{code(0x13, 0x22, twice=False)} {chars("e")} {code(0x12, 0x21)} {chars(" ")} '
                    f'{code(0x11, 0x37)} {code(0x11, 0x37)} '
                    f'{chars("n")} {code(0x13, 0x28)} {chars("o ")} {raw(0x2A, 0x7D, 0x7E)}'),
    ('00:00:01;00', EOC),
    ('00:00:02;00', EDM),
)
check('special characters; an extended one replaces the fallback before it; codes twice count once, four times twice; '
      "the basic set's own letters", cues(CHARACTERS)[0][2], '{\\an1}Aà la caÍÉ ♪♪õo áÑñ')
check('the cursor past the last column: what follows takes its place', cues(doc(
    ('00:00:00;00', f'{RCL} {ENM} {pac(15, 0)} {chars("x" * 31 + "c")} {code(0x12, 0x2B)}'),
    ('00:00:01;00', EOC), ('00:00:02;00', EDM)))[0][2], 'x' * 31 + '©')
check('backspace, delete to end of row, tab offsets', cues(doc(
    ('00:00:00;00', f'{RCL} {ENM} {pac(15, 4)} {code(0x17, 0x22)} {chars("Typoo")} {BS} {chars(" and junk")} '
                    f'{pac(15, 12)} {code(0x17, 0x22)} {DER}'),
    ('00:00:01;00', EOC), ('00:00:02;00', EDM)))[0][2], '{\\an1}Typo and')
check('the other channel is not this one', cues(doc(
    ('00:00:00;00', f'{RCL} {ENM} {pac(15, 0)} {chars("One")} {code(0x1C, 0x20)} {chars("Two")} {code(0x14, 0x2F)}'),
    ('00:00:02;00', EDM)))[0][2], '{\\an1}One')
check('a file with only the second channel reads it', cues(doc(
    ('00:00:00;00', f'{code(0x1C, 0x20)} {code(0x1C, 0x2E)} {code(0x1C, 0x60)} {chars("Second")} {code(0x1C, 0x2F)}'),
    ('00:00:02;00', code(0x1C, 0x2C)))), [(0.3, 2.002, '{\\an1}Second')])
check('the last caption, never cleared: four seconds', cues(doc(
    ('00:00:00;00', f'{RCL} {ENM} {pac(15, 0)} {chars("End")} {EOC}')))[0][1], 4.267)

print('roll-up and paint-on')
MODES = doc(
    ('00:00:01;00', f'{RU2} {pac(15)} {chars("HELLO, THIS IS")}'),
    ('00:00:02;00', chars(' A ROLL-UP TEST.')),
    ('00:00:03;00', f'{CR} {pac(15)} {chars("SECOND LINE.")}'),
    ('00:00:05;00', EDM),
    ('00:00:10;00', f'{RDC} {pac(15, 4)} {chars("Painted ")}'),
    ('00:00:10;15', chars('word by word')),
    ('00:00:12;00', EDM),
    ('00:00:13;00', f'{RDC} {pac(13)} {chars("Top row")} {pac(15)} {chars("and bottom")}'),
    ('00:00:14;00', f'{pac(15)} {DER}'),
    ('00:00:15;00', EDM),
)
check('roll-up: a line a subtitle, until the next begins; paint-on: what it paints, as it grows, then as it changes',
      cues(MODES), [(1.134, 3.136, 'HELLO, THIS IS A ROLL-UP TEST.'), (3.136, 5.005, 'SECOND LINE.'),
                    (10.143, 12.012, 'Painted word by word'), (13.146, 14.081, '{\\an1}Top row\nand bottom'),
                    (14.081, 15.015, '{\\an1}Top row')])

print('writing')
segments = [
    {'start': 1.0, 'end': 2.5, 'text': "You're a <i>jerk</i>,\nCelia.", 'speaker': 'Celia'},
    {'start': 2.5, 'end': 4.0, 'text': '{\\an8}<font color="#ffd700">Gold at the top</font>', 'speaker': 'Celia'},
    {'start': 5.0, 'end': 7.0, 'text': 'Ação, café, naïve — “quotes” ♪ ½ ñ ü…', 'speaker': 'Thom'},
    {'start': 8.0, 'end': 10.0, 'text': 'A line much longer than thirty-two characters, which must wrap'},
    {'start': 12.0, 'end': 13.0, 'text': '{\\an1}Left <i>italic start</i>'},
    {'start': 13.0, 'end': 14.0, 'text': '{\\an3}<i>Right</i>, ok 中文'},
    {'start': 15.0, 'end': 16.0, 'text': '  <i> </i> '},
]
written = scc.write(segments)
lines = [line for line in written.split('\n') if '\t' in line]
check('Scenarist_SCC V1.0, drop-frame timecodes', (written.startswith('Scenarist_SCC V1.0\n\n'), lines[1].split('\t')[0]),
      (True, '00:00:01;00'))
check('each EOC and EDM begins a line, at its frame', [line[:22] for line in lines if line.split('\t')[1][:9] in (EOC, EDM)][:4],
      [f'00:00:01;00\t{EOC}', f'00:00:02;15\t{EOC}', f'00:00:04;00\t{EDM} ', f'00:00:05;00\t{EOC}'])
check('no EDM between subtitles one after the other', '00:00:02;15\t' + EDM in written, False)
check('a row centred: the PAC at the nearest four columns, a tab offset for the rest, the mid-row code where the space was',
      lines[0].split('\t')[1].split()[4:14],
      ['9454', '9454', '97a1', '97a1', 'd9ef', '75a7', 'f2e5', '2061', '91ae', '91ae'])
back = scc.parse(written)
check('read back as written, on the same frames', [(s['start'], s['end'], s['text']) for s in back], [
    (1.001, 2.502, "You're a <i>jerk,</i>\nCelia."),
    (2.502, 4.004, '{\\an8}<font color="#ffff00">Gold at the top</font>'),
    (5.005, 7.007, 'Ação, café, naïve — “quotes” ♪ ½\nñ ü...'),
    (8.008, 10.01, 'A line much longer than\nthirty-two characters, which\nmust wrap'),
    (12.012, 13.013, '{\\an1}Left <i>italic start</i>'),
    (13.013, 14.014, '{\\an3}<i>Right,</i> ok ??')])
check('an extended character after its fallback', 'c1fb 6180 13a1 13a1' in written, True)
check('non-drop-frame', scc.write(segments[:1], drop_frame=False).split('\n')[4].split('\t')[0], '00:00:01:00')
chevrons = scc.parse(scc.write(segments, speaker_changes=True))
check('>> at each change of speaker', [s['text'].split('\n')[0][:12] for s in chevrons][:4],
      [">> You're a ", '{\\an8}<font ', '>> Ação, caf', 'A line much '])
crowded = scc.write([{'start': 1.0, 'end': 1.1, 'text': 'A' * 32 + '\n' + 'B' * 32},
                     {'start': 1.1, 'end': 3.0, 'text': 'C' * 32 + '\n' + 'D' * 32}])
crowded_frames = [scc.frame_of(line.split('\t')[0]) + index for line in crowded.split('\n') if '\t' in line
                  for index, _word in enumerate(line.split('\t')[1].split())]
check('too close to load in time: shown as soon as it is loaded (40 frames each), no frame used twice',
      ([round(s['start'], 2) for s in scc.parse(crowded)], len(crowded_frames) == len(set(crowded_frames))), ([1.33, 2.74], True))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.scc').write_text(POP, encoding='ascii')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.scc'))
check('opens', (fmt, len(opened), opened[1]['text']), ('SCC', 5, '{\\an8}<font color="#ffff00">Yellow at the top</font>'))
session.SUBTITLE['segments'] = segments
session.SUBTITLE['language'] = 'en-us'
session.FORMAT['options'] = {'timecode': 'non-drop', 'speakers': 'voice'}
file_io.save_file(str(folder / 'out.scc'), 'SCC')
saved = (folder / 'out.scc').read_bytes()
check('exported: non-drop-frame, >> marks, Windows line ends', (
    b'00:00:01:00\t' in saved, scc.parse(saved.decode('ascii'))[0]['text'][:3], b'\r\n' in saved), (True, '>> ', True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SCC')
panel._refresh_options()
combo = panel.speakers_combo.combobox
check('SCC: its timecode, >> for speakers', (panel.scc_options.isHidden(), panel.timecode_combo.combobox.currentData(),
                                             combo.itemText(combo.count() - 1)), (False, 'drop', 'As >> at each change of speaker'))
panel.timecode_combo.combobox.setCurrentIndex(1)
check('chosen', panel.get_config()['options'].get('timecode'), 'non-drop')
check('remembered', _SubtitlesPanel().timecode_combo.combobox.currentData(), 'non-drop')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.scc_options.isHidden(), 'timecode' in panel.get_config()['options']), (True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
