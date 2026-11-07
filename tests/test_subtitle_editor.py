"""The subtitle list's editor: a subtitle's text shown formatted, what is
typed and formatted there kept as tags, and the formatting bar.

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
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import history, markup, session
from subtitld.interface import subtitle_text_editor as ste

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def select(editor, start, end):
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)


def loaded(text):
    editor = QTextEdit()
    ste.set_text(editor, text)
    return editor


print('the markup the editor needs')
check('runs back to well-nested tags', markup.from_spans([('a ', markup.Style(italic=True)), ('b', markup.Style(italic=True, bold=True)),
                                                         (' c', markup.Style(italic=True)), (' d', markup.PLAIN)]), '<i>a <b>b</b> c</i> d')
check('a colour outermost', markup.from_spans([('x', markup.Style(bold=True, color='#ff0000'))]), '<font color="#ff0000"><b>x</b></font>')
check('the codes a text starts with', markup.split_lead('{\\an8}{\\fad(1,2)}Top'), ('{\\an8}{\\fad(1,2)}', 'Top'))
check('none', markup.split_lead('<i>x</i>'), ('', '<i>x</i>'))
check('a position set, others kept', markup.with_alignment('{\\fad(1,2)}', 8), '{\\an8\\fad(1,2)}')
check('the default position takes the code away', markup.with_alignment('{\\an8}', 2), '')

print('shown formatted, kept as tags')
for text in ('Plain', '<i>Italic</i> and <b>bold</b>', '<font color="#ffd700">Gold</font>\n<i>second line</i>',
             '<u>under</u> <s>struck</s>', '{\\an8}<i>Top</i>', 'Tom & Jerry <3', ''):
    editor = loaded(text)
    check(f'round trip {text!r}', ste.text(editor), text)
editor = loaded('{\\an8}<i>Hello</i> world')
check('the editor shows no tag', editor.toPlainText(), 'Hello world')
check('the position is kept aside', (editor.subtitle_lead, ste.alignment(editor)), ('{\\an8}', 8))
check('a tag spanning lines stays one', ste.text(loaded('<i>one\ntwo</i>')), '<i>one\ntwo</i>')
check('it ends on the line it ends on', ste.text(loaded('<font color="#ff0000">one</font>\n<i>two</i>')),
      '<font color="#ff0000">one</font>\n<i>two</i>')

print('typing keeps the formatting around it')
editor = loaded('<i>Hello</i> world')
cursor = editor.textCursor()
cursor.setPosition(3)
editor.setTextCursor(cursor)
editor.insertPlainText('XY')
check('typed inside italic is italic', ste.text(editor), '<i>HelXYlo</i> world')

print('formatting commands')
editor = loaded('Hello world')
select(editor, 6, 11)
ste.toggle(editor, 'italic')
check('italic on the selection', ste.text(editor), 'Hello <i>world</i>')
check('the selection stays', (editor.textCursor().selectionStart(), editor.textCursor().selectionEnd()), (6, 11))
ste.toggle(editor, 'italic')
check('again takes it off', ste.text(editor), 'Hello world')
editor.setTextCursor(QTextCursor(editor.document()))
ste.toggle(editor, 'bold')
check('nothing selected: the whole text', ste.text(editor), '<b>Hello world</b>')
select(editor, 0, 5)
ste.toggle(editor, 'bold')
check('part of it off (the space after it was not selected)', ste.text(editor), 'Hello<b> world</b>')
select(editor, 0, 11)
check('mixed is not all on', ste.is_on(editor, 'bold'), False)
ste.toggle(editor, 'bold')
check('mixed turns all on', ste.text(editor), '<b>Hello world</b>')
ste.set_color(editor, '#00ff00')
check('a colour', ste.text(editor), '<font color="#00ff00"><b>Hello world</b></font>')
ste.set_color(editor, '')
check('the default colour', ste.text(editor), '<b>Hello world</b>')
ste.toggle(editor, 'underline')
ste.toggle(editor, 'strike')
check('underline and strikeout', ste.text(editor), '<b><u><s>Hello world</s></u></b>')
ste.clear_formatting(editor)
check('cleared', ste.text(editor), 'Hello world')
ste.set_alignment(editor, 8)
check('the position', ste.text(editor), '{\\an8}Hello world')

print('cut at the cursor, each half whole')
editor = loaded('{\\an8}<i>Hello <b>big</b> world</i>')
check('halves', ste.halves(editor, 8), ('{\\an8}<i>Hello <b>bi</b></i>', '{\\an8}<i><b>g</b> world</i>'))
check('at the start', ste.halves(editor, 0), ('{\\an8}', '{\\an8}<i>Hello <b>big</b> world</i>'))

print('the bar')
session.SUBTITLE['segments'] = []
history.history_clear() if hasattr(history, 'history_clear') else None
editor = loaded('<i>Hello</i> world')
bar = ste.FormattingBar(editor)
changes = []
bar.changed.connect(lambda: changes.append(ste.text(editor)))
select(editor, 0, 5)
check('shows the selection italic', (bar.flag_buttons['italic'].isChecked(), bar.flag_buttons['bold'].isChecked()), (True, False))
bar.flag_buttons['bold'].click()
check('a click formats', ste.text(editor), '<i><b>Hello</b></i> world')
select(editor, 6, 11)
check('follows the selection', bar.flag_buttons['italic'].isChecked(), False)
bar._pick_color('#ff0000')
check('a swatch colours', ste.text(editor), '<i><b>Hello</b></i> <font color="#ff0000">world</font>')
bar._pick_position(9)
check('a position, told', (ste.text(editor), changes[-1:]), ('{\\an9}<i><b>Hello</b></i> <font color="#ff0000">world</font>',
                                                         ['{\\an9}<i><b>Hello</b></i> <font color="#ff0000">world</font>']))
check('the position cell lit', [key for key, cell in bar.position_cells.items() if cell.isChecked()], [9])
editor.show()
editor.setFocus()
select(editor, 0, 5)
QTest.keyClick(editor, Qt.Key_U, Qt.ControlModifier)
check('Ctrl+U underlines', ste.text(editor), '{\\an9}<i><b><u>Hello</u></b></i> <font color="#ff0000">world</font>')
QTest.keyClick(editor, Qt.Key_I, Qt.ControlModifier)
check('Ctrl+I takes italic off', ste.text(editor), '{\\an9}<b><u>Hello</u></b> <font color="#ff0000">world</font>')
editor.setTextCursor(QTextCursor(editor.document()))
bar.clear_button.click()
check('clear: all of it, the position too', ste.text(editor), 'Hello world')

print('placed finely: margins and a turn')
from subtitld.modules import position
check('dragged up from the bottom: a vertical margin from the bottom',
      position.dragged(position.NOWHERE, 2, 0, -0.1), position.Place(v='20%'))
check('dragged right at the top right: in from the right edge',
      position.dragged(position.NOWHERE, 9, 0.04, 0.05), position.Place(h='6%', v='15%'))
check('in the middle: off the middle, kept on the picture',
      position.dragged(position.NOWHERE, 5, 0.8, 0), position.Place(h='50%'))
check('a margin of its own moves from where it is',
      position.dragged(position.Place(v='30%', rotate=(0.0, 0.0, 10.0)), 2, 0, 0.05), position.Place(v='25%', rotate=(0.0, 0.0, 10.0)))
check('what it sits by without one: the title-safe area', position.margins(position.NOWHERE, 7, (0.05, 0.1)), (5.0, 10.0))

held = {'text': 'Hello', 'start': 0, 'end': 1}
placed = loaded('Hello')
placed_bar = ste.FormattingBar(placed, segment=lambda: held)
placed_bar._show_place()
check('the menu shows where it sits now', [placed_bar.place_spins[k].value() for k in ('horizontal', 'vertical', 'rotation')],
      [0.0, 10.0, 0.0])
check('centred across: either side of the middle', (placed_bar.place_spins['horizontal'].minimum(), placed_bar.place_spins['horizontal'].maximum()), (-50.0, 50.0))
steps = len(history.ALL_HISTORY)
placed_bar.place_spins['vertical'].setValue(25)
placed_bar.place_spins['rotation'].setValue(15)
check('a margin and a turn kept on the subtitle', held.get('position'), {'v': '25%', 'rotate': {'z': 15.0}})
check('one undo step for the menu', len(history.ALL_HISTORY) - steps, 1)
check('the position button lit', placed_bar.position_button.icon().isNull(), False)
placed_bar._pick_position(8)
check('one of the nine places: the margins go, the turn stays', (ste.text(placed), held.get('position')), ('{\\an8}Hello', {'rotate': {'z': 15.0}}))
placed_bar._show_place()
placed_bar.place_spins['horizontal'].setValue(-12.5)
placed_bar._reset_place()
check('reset: back to the place, unturned', 'position' in held, False)
placed_bar.place_spins['vertical'].setValue(30)
placed_bar.clear_button.click()
check('clear takes the position too', ('position' in held, ste.text(placed)), (False, 'Hello'))
check('without a subtitle the numbers are not offered', (ste.FormattingBar(loaded('x'))._show_place(), ), (None,))

print('a reading stays a reading')
ruby = QTextEdit()
ste.set_text(ruby, '<ruby>東京<rt>とうきょう</rt></ruby> is big')
check('shown and read back as it was', ste.text(ruby),
      '<ruby>東京<rt>とうきょう</rt></ruby> is big')
check('the reading drawn raised, where ruby draws it above',
      ste.runs(ruby)[1][1].ruby, 'text')
ste.set_text(ruby, 'A <ruby><i>漢字</i><rt>かんじ</rt></ruby> here')
check('with the formatting inside it', ste.text(ruby), 'A <ruby><i>漢字</i><rt>かんじ</rt></ruby> here')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
