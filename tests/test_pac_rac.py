"""Screen's .rac: PAC's records without PAC's header and with other bytes
between them, read as Subtitle Edit reads them (Pac.cs), opened as PAC
through file_io; a .pac read as before, and a .rac with fewer than two
timed records with text not taken for one.

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
from subtitld.modules import file_io, pac, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


SUBTITLES = [{'start': 1.0, 'end': 3.0, 'text': 'Olá, mundo'}, {'start': 4.0, 'end': 6.0, 'text': '<i>Second</i>\nline'},
             {'start': 7.0, 'end': 8.0, 'text': '{\\an8}Third'}]
written = pac.write(SUBTITLES)
# The records alone, with bytes of something else before and between them.
body = written[20:written.index(b'\xff' + bytes(11))]
second = body.index(b'\x00\x01\x00\x60')
RAC = b'\x07junk\x00\x01' + body[:second] + b'\x13\x37\x00\x00garbage' + body[second:] + b'\x00\x00tail'

print('reading')
check('a .pac as before', [s['text'] for s in pac.parse(written)[0]], [s['text'] for s in SUBTITLES])
segments, _settings = pac.parse(RAC, recovery=True)
check('the records found between other bytes', [(s['start'], s['end'], s['text']) for s in segments],
      [(s['start'], s['end'], s['text']) for s in SUBTITLES])
check('is_rac', (pac.is_rac(RAC), pac.is_rac(b'\x00' * 300), pac.is_rac(body[:second] + bytes(40))), (True, False, False))
try:
    pac.parse(RAC)
    check('without recovery, not PAC', None, 'ValueError')
except ValueError:
    check('without recovery, not PAC', True, True)

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'autosave.rac').write_bytes(RAC)
session.VIDEO = {}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'autosave.rac'))
check('opens as PAC, to be saved as one', (fmt, len(opened), session.FORMAT['options']['pac_code_page']), ('PAC', 3, 'latin'))
check('offered to open', session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS['RAC']['extensions'], ['rac'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
