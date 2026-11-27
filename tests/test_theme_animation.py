"""An animated theme over time (modules.themes): its intro and outro read
from the manifest, a cue shorter than both caught, the timeline's zones
for them, the moments a cue is drawn at for a file and for a video, and a
burned video that shows the intro, the cue settled and the outro.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import burned_video, config, session, themes
from subtitld.modules.segment import Segment, Subtitle
from subtitld.interface import subtitle_pictures
from subtitld.interface.timeline import ANIMATION_SHORT, _paint_animation_zones

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.SUBTITLE = Subtitle(segments=[])
session.VIDEO = {'framerate': 25}
title = Segment({'kind': 'title', 'start': 2, 'end': 6, 'text': 'Maria Souza', 'title_theme': 'subtitld.lower_third',
                 'title_slots': {'role': 'Field reporter'}})
plain = Segment({'kind': 'title', 'start': 2, 'end': 6, 'text': 'Plain', 'title_theme': 'subtitld.plain'})
subtitle = Segment({'start': 1, 'end': 2, 'text': 'Hello'})
mark = Segment({'kind': 'mark', 'start': 1, 'end': 1, 'text': 'Chapter'})

print('timing')
check('Lower third: intro and outro', themes.animation(title), (0.7, 0.4))
check('Plain: not animated', themes.animation(plain), None)
check('Classic: not animated', themes.animation(subtitle), None)
check('a mark: none', themes.animation(mark), None)
check('long enough', themes.too_short(title), None)
short = Segment(dict(title, end=2.9))
check('shorter than both: how long they take', themes.too_short(short), 1.1)
check('just long enough', themes.too_short(Segment(dict(title, end=3.1))), None)

print('the moments drawn')
check('a still cue: one, the whole cue', subtitle_pictures.moments(subtitle, 25), [(1.0, 1.0, 2.0)])
check('for a file: once, settled after the intro', subtitle_pictures.moments(title), [(2.7, 2.0, 6.0)])
frames = subtitle_pictures.moments(title, 25)
intro = [m for m in frames if m[2] <= 2.7 + 1e-6]
outro = [m for m in frames if m[1] >= 5.6 - 1e-6]
check('for a video: the intro a frame each', len(intro), 18)          # 0.7 s at 25 fps, the last frame cut short
check('the middle once', [m for m in frames if m[1] >= 2.7 - 1e-6 and m[2] <= 5.6 + 1e-6 and m[2] - m[1] > 1],
      [(2.7, 2.7, 5.6)])
check('the outro a frame each', len(outro), 10)
check('no gaps, no overlaps', all(abs(a[2] - b[1]) < 1e-6 for a, b in zip(frames, frames[1:])), True)
check('from the start to the end', (frames[0][1], round(frames[-1][2], 6)), (2.0, 6.0))
check('too short: intro and outro meet', all(abs(a[2] - b[1]) < 1e-6 for a, b in
                                             zip(subtitle_pictures.moments(short, 25), subtitle_pictures.moments(short, 25)[1:])), True)

print('the timeline\'s zones')


def zones(timing, length):
    image = QImage(200, 20, QImage.Format_ARGB32)
    image.fill(Qt.black)
    painter = QPainter(image)
    _paint_animation_zones(painter, QRectF(0, 0, 200, 20), 0, timing, length, 50)   # 4 s across 200 px
    painter.end()
    return image


image = zones((0.7, 0.4), 4.0)
check('the intro lightens the start', image.pixelColor(1, 10).red() > 40, True)
check('fading out by its end (35 px)', image.pixelColor(33, 10).red() < 10, True)
check('the middle untouched', image.pixelColor(100, 10).red(), 0)
check('the outro lightens the end', image.pixelColor(198, 10).red() > 40, True)
image = zones((0.7, 0.4), 1.0)
red = image.pixelColor(1, 10)
check('too short: red', red.red() > red.green() + 30, True)

print('burned into a video')
folder = Path(tempfile.mkdtemp())
video = folder / 'in.mp4'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=gray:size=640x360:rate=25:duration=8', '-pix_fmt', 'yuv420p', str(video)], check=True)
out = folder / 'out.mp4'
burned_video.write_video(str(out), 'BURN MP4', [title], video=str(video), size=(640, 360), fps=25, duration=8.0)


def frame(seconds):
    raw = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', f'{seconds}', '-i', str(out),
                          '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True, check=True).stdout
    # Where the lower third is: the lower left quarter, darker than the gray where it is drawn.
    return sum(1 for y in range(200, 340) for x in range(0, 320) if raw[y * 640 + x] < 70)


at_start, settled, ending = frame(2.04), frame(4.0), frame(5.92)
check('settled: the title drawn', settled > 2000, True)
check('its first frame: hardly anything yet', at_start < settled // 4, True)
check('its last frames: fading away', ending < settled, True)
check('after it: gone', frame(7.0), 0)

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
