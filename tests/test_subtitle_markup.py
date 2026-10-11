"""Formatting tags in a subtitle's text: read for each use, drawn in the
preview, not counted by the quality check, kept well formed when a subtitle
is cut in two.

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
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtCore import QRectF
app = QApplication([])
for font in (ROOT / 'src/subtitld/graphics').glob('Montserrat-*.ttf'):
    QFontDatabase.addApplicationFont(str(font))
from subtitld.modules import markup, quality_check, session, subtitles
from subtitld.interface import subtitle_painter

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def styles(text):
    return [(run, ''.join(flag for flag, on in zip('ibus', style[:4]) if on) + style.color) for run, style in markup.spans(text)]


print('reading the tags')
check('plain text is left alone', markup.plain('Tom & Jerry <3 > you'), 'Tom & Jerry <3 > you')
check('tags go, in any case', markup.plain('<I>Italic</i> and <b>bold</B>'), 'Italic and bold')
check('ASS codes go', markup.plain('{\\an8}Top {\\i1}line'), 'Top line')
check('a tag it does not know is text', markup.plain('<v Bob>Hi'), '<v Bob>Hi')
check('has_markup', [markup.has_markup(t) for t in ('plain', '<i>x</i>', '{\\an8}x', 'a < b', '', None)],
      [False, True, True, False, False, False])
check('nested', styles('<i>outer <b>both</b> still</i> plain'),
      [('outer ', 'i'), ('both', 'ib'), (' still', 'i'), (' plain', '')])
check('left open runs to the end', styles('<u>under\nline'), [('under\nline', 'u')])
check('a stray closing tag is dropped', styles('stray </i>close'), [('stray close', '')])
check('font colours, by hex, short hex and name',
      styles('<font color="#FF0000">a</font><font color=#0f0>b</font><font color=\'blue\'>c</font><font face="Arial">d</font>'),
      [('a', '#ff0000'), ('b', '#00ff00'), ('c', '#0000ff'), ('d', '')])
check('ASS styles', styles('{\\i1}x{\\i0}y{\\b1\\c&H0000FF&}z{\\r}w'), [('x', 'i'), ('y', ''), ('z', 'b#ff0000'), ('w', '')])
check('alignment', [markup.alignment(t) for t in ('x', '{\\an8}x', '{\\an7}<i>x</i>', '{\\a6}x', '{\\pos(1,2)}x')], [2, 8, 7, 8, 2])

print('writing them other ways')
check('HTML for Qt', markup.to_html('<i>a & b</i>\n<font color="red">c</font>'),
      '<i>a &amp; b</i><br/><span style="color:#ff0000">c</span>')
check('HTML without colours (a shadow)', markup.to_html('<font color="red">c</font>', colors=False), 'c')
check('ASS', markup.to_ass('{\\an8}<i>a <b>b</b></i>\n<font color="#ff8000">c</font>'),
      '{\\an8}{\\i1}a {\\b1}b{\\b0}{\\i0}\\N{\\c&H0080FF&}c{\\c}')
nodes = markup.caption_nodes('<i>a</i>\nb')
check('pycaption nodes: style, text, end, break, text',
      [(n.type_, n.content if n.type_ == 1 else n.start if n.type_ == 2 else None) for n in nodes],
      [(2, True), (1, 'a'), (2, False), (3, None), (1, 'b')])

print('cut in two, both halves well formed')
session.CONFIG.setdefault('quality_check', {}).update({'reading_speed_cps': 21, 'maximum_characters_per_line': 42})
check('nested tags', markup.rebalance('<i>Hello <b>big', 'world</b> end</i>'), ('<i>Hello <b>big</b></i>', '<i><b>world</b> end</i>'))
check('colour and position carry over', markup.rebalance('{\\an8}<font color="red">Top', 'line</font>'),
      ('{\\an8}<font color="red">Top</font>', '{\\an8}<font color="red">line</font>'))
check('closed before the cut', markup.rebalance('<i>a</i> b', 'c'), ('<i>a</i> b', 'c'))
check('plain', markup.rebalance('plain', 'text'), ('plain', 'text'))

session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 3.0, 'text': '<i>Hello world</i>'}]
session.SUBTITLE['selected'] = session.SUBTITLE['segments'][0]
subtitles.slice_subtitle(selected_subtitle=session.SUBTITLE['segments'][0], position=2.0,
                         last_text='<i>Hello ', next_text='world</i>')
check('slicing a subtitle', [s['text'] for s in session.SUBTITLE['segments']], ['<i>Hello</i>', '<i>world</i>'])
session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 2.0, 'text': '<b>one two</b>'}, {'start': 3.0, 'end': 4.0, 'text': 'three'}]
subtitles.send_text_to_next_subtitle(selected_subtitle=session.SUBTITLE['segments'][0], last_text='<b>one', next_text='two</b>')
check('sending text to the next subtitle', [s['text'] for s in session.SUBTITLE['segments']], ['<b>one</b>', '<b>two</b> three'])

print('the quality check counts what is read')
long_tags = '<font color="#ff0000"><i>' + 'x' * 30 + '</i></font>'
check('tags are not characters per line', 'cpl' in quality_check.check_subtitle({'start': 0, 'end': 5, 'text': long_tags})[2], False)
check('nor characters per second', 'cps' in quality_check.check_subtitle({'start': 0, 'end': 1.5, 'text': long_tags})[2], False)

print('the preview draws them')


def render(text):
    image = QImage(640, 360, QImage.Format_ARGB32)
    image.fill(QColor('#000000'))
    painter = QPainter(image)
    painter.setFont(QFont('Montserrat', 20))
    subtitle_painter.paint(painter, QRectF(32, 18, 576, 324), text, {'backgroundbox_enabled': False, 'shadow_enabled': False})
    painter.end()
    return image


def lit_rows(image):
    rows = [y for y in range(image.height()) if any(QColor(image.pixel(x, y)).red() > 128 for x in range(0, image.width(), 2))]
    return (rows[0], rows[-1]) if rows else None


plain_image, italic_image = render('Hello there'), render('<i>Hello</i> there')
check('plain text is drawn', lit_rows(plain_image) is not None, True)
check('italic is drawn, and not as the plain', (lit_rows(italic_image) is not None, plain_image != italic_image), (True, True))
check('the same place as plain text', abs(lit_rows(plain_image)[1] - lit_rows(italic_image)[1]) <= 2, True)
check('no tag drawn as text', render('<i></i>') == render(''), True)
top = lit_rows(render('{\\an8}<b>Top</b>'))
check('{\\an8} draws at the top', top is not None and top[0] < 100, True)
coloured = render('<font color="#00ff00">green</font>')
check('a colour is drawn in it', any(QColor(coloured.pixel(x, y)).green() > 200 and QColor(coloured.pixel(x, y)).red() < 80
                                     for x in range(0, 640, 2) for y in range(200, 360, 2)), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
