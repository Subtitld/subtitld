"""Cheetah ASCII (.asc): Subtitle Edit's test sample read (the "\\E" end
marker, a caption without *E lasting until the next, the commands read
past, *DropFrame), written as Subtitle Edit writes it, round trips at 25
and 29.97 drop-frame, another .asc not claimed, and the file through
file_io and the dialog.

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
from subtitld.modules import cheetahcap, file_io, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# Subtitle Edit's CheetahCaptionAscTest sample.
SAMPLE = ('*DropFrame\r\n*WIDTH 32\r\n\r\n'
          '** Caption Number 1\r\n*PopOn\r\n*T 01:00:14:13\r\n*E 01:00:16:13\r\n*BottomUp\r\n*Cf16\r\n[SCREAM]\\E\r\n\r\n'
          '** Caption Number 2\r\n*PopOn\r\n*T 01:00:28:11\r\n*BottomUp\r\n*Lf07\r\nIN PURSUIT\r\nOF A RUNAWAY VEHICLE.\\E\r\n\r\n'
          '** Caption Number 3\r\n*PopOn\r\n*T 01:00:31:13\r\n*E 01:00:33:12\r\n*BottomUp\r\n*Cf16\r\nAGH!\\E\r\n')


def drop(hours, minutes, seconds, frames):
    return round(cheetahcap.frame_of(hours, minutes, seconds, frames, 30, True) * 1001 / 30000, 3)


print('reading')
segments, settings = cheetahcap.parse_asc(SAMPLE)
check('three captions, \\E gone, drop-frame', ([(s['start'], s['end'], s['text']) for s in segments], settings), (
    [(drop(1, 0, 14, 13), drop(1, 0, 16, 13), '[SCREAM]'),
     (drop(1, 0, 28, 11), drop(1, 0, 31, 13), 'IN PURSUIT\nOF A RUNAWAY VEHICLE.'),
     (drop(1, 0, 31, 13), drop(1, 0, 33, 12), 'AGH!')], {'fps': 29.97, 'drop': True}))
plain = cheetahcap.parse_asc(SAMPLE.replace('*DropFrame\r\n', ''))
check('without *DropFrame: frames of a second at 25', (plain[0][0]['start'], plain[1]), (3614.52, {'fps': 25.0, 'drop': False}))
check('another .asc (Unknown 30) is not claimed',
      cheetahcap.looks_like_asc('@ headers\r\n\r\n1.\r\n00:00:04:12\r\n00:00:06:05\r\nHello.\r\n'), False)

print('writing')
trip = [{'start': 3600.0, 'end': 3602.0, 'text': 'Hello\n<i>world</i>'}, {'start': 3603.0, 'end': 3604.0, 'text': 'Bye'}]
written = cheetahcap.write_asc(trip, 25)
check('as Subtitle Edit writes it', written.split('\r\n')[:11], [
    '*WIDTH 32', '', '** Caption Number 1', '*PopOn', '*T 01:00:00:00', '*E 01:00:02:00', '*BottomUp', '*Cf16', 'Hello',
    'world\\E', ''])
check('round trip at 25', [(s['start'], s['end'], s['text']) for s in cheetahcap.parse_asc(written)[0]],
      [(3600.0, 3602.0, 'Hello\nworld'), (3603.0, 3604.0, 'Bye')])
dropped = cheetahcap.write_asc(trip, 29.97)
got = cheetahcap.parse_asc(dropped)[0]
check('29.97: *DropFrame, read back within a frame', (dropped.startswith('*DropFrame\r\n'),
                                                     all(abs(a['start'] - b['start']) < 0.034 for a, b in zip(got, trip))), (True, True))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.asc').write_text(SAMPLE, encoding='utf-8')
session.VIDEO = {}
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.asc'))
check('opens as CHEETAHASC, its rate kept', (fmt, len(opened), session.FORMAT['options']['fps']), ('CHEETAHASC', 3, 29.97))
(folder / 'other.asc').write_text('@ headers\r\n\r\n1.\r\n00:00:04:12\r\n00:00:06:05\r\nHello.\r\n', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'other.asc'))
    check('another .asc says it is not one', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('another .asc says it is not one', True, True)
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 25}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.asc'), 'CHEETAHASC')
check('saved and read back', [s['text'] for s in cheetahcap.read_asc(str(folder / 'out.asc'))[0]], ['Hello\nworld', 'Bye'])
check('the binary .cap untouched', cheetahcap.parse(cheetahcap.write(trip))[0][0]['text'], 'Hello\n<i>world</i>')

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('CHEETAHASC')
panel._refresh_options()
check('in the menu with a frame rate', ('CHEETAHASC' in SUBTITLE_FORMATS, panel.sub_options.isHidden()), (True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
