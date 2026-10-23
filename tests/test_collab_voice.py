"""The editors' voice call (modules.collab_voice) between two calls in this process, over real links: a link
of its own per collaborator, push-to-talk (nothing goes until talking), what comes in mixed and played, who
is speaking, leaving. Audio devices are stand-ins: frames are fed and played by the test.
Skipped where there is no libdatachannel or libopus.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import faulthandler, math, queue, sys, threading, time
from pathlib import Path
faulthandler.dump_traceback_later(80, exit=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import collab_voice, opus, rtc

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait(condition, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


if not (rtc.available() and opus.available()):
    print('libdatachannel or libopus is not available here: skipped')
    sys.exit(0)

# Set-up messages go through subtitld.cc in the app; here a thread of their own carries them, so nothing calls
# libdatachannel from inside its callbacks.
mail = queue.Queue()
calls = {}


def carrier():
    while True:
        to, frm, payload = mail.get()
        if to is None:
            return
        calls[to].on_rtc(frm, payload)


threading.Thread(target=carrier, daemon=True).start()
calls['a'] = collab_voice.Call(lambda peer, payload: mail.put((peer, 'a', payload)), audio='test')
calls['b'] = collab_voice.Call(lambda peer, payload: mail.put((peer, 'b', payload)), audio='test')
ana, bob = calls['a'], calls['b']

print('joining')
ana.start()
bob.start()
ana.sync('a', {'b'})
bob.sync('b', {'a'})
check('a link each, the first ID offering', (sorted(ana.links), sorted(bob.links)), (['b'], ['a']))
check('it opens', wait(lambda: bob.links['a'].channel is not None and bob.links['a'].channel.opened.is_set()
                       and ana.links['b'].channel.opened.is_set()), True)

tone = [0.4 * math.sin(2 * math.pi * 220 * i / opus.RATE) for i in range(opus.FRAME)]


def talk_and_listen(talker, listener, frames=40):
    """Feed `frames` blocks to the talker's microphone at real pace; what the listener plays meanwhile."""
    played = []
    started = time.perf_counter()
    for i in range(frames):
        talker.captured(tone)
        played.append(listener.playback())
        time.sleep(max(0, started + (i + 1) * 0.02 - time.perf_counter()))
    for _ in range(10):
        played.append(listener.playback())
        time.sleep(0.02)
    return played


def energy(blocks):
    return sum(float((block * block).sum()) for block in blocks) / max(1, len(blocks) * opus.FRAME)


print('push-to-talk')
check('nothing goes while not talking', energy(talk_and_listen(ana, bob, 20)), 0.0)
ana.held = True
heard = talk_and_listen(ana, bob)
check('held: Bob hears Ana', energy(heard) > 0.02, True)
check('and sees who speaks', wait(lambda: 'a' in bob.speaking(), 1) or 'a' in bob.speaking(), True)
ana.held = False
check('let go: quiet again', wait(lambda: not bob.speaking(), 2), True)
bob.latched = True
check('latched on: Ana hears Bob without holding', energy(talk_and_listen(bob, ana)) > 0.02, True)
bob.latched = False

print('leaving')
bob.stop()
ana.sync('a', set())
check('no links left', (ana.links, bob.links), ({}, {}))
check('not talking once out of the call', (bob.talking, ana.on), (False, True))
ana.stop()
time.sleep(0.5)

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
