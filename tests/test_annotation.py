"""ELAN EAF, Transcriber TRS and EXMARaLDA EXB: reading files laid out as
the tools write them (unaligned time slots, participants, translation
tiers, Sync points, Who and Event marks, word events), writing what their
schema and DTDs take, round trips, telling them apart, and the files and
the export dialog.

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
from subtitld.modules import annotation, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# As ELAN saves one: two participants' tiers, a slot with no time between
# two that have one, a Portuguese translation tier (Symbolic_Association)
# and a word tier (Symbolic_Subdivision) under Mary's, which is not read.
EAF = '''<?xml version="1.0" encoding="UTF-8"?>
<ANNOTATION_DOCUMENT AUTHOR="" DATE="2024-01-01T00:00:00+00:00" FORMAT="3.0" VERSION="3.0">
    <HEADER MEDIA_FILE="" TIME_UNITS="milliseconds">
        <MEDIA_DESCRIPTOR MEDIA_URL="file:///clip.wav" MIME_TYPE="audio/x-wav"/>
    </HEADER>
    <TIME_ORDER>
        <TIME_SLOT TIME_SLOT_ID="ts1" TIME_VALUE="1000"/>
        <TIME_SLOT TIME_SLOT_ID="ts2"/>
        <TIME_SLOT TIME_SLOT_ID="ts3" TIME_VALUE="3000"/>
        <TIME_SLOT TIME_SLOT_ID="ts4" TIME_VALUE="3500"/>
        <TIME_SLOT TIME_SLOT_ID="ts5" TIME_VALUE="5000"/>
    </TIME_ORDER>
    <TIER LINGUISTIC_TYPE_REF="utterance" PARTICIPANT="Mary" TIER_ID="Mary" LANG_REF="eng">
        <ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a1" TIME_SLOT_REF1="ts1" TIME_SLOT_REF2="ts2">
            <ANNOTATION_VALUE>Hello there</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION>
        <ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a2" TIME_SLOT_REF1="ts2" TIME_SLOT_REF2="ts3">
            <ANNOTATION_VALUE>Bye</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION>
    </TIER>
    <TIER LINGUISTIC_TYPE_REF="translation" PARENT_REF="Mary" PARTICIPANT="Mary" TIER_ID="Mary-pt" LANG_REF="por">
        <ANNOTATION><REF_ANNOTATION ANNOTATION_ID="a3" ANNOTATION_REF="a1">
            <ANNOTATION_VALUE>Olá</ANNOTATION_VALUE></REF_ANNOTATION></ANNOTATION>
    </TIER>
    <TIER LINGUISTIC_TYPE_REF="words" PARENT_REF="Mary" PARTICIPANT="Mary" TIER_ID="Mary-words" LANG_REF="eng">
        <ANNOTATION><REF_ANNOTATION ANNOTATION_ID="a4" ANNOTATION_REF="a1">
            <ANNOTATION_VALUE>Hello</ANNOTATION_VALUE></REF_ANNOTATION></ANNOTATION>
    </TIER>
    <TIER LINGUISTIC_TYPE_REF="utterance" PARTICIPANT="spk2" TIER_ID="Other">
        <ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a5" TIME_SLOT_REF1="ts4" TIME_SLOT_REF2="ts5">
            <ANNOTATION_VALUE>Me too</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION>
    </TIER>
    <LINGUISTIC_TYPE GRAPHIC_REFERENCES="false" LINGUISTIC_TYPE_ID="utterance" TIME_ALIGNABLE="true"/>
    <LINGUISTIC_TYPE CONSTRAINTS="Symbolic_Association" GRAPHIC_REFERENCES="false" LINGUISTIC_TYPE_ID="translation" TIME_ALIGNABLE="false"/>
    <LINGUISTIC_TYPE CONSTRAINTS="Symbolic_Subdivision" GRAPHIC_REFERENCES="false" LINGUISTIC_TYPE_ID="words" TIME_ALIGNABLE="false"/>
    <LANGUAGE LANG_ID="eng"/>
    <LANGUAGE LANG_ID="por"/>
    <CONSTRAINT DESCRIPTION="1-1 association with a parent annotation" STEREOTYPE="Symbolic_Association"/>
    <CONSTRAINT DESCRIPTION="Symbolic subdivision of a parent annotation." STEREOTYPE="Symbolic_Subdivision"/>
</ANNOTATION_DOCUMENT>
'''
print('ELAN')
check('participants as speakers (numbered as letters), an unaligned slot placed, the translation tier, words not read',
      annotation.parse_eaf(EAF),
      ([{'start': 1.0, 'end': 2.0, 'text': 'Hello there', 'speaker': 'Mary', 'translations': {'por': 'Olá'}},
        {'start': 2.0, 'end': 3.0, 'text': 'Bye', 'speaker': 'Mary'},
        {'start': 3.5, 'end': 5.0, 'text': 'Me too', 'speaker': 'A'}], 'eng'))
trip = [{'start': 1.0, 'end': 2.5, 'text': 'Hello & you\nline two', 'speaker': 'Mary', 'translations': {'pt-br': 'Olá'}},
        {'start': 2.0, 'end': 3.0, 'text': 'Overlap', 'speaker': 'Mary'},
        {'start': 4.0, 'end': 5.0, 'text': '<i>John</i> here', 'speaker': 'John'},
        {'start': 6.0, 'end': 7.0, 'text': 'nobody'}]
eaf = annotation.write_eaf(trip, 'en-us', speakers=True, media='/videos/my clip.mp4')
root = ET.fromstring(eaf.encode())
check("in the schema's order; a tier a speaker, an overlap's own, a translation tier, ELAN's constraints",
      ([child.tag for child in root][:3], [(tier.get('TIER_ID'), tier.get('PARTICIPANT'), tier.get('PARENT_REF'), tier.get('LANG_REF'))
                                          for tier in root.findall('TIER')],
       [constraint.get('STEREOTYPE') for constraint in root.findall('CONSTRAINT')]),
      (['HEADER', 'TIME_ORDER', 'TIER'],
       [('Mary', 'Mary', None, 'eng'), ('Mary - pt-br', 'Mary', 'Mary', 'por'), ('Mary (2)', 'Mary', None, 'eng'),
        ('John', 'John', None, 'eng'), ('default', None, None, 'eng')],
       ['Time_Subdivision', 'Symbolic_Subdivision', 'Symbolic_Association', 'Included_In']))
check('the video as the media', root.find('HEADER/MEDIA_DESCRIPTOR').attrib,
      {'MEDIA_URL': 'file:///videos/my%20clip.mp4', 'MIME_TYPE': 'video/mp4', 'RELATIVE_MEDIA_URL': './my%20clip.mp4'})
check('read back: plain text, speakers, translations', annotation.parse_eaf(eaf),
      ([dict(trip[0], translations={'por': 'Olá'}), trip[1], dict(trip[2], text='John here'), trip[3]], 'eng'))
check('without speakers: no participants, read back without them',
      [subtitle.get('speaker') for subtitle in annotation.parse_eaf(annotation.write_eaf(trip))[0]], [None] * 4)
check('a translation in the original\'s language keeps a language of its own',
      [language.get('LANG_ID') for language in ET.fromstring(annotation.write_eaf(
          [{'start': 0, 'end': 1, 'text': 'Hi', 'translations': {'en-gb': 'Hullo'}}], 'en-us').encode()).findall('LANGUAGE')],
      ['eng', 'en-gb'])

TRS = '''<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE Trans SYSTEM "trans-14.dtd">
<Trans scribe="x" audio_filename="news" version="2" version_date="080623" xml:lang="fr">
<Speakers>
<Speaker id="spk1" name="Simon Tivolle" check="no" dialect="native" accent="" scope="local"/>
<Speaker id="spk2" name="spk2" check="no" dialect="native" accent="" scope="local"/>
</Speakers>
<Episode>
<Section type="report" startTime="0" endTime="12.5">
<Turn startTime="0" endTime="2">
<Sync time="0"/>
</Turn>
<Turn speaker="spk1" startTime="2" endTime="9.609">
<Sync time="2"/>
le journal , <Event desc="rire" type="noise" extent="instantaneous"/> Simon
<Comment desc="?"/>Tivolle :
<Sync time="5.5"/>
bonsoir \xe0 tous
</Turn>
<Turn speaker="spk1 spk2" startTime="9.609" endTime="12.5">
<Sync time="9.609"/>
<Who nb="1"/>
oui
<Who nb="2"/>
non
</Turn>
</Section>
</Episode>
</Trans>
'''.encode('latin-1')
print('Transcriber')
check('Sync to Sync, Speakers\' names, Event and Comment out, Who, the declared encoding',
      annotation.parse_trs(TRS),
      ([{'start': 2.0, 'end': 5.5, 'text': 'le journal , Simon Tivolle :', 'speaker': 'Simon Tivolle'},
        {'start': 5.5, 'end': 9.609, 'text': 'bonsoir à tous', 'speaker': 'Simon Tivolle'},
        {'start': 9.609, 'end': 12.5, 'text': 'oui', 'speaker': 'Simon Tivolle'},
        {'start': 9.609, 'end': 12.5, 'text': 'non', 'speaker': 'A'}], 'fr'))
trs = annotation.write_trs(trip, 'pt-br', speakers=True, media='/videos/clip.mp4')
root = ET.fromstring(trs.encode())
check('a Turn a run of a speaker, pauses their own; overlaps cut',
      [(turn.get('speaker'), turn.get('startTime'), turn.get('endTime'), [sync.get('time') for sync in turn.iter('Sync')])
       for turn in root.iter('Turn')],
      [(None, '0', '1', ['0']), ('spk1', '1', '3', ['1', '2']), (None, '3', '4', ['3']), ('spk2', '4', '5', ['4']),
       (None, '5', '7', ['5', '6'])])
check('the header', (root.get('audio_filename'), root.get('{http://www.w3.org/XML/1998/namespace}lang'),
                     [speaker.get('name') for speaker in root.iter('Speaker')]), ('clip', 'pt', ['Mary', 'John']))
check('read back: one line each', annotation.parse_trs(trs)[0],
      [{'start': 1.0, 'end': 2.0, 'text': 'Hello & you line two', 'speaker': 'Mary'},
       {'start': 2.0, 'end': 3.0, 'text': 'Overlap', 'speaker': 'Mary'},
       {'start': 4.0, 'end': 5.0, 'text': 'John here', 'speaker': 'John'}, {'start': 6.0, 'end': 7.0, 'text': 'nobody'}])

EXB = '''<?xml version="1.0" encoding="UTF-8"?>
<basic-transcription>
<head><meta-information><project-name/><transcription-name/><referenced-file url="a.wav"/><ud-meta-information/>
<comment/><transcription-convention/></meta-information>
<speakertable><speaker id="SPK0"><abbreviation>TOM</abbreviation><sex value="m"/><languages-used/><l1/><l2/>
<ud-speaker-information/><comment/></speaker></speakertable></head>
<basic-body>
<common-timeline><tli id="T0" time="0.0"/><tli id="T1" time="0.4"/><tli id="T2"/><tli id="T3" time="1.2"/>
<tli id="T4" time="3.0"/><tli id="T5" time="4.5"/></common-timeline>
<tier id="TIE0" speaker="SPK0" category="v" type="t" display-name="TOM [v]">
<event start="T0" end="T1">so </event><event start="T1" end="T2">geht</event><event start="T2" end="T3">s. </event>
<event start="T4" end="T5">ja </event>
</tier>
<tier id="TIE1" speaker="SPK0" category="en" type="a" display-name="TOM [en]">
<event start="T0" end="T3">that's how it goes</event>
</tier>
</basic-body>
</basic-transcription>
'''
print('EXMARaLDA')
check('word events grouped, a word in two events joined, an unaligned point placed, annotation tiers left out',
      annotation.parse_exb(EXB),
      [{'start': 0.0, 'end': 1.2, 'text': 'so gehts.', 'speaker': 'TOM'}, {'start': 3.0, 'end': 4.5, 'text': 'ja', 'speaker': 'TOM'}])
exb = annotation.write_exb(trip, speakers=True, media='/videos/clip.mp4', name='clip')
root = ET.fromstring(exb.encode())
check('the head, the speakers, the timeline, a tier a speaker of events',
      (root.find('head/meta-information/referenced-file').get('url'),
       [speaker.findtext('abbreviation') for speaker in root.iter('speaker')],
       [point.get('time') for point in root.iter('tli')],
       [(tier.get('speaker'), tier.get('type'), [(e.get('start'), e.get('end'), e.text) for e in tier]) for tier in root.iter('tier')]),
      ('/videos/clip.mp4', ['Mary', 'John'], ['1', '2', '2.5', '3', '4', '5', '6', '7'],
       [('SPK0', 't', [('T0', 'T2', 'Hello & you line two ')]), ('SPK0', 't', [('T1', 'T3', 'Overlap ')]),
        ('SPK1', 't', [('T4', 'T5', 'John here ')]), (None, 't', [('T6', 'T7', 'nobody ')])]))
check('read back', annotation.parse_exb(exb),
      [{'start': 1.0, 'end': 2.5, 'text': 'Hello & you line two', 'speaker': 'Mary'},
       {'start': 2.0, 'end': 3.0, 'text': 'Overlap', 'speaker': 'Mary'},
       {'start': 4.0, 'end': 5.0, 'text': 'John here', 'speaker': 'John'}, {'start': 6.0, 'end': 7.0, 'text': 'nobody'}])

print('telling them apart')
check('by the root', [annotation.kind(data) for data in (EAF, TRS, EXB.encode(), b'<tt xmlns="http://www.w3.org/ns/ttml"/>')],
      ['eaf', 'trs', 'exb', None])
for name, call, start in (('a TTML as EAF: refused', lambda: annotation.parse_eaf('<tt/>'), 'Not an ELAN document'),
                          ('broken XML: refused', lambda: annotation.parse_trs('<Trans>'), 'Not well-formed XML')):
    try:
        call()
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.SUBTITLE['language'] = 'en-us'
for name, data, fmt, count in (('in.eaf', EAF.encode(), 'EAF', 3), ('in.trs', TRS, 'TRS', 4), ('in.exb', EXB.encode(), 'EXB', 2)):
    session.SPEAKERS.clear()
    session.FORMAT = {}
    (folder / name).write_bytes(data)
    opened, got = file_io.process_subtitles_file(str(folder / name))
    check(f'{fmt} opens, its speakers listed', (got, len(opened), bool(session.SPEAKERS), session.FORMAT['options'].get('speakers')),
          (fmt, count, True, 'voice'))
session.SPEAKERS.clear()
session.FORMAT = {}
file_io.process_subtitles_file(str(folder / 'in.eaf'))
opened, _fmt = file_io.process_subtitles_file(str(folder / 'in.eaf'))
check("a translation by Subtitld's code for its language", opened[0].get('translations'), {'pt-pt': 'Olá'})
check("TRS's language the project's", (file_io.process_subtitles_file(str(folder / 'in.trs'))[1], session.SUBTITLE['language']),
      ('TRS', 'fr-fr'))
session.SUBTITLE.segments = trip
session.SUBTITLE['language'] = 'en-us'
session.VIDEO = {'width': 1280, 'height': 720, 'filepath': '/videos/clip.mp4'}
for fmt, name in (('EAF', 'out.eaf'), ('TRS', 'out.trs'), ('EXB', 'out.exb')):
    session.FORMAT = {'options': {'speakers': 'voice'}}
    file_io.save_file(str(folder / name), fmt)
    session.FORMAT = {}
    back, back_fmt = file_io.process_subtitles_file(str(folder / name))
    check(f'{fmt}: saved and opened again', (back_fmt, [(s['start'], s['speaker']) for s in back]),
          (fmt, [(1.0, 'Mary'), (2.0, 'Mary'), (4.0, 'John'), (6.0, 'A')]))
check('EAF keeps the translation', annotation.read_eaf(str(folder / 'out.eaf'))[0][0].get('translations'),
      {'por': 'Olá'})

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
for fmt in ('EAF', 'TRS', 'EXB'):
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    combo = panel.speakers_combo.combobox
    check(f'{fmt} in the export menu, text options, no formatting, its own speakers',
          (fmt in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.formatting_combo.isHidden(),
           'voice' in [combo.itemData(index) for index in range(combo.count())]), (True, False, True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
