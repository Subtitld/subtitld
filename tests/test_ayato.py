"""Ayato: records laid out as Subtitle Edit's reader and writer have them
(the 2713-byte header, frames from the start in three bytes, UTF-8 text
after 07, lines parted by 1F 07 and by other control runs), a round trip,
the header's counts, and the file through file_io and the export dialog.

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
from subtitld.modules import ayato, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def record(number, first, last, text):
    # As Ayato.cs lays a record out: number, 00 00, frames, 00, frames, 00,
    # 17 00 02 00, the length, 55 zeros, 07 and the text.
    return (number.to_bytes(2, 'little') + b'\x00\x00' + first.to_bytes(3, 'little') + b'\x00'
            + last.to_bytes(3, 'little') + b'\x00\x17\x00\x02\x00' + bytes([len(text) + 1]) + bytes(55) + b'\x07' + text)


SAMPLE = (bytes(ayato.HEADER_SIZE) + record(1, 25, 75, 'Olá, mundo'.encode('utf-8'))
          + record(2, 100, 150, b'First\x1f\x07Second') + record(3, 200, 250, b'\x01\x02\x07One\x0d\x0a\x07Two')
          + record(4, 300, 350, b''))

print('reading')
segments, settings = ayato.parse(SAMPLE)
check('frames at 25, UTF-8, line breaks', [(s['start'], s['end'], s['text']) for s in segments], [
    (1.0, 3.0, 'Olá, mundo'), (4.0, 6.0, 'First\nSecond'), (8.0, 10.0, 'One\nTwo')])
check('at 30', ayato.parse(SAMPLE, 30)[0][0]['start'], round(25 / 30, 3))
check('not Ayato: a first record not numbered 1', ayato.is_ayato(bytes(ayato.HEADER_SIZE) + record(2, 0, 1, b'x')), False)

print('writing')
trip = [{'start': 1.0, 'end': 3.0, 'text': '<i>Olá</i>, mundo'}, {'start': 4.0, 'end': 6.0, 'text': '{\\an8}Top\nand bottom'}]
written = ayato.write(trip)
check('the header: magic and the count twice', (written[:13], written[682:684], written[2050:2052], written[2088:2108]),
      (b'\x0502.03\x05Ayato\x02', b'\x02\x00', b'\x02\x00', b'Microsoft Sans Serif'))
check('read back, plain text', [(s['start'], s['end'], s['text']) for s in ayato.parse(written)[0]],
      [(1.0, 3.0, 'Olá, mundo'), (4.0, 6.0, 'Top\nand bottom')])
check('written again as it was read', ayato.write(ayato.parse(written)[0]) == ayato.write(ayato.parse(ayato.write(ayato.parse(written)[0]))[0]), True)

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.aya').write_bytes(SAMPLE)
session.VIDEO = {}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.aya'))
check('opens as AYATO', (fmt, len(opened)), ('AYATO', 3))
session.SUBTITLE.segments = trip
session.VIDEO = {'framerate': 30}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.aya'), 'AYATO')
check('saved at the video rate', ayato.read(str(folder / 'out.aya'), 30)[0][1]['start'], 4.0)
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['AYATO']['extensions'], ['aya'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('AYATO')
panel._refresh_options()
check('AYATO in the menu with a frame rate', ('AYATO' in SUBTITLE_FORMATS, panel.sub_options.isHidden()), (True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
