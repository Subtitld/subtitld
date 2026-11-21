"""The timeline's look, a box of settings a group (modules.timeline_style).

The timeline's own settings first and last; between them, the boxes of a
kind of cue, picked above them once there is more than one kind (each
kind's boxes have a look of their own).

Each field gets the control subtitle styles' fields get. A group whose
first field is a bool the rest are `visible_if` is a group that can be
switched off; any other field with `visible_if` or `hidden_if` shows or
hides as its bool is set (`hidden_if` may be a [key, value] pair too, and
`visible_when` is one). A field naming a file (an SVG shape) gets a
button to pick one, which is checked and kept (modules.timeline_svg), and
one to take it off.

Labels come from the locale files: `timeline_style.<key>` (a box's
`timeline_style.box.<key>`), `timeline_style.<key>.<value>` for an
option, `timeline_style.group.<group>` for a group and
`timeline_style.kind.<kind>` for a kind of cue.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from subtitld.interface.subtitle_style_panel import _text, field_control
from subtitld.interface.translation import _
from subtitld.modules import timeline_style, timeline_svg

COLUMNS = 2
FONT_LIST_WIDTH = 180
# The box groups go after these of the timeline's own.
BOXES_AFTER = 'waveform'
TIMELINE, BOX = 'timeline', 'box'


def _humane(key):
    return key.replace('_', ' ').capitalize()


def _prefix(scope):
    return 'timeline_style.box.' if scope == BOX else 'timeline_style.'


def field_label(field, scope=TIMELINE):
    return _text(f'{_prefix(scope)}{field["key"]}', _humane(field['key']))


def option_label(field, option, scope=TIMELINE):
    return _text(f'{_prefix(scope)}{field["key"]}.{option["value"]}', option.get('label') or str(option['value']))


def group_label(group):
    return _text(f'timeline_style.group.{group}', _humane(group))


def kind_label(kind):
    return _text(f'timeline_style.kind.{kind}', _humane(kind))


class TimelineStylePanel(QWidget):
    """The timeline's settings; `changed` after each."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(20)
        self.kind = timeline_style.SUBTITLE
        self.controls = {}          # (scope, key) -> (field, control)
        self.cells = {}             # (scope, key) -> the widget holding its column
        self.grids = []             # (grid, scope, its fields' keys in order)
        self.fields_widget = None

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.reset_button = QPushButton()
        self.reset_button.setObjectName('timeline_style_reset_button')
        self.reset_button.clicked.connect(self.reset)
        actions.addWidget(self.reset_button, 0, Qt.AlignLeft)
        actions.addStretch()
        self.layout().addLayout(actions)
        self.build()

    # --- building ----------------------------------------------------------
    def build(self):
        """(Re)build the groups on the settings as they are, in the
        interface's language."""
        self.reset_button.setText(_('timeline_style.reset'))
        if self.kind not in timeline_style.KINDS:
            self.kind = timeline_style.SUBTITLE
        if self.fields_widget is not None:
            self.layout().removeWidget(self.fields_widget)
            self.fields_widget.setParent(None)
            self.fields_widget.deleteLater()
        self.fields_widget = QWidget()
        self.fields_widget.setLayout(QVBoxLayout())
        self.fields_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fields_widget.layout().setSpacing(20)
        self.layout().addWidget(self.fields_widget)
        self.controls.clear()
        self.cells.clear()
        self.grids.clear()

        layout = self.fields_widget.layout()
        values = timeline_style.values()
        for group in timeline_style.TIMELINE_GROUPS:
            fields = [f for f in timeline_style.TIMELINE_FIELDS if f['group'] == group]
            if fields:
                layout.addWidget(self._group(TIMELINE, group, fields, values))
            if group == BOXES_AFTER:
                self._kind_picker(layout)
                box_values = timeline_style.values(self.kind)
                for box_group in timeline_style.BOX_GROUPS:
                    fields = [f for f in timeline_style.BOX_FIELDS if f['group'] == box_group]
                    layout.addWidget(self._group(BOX, box_group, fields, box_values))
        self._show_dependents()

    def _kind_picker(self, layout):
        """Which kind of cue's boxes the box groups set: shown once there
        is more than one."""
        self.kind_label = QLabel(_('timeline_style.kind'))
        self.kind_label.setProperty('class', 'widget_label')
        self.kind_picker = QComboBox()
        self.kind_picker.setObjectName('timeline_style_kind_picker')
        for kind in timeline_style.KINDS:
            self.kind_picker.addItem(kind_label(kind), kind)
        self.kind_picker.setCurrentIndex(self.kind_picker.findData(self.kind))
        self.kind_picker.activated.connect(self._kind_picked)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(self.kind_label, 0, Qt.AlignLeft)
        column.addWidget(self.kind_picker, 0, Qt.AlignLeft)
        holder = QWidget()
        holder.setLayout(column)
        holder.setVisible(len(timeline_style.KINDS) > 1)
        self.kind_holder = holder
        layout.addWidget(holder)

    def _group(self, scope, group, fields, values):
        box = QGroupBox(group_label(group))
        box.setObjectName(f'timeline_style_group_{group}')
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        first = fields[0]
        if first['type'] == 'bool' and len(fields) > 1 and all(f.get('visible_if') == first['key'] for f in fields[1:]):
            box.setTitle(field_label(first, scope))
            box.setCheckable(True)
            box.setChecked(values[first['key']])
            box.toggled.connect(lambda on, key=first['key']: self._set(scope, key, on))
            self.controls[(scope, first['key'])] = (first, box)
            fields = fields[1:]
        for field in fields:
            if field.get('file'):
                column, control = self._file_field(scope, field, values[field['key']])
            else:
                column, control = field_control(field, values[field['key']], field_label(field, scope),
                                                lambda option, field=field: option_label(field, option, scope),
                                                lambda key, value: self._set(scope, key, value), self)
            self.controls[(scope, field['key'])] = (field, control)
            if field['type'] == 'font':
                control.setMaximumWidth(FONT_LIST_WIDTH)
            cell = QWidget()
            cell.setLayout(column)
            self.cells[(scope, field['key'])] = cell
        grid.setColumnStretch(COLUMNS, 1)
        self.grids.append((grid, scope, [f['key'] for f in fields]))
        return box

    def _file_field(self, scope, field, value):
        """A shape's file: its name, a button to pick one, one to take it off,
        and why a file picked was refused."""
        key = field['key']
        title = QLabel(field_label(field, scope))
        title.setProperty('class', 'widget_label')
        choose = QPushButton(_('timeline_style.choose_svg'))
        choose.setAccessibleName(f"{field_label(field, scope)}: {_('timeline_style.choose_svg')}")
        clear = QPushButton(_('timeline_style.no_svg'))
        name = QLabel()
        name.setProperty('class', 'units_label')
        problem = QLabel()
        problem.setProperty('class', 'units_label')
        problem.setWordWrap(True)
        problem.hide()
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(5)
        line.addWidget(choose, 0, Qt.AlignLeft)
        line.addWidget(clear, 0, Qt.AlignLeft)
        line.addWidget(name, 0, Qt.AlignLeft)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(title, 0, Qt.AlignLeft)
        column.addLayout(line)
        column.addWidget(problem)

        def show(current):
            name.setText(current if current else _('timeline_style.no_svg_set'))
            clear.setEnabled(bool(current))

        def pick():
            path, _filter = QFileDialog.getOpenFileName(self, field_label(field, scope), '', 'SVG (*.svg)')
            if path:
                self.import_shape(scope, key, path)

        def take_off():
            self._set(scope, key, '')
            show('')

        choose.clicked.connect(pick)
        clear.clicked.connect(take_off)
        choose.shape_name, choose.shape_problem, choose.show_shape = name, problem, show
        show(value)
        return column, choose

    def import_shape(self, scope, key, path):
        """Check and keep the SVG at `path` as the shape of `key`; say why
        not when it is refused. The name it is kept by, or None."""
        control = self.controls[(scope, key)][1]
        try:
            name = timeline_svg.import_file(path)
        except (OSError, timeline_svg.ShapeError) as error:
            control.shape_problem.setText(_('timeline_style.svg_refused').format(reason=str(error)))
            control.shape_problem.show()
            return None
        control.shape_problem.hide()
        self._set(scope, key, name)
        control.show_shape(name)
        return name

    def _shown(self, scope, field, values):
        """Whether a field shows: while its `visible_if` is on (unless that
        is its group's switch, which turns the group off instead) and its
        `hidden_if` off (or, a [key, value] pair, while that setting is
        not that value)."""
        if (scope, field.get('visible_if')) in self.cells and not values[field['visible_if']]:
            return False
        visible_when = field.get('visible_when')
        if visible_when and values[visible_when[0]] != visible_when[1]:
            return False
        hidden_if = field.get('hidden_if')
        if isinstance(hidden_if, list):
            return values[hidden_if[0]] != hidden_if[1]
        return not (hidden_if and values[hidden_if])

    def _show_dependents(self):
        """Each group's fields that show, in its grid in order: no holes
        where one is hidden."""
        values = {TIMELINE: timeline_style.values(), BOX: timeline_style.values(self.kind)}
        fields = {TIMELINE: timeline_style.TIMELINE_FIELDS_BY_KEY, BOX: timeline_style.BOX_FIELDS_BY_KEY}
        for grid, scope, keys in self.grids:
            for key in keys:
                grid.removeWidget(self.cells[(scope, key)])
            index = 0
            for key in keys:
                cell = self.cells[(scope, key)]
                shown = self._shown(scope, fields[scope][key], values[scope])
                cell.setVisible(shown)
                if shown:
                    grid.addWidget(cell, index // COLUMNS, index % COLUMNS, Qt.AlignLeft | Qt.AlignTop)
                    index += 1

    # --- changes -----------------------------------------------------------
    def _set(self, scope, key, value):
        try:
            value = timeline_style.set_value(key, value, self.kind if scope == BOX else None)
        except (KeyError, ValueError):
            return None
        self._show_dependents()
        self.changed.emit()
        return value

    def _kind_picked(self, index):
        kind = self.kind_picker.itemData(index)
        if kind and kind != self.kind:
            self.kind = kind
            self.build()

    def reset(self):
        timeline_style.reset()
        self.build()
        self.changed.emit()
