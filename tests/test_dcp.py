"""DCP subtitles: reading a cinema's Interop (CineCanvas) and SMPTE ST 428-7
files (times in ticks, milliseconds and frames, the reel's start, lines and
where they sit, fonts, rubies, columns), writing both and reading them back,
opening and saving through file_io, picture subtitles for the OCR dialog,
and the export dialog's options.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import dcp, file_io, picture_ocr, session
from subtitld.modules.addons.provider import OCRProvider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data):
    return [(s['start'], s['end'], s['text']) for s in dcp.parse(data)[0]]


# An Interop file as DCP-o-matic and the subtitling houses write them: a
# font loaded, one <Font> around the reel, times in ticks of 1/250 s.
INTEROP = '''<?xml version="1.0" encoding="UTF-8"?>
<DCSubtitle Version="1.0">
  <SubtitleID>40950d85-63eb-4ee2-b1e8-45c126601b94</SubtitleID>
  <MovieTitle>Tears of Steel</MovieTitle>
  <ReelNumber>1</ReelNumber>
  <Language>English</Language>
  <LoadFont Id="theFont" URI="arial.ttf"/>
  <Font Id="theFont" Size="42" Color="FFFFFFFF" Effect="border" EffectColor="FF000000" Italic="no" Weight="normal">
    <Subtitle SpotNumber="1" TimeIn="00:00:05:198" TimeOut="00:00:07:115" FadeUpTime="20" FadeDownTime="20">
      <Text VAlign="bottom" VPosition="10" HAlign="center" HPosition="0">and I could tell you</Text>
      <Text VAlign="bottom" VPosition="16.4" HAlign="center" HPosition="0">You're a <Font Italic="yes">jerk,</Font> Thom,</Text>
    </Subtitle>
    <Subtitle SpotNumber="2" TimeIn="00:01:02:000" TimeOut="00:01:04:125" FadeUpTime="0" FadeDownTime="0">
      <Font Color="FFFFFF00" Weight="bold">
        <Text VAlign="top" VPosition="10" HAlign="left" HPosition="10">Gold at the top left</Text>
      </Font>
    </Subtitle>
    <Subtitle SpotNumber="3" TimeIn="01:00:00:000" TimeOut="01:00:02:249">
      <Text VAlign="bottom" VPosition="20" HAlign="right" HPosition="5">Higher <Font Underlined="yes">and</Font> <Font Script="super">right</Font></Text>
    </Subtitle>
    <Subtitle SpotNumber="4" TimeIn="00:00:10:000" TimeOut="00:00:12:000">
      <Font Size="52" Effect="shadow" EffectColor="FF202020">
        <Text VAlign="center" VPosition="0" HAlign="center">In the middle,<Space Size="1em"/>bigger <Font Size="26">and small</Font></Text>
      </Font>
    </Subtitle>
    <Subtitle SpotNumber="5" TimeIn="00:00:20:000" TimeOut="00:00:22:000">
      <Text VAlign="bottom" VPosition="10"><Ruby><Rb>漢字</Rb><Rt>かんじ</Rt></Ruby>を<Ruby><Rb>読</Rb><Rt Position="after">よ</Rt></Ruby>む</Text>
    </Subtitle>
    <Subtitle SpotNumber="6" TimeIn="00:00:25:000" TimeOut="00:00:27:000">
      <Text VAlign="center" HAlign="right" HPosition="10" Direction="vertical">右の列</Text>
      <Text VAlign="center" HAlign="right" HPosition="16.4" Direction="vertical">左<HGroup>12</HGroup>月</Text>
    </Subtitle>
  </Font>
</DCSubtitle>
'''

print('Interop')
segments, settings = dcp.parse(INTEROP)
check('the reel', {key: settings[key] for key in ('standard', 'language', 'metadata', 'fps')},
      {'standard': 'interop', 'language': 'English', 'metadata': {'title': 'Tears of Steel'}, 'fps': None})
check('times in ticks of 1/250 s, sorted', [(s['start'], s['end']) for s in segments],
      [(5.792, 7.46), (10.0, 12.0), (20.0, 22.0), (25.0, 27.0), (62.0, 64.5), (3600.0, 3602.996)])
by_start = {s['start']: s for s in segments}
check('two lines in the order they are on the picture; italics',
      by_start[5.792]['text'], "You're a <i>jerk,</i> Thom,\nand I could tell you")
check('a subtitle where Subtitld puts one has no position, nor a look', [key for key in by_start[5.792]
                                                                          if key not in ('start', 'end', 'text')], [])
check('a colour and bold from its own <Font>; top left, at the title-safe margins',
      (by_start[62.0]['text'], by_start[62.0].get('position')), ('{\\an7}<font color="#ffff00"><b>Gold at the top left</b></font>', None))
check('placed elsewhere: its margins; underline; raised text', (by_start[3600.0]['text'], by_start[3600.0]['position']),
      ('{\\an3}Higher <u>and</u> <sup>right</sup>', {'h': '5%', 'v': '20%'}))
check('in the middle; a <Space> a space; a size against the subtitle\'s',
      by_start[10.0]['text'], '{\\an5}In the middle, bigger <font size="50%">and small</font>')
check('its own size and shadow as its look', by_start[10.0]['look'],
      {'size': 6.57, 'shadow': [{'x': 0.328, 'y': 0.328, 'blur': 0, 'color': '#ff202020'}]})
check('rubies, the second\'s reading after its text', by_start[20.0]['text'],
      '<ruby>漢字<rt>かんじ</rt></ruby>を<ruby position="after">読<rt>よ</rt></ruby>む')
check('written down the picture: columns from the right, a group set upright',
      (by_start[25.0]['text'], by_start[25.0]['writing']),
      ('{\\an6}右の列\n左<font combine="all">12</font>月', 'vertical-rl'))

check('times in milliseconds, in files from before the ticks', cues(
    '<DCSubtitle Version="1.0"><Subtitle SpotNumber="1" TimeIn="00:00:01:500" TimeOut="00:00:02:750">'
    '<Text VAlign="bottom" VPosition="10">ms</Text></Subtitle>'
    '<Subtitle SpotNumber="2" TimeIn="00:00:03:100" TimeOut="00:00:04:000"><Text VAlign="bottom" VPosition="10">b</Text>'
    '</Subtitle></DCSubtitle>'), [(1.5, 2.75, 'ms'), (3.1, 4.0, 'b')])
check('and with a dot', cues('<DCSubtitle Version="1.0"><Subtitle SpotNumber="1" TimeIn="00:00:01.5" TimeOut="00:00:02.250">'
                             '<Text VAlign="bottom" VPosition="10">dot</Text></Subtitle></DCSubtitle>'), [(1.5, 2.25, 'dot')])
check('SMPTE\'s spelling read in Interop too', cues(
    '<DCSubtitle Version="1.0"><Subtitle SpotNumber="1" TimeIn="00:00:01:000" TimeOut="00:00:02:000">'
    '<Text Valign="top" Vposition="10" Halign="right">x</Text></Subtitle></DCSubtitle>'), [(1.0, 2.0, '{\\an9}x')])
check('an empty subtitle and an image are not text', cues(
    '<DCSubtitle Version="1.0"><Subtitle SpotNumber="1" TimeIn="00:00:01:000" TimeOut="00:00:02:000">'
    '<Text VAlign="top" VPosition="10">  </Text></Subtitle>'
    '<Subtitle SpotNumber="2" TimeIn="00:00:03:000" TimeOut="00:00:04:000"><Image VAlign="bottom" VPosition="10">a.png</Image>'
    '</Subtitle></DCSubtitle>'), [])

# A SMPTE reel: its namespace, frames of 25 a second counted from the
# reel's start at 01:00:00:00, the attributes as SMPTE spells them.
SMPTE = '''<?xml version="1.0" encoding="UTF-8"?>
<SubtitleReel xmlns="http://www.smpte-ra.org/schemas/428-7/2010/DCST" xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <Id>urn:uuid:a6c58cff-3e1e-4b38-acec-a42224475ef6</Id>
  <ContentTitleText>Sintel</ContentTitleText>
  <AnnotationText>Sintel, French subtitles</AnnotationText>
  <IssueDate>2026-11-10T12:00:00.000+00:00</IssueDate>
  <ReelNumber>1</ReelNumber>
  <Language>fr</Language>
  <EditRate>25 1</EditRate>
  <TimeCodeRate>25</TimeCodeRate>
  <StartTime>01:00:00:00</StartTime>
  <LoadFont ID="theFont">urn:uuid:3dec6dc0-39d0-498d-97d0-928d2eb78391</LoadFont>
  <SubtitleList>
    <Font ID="theFont" Size="42" Color="FFFFFFFF" Effect="border" EffectColor="FF000000">
      <Subtitle SpotNumber="1" TimeIn="01:00:01:12" TimeOut="01:00:03:00" FadeUpTime="00:00:00:02" FadeDownTime="00:00:00:02">
        <Text Valign="bottom" Vposition="10" Halign="center">Le dragon <Font Underline="yes" Italic="yes">arrive</Font></Text>
      </Subtitle>
      <Subtitle SpotNumber="2" TimeIn="01:00:04:00" TimeOut="01:00:05:24">
        <Font Effect="border" EffectColor="FF0000FF">
          <Text Valign="top" Vposition="10" Halign="center">En haut</Text>
          <Text Valign="top" Vposition="16.4" Halign="center">deux lignes</Text>
        </Font>
      </Subtitle>
      <Subtitle SpotNumber="3" TimeIn="01:00:06:00" TimeOut="01:00:07:00">
        <Text Valign="bottom" Vposition="10" Direction="rtl">שלום</Text>
      </Subtitle>
    </Font>
  </SubtitleList>
</SubtitleReel>
'''

print('SMPTE')
segments, settings = dcp.parse(SMPTE)
check('the reel', {key: settings[key] for key in ('standard', 'language', 'metadata', 'fps')},
      {'standard': 'smpte', 'language': 'fr', 'metadata': {'title': 'Sintel', 'description': 'Sintel, French subtitles'},
       'fps': 25})
check('frames of 25, from the reel\'s start', [(s['start'], s['end']) for s in segments],
      [(1.48, 3.0), (4.0, 5.96), (6.0, 7.0)])
check('formatting; the lines from the top; right to left', [s['text'] for s in segments],
      ['Le dragon <i><u>arrive</u></i>', '{\\an8}En haut\ndeux lignes', 'שלום'])
check('its own border, in blue', segments[1]['look'], {'outline': {'color': '#ff0000ff', 'width': 0.265}})
check('a direction', segments[2].get('direction'), 'rtl')
check('subtitles before the StartTime: times as they are', cues(SMPTE.replace('<StartTime>01:00:00:00', '<StartTime>02:00:00:00')
                                                                )[0][:2], (3601.48, 3603.0))
for year in ('2007', '2014'):
    check(f'the {year} namespace', cues(SMPTE.replace('2010/DCST', f'{year}/DCST'))[0][2], 'Le dragon <i><u>arrive</u></i>')
check('a frame rate only its EditRate gives', cues(SMPTE.replace('<TimeCodeRate>25</TimeCodeRate>', '').replace(
    '<EditRate>25 1', '<EditRate>24 1'))[0][:2], (1.5, 3.0))

print('told apart')
folder = Path(tempfile.mkdtemp())
TTML = ('<?xml version="1.0" encoding="UTF-8"?><tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><body><div>'
        '<p begin="00:00:01.000" end="00:00:02.000">TTML</p></div></body></tt>')
for name, text in (('interop.xml', INTEROP), ('smpte.xml', SMPTE), ('ttml.xml', TTML), ('broken.xml', '<DCSubtitle')):
    (folder / name).write_text(text, encoding='utf-8')
check('by their root', [dcp.is_dcp(str(folder / name)) for name in ('interop.xml', 'smpte.xml', 'ttml.xml', 'broken.xml')],
      [True, True, False, False])
check('a <SubtitleReel> in another namespace is not SMPTE\'s',
      dcp.kind(dcp._root('<SubtitleReel xmlns="urn:other"/>')), None)
try:
    dcp.parse(TTML)
    check('TTML is not DCP subtitles', None, 'ValueError')
except ValueError as error:
    check('TTML is not DCP subtitles', str(error).startswith('Not DCP subtitles'), True)

print('writing')
WRITE = [
    {'start': 1.0, 'end': 2.5, 'text': 'Two <i>lines</i>,\n<font color="#ffff00">in yellow</font> and <b>bold</b>'},
    {'start': 3.004, 'end': 4.0, 'text': '{\\an8}At the top'},
    {'start': 5.0, 'end': 6.0, 'text': '{\\an1}Placed <u>left</u>', 'position': {'h': '15%', 'v': '22.5%'}},
    {'start': 7.0, 'end': 8.0, 'text': '{\\an5}Middle\nof\nthree', 'look': {'size': 6.57, 'outline': {'color': '#ff0000ff', 'width': 0.2}}},
    {'start': 9.0, 'end': 10.0, 'text': '<ruby>漢字<rt>かんじ</rt></ruby>と<ruby position="after">字<rt>じ</rt></ruby>'},
    {'start': 11.0, 'end': 12.0, 'text': '{\\an6}縦の<font combine="all">12</font>\n二列', 'writing': 'vertical-rl'},
    {'start': 13.0, 'end': 14.0, 'text': 'H<sub>2</sub>O and <font size="50%">small</font>'},
    {'start': 15.0, 'end': 16.0, 'text': '   '},
]
WANT = [
    (1.0, 2.5, 'Two <i>lines</i>,\n<font color="#ffff00">in yellow</font> and <b>bold</b>'),
    (3.004, 4.0, '{\\an8}At the top'),
    (5.0, 6.0, '{\\an1}Placed <u>left</u>'),
    (7.0, 8.0, '{\\an5}Middle\nof\nthree'),
    (9.0, 10.0, '<ruby>漢字<rt>かんじ</rt></ruby>と<ruby position="after">字<rt>じ</rt></ruby>'),
    (11.0, 12.0, '{\\an6}縦の<font combine="all">12</font>\n二列'),
    (13.0, 14.0, 'H<sub>2</sub>O and <font size="50%">small</font>'),
]
for standard in dcp.STANDARDS:
    written = dcp.write(WRITE, standard=standard, fps=24, language='pt-br', title='Tears & Steel',
                        identifier='0f2e3c1a-1111-4222-8333-444455556666')
    back, info = dcp.parse(written)
    got = [(s['start'], s['end'], s['text']) for s in back]
    if standard == dcp.SMPTE:
        # Frames of 24: 3.004 s is the 72nd frame, 3 s.
        WANT_HERE = [(3.0,) + cue[1:] if cue[0] == 3.004 else cue for cue in WANT]
    else:
        # Ticks of 1/250 s: 3.004 s is 751 ticks.
        WANT_HERE = WANT
    check(f'{standard}: read back as written', got, WANT_HERE)
    check(f'{standard}: the reel', (info['standard'], info['language'], info['metadata'], info['fps']),
          (standard, 'pt-BR', {'title': 'Tears & Steel'}, 24 if standard == dcp.SMPTE else None))
    check(f'{standard}: margins kept, and none where there were none',
          [s.get('position') for s in back], [None, None, {'h': '15%', 'v': '22.5%'}, None, None, None, None])
    check(f'{standard}: a look kept, and none where there was none',
          [s.get('look') for s in back][2:4], [None, {'size': 6.57, 'outline': {'color': '#ff0000ff', 'width': 0.328}}])
    check(f'{standard}: written down the picture', back[5].get('writing'), 'vertical-rl')

interop = dcp.write(WRITE[:3], standard=dcp.INTEROP, identifier='0f2e3c1a-1111-4222-8333-444455556666', title='T')
check('Interop: its root, its ID, its ticks and spellings, no fades', (
    '<DCSubtitle Version="1.0">' in interop, '<SubtitleID>0f2e3c1a-1111-4222-8333-444455556666</SubtitleID>' in interop,
    'TimeIn="00:00:03:001"' in interop, 'Underlined="yes"' in interop, 'HAlign="left" HPosition="15"' in interop,
    'FadeUpTime="0" FadeDownTime="0"' in interop, '<LoadFont' in interop),
      (True, True, True, True, True, True, False))
check('Interop: two lines a line\'s height apart, the last at the margin',
      ('VAlign="bottom" VPosition="16.4"' in interop, 'VAlign="bottom" VPosition="10"' in interop), (True, True))
smpte = dcp.write(WRITE[:3], standard=dcp.SMPTE, fps=30, identifier='0f2e3c1a-1111-4222-8333-444455556666', title='T')
check('SMPTE: its namespace, urn:uuid, rates, frames, spellings', (
    '<SubtitleReel xmlns="http://www.smpte-ra.org/schemas/428-7/2010/DCST">' in smpte,
    '<Id>urn:uuid:0f2e3c1a-1111-4222-8333-444455556666</Id>' in smpte, '<EditRate>30 1</EditRate>' in smpte,
    '<TimeCodeRate>30</TimeCodeRate>' in smpte, 'TimeOut="00:00:02:15"' in smpte, 'Underline="yes"' in smpte,
    'Valign="bottom" Vposition="22.5" Halign="left" Hposition="15"' in smpte, '<IssueDate>' in smpte),
      (True, True, True, True, True, True, True, True))
check('SMPTE: an unknown rate is the nearest a DCP plays', '<TimeCodeRate>24</TimeCodeRate>' in dcp.write(WRITE[:1], fps=23.976),
      True)
check('the rate for a video', [dcp.rate_for(fps) for fps in (23.976, 25, 29.97, 59.94, 0, None)], [24, 25, 30, 60, 24, 24])

print('through file_io')
session.FORMAT = {}
session.SUBTITLE = {'language': 'pt-br'}
opened, fmt = file_io.process_subtitles_file(str(folder / 'interop.xml'))
check('Interop opens: its language ("English") the project\'s, its generation kept', (
    fmt, len(opened), session.SUBTITLE['language'], session.FORMAT['options'].get('dcp_standard'),
    session.SUBTITLE.get('metadata')), ('DCP', 6, 'en-us', 'interop', {'title': 'Tears of Steel'}))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'smpte.xml'))
check('SMPTE opens: its rate kept too', (fmt, len(opened), session.SUBTITLE['language'][:2],
                                        {key: session.FORMAT['options'].get(key) for key in ('dcp_standard', 'dcp_fps')}),
      ('DCP', 3, 'fr', {'dcp_standard': 'smpte', 'dcp_fps': 25}))
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'ttml.xml'))
check('TTML in a .xml is still TTML', (fmt, [s['text'] for s in opened]), ('TTML', ['TTML']))
session.FORMAT = {}
(folder / 'captionate.xml').write_text('<captionate><timeformat>ms</timeformat><captioninfo><trackinfo><track>'
                                      '<displayname>en</displayname></track></trackinfo></captioninfo><captions>'
                                      '<caption time="1000"><tracks><track0>Flash</track0></tracks></caption>'
                                      '<caption time="2000"><tracks><track0></track0></tracks></caption></captions>'
                                      '</captionate>', encoding='utf-8')
check('and Captionate still Captionate', file_io.process_subtitles_file(str(folder / 'captionate.xml'))[1], 'CAPTIONATE')

session.SUBTITLE = {'segments': WRITE, 'language': 'en-us', 'metadata': {'title': 'Tears of Steel'}}
session.VIDEO = {'framerate': 23.976, 'filepath': '/videos/tears.mov'}
session.FORMAT = {'options': {'dcp_standard': 'interop'}}
file_io.save_file(str(folder / 'out.xml'), 'DCP')
saved = (folder / 'out.xml').read_text(encoding='utf-8')
check('saved as the generation it asks, with the file\'s title, in the project\'s language',
      ('<DCSubtitle' in saved, '<MovieTitle>Tears of Steel</MovieTitle>' in saved, '<Language>en-US</Language>' in saved),
      (True, True, True))
session.SUBTITLE = {'segments': WRITE, 'language': 'en-us'}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.xml'), 'DCP')
saved = (folder / 'out.xml').read_text(encoding='utf-8')
check('else SMPTE, at the video\'s rate as a DCP plays it, titled after the video',
      ('<SubtitleReel' in saved, '<TimeCodeRate>24</TimeCodeRate>' in saved, '<ContentTitleText>tears</ContentTitleText>' in saved),
      (True, True, True))
session.FORMAT = {'options': {'dcp_standard': 'smpte', 'dcp_fps': 50, 'text': 'original', 'offset': 1.0}}
file_io.save_file(str(folder / 'out.xml'), 'DCP')
opened, fmt = file_io.process_subtitles_file(str(folder / 'out.xml'))
check('the export\'s rate and shift; opened again as written', (fmt, opened[0]['start'], opened[0]['text'],
                                                                 session.FORMAT['options'].get('dcp_fps')),
      ('DCP', 2.0, WANT[0][2], 50))

print('pictures')


def png(width, height):
    """A white bar on nothing, as a PNG's bytes."""
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for x in range(4, width - 4):
        for y in range(4, height - 4):
            image.setPixelColor(x, y, QColor(255, 255, 255))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


