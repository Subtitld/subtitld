"""MacCaption (.mcc): reading the captions of its packets, built here packet
by packet (CEA-608 with one pair a frame or several, the second field when
the first is empty, SMPTE 334's 608 packets; CEA-708 first, or alone;
MCC's letters, the header's rate), writing what MacCaption writes (the
header whole, a packet a frame, checksums, each rate's cc_data count,
CEA-608 and CEA-708 or CEA-608 alone) and the export's options.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import datetime, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import cea708, file_io, mcc, scc, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def chars(text):
    return ' '.join(scc._words([('b', scc._TO_BASIC[c]) for c in text]))


def code(first, second):
    return ' '.join([scc._word(first, second)] * 2)


def pac(row, indent=0):
    first, base = scc._PAC_ROWS[row]
    return code(first, base + 0x10 + indent // 4 * 2)


RCL, ENM, EOC, EDM = code(0x14, 0x20), code(0x14, 0x2E), code(0x14, 0x2F), code(0x14, 0x2C)
SCC = 'Scenarist_SCC V1.0\n\n' + '\n\n'.join(f'{tc}\t{words}' for tc, words in (
    ('00:00:00;20', f'{RCL} {ENM} {pac(14, 12)} {chars("You")} {code(0x11, 0x2E)} {chars("jerk,")} {pac(15, 12)} {chars("Celia.")}'),
    ('00:00:01;00', EOC),
    ('00:00:01;20', f'{RCL} {ENM} {pac(1, 8)} {code(0x11, 0x2A)} {chars("Yellow at the top")}'),
    ('00:00:02;15', EOC),
    ('00:00:03;00', EDM),
    ('00:01:00;02', f'{RCL} {ENM} {pac(15, 0)} {chars("A minute on")}'),
    ('00:01:01;00', EOC),
    ('00:01:02;00', EDM))) + '\n'
WANT = [(1.001, 2.502, 'You <i>jerk,</i>\nCelia.'),
        (2.502, 3.003, '{\\an8}<font color="#ffff00">Yellow at the top</font>'),
        (60.994, 61.995, '{\\an1}A minute on')]


def cues(text):
    return [(s['start'], s['end'], s['text']) for s in mcc.parse(text)[0]]


def anc(pairs, field=1, rate_code=4, count=20, extra=b''):
    """An ANC packet: a CDP with `pairs` for `field` (and the caption data
    `extra`), padded to `count`."""
    cc = b''.join(bytes([0xFC if field == 1 else 0xFD, *pair]) for pair in pairs)
    if field == 2:
        cc = b'\xfc\x80\x80' * len(pairs) + cc
    cc += extra
    extra = b''
    cc += b'\xfa\x00\x00' * (count - len(cc) // 3)
    body = bytes([0x72, 0xE0 | count]) + cc + extra + b'\x74\x00\x00'
    cdp = bytes([0x96, 0x69, 7 + len(body) + 1, rate_code << 4 | 0x0F, 0x43, 0, 0]) + body
    cdp += bytes([-sum(cdp) & 0xFF])
    packet = bytes([0x61, 0x01, len(cdp)]) + cdp
    return packet + bytes([sum(packet) & 0xFF])


def at_25(frame):
    return f'{frame // 90000:02d}:{frame // 1500 % 60:02d}:{frame // 25 % 60:02d}:{frame % 25:02d}'


def to_mcc(scc_text, rate='30DF', bunched=False, timecode=None, **packet):
    """The SCC's bytes as an MCC file, its frames counted at `rate`: a
    packet a frame, or a line's bytes in one packet."""
    timecode = timecode or (lambda frame: scc.timecode(frame, rate.endswith('DF')))
    lines = [f'File Format=MacCaption_MCC V1.0\n\nTime Code Rate={rate}\n']
    for frame, words in scc._lines(scc_text):
        for offset, batch in ([(0, words)] if bunched else [(offset, [word]) for offset, word in enumerate(words)]):
            lines.append(f'{timecode(frame + offset)}\t{anc(batch, **packet).hex().upper()}')
    return '\n'.join(lines) + '\n'


