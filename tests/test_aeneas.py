"""Aeneas sync maps: reading one as aeneas writes it (and a multilevel one,
and Subtitle Edit's), writing aeneas's layout, a round trip, telling it
from the speech recognisers' JSON and Subtitld's own, and the files and
the export dialog.

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
from subtitld.modules import aeneas, file_io, json_subtitles, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# As SyncMap.json_string writes one: indent 1, keys sorted, times SS.mmm.
MAP = json.dumps({'fragments': [
    {'begin': '0.000', 'children': [], 'end': '1.320', 'id': 'f000001', 'language': 'por', 'lines': ['Primeira linha', 'segunda']},
    {'begin': '1.320', 'children': [], 'end': '2.000', 'id': 'f000002', 'language': 'por', 'lines': ['Outra']}]},
    indent=1, sort_keys=True)
# A multilevel map: a paragraph with no lines of its own, its sentences.
LEVELS = {'fragments': [{'begin': '0.000', 'end': '4.000', 'id': 'p000001', 'language': 'eng', 'lines': [], 'children': [
    {'begin': '0.000', 'end': '2.000', 'id': 'p000001s000001', 'language': 'eng', 'lines': ['One.'], 'children': []},
    {'begin': '2.000', 'end': '4.000', 'id': 'p000001s000002', 'language': 'eng', 'lines': ['Two.'], 'children': []}]}]}
print('reading')
check('a fragment a subtitle, its lines its lines, its language', aeneas.parse(MAP),
      ([{'start': 0.0, 'end': 1.32, 'text': 'Primeira linha\nsegunda'}, {'start': 1.32, 'end': 2.0, 'text': 'Outra'}], 'por'))
check('a multilevel map: the sentences of a paragraph with no lines', aeneas.parse(LEVELS)[0],
      [{'start': 0.0, 'end': 2.0, 'text': 'One.'}, {'start': 2.0, 'end': 4.0, 'text': 'Two.'}])
SE = '{\n    "fragments": [\n        {\n            "begin": "1.5",\n            "children": [],\n            "end": "3",\n' \
     '            "id": "f000001",\n            "language": "eng",\n            "lines": [\n                "She said \\"hi\\""\n' \
     '            ]\n        }\n    ]\n}'
check("Subtitle Edit's, times as it writes them", aeneas.parse(SE.encode())[0], [{'start': 1.5, 'end': 3.0, 'text': 'She said "hi"'}])

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': '<i>Two</i>\nlines'}, {'start': 3.0, 'end': 3.25, 'text': '{\\an8}Top'}]
written = aeneas.write(trip, 'pt-br')
check("aeneas's layout", json.loads(written), {'fragments': [
    {'begin': '1.000', 'children': [], 'end': '2.500', 'id': 'f000001', 'language': 'por', 'lines': ['Two', 'lines']},
    {'begin': '3.000', 'children': [], 'end': '3.250', 'id': 'f000002', 'language': 'por', 'lines': ['Top']}]})
check('indented and sorted as aeneas does', written == json.dumps(json.loads(written), indent=1, sort_keys=True) + '\n', True)
check('read back', aeneas.parse(written), ([{'start': 1.0, 'end': 2.5, 'text': 'Two\nlines'}, {'start': 3.0, 'end': 3.25, 'text': 'Top'}], 'por'))

print('telling them apart')
OTHERS = {'Subtitld': {'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'Hi'}]},
          'WhisperX': {'segments': [{'start': 0, 'end': 1, 'text': 'Hi', 'words': [], 'speaker': 'SPEAKER_00'}]},
          'Deepgram': {'results': {'channels': []}}, 'Bilibili': {'body': [{'from': 0, 'to': 1, 'content': 'Hi'}]},
          'Amara': [{'start': 0, 'end': 1, 'text': 'Hi', 'meta': {}}], 'fragments of something else': {'fragments': [{'a': 1}]}}
check('a sync map is one, as text, cut short, or parsed', (aeneas.looks_like(MAP), aeneas.looks_like(MAP[:40]), aeneas.looks_like(LEVELS)),
      (True, True, True))
check('no other JSON is', {name: aeneas.looks_like(data) for name, data in OTHERS.items()}, dict.fromkeys(OTHERS, False))
check('and the recognisers\' reader does not take it', json_subtitles.kind(json.loads(MAP)), None)

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.SUBTITLE['language'] = 'en-us'
(folder / 'map.json').write_text(MAP, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'map.json'))
check("a sync map opens as Aeneas, its language the project's", (fmt, len(opened), session.SUBTITLE['language']),
      ('AENEAS', 2, 'pt-pt'))
(folder / 'whisperx.json').write_text(json.dumps(OTHERS['WhisperX']), encoding='utf-8')
check('a WhisperX .json still opens as JSON', file_io.process_subtitles_file(str(folder / 'whisperx.json'))[1], 'JSON')
session.SUBTITLE['segments'] = trip
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.json'), 'AENEAS')
check('saved, and opened again', file_io.process_subtitles_file(str(folder / 'out.json'))[1], 'AENEAS')

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('AENEAS')
panel._refresh_options()
check('in the export menu, with the text options, no formatting',
      ('AENEAS' in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.formatting_combo.isHidden()), (True, False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
