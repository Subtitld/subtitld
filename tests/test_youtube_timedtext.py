"""YouTube's timed text in XML: srv3 read (pens, window positions, runs,
the body's own text, automatic captions rolling) and written (a pen a
style, a position a place but the bottom centre), srv1 read, and both
through file_io (.srv3, an .xml told from TTML by its root) and the dialog.

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
from subtitld.modules import file_io, session, youtube_timedtext as ytt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data):
    return [(s['start'], s['end'], s['text']) for s in ytt.parse(data)]


print('reading srv3')
SRV3 = b'''<?xml version="1.0" encoding="utf-8" ?><timedtext format="3">
<head>
<pen id="1" b="1" fc="#FFFF00"/>
<pen id="2" i="1" u="1"/>
<wp id="1" ap="1" ah="50" av="0"/>
</head>
<body>
<p t="1000" d="1500"><s p="1">Bold yellow</s> and <s p="2">italic</s></p>
<p t="3000" d="1000" wp="1">Top
line</p>
<p t="5000" d="1000">Don&#39;t &quot;quote&quot;</p>
<p t="7000">No duration: 5 s</p>
</body></timedtext>'''
check('pens as tags; a position as a place; the text, its entities, its line breaks; 5 s without a duration', cues(SRV3), [
    (1.0, 2.5, '<font color="#ffff00"><b>Bold yellow</b></font> and <i><u>italic</u></i>'),
    (3.0, 4.0, '{\\an8}Top\nline'), (5.0, 6.0, 'Don\'t "quote"'), (7.0, 12.0, 'No duration: 5 s')])
ROLLING = b'''<timedtext format="3"><head><ws id="1" mh="2"/><wp id="1" ap="6" ah="20" av="100"/></head><body>
<w t="0" id="1" wp="1" ws="1"/>
<p t="160" d="3840" w="1"><s>hi</s><s t="400"> i'm</s></p>
<p t="1990" d="2010" w="1" a="1">
</p>
<p t="2000" d="4000" w="1"><s>taylor</s></p>
</body></timedtext>'''
check('automatic captions: a line until the next, no place', cues(ROLLING), [(0.16, 2.0, "hi i'm"), (2.0, 6.0, 'taylor')])

print('reading srv1')
check('<transcript>: seconds, its text escaped once more', cues(
    b'<transcript><text start="1.5" dur="2">It&amp;#39;s here</text><text start="4" dur="1"> </text></transcript>'),
      [(1.5, 3.5, "It's here")])
try:
    ytt.parse(b'<tt xmlns="http://www.w3.org/ns/ttml"/>')
    check('other XML: refused', None, 'ValueError')
except ValueError as error:
    check('other XML: refused', str(error).startswith('Not YouTube timed text'), True)

print('writing srv3')
segments = [{'start': 1, 'end': 2.5, 'text': '<b><font color="#ffff00">Bold yellow</font></b> and <i>italic</i>'},
            {'start': 3, 'end': 4, 'text': '{\\an8}Top\nline'}, {'start': 5, 'end': 6, 'text': 'A & B <3 <i>also</i> <i>twice</i>'},
            {'start': 7, 'end': 8, 'text': '{\\an1}<i>Low left</i>'}, {'start': 9, 'end': 9, 'text': ' '}]
written = ytt.write(segments)
check('a pen a style, a position a place, no position for the bottom centre', [
    line for line in written.splitlines() if line.startswith(('<pen', '<wp'))], [
        '<pen id="1" b="1" fc="#FFFF00" />', '<pen id="2" i="1" />',
        '<wp id="1" ap="1" ah="50" av="0" />', '<wp id="2" ap="6" ah="0" av="100" />'])
check('read back', cues(written.encode()), [
    (1.0, 2.5, '<font color="#ffff00"><b>Bold yellow</b></font> and <i>italic</i>'), (3.0, 4.0, '{\\an8}Top\nline'),
    (5.0, 6.0, 'A & B <3 <i>also</i> <i>twice</i>'), (7.0, 8.0, '{\\an1}<i>Low left</i>')])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.srv3').write_bytes(SRV3)
(folder / 'youtube.xml').write_bytes(SRV3)
(folder / 'ttml.xml').write_text('<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1s" end="2s">TTML</p></div></body></tt>',
                                 encoding='utf-8')
session.FORMAT = {}
check('.srv3 and an .xml of timed text: XML; a TTML .xml: TTML', [
    (file_io.process_subtitles_file(str(folder / name))[1], file_io.process_subtitles_file(str(folder / name))[0][0]['text'][-4:])
    for name in ('in.srv3', 'youtube.xml', 'ttml.xml')], [('XML', '</i>'), ('XML', '</i>'), ('TTML', 'TTML')])
session.SUBTITLE = {'language': 'en-us', 'segments': segments}
session.FORMAT = {'options': {'speakers': 'none', 'formatting': 'remove'}}
file_io.save_file(str(folder / 'out.srv3'), 'XML')
check('exported, with the export\'s options (formatting, and so the place, removed)', [s['text'] for s in ytt.read(folder / 'out.srv3')][:2],
      ['Bold yellow and italic', 'Top\nline'])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('XML')
panel._refresh_options()
check('XML: the text options', (panel.text_options.isHidden(), panel.formatting_combo.isHidden()), (False, False))
check('its extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['XML']['extensions'][0], 'srv3')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
