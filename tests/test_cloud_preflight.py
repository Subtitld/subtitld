"""Subtitld Cloud: the account is checked before a cloud transcription starts.

The network is stubbed out — no request reaches the cloud and no real key
is read.

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
from subtitld.modules import session
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import left_panel_import as lpi
from subtitld.interface import cloud_dashboard
from subtitld.modules.addons.builtin import subtitld_cloud_shared as cloud

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


print('what in an account blocks a paid job')
check('money left', cloud.account_problem({'balance': 5.59}), '')
check('an empty balance', cloud.account_problem({'balance': 0}), 'no_balance')
check('no credits', cloud.account_problem({'credits': 0}), 'no_balance')
check('a cloud that sends no balance', cloud.account_problem({'email': 'a@b.c'}), '')
check('not a number', cloud.account_problem({'balance': '0'}), '')


class FakeWorker(QObject):
    """Stands in for the account fetch: answers as soon as it starts."""

    loaded = Signal(dict)
    failed = Signal(str)
    finished = Signal()
    answer = ('loaded', {'balance': 10})

    def __init__(self, parent=None):
        super().__init__(parent)

    def start(self):
        kind, value = FakeWorker.answer
        (self.loaded if kind == 'loaded' else self.failed).emit(value)
        self.finished.emit()


cloud_dashboard._CloudAccountWorker = FakeWorker

shown = []


class FakeDialog:
    def __init__(self, parent=None, title=''):
        self.content = QWidget()
        self.content.setLayout(QVBoxLayout())
        self.reject_button = QWidget()

    def exec(self):
        shown.append(self.content.findChildren(QWidget)[0].text())
        return 1

    def result(self):
        return 1


lpi.utils.SimpleDialog = FakeDialog


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


session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'keep me', 'speaker': 'A'}],
                    'selected': None, 'language': 'en-us', 'position': 0.0}
session.CONFIG = {'transcription': {'scope': 'all'}}
host = Host()
host.show()
lpi.load(host)

started = []


def engine(uses_cloud):
    """A stand-in engine panel, selected in the combobox."""
    panel = QWidget()
    panel.setProperty('transcription_engine', 'probe')
    panel.provider = types.SimpleNamespace(uses_subtitld_cloud=uses_cloud)
    panel.transcript_callback = lambda: started.append(True)
    host.global_panel_import_tabwidget.addWidget(panel)
    host.global_panel_import_engine_combobox.currentText = lambda: 'probe'
    return panel


def cloud_messages():
    """What was shown about the account (not the usual overwrite question)."""
    return [text for text in shown if 'Subtitld Cloud' in text]


def click(answer):
    FakeWorker.answer = answer
    started.clear()
    shown.clear()
    session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 2.0, 'text': 'keep me', 'speaker': 'A'}]
    lpi.global_panel_import_start_transcription_button_clicked(host)
    app.processEvents()


panel = engine(uses_cloud=True)
for answer, label, url_part in ((('failed', 'bad_key'), 'a rejected key', '/dashboard/'),
                                (('failed', 'no_key'), 'no key at all', '/dashboard/'),
                                (('loaded', {'balance': 0}), 'an empty balance', '/billing/'),
                                (('failed', 'network'), 'no connection', None)):
    print(f'{label} stops the job before it starts')
    click(answer)
    check('nothing started', started, [])
    check('the subtitles were not cleared', [s['text'] for s in session.SUBTITLE['segments']], ['keep me'])
    check('one message explains it', len(cloud_messages()), 1)
    if url_part:
        check('with where to fix it', url_part in cloud_messages()[0], True)

print('a good account starts the job')
click(('loaded', {'balance': 10}))
check('started', started, [True])
check('without an account message', cloud_messages(), [])
check('Start is usable again afterwards',
      host.global_panel_import_start_transcription_button.isEnabled(), True)

print('a cloud without the account endpoint does not block the job')
click(('failed', 'no_endpoint'))
check('started', started, [True])

print('an engine that is not on the cloud is never checked')
host.global_panel_import_tabwidget.removeWidget(panel)
panel.setProperty('transcription_engine', 'other')
engine(uses_cloud=False)
click(('failed', 'bad_key'))
check('started without a check', (started, cloud_messages()), ([True], []))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
