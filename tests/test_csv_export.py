"""CSV export: Audition's marker list (its layout, times, bytes), through
file_io, and the Documents tab's CSV type.

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
from subtitld.modules import csv_export, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('times')
check("Audition's decimal: M:SS.mmm, H:MM:SS.mmm from an hour on", [csv_export.audition_time(t) for t in (
    0, 3.55, 61.66, 599.9996, 3661.5, -1)], ['0:00.000', '0:03.550', '1:01.660', '10:00.000', '1:01:01.500', '0:00.000'])

print('Audition markers')
segments = [
    {'start': 7.248, 'end': 9.0, 'text': 'Second\tone', 'speaker': 'BOB'},
    {'start': 3.55, 'end': 4.16, 'text': '{\\an8}<i>Hello</i>\nthere', 'speaker': 'ALICE'},
    {'start': 5.0, 'end': 6.0, 'text': ' <i> </i> '},
    {'start': 3661.5, 'end': 3660.0, 'text': 'Ends before it starts'},
]
check('header, in order, plain text on one line, length, speaker, empty ones left out',
      csv_export.write(segments).split('\r\n'), [
          'Name\tStart\tDuration\tTime Format\tType\tDescription',
          'Hello there\t0:03.550\t0:00.610\tdecimal\tCue\tALICE',
          'Second one\t0:07.248\t0:01.752\tdecimal\tCue\tBOB',
          'Ends before it starts\t1:01:01.500\t0:00.000\tdecimal\tCue\t',
          ''])
try:
    csv_export.write(segments, 'nope')
    check('an unknown type is refused', False, True)
except ValueError:
    check('an unknown type is refused', True, True)

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.SUBTITLE['segments'] = segments
session.FORMAT['options'] = {'fps': 25.0, 'csv_type': 'audition_markers'}
file_io.save_file(str(folder / 'markers.csv'), 'CSV')
written = (folder / 'markers.csv').read_bytes()
check('UTF-8 with a byte order mark, Windows line ends, as Audition writes',
      (written[:3], written.count(b'\r\n'), written.count(b'\n')), (b'\xef\xbb\xbf', 4, 4))
check('the same as written', written.decode('utf-8-sig'), csv_export.write(segments))

print('the dialog')
from subtitld.interface.export_dialog import _DocumentsPanel
panel = _DocumentsPanel()
check('TXT: no CSV type, the note', (panel.csv_type_combo.isHidden(), panel.note.isHidden(), 'options' in panel.get_config()),
      (True, False, False))
panel.format_combo.setCurrentText('CSV')
combo = panel.csv_type_combo.combobox
check('CSV: its types, Audition markers', (panel.csv_type_combo.isHidden(), panel.note.isHidden(),
                                           [combo.itemText(i) for i in range(combo.count())]),
      (False, True, ['Audition markers']))
check('the config', panel.get_config(),
      {'category': 'documents', 'format': 'CSV', 'options': {'csv_type': 'audition_markers'}})
check('the file picker knows CSV', session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS['CSV']['extensions'], ['csv'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
