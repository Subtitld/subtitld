"""The subtitle style panel: the picker, and each style's settings as its
manifest lists them, the control each type asks for, saved for that style
as they change.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QLabel, QLineEdit,
                               QPushButton, QSpinBox)
app = QApplication([])
from subtitld.modules import config, session, subtitle_styles as styles
from subtitld.interface import translation
from subtitld.interface.subtitle_style_panel import SubtitleStylePanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = session.PATH_SUBTITLD_USER_DATA / 'subtitle_styles' / 'neon'
folder.mkdir(parents=True)
(folder / 'style.html').write_text('<!DOCTYPE html>')
(folder / 'style.json').write_text(json.dumps({'id': 'test.neon', 'name': 'Neon', 'fields': [
    {'key': 'glow', 'type': 'number', 'label': 'Glow', 'default': 1.5, 'min': 0, 'max': 4, 'step': 0.25, 'unit': 'em'},
    {'key': 'motion', 'type': 'select', 'label': 'Motion', 'default': 'pulse',
     'options': [{'value': 'none', 'label': 'Still'}, {'value': 'pulse', 'label': 'Pulse'}]},
    {'key': 'caps', 'type': 'bool', 'label': 'All caps', 'default': False},
    {'key': 'tag', 'type': 'text', 'label': 'Tag', 'default': 'LIVE'},
    {'key': 'outline', 'type': 'bool', 'label': 'Outline', 'default': True},
    {'key': 'outline_width', 'type': 'int', 'label': 'Width', 'default': 3, 'min': 1, 'max': 9, 'unit': 'px',
     'visible_if': 'outline'},
]}))
styles.available(reload=True)
changes = []
panel = SubtitleStylePanel()
panel.changed.connect(lambda: changes.append(1))


def control(key):
    return panel.controls[key][1]


def labels():
    return [label.text() for label in panel.fields_widget.findChildren(QLabel) if label.property('class') == 'widget_label']


print('Classic, as its manifest lists it')
check('the picker: every style, the current one picked',
      ([panel.picker.itemData(i) for i in range(panel.picker.count())], panel.picker.currentData()),
      (['subtitld.classic', 'test.neon'], 'subtitld.classic'))
check('a control for each field', sorted(panel.controls), sorted(f['key'] for f in styles.get().fields))
check('the fonts: a list', type(control('font_family')).__name__, 'QComboBox')
check('the size: whole numbers in its range', (type(control('font_size')).__name__, control('font_size').minimum(),
                                               control('font_size').maximum(), control('font_size').value()),
      ('QSpinBox', 1, 999, 40))
check('a colour: a button', type(control('color')).__name__, 'QPushButton')
groups = panel.fields_widget.findChildren(QGroupBox)
check('groups: the loose fields, then one a switch turns on and off',
      [(g.title(), g.isCheckable()) for g in groups], [('', False), ('Shadow', True), ('Background box', True)])
check('a switch is its group', control('shadow_enabled') is groups[1], True)
check('the switch\'s fields inside its group', control('shadow_x').parentWidget() is groups[1], True)
check('labels from the locale files', labels()[:3], ['Font family', 'Size', 'Color'])

print('changes are saved for the style')
control('font_size').setValue(52)
check('a number', (styles.values()['font_size'], len(changes)), (52, 1))
groups[1].setChecked(False)
check('a switch', styles.values()['shadow_enabled'], False)
check('its fields off with it', control('shadow_x').isEnabled(), False)
index = control('font_family').findText(control('font_family').itemText(0))
control('font_family').activated.emit(index)
check('a font', styles.values()['font_family'], control('font_family').itemText(0))

print('another style: its own fields')
panel.picker.activated.emit(panel.picker.findData('test.neon'))
check('picked and saved', (styles.get().id, session.CONFIG['subtitle_style']['current']), ('test.neon', 'test.neon'))
check('a change', len(changes) >= 4, True)
check('its fields now', sorted(panel.controls), ['caps', 'glow', 'motion', 'outline', 'outline_width', 'tag'])
glow = control('glow')
check('a number with decimals, a step and a unit',
      (type(glow).__name__, glow.singleStep(), glow.maximum(), glow.value()), ('QDoubleSpinBox', 0.25, 4.0, 1.5))
check('units as the manifest has them, px in words',
      sorted(l.text() for l in panel.fields_widget.findChildren(QLabel) if l.property('class') == 'units_label'),
      ['Pixels', 'em'])
motion = control('motion')
check('a select: its options\' labels', [motion.itemText(i) for i in range(motion.count())], ['Still', 'Pulse'])
check('on its value', motion.currentData(), 'pulse')
caps = control('caps')
check('a loose bool: a check box with its label', (type(caps).__name__, caps.text()), ('QCheckBox', 'All caps'))
check('labels from the manifest', labels(), ['Glow', 'Motion', 'Tag', 'Width'])
motion.activated.emit(0)
caps.setChecked(True)
control('tag').setText('ON AIR')
control('tag').editingFinished.emit()
glow.setValue(3.3)
check('each saved for this style', {k: styles.values()[k] for k in ('motion', 'caps', 'tag', 'glow')},
      {'motion': 'none', 'caps': True, 'tag': 'ON AIR', 'glow': 3.3})

print('back to Classic')
panel.picker.activated.emit(panel.picker.findData('subtitld.classic'))
check('what was set there is still set', (control('font_size').value(), control('shadow_enabled').isChecked()),
      (52, False))
panel.picker.activated.emit(panel.picker.findData('test.neon'))
check('and Neon\'s too', (control('motion').currentData(), control('glow').value()), ('none', 3.3))

print('in another language')
translation.set_language('pt_BR')
panel.build()
check('Classic\'s name translated', panel.picker.itemText(panel.picker.findData('subtitld.classic')), 'Clássico')
check('a style from elsewhere: its own name', panel.picker.itemText(panel.picker.findData('test.neon')), 'Neon')
check('the picker\'s label', panel.picker_label.text(), 'Estilo da legenda')

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
