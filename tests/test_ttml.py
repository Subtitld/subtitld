"""TTML and DFXP: reading the profiles in use (styles and their chains,
regions, times in frames, ticks and offsets, div offsets, agents, the old
DFXP namespace, prefixed EBU-TT-D, Apple's iTT, what pycaption wrote),
writing TTML that reads back as it was, and the export's options.

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
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, markup, session, ttml

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text):
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ()) for s in ttml.parse(text)]


HEAD = ('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" '
        'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"{attrs}>')


def doc(body, head='', attrs=''):
    return f'{HEAD.format(attrs=attrs)}<head>{head}</head><body>{body}</body></tt>'


print('styles')
STYLED = doc(
    '<div>'
    '<p begin="00:00:05.000" end="00:00:06.000">Later</p>'
    '<p begin="00:00:01.000" end="00:00:02.500">You\'re a <span style="it">jerk</span>,<br/>Celia.</p>'
    '<p begin="00:00:03.000" end="00:00:04.000" style="gold">Gold <span tts:fontWeight="bold">bold</span> and '
    '<span tts:textDecoration="underline">under</span> <span tts:textDecoration="lineThrough">struck</span></p>'
    '<p begin="00:00:07.000" end="00:00:08.000">Line one\n        continues on the same line</p>'
    '<p begin="00:00:09.000" end="00:00:10.000" style="chain">Chained</p>'
    '</div>',
    '<styling><style xml:id="it" tts:fontStyle="italic"/><style xml:id="gold" tts:color="#ffd700ff"/>'
    '<style xml:id="chain" style="it gold" tts:fontWeight="bold"/>'
    '<style xml:id="loop1" style="loop2"/><style xml:id="loop2" style="loop1"/></styling>')
check('styles, spans, line breaks, sorted', cues(STYLED), [
    (1.0, 2.5, "You're a <i>jerk</i>,\nCelia."),
    (3.0, 4.0, '<font color="#ffd700">Gold <b>bold</b> and <u>under</u> <s>struck</s></font>'),
    (5.0, 6.0, 'Later'),
    (7.0, 8.0, 'Line one continues on the same line'),
    (9.0, 10.0, '<font color="#ffd700"><i><b>Chained</b></i></font>'),
])
check('a style naming itself through another', cues(doc('<p begin="1s" end="2s" style="loop1">Loop</p>',
                                                         '<styling><style xml:id="loop1" style="loop2" tts:fontStyle="oblique"/>'
                                                         '<style xml:id="loop2" style="loop1"/></styling>')),
      [(1.0, 2.0, '<i>Loop</i>')])
check('inherited from body and div; white is no colour', cues(doc(
    '<div tts:fontStyle="italic"><p begin="1s" end="2s">Body <span tts:fontStyle="normal">and div</span></p></div>',
    attrs='').replace('<body>', '<body tts:color="white">')), [(1.0, 2.0, '<i>Body </i>and div')])
check('colours as TTML writes them', [c[2] for c in cues(doc(
    '<p begin="1s" end="2s" tts:color="rgb(255, 0, 0)">a</p><p begin="2s" end="3s" tts:color="rgba(0,0,255,128)">b</p>'
    '<p begin="3s" end="4s" tts:color="yellow">c</p><p begin="4s" end="5s" tts:color="transparent">d</p>'))],
      ['<font color="#ff0000">a</font>', '<font color="#0000ff">b</font>', '<font color="#ffff00">c</font>', 'd'])

print('whitespace')
check('spaces collapsed, none around a break, no empty line', cues(doc(
    '<p begin="1s" end="2s">  Spaces <br/> <br/>  around <span tts:fontStyle="italic"> breaks </span> end </p>')),
      [(1.0, 2.0, 'Spaces\naround <i>breaks </i>end')])
check('xml:space="preserve": its line ends are breaks', cues(doc(
    '<p begin="1s" end="2s" xml:space="preserve">  Pre\n   served  </p>')), [(1.0, 2.0, 'Pre\nserved')])
check('a no-break space is no whitespace; entities as HTML has them', cues(doc(
    '<p begin="1s" end="2s">A&nbsp;B &amp; Tom & Jerry &eacute; &#233;</p>')), [(1.0, 2.0, 'A B & Tom & Jerry é é')])
check('an empty paragraph is no subtitle', cues(doc('<p begin="1s" end="2s"> <br/> </p>')), [])

print('times')
check('ticks', cues(doc('<p begin="10000000t" end="25000000t">T</p>', attrs=' ttp:tickRate="10000000"')),
      [(1.0, 2.5, 'T')])
check('ticks, by the frame rate when no tick rate is given', cues(doc('<p begin="25t" end="50t">T</p>', attrs=' ttp:frameRate="25"')),
      [(1.0, 2.0, 'T')])
check('offsets and dur', cues(doc('<p begin="1.5s" end="2500ms">a</p><p begin="0.05m" dur="1s">b</p>'
                                  '<p begin="0.0833h" end="300.5s">c</p>')),
      [(1.5, 2.5, 'a'), (3.0, 4.0, 'b'), (299.88, 300.5, 'c')])
check('frames, at 24 and at 30000/1001', (
    cues(doc('<p begin="00:00:01:12" end="48f">a</p>', attrs=' ttp:frameRate="24"')),
    cues(doc('<p begin="00:00:01:15" end="00:00:02:00">b</p>', attrs=' ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001"'))),
      ([(1.5, 2.0, 'a')], [(1.501, 2.0, 'b')]))
check("a div's begin moves its paragraphs", cues(doc('<div begin="10s"><p begin="1s" end="2s">Later</p></div>')),
      [(11.0, 12.0, 'Later')])
check("a paragraph without an end ends with its div; seq follows on", cues(doc(
    '<div begin="1s" end="3s"><p>Held</p></div>').replace('<body>', '<body timeContainer="seq">').replace(
    '</div>', '</div><div dur="2s"><p>Next</p></div>')), [(1.0, 3.0, 'Held'), (3.0, 5.0, 'Next')])
check('a paragraph with no time at all is left out', cues(doc('<p>Never</p>')), [])

print('where it sits')
REGIONS = ('<layout>'
           '<region xml:id="bottom" tts:origin="10% 50%" tts:extent="80% 40%" tts:displayAlign="after"/>'
           '<region xml:id="top" tts:origin="10% 10%" tts:extent="80% 40%" tts:displayAlign="before"/>'
           '<region xml:id="topleft" tts:origin="10% 10%" tts:extent="80% 40%" tts:displayAlign="before" tts:textAlign="left"/>'
           '<region xml:id="middle" tts:origin="10% 10%" tts:extent="80% 80%" tts:displayAlign="center" tts:textAlign="end"/>'
           '<region xml:id="lowtop" tts:origin="0% 85%" tts:extent="100% 15%" tts:displayAlign="before"/>'
           '<region xml:id="cells"><style tts:origin="2c 1c" tts:extent="36c 6c"/></region>'
           '<region xml:id="pixels" tts:origin="96px 540px" tts:extent="400px 200px" tts:displayAlign="center"/>'
           '<region xml:id="bare" tts:displayAlign="before" tts:textAlign="right"/>'
           '<region xml:id="pycaption" tts:displayAlign="after" tts:textAlign="start"/>'
           '</layout>')
places = ['bottom', 'top', 'topleft', 'middle', 'lowtop', 'cells', 'pixels', 'bare', 'pycaption']
check('regions: origin, extent, displayAlign, textAlign; %, cells, px', [markup.alignment(c[2]) for c in cues(doc(
    ''.join(f'<p begin="{i}s" end="{i + 1}s" region="{name}">{name}</p>' for i, name in enumerate(places)),
    REGIONS, attrs=' tts:extent="1920px 1080px" ttp:cellResolution="40 20"'))], [2, 8, 7, 6, 2, 8, 4, 9, 2])
check("the body's region, and the paragraph's textAlign over the region's", [markup.alignment(c[2]) for c in cues(doc(
    '<p begin="1s" end="2s">a</p><p begin="2s" end="3s" tts:textAlign="right">b</p>', REGIONS).replace(
    '<body>', '<body region="top">'))], [8, 9])

print('speakers and language')
AGENTS = doc('<div><p begin="1s" end="2s" ttm:agent="thom">You\'re a jerk.</p>'
             '<p begin="3s" end="4s"><span ttm:agent="celia">Look, Thom!</span></p></div>'
             '<div ttm:agent="celia"><p begin="5s" end="6s">Hers too</p><p begin="7s" end="8s" ttm:agent="nameless">Who?</p></div>',
             '<metadata><ttm:agent xml:id="thom" type="character"><ttm:name type="full">Thom</ttm:name></ttm:agent>'
             '<ttm:agent xml:id="celia" type="character"><ttm:name type="alias">Cee</ttm:name>'
             '<ttm:name type="full">Celia</ttm:name></ttm:agent></metadata>',
             attrs=' xml:lang="pt-BR"')
check('agents are speakers, by their full names', [c[3:] for c in cues(AGENTS)], [('Thom',), ('Celia',), ('Celia',), ('nameless',)])
check('the language', ttml._document(AGENTS)[1], 'pt-BR')

print('profiles')
OLD = ('<?xml version="1.0" encoding="utf-8"?><tt xmlns="http://www.w3.org/2006/10/ttaf1" '
       'xmlns:tts="http://www.w3.org/2006/10/ttaf1#styling" xml:lang="en"><head><styling>'
       '<style id="s1" tts:fontStyle="italic"/></styling></head><body><div>'
       '<p begin="00:00:01.00" end="00:00:02.00" style="s1">Old namespace</p></div></body></tt>')
check('the old DFXP namespace, its plain id', cues(OLD), [(1.0, 2.0, '<i>Old namespace</i>')])
EBU = ('<?xml version="1.0" encoding="UTF-8"?><tt:tt xmlns:tt="http://www.w3.org/ns/ttml" '
       'xmlns:tts="http://www.w3.org/ns/ttml#styling" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
       'ttp:timeBase="media" xml:lang="en"><tt:head><tt:styling><tt:style xml:id="yellow" tts:color="#FFFF00"/>'
       '</tt:styling><tt:layout><tt:region xml:id="r" tts:origin="10% 10%" tts:extent="80% 80%" tts:displayAlign="after"/>'
       '</tt:layout></tt:head><tt:body><tt:div><tt:p xml:id="s1" region="r" begin="00:00:01.000" end="00:00:02.000">'
       '<tt:span>EBU-TT-D </tt:span><tt:span style="yellow">yellow</tt:span></tt:p></tt:div></tt:body></tt:tt>')
check('EBU-TT-D, its tt: prefix', cues(EBU), [(1.0, 2.0, 'EBU-TT-D <font color="#ffff00">yellow</font>')])
ITT = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?><tt xmlns="http://www.w3.org/ns/ttml" '
       'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:tts="http://www.w3.org/ns/ttml#styling" '
       'ttp:timeBase="smpte" ttp:frameRate="25" ttp:frameRateMultiplier="1 1" xml:lang="en"><head>'
       '<layout><region xml:id="top" tts:origin="0% 0%" tts:extent="100% 15%" tts:displayAlign="before"/>'
       '<region xml:id="bottom" tts:origin="0% 85%" tts:extent="100% 15%" tts:displayAlign="after"/></layout></head>'
       '<body region="bottom"><div><p begin="00:00:01:00" end="00:00:02:12">Apple iTT</p>'
       '<p begin="00:00:03:00" end="00:00:04:00" region="top">At the top</p></div></body></tt>')
check("Apple's iTT", cues(ITT), [(1.0, 2.48, 'Apple iTT'), (3.0, 4.0, '{\\an8}At the top')])
# As pycaption 2.3's DFXPWriter wrote it, byte for byte: a region with
# textAlign="start" on every paragraph.
PYCAPTION = ('<?xml version="1.0" encoding="utf-8"?>\n'
             '<tt xml:lang="en" xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling">\n'
             ' <head>\n'
             '  <styling>\n'
             '   <style tts:color="white" tts:fontFamily="monospace" tts:fontSize="1c" xml:id="default"/>\n'
             '  </styling>\n'
             '  <layout>\n'
             '   <region tts:displayAlign="after" tts:textAlign="start" xml:id="bottom"/>\n'
             '  </layout>\n'
             ' </head>\n'
             ' <body>\n'
             '  <div region="bottom" xml:lang="en">\n'
             '   <p begin="00:00:01.000" end="00:00:02.000" region="bottom" style="default">\n'
             '    Plain <span tts:fontStyle="italic">italic</span><br/>\n'
             '    second\n'
             '   </p>\n'
             '   <p begin="00:00:03.000" end="00:00:04.000" region="bottom" style="default">\n'
             '    Top <span tts:fontWeight="bold">bold</span>\n'
             '   </p>\n'
             '  </div>\n'
             ' </body>\n'
             '</tt>\n')
check('what pycaption wrote (and Subtitld with it): centred, not at the left',
      cues(PYCAPTION), [(1.0, 2.0, 'Plain <i>italic</i>\nsecond'), (3.0, 4.0, 'Top <b>bold</b>')])
try:
    ttml.parse('<usf><subtitle/></usf>')
    check('not TTML: refused', False, True)
except ValueError as error:
    check('not TTML: refused', str(error), 'Not a TTML document: it is <usf>, not <tt>')

print('writing')
segments = [
    {'start': 5, 'end': 6, 'text': '{\\an7}Top left <u><s>both</s></u>'},
    {'start': 1, 'end': 2.5, 'text': "You're a <i>jerk</i>,\nCelia.", 'speaker': 'Thom'},
    {'start': 3, 'end': 4, 'text': '{\\an8}<font color="#ffd700">Gold <b>bold</b> & <u>under</u> <s>struck</s></font>',
     'speaker': 'Celia'},
    {'start': 6, 'end': 7, 'text': '{\\an5}Middle'},
    {'start': 7, 'end': 8, 'text': '{\\an3}Bottom right < >', 'speaker': 'Thom'},
    {'start': 8, 'end': 9, 'text': '  <i> </i> '},
]
written = ttml.write(segments, 'pt-br', agents=True)
check('TTML 1, media time, the language as BCP 47 writes it',
      written.splitlines()[1].endswith('ttp:timeBase="media" xml:lang="pt-BR">'), True)
check('the subtitles in order, times as HH:MM:SS.mmm, empty ones left out',
      [line.split('"')[1] for line in written.splitlines() if '<p ' in line],
      ['00:00:01.000', '00:00:03.000', '00:00:05.000', '00:00:06.000', '00:00:07.000'])
check('formatting as spans, nested as the tags are; breaks as <br/>',
      [line.split('>', 1)[1].rsplit('</p>', 1)[0] for line in written.splitlines() if '<p ' in line][:3], [
          'You\'re a <span tts:fontStyle="italic">jerk</span>,<br/>Celia.',
          '<span tts:color="#ffd700">Gold <span tts:fontWeight="bold">bold</span> &amp; '
          '<span tts:textDecoration="underline">under</span> <span tts:textDecoration="lineThrough">struck</span></span>',
          'Top left <span tts:textDecoration="underline lineThrough">both</span>'])
check('a region for each place used, top first', [line.split('"')[1] for line in written.splitlines() if '<region ' in line],
      ['top-left', 'top', 'middle', 'bottom', 'bottom-right'])
check('each speaker an agent once', (written.count('<ttm:agent '), written.count('ttm:agent="speaker1"')), (2, 2))
check('read back as written', [(s['start'], s['text'], s.get('speaker')) for s in ttml.parse(written)],
      [(s['start'], s['text'], s.get('speaker')) for s in sorted(segments[:5], key=lambda s: s['start'])])
check('without agents: none', 'agent' in ttml.write(segments), False)
clean = ttml.write(segments)
check('clean: white with a black outline', 'tts:color="white" tts:textOutline="black 5%"' in clean, True)
preview = ttml.write(segments, 'en-us', look='preview',
                     preview={'font_family': 'Montserrat', 'color': '#ffffd700', 'backgroundbox_color': '#80000000'})
check("the preview's look: its font and colour, its box behind the text", (
    'tts:fontFamily="Montserrat"' in preview, 'tts:color="#ffd700"' in preview,
    '<style xml:id="box" tts:backgroundColor="#00000080"/>' in preview, preview.count('<span style="box">'), 'textOutline' in preview),
      (True, True, True, 5, False))
check('read back, the box changes nothing', [s['text'] for s in ttml.parse(preview)][1],
      '{\\an8}<font color="#ffd700">Gold <b>bold</b> & <u>under</u> <s>struck</s></font>')
check('without the box: none', 'box' in ttml.write(segments, look='preview', preview={'backgroundbox_enabled': False}), False)

print('the profile a file declares')
check('nothing declared by default, as Subtitld has always written it',
      ('ttp:profile' in clean, 'contentProfiles' in clean, 'cellResolution' in clean), (False, False, False))
imsc1 = ttml.write(segments, 'en', profile='imsc1')
check("IMSC 1.0.1: its Text Profile in ttp:profile, the grid written down",
      (f'ttp:profile="{ttml._IMSC1_TEXT}"' in imsc1, 'ttp:cellResolution="32 15"' in imsc1), (True, True))
imsc11 = ttml.write(segments, 'en', profile='imsc1.1')
check('IMSC 1.1: in ttp:contentProfiles, where 1.1 moved it',
      (f'ttp:contentProfiles="{ttml._IMSC11_TEXT}"' in imsc11, 'ttp:profile=' in imsc11), (True, False))
ebu = ttml.write(segments, 'en', profile='ebu-tt-d')
check('EBU-TT-D: its URN in metadata, its namespace, and no contentProfiles', (
    f'<ebuttm:conformsToStandard>{ttml._EBUTTD_STANDARD}</ebuttm:conformsToStandard>' in ebu,
    f'xmlns:ebuttm="{ttml._EBUTTM_NS}"' in ebu, 'contentProfiles' in ebu), (True, True, False))
check('agents still written beside it', ttml.write(segments, 'en', agents=True, profile='ebu-tt-d').count('<ttm:agent '), 2)
check('an unknown profile is plain TTML 1', 'Profiles' in ttml.write(segments, 'en', profile='nonsense'), False)
check('all of them read back the same', [len(ttml.parse(written)) for written in (imsc1, imsc11, ebu)], [5, 5, 5])

japanese = [{'start': 1, 'end': 2, 'text': '<i>こんにちは</i>'}]
ja = ttml.write(japanese, 'ja', profile='imsc1.1')
check('Japanese in an IMSC profile: italics as a shear, no fontStyle',
      (f'tts:shear="{ttml._SHEAR}"' in ja, 'fontStyle' in ja), (True, False))
check('and they read back as italics', [s['text'] for s in ttml.parse(ja)], ['<i>こんにちは</i>'])
check('English keeps fontStyle; so does plain TTML 1 in Japanese',
      ('tts:fontStyle="italic"' in ttml.write(japanese, 'en', profile='imsc1.1'),
       'tts:fontStyle="italic"' in ttml.write(japanese, 'ja')), (True, True))
check('a shear read on its own is italics',
      cues(doc('<p begin="1s" end="2s"><span tts:shear="16.67%">Slanted</span></p>')), [(1.0, 2.0, '<i>Slanted</i>')])
check('a shear of zero stands the text up again',
      cues(doc('<p begin="1s" end="2s" tts:shear="16.67%">A<span tts:shear="0%">B</span></p>')), [(1.0, 2.0, '<i>A</i>B')])

print('ruby, the reading above the text')
RUBY = '<ruby>漢字<rt>かんじ</rt></ruby>を読む'
ruby_written = ttml.write([{'start': 1, 'end': 2, 'text': RUBY}], 'ja', profile='imsc1.1')
check('written as IMSC writes one: a container holding the base and its text',
      [line.split('>', 1)[1].rsplit('</p>', 1)[0] for line in ruby_written.splitlines() if '<p ' in line],
      ['<span tts:ruby="container"><span tts:ruby="base">漢字</span>'
       '<span tts:ruby="text">かんじ</span></span>を読む'])
check('and read back as it was', [s['text'] for s in ttml.parse(ruby_written)], [RUBY])
check('the containers of a file written elsewhere', cues(doc(
    '<p begin="1s" end="2s"><span tts:ruby="container"><span tts:ruby="base">東京</span>'
    '<span tts:ruby="text">とうきょう</span></span>へ</p>')),
      [(1.0, 2.0, '<ruby>東京<rt>とうきょう</rt></ruby>へ')])
check('a delimiter is punctuation a player draws, not words of the subtitle', cues(doc(
    '<p begin="1s" end="2s"><span tts:ruby="container"><span tts:ruby="base">A</span>'
    '<span tts:ruby="delimiter">(</span><span tts:ruby="text">a</span>'
    '<span tts:ruby="delimiter">)</span></span></p>')),
      [(1.0, 2.0, '<ruby>A<rt>a</rt></ruby>')])
check('italics inside a ruby, and the shear with them',
      '<span tts:ruby="base"><span tts:shear="16.67%">A</span></span>' in
      ttml.write([{'start': 1, 'end': 2, 'text': '<ruby><i>A</i><rt>a</rt></ruby>'}], 'ja', profile='imsc1.1'), True)
check('a file with no ruby is written exactly as before', ttml.write(segments, 'pt-br', agents=True), written)

print('placed more finely than the nine places')
from subtitld.modules import position                                    # noqa: E402
placed = [{'start': 1, 'end': 2, 'text': 'Plain'},
          {'start': 3, 'end': 4, 'text': '{\\an8}High', 'position': {'v': '5%'}},
          {'start': 5, 'end': 6, 'text': 'Lifted', 'position': {'v': '30%'}},
          {'start': 7, 'end': 8, 'text': '{\\an4}Side', 'position': {'h': '3%'}}]
placed_written = ttml.write(placed, 'en')
check('a region for each place, the plain one as it always was',
      [line.split('"')[1] + ' ' + line.split('tts:origin="')[1].split('"')[0] + ' ' + line.split('tts:extent="')[1].split('"')[0]
       for line in placed_written.splitlines() if '<region ' in line],
      ['top 10% 5% 80% 85%', 'left 3% 10% 87% 80%', 'bottom 10% 10% 80% 80%', 'bottom-2 10% 10% 80% 60%'])
check('and every subtitle comes back exactly as it went in',
      [(s['text'], s.get('position')) for s in ttml.parse(placed_written)],
      [(s['text'], s.get('position')) for s in placed])
check('a file that places its subtitles itself keeps that', [(s['text'], s.get('position')) for s in ttml.parse(doc(
    '<p begin="1s" end="2s" region="r">Over here</p>',
    head='<layout><region xml:id="r" tts:origin="30% 20%" tts:extent="50% 30%" '
         'tts:displayAlign="after" tts:textAlign="center"/></layout>'))],
      [('Over here', {'h': '5%', 'v': '50%'})])
check('a region where a subtitle would sit anyway says nothing', [s.get('position') for s in ttml.parse(doc(
    '<p begin="1s" end="2s" region="r">Ordinary</p>',
    head='<layout><region xml:id="r" tts:origin="10% 10%" tts:extent="80% 80%" '
         'tts:displayAlign="after" tts:textAlign="center"/></layout>'))], [None])
check('TTML has no rotation, so a turned subtitle is written flat',
      'rotate' in ttml.write([{'start': 1, 'end': 2, 'text': 'Turned', 'position': {'rotate': {'z': 45}}}], 'en'), False)

print('checking a file against its profile')
check('what Subtitld writes passes its own check',
      (ttml.check(imsc1, 'imsc1'), ttml.check(imsc11, 'imsc1.1'), ttml.check(ebu, 'ebu-tt-d')), ([], [], []))
check('the wrong declaration is found', ttml.check(imsc1, 'imsc1.1'),
      [f"ttp:contentProfiles does not name IMSC 1.1's Text Profile ({ttml._IMSC11_TEXT})"])
check('a file with no language', ttml.check(doc('<p begin="1s" end="2s">Hi</p>'), 'ttml1')[0].startswith('<tt> has no xml:lang'), True)
check('a region a subtitle names but the file has not',
      ttml.check(doc('<p begin="1s" end="2s" region="nope">Hi</p>', attrs=' xml:lang="en"'), 'ttml1'),
      ['Subtitles name regions the file does not have: nope'])
image = doc('<p begin="1s" end="2s" smpte:backgroundImage="card.png"/>',
            attrs=' xml:lang="en" xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt"')
check('pictures in a text profile', ttml.check(image, 'imsc1.1')[-1].startswith('1 element(s) carry smpte:backgroundImage'), True)
check('EBU-TT-D with a profile attribute it may not have',
      ttml.check(doc('<p begin="1s" end="2s">Hi</p>', attrs=' xml:lang="en" ttp:contentProfiles="x"'), 'ebu-tt-d'),
      ['No ebuttm:conformsToStandard: EBU-TT-D is declared in metadata, not in a ttp: attribute',
       'ttp:contentProfiles is prohibited in EBU-TT-D'])
check('a cell grid that is not two numbers',
      ttml.check(doc('<p begin="1s" end="2s">Hi</p>', attrs=' xml:lang="en" ttp:cellResolution="32"'), 'ttml1'),
      ["ttp:cellResolution is '32': it is two whole numbers above zero"])
check('text that is not XML at all', ttml.check('not xml', 'ttml1')[0].startswith('Not XML that can be read'), True)
check('XML that is not TTML', ttml.check('<html><body/></html>', 'ttml1'), ['The document is <html>, not <tt>'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
for name, text, encoding in (('in.ttml', AGENTS, 'utf-8'), ('in.dfxp', OLD, 'utf-8'), ('in.itt', ITT, 'utf-8'),
                             ('in.xml', EBU, 'utf-8')):
    (folder / name).write_text(text, encoding=encoding)
session.SUBTITLE['language'] = 'en-us'
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.ttml'))
check('opens, its language for the project', (len(opened), fmt, opened[0].get('speaker'), session.SUBTITLE['language']),
      (4, 'TTML', 'Thom', 'pt-br'))
check('.dfxp opens as DFXP, .itt and .xml as TTML', [file_io.process_subtitles_file(str(folder / name))[1]
                                                    for name in ('in.dfxp', 'in.itt', 'in.xml')], ['DFXP', 'TTML', 'TTML'])
check('a language alone, as Subtitld names it', [file_io._known_language(tag) for tag in ('en', 'fr', 'pt_BR', 'xx', '')],
      ['en-us', 'fr-fr', 'pt-br', '', ''])
latin = '<?xml version="1.0" encoding="ISO-8859-1"?>' + doc('<p begin="1s" end="2s">Ação é café</p>')
(folder / 'latin.ttml').write_bytes(latin.encode('latin-1'))
check('the encoding the declaration names', file_io.process_subtitles_file(str(folder / 'latin.ttml'))[0][0]['text'], 'Ação é café')
(folder / 'broken.ttml').write_text(doc('<p begin="1s" end="2s">Unclosed</div>'), encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'broken.ttml'))
    check('broken XML: a message, not a crash', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('broken XML: a message, not a crash', str(error).startswith('Line 1, column '), True)
session.SUBTITLE['segments'] = segments
session.SUBTITLE['language'] = 'pt-br'
session.SUBTITLE['translations'] = {}
session.CONFIG['subtitle_style'] = {'settings': {'subtitld.classic': {'font_family': 'Ubuntu', 'backgroundbox_enabled': False}}}
for fmt, name in (('TTML', 'out.ttml'), ('DFXP', 'out.dfxp')):
    session.FORMAT['options'] = {'speakers': 'voice', 'look': 'preview'}
    file_io.save_file(str(folder / name), fmt)
    saved = (folder / name).read_text(encoding='utf-8')
    check(f'exported as {fmt}: agents, the look, the language', (
        saved.count('<ttm:agent '), 'tts:fontFamily="Ubuntu"' in saved, 'xml:lang="pt-BR"' in saved,
        len(ttml.parse(saved))), (2, True, True, 5))
session.FORMAT['options'] = {'speakers': 'prefix'}
file_io.save_file(str(folder / 'prefix.ttml'), 'TTML')
check('names before the text instead', [s['text'] for s in ttml.parse((folder / 'prefix.ttml').read_text(encoding='utf-8'))][:1],
      ["Thom: You're a <i>jerk</i>,\nCelia."])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()


def offered():
    combo = panel.speakers_combo.combobox
    return [combo.itemText(i) for i in range(combo.count())][-1], combo.currentData()


for fmt in ('TTML', 'DFXP'):
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    panel.speakers_combo.combobox.setCurrentIndex(2)
    check(f'{fmt}: agents and the look offered', (offered(), panel.ass_options.isHidden(),
                                                   panel.look_combo.combobox.itemText(1).endswith('(font, colour, box)')),
          (('As agents (ttm:agent)', 'voice'), False, True))
panel.look_combo.combobox.setCurrentIndex(1)
check('chosen', {key: panel.get_config()['options'].get(key) for key in ('speakers', 'look')}, {'speakers': 'voice', 'look': 'preview'})
panel.format_combo.setCurrentText('ASS')
panel._refresh_options()
check('ASS: its own words for the look', panel.look_combo.combobox.itemText(1).endswith('(font, colour, shadow, box)'), True)

print('a background behind a run')
_boxed = [{'start': 1.0, 'end': 2.0,
           'text': 'Plain <font color="#ffd700" background="#000000cc">on a box</font> again'}]
_written = ttml.write(_boxed, 'en')
check('written as tts:backgroundColor on the span that carries the colour',
      'tts:color="#ffd700" tts:backgroundColor="#000000cc"' in _written, True)
check('read back as it went in', [(s['start'], s['text']) for s in ttml.parse(_written)],
      [(1.0, 'Plain <font color="#ffd700" background="#000000cc">on a box</font> again')])
check('a background on every run is the subtitle\'s own box, and no run\'s',
      [s['text'] for s in ttml.parse(
'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"><body><div>'
          '<p begin="1s" end="2s"><span tts:backgroundColor="#55000000">All of it</span></p>'
          '</div></body></tt>')],
      ['All of it'])
check('one on only some of them is theirs',
      [s['text'] for s in ttml.parse(
'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"><body><div>'
          '<p begin="1s" end="2s">Plain <span tts:backgroundColor="#ff0000">red box</span></p>'
          '</div></body></tt>')],
      ['Plain <font background="#ff0000">red box</font>'])
check('rgba() with its opacity, as TTML may write one',
      [s['text'] for s in ttml.parse(
'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"><body><div>'
          '<p begin="1s" end="2s">Plain <span tts:backgroundColor="rgba(255, 0, 0, 128)">half</span></p>'
          '</div></body></tt>')],
      ['Plain <font background="#ff000080">half</font>'])


print('a size and an edge')
_drawn = [{'start': 1.0, 'end': 2.0,
           'text': 'Plain <font size="80%" edge="uniform" edge-color="#ff0000">small, outlined</font>'}]
_written = ttml.write(_drawn, 'en')
check('written as TTML says them',
      ('tts:fontSize="80%"' in _written, 'tts:textOutline="#ff0000 4%"' in _written), (True, True))
check('read back, the outline as the edge TTML can draw',
      [s['text'] for s in ttml.parse(_written)],
      ['Plain <font size="80%" edge="uniform" edge-color="#ff0000">small, outlined</font>'])
check('the outline every subtitle is drawn with is no run\'s own edge',
      [s['text'] for s in ttml.parse(ttml.write([{'start': 1.0, 'end': 2.0, 'text': 'Plain'}], 'en'))],
      ['Plain'])
check('a size given across and down takes the one that is a proportion',
      [s['text'] for s in ttml.parse(
          '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"><body><div>'
          '<p begin="1s" end="2s">Plain <span tts:fontSize="100% 80%">small</span></p>'
          '</div></body></tt>')],
      ['Plain <font size="80%">small</font>'])


print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
