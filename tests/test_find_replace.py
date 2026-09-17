"""Find & Replace dialog: cursor-relative navigation and Replace targeting.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QDir, QPoint, Qt
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session
QDir.addSearchPath('graphics', session.PATH_SUBTITLD_GRAPHICS)
session.set_unsaved = lambda *a, **k: None
from subtitld.interface import find_replace_dialog as frd

def cue(t, text): return {'start': t, 'end': t + 2.0, 'text': text}

class Player:
    def set_position(self, pos):
        # Mimic the real player: writes immediately, then the echo comes back
        # ms-quantised (truncated).
        session.SUBTITLE['position'] = int(float(pos) * 1000) / 1000.0

host = types.SimpleNamespace(preview_panel_player=Player())
fails = []

def fresh(segments, cursor):
    session.SUBTITLE = {'segments': segments, 'position': cursor}
    d = frd.FindReplaceDialog.__new__(frd.FindReplaceDialog)
    frd.FindReplaceDialog.__init__(d, None)
    d._host = host
    d._find_field.lineedit.setText('cat')
    return d

def landed(d):
    seg, start = d._current
    return (seg['start'], start)

def check(label, got, want):
    ok = got == want
    print(f'  {"ok " if ok else "BAD"} {label}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok: fails.append(label)

# cues: 1.0 "cat", 5.0 "cat cat" (two matches), 9.5 "cat", 20.25 "cat"
segs = [cue(1.0, 'cat'), cue(5.0, 'a cat and a cat'), cue(9.5, 'cat'), cue(20.25, 'the cat')]

print('forward from a cursor between cues')
d = fresh(segs, 7.0)
d._on_find_next();  check('> from 7.0 lands on 9.5', landed(d), (9.5, 0))
d._on_find_next();  check('> again continues to 20.25', landed(d), (20.25, 4))
d._on_find_next();  check('> wraps to the first match', landed(d), (1.0, 0))
d._on_find_next();  check('> reaches 1st match in 5.0', landed(d), (5.0, 2))
d._on_find_next();  check('> reaches 2nd match in SAME cue', landed(d), (5.0, 12))

print('backward')
d._on_find_previous(); check('< steps back within the cue', landed(d), (5.0, 2))
d._on_find_previous(); check('< steps to the previous cue', landed(d), (1.0, 0))
d._on_find_previous(); check('< wraps to the last match', landed(d), (20.25, 4))

print('backward from a fresh cursor')
d = fresh(segs, 9.6)
d._on_find_previous(); check('< from 9.6 finds 9.5 (starts before)', landed(d), (9.5, 0))
d = fresh(segs, 9.5)
d._on_find_previous(); check('< from exactly 9.5 skips the cue AT the cursor', landed(d), (5.0, 12))
d = fresh(segs, 9.5)
d._on_find_next();     check('> from exactly 9.5 includes the cue at the cursor', landed(d), (9.5, 0))

print('moving the cursor breaks continuation')
d = fresh(segs, 0.0)
d._on_find_next(); check('> from 0 lands on 1.0', landed(d), (1.0, 0))
session.SUBTITLE['position'] = 15.0            # user clicks the timeline
d._on_find_next(); check('> after moving to 15 lands on 20.25', landed(d), (20.25, 4))
session.SUBTITLE['position'] = 15.0
d._on_find_previous(); check('< after moving to 15 lands on 9.5', landed(d), (9.5, 0))

print('changing the search resets continuation')
d = fresh(segs, 0.0)
d._on_find_next(); d._on_find_next()           # now on (5.0, 2)
d._find_field.lineedit.setText('cat ')          # different query
check('continuation cleared', d._current, None)

print('Replace acts on the match ON SCREEN (old bug: it hit the next one)')
segs2 = [cue(1.0, 'cat'), cue(5.0, 'cat'), cue(9.0, 'cat')]
d = fresh(segs2, 0.0)
d._replace_field.lineedit.setText('dog')
d._on_find_next()                               # shows 1.0
d._on_replace()
check('the shown cue was replaced', [s['text'] for s in segs2], ['dog', 'cat', 'cat'])
check('then it moved on to the next match', landed(d), (5.0, 0))

print('Replace with the needle inside the replacement does not loop')
segs3 = [cue(1.0, 'a cat here'), cue(3.0, 'cat')]
d = fresh(segs3, 0.0)
d._replace_field.lineedit.setText('big cat')
d._on_find_next(); d._on_replace()
check('first replaced, moved to the NEXT cue', (segs3[0]['text'], landed(d)), ('a big cat here', (3.0, 0)))

print('Replace without a prior find uses the match > would land on')
segs4 = [cue(1.0, 'cat'), cue(5.0, 'cat')]
d = fresh(segs4, 3.0)
d._replace_field.lineedit.setText('dog')
d._on_replace()
check('replaced the first match after the cursor', [s['text'] for s in segs4], ['cat', 'dog'])

print('unsorted cue list is still walked in time order')
segs5 = [cue(9.0, 'cat'), cue(1.0, 'cat'), cue(5.0, 'cat')]
d = fresh(segs5, 0.0)
order = []
for _ in range(3):
    d._on_find_next(); order.append(landed(d)[0])
check('time order', order, [1.0, 5.0, 9.0])

print('keyboard')
d = fresh(segs, 0.0)
le = d._find_field.lineedit
QTest.keyClick(le, Qt.Key_Return);  check('Enter = next', landed(d), (1.0, 0))
QTest.keyClick(le, Qt.Key_Return);  check('Enter again = next', landed(d), (5.0, 2))
QTest.keyClick(le, Qt.Key_Return, Qt.ShiftModifier); check('Shift+Enter = previous', landed(d), (1.0, 0))

print('buttons')
check('labels', (d._find_previous_button.text(), d._find_next_button.text(), d._replace_button.text(), d._replace_all_button.text()),
      ('', 'FIND', 'Replace', 'All'))
check('chevrons are stylesheet icons, after the text on Find',
      (d._find_previous_button.objectName(), d._find_next_button.objectName(),
       d._find_next_button.layoutDirection()),
      ('find_replace_previous_button', 'find_replace_next_button', Qt.RightToLeft))
d._find_field.lineedit.setText('zzz')
check('disabled with no matches', (d._find_previous_button.isEnabled(), d._find_next_button.isEnabled()), (False, False))

print('All sits inside Replace')
def clicks(r):
    hits = []
    r._replace_button.clicked.connect(lambda: hits.append('replace'))
    r._replace_all_button.clicked.connect(lambda: hits.append('all'))
    return hits
segs6 = [cue(1.0, 'cat'), cue(5.0, 'cat')]
r = fresh(segs6, 0.0); r.show(); app.processEvents()
rb, ab = r._replace_button, r._replace_all_button
check('All is a child of Replace', ab.parent() is rb, True)
text_end = 12 + rb.fontMetrics().horizontalAdvance(rb.text().upper())
check('All is inside Replace, right of its text',
      (rb.rect().contains(ab.geometry()), ab.geometry().left() >= text_end), (True, True))
g = ab.geometry()
check('All rises from Replace\'s bottom edge, flush right, gap above',
      (g.bottom(), g.right(), g.top()), (rb.height() - 1, rb.width() - 1, rb.INNER_TOP_GAP))
QTest.mouseMove(ab); app.processEvents()
check('pointing at All drops Replace\'s hover fill', bool(rb.property('inner_hover')), True)
QTest.mouseMove(rb, QPoint(4, 4)); app.processEvents()
check('back on Replace restores it', bool(rb.property('inner_hover')), False)
r._replace_field.lineedit.setText('dog')
hits = clicks(r)
QTest.mouseClick(ab, Qt.LeftButton)
check('clicking All runs Replace all only', (hits, [s['text'] for s in segs6]), (['all'], ['dog', 'dog']))
r.close()
segs7 = [cue(1.0, 'cat'), cue(5.0, 'cat')]
r = fresh(segs7, 0.0); r.show(); app.processEvents()
r._replace_field.lineedit.setText('dog')
hits = clicks(r)
QTest.mouseClick(r._replace_button, Qt.LeftButton, pos=QPoint(4, r._replace_button.height() // 2))
check('clicking Replace\'s text runs Replace only', (hits, [s['text'] for s in segs7]), (['replace'], ['dog', 'cat']))
r.close()

print('whole-word toggle label')
toggle = d._find_field.whole_word_toggle
check('shows its text, not markup', toggle.text(), 'ab')
check('is underlined via the font', toggle.font().underline(), True)

print()
print('FAIL: ' + ', '.join(fails) if fails else 'ALL FIND & REPLACE TESTS PASS')
sys.exit(1 if fails else 0)

