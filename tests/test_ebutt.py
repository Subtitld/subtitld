"""EBU-TT Part 1 (EBU Tech 3350), the EBU's production and archive profile
of TTML: written styled by reference only, timed in SMPTE timecode where
there is a frame rate, declared in ebuttm:conformsToStandard, checked by
ttml.check against the spec's rules; read in its 1.0 and 1.2 forms; and EBU
Tech 3360, the mapping from EBU STL: the GSI fields carried into ebuttm
metadata and back, through file_io, STL to EBU-TT to STL.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import re
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, ebustl, file_io, look, session, ttml
from subtitld.interface.export_dialog import _SubtitlesPanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>, <font color="#ffff00">yellow</font>',
             'speaker': 'Narrator'},
            {'start': 3.0, 'end': 4.48, 'text': '{\\an8}<u>At the top</u>', 'look': {'line_padding': 2.0,
                                                                                    'row_align': 'center'}},
            {'start': 60.06, 'end': 62.0, 'text': 'مرحبا', 'direction': 'rtl'},
            {'start': 63.0, 'end': 64.0, 'text': 'A <s>struck</s> word', 'hidden': True}]

print('written, in media time')
written = ttml.write(SEGMENTS, 'en', agents=True, profile='ebu-tt', metadata={'title': 'Tears', 'copyright': '© EBU'})
check('declared in ebuttm:conformsToStandard, as Part 1 v1.2 asks',
      '<ebuttm:conformsToStandard>urn:ebu:tt:exchange:2017-05</ebuttm:conformsToStandard>' in written, True)
check('ttp:timeBase always said, media without a frame rate; the cell grid said', (
    'ttp:timeBase="media"' in written, 'ttp:cellResolution="32 15"' in written), (True, True))
body = written.split('<body', 1)[1]
check('no style stated on a paragraph or a span: each names a <style>',
      re.findall(r'<(?:p|span)[^>]* (?:tts|ebutts):', body), [])
check('a region names the style its textAlign is in, and says nothing it may not',
      (re.findall(r'<region[^>]*tts:textAlign', written), 'style="align-center"' in written), ([], True))
check('ttm:copyright in <head> itself, after its <metadata>',
      re.search(r'</metadata>\s*<ttm:copyright>© EBU</ttm:copyright>\s*<styling>', written) is not None, True)
check('what EBU-TT has no word for is left out: line-through, the hidden subtitle\'s tts:display',
      ('lineThrough' in written, 'tts:display=' in written), (False, False))
check('the counts Tech 3360 and Part M name, this file\'s own',
      ('<ebuttm:documentTotalNumberOfSubtitles>4</ebuttm:documentTotalNumberOfSubtitles>' in written,
       '<ebuttm:documentMaximumNumberOfDisplayableCharacterInAnyRow>19</ebuttm:'
       'documentMaximumNumberOfDisplayableCharacterInAnyRow>' in written), (True, True))
check('ttml.check finds nothing wrong with it', ttml.check(written, 'ebu-tt'), [])
read, language, metadata = ttml.read_text(written)
check('read back as it was written, but for what EBU-TT cannot say',
      [(s['start'], s['end'], s['text'], s.get('speaker'), s.get('direction')) for s in read],
      [(1.0, 2.5, 'Hello <i>there</i>, <font color="#ffff00">yellow</font>', 'Narrator', None),
       (3.0, 4.48, '{\\an8}<u>At the top</u>', None, None), (60.06, 62.0, 'مرحبا', None, 'rtl'),
       (63.0, 64.0, 'A struck word', None, None)])
check('EBU-TT\'s own two, through a <style>', {k: v for k, v in look.of(read[1]).items()
                                                if k in ('line_padding', 'row_align')},
      {'line_padding': 2.0, 'row_align': 'center'})
check('the title, and the copyright from <head>', (metadata['title'], metadata['copyright']), ('Tears', '© EBU'))
check('a ruby, which EBU-TT has none of, as its reading in brackets',
      ttml.parse(ttml.write([{'start': 1, 'end': 2, 'text': '<ruby>東京<rt>とうきょう</rt></ruby>'}], 'ja',
                            profile='ebu-tt'))[0]['text'], '東京(とうきょう)')

print('written, in timecode')
timed = ttml.write(SEGMENTS, 'en', profile='ebu-tt', fps=25, start='10:00:00:00')
check('ttp:timeBase="smpte" with the four parameters EBU-TT asks of it',
      re.search(r'ttp:timeBase="smpte" ttp:frameRate="25" ttp:frameRateMultiplier="1 1" ttp:markerMode="continuous" '
                r'ttp:dropMode="nonDrop"', timed) is not None, True)
check('times as timecodes from the programme start, which the metadata names',
      ('begin="10:00:01:00" end="10:00:02:13"' in timed,
       '<ebuttm:documentStartOfProgramme>10:00:00:00</ebuttm:documentStartOfProgramme>' in timed), (True, True))
check('read back counting from the programme start', [(s['start'], s['end']) for s in ttml.parse(timed)],
      [(1.0, 2.52), (3.0, 4.48), (60.08, 62.0), (63.0, 64.0)])
check('still nothing wrong', ttml.check(timed, 'ebu-tt'), [])
ntsc = ttml.write(SEGMENTS, 'en', profile='ebu-tt', fps=30000 / 1001, drop_frame=True)
check('29.97 drop-frame: dropNTSC, the label after 00:00:59:29 is 00:01:00:02',
      ('ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001"' in ntsc, 'ttp:dropMode="dropNTSC"' in ntsc,
       'begin="00:01:00:02"' in ntsc), (True, True, True))
check('and read back to the frame', [s['start'] for s in ttml.parse(ntsc)], [1.001, 3.003, 60.06, 62.996])
check('a file that is not timed in timecode has no programme start to name',
      'documentStartOfProgramme' in ttml.write(SEGMENTS, 'en', profile='ebu-tt',
                                               metadata={'stl': {'TCP': '10000000'}}), False)

print('read: Part 1 version 1.0, as a subtitling system writes it')
V10 = '''<?xml version="1.0" encoding="UTF-8"?>
<tt:tt xmlns:tt="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
       xmlns:tts="http://www.w3.org/ns/ttml#styling" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
       xmlns:ebuttm="urn:ebu:tt:metadata" xmlns:ebutts="urn:ebu:tt:style"
       ttp:timeBase="smpte" ttp:frameRate="25" ttp:frameRateMultiplier="1 1" ttp:markerMode="discontinuous"
       ttp:dropMode="nonDrop" ttp:cellResolution="50 30" xml:lang="de">
  <tt:head>
    <tt:metadata>
      <ebuttm:documentMetadata>
        <ebuttm:documentEbuttVersion>v1.0</ebuttm:documentEbuttVersion>
        <ebuttm:documentTotalNumberOfSubtitles>2</ebuttm:documentTotalNumberOfSubtitles>
        <ebuttm:documentMaximumNumberOfDisplayableCharacterInAnyRow>38</ebuttm:documentMaximumNumberOfDisplayableCharacterInAnyRow>
        <ebuttm:documentStartOfProgramme>10:00:00:00</ebuttm:documentStartOfProgramme>
        <ebuttm:documentCountryOfOrigin>DE</ebuttm:documentCountryOfOrigin>
        <ebuttm:documentPublisher>Rundfunk</ebuttm:documentPublisher>
        <ebuttm:documentCopyright>© Rundfunk 2026</ebuttm:documentCopyright>
      </ebuttm:documentMetadata>
    </tt:metadata>
    <tt:styling>
      <tt:style xml:id="defaultStyle" tts:fontFamily="monospaceSansSerif" tts:fontSize="1c 1c" tts:lineHeight="normal"
                tts:textAlign="center" tts:color="white" tts:backgroundColor="transparent"/>
      <tt:style xml:id="WhiteOnBlack" tts:color="white" tts:backgroundColor="black"/>
      <tt:style xml:id="textCenter" tts:textAlign="center" ebutts:multiRowAlign="start" ebutts:linePadding="0.5c"/>
      <tt:style xml:id="italic" tts:fontStyle="italic"/>
    </tt:styling>
    <tt:layout>
      <tt:region xml:id="bottom" tts:origin="10% 10%" tts:extent="80% 80%" tts:padding="0c" tts:displayAlign="after"
                 tts:writingMode="lrtb"/>
    </tt:layout>
  </tt:head>
  <tt:body>
    <tt:div style="defaultStyle">
      <tt:p xml:id="sub1" region="bottom" style="textCenter" begin="10:00:03:20" end="10:00:07:00">
        <tt:span style="WhiteOnBlack">Wo warst du</tt:span><tt:br/><tt:span style="WhiteOnBlack italic">so lange?</tt:span>
      </tt:p>
      <tt:p xml:id="sub2" region="bottom" style="textCenter" begin="10:00:08:00" end="10:00:10:12">
        <tt:span style="WhiteOnBlack">Im Wald.</tt:span>
      </tt:p>
    </tt:div>
  </tt:body>
</tt:tt>'''
read, language, metadata = ttml.read_text(V10)
check('its subtitles from the programme start, in timecode at 25',
      [(s['start'], s['end'], s['text']) for s in read],
      [(3.8, 7.0, '<font background="#000000">Wo warst du</font>\n<font background="#000000"><i>so lange?</i></font>'),
       (8.0, 10.48, 'Im Wald.')])
check('its row alignment, and its line padding as half a cell across, in its own grid: 1% of the width, '
      'in % of the height', (look.of(read[0]).get('row_align'), round(look.of(read[0]).get('line_padding', 0), 3)),
      ('start', round(0.5 * 100 / 50 * 16 / 9, 3)))
check('what ebuttm:documentMetadata says, by the STL\'s names; the old documentCopyright as the copyright',
      (language, metadata.get('stl'), metadata.get('copyright')),
      ('de', {'TNS': '2', 'MNC': '38', 'TCP': '10000000', 'CO': 'DE', 'PUB': 'Rundfunk'}, '© Rundfunk 2026'))
check('version 1.0, declared by its documentEbuttVersion, is EBU-TT; the two sizes in one fontSize are not',
      ttml.check(V10, 'ebu-tt'), ['Font sizes with a width and a height of their own (1c 1c): '
                                  'the profile allows one size for both, and the height is what is read'])

print('what check finds')
broken = (re.sub(r'ttp:markerMode="discontinuous"\s+', '', V10).replace('ttp:dropMode="nonDrop"', 'ttp:dropMode="dropNTSC"')
          .replace('<tt:p xml:id="sub2" region="bottom"', '<tt:p xml:id="sub2" region="bottom" tts:color="yellow"')
          .replace('tts:padding="0c" tts:displayAlign="after"', 'tts:displayAlign="after" tts:textAlign="left"')
          .replace('<tt:style xml:id="italic" tts:fontStyle="italic"/>',
                   '<tt:style xml:id="italic" tts:fontStyle="oblique" tts:textOutline="black 1px" '
                   'tts:textDecoration="lineThrough"/>')
          .replace('begin="10:00:08:00"', 'begin="10:00:08.000"')
          .replace('tts:fontSize="1c 1c"', 'tts:fontSize="1c"'))
check('timecode without its markerMode, a drop mode at a whole rate, a time that is not a timecode, a style on a '
      'paragraph, a region\'s textAlign, an outline, oblique and line-through',
      ttml.check(broken, 'ebu-tt'),
      ['Timed in timecode, without ttp:markerMode: EBU-TT asks for each with ttp:timeBase="smpte"',
       'ttp:dropMode="dropNTSC" at a whole frame rate: EBU-TT asks for nonDrop there',
       'Times that are not timecodes (10:00:08.000): with ttp:timeBase="smpte" EBU-TT writes HH:MM:SS:FF',
       'Style italic says tts:textOutline, which EBU-TT does not have',
       'Region bottom says tts:textAlign: EBU-TT puts that in a <style> the region names',
       'Styles stated on <p> itself: EBU-TT styles content by naming a <style>, never inline',
       'tts:fontStyle="oblique", tts:textDecoration="lineThrough": not a value EBU-TT has',
       'Lengths in px, but <tt> has no tts:extent: a file that measures in pixels has to say how big its picture is'])
plain = ttml.write(SEGMENTS[:1], 'en', profile='ttml1')
check('a TTML 1 file is not EBU-TT: no conformance said, an outline, styles inline',
      ttml.check(plain, 'ebu-tt'),
      ['No ebuttm:conformsToStandard naming EBU-TT Part 1 (urn:ebu:tt:exchange:2017-05)',
       'Style text says tts:textOutline, which EBU-TT does not have',
       'Region bottom says tts:textAlign: EBU-TT puts that in a <style> the region names',
       'Styles stated on <span> itself: EBU-TT styles content by naming a <style>, never inline'])
check('and with no time base or layout at all',
      ttml.check('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ebuttm="urn:ebu:tt:metadata" xml:lang="en"><head>'
                 '<metadata><ebuttm:conformsToStandard>urn:ebu:tt:exchange:2017-05</ebuttm:conformsToStandard>'
                 '</metadata></head><body/></tt>', 'ebu-tt'),
      ['<tt> has no ttp:timeBase: EBU-TT asks a file to say it',
       '<head> has no <styling> with a <style> in it: EBU-TT asks for one',
       '<head> has no <layout> with a <region> in it: EBU-TT asks for one'])

print('EBU STL to EBU-TT and back (EBU Tech 3360)')
GSI = {'OPT': 'Tears of Steel', 'OET': 'Episode 1', 'TPT': 'Lágrimas de Aço', 'TET': 'Episódio 1',
       'TN': 'Ana Tradutora', 'TCD': 'ana@example.org', 'SLR': 'REF-0042', 'CD': '260315', 'RN': '7',
       'CO': 'BRA', 'PUB': 'Blender Foundation', 'EN': 'Edu Editor', 'ECD': 'edu@example.org',
       'UDA': 'U3VidGl0bGQgd2FzIGhlcmU='}
stl_segments = [{'start': 1.0, 'end': 2.0, 'text': 'First'}, {'start': 3.0, 'end': 4.0, 'text': '{\\an8}Second'}]
stl = ebustl.write(stl_segments, fps=25, display='open', start='10:00:00:00', language='pt-br', title='', gsi=GSI)
fields = ebustl.gsi_values(stl)
check('ebustl reads the GSI fields back as it wrote them, the counts and dates its own',
      {key: fields.get(key) for key in GSI}, dict(GSI, RN='07'))
check('and the frame rate and programme start it counts in',
      (fields['DFC'], fields['TCP'], fields['TNS']), ('STL25.01', '10000000', '00002'))
(folder / 'in.stl').write_bytes(stl)
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.stl'))
check('opened through file_io: the GSI kept with the subtitle, its title the title',
      (fmt, session.SUBTITLE['metadata']['title'], session.SUBTITLE['metadata']['stl']['PUB']),
      ('STL', 'Tears of Steel', 'Blender Foundation'))
session.SUBTITLE['segments'] = opened
session.FORMAT = {'format': 'TTML', 'options': {'profile': 'ebu-tt'}}
file_io.save_file(str(folder / 'out.ttml'), 'TTML')
out = (folder / 'out.ttml').read_text(encoding='utf-8')
check('saved as EBU-TT with no video: in timecode at the STL\'s 25, from its programme start',
      ('ttp:timeBase="smpte" ttp:frameRate="25"' in out, 'begin="10:00:01:00"' in out), (True, True))
check('the GSI as Tech 3360 maps it', [re.search(f'<ebuttm:{name}>([^<]*)<', out).group(1) for name in (
    'documentOriginalProgrammeTitle', 'documentOriginalEpisodeTitle', 'documentTranslatedProgrammeTitle',
    'documentTranslatedEpisodeTitle', 'documentTranslatorsName', 'documentTranslatorsContactDetails',
    'documentSubtitleListReferenceCode', 'stlCreationDate', 'stlRevisionNumber', 'documentStartOfProgramme',
    'documentCountryOfOrigin', 'documentPublisher', 'documentEditorsName', 'documentEditorsContactDetails',
    'documentUserDefinedArea')],
    ['Tears of Steel', 'Episode 1', 'Lágrimas de Aço', 'Episódio 1', 'Ana Tradutora', 'ana@example.org', 'REF-0042',
     '2026-03-15', '7', '10:00:00:00', 'BRA', 'Blender Foundation', 'Edu Editor', 'edu@example.org',
     'U3VidGl0bGQgd2FzIGhlcmU='])
check('and is EBU-TT', ttml.check(out, 'ebu-tt'), [])
session.SUBTITLE = {'segments': [], 'language': 'en'}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'out.ttml'))
check('the EBU-TT opened: its subtitles from the programme start, the start kept for STL',
      ([(s['start'], s['text']) for s in opened], session.FORMAT['options'].get('start')),
      ([(1.0, 'First'), (3.0, '{\\an8}Second')], '10:00:00:00'))
session.SUBTITLE['segments'] = opened
session.FORMAT = {'format': 'STL', 'options': {'display': 'open', 'start': '10:00:00:00'}}
file_io.save_file(str(folder / 'back.stl'), 'STL')
back = ebustl.gsi_values((folder / 'back.stl').read_bytes())
check('and saved as EBU STL again: the GSI fields back where they were',
      {key: back.get(key) for key in GSI}, dict(GSI, RN='07'))

print('the export dialog')
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': [dict(s) for s in SEGMENTS], 'language': 'en'}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTML')
panel._refresh_options()
panel.profile_combo.combobox.setCurrentIndex(panel.profile_combo.combobox.findData('ebu-tt'))
check('EBU-TT Part 1 offered, and the file it would write checked',
      (panel.profile_combo.combobox.currentText(), panel.check_label.text()),
      ('EBU-TT Part 1 (production, archive)', 'Nothing Subtitld checks for breaks EBU-TT Part 1 (production, archive) '
                                              'in this file.'))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
