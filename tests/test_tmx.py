"""TMX with times: reading Subtitle Edit's (its props, <br />, its srclang)
and a TMX 1.4 file with several languages and inline codes, writing the
original and its translations side by side, a round trip, refusing a
translation memory with no times, and the files and the export dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, tmx

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# As Subtitle Edit's Tmx14.cs writes one.
SE = '''<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header creationtool="Subtitle Edit" creationtoolversion="4.0" datatype="html" segtype="sentence" adminlang="en-us" srclang="EN" o-encoding="utf-8">
    <note>This is a subtitle</note>
  </header>
  <body>
    <tu tuid="0001" datatype="html">
      <prop type="start">00:00:01,000</prop>
      <prop type="end">00:00:03,500</prop>
      <tuv xml:lang="EN">
        <seg>Hello<br />world &amp; you</seg>
      </tuv>
    </tu>
    <tu tuid="0002" datatype="html">
      <prop type="start">00:01:02.050</prop>
      <prop type="end">00:01:04.5</prop>
      <tuv xml:lang="EN">
        <seg>Bye</seg>
      </tuv>
    </tu>
  </body>
</tmx>
'''
# TMX 1.4 with three languages, the source not first, inline codes.
MULTI = '''<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4"><header creationtool="x" creationtoolversion="1" segtype="block" o-tmf="x" adminlang="en" srclang="pt-BR" datatype="plaintext"/>
<body><tu><prop type="start">00:00:01,000</prop><prop type="end">00:00:02,000</prop>
<tuv xml:lang="en-US"><seg><bpt i="1">&lt;i&gt;</bpt>Hi<ept i="1">&lt;/i&gt;</ept> <hi>there</hi></seg></tuv>
<tuv xml:lang="pt-BR"><seg>Olá aí</seg></tuv><tuv lang="fr"><seg>Salut</seg></tuv></tu></body></tmx>
'''
print('reading')
check("Subtitle Edit's: its times with , or ., its <br />", tmx.parse(SE),
      ([{'start': 1.0, 'end': 3.5, 'text': 'Hello\nworld & you'}, {'start': 62.05, 'end': 64.5, 'text': 'Bye'}], 'EN'))
check('the srclang the original, the others its translations; inline codes out, <hi> kept, TMX 1.1 lang', tmx.parse(MULTI),
      ([{'start': 1.0, 'end': 2.0, 'text': 'Olá aí', 'translations': {'en-US': 'Hi there', 'fr': 'Salut'}}], 'pt-BR'))
try:
    tmx.parse('<tmx version="1.4"><header srclang="en"/><body><tu><tuv xml:lang="en"><seg>Hi</seg></tuv></tu></body></tmx>')
    check('a translation memory with no times: refused', None, 'ValueError')
except ValueError as error:
    check('a translation memory with no times: refused', str(error).startswith('This TMX has no times'), True)

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': '<i>Two</i>\nlines & more', 'translations': {'pt-br': 'Duas\nlinhas', 'fr': ''}},
        {'start': 3723.004, 'end': 3724.0, 'text': 'Late'}]
written = tmx.write(trip, 'en-us', '26.11')
root = ET.fromstring(written.encode())
header = root.find('header')
check("the header TMX 1.4 requires", sorted(key for key in header.attrib if key in (
    'creationtool', 'creationtoolversion', 'segtype', 'o-tmf', 'adminlang', 'srclang', 'datatype')), sorted(
    ['creationtool', 'creationtoolversion', 'segtype', 'o-tmf', 'adminlang', 'srclang', 'datatype']))
check("a unit a subtitle: Subtitle Edit's props, the original and each translation, <br /> for a line break",
      [([prop.text for prop in tu.findall('prop')], [(tuv.get('{http://www.w3.org/XML/1998/namespace}lang'),
                                                      ET.tostring(tuv.find('seg'), encoding='unicode')) for tuv in tu.findall('tuv')])
       for tu in root.iter('tu')],
      [(['00:00:01,000', '00:00:02,500'], [('en-US', '<seg>Two<br />lines &amp; more</seg>'), ('pt-BR', '<seg>Duas<br />linhas</seg>')]),
       (['01:02:03,004', '01:02:04,000'], [('en-US', '<seg>Late</seg>')])])
check('read back', tmx.parse(written),
      ([{'start': 1.0, 'end': 2.5, 'text': 'Two\nlines & more', 'translations': {'pt-BR': 'Duas\nlinhas'}},
        {'start': 3723.004, 'end': 3724.0, 'text': 'Late'}], 'en-US'))
check('looks like TMX, and a TTML does not', (tmx.looks_like(SE.encode()), tmx.looks_like('<tt xmlns="x"/>')), (True, False))

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.SUBTITLE.language = 'en-us'
(folder / 'in.tmx').write_text(MULTI, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.tmx'))
check("opens: the original's language the project's, translations by Subtitld's codes",
      (fmt, session.SUBTITLE.language, opened[0].get('translations')), ('TMX', 'pt-br', {'en-us': 'Hi there', 'fr-fr': 'Salut'}))
session.SUBTITLE.segments = trip
session.SUBTITLE.language = 'en-us'
# The text option asks for the translation alone: TMX keeps both anyway.
session.FORMAT = {'options': {'text': 'translation', 'language': 'pt-br', 'offset': 1.0}}
file_io.save_file(str(folder / 'out.tmx'), 'TMX')
check('saved: both texts whatever the text option, the offset applied', tmx.read(str(folder / 'out.tmx'))[0][0],
      {'start': 2.0, 'end': 3.5, 'text': 'Two\nlines & more', 'translations': {'pt-BR': 'Duas\nlinhas'}})

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TMX')
panel._refresh_options()
check('in the export menu, with the text options, no formatting',
      ('TMX' in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.formatting_combo.isHidden()), (True, False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
