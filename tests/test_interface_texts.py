"""Interface texts: every one has a key in en_US.json and follows the
interface language.

- Every literal _('...') in the code is a key en_US.json has: _() shows a key
  no file has as itself ("cancel", "{days} day{plural} ago").
- Every keyboard shortcut's description is such a key.
- friendly_time() takes each of its phrases, one and many, from a key.
- The window, built in a stand-in language, shows no English: pt_BR with
  every text of en_US.json as ⟦English⟧, so every key has a translation and
  none equals the English. A text the window shows that equals an en_US value
  never went through _(). Switched live to English and back, every text comes
  back as built.
- What the subtitle alignment and the step unit save does not depend on the
  language their labels are in.

The system's preferred language is set to pt_BR through LANGUAGE. The add-ons
catalog fetch is a stand-in, so nothing goes to the network.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, json, ast, time
from datetime import datetime, timedelta
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LANGUAGE'] = 'pt_BR'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QAbstractButton, QLineEdit, QTextEdit,
                               QPlainTextEdit, QTabWidget, QComboBox, QGroupBox, QListWidget)
from PySide6.QtCore import Qt
app = QApplication([])
import i18n
from subtitld.modules import session, shortcuts
from subtitld.modules.addons import installer
from subtitld.interface import translation, utils, startscreen, left_panel_global, playercontrols
from subtitld import __main__ as subtitld_main

installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


EN = json.loads((session.PATH_LOCALE / 'en_US.json').read_text(encoding='utf-8'))

print('every _() key is in en_US.json')
missing = []
for path in sorted(SRC.rglob('*.py')):
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), str(path))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_'
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and node.args[0].value not in EN):
            missing.append(f'{path.relative_to(SRC)}:{node.lineno}: {node.args[0].value!r}')
check('literal keys missing', missing, [])
check('shortcut descriptions missing',
      [command for command in shortcuts._registry.get_all_commands()
       if shortcuts._registry.get_description(command) not in EN], [])


print('friendly_time, in English')
translation.set_language('en_US')
SPANS = [  # seconds from now (half a second off, so a slow run lands the same), English
    (-2.5, 'just now'), (-30.5, '30 seconds ago'),
    (-90.5, '1 minute ago'), (-150.5, '2 minutes ago'),
    (-3600.5, '1 hour ago'), (-7200.5, '2 hours ago'),
    (-86400.5, '1 day ago'), (-3 * 86400.5, '3 days ago'),
    (-604800.5, '1 week ago'), (-2 * 604800.5, '2 weeks ago'),
    (-2419200.5, '1 month ago'), (-5 * 2419200.5, '5 months ago'),
    (2.5, 'in a few seconds'), (30.5, 'in 30 seconds'),
    (90.5, 'in 1 minute'), (150.5, 'in 2 minutes'),
    (3600.5, 'in 1 hour'), (7200.5, 'in 2 hours'),
    (86400.5, 'in 1 day'), (3 * 86400.5, 'in 3 days'),
    (604800.5, 'in 1 week'), (2 * 604800.5, 'in 2 weeks'),
    (2419200.5, 'in 1 month'), (5 * 2419200.5, 'in 5 months'),
]
check('each span, one and many', [utils.friendly_time(datetime.now() + timedelta(seconds=offset)) for offset, _text in SPANS],
      [text for _offset, text in SPANS])
check('a timestamp too', utils.friendly_time(time.time() - 7200.5), '2 hours ago')

# The stand-in language: pt_BR, every text ⟦English⟧ but its own name.
_pt_name = translation.get_available_language_names()['pt_BR']
i18n.translations.container['pt_BR'] = {key: f'⟦{value}⟧' for key, value in EN.items()} | {'language_name': _pt_name}
ENGLISH = set(EN.values()) | {value.upper() for value in EN.values()}
# Names, the same in every language: English in the language picker, the
# plain-text editor's Subtitld theme, the subtitld.cc card's title.
NAMES = {'English', 'Subtitld', 'subtitld.cc'}

print('friendly_time, in another language')
translation.set_language('pt_BR')
check('each phrase is its own text there', [utils.friendly_time(datetime.now() + timedelta(seconds=offset)) for offset, _text in SPANS],
      [f'⟦{text}⟧' for _offset, text in SPANS])


def texts(window):
    """Every text the window shows, widget by widget, in a stable order."""
    out = []
    for index, widget in enumerate([window] + window.findChildren(QWidget)):
        values = [widget.toolTip()]
        if isinstance(widget, (QLabel, QAbstractButton)):
            values.append(widget.text())
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            values.append(widget.placeholderText())
        if isinstance(widget, QGroupBox):
            values.append(widget.title())
        if isinstance(widget, QTabWidget):
            values += [widget.tabText(i) for i in range(widget.count())]
        if isinstance(widget, QComboBox):
            values += [widget.itemText(i) for i in range(widget.count())]
        if isinstance(widget, QListWidget):
            values += [widget.item(i).text() for i in range(widget.count())]
        out += [(index, type(widget).__name__, value) for value in values if value]
    return out


def pick(window, code):
    """Pick an interface language in the General tab, as the user does."""
    inner = window.global_panel_language_combobox.combobox
    inner.setCurrentIndex(inner.findData(code))
    inner.activated.emit(inner.currentIndex())


def choose(combobox, data):
    """Choose a combobox's item by its data, as the user does."""
    combobox.setCurrentIndex(combobox.findData(data))
    combobox.activated.emit(combobox.currentIndex())


