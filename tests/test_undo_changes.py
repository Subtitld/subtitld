"""Change-based undo (modules.history): undoing a step changes only what it
changed, so edits made since by anything else stay; subtitles keep their
dicts; a subtitle or speaker brought back gets its runtime data back; a
change whose subtitle is gone is skipped; steps hold changes, not copies of
the document.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import history, session, subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def project(*texts):
    session.SUBTITLE = {'segments': [{'start': float(i * 2), 'end': i * 2 + 1.0, 'text': text, 'speaker': 'A'}
                                     for i, text in enumerate(texts)], 'language': 'en'}
    session.SPEAKERS.clear()
    session.SPEAKERS['A'] = {'color': '#ff0000'}
    history.history_clear()
    return session.SUBTITLE['segments']


def texts():
    return [s['text'] for s in session.SUBTITLE['segments']]


def edit(fn):
    history.history_append()
    fn()
    session.set_unsaved()


def elsewhere(fn):
    """A change that is not the user's to undo: in a shared project, a
    collaborator's."""
    with history.amending(None):
        fn()
    session.set_unsaved()


print('undo changes only what the step changed')
one, two = project('one', 'two')
edit(lambda: one.update(text='ONE'))
elsewhere(lambda: two.update(text='TWO'))
history.history_undo()
check('the step is undone, the other edit stays', texts(), ['one', 'TWO'])
check('the subtitle is still the same dict', session.SUBTITLE['segments'][0] is one, True)
history.history_redo()
check('redo', texts(), ['ONE', 'TWO'])

print('a change whose subtitle is gone is skipped')
one, two = project('one', 'two')
edit(lambda: one.update(text='ONE'))
edit(lambda: two.update(text='TWO'))
elsewhere(lambda: session.SUBTITLE['segments'].remove(one))
check('undo still happens', history.history_undo(), True)
check('the other step undoes', history.history_undo() and texts(), ['two'])

print('a collaborator\'s change is not undone here')
one, two = project('one', 'two')
edit(lambda: one.update(text='mine'))
edit(lambda: two.update(text='mine too'))
theirs = [{'op': 'cue.set', 'id': one['id'], 'field': 'text', 'value': 'theirs'}]
history.forget(theirs)
elsewhere(lambda: one.update(text='theirs'))
history.history_undo()
history.history_undo()
check('undo leaves what they replaced, and undoes the rest', texts(), ['theirs', 'two'])
history.history_redo()
history.history_redo()
check('redo too', texts(), ['theirs', 'mine too'])

print('runtime data comes back')
one, two = project('one', 'two')
one['_rec'] = 'take-1'
session.SPEAKERS['B'] = {'color': '#00ff00', 'image': 'a picture', 'image_bytes': b'png'}
session.set_unsaved()
edit(lambda: session.SUBTITLE['segments'].remove(one))
edit(lambda: session.SPEAKERS.pop('B'))
history.history_undo()
history.history_undo()
back = session.SUBTITLE['segments'][0]
check('a subtitle brought back has its runtime keys', (back['text'], back.get('_rec')), ('one', 'take-1'))
check('a speaker brought back has its picture',
      (session.SPEAKERS['B'].get('image'), session.SPEAKERS['B'].get('image_bytes')), ('a picture', b'png'))

one, two = project('one', 'two')
session.SPEAKERS['A']['image'] = 'old picture'
edit(lambda: session.SPEAKERS['A'].update(image='new picture'))
history.history_undo()
check('undo puts a speaker\'s picture back', session.SPEAKERS['A']['image'], 'old picture')
history.history_redo()
check('redo the new one', session.SPEAKERS['A']['image'], 'new picture')

print('dubs keep their files through undo')
from subtitld.modules import collab_media  # noqa: E402 (the stamping it adds runs on every change)
dub_folder = tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))
take_one, take_two = os.path.join(dub_folder, 'one.wav'), os.path.join(dub_folder, 'two.wav')
for path, data in ((take_one, b'RIFF first take'), (take_two, b'RIFF second take')):
    with open(path, 'wb') as handle:
        handle.write(data)
one, two = project('one', 'two')
one['dubbing'] = [{'uid': 'u1', 'path': take_one, 'start': 0.0, 'segments': [{'path': take_one, 'start': 0.0}]}]
session.document_loaded()
check('each dub file named by its content', bool(one['dubbing'][0].get('hash')) and bool(one['dubbing'][0]['segments'][0].get('hash')), True)
edit(lambda: one.update(dubbing=[{'uid': 'u2', 'path': take_two, 'start': 0.0}]))
history.history_undo()
check('undoing a new take brings the old one back, with its file here', session.SUBTITLE['segments'][0]['dubbing'][0].get('path'), take_one)
check('and its subclip\'s', session.SUBTITLE['segments'][0]['dubbing'][0]['segments'][0].get('path'), take_one)
history.history_redo()
check('redo, the new one', session.SUBTITLE['segments'][0]['dubbing'][0].get('path'), take_two)
edit(lambda: session.SUBTITLE['segments'].remove(session.SUBTITLE['segments'][0]))
history.history_undo()
check('a deleted subtitle comes back with its dub\'s file', session.SUBTITLE['segments'][0]['dubbing'][0].get('path'), take_two)

print('selection')
one, two = project('one', 'two')
session.SUBTITLE['selected'] = two
edit(lambda: subtitles.remove_subtitle(two))
session.SUBTITLE['selected'] = one
history.history_undo()
check('undo selects what was selected when the step began', session.SUBTITLE['selected']['text'], 'two')
history.history_redo()
check('redo what was selected when it was undone', session.SUBTITLE['selected']['text'], 'one')

print('steps hold changes, not the document')
segments = project(*[f'line {i}' for i in range(5000)])
started = time.perf_counter()
edit(lambda: segments[2500].update(text='changed'))
took_edit = (time.perf_counter() - started) * 1000
check('one edit is one change', sum(len(batch) for batch in history.ALL_HISTORY[-1].undo), 1)
started = time.perf_counter()
history.history_undo()
took_undo = (time.perf_counter() - started) * 1000
print(f'  among 5000 subtitles: edit {took_edit:.1f} ms, undo {took_undo:.1f} ms')
check('undone', segments[2500]['text'], 'line 2500')
check('both quick', took_edit < 100 and took_undo < 100, True)

print('bounded')
one, = project('x')
for i in range(history.MAX_HISTORY + 20):
    edit(lambda i=i: one.update(text=str(i)))
check('at most MAX_HISTORY steps', len(history.ALL_HISTORY), history.MAX_HISTORY)
while history.history_undo():
    pass
check('undoing them all goes back as far as they reach', texts(), ['19'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
