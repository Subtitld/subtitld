"""JSON files: saved and opened in Whisper's format, the same one the
plain-text editor shows.

Files go to a scratch folder; the XDG folders are scratch too.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session, file_io

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())
session.SUBTITLE = {
    'language': 'pt-br', 'filepath': '/somewhere/project.usfx', 'position': 12.5,
    'segments': [
        {'start': 1.0, 'end': 2.5, 'text': 'Olá\nmundo', 'speaker': 'A',
         'translations': {'en-us': 'Hello world'}, 'dubbing': [{'path': 'a.wav', 'uid': 'd1'}], '_rec': 'runtime'},
        {'start': 3.0, 'end': 4.0, 'text': 'Tchau', 'speaker': 'B', 'locked': True},
    ],
}
session.SUBTITLE['selected'] = session.SUBTITLE['segments'][0]
session.FORMAT = {}

print('saving writes Whisper\'s format')
path = folder / 'out.json'
file_io.save_file(str(path), 'JSON')
data = json.loads(path.read_text(encoding='utf-8'))
check('top level', list(data), ['language', 'segments', 'text'])
check('language and whole text', (data['language'], data['text']), ('pt-br', 'Olá mundo Tchau'))
check('segments: Whisper\'s keys, then every field, no runtime keys', data['segments'], [
    {'id': 0, 'start': 1.0, 'end': 2.5, 'text': 'Olá\nmundo', 'speaker': 'A',
     'translations': {'en-us': 'Hello world'}, 'dubbing': [{'path': 'a.wav', 'uid': 'd1'}]},
    {'id': 1, 'start': 3.0, 'end': 4.0, 'text': 'Tchau', 'speaker': 'B', 'locked': True}])
check('no project internals (selection, position, paths)', [k for k in ('selected', 'position', 'filepath') if k in data], [])

print('opening reads it all back')
session.SUBTITLE = {'language': 'en-us', 'segments': []}
segments, fmt = file_io.process_subtitles_file(str(path))
check('format', fmt, 'JSON')
check('segments', segments, [
    {'start': 1.0, 'end': 2.5, 'text': 'Olá\nmundo', 'speaker': 'A',
     'translations': {'en-us': 'Hello world'}, 'dubbing': [{'path': 'a.wav', 'uid': 'd1'}]},
    {'start': 3.0, 'end': 4.0, 'text': 'Tchau', 'speaker': 'B', 'locked': True}])
check('the project language comes with it', session.SUBTITLE['language'], 'pt-br')

print('Whisper\'s own output opens')
whisper = folder / 'whisper.json'
whisper.write_text(json.dumps({
    'text': ' Hello there.', 'language': 'en',
    'segments': [{'id': 0, 'seek': 0, 'start': 0.0, 'end': 2.0, 'text': ' Hello there.', 'tokens': [1, 2],
                  'temperature': 0.0, 'avg_logprob': -0.3, 'compression_ratio': 0.8, 'no_speech_prob': 0.01}]}))
session.SUBTITLE = {'language': 'pt-br', 'segments': []}
segments, fmt = file_io.process_subtitles_file(str(whisper))
check('one subtitle, speaker A as for other formats, no decoder statistics', segments,
      [{'start': 0.0, 'end': 2.0, 'text': 'Hello there.', 'speaker': 'A'}])
check('"en" is not a Subtitld language: the project keeps its own', session.SUBTITLE['language'], 'pt-br')

print('a broken file says where')
broken = folder / 'broken.json'
broken.write_text('{\n  "segments": [\n    {"start": 1, "end": 0.5, "text": "x"}\n  ]\n}\n')
try:
    file_io.process_subtitles_file(str(broken))
    raised = None
except file_io.CorruptedProjectFileError as exc:
    raised = str(exc)
check('the open is refused with the line and column', raised, 'Line 3, column 25: The end time must be after the start time')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