print('reading')
check('a packet a frame: what the SCC holds, its times in drop-frame', cues(to_mcc(SCC)), WANT)
check('the same as the SCC module reads it', cues(to_mcc(SCC)), [(s['start'], s['end'], s['text']) for s in scc.parse(SCC)])
check("a line's pairs in one packet, as FFmpeg writes them", cues(to_mcc(SCC, bunched=True)), WANT)
check('the second field when the first holds nothing', cues(to_mcc(SCC, field=2)), WANT)
check('SMPTE 334 608 packets', cues('File Format=MacCaption_MCC V1.0\nTime Code Rate=30DF\n' + '\n'.join(
    f'{scc.timecode(frame + offset, True)}\t' + bytes([0x61, 0x02, 0x03, 0x80, *word, 0]).hex()
    for frame, words in scc._lines(SCC) for offset, word in enumerate(words))), WANT)
check("MCC's letters", mcc._expand('T S GHO P Q R U V W X Y Z 0a'), bytes.fromhex(
    '6101' '9669' + 'FA0000' * 12 + 'FB8080' 'FC8080' 'FD8080' 'E1000000' 'FC8080' 'FC8080' '00' '0A'))
check('a line in letters, as MacCaption writes it', cues(
    'File Format=MacCaption_MCC V1.0\nTime Code Rate=30DF\n'
    '00:00:00;06\tT49S494F43ZZ72F4FC9420ROO74ZZ0BAB\n00:00:00;07\tT49S494F43Z0172F4FC9420ROO74Z010AAB\n'
    + '\n'.join(f'00:00:00;{8 + i:02d}\tT49S494F43ZZ72F4FC{word}ROO74ZZ0BAB' for i, word in enumerate(
        ('9452', '9452', 'c8e5', 'ecec', 'ef80', '942f', '942f'))) + '\n'), [(0.434, 4.434, '{\\an1}Hello')])
AT_25 = to_mcc(SCC, rate='25', timecode=at_25, rate_code=3, count=24)
check('at 25: the header says so, and the packets', (cues(AT_25)[0][:2], mcc.parse(AT_25)[1]),
      ((1.2, 3.0), {'rate': '25', 'captions': '608'}))
check('the rate the export offers, by the header and the packets', [mcc.parse(to_mcc(SCC, rate=label, rate_code=code))[1]['rate']
      for label, code in (('30DF', 4), ('30', 4), ('30', 5), ('24', 1), ('24', 2), ('60', 8))],
      ['29.97DF', '29.97', '30', '23.976', '24', None])
# CEA-708 as the cea708 module writes it: in a file with CEA-608 too, it is read first; alone, it is read.
DTVCC_DATA = cea708.frame_data([('{\\an8}<font color="#ffd700">Only in 708</font>', 30, 60)], 18)
CEA608_TOO = to_mcc(SCC.split('\n\n00:00:01;20')[0])
ONLY_708 = 'File Format=MacCaption_MCC V1.0\nTime Code Rate=30DF\n' + '\n'.join(
    f'{scc.timecode(frame, True)}\t' + anc([], extra=b''.join(bytes([0xFF if t == 3 else 0xFE, a, b]) for t, a, b in data)).hex()
    for frame, data in sorted(DTVCC_DATA.items()))
BOTH = 'File Format=MacCaption_MCC V1.0\nTime Code Rate=30DF\n' + '\n'.join(
    line for line in sorted(CEA608_TOO.split('\n')[3:] + ONLY_708.split('\n')[2:]) if line)
check('CEA-708 alone: read; with CEA-608 too, read first; the file says it has both',
      (cues(ONLY_708), cues(BOTH), mcc.parse(BOTH)[1]['captions']),
      ([(1.001, 2.002, '{\\an8}<font color="#ffff00">Only in 708</font>')],
       [(1.001, 2.002, '{\\an8}<font color="#ffff00">Only in 708</font>')], 'both'))
