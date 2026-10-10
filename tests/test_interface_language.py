"""Interface language: picked from the system's by default, applied to the
window as it is built, and switched live from the global settings' General
tab, every text following.

The system's preferred language is set to pt_BR through LANGUAGE, so the
result does not depend on the machine's locale. The add-ons catalog fetch is
a stand-in, so nothing goes to the network.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, json
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LANGUAGE'] = 'pt_BR'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QAbstractButton, QLineEdit, QTextEdit,
                               QPlainTextEdit, QTabWidget, QComboBox, QGroupBox)
from PySide6.QtCore import QLocale
app = QApplication([])
import i18n
from subtitld.modules import session, config
from subtitld.modules.addons import installer
from subtitld.interface import translation, left_panel_global
from subtitld import __main__ as subtitld_main

installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


EN = json.loads((session.PATH_LOCALE / 'en_US.json').read_text(encoding='utf-8'))
PT = json.loads((session.PATH_LOCALE / 'pt_BR.json').read_text(encoding='utf-8'))
MISSING = next(key for key in EN if key not in PT)

print('the system language')
check('the system prefers pt_BR (LANGUAGE)', QLocale.system().uiLanguages()[0].startswith('pt'), True)
check('found from the system', translation.system_language(), 'pt_BR')
check('a region with no file: same language', translation.system_language(['pt-PT']), 'pt_BR')
check('in the order preferred', translation.system_language(['en-GB', 'pt-BR']), 'en_US')
check('skipping what has no file', translation.system_language(['de-DE', 'pt-BR']), 'pt_BR')
check('none there: English', [translation.system_language(tags) for tags in (['de-DE'], ['C'], [])],
      ['en_US', 'en_US', 'en_US'])
check('a saved language wins', translation.pick_language('en_US', ['pt-BR']), 'en_US')
check('nothing saved, or no file for it: the system\'s',
      (translation.pick_language('', ['pt-BR']), translation.pick_language('xx_XX', ['pt-BR'])), ('pt_BR', 'pt_BR'))
check('every locale file, by its own name', translation.get_available_language_names(),
      {'en_US': 'English', 'pt_BR': 'Português (Brasil)'})

print('lookups')
translation.set_language('pt_BR')
check('a key pt_BR has', translation._('global_panel.tab_general'), PT['global_panel.tab_general'])
check('a key pt_BR lacks falls back to English', translation._(MISSING), EN[MISSING])
check('an unknown key is itself', translation._('no.such.key'), 'no.such.key')
loads = []
_load_file = i18n.resource_loader.load_translation_file
i18n.resource_loader.load_translation_file = lambda *args, **kwargs: (loads.append(args), _load_file(*args, **kwargs))
try:
    for _i in range(20):
        translation._(MISSING)
        translation._('no.such.key')
finally:
    i18n.resource_loader.load_translation_file = _load_file
check('missing keys do not read the locale files again', len(loads), 0)
translation.set_language('xx_XX')
check('no file for a language: English', translation.get_language(), 'en_US')


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
        out += [(index, type(widget).__name__, value) for value in values if value]
    return out


def pick(window, code):
    """Pick a language in the General tab, as the user does."""
    inner = window.global_panel_language_combobox.combobox
    inner.setCurrentIndex(inner.findData(code))
    inner.activated.emit(inner.currentIndex())


print('the window, nothing saved')
window = subtitld_main.Window()
check('built in the system language', translation.get_language(), 'pt_BR')
check('tabs', [window.left_panel_global_tabs.tabText(i) for i in range(2)],
      [PT['global_panel.tab_subtitles'], PT['global_panel.tab_general']])
check('a tooltip', window.playercontrols_stop_button.toolTip(), PT['playercontrols.stop'])
check('the exit dialog', window.confirm_exit_dialog.accept_button.text(), PT['confirm_exit_dialog.save_button'])
check('and not English', window.left_panel_global_tabs.tabText(1) != EN['global_panel.tab_general'], True)

print('the picker, in the General tab')
combobox = window.global_panel_language_combobox
check('in the General tab', window.left_panel_global_tab_general.isAncestorOf(combobox), True)
inner = combobox.combobox
check('the system\'s, then each language', [(inner.itemText(i), inner.itemData(i)) for i in range(inner.count())],
      [(translation._('global_panel.language_system').format(language='Português (Brasil)'), ''),
       ('English', 'en_US'), ('Português (Brasil)', 'pt_BR')])
check('the system\'s selected', inner.currentIndex(), 0)

print('switching, live')
built_in_pt = texts(window)
pick(window, 'en_US')
check('saved', session.CONFIG['interface_language'], 'en_US')
check('the language', translation.get_language(), 'en_US')
check('tabs', window.left_panel_global_tabs.tabText(1), EN['global_panel.tab_general'])
check('the exit dialog', window.confirm_exit_dialog.accept_button.text(), EN['confirm_exit_dialog.save_button'])
check('the picker\'s own label', combobox.label.text(), EN['global_panel.language'])
check('English still selected', inner.currentData(), 'en_US')
switched = texts(window)
check('hundreds of texts changed', sum(a != b for a, b in zip(built_in_pt, switched)) > 200, True)
# A text the switch does not reach keeps the language the window was built
# in. (Switching back and comparing would not show it: it never changed.)
portuguese = {value for key, value in PT.items() if key != 'language_name'} - set(EN.values())
check('no text left in Portuguese', [text for text in switched if text[2] in portuguese], [])

pick(window, '')
check('back to the system\'s: nothing saved', session.CONFIG['interface_language'], '')
check('the language', translation.get_language(), 'pt_BR')
switched_back = texts(window)
stale = [(new, old) for new, old in zip(switched_back, built_in_pt) if new != old]
check('every text as when built in pt_BR', stale, [])
check('as many texts', len(switched_back), len(built_in_pt))

print('the saved choice is used at the next start')
pick(window, 'en_US')
session.CONFIG.save()
saved = config.Config().get('interface_language')
check('saved to the config file', saved, 'en_US')
check('picked over the system\'s', translation.pick_language(saved), 'en_US')

window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
