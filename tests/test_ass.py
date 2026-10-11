"""SubStation Alpha and Advanced SubStation Alpha: reading what subtitlers
and fansubbers write (styles, override codes, actors, comments, drawings,
wrap styles, SSA v4), writing what players show, and the export's look.

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
import pysubs2
from subtitld.modules import ass, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


ASS = r"""[Script Info]
Title: Tears of Steel
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,50,1
Style: Thoughts,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,-1,0,0,100,100,0,0,1,3,1,2,40,40,50,1
Style: Sign,Arial,60,&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,1,8,40,40,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:05.00,0:00:06.00,Default,Celia,0,0,0,,Later line
Dialogue: 0,0:00:01.00,0:00:02.50,Default,Thom,0,0,0,,You're a {\i1}jerk{\i0},\NCelia.
Comment: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,This is a comment, not a subtitle
Dialogue: 0,0:00:03.00,0:00:04.00,Thoughts,Celia,0,0,0,,Thinking {\i0}out{\r} loud
Dialogue: 1,0:00:03.00,0:00:04.00,Sign,,0,0,0,,{\pos(960,100)\fad(200,200)}ROBOTICS LAB
Dialogue: 0,0:00:04.10,0:00:04.90,Default,,0,0,0,,{\c&H00D7FF&}Gold{\c} and {\b1}bold{\b0}, {\b700}heavy{\b400} and {\s1}struck{\s0}, {\u1}under{\u0}
Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,{\k20}Ka{\k30}ra{\k40}o{\k50}ke
Dialogue: 0,0:00:09.00,0:00:10.00,Default,,0,0,0,,{\p1}m 0 0 l 100 0 100 100 0 100{\p0}
Dialogue: 0,0:00:09.50,0:00:10.00,Default,,0,0,0,,{\p1}m 0 0 l 1 1{\p0}Text after a drawing
Dialogue: 0,0:00:11.00,0:00:12.00,Default,,0,0,0,,Soft\nbreak and hard\hspace
Dialogue: 0,0:00:13.00,0:00:14.00,Default,,0,0,0,,{\an7}Top left
Dialogue: 0,0:00:15.00,0:00:16.00,Default,,0,0,0,,{\rSign}Restyled as a sign
Dialogue: 0,0:00:17.00,0:00:18.00,Default,,0,0,0,,\N\N  Spaces and breaks \N\N around  \N
"""


def read(text, encoding='utf-8'):
    path = Path(tempfile.mkdtemp()) / 'in.ass'
    path.write_bytes(text.encode(encoding))
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ()) for s in ass.read(str(path))]


print('reading')
check('every event as Subtitld keeps it', read(ASS), [
    (1.0, 2.5, "You're a <i>jerk</i>,\nCelia.", 'Thom'),
    (3.0, 4.0, '<i>Thinking </i>out<i> loud</i>', 'Celia'),
    (3.0, 4.0, '{\\an8}<font color="#ffff00"><b>ROBOTICS LAB</b></font>'),
    (4.1, 4.9, '<font color="#ffd700">Gold</font> and <b>bold</b>, <b>heavy</b> and <s>struck</s>, <u>under</u>'),
    (5.0, 6.0, 'Later line', 'Celia'),
    (7.0, 8.0, 'Karaoke'),
    (9.5, 10.0, 'Text after a drawing'),
    (11.0, 12.0, 'Soft break and hard space'),
    (13.0, 14.0, '{\\an7}Top left'),
    (15.0, 16.0, '{\\an8}<font color="#ffff00"><b>Restyled as a sign</b></font>'),
    (17.0, 18.0, 'Spaces and breaks\naround'),
])
check('WrapStyle 2: \\n is a line break', [t[2] for t in read(ASS.replace('WrapStyle: 0', 'WrapStyle: 2')) if 'Soft' in t[2]],
      ['Soft\nbreak and hard space'])
check('Windows-1252', [t[2] for t in read(ASS.replace('Later line', 'Ação é café'), 'cp1252') if t[0] == 5.0], ['Ação é café'])
check('a byte order mark and Windows line ends', len(read('﻿' + ASS.replace('\n', '\r\n'))), 11)
SSA = r"""[Script Info]
ScriptType: v4.00

