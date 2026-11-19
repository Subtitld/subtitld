"""Audacity's labels: reading the manual's region and point labels and its
extended form with frequency lines, writing what Audacity and Subtitle
Edit write, a round trip, telling them from the other .txt formats, and
the files and the export dialog.

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
from subtitld.modules import audacity, avid_subcap, dvd_scripts, file_io, legacy, session, ultrastar

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# The manual's examples: a point label, a region label, the extended form's
# frequency lines, a label of no text, and a point label near the next.
LABELS = ('2.150000\t2.150000\tpoint label at 2.15 seconds\n3.400000\t6.100000\tregion label from 3.4 to 6.1 seconds\n'
          '\\\t1484.669312\t2969.338523\n7.000000\t8.000000\n9,500000\t9,500000\tclose to the next\n10.000000\t11.000000\tlast\n')
print('reading')
check('a point label until the next, at most three seconds; frequency lines and empty labels out; decimal commas',
      audacity.parse(LABELS),
      [{'start': 2.15, 'end': 3.4, 'text': 'point label at 2.15 seconds'},
       {'start': 3.4, 'end': 6.1, 'text': 'region label from 3.4 to 6.1 seconds'},
       {'start': 9.5, 'end': 10.0, 'text': 'close to the next'}, {'start': 10.0, 'end': 11.0, 'text': 'last'}])
try:
    audacity.parse('1.0\t2.0\tfine\nnot a label\n')
    check('a line that is no label: refused', None, 'ValueError')
except ValueError as error:
    check('a line that is no label: refused', str(error).startswith('Line 2 is not an Audacity label'), True)

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': '<i>Two</i>\nlines\twith a tab'}, {'start': 3.0, 'end': 3.25, 'text': '{\\an8}Top'}]
check('six decimals, tabs between, plain text on one line', audacity.write(trip),
      '1.000000\t2.500000\tTwo lines with a tab\n3.000000\t3.250000\tTop\n')
check('read back', audacity.parse(audacity.write(trip)),
      [{'start': 1.0, 'end': 2.5, 'text': 'Two lines with a tab'}, {'start': 3.0, 'end': 3.25, 'text': 'Top'}])

print('telling them apart')
OTHERS = {'VPlayer': '00:00:01:Hello\n00:00:03:World\n', 'TMPlayer': '0:00:01=Hello\n',
          'MicroDVD-ish': '{1}{25}Hello\n', 'a transcript': 'Hello there.\nHow are you?\n',
          'UltraStar': '#TITLE:Song\n#BPM:300\n: 0 4 59 Hel\nE\n',
          'Encore': '00:00:01:00 00:00:02:00 Hello\n', 'a tab table': 'name\tage\nann\t3\n'}
check('labels are Audacity\'s, a cut-off last line not held against them',
      (audacity.looks_like(LABELS), audacity.looks_like(LABELS[:70])), (True, True))
check('nothing else is', {name: audacity.looks_like(text) for name, text in OTHERS.items()}, dict.fromkeys(OTHERS, False))
check('and labels are none of the others',
      (bool(legacy.kind(LABELS)), bool(ultrastar.kind(LABELS)), bool(avid_subcap.is_subcap(LABELS)),
       bool(dvd_scripts.kind(LABELS, 'txt'))), (False, False, False, False))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'labels.txt').write_text(LABELS, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'labels.txt'))
check('a labels .txt opens as Audacity', (fmt, len(opened)), ('AUDACITY', 4))
(folder / 'vplayer.txt').write_text(OTHERS['VPlayer'], encoding='utf-8')
check('a VPlayer .txt still opens as VPlayer', file_io.process_subtitles_file(str(folder / 'vplayer.txt'))[1], 'VPLAYER')
session.SUBTITLE.segments = trip
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.txt'), 'AUDACITY')
check('saved, and opened again', file_io.process_subtitles_file(str(folder / 'out.txt'))[1], 'AUDACITY')
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['AUDACITY']['extensions'], ['txt'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('AUDACITY')
panel._refresh_options()
check('in the export menu, with the text options, no formatting',
      ('AUDACITY' in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.formatting_combo.isHidden()), (True, False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
