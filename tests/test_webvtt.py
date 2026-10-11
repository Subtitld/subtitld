"""WebVTT: reading the files browsers and players read (header, NOTE,
STYLE and REGION blocks, identifiers, cue settings, voices, classes,
entities), writing what they show, and the export's voice tags.

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
from subtitld.modules import file_io, session, webvtt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text):
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ())
            for s in webvtt.parse(webvtt.decode(text.encode('utf-8')))]


print('the file')
check('basic, with formatting', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello there.\nSecond line.\n\n'
                                     '00:00:03.000 --> 00:00:04.000\n<i>Italic</i> and <b>bold</b>\n'),
      [(1.0, 2.5, 'Hello there.\nSecond line.'), (3.0, 4.0, '<i>Italic</i> and <b>bold</b>')])
check('a title, header lines, NOTE, STYLE and REGION are not subtitles',
      cues('WEBVTT - Tears of Steel\nKind: captions\n\nNOTE a comment\non two lines\n\nSTYLE\n::cue(.gold) { color: #ffd700; }\n\n'
           'REGION\nid:fred\nwidth:40%\n\n1\n00:00:01.000 --> 00:00:02.000 region:fred\n<c.gold>Gold</c> text\n'),
      [(1.0, 2.0, '<font color="#ffd700">Gold</font> text')])
check('identifiers, numbers or words', cues('WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nA\n\nintro\n00:00:03.000 --> 00:00:04.000\nB\n'),
      [(1.0, 2.0, 'A'), (3.0, 4.0, 'B')])
check('a byte order mark, Windows line ends', cues('﻿WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nWindows\r\n'),
      [(1.0, 2.0, 'Windows')])
check('no header', cues('00:00:01.000 --> 00:00:02.000\nNo header\n'), [(1.0, 2.0, 'No header')])
check('no blank line between cues', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA\n00:00:03.000 --> 00:00:04.000\nB\n'),
      [(1.0, 2.0, 'A'), (3.0, 4.0, 'B')])
check('out of order, sorted', cues('WEBVTT\n\n00:00:05.000 --> 00:00:06.000\nLater\n\n00:00:01.000 --> 00:00:02.000\nEarlier\n'),
      [(1.0, 2.0, 'Earlier'), (5.0, 6.0, 'Later')])
check('an empty file', cues(''), [])

print('timings and settings')
check('no hours', cues('WEBVTT\n\n00:01.000 --> 00:02.000\nx\n'), [(1.0, 2.0, 'x')])
check('a comma for the dot', cues('WEBVTT\n\n00:00:01,500 --> 00:00:02,000\nx\n'), [(1.5, 2.0, 'x')])
check('positions from the settings', [c[2] for c in cues(
    'WEBVTT\n\n00:00:01.000 --> 00:00:02.000 line:0 align:start position:10%\na\n\n'
    '00:00:02.000 --> 00:00:03.000 line:50%\nb\n\n00:00:03.000 --> 00:00:04.000 align:end\nc\n\n'
    '00:00:04.000 --> 00:00:05.000 line:-1\nd\n\n00:00:05.000 --> 00:00:06.000 line:10%,start\ne\n')],
      ['{\\an7}a', '{\\an5}b', '{\\an3}c', 'd', '{\\an8}e'])

print('the cue text')
check('voices are speakers', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Thom>You\'re a jerk.</v>\n\n'
                                 '00:00:03.000 --> 00:00:04.000\n<v.loud Celia Maria>Look, Thom!\n'),
      [(1.0, 2.0, "You're a jerk.", 'Thom'), (3.0, 4.0, 'Look, Thom!', 'Celia Maria')])
check('a second voice in a cue keeps its name', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>Hi\n<v B>Hello\n'),
      [(1.0, 2.0, 'Hi\nB: Hello', 'A')])
check('entities', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTom &amp; Jerry &lt;3&nbsp;you &gt; me &#x2665;\n'),
      [(1.0, 2.0, 'Tom & Jerry <3\xa0you > me ♥')])
check("WebVTT's own colour classes", cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c.yellow>Yellow</c> <c.cyan.bg_black>cyan</c> <c.loud>no colour</c>\n'),
      [(1.0, 2.0, '<font color="#ffff00">Yellow</font> <font color="#00ffff">cyan</font> no colour')])
check('karaoke timestamps, ruby readings and lang go', cues(
    'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nNever <00:00:01.500>drink <lang pt>Olá</lang>\n\n'
    '00:00:04.000 --> 00:00:05.000\n<ruby>漢<rt>kan</rt>字<rt>ji</rt></ruby>\n'),
      [(1.0, 3.0, 'Never drink Olá'), (4.0, 5.0, '漢字')])
check('tags closed out of order stay well nested', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>a <b>b</i> c</b>\n'),
      [(1.0, 2.0, '<i>a <b>b</b></i><b> c</b>')])
check('left open: closed at the end', cues('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>open\n'), [(1.0, 2.0, '<i>open</i>')])

print('writing')
segments = [
    {'start': 5.0, 'end': 6.0, 'text': 'Later', 'speaker': 'Celia'},
    {'start': 1.0, 'end': 2.5, 'text': '{\\an8}<font color="#ffd700">Why</font>\n<i>you\'re <b>freaked</b> out</i>', 'speaker': 'Thom'},
    {'start': 3.0, 'end': 4.0, 'text': 'Tom & Jerry <3 --> <s>struck</s> <font color="#ffff00">yellow</font>', 'speaker': 'Thom'},
    {'start': 4.5, 'end': 4.6, 'text': ' ', 'speaker': 'A'},
    {'start': 7.0, 'end': 8.0, 'text': '{\\an1}Bottom left\n\nafter a blank line', 'speaker': 'A'},
]
written = webvtt.write(segments, voices=True)
check('the whole file', written, (
    'WEBVTT\n\nSTYLE\n::cue(.color_ffd700) { color: #ffd700; }\n\n'
    '00:00:01.000 --> 00:00:02.500 line:0\n<v Thom><c.color_ffd700>Why</c>\n<i>you\'re <b>freaked</b> out</i>\n\n'
    '00:00:03.000 --> 00:00:04.000\n<v Thom>Tom &amp; Jerry &lt;3 --&gt; struck <c.yellow>yellow</c>\n\n'
    '00:00:05.000 --> 00:00:06.000\n<v Celia>Later\n\n'
    '00:00:07.000 --> 00:00:08.000 position:10% align:start\n<v A>Bottom left\nafter a blank line\n'))
check('read back as written', [(s['text'], s.get('speaker')) for s in webvtt.parse(written)], [
    ('{\\an8}<font color="#ffd700">Why</font>\n<i>you\'re <b>freaked</b> out</i>', 'Thom'),
    ('Tom & Jerry <3 --> struck <font color="#ffff00">yellow</font>', 'Thom'),
    ('Later', 'Celia'), ('{\\an1}Bottom left\nafter a blank line', 'A')])
check('without voices', webvtt.write(segments[:1]), 'WEBVTT\n\n00:00:05.000 --> 00:00:06.000\nLater\n')
check('nothing to write', webvtt.write([{'start': 0, 'end': 1, 'text': ''}]), 'WEBVTT\n')
check('format_time', [webvtt.format_time(t) for t in (0, 61.25, 360000.5)], ['00:00:00.000', '00:01:01.250', '100:00:00.500'])

print('opened and exported through file_io')
folder = Path(tempfile.mkdtemp())
source = folder / 'in.vtt'
source.write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000 line:0\n<v Thom><i>Hello</i>\n', encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(source))
check('opens with its speaker and position', ([(s['text'], s.get('speaker')) for s in opened], fmt),
      ([('{\\an8}<i>Hello</i>', 'Thom')], 'VTT'))
session.SUBTITLE['segments'] = opened
session.SUBTITLE['language'] = 'en'
session.VIDEO = {}
for speakers, want in (('voice', '<v Thom><i>Hello</i>'), ('prefix', 'Thom: <i>Hello</i>'), ('none', '<i>Hello</i>')):
    session.FORMAT['options'] = {'speakers': speakers}
    file_io.save_file(str(folder / 'out.vtt'), 'VTT')
    check(f'speakers: {speakers}', (folder / 'out.vtt').read_text(encoding='utf-8').split('\n')[3], want)

print('the dialog offers voice tags for WebVTT, not for SRT')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()


def offered():
    combo = panel.speakers_combo.combobox
    return [combo.itemData(i) for i in range(combo.count())], combo.currentData()


panel.format_combo.setCurrentText('VTT')
panel._refresh_options()
check('VTT: three', offered()[0], ['none', 'prefix', 'voice'])
panel.speakers_combo.combobox.setCurrentIndex(2)
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('SRT: two, names before the text in their place', offered(), (['none', 'prefix'], 'prefix'))
panel.format_combo.setCurrentText('VTT')
panel._refresh_options()
check('back to VTT: voice tags again', offered()[1], 'voice')
panel.get_config()
again = _SubtitlesPanel()
again.format_combo.setCurrentText('VTT')
again._refresh_options()
check('remembered', again.speakers_combo.combobox.currentData(), 'voice')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
