"""Direct links (modules.rtc, libdatachannel through ctypes): two connections in this process, set up through
their own callbacks, carry text and 32 MB intact, and close cleanly. Skipped where there is no libdatachannel.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import faulthandler, hashlib, os, sys, threading, time
from pathlib import Path
# A link that never settles shows where every thread is waiting, instead of hanging the suite.
faulthandler.dump_traceback_later(50, exit=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import rtc

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


if not rtc.available():
    print('libdatachannel is not available here: skipped (packaging/libdatachannel/build-linux.sh builds it)')
    sys.exit(0)

print('a link')
offerer, answerer = rtc.PeerConnection(), rtc.PeerConnection()
offerer.on_description = lambda sdp, kind: answerer.set_remote_description(sdp, kind)
answerer.on_description = lambda sdp, kind: offerer.set_remote_description(sdp, kind)
offerer.on_candidate = lambda cand, mid: answerer.add_remote_candidate(cand, mid)
answerer.on_candidate = lambda cand, mid: offerer.add_remote_candidate(cand, mid)

received = {'text': [], 'bytes': 0, 'hash': hashlib.sha256()}
got_all = threading.Event()
remote = {}


def channel_arrived(channel):
    remote['channel'] = channel

    def message(data):
        if isinstance(data, str):
            received['text'].append(data)
            if data == 'end':
                got_all.set()
        else:
            received['bytes'] += len(data)
            received['hash'].update(data)

    channel.on_message = message


answerer.on_channel = channel_arrived
local = offerer.create_channel('media')
check('it opens', local.opened.wait(10), True)
check('the state says so', offerer.state, rtc.CONNECTED)

print('what it carries')
local.send_text('hello')
blob = os.urandom(32 * 1024 * 1024)
started = time.time()
for offset in range(0, len(blob), 64 * 1024):
    if not local.send(blob[offset:offset + 64 * 1024]):
        break
local.send_text('end')
check('everything arrives', got_all.wait(60), True)
elapsed = time.time() - started
print(f'  32 MB in {elapsed:.2f} s ({32 / elapsed:.0f} MB/s, one process)')
check('text as text, in order', received['text'], ['hello', 'end'])
check('the bytes intact', (received['bytes'], received['hash'].hexdigest()), (len(blob), hashlib.sha256(blob).hexdigest()))

print('closing')
closed = remote['channel'].closed
offerer.close()
check('the other side sees it close', closed.wait(10), True)
answerer.close()
check('nothing left registered', (rtc._peers, rtc._channels), ({}, {}))

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
