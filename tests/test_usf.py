"""USF as other tools write it and as the specification has it: named
styles a text uses, <font italic="yes">, "duration", times as seconds,
several texts in a subtitle, the encoding the XML declares, each
language's block matched by time, ISO 639-2 codes and languages' names;
and what Subtitld writes: no Default style, the languages' names, the
specification's codes when exporting a USF.

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
from subtitld.modules import file_io, session, usf

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


OTHER = '''<?xml version="1.0" encoding="ISO-8859-1"?>
<USFSubtitles version="1.1">
  <metadata>
    <title>Tears of Steel</title>
    <author><name>Someone</name></author>
    <language code="eng">English</language>
  </metadata>
  <styles>
    <style name="Default"><fontstyle face="Arial" size="24" color="#FFFFFF"/></style>
    <style name="Narrator"><fontstyle italic="yes" color="#00FFFF00"/><position alignment="TopCenter"/></style>
    <style name="Loud"><fontstyle weight="bold" underline="yes"/></style>
  </styles>
  <subtitles>
    <language code="eng">English</language>
    <subtitle start="00:00:05.000" stop="00:00:06.000"><text>Later, café</text></subtitle>
    <subtitle start="00:00:01.000" duration="1.5"><text style="Narrator">Once upon a time</text></subtitle>
    <subtitle start="3" stop="4.25"><text style="Loud">Seconds alone</text></subtitle>
    <subtitle start="00:00:07.000" stop="00:00:08.000"><text><font italic="yes">Font italic</font>, <font bold="yes" color="#FF0000">bold red</font></text></subtitle>
    <subtitle start="00:00:09.000" stop="00:00:10.000"><text>First text</text><text alignment="TopLeft">second text</text></subtitle>
    <subtitle start="00:00:11.000"><text>No end at all</text></subtitle>
  </subtitles>
  <subtitles>
    <language code="fre">French</language>
    <subtitle start="00:00:01.100" stop="00:00:02.400"><text>Il était une fois</text></subtitle>
    <subtitle start="00:00:05.000" stop="00:00:06.000"><text>Plus tard</text></subtitle>
  </subtitles>
</USFSubtitles>'''.encode('latin-1')

print('a file another tool wrote')
reader = usf.USFReader()
segments = reader.read(OTHER)
check("the encoding it declares; its style's italic, colour and place; weight and underline; duration; seconds alone; "
      "<font italic>; several texts, in the first's place; one with no end left out; sorted by start",
      [(s['start'], s['end'], s['text']) for s in segments], [
          (1.0, 2.5, '{\\an8}<font color="#ffff00"><i>Once upon a time</i></font>'),
          (3.0, 4.25, '<b><u>Seconds alone</u></b>'),
          (5.0, 6.0, 'Later, café'),
          (7.0, 8.0, '<i>Font italic</i>, <font color="#ff0000"><b>bold red</b></font>'),
          (9.0, 10.0, 'First text\nsecond text')])
check('the other language matched by time, not by place', [s.get('translations') for s in segments][:3],
      [{'fre': 'Il était une fois'}, None, {'fre': 'Plus tard'}])
check('the language as written, without a resolver', reader.language, 'eng')

print("Subtitld's own speakers")
own = usf.USFWriter().write([{'start': 0, 'end': 1, 'text': 'Hi', 'speaker': 'Thom'}],
                            speakers={'Thom': {'color': '#ff0000'}}, language='en-us')
check("a speaker's style gives the speaker's colour, not the text's", usf.USFReader().read(own)[0]['text'], 'Hi')

print('what Subtitld writes')
segments_pt = [{'start': 1, 'end': 2, 'text': 'Hello', 'speaker': 'A', 'translations': {'pt-br': 'Olá', 'pt-pt': 'Olá!'}}]
names = {code: name for name, code in session.LANGUAGE_DICT_LIST.items()}
written = usf.USFWriter().write(segments_pt, language='en-us', names=names, metadata={'title': 'x'})
check("no Default style; the specification's ISO 639-2 codes, each language's name, the title", (
    'name="Default"' in written, written.count('<language code="eng">English (United States)</language>'),
    '<language code="por">Portuguese (Brazil)</language>' in written,
    '<language code="por">Portuguese (Portugal)</language>' in written, '<title>x</title>' in written), (False, 2, True, True, True))
check("read back, the names tell the two Portugueses apart", usf.USFReader(language_of=file_io._usf_language).read(written)[0]
      ['translations'], {'pt-br': 'Olá', 'pt-pt': 'Olá!'})
check('a code in BCP 47 case finds its name', '<language code="por">Portuguese (Brazil)</language>' in usf.USFWriter().write(
    [{'start': 0, 'end': 1, 'text': 'x'}], language='pt-BR', names=names), True)
check('ISO 639-2 for Subtitld\'s languages', [usf.iso639_2(code) for code in ('pt-br', 'de-de', 'cmn-hans-cn', 'fr-ca', 'xx')],
      ['por', 'ger', 'chi', 'fre', 'xx'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'other.usf').write_bytes(OTHER)
session.SUBTITLE['language'] = 'pt-br'
opened, fmt = file_io.process_subtitles_file(str(folder / 'other.usf'))
check('ISO 639-2 codes become Subtitld\'s', (fmt, session.SUBTITLE['language'], opened[0]['translations']),
      ('USF', 'en-us', {'fr-fr': 'Il était une fois'}))
session.SUBTITLE['segments'] = segments_pt
session.SUBTITLE['language'] = 'en-us'
session.FORMAT = {}
file_io.save_file(str(folder / 'export.usf'), 'USF')
exported = (folder / 'export.usf').read_text(encoding='utf-8')
check('a saved USF: the codes and names the specification asks for, a title', (
    '<language code="eng">English (United States)</language>' in exported, '<title>export</title>' in exported), (True, True))
reopened, _fmt = file_io.process_subtitles_file(str(folder / 'export.usf'))
check("reopened: the names tell Brazil's Portuguese from Portugal's", (session.SUBTITLE['language'], reopened[0]['translations']),
      ('en-us', {'pt-br': 'Olá', 'pt-pt': 'Olá!'}))
(folder / 'not.usf').write_text('<?xml version="1.0"?><tt xmlns="http://www.w3.org/ns/ttml"/>', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'not.usf'))
    check('not USF: says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('not USF: says so', str(error), 'Not a USF document: it is <tt>, not <USFSubtitles>')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
