"""The timeline's look: the timeline's own settings and a box look per kind
of cue, kept in CONFIG['timeline']['style'] with defaults and inherited
values left out; the loose colours of before moved in; a kind other than
subtitles inheriting their look and having its own lane; the ruler's
formats and ticks; the playhead's line, head, time and badge; the panel
in the Interface panel's Timeline tab.

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
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import config, history, session, timeline_frames, timeline_style, timeline_svg
from subtitld.modules.segment import Subtitle
from subtitld.interface import timeline
from subtitld.interface.timeline_style_panel import BOX, TIMELINE, TimelineStylePanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def look(timeline=None, **box):
    """A whole look: the timeline's settings, and the subtitles' boxes'."""
    return {'timeline': timeline or {}, 'boxes': {'subtitle': box}}


print('the settings')
config_path = Path(tempfile.mkdtemp()) / 'subtitld.config'
config_path.write_text(json.dumps({'timeline': {'snap': True, 'grid_color': '#ff112233', 'cursor_color': 'red',
                                                'subtitle_fill_color': '#ff445566'}}))
session.CONFIG = config.Config(config_path)
check('loose colours of before move into the style; ones it cannot hold are dropped', session.CONFIG['timeline'],
      {'snap': True, 'style': {'timeline': {'grid_color': '#ff112233'}, 'boxes': {'subtitle': {'fill_color': '#ff445566'}}}})
values = timeline_style.values()
check('every timeline field has a value', sorted(values), sorted(f['key'] for f in timeline_style.TIMELINE_FIELDS))
check('and every box field', sorted(timeline_style.values('subtitle')), sorted(f['key'] for f in timeline_style.BOX_FIELDS))
check('what was set, and the defaults', (values['grid_color'], values['cursor_width'],
                                         timeline_style.values('subtitle')['corner_radius']), ('#ff112233', 2.0, 3))
check('every field in a group the panel knows',
      (all(f['group'] in timeline_style.TIMELINE_GROUPS for f in timeline_style.TIMELINE_FIELDS),
       all(f['group'] in timeline_style.BOX_GROUPS for f in timeline_style.BOX_FIELDS)), (True, True))
check('a colour without alpha is opaque', timeline_style.set_value('fill_color', '#AABBCC', 'subtitle'), '#ffaabbcc')
check('a number past its range is held to it', timeline_style.set_value('corner_radius', 500, 'subtitle'), 40)
for key, value, kind in (('corner_radius', 'round', 'subtitle'), ('text_vertical_alignment', 'middle', 'subtitle'),
                         ('fill_gradient', 1, 'subtitle'), ('cursor_width', 'wide', None)):
    try:
        timeline_style.set_value(key, value, kind)
        check(f'{key} = {value!r} refused', False, True)
    except ValueError:
        check(f'{key} = {value!r} refused', True, True)
try:
    timeline_style.set_value('corner_radius', 3)
    check('a box setting is not the timeline\'s', False, True)
except KeyError:
    check('a box setting is not the timeline\'s', True, True)
timeline_style.set_value('corner_radius', 3, 'subtitle')
check('a default is not saved', 'corner_radius' in session.CONFIG['timeline']['style']['boxes']['subtitle'], False)
check('changes: what differs from the defaults', timeline_style.changes(),
      look({'grid_color': '#ff112233'}, fill_color='#ffaabbcc'))
current = timeline_style.current()
check('the look is made once', timeline_style.current() is current, True)
current.color('cursor_color').setAlpha(0)
check('its colours are copies', current.color('cursor_color').alpha(), 0xcc)
timeline_style.set_values({'timeline': {'cursor_color': 'not a colour', 'unknown': 1}, 'boxes': {'subtitle': {'gap': 4}}})
check('a whole look: only what its fields can hold', timeline_style.changes(), look(gap=4))
check('a new look after a change', timeline_style.current() is current, False)
timeline_style.reset()
check('reset: the defaults', timeline_style.changes(), {'timeline': {}, 'boxes': {}})

print('kinds of cue')
check('subtitles only, for now', list(timeline_style.KINDS), ['subtitle'])
check('a cue of a kind not registered is a subtitle', timeline_style.kind_of({'kind': 'title', 'start': 0}), 'subtitle')
timeline_style.set_values({'boxes': {'subtitle': {'corner_radius': 9, 'fill_color': '#ff112233'},
                                     'credits': {'corner_radius': 0}}})
check('a kind this Subtitld does not know is kept, for the one that will',
      timeline_style.changes()['boxes']['credits'], {'corner_radius': 0})
timeline_style.register_kind('title', {'fill_color': '#ff884400'})
check('registered: its own', timeline_style.kind_of({'kind': 'title'}), 'title')
title = timeline_style.values('title')
check('a kind: its defaults, the subtitles\' look for the rest', (title['fill_color'], title['corner_radius']),
      ('#ff884400', 9))
timeline_style.set_value('corner_radius', 9, 'title')
check('what it inherits is not saved for it', 'title' in timeline_style.changes()['boxes'], False)
timeline_style.set_value('corner_radius', 20, 'title')
timeline_style.set_value('corner_radius', 2, 'subtitle')
check('what it sets stays when the subtitles change',
      (timeline_style.values('title')['corner_radius'], timeline_style.values('subtitle')['corner_radius']), (20, 2))
