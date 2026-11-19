"""SoftNi: both punctuations of its times read as Subtitle Edit's readers
take them ("[" "]" italics a line at a time, "}" the top, text before its
times, the empty first times and the *END* ones, the rate *TIMING* says),
written as Subtitle Edit writes it (*TIMING* with the rate, its tests'
"1 24 0" at 23.976), round trips, told from the other .sub formats and
opened and saved through file_io and the dialog.

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
from subtitld.modules import file_io, microdvd, session, softni

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


SAMPLE = ('*PART 1*\r\n00:00:00.00\\00:00:00.00\r\n'
          '}[Top and italic]\r\n00:00:01.12\\00:00:03.00\r\n'
          '—Peter.\r\n—[Estoy] de licencia.\r\n00:00:04.00\\00:00:06.05\r\n'
          '[Two lines]\r\n[in italics]\r\n00:00:07.00\\00:00:08.00\r\n'
          '*END*\r\n00:00:09.00\\00:00:09.00\r\n*CODE*\r\n0000000000000000\r\n*TIMING*\r\n1 25 0\r\n*MARKERS*\r\n')
COLON = SAMPLE.replace('.', ':').replace('—Peter:', '—Peter.').replace('licencia:', 'licencia.')

print('reading')
segments, settings = softni.parse(SAMPLE)
want = [(1.48, 3.0, '{\\an8}<i>Top and italic</i>'), (4.0, 6.2, '—Peter.\n—<i>Estoy</i> de licencia.'),
        (7.0, 8.0, '<i>Two lines\nin italics</i>')]
check('periods: times after the text, italics, the top', ([(s['start'], s['end'], s['text']) for s in segments], settings),
      (want, {'fps': 25.0, 'separator': '.'}))
check('colons', ([(s['start'], s['end'], s['text']) for s in softni.parse(COLON)[0]], softni.parse(COLON)[1]['separator']),
      (want, ':'))
check('the rate *TIMING* says', softni.parse(SAMPLE.replace('1 25 0', '1 30 0'))[0][0]['start'], round(1 + 12 / 30, 3))
check('looks_like', (softni.looks_like(SAMPLE), softni.looks_like(COLON.encode()), softni.looks_like('{1}{2}Hi')),
      ('.', ':', None))

print('writing')
written = softni.write(segments, 23.976)
lines = written.split('\r\n')
check('as Subtitle Edit writes it: *PART 1*, *END* a second on, *TIMING* "1 24 0"',
      (lines[:3], lines[lines.index('*END*') + 1], lines[lines.index('*TIMING*') + 1], written.count('\r\n') == written.count('\n')),
      (['*PART 1*', '00:00:00.00\\00:00:00.00', '}[Top and italic]'], '00:00:09.00\\00:00:09.00', '1 24 0', True))
check('round trip at 25', [(s['start'], s['end'], s['text']) for s in softni.parse(softni.write(segments, 25))[0]], want)
check('round trip with colons', softni.parse(softni.write(segments, 25, softni.COLON))[0], softni.parse(SAMPLE)[0])
check('an empty file', softni.write([]).split('\r\n')[2:4], ['*END*', '...........\\...........'])

print('through file_io')
folder = Path(tempfile.mkdtemp())
for name, text, fmt in (('a.sub', SAMPLE, 'SOFTNI'), ('b.sub', COLON, 'SOFTNICOLON')):
    (folder / name).write_text(text, encoding='cp1252')
    session.VIDEO = {}
    session.FORMAT = {}
    opened, got = file_io.process_subtitles_file(str(folder / name))
    check(f'{name} opens as {fmt}', (got, len(opened), opened[1]['text']), (fmt, 3, want[1][2]))
(folder / 'm.sub').write_text('{25}{75}Hello\n', encoding='utf-8')
session.FORMAT = {}
check('MicroDVD still MicroDVD', file_io.process_subtitles_file(str(folder / 'm.sub'))[1], 'SUB')
session.SUBTITLE.segments = segments
session.VIDEO = {'framerate': 25}
for fmt, punctuation in (('SOFTNI', '.'), ('SOFTNICOLON', ':')):
    session.FORMAT = {'options': {}}
    file_io.save_file(str(folder / f'{fmt}.sub'), fmt)
    data = (folder / f'{fmt}.sub').read_bytes()
    check(f'saved as {fmt}', (data[:3], softni.looks_like(data[3:]), microdvd.kind(data) != 'vobsub'), (b'\xef\xbb\xbf', punctuation, True))

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SOFTNICOLON')
panel._refresh_options()
check('both in the menu, with a frame rate', ('SOFTNI' in SUBTITLE_FORMATS, 'SOFTNICOLON' in SUBTITLE_FORMATS,
                                              panel.sub_options.isHidden()), (True, True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