(folder / 'sub_0001.png').write_bytes(png(400, 60))
(folder / '5c7a1b2e-0000-4000-8000-00000000abcd.png').write_bytes(png(300, 60))
(folder / 'interop_images.xml').write_text(
    '<?xml version="1.0" encoding="UTF-8"?><DCSubtitle Version="1.0"><SubtitleID>40950d85-63eb-4ee2-b1e8-45c126601b94'
    '</SubtitleID><MovieTitle>Pictures</MovieTitle><ReelNumber>1</ReelNumber><Language>fr</Language>'
    '<Subtitle SpotNumber="1" TimeIn="00:00:01:000" TimeOut="00:00:02:000" FadeUpTime="0" FadeDownTime="0">'
    '<Image VAlign="bottom" VPosition="10" HAlign="center">sub_0001.png</Image></Subtitle>'
    '<Subtitle SpotNumber="2" TimeIn="00:00:03:000" TimeOut="00:00:04:000">'
    '<Text VAlign="top" VPosition="10">Du texte en haut</Text></Subtitle></DCSubtitle>', encoding='utf-8')
(folder / 'smpte_images.xml').write_text(SMPTE.replace(
    '<Text Valign="bottom" Vposition="10" Direction="rtl">שלום</Text>',
    '<Image Valign="top" Vposition="5" Halign="left" Hposition="10">urn:uuid:5c7a1b2e-0000-4000-8000-00000000abcd</Image>')
    .replace('<SubtitleReel ', '<SubtitleReel IntrinsicPictureResolution="1998x1080" '), encoding='utf-8')