check('a look of its own', timeline_style.current().box('title')['corner_radius'], 20)
timeline_style.reset()
del timeline_style.KINDS['title']


print('times as the ruler writes them')
times = {fmt: (timeline_style.time_label(3725.48, fmt, 25), timeline_style.time_label(3725.48, fmt, 25, precise=True),
               timeline_style.time_label(65, fmt, 30))
         for fmt in timeline_style.TIME_FORMATS}
check('each format, to the second and precise', times, {
    'clock': ('01:02:05', '01:02:05.480', '01:05'),
    'seconds': ('3725s', '3725.480s', '65s'),
    'timecode': ('01:02:05:12', '01:02:05:12', '00:01:05:00'),
    'frames': ('93137', '93137', '1950')})
check('labels a step apart that leaves the room asked for',
      [timeline_style.label_step(pps, 75) for pps in (100, 60, 20, 1, 0.01)], [1, 2, 5, 120, 3600])


print('SVG shapes')
GOOD = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20" height="10" fill="currentColor"/></svg>'
OWN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#0000ff"/></svg>'
refused = {
    'a DOCTYPE': b'<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>',
    'a script': b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    'an image': b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>',
    'a link outside': b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink"><use x:href="other.svg#a"/></svg>',
    'a url() outside': b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(http://x/y)"/></svg>',
    'an event handler': b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
    'a style import': b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import "x.css";</style></svg>',
    'not an SVG': b'<html/>',
    'not XML': b'<svg',
    'too big': b'<svg xmlns="http://www.w3.org/2000/svg">' + b' ' * (300 * 1024) + b'</svg>',
}
outcomes = {}
for why, data in refused.items():
    try:
        timeline_svg.check(data)
        outcomes[why] = 'taken'
    except timeline_svg.ShapeError:
        outcomes[why] = 'refused'
check('refused: anything reaching outside it, or too big', set(outcomes.values()), {'refused'})
timeline_svg.check(GOOD)
check('a plain one, and one with gradients of its own, taken', timeline_svg.check(
    b'<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/></defs><rect fill="url(#g)"/></svg>'), None)
shapes = Path(tempfile.mkdtemp())
(shapes / 'good.svg').write_bytes(GOOD)
(shapes / 'own.svg').write_bytes(OWN)
(shapes / 'bad.svg').write_bytes(refused['a script'])
good_name = timeline_svg.import_file(shapes / 'good.svg')
check('kept under a name made of its content', (len(good_name), good_name.endswith('.svg'),
                                                (timeline_svg.folder() / good_name).read_bytes() == GOOD), (16, True, True))
check('its width over its height', timeline_svg.aspect(good_name), 2.0)
check('drawn in the colour given', timeline_svg.image(good_name, 4, 2, '#ff0000').toImage().pixelColor(1, 1).name(),
      '#ff0000')
own_name = timeline_svg.import_file(shapes / 'own.svg')
check('one in colours of its own keeps them', (timeline_svg.takes_color(own_name),
      timeline_svg.image(own_name, 4, 4, '#ff0000').toImage().pixelColor(1, 1).name()), (False, '#0000ff'))
(timeline_svg.folder() / own_name).write_bytes(refused['a script'])
timeline_svg.forget()
check('a kept one changed into one refused: not drawn', timeline_svg.available(own_name), False)
check('a name not of a kept shape: nothing', (timeline_svg.available('../../etc/passwd'), timeline_svg.image('x.svg', 4, 4)),
      (False, None))


print('the panel')
changes = []
panel = TimelineStylePanel()
panel.changed.connect(lambda: changes.append(1))


def control(key, scope=BOX):
    return panel.controls[(scope, key)][1]


def cell(key, scope=BOX):
    return panel.cells[(scope, key)]


check('a control for each field', sorted(panel.controls),
      sorted([(TIMELINE, f['key']) for f in timeline_style.TIMELINE_FIELDS] + [(BOX, f['key']) for f in timeline_style.BOX_FIELDS]))
check('the kinds of control',
      [type(control(k, s)).__name__ for k, s in (('grid_color', TIMELINE), ('font', BOX), ('font_size', BOX),
                                                 ('grid_width', TIMELINE), ('fill_gradient', BOX),
                                                 ('text_vertical_alignment', BOX))],
      ['QPushButton', 'QComboBox', 'QSpinBox', 'QDoubleSpinBox', 'QCheckBox', 'QComboBox'])
groups = panel.fields_widget.findChildren(QGroupBox)
check('a box a group, the boxes\' after the waveform; the ruler, its ticks and the waveform switched on and off',
      [(g.title(), g.isCheckable()) for g in groups],
      [('Background', False), ('Time ruler', True), ('Ruler ticks', True), ('Grid', False), ('Waveform', True),
       ('Boxes', False), ('Frames (SVG)', False), ('Box fill', False), ('Box text', False), ('Start and end edges', False), ('Playhead', False),
       ('Repeat and speed badge', False), ('Marks', False)])
check('one kind: no picking it', panel.kind_holder.isHidden(), True)
check('labels from the locale files', (control('full_height').text(), panel.reset_button.text()),
      ('As tall as the timeline', 'Reset to defaults'))
check('the gradient\'s colours hidden without it', cell('fill_gradient_color').isHidden(), True)
control('fill_gradient').setChecked(True)
check('shown with it', (cell('fill_gradient_color').isHidden(), timeline_style.values('subtitle')['fill_gradient']),
      (False, True))
check('the band\'s distances shown', cell('top').isHidden(), False)
control('full_height').setChecked(True)
check('hidden when the boxes are as tall as the timeline', cell('top').isHidden(), True)
check('a head\'s size hidden without a head', cell('cursor_head_size', TIMELINE).isHidden(), True)
head = control('cursor_head', TIMELINE)
head.activated.emit(head.findData('flag'))
check('shown with one', (cell('cursor_head_size', TIMELINE).isHidden(), timeline_style.values()['cursor_head']),
      (False, 'flag'))
check('a head\'s SVG hidden but for a custom head', cell('cursor_head_svg', TIMELINE).isHidden(), True)
head.activated.emit(head.findData('custom'))
check('shown for one', cell('cursor_head_svg', TIMELINE).isHidden(), False)
check('refused, and why', (panel.import_shape(TIMELINE, 'cursor_head_svg', shapes / 'bad.svg'),
                           control('cursor_head_svg', TIMELINE).shape_problem.isHidden()), (None, False))
check('taken, and kept in the look', (panel.import_shape(TIMELINE, 'cursor_head_svg', shapes / 'good.svg'),
                                      timeline_style.values()['cursor_head_svg']), (good_name, good_name))
check('its name shown, the problem gone', (control('cursor_head_svg', TIMELINE).shape_name.text(),
                                            control('cursor_head_svg', TIMELINE).shape_problem.isHidden()), (good_name, True))
head.activated.emit(head.findData('flag'))
grid = cell('gap').parentWidget().layout()
check('the next field takes its place', grid.getItemPosition(grid.indexOf(cell('gap')))[:2], (0, 1))
control('corner_radius').setValue(12)
check('a number saved, for the subtitles', timeline_style.values('subtitle')['corner_radius'], 12)
groups[4].setChecked(False)
check('a group\'s switch saved', timeline_style.values()['show_waveform'], False)
check('each change said', len(changes), 8)
panel.reset()
check('reset: the defaults, and the controls on them',
      (timeline_style.changes(), control('corner_radius').value(), control('full_height').isChecked()),
      ({'timeline': {}, 'boxes': {}}, 3, False))

timeline_style.register_kind('title', {'fill_color': '#ff884400'})
panel.build()
check('more kinds: picked above the boxes', (panel.kind_holder.isHidden(),
                                              [panel.kind_picker.itemData(i) for i in range(panel.kind_picker.count())]),
      (False, ['subtitle', 'title']))
panel.kind_picker.activated.emit(panel.kind_picker.findData('title'))
check('the boxes\' settings: the kind\'s', (panel.kind, control('corner_radius').value()), ('title', 3))
control('corner_radius').setValue(15)
check('saved for that kind alone', (timeline_style.values('title')['corner_radius'],
                                    timeline_style.values('subtitle')['corner_radius']), (15, 3))
panel.reset()
del timeline_style.KINDS['title']
panel.build()
check('a kind gone: back to subtitles', (panel.kind, panel.kind_holder.isHidden()), ('subtitle', True))


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


def apply(new_look):
    timeline_style.set_values(new_look)
    widget.apply_style()


GREEN = '#ff00ff00'
apply(look(fill_color=GREEN, corner_radius=0))
check('a box in its fill colour', pixel(150, 100), GREEN)
check('two boxes that touch, touch', pixel(300, 100), GREEN)
apply(look(fill_color=GREEN, corner_radius=0, gap=10, full_height=True))
check('as tall as the timeline', (widget.subtitle_y, widget.subtitle_height), (0, height))
check('filled at its top', pixel(150, 1), GREEN)
check('the gap between two', pixel(300, 100) != GREEN, True)
check('half of it off each', [pixel(x, 100) == GREEN for x in (293, 296, 304, 307)], [True, False, False, True])
apply(look(fill_color=GREEN, corner_radius=20))
check('a rounded corner is not filled', pixel(101, 56) != GREEN, True)
check('the middle is', pixel(200, 100), GREEN)
apply(look({'waveform_top': 10, 'waveform_bottom': 5}, top=30, bottom=40))
check('the band where the look says',
      (widget.subtitle_y, widget.subtitle_height, widget.waveform_y, widget.waveform_height),
      (30, height - 70, 10, height - 15))
apply(look({'background_color': '#ff0000ff'}))
check('the background', pixel(700, 5), '#ff0000ff')
session.SUBTITLE.position = 6.0
apply(look({'cursor_color': '#ffff00ff', 'cursor_width': 6}))
check('the playhead in its colour, as wide as it is set', (pixel(598, 150), pixel(602, 150), pixel(606, 150) == '#ffff00ff'),
      ('#ffff00ff', '#ffff00ff', False))

print('a kind of cue in a lane of its own')
timeline_style.register_kind('title', {'fill_color': '#ffff8800', 'top': 30, 'bottom': 140})
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'},
                                          {'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A', 'kind': 'title'}],
                             'selected': None, 'position': 10.0})
