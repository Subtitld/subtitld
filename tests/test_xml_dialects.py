"""The smaller XML dialects (modules.xml_dialects): reading each as Subtitle
Edit's readers take it (Flash XML, the 2006 Timed Text draft with Ooyala's
missing space and the CDATA variant, Rhozet Harmonic, Ninsight, Subtitle
Edit's two Xml layouts, XMP markers, Combined XML), writing the five
Subtitle Edit writes and reading them back, telling them from the other
XML Subtitld opens (TTML, DFXP's 2006/10 namespace, DCP, BDN, YouTube's),
and the file and the export dialog.

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
from subtitld.modules import file_io, session, xml_dialects as xd
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fps=None):
    return [(s['start'], s['end'], s['text']) for s in xd.read_text(text, fps=fps)[0]]


# As FlashXml.cs's comment shows one.
FLASH = '''<?xml version="1.0" encoding="UTF-8"?>
<tt>
    <div>
      <p begin="00:00:00.700" end="00:00:05.000"><![CDATA[<sub>This is fully skinnable through XML<br/>using external images</sub>]]></p>
      <p begin="00:00:05.200" end="00:00:10.000"><![CDATA[<sub>Tom & Jerry <i>said</i></sub>]]></p>
      <p begin="00:00:10.200" dur="2s">Plain text</p>
 </div>
</tt>'''
# TimedText200604Ooyala.cs: the draft namespace, no space before xml:lang.
OOYALA = ('<?xml version="1.0" encoding="utf-8"?>\n<tt xmlns="http://www.w3.org/2006/04/ttaf1" '
          'xmlns:tts="http://www.w3.org/2006/04/ttaf1#styling"xml:lang="en">\n<head><styling>'
          '<style id="italic" tts:fontStyle="italic"/></styling></head>\n<body><div xml:lang="en">'
          '<p begin="00:00:01.000" id="p0" end="00:00:02.500">One<br/>two</p>'
          '<p begin="00:00:03.000" id="p1" end="00:00:04.000"><span tts:fontStyle="italic">Leaning</span></p>'
          '</div></body></tt>')
# TimedText200604CData.cs: the text, tags and all, in CDATA.
CDATA = ('<?xml version="1.0" encoding="utf-8"?>\n<tt xmlns="http://www.w3.org/2006/04/ttaf1" '
         'xmlns:tts="http://www.w3.org/2006/04/ttaf1#styling"><body><div>'
         '<p begin="00:00:01.000" id="p0" end="00:00:02.000"><![CDATA[<i>Hi</i> & bye]]></p></div></body></tt>')
# RhozetHarmonic.cs's comment: single quotes, a style <Data> first.
RHOZET = ("<?xml version=\"1.0\"?>\n<TitlerData>\n<Data CharSize='0.2' PosX='0.5' PosY='0.75' ColorR='245' ColorG='245' "
          "ColorB='245' Transparency='0.0' ShadowSize='0.5' BkgEnable='1' BkgExtraWidth='0.02' BkgExtraHeight='0.02'/>\n"
          "<Data StartTimecode='00:00:02;21' EndTimecode='00:00:05;21' Title='CAPTIONING PROVIDED BY&#xA;SOMEONE'/>\n"
          "<Data StartTimecode='00:00:06:00' EndTimecode='00:00:07:12' Title='Next'/>\n</TitlerData>")
# NinsightXml.cs's template: frames at the MODEL's rate, a LIGNE a line.
NINSIGHT = ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE FILE SYSTEM "ninsight.dtd"><FILE xmlns="http://www.ninsight.com/Subtitle" '
            'Title="TAKEN" Version="1.8" Type="Protitle"><TRACKS><TEXT_TRACK Name="Subtitle"><TEXT_VERSION Lang="FRE">'
            '<TEXT_HEAD><MODEL FramePerSec="25" FramePerSecDivisor="1" UseDropFrames="false"/></TEXT_HEAD>'
            '<TEXT_CLIP TIMECODEIN="25" TIMECODEOUT="75"><LIGNES>'
            '<LIGNE Align="center"><SECTIONS><SECTION_TEXT><TEXT>Viens à table.</TEXT><FONT><STYLE Italic="false"/></FONT>'
            '</SECTION_TEXT></SECTIONS></LIGNE>'
            '<LIGNE Align="center"><SECTIONS><SECTION_TEXT><TEXT>Tout de suite</TEXT><FONT><STYLE Italic="true"/></FONT>'
            '</SECTION_TEXT></SECTIONS></LIGNE></LIGNES></TEXT_CLIP>'
            '<TEXT_CLIP TIMECODEIN="100" TIMECODEOUT="150"><LIGNES><LIGNE><SECTIONS><SECTION_TEXT><TEXT>Merci</TEXT>'
            '</SECTION_TEXT></SECTIONS></LIGNE></LIGNES></TEXT_CLIP>'
            '</TEXT_VERSION></TEXT_TRACK></TRACKS></FILE>')
TIMEXML = ('<?xml version="1.0" encoding="utf-8"?>\n<Subtitle>\n  <Paragraph>\n    <Number>1</Number>\n'
           '    <StartMilliseconds>1500</StartMilliseconds>\n    <EndMilliseconds>3000</EndMilliseconds>\n'
           '    <Text>Line &amp; one\nline two</Text>\n  </Paragraph>\n</Subtitle>')
TIMEXML2 = ('<?xml version="1.0" encoding="utf-8"?>\n<Subtitles>\n  <Subtitle>\n    <Number>1</Number>\n'
            '    <Start>00:01:02,500</Start>\n    <End>00:01:04,000</End>\n    <Duration>1,500</Duration>\n'
            '    <Text>Hello</Text>\n  </Subtitle>\n</Subtitles>')
RDF = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
       'xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"><rdf:Description rdf:about="">'
       '<xmpDM:Tracks><rdf:Bag><rdf:li><rdf:Description xmpDM:trackName="Subtitles"><xmpDM:markers><rdf:Seq><rdf:li>'
       '<rdf:Description xmpDM:startTime="1000" xmpDM:duration="2000"><xmpDM:name><rdf:Alt>'
       '<rdf:li xml:lang="x-default">{\\rtf1 First\\par Second}</rdf:li></rdf:Alt></xmpDM:name></rdf:Description>'
       '</rdf:li></rdf:Seq></xmpDM:markers></rdf:Description></rdf:li><rdf:li>'
       '<rdf:Description xmpDM:frameRate="f25"><xmpDM:markers><rdf:Seq><rdf:li>'
       '<rdf:Description xmpDM:startTime="100" xmpDM:duration="50"><xmpDM:name><rdf:Alt>'
       '<rdf:li xml:lang="x-default">In frames</rdf:li></rdf:Alt></xmpDM:name></rdf:Description>'
       '</rdf:li></rdf:Seq></xmpDM:markers></rdf:Description></rdf:li></rdf:Bag></xmpDM:Tracks>'
       '</rdf:Description></rdf:RDF></x:xmpmeta>')


def ttml_doc(begin, end, text):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><body><div>'
            f'<p begin="{begin}" end="{end}">{text}</p></div></body></tt>\n')


COMBINED = ttml_doc('00:00:01.000', '00:00:02.000', 'Same') + ttml_doc('00:00:02.100', '00:00:03.000', 'Same') \
    + ttml_doc('00:00:05.000', '00:00:06.000', 'Other')

print('reading')
check('Flash: CDATA <sub>, <br/>, a bare &, a duration', cues(FLASH),
      [(0.7, 5.0, 'This is fully skinnable through XML\nusing external images'), (5.2, 10.0, 'Tom & Jerry <i>said</i>'),
       (10.2, 12.2, 'Plain text')])
check("Ooyala's 2006-04 draft, its missing space mended", cues(OOYALA), [(1.0, 2.5, 'One\ntwo'), (3.0, 4.0, '<i>Leaning</i>')])
check("the draft's CDATA variant: its tags as formatting", cues(CDATA), [(1.0, 2.0, '<i>Hi</i> & bye')])
check("Rhozet: frames at the rate given, ';' as Subtitle Edit reads it, the style <Data> skipped", cues(RHOZET, fps=30),
      [(2.7, 5.7, 'CAPTIONING PROVIDED BY\nSOMEONE'), (6.0, 7.4, 'Next')])
check("Ninsight: frames at the MODEL's rate, a line a LIGNE, italics, the doctype dropped", cues(NINSIGHT, fps=30),
      [(1.0, 3.0, 'Viens à table.\n<i>Tout de suite</i>'), (4.0, 6.0, 'Merci')])
check('Subtitle Edit Xml: milliseconds', cues(TIMEXML), [(1.5, 3.0, 'Line & one\nline two')])
check('Subtitle Edit Xml 2: HH:MM:SS,mmm', cues(TIMEXML2), [(62.5, 64.0, 'Hello')])
check("XMP markers: milliseconds as Subtitle Edit takes them, a Track's frameRate where it says one, the not-RTF",
      cues(RDF), [(1.0, 3.0, 'First\nSecond'), (4.0, 6.0, 'In frames')])
check('Combined XML: each document read, the same text touching joined', cues(COMBINED),
      [(1.0, 3.0, 'Same'), (5.0, 6.0, 'Other')])

print('telling them apart')
check('each by its root', [xd.looks_like(text) for text in (FLASH, OOYALA, CDATA, RHOZET, NINSIGHT, TIMEXML, TIMEXML2, RDF, COMBINED)],
      ['flash', 'ttaf2006', 'ttaf2006', 'rhozet', 'ninsight', 'timexml', 'timexml2', 'rdf1', 'combined'])
OTHERS = {
    'TTML': ttml_doc('00:00:01.000', '00:00:02.000', 'x'),
    "DFXP's 2006/10 namespace": '<tt xmlns="http://www.w3.org/2006/10/ttaf1"><body><div><p begin="1s" end="2s">x</p></div></body></tt>',
    'DCP Interop': '<?xml version="1.0"?><DCSubtitle Version="1.0"><Subtitle SpotNumber="1"><Text>x</Text></Subtitle></DCSubtitle>',
    'SMPTE DCP': '<SubtitleReel xmlns="http://www.smpte-ra.org/schemas/428-7/2010/DCST"><SubtitleList/></SubtitleReel>',
    'BDN': '<BDN Version="0.93"><Events><Event InTC="00:00:01:00" OutTC="00:00:02:00"><Graphic>a.png</Graphic></Event></Events></BDN>',
    "YouTube's srv3": '<timedtext format="3"><body><p t="1000" d="500">x</p></body></timedtext>',
    'TeXML': '<?xml version="1.0"?><text3GTrack trackWidth="320"></text3GTrack>',
}
check('none of the other XML Subtitld opens', {name: xd.looks_like(text) for name, text in OTHERS.items()},
      {name: '' for name in OTHERS})

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': 'Hello & <i>you</i>\nsecond line'},
        {'start': 3.04, 'end': 4.0, 'text': '{\\an8}"Quoted" <it\'s>'}]
plain = [(1.0, 2.5, 'Hello & you\nsecond line'), (3.04, 4.0, '"Quoted" <it\'s>')]
for dialect in xd.WRITABLE:
    written = xd.write(trip, dialect, fps=25)
    want = plain
    if dialect == 'ttaf2006':
        want = [(1.0, 2.5, 'Hello & <i>you</i>\nsecond line'), plain[1]]
    if dialect == 'rhozet':
        want = [(1.0, 2.48, plain[0][2]), (3.04, 4.0, plain[1][2])]
    check(f'{xd.DIALECTS[dialect]}: told apart and read back', (xd.looks_like(written), cues(written, fps=25)), (dialect, want))
flash = xd.write(trip, 'flash')
check("Flash as Subtitle Edit writes it: CDATA <sub>, <br />, three digits of milliseconds",
      '<p begin="00:00:01.000" end="00:00:02.500"><![CDATA[<sub>Hello & you<br />second line</sub>]]></p>' in flash, True)
check('Rhozet: single quotes, the style <Data> first, HH:MM:SS:FF',
      xd.write(trip, 'rhozet', fps=25).splitlines()[2:4],
      ["<Data CharSize='0.2' PosX='0.5' PosY='0.75' ColorR='245' ColorG='245' ColorB='245' Transparency='0.0' "
       "ShadowSize='0.5' BkgEnable='1' BkgExtraWidth='0.02' BkgExtraHeight='0.02'/>",
       "<Data StartTimecode='00:00:01:00' EndTimecode='00:00:02:12' Title='Hello &amp; you&#10;second line'/>"])
check("Xml 2: Subtitle Edit's short duration", '<Duration>1,500</Duration>' in xd.write(trip, 'timexml2'), True)
try:
    xd.write(trip, 'ninsight')
    check('Ninsight is read only', None, 'ValueError')
except ValueError:
    check('Ninsight is read only', True, True)

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.VIDEO = {}
for name, text, want in (('flash.xml', FLASH, ('XMLDIALECT', 3, 'flash')), ('ooyala.dfxp', OOYALA, ('XMLDIALECT', 2, 'ttaf2006')),
                         ('ninsight.xml', NINSIGHT, ('XMLDIALECT', 2, 'ninsight'))):
    (folder / name).write_text(text, encoding='utf-8')
    session.FORMAT = {}
    opened, fmt = file_io.process_subtitles_file(str(folder / name))
    check(f'{name} opens as its dialect, which is kept', (fmt, len(opened), session.FORMAT['options'].get('xml_dialect')), want)
(folder / 'plain.xml').write_text(ttml_doc('00:00:01.000', '00:00:02.000', 'x'), encoding='utf-8')
session.FORMAT = {}
check('TTML in an .xml is TTML still', file_io.process_subtitles_file(str(folder / 'plain.xml'))[1], 'TTML')
session.SUBTITLE = Subtitle({'segments': trip, 'language': 'en'})
session.FORMAT = {'options': {'xml_dialect': 'rhozet', 'fps': 30}}
file_io.save_file(str(folder / 'out.xml'), 'XMLDIALECT')
check('saved in the dialect kept, at the rate given', cues((folder / 'out.xml').read_text(encoding='utf-8'), fps=30),
      [(1.0, 2.5, plain[0][2]), (3.033, 4.0, plain[1][2])])
session.FORMAT = {'options': {'xml_dialect': 'ninsight'}}
file_io.save_file(str(folder / 'fallback.xml'), 'XMLDIALECT')
check("a read-only dialect is saved as Flash's", xd.looks_like((folder / 'fallback.xml').read_text(encoding='utf-8')), 'flash')

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.FORMAT = {'options': {'xml_dialect': 'timexml2'}}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('XMLDIALECT')
panel._refresh_options()
config = panel.get_config()
check('in the export menu: the writable dialects, the one opened chosen, the frame rate offered',
      ('XMLDIALECT' in SUBTITLE_FORMATS,
       [panel.dialect_combo.combobox.itemData(i) for i in range(panel.dialect_combo.combobox.count())],
       config['options'].get('xml_dialect'), panel.sub_options.isHidden(), 'fps' in config['options']),
      (True, list(xd.WRITABLE), 'timexml2', False, True))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('hidden for other formats', panel.dialect_options.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
