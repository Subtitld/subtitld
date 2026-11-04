"""A video just opened shows, paused, the frame under the playhead, and plays
on from there. A moment after it opens, the sound engine takes the video's
sound over and the player drops its audio output. Qt then builds the player's
pipeline anew, and a demuxer made anew at 0 does not seek: it reads on from
where the old one had stopped, seconds ahead. The picture came from the next
keyframe (5 s into this clip) instead of the first frame.

A real clip (FFmpeg lavfi, keyframes 5 s apart) in the real preview; no
sound device for the engine.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
app = QApplication([])
from subtitld.modules import audioengine, session
from subtitld.interface import preview_panel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait(seconds, until=lambda: False):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not until():
        app.processEvents()
        time.sleep(0.01)


ffmpeg = shutil.which('ffmpeg')
if not ffmpeg:
    print('  [SKIP] FFmpeg is not installed: no clip to open')
    print()
    print('ALL PASS')
    sys.exit(0)


def no_device(**_kwargs):
    raise audioengine.sd.PortAudioError('no device in the test')


audioengine.sd.OutputStream = no_device
session.SUBTITLE = {'segments': [], 'selected': None, 'position': 0.0}

clip = Path(tempfile.mkdtemp()) / 'clip.mkv'
subprocess.run([ffmpeg, '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30:d=12',
                '-f', 'lavfi', '-i', 'sine=d=12', '-c:v', 'mpeg4', '-g', '150', '-q:v', '5', '-c:a', 'aac',
                '-shortest', str(clip)], check=True)

player = preview_panel.PlayerWidget()
player.resize(320, 180)
player.show()
frames = []
player._graphics_video_item.videoSink().videoFrameChanged.connect(
    lambda frame: frame.isValid() and frames.append(round(frame.startTime() / 1e6, 3)))

print('opened')
player.loadfile(str(clip))
wait(10, lambda: frames)
# The clip's sound starts a little before its picture: its first frame is a
# little after 0.
first = frames[0] if frames else None
check('the first frame shown', first is not None and first < 0.05, True)
# The player's demuxer reads seconds ahead meanwhile.
wait(1)

print('the sound handed to the engine')
shown = len(frames)
player.hand_sound_to_engine()
wait(1.5)
check('the player without its audio output', player._media_player.audioOutput(), None)
check('the playhead where it was', player._media_player.position(), 0)
check('still the first frame', frames[-1:], [first])
check('no frame from further on', [time for time in frames[shown:] if time != first], [])

print('handed over again: nothing to do')
player.hand_sound_to_engine()
wait(0.5)
check('still the first frame', frames[-1:], [first])

print('played')
shown = len(frames)
player.play()
wait(1)
check('playing', player._media_player.playbackState(), QMediaPlayer.PlayingState)
played = frames[shown:]
check('the picture from the start, not from the next keyframe', bool(played) and first < played[0] < 0.2, True)
check('and on, up to about a second', bool(played) and 0.3 < played[-1] < 1.5, True)
player.pause()
if player._subtitle_renderer is not None:
    player._subtitle_renderer.close()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
