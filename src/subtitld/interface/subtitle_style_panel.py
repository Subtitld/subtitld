"""The subtitle style picker and the current style's settings, built from
its manifest (modules.subtitle_styles).

Each field gets the control its type asks for: a spin box with its range,
step and unit for a number, a colour button, a font list, a list of the
options of a select, a check box for a bool, a line for text. A bool that
other fields are `visible_if` becomes a group that can be switched off,
those fields inside it; the other fields come first, in a group of their
own, in the manifest's order.

Labels come from the locale files when they have
`subtitle_styles.<style id>.<key>` (and `.name` for the style's own name,
`.<key>.<value>` for an option), from the manifest otherwise: a built-in
style is translated with the rest of Subtitld, one from elsewhere says
what it says.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from subtitld.interface.translation import _
from subtitld.modules import subtitle_styles

# Units the interface has words for; any other is shown as the manifest has it.
_UNITS = {'px': 'units.pixels'}


def _text(key, fallback):
    translated = _(key)
    return translated if translated and translated != key else fallback


def style_name(style):
    return _text(f'subtitle_styles.{style.id}.name', style.name)


def field_label(style, field):
    return _text(f'subtitle_styles.{style.id}.{field["key"]}', field.get('label') or field['key'])


def option_label(style, field, option):
    return _text(f'subtitle_styles.{style.id}.{field["key"]}.{option["value"]}', option.get('label') or str(option['value']))


def _column(label, control, unit=None):
    """A field: its label over its control (and unit)."""
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    if label is not None:
        title = QLabel(label)
        title.setProperty('class', 'widget_label')
        column.addWidget(title, 0, Qt.AlignLeft)
    line = QHBoxLayout()
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(5)
    line.addWidget(control, 0, Qt.AlignLeft)
    if unit:
        units = QLabel(_(_UNITS[unit]) if unit in _UNITS else unit)
        units.setProperty('class', 'units_label')
        line.addWidget(units, 0, Qt.AlignLeft)
    column.addLayout(line)
    return column


class SubtitleStylePanel(QWidget):
    """Pick the style; set its settings. `changed` after either."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(20)
        self.controls = {}          # key -> (field, control)
        self.fields_widget = None
        self.picker = QComboBox()
        self.picker.activated.connect(self._picked)
        self.picker_label = QLabel()
        self.picker_label.setProperty('class', 'widget_label')
        picker = QVBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(2)
        picker.addWidget(self.picker_label, 0, Qt.AlignLeft)
        picker.addWidget(self.picker, 0, Qt.AlignLeft)
        self.layout().addLayout(picker)
        self.build()

    # --- building ----------------------------------------------------------
    def build(self):
        """(Re)build the picker and the current style's fields, in the
        interface's language."""
        self.picker_label.setText(_('interface_panel.subtitle_style'))
        styles = subtitle_styles.available()
        current = subtitle_styles.get()
        self.picker.clear()
        for style in styles.values():
            self.picker.addItem(style_name(style), style.id)
        self.picker.setCurrentIndex(self.picker.findData(current.id))

        if self.fields_widget is not None:
            # Out of the window at once, not only when Qt gets to deleting it.
            self.layout().removeWidget(self.fields_widget)
            self.fields_widget.setParent(None)
            self.fields_widget.deleteLater()
        self.fields_widget = QWidget()
        self.fields_widget.setLayout(QVBoxLayout())
        self.fields_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fields_widget.layout().setSpacing(20)
        self.layout().addWidget(self.fields_widget)
        self.controls.clear()

        values = subtitle_styles.values(current.id)
        switches = {f.get('visible_if') for f in current.fields if f.get('visible_if')}
        loose = [f for f in current.fields if f['key'] not in switches and not f.get('visible_if')]
        if loose:
            self.fields_widget.layout().addWidget(self._group(current, None, loose, values))
        for field in current.fields:
            if field['key'] in switches:
                inside = [f for f in current.fields if f.get('visible_if') == field['key']]
                self.fields_widget.layout().addWidget(self._group(current, field, inside, values))

    def _group(self, style, switch, fields, values):
        group = QGroupBox(field_label(style, switch) if switch else '')
        group.setLayout(QHBoxLayout())
        group.layout().setContentsMargins(10, 10, 10, 10)
        group.layout().setSpacing(20)
        if switch is not None:
            group.setCheckable(True)
            group.setChecked(values[switch['key']])
            group.toggled.connect(lambda on, key=switch['key']: self._set(key, on))
            self.controls[switch['key']] = (switch, group)
        for field in fields:
            group.layout().addLayout(self._field(style, field, values[field['key']]))
        group.layout().addStretch()
        return group

    def _field(self, style, field, value):
        key, kind = field['key'], field['type']
        label = field_label(style, field)
        if kind in ('int', 'number'):
            control = QSpinBox() if kind == 'int' else QDoubleSpinBox()
            if kind == 'number':
                control.setDecimals(2)
            low, high = field.get('min', -999999), field.get('max', 999999)
            control.setRange(*((int(low), int(high)) if kind == 'int' else (low, high)))
            if field.get('step') is not None:
                control.setSingleStep(field['step'])
            control.setValue(value)
            control.valueChanged.connect(lambda number, key=key: self._set(key, number))
            self.controls[key] = (field, control)
            return _column(label, control, field.get('unit'))
        if kind == 'bool':
            control = QCheckBox(label)
            control.setChecked(value)
            control.toggled.connect(lambda on, key=key: self._set(key, on))
            self.controls[key] = (field, control)
            return _column(None, control)
        if kind == 'select':
            control = QComboBox()
            for option in field['options']:
                control.addItem(option_label(style, field, option), option['value'])
            control.setCurrentIndex(max(0, control.findData(value)))
            control.activated.connect(lambda index, key=key, control=control: self._set(key, control.itemData(index)))
        elif kind == 'font':
            control = QComboBox()
            control.addItems(QFontDatabase.families())
            if control.findText(value) < 0:
                control.addItem(value)          # a font this system has not: still the setting
            control.setCurrentText(value)
            control.activated.connect(lambda index, key=key, control=control: self._set(key, control.itemText(index)))
        elif kind == 'color':
            control = QPushButton()
            control.setProperty('class', 'color_pick_button')
            control.setFixedWidth(80)
            self._paint_color(control, value)
            control.clicked.connect(lambda checked=False, key=key, control=control: self._pick_color(key, control))
        else:
            control = QLineEdit(value)
            control.editingFinished.connect(lambda key=key, control=control: self._set(key, control.text()))
        self.controls[key] = (field, control)
        return _column(label, control, field.get('unit'))

    @staticmethod
    def _paint_color(button, value):
        button.setStyleSheet('QPushButton { background-color: ' + value + ' }')

    # --- changes -----------------------------------------------------------
    def _set(self, key, value):
        try:
            subtitle_styles.set_value(key, value)
        except (KeyError, ValueError):
            return
        self.changed.emit()

    def _pick_color(self, key, button):
        current = QColor(subtitle_styles.values()[key])
        color = QColorDialog.getColor(current, self, options=QColorDialog.ShowAlphaChannel)
        if color.isValid():
            value = subtitle_styles.set_value(key, color.name(QColor.HexArgb))
            self._paint_color(button, value)
            self.changed.emit()

    def _picked(self, index):
        style_id = self.picker.itemData(index)
        if style_id and style_id != subtitle_styles.get().id:
            subtitle_styles.set_current(style_id)
            self.build()
            self.changed.emit()
