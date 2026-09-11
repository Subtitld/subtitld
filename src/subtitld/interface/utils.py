from PySide6.QtWidgets import QVBoxLayout, QWidget, QComboBox, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QListWidgetItem, QSizePolicy
from PySide6.QtGui import QImage, QPixmap, QPainter, QBrush, QPen, QPainterPath, QColor
from PySide6.QtCore import QThread, Signal, Qt, QRect, QPoint, QSize


from subtitld.interface.translation import _


def _widget_outer_extent(widget):
    """Return (width, height) for the widget's slide off-screen origin.
    `widget.parent().width()` is the natural choice, but during a project
    load the splitter's layout pass for newly-shown panels hasn't run
    when animate_element is called — `parent().width()` returns 0 / a
    tiny sizeHint and the slide collapses to `start_pos == end_pos`.
    Walk up the parent chain until we find a widget with a meaningful
    geometry (top-level window is always sized). Fall back to a generous
    default so the slide is at least visible."""
    parent = widget.parentWidget()
    w = h = 0
    while parent is not None:
        pw, ph = parent.width(), parent.height()
        if pw > w:
            w = pw
        if ph > h:
            h = ph
        if pw > 100 and ph > 100:
            # Found a substantively-sized ancestor — far enough.
            return w, h
        parent = parent.parentWidget()
    # No ancestor had a real size yet. Use the application screen size
    # as a last resort so the slide is at least the screen's width.
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geom = screen.availableGeometry()
        return max(w, geom.width()), max(h, geom.height())
    return max(w, 1920), max(h, 1080)


