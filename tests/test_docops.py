"""Document changes (modules.docops) and the journal that records them
(modules.journal): each change undoes, the changes found between two states
turn one into the other, and the journal's changes, replayed elsewhere, give
the same document as the app's own edits did — undo, the plain-text panel
and speakers included. Opening a project is not an edit.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import copy, json, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import docops, history, journal, plaintext, session, subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def same_document(a, b):
    """Two documents alike, whatever the order of their subtitles."""
    def norm(document):
        return {'segments': {s['id']: s for s in document['segments']},
                'speakers': document['speakers'], 'meta': document['meta']}
    return norm(a) == norm(b)


def sample():
    return {
        'segments': [
            {'id': 'a', 'start': 1.0, 'end': 2.0, 'text': 'One', 'speaker': 'A', 'translations': {'pt': 'Um'}},
            {'id': 'b', 'start': 3.0, 'end': 4.0, 'text': 'Two', 'speaker': 'B', 'dubbing': [{'uid': 'x', 'start': 3.0}]},
        ],
        'speakers': {'A': {'color': '#ff0000'}, 'B': {'color': '#00ff00', 'dubbing': {'voice': 'v1'}}},
        'meta': {'language': 'en', 'translations': {'pt': {}}},
    }


print('each change undoes')
CHANGES = [
    {'op': 'cue.insert', 'cue': {'id': 'c', 'start': 2.5, 'end': 2.9, 'text': 'Between'}},
    {'op': 'cue.delete', 'id': 'b'},
    {'op': 'cue.set', 'id': 'a', 'field': 'text', 'value': 'Uno'},
    {'op': 'cue.set', 'id': 'a', 'field': 'locked', 'value': True},
    {'op': 'cue.unset', 'id': 'a', 'field': 'speaker'},
    {'op': 'cue.set', 'id': 'a', 'field': 'translations.pt', 'value': 'Um!'},
    {'op': 'cue.set', 'id': 'b', 'field': 'translations.de', 'value': 'Zwei'},
    {'op': 'cue.unset', 'id': 'a', 'field': 'translations.pt'},
    {'op': 'speaker.insert', 'name': 'C', 'speaker': {'color': '#0000ff'}},
    {'op': 'speaker.delete', 'name': 'B'},
    {'op': 'speaker.set', 'name': 'A', 'field': 'dubbing', 'value': {'voice': 'v2'}},
    {'op': 'speaker.unset', 'name': 'A', 'field': 'color'},
    {'op': 'meta.set', 'field': 'language', 'value': 'pt'},
    {'op': 'meta.unset', 'field': 'translations'},
]
for change in CHANGES:
    document = sample()
    undo = docops.apply(document, [change])
    check(f'{change["op"]} {change.get("field", "")}: changes it', same_document(document, sample()), False)
    docops.apply(document, undo)
    check(f'{change["op"]} {change.get("field", "")}: and undoes', same_document(document, sample()), True)

document = sample()
undo = docops.apply(document, CHANGES[2:9])
docops.apply(document, undo)
check('several together undo together', same_document(document, sample()), True)

document = sample()
docops.apply(document, [CHANGES[0]])
check('an inserted subtitle goes in time order', [s['id'] for s in document['segments']], ['a', 'c', 'b'])
check('changes are JSON', json.loads(json.dumps(CHANGES)) == CHANGES, True)

print('changes that do not fit')
for name, change in (
        ('a subtitle twice', {'op': 'cue.insert', 'cue': {'id': 'a', 'start': 0.0}}),
        ('a subtitle without an id', {'op': 'cue.insert', 'cue': {'start': 0.0}}),
        ('a missing subtitle', {'op': 'cue.set', 'id': 'zz', 'field': 'text', 'value': ''}),
        ('a changed id', {'op': 'cue.set', 'id': 'a', 'field': 'id', 'value': 'q'}),
        ('a missing speaker', {'op': 'speaker.delete', 'name': 'Z'}),
        ('an unknown change', {'op': 'cue.paint'})):
    try:
        docops.apply(sample(), [change])
        check(f'refused: {name}', 'applied', 'refused')
    except docops.OpError:
        check(f'refused: {name}', 'refused', 'refused')

print('the changes between two states')
before = sample()
live = copy.deepcopy(before)
live['segments'][0]['text'] = 'Uno'
live['segments'][0]['translations']['pt'] = 'Um!'
live['segments'][0]['translations']['es'] = 'Uno'
live['segments'][0]['_rec'] = object()          # runtime: not the document's
del live['segments'][1]
live['segments'].append({'id': 'd', 'start': 9.0, 'end': 9.5, 'text': 'New', '_waveform': object()})
live['speakers']['A']['image'] = object()       # a picture travels as media
live['speakers']['C'] = {'color': '#000000', 'image_bytes': b'png'}
live['meta']['language'] = 'pt'
ops = docops.diff(before, live['segments'], live['speakers'], live['meta'])
check('found', sorted(op['op'] for op in ops), sorted([
    'cue.delete', 'cue.insert', 'cue.set', 'cue.set', 'cue.set', 'speaker.insert', 'meta.set']))
check('one language at a time', sorted(op['field'] for op in ops if op['op'] == 'cue.set'),
      ['text', 'translations.es', 'translations.pt'])
check('no runtime data in them', json.loads(json.dumps(ops)) == ops, True)
docops.apply(before, ops)
check('they turn one into the other', same_document(before, docops.snapshot(live['segments'], live['speakers'], live['meta'])), True)
check('and then there is nothing more', docops.diff(before, live['segments'], live['speakers'], live['meta']), [])

before = sample()
live = copy.deepcopy(before)
del live['segments'][0]['translations']
ops = docops.diff(before, live['segments'], live['speakers'], live['meta'])
check('no translations at all is not an empty set of them', ops, [{'op': 'cue.unset', 'id': 'a', 'field': 'translations'}])

print('the journal')
history.history_clear()
session.SUBTITLE = {'segments': [], 'language': 'en'}
session.SPEAKERS.clear()
session.SPEAKERS['A'] = {'color': '#ff0000'}
history.history_clear()
recorded = []
replica = docops.snapshot(session.SUBTITLE['segments'], session.SPEAKERS, session.SUBTITLE)


def on_changes(ops, _undo):
    recorded.append(ops)
    before = copy.deepcopy(replica)
    undo = docops.apply(replica, ops)
    # Each batch, undone, gives back the document before it.
    undone_document = copy.deepcopy(replica)
    docops.apply(undone_document, undo)
    undone.append(same_document(undone_document, before))


undone = []
journal.add_listener(on_changes)


def edit(action):
    action()
    session.set_unsaved()


def live_document():
    return docops.snapshot(session.SUBTITLE['segments'], session.SPEAKERS, session.SUBTITLE)


segments = session.SUBTITLE['segments']
edit(lambda: subtitles.add_subtitle(position=1.0, duration=4.0, text='one two'))
edit(lambda: subtitles.add_subtitle(position=8.0, duration=2.0, text='three'))
edit(lambda: subtitles.slice_subtitle(selected_subtitle=segments[0], position=3.0, last_text='one', next_text='two'))
edit(lambda: subtitles.move_subtitle(selected_subtitle=segments[2], amount=0.5))
edit(lambda: subtitles.change_subtitle_text(segments[1], 'TWO'))
edit(lambda: subtitles.merge_next_subtitle(selected_subtitle=segments[0]))
check('edits are recorded', len(recorded) >= 5, True)
check('the replica follows', same_document(replica, live_document()), True)

edit(lambda: segments[0].setdefault('translations', {}).update({'pt': 'um dois'}))
edit(lambda: session.SPEAKERS.update({'B': {'color': '#00ff00'}}))
edit(lambda: session.SPEAKERS['A'].update({'dubbing': {'voice': 'v1'}, 'image': object()}))
edit(lambda: segments[1].update({'speaker': 'B'}))
edit(lambda: session.SUBTITLE.update({'language': 'en-us'}))
check('translations, speakers and the language too', same_document(replica, live_document()), True)

count = len(recorded)
history.history_undo()
history.history_undo()
check('undo is recorded', len(recorded), count + 2)
history.history_redo()
check('so is redo, and the replica follows', same_document(replica, live_document()), True)

text = plaintext.serialize(session.SUBTITLE['segments'], plaintext.SRT).replace('three', 'THREE')
edit(lambda: plaintext.apply_cues(session.SUBTITLE['segments'], plaintext.parse(text, plaintext.SRT).cues))
check('the plain-text panel', same_document(replica, live_document()), True)

# An edit some code made without calling set_unsaved: the next undo step
# starts with it on record.
segments[0]['end'] += 0.25
history.history_append()
check('an edit nobody announced is caught when the next one starts', same_document(replica, live_document()), True)
check('every batch undoes', all(undone), True)
check('recorded changes are JSON', all(json.loads(json.dumps(ops)) == ops for ops in recorded), True)

print('opening a project')
count = len(recorded)
session.SUBTITLE['segments'] = [{'start': 0.0, 'end': 1.0, 'text': 'Opened'}]
session.document_loaded()
session.set_unsaved(False)
check('is not an edit', len(recorded), count)
check('and its subtitles have IDs', bool(session.SUBTITLE['segments'][0].get('id')), True)
journal.remove_listener(on_changes)

print('speed')
session.SUBTITLE['segments'] = [{'start': float(i), 'end': i + 0.5, 'text': f'Line {i}', 'speaker': 'A',
                                 'translations': {'pt': f'Linha {i}'}, 'dubbing': [{'uid': str(i), 'start': float(i)}]}
                                for i in range(5000)]
session.document_loaded()
session.SUBTITLE['segments'][2500]['text'] = 'Changed'
started = time.perf_counter()
ops = session.capture_changes()
took = (time.perf_counter() - started) * 1000
print(f'  one edit among 5000 subtitles: {took:.1f} ms')
check('found in under 50 ms', took < 50, True)

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
