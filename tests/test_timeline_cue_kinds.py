"""Marks and titles on the timeline (modules.cue_kinds): a click on one in
its band selects it, and it stays selected when the button is let go, over
a subtitle or not; a click where there is nothing selects nothing.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, sys, tempfile, types
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history, cue_kinds, timeline_themes, config
from subtitld.modules.segment import Subtitle
from subtitld.interface import timeline
class Player:
    def seek(self, p): pass
    def is_paused(self): return True
class Host(QWidget):
    def __init__(self):
        super().__init__(); self.resize(900, 200)
        self.setLayout(QVBoxLayout()); self.layout().setContentsMargins(0,0,0,0)
        self.preview_panel_player = Player()
        panel = types.SimpleNamespace(update=lambda w: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)
        self.subtitles_panel_qlistwidget = types.SimpleNamespace(update_content=lambda: None)
session.CONFIG = config.Config(); session.CONFIG['timeline_zoom'] = 100
timeline_themes.apply('subtitld.classic-light')
session.VIDEO = {'duration': 9.0}
session.SPEAKERS = {'A': {}}
segs = [{'start': 1.0, 'end': 3.5, 'text': 'Hello', 'speaker': 'A'},
        {'kind': 'title', 'start': 0.5, 'end': 4.0, 'text': 'Lisbon', 'title_theme': 'subtitld.plain', 'title_settings': {}},
        cue_kinds.new_mark(5.0, 'Mark')]
session.SUBTITLE = Subtitle({'segments': sorted(segs, key=lambda s: s['start']), 'selected': None, 'position': 8.0})
history.history_clear()
host = Host(); timeline.load(host); host.layout().addWidget(host.timeline_scroll); host.show(); QTest.qWait(200)
w = host.timeline_widget

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def click(pt):
    QTest.mouseMove(w, pt)
    QTest.qWait(20)
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, pt)
    QTest.qWait(20)
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, pt)
    QTest.qWait(20)
    return session.SUBTITLE.selected['text'] if session.SUBTITLE.selected else None


print('clicking cues of every kind')
for name in ('Lisbon', 'Hello', 'Mark'):
    cue = next(s for s in session.SUBTITLE.segments if s['text'] == name)
    r = w.box_rect(cue)
    for where, pt in (('in its middle', r.center().toPoint()), ('near its start', QPoint(int(r.left()) + 30, int(r.center().y())))):
        session.SUBTITLE.selected = None
        check(f'{name}, {where}', click(pt), name)
title = next(s for s in session.SUBTITLE.segments if s['text'] == 'Lisbon')
check('a title over no subtitle stays selected', click(QPoint(int(w.box_rect(title).left()) + 10, int(w.box_rect(title).center().y()))), 'Lisbon')
check('nothing there: nothing selected', click(QPoint(850, 150)), None)

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
