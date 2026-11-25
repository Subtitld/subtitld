import html
import re

from PySide6.QtWidgets import QVBoxLayout, QWidget, QComboBox, QDoubleSpinBox, QHBoxLayout, QDialog, QPushButton, QLabel, QLineEdit, QListWidgetItem, QSizePolicy, QAbstractButton, QStyledItemDelegate
from PySide6.QtGui import QImage, QPixmap, QPainter, QBrush, QPen, QPainterPath, QColor, QFont
from PySide6.QtCore import QThread, Signal, Qt, QRect, QRectF, QPoint, QPointF, QSize, QObject, QEvent, QPropertyAnimation, QTimer


from subtitld.interface import theme
from subtitld.interface import shapes
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


def name_icon_buttons(root):
    """A button that shows only an icon has no text for a screen reader to
    read out: name each one after its tooltip, without the tooltip's markup.
    Run again after the tooltips change (a new interface language)."""
    for button in root.findChildren(QAbstractButton):
        if button.text().strip():
            continue
        tip = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', button.toolTip())).split())
        if tip:
            button.setAccessibleName(tip)


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
        # origin rather than the laid-out final position.
        widget.move(start_pos)
        # A layout-managed widget is put back at its final position by the
        # next layout pass (the window being shown, a panel becoming
        # visible), and the animation only moves it again on its first
        # tick, which waits for the event loop: any startup work after
        # this call left it painted in place first. So nothing paints
        # until that tick has moved it, however long that takes.
        widget.setUpdatesEnabled(False)
    previous = getattr(animation, '_reveal_on_first_tick', None)
    if previous is not None:
        animation.valueChanged.disconnect(previous)
        animation.stateChanged.disconnect(previous)
        animation._reveal_on_first_tick = None
    animation.start()
    if start_pos is not None:
        def _reveal(*_):
            animation.valueChanged.disconnect(_reveal)
            animation.stateChanged.disconnect(_reveal)
            animation._reveal_on_first_tick = None
            widget.setUpdatesEnabled(True)
        # Connected after start(), whose own t=0 value change would fire it.
        # A stop before the first tick (hidden again at once) reveals too.
        animation.valueChanged.connect(_reveal)
        animation.stateChanged.connect(_reveal)
        animation._reveal_on_first_tick = _reveal
        # Layout passes during the slide (a panel filling in, the splitter
        # settling) put the widget back at its final position too, and it
        # would paint there until the next tick: put it back on the slide.
        if not hasattr(animation, '_slide_guard'):
            animation._slide_guard = _SlideGuard(animation)
            widget.installEventFilter(animation._slide_guard)


class _SlideGuard(QObject):
    """Keeps a sliding widget where its animation has it while it runs."""

    def __init__(self, animation):
        super().__init__(animation)
        self._animation = animation

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Move and self._animation.state() == QPropertyAnimation.Running and self._animation.propertyName() == b'pos':
            value = self._animation.currentValue()
            if value is not None and watched.pos() != value:
                watched.move(value)
        return False

from datetime import datetime, timedelta

def _counted(count, one, many):
    """`one` for a count of 1, else `many`, with {count} filled in. The two
    are separate texts, so each language words its own singular and plural
    rather than English's added "s"."""
    return (one if count == 1 else many).format(count=count)