apply(look(fill_color=GREEN, corner_radius=0))
check('its lane', widget.lane('title'), (30, height - 170))
check('the subtitles\' where they were', widget.lane('subtitle'), (55, height - 80))
check('a title in its colour, in its lane', pixel(150, 40), '#ffff8800')
check('a subtitle in its own, in its own', pixel(150, 120), GREEN)
del timeline_style.KINDS['title']
timeline_style.reset()
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'},
                                          {'start': 3.0, 'end': 5.0, 'text': '', 'speaker': 'A'}],
                             'selected': None, 'position': 6.0})
widget.apply_style()


class Recorder:
    """What the timeline paints: texts and lines, in its own QPainter."""
    def __init__(self):
        self.texts, self.lines = [], []
        original = timeline.QPainter
        recorder = self

        class RecordingPainter(original):
            def drawText(self, *args):
                rect = next((a for a in args if hasattr(a, 'top')), None)
                recorder.texts.append((next(a for a in args if isinstance(a, str)), rect.top() if rect is not None else None))
                return super().drawText(*args)

            def drawLine(self, *args):
                line = args[0] if len(args) == 1 else None
                if line is not None and hasattr(line, 'x1'):
                    recorder.lines.append((round(line.x1(), 1), round(line.y1(), 1), round(line.y2(), 1)))
                return super().drawLine(*args)

        self.original = original
        timeline.QPainter = RecordingPainter

    def done(self):
        timeline.QPainter = self.original


