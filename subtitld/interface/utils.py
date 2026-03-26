from PySide6.QtWidgets import QVBoxLayout, QWidget, QComboBox, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QListWidgetItem, QSizePolicy
from PySide6.QtGui import QImage, QPixmap, QPainter, QBrush, QPen, QPainterPath, QColor
from PySide6.QtCore import QThread, Signal, Qt, QRect, QPoint, QSize


from subtitld.interface.translation import _


def animate_element(animation, duration=1000, effect='fadein'):
    widget = animation.targetObject()
    animation.setDuration(duration)
    if effect.startswith('slide_'):
        original_position = widget.pos()
    if effect == 'slide_from_left':
        animation.setStartValue(QPoint(-widget.parent().width(), 0))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_right':
        animation.setStartValue(QPoint(widget.parent().width(), 0))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_bottom':
        animation.setStartValue(QPoint(0, widget.parent().height()))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_top':
        animation.setStartValue(QPoint(0, -widget.parent().height()))
        animation.setEndValue(original_position)
    elif effect == 'slide_to_bottom':
        animation.setStartValue(original_position)
        animation.setEndValue(QPoint(0, widget.parent().height()))
    elif effect == 'fadein':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 0)
        animation.setEndValue(1)
    elif effect == 'fadeout':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 1)
        animation.setEndValue(0)
    animation.start()

from datetime import datetime, timedelta

def friendly_time(dt):
    """
    Returns a human-friendly string showing the time difference between `dt` and now,
    using `_()` for translatable strings.
    """
    now = datetime.now()
    
    # Convert timestamp to datetime
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt)
    
    delta = now - dt
    seconds = int(delta.total_seconds())
    
    if seconds < 0:  # Future
        seconds = abs(seconds)
        if seconds < 60:
            return _("in a few seconds") if seconds < 5 else _("in {seconds} seconds").format(seconds=seconds)
        elif seconds < 3600:
            minutes = seconds // 60
            return _("in {minutes} minute{plural}").format(minutes=minutes, plural='' if minutes == 1 else 's')
        elif seconds < 86400:
            hours = seconds // 3600
            return _("in {hours} hour{plural}").format(hours=hours, plural='' if hours == 1 else 's')
        elif seconds < 604800:
            days = seconds // 86400
            return _("in {days} day{plural}").format(days=days, plural='' if days == 1 else 's')
        elif seconds < 2419200:
            weeks = seconds // 604800
            return _("in {weeks} week{plural}").format(weeks=weeks, plural='' if weeks == 1 else 's')
        else:
            months = seconds // 2419200
            return _("in {months} month{plural}").format(months=months, plural='' if months == 1 else 's')
    else:  # Past
        if seconds < 60:
            return _("just now") if seconds < 5 else _("{seconds} seconds ago").format(seconds=seconds)
        elif seconds < 3600:
            minutes = seconds // 60
            return _("{minutes} minute{plural} ago").format(minutes=minutes, plural='' if minutes == 1 else 's')
        elif seconds < 86400:
            hours = seconds // 3600
            return _("{hours} hour{plural} ago").format(hours=hours, plural='' if hours == 1 else 's')
        elif seconds < 604800:
            days = seconds // 86400
            return _("{days} day{plural} ago").format(days=days, plural='' if days == 1 else 's')
        elif seconds < 2419200:
            weeks = seconds // 604800
            return _("{weeks} week{plural} ago").format(weeks=weeks, plural='' if weeks == 1 else 's')
        else:
            months = seconds // 2419200
            return _("{months} month{plural} ago").format(months=months, plural='' if months == 1 else 's')


