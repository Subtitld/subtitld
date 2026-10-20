"""Picking a speaker inside a multi-speaker voice (Piper's VCTK, LibriTTS, ...).

A manifest voice can list `speakers`; the generic dubbing panels then show a
"Speaker in this voice" combobox for it, store the choice as `voice_speaker`
next to the voice, and send it with every request. Covers the per-speaker and
per-subtitle panels, the regenerate path's resolver, and the add-on provider
forwarding the key and recording it on the take.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, wave
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
    {'id': 'en_US-mike-medium', 'display_name': 'Mike'},
    {'id': 'en_GB-vctk-medium', 'display_name': 'VCTK',
     'speakers': [{'id': 'p239'}, {'id': 'p236'}, {'id': 'p225', 'display_name': 'p225 (female)'}]},
    {'id': 'de_DE-thorsten_emotional-medium', 'display_name': 'Thorsten emotional',
     'speakers': [{'id': 'amused'}, {'id': 'angry'}]},
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


def cue(start, end, text, speaker='A'):
    return {'start': start, 'end': end, 'text': text, 'speaker': speaker}


def pick(combobox, data):
    """Select the row holding `data` and fire `activated`, as a click does."""
    combobox.combobox.setCurrentIndex(combobox.combobox.findData(data))
    combobox.activated.emit()


session.CONFIG = {'dubbing': {'scope': 'all'}}
session.VIDEO = {'duration': 60.0}
session.SPEAKERS = {'A': {'dubbing': {'voice': 'en_US-mike-medium'}}}
line_one, line_two = cue(1.0, 2.0, 'one'), cue(3.0, 4.0, 'two')
session.SUBTITLE = {'segments': [line_one, line_two], 'selected': None, 'position': 0.0}

provider = FakeProvider()

print('the speaker panel shows the choice only for a voice that has speakers')
panel = lpd._GenericTTSSpeakerPanel(provider)
panel.setProperty('speaker', 'A')
panel.translate()
panel.show()
panel.update()
app.processEvents()
check('a single-speaker voice hides it', panel.voice_speaker_combobox.isVisible(), False)
check('its label', panel.voice_speaker_combobox.label.text(), 'Speaker in this voice')
pick(panel.voice_combobox, 'en_GB-vctk-medium')
app.processEvents()
check('VCTK shows it', panel.voice_speaker_combobox.isVisible(), True)
check('with its speakers in model order, display names where given',
      [panel.voice_speaker_combobox.combobox.itemText(i)
       for i in range(panel.voice_speaker_combobox.combobox.count())],
      ['p239', 'p236', 'p225 (female)'])
check('on the default (first) speaker', panel.voice_speaker_combobox.combobox.currentData(), 'p239')

print('the choice is stored with the voice and sent with every line')
pick(panel.voice_speaker_combobox, 'p225')
check('stored on the speaker', session.SPEAKERS['A']['dubbing'],
      {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p225'})
panel.generate_all_speeches_button_clicked()
check('both requests carry it', [r.get('voice_speaker') for r in provider.requests], ['p225', 'p225'])
check('and each line keeps it with its voice',
      [(s['dubbing_options']['voice'], s['dubbing_options'].get('voice_speaker')) for s in (line_one, line_two)],
      [('en_GB-vctk-medium', 'p225')] * 2)

print('the panel reads it back')
panel.voice_speaker_combobox.combobox.setCurrentIndex(0)
panel.update()
check('update() selects the stored speaker', panel.voice_speaker_combobox.combobox.currentData(), 'p225')

print('another voice starts on its own default speaker')
pick(panel.voice_combobox, 'de_DE-thorsten_emotional-medium')
check('the old speaker is dropped', 'voice_speaker' in session.SPEAKERS['A']['dubbing'], False)
check('the list is the new voice\'s', panel.voice_speaker_combobox.combobox.currentData(), 'amused')
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('the lines follow to the new voice', [r['voice'] for r in provider.requests],
      ['de_DE-thorsten_emotional-medium'] * 2)
check('no speaker is sent: the add-on uses the default',
      ['voice_speaker' in r for r in provider.requests], [False, False])
check('nor left on the lines', ['voice_speaker' in s['dubbing_options'] for s in (line_one, line_two)],
      [False, False])
pick(panel.voice_combobox, 'en_US-mike-medium')
check('a single-speaker voice hides it again', panel.voice_speaker_combobox.isVisible(), False)

print('the per-subtitle panel overrides it for one line')
session.SPEAKERS['A']['dubbing'] = {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p236'}
line_one['dubbing_options'] = {}
session.SUBTITLE['selected'] = line_one
line_panel = lpd._GenericTTSDubbingPanel(provider)
line_panel.translate()
line_panel.show()
line_panel.update()
app.processEvents()
check('it follows the speaker\'s choice', line_panel.voice_speaker_combobox.combobox.currentData(), 'p236')
pick(line_panel.voice_speaker_combobox, 'p225')
check('a pick is stored on the line, with its voice', line_one['dubbing_options'],
      {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p225'})
provider.requests.clear()
line_panel.generate_speech_button_clicked()
check('and sent', provider.requests[0].get('voice_speaker'), 'p225')
line_one['dubbing'] = [{'engine': 'piper-tts', 'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p239'}]
line_panel.update()
check('the latest take wins when showing the line', line_panel.voice_speaker_combobox.combobox.currentData(), 'p239')

print('the resolver used by regenerate')
check('a take\'s speaker comes first',
      lpd._resolve_voice_speaker(provider, 'en_GB-vctk-medium',
                                 {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p239'},
                                 {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p225'}), 'p239')
check('a speaker chosen for another voice does not carry over',
      lpd._resolve_voice_speaker(provider, 'en_GB-vctk-medium',
                                 {'voice': 'de_DE-thorsten_emotional-medium', 'voice_speaker': 'angry'}), '')
check('nor a name the voice does not have',
      lpd._resolve_voice_speaker(provider, 'en_GB-vctk-medium',
                                 {'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p999'}), '')

print('the add-on provider forwards it and records it on the take')
from subtitld.modules.addons.addon_provider import AddonTTSProvider
addon = AddonTTSProvider('piper-tts', {'voices': VOICES}, '/nonexistent')
addon._drain_next_tts = lambda: None
addon.generate_speeches([{'uid': 'u1', 'text': 'one', 'start': 1.0, 'end': 2.0,
                          'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p225'}])
check('in the request params', addon._tts_queue[0][3].get('voice_speaker'), 'p225')
wav_path = os.path.join(_xdg, 'take.wav')
with wave.open(wav_path, 'wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(22050)
    wf.writeframes(b'\0\0' * 2205)
line_one['dubbing'] = []
addon._on_speech_ready('u1', {'start': 1.0, 'voice': 'en_GB-vctk-medium', 'voice_speaker': 'p225'}, wav_path)
check('on the new take', line_one['dubbing'][0].get('voice_speaker'), 'p225')
addon._on_speech_ready('u2', {'start': 1.0, 'voice': 'en_US-mike-medium'}, wav_path)
check('and only when there is one', 'voice_speaker' in line_one['dubbing'][0], False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
