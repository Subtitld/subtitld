"""Changing a speaker's voice after "Generate all speeches".

The speaker batch pins the voice it used on each line (`dubbing_options.voice`)
so a crash mid-batch or a later regenerate keeps it, and a line's own voice
beats the speaker's on the next batch. Changing the speaker's voice must
release the lines that were only following the speaker, or the next batch
synthesizes them with the old voice again; a voice picked for one line in the
per-subtitle panel must survive it. Covers the add-on panels and Edge TTS.

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
    {'id': 'en_US-mike-medium', 'display_name': 'Mike'},
    {'id': 'en_US-amy-medium', 'display_name': 'Amy'},
    {'id': 'en_GB-alan-medium', 'display_name': 'Alan'},
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


def voices_sent(requests):
    return [r['voice'] for r in requests]


session.CONFIG = {'dubbing': {'scope': 'all'}}
session.VIDEO = {'duration': 60.0}
session.SPEAKERS = {'A': {'dubbing': {'voice': 'en_US-mike-medium'}},
                    'B': {'dubbing': {'voice': 'en_US-mike-medium'}}}
line_one, line_two, line_three = cue(1.0, 2.0, 'one'), cue(3.0, 4.0, 'two'), cue(5.0, 6.0, 'three')
other_line = cue(7.0, 8.0, 'other', speaker='B')
session.SUBTITLE = {'segments': [line_one, line_two, line_three, other_line],
                    'selected': None, 'position': 0.0}

provider = FakeProvider()
panel = lpd._GenericTTSSpeakerPanel(provider)
panel.setProperty('speaker', 'A')
panel.translate()
panel.show()
panel.update()
app.processEvents()

print('a line given its own voice in the per-subtitle panel')
session.SUBTITLE['selected'] = line_two
line_panel = lpd._GenericTTSDubbingPanel(provider)
line_panel.translate()
line_panel.show()
line_panel.update()
pick(line_panel.voice_combobox, 'en_GB-alan-medium')
check('is stored on the line', line_two['dubbing_options'].get('voice'), 'en_GB-alan-medium')

print('the first batch uses the speaker voice and pins it on the lines')
panel.generate_all_speeches_button_clicked()
check('requests', voices_sent(provider.requests),
      ['en_US-mike-medium', 'en_GB-alan-medium', 'en_US-mike-medium'])
check('pinned', [s['dubbing_options']['voice'] for s in (line_one, line_two, line_three)],
      ['en_US-mike-medium', 'en_GB-alan-medium', 'en_US-mike-medium'])

print('another speaker on the same voice, already generated')
other_line['dubbing_options'] = {'engine': 'piper-tts', 'voice': 'en_US-mike-medium'}

print('switching the speaker voice, then generating again')
pick(panel.voice_combobox, 'en_US-amy-medium')
check('the speaker has the new voice', session.SPEAKERS['A']['dubbing']['voice'], 'en_US-amy-medium')
check('lines that followed the speaker are released',
      ['voice' in s['dubbing_options'] for s in (line_one, line_three)], [False, False])
check('the hand-picked line keeps its voice', line_two['dubbing_options'].get('voice'), 'en_GB-alan-medium')
check('another speaker\'s line is left alone', other_line['dubbing_options'].get('voice'), 'en_US-mike-medium')
provider.requests.clear()
panel.generate_all_speeches_button_clicked()
check('the new voice applies to the following lines, not to the hand-picked one',
      voices_sent(provider.requests), ['en_US-amy-medium', 'en_GB-alan-medium', 'en_US-amy-medium'])
check('and is pinned in turn', [s['dubbing_options']['voice'] for s in (line_one, line_two, line_three)],
      ['en_US-amy-medium', 'en_GB-alan-medium', 'en_US-amy-medium'])

print('picking the voice the speaker already has changes nothing')
pick(panel.voice_combobox, 'en_US-amy-medium')
check('the pins stay', [s['dubbing_options'].get('voice') for s in (line_one, line_three)],
      ['en_US-amy-medium', 'en_US-amy-medium'])

print('a speaker inside the old voice goes with it')
line_one['dubbing_options']['voice_speaker'] = 'p239'
pick(panel.voice_combobox, 'en_US-mike-medium')
check('voice and voice_speaker dropped', {k: v for k, v in line_one['dubbing_options'].items()
                                          if k in ('voice', 'voice_speaker')}, {})
check('the rest of the line options stay', line_one['dubbing_options'].get('engine'), 'piper-tts')

print('the per-subtitle panel shows the released line on the speaker voice')
session.SUBTITLE['selected'] = line_three
line_panel.update()
check('it shows the speaker\'s new voice', line_panel.voice_combobox.combobox.currentData(), 'en_US-mike-medium')

print('Edge TTS, same rule')
edge_voices = {'en-US-AriaNeural': {}, 'en-US-GuyNeural': {}, 'en-GB-RyanNeural': {}}
session.CONFIG['dubbing']['edge-tts'] = {'voices': edge_voices}
edge_requests = []
lpd.EdgeTTSEngine.generate_speeches = staticmethod(lambda text_list: edge_requests.extend(text_list))
session.SPEAKERS['A']['dubbing'] = {'voice': 'en-US-AriaNeural'}
for line in (line_one, line_two, line_three):
    line['dubbing_options'] = {}
line_two['dubbing_options']['voice'] = 'en-GB-RyanNeural'
edge_panel = lpd._EdgeTTSSpeakerPanel()
edge_panel.setProperty('speaker', 'A')
edge_panel.translate()
edge_panel.show()
edge_panel.update()
edge_panel.generate_all_speeches_button_clicked()
check('first batch', voices_sent(edge_requests), ['en-US-AriaNeural', 'en-GB-RyanNeural', 'en-US-AriaNeural'])
edge_panel.voice_combobox.setCurrentText('en-US-GuyNeural')
edge_panel.voice_combobox.activated.emit()
edge_requests.clear()
edge_panel.generate_all_speeches_button_clicked()
check('after the switch', voices_sent(edge_requests), ['en-US-GuyNeural', 'en-GB-RyanNeural', 'en-US-GuyNeural'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
