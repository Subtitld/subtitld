"""Speakers panel: Add speaker sits in the bottom line, as Start
transcription does, and speakers with no subtitles are faded.

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
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget, QScrollArea
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session
from subtitld.interface import left_panel_speakers as lps
from subtitld.interface.scope_selector import ScopeFooter

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


print('a footer without a scope')
footer = ScopeFooter(None)
check('no selector, still the hairline and the row', (footer.selector, footer.divider is not None, footer.row is not None),
      (None, True, True))
footer.refresh()
footer.retranslate()
footer.show_progress(True)
footer.show_progress(False)
check('its calls work without one', footer.is_showing_progress(), False)


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(420, 900)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.timeline_widget = types.SimpleNamespace(update=lambda: None)


session.CONFIG = {'dubbing': {'enabled': False}}
session.VIDEO = {'duration': 60}
session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Hi', 'speaker': 'A'}], 'selected': None}
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'B': {'color': '#2e7fb8'}}
host = Host()
lps.load(host)
lps.translate(host)
host.show()
QTest.qWait(100)

print('Add speaker, in the bottom line')
button = host.left_panel_speakers_add_button
check('in the footer, as the primary action', (host.left_panel_speakers_footer.isAncestorOf(button),
                                               button.property('class')), (True, 'scope_action'))
scroll = host.findChild(QScrollArea, 'left_panel_speakers_panel_scroll')
check('outside the scrolling list, so always in view', scroll.isAncestorOf(button), False)

print('speakers with no subtitles are faded')
cards = host._speakers_list_widgets
check('A has a subtitle, B none', (cards['A'].unused_fade.isEnabled(), cards['B'].unused_fade.isEnabled()), (False, True))
check('at half strength', cards['B'].unused_fade.opacity(), 0.5)
session.SUBTITLE['segments'].append({'start': 3.0, 'end': 4.0, 'text': 'Hello', 'speaker': 'B'})
lps.update(host)
QTest.qWait(100)
check('given a subtitle, B is drawn fully', cards['B'].unused_fade.isEnabled(), False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
