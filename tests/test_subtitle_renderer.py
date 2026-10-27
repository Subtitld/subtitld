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

print('the helper process')
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
