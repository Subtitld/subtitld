"""The preview of a subtitle timed word by word (modules.segment): each piece
lights up from its start, as its effect says or else the style's, in the
style's karaoke colour, and the layers are kept by how many pieces have
started, a frame at a time only while a piece wipes or pops in.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, sys, tempfile, time
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
from subtitld.interface.subtitle_renderer import karaoke_html, karaoke_moment

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait_for(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


def rendered(segment, at):
    def ready():
        renderer.layers([segment], at)
        return renderer._job(segment, at, *renderer._style())[0] in renderer.cache
    if not wait_for(ready):
        print(f'    (not rendered in time at {at})')
    layers = renderer.layers([segment], at)
    return layers[0][1] if layers else None


def count(image, test):
    if image is None:
        return 0
    return sum(1 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2) if test(image.pixelColor(x, y)))


def gold(color):
    return color.alpha() > 200 and color.red() > 200 and color.green() > 150 and color.blue() < 90


def white(color):
    return color.alpha() > 200 and min(color.red(), color.green(), color.blue()) > 225


PIECES = [{'text': 'HELLO ', 'start': 0.0, 'end': 0.5}, {'text': 'BIG ', 'start': 0.5, 'end': 1.0},
          {'text': 'WORLD', 'start': 1.2, 'end': 1.8}]

print('the HTML and the moments')
html = karaoke_html([{'text': '{\\an8}<i>Hel', 'start': 0.0, 'end': 0.5, 'effect': 'wipe'},
                     {'text': 'lo</i> ', 'start': 0.5, 'end': 0.8}])
check('a span a piece, its effect, its start and length; tags closed and opened again', html,
      '<span class="subtitld-k subtitld-k-wipe" style="--k-start:0ms;--k-length:500ms"><i>Hel</i></span>'
      '<span class="subtitld-k subtitld-k-fill" style="--k-start:500ms;--k-length:300ms"><i>lo</i> </span>')
check('the style\'s effect for a piece without one', 'subtitld-k-pop' in karaoke_html(PIECES[:1], 'pop'), True)
check('a pop is quick', '--k-length:150ms' in karaoke_html(PIECES[:1], 'pop'), True)
check('words taking no time: there, unlit', karaoke_html([{'text': 'Ann: ', 'start': 0.0, 'end': 0.0}] + PIECES[:1])
      .startswith('Ann: <span'), True)
check('before any piece: none started, a moment before the first', karaoke_moment(PIECES, -0.1, 25), (('started', 0), -1))
check('as one starts: held once it has lit up, before the next starts', karaoke_moment(PIECES, 0.7, 25), (('started', 2), 1000))
check('the same picture all through a piece lit at once', karaoke_moment(PIECES, 0.9, 25)[0], ('started', 2))
check('through a wipe: a frame at a time', karaoke_moment(PIECES, 0.7, 25, 'wipe'), (('frame', 17), 680))
check('after it: the pieces started again', karaoke_moment(PIECES, 1.1, 25, 'wipe'), (('started', 2), 1000))
check('back to back: before the next', karaoke_moment([dict(PIECES[0]), dict(PIECES[1], start=0.5)], 0.2, 25), (('started', 1), 499))
check('a pop held once it is in', karaoke_moment(PIECES, 0.4, 25, 'pop'), (('started', 1), 150))

session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
session.VIDEO = {'framerate': 25}
renderer = subtitle_renderer.SubtitleRenderer()
renderer.set_size(960, 540)
styles.set_value('backgroundbox_enabled', False)
styles.set_value('shadow_enabled', False)

print('lit up piece by piece')
segment = {'start': 10.0, 'end': 12.0, 'text': [dict(piece) for piece in PIECES]}
before = rendered(segment, 9.99 + 0.005)
first = rendered(segment, 10.2)
two = rendered(segment, 10.7)
done = rendered(segment, 11.9)
check('before it starts (shown early): nothing gold', count(before, gold), 0)
lit = [count(image, gold) for image in (first, two, done)]
check('more gold as each piece starts', lit[0] > 0 and lit[0] < lit[1] < lit[2], True)
check('at the end, all of it', count(done, white), 0)
check('the same layer all through a piece', renderer._job(segment, 10.6, *renderer._style())[0]
      == renderer._job(segment, 10.9, *renderer._style())[0], True)

print('wiping across')
wiped = {'start': 20.0, 'end': 22.0, 'text': [dict(piece, effect='wipe') for piece in PIECES]}
half = rendered(wiped, 20.25)
check('half the first word gold, half not', (count(half, gold) > 0, count(half, white) > 0), (True, True))
check('a layer a frame while it wipes', renderer._job(wiped, 20.25, *renderer._style())[0]
      != renderer._job(wiped, 20.33, *renderer._style())[0], True)

print('popping in')
popping = {'start': 30.0, 'end': 32.0, 'text': [dict(piece, effect='pop') for piece in PIECES]}
early = rendered(popping, 30.6)
late = rendered(popping, 31.9)
check('a piece not there before its start', count(early, white) < count(late, white), True)
plain = rendered({'start': 40.0, 'end': 42.0, 'text': 'HELLO BIG WORLD'}, 41.0)
check('all of them there once each has popped in', count(late, white) >= 0.9 * count(plain, white), True)

print('the style says')
styles.set_value('karaoke_effect', 'wipe')
check('its effect, for pieces with none', 'subtitld-k-wipe' in renderer._job(segment, 10.2, *renderer._style())[1]['page']['html'],
      True)
styles.set_value('karaoke_color', '#ff00ff00')
check('its colour', renderer._style()[1].get('--karaoke-color'), '#00ff00')
green = rendered(segment, 11.9)
check('drawn in it', count(green, lambda c: c.alpha() > 200 and c.green() > 200 and c.red() < 80 and c.blue() < 80) > 0, True)

renderer.close()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