def painted(timeline_values):
    apply(look(timeline_values))
    recorder = Recorder()
    widget.repaint()
    recorder.done()
    return recorder


print('the ruler')
session.VIDEO['framerate'] = 25
got = painted({})
labels = [text for text, top in got.texts if ':' in text]
check('labels a second apart at 100 px a second, mm:ss', labels[:3], ['00:00', '00:01', '00:02'])
check('at their distance from the top', {top for text, top in got.texts if text in labels}, {27})
got = painted({'ruler_time_format': 'timecode', 'ruler_font_size': 16})
check('timecode labels, spaced so they do not touch', [t for t, _ in got.texts if t.count(':') == 3][:2],
      ['00:00:00:00', '00:00:02:00'])
got = painted({'ruler_time_format': 'frames', 'ruler_label_spacing': 300})
check('frame numbers, the first step past 300 px apart (5 s)', [t for t, _ in got.texts if t.isdigit()][:3], ['0', '125'])
got = painted({'ruler_position': 'bottom', 'ruler_offset': 5})
bottom_tops = {top for text, top in got.texts if text.count(':') == 1}
check('along the bottom, 5 px from it', bottom_tops,
      {height - 5 - timeline.QFontMetrics(timeline_style.current().ruler_font).height()})
got = painted({'show_ruler': False})
check('no ruler, no labels', [t for t, _ in got.texts if t.count(':') == 1], [])
got = painted({'ruler_ticks': True, 'ruler_tick_length': 10, 'ruler_subdivisions': 1, 'ruler_offset': 20})
ticks = sorted({line for line in got.lines if line[1] == 20 and line[0] <= 200})
check('a tick at each label and one between', ticks, [(0.0, 20, 30), (50.0, 20, 25), (100.0, 20, 30),
                                                         (150.0, 20, 25), (200.0, 20, 30)])

print('the playhead')
reach = widget.playhead_reach()
playhead = {'cursor_head': 'flag', 'cursor_head_size': 20, 'cursor_show_time': True,
            'cursor_time_background_color': '#ff0000ff', 'cursor_color': GREEN}
check('its time beside it, to the millisecond', any(t == '00:06.000' for t, _ in painted(playhead).texts), True)
check('a flag at the ruler\'s top edge, as wide as 1.6 its size',
      (pixel(625, 32), pixel(636, 32) == GREEN, pixel(625, 20) == GREEN), (GREEN, False, False))
check('the repaint strip reaches past the flag and the time',
      (widget.playhead_reach()[1] > reach[1] + 40, widget.playhead_reach()[0]), (True, 96))
apply(look({'cursor_badge_side': 'right'}))
check('a badge on the right: the strip reaches right', widget.playhead_reach()[1] >= 96, True)
session.CONFIG['playback_speed'] = 1.5
got = painted({'cursor_badge_side': 'right', 'cursor_badge_shape': 'rectangle', 'cursor_badge_color': '#ffff0000'})
check('the speed in the badge', any(t == 'x1.5' for t, _ in got.texts), True)
check('on the right of the playhead, as a rectangle', (pixel(604, 26), pixel(590, 26) == '#ffff0000'), ('#ffff0000', False))
session.CONFIG['playback_speed'] = 1.0

print('a playhead of SVG shapes')
PINK = '#ffff00aa'
apply(look({'cursor_head': 'custom', 'cursor_head_svg': good_name, 'cursor_head_size': 10, 'cursor_color': PINK,
            'cursor_line_style': 'custom', 'cursor_line_svg': good_name, 'cursor_width': 4}))
check('the head: the shape in the playhead\'s colour, twice as wide as tall, on the ruler\'s edge',
      (pixel(591, 30), pixel(609, 30), pixel(612, 30) == PINK, pixel(595, 25) == PINK), (PINK, PINK, False, False))
check('the line: the shape stretched as wide as the line', (pixel(599, 150), pixel(601, 150), pixel(603, 150) == PINK),
      (PINK, PINK, False))
