"""A speaker's lines follow its voice after "Generate all speeches".

The batch copies the speaker's voice onto each line's `dubbing_options`, and
a line's own voice wins over the speaker's. Changing the speaker's voice used
to leave every line already generated on the old one. Now the lines that were
only following the speaker let go of it, while a voice set on one line in the
per-subtitle panel stays. Same for the speaker inside a multi-speaker voice.
Covers the add-on panel and the Edge TTS one.

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
from PySide6.QtCore import QDir, QObject, Signal
app = QApplication([])
from subtitld.modules import session
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import left_panel_dubbing as lpd

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


VOICES = [
    {'id': 'mike'},
    {'id': 'amy'},
    {'id': 'vctk', 'speakers': [{'id': 'p239'}, {'id': 'p236'}, {'id': 'p225'}]},
]


class FakeProvider(QObject):
    voices_updated = Signal()
    id = 'piper-tts'

    def __init__(self):
        super().__init__()
        self.requests = []

    def list_voices(self):
        return VOICES

    def generate_speeches(self, text_list):
        self.requests.extend(text_list)


def cue(start, text, speaker='A'):
    return {'start': start, 'end': start + 1.0, 'text': text, 'speaker': speaker}


def pick(combobox, data):
    """Select the row holding `data` and fire `activated`, as a click does."""
    combobox.combobox.setCurrentIndex(combobox.combobox.findData(data))
    combobox.activated.emit()


def sent(requests):
    return {r['text']: (r['voice'], r.get('voice_speaker')) for r in requests}


session.CONFIG = {'dubbing': {'scope': 'all'}}
session.VIDEO = {'duration': 60.0}
session.SPEAKERS = {'A': {'dubbing': {}}, 'B': {'dubbing': {'voice': 'mike'}}}
one, two, own, other = cue(1, 'one'), cue(3, 'two'), cue(5, 'own'), cue(7, 'other', speaker='B')
session.SUBTITLE = {'segments': [one, two, own, other], 'selected': None, 'position': 0.0}
provider = FakeProvider()
panel = lpd._GenericTTSSpeakerPanel(provider)
panel.setProperty('speaker', 'A')
panel.update()

print('a first batch on the voice the panel shows')
panel.generate_all_speeches_button_clicked()
check('every line on it', sent(provider.requests),
      {'one': ('mike', None), 'two': ('mike', None), 'own': ('mike', None)})
check('which becomes the speaker\'s voice', session.SPEAKERS['A']['dubbing'].get('voice'), 'mike')

print('one line gets its own voice in the per-subtitle panel')
own['dubbing_options']['voice'] = 'amy'

print('the speaker\'s voice changes')
pick(panel.voice_combobox, 'vctk')
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('the following lines move to it, the line with its own voice stays', sent(provider.requests),
      {'one': ('vctk', None), 'two': ('vctk', None), 'own': ('amy', None)})
check('another speaker\'s line on the same old voice is left alone',
      other.get('dubbing_options'), None)

print('the speaker inside the voice changes, after a line picked its own')
pick(panel.voice_speaker_combobox, 'p236')
two['dubbing_options']['voice_speaker'] = 'p225'
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('the speaker\'s pick reaches the following line, the line\'s own pick stays',
      sent(provider.requests),
      {'one': ('vctk', 'p236'), 'two': ('vctk', 'p225'), 'own': ('amy', None)})
pick(panel.voice_speaker_combobox, 'p239')
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('and changing it again moves only the following line',
      sent(provider.requests),
      {'one': ('vctk', 'p239'), 'two': ('vctk', 'p225'), 'own': ('amy', None)})

print('a new voice drops the old speaker everywhere it was following')
pick(panel.voice_combobox, 'mike')
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('lines on the speaker\'s voice move, with no stale speaker name',
      sent(provider.requests),
      {'one': ('mike', None), 'two': ('mike', None), 'own': ('amy', None)})

print('the Edge TTS speaker panel follows too')
edge_sent = []
lpd.EdgeTTSEngine.generate_speeches = lambda text_list: edge_sent.extend(text_list)
session.CONFIG['dubbing']['edge-tts'] = {'voices': dict.fromkeys(
    ['en-US-AriaNeural', 'en-GB-RyanNeural', 'en-AU-NatashaNeural'], {})}
session.SPEAKERS['A']['dubbing'] = {'voice': 'en-US-AriaNeural'}
for line in (one, two, own):
    line['dubbing_options'] = {}
edge = lpd._EdgeTTSSpeakerPanel()
edge.setProperty('speaker', 'A')
edge.update()
edge.generate_all_speeches_button_clicked()
own['dubbing_options']['voice'] = 'en-GB-RyanNeural'
edge.voice_combobox.setCurrentText('en-AU-NatashaNeural')
edge.voice_combobox_changed()
edge_sent.clear()
edge.generate_all_speeches_button_clicked()
check('the following lines move, the line with its own voice stays',
      {r['text']: r['voice'] for r in edge_sent},
      {'one': 'en-AU-NatashaNeural', 'two': 'en-AU-NatashaNeural', 'own': 'en-GB-RyanNeural'})

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