[V4 Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding
Style: Default,Arial,28,16777215,65535,65535,-2147483640,0,0,1,2,1,2,10,10,10,0,0
Style: Top,Arial,28,16777215,65535,65535,-2147483640,0,-1,1,2,1,6,10,10,10,0,0

[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: Marked=0,0:00:01.00,0:00:02.00,Default,Thom,0000,0000,0000,,SSA line\Nsecond
Dialogue: Marked=0,0:00:03.00,0:00:04.00,Top,,0000,0000,0000,,Italic at the top
Dialogue: Marked=0,0:00:05.00,0:00:06.00,Default,,0000,0000,0000,,{\a9}SSA's middle left
"""
check('SSA v4, its alignments', read(SSA), [(1.0, 2.0, 'SSA line\nsecond', 'Thom'), (3.0, 4.0, '{\\an8}<i>Italic at the top</i>'),
                                           (5.0, 6.0, "{\\an4}SSA's middle left")])

print('writing')
segments = [
    {'start': 3.0, 'end': 4.0, 'text': 'Second', 'speaker': 'Celia'},
    {'start': 1.0, 'end': 2.5, 'text': '{\\an8}<font color="#ffd700">Why</font>\n<i>you\'re <b>freaked</b> out</i>', 'speaker': 'Thom'},
    {'start': 2.6, 'end': 2.9, 'text': ' ', 'speaker': 'A'},
]
written = ass.write(segments, 1920, 800)
events = [line for line in written.splitlines() if line.startswith('Dialogue:')]
check('sized to the video', ('PlayResX: 1920' in written, 'PlayResY: 800' in written, 'ScaledBorderAndShadow: yes' in written),
      (True, True, True))
style = pysubs2.SSAFile.from_string(written).styles['Default']
check('the style scaled to its height', (style.fontname, style.fontsize, style.outline, style.marginv, int(style.alignment)),
      ('Arial', 44.0, 2.2, 40, 2))
check('events in order, the speaker as the actor, empty ones left out', events, [
    "Dialogue: 0,0:00:01.00,0:00:02.50,Default,Thom,0,0,0,,{\\an8}{\\c&H00D7FF&}Why{\\c}\\N{\\i1}you're {\\b1}freaked{\\b0} out{\\i0}",
    'Dialogue: 0,0:00:03.00,0:00:04.00,Default,Celia,0,0,0,,Second'])
check('read back as written', [(s['text'], s.get('speaker')) for s in ass.segments_of(pysubs2.SSAFile.from_string(written))],
      [(segments[1]['text'], 'Thom'), ('Second', 'Celia')])
preview = {'font_family': 'Montserrat', 'color': '#ffffd700', 'shadow_enabled': True, 'shadow_color': '#ff000000', 'shadow_x': 2,
           'backgroundbox_enabled': True, 'backgroundbox_color': '#55000000', 'backgroundbox_padding': 10}
looked = pysubs2.SSAFile.from_string(ass.write(segments, 1920, 1080, look='preview', preview=preview)).styles['Default']
check("the preview's look: font, colour, a translucent box, no shadow behind it",
      (looked.fontname, looked.primarycolor, looked.borderstyle, looked.outlinecolor, looked.outline, looked.shadow),
      ('Montserrat', pysubs2.Color(255, 0xd7, 0, 0), 3, pysubs2.Color(0, 0, 0, 0xaa), 10.0, 0))
preview['backgroundbox_enabled'] = False
looked = pysubs2.SSAFile.from_string(ass.write(segments, 1920, 1080, look='preview', preview=preview)).styles['Default']
check('without the box: its shadow', (looked.borderstyle, looked.outline, looked.shadow), (1, 0, 2.0))
ssa_text = ass.write(segments, 1920, 1080, fmt='ssa')
check('SSA v4: its sections and its codes for the position', ('[V4 Styles]' in ssa_text, '{\\a6}' in ssa_text, '\\an' in ssa_text),
      (True, True, False))

print('through file_io')
folder = Path(tempfile.mkdtemp())
source = folder / 'in.ass'
source.write_text(ASS, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(source))
check('opens', (len(opened), fmt, opened[0].get('speaker')), (11, 'ASS', 'Thom'))
sub = folder / 'in.sub'
sub.write_text('{1}{1}25\n{25}{50}{y:i}Italic line|second\n{75}{100}Plain\n', encoding='utf-8')
check('MicroDVD keeps its italics, {y:i} to the end of its line', [(s['start'], s['text']) for s in file_io.process_subtitles_file(str(sub))[0]],
      [(1.0, '<i>Italic line</i>\nsecond'), (3.0, 'Plain')])
session.SUBTITLE['segments'] = segments
session.SUBTITLE['language'] = 'en'
session.VIDEO = {'width': 1280, 'height': 720}
session.CONFIG['videoplayer'] = {'font_family': 'Ubuntu', 'backgroundbox_enabled': False}
for look, font in (('clean', 'Arial'), ('preview', 'Ubuntu')):
    session.FORMAT['options'] = {'look': look}
    file_io.save_file(str(folder / 'out.ass'), 'ASS')
    saved = pysubs2.SSAFile.load(str(folder / 'out.ass'))
    check(f'exported, {look}', (saved.info['PlayResY'], saved.styles['Default'].fontname, saved.styles['Default'].fontsize),
          ('720', font, 40.0))
session.FORMAT['options'] = {}
file_io.save_file(str(folder / 'out.ssa'), 'ASS')
check('a .ssa is written as SSA', '[V4 Styles]' in (folder / 'out.ssa').read_text(encoding='utf-8'), True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('ASS')
panel._refresh_options()
check('ASS: its look offered', (panel.ass_options.isHidden(), panel.look_combo.combobox.currentData()), (False, 'clean'))
panel.look_combo.combobox.setCurrentIndex(1)
check('chosen', panel.get_config()['options'].get('look'), 'preview')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.ass_options.isHidden(), 'look' in panel.get_config()['options']), (True, False))
again = _SubtitlesPanel()
check('remembered', again.look_combo.combobox.currentData(), 'preview')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
