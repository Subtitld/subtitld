"""Softel Swift: the three text layouts as Subtitle Edit's examples lay
them out (dashes for no time, DURATION over TIMEOUT in the first), XIF
(Text and HardReturn, the TimingStandard's rate) and SIF (the commands,
DURATION and TIMEOUT, the italic prefix, the Originating Swift's rate),
each written and read back, durations past a minute kept; the .txt told
from the other .txt formats, and all through file_io and the dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import datetime, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, swift

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def timed(segments):
    return [(s['start'], s['end'], s['text']) for s in segments]


DURATION = ('TIMEIN: 01:00:01:09\tDURATION: 01:20\tTIMEOUT: --:--:--:--\r\nBroadcasting\r\nfrom an undisclosed location...\r\n\r\n'
            'TIMEIN: 01:00:04:12\tDURATION: --:--\tTIMEOUT: 01:00:09:07\r\nSecond\r\n')
NUMBER = ('SUBTITLE: 1\tTIMEIN: 00:00:00:00\tTIMEOUT: 00:00:04:00\r\nVoor de oorlog\r\n\r\n'
          'SUBTITLE: 2\tTIMEIN: 00:00:05:12\tTIMEOUT: 00:00:10:02\r\nIk ben Marie Pinhas. Ik ben geboren\r\nin Thessaloniki in Griekenland,\r\n')
NUMBER_DURATION = ('SUBTITLE: 1\tTIMEIN: 00:00:07:01\tDURATION: 03:11\tTIMEOUT: 00:00:10:12\r\nVoor de oorlog\r\n\r\n'
                   'SUBTITLE: 2\tTIMEIN: 00:00:10:16\tDURATION: 01:08\tTIMEOUT: 00:00:11:24\r\nop 6 maart \'31,\r\n')

print('the text layouts')
check('duration: DURATION when it says one, else TIMEOUT', (timed(swift.parse_text(DURATION)[0]), swift.parse_text(DURATION)[1]['layout']), (
    [(3601.36, 3603.16, 'Broadcasting\nfrom an undisclosed location...'), (3604.48, 3609.28, 'Second')], 'duration'))
check('number', (timed(swift.parse_text(NUMBER)[0]), swift.parse_text(NUMBER)[1]['layout']), (
    [(0.0, 4.0, 'Voor de oorlog'), (5.48, 10.08, 'Ik ben Marie Pinhas. Ik ben geboren\nin Thessaloniki in Griekenland,')], 'number'))
check('number-duration: TIMEOUT', (timed(swift.parse_text(NUMBER_DURATION)[0]), swift.parse_text(NUMBER_DURATION)[1]['layout']), (
    [(7.04, 10.48, 'Voor de oorlog'), (10.64, 11.96, "op 6 maart '31,")], 'number-duration'))
check('not Swift: QTtext, a transcript', (swift.looks_like('{QTtext}\n' + DURATION), swift.looks_like('Hello\nworld')), (None, None))
trip = [{'start': 1.0, 'end': 3.6, 'text': 'One\n<i>two</i>'}, {'start': 10.0, 'end': 85.0, 'text': 'Long, past a minute'}]
plain = [(1.0, 3.6, 'One\ntwo'), (10.0, 85.0, 'Long, past a minute')]
for layout in swift.LAYOUTS:
    written = swift.write_text(trip, 25, layout)
    check(f'{layout}: read back, its layout', (timed(swift.parse_text(written)[0]), swift.looks_like(written)), (plain, layout))
check('DURATION in whole seconds past a minute', 'DURATION: 75:00' in swift.write_text(trip, 25), True)

print('XIF')
xif = swift.write_xif(trip, 25, 'Title & co')
segments, settings = swift.parse_xif(xif)
check('read back, SMPTE25', (timed(segments), settings['fps'], 'Type="SMPTE25"' in xif, 'ProgrammeName="Title &amp; co"' in xif),
      (plain, 25.0, True, True))
check('the TimingStandard\'s rate', swift.parse_xif(swift.write_xif(trip, 30))[1]['fps'], 30.0)
check('runs joined with a space, lines trimmed', timed(swift.parse_xif(
    '<XIF><FileBody><ContentBlock><ThreadedObject><TimingObject><TimeIn value="00:00:01:00"/><TimeOut value="00:00:02:00"/>'
    '</TimingObject><Content><SubtitleText><Paragraph><Text>Hello</Text><Text>there</Text><HardReturn/><Text>again</Text>'
    '</Paragraph></SubtitleText></Content></ThreadedObject></ContentBlock></FileBody></XIF>')[0]), [(1.0, 2.0, 'Hello there\nagain')])

print('SIF')
sif = swift.write_sif([{'start': 1.0, 'end': 3.6, 'text': '<i>All italic\nsecond</i>'}, trip[1]], 25, 'clip',
                      today=datetime.date(2026, 11, 18))
check('the header and a block', sif.split('\r\n')[:3] + sif.split('\r\n')[16:26], [
    '# SWIFT INTERCHANGE FILE V2', "# DO NOT EDIT LINES BEGINNING WITH '#' SIGN", '# Originating Swift: Open 25 English (USA)',
    '# SUBTITLE 1', '# TIMEIN 00:00:01:00', '# DURATION 02:15 AUTO', '# TIMEOUT 00:00:03:15', '# START ROW BOTTOM',
    '# ALIGN CENTRE JUSTIFY CENTER', '# ROW 0 RETURN', '<fontstyle-italic>All italic', '# ROW 1', 'second'])
check('read back: the italic prefix', timed(swift.parse_sif(sif)[0]),
      [(1.0, 3.6, '<i>All italic\nsecond</i>'), (10.0, 85.0, 'Long, past a minute')])
check('the Originating Swift\'s rate', swift.parse_sif(sif.replace('Open 25', 'Line21 30 DROP'))[1]['fps'], 30.0)
check('looks_like_sif', (swift.looks_like_sif(sif), swift.looks_like_sif('{\\rtf1 # SUBTITLE')), (True, False))

print('through file_io')
folder = Path(tempfile.mkdtemp())
for name, text, fmt in (('a.txt', DURATION, 'SWIFTTEXT'), ('b.txt', NUMBER, 'SWIFTNUMBERS'),
                        ('c.txt', NUMBER_DURATION, 'SWIFTNUMBERSDURATION'), ('d.xif', xif, 'XIF'), ('e.sif', sif, 'SIF'),
                        ('g.txt', '00:00:01:Hello\n00:00:03:World\n', 'VPLAYER')):
    (folder / name).write_text(text, encoding='utf-8')
    session.VIDEO = {}
    session.FORMAT = {}
    check(name, file_io.process_subtitles_file(str(folder / name))[1], fmt)
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 25, 'filepath': '/videos/clip.mp4'}
for fmt, name in (('SWIFTTEXT', 'o1.txt'), ('SWIFTNUMBERS', 'o2.txt'), ('SWIFTNUMBERSDURATION', 'o3.txt'), ('XIF', 'o.xif'), ('SIF', 'o.sif')):
    session.FORMAT = {'options': {}}
    file_io.save_file(str(folder / name), fmt)
    session.FORMAT = {}
    check(f'saved as {fmt} and opened again', (file_io.process_subtitles_file(str(folder / name))[1],
                                               [s['text'] for s in swift.read(str(folder / name))[0]]),
          (fmt, ['One\ntwo', 'Long, past a minute']))

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS
check('all five in the menu', all(fmt in SUBTITLE_FORMATS for fmt in ('SWIFTTEXT', 'SWIFTNUMBERS', 'SWIFTNUMBERSDURATION', 'XIF', 'SIF')),
      True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
