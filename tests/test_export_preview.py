"""The export dialog's preview: the start of the file the chosen format and
options write, the same text save_file writes; a note for binary files;
nothing for the other categories; and the session left as it was.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import copy, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
from subtitld.interface import export_dialog
from subtitld.interface.translation import _

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:70]!r}' + ('' if ok else f'   (want {str(want)[:70]!r})'))
    if not ok:
        fails.append(name)


session.SUBTITLE = {'segments': [
    {'start': 0.2 + index, 'end': 0.9 + index, 'text': f'<i>Line</i> {index}', 'speaker': 'Thom'}
    for index in range(300)]}
session.FORMAT = {'format': 'USFX', 'options': {'offset': 7}}
session.CONFIG.setdefault('selected_language', 'en')
before = (copy.deepcopy(session.SUBTITLE), copy.deepcopy(session.FORMAT))

dialog = export_dialog.ExportDialog()
panel = dialog.subtitles_panel
preview = dialog.preview_panel.file


def choose(labeled, data):
    export_dialog._select(labeled, data)


def written(fmt, options):
    """What the export itself writes for these options, the way top_bar sets them."""
    saved = session.FORMAT
    session.FORMAT = {'format': fmt, 'options': options}
    path = os.path.join(tempfile.mkdtemp(), 'out.' + fmt.lower())
    try:
        file_io.save_file(path, fmt, 'en')
    finally:
        session.FORMAT = saved
    with open(path, 'rb') as out:
        return out.read()


print('SRT, as save_file writes it')
panel.format_combo.combobox.setCurrentText('SRT')
choose(panel.speakers_combo, 'prefix')
choose(panel.formatting_combo, panel.formatting_combo.combobox.itemData(1))
choose(panel.line_ends_combo, 'crlf')
choose(panel.encoding_combo, 'cp1252')
panel.offset_spin.setValue(1.5)
check('a change waits for the timer', dialog._preview_timer.isActive(), True)
dialog.refresh_preview()
fmt, _choices, options = panel.export_choices()
data = written(fmt, options)
want = ''.join(data.decode('cp1252').splitlines(keepends=True)[:200])
check('the preview is the file\'s start', preview.toPlainText(), want.replace('\r\n', '\n'))
check('two hundred lines at most', preview.blockCount() <= 201, True)
check('the shift is in it', '00:00:01,700' in preview.toPlainText(), True)
check('the remembered choices are left alone', 'export_subtitles' in session.CONFIG, False)

print('UTF-16 decodes')
choose(panel.encoding_combo, 'utf-16')
dialog.refresh_preview()
check('utf-16 text', preview.toPlainText().startswith('1\n00:00:01,700'), True)

print('ASS is text')
panel.format_combo.combobox.setCurrentText('ASS')
dialog.refresh_preview()
check('ASS header', preview.toPlainText().startswith('[Script Info]'), True)

print('binary formats get a note')
for fmt in ('STL', 'PAC', 'CAVENA890', 'CAP'):
    panel.format_combo.combobox.setCurrentText(fmt)
    dialog.refresh_preview()
    check(f'{fmt}: binary note', preview.toPlainText(), _('export_dialog.preview_binary'))

print('a writer that fails gets a note')
real = file_io.save_file
file_io.save_file = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
panel.format_combo.combobox.setCurrentText('VTT')
dialog.refresh_preview()
file_io.save_file = real
check('failed note', preview.toPlainText(), _('export_dialog.preview_failed'))

print('the other categories show nothing')
dialog._select_category(1, 'audio')
check('audio: empty', preview.toPlainText(), '')
dialog._select_category(0, 'subtitles')
check('back to subtitles: the file', preview.toPlainText().startswith('WEBVTT'), True)

print('the session is as it was')
check('SUBTITLE', session.SUBTITLE, before[0])
check('FORMAT', session.FORMAT, before[1])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
