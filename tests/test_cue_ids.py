"""Stable subtitle IDs (modules.cue_ids): every subtitle gets one, keeps it
through editing, saving, opening, undo and the plain-text panel, and a split
gives the new half its own.

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
app = QApplication([])
from subtitld.modules import cue_ids, file_io, history, plaintext, session, subtitles, usf

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def fresh(*segments):
    history.history_clear()
    session.SUBTITLE['segments'] = [dict(segment) for segment in segments]
    session.SUBTITLE['selected'] = None
    return session.SUBTITLE['segments']


def ids(segments):
    return [segment.get('id') for segment in segments]


print('ensure_ids')
segments = [{'start': 0, 'end': 1, 'text': 'a', 'id': 'keep0000keep0000'},
            {'start': 1, 'end': 2, 'text': 'b'},
            {'start': 2, 'end': 3, 'text': 'c', 'id': 'keep0000keep0000'},
            {'start': 3, 'end': 4, 'text': 'd', 'id': 7}]
cue_ids.ensure_ids(segments)
check('an ID is kept', segments[0]['id'], 'keep0000keep0000')
check('every subtitle has one', all(isinstance(s, str) and len(s) == 16 for s in ids(segments)), True)
check('a copied ID is replaced on the later subtitle', segments[2]['id'] != 'keep0000keep0000', True)
check('a number (Whisper numbering) is replaced', isinstance(segments[3]['id'], str), True)
check('all different', len(set(ids(segments))), 4)
check('find', cue_ids.find(segments, segments[1]['id']) is segments[1], True)
check('find nothing', cue_ids.find(segments, 'missing'), None)

print('editing')
segments = fresh()
subtitles.add_subtitle(position=1.0, duration=4.0, text='one two')
first = segments[0]
check('a new subtitle has an ID', bool(first.get('id')), True)
first_id = first['id']
subtitles.slice_subtitle(selected_subtitle=first, position=3.0, last_text='one', next_text='two')
check('a split keeps the first half', segments[0]['id'], first_id)
check('and gives the second half its own', bool(segments[1].get('id')) and segments[1]['id'] != first_id, True)
second_id = segments[1]['id']
subtitles.merge_next_subtitle(selected_subtitle=segments[0])
check('a merge keeps the first', ids(segments), [first_id])

segments = fresh({'start': 0, 'end': 1, 'text': 'a'})
segments.append({'start': 2, 'end': 3, 'text': 'b'})
session.set_unsaved()
check('an edit leaves every subtitle with an ID', all(ids(segments)), True)

print('undo')
segments = fresh({'start': 0, 'end': 1, 'text': 'a'}, {'start': 2, 'end': 3, 'text': 'b'})
cue_ids.ensure_ids(segments)
before = ids(segments)
session.SUBTITLE['selected'] = segments[1]
subtitles.add_subtitle(position=10.0, text='c')
history.history_undo()
check('undo brings the same IDs back', ids(session.SUBTITLE['segments']), before)
check('undo keeps the selection by ID', session.SUBTITLE['selected']['id'], before[1])
history.history_redo()
check('redo too', session.SUBTITLE['selected']['id'], before[1])

print('USF')
segments = [{'id': 'aaaa0000aaaa0000', 'start': 1.0, 'end': 2.0, 'text': 'Hi', 'translations': {'pt': 'Oi'}},
            {'id': 'bbbb0000bbbb0000', 'start': 3.0, 'end': 4.0, 'text': 'Bye'}]
written = usf.USFWriter().write(segments, language='en')
check('the namespace is declared', 'xmlns:subtitldcc="https://subtitld.cc/ns/usf/1"' in written, True)
check('only the language\'s own block carries IDs', written.count('subtitldcc:id='), 2)
read = usf.USFReader().read(written)
check('read back', ids(read), ['aaaa0000aaaa0000', 'bbbb0000bbbb0000'])
check('no IDs, no namespace', 'subtitldcc' in usf.USFWriter().write([{'start': 1.0, 'end': 2.0, 'text': 'x'}]), False)

folder = tempfile.mkdtemp()
path = os.path.join(folder, 'project.usf')
with open(path, 'w', encoding='utf-8') as handle:
    handle.write(written)
check('opening a USF keeps them', ids(file_io.process_subtitles_file(path, 'USF')[0]), ['aaaa0000aaaa0000', 'bbbb0000bbbb0000'])
path = os.path.join(folder, 'old.srt')
with open(path, 'w', encoding='utf-8') as handle:
    handle.write('1\n00:00:01,000 --> 00:00:02,000\nHi\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n')
opened = file_io.process_subtitles_file(path, 'SRT')[0]
check('opening a file without IDs gives them', len(set(ids(opened))) == 2 and all(ids(opened)), True)

print('plain-text panel')
segments = [{'id': 'aaaa0000aaaa0000', 'start': 1.0, 'end': 2.0, 'text': 'Hi', 'speaker': 'A'},
            {'id': 'bbbb0000bbbb0000', 'start': 3.0, 'end': 4.0, 'text': 'Bye', 'speaker': 'A'}]
for fmt in plaintext.FORMATS:
    check(f'{fmt}: not written', 'aaaa0000' in plaintext.serialize(segments, fmt), False)
    text = plaintext.serialize(segments, fmt).replace('Bye', 'Goodbye')
    plaintext.apply_cues(segments, plaintext.parse(text, fmt).cues)
    check(f'{fmt}: an edit keeps them', ids(segments), ['aaaa0000aaaa0000', 'bbbb0000bbbb0000'])
text = '# id: forged\n' + plaintext.serialize(segments, plaintext.SRT).replace('Hi', 'Hello')
plaintext.apply_cues(segments, plaintext.parse(text, plaintext.SRT).cues)
check('srt: a typed "# id" is ignored', ids(segments), ['aaaa0000aaaa0000', 'bbbb0000bbbb0000'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