for name in ('interop_images.xml', 'smpte_images.xml'):
    session.FORMAT = {}
    try:
        file_io.process_subtitles_file(str(folder / name))
        check(f'{name}: for the OCR dialog', None, 'PictureSubtitlesError')
    except file_io.PictureSubtitlesError as error:
        check(f'{name}: for the OCR dialog, the generation kept to save it as text',
              (error.path, error.kind, session.FORMAT['options'].get('dcp_standard')),
              (str(folder / name), 'dcp', name.split('_')[0]))
pictures, tag = picture_ocr.read_file(str(folder / 'interop_images.xml'))
check('Interop: each PNG where its <Image> puts it; the text kept as its words', [
    (p.start, p.x, p.y, p.width, p.height, p.screen_width, p.text) for p in pictures] + [tag],
      [(1.0, 760, 912, 400, 60, 1920, ''), (3.0, 0, 0, 1, 1, 1920, 'Du texte en haut'), 'fr-fr'])
pictures, tag = picture_ocr.read_file(str(folder / 'smpte_images.xml'))
check('SMPTE: the PNG its urn:uuid names, on the picture the reel gives the size of',
      [(p.start, p.x, p.y, p.width, p.screen_width, p.text) for p in pictures][2],
      (6.0, 200, 54, 300, 1998, ''))