check('the repaint strip reaches the head\'s side', widget.playhead_reach()[0] >= 12, True)
apply(look({'cursor_head': 'custom', 'cursor_head_svg': 'gone0000000.svg', 'cursor_line_style': 'custom',
            'cursor_color': PINK, 'cursor_width': 4}))
check('a shape that is not there: a line Subtitld draws', pixel(600, 150), PINK)

print('the edges that retime')
session.SUBTITLE.position = 15.0


def hover(x, y=100):
    QTest.mouseMove(widget, QPoint(x, y))
    QTest.qWait(20)
    return widget.subtitle_edge_hovered


apply(look(fill_color=GREEN, corner_radius=0, edge_style='bar', edge_handle_thickness=4, edge_start_color='#ffff0000',
           edge_end_color='#ff0000ff'))
check('nothing on an edge the mouse is not on', pixel(101, 100), GREEN)
check('the mouse near the start', hover(110), (id(session.SUBTITLE.segments[0]), 'start'))
check('a bar in the start colour', (pixel(101, 100), pixel(106, 100)), ('#ffff0000', GREEN))
hover(500, 190)
apply(look(fill_color=GREEN, corner_radius=0, edge_style='bar', edge_show='always', edge_handle_thickness=4,
           edge_start_color='#ffff0000', edge_end_color='#ff0000ff'))
check('always: both edges of every box', (pixel(101, 100), pixel(298, 100), pixel(302, 100), pixel(498, 100)),
      ('#ffff0000', '#ff0000ff', '#ffff0000', '#ff0000ff'))
apply(look(fill_color=GREEN, corner_radius=0, edge_style='none', edge_show='always'))
check('none: no edges', (pixel(101, 100), pixel(298, 100)), (GREEN, GREEN))
apply(look(edge_grab_width=40))
check('grabbed as far in as the look says', hover(135), (id(session.SUBTITLE.segments[0]), 'start'))
apply(look())
hover(500, 190)
check('not as far by default', hover(135), None)
hover(500, 190)
timeline_style.reset()

print('box frames')
FRAME = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#00ff00"/><stop offset="1" stop-color="#00ff00"/></linearGradient></defs>
  <g data-x="stretch"><rect x="0" y="0" width="100" height="40" fill="url(#g)"/></g>
  <g data-x="left" data-hit="start"><rect x="0" y="0" width="12" height="40" fill="#ff0000"/></g>
  <g data-x="right" data-hit="end"><rect x="88" y="0" width="12" height="40" fill="#0000ff"/></g>
  <g data-x="center" data-y="top" data-hit="body"><rect x="40" y="0" width="20" height="10" fill="currentColor"/></g>
