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
from subtitld.modules.segment import Subtitle
from subtitld.interface import export_dialog
from subtitld.interface.translation import _

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:70]!r}' + ('' if ok else f'   (want {str(want)[:70]!r})'))
    if not ok:
        fails.append(name)


session.SUBTITLE = Subtitle({'segments': [
    {'start': 0.2 + index, 'end': 0.9 + index, 'text': f'<i>Line</i> {index}', 'speaker': 'Thom'}
    for index in range(300)]})
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

print('audio shows its dashboard')
dialog._select_category(1, 'audio')
check('audio: the cards, not the well', (dialog.preview_panel.dashboard.isHidden(), preview.isHidden()), (False, True))
dialog._select_category(0, 'subtitles')
check('back to subtitles: the file', preview.toPlainText().startswith('WEBVTT'), True)

print('a speaker with a picture')
from PySide6.QtGui import QImage
saved_speakers = session.SPEAKERS
session.SPEAKERS = {'A': {'image': QImage(8, 8, QImage.Format_ARGB32)}}
snapshot = file_io._snapshot_for_save()
check('the snapshot is taken, the picture copied, not the same object',
      (snapshot is not None, isinstance(snapshot and snapshot['SPEAKERS']['A']['image'], QImage),
       snapshot and snapshot['SPEAKERS']['A']['image'] is session.SPEAKERS['A']['image']), (True, True, False))
dialog._select_category(0, 'subtitles')
dialog.refresh_preview()
check('the preview shows the file, not an endless wait', (preview.toPlainText().startswith('WEBVTT'),
                                                        dialog._preview_timer.isActive()), (True, False))
session.SPEAKERS = saved_speakers

print('pictures: the frame the export draws on')
session.VIDEO = {}
check('PGS, no video', file_io.picture_frame('PGS', {}), (1920, 1080))
check('VobSub PAL', file_io.picture_frame('VOBSUB', {'vobsub_size': 'pal'}), (720, 576))
check('IMSC image profile', file_io.picture_frame('TTML', {'profile': next(iter(file_io.ttml.IMAGE_PROFILES))}), (1920, 1080))
check('DCP as text', file_io.picture_frame('DCP', {}), None)
check('DCP as pictures', file_io.picture_frame('DCP', {'dcp_content': 'pictures'}), (1920, 1080))
check('SRT', file_io.picture_frame('SRT', {}), None)

print('pictures: the subtitle under the cursor, else the next')
saved_segments = session.SUBTITLE
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 2.0, 'text': 'one'}, {'start': 3.0, 'end': 4.0, 'text': ' '},
                                 {'start': 5.0, 'end': 6.0, 'text': 'three'}], 'position': 1.5})
cue = dialog._picture_cue('PGS', {})
check('under the cursor', cue['text'], 'one')
session.SUBTITLE.position = 2.5
check('between: the next with words', dialog._picture_cue('PGS', {})['text'], 'three')
check('shifted as written', dialog._picture_cue('PGS', {'offset': 1.0})['start'], 6.0)
session.SUBTITLE.position = 9.0
check('after the last: none', dialog._picture_cue('PGS', {}), None)

print('pictures: one drawn')
from subtitld.interface import subtitle_renderer
if subtitle_renderer.available():
    import time
    session.SUBTITLE.position = 0.0
    panel.offset_spin.setValue(0)
    panel.format_combo.combobox.setCurrentText('PGS')
    dialog.refresh_preview()
    deadline = time.time() + 40
    while dialog._picture is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check('the picture shows', dialog.preview_panel.picture.isVisibleTo(dialog.preview_panel), True)
    check('the picture itself under it', dialog.preview_panel.crop.isVisibleTo(dialog.preview_panel), True)
    check('no well, nothing but the picture', preview.isVisibleTo(dialog.preview_panel), False)
    check('no text under a PGS picture', preview.isVisibleTo(dialog.preview_panel), False)

    print('pictures: BDN, the picture and the start of its XML')
    session.SUBTITLE.segments = [{'start': 1.0 + 2 * index, 'end': 2.0 + 2 * index, 'text': f'Line {index}'}
                                    for index in range(10)]
    panel.format_combo.combobox.setCurrentText('BDN')
    dialog.refresh_preview()
    deadline = time.time() + 40
    while dialog._picture is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    shown = preview.toPlainText()
    _fmt, _choices, bdn_options = panel.export_choices()
    folder = tempfile.mkdtemp()
    saved_format = session.FORMAT
    session.FORMAT = {'format': 'BDN', 'options': bdn_options}
    file_io.save_file(os.path.join(folder, 'subtitle.xml'), 'BDN', 'en')
    session.FORMAT = saved_format
    with open(os.path.join(folder, 'subtitle.xml'), encoding='utf-8') as written_file:
        whole = written_file.read()
    check('the XML shows under the picture', preview.isVisibleTo(dialog.preview_panel), True)
    check('it is the start of the file written', whole.startswith(shown) and len(shown) > 200, True)
    check('as many events as the file', 'NumberofEvents="10"' in shown, True)
    check('the first pictures with their sizes', shown.count('<Graphic '), export_dialog.ExportDialog.BDN_EVENTS)
    check('not in a note\'s faded look', preview.document().firstBlock().charFormat().fontItalic()
          or any(f.format.fontItalic() for f in preview.document().firstBlock().layout().formats()), False)

    dialog._select_category(1, 'audio')
    check('audio: no picture', dialog.preview_panel.picture.isVisibleTo(dialog.preview_panel), False)
    dialog.done(0)
    check('the renderer goes with the dialog', dialog._renderer, None)
else:
    print('  (no QtWebEngine: not drawn)')
session.SUBTITLE = Subtitle(saved_segments)

print('the session is as it was')
check('SUBTITLE', session.SUBTITLE, before[0])
check('FORMAT', session.FORMAT, before[1])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
