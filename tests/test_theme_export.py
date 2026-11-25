"""Themes in formats of subtitles (modules.title_themes.export_look,
subtitle_styles.preview_look): a title written to ASS and TTML in its
theme's fallback with its own settings over it, its other text slots as
lines, a title whose theme has no fallback left out (and the project left
as it was), the export dialog saying so, and the subtitles' "preview" look
following the project's theme.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import ass, config, file_io, session, subtitle_styles, title_themes
from subtitld.modules.segment import Segment, Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# A title theme with no fallback, and a subtitle theme with one.
themes_folder = session.PATH_SUBTITLD_USER_DATA / 'themes'
themes_folder.mkdir(parents=True, exist_ok=True)
for name, manifest in (('drawn.html', {'format': 1, 'id': 'test.drawn', 'kind': 'title', 'name': 'Drawn only'}),
                       ('boxed.html', {'format': 1, 'id': 'test.boxed', 'kind': 'subtitle', 'name': 'Boxed',
                                       'fields': [{'key': 'color', 'type': 'color', 'default': '#ff00ff00'}],
                                       'fallback': {'font_family': 'Ubuntu', 'font_size': 50, 'box_color': '#cc112233',
                                                    'box_padding': 12}})):
    (themes_folder / name).write_text('<!doctype html><html><head><meta charset="utf-8">'
                                      f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
                                      '</head><body></body></html>')
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.VIDEO = {'framerate': 25, 'width': 1920, 'height': 1080}
check('themes there', sorted(title_themes.available(reload=True)), ['subtitld.lower_third', 'subtitld.plain', 'test.drawn'])
check('subtitle themes there', sorted(subtitle_styles.available(reload=True)), ['subtitld.classic', 'test.boxed'])

lower = Segment({'kind': 'title', 'start': 1, 'end': 4, 'text': 'Maria Souza', 'title_theme': 'subtitld.lower_third',
                 'title_slots': {'role': 'Field reporter'}, 'title_settings': {'color': '#ffffee00'}})
plain = Segment({'kind': 'title', 'start': 5, 'end': 7, 'text': 'Chapter one', 'title_theme': 'subtitld.plain',
                 'title_settings': {'font_weight': '400', 'box_enabled': True, 'uppercase': True}})
drawn = Segment({'kind': 'title', 'start': 8, 'end': 9, 'text': 'Only drawn', 'title_theme': 'test.drawn'})
line = Segment({'start': 0, 'end': 1, 'text': 'A subtitle'})

print('the export look')
look = title_themes.export_look(lower)
check('Lower third: its fallback\'s size', look['font_size'], 54)
check('its own setting over the fallback', look['color'], '#ffffee00')
check('its placement', look['placement'], 'lower_third')
check('its box (a setting it has, on)', (look['box_enabled'], look['box_color']), (True, '#d9141a21'))
look = title_themes.export_look(plain)
check('Plain: weight 400 is not bold', look['bold'], False)
check('Plain: its box, its capitals', (look['box_enabled'], look['uppercase']), (True, True))
check('no fallback: drawn only', (title_themes.export_look(drawn), title_themes.exportable(drawn)), (None, False))
check('the text with its role', title_themes.export_text(lower), 'Maria Souza\nField reporter')
title_themes.set_value(lower, 'show_logo', False)
check('a hidden image slot changes nothing', title_themes.export_text(lower), 'Maria Souza\nField reporter')

print('ASS')
text = ass.write([line, lower, plain], 1920, 1080)
check('a Title style, lower left (1)', 'Style: Title,Montserrat,54,' in text and ',1,96,96,54,' in text.split('Style: Title,')[1].split('\n')[0], True)
check('the role a line of its own', 'Maria Souza\\NField reporter' in text, True)
check('Plain\'s in capitals', 'CHAPTER ONE' in text, True)

print('TTML')
copy = title_themes.as_subtitle(lower)
check('its place and its role', copy['text'].startswith('{\\an1}') and 'Field reporter' in copy['text'], True)
check('no title fields left', [key for key in copy if key.startswith('title_')], [])

print('saving: titles drawn only are left out')
session.SUBTITLE = Subtitle(segments=[line, lower, plain, drawn])
folder = tempfile.mkdtemp()
session.FORMAT = {'format': 'ASS', 'options': {'titles': True}}
file_io.save_file(os.path.join(folder, 'a.ass'), 'ASS')
written = open(os.path.join(folder, 'a.ass'), encoding='utf-8').read()
check('the exportable titles written', ('Maria Souza' in written, 'CHAPTER ONE' in written), (True, True))
check('the drawn-only one left out', 'Only drawn' in written, False)
check('the project as it was', [s['text'] for s in session.SUBTITLE.segments],
      ['A subtitle', 'Maria Souza', 'Chapter one', 'Only drawn'])
session.SUBTITLE = Subtitle(segments=[line, lower, plain])
session.FORMAT = {'format': 'ASS', 'options': {'titles': True}}
file_io.save_file(os.path.join(folder, 'b.ass'), 'ASS')
check('every title exportable: all there', 'CHAPTER ONE' in open(os.path.join(folder, 'b.ass'), encoding='utf-8').read(), True)

print('the export dialog says so')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.SUBTITLE = Subtitle(segments=[line, lower, drawn])
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('ASS')
panel._refresh_options()                 # as picking it does
panel.titles_check.setChecked(True)
panel._show_titles_note()
check('the note: one title', (panel.titles_note.isHidden(), '1' in panel.titles_note.text()), (False, True))
panel.titles_check.setChecked(False)
check('not asked for titles: no note', panel.titles_note.isHidden(), True)
session.SUBTITLE = Subtitle(segments=[line, lower])
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('ASS')
panel._refresh_options()                 # as picking it does
panel.titles_check.setChecked(True)
panel._show_titles_note()
check('every title exportable: no note', panel.titles_note.isHidden(), True)

print('the subtitles\' "preview" look')
session.SUBTITLE = Subtitle(segments=[line])
check('Classic: its own', subtitle_styles.preview_look()['font_family'], 'Montserrat')
subtitle_styles.set_current('test.boxed')
preview = subtitle_styles.preview_look()
check('another theme: its fallback', (preview['font_family'], preview['font_size'], preview['backgroundbox_color'],
                                     preview['backgroundbox_padding']), ('Ubuntu', 50, '#cc112233', 12))
check('its own setting of Classic\'s name over it', preview['color'], '#ff00ff00')
check('no shadow in its fallback: none', preview['shadow_enabled'], False)

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