class FakeOCR(OCRProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append((len(image_paths), language))
        QTimer.singleShot(0, lambda: self.finished.emit(['lu'] * len(image_paths), [0.95] * len(image_paths)))


engine = FakeOCR()
job = picture_ocr.OCRJob.for_file(str(folder / 'interop_images.xml'), engine)
loop = QEventLoop()
out = {}
job.finished.connect(lambda found, info: (out.update(segments=found, info=info), loop.quit()))
job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
job.start()
QTimer.singleShot(20000, loop.quit)
loop.exec()
check('only the picture is read, in the file\'s language; the text kept, at the top',
      (engine.calls, [s['text'] for s in out.get('segments', [])]), ([(1, 'fr-fr')], ['lu', '{\\an8}Du texte en haut']))

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
from subtitld.modules import export_options
check('offered, and a timed-text format', ('DCP' in SUBTITLE_FORMATS, 'DCP' in export_options.TEXT_FORMATS,
                                          session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['DCP']['extensions']),
      (True, True, ['xml']))
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': WRITE, 'language': 'en-us'}
session.VIDEO = {'framerate': 23.976}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DCP')
panel._refresh_options()
check('DCP: its options, SMPTE at the video\'s rate (23.976: 24)', (
    panel.dcp_options.isHidden(), panel.text_options.isHidden(), panel.dcp_rate_combo.isHidden(),
    {key: panel.get_config()['options'].get(key) for key in ('dcp_standard', 'dcp_fps')}),
      (False, False, False, {'dcp_standard': 'smpte', 'dcp_fps': 24}))
panel.dcp_standard_combo.combobox.setCurrentIndex(1)
check('Interop: no frame rate to choose', (panel.dcp_rate_combo.isHidden(), panel.get_config()['options']['dcp_standard']),
      (True, 'interop'))
session.VIDEO = {}
panel.dcp_standard_combo.combobox.setCurrentIndex(0)
panel.dcp_rate_combo.combobox.setCurrentIndex(dcp.RATES.index(48))
panel.get_config()
again = _SubtitlesPanel()
check('remembered, without a video', (again.dcp_standard_combo.combobox.currentData(), again.dcp_rate_combo.combobox.currentData()),
      ('smpte', 48))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.dcp_options.isHidden(), 'dcp_standard' in panel.get_config()['options']), (True, False))

shutil.rmtree(folder, ignore_errors=True)
print()
print(f'{len(fails)} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
