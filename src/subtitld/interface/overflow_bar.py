"""A row of buttons that shows as many as fit, the rest behind a "…" button
at its end, in a popup that opens over it: as Inkscape's toolbars do.

The buttons are a bar's own (the formatting bar's): those that do not fit
are moved into the popup, in their order, and back as the row grows, so
they keep working wherever they are. A button its bar hides (one for
timed text alone) stays hidden in either place and takes no room.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget


class OverflowBar(QWidget):
    """`bar`'s buttons in a row as wide as there is room for; `more` opens
    the ones left out."""

    def __init__(self, bar, more_text='…', parent=None):
        super().__init__(parent)
        self.bar = bar
        self.setSizePolicy(QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred))
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.layout().addWidget(bar)
        self.more = QPushButton(more_text)
        self.more.setObjectName('overflow_bar_more')
        self.more.setProperty('class', 'subtitle_formatting_button')
        self.more.setCheckable(True)
        self.more.clicked.connect(self.open_more)
        self.layout().addWidget(self.more)
        self.layout().addStretch()
        self.popup = QFrame(self, Qt.Popup | Qt.FramelessWindowHint)
        self.popup.setObjectName('overflow_bar_popup')
        self.popup.setLayout(QHBoxLayout())
        self.popup.layout().setContentsMargins(4, 4, 4, 4)
        self.popup.layout().setSpacing(2)
        self.popup.hide()
        self.popup.installEventFilter(self)
        # The buttons, in their order, wherever they are now.
        layout = bar.layout()
        self.items = [layout.itemAt(index).widget() for index in range(layout.count())
                      if layout.itemAt(index).widget() is not None]
        self.stretch_at = layout.count()            # where the bar's own stretch is, after its buttons
        self.more.hide()

    def eventFilter(self, watched, event):
        if watched is self.popup and event.type() == event.Type.Hide:
            self.more.setChecked(False)
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def relayout(self):
        """As many buttons in the row as fit, the rest in the popup."""
        shown = [item for item in self.items if not item.isHidden()]
        spacing = self.bar.layout().spacing()
        margins = self.bar.layout().contentsMargins()
        room = self.width() - margins.left() - margins.right()
        widths = [item.sizeHint().width() + spacing for item in shown]
        fits = len(shown) if sum(widths) <= room else 0
        if fits < len(shown):
            room -= self.more.sizeHint().width()
            used = 0
            for width in widths:
                if used + width > room:
                    break
                used += width
                fits += 1
        keep = set(map(id, shown[:fits]))
        bar_layout, popup_layout = self.bar.layout(), self.popup.layout()
        place = 0
        for item in self.items:
            hidden = item.isHidden()
            # Hidden by its bar: left where it is.
            target = bar_layout if hidden or id(item) in keep else popup_layout
            if target is bar_layout:
                if item.parent() is not self.bar:
                    bar_layout.insertWidget(place, item)
                    item.setVisible(not hidden)
                place += 1
            elif item.parent() is not self.popup:
                popup_layout.addWidget(item)
                item.setVisible(True)
        # The popup's in the bar's order too.
        for item in self.items:
            if item.parent() is self.popup:
                popup_layout.removeWidget(item)
                popup_layout.addWidget(item)
        self.more.setVisible(fits < len(shown))
        if not self.more.isVisible():
            self.popup.hide()

    def open_more(self):
        """The buttons left out, in a popup over the "…" button."""
        if not self.more.isChecked():
            self.popup.hide()
            return
        self.popup.adjustSize()
        corner = self.more.mapToGlobal(QPoint(self.more.width(), 0))
        self.popup.move(corner.x() - self.popup.width(), corner.y() - self.popup.height() - 2)
        self.popup.show()
