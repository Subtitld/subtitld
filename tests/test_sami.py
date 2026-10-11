"""SAMI: reading what players and subtitlers have (Microsoft's own sample,
Korean files in CP949 with two languages, closed and unclosed tags, class
styles, Source lines, ruby, stray text), writing SAMI that FFmpeg and
Windows Media Player read, and the export's options.

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
from subtitld.modules import file_io, sami, session, srt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text):
    segments, _languages = sami.parse(text)
    return [(s['start'], s['end'], s['text']) + tuple(s[key] for key in ('speaker', 'translations') if key in s)
            for s in segments]


# Microsoft's sample, from "Understanding SAMI 1.0", shortened.
KENNEDY = """<SAMI>
<Head>
   <Title>President John F. Kennedy Speech</Title>
   <SAMIParam>
      Metrics {time:ms; duration: 73000;}
      Spec {MSFT:1.0;}
   </SAMIParam>
   <STYLE TYPE="text/css"><!--
      P {margin-left: 29pt; font-size: 12pt; text-align: left; color: white; background-color: black;}
      .ENUSCC {Name: "English Captions"; lang: en-US-CC;}
#Source {margin-bottom: -15pt; background-color: silver; color: black;}
-->
   </Style>
</Head>
<Body>
   <SYNC Start=0>
      <P Class=ENUSCC ID=Source>Pres. John F. Kennedy
   <SYNC Start=10>
      <P Class=ENUSCC>Let the word go forth,
         from this time and place
   <SYNC Start=8800>
      <P Class=ENUSCC>has been passed to a new generation
   <SYNC Start=73000>
      <P Class=ENUSCC ID=Source>End of:
      <P Class=ENUSCC>President John F. Kennedy Speech