def animate_element(animation, duration=1000, effect='fadein'):
    widget = animation.targetObject()
    animation.setDuration(duration)
    start_pos = None  # for slide effects, the off-screen origin we snap to NOW
    if effect.startswith('slide_'):
        original_position = widget.pos()
        outer_w, outer_h = _widget_outer_extent(widget)
    if effect == 'slide_from_left':
        start_pos = QPoint(-outer_w, 0)
        animation.setStartValue(start_pos)
        animation.setEndValue(original_position)
    elif effect == 'slide_from_right':
        start_pos = QPoint(outer_w, 0)
        animation.setStartValue(start_pos)
        animation.setEndValue(original_position)
    elif effect == 'slide_from_bottom':
        start_pos = QPoint(0, outer_h)
        animation.setStartValue(start_pos)
        animation.setEndValue(original_position)
    elif effect == 'slide_from_top':
        start_pos = QPoint(0, -outer_h)
        animation.setStartValue(start_pos)
        animation.setEndValue(original_position)
    elif effect == 'slide_to_bottom':
        animation.setStartValue(original_position)
        animation.setEndValue(QPoint(0, outer_h))
    elif effect == 'fadein':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 0)
        animation.setEndValue(1)
    elif effect == 'fadeout':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 1)
        animation.setEndValue(0)
    if start_pos is not None:
        # Snap the widget to start_pos synchronously so any paint between
        # this call and the animation's first tick reads the off-screen
        # origin rather than the laid-out final position. For widgets
        # NOT under a layout this is enough; for layout-managed widgets
        # (the production panels), `productionscreen.show` uses
        # `setUpdatesEnabled(False)` for ~80 ms around this call to
        # suppress paint while the layout pass overrides our move().
        widget.move(start_pos)
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
        self.setMinimumWidth(400)

        # Everything lives inside one frame so the dialog's background can be
        # a single gradient spanning title, content and buttons. A QDialog
        # will not paint a stylesheet background itself (verified: neither a
        # type nor an objectName selector reaches it, even with
        # WA_StyledBackground), and styling the three sections separately
        # would restart the gradient three times instead of running it once
        # top to bottom. The frame also carries the corner radius, which the
        # translucent window lets show through.
        self.frame = QWidget()
        self.frame.setObjectName('dialog_frame')
        self.frame.setAttribute(Qt.WA_StyledBackground, True)
        self.frame.setLayout(QVBoxLayout())
        # Vertical only. The 1px reserves the frame's top and bottom border
        # (QSS border-width does not inset a plain QWidget's contents rect),
        # but the sides stay at 0 so the title bar and footer run edge to
        # edge — inset horizontally, their artwork's rounded corners sat 1px
        # inside the dialog's own radius and the two corners fought.
        self.frame.layout().setContentsMargins(0, 1, 0, 1)
        self.frame.layout().setSpacing(0)
        self.layout().addWidget(self.frame)

        # The title bar is two widgets, each painting its own slice of
        # dialog_title.svg: the label is the tab itself (which carries the
        # rounded corner and the slant on its right), and the right-hand
        # widget takes the remaining width and holds the close button. They
        # are separate because the two need different border-image slices —
        # one shape cannot express both.
        self.title_line = QWidget()
        self.title_line.setObjectName('dialog_title')
        # Fixed 32px. The title bar otherwise absorbs the frame layout's spare
        # vertical space, which was invisible against a flat background but
        # stretches the tab artwork now the bar is drawn from an SVG.
        self.title_line.setFixedHeight(32)
        self.title_line.setLayout(QHBoxLayout())
        self.title_line.layout().setContentsMargins(0, 0, 0, 0)
        self.title_line.layout().setSpacing(0)

        self.title_line.label = QLabel(title)
        self.title_line.label.setObjectName('dialog_title_label')
        self.title_line.label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.title_line.layout().addWidget(self.title_line.label, 0)

        # Spans whatever the label leaves; the close button sits at its right.
        self.title_line.right = QWidget()
        self.title_line.right.setObjectName('dialog_title_right')
        self.title_line.right.setAttribute(Qt.WA_StyledBackground, True)
        # Expanding across, but NOT down: a plain QWidget defaults to
        # Preferred vertically and would soak up the frame layout's slack,
        # stretching the title bar to twice its height.
        self.title_line.right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.title_line.right.setLayout(QHBoxLayout())
        self.title_line.right.layout().setContentsMargins(0, 0, 0, 0)
        self.title_line.right.layout().setSpacing(0)
        self.title_line.right.layout().addStretch()
        self.title_line.layout().addWidget(self.title_line.right, 1)

        close_button = QPushButton()
        close_button.setFixedSize(QSize(32, 32))
        close_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        close_button.setObjectName('dialog_close_button')
        close_button.clicked.connect(lambda: self.reject())
        self.title_line.right.layout().addWidget(close_button)

        self.frame.layout().addWidget(self.title_line)

        # Enable mouse tracking for dragging functionality
        self.title_line.setMouseTracking(True)
        self.title_line.mousePressEvent = self.title_mouse_press_event
        self.title_line.mouseMoveEvent = self.title_mouse_move_event
        self.title_line.mouseReleaseEvent = self.title_mouse_release_event

        self.content = QWidget()
        self.content.setObjectName('dialog_content')
        self.content.setLayout(QVBoxLayout())
        self.content.layout().setContentsMargins(10, 10, 10, 10)
        # The content sits in a 1px-inset row so the frame's side border stays
        # visible beside it. The title bar and footer are deliberately NOT
        # inset — they run edge to edge so their tab artwork lands in the
        # dialog's corners — but a full-bleed content area would let an
        # opaque child paint over the border (ExportDialog's sidebar does
        # exactly that, since it zeroes the content margins).
        content_row = QWidget()
        content_row.setLayout(QHBoxLayout())
        content_row.layout().setContentsMargins(1, 0, 1, 0)
        content_row.layout().setSpacing(0)
        content_row.layout().addWidget(self.content)
        self.frame.layout().addWidget(content_row)

        # The bottom bar mirrors the title bar: a plain stretch on the left
        # carrying Cancel, and a tab on the right — dialog_bottom.svg is
        # dialog_title.svg rotated 180 degrees — holding the default button.
        # Exposed as `self.bottom_line` (and `bottom_left` / `bottom_right`)
        # because dialogs used to reach it through `accept_button.parent()`,
        # which silently pointed at the wrong widget the moment the buttons
        # moved into the tab.
        self.bottom_line = QWidget()
        self.bottom_line.setObjectName('dialog_bottom')
        # Hug the buttons. Like the title bar, this row otherwise soaks up the
        # frame layout's spare height and stretches the tab artwork. A size
        # policy rather than setFixedHeight, so a dialog that needs a taller
        # footer (find & replace asks for 44px in QSS) can still have one.
        self.bottom_line.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.bottom_line.setLayout(QHBoxLayout())
        self.bottom_line.layout().setContentsMargins(0, 0, 0, 0)
        self.bottom_line.layout().setSpacing(0)

        self.accept_button = QPushButton("OK")
        self.accept_button.setProperty('class', 'accept_button')

        self.reject_button = QPushButton("Cancel")
        self.reject_button.setProperty('class', 'reject_button')

        self.bottom_left = QWidget()
        self.bottom_left.setObjectName('dialog_bottom_left')
        self.bottom_left.setAttribute(Qt.WA_StyledBackground, True)
        self.bottom_left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.bottom_left.setLayout(QHBoxLayout())
        # No horizontal padding — Cancel starts at the bar's edge. The 5px
        # left cap in the artwork needs no reservation: the button is
        # transparent and its own 12px text padding already clears it.
        self.bottom_left.layout().setContentsMargins(0, 0, 0, 0)
        self.bottom_left.layout().setSpacing(0)
        self.bottom_left.layout().addWidget(self.reject_button)
        self.bottom_left.layout().addStretch()
        self.bottom_line.layout().addWidget(self.bottom_left, 1)

        # The tab. Extra actions go here via `add_bottom_button()`; it sizes
        # to whatever it holds, so several buttons simply widen the tab.
        self.bottom_right = QWidget()
        self.bottom_right.setObjectName('dialog_bottom_right')
        self.bottom_right.setAttribute(Qt.WA_StyledBackground, True)
        self.bottom_right.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.bottom_right.setLayout(QHBoxLayout())
        # 24px clears the diagonal, 5px the rounded right cap — otherwise the
        # buttons render underneath the artwork.
        self.bottom_right.layout().setContentsMargins(24, 0, 5, 0)
        self.bottom_right.layout().setSpacing(0)
        self.bottom_right.layout().addWidget(self.accept_button)
        self.bottom_line.layout().addWidget(self.bottom_right, 0)

        self.accept_button.clicked.connect(self.accept)
        self.reject_button.clicked.connect(self.reject)

        self.frame.layout().addWidget(self.bottom_line)

    # A plain message dialog gets roomier padding and wrapped text. Applied
    # centrally rather than at the ~17 call sites that build one, so future
    # dialogs get it for free and none can forget it.
    _TEXT_DIALOG_PADDING = (20, 16, 20, 16)
    _TEXT_DIALOG_MAX_WIDTH = 520

    def _prepare_text_content(self):
        """If the content is nothing but labels, treat this as a text dialog.

        Form dialogs (export, find & replace, the config editors) hold other
        widget types and are left exactly as they are.
        """
        layout = self.content.layout()
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        widgets = [w for w in widgets if w is not None]
        if not widgets or not all(isinstance(w, QLabel) for w in widgets):
            return
        layout.setContentsMargins(*self._TEXT_DIALOG_PADDING)
        for label in widgets:
            label.setWordWrap(True)
            # A wrapped label's height depends on the width it is given, and
            # the layout only asks when the policy says to — without this the
            # dialog keeps its one-line height and clips the wrapped text.
            policy = label.sizePolicy()
            policy.setHeightForWidth(True)
            label.setSizePolicy(policy)
        # Without a cap the dialog widens to fit the message on one line,
        # which is what word wrap is meant to prevent. Respect a width a
        # subclass chose for itself.
        if self.maximumWidth() >= 16777215:
            self.setMaximumWidth(self._TEXT_DIALOG_MAX_WIDTH)
        # Re-run the layout now the labels wrap, then grow to the height the
        # wrapped text actually needs.
        self.content.layout().activate()
        self.layout().activate()
        self.adjustSize()

    def showEvent(self, event):
        self._prepare_text_content()
        return super().showEvent(event)

    def add_bottom_button(self, button, before_default=True):
        """Add an extra action button to the bottom-right tab.

        `before_default` keeps the dialog's default action (OK / Export /
        ...) rightmost, which is where the eye expects it; pass False to
        place the new button after it instead.
        """
        layout = self.bottom_right.layout()
        if before_default:
            layout.insertWidget(layout.indexOf(self.accept_button), button)
        else:
            layout.addWidget(button)
        return button

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
    
    def addItem(widget, item):
        widget.combobox.addItem(item)

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