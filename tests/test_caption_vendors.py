"""The caption houses' binaries: NCI's .cap in both of the layouts Subtitle
Edit reads its times from (1.2 and 2.0), CapMaker Plus (records, the lines
of a caption sharing a start, a cleared screen ending one, italics, the
places, the header's time every time counts from; Subtitle Edit's header
written and read back), Captions Inc.'s .cin and Ultech's .ult, built as
Subtitle Edit's readers and writers lay them out; every .cap told from
Cheetah's and Lambda's, and all through file_io and the dialog.

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
from subtitld.modules import caption_vendors as vendors, cheetahcap, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def timed(segments):
    return [(s['start'], s['end'], s['text']) for s in segments]


def tc(h, m, s, f):
    return bytes([h, m, s, f])


# -- NCI ----------------------------------------------------------------------
def nci_texts(texts):
    out = bytearray()
    for text in texts:
        out += b'\xff\xff\x00\x00' + text.ljust(60, b'\x00')
    return bytes(out + b'\xff\xff\x00\x00')


TEXTS = [b'Hello\x0d\x00\x00world \x87', b'Second', b'Third']
NCI_12 = bytearray(b'CAPT\x001.2'.ljust(128, b'\x00') + nci_texts(TEXTS))
NCI_12 = NCI_12.ljust(396, b'\x00')
for number, (start, end) in enumerate(((tc(0, 0, 1, 0), tc(0, 0, 3, 0)), (tc(0, 0, 4, 0), tc(0, 0, 5, 12)),
                                       (tc(0, 0, 6, 0), tc(0, 0, 7, 0))), 1):
    NCI_12 += b'\xff\xff\x01' + bytes(11) + bytes([number]) + bytes(3) + start + end + bytes(2)
NCI_12 = bytes(NCI_12 + b'\xff\xff\x01' + bytes(700))

records = bytearray()
for number, (start, end) in enumerate(((tc(0, 0, 1, 0), tc(0, 0, 3, 0)), (tc(0, 0, 4, 0), tc(0, 0, 5, 12)),
                                       (tc(0, 0, 6, 0), bytes(4)))):
    records += number.to_bytes(2, 'little') + bytes(4) + start + end
NCI_20 = (b'CAPT\x002.0'.ljust(230, b'\x00') + b'\x00\x00' + bytes(records) + b'\xff\xff\xff\xff').ljust(400, b'\x00')
NCI_20 = NCI_20 + nci_texts(TEXTS) + bytes(700)

print('NCI')
want = [(1.0, 3.0, 'Hello\nworld ♪'), (4.0, 5.48, 'Second'), (6.0, 7.0, 'Third')]
check('1.2: the texts, then the numbered times', timed(vendors.parse_nci(NCI_12)[0]), want)
check('2.0: runs of times, the last with a start only', timed(vendors.parse_nci(NCI_20)[0]), want)

# -- CapMaker Plus --------------------------------------------------------------
def capmaker_record(stamp, text, style=1, vertical=0xC0, across=b'\x80\xbf'):
    styled = bytearray([0, 0, across[0], across[1], 0, 0, 0, vertical, 2, 0, style, 0])
    return b'\x0b' + stamp.encode() + bytes(styled) + bytes([len(text)]) + text + bytes(74)


CAPMAKER = (b'+' + bytes(1402) + capmaker_record('00:00:01:00', b'First line') + capmaker_record('00:00:01:00', b'second line')
            + capmaker_record('00:00:03:00', b'') + capmaker_record('00:00:04:00', b'Italic top left', 3, 0, b'\x00\x00')
            + capmaker_record('00:00:06:12', b'Bottom right', 1, 0xC0, b'\x00\xc0') + bytes(40))

print('CapMaker Plus')
check('lines sharing a start, a cleared screen, italics, places', timed(vendors.parse_capmaker(CAPMAKER)[0]), [
    (1.0, 3.0, 'First line\nsecond line'), (4.0, 6.479, '{\\an7}<i>Italic top left</i>'), (6.48, 7.48, '{\\an3}Bottom right')])
shifted = bytearray(CAPMAKER)
shifted[1353:1365] = b'\x0b00:00:01:00'
check('the header\'s time counted from', timed(vendors.parse_capmaker(bytes(shifted))[0])[0][:2], (0.0, 2.0))
trip = [{'start': 1.0, 'end': 3.0, 'text': '{\\an8}<i>Top</i>'}, {'start': 3.04, 'end': 5.0, 'text': 'One\nTwo'},
        {'start': 7.0, 'end': 9.0, 'text': '{\\an3}Right'}]
written = vendors.write_capmaker(trip)
check('Subtitle Edit\'s header, the count with the gap record', (written[:18], written[1400:1402], len(written) > 2638),
      (b'+\'\x0f<CapMaker Plus>', b'\x04\x00', True))
check('read back', timed(vendors.parse_capmaker(written)[0]), [
    (1.0, 3.039, '{\\an8}<i>Top</i>'), (3.04, 5.0, 'One\nTwo'), (7.0, 9.0, '{\\an3}Right')])

# -- Captions Inc. --------------------------------------------------------------
def cin_record(start, end, lines):
    codes = b'\x14\x20\x14\x2e\x14\x54\x17' + (b'\x22' if len(lines) == 1 else b'\x21')
    text = codes + b''.join(line + b'\x14\x74' for line in lines)
    return (b'\x0d\x0a\xfe' + b'%03d' % len(text) + start + text + b'\x14\x2f\x0d\x0a\xfe002' + end + b'\x0d\x0a')


CIN = (b'CLIP'.ljust(256, b' ') + cin_record(b'00000100', b'00000300', [b'Ol\xe1', b'mundo'])
       + cin_record(b'00000400', b'00000512', [b'Second']) + bytes(30))

print('Captions Inc.')
check('times as eight digits, codes between lines', timed(vendors.parse_captions_inc(CIN)[0]),
      [(1.0, 3.0, 'Olá\nmundo'), (4.0, 5.48, 'Second')])

# -- Ultech ---------------------------------------------------------------------
def ultech_record(start, end, lines, italic=False):
    text = b''
    for index, line in enumerate(lines):
        if index:
            text += b'\x14\x74\x17\x21'
        text += (b'\x11\x2e' if italic else b'') + line
    body = b'\x14\x20\x14\x2e\x14' + bytes([0x74 - 0x20 * (len(lines) - 1)]) + b'\x17\x21' + text
    return (b'\xf1' + bytes([len(text) + 15, 0]) + start + body + b'\x14\x2f\x00'
            + b'\xf1\x07\x00' + end + b'\x14\x2c\x00')


ULTECH = (vendors.ULTECH_MAGIC.ljust(512, b'\x00') + ultech_record(tc(0, 0, 1, 0), tc(0, 0, 3, 0), [b'Hello there', b'world'])
          + ultech_record(tc(0, 0, 4, 0), tc(0, 0, 6, 0), [b'In italics now'], True)
          + b'\xf1\x0b\x00\x00\x00\x1b\x18\x14\x20\x14\x2e\x14\x2f\x00' + bytes(40))

print('Ultech')
check('608 codes between lines, 11 2E italics, the end records', timed(vendors.parse_ultech(ULTECH)[0]),
      [(1.0, 3.0, 'Hello there\nworld'), (4.0, 6.0, '<i>In italics now</i>')])

# -- Detection and file_io -------------------------------------------------------
print('.cap by its bytes')
lambda_cap = 'Lambda字幕V4\tDF0+1\tSCENE"和文標準"\r\n\r\n1\t00000100/00000300\tこんにちは\r\n'.encode('utf-8')
cheetah = cheetahcap.write([{'start': 1.0, 'end': 2.0, 'text': 'Hello'}])
check('looks_like_cap', [vendors.looks_like_cap(data[:4096]) for data in (NCI_12, NCI_20, CAPMAKER, written, cheetah, lambda_cap)],
      ['NCI', 'NCI', 'CAPMAKER', 'CAPMAKER', None, None])
folder = Path(tempfile.mkdtemp())
cases = (('nci12.cap', NCI_12, 'NCI', 3), ('nci20.cap', NCI_20, 'NCI', 3), ('capmaker.cap', CAPMAKER, 'CAPMAKER', 3),
         ('cheetah.cap', cheetah, 'CAP', 1), ('lambda.cap', lambda_cap, 'LAMBDACAP', 1), ('clip.cin', CIN, 'CAPTIONSINC', 2),
         ('clip.ult', ULTECH, 'ULTECH', 2), ('clip.uld', ULTECH, 'ULTECH', 2))
for name, data, fmt, count in cases:
    (folder / name).write_bytes(data)
    session.VIDEO = {}
    session.FORMAT = {}
    opened, got = file_io.process_subtitles_file(str(folder / name))
    check(name, (got, len(opened)), (fmt, count))
check('read only: NCI, Captions Inc., Ultech', [key in session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS and
                                                key not in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS
                                                for key in ('NCI', 'CAPTIONSINC', 'ULTECH')], [True] * 3)
session.SUBTITLE.segments = trip
session.VIDEO = {'framerate': 25}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.cap'), 'CAPMAKER')
session.FORMAT = {}
check('saved as CapMaker Plus, opened again as it', file_io.process_subtitles_file(str(folder / 'out.cap'))[1], 'CAPMAKER')

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('CAPMAKER')
panel._refresh_options()
check('CAPMAKER in the menu with a frame rate, the read-only ones not',
      ('CAPMAKER' in SUBTITLE_FORMATS, panel.sub_options.isHidden(), any(k in SUBTITLE_FORMATS for k in ('NCI', 'ULTECH', 'CAPTIONSINC'))),
      (True, False, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
