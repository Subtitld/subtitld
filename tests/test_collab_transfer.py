"""Media from collaborator to collaborator (modules.collab_transfer), over real direct links (libdatachannel)
between managers in this process, their set-up messages passed straight from one to the other: a file comes
whole and checked, from two collaborators at once, compressed when that helps; a collaborator that does not
share refuses; a download picks up where it stopped. Skipped where there is no libdatachannel.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import faulthandler, hashlib, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
faulthandler.dump_traceback_later(170, exit=True)
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from subtitld.modules import collab_media, collab_transfer, rtc

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


if not rtc.available():
    print('libdatachannel is not available here: skipped (packaging/libdatachannel/build-linux.sh builds it)')
    sys.exit(0)

folder = tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))
managers, sharing = {}, {}


def manager(name, files=()):
    """A collaborator: shares `files` ({hash: path}) when sharing[name] is on."""
    sharing[name] = True
    offered = dict(files)

    def shareable(file_hash):
        return offered.get(file_hash) if sharing[name] else None

    def holders(file_hash):
        return [other for other, (_m, owned) in managers.items() if other != name and file_hash in owned]

    made = collab_transfer.Manager(lambda peer, payload: managers[peer][0].on_rtc(name, payload), shareable, holders)
    managers[name] = (made, offered)
    return made


def wait(condition, seconds=60, tick=None):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        if tick is not None:
            tick()
        time.sleep(0.02)
    return False


def a_file(name, size, compressible=False):
    path = os.path.join(folder, name)
    with open(path, 'wb') as handle:
        handle.write((b'subtitld ' * (size // 9 + 1))[:size] if compressible else os.urandom(size))
    return path, dict(collab_media.describe(path), kind='waveform' if compressible else 'video')


def same(a, b):
    with open(a, 'rb') as one, open(b, 'rb') as two:
        return hashlib.sha256(one.read()).digest() == hashlib.sha256(two.read()).digest()


video, video_media = a_file('original.mkv', 20 * 1024 * 1024 + 4321)
peaks, peaks_media = a_file('waveform.npy', 9 * 1024 * 1024, compressible=True)
bob = manager('bob', {video_media['hash']: video, peaks_media['hash']: peaks})
ana = manager('ana')
done = []
ana.bridge.finished.connect(lambda file_hash, path: done.append((file_hash, path)))

print('a file from one collaborator')
started = time.time()
target = os.path.join(folder, 'received', 'original.mkv')
download = ana.download(video_media, target)
check('it comes', wait(lambda: done, tick=ana.tick), True)
print(f'  20 MB in {time.time() - started:.1f} s, set-up included')
check('whole and checked, where it was asked to go', (done[0], same(video, target)), ((video_media['hash'], target), True))
check('no leftovers', (os.path.exists(target + '.part'), os.path.exists(target + '.part.json')), (False, False))

print('compressed when that helps')
sent_before = bob.bytes_sent
peaks_target = os.path.join(folder, 'received', 'waveform.npy')
ana.download(peaks_media, peaks_target)
check('it comes', wait(lambda: len(done) == 2, tick=ana.tick), True)
check('intact', same(peaks, peaks_target), True)
check('in far fewer bytes than its size', bob.bytes_sent - sent_before < peaks_media['size'] // 10, True)

print('from two collaborators at once')
cai = manager('cai', {video_media['hash']: video})
dan = manager('dan')
dan_done = []
dan.bridge.finished.connect(lambda file_hash, path: dan_done.append(path))
bob_before, cai_before = bob.bytes_sent, cai.bytes_sent
dan_target = os.path.join(folder, 'dan', 'original.mkv')
dan.download(video_media, dan_target)
check('it comes', wait(lambda: dan_done, tick=dan.tick), True)
check('intact', same(video, dan_target), True)
check('both sent part of it', (bob.bytes_sent > bob_before, cai.bytes_sent > cai_before), (True, True))

print('someone who does not share')
sharing['bob'] = sharing['cai'] = False
eve = manager('eve')
eve_target = os.path.join(folder, 'eve', 'original.mkv')
refused = eve.download(video_media, eve_target)
wait(lambda: False, seconds=4, tick=eve.tick)
check('nothing comes', (refused.done, os.path.exists(eve_target)), (set(), False))

print('picking up where it stopped')
sharing['bob'] = True
fay = manager('fay')
fay_done = []
fay.bridge.finished.connect(lambda file_hash, path: fay_done.append(path))
fay_target = os.path.join(folder, 'fay', 'original.mkv')
first = fay.download(video_media, fay_target)
wait(lambda: len(first.done) >= 2, tick=fay.tick)
fay.cancel(video_media['hash'])
fay.close()
kept = len(first.done)
first.save_progress()
fay2 = manager('fay2')
fay2.bridge.finished.connect(lambda file_hash, path: fay_done.append(path))
again = fay2.download(video_media, fay_target)
check('the chunks already in are kept', len(again.done) >= kept > 0, True)
check('and the rest comes', wait(lambda: fay_done, tick=fay2.tick), True)
check('intact', same(video, fay_target), True)

for made, _owned in managers.values():
    made.close()
print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
