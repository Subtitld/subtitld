"""Avid SubCap: reading the .txt Media Composer's SubCap effect exports
(comments, text beside the times, drop-frame, a timeline an hour in),
writing what it imports, and telling it from the other formats of .txt.

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
from subtitld.modules import avid_subcap, file_io, legacy, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fps=None):
    segments, settings = avid_subcap.parse(text, fps)
    return [(s['start'], s['end'], s['text']) for s in segments], settings


# As Media Composer exports it, from a 25 fps sequence starting at 00:00:00:00.
EXPORTED = ('@ This file written with the Avid Caption plugin, version 1\r\n\r\n<begin subtitles>\r\n'
            '00:00:03:15 00:00:08:05\r\nIch muss dafür sorgen,\r\ndass die Epsteins weiterleben\r\n\r\n'
            '00:00:10:00 00:00:12:12\r\nZweite Zeile\r\n\r\n<end subtitles>\r\n')

print('the file')
check('as Media Composer writes it', cues(EXPORTED), (
    [(3.6, 8.2, 'Ich muss dafür sorgen,\ndass die Epsteins weiterleben'), (10.0, 12.48, 'Zweite Zeile')],
    {'fps': 25.0, 'drop': False, 'start': '00:00:00:00'}))
check('comments anywhere, no header, text beside the times, nothing after <end subtitles>', cues(
    '<BEGIN SUBTITLES>\n@ a note\n00:00:01:00 00:00:02:00 Beside the times\nand under them\n\n'
    '@ another\n00:00:03:00 00:00:04:00\nNext\n<end subtitles>\n00:00:05:00 00:00:06:00\nAfter the end\n')[0],
      [(1.0, 2.0, 'Beside the times\nand under them'), (3.0, 4.0, 'Next')])
check('no blank line between subtitles, sorted', cues(
    '<begin subtitles>\n00:00:05:00 00:00:06:00\nLater\n00:00:01:00 00:00:02:00\nEarlier\n<end subtitles>\n')[0],
      [(1.0, 2.0, 'Earlier'), (5.0, 6.0, 'Later')])
check('an empty subtitle is none', cues('<begin subtitles>\n00:00:01:00 00:00:02:00\n\n<end subtitles>\n')[0], [])
check("at the video's rate", cues('<begin subtitles>\n00:00:01:12 00:00:02:00\nx\n<end subtitles>\n', 24)[0],
      [(1.5, 2.0, 'x')])
check('23.976: the frames last a thousandth longer', cues('<begin subtitles>\n00:00:01:12 00:00:10:00\nx\n<end subtitles>\n', 23.976)[0],
      [(1.502, 10.01, 'x')])

print('drop-frame')
dropped = cues('<begin subtitles>\n00:01:00;02 00:01:02;00\nA minute in\n\n00:10:00;00 00:10:01;00\nTen\n<end subtitles>\n', 25)
check('";" is drop-frame: read at 29.97 whatever the video is', dropped, (
    [(60.06, 61.995, 'A minute in'), (599.999, 601.0, 'Ten')], {'fps': 29.97, 'drop': True, 'start': '00:00:00:00'}))
check('59.94 for a video near 60', cues('<begin subtitles>\n00:01:00;04 00:01:01;00\nx\n<end subtitles>\n', 59.94)[1]['fps'], 59.94)
check('the timecode of a frame and back, every minute of an hour (29.97)',
      all(avid_subcap.frame_of(*map(int, avid_subcap.timecode(frame, 30, True).replace(';', ':').split(':')), 30, True) == frame
          for frame in range(0, 107892 + 1, 17)), True)
check('the timecode of a frame and back (59.94)',
      all(avid_subcap.frame_of(*map(int, avid_subcap.timecode(frame, 60, True).replace(';', ':').split(':')), 60, True) == frame
          for frame in range(0, 215784 + 1, 31)), True)
check('the frames drop-frame skips', [avid_subcap.timecode(frame, 30, True) for frame in (1799, 1800, 17981, 17982)],
      ['00:00:59;29', '00:01:00;02', '00:09:59;29', '00:10:00;00'])

print('a timeline an hour in')
check('01:00:00:00 taken off, and remembered', cues(EXPORTED.replace('00:00:', '01:00:')), (
    [(3.6, 8.2, 'Ich muss dafür sorgen,\ndass die Epsteins weiterleben'), (10.0, 12.48, 'Zweite Zeile')],
    {'fps': 25.0, 'drop': False, 'start': '01:00:00:00'}))
check('drop-frame an hour in', cues('<begin subtitles>\n01:00:01;00 01:00:02;00\nx\n<end subtitles>\n')[0], [(1.001, 2.002, 'x')])
check('a subtitle before the hour: nothing taken off', cues(
    '<begin subtitles>\n00:59:59:00 01:00:01:00\nx\n<end subtitles>\n')[0][0][0], 3599.0)

print('writing')
SEGMENTS = [{'start': 3.6, 'end': 8.2, 'text': 'Ich muss <i>dafür</i> sorgen,\n\ndass die Epsteins weiterleben'},
            {'start': 10.0, 'end': 12.48, 'text': '{\\an8}Zweite Zeile'},
            {'start': 13.0, 'end': 14.0, 'text': '<b></b>'}]
check("SubCap's layout: header, frames, plain text, no blank line inside", avid_subcap.write(SEGMENTS, 25), (
    '@ This file written with the Avid Caption plugin, version 1\n\n<begin subtitles>\n'
    '00:00:03:15 00:00:08:05\nIch muss dafür sorgen,\ndass die Epsteins weiterleben\n\n'
    '00:00:10:00 00:00:12:12\nZweite Zeile\n\n<end subtitles>\n'))
check('read back the same', cues(avid_subcap.write(SEGMENTS, 25))[0],
      [(3.6, 8.2, 'Ich muss dafür sorgen,\ndass die Epsteins weiterleben'), (10.0, 12.48, 'Zweite Zeile')])
at_2997 = avid_subcap.write([{'start': 60.06, 'end': 61.995, 'text': 'x'}], 29.97)
check('29.97: drop-frame', at_2997.split('\n')[3], '00:01:00;02 00:01:02;00')
check('29.97, not drop-frame', avid_subcap.write([{'start': 60.06, 'end': 62.062, 'text': 'x'}], 29.97, drop=False).split('\n')[3],
      '00:01:00:00 00:01:02:00')
check('23.976 has no drop-frame', avid_subcap.write([{'start': 1.502, 'end': 10.01, 'text': 'x'}], 23.976).split('\n')[3],
      '00:00:01:12 00:00:10:00')
check('from 01:00:00:00, Windows line ends', avid_subcap.write(SEGMENTS[:1], 25, start='01:00:00:00', line_end='\r\n').split('\r\n')[3],
      '01:00:03:15 01:00:08:05')
check('round trip at 29.97 drop-frame, an hour in', cues(avid_subcap.write(dropped[0] and [
    {'start': s, 'end': e, 'text': t} for s, e, t in dropped[0]], 29.97, start='01:00:00:00'))[0], dropped[0])
check('a subtitle shorter than a frame lasts one', avid_subcap.write([{'start': 1.0, 'end': 1.01, 'text': 'x'}], 25).split('\n')[3],
      '00:00:01:00 00:00:01:01')

print('telling .txt apart')
check('SubCap is SubCap', avid_subcap.is_subcap(EXPORTED.encode('utf-8')), True)
check('the header alone is enough', avid_subcap.is_subcap(EXPORTED.split('<begin')[0]), True)
VPLAYER = '00:00:01:Hello there\n00:00:04:Second\n'
DVDSS = '00:00:01:00 00:00:03:00 DVD Subtitle System\n00:00:04:00 00:00:06:00 Second\n'
for name, text in (('VPlayer', VPLAYER), ('DVDSS', DVDSS), ('a transcript', 'Just some words.\nAnd more.\n')):
    check(f'{name} is not SubCap', avid_subcap.is_subcap(text), False)

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'avid.txt').write_bytes(EXPORTED.replace('00:00:', '01:00:').encode('utf-8'))
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'avid.txt'))
check('opens as AVID; its rate, drop-frame and hour kept to save it again', (
    fmt, [(s['start'], s['end']) for s in opened],
    {key: session.FORMAT['options'].get(key) for key in ('fps', 'timecode', 'timeline_start')}),
      ('AVID', [(3.6, 8.2), (10.0, 12.48)], {'fps': 25.0, 'timecode': 'non-drop', 'timeline_start': '01:00:00:00'}))
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.txt'), 'AVID')
check('saved again as it was', (folder / 'again.txt').read_text(encoding='utf-8').split('\n')[3:5],
      ['01:00:03:15 01:00:08:05', 'Ich muss dafür sorgen,'])
session.SUBTITLE['segments'] = [dict(segment) for segment in SEGMENTS]
session.VIDEO = {'framerate': 29.97}
session.FORMAT = {'options': {'text': 'original', 'speakers': 'none', 'formatting': 'keep', 'offset': 0,
                              'encoding': 'cp1252', 'line_ends': 'crlf'}}
file_io.save_file(str(folder / 'out.txt'), 'AVID')
saved = (folder / 'out.txt').read_bytes()
check("saved at the video's rate, drop-frame, in the encoding and line ends asked", (
    saved.split(b'\r\n')[3], saved.split(b'\r\n')[4]), (b'00:00:03;18 00:00:08;06', 'Ich muss dafür sorgen,'.encode('cp1252')))
check('and opened again', [s['text'] for s in file_io.process_subtitles_file(str(folder / 'out.txt'))[0]],
      ['Ich muss dafür sorgen,\ndass die Epsteins weiterleben', 'Zweite Zeile'])

(folder / 'vplayer.txt').write_text(VPLAYER, encoding='utf-8')
check('.txt: VPlayer still VPlayer', file_io.process_subtitles_file(str(folder / 'vplayer.txt'))[1], 'VPLAYER')
(folder / 'dvdss.txt').write_text(DVDSS, encoding='utf-8')
check('.txt: DVDSS still DVDSS', file_io.process_subtitles_file(str(folder / 'dvdss.txt'))[1], 'DVDSS')
session.FORMAT = {}
for name, fmt in sorted(legacy.SESSION_FORMATS.items()):
    if legacy.EXTENSIONS[fmt] != 'txt':
        continue
    path = folder / f'{fmt}.txt'
    session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 3.5, 'text': 'First line\nsecond line'},
                                    {'start': 4.0, 'end': 6.25, 'text': 'Second cue'}]
    session.VIDEO = {'framerate': 25.0}
    session.FORMAT = {}
    file_io.save_file(str(path), name)
    check(f'.txt: {name} is not taken for SubCap', file_io.process_subtitles_file(str(path))[1], name)

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE['segments'] = [dict(segment) for segment in SEGMENTS]
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('AVID')
panel._refresh_options()
options = panel.get_config()['options']
check("AVID: the rate (the video's), drop-frame, the timeline start, encoding; no formatting", (
    panel.sub_options.isHidden(), panel.scc_options.isHidden(), panel.timeline_options.isHidden(),
    panel.srt_options.isHidden(), panel.formatting_combo.isHidden(),
    {key: options.get(key) for key in ('fps', 'timecode', 'timeline_start', 'encoding')}),
      (False, False, False, False, True,
       {'fps': 29.97, 'timecode': 'drop', 'timeline_start': '00:00:00:00', 'encoding': 'utf-8'}))
panel.fps_combo.combobox.setCurrentIndex(next(index for index in range(panel.fps_combo.combobox.count())
                                              if panel.fps_combo.combobox.itemData(index) == 25.0))
check('at 25 there is no drop-frame to choose', panel.scc_options.isHidden(), True)
panel.timeline_start_combo.combobox.setCurrentIndex(1)
check('chosen', panel.get_config()['options'].get('timeline_start'), '01:00:00:00')
check('remembered', _SubtitlesPanel().timeline_start_combo.combobox.currentData(), '01:00:00:00')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', (panel.timeline_options.isHidden(), panel.sub_options.isHidden()), (True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
