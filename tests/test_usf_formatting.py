"""Formatting in USF as its specification (1.1) writes it: inline <i>, <b>,
<u> and <font color>, <br/> between lines, a reading as <ruby> and <rt>,
and the position as the <text> element's "alignment"; read back into the
tags Subtitld keeps.

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

print('placed more finely than the nine places')
from subtitld.modules import position                                    # noqa: E402
placed = {'start': 0, 'end': 1, 'text': '{\\an8}Up', 'speaker': 'A',
          'position': {'h': '10%', 'v': '20%', 'relative_to': 'window', 'rotate': {'z': 45}}}
written = USFWriter().write([placed], speakers={}, language='en-us')
check('USF writes its own <position> attributes beside the alignment',
      sorted(part for part in written.split('<text ')[1].split('>')[0].split() if '=' in part),
      ['alignment="TopCenter"', 'horizontal-margin="10%"', 'relative-to="Window"',
       'rotate-z="45"', 'speaker="A"', 'vertical-margin="20%"'])
read_place = USFReader().read(written)[0]
check('and reads them back as they were', (read_place['text'], read_place.get('position')),
      ('{\\an8}Up', {'h': '10%', 'v': '20%', 'relative_to': 'window', 'rotate': {'z': 45.0}}))
plain_usf = USFWriter().write([{'start': 0, 'end': 1, 'text': 'Plain', 'speaker': 'A'}], speakers={}, language='en-us')
check('a subtitle that says nothing adds nothing to the file',
      ('margin' in plain_usf or 'rotate' in plain_usf or 'relative-to' in plain_usf), False)
check('and reads back with no position at all', 'position' in USFReader().read(plain_usf)[0], False)
styled = plain_usf.replace('<styles/>',
                           '<styles><style name="Narrator"><position alignment="TopCenter" vertical-margin="15%"/>'
                           '</style></styles>').replace('<text ', '<text style="Narrator" ')
check("a style's position places a text that uses it",
      [(s['text'], s.get('position')) for s in USFReader().read(styled)], [('{\\an8}Plain', {'v': '15%'})])

print('a reading above the text')
readings = [
    '<ruby>東京<rt>とうきょう</rt></ruby> is big',
    'A <ruby><i>漢字</i><rt>かんじ</rt></ruby> here',
    '<ruby>東<rt>と</rt></ruby><ruby>京<rt>きょう</rt></ruby>',
    '<ruby>東京</ruby> no reading',
]
content, elements = text_elements(readings)
check('a ruby as <ruby> and <rt>, as XHTML writes one', elements[0],
      '<text speaker="A"><ruby>東京<rt>とうきょう</rt></ruby> is big</text>')
check('the formatting inside it kept, and closed before the ruby ends', elements[1],
      '<text speaker="A">A <ruby><i>漢字</i><rt>かんじ</rt></ruby> here</text>')
check('one ruby for each reading', elements[2],
      '<text speaker="A"><ruby>東<rt>と</rt></ruby><ruby>京<rt>きょう</rt></ruby></text>')
check('a base with no reading is the text it is', elements[3], '<text speaker="A">東京 no reading</text>')
check('read back, each reading still with what it reads', read_back(content), [
    '<ruby>東京<rt>とうきょう</rt></ruby> is big',
    'A <ruby><i>漢字</i><rt>かんじ</rt></ruby> here',
    '<ruby>東<rt>と</rt></ruby><ruby>京<rt>きょう</rt></ruby>',
    '東京 no reading',
])

print('forced: shown with subtitles off')
forced_usf = USFWriter().write([{'start': 0, 'end': 1, 'text': 'A sign', 'speaker': 'A', 'forced': True},
                                {'start': 2, 'end': 3, 'text': 'Ordinary', 'speaker': 'A'}],
                               speakers={}, language='en-us')
check("on the subtitle, in Subtitld's namespace, since USF has no word for it",
      ('subtitldcc:forced="true"' in forced_usf, forced_usf.count('subtitldcc:forced'),
       'xmlns:subtitldcc' in forced_usf), (True, 1, True))
check('read back as it was', [(s['text'], s.get('forced')) for s in USFReader().read(forced_usf)],
      [('A sign', True), ('Ordinary', None)])
check('a project with nothing forced says nothing',
      'forced' in USFWriter().write([{'start': 0, 'end': 1, 'text': 'Plain', 'speaker': 'A'}],
                                    speakers={}, language='en-us'), False)

print('hidden: what the file says a player should not draw')
hidden_usf = USFWriter().write([{'start': 0, 'end': 1, 'text': 'Not for the viewer', 'speaker': 'A', 'hidden': True},
                                {'start': 2, 'end': 3, 'text': 'Ordinary', 'speaker': 'A'}],
                               speakers={}, language='en-us')
check("on the subtitle, in Subtitld's namespace, as forced is",
      ('subtitldcc:hidden="true"' in hidden_usf, hidden_usf.count('subtitldcc:hidden'),
       'xmlns:subtitldcc' in hidden_usf), (True, 1, True))
check('read back as it was', [(s['text'], s.get('hidden')) for s in USFReader().read(hidden_usf)],
      [('Not for the viewer', True), ('Ordinary', None)])
check('a project with nothing hidden says nothing',
      'hidden' in USFWriter().write([{'start': 0, 'end': 1, 'text': 'Plain', 'speaker': 'A'}],
                                    speakers={}, language='en-us'), False)

print('what the project says about itself')
told = USFWriter().write([{'start': 0, 'end': 1, 'text': 'Line', 'speaker': 'A'}], speakers={}, language='en-us',
                         metadata={'title': 'The Trial', 'description': 'For the restored print',
                                   'copyright': '© 2026 Someone'})
check('a description is USF\'s own <comment>; a copyright is not USF\'s, so it is Subtitld\'s',
      ('<comment>For the restored print</comment>' in told,
       '<subtitldcc:copyright>© 2026 Someone</subtitldcc:copyright>' in told), (True, True))
reader = USFReader()
reader.read(told)
check('read back, beside the title USF has always had',
      {key: reader.metadata[key] for key in ('title', 'description', 'copyright')},
      {'title': 'The Trial', 'description': 'For the restored print', 'copyright': '© 2026 Someone'})
check('a project told nothing says nothing of the two',
      [word in USFWriter().write([{'start': 0, 'end': 1, 'text': 'Line', 'speaker': 'A'}],
                                 speakers={}, language='en-us', metadata={'title': 'Bare'})
       for word in ('comment', 'copyright')], [False, False])

print('which way the writing runs, and what is forced inside it')
bidi_usf = USFWriter().write([{'start': 0, 'end': 1, 'text': 'مرحبا', 'speaker': 'A',
                               'direction': 'rtl', 'bidi': 'override'},
                              {'start': 2, 'end': 3, 'text': 'Ordinary', 'speaker': 'A'}],
                             speakers={}, language='ar')
check('both on the subtitle, in Subtitld\'s namespace',
      ('subtitldcc:direction="rtl"' in bidi_usf, 'subtitldcc:bidi="override"' in bidi_usf), (True, True))
check('read back as they were',
      [(s.get('direction'), s.get('bidi')) for s in USFReader().read(bidi_usf)],
      [('rtl', 'override'), (None, None)])
check('a project that says neither stays as it was',
      ('direction' in USFWriter().write([{'start': 0, 'end': 1, 'text': 'Plain', 'speaker': 'A'}],
                                        speakers={}, language='en-us')), False)

print('the look the subtitle\'s own file gave it')
LOOK = {'size': 10.0, 'background': '#c8000000', 'padding': [1.0, 2.0, 1.0, 2.0],
        'outline': {'color': '#ff000000', 'width': 0.5}, 'shadow': [{'x': 0.2, 'y': 0.2}]}
look_usf = USFWriter().write([{'start': 0, 'end': 1, 'text': 'Styled', 'speaker': 'A', 'look': LOOK},
                              {'start': 2, 'end': 3, 'text': 'Plain', 'speaker': 'A'}],
                             speakers={}, language='en-us')
check('on the subtitle, as JSON, since USF has no word for most of it',
      ('subtitldcc:look=' in look_usf, look_usf.count('subtitldcc:look'), 'xmlns:subtitldcc' in look_usf),
      (True, 1, True))
check('read back exactly, shadows and sides and all',
      [(s['text'], s.get('look')) for s in USFReader().read(look_usf)], [('Styled', LOOK), ('Plain', None)])
check('a project nothing styled says nothing',
      'look' in USFWriter().write([{'start': 0, 'end': 1, 'text': 'Plain', 'speaker': 'A'}],
                                  speakers={}, language='en-us'), False)
check('a look that will not parse is no reason to lose the subtitle',
      [s['text'] for s in USFReader().read(look_usf.replace('{&quot;background', '{oops'))], ['Styled', 'Plain'])

print('a background behind a run')
_boxed = {'start': 0, 'end': 1, 'speaker': 'A',
          'text': 'Plain <font color="#ffd700" background="#000000cc">on a box</font>'}
_written = USFWriter().write([_boxed], speakers={}, language='en-us')
check('written as USF writes one: back-color, the alpha first',
      'back-color="#cc000000"' in _written, True)
check('read back as it went in', USFReader().read(_written)[0]['text'], _boxed['text'])
check('an opaque one needs no alpha of its own to come back',
      USFReader().read(USFWriter().write(
          [{'start': 0, 'end': 1, 'speaker': 'A', 'text': '<font background="#00ff00">Green box</font>'}],
          speakers={}, language='en-us'))[0]['text'],
      '<font background="#00ff00">Green box</font>')

print('what a run is drawn in, beyond its colours')
# A caption's pen and TTML's spans say all of these, and the project file
# is where they have to survive a save; USF has no word for most of them.
_pens = [
    '<font size="80%">small</font> and normal',
    '<font edge="uniform" edge-color="#ff0000">outlined</font>',
    '<font face="Courier New">typed</font>',
    '<font kind="sound-effect">[door slams]</font>',
    'H<sub>2</sub>O and x<sup>2</sup>',
    '<blink>Breaking</blink> news',
    '<font color="#ffd700" background="#000000cc" size="80%" kind="lyrics">all at once</font> <i>and</i> plain',
]
_written = USFWriter().write([{'start': i, 'end': i + 0.5, 'speaker': 'A', 'text': t} for i, t in enumerate(_pens)],
                             speakers={}, language='en-us')
check('read back exactly as they went in', read_back(_written), _pens)
check('in Subtitld\'s namespace, which the file declares',
      ('subtitldcc:size="80%"' in _written, 'subtitldcc:edge-color="#ff0000"' in _written,
       'xmlns:subtitldcc' in _written), (True, True, True))
check('raised, lowered and flashing text as the elements markup names',
      ('<sub>2</sub>' in _written, '<sup>2</sup>' in _written, '<blink>Breaking</blink>' in _written),
      (True, True, True))
_annotated = ['彼は<font emphasis="filled sesame">本当</font>に',
              '<ruby position="after" align="space-around">東京<rt>とうきょう</rt></ruby>へ']
_written = USFWriter().write([{'start': i, 'end': i + 0.5, 'speaker': 'A', 'text': t} for i, t in enumerate(_annotated)],
                             speakers={}, language='ja')
check('emphasis marks and what a ruby says about its reading, kept',
      (read_back(_written), 'subtitldcc:emphasis="filled sesame"' in _written,
       '<ruby subtitldcc:align="space-around" subtitldcc:position="after">' in _written),
      (_annotated, True, True))
check('a project with only ordinary formatting declares no namespace',
      'xmlns:subtitldcc' in USFWriter().write([{'start': 0, 'end': 1, 'speaker': 'A', 'text': '<i>plain</i>'}],
                                              speakers={}, language='en-us'), False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
