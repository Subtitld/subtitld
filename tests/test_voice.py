"""The editors' voice chat without audio devices: Opus through ctypes (modules.opus), the voice packets and
the jitter buffer (modules.voice): order, a lost frame rebuilt from the next one, concealment, quiet when the
talker stops, late packets, and sequence numbers wrapping. Skipped where there is no libopus.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import opus, voice

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


if not opus.available():
    print('libopus is not available here: skipped')
    sys.exit(0)

print('Opus')
encoder, decoder = opus.Encoder(), opus.Decoder()
tone = [0.4 * math.sin(2 * math.pi * 220 * i / opus.RATE) for i in range(opus.FRAME)]
packets = [encoder.encode(tone) for _ in range(10)]
check('a 20 ms frame of voice is a small packet', all(0 < len(p) < 200 for p in packets), True)
decoded = [decoder.decode(p) for p in packets]
check('and comes back as 20 ms', {len(f) for f in decoded}, {opus.FRAME})
energy = sum(x * x for x in decoded[-1]) / opus.FRAME
check('with the sound in it', 0.03 < energy < 0.15, True)
silence = [len(encoder.encode([0.0] * opus.FRAME)) for _ in range(30)]
check('silence, once Opus is sure of it, costs almost nothing (DTX)', max(silence[-10:]) <= 3, True)
check('a lost frame is concealed', len(decoder.decode(None)), opus.FRAME)

print('voice packets')
check('a sequence number and the Opus frame', voice.unpack(voice.packet(70000, b'abc')), (70000 & 0xFFFF, b'abc'))
check('not a voice packet', (voice.unpack(b'x'), voice.unpack(bytes([9, 0, 1]) + b'abc')), (None, None))


class Recorder:
    """A decoder that says what it was asked to do."""

    def decode(self, packet):
        return ['conceal'] if packet is None else [packet.decode()]

    def recover(self, packet):
        return ['rebuilt from ' + packet.decode()]


def played(buffer, count):
    return [(frame or [None])[0] for frame in (buffer.pop() for _ in range(count))]


print('the jitter buffer')
buffer = voice.JitterBuffer(Recorder())
for seq in (2, 0, 1):
    buffer.push(seq, f'f{seq}'.encode())
check('waits for 60 ms of frames, then plays them in order', played(buffer, 3), ['f0', 'f1', 'f2'])
for seq in (3, 5, 6):
    buffer.push(seq, f'f{seq}'.encode())
check('a lost frame comes back from the next one', played(buffer, 4), ['f3', 'rebuilt from f5', 'f5', 'f6'])
buffer.push(9, b'f9')
check('two lost in a row: the first concealed, the second rebuilt from the next',
      played(buffer, 4), ['conceal', 'rebuilt from f9', 'f9', 'conceal'])
check('a talker who stopped: quiet', played(buffer, 3), [None, None, None])
buffer.push(3, b'old')
check('the next talk spurt starts afresh', buffer.next, None)
buffer = voice.JitterBuffer(Recorder())
for seq in (65534, 65535, 0, 1):
    buffer.push(seq, f'f{seq}'.encode())
check('sequence numbers wrap', played(buffer, 4), ['f65534', 'f65535', 'f0', 'f1'])
buffer.push(65535, b'late')
check('a frame that comes after its turn is dropped', (buffer.stats['late'], 65535 in buffer.held), (1, False))
buffer = voice.JitterBuffer(Recorder())
for seq in range(voice.MAX_HELD + 5):
    buffer.push(seq, f'f{seq}'.encode())
check('a burst beyond 300 ms drops the oldest', (len(buffer.held), buffer.stats['dropped'], played(buffer, 1)),
      (voice.MAX_HELD, 5, ['f5']))

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