for name, text, start in (('no captions at all: says so', 'File Format=MacCaption_MCC V1.0\n00:00:01;00\t' + anc([]).hex(),
                           'This MacCaption file holds no captions'),
                          ('not MCC: refused', 'Scenarist_SCC V1.0\n', 'Not a MacCaption file')):
    try:
        mcc.parse(text)
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)

print('writing')
segments = [
    {'start': 1.0, 'end': 2.5, 'text': "You're a <i>jerk</i>,\nCelia.", 'speaker': 'Celia'},
    {'start': 2.5, 'end': 4.0, 'text': '{\\an8}<font color="#ffd700">Gold at the top</font>', 'speaker': 'Celia'},
    {'start': 61.0, 'end': 63.0, 'text': 'A minute on', 'speaker': 'Thom'},
]
now = datetime.datetime(2026, 10, 11, 9, 5, 7)
written = mcc.write(segments, '29.97DF', now=now)
head, _blank, body = written.partition('\n\n' + 'UUID=')
check('the header whole, then the file\'s own', (written.startswith(mcc.HEADER), body.split('\n')[1:6]), (True, [
    'Creation Program=Subtitld', 'Creation Date=Sunday, October 11, 2026', 'Creation Time=9:05:07', 'Time Code Rate=30DF', '']))
data_lines = [line.split('\t') for line in written.split('\n') if '\t' in line]
check('the first packet, as MacCaption writes it', '\t'.join(data_lines[0]), '00:00:00;06\tT49S494F43ZZ72F4FC9420ROO74ZZ0BAB')
check('CEA-608 alone: no CEA-708 in any packet', all(
    b'\xfe' not in cdp[9:-4:3] and b'\xff' not in cdp[9:-4:3] for cdp in (mcc._expand(data)[3:-1] for _tc, data in (
        line.split('\t') for line in mcc.write(segments, captions='608').split('\n') if '\t' in line))), True)
for rate, (label, nominal, fps, drop, rate_code, count) in mcc.RATES.items():
    text = mcc.write(segments, rate)
    lines = [line.split('\t') for line in text.split('\n') if '\t' in line]
    packets = [mcc._expand(data) for _tc, data in lines]
    cdps = [packet[3:-1] for packet in packets]
    markers = [[cdp[9 + 3 * i] for i in range(count)] for cdp in cdps]
    check(f'{rate}: packets DID 61h SDID 01h, their checksums, the rate, {count} cc_data: field 1, field 2, CEA-708, padding', (
        f'Time Code Rate={label}\n' in text,
        all(packet[:2] == b'\x61\x01' and packet[2] == len(packet) - 4 and sum(packet[:-1]) & 0xFF == packet[-1]
            for packet in packets),
        all(sum(cdp) & 0xFF == 0 and cdp[2] == len(cdp) and cdp[3] == rate_code << 4 | 0x0F and cdp[7:9] == bytes([0x72, 0xE0 | count])
            and cdp[12:15] == b'\xfd\x80\x80' for cdp in cdps),
        all(m[0] == 0xFC and m[1] == 0xFD and all(x in (0xFE, 0xFF) for x in m[2:len(m) - m.count(0xFA)])
            and all(x == 0xFA for x in m[len(m) - m.count(0xFA):]) for m in markers),
        any(0xFF in m for m in markers),
        [int.from_bytes(cdp[5:7], 'big') for cdp in cdps] == list(range(len(cdps)))),
          (True, True, True, True, True, True))
    check(f'{rate}: read back through CEA-708, the times to the frame', [
        (s['text'], abs(s['start'] - want['start']) <= 1 / fps, abs(s['end'] - want['end']) <= 1 / fps)
        for s, want in zip(mcc.parse(text)[0], segments)], [
            ("You're a <i>jerk</i>,\nCelia.", True, True), ('{\\an8}<font color="#ffff00">Gold at the top</font>', True, True),
            ('A minute on', True, True)])
check('CEA-608 alone, read back as SCC reads it', [s['text'] for s in mcc.parse(mcc.write(segments, captions='608'))[0]][:1],
      ["You're a <i>jerk,</i>\nCelia."])
