"""Spot: .spt records laid out as Subtitle Edit's reader takes them (times
as HHMMSSFF digits, the italic flag, two lengths and two lines in
Windows-1252, "<" ">" italics, an empty record skipped) and .sptx's
(found by their digits, UTF-16 text), read only through file_io.

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
from subtitld.modules import file_io, session, spot

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def spt_record(time_in, time_out, first, second=b'', italic=False):
    head = bytearray(52)
    head[0:16] = (time_in + time_out).encode('ascii')
    head[20] = 1 if italic else 0
    head[36], head[40] = len(first), len(second)
    return bytes(head) + first + second


def sptx_record(time_in, time_out, text, italic=False):
    encoded = text.encode('utf-16-le')
    head = bytearray(50)
    head[0:16] = (time_in + time_out).encode('ascii')
    head[20] = 1 if italic else 0
    head[36] = len(encoded)
    return bytes(head) + encoded


SPT = (bytes([24]) + bytes(23) + spt_record('00000100', '00000300', b'Ol\xe1', b'mundo <grande>')
       + spt_record('00000400', '00000512', b'', b'') + spt_record('00000600', '00000700', b'All italic', italic=True)
       + bytes(60))
SPTX = (bytes([16]) + bytes(15) + sptx_record('00000100', '00000300', 'Привет <мир>') + bytes(4)
        + sptx_record('00000400', '00000512', 'Second', italic=True) + bytes(60))

print('reading')
check('.spt: two lines, italics, an empty record skipped', [(s['start'], s['end'], s['text']) for s in spot.parse(SPT)[0]], [
    (1.0, 3.0, 'Olá\nmundo <i>grande</i>'), (6.0, 7.0, '<i>All italic</i>')])
check('.sptx: UTF-16, found by its digits', [(s['start'], s['end'], s['text']) for s in spot.parse_sptx(SPTX)[0]], [
    (1.0, 3.0, 'Привет <i>мир</i>'), (4.0, 5.48, '<i>Second</i>')])
check('frames at 30', spot.parse_sptx(SPTX, 30)[0][1]['end'], 5.4)

print('through file_io, read only')
folder = Path(tempfile.mkdtemp())
for name, data in (('a.spt', SPT), ('b.sptx', SPTX)):
    (folder / name).write_bytes(data)
    session.VIDEO = {}
    session.FORMAT = {}
    opened, fmt = file_io.process_subtitles_file(str(folder / name))
    check(name, (fmt, len(opened)), ('SPOT', 2))
check('offered to open, not to save', (session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS['SPOT']['extensions'],
                                       'SPOT' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS), (['spt', 'sptx'], False))
(folder / 'bad.spt').write_bytes(b'\x00' * 200)
try:
    file_io.process_subtitles_file(str(folder / 'bad.spt'))
    check('a .spt that is not one says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('a .spt that is not one says so', True, True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
