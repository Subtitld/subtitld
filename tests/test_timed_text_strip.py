"""Timing a subtitle in the subtitles list: the formatting bar's timing menu
times its text word by word, syllable by syllable (in its language) or
letter by letter, or takes the timing away; the pieces strip under it shows
the pieces, each a field: a click chooses one and types in it, Enter or a
click away keeping the words, Escape not, Tab going on to the next; with
Ctrl a click cuts one between two letters, and a click on the gap between
two joins them. It sets the chosen one's times and effect too. Each change
is one undo step.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import copy, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit
app = QApplication([])
from subtitld.interface import subtitle_text_editor, timed_text_strip
from subtitld.modules import history, session
from subtitld.modules.segment import Subtitle, timed_pieces

fails = []


def check(name, got, want):
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f'  (got {got!r}, want {want!r})'))
    if not ok:
        fails.append(name)


def hover(point, keys=Qt.NoModifier):
    """The pointer moved over the chips, no button down: sent to them
    directly, as a display with no pointer of its own may not."""
    at = QPointF(point)
    strip.chips.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, at, at, Qt.NoButton, Qt.NoButton, keys))


def texts():
    return [piece['text'] for piece in timed_pieces(session.SUBTITLE.selected)]


session.CONFIG.setdefault('quality_check', {})
session.SUBTITLE = Subtitle({'segments': [{'id': 'a1', 'start': 1.0, 'end': 4.0, 'text': 'Cantando junto a palavra'}],
                             'language': 'pt-br'})
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
history.history_clear()

textedit = QTextEdit()
subtitle_text_editor.set_text(textedit, session.SUBTITLE.selected.text())
bar = subtitle_text_editor.FormattingBar(textedit, segment=lambda: session.SUBTITLE.selected,
                                         language=lambda: session.SUBTITLE.language)
strip = timed_text_strip.TimedTextStrip(lambda: session.SUBTITLE.selected)
strip.setFixedWidth(420)
bar.changed.connect(strip.refresh)
strip.show()
strip.refresh()
app.processEvents()

print('the timing menu')
check('a subtitle not timed: no strip', strip.isVisible(), False)
check('the button is there', bar.timing_button.isVisibleTo(bar), True)
bar._show_timing()
check('no level checked, nothing to take away', ([a.isChecked() for a in bar.timing_actions.values()],
                                                  bar.untimed_action.isEnabled()), ([False, False, False], False))
bar.timing_actions['syllable'].trigger()
check('syllable by syllable, in its language', texts(), ['Can', 'tan', 'do ', 'jun', 'to ', 'a ', 'pa', 'la', 'vra'])
check('the strip shows', strip.isVisible(), True)
check('the editor keeps the words', subtitle_text_editor.text(textedit), 'Cantando junto a palavra')
bar._show_timing()
check('its level checked', bar.timing_actions['syllable'].isChecked(), True)
bar.timing_actions['word'].trigger()
check('word by word, from the syllables', texts(), ['Cantando ', 'junto ', 'a ', 'palavra'])
history.history_undo()
check('one undo step: syllables again', len(timed_pieces(session.SUBTITLE.segments[0])), 9)
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
bar.timing_actions['word'].trigger()

print('the strip')
strip.refresh()
app.processEvents()
chips = strip.chips._chips
check('a chip a piece', len(chips), 4)
check('the first chosen', strip.chosen, 0)
QTest.mouseClick(strip.chips, Qt.LeftButton, pos=chips[1][0].center().toPoint())
check('a click chooses another', strip.chosen, 1)
check('its times shown', (strip.start_spin.value(), strip.end_spin.value()),
      tuple(float(timed_pieces(session.SUBTITLE.selected)[1][key]) for key in ('start', 'end')))
check('and types in it', (strip.chips.editing, strip.chips.editor.isVisible(), strip.chips.editor.text()),
      (1, True, 'junto'))
strip.chips.finish('cancel')
rect, edges = strip.chips._chips[1]
index, x = edges[2]                       # before the third letter of "junto"
point = QPoint(int(x), int(rect.center().y()))
hover(point)
check('between two letters, no Ctrl: typed in', strip.chips.hover, ('chip', 1))
hover(point, Qt.ControlModifier)
check('with Ctrl: a cut', strip.chips.hover[:3], ('cut', 1, index))
rect, edges = strip.chips._chips[3]
hover(QPoint(int(edges[3][1]), int(rect.center().y())), Qt.ControlModifier)
check('with Ctrl, any piece, not only the chosen one', strip.chips.hover[:2], ('cut', 3))
QTest.mouseClick(strip.chips, Qt.LeftButton, Qt.ControlModifier, point)
check('cut there', texts(), ['Cantando ', 'ju', 'nto ', 'a ', 'palavra'])
check('the second half chosen', strip.chosen, 2)
history.history_undo()
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
strip.refresh()
check('one undo step', texts(), ['Cantando ', 'junto ', 'a ', 'palavra'])
rect = strip.chips._chips[0][0]
gap = QPoint(int(rect.right() + 3), int(rect.center().y()))
hover(gap)
check('the gap after a chip: a join', strip.chips.hover, ('gap', 0))
QTest.mouseClick(strip.chips, Qt.LeftButton, pos=gap)
check('joined', texts(), ['Cantando junto ', 'a ', 'palavra'])

print('typing')
before_typing = copy.deepcopy(session.SUBTITLE.selected['text'])


def type_in(number, keys, caret=None):
    strip.choose(number)
    strip.chips.edit(number, caret)
    QTest.keyClicks(strip.chips.editor, keys)


type_in(0, 'Dancando')
check('typed over the words, all selected', texts(), ['Cantando junto ', 'a ', 'palavra'])
QTest.keyClick(strip.chips.editor, Qt.Key_Return)
check('Enter keeps them, the space after', texts(), ['Dancando ', 'a ', 'palavra'])
check('the field gone', (strip.chips.editing, strip.chips.editor.isVisible()), (None, False))
history.history_undo()
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
strip.refresh()
check('one undo step', texts(), ['Cantando junto ', 'a ', 'palavra'])
times = [(piece['start'], piece['end']) for piece in timed_pieces(session.SUBTITLE.selected)]
type_in(2, 's', caret=7)
QTest.keyClick(strip.chips.editor, Qt.Key_Escape)
check('Escape leaves it as it was', texts(), ['Cantando junto ', 'a ', 'palavra'])
type_in(2, 's', caret=7)
QTest.keyClick(strip.chips.editor, Qt.Key_Tab)
check('Tab keeps it', texts(), ['Cantando junto ', 'a ', 'palavras'])
check('its times as they were', [(piece['start'], piece['end']) for piece in timed_pieces(session.SUBTITLE.selected)],
      times)
check('the last: nowhere to go', strip.chips.editing, None)
type_in(1, 'b')
QTest.keyClick(strip.chips.editor, Qt.Key_Backtab)
check('Shift+Tab keeps it and goes back', (texts()[1], strip.chips.editing, strip.chosen), ('b ', 0, 0))
check('all its words selected', strip.chips.editor.selectedText(), 'Cantando junto')
QTest.keyClick(strip.chips.editor, Qt.Key_Escape)
type_in(1, 'a seu')
QTest.keyClick(strip.chips.editor, Qt.Key_Return)
check('a word typed a space into: cut in two', texts(), ['Cantando junto ', 'a ', 'seu ', 'palavras'])
type_in(2, '')
strip.chips.editor.clear()
QTest.keyClick(strip.chips.editor, Qt.Key_Return)
check('emptied: gone, its time to the one before', (texts(), timed_pieces(session.SUBTITLE.selected)[1]['end']),
      (['Cantando junto ', 'a ', 'palavras'], times[2][0]))
type_in(2, 'x', caret=0)
QTest.mouseClick(strip.chips, Qt.LeftButton, pos=strip.chips._chips[0][0].center().toPoint())
check('a click on another keeps it, then types in that one', (texts()[2], strip.chips.editing), ('xpalavras', 0))
strip.chips.finish('cancel')
type_in(2, 'y', caret=0)
history.history_undo()
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
strip.refresh()
check('an undo while typed in: the field gone, nothing typed kept', (strip.chips.editing, texts()[2]),
      (None, 'palavras'))
session.SUBTITLE.segments.append({'id': 'b2', 'start': 5.0, 'end': 6.0, 'text': 'Outra'})
type_in(2, 'z', caret=0)
session.SUBTITLE.selected = session.SUBTITLE.segments[1]
strip.refresh()
check('another subtitle chosen while typed in: kept in the first',
      timed_pieces(session.SUBTITLE.segments[0])[2]['text'], 'zpalavras')
check('the strip hides for it', strip.isVisible(), False)
del session.SUBTITLE.segments[1]
session.SUBTITLE.segments[0]['text'] = before_typing
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
strip.refresh()
history.history_clear()

print('times and effect')
strip.choose(1)
pieces = timed_pieces(session.SUBTITLE.selected)
strip.start_spin.setValue(0.0)
check('a start before the piece before: kept after it', pieces[1]['start'], pieces[0]['end'])
strip.end_spin.setValue(99.0)
check('an end past the next: kept before it', pieces[1]['end'], pieces[2]['start'])
check('the fields show what it took', strip.end_spin.value(), pieces[2]['start'])
strip.effect_combo.setCurrentIndex(timed_text_strip.EFFECTS.index('wipe'))
check('an effect', pieces[1].get('effect'), 'wipe')
strip.effect_combo.setCurrentIndex(0)
check('the style\'s again', 'effect' in timed_pieces(session.SUBTITLE.selected)[1], False)

print('joined from the timing line')
before = texts()
strip.choose(0)
check('the first has none before it', (strip.join_back_button.isEnabled(), strip.join_next_button.isEnabled()), (False, True))
strip.choose(1)
strip.join_back_button.click()
check('with the one before', texts(), [before[0] + before[1], *before[2:]])
check('the joined one chosen', strip.chosen, 0)
history.history_undo()
strip.refresh()
strip.choose(1)
strip.join_next_button.click()
check('with the next', texts(), [before[0], before[1] + before[2], *before[3:]])
strip.choose(len(texts()) - 1)
check('the last has none after it', strip.join_next_button.isEnabled(), False)
history.history_undo()
strip.refresh()

print('not timed')
bar._show_timing()
bar.untimed_action.trigger()
check('a string again', session.SUBTITLE.selected['text'], 'Cantando junto a palavra')
strip.refresh()
check('the strip hides', strip.isVisible(), False)
session.SUBTITLE.language = 'ja-jp'
bar.timing_actions['syllable'].trigger()
check('syllables in a language without a dictionary: words', texts(), ['Cantando ', 'junto ', 'a ', 'palavra'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