print('the window, in the stand-in language')
window = subtitld_main.Window()
check('built in pt_BR', translation.get_language(), 'pt_BR')
# A recent file, for the start screen's "2 hours ago".
_project = Path(_xdg) / 'project'
_project.mkdir()
(_project / 'film.srt').write_text('')
(_project / 'film.mp4').write_bytes(b'')
session.CONFIG['recent_files'] = {str(_project / 'film.srt'): {
    'video_filepath': str(_project / 'film.mp4'), 'last_opened': time.time() - 7200.5}}
startscreen.update_recent_files_list(window)
ages = [label for label in window.start_screen_recent_listwidget.findChildren(QLabel) if label.property('class') == 'age']
check('a recent file\'s age', [label.text() for label in ages], ['⟦2 hours ago⟧'])

built = texts(window)
check('no text is English', sorted({text for _i, _type, text in built if text in ENGLISH} - NAMES), [])
alignment = window.left_panel_global_subtitle_alignment.combobox
check('subtitle alignments', [alignment.itemText(i) for i in range(alignment.count())],
      ['⟦Left⟧', '⟦Center⟧', '⟦Right⟧'])
check('step units', [window.step_unit.itemText(i) for i in range(window.step_unit.count())],
      ['⟦Frames⟧', '⟦Seconds⟧'])
rows = window.keyboard_panel_rows
check('a shortcut\'s description', rows['playpause'].name_label.text(), '⟦Play/Pause⟧')
check('the selected one, in caps', window.keyboard_panel_capture_title.text(),
      f'⟦{EN[shortcuts.shortcuts_dict[window.keyboard_panel_selected]].upper()}⟧')
metadata_labels = window.left_panel_metadata_labels
check('a metadata field', metadata_labels['metadata_panel.field_title'].text(), '⟦TITLE⟧')
check('every metadata field', [label.text() for label in metadata_labels.values()
                               if not (label.text().startswith('⟦') and label.text().endswith('⟧'))], [])
dialog = window.left_panel_speakers_new_name_dialog
check('a kept dialog\'s buttons', (dialog.accept_button.text(), dialog.reject_button.text()), ('⟦OK⟧', '⟦Cancel⟧'))
fresh = utils.SimpleDialog()
check('a dialog built when needed', (fresh.accept_button.text(), fresh.reject_button.text()), ('⟦OK⟧', '⟦Cancel⟧'))
check('the subtitle list\'s new speaker dialog', window.left_panel_subtitleslist_new_name_dialog.input_label.text(),
      f"⟦{EN['subtitles_panel_widget_speakers.enter_speaker_name']}⟧")

print('what is saved does not depend on the labels')
choose(alignment, 'center')
check('an alignment saved by name', session.CONFIG['default_values']['subtitle_alignment'], 'center')
check('and used', window.timeline_widget.subtitle_alignment, Qt.AlignCenter)
session.CONFIG['default_values']['subtitle_alignment'] = 'right'
left_panel_global.update(window)
check('a saved alignment selected', alignment.currentData(), 'right')
choose(window.step_unit, 'Seconds')
check('a step unit saved by name', session.CONFIG['timeline']['step_unit'], 'Seconds')
session.CONFIG['timeline']['step_unit'] = 'Frames'
playercontrols.update_step_information(window)
check('a saved step unit selected', window.step_unit.currentData(), 'Frames')
built = texts(window)

print('switching to English, live, and back')
pick(window, 'en_US')
check('the language', translation.get_language(), 'en_US')
switched = texts(window)
check('no stand-in text left', [text for text in switched if '⟦' in text[2]], [])
check('the alignments', [alignment.itemText(i) for i in range(alignment.count())], ['Left', 'Center', 'Right'])
check('the selected alignment kept', alignment.currentData(), 'right')
check('the step units', [window.step_unit.itemText(i) for i in range(window.step_unit.count())], ['Frames', 'Seconds'])
check('a shortcut', window.keyboard_panel_rows['zoom_in'].name_label.text(), 'Zoom in')
check('a metadata field', metadata_labels['metadata_panel.field_has_hdr_content'].text(), 'HAS HDR CONTENT')
check('a kept dialog\'s Cancel', dialog.reject_button.text(), 'Cancel')
check('the recent file\'s age', [label.text() for label in ages], ['2 hours ago'])
pick(window, '')
check('back to the system\'s', translation.get_language(), 'pt_BR')
switched_back = texts(window)
check('every text as when built', [(new, old) for new, old in zip(switched_back, built) if new != old], [])
check('as many texts', len(switched_back), len(built))

window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
