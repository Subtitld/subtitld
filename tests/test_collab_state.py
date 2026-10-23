"""A shared project's co-editing state (modules.collab_state): while shared,
every recorded batch of changes is queued (undo included, the server's own
not); the queue and the shared document's ID are saved in the .usfx and come
back when it is opened; a project that is not shared saves none, and opening
another file does not take an earlier one's.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, zipfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import collab_state, docops, file_io, history, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = tempfile.mkdtemp()


def new_project(*texts):
    session.SUBTITLE = {'segments': [{'start': float(i * 2), 'end': i * 2 + 1.0, 'text': text, 'speaker': 'A'}
                                     for i, text in enumerate(texts)], 'language': 'en', 'filepath': ''}
    session.SPEAKERS.clear()
    session.SPEAKERS['A'] = {'color': '#ff0000'}
    session.FORMAT = {}
    session.VIDEO = {}
    history.history_clear()
    return session.SUBTITLE['segments']


def edit(fn):
    history.history_append()
    fn()
    session.set_unsaved()


def open_project(path):
    """As the open flows do it."""
    history.history_clear()
    session.SUBTITLE['segments'] = file_io.process_subtitles_file(path)[0]
    session.document_loaded()


def members(path):
    with zipfile.ZipFile(path) as bundle:
        return sorted(name for name in bundle.namelist() if name.startswith(('collab', 'pending')))


print('not shared')
one, two = new_project('one', 'two')
edit(lambda: one.update(text='ONE'))
check('nothing queued', collab_state.STATE['pending'], [])
plain_path = os.path.join(folder, 'plain.usfx')
file_io.save_file(plain_path, 'USFX')
check('nothing saved', members(plain_path), [])

print('shared')
one, two = new_project('one', 'two')
collab_state.start('doc123', 'https://draft.subtitld.cc', seq=7,
                   base=docops.snapshot(session.SUBTITLE['segments'], session.SPEAKERS, session.SUBTITLE))
edit(lambda: one.update(text='ONE'))
edit(lambda: two.update(text='TWO'))
history.history_undo()
with collab_state.remote():
    one['text'] = 'from the server'
    session.set_unsaved()
pending = collab_state.STATE['pending']
check('each batch queued, undo too, the server\'s not', [batch['ops'][0].get('value') for batch in pending],
      ['ONE', 'TWO', 'two'])
check('on top of the server\'s change 7', {batch['base_seq'] for batch in pending}, {7})
check('each with its own ID', len({batch['client_op_id'] for batch in pending}), 3)
collab_state.acknowledge(pending[0]['client_op_id'], 8)
check('a confirmed batch leaves the queue', (len(collab_state.STATE['pending']), collab_state.STATE['seq']), (2, 8))

print('saved and opened')
shared_path = os.path.join(folder, 'shared.usfx')
file_io.save_file(shared_path, 'USFX')
check('saved in the project', members(shared_path), ['collab-base.json', 'collab.json', 'pending.jsonl'])
saved = {key: (list(value) if isinstance(value, list) else value) for key, value in collab_state.STATE.items()}
new_project('something else')
check('another project is not shared', collab_state.shared(), False)
open_project(shared_path)
check('opening it shares it again, queue and all', collab_state.STATE, saved)

print('a save made from a snapshot')
snapshot = file_io._snapshot_for_save()
edit(lambda: session.SUBTITLE['segments'][0].update(text='after the snapshot'))
thread_path = os.path.join(folder, 'thread.usfx')
file_io.SaveFileThread(thread_path, 'USFX', 'en', snapshot=snapshot).run()
open_project(thread_path)
check('has the queue as it was then', len(collab_state.STATE['pending']), len(saved['pending']))

print('no longer shared')
collab_state.clear()
file_io.save_file(shared_path, 'USFX')
check('saves none', members(shared_path), [])
open_project(shared_path)
check('and opens unshared, whatever an earlier open left behind', collab_state.shared(), False)

print('other files')
open_project(thread_path)
srt_path = os.path.join(folder, 'other.srt')
with open(srt_path, 'w', encoding='utf-8') as handle:
    handle.write('1\n00:00:01,000 --> 00:00:02,000\nHi\n')
file_io.process_subtitles_file(srt_path)
check('importing a file into a shared project leaves it shared', collab_state.shared(), True)
open_project(srt_path)
check('opening one is not shared', collab_state.shared(), False)

print('reading')
base = b'{"segments": [], "speakers": {}, "meta": {}}'
state = collab_state.read(b'{"version": 1, "doc": "d", "seq": 3}',
                          b'{"client_op_id": "a-1", "base_seq": 3, "ops": []}\n{"client_op_id": "a-2", "ba', base)
check('a line cut short ends the queue', (state['seq'], [b['client_op_id'] for b in state['pending']]), (3, ['a-1']))
check('without its base, the server sends the whole document again',
      collab_state.read(b'{"version": 1, "doc": "d", "seq": 3}', b'', b'not json')[('seq')], 0)
check('another version is not read', collab_state.read(b'{"version": 2, "doc": "d"}'), None)
check('nor something else', collab_state.read(b'not json'), None)

collab_state.clear()
# The projects opened above start background extractors (file_io): a QThread still running when the app
# object goes aborts the process.
for thread in (session.USFX_BACKGROUND_LOAD, session.SPEAKER_IMAGE_LOAD):
    if thread is not None:
        thread.wait(10000)
print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
