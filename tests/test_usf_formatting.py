"""Formatting in USF as its specification (1.1) writes it: inline <i>, <b>,
<u> and <font color>, <br/> between lines, and the position as the <text>
element's "alignment"; read back into the tags Subtitld keeps.

Pure module — no Qt.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules.usf import USFReader, USFWriter

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def text_elements(texts):
    content = USFWriter().write([{'start': i, 'end': i + 0.5, 'text': t, 'speaker': 'A'} for i, t in enumerate(texts)],
                                speakers={}, language='en-us')
    return content, re.findall(r'<text[^>]*>.*?</text>|<text[^>]*/>', content, re.S)


def read_back(content):
    return [subtitle['text'] for subtitle in USFReader().read(content)]


print('written as the specification says')
texts = [
    '{\\an8}<font color="#ffd700">Why</font>\n<i>you\'re <b>freaked</b> out</i>',
    '<i>Hello </i>world & <3 <u>under</u> <s>struck</s>',
    '{\\fad(100,200)}{\\an7}Fade, top left',
    'Plain\ntwo lines',
]
content, elements = text_elements(texts)
check('inline elements, the position as "alignment"', elements[0],
      '<text alignment="TopCenter" speaker="A"><font color="#ffd700">Why</font><br/><i>you\'re <b>freaked</b> out</i></text>')
check('text escaped as text, strikeout as <s>', elements[1],
      '<text speaker="A"><i>Hello </i>world &amp; &lt;3 <u>under</u> <s>struck</s></text>')
check('another code kept as written', elements[2], '<text alignment="TopLeft" speaker="A">{\\fad(100,200)}Fade, top left</text>')
check('lines', elements[3], '<text speaker="A">Plain<br/>two lines</text>')

print('read back as written')
check('the same text', read_back(content), [
    '{\\an8}<font color="#ffd700">Why</font>\n<i>you\'re <b>freaked</b> out</i>',
    '<i>Hello </i>world & <3 <u>under</u> <s>struck</s>',
    '{\\an7}{\\fad(100,200)}Fade, top left',
    'Plain\ntwo lines',
])

print('a file another tool wrote')
other = '''<?xml version="1.0" encoding="UTF-8"?>
<USFSubtitles version="1.1">
  <subtitles>
    <subtitle start="00:00:01.000" stop="00:00:02.000"><text alignment="TopRight">A <i>spaced </i>word</text></subtitle>
    <subtitle start="00:00:03.000" stop="00:00:04.000"><text><font color="#7FFF0000" face="Arial">red, half alpha</font> <font face="Arial">no colour</font></text></subtitle>
    <subtitle start="00:00:05.000" stop="00:00:06.000"><text alignment="BottomCenter"><b>Bottom</b><br/><font color="red">named</font></text></subtitle>
  </subtitles>
</USFSubtitles>'''
check('spaces at the edge of a tag kept, the position read', read_back(other), [
    '{\\an9}A <i>spaced </i>word',
    '<font color="#ff0000">red, half alpha</font> no colour',
    '<b>Bottom</b>\n<font color="#ff0000">named</font>',
])

print('a project an older Subtitld wrote (tags as text)')
older = USFWriter().write([{'start': 0, 'end': 1, 'text': 'x', 'speaker': 'A'}], speakers={}, language='en-us').replace(
    '>x</text>', '>&lt;i&gt;Italic&lt;/i&gt; still</text>')
check('reads as tags', read_back(older), ['<i>Italic</i> still'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
