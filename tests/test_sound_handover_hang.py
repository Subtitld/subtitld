"""Opening a project whose sound is in the cache hands the video's sound to
the engine at once: the player lets go of its audio output a moment after it
started loading the video. The output was taken off with setAudioOutput(None),
from Python, holding the GIL; the player's audio thread, letting go of the
same output (a Python object), took a lock that call waits on and then waited
for the GIL. Each waited on the other, and Subtitld hung, now and then, on
double-clicking a recent project.

The open and the handover, over and over, in a child process: a hang holds
the GIL, so only another process can tell. Then that the player is left
without its output, and nothing calls setAudioOutput from Python.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

ROUNDS = 40
TIMEOUT = 150


def child(clip):
    """Open `clip` and hand its sound over, ROUNDS times; print each round."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication([])  # noqa: F841
    from subtitld.modules import session
    from subtitld.modules.segment import Subtitle
    from subtitld.interface import preview_panel
    session.SUBTITLE = Subtitle({'segments': [], 'selected': None, 'position': 0.0})

    def run(seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    for round_ in range(ROUNDS):
        player = preview_panel.PlayerWidget()
        player.resize(320, 180)
        player.show()
        player.loadfile(clip)
        # As the open does: the handover comes while the player is still
        # setting the video up.
        run(0.05 + 0.25 * (round_ % 5) / 4)
        player.hand_sound_to_engine()
        player.seek(20.0)
        run(0.4)
        print('round', round_, 'output', player._media_player.audioOutput(), flush=True)
        player._media_player.stop()
        if player._subtitle_renderer is not None:
            player._subtitle_renderer.close()
        player.deleteLater()
        run(0.05)
    # Leave at once: the players' threads are no business of this test.
    os._exit(0)


if len(sys.argv) > 2 and sys.argv[1] == '--child':
    child(sys.argv[2])

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


ffmpeg = shutil.which('ffmpeg')
if not ffmpeg:
    print('  [SKIP] FFmpeg is not installed: no clip to open')
    print()
    print('ALL PASS')
    sys.exit(0)

clip = Path(tempfile.mkdtemp()) / 'clip.mkv'
subprocess.run([ffmpeg, '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=60',
                '-f', 'lavfi', '-i', 'sine=d=60', '-c:v', 'mpeg4', '-g', '60', '-q:v', '5', '-c:a', 'aac',
                '-shortest', str(clip)], check=True)

print(f'opened and handed over, {ROUNDS} times')
started = time.monotonic()
try:
    done = subprocess.run([sys.executable, __file__, '--child', str(clip)], capture_output=True, text=True,
                          timeout=TIMEOUT)
    out, hung = done.stdout, False
except subprocess.TimeoutExpired as exc:
    out, hung = (exc.stdout or b'').decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ''), True
rounds = [line for line in out.splitlines() if line.startswith('round ')]
check('no hang', hung, False)
check('every round', len(rounds), ROUNDS)
check('the player without its output after each', [line.split(' output ')[1] for line in rounds],
      ['None'] * len(rounds))
print(f'  ({time.monotonic() - started:.0f} s)')

print('the output is let go of without setAudioOutput')
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session
from subtitld.modules.segment import Subtitle
from subtitld.interface import preview_panel
session.SUBTITLE = Subtitle({'segments': [], 'selected': None, 'position': 0.0})
player = preview_panel.PlayerWidget()
calls = []
player._media_player.setAudioOutput = lambda output: calls.append(output)
player.loadfile(str(clip))
player.hand_sound_to_engine()
loop = QEventLoop()
QTimer.singleShot(300, loop.quit)
loop.exec()
check('no setAudioOutput from Python', calls, [])
check('the player without its output', player._media_player.audioOutput(), None)
player._media_player.stop()
if player._subtitle_renderer is not None:
    player._subtitle_renderer.close()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.stdout.flush()
os._exit(1 if fails else 0)
