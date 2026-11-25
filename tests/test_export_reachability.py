"""Every format Subtitld writes can be written from the app: each one in the
session's list (but USFX, which Save writes) is offered by the export
dialog's Subtitles list, karaoke's and the player era's after a line each;
chosen there, the options the dialog gives write a file that opens again.
And karaoke timing ({\\k}) goes through USF and USFX whole, not only a
subtitle's first code.

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
from subtitld.modules import file_io, karaoke, session, usf
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('the export list')
from subtitld.interface.export_dialog import KARAOKE_FORMATS, PLAYER_ERA_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = Subtitle(segments=[{'start': 1.0, 'end': 2.0, 'text': 'Hi', 'speaker': 'A'}])
session.SPEAKERS, session.FORMAT, session.VIDEO = {}, {}, {}
panel = _SubtitlesPanel()
combo = panel.format_combo.combobox
offered = [combo.itemText(index) for index in range(combo.count())]
missing = [fmt for fmt in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS if fmt != 'USFX' and fmt not in offered]
check('every format the session lists, but USFX, is offered', missing, [])
check('karaoke: UltraStar, KAR, MIDI, KBP', KARAOKE_FORMATS, ('ULTRASTAR', 'KAR', 'MIDI', 'KBP'))
check('the player era: thirty formats', len(PLAYER_ERA_FORMATS), 30)
separators = [index for index in range(combo.count()) if not combo.itemText(index)]
check('a line before karaoke and one before the player era',
      [offered[index + 1] for index in separators], ['ULTRASTAR', PLAYER_ERA_FORMATS[0]])
# The stylesheet's item delegate would draw them no taller than nothing.
app.setStyleSheet(open(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'stylesheet.qss')).read())
panel.show()
combo.showPopup()
app.processEvents()
check('the lines are drawn, as tall as a separator',
      [combo.view().visualRect(combo.model().index(index, 0)).height() for index in separators], [9, 9])
combo.hidePopup()

print('chosen in the dialog, written, opened again')
folder = Path(tempfile.mkdtemp())
WRITTEN = [{'start': 1.0, 'end': 3.5, 'text': '{\\k50}First {\\k100}line', 'speaker': 'A'},
           {'start': 4.0, 'end': 6.25, 'text': '{\\k60}Second {\\k80}cue', 'speaker': 'A'}]


def export(fmt, options_from=None):
    """As the export button does: the dialog's options, then the file."""
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    _fmt, _choices, options = panel.export_choices()
    session.FORMAT['options'] = options
    session.SUBTITLE['segments'] = [dict(segment) for segment in WRITTEN]
    path = folder / f'out-{fmt}.{session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[fmt]["extensions"][0]}'
    file_io.save_file(str(path), fmt)
    return path, options


for fmt in KARAOKE_FORMATS + PLAYER_ERA_FORMATS:
    session.FORMAT, session.VIDEO = {}, {'framerate': 25.0}
    path, options = export(fmt)
    session.SUBTITLE['segments'] = []
    opened, found = file_io.process_subtitles_file(str(path))
    check(f'{fmt}: written and opened again', (found, len(opened)), (fmt, 2))

for fmt in PLAYER_ERA_FORMATS:
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    options = panel.export_choices()[2]
    check(f'{fmt}: an encoding and line ends', ('encoding' in options, 'line_ends' in options, panel.srt_options.isHidden()),
          (True, True, False))

for fmt in ('AQTITLE', 'SPRUCE', 'ORESME', 'SSTS', 'DVDSS'):
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    check(f'{fmt}: counted in frames, at a rate chosen', ('fps' in panel.export_choices()[2], panel.sub_options.isHidden()),
          (True, False))
panel.format_combo.setCurrentText('MPL2')
panel._refresh_options()
check('MPL2: no frame rate, as it has none', ('fps' in panel.export_choices()[2], panel.sub_options.isHidden()), (False, True))

# A crude encoding asked for is the one written.
panel.format_combo.setCurrentText('MPL2')
panel._refresh_options()
panel.encoding_combo.combobox.setCurrentIndex(panel.encoding_combo.combobox.findData('cp1252'))
panel.line_ends_combo.combobox.setCurrentIndex(panel.line_ends_combo.combobox.findData('crlf'))
session.FORMAT = {'options': panel.export_choices()[2]}
session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 2.0, 'text': 'Café', 'speaker': 'A'}]
file_io.save_file(str(folder / 'cp.txt'), 'MPL2')
data = (folder / 'cp.txt').read_bytes()
check('MPL2 in cp1252 with CRLF', ('Café'.encode('cp1252') in data, b'\r\n' in data), (True, True))

# KAR: the MIDI file the song was opened from goes with the export, for its music.
source = folder / 'out-KAR.kar'
session.FORMAT = {'options': {'kar_source': str(source)}}
panel.format_combo.setCurrentText('KAR')
panel._refresh_options()
check('KAR: the song opened from carried to the export', panel.export_choices()[2].get('kar_source'), str(source))
session.FORMAT = {}
check('KAR: none when nothing was opened', 'kar_source' in panel.export_choices()[2], False)

print('karaoke timing through USF and USFX')
SUNG = [{'start': 1.0, 'end': 3.0, 'speaker': 'A', 'text': '{\\k50}Hel{\\k30}lo {\\k20}{\\kf60}world'},
        {'start': 4.0, 'end': 6.0, 'speaker': 'A', 'text': '{\\an8}{\\k40}<i>Up{\\K40}per</i> {\\ko30\\b1}line{\\b0}'}]
written = usf.USFWriter().write([dict(segment) for segment in SUNG], language='en')
read = usf.USFReader().read(written)
check('USF writer and reader: every code, in its place', [segment['text'] for segment in read],
      ['{\\k50}Hel{\\k30}lo {\\k20}{\\kf60}world', '{\\an8}{\\k40}<i>Up{\\K40}per</i> <b>{\\ko30}line</b>'])
check('USF: a code that also sets bold times its syllable as before', karaoke.timed(read[1]['text'], 4.0),
      karaoke.timed(SUNG[1]['text'], 4.0))
for fmt in ('USF', 'USFX'):
    session.SUBTITLE = Subtitle(segments=[dict(segment) for segment in SUNG], language='en')
    session.SPEAKERS, session.FORMAT, session.VIDEO = {}, {}, {}
    file_io.save_file(str(folder / f'sung.{fmt.lower()}'), fmt)
    session.SUBTITLE, session.SPEAKERS, session.FORMAT, session.VIDEO = Subtitle(), {}, {}, {}
    opened = file_io.process_subtitles_file(str(folder / f'sung.{fmt.lower()}'))[0]
    if session.USFX_BACKGROUND_LOAD is not None:
        session.USFX_BACKGROUND_LOAD.wait(10000)
    # Opened, a subtitle's {\\k} codes are its timed pieces (modules.segment):
    # each syllable at the time it was sung, the wipe kept.
    pieces = opened[0]['text'] if isinstance(opened[0]['text'], list) else []
    check(f'{fmt}: saved and opened, the syllables timed as before',
          [(round(opened[0]['start'] + p['start'], 3), round(opened[0]['start'] + p['end'], 3), p['text']) for p in pieces],
          [syllable for syllable in karaoke.timed(SUNG[0]['text'], SUNG[0]['start']) if syllable[2]])
    check(f'{fmt}: the \\kf one still wipes', [p.get('effect') for p in pieces], [None, None, 'wipe'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
