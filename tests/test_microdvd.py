"""MicroDVD (.sub): reading its frames by the rate it declares (or the
video's), its style codes (lower case for a line, upper case for the
whole subtitle, colours in BGR), MPL2's "/" italics and open ends;
telling it from SubViewer 2 and VobSub; writing what players read; and
the export's options.

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
from subtitld.modules import file_io, microdvd, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fps=None):
    return [(s['start'], s['end'], s['text']) for s in microdvd.parse(text, fps)[0]]


SUB = """{1}{1}25
{25}{50}{y:i}Italic line|second
{75}{100}{Y:b}Bold all|both lines
{125}{150}Plain|{y:i}only second italic
{175}{200}{c:$0000FF}Red BGR|{c:$00FF00}green
{225}{250}{y:u}under {y:s}struck
{275}{300}{P:100,50}{f:Arial}{s:30}{H:WINDOWS-1250}Positioned, fonted, sized
{325}{}Open end, until the next
{375}{400}/Slash italic|plain
{425}{450}{y:i,b}Both
{475}{500}{C:$00FFFF}Yellow|whole
"""

print('reading')
check('declared 25 fps; {y:...} to the end of its line, {Y:...} the whole subtitle; colours in BGR; fonts, sizes and '
      'positions go; an open end lasts until the next; "/" italics', cues(SUB), [
          (1.0, 2.0, '<i>Italic line</i>\nsecond'),
          (3.0, 4.0, '<b>Bold all\nboth lines</b>'),
          (5.0, 6.0, 'Plain\n<i>only second italic</i>'),
          (7.0, 8.0, '<font color="#ff0000">Red BGR</font>\n<font color="#00ff00">green</font>'),
          (9.0, 10.0, '<u>under <s>struck</s></u>'),
          (11.0, 12.0, 'Positioned, fonted, sized'),
          (13.0, 15.0, 'Open end, until the next'),
          (15.0, 16.0, '<i>Slash italic</i>\nplain'),
          (17.0, 18.0, '<i><b>Both</b></i>'),
          (19.0, 20.0, '<font color="#ffff00">Yellow\nwhole</font>')])
check('the rate it declares', microdvd.parse(SUB)[1], 25.0)
check('an old {0}{0} declaration is one too, no subtitle', (cues('{0}{0}23.976\n{24}{48}Old\n'), microdvd.parse('{0}{0}23.976\n')[1]),
      ([(1.001, 2.002, 'Old')], 23.976))
check('none declared: the video\'s rate, else 25', (cues('{24}{48}Hi\n', fps=24), cues('{25}{50}Hi\n')),
      ([(1.0, 2.0, 'Hi')], [(1.0, 2.0, 'Hi')]))
check('out of order, blank and stray lines, an empty subtitle', cues('\n{75}{100}Later\nnot a subtitle\n{25}{50}First\n{60}{70}  \n'),
      [(1.0, 2.0, 'First'), (3.0, 4.0, 'Later')])

print('what a .sub is')
check('VobSub, SubViewer 2 (with its header or without), MicroDVD', [microdvd.kind(data) for data in (
    b'\x00\x00\x01\xba\x44\x00\x04', b'[INFORMATION]\n[TITLE]x\n[END INFORMATION]\n[SUBTITLE]\n00:00:01.00,00:00:02.00\nHi[br]there\n',
    b'00:00:01.00,00:00:02.00\nHi\n', b'\n{1}{1}25\n{25}{50}Hi\n')], ['vobsub', 'subviewer', 'subviewer', 'microdvd'])

print('writing')
segments = [
    {'start': 2, 'end': 3, 'text': '<i>whole</i>'},
    {'start': 1, 'end': 2, 'text': '<i>Italic</i> and plain'},
    {'start': 3, 'end': 4, 'text': 'Line one\n<i>line two</i>'},
    {'start': 4, 'end': 5, 'text': 'Plain then <font color="#ff0000">red to the end</font>\n<font color="#ff0000">All red</font>'},
    {'start': 5, 'end': 6, 'text': '<b><u>bold under</u></b>\n<b>bold</b>'},
    {'start': 6, 'end': 7, 'text': '{\\an8}top | pipe'},
    {'start': 7, 'end': 8, 'text': ' <i> </i> '},
]
written = microdvd.write(segments, 23.976)
check('the rate first; frames at it, in order; "|" between lines; a whole style upper case at the start, a whole line\'s '
      'lower case at its start; part of a line plain; no place; no empty subtitle', written.split('\n'), [
          '{1}{1}23.976', '{24}{48}Italic and plain', '{48}{72}{Y:i}whole', '{72}{96}Line one|{y:i}line two',
          '{96}{120}Plain then red to the end|{c:$0000FF}All red', '{120}{144}{Y:b}{y:u}bold under|bold', '{144}{168}top ¦ pipe', ''])
check('read back', [s['text'] for s in microdvd.parse(written)[0]], [
    'Italic and plain', '<i>whole</i>', 'Line one\n<i>line two</i>', 'Plain then red to the end\n<font color="#ff0000">All red</font>',
    '<b><u>bold under</u>\nbold</b>', 'top ¦ pipe'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'polish.sub').write_bytes('{1}{1}25\n{25}{50}Zażółć gęślą jaźń|{y:i}druga\n'.encode('cp1250') * 1)
session.FORMAT = {}
session.VIDEO = {'framerate': 30.0}
opened, fmt = file_io.process_subtitles_file(str(folder / 'polish.sub'))
check('opens a Windows-1250 file at the rate it declares, and keeps the rate to save with', (
    fmt, opened[0]['text'], opened[0]['start'], session.FORMAT['options'].get('fps')),
      ('SUB', 'Zażółć gęślą jaźń\n<i>druga</i>', 1.0, 25.0))
(folder / 'subviewer.sub').write_text('[INFORMATION]\n[END INFORMATION]\n[SUBTITLE]\n00:00:01.00,00:00:02.50\nOne[br]two\n',
                                      encoding='utf-8')
session.FORMAT = {}
viewer, viewer_format = file_io.process_subtitles_file(str(folder / 'subviewer.sub'))
check('a SubViewer .sub opens, as SBV', (viewer_format, viewer[0]['start'], viewer[0]['end'], viewer[0]['text']),
      ('SBV', 1.0, 2.5, 'One\ntwo'))
(folder / 'vobsub.sub').write_bytes(b'\x00\x00\x01\xba' + bytes(2044))
try:
    file_io.process_subtitles_file(str(folder / 'vobsub.sub'))
    check('VobSub: says its subtitles are pictures', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError:
    check('VobSub: says its subtitles are pictures', True, True)
session.SUBTITLE['segments'] = segments
session.FORMAT = {'options': {'fps': 25.0, 'encoding': 'cp1250', 'line_ends': 'crlf'}}
file_io.save_file(str(folder / 'out.sub'), 'SUB')
saved = (folder / 'out.sub').read_bytes()
check('exported: its rate, its encoding and line ends', (saved.split(b'\r\n')[:2], b'\r\n' in saved),
      ([b'{1}{1}25', b'{25}{50}Italic and plain'], True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SUB')
panel._refresh_options()
check('SUB: the frame rate, the encoding and line ends; Windows-1250 and 1251 offered', (
    panel.sub_options.isHidden(), panel.srt_options.isHidden(), panel.encoding_combo.combobox.findData('cp1250') >= 0,
    panel.encoding_combo.combobox.findData('cp1251') >= 0), (False, False, True, True))
panel.encoding_combo.combobox.setCurrentIndex(panel.encoding_combo.combobox.findData('cp1250'))
check("chosen; the frame rate the video's", {key: panel.get_config()['options'].get(key) for key in ('fps', 'encoding', 'line_ends')},
      {'fps': 30.0, 'encoding': 'cp1250', 'line_ends': 'lf'})

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
