"""The preview's subtitles rendered as HTML: layers where the style and the
text put them, checked against the job they were rendered for, kept by what
changes their pixels, stood in for while a new one renders, at the size the
preview settles on, and a layer a frame for an animated style.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. Chromium draws nothing offscreen with its
GPU path; the renderer's helper process draws in software anyway.
"""
import json, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, session, subtitle_styles as styles
from subtitld.interface import subtitle_renderer

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait_for(condition, seconds=15):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


def rendered(renderer, segment, at=None):
    """The layers for `segment`, once they are rendered."""
    at = segment['start'] if at is None else at
    def ready():
        renderer.layers([segment], at)      # asks for it
        return renderer._job(segment, at, *renderer._style())[0] in renderer.cache
    if not wait_for(ready, 60):
        print(f'    (not rendered in time: {segment["text"]!r} at {at})')
    return renderer.layers([segment], at)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
session.VIDEO = {'framerate': 25}
check('QtWebEngine is here', subtitle_renderer.available(), True)
renderer = subtitle_renderer.SubtitleRenderer()
renderer.set_size(960, 540)

print('placed as the style and the text say')
bottom = {'start': 1.0, 'end': 3.0, 'text': 'Hello, <i>world</i>\nsecond line'}
layers = rendered(renderer, bottom)
check('one layer', len(layers), 1)
area, image = layers[0]
check('centred', abs(area.center().x() - 0.5) < 0.01, True)
check('at the bottom of the title-safe area (10%), padded', 0.9 <= area.bottom() <= 0.9 + 0.04, True)
check('pixels, not just a rect', image.width() > 50 and image.height() > 30, True)
middle = image.pixelColor(image.width() // 2, image.height() // 2)
check('the background box: translucent black', (middle.alpha() > 40, middle.alpha() < 255), (True, True))
corner = image.pixelColor(0, 0)
check('outside the box: clear', corner.alpha(), 0)
top = {'start': 3.0, 'end': 4.0, 'text': '{\\an7}Top left'}
area_top = rendered(renderer, top)[0][0]
check('{\\an7}: top left of the title-safe area', (area_top.top() < 0.15, area_top.left() < 0.15), (True, True))
empty = {'start': 4.0, 'end': 5.0, 'text': '   '}
check('an empty subtitle: no layer to draw', rendered(renderer, empty), [])
check('each capture is the job asked for', renderer.stats['rendered'] >= 3, True)

print('Subtitld\'s own fonts, whatever the system has')
wide = {'start': 6.0, 'end': 7.0, 'text': 'Condensed words here'}
styles.set_value('font_family', 'No Such Font At All')
fallback = rendered(renderer, wide)[0][1].width()
styles.set_value('font_family', 'Ubuntu Condensed')     # in graphics/, not a system font here
condensed = rendered(renderer, wide)[0][1].width()
check('a bundled font is used (narrower than the fallback)', condensed < fallback * 0.92, True)
styles.set_value('font_family', 'Montserrat')

print('kept by what changes the pixels')
before = renderer.stats['rendered']
renderer.layers([bottom], 1.5)
check('the same subtitle later: no new render', renderer.stats['rendered'], before)
check('a copy of the subtitle: the same layer', len(rendered(renderer, dict(bottom))), 1)
check('still no new render', renderer.stats['rendered'], before)

print('stood in for while the new one renders')
bottom['text'] = 'Hello, edited'
stand_in = renderer.layers([bottom], 1.0)
check('an edit: the layer before stands in at once', stand_in and stand_in[0][1] is image, True)
edited = rendered(renderer, bottom)
check('then the edited one', edited[0][1] is not image, True)
styles.set_value('font_size', 80)
during = renderer.layers([bottom], 1.0)
check('a setting changed: the layer before stands in', during and during[0][1] is edited[0][1], True)
bigger = rendered(renderer, bottom)
check('then a larger one', bigger[0][1].height() > edited[0][1].height(), True)
styles.set_value('backgroundbox_enabled', False)
plain = rendered(renderer, bottom)[0][1]
check('no box: clear between the letters', plain.pixelColor(0, plain.height() // 2).alpha(), 0)
styles.set_value('backgroundbox_enabled', True)
check('a setting set back: its layers are still there', renderer.layers([bottom], 1.0)[0][1] is bigger[0][1], True)

print('at the size the preview settles on')
renderer.set_size(1280, 720)
check('a new size does not apply at once', renderer.size, (960, 540))
wait_for(lambda: renderer.size == (1280, 720), 10)
check('it applies once it holds', renderer.size, (1280, 720))
check('meanwhile the layer before stands in', renderer.layers([bottom], 1.0)[0][1] is bigger[0][1], True)
resized = rendered(renderer, bottom)
check('then one at the new size', resized[0][1].width() > bigger[0][1].width(), True)

print('rendering ahead')
later = [{'start': 10.0 + i, 'end': 10.5 + i, 'text': f'Later {i}'} for i in range(5)]
renderer.prefetch(later, 9.0)
check('every subtitle ahead rendered before it shows', wait_for(lambda: all(
    renderer._job(s, s['start'], *renderer._style())[0] in renderer.cache for s in later)), True)

print('a subtitle on screen goes first')
busy = [{'start': 100.0 + i, 'end': 100.5 + i, 'text': f'Busy {i} ' * 6} for i in range(40)]
renderer.prefetch(busy, 100.0)
far = {'start': 500.0, 'end': 501.0, 'text': 'Jumped here'}
started = time.perf_counter()
check('not ready the moment it shows', renderer.layers([far], 500.0), [])
check('ready long before the 40 rendered ahead', len(rendered(renderer, far)), 1)
waited = (time.perf_counter() - started) * 1000
ahead_done = sum(renderer._job(s, s['start'], *renderer._style())[0] in renderer.cache for s in busy)
print(f'    (waited {waited:.0f} ms, with {ahead_done} of 40 rendered ahead meanwhile)')
check('fewer than half rendered ahead meanwhile', ahead_done < 20, True)
check('the wait was measured', len(renderer.gaps) >= 1 and renderer.gaps[-1] > 0, True)
renderer.prefetch(busy, 130.0)
first = next(iter(renderer.queue.values()), None)
check('a seek: rendering ahead goes on from there',
      first is None or first['page']['html'].startswith('Busy 3'), True)

print('an animated style: a layer a frame')
folder = session.PATH_SUBTITLD_USER_DATA / 'subtitle_styles' / 'fade'
folder.mkdir(parents=True)
(folder / 'style.json').write_text(json.dumps({'id': 'test.fade', 'name': 'Fade', 'animated': True, 'fields': []}))
(folder / 'style.html').write_text("""<!DOCTYPE html><style>
  .cue { color: #fff; font-size: 60px; animation: fade 400ms linear both; }
  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }
</style>""")
styles.available(reload=True)
styles.set_current('test.fade')
fading = {'start': 20.0, 'end': 21.0, 'text': 'Fading in'}


def strongest(layers):
    image = layers[0][1]
    return max(image.pixelColor(x, y).alpha() for x in range(0, image.width(), 2) for y in range(0, image.height(), 2))


early, late = rendered(renderer, fading, 20.04), rendered(renderer, fading, 20.6)
check('early in the fade: faint', strongest(early) < 80, True)
check('after it: solid', strongest(late) > 200, True)
check('the same frame: one layer', renderer._job(fading, 20.04, *renderer._style())[0],
      renderer._job(fading, 20.05, *renderer._style())[0])

print('kept on disk')
disk_path = Path(tempfile.mkdtemp()) / 'layers.sqlite'
styles.set_current('subtitld.classic')
first_run = subtitle_renderer.SubtitleRenderer(disk_cache=disk_path)
first_run.set_size(960, 540)
project = [{'start': 40.0 + i, 'end': 40.8 + i, 'text': f'Kept {i}'} for i in range(6)]
before = [rendered(first_run, segment) for segment in project]
check('rendered the first time', first_run.stats['rendered'], 6)
first_run.close()
second_run = subtitle_renderer.SubtitleRenderer(disk_cache=disk_path)
check('on disk once closed', len(second_run.disk.index), 6)
second_run.set_size(960, 540)
again = [rendered(second_run, segment) for segment in project]
check('the next time: read from disk, none rendered', (second_run.stats['from_disk'], second_run.stats['rendered']), (6, 0))
check('the same pixels, in the same place', all(a[0][1] == b[0][1] and a[0][0] == b[0][0] for a, b in zip(before, again)),
      True)
second_run.prefetch(project, 40.0)
check('rendering ahead reads from disk too', second_run.stats['rendered'], 0)
second_run.set_size(1280, 720)
wait_for(lambda: second_run.size == (1280, 720), 10)
check('another size: rendered, then kept', len(rendered(second_run, project[0])), 1)
check('which was rendered', second_run.stats['rendered'], 1)
second_run.close()
third_run = subtitle_renderer.SubtitleRenderer(disk_cache=disk_path)
third_run.set_size(960, 540)
import sqlite3
db = sqlite3.connect(str(disk_path))
db.execute('DELETE FROM layers'); db.execute('DELETE FROM images'); db.commit(); db.close()
check('gone from the file, still in the index', len(third_run.disk.index), 7)
check('then rendered again', len(rendered(third_run, project[1])), 1)
check('by the helper', third_run.stats['rendered'], 1)
third_run.close()
disk_path.write_bytes(b'not a database' * 100)
for suffix in ('-wal', '-shm'):
    Path(str(disk_path) + suffix).unlink(missing_ok=True)
fourth_run = subtitle_renderer.SubtitleRenderer(disk_cache=disk_path)
check('a file that is not a cache: started over', (fourth_run.disk is not None, len(fourth_run.disk.index)), (True, 0))
fourth_run.set_size(960, 540)
check('and it works', len(rendered(fourth_run, project[2])), 1)
fourth_run.close()

from subtitld.interface.subtitle_disk_cache import SubtitleDiskCache
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
small = SubtitleDiskCache(Path(tempfile.mkdtemp()) / 'small.sqlite', limit_mb=1)
for i in range(12):
    # Opaque noise: 360 KB that does not compress.
    pixels = bytearray(os.urandom(300 * 300 * 4))
    pixels[3::4] = b'\xff' * (300 * 300)
    noise = QImage(bytes(pixels), 300, 300, 1200, QImage.Format_ARGB32_Premultiplied).copy()
    small.put(f'layer {i}', QRectF(0, 0, 0.1, 0.1), noise)
small.put('empty', None, None)
small.maintain()
small.writer.submit(lambda: None).result()
size, images, layers = small.size()
check('past its limit: trimmed under it', size <= 2**20, True)
check('the oldest layers went first', ('layer 0' not in small.index, 'layer 11' in small.index), (True, True))
check('and their images with them', images < 12, True)
small.close()

print('a style reaches its own files, and nothing else')
from PySide6.QtGui import QColor as _QColor
outside = Path(tempfile.mkdtemp())
red = QImage(40, 40, QImage.Format_ARGB32)
red.fill(_QColor('#ff0000'))
red.save(str(outside / 'red.png'))
folder = session.PATH_SUBTITLD_USER_DATA / 'subtitle_styles' / 'reach'
folder.mkdir(parents=True)
green = QImage(40, 40, QImage.Format_ARGB32)
green.fill(_QColor('#00ff00'))
green.save(str(folder / 'green.png'))
(folder / 'style.json').write_text(json.dumps({'id': 'test.reach', 'name': 'Reach', 'fields': []}))
(folder / 'style.html').write_text(f"""<!DOCTYPE html><style>
  .cue {{ color: #fff; font-size: 60px; }}
  .cue::before {{ content: url("green.png"); }}
  .cue::after {{ content: url("{(outside / 'red.png').as_uri()}"); }}
</style>""")
styles.available(reload=True)
styles.set_current('test.reach')
reach_renderer = subtitle_renderer.SubtitleRenderer(disk_cache=None)
reach_renderer.set_size(960, 540)
reached = rendered(reach_renderer, {'start': 0.0, 'end': 1.0, 'text': 'Reach'})[0][1]


def has(image, test):
    return any(test(image.pixelColor(x, y)) for x in range(0, image.width(), 2) for y in range(0, image.height(), 2))


check('an image in its own folder: shown', has(reached, lambda c: c.green() > 200 and c.red() < 60), True)
check('a file outside it: refused', has(reached, lambda c: c.red() > 200 and c.green() < 60), False)
reach_renderer.close()
styles.set_current('subtitld.classic')

print('where Chromium\'s sandbox cannot start')
stand_in = Path(tempfile.mkdtemp()) / 'no_sandbox_worker.py'
stand_in.write_text(f"""import os, runpy, sys
if '--no-sandbox' not in sys.argv[1:]:
    sys.exit(subtitle_render_worker_exit := {subtitle_renderer.NO_SANDBOX_EXIT})
sys.argv = ['subtitld', '--subtitle-render-worker', '--no-sandbox']
runpy.run_module('subtitld', run_name='__main__')
""")
real_command = subtitle_renderer.worker_command
subtitle_renderer.worker_command = lambda: (sys.executable, [str(stand_in)])
sandboxless = subtitle_renderer.SubtitleRenderer(disk_cache=None)
sandboxless.set_size(640, 360)
check('started again without it, and it renders', len(rendered(sandboxless, {'start': 0.0, 'end': 1.0, 'text': 'No sandbox'})), 1)
check('not counted as a stop', (sandboxless.no_sandbox, sandboxless.stats['restarts']), (True, 0))
sandboxless.close()
subtitle_renderer.worker_command = real_command

print('the helper process')
cold = subtitle_renderer.SubtitleRenderer()
cold.set_size(640, 360)
cold.warm_up()
check('warmed up: the helper starts before any subtitle', cold.process is not None, True)
wait_for(lambda: cold.slots > 0)
loop = QEventLoop()
QTimer.singleShot(1500, loop.quit)       # the helper loads the style meanwhile
loop.exec()
started = time.perf_counter()
first = rendered(cold, {'start': 0.0, 'end': 1.0, 'text': 'First'})
check('then the first subtitle comes as fast as any', (len(first), (time.perf_counter() - started) * 1000 < 300), (1, True))
cold.close()
process = renderer.process
check('one helper, running', process is not None and process.state() == process.ProcessState.Running, True)
process.kill()
check('killed: started again for the next subtitle', len(rendered(renderer, {'start': 30.0, 'end': 31.0, 'text': 'After'})), 1)
check('a new helper', renderer.process is not None and renderer.process is not process, True)
process = renderer.process
renderer.close()
check('closed with Subtitld', process.state(), process.ProcessState.NotRunning)

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