def friendly_time(dt):
    """How long ago `dt` was, or how long until it, in words: "just now",
    "3 minutes ago", "in 1 week". `dt` is a datetime or a timestamp."""
    now = datetime.now()
    
    # Convert timestamp to datetime
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt)
    
    delta = now - dt
    seconds = int(delta.total_seconds())
    
    if seconds < 0:  # Future
        seconds = abs(seconds)
        if seconds < 60:
            return _('friendly_time.in_a_few_seconds') if seconds < 5 else _('friendly_time.in_seconds').format(count=seconds)
        elif seconds < 3600:
            return _counted(seconds // 60, _('friendly_time.in_minutes_one'), _('friendly_time.in_minutes_many'))
        elif seconds < 86400:
            return _counted(seconds // 3600, _('friendly_time.in_hours_one'), _('friendly_time.in_hours_many'))
        elif seconds < 604800:
            return _counted(seconds // 86400, _('friendly_time.in_days_one'), _('friendly_time.in_days_many'))
        elif seconds < 2419200:
            return _counted(seconds // 604800, _('friendly_time.in_weeks_one'), _('friendly_time.in_weeks_many'))
        else:
            return _counted(seconds // 2419200, _('friendly_time.in_months_one'), _('friendly_time.in_months_many'))
    else:  # Past
        if seconds < 60:
            return _('friendly_time.just_now') if seconds < 5 else _('friendly_time.seconds_ago').format(count=seconds)
        elif seconds < 3600:
            return _counted(seconds // 60, _('friendly_time.minutes_ago_one'), _('friendly_time.minutes_ago_many'))
        elif seconds < 86400:
            return _counted(seconds // 3600, _('friendly_time.hours_ago_one'), _('friendly_time.hours_ago_many'))
        elif seconds < 604800:
            return _counted(seconds // 86400, _('friendly_time.days_ago_one'), _('friendly_time.days_ago_many'))
        elif seconds < 2419200:
            return _counted(seconds // 604800, _('friendly_time.weeks_ago_one'), _('friendly_time.weeks_ago_many'))
        else:
            return _counted(seconds // 2419200, _('friendly_time.months_ago_one'), _('friendly_time.months_ago_many'))


class _HoverMirror(QObject):
    """Mirror one widget's hover state onto another as a `hover` property.

    The dialog close button sits inside a slanted notch. A hover fill on the
    button alone is a rectangle beside the slant, which reads as a box inside
    the notch; lighting up the notch itself needs the NOTCH to know the
    button is hovered. Using the notch's own :hover would also trigger over
    the empty part of its slant slice, where a click does nothing.
    """

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self._target = target

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Enter, QEvent.Leave):
            self._target.setProperty('hover', kind == QEvent.Enter)
            self._target.style().unpolish(self._target)
            self._target.style().polish(self._target)
            self._target.update()
        return False


class _ResizeEdge(QWidget):
    """A thin strip along one edge (or a square at one corner) of a frameless
    window, over its contents: dragging it resizes the window, through the
    window system where it can (Wayland), by hand otherwise."""
    THICKNESS = 4
    CORNER = 10
    CURSORS = {
        Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
        Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
        Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor, Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor, Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
    }

    def __init__(self, window, edges):
        super().__init__(window)
        self.edges = edges
        self.setCursor(self.CURSORS[edges])
        self._start = None

    def place(self):
        w, h = self.parentWidget().width(), self.parentWidget().height()
        t, c = self.THICKNESS, self.CORNER
        left, right = bool(self.edges & Qt.LeftEdge), bool(self.edges & Qt.RightEdge)
        top, bottom = bool(self.edges & Qt.TopEdge), bool(self.edges & Qt.BottomEdge)
        if (left or right) and (top or bottom):
            self.setGeometry(0 if left else w - c, 0 if top else h - c, c, c)
        elif left or right:
            self.setGeometry(0 if left else w - t, c, t, h - 2 * c)
        else:
            self.setGeometry(c, 0 if top else h - t, w - 2 * c, t)
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        handle = self.window().windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            return
        self._start = (event.globalPosition().toPoint(), self.window().geometry())

    def mouseMoveEvent(self, event):
        if self._start is None:
            return
        origin, geometry = self._start
        delta = event.globalPosition().toPoint() - origin
        rect = QRect(geometry)
        if self.edges & Qt.LeftEdge:
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - self.window().minimumWidth()))
        if self.edges & Qt.RightEdge:
            rect.setRight(rect.right() + delta.x())
        if self.edges & Qt.TopEdge:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - self.window().minimumHeight()))
        if self.edges & Qt.BottomEdge:
            rect.setBottom(rect.bottom() + delta.y())
        self.window().setGeometry(rect)

    def mouseReleaseEvent(self, event):
        self._start = None


def build_dialog_chrome(host, title, on_close):
    """A dialog's look on `host` (a widget with an empty vertical layout): the gradient frame, the title with the
    close notch, the content area and the footer with Cancel and the default button's tab. Sets on `host`:
    frame, title_line, content, bottom_line, bottom_left, bottom_right, accept_button, reject_button. SimpleDialog
    is built on it; a widget that looks like a dialog without being one (the start screen's link card) too."""
    # Everything lives inside one frame so the dialog's background can be
    # a single gradient spanning title, content and buttons. A QDialog
    # will not paint a stylesheet background itself (verified: neither a
    # type nor an objectName selector reaches it, even with
    # WA_StyledBackground), and styling the three sections separately
    # would restart the gradient three times instead of running it once
    # top to bottom. The frame also carries the corner radius, which the
    # translucent window lets show through.
    host.frame = QWidget()
    host.frame.setObjectName('dialog_frame')
    host.frame.setAttribute(Qt.WA_StyledBackground, True)
    host.frame.setLayout(QVBoxLayout())
    # Vertical only. The 1px reserves the frame's top and bottom border
    # (QSS border-width does not inset a plain QWidget's contents rect),
    # but the sides stay at 0 so the title bar and footer run edge to
    # edge — inset horizontally, their artwork's rounded corners sat 1px
    # inside the dialog's own radius and the two corners fought.
    host.frame.layout().setContentsMargins(0, 1, 0, 1)
    host.frame.layout().setSpacing(0)
    host.layout().addWidget(host.frame)

    # The title sits directly on the dialog gradient. Only the top-right
    # corner has chrome: a notch (dialog_title_close_background.svg, drawn
    # like the main window's version tab) behind the close button. Label, spacer and notch are separate widgets so the notch
    # can carry its own border-image slices without affecting the rest.
    host.title_line = QWidget()
    host.title_line.setObjectName('dialog_title')
    # Fixed 32px. The title bar otherwise absorbs the frame layout's spare
    # vertical space, which was invisible against a flat background but
    # stretches the tab artwork now the bar is drawn from an SVG.
    host.title_line.setFixedHeight(32)
    host.title_line.setLayout(QHBoxLayout())
    host.title_line.layout().setContentsMargins(0, 0, 0, 0)
    host.title_line.layout().setSpacing(0)

    host.title_line.label = QLabel(title)
    host.title_line.label.setObjectName('dialog_title_label')
    host.title_line.label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    host.title_line.layout().addWidget(host.title_line.label, 0)

    # Spans whatever the label leaves. Kept as its own widget (rather than
    # a bare stretch) so callers have somewhere to put extra title-bar
    # controls later, left of the notch.
    host.title_line.right = QWidget()
    host.title_line.right.setObjectName('dialog_title_right')
    # Expanding across, but NOT down: a plain QWidget defaults to
    # Preferred vertically and would soak up the frame layout's slack,
    # stretching the title bar to twice its height.
    host.title_line.right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    host.title_line.right.setLayout(QHBoxLayout())
    host.title_line.right.layout().setContentsMargins(0, 0, 0, 0)
    host.title_line.right.layout().setSpacing(0)
    host.title_line.right.layout().addStretch()
    host.title_line.layout().addWidget(host.title_line.right, 1)

    # The notch. Its artwork is sliced 0/1/1/36: the left 36px carry the
    # slanted edge at 1:1 and the rest stretches. QSS border widths do not
    # inset a plain widget's contents, so the layout margins place the
    # close button explicitly. It starts 17px in, inside the slant slice:
    # the slant runs down-right, and at the icon's height the notch body
    # already begins left of it, so the icon stays clear of the edge
    # while the notch stays narrow (17 + 30 + 1 = 48px). Only the icon is
    # drawn; the hover fill is the notch's own artwork, not the button.
    host.title_line.notch = QWidget()
    host.title_line.notch.setObjectName('dialog_title_notch')
    host.title_line.notch.setAttribute(Qt.WA_StyledBackground, True)
    host.title_line.notch.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    host.title_line.notch.setLayout(QHBoxLayout())
    host.title_line.notch.layout().setContentsMargins(17, 0, 1, 1)
    host.title_line.notch.layout().setSpacing(0)
    host.title_line.layout().addWidget(host.title_line.notch, 0)

    close_button = QPushButton()
    # 31 tall, not 32: the notch's bottom hairline takes the last row, and
    # a 32px button would paint over it on hover.
    close_button.setFixedSize(QSize(30, 31))
    close_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    close_button.setObjectName('dialog_close_button')
    close_button.clicked.connect(lambda: on_close())
    host.title_line.notch.layout().addWidget(close_button)
    host._close_hover = _HoverMirror(host.title_line.notch, close_button)
    close_button.installEventFilter(host._close_hover)

    host.frame.layout().addWidget(host.title_line)

    host.content = QWidget()
    host.content.setObjectName('dialog_content')
    host.content.setLayout(QVBoxLayout())
    # 14px sides + the content row's 1px inset: body content starts on
    # the dialogs' 15px text edge, with the title and Cancel.
    host.content.layout().setContentsMargins(14, 10, 14, 10)
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
    content_row.layout().addWidget(host.content)
    host.frame.layout().addWidget(content_row)

    # The bottom bar mirrors the title bar: a plain stretch on the left
    # carrying Cancel, and a tab on the right — dialog_bottom.svg is
    # dialog_bottom.svg — holding the default button.
    # Exposed as `host.bottom_line` (and `bottom_left` / `bottom_right`)
    # because dialogs used to reach it through `accept_button.parent()`,
    # which silently pointed at the wrong widget the moment the buttons
    # moved into the tab.
    host.bottom_line = QWidget()
    host.bottom_line.setObjectName('dialog_bottom')
    # Hug the buttons. Like the title bar, this row otherwise soaks up the
    # frame layout's spare height and stretches the tab artwork. A size
    # policy rather than setFixedHeight, so a dialog that needs a taller
    # footer (find & replace asks for 44px in QSS) can still have one.
    host.bottom_line.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    host.bottom_line.setLayout(QHBoxLayout())
    host.bottom_line.layout().setContentsMargins(0, 0, 0, 0)
    host.bottom_line.layout().setSpacing(0)

    host.accept_button = QPushButton(_('dialog.ok'))
    host.accept_button.setProperty('class', 'accept_button')

    host.reject_button = QPushButton(_('dialog.cancel'))
    host.reject_button.setProperty('class', 'reject_button')

    host.bottom_left = QWidget()
    host.bottom_left.setObjectName('dialog_bottom_left')
    host.bottom_left.setAttribute(Qt.WA_StyledBackground, True)
    host.bottom_left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    host.bottom_left.setLayout(QHBoxLayout())
    # No horizontal padding — Cancel starts at the bar's edge. The 5px
    # left cap in the artwork needs no reservation: the button is
    # transparent and its own 12px text padding already clears it.
    host.bottom_left.layout().setContentsMargins(0, 0, 0, 0)
    host.bottom_left.layout().setSpacing(0)
    host.bottom_left.layout().addWidget(host.reject_button)
    host.bottom_left.layout().addStretch()
    host.bottom_line.layout().addWidget(host.bottom_left, 1)

    # The tab. Extra actions go here via `add_bottom_button()`; it sizes
    # to whatever it holds, so several buttons simply widen the tab.
    host.bottom_right = QWidget()
    host.bottom_right.setObjectName('dialog_bottom_right')
    host.bottom_right.setAttribute(Qt.WA_StyledBackground, True)
    host.bottom_right.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    host.bottom_right.setLayout(QHBoxLayout())
    # 24px clears the diagonal, 5px the rounded right cap — otherwise the
    # buttons render underneath the artwork.
    host.bottom_right.layout().setContentsMargins(24, 0, 5, 0)
    host.bottom_right.layout().setSpacing(0)
    host.bottom_right.layout().addWidget(host.accept_button)
    host.bottom_line.layout().addWidget(host.bottom_right, 0)

    host.frame.layout().addWidget(host.bottom_line)


class SimpleDialog(QDialog):
    def __init__(self, parent=None, title='', *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.setMinimumWidth(400)

        build_dialog_chrome(self, title, self.reject)

        # Enable mouse tracking for dragging functionality
        self.title_line.setMouseTracking(True)
        self.title_line.mousePressEvent = self.title_mouse_press_event
        self.title_line.mouseMoveEvent = self.title_mouse_move_event
        self.title_line.mouseReleaseEvent = self.title_mouse_release_event

        self.accept_button.clicked.connect(self.accept)
        self.reject_button.clicked.connect(self.reject)

    # A plain message dialog gets roomier vertical padding and wrapped text;
    # its sides stay on the 15px text edge. Applied centrally rather than at
    # the ~17 call sites that build one, so future dialogs get it for free
    # and none can forget it.
    _TEXT_DIALOG_PADDING = (14, 16, 14, 16)
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
            # A word longer than the line (a URL, a path) sets the label's
            # minimum width to that word's width, and the layout then shares
            # out height as if the label were that wide. Qt breaks such a
            # word anyway, so let the label narrow to what it is given.
            label.setMinimumWidth(1)
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
        # adjustSize() takes the height the text needs at the size-hint width,
        # before the minimum/maximum width is applied. Take it again at the
        # width the dialog actually got, or the text is clipped (hint wider
        # than the maximum) or left with a spare line (hint narrower than the
        # minimum).
        outer = self.layout()
        if outer.hasHeightForWidth():
            self.resize(self.width(), outer.totalHeightForWidth(self.width()))

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
            # the window system moves the window (Wayland lets no window place itself); moved by hand otherwise
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def make_resizable(self):
        """Let the dialog be resized from its edges and corners: it has no
        window frame, so thin strips along them hand the resize to the
        window system."""
        self._resize_edges = [_ResizeEdge(self, edges) for edges in (
            Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
            Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.TopEdge,
            Qt.LeftEdge | Qt.BottomEdge, Qt.RightEdge | Qt.BottomEdge)]
        self._place_resize_edges()

    def _place_resize_edges(self):
        for edge in getattr(self, '_resize_edges', []):
            edge.place()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_resize_edges()

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

    def retranslate(self):
        """Put OK and Cancel on the buttons in the current language. A dialog
        built once and kept has its panel's translate() call this, so it
        follows a change of interface language; one that names its buttons
        itself does that after."""
        self.accept_button.setText(_('dialog.ok'))
        self.reject_button.setText(_('dialog.cancel'))

    def accept(self):
        super().accept()

    
class SlantLabeledField(QWidget):
    """A value with its label in front of it, in one 26px field: the label
    sits in a darker segment cut off by a 75° slant, as wide as its own text.
    The value widget (and anything added to bottom_line after it) is drawn on
    the field's own background. A choice (combo box) is raised like a button;
    a value typed in (spin box, line edit) is sunken like any field. Both
    have the 1px contact line along the bottom."""
    HEIGHT = 26
    LABEL_LEFT = 6                    # before the label's text (the stylesheet's padding-left on the label)
    LABEL_PADDING = 9                 # after it, measured at mid-height
    RAISED = True
    ROUNDED = True

    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        widget.setFixedHeight(widget.HEIGHT)
        widget.setAttribute(Qt.WA_Hover)

        widget.setLayout(QHBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 1)       # the contact line's row
        widget.layout().setSpacing(0)

        widget.label = QLabel()
        widget.layout().addWidget(widget.label)

        widget.bottom_line = QHBoxLayout()
        widget.bottom_line.setContentsMargins(0, 0, 0, 0)
        widget.bottom_line.setSpacing(0)
        widget.layout().addLayout(widget.bottom_line, 1)
        widget._fit_label()

    def _run(widget):
        return widget.HEIGHT * shapes.RUN15

    def _fit_label(widget):
        """The label segment fits its own text: a little padding, the text, more padding, then the slant."""
        text = widget.label.text().upper()
        width = widget.label.fontMetrics().horizontalAdvance(text) if text else 0
        widget._label_width = width + widget.LABEL_LEFT + widget.LABEL_PADDING if text else 0
        widget.label.setVisible(bool(text))
        widget.label.setFixedWidth(int(widget._label_width + widget._run() / 2) if text else 0)

    def setLabel(widget, label):
        widget.label.setText(label)
        widget._fit_label()
        widget.update()

    def changeEvent(widget, event):
        if event.type() in (QEvent.StyleChange, QEvent.FontChange):
            widget._fit_label()
        super().changeEvent(event)

    def _active(widget):
        return widget.underMouse()

    def _field(widget, rect):
        return shapes.rounded_path(rect) if widget.ROUNDED else shapes.rect_path(rect)

    def paintEvent(widget, event):
        painter = QPainter(widget)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(widget.rect())
        field = widget._field(rect)
        if not widget.isEnabled():
            painter.fillPath(field, theme.color(QColor('#25323d')))
            return
        active = widget._active()
        if widget.RAISED:
            shapes.fill_raised(painter, field, rect, shapes.RAISED if active else '#3e5363', contact=False)
        else:
            painter.fillPath(field, shapes.gradient(rect.top(), rect.bottom(), '#46596a' if active else '#3e5363', 'sunken'))
        if widget._label_width:
            run = widget._run()
            segment = QPainterPath(QPointF(0, 0))
            segment.lineTo(widget._label_width + run / 2, 0)
            segment.lineTo(widget._label_width - run / 2, rect.height())
            segment.lineTo(0, rect.height())
            segment.closeSubpath()
            shapes.fill_raised(painter, segment.intersected(field), rect, shapes.NEST, contact=False)
        painter.fillPath(field.subtracted(field.translated(0, -1)), theme.color(shapes.CONTACT))


class _SeparatorDelegate(QStyledItemDelegate):
    """A combo box's list items, and its separators as a line between them.
    The stylesheet styles the list's items, and Qt then draws them with a
    plain item delegate, which gives a separator no height at all."""
    HEIGHT = 9
    MARGIN = 6
    COLOR = QColor(184, 206, 224, 60)       # the list's text, faint

    @staticmethod
    def _is_separator(index):
        return index.data(Qt.AccessibleDescriptionRole) == 'separator'

    def sizeHint(self, option, index):
        if self._is_separator(index):
            return QSize(0, self.HEIGHT)
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        if not self._is_separator(index):
            super().paint(painter, option, index)
            return
        middle = option.rect.center().y()
        painter.fillRect(QRect(option.rect.left() + self.MARGIN, middle, option.rect.width() - 2 * self.MARGIN, 1), self.COLOR)


class LabeledComboBox(SlantLabeledField):
    """A combo box with its label in front of it (see SlantLabeledField)."""
    activated = Signal()

    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setObjectName('labeled_combobox')
        widget.combobox = QComboBox()
        widget.bottom_line.addWidget(widget.combobox, 1)
        widget.combobox.activated.connect(widget.activated)
        # the field is lit while the list is open: repaint all of it when the list opens or closes, or the part
        # under the combo box alone keeps the other state
        widget.combobox.view().window().installEventFilter(widget)

    def _active(widget):
        return widget.underMouse() or widget.combobox.view().isVisible()

    def eventFilter(widget, watched, event):
        if event.type() in (QEvent.Show, QEvent.Hide):
            QTimer.singleShot(0, widget.update)
        return False

    def clear(widget):
        widget.combobox.clear()
    
    def addItems(widget, items):
        widget.combobox.addItems(items)
    
    def addItem(widget, item):
        widget.combobox.addItem(item)

    def addSeparator(widget):
        """A line after the items so far, before the ones added next."""
        if not isinstance(widget.combobox.itemDelegate(), _SeparatorDelegate):
            widget.combobox.setItemDelegate(_SeparatorDelegate(widget.combobox))
        widget.combobox.insertSeparator(widget.combobox.count())

    def setCurrentText(widget, text):
        widget.combobox.setCurrentText(text)
    
    def currentText(widget):
        return widget.combobox.currentText()
    

class HeaderComboBox(LabeledComboBox):
    """The choice a page is about (an export page's format), as the page's
    header: 34px tall, edge to edge, flush with the page's top; its corners
    are square but the top two, 6px curves. The label is larger and the chosen option bold (the
    stylesheet's HeaderComboBox rules). Subclass it for headers of other pages."""
    HEIGHT = 34                       # as tall as the export dialog's categories
    LABEL_LEFT = 12
    LABEL_PADDING = 12
    ROUNDED = False

    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setObjectName('header_combobox')
        # the list draws its items in the box's font unless they carry their own: give them the usual one
        widget.combobox.model().rowsInserted.connect(widget._plain_items)

    def _plain_items(widget, _parent=None, first=0, last=-1):
        font = QFont(widget.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Normal)
        for row in range(first, last + 1):
            widget.combobox.setItemData(row, font, Qt.FontRole)

    def _field(widget, rect):
        # square, but for 6px curves at the top corners, where the header meets the columns on either side
        r = 6
        path = QPainterPath(QPointF(rect.left(), rect.top() + r))
        path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        return path


class LabeledDoubleSpinBox(SlantLabeledField):
    """A number with its label in front of it (see SlantLabeledField)."""
    valueChanged = Signal(float)
    RAISED = False

    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.spinbox = QDoubleSpinBox()
        widget.bottom_line.addWidget(widget.spinbox, 1)
        widget.spinbox.valueChanged.connect(widget.valueChanged)

    def setRange(widget, minimum, maximum):
        widget.spinbox.setRange(minimum, maximum)

    def setDecimals(widget, decimals):
        widget.spinbox.setDecimals(decimals)

    def setSingleStep(widget, step):
        widget.spinbox.setSingleStep(step)

    def setSuffix(widget, suffix):
        widget.spinbox.setSuffix(suffix)

    def setValue(widget, value):
        widget.spinbox.setValue(value)

    def value(widget):
        return widget.spinbox.value()


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
    

class LabeledLineEdit(SlantLabeledField):
    """A text with its label in front of it (see SlantLabeledField)."""
    editingFinished = Signal(str)
    RAISED = False

    def __init__(widget, parent=None):
        super().__init__(parent=parent)
        widget.setObjectName('labeled_lineedit')
        widget.lineedit = QLineEdit()
        widget.lineedit.editingFinished.connect(lambda: widget.editing_finished())
        widget.bottom_line.addWidget(widget.lineedit, 1)

    def setText(widget, text):
        widget.lineedit.setText(text)
    
    def text(widget):
        return widget.lineedit.text()
    
    def editing_finished(widget):
        widget.editingFinished.emit(widget.text())