shown_at = [{tc: mcc._expand(data)[13:15].hex() for tc, data in (line.split('\t') for line in mcc.write(segments[2:], rate).split('\n')
                                                                 if '\t' in line)} for rate in ('29.97DF', '29.97')]
check('timecodes: drop-frame skips, non-drop does not (shown at 61 s: frame 1828)',
      (shown_at[0].get('00:01:01;00'), shown_at[1].get('00:01:00:28')), ('942f', '942f'))
check('>> at each change of speaker', [s['text'][:3] for s in mcc.parse(mcc.write(segments, speaker_changes=True))[0]],
      ['>> ', '{\\a', '>> '])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.mcc').write_text(AT_25, encoding='ascii')
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.mcc'))
check('opens; its rate, and what it carries, kept to save it again', (
    fmt, len(opened), session.FORMAT['options'].get('mcc_rate'), session.FORMAT['options'].get('mcc_captions')),
      ('MCC', 3, '25', '608'))
session.SUBTITLE['segments'] = segments
session.SUBTITLE['language'] = 'en-us'
session.VIDEO = {'framerate': 25.0}
for name, options, framerate, want in (
        ('saved at the rate asked; >> marks; Windows line ends', {'mcc_rate': '23.976', 'speakers': 'voice'}, 25.0,
         ('Time Code Rate=24', 1, '>> ', True)),
        ("else the video's; CEA-608 alone when asked", {'mcc_captions': '608'}, 25.0, ('Time Code Rate=25', 3, "You", True)),
        ('else 29.97 drop-frame', {}, 50.0, ('Time Code Rate=30DF', 4, "You", True))):
    session.FORMAT = {'options': options}
    session.VIDEO = {'framerate': framerate}
    file_io.save_file(str(folder / 'out.mcc'), 'MCC')
    saved = (folder / 'out.mcc').read_bytes()
    text = saved.decode('ascii').replace('\r\n', '\n')
    first = mcc._expand(next(line for line in text.split('\n') if '\t' in line).split('\t')[1])
    check(name, (next(line for line in text.split('\n') if line.startswith('Time Code Rate=')), first[6] >> 4,
                 mcc.parse(text)[0][0]['text'][:3], b'\r\n' in saved), want)
(folder / 'not.mcc').write_text('Hello', encoding='ascii')
try:
    file_io.process_subtitles_file(str(folder / 'not.mcc'))
    check('not MCC: says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('not MCC: says so', str(error).startswith('Not a MacCaption file'), True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 30000 / 1001}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('MCC')
panel._refresh_options()
combo, rates = panel.speakers_combo.combobox, panel.mcc_rate_combo.combobox
check("MCC: its rate, the video's, drop-frame at 29.97; >> for speakers", (
    panel.mcc_options.isHidden(), panel.text_options.isHidden(), panel.get_config()['options'].get('mcc_rate'),
    rates.currentText(), combo.itemText(combo.count() - 1)),
      (False, False, '29.97DF', "29.97 fps, drop-frame (00:00:01;00) (the video's)", 'As >> at each change of speaker'))
check('CEA-608 and CEA-708 unless asked', (panel.mcc_captions_combo.combobox.currentText(),
                                            panel.get_config()['options'].get('mcc_captions')), ('CEA-608 and CEA-708', 'both'))
rates.setCurrentIndex(rates.findData('25'))
panel.mcc_captions_combo.combobox.setCurrentIndex(panel.mcc_captions_combo.combobox.findData('608'))
check('chosen', {key: panel.get_config()['options'].get(key) for key in ('mcc_rate', 'mcc_captions')},
      {'mcc_rate': '25', 'mcc_captions': '608'})
session.VIDEO = {}
check('remembered, without a video', (_SubtitlesPanel().mcc_rate_combo.combobox.currentData(),
                                      _SubtitlesPanel().mcc_captions_combo.combobox.currentData()), ('25', '608'))
panel.format_combo.setCurrentText('SCC')
panel._refresh_options()
check('not for SCC', (panel.mcc_options.isHidden(), 'mcc_rate' in panel.get_config()['options'],
                      'mcc_captions' in panel.get_config()['options']), (True, False, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
