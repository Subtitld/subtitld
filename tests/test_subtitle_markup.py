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
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtCore import QEventLoop, QRectF, QTimer
app = QApplication([])
from subtitld.modules import config, markup, quality_check, session, subtitle_styles, subtitles
from subtitld.interface import subtitle_renderer

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

print('Markdown')
MD_CASES = {
    'Plain text': 'Plain text',
    '<i>Italic</i> and <b>bold</b>': '*Italic* and **bold**',
    '<i>outer <b>both</b> still</i> plain': '*outer **both** still* plain',
    '<b><i>both</i></b>': '***both***',
    '<i>Hello </i>world': '*Hello* world',
    '<i>Hel</i><b>lo</b>': '*Hel***lo**',
    '<u>under</u> <s>struck</s>': '[under]{.underline} ~~struck~~',
    '<font color="#ffd700">Gold</font>\n<i>second</i>': '[Gold]{color=#ffd700}\n*second*',
    '<font color="#ff0000"><u><i>all</i></u></font>': '[*all*]{.underline color=#ff0000}',
    '<i>one\ntwo</i>': '*one\ntwo*',
    '*sigh* 5 * 3 snake_case __init__ ~~not~~ [MUSIC] a]{b}': '\\*sigh\\* 5 \\* 3 snake_case \\_\\_init\\_\\_ \\~\\~not\\~\\~ [MUSIC] a\\]{b}',
    'mid {\\fad(1,2)} code': 'mid {\\fad(1,2)} code',
    '{\\an8}Top <i>x</i>': 'Top *x*',
}
for text, written in MD_CASES.items():
    check(f'written {text!r}', markup.to_markdown(text), written)
    check(f'  and read back the same', markup.key(markup.from_markdown(written)), markup.key(markup.split_lead(text)[1]))
check('read: _x_ and __x__ too', markup.from_markdown('_it_ __bold__'), '<i>it</i> <b>bold</b>')
check('read: not inside a word', markup.from_markdown('snake_case_name'), 'snake_case_name')
check('read: a span with a style colour', markup.from_markdown('[x]{style="color: red"}'), '<font color="#ff0000">x</font>')
check('read: a span it does not know is text', markup.from_markdown('[x]{.smallcaps}'), '[x]{.smallcaps}')
check('read: unclosed is text', markup.from_markdown('**open *closed*'), '**open <i>closed</i>')
check('the same, spelt otherwise', markup.key('<i>a </i>b') == markup.key('<i>a</i> b'), True)
check('not the same', markup.key('<i>a</i>') == markup.key('a'), False)
styled, marks = markup.markdown_layout('*you **freaked** out* [x]{.underline}')
check('layout: where the styles are', [(a, b, ''.join(f for f, on in zip('ibus', st[:4]) if on)) for a, b, st in styled],
      [(1, 5, 'i'), (7, 14, 'ib'), (16, 20, 'i'), (21, 22, ''), (23, 24, 'u')])
check('layout: the syntax', marks, [(0, 1), (5, 7), (14, 16), (20, 21), (22, 23), (24, 37)])

print('the preview draws them')


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
subtitle_styles.set_value('backgroundbox_enabled', False)
subtitle_styles.set_value('shadow_enabled', False)
subtitle_styles.set_value('font_size', 60)
renderer = subtitle_renderer.SubtitleRenderer(disk_cache=None)
renderer.set_size(640, 360)


def render(text):
    """The subtitle as the preview draws it: its layer over a black frame."""
    segment = {'start': 0.0, 'end': 1.0, 'text': text}
    key = renderer._job(segment, 0.0, *renderer._style())[0]
    for _i in range(1500):
        renderer.layers([segment], 0.0)
        if key in renderer.cache:
            break
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    image = QImage(640, 360, QImage.Format_ARGB32)
    image.fill(QColor('#000000'))
    painter = QPainter(image)
    for area, layer in renderer.layers([segment], 0.0):
        painter.drawImage(QRectF(area.x() * 640, area.y() * 360, area.width() * 640, area.height() * 360), layer)
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

renderer.close()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