</Body>
</SAMI>"""

KOREAN = """<SAMI>
<HEAD>
<STYLE TYPE="text/css">
<!--
P { margin-left:8pt; text-align:center; font-size:20pt; font-family:굴림; color:white; }
.KRCC { Name:한국어; lang:ko-KR; SAMIType:CC; }
.ENCC { Name:English; lang:en-US; SAMIType:CC; }
-->
</STYLE>
</HEAD>
<BODY>
<SYNC Start=23000><P Class=KRCC>넌 정말 <font color="#ffff00">못됐어</font>, 톰.
<P Class=ENCC>You're a <font color="#ffff00">jerk</font>, Thom.
<SYNC Start=24500><P Class=KRCC>&nbsp;
<P Class=ENCC>&nbsp;
<SYNC Start=27000><P Class=KRCC><i>...넌 로봇공학이 있잖아</i>,<br>난 그냥 우주에서
<P Class=ENCC><i>...you have your robotics</i>, and I<br>just want to be <b>awesome</b> in space.
<SYNC Start=30500><P Class=KRCC>&nbsp;
<P Class=ENCC>&nbsp;
<SYNC Start=31000><P Class=ENCC>Only in English
<SYNC Start=32000><P Class=ENCC>&nbsp;
</BODY>
</SAMI>"""

print('reading')
check("Microsoft's sample: Source lines are speakers and stay, whitespace is a space, Metrics' duration",
      cues(KENNEDY), [
          (0.01, 8.8, 'Let the word go forth, from this time and place', 'Pres. John F. Kennedy'),
          (8.8, 73.0, 'has been passed to a new generation', 'Pres. John F. Kennedy'),
          (73.0, 77.0, 'President John F. Kennedy Speech', 'End of:')])
check('its language, without -CC', sami.parse(KENNEDY)[1], ['en-US'])
check('two languages: the first defined the original, the other its translation; <br> a line break',
      cues(KOREAN), [
          (23.0, 24.5, '넌 정말 <font color="#ffff00">못됐어</font>, 톰.',
           {'en-US': 'You\'re a <font color="#ffff00">jerk</font>, Thom.'}),
          (27.0, 30.5, '<i>...넌 로봇공학이 있잖아</i>,\n난 그냥 우주에서',
           {'en-US': '<i>...you have your robotics</i>, and I\njust want to be <b>awesome</b> in space.'})])
check('their languages', sami.parse(KOREAN)[1], ['ko-KR', 'en-US'])
CLOSED = """<sami><head><style type="text/css"><!--
p { color: white; }
.ENCC { name: English; lang: en-US; color: #FFD700; }
--></style></head>
<body>
<sync start="1000"><p class="ENCC">Gold by class <span style="color: white">white span</span></p></sync>
<sync start="2000"><p class="ENCC" style="font-style: italic">Italic by style<br/>second line</p></sync>
<sync start="3000"><p class="ENCC">&nbsp;</p></sync>
<sync start="4000"><p class="ENCC"><u>under</u> <s>struck</s> <strike>strike</strike> <em>em</em> <strong>strong</strong>
 <span style="font-weight: 700; text-decoration: underline">css</span> &amp; &lt;tag&gt;</p></sync>
<sync start="5000"><p class="ENCC">Last one, no blank after</p></sync>
</body></sami>"""
check('lower case, closed, quoted; the class colour, the CSS, entities; the last one four seconds', cues(CLOSED), [
    (1.0, 2.0, '<font color="#ffd700">Gold by class </font>white span'),
    (2.0, 3.0, '<font color="#ffd700"><i>Italic by style\nsecond line</i></font>'),
    (4.0, 5.0, '<font color="#ffd700"><u>under</u> <s>struck</s> <s>strike</s> <i>em</i> <b>strong</b> '
               '<b><u>css</u></b> & <tag></font>'),
    (5.0, 9.0, '<font color="#ffd700">Last one, no blank after</font>')])
MESSY = """<SAMI><BODY>
<!-- a comment -->
<SYNC Start=1000><P Class=ENCC><FONT color=red>Unclosed font
<SYNC Start=2000><P Class=ENCC>Next, plain <ruby>漢<rt>kan</rt></ruby>字
<SYNC Start=3000>
<SYNC Start=3500>Bare text, no P
<SYNC Start=4500><P Class=ENCC>  &nbsp;  </P>
<SYNC Start=0500><P Class=ENCC>Out&nbsp;of  order
<SYNC Start=600><P Class=ENCC>
<SYNC Start=nonsense><P Class=ENCC>No time
</BODY></SAMI>"""
check('unclosed tags, ruby without its reading, an empty sync ends, text with no paragraph, out of order',
      cues(MESSY), [(0.5, 0.6, 'Out of order'), (1.0, 2.0, '<font color="#ff0000">Unclosed font</font>'),
                    (2.0, 3.0, 'Next, plain 漢字'), (3.5, 4.5, 'Bare text, no P')])
check('a language from the class name when the style sheet has none', sami.parse(MESSY)[1], ['en'])
check('class names, guessed', [sami._guess_language(name) for name in ('KRCC', 'ENUSCC', 'jpcc', 'FRFRCC', 'Subtitle')],
      ['ko', 'en-us', 'ja', 'fr-fr', ''])
check('a translation goes to the subtitle it overlaps most; one that overlaps none is left out', cues(
    '<SAMI><BODY><SYNC Start=1000><P Class=AA>One<SYNC Start=3000><P Class=AA>Two<SYNC Start=5000><P Class=AA>&nbsp;'
    '<SYNC Start=2600><P Class=BB>Late one<SYNC Start=4000><P Class=BB>&nbsp;'
    '<SYNC Start=6000><P Class=BB>Alone<SYNC Start=7000><P Class=BB>&nbsp;</BODY></SAMI>'),
      [(1.0, 3.0, 'One'), (3.0, 5.0, 'Two', {'bb': 'Late one'})])
check('nothing but a head: no subtitles', sami.parse('<SAMI><HEAD><TITLE>x</TITLE></HEAD><BODY></BODY></SAMI>'), ([], []))

print('decoding')
check('CP949, as Korean files are', [s['text'] for s in sami.parse(srt.decode(KOREAN.encode('cp949')))[0]][0],
      '넌 정말 <font color="#ffff00">못됐어</font>, 톰.')
japanese = '第一行字幕①。日本語の字幕です。' * 20
check('CP932 for what chardet calls Shift JIS (① is only in CP932)', srt.decode(japanese.encode('cp932')), japanese)

print('writing')
segments = [
    {'start': 5, 'end': 6, 'text': 'No speaker, Tom & Jerry &amp; <tag>'},
    {'start': 1, 'end': 2.5, 'text': "You're a <i>jerk</i>,\nCelia.", 'speaker': 'Thom'},
    {'start': 2.5, 'end': 4, 'text': '{\\an8}<font color="#ffd700">Gold <b>bold</b> <u>under</u> <s>struck</s></font>',
     'speaker': 'Celia'},
    {'start': 7, 'end': 8, 'text': '  <i> </i> '},
]
portuguese = [{'start': 1, 'end': 2.5, 'text': 'Você é um <i>idiota</i>,\nCelia.', 'speaker': 'Thom'},
              {'start': 5, 'end': 6, 'text': 'Sem falante'}]
one = sami.write([('en-us', 'English (United States)', segments)])
body = one.split('<BODY>\n')[1].split('\n</BODY>')[0].split('\n')
check('SAMI 1.0 as Microsoft writes it: SAMIParam, a class for the language', (
    '  Metrics {time:ms;}' in one, '.ENUSCC { Name: "English (United States)"; lang: en-US; SAMIType: CC; }' in one,
    'Source' in one), (True, True, False))
check('a sync where each starts, an &nbsp; one where it ends unless the next starts there; tags in capitals; '
      'no place on the picture; & only escaped where it would read as an entity', body, [
          '<SYNC Start=1000>', "<P Class=ENUSCC>You're a <I>jerk</I>,<BR>Celia.",
          '<SYNC Start=2500>', '<P Class=ENUSCC><FONT color="#ffd700">Gold <B>bold</B> <U>under</U> <S>struck</S></FONT>',
          '<SYNC Start=4000>', '<P Class=ENUSCC>&nbsp;',
          '<SYNC Start=5000>', '<P Class=ENUSCC>No speaker, Tom & Jerry &amp;amp; &lt;tag>',
          '<SYNC Start=6000>', '<P Class=ENUSCC>&nbsp;'])
check('read back as written, but for the place', [(s['start'], s['text']) for s in sami.parse(one)[0]], [
    (1.0, "You're a <i>jerk</i>,\nCelia."), (2.5, '<font color="#ffd700">Gold <b>bold</b> <u>under</u> <s>struck</s></font>'),
    (5.0, 'No speaker, Tom & Jerry &amp; <tag>')])
two = sami.write([('en-us', 'English', segments), ('pt-br', 'Portuguese', portuguese)], sources=True)
check('two languages and Source lines: an empty Source line ends a speaker, first in its sync; a blank in a sync '
      'where another language shows is an empty paragraph', two.split('<SYNC Start=2500>\n')[1].split('<SYNC Start=5000>')[0],
      '<P Class=PTBRCC ID=Source>\n<P Class=ENUSCC ID=Source>Celia\n'
      '<P Class=ENUSCC><FONT color="#ffd700">Gold <B>bold</B> <U>under</U> <S>struck</S></FONT>\n<P Class=PTBRCC>\n'
      '<SYNC Start=4000>\n<P Class=ENUSCC ID=Source>\n<P Class=ENUSCC>&nbsp;\n')
check('#Source in the style sheet', '#Source {' in two, True)
check('read back: speakers, the translation', [(s['start'], s.get('speaker'), s.get('translations')) for s in sami.parse(two)[0]], [
    (1.0, 'Thom', {'pt-BR': 'Você é um <i>idiota</i>,\nCelia.'}), (2.5, 'Celia', None), (5.0, None, {'pt-BR': 'Sem falante'})])
check('class names, one each', [sami.class_name(tag, taken) for tag, taken in (('en-us', ()), ('ko', ()), ('en-us', ('ENUSCC',)))],
      ['ENUSCC', 'KOCC', 'ENUSCC2'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'korean.smi').write_bytes(KOREAN.encode('cp949'))
session.SUBTITLE['language'] = 'en-us'
opened, fmt = file_io.process_subtitles_file(str(folder / 'korean.smi'))
check('opens: the language for the project, the translation as Subtitld names it',
      (fmt, session.SUBTITLE['language'], len(opened), opened[0]['translations']),
      ('SAMI', 'ko-kr', 2, {'en-us': 'You\'re a <font color="#ffff00">jerk</font>, Thom.'}))
session.SUBTITLE['segments'] = opened
session.SUBTITLE['language'] = 'ko-kr'
for opened_segment, speaker in zip(opened, ('Celia', 'Thom')):
    opened_segment['speaker'] = speaker


def export(name, **options):
    session.FORMAT['options'] = options
    file_io.save_file(str(folder / name), 'SAMI')
    return (folder / name).read_bytes()


both = export('both.smi', text='both', language='en-us', speakers='voice', encoding='cp949', line_ends='crlf')
check('both: two languages, Korean first; CP949, Windows line ends', (
    both.decode('cp949').count('<P Class=KOKRCC>'), both.decode('cp949').count('<P Class=ENUSCC>'),
    b'\r\n' in both, 'lang: ko-KR' in both.decode('cp949')), (4, 4, True, True))
check('read back', [(s['speaker'], s['translations']['en-us'][:9]) for s in file_io.process_subtitles_file(str(folder / 'both.smi'))[0]],
      [('Celia', "You're a "), ('Thom', '<i>...you')])
translated = export('english.smi', text='translation', language='en-us', speakers='prefix').decode('utf-8')
check('the translation alone, in its language, names before the text', (
    'lang: en-US' in translated, 'KOKRCC' in translated, "Celia: You're a" in translated), (True, False, True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SAMI')
panel._refresh_options()
combo = panel.speakers_combo.combobox
check('SAMI: the encoding and line ends; speakers as the Source line', (
    panel.srt_options.isHidden(), combo.itemText(combo.count() - 1), combo.itemData(combo.count() - 1)),
      (False, 'As the Source line (ID=Source)', 'voice'))
texts = [panel.text_combo.combobox.itemText(i) for i in range(panel.text_combo.combobox.count())]
check('both, as two languages', texts[-1], 'Original and translation, as two languages: English (United States)')
check('CP949 offered', panel.encoding_combo.combobox.findData('cp949') >= 0, True)
panel.encoding_combo.combobox.setCurrentIndex(panel.encoding_combo.combobox.findData('cp949'))
check('chosen', {key: panel.get_config()['options'].get(key) for key in ('encoding', 'line_ends')},
      {'encoding': 'cp949', 'line_ends': 'lf'})
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check("SRT: its own words for both", panel.text_combo.combobox.itemText(panel.text_combo.combobox.count() - 1),
      'Original and translation: English (United States)')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
