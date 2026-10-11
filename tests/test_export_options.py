"""Export options: the text, speaker names, formatting and shift every
timed-text format takes; SRT's encoding and line ends; SUB's frame rate;
and the export dialog that sets and remembers them.

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
from subtitld.modules import export_options, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def project():
    return [
        {'start': 4.0, 'end': 5.0, 'text': 'No translation', 'speaker': 'Celia'},
        {'start': 1.0, 'end': 2.0, 'text': '<i>Hello</i> there', 'speaker': 'Thom',
         'translations': {'pt-br': '<i>Olá</i>', 'es-es': ' '}},
        {'start': 0.2, 'end': 0.6, 'text': '{\\an8}Sign', 'speaker': 'Thom'},
    ]


def texts(options):
    return [(s['start'], s['end'], s['text']) for s in export_options.prepare(project(), options)]


print('what is written')
check('as it is, in order', texts({}), [(0.2, 0.6, '{\\an8}Sign'), (1.0, 2.0, '<i>Hello</i> there'), (4.0, 5.0, 'No translation')])
check('languages with a translation (blank ones do not count)', export_options.translation_languages(project()), ['pt-br'])
check('the translation, the original where there is none', [t[2] for t in texts({'text': 'translation', 'language': 'pt-br'})],
      ['{\\an8}Sign', '<i>Olá</i>', 'No translation'])
check('both, the translation below', texts({'text': 'both', 'language': 'pt-br'})[1][2], '<i>Hello</i> there\n<i>Olá</i>')
check('speaker names before the text, after a position code', [t[2] for t in texts({'speakers': 'prefix'})],
      ['{\\an8}Thom: Sign', 'Thom: <i>Hello</i> there', 'Celia: No translation'])
check('formatting removed', [t[2] for t in texts({'formatting': 'remove', 'speakers': 'prefix'})],
      ['Thom: Sign', 'Thom: Hello there', 'Celia: No translation'])
check('shifted later', [t[:2] for t in texts({'offset': 1.5})], [(1.7, 2.1), (2.5, 3.5), (5.5, 6.5)])
check('shifted earlier: before 0 is left out, across 0 starts at 0', [t[:2] for t in texts({'offset': -1.2})], [(0.0, 0.8), (2.8, 3.8)])
check('the project is not changed', project()[1]['text'], '<i>Hello</i> there')

print('written by file_io with the options')
folder = Path(tempfile.mkdtemp())
session.SUBTITLE['segments'] = project()
session.SUBTITLE['language'] = 'en'
session.VIDEO = {'width': 1920, 'height': 1080, 'framerate': 25.0}


def export(fmt, ext, options):
    session.FORMAT['options'] = options
    target = folder / f'out.{ext}'
    file_io.save_file(str(target), fmt)
    return target.read_bytes()


data = export('SRT', 'srt', {'text': 'translation', 'language': 'pt-br', 'speakers': 'prefix', 'formatting': 'remove',
                             'offset': 0.5, 'encoding': 'cp1252', 'line_ends': 'crlf'})
check('SRT in Windows-1252 with CRLF', data, (
    '1\r\n00:00:00,700 --> 00:00:01,100\r\nThom: Sign\r\n\r\n'
    '2\r\n00:00:01,500 --> 00:00:02,500\r\nThom: Olá\r\n\r\n'
    '3\r\n00:00:04,500 --> 00:00:05,500\r\nCelia: No translation\r\n').encode('cp1252'))
check('SRT with a byte order mark', export('SRT', 'srt', {'encoding': 'utf-8-sig'})[:3], b'\xef\xbb\xbf')
check('SRT: a character the encoding lacks becomes ?',
      (session.SUBTITLE.__setitem__('segments', [{'start': 0, 'end': 1, 'text': '日本'}]), export('SRT', 'srt', {'encoding': 'cp1252'}))[1],
      b'1\n00:00:00,000 --> 00:00:01,000\n??\n')
session.SUBTITLE['segments'] = project()
check('SRT: options left from another export do not apply', export('SRT', 'srt', {}).decode().count('Thom:'), 0)
sub = export('SUB', 'sub', {'fps': 25.0}).decode()
check('SUB counts in the frame rate, and says which', sub.splitlines()[:2], ['{1}{1}25', '{5}{15}Sign'])
check('SUB: one second is 25 frames at 25 fps', '{25}{50}' in sub, True)
check('SUB: at 50 fps, 50 frames', '{50}{100}' in export('SUB', 'sub', {'fps': 50.0}).decode(), True)
ass = export('ASS', 'ass', {}).decode()
check('ASS: sized to the video', ('PlayResX: 1920' in ass, 'PlayResY: 1080' in ass), (True, True))
vtt = export('VTT', 'vtt', {'speakers': 'prefix'}).decode()
check('VTT: speakers, formatting', 'Thom: <i>Hello</i> there' in vtt, True)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
check('starts on SRT', panel.format_combo.currentText(), 'SRT')
check('offers the translation', [panel.text_combo.combobox.itemData(i) for i in range(panel.text_combo.combobox.count())],
      [('original', ''), ('translation', 'pt-br'), ('both', 'pt-br')])
check('the video frame rate is chosen', panel.fps_combo.combobox.currentData(), 25.0)
panel.text_combo.combobox.setCurrentIndex(2)
panel.speakers_combo.combobox.setCurrentIndex(1)
panel.encoding_combo.combobox.setCurrentIndex(1)
panel.offset_spin.setValue(-2.5)
check('SRT config', panel.get_config(), {'category': 'subtitles', 'format': 'SRT', 'options': {
    'text': 'both', 'language': 'pt-br', 'speakers': 'prefix', 'formatting': 'keep', 'offset': -2.5,
    'encoding': 'utf-8-sig', 'line_ends': 'lf'}})
panel.format_combo.setCurrentText('USF')
panel._refresh_options()
check('USF: its own options only', panel.get_config()['options'], {'embed_speaker_images': False, 'embed_audio_clips': False})
check('USF: the text options hidden', (panel.text_options.isHidden(), panel.srt_options.isHidden(), panel.usf_options.isHidden()), (True, True, False))
panel.format_combo.setCurrentText('JSON')
check('JSON: no options', panel.get_config()['options'], {})
again = _SubtitlesPanel()
check('remembered, but not the shift', (again.format_combo.currentText(), again.text_combo.combobox.currentData(),
                                       again.speakers_combo.combobox.currentData(), again.encoding_combo.combobox.currentData(),
                                       again.offset_spin.value()),
      ('JSON', ('both', 'pt-br'), 'prefix', 'utf-8-sig', 0.0))
session.SUBTITLE['segments'] = [{'start': 1, 'end': 2, 'text': 'x'}]
bare = _SubtitlesPanel()
check('no translation, no text choice', bare.text_combo.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
