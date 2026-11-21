"""The timeline's look: its settings, kept in CONFIG['timeline']['style'] with
defaults left out; the loose colours of before moved in; the panel in the
Interface panel's Timeline tab; and the timeline painted and laid out as
they say.

Standalone script, like the other suites here; puts this checkout's src/
on the path itself.
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import json
from PySide6.QtWidgets import QApplication, QGroupBox, QWidget, QVBoxLayout
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import config, history, session, timeline_style
from subtitld.modules.segment import Subtitle
from subtitld.interface import timeline
from subtitld.interface.timeline_style_panel import TimelineStylePanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('the settings')
config_path = Path(tempfile.mkdtemp()) / 'subtitld.config'
config_path.write_text(json.dumps({'timeline': {'snap': True, 'grid_color': '#ff112233', 'cursor_color': 'red',
                                                'style': {'cursor_width': 4}}}))
session.CONFIG = config.Config(config_path)
check('loose colours of before move into the style; ones it cannot hold are dropped',
      session.CONFIG['timeline'], {'snap': True, 'style': {'cursor_width': 4, 'grid_color': '#ff112233'}})
values = timeline_style.values()
check('every field has a value', sorted(values), sorted(f['key'] for f in timeline_style.FIELDS))
check('what was set, and the defaults', (values['grid_color'], values['cursor_width'], values['subtitle_corner_radius']),
      ('#ff112233', 4.0, 3.0))
check('every field in a group the panel knows', all(f['group'] in timeline_style.GROUPS for f in timeline_style.FIELDS),
      True)
check('a colour without alpha is opaque', timeline_style.set_value('subtitle_fill_color', '#AABBCC'), '#ffaabbcc')
check('a number past its range is held to it', timeline_style.set_value('subtitle_corner_radius', 500), 40)
for key, value in (('subtitle_corner_radius', 'round'), ('subtitle_text_vertical_alignment', 'middle'),
                   ('subtitle_fill_gradient', 1)):
    try:
        timeline_style.set_value(key, value)
        check(f'{key} = {value!r} refused', False, True)
    except ValueError:
        check(f'{key} = {value!r} refused', True, True)
timeline_style.set_value('subtitle_corner_radius', 3)
check('a default is not saved', 'subtitle_corner_radius' in session.CONFIG['timeline']['style'], False)
check('changes: what differs from the defaults',
      timeline_style.changes(), {'cursor_width': 4.0, 'grid_color': '#ff112233', 'subtitle_fill_color': '#ffaabbcc'})
look = timeline_style.current()
check('the look is made once', timeline_style.current() is look, True)
look.color('cursor_color').setAlpha(0)
check('its colours are copies', look.color('cursor_color').alpha(), 0xcc)
timeline_style.set_values({'subtitle_gap': 4, 'unknown': 1, 'cursor_color': 'not a colour'})
check('a whole look: only what its fields can hold', timeline_style.changes(), {'subtitle_gap': 4})
check('a new look after a change', timeline_style.current() is look, False)
timeline_style.reset()
check('reset: the defaults', timeline_style.changes(), {})


print('the panel')
changes = []
panel = TimelineStylePanel()
panel.changed.connect(lambda: changes.append(1))


def control(key):
    return panel.controls[key][1]


check('a control for each field', sorted(panel.controls), sorted(f['key'] for f in timeline_style.FIELDS))
check('the kinds of control',
      [type(control(k)).__name__ for k in ('grid_color', 'subtitle_font', 'subtitle_font_size', 'grid_width',
                                            'subtitle_fill_gradient', 'subtitle_text_vertical_alignment')],
      ['QPushButton', 'QComboBox', 'QSpinBox', 'QDoubleSpinBox', 'QCheckBox', 'QComboBox'])
groups = panel.fields_widget.findChildren(QGroupBox)
check('a box a group, the ruler and the waveform ones switched on and off',
      [(g.title(), g.isCheckable()) for g in groups],
      [('Background', False), ('Time ruler', True), ('Grid', False), ('Waveform', True), ('Subtitle boxes', False),
       ('Subtitle fill', False), ('Subtitle text', False), ('Playhead', False), ('Marks', False)])
check('labels from the locale files', (control('subtitle_full_height').text(), panel.reset_button.text()),
      ('As tall as the timeline', 'Reset to defaults'))
check('the gradient\'s colours hidden without it', panel.cells['subtitle_fill_gradient_color'].isHidden(), True)
control('subtitle_fill_gradient').setChecked(True)
check('shown with it', (panel.cells['subtitle_fill_gradient_color'].isHidden(), timeline_style.values()['subtitle_fill_gradient']),
      (False, True))
check('the band\'s distances shown', panel.cells['subtitle_top'].isHidden(), False)
control('subtitle_full_height').setChecked(True)
check('hidden when the boxes are as tall as the timeline', panel.cells['subtitle_top'].isHidden(), True)
grid = panel.cells['subtitle_gap'].parentWidget().layout()
check('the next field takes its place', grid.getItemPosition(grid.indexOf(panel.cells['subtitle_gap']))[:2], (0, 1))
control('subtitle_corner_radius').setValue(12)
check('a number saved', timeline_style.values()['subtitle_corner_radius'], 12.0)
groups[3].setChecked(False)
check('a group\'s switch saved', timeline_style.values()['show_waveform'], False)
check('each change said', len(changes), 4)
panel.reset()
check('reset: the defaults, and the controls on them',
      (timeline_style.changes(), control('subtitle_corner_radius').value(), control('subtitle_full_height').isChecked()),
      ({}, 3.0, False))


print('the timeline')


class Player:
    def seek(self, position):
        pass

    def is_paused(self):
        return True


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(800, 200)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.preview_panel_player = Player()
        panel = types.SimpleNamespace(update=lambda window: None)
        self.left_panel_stackedwidgets = types.SimpleNamespace(currentWidget=lambda: panel)
        self.subtitles_panel_qlistwidget = types.SimpleNamespace(update_content=lambda: None)


session.CONFIG['timeline_zoom'] = 100
session.CONFIG['default_values'] = {}
session.VIDEO = {'duration': 20.0}
session.SPEAKERS = {'A': {'color': '#ff2e93'}}
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'},
                                          {'start': 3.0, 'end': 5.0, 'text': '', 'speaker': 'A'}],
                             'selected': None, 'position': 10.0})
history.history_clear()
host = Host()
timeline.load(host)
host.layout().addWidget(host.timeline_scroll)
host.show()
QTest.qWait(100)
widget = host.timeline_widget
height = widget.height()
check('the subtitles\' band by default', (widget.subtitle_y, widget.subtitle_height), (55, height - 80))


def pixel(x, y):
    return widget.grab().toImage().pixelColor(x, y).name(QColor.HexArgb)


timeline_style.set_values({'subtitle_fill_color': '#ff00ff00', 'subtitle_corner_radius': 0})
widget.apply_style()
check('a box in its fill colour', pixel(150, 100), '#ff00ff00')
check('two boxes that touch, touch', pixel(300, 100), '#ff00ff00')
timeline_style.set_values({'subtitle_fill_color': '#ff00ff00', 'subtitle_corner_radius': 0, 'subtitle_gap': 10,
                           'subtitle_full_height': True})
widget.apply_style()
check('as tall as the timeline', (widget.subtitle_y, widget.subtitle_height), (0, height))
check('filled at its top', pixel(150, 1), '#ff00ff00')
check('the gap between two', pixel(300, 100) != '#ff00ff00', True)
check('half of it off each', [pixel(x, 100) == '#ff00ff00' for x in (293, 296, 304, 307)], [True, False, False, True])
timeline_style.set_values({'subtitle_fill_color': '#ff00ff00', 'subtitle_corner_radius': 20})
widget.apply_style()
check('a rounded corner is not filled', pixel(101, 56) != '#ff00ff00', True)
check('the middle is', pixel(200, 100), '#ff00ff00')
timeline_style.set_values({'subtitle_top': 30, 'subtitle_bottom': 40, 'waveform_top': 10, 'waveform_bottom': 5})
widget.apply_style()
check('the band where the look says',
      (widget.subtitle_y, widget.subtitle_height, widget.waveform_y, widget.waveform_height),
      (30, height - 70, 10, height - 15))
timeline_style.set_values({'background_color': '#ff0000ff'})
widget.apply_style()
check('the background', pixel(700, 5), '#ff0000ff')
session.SUBTITLE.position = 6.0
timeline_style.set_values({'cursor_color': '#ffff00ff', 'cursor_width': 6})
widget.apply_style()
check('the playhead in its colour, as wide as it is set', (pixel(598, 150), pixel(602, 150), pixel(606, 150) == '#ffff00ff'),
      ('#ffff00ff', '#ffff00ff', False))

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
