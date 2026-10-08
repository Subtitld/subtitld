"""The shared SCOPE selector, as mounted by the translation and dubbing panels.

The transcription panel's own use is covered by test_transcription_scope.py;
this suite is about the other two hosts picking up the SAME widget and the
same behaviour — a disabled action button (plus a hint) when scope is
"selection" with nothing selected, and correct scoping of what each one acts on.

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
from PySide6.QtCore import QDir
app = QApplication([])
from subtitld.modules import session
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import scope_selector
from subtitld.interface.scope_selector import ScopeSelector
from subtitld.interface.translation import _

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def cue(start, end, text, speaker='A'):
    return {'start': start, 'end': end, 'text': text, 'speaker': speaker}


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


print('each host keeps its own scope — picking Range to translate does not')
print('also narrow the next transcription')
session.CONFIG = {}
session.VIDEO = {'duration': 60.0}
session.SUBTITLE = {'segments': [], 'selected': None, 'position': 0.0}
one = ScopeSelector('transcription')
two = ScopeSelector('translation')
session.CONFIG['translation']['scope'] = 'range'
two.refresh()
check('the two read different config sections', (one.scope, two.scope), ('all', 'range'))

print('scoped_segments narrows to what the scope covers')
a, b, c = cue(0.0, 2.0, 'first'), cue(10.0, 12.0, 'middle'), cue(50.0, 52.0, 'last')
session.SUBTITLE = {'segments': [a, b, c], 'selected': b, 'position': 0.0}
sel = ScopeSelector('probe')
session.CONFIG['probe'].update({'scope': 'all'})
check('all → every subtitle', [s['text'] for s in sel.scoped_segments()], ['first', 'middle', 'last'])
session.CONFIG['probe'].update({'scope': 'range', 'scope_from': 5.0, 'scope_to': 20.0})
sel.refresh()
check('range → only what overlaps it', [s['text'] for s in sel.scoped_segments()], ['middle'])
session.CONFIG['probe']['scope'] = 'selection'
sel.refresh()
check('selection → the selected subtitle', [s['text'] for s in sel.scoped_segments()], ['middle'])
session.SUBTITLE['selected'] = None
sel.refresh()
check('selection with nothing selected → nothing', sel.scoped_segments(), [])
check('and it reports itself as not ready', sel.is_ready(), False)

print('a range that overlaps a subtitle only partly still includes it —')
print('half a line still needs translating')
session.CONFIG['probe'].update({'scope': 'range', 'scope_from': 11.0, 'scope_to': 40.0})
sel.refresh()
check('a straddling subtitle is in scope', [s['text'] for s in sel.scoped_segments()], ['middle'])

print('the translation panel mounts it and gates START TRANSLATION')
from subtitld.interface import left_panel_translation as lpt
picked = cue(5.0, 8.0, 'translate me')
session.SUBTITLE = {'segments': [picked], 'selected': picked, 'language': 'en-us', 'position': 0.0}
session.CONFIG['translation'] = {'engine': 'GoogleTranslator', 'engine_options': {'target_language': 'en-us'},
                                 'scope': 'selection'}
host = Host()
host.resize(520, 560)
host.show()
lpt.load(host)
lpt.translate(host)
app.processEvents()
check('the panel has the shared selector', isinstance(host.translation_scope, ScopeSelector), True)
check('a usable selection leaves the button live',
      host.global_panel_translation_start_translation_button.isEnabled(), True)
check('and it translates just that subtitle',
      [s['text'] for s in host.translation_scope.scoped_segments()], ['translate me'])
session.SUBTITLE['selected'] = None
lpt.update(host)
check('nothing selected disables the button',
      host.global_panel_translation_start_translation_button.isEnabled(), False)
check('the times give way to the hint',
      (host.translation_scope.from_field.isVisible(), host.translation_scope.hint_label.isVisible()),
      (False, True))
check('and the dead button explains itself too',
      host.global_panel_translation_start_translation_button.toolTip(),
      host.translation_scope.hint_label.text())
check('the hint says what to do', host.translation_scope.hint_label.isVisible(), True)
check('it is the selection hint', host.translation_scope.hint_label.text(),
      _('panel_scope.selection_empty'))
session.CONFIG['translation']['scope'] = 'all'
lpt.update(host)
check('scope "all" needs no selection',
      (host.global_panel_translation_start_translation_button.isEnabled(),
       host.translation_scope.hint_label.isVisible()),
      (True, False))

print('a dubbing speaker panel scopes to ITS speaker, then to the scope')
from subtitld.interface import left_panel_dubbing as lpd
alice, bob = cue(1.0, 2.0, 'alice line', speaker='A'), cue(10.0, 12.0, 'bob line', speaker='B')
session.SUBTITLE = {'segments': [alice, bob], 'selected': bob, 'position': 0.0}
session.CONFIG['dubbing'] = {'scope': 'all'}
panel = QWidget()
panel.setLayout(QVBoxLayout())
panel.setProperty('speaker', 'A')
lpd._install_scope_footer(panel)  # scope row + the Generate button in it
panel.show()  # isVisible() is False for every child of an unshown widget
app.processEvents()
check('all → this speaker\'s subtitles only',
      [s['text'] for s in lpd._speaker_scoped_segments(panel, 'A')], ['alice line'])
session.CONFIG['dubbing']['scope'] = 'selection'
panel.scope_selector.refresh()
check("selection → nothing, because the selected line is another speaker's",
      lpd._speaker_scoped_segments(panel, 'A'), [])
check("but it IS in scope for its own speaker",
      [s['text'] for s in lpd._speaker_scoped_segments(panel, 'B')], ['bob line'])
session.SUBTITLE['selected'] = alice
panel.scope_selector.refresh()
check('selecting this speaker\'s line puts it in scope',
      [s['text'] for s in lpd._speaker_scoped_segments(panel, 'A')], ['alice line'])
session.SUBTITLE['selected'] = None
panel.scope_selector.refresh()
lpd._reconcile_generate_button(panel)
check('nothing selected disables "generate all speeches"',
      panel.generate_all_speeches_button.isEnabled(), False)
check('with the hint to explain it', panel.scope_selector.hint_label.isVisible(), True)
session.CONFIG['dubbing']['scope'] = 'range'
session.CONFIG['dubbing'].update({'scope_from': 8.0, 'scope_to': 20.0})
panel.scope_selector.refresh()
lpd._reconcile_generate_button(panel)
check('range re-enables it', panel.generate_all_speeches_button.isEnabled(), True)
check('and scopes to the range within the speaker',
      ([s['text'] for s in lpd._speaker_scoped_segments(panel, 'A')],
       [s['text'] for s in lpd._speaker_scoped_segments(panel, 'B')]),
      ([], ['bob line']))

print('the footer swaps its buttons for an edge-to-edge progress bar')
from PySide6.QtWidgets import QProgressBar, QPushButton
from subtitld.interface.scope_selector import ScopeFooter
session.CONFIG['footer_probe'] = {'scope': 'all'}
holder = QWidget()
holder.setLayout(QVBoxLayout())
holder.layout().setContentsMargins(0, 0, 0, 0)
footer = ScopeFooter('footer_probe', parent=holder)
holder.layout().addWidget(footer)
button = footer.add_action(QPushButton('Start'), primary=True)
bar = footer.set_progress_bar(QProgressBar())
holder.resize(500, 200)
holder.show()
app.processEvents()
idle_height = footer.row.height()
check('idle: the button is inset from the edge', button.mapTo(footer, button.rect().topRight()).x() < footer.width() - 1, True)
footer.show_progress(True)
app.processEvents()
check('running: the bar spans the row edge to edge',
      (bar.mapTo(footer.row, bar.rect().topLeft()).x(), bar.width(), bar.height()),
      (0, footer.row.width(), footer.row.height()))
check('the row keeps its height', footer.row.height(), idle_height)
check('the scope hides while it runs', footer.selector.isVisible(), False)
footer.show_progress(False)
app.processEvents()
check('back to the button and the scope', (button.isVisible(), footer.selector.isVisible()), (True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