class SimpleDialog(QDialog):
    def __init__(self, parent=None, title='', *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.title_line = QWidget()
        self.title_line.setObjectName('dialog_title')
        self.title_line.setLayout(QHBoxLayout())
        self.title_line.layout().setContentsMargins(10, 0, 0, 1)

        self.title_line.label = QLabel(title)
        self.title_line.label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        self.title_line.layout().addWidget(self.title_line.label)

        close_button = QPushButton()
        close_button.setFixedSize(QSize(32, 32))
        close_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        close_button.setObjectName('dialog_close_button')
        close_button.clicked.connect(lambda: self.reject())
        self.title_line.layout().addWidget(close_button)

        self.layout().addWidget(self.title_line)

        # Enable mouse tracking for dragging functionality
        self.title_line.setMouseTracking(True)
        self.title_line.mousePressEvent = self.title_mouse_press_event
        self.title_line.mouseMoveEvent = self.title_mouse_move_event
        self.title_line.mouseReleaseEvent = self.title_mouse_release_event

        self.content = QWidget()
        self.content.setObjectName('dialog_content')
        self.content.setLayout(QVBoxLayout())
        self.content.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(self.content)

        bottom_line = QWidget()
        bottom_line.setObjectName('dialog_bottom')
        bottom_line.setLayout(QHBoxLayout())
        bottom_line.layout().setContentsMargins(0, 1, 0, 0)
        bottom_line.layout().setSpacing(0)

        self.accept_button = QPushButton("OK")
        self.accept_button.setProperty('class', 'accept_button')

        self.reject_button = QPushButton("Cancel")
        self.reject_button.setProperty('class', 'reject_button')

        bottom_line.layout().addStretch()
        bottom_line.layout().addWidget(self.reject_button)
        bottom_line.layout().addWidget(self.accept_button)

        self.accept_button.clicked.connect(self.accept)
        self.reject_button.clicked.connect(self.reject)

        self.layout().addWidget(bottom_line)

    def title_mouse_press_event(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def title_mouse_move_event(self, event):
        if event.buttons() == Qt.LeftButton:
            if hasattr(self, 'drag_position'):
                new_pos = event.globalPos() - self.drag_position
                self.move(new_pos)
                event.accept()

    def title_mouse_release_event(self, event):
        if hasattr(self, 'drag_position'):
            delattr(self, 'drag_position')
    
    def set_title(self, title):
        self.title_line.label.setText(title)

    def accept(self):
        super().accept()

    
class LabeledComboBox(QWidget):
    activated = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setObjectName('labeled_combobox')
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.setAttribute(Qt.WA_StyledBackground)

        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(0)

        widget.label = QLabel()
        widget.layout().addWidget(widget.label, 1)

        widget.bottom_line = QHBoxLayout()
        widget.bottom_line.setContentsMargins(0, 0, 0, 0)
        widget.bottom_line.setSpacing(0)
        widget.layout().addLayout(widget.bottom_line)

        widget.combobox = QComboBox()
        widget.bottom_line.addWidget(widget.combobox, 1)

        widget.combobox.activated.connect(widget.activated)

    def clear(widget):
        widget.combobox.clear()
    
    def addItems(widget, items):
        widget.combobox.addItems(items)

    def setLabel(widget, label):
        widget.label.setText(label)

    def setCurrentText(widget, text):
        widget.combobox.setCurrentText(text)
    
    def currentText(widget):
        return widget.combobox.currentText()
    

class LabeledLabel(QWidget):
    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.setAttribute(Qt.WA_StyledBackground)

        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(0)

        widget.top_label = QLabel()
        widget.top_label.setObjectName('top_label')
        widget.layout().addWidget(widget.top_label, 1)

        widget.bottom_line = QHBoxLayout()
        widget.bottom_line.setContentsMargins(0, 0, 0, 0)
        widget.bottom_line.setSpacing(0)
        widget.layout().addLayout(widget.bottom_line)

        widget.label = QLabel()
        widget.label.setObjectName('label')
        widget.label.setWordWrap(True)
        widget.bottom_line.addWidget(widget.label, 1)
    
    def setText(widget, label):
        widget.label.setText(label)

    def setLabel(widget, text):
        widget.top_label.setText(text)
    

class LabeledLineEdit(QWidget):
    editingFinished = Signal(str)
    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setObjectName('labeled_lineedit')
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        widget.setAttribute(Qt.WA_StyledBackground)

        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)
        widget.layout().setSpacing(0)

        widget.label = QLabel()
        widget.label.setObjectName('top_label')
        widget.layout().addWidget(widget.label, 1)

        widget.bottom_line = QHBoxLayout()
        widget.bottom_line.setContentsMargins(0, 0, 0, 0)
        widget.bottom_line.setSpacing(0)
        widget.layout().addLayout(widget.bottom_line)

        widget.lineedit = QLineEdit()
        widget.lineedit.editingFinished.connect(lambda: widget.editing_finished())
        widget.bottom_line.addWidget(widget.lineedit, 1)

    def setLabel(widget, label):
        widget.label.setText(label)

    def setText(widget, text):
        widget.lineedit.setText(text)
    
    def text(widget):
        return widget.lineedit.text()
    
    def editing_finished(widget):
        widget.editingFinished.emit(widget.text())