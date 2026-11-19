"""Timing a subtitle in the subtitles list: the formatting bar's timing menu
times its text word by word, syllable by syllable (in its language) or
letter by letter, or takes the timing away; the pieces strip under it shows
the pieces, a click choosing one, cutting the chosen one between two
letters or joining two at the gap between them, and sets the chosen one's
times and effect. Each change is one undo step.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QPoint, Qt
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
                                         language=lambda: session.SUBTITLE.get('language'))
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
rect, edges = strip.chips._chips[1]
index, x = edges[2]                       # before the third letter of "junto"
point = QPoint(int(x), int(rect.center().y()))
QTest.mouseMove(strip.chips, point)
check('between two letters of the chosen one: a cut', strip.chips.hover[:3], ('cut', 1, index))
QTest.mouseClick(strip.chips, Qt.LeftButton, pos=point)
check('cut there', texts(), ['Cantando ', 'ju', 'nto ', 'a ', 'palavra'])
check('the second half chosen', strip.chosen, 2)
history.history_undo()
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
strip.refresh()
check('one undo step', texts(), ['Cantando ', 'junto ', 'a ', 'palavra'])
rect = strip.chips._chips[0][0]
gap = QPoint(int(rect.right() + 3), int(rect.center().y()))
QTest.mouseMove(strip.chips, gap)
check('the gap after a chip: a join', strip.chips.hover, ('gap', 0))
QTest.mouseClick(strip.chips, Qt.LeftButton, pos=gap)
check('joined', texts(), ['Cantando junto ', 'a ', 'palavra'])

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

print('not timed')
bar._show_timing()
bar.untimed_action.trigger()
check('a string again', session.SUBTITLE.selected['text'], 'Cantando junto a palavra')
strip.refresh()
check('the strip hides', strip.isVisible(), False)
session.SUBTITLE['language'] = 'ja-jp'
bar.timing_actions['syllable'].trigger()
check('syllables in a language without a dictionary: words', texts(), ['Cantando ', 'junto ', 'a ', 'palavra'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
