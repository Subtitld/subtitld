"""Transcription scope: the selection scope's fields and where its text lands.

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
from subtitld.interface import left_panel_import as lpi
from subtitld.interface.translation import _

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def cue(start, end, text, speaker='A'):
    return {'start': start, 'end': end, 'text': text, 'speaker': speaker}


def fake_panel(target=None, scope_range=None, offset=0.0):
    """Stand-in for a _GenericASRPanel: the attributes its handlers touch."""
    return types.SimpleNamespace(
        _scope_target=target, _scope_target_parts=[], _scope_range=scope_range,
        _scope_offset=offset, window=lambda: types.SimpleNamespace(),
        transcript_finished=types.SimpleNamespace(emit=lambda: None))


finished = lpi._GenericASRPanel._on_finished
partial = lpi._GenericASRPanel._on_partial

print('the selected subtitle is what a selection run targets')
picked = cue(5.0, 8.0, 'old text')
session.SUBTITLE = {'segments': [cue(1.0, 2.0, 'before'), picked], 'selected': picked}
check('selected cue is the target', lpi._scope_selected_segment() is picked, True)
session.SUBTITLE['selected'] = None
check('nothing selected', lpi._scope_selected_segment(), None)
session.SUBTITLE['selected'] = cue(5.0, 8.0, 'a copy, not on the timeline')
check('a cue that is not on the timeline', lpi._scope_selected_segment(), None)
session.SUBTITLE['selected'] = picked
picked['end'] = picked['start']
check('an empty range', lpi._scope_selected_segment(), None)
picked['end'] = 8.0

print('a selection run writes the transcript into that subtitle')
picked = cue(5.0, 8.0, 'old text', speaker='B')
outside = cue(1.0, 2.0, 'before')
live = cue(5.2, 6.0, 'live partial')  # appended during the run, inside the range
session.SUBTITLE = {'segments': [outside, picked, live], 'selected': picked}
session.SPEAKERS = {'A': {}, 'B': {}}
finished(fake_panel(target=picked, scope_range=(5.0, 8.0), offset=5.0),
         [{'start': 0.0, 'end': 1.2, 'text': 'Hello there'}, {'start': 1.2, 'end': 2.5, 'text': 'second part'}])
check('the transcript lands in the selected subtitle', picked['text'], 'Hello there second part')
check('its timing and speaker are untouched', (picked['start'], picked['end'], picked['speaker']), (5.0, 8.0, 'B'))
check('no cues are added; the live partial is gone',
      [s['text'] for s in session.SUBTITLE['segments']], ['before', 'Hello there second part'])

print('partials fill the subtitle in as they arrive')
picked = cue(5.0, 8.0, '')
session.SUBTITLE = {'segments': [picked], 'selected': picked}
panel = fake_panel(target=picked, scope_range=(5.0, 8.0), offset=5.0)
partial(panel, {'start': 0.0, 'end': 1.0, 'text': 'Hello'})
partial(panel, {'start': 1.0, 'end': 2.0, 'text': 'there'})
check('text accumulates in place', picked['text'], 'Hello there')
check('still one cue', len(session.SUBTITLE['segments']), 1)

print('a subtitle deleted mid-run falls back to a scoped merge')
picked = cue(5.0, 8.0, 'old text')
session.SUBTITLE = {'segments': [cue(1.0, 2.0, 'before')], 'selected': picked}
finished(fake_panel(target=picked, scope_range=(5.0, 8.0), offset=5.0),
         [{'start': 0.0, 'end': 1.0, 'text': 'spliced'}])
check('the result is spliced into the range instead',
      [(s['start'], s['text']) for s in session.SUBTITLE['segments']], [(1.0, 'before'), (5.0, 'spliced')])

print('a range run still replaces what is inside the range')
session.SUBTITLE = {'segments': [cue(1.0, 2.0, 'before'), cue(6.0, 7.0, 'inside'), cue(9.0, 10.0, 'after')],
                    'selected': None}
finished(fake_panel(target=None, scope_range=(5.0, 8.0), offset=5.0),
         [{'start': 0.0, 'end': 1.0, 'text': 'new'}])
check('inside replaced, outside kept',
      [(s['start'], s['text']) for s in session.SUBTITLE['segments']],
      [(1.0, 'before'), (5.0, 'new'), (9.0, 'after')])

print('the scope bar shows labels for selection, inputs for range')


class Host(QWidget):
    def __init__(self):
        super().__init__()
        # The whole-media path repaints the timeline; a selection run must
        # never reach it, but the stub keeps a mutation from crashing here
        # instead of failing a check below.
        self.timeline_widget = types.SimpleNamespace(update=lambda: None)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)


picked = cue(5.0, 8.0, 'old text')
session.SUBTITLE = {'segments': [picked], 'selected': picked, 'language': 'en-us', 'position': 0.0}
session.CONFIG = {'transcription': {'scope': 'range', 'scope_from': 0.0, 'scope_to': 30.0, 'scope_expanded': True}}
host = Host()
host.resize(520, 560)
host.show()
lpi.load(host)
app.processEvents()
check('range: the inputs are the editable fields',
      (host.transcription_scope_from_input.isVisible(), host.transcription_scope_from_value.isVisible()),
      (True, False))
session.CONFIG['transcription']['scope'] = 'selection'
lpi.global_panel_import_scope_update(host)
app.processEvents()
check('selection: labels instead of inputs',
      (host.transcription_scope_from_input.isVisible(), host.transcription_scope_from_value.isVisible(),
       host.transcription_scope_to_input.isVisible(), host.transcription_scope_to_value.isVisible()),
      (False, True, False, True))
check('they show the selected subtitle',
      (host.transcription_scope_from_value.text(), host.transcription_scope_duration_value.text(),
       host.transcription_scope_to_value.text()),
      ('00:00:05.000', '00:00:03.000', '00:00:08.000'))
check('the value labels shrink instead of widening the panel',
      host.transcription_scope_from_value.minimumSizeHint().width(), 0)
check('the value labels share one style',
      {w.property('class') for w in (host.transcription_scope_from_value, host.transcription_scope_duration_value,
                                     host.transcription_scope_to_value)},
      {'transcription_scope_value'})
session.SUBTITLE['selected'] = None
lpi.global_panel_import_scope_update(host)
check('no selection shows dashes',
      (host.transcription_scope_from_value.text(), host.transcription_scope_to_value.text()), ('—', '—'))
check('and there is nothing to transcribe', lpi._current_scope_range(host), None)

print('selection with nothing selected never falls through to a whole-media run')
session.SUBTITLE = {'segments': [cue(1.0, 2.0, 'keep me')], 'selected': None, 'language': 'en-us'}
session.CONFIG['transcription']['scope'] = 'selection'
warned = []


class FakeDialog:
    def __init__(self, parent=None, title=''):
        warned.append(title)
        self.content = QWidget()
        self.content.setLayout(QVBoxLayout())
        self.reject_button = QWidget()

    def exec(self):
        return 1

    def result(self):
        # "Yes, replace every subtitle" — what the whole-media confirmation
        # would get. The guard must return before ever asking.
        return 1


real_dialog = lpi.utils.SimpleDialog
lpi.utils.SimpleDialog = FakeDialog
try:
    lpi.global_panel_import_start_transcription_button_clicked(host)
finally:
    lpi.utils.SimpleDialog = real_dialog
check('it warns instead of starting', warned, [_('transcription_panel.error')])
check('the subtitles are untouched', [s['text'] for s in session.SUBTITLE['segments']], ['keep me'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
