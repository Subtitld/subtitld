"""CMX 3600 EDL: subtitles read from events (Subtitle Edit's layout), from
Avid's locators (OpenTimelineIO's) and from DaVinci Resolve's marker EDL,
drop-frame and the hour a sequence starts at, the three styles written and
read back, telling it from the other text formats, and the file and the
export dialog.

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
from subtitld.modules import edl, file_io, legacy, session, srt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# Subtitle Edit's layout: a black event, then the subtitles as events with
# their clip names, padded with trailing spaces as NLEs do, a motion line
# and a source file comment that are not text.
EVENTS = ('TITLE: Reel 1\r\nFCM: NON-DROP FRAME\r\n\r\n'
          '000001  BL       V     C        01:00:00:00 01:00:01:00 01:00:00:00 01:00:01:00   \r\n\r\n'
          '000002  AX       V     C        01:00:01:00 01:00:03:12 01:00:01:00 01:00:03:12\r\n'
          '* FROM CLIP NAME: Hello there\r\n* COMMENT: second line\r\n\r\n'
          '000003  AX       V     D    025 00:10:00:00 00:10:02:00 01:00:04:00 01:00:06:00\r\n'
          'M2   AX       050.0                00:10:00:00\r\n'
          '* FROM CLIP: /media/clip.mov\r\n*FROM CLIP NAME: Dissolved in\r\n')
# Avid's locators, as OpenTimelineIO reads and writes them: one under an
# event whose source and record differ (in the source's timecode), two
# under another.
LOCATORS = ('TITLE: Notes\nFCM: NON-DROP FRAME\n\n'
            '001  ABC0000  V     C        00:59:50:00 01:00:10:00 01:00:00:00 01:00:20:00\n'
            '* FROM CLIP NAME:  shot_010\n'
            '* LOC: 00:59:55:00 RED     First note\n'
            '002  ABC0001  V     C        01:00:20:00 01:00:40:00 01:00:20:00 01:00:40:00\n'
            '* LOC: 01:00:21:00 YELLOW  Second note\n'
            '* LOC: 01:00:22:00 GREEN   Third note\n')
# DaVinci Resolve's marker EDL (Subtitle Edit's example).
MARKERS = ('TITLE: Timeline 1\nFCM: NON-DROP FRAME\n\n'
           '001  001      V     C        01:00:05:00 01:00:05:01 01:00:05:00 01:00:05:01  \n'
           ' |C:ResolveColorBlue |M:Marker name |D:50\n\n'
           '002  001      V     C        01:00:09:00 01:00:09:01 01:00:09:00 01:00:09:01  \n'
           ' |C:ResolveColorRed |M:Point |D:1\n')

print('reading')
check('events: record times, clip names and comments, BL left out, the hour taken off',
      edl.parse(EVENTS, 25)[0],
      [{'start': 1.0, 'end': 3.48, 'text': 'Hello there\nsecond line'}, {'start': 4.0, 'end': 6.0, 'text': 'Dissolved in'}])
check('events: the settings', edl.parse(EVENTS, 25)[1],
      {'fps': 25.0, 'drop': False, 'start': '01:00:00:00', 'style': 'events', 'title': 'Reel 1'})
segments, settings = edl.parse(LOCATORS, 25)
check('locators: a source timecode mapped to the record, each until the next or its event\'s end, colours kept',
      segments, [{'start': 5.0, 'end': 8.0, 'text': 'First note', 'edl': {'color': 'RED'}},
                 {'start': 21.0, 'end': 22.0, 'text': 'Second note', 'edl': {'color': 'YELLOW'}},
                 {'start': 22.0, 'end': 25.0, 'text': 'Third note', 'edl': {'color': 'GREEN'}}])
check('locators: the style, the clip names not read as subtitles', (settings['style'], len(segments)), ('locators', 3))
check("Resolve's markers: |D: frames, a one-frame marker a point",
      edl.parse(MARKERS, 25)[0], [{'start': 5.0, 'end': 7.0, 'text': 'Marker name', 'edl': {'color': 'ResolveColorBlue'}},
                                  {'start': 9.0, 'end': 12.0, 'text': 'Point', 'edl': {'color': 'ResolveColorRed'}}])
DROP = ('TITLE: x\nFCM: DROP FRAME\n\n'
        '001  AX       V     C        00:01:00;02 00:01:02;00 00:01:00;02 00:01:02;00\n* FROM CLIP NAME: Drop\n')
segments, settings = edl.parse(DROP, 25)
check('drop-frame: 29.97 whatever the video, 00:01:00;02 is frame 1800',
      (settings['fps'], settings['drop'], segments[0]['start']), (29.97, True, round(1800 * 1001 / 30000, 3)))

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': '<i>Hello</i>\nthere'}, {'start': 3.0, 'end': 4.0, 'text': 'Bye'}]
written = edl.write(trip, fps=25, drop=False, start='01:00:00:00', style='events', title='Show')
check('events as Subtitle Edit and OpenTimelineIO lay them out', written.split('\n'), [
    'TITLE: Show', 'FCM: NON-DROP FRAME', '',
    '001  AX       V     C        01:00:01:00 01:00:02:12 01:00:01:00 01:00:02:12',
    '* FROM CLIP NAME: Hello', '* COMMENT: there', '',
    '002  AX       V     C        01:00:03:00 01:00:04:00 01:00:03:00 01:00:04:00',
    '* FROM CLIP NAME: Bye', ''])
check('events read back', edl.parse(written, 25)[0], [{'start': 1.0, 'end': 2.48, 'text': 'Hello\nthere'},
                                                      {'start': 3.0, 'end': 4.0, 'text': 'Bye'}])
located = edl.write(trip, fps=25, drop=False, style='locators')
check('locators: a * LOC: at each start, the text on one line', [line for line in located.split('\n') if 'LOC' in line],
      ['* LOC: 00:00:01:00 YELLOW  Hello there', '* LOC: 00:00:03:00 YELLOW  Bye'])
check('locators read back, each until its event ends', edl.parse(located, 25)[0],
      [{'start': 1.0, 'end': 2.48, 'text': 'Hello there', 'edl': {'color': 'YELLOW'}},
       {'start': 3.0, 'end': 4.0, 'text': 'Bye', 'edl': {'color': 'YELLOW'}}])
marked = edl.write(trip, fps=25, drop=False, style='markers')
check("markers as Subtitle Edit writes Resolve's", marked.split('\n')[3:5],
      ['001  001      V     C        00:00:01:00 00:00:01:01 00:00:01:00 00:00:01:01  ',
       ' |C:ResolveColorBlue |M:Hello there |D:37'])
check('markers read back', [(s['start'], s['end'], s['text']) for s in edl.parse(marked, 25)[0]],
      [(1.0, 2.48, 'Hello there'), (3.0, 4.0, 'Bye')])
dropped = edl.write([{'start': 60.06, 'end': 62.0, 'text': 'x'}], fps=29.97, drop=True)
check('drop-frame written with FCM and ";"', (dropped.split('\n')[1], dropped.split('\n')[3][29:40]),
      ('FCM: DROP FRAME', '00:01:00;02'))
many = edl.write([{'start': n, 'end': n + 0.5, 'text': str(n)} for n in range(1000)], fps=25)
check('more than 999 events count with four digits', [line[:4] for line in many.split('\n') if line[:1].isdigit()][-1],
      '1000')

print('detection')
check('looks like an EDL: the three, not SRT nor a transcript',
      [edl.looks_like(text) for text in (EVENTS, LOCATORS, MARKERS.encode(), '1\n00:00:01,000 --> 00:00:02,000\nHi\n',
                                         'Just words\nand more')], [True, True, True, False, False])

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.edl').write_bytes(LOCATORS.encode())
session.VIDEO = {}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.edl'))
check('opens as EDL, the locators, the style and the hour kept', (fmt, len(opened), session.FORMAT['options']['edl_style'],
                                                                   session.FORMAT['options']['timeline_start']),
      ('EDL', 3, 'locators', '01:00:00:00'))
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/show.mov'}
session.FORMAT = {'options': {'edl_style': 'events', 'timeline_start': '01:00:00:00', 'timecode': 'non-drop'}}
session.SUBTITLE.pop('metadata', None)
file_io.save_file(str(folder / 'out.edl'), 'EDL')
saved = (folder / 'out.edl').read_bytes()
check('saved with CRLF, titled after the video, read back', (saved.count(b'\r\n') > 3, saved.startswith(b'TITLE: show\r\n'),
                                                            edl.read(str(folder / 'out.edl'), 25)[0][1]),
      (True, True, {'start': 3.0, 'end': 4.0, 'text': 'Bye'}))
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['EDL']['extensions'], ['edl'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('EDL')
panel._refresh_options()
check('EDL in the export menu, with a frame rate, drop-frame at 29.97, the timeline start and the style',
      ('EDL' in SUBTITLE_FORMATS, panel.sub_options.isHidden(), panel.scc_options.isHidden(),
       panel.timeline_options.isHidden(), panel.edl_style_combo.isHidden()), (True, False, False, False, False))
panel.edl_style_combo.combobox.setCurrentIndex(2)
check('the options it exports with', {key: value for key, value in panel.export_choices()[2].items()
                                      if key in ('edl_style', 'timeline_start', 'timecode')},
      {'edl_style': 'markers', 'timeline_start': '00:00:00:00', 'timecode': panel.timecode_combo.combobox.currentData()})
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('the style is only for EDL', (panel.edl_style_combo.isHidden(), 'edl_style' in panel.export_choices()[2]), (True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
