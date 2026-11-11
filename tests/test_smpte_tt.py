"""SMPTE-TT (SMPTE ST 2052-1): written as the conservative TTML 1 that
Premiere and the broadcast tools import, declared in ttp:profile, checked by
ttml.check against the standard's rules; read with its own vocabulary — the
pictures it holds in smpte:image, the CEA-608 and CEA-708 captions it
tunnels in smpte:data (RP 2052-10, RP 2052-11), frame-based and SMPTE
timecode times, the regions RP 2052-10 makes of CEA-608's rows and columns —
and opened through file_io, a .xml that is TTML by its root.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import base64
import os
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import cea708, config, file_io, markup, picture_ocr, scc, session, ttml
from subtitld.interface.export_dialog import _SubtitlesPanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def png(width, height):
    """A white-on-transparent PNG, as a picture subtitle is."""
    rows = b''.join(b'\x00' + b'\xff\xff\xff\xff' * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>', 'speaker': 'ANNA'},
            {'start': 3.0, 'end': 4.4, 'text': '{\\an8}A sign at the top'},
            {'start': 5.0, 'end': 7.0, 'text': 'Two lines\nof <b>dialogue</b>'}]

print('written')
written = ttml.write(SEGMENTS, 'en', agents=True, profile='smpte-tt')
check('declared in ttp:profile, by the designator ST 2052-1 gives it',
      'ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full"' in written, True)
check('the media time base and clock times, nothing in frames or ticks',
      ('ttp:timeBase="media"' in written, 'begin="00:00:01.000" end="00:00:02.500"' in written,
       'frameRate' in written), (True, True, False))
check('no SMPTE vocabulary it does not need: no namespace, no smpte:information',
      ('xmlns:smpte' in written, 'information' in written), (False, False))
check('ttml.check finds nothing wrong with it', ttml.check(written, 'smpte-tt'), [])
check('read back as it was written', [(s['start'], s['end'], s['text'], s.get('speaker')) for s in ttml.parse(written)],
      [(1.0, 2.5, 'Hello <i>there</i>', 'ANNA'), (3.0, 4.4, '{\\an8}A sign at the top', None),
       (5.0, 7.0, 'Two lines\nof <b>dialogue</b>', None)])
at_rate = ttml.write(SEGMENTS, 'en', profile='smpte-tt', fps=30000 / 1001)
check('the video\'s frame rate stated: 29.97 as 30 at 1000/1001',
      ('ttp:frameRate="30"' in at_rate, 'ttp:frameRateMultiplier="1000 1001"' in at_rate), (True, True))
check('and 25 as itself', ('ttp:frameRate="25"' in ttml.write(SEGMENTS, 'en', profile='smpte-tt', fps=25),
                          'Multiplier' in ttml.write(SEGMENTS, 'en', profile='smpte-tt', fps=25)), (True, False))
check('times still read as they were', [s['start'] for s in ttml.parse(at_rate)], [1.0, 3.0, 5.0])
check('TTML2 styling is not SMPTE-TT\'s: an emphasis mark is left out',
      'textEmphasis' in ttml.write([{'start': 1, 'end': 2, 'text': '<font emphasis="filled dot">強調</font>'}],
                                   'ja', profile='smpte-tt'), False)

print('CEA-608 tunnelled (RP 2052-10)')
tunnelled = ttml.write(SEGMENTS, 'en', profile='smpte-tt', tunnel=True)
check('the namespaces, and smpte:information saying what is tunnelled and from which field',
      ('xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt"' in tunnelled,
       'xmlns:m608="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt#cea608"' in tunnelled,
       'm608:channel="CC1" m608:fieldStart="1"' in tunnelled), (True, True, True))
datas = re.findall(r'<div begin="([^"]+)" end="[^"]+">\s*<metadata>\s*<smpte:data encoding="Base64" '
                   r'datatype="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt#cea608">([^<]*)</smpte:data>',
                   tunnelled)
check('the data in <div>s of their own, each beginning on its first frame', len(datas) >= 1, True)
raw = base64.b64decode(''.join(datas[0][1].split()))
check('field 1\'s pair and field 2\'s by turns, field 2 empty', set(raw[2::4]) | set(raw[3::4]), {0x80})
check('field 1 carries the codes SCC would (odd parity throughout)',
      all(bin(byte).count('1') % 2 for byte in raw), True)
check('still nothing wrong in it', ttml.check(tunnelled, 'smpte-tt'), [])
check('with text in it, the text is what is read', [s['text'] for s in ttml.parse(tunnelled)],
      [s['text'] for s in SEGMENTS])
only_data = re.sub(r'<p [^>]*>.*?</p>', '', tunnelled, flags=re.S)
decoded = ttml.parse(only_data)
check('with no text, the captions are decoded from it, as an SCC is read',
      [markup.plain(s['text']) for s in decoded], [markup.plain(s['text']) for s in SEGMENTS])
check('each where it was, to a frame',
      [abs(a['start'] - b['start']) < 0.05 and abs(a['end'] - b['end']) < 0.1 for a, b in zip(decoded, SEGMENTS)],
      [True, True, True])
check('as SCC reads the same captions', [s['text'] for s in decoded],
      [s['text'] for s in scc.parse(scc.write(SEGMENTS))])
check('a tunnel with no fieldStart is named',
      ttml.check(tunnelled.replace(' m608:fieldStart="1"', ''), 'smpte-tt')[-1],
      'CEA-608 data tunnelled, but no m608:fieldStart says which field its first pair is from')
starts_two = only_data.replace('m608:fieldStart="1"', 'm608:fieldStart="2"')
starts_two = re.sub(r'(datatype="[^"]+">)\s*([^<]+)',
                    lambda m: m.group(1) + base64.b64encode(b'\x80\x80' + base64.b64decode(''.join(m.group(2).split()))).decode(),
                    starts_two)
check('m608:fieldStart="2": the first pair is field 2\'s, and the rest falls into place',
      [markup.plain(s['text']) for s in ttml.parse(starts_two)], [markup.plain(s['text']) for s in SEGMENTS])

print('CEA-708 tunnelled (RP 2052-11)')
_words, _breaks, shows = scc.frame_words(SEGMENTS, 30000 / 1001)
dtvcc = cea708.frame_data([(text, shown, cleared) for text, shown, cleared, *_rest in shows], 18)
frames = []
for frame in range(0, max(dtvcc) + 2):
    entries = [(0xFC, 0x80, 0x80), (0xFD, 0x80, 0x80)]
    entries += [(0xFF if kind == 3 else 0xFE, first, second) for kind, first, second in dtvcc.get(frame, [])]
    entries += [(0xFA, 0, 0)] * (20 - len(entries))
    frames.append(bytes([0x40 | 20, 0xFF]) + b''.join(bytes(entry) for entry in entries))
cc_data = base64.b64encode(b''.join(frames)).decode()
SMPTE708 = f'''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt"
    xmlns:m708="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt#cea708"
    ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2013/profiles/smpte-tt-full" xml:lang="">
  <head>
    <metadata>
      <smpte:information mode="Preserved" origin="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt#cea708"
                         m708:number="1"/>
    </metadata>
    <layout><region xml:id="r1"/></layout>
  </head>
  <body>
    <div begin="00:00:00.000"><metadata>
      <smpte:data encoding="Base64" datatype="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt#cea708">{cc_data}</smpte:data>
    </metadata></div>
  </body>
</tt>'''
decoded = ttml.parse(SMPTE708)
check('cc_data() a frame, at the rate 20 entries a frame make (29.97): service 1 decoded',
      [markup.plain(s['text']) for s in decoded], [markup.plain(s['text']) for s in SEGMENTS])
check('to a frame of where they were shown', [abs(a['start'] - b['start']) < 0.05 for a, b in zip(decoded, SEGMENTS)],
      [True, True, True])
check('the 2013 edition\'s namespace and profile are SMPTE-TT\'s too', ttml.check(SMPTE708, 'smpte-tt'), [])

print('times')
FRAMES = '''<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xml:lang="en"
    ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full"
    ttp:timeBase="{base}" ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001" ttp:dropMode="{drop}" {extra}>
  <body><div>
    <p begin="{begin}" end="{end}">Frames</p>
  </div></body>
</tt>'''
media = FRAMES.format(base='media', drop='nonDrop', extra='', begin='00:00:01:15', end='00:00:02:00')
check('media time base: hours, minutes and seconds are real ones, a frame 1/29.97 of a second',
      [(s['start'], s['end']) for s in ttml.parse(media)], [(round(1 + 15 / (30000 / 1001), 3), 2.0)])
dropped = FRAMES.format(base='smpte', drop='dropNTSC', extra='ttp:markerMode="continuous"',
                        begin='00:01:00:02', end='00:10:00:00')
check('smpte time base, dropNTSC: 00:01:00;02 is frame 1800 and 00:10:00;00 frame 17982, at 29.97',
      [(s['start'], s['end']) for s in ttml.parse(dropped)],
      [(round(1800 / (30000 / 1001), 3), round(17982 / (30000 / 1001), 3))])
non_drop = FRAMES.format(base='smpte', drop='nonDrop', extra='', begin='00:01:00:00', end='00:01:01:00')
check('smpte time base, nonDrop: the label counts 30 a second, the frames come at 29.97',
      ttml.parse(non_drop)[0]['start'], round(1800 / (30000 / 1001), 3))
check('frames and labels both ways, drop-frame and not',
      [ttml.timecode_label(count, 30, 'dropNTSC') for count in (1799, 1800, 17982, 107892)]
      + [ttml.timecode_label(3000, 25, 'dropPAL'), ttml.timecode_label(90000, 25)],
      ['00:00:59:29', '00:01:00:02', '00:10:00:00', '01:00:00:00', '00:02:00:04', '01:00:00:00'])
check('every frame of a day comes back to itself through its label',
      all(ttml.timecode_frames(*map(int, ttml.timecode_label(count, nominal, drop).split(':')), nominal, drop) == count
          for count in range(0, 2600000, 1777) for nominal, drop in ((30, 'dropNTSC'), (25, 'dropPAL'), (24, ''))),
      True)
check('ST 2052-1: the time base should be media, and an offset counted in ticks',
      ttml.check(dropped.replace('begin="00:01:00:02"', 'begin="45f"'), 'smpte-tt'),
      ['SMPTE-TT asks for ttp:timeBase="media": "smpte" and "clock" should not be used',
       'Offset times in f: an offset time in SMPTE-TT is counted in ticks (t)'])

print('CEA-608\'s grid as RP 2052-10 converts it')
CONVERTED = '''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"
    xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt"
    xmlns:m608="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt#cea608"
    ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full"
    ttp:cellResolution="40 19" xml:lang="en">
  <head>
    <metadata>
      <smpte:information origin="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt#cea608" mode="Preserved"
                         m608:channel="CC1"/>
    </metadata>
    <styling>
      <style xml:id="basic" tts:color="white" tts:fontFamily="monospace" tts:fontSize="1c"/>
    </styling>
    <layout>
      <region xml:id="pop1" tts:backgroundColor="transparent">
        <set begin="00:00:01.000" end="00:00:03.000" tts:origin="10% 79%"/>
        <set begin="00:00:04.000" end="00:00:06.000" tts:origin="35% 10%"/>
      </region>
      <region xml:id="pop2" tts:origin="10% 47%"/>
    </layout>
  </head>
  <body>
    <div style="basic">
      <p region="pop1" begin="00:00:01.000" end="00:00:03.000"><span tts:backgroundColor="black">At the bottom,<br/>left</span></p>
      <p region="pop1" begin="00:00:04.000" end="00:00:06.000"><span tts:backgroundColor="black">Indented at the top</span></p>
      <p region="pop2" begin="00:00:07.000" end="00:00:08.000"><span tts:backgroundColor="black">Row 9</span></p>
    </div>
  </body>
</tt>'''
placed = ttml.parse(CONVERTED)
check('a region moved by <set> is where the set puts it when the caption begins; one with an origin and no extent '
      'holds text from its origin, left-aligned',
      [(markup.split_lead(s['text'])[0], s.get('position')) for s in placed],
      [('{\\an1}', {'v': '10.47%'}), ('{\\an7}', {'h': '35%'}), ('{\\an4}', {'v': '-0.368%'})])
check('a conversion from CEA-608 is SMPTE-TT, a profile check finding nothing', ttml.check(CONVERTED, 'smpte-tt'), [])

print('pictures (smpte:image)')
logo = base64.b64encode(png(100, 40)).decode()
PICTURES = f'''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"
    xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt"
    ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full" tts:extent="1920px 1080px"
    xml:lang="de">
  <head>
    <metadata>
      <smpte:image xml:id="sub1" imageType="PNG" encoding="Base64">{logo}</smpte:image>
      <smpte:image xml:id="sub2" imageType="PNG" encoding="Base64">{logo}</smpte:image>
    </metadata>
    <layout>
      <region xml:id="low" tts:origin="910px 950px" tts:extent="100px 40px"/>
    </layout>
  </head>
  <body region="low">
    <div begin="00:00:01.000" end="00:00:02.000" smpte:backgroundImage="#sub1"/>
    <div begin="00:00:03.000" end="00:00:04.000" smpte:backgroundImage="#sub2"><p>Guten Tag</p></div>
  </body>
</tt>'''
(folder / 'pictures.xml').write_text(PICTURES, encoding='utf-8')
images, language, size = ttml.read_images(str(folder / 'pictures.xml'))
check('the pictures it holds, where their region puts them, the text beside one as its words',
      [(image['start'], image['data'] == png(100, 40), image['alt'], image['origin']) for image in images],
      [(1.0, True, '', images[0]['origin']), (3.0, True, 'Guten Tag', images[0]['origin'])])
check('placed in % of the picture the file gives the size of',
      tuple(round(value, 2) for value in images[0]['origin']), (47.4, 87.96))
check('the one with words is read as them', [s['text'] for s in ttml.parse(PICTURES)], ['Guten Tag'])
check('pictures in SMPTE-TT are its own: nothing wrong', ttml.check(PICTURES, 'smpte-tt'), [])
try:
    file_io.process_subtitles_file(str(folder / 'pictures.xml'))
    check('a picture without words goes to an OCR add-on', 'opened', 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('a picture without words goes to an OCR add-on, as IMSC\'s do', error.kind, 'imsc')
pictures, _language = picture_ocr.imsc_pictures(str(folder / 'pictures.xml'))
check('and picture_ocr decodes the PNGs the file holds',
      [(p.x, p.y, p.width, p.height) for p in pictures], [(910, 950, 100, 40), (910, 950, 100, 40)])
broken = PICTURES.replace('imageType="PNG" encoding="Base64">' + logo, 'imageType="GIF" encoding="Base64">' + logo, 1) \
    .replace('#sub2', '#sub3').replace('<smpte:image xml:id="sub2"', '<smpte:data datatype="cea608" encoding="Base64">AAAA</smpte:data><smpte:image xml:id="sub2"')
check('a picture that is not a PNG, a datatype SMPTE does not define, a picture the file does not hold',
      ttml.check(broken, 'smpte-tt'),
      ['smpte:image with imageType="GIF": SMPTE-TT\'s pictures are PNG',
       'smpte:data with datatype="cea608": the datatype is one SMPTE defines (CEA-608\'s, CEA-708\'s), '
       'or a private one beginning "x-"',
       'smpte:backgroundImage="#sub3" names no smpte:image the file holds'])
check('a private datatype is the file\'s own business',
      ttml.check(broken.replace('datatype="cea608"', 'datatype="x-private"'), 'smpte-tt')[1:2],
      ['smpte:backgroundImage="#sub3" names no smpte:image the file holds'])
check('no profile declared, and two smpte:information, one of them in the body',
      ttml.check(CONVERTED.replace('ttp:profile="http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full"', '')
                 .replace('<div style="basic">', '<div style="basic"><metadata><smpte:information mode="Faithful"/></metadata>'),
                 'smpte-tt'),
      ['ttp:profile does not name SMPTE-TT\'s profile '
       '(http://www.smpte-ra.org/schemas/2052-1/2010/profiles/smpte-tt-full)',
       '2 smpte:information elements: a document has one, in <head>',
       'smpte:information outside <head>\'s <metadata>',
       'smpte:information mode="Faithful": it is Enhanced or Preserved'])

print('the export dialog')
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': [dict(s) for s in SEGMENTS], 'language': 'en'}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTML')
panel._refresh_options()
check('SMPTE-TT offered among the text profiles, before the pictures',
      [panel.profile_combo.combobox.itemData(i) for i in range(panel.profile_combo.combobox.count())],
      list(ttml.PROFILES + ttml.IMAGE_PROFILES))
check('the CEA-608 tunnel is not offered for another profile', panel.tunnel_608_check.isHidden(), True)
panel.profile_combo.combobox.setCurrentIndex(panel.profile_combo.combobox.findData('smpte-tt'))
check('for SMPTE-TT it is, and the file it would write is checked', (panel.tunnel_608_check.isHidden(),
      panel.check_label.text()), (False, 'Nothing Subtitld checks for breaks SMPTE-TT in this file.'))
panel.tunnel_608_check.setChecked(True)
check('what the export asks for', {key: value for key, value in panel.get_config()['options'].items()
                                   if key in ('profile', 'tunnel_608')}, {'profile': 'smpte-tt', 'tunnel_608': True})

print('through file_io')
(folder / 'smpte.xml').write_text(written, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'smpte.xml'))
check('a .xml that is TTML by its root opens as TTML, not as YouTube\'s, DCP\'s or Captionate\'s',
      (fmt, [s['text'] for s in opened]), ('TTML', [s['text'] for s in SEGMENTS]))
(folder / 'tunnel.xml').write_text(only_data, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'tunnel.xml'))
check('a SMPTE-TT holding only the CEA-608 it was converted from opens with its captions',
      [markup.plain(s['text']) for s in opened], [markup.plain(s['text']) for s in SEGMENTS])
session.SUBTITLE = {'segments': [dict(s) for s in SEGMENTS], 'language': 'en'}
session.VIDEO = {'framerate': 25}
session.FORMAT = {'format': 'TTML', 'options': {'profile': 'smpte-tt', 'tunnel_608': True}}
file_io.save_file(str(folder / 'out.xml'), 'TTML')
out = (folder / 'out.xml').read_text(encoding='utf-8')
check('saved through file_io: the profile, the video\'s rate and the tunnel the export asked for',
      ('smpte-tt-full' in out, 'ttp:frameRate="25"' in out, '<smpte:data' in out, ttml.check(out, 'smpte-tt')),
      (True, True, True, []))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