</svg>'''
(shapes / 'frame.svg').write_bytes(FRAME)
(shapes / 'selected.svg').write_bytes(FRAME.replace(b'#00ff00', b'#ffffff'))
frame_name = timeline_svg.import_file(shapes / 'frame.svg')
selected_name = timeline_svg.import_file(shapes / 'selected.svg')
frame = timeline_frames.get(frame_name)
check('its parts and their anchors', [(p.x, p.y, p.hit) for p in frame.parts],
      [('stretch', 'stretch', None), ('left', 'stretch', 'start'), ('right', 'stretch', 'end'), ('center', 'left', 'body')])
placed = [r.getRect() for _, r in timeline_frames.layout(frame, timeline.QRectF(200, 50, 300, 80))]
check('laid out in a box 300 × 80: stretched, pinned left, right, centred at the top',
      placed, [(200.0, 50.0, 300.0, 80.0), (200.0, 50.0, 12.0, 80.0), (488.0, 50.0, 12.0, 80.0), (340.0, 50.0, 20.0, 10.0)])
box = timeline.QRectF(200, 50, 300, 80)
check('its click regions', [timeline_frames.hit(frame, box, timeline.QPointF(x, y)) for x, y in
                            ((205, 90), (495, 90), (350, 55), (350, 100))], ['start', 'end', 'body', None])
(shapes / 'empty.svg').write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs/></svg>')
check('a shape with nothing to draw is no frame', timeline_frames.get(timeline_svg.import_file(shapes / 'empty.svg')), None)

session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'},
                                          {'start': 3.5, 'end': 6.0, 'text': '', 'speaker': 'A'}],
                             'selected': None, 'position': 15.0})
apply(look(frame_svg=frame_name, frame_selected_svg=selected_name, fill_color='#ff123456'))
check('the frame drawn as the box: its caps at its sides, the middle stretched',
      (pixel(105, 100), pixel(200, 100), pixel(295, 100)), ('#ffff0000', '#ff00ff00', '#ff0000ff'))
check('currentColor: the box\'s fill', pixel(200, 58), '#ff123456')
session.SUBTITLE.selected = session.SUBTITLE.segments[1]
widget.update()
check('the selected state\'s own frame', (pixel(450, 100), pixel(200, 100)), ('#ffffffff', '#ff00ff00'))
session.SUBTITLE.selected = None


def press(x, y=100):
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
    grabbed = ('start' if widget.subtitle_start_is_clicked else 'end' if widget.subtitle_end_is_clicked
               else 'body' if widget.subtitle_is_clicked else None, session.SUBTITLE.selected)
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
    session.SUBTITLE.selected = None
    return grabbed


first = session.SUBTITLE.segments[0]
check('a press in the start region grabs the start', press(105), ('start', first))
check('in the end region, the end', press(295), ('end', first))
check('in the body region, the cue', press(200, 60), ('body', first))
check('outside the regions: nothing grabbed, nothing selected', press(200, 120), (None, None))
apply(look(frame_svg=good_name))
check('a frame with no regions: the grab zone', (press(105), press(200, 120)), (('start', first), ('body', first)))

print('sliced parts keep their corners')
ROUND = '<g{}><rect width="100" height="40" rx="10" fill="#00ff00"/></g>'
(shapes / 'round.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">{ROUND.format("")}</svg>')
(shapes / 'round_sliced.svg').write_text(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">{ROUND.format(" data-slice=\"10\"")}</svg>')
round_name = timeline_svg.import_file(shapes / 'round.svg')
sliced_name = timeline_svg.import_file(shapes / 'round_sliced.svg')
check('data-slice read as four insets', timeline_frames.get(sliced_name).parts[0].slice, (10.0, 10.0, 10.0, 10.0))
check('one, two or four numbers, as CSS', [timeline_frames._insets(v) for v in ('4', '4 8', '1 2 3 4', 'x', '')],
      [(4.0,) * 4, (4.0, 8.0, 4.0, 8.0), (1.0, 2.0, 3.0, 4.0), None, None])
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'}],
                             'selected': None, 'position': 15.0})
apply(look(frame_svg=round_name))
check('stretched whole: a corner three times as tall as it was drawn', pixel(102, 70) == GREEN, False)
apply(look(frame_svg=sliced_name))
check('sliced: the corner as drawn, the edge straight past it', (pixel(102, 70), pixel(101, 56) == GREEN), (GREEN, False))
check('the middle filled', pixel(200, 115), GREEN)
timeline_style.reset()

print('frames only where the timeline is painted')
from PySide6.QtCore import QRect
session.SUBTITLE = Subtitle({'segments': [{'start': i * 0.5, 'end': i * 0.5 + 0.4, 'text': '', 'speaker': 'A'}
                                          for i in range(16)], 'selected': None, 'position': 15.0})
apply(look(frame_svg=frame_name))
framed = []
original_paint = timeline_frames.paint
timeline_frames.paint = lambda painter, frame, rect, *rest, **named: (framed.append(rect.left()), original_paint(painter, frame, rect, *rest, **named))
widget.repaint()
every = len(framed)
framed.clear()
widget.repaint(QRect(290, 0, 30, height))      # a playback strip, across one box
timeline_frames.paint = original_paint
check('a strip repainted: the frames of the boxes in it alone', (every, framed), (16, [300.0]))
timeline_style.reset()

print('frames, timed')
import time
session.SUBTITLE = Subtitle({'segments': [{'start': i * 0.08, 'end': i * 0.08 + 0.06, 'text': '', 'speaker': 'A'}
                                          for i in range(100)], 'selected': None, 'position': 15.0})


def full_repaint_ms(new_look, rounds=20):
    apply(new_look)
    widget.repaint()
    started = time.perf_counter()
    for _ in range(rounds):
        widget.repaint()
    return (time.perf_counter() - started) / rounds * 1000


plain_ms = full_repaint_ms(look())
framed_ms = full_repaint_ms(look(frame_svg=frame_name))
print(f'  100 boxes in view, a full repaint: {plain_ms:.1f} ms plain, {framed_ms:.1f} ms with a frame')
check('a frame keeps a full repaint of 100 boxes under 16 ms', framed_ms < 16, True)
timeline_style.reset()

print('every other part, as SVG')
import numpy as np


def svg_file(name, body, view='0 0 10 10'):
    (shapes / name).write_bytes(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{body}</svg>'.encode())
    return timeline_svg.import_file(shapes / name)


TILE = svg_file('tile.svg', '<rect width="10" height="10" fill="#ff0000"/><rect x="10" width="10" height="10" fill="currentColor"/>',
                '0 0 20 10')
SOLID = svg_file('solid.svg', '<rect width="10" height="10" fill="currentColor"/>')
NARROW = svg_file('narrow.svg', '<rect width="4" height="10" fill="currentColor"/>', '0 0 4 10')
LOCK = svg_file('lock.svg', '<rect width="10" height="10" fill="#ff8800"/>')
SPEAKER = svg_file('speaker.svg', '<g data-x="stretch"><rect width="100" height="40" fill="currentColor"/></g>'
                   '<g data-x="stretch" data-y="top"><rect width="100" height="6" fill="speakerColor"/></g>', '0 0 100 40')
session.SUBTITLE = Subtitle({'segments': [{'start': 1.0, 'end': 3.0, 'text': '', 'speaker': 'A'},
                                          {'start': 3.5, 'end': 6.0, 'text': '', 'speaker': 'A'}],
                             'selected': None, 'position': 6.0})

apply(look({'background_svg': TILE, 'background_color': '#ff0000ff'}))
check('a background tile, repeated, its currentColor the background colour', (pixel(705, 5), pixel(715, 5), pixel(725, 5)),
      ('#ffff0000', '#ff0000ff', '#ffff0000'))
apply(look({'show_ruler': True, 'ruler_background_svg': TILE, 'ruler_background_color': '#ff00ff00'}))
check('the ruler\'s band tiled', (pixel(705, 10), pixel(715, 10)), ('#ffff0000', '#ff00ff00'))

session.CONFIG['timeline'].update({'show_grid': True, 'grid_type': 'seconds'})
apply(look({'grid_line_svg': NARROW, 'grid_color': '#ff00ffff'}))
check('grid lines of a shape, centred on their second', (pixel(699, 5), pixel(701, 5), pixel(704, 5) == '#ff00ffff'),
      ('#ff00ffff', '#ff00ffff', False))
session.CONFIG['timeline'].update({'show_grid': False})

apply(look({'ruler_ticks': True, 'ruler_tick_svg': SOLID, 'ruler_tick_length': 10, 'ruler_tick_color': '#ffffff00',
            'ruler_offset': 20, 'ruler_subdivisions': 0}))
check('ticks of a shape, as long as a tick', (pixel(98, 25), pixel(98, 33) == '#ffffff00'), ('#ffffff00', False))

session.CONFIG['playback_speed'] = 1.5
apply(look({'cursor_badge_shape': 'custom', 'cursor_badge_svg': frame_name, 'cursor_show_time': True,
            'cursor_time_svg': frame_name}))
check('the badge a frame: its caps at its sides', (pixel(560, 36), pixel(597, 40)), ('#ffff0000', '#ff0000ff'))
check('the playhead\'s time in a frame', pixel(604, 40), '#ffff0000')
session.CONFIG['playback_speed'] = 1.0

session.CONFIG['timeline']['scrolling'] = 'none'
session.SUBTITLE.position = 15.0
apply(look({'offscreen_arrow_svg': SOLID, 'offscreen_arrow_color': '#ff00ff00'}))
check('the arrow to a playhead off the right, a shape', pixel(792, 33), '#ff00ff00')
session.SUBTITLE.position = 6.0
session.CONFIG['timeline']['scrolling'] = 'page'

widget.background_onsets = np.array([2.0])
session.CONFIG['timeline']['show_onset_markers'] = True
apply(look({'onset_marker_svg': NARROW, 'onset_marker_color': '#ff00ff00'}))
check('onset markers of a shape, from their time', (pixel(201, 190), pixel(206, 190) == '#ff00ff00'), ('#ff00ff00', False))
session.CONFIG['timeline']['show_onset_markers'] = False
widget.background_onsets = None

widget.show_tug_of_war = 3.0
apply(look({'push_handle_svg': NARROW, 'selected_subtitle_arrow_color': '#ff00ffff'}))
check('the push handle a shape, on the meeting', pixel(299, 100), '#ff00ffff')
widget.show_tug_of_war = False

session.SUBTITLE.segments[0]['locked'] = True
apply(look(lock_svg=LOCK))
check('a padlock of a shape at a locked box\'s top right', pixel(290, 63), '#ffff8800')
session.SUBTITLE.segments[0]['locked'] = False

widget.show_speaker_color = True
apply(look(frame_svg=SPEAKER, fill_color='#ff123456'))
check('speakerColor: the speaker\'s colour where the frame puts it, no strip of the timeline\'s',
      (pixel(200, 58), pixel(200, 100)), ('#ffff2e93', '#ff123456'))
widget.show_speaker_color = False
widget.update()
check('speaker colours off: the fill there', pixel(200, 58), '#ff123456')
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
widget.show_speaker_color = True
apply(look(frame_svg=SPEAKER, frame_selected_svg=frame_name, fill_color='#ff123456'))
check('a state whose frame has no speakerColor, when another\'s has: no strip either', pixel(200, 56) != '#ffff2e93', True)
session.SUBTITLE.selected = None
apply(look(frame_svg=frame_name, fill_color='#ff123456'))
check('frames without it in any state: the timeline\'s strip over them', pixel(200, 56), '#ffff2e93')
widget.show_speaker_color = False
timeline_style.reset()

print('themes: one JSON file, its SVGs in it')
import copy
from subtitld.modules import timeline_themes
themes = timeline_themes.available(reload=True)
check('Classic, Subtitld\'s own', ([t for t in themes], themes['subtitld.classic'].builtin, timeline_themes.problems), (['subtitld.classic'], True, {}))
classic_file = json.loads(themes['subtitld.classic'].path.read_text())


def verdict(change):
    theme = copy.deepcopy(classic_file)
    change(theme)
    try:
        timeline_themes.parse(json.dumps(theme).encode())
        return 'taken'
    except timeline_themes.ThemeError as error:
        return str(error).split(':')[0]


check('refused: another format, a bad id, no name, a shape refused, a shape not carried',
      [verdict(lambda t: t.update(format=2)), verdict(lambda t: t.update(id='Bad Id')), verdict(lambda t: t.update(name=' ')),
       verdict(lambda t: t['shapes'].update(box='<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')),
       verdict(lambda t: t['shapes'].pop('padlock'))],
      ['format 2', 'id \'Bad Id\'', 'no name, or a name, author, version or description that is not text', 'shape "box"',
       'names shapes it does not carry'])
not_themes = []
for data in (b'not json', b'[]', b'{}'):
    try:
        timeline_themes.parse(data)
        not_themes.append('taken')
    except timeline_themes.ThemeError:
        not_themes.append('refused')
check('and what is not a theme at all', not_themes, ['refused'] * 3)
check('a setting it does not know, or cannot hold: left out, the theme taken',
      verdict(lambda t: t['timeline'].update(sparkles=True, cursor_width='wide')), 'taken')

timeline_style.reset()
timeline_themes.forget_current()
check('no theme: the look its own', (timeline_themes.current(), timeline_themes.changed()), (None, True))
timeline_themes.apply('subtitld.classic')
check('Classic applied: its settings, its shapes kept', (timeline_themes.current().id, timeline_themes.changed(),
      bool(timeline_svg.read(timeline_style.values('subtitle')['frame_svg']))), ('subtitld.classic', False, True))
timeline_style.set_value('gap', 4, 'subtitle')
check('a setting changed after: still Classic, changed', (timeline_themes.current().id, timeline_themes.changed()),
      ('subtitld.classic', True))

exported = Path(tempfile.mkdtemp()) / 'mine.json'
written = timeline_themes.export(exported, 'My Theme', author='Me')
saved = json.loads(exported.read_text())
check('exported: one file, the shapes in it by name', (saved['id'], saved['author'], saved['boxes']['subtitle']['frame_svg'],
      saved['shapes'][saved['boxes']['subtitle']['frame_svg']].startswith('<svg')), ('user.my-theme', 'Me', 'subtitle_frame', True))
before = timeline_style.changes()
timeline_style.reset()
imported = timeline_themes.import_file(exported)
timeline_themes.apply(imported.id)
check('imported back: the same look, kept among the user\'s', (timeline_style.changes() == before, imported.builtin,
      sorted(timeline_themes.available())), (True, False, ['subtitld.classic', 'user.my-theme']))
copy_of_classic = Path(tempfile.mkdtemp()) / 'classic.json'
copy_of_classic.write_text(themes['subtitld.classic'].path.read_text())
try:
    timeline_themes.import_file(copy_of_classic)
    check('a theme taking Subtitld\'s own id refused', False, True)
except timeline_themes.ThemeError:
    check('a theme taking Subtitld\'s own id refused', True, True)

print('the panel\'s theme')
timeline_style.reset()
timeline_themes.forget_current()
panel = TimelineStylePanel()
check('no theme: Custom, then the themes', [panel.theme_picker.itemText(i) for i in range(panel.theme_picker.count())],
      ['Custom', 'Classic', 'My Theme'])
panel.theme_picker.activated.emit(panel.theme_picker.findData('subtitld.classic'))
check('picked: set, shown, not changed', (timeline_themes.current().id, panel.theme_picker.currentText(),
      panel.theme_changed_label.text(), panel.theme_picker.findData(None)), ('subtitld.classic', 'Classic', '', -1))
control('corner_radius').setValue(9)
check('a setting changed: said', panel.theme_changed_label.text(), '(changed)')
check('a file that is no theme: refused, and why', (panel.import_theme(shapes / 'good.svg'), panel.theme_problem.isHidden()),
      (None, False))
panel.reset()
check('reset: the defaults, no theme', (timeline_themes.current(), panel.theme_picker.currentText()), (None, 'Custom'))

print('Classic draws the timeline as Subtitld always has')
from PySide6.QtGui import QImage
session.CONFIG['timeline'].update({'show_grid': True, 'grid_type': 'seconds', 'show_onset_markers': True})
session.CONFIG['playback_speed'] = 1.5
session.SPEAKERS = {'A': {'color': '#ff2e93'}, 'B': {'color': '#2e93ff'}}
session.SUBTITLE = Subtitle({'segments': [
    {'start': 0.5, 'end': 3.0, 'text': 'Normal', 'speaker': 'A'}, {'start': 3.0, 'end': 4.5, 'text': 'Touching', 'speaker': 'B'},
    {'start': 5.2, 'end': 7.6, 'text': 'Selected', 'speaker': 'A'},
    {'start': 8.0, 'end': 8.6, 'text': 'Locked', 'speaker': 'A', 'locked': True}], 'selected': None, 'position': 6.4})
session.SUBTITLE.selected = session.SUBTITLE.segments[2]
widget.show_speaker_color = True
widget.background_onsets = np.array([1.2, 4.9, 7.9])
widget.show_tug_of_war = 3.0


def rendered():
    widget.apply_style()
    image = widget.grab().toImage().convertToFormat(QImage.Format_RGB32).copy(0, 0, 800, height)
    bits = image.constBits()
    return np.frombuffer(bits, np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)[:, :800, :3].astype(int)


timeline_style.reset()
original = rendered()
timeline_themes.apply('subtitld.classic')
difference = np.abs(original - rendered()).max(axis=2)
print(f'  {int((difference > 32).sum())} of {difference.size} pixels off by more than 32 levels, at most {int(difference.max())}')
check('the same to the eye: under 0.1% of pixels off by more than 32 levels, none by more than 80',
      ((difference > 32).sum() < difference.size / 1000, difference.max() <= 80), (True, True))
widget.show_speaker_color = False
widget.background_onsets = None
widget.show_tug_of_war = False
session.CONFIG['playback_speed'] = 1.0
session.CONFIG['timeline'].update({'show_grid': False, 'show_onset_markers': False})
timeline_style.reset()
timeline_themes.forget_current()

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
