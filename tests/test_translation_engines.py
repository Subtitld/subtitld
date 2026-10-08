"""Translation panel: add-on engines appear and go while the app runs.

An add-on installed (or removed, enabled, disabled) at runtime makes the
add-on manager emit `providers_changed`; the engine picker must follow.
The manager here is a stand-in — no real add-on is started.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget
from PySide6.QtCore import QObject, Signal
app = QApplication([])
from subtitld.modules import session, addons
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import left_panel_translation as lpt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


class FakeProvider(QObject):
    translation_ready = Signal(object)
    error = Signal(str)

    def __init__(self, provider_id, name):
        super().__init__()
        self.id = provider_id
        self.display_name = name
        self.config_schema = None


class FakeManager(QObject):
    providers_changed = Signal()

    def __init__(self):
        super().__init__()
        self.providers = []

    def providers_for_task(self, task):
        return list(self.providers)


manager = FakeManager()
addons.get_manager = lambda: manager


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.timeline_widget = types.SimpleNamespace(update=lambda: None)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)


session.SUBTITLE = {'segments': [], 'selected': None, 'language': 'en-us', 'position': 0.0}
session.CONFIG = {'translation': {'engine': 'translate-offline', 'scope': 'all',
                                  'engine_options': {'target_language': 'pt-br'}}}
host = Host()
host.show()
lpt.load(host)
lpt.translate(host)
combobox = host.global_panel_translation_engine_combobox.combobox


def engines():
    return [combobox.itemText(i) for i in range(combobox.count())]


print('with no add-on installed, the built-in engine is there alone')
check('engines', engines(), ['GoogleTranslator'])

print('installing an add-on while the app runs adds it to the picker')
offline = FakeProvider('translate-offline', 'Offline Translate (CTranslate2)')
manager.providers = [offline]
manager.providers_changed.emit()
app.processEvents()
check('engines', engines(), ['GoogleTranslator', 'translate-offline'])
check('the engine picked before comes back selected', combobox.currentText(), 'translate-offline')
check('and its panel is the one shown',
      host.global_panel_translation_tabwidget.currentWidget().property('translation_engine'), 'translate-offline')
panel = host.global_panel_translation_addon_widgets['translate-offline']

print('an unrelated change keeps the existing panel')
manager.providers = [offline, FakeProvider('other-mt', 'Other MT')]
manager.providers_changed.emit()
app.processEvents()
check('engines', engines(), ['GoogleTranslator', 'translate-offline', 'other-mt'])
check('same panel object, so a running translation is untouched',
      host.global_panel_translation_addon_widgets['translate-offline'] is panel, True)

print('removing it takes it out of the picker')
manager.providers = []
manager.providers_changed.emit()
app.processEvents()
check('engines', engines(), ['GoogleTranslator'])
check('falls back to the built-in engine', combobox.currentText(), 'GoogleTranslator')
check('no stale panel left behind as a child either',
      sorted(w.property('translation_engine') for w in host.global_panel_translation_tabwidget.findChildren(QWidget)
             if w.property('translation_engine')),
      ['GoogleTranslator'])
check('no stale panel left in the stack',
      [host.global_panel_translation_tabwidget.widget(i).property('translation_engine')
       for i in range(host.global_panel_translation_tabwidget.count())],
      ['GoogleTranslator'])

print('Start translation warns instead of copying the subtitles unchanged')
shown = []


class FakeDialog:
    answer = 1

    def __init__(self, parent=None, title=''):
        self.title = title
        self.content = QWidget()
        self.content.setLayout(QVBoxLayout())
        self.reject_button = QWidget()
        self.accept_button = types.SimpleNamespace(setText=lambda text: None)

    def exec(self):
        labels = self.content.findChildren(QWidget)
        shown.append((self.title, labels[0].text() if labels else ''))
        return FakeDialog.answer

    def result(self):
        return FakeDialog.answer


lpt.utils.SimpleDialog = FakeDialog
manager.providers = [offline]
manager.providers_changed.emit()
app.processEvents()
started = []
host.global_panel_translation_addon_widgets['translate-offline'].translate_process_callback = \
    lambda segments: started.append(len(segments))
host.global_panel_translation_googletranslator_widget.translate_process_callback = \
    lambda segments: started.append(('google', len(segments)))
session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 2.0, 'text': 'Hello', 'speaker': 'A'}]


def start(engine, source, target, answer=1):
    shown.clear()
    started.clear()
    FakeDialog.answer = answer
    session.SUBTITLE['language'] = source
    session.CONFIG['translation']['engine_options']['target_language'] = target
    host.global_panel_translation_engine_combobox.setCurrentText(engine)
    lpt.global_panel_translation_engine_combobox_activated(host)
    lpt.global_panel_translation_start_translation_button_clicked(host)


def warned():
    return [title for title, _text in shown if title == lpt._('translation_panel.same_language_title')]


start('translate-offline', 'pt-br', 'pt-br')
check('subtitles marked as the target language: warned', len(warned()), 1)
check('and nothing starts', started, [])
check('the message names the language and where to fix it',
      ('Portuguese' in shown[0][1], lpt._('transcription_panel.language') in shown[0][1]), (True, True))

start('translate-offline', 'en-us', 'pt-br')
check('English to Portuguese: no warning', warned(), [])
check('and it starts', started, [1])

start('translate-offline', 'en-us', 'en-gb', answer=0)
check('two variants of one language: asked', len(warned()), 1)
check('cancelled, nothing starts', started, [])
start('translate-offline', 'en-us', 'en-gb', answer=1)
check('"Translate anyway" starts it', started, [1])

start('GoogleTranslator', 'pt-br', 'pt-br')
check('GoogleTranslator detects the language itself: never warned', warned(), [])
check('and starts', started, [('google', 1)])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
