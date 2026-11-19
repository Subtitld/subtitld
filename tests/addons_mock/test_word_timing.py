"""Words timed by speech recognition add-ons: a transcription whose segments
carry `words` comes in timed word by word when the panel asks for it, and
plain otherwise; an add-on that offers `asr.align` times a subtitle's words
against the audio, through the real AddonManager and AddonASRProvider stack
and a mock add-on (mock_asr_addon) in scratch directories.

Standalone script, like the other suites here.
"""
import os, subprocess, sys, tempfile, time

_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session
from subtitld.modules.addons import get_manager
from subtitld.modules.addons.provider import TASK_ASR_ALIGN, TASK_ASR_TRANSCRIBE
from subtitld.modules.segment import Subtitle, timed_pieces
import mock_asr_addon

fails = []


def check(name, got, want):
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f'  (got {got!r}, want {want!r})'))
    if not ok:
        fails.append(name)


def wait_for(condition, seconds=15):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return bool(condition())


mock_asr_addon.install(session.PATH_SUBTITLD_ADDONS)
manager = get_manager()
manager.discover()
provider = manager.get('mock-asr')

print('the tasks an add-on declares')
check('found', provider is not None, True)
check('it transcribes and aligns', provider.tasks, [TASK_ASR_TRANSCRIBE, TASK_ASR_ALIGN])
check('an aligner, for the timing menu', manager.default_for_task(TASK_ASR_ALIGN) is provider, True)

print('a transcription with words')
from subtitld.interface import left_panel_import
results = []
provider.transcript_finished.connect(results.append)
provider.transcribe('/nonexistent.wav', 'en')
check('it answers', wait_for(lambda: results), True)
session.CONFIG = {'transcription': {'word_timing': False}}
plain = left_panel_import.timed_words(results[0])
check('not asked for: plain text, the words not kept', (plain[0]['text'], 'words' in plain[0]),
      ('Hello, big world.', False))
session.CONFIG['transcription']['word_timing'] = True
timed = left_panel_import.timed_words(results[0])
check('asked for: timed word by word, the text as written',
      [(p['text'], p['start'], p['end']) for p in timed_pieces(timed[0])],
      [('Hello, ', 0.0, 0.4), ('big ', 0.5, 0.8), ('world.', 0.9, 1.5)])
check('the add-on\'s result left as it was', 'words' in results[0][0], True)

print('aligning a subtitle')
media = os.path.join(tempfile.mkdtemp(), 'tone.wav')
subprocess.run([session.FFMPEG_EXECUTABLE, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
                media], check=True)
session.VIDEO = {'filepath': media}
session.SUBTITLE = Subtitle({'segments': [{'id': 'a1', 'start': 1.0, 'end': 3.0, 'text': '<i>Never</i> drink, ever!'}],
                             'language': 'en'})
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
from subtitld.interface import word_alignment
check('it can align', word_alignment.available(), True)
answers = []
started = word_alignment.align(None, session.SUBTITLE.selected, on_done=lambda ok, message: answers.append((ok, message)))
check('it starts', started, True)
check('and finishes', wait_for(lambda: answers), True)
check('well', answers[:1], [(True, '')])
check('the words timed against the subtitle\'s audio, the text as written',
      [(p['text'], p['start'], p['end']) for p in timed_pieces(session.SUBTITLE.segments[0])],
      [('<i>Never</i> ', 0.0, 0.625), ('drink, ', 0.625, 1.375), ('ever!', 1.375, 2.0)])
session.VIDEO = {}
check('no audio: it cannot', word_alignment.available(), False)

manager.shutdown_all()
print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
