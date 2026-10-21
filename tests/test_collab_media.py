"""A shared project's media files (modules.collab_media): a file is known by the SHA-256 of its bytes with one
for each 4 MiB chunk; the index remembers descriptions by path, size and modification time, so a large video
is hashed once; the project's files are its video, waveform and separated audio; a received waveform or stem
goes where this app looks for the video open here. And the sync engine keeps who can send what.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import hashlib, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import collab_media, session, utils
from subtitld.modules.collab_sync import Engine, Live

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = tempfile.mkdtemp()
video = os.path.join(folder, 'Sintel.2010.mkv')
data = os.urandom(9 * 1024 * 1024 + 123)
with open(video, 'wb') as handle:
    handle.write(data)

print('a file, described')
description = collab_media.describe(video)
check('the hash of its bytes', description['hash'], hashlib.sha256(data).hexdigest())
check('and of each 4 MiB chunk', description['chunks'],
      [hashlib.sha256(data[i:i + collab_media.CHUNK_SIZE]).hexdigest() for i in range(0, len(data), collab_media.CHUNK_SIZE)])
check('its size', (description['size'], description['chunk_size']), (len(data), 4 * 1024 * 1024))
check('cancelled, nothing', collab_media.describe(video, cancelled=lambda: True), None)

print('the index')
index = collab_media.Index(os.path.join(folder, 'index.json'))
check('nothing known yet', index.known(video), None)
index.remember(video, description)
check('known once described', index.known(video)['hash'], description['hash'])
check('found by hash', index.path_for(description['hash']), os.path.abspath(video))
check('kept', collab_media.Index(os.path.join(folder, 'index.json')).known(video)['hash'], description['hash'])
time.sleep(0.01)
os.utime(video, (time.time() + 5, time.time() + 5))
check('a file that changed is not', (index.known(video), index.path_for(description['hash'])), (None, None))

print('the project\'s files')
session.VIDEO = {'filepath': video}
key = utils.get_cache_key(video)
waveform = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{key}_waveform.npy')
os.makedirs(os.path.dirname(waveform), exist_ok=True)
with open(waveform, 'wb') as handle:
    handle.write(b'peaks')
vocals = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{key}_vocals.flac')
with open(vocals, 'wb') as handle:
    handle.write(b'fLaC')
check('the video, its waveform and its separated audio',
      [(f['kind'], f['name']) for f in collab_media.project_files()],
      [('video', 'Sintel.2010.mkv'), ('waveform', 'waveform.npy'), ('stem', 'vocals.flac')])
check('received ones go where this app looks for them',
      (collab_media.destination('waveform', 'waveform.npy'), collab_media.destination('stem', 'vocals.flac'),
       collab_media.destination('stem', '../../etc.flac'), collab_media.destination('video', 'x.mkv')),
      (waveform, vocals, None, None))

print('scanning: each file hashed once')
collab_media._index = collab_media.Index(os.path.join(folder, 'scan.json'))
hashed = []
real_describe = collab_media.describe
collab_media.describe = lambda path, *a, **k: hashed.append(os.path.basename(path)) or real_describe(path, *a, **k)
first = collab_media.scan()
second = collab_media.scan()
collab_media.describe = real_describe
check('the first time, every file', sorted(hashed), sorted(['Sintel.2010.mkv', f'{key}_waveform.npy', f'{key}_vocals.flac']))
check('then from the index', [f['hash'] for f in second], [f['hash'] for f in first])
check('the video with its lookup fingerprint', bool(first[0]['fingerprint']), True)

print('who can send what')
sent = []
class Screen(Live):
    def snapshot(self):
        return {'segments': [], 'speakers': {}, 'meta': {}}

    def apply(self, ops):
        pass


engine = Engine(sent.append, Screen(), store={'doc': 'd', 'seq': 0, 'base': None, 'pending': []})
engine.have(['a' * 64])
check('nothing goes before the connection is live', sent, [])
engine.connected()
engine.on_message({'type': 'snapshot', 'seq': 0, 'state': {'segments': [], 'speakers': {}, 'meta': {}}})
check('then it does', sent[-1], {'type': 'have', 'hashes': ['a' * 64]})
engine.on_message({'type': 'have', 'peer': 'p1', 'user': {'handle': 'bob'}, 'hashes': ['b' * 64]})
engine.on_message({'type': 'have', 'peer': 'p2', 'user': {'handle': 'cai'}, 'hashes': ['b' * 64, 'c' * 64]})
check('who has a file', (engine.holders('b' * 64), engine.holders('c' * 64), engine.holders('z' * 64)), (['p1', 'p2'], ['p2'], []))
engine.on_message({'type': 'media', 'media': {'hash': 'c' * 64, 'kind': 'stem', 'name': 'vocals.flac'}})
check('a file added to the list', engine.media['c' * 64]['name'], 'vocals.flac')
engine.on_message({'type': 'left', 'peer': 'p2'})
check('someone leaving takes theirs along', engine.holders('c' * 64), [])
engine.rtc('p1', {'offer': 'sdp'})
check('set-up for a direct link goes to one peer', sent[-1], {'type': 'rtc', 'to': 'p1', 'payload': {'offer': 'sdp'}})

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
