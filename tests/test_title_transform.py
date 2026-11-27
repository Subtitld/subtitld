"""A title moved, turned and sized as a whole, whatever its theme
(modules.title_themes, 'title_transform'): its numbers and their limits,
switched off and on with its numbers kept, in its USFX, carried by a cut,
left out of a title exported as a subtitle, drawn by the renderer (moved by a
share of the picture, sized, turned), and set in the title's settings dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QGroupBox
app = QApplication([])
from subtitld.modules import config, session, subtitles, title_themes, usf
from subtitld.modules.segment import Segment, Subtitle
from subtitld.interface import subtitle_renderer
from subtitld.interface.title_panel import TitleSettingsDialog

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def raises(name, error, call):
    try:
        call()
    except error:
        check(name, True, True)
        return
    check(name, False, True)


def wait_for(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.SUBTITLE = Subtitle(segments=[])
session.VIDEO = {'framerate': 25}

print('its numbers')
title = Segment({'kind': 'title', 'start': 1, 'end': 5, 'text': 'Lisbon, 1755', 'title_theme': 'subtitld.plain'})
check('off, nothing moved', title_themes.transform_values(title),
      {'on': False, 'x': 0, 'y': 0, 'rotate': 0, 'size': 100})
check('nothing kept', 'title_transform' in title, False)
check('nothing drawn', title_themes.drawn_transform(title), None)
title_themes.set_transform(title, 'x', 25)
check('a number set while off: kept, not drawn', (title['title_transform'], title_themes.drawn_transform(title)),
      ({'x': 25}, None))
title_themes.set_transform(title, 'on', True)
check('switched on: drawn', title_themes.drawn_transform(title), {'x': 25, 'y': 0, 'rotate': 0, 'size': 100})
check('held in its limits', (title_themes.set_transform(title, 'y', -500), title_themes.set_transform(title, 'size', 1)),
      (-100, 10))
raises('not a number', ValueError, lambda: title_themes.set_transform(title, 'rotate', 'a lot'))
raises('on is true or false', ValueError, lambda: title_themes.set_transform(title, 'on', 1))
raises('no such number', KeyError, lambda: title_themes.set_transform(title, 'skew', 3))
title_themes.set_transform(title, 'y', 0)
title_themes.set_transform(title, 'size', 100)
title_themes.set_transform(title, 'on', False)
check('off: its numbers kept for when it is on again', title['title_transform'], {'x': 25})
title_themes.set_transform(title, 'x', 0)
check('nothing left to keep: gone', 'title_transform' in title, False)
title_themes.set_transform(title, 'on', True)
check('on, changing nothing: nothing drawn', (title['title_transform'], title_themes.drawn_transform(title)),
      ({'on': True}, None))
title_themes.set_transform(title, 'rotate', -12.5)
title_themes.set_transform(title, 'size', 80)

print('in the file')
read = usf.USFReader().read(usf.USFWriter().write([title], language='en-us'))
check('read back', read[0].get('title_transform'), {'on': True, 'rotate': -12.5, 'size': 80})
plain = Segment({'kind': 'title', 'start': 1, 'end': 5, 'text': 'x', 'title_theme': 'subtitld.plain'})
check('none written for a title without', 'title_transform' in usf.USFReader().read(
    usf.USFWriter().write([plain], language='en-us'))[0], False)

print('a cut and an export')
session.SUBTITLE = Subtitle(segments=[title])
subtitles.slice_subtitle(title, 3.0, 'Lisbon,', '1755')
check('both halves keep it', [cue.get('title_transform') for cue in session.SUBTITLE.segments],
      [{'on': True, 'rotate': -12.5, 'size': 80}] * 2)
check('their own, not shared', session.SUBTITLE.segments[0]['title_transform'] is
      session.SUBTITLE.segments[1]['title_transform'], False)
check('not a subtitle\'s', 'title_transform' in title_themes.as_subtitle(title), False)
session.SUBTITLE = Subtitle(segments=[])

print('drawn')
check('QtWebEngine is here', subtitle_renderer.available(), True)
renderer = subtitle_renderer.SubtitleRenderer()
renderer.set_size(1280, 720)


def area(cue):
    def ready():
        renderer.layers([cue], 2.0)
        return renderer._job(cue, 2.0, *renderer._style())[0] in renderer.cache
    wait_for(ready)
    layers = renderer.layers([cue], 2.0)
    return layers[0][0] if layers else None


still = Segment({'kind': 'title', 'start': 1, 'end': 5, 'text': 'Moved', 'title_theme': 'subtitld.plain'})
before = area(still)
moved = Segment(dict(still, title_transform={'on': True, 'x': 20, 'y': -10}))
after = area(moved)
check('moved by a share of the picture', (round(after.center().x() - before.center().x(), 2),
                                          round(after.center().y() - before.center().y(), 2)), (0.2, -0.1))
bigger = area(Segment(dict(still, title_transform={'on': True, 'size': 200})))
# The layer's own padding does not grow with it: a little under twice as wide.
check('twice the size, about its middle', (1.7 < bigger.width() / before.width() < 2.0,
                                           round(bigger.center().x() - before.center().x(), 2)), (True, 0.0))
turned = area(Segment(dict(still, title_transform={'on': True, 'rotate': 90})))
check('turned a quarter: taller than wide', turned.height() > turned.width(), True)
off = area(Segment(dict(still, title_transform={'x': 20})))
check('switched off: where its theme puts it', (round(off.x(), 3), round(off.y(), 3)),
      (round(before.x(), 3), round(before.y(), 3)))

print('the settings dialog')
title = Segment({'kind': 'title', 'start': 1, 'end': 5, 'text': 'x', 'title_theme': 'subtitld.plain'})
dialog = TitleSettingsDialog(title)
group = dialog.findChild(QGroupBox, 'title_transform_group')
spins = group.findChildren(QAbstractSpinBox)
check('a group switched off, four numbers', (group.isCheckable(), group.isChecked(), len(spins)), (True, False, 4))
changes = []
dialog.changed.connect(lambda: changes.append(1))
group.setChecked(True)
spins[0].setValue(15)
spins[2].setValue(30)
check('switched on, moved and turned', title.get('title_transform'), {'on': True, 'x': 15, 'rotate': 30})
check('each change said', len(changes), 3)
dialog.reset()
group = dialog.findChild(QGroupBox, 'title_transform_group')
check('Reset: where its theme puts it', ('title_transform' in title, group.isChecked()), (False, False))

print()
print('ALL PASS' if not fails else f'FAILED: {fails}')
sys.exit(1 if fails else 0)
