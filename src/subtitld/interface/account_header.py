"""The header an account panel keeps in view above its cards (subtitld.cc,
Subtitld Cloud): edge to edge, a shade darker than the panel; on the left
who is connected (a name in bold, under it a second line and a badge
saying whether it is connected); in the top-right corner the service's
mark on a slanted tab, and under it its icon buttons, shown while the
pointer is over the header. HeaderShadow falls from it over the cards.
"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from subtitld.modules import session


def _label(object_name):
    label = QLabel()
    label.setObjectName(object_name)
    return label


class AccountHeader(QWidget):
    SHADE = QColor(0, 0, 0, 70)                        # well apart from the lighter cards' area (account_cards)

    def __init__(self, mark, word):
        """`mark`: the service's mark (a file in graphics); `word`: its name
        beside the mark, on the tab."""
        super().__init__()
        self.setObjectName('account_header')
        self.setMinimumHeight(54)                       # one height, connected or not
        # as tall as it needs, never more: the panel's spare height is the cards'
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        who = QVBoxLayout()
        who.setContentsMargins(0, 6, 0, 6)
        who.setSpacing(4)
        who.addStretch()                                # centred, with or without a name
        self.name = _label('account_header_name')
        who.addWidget(self.name)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.handle = _label('account_header_handle')
        line.addWidget(self.handle)
        self.badge = _label('account_header_badge')
        line.addWidget(self.badge)
        # not connected: only this, in the badge's place
        self.connect_button = QPushButton()
        self.connect_button.setObjectName('account_header_connect')
        self.connect_button.setCursor(Qt.PointingHandCursor)
        self.connect_button.setVisible(False)
        line.addWidget(self.connect_button)
        line.addStretch()
        for piece in (self.handle, self.badge):
            piece.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        who.addLayout(line)
        who.addStretch()
        layout.addLayout(who, 1)

        corner = QVBoxLayout()
        corner.setContentsMargins(0, 0, 0, 0)
        corner.setSpacing(0)
        self.logo = QWidget()
        self.logo.setObjectName('account_header_logo')
        self.logo.setAttribute(Qt.WA_StyledBackground, True)
        self.logo.setFixedHeight(31)                    # the tab and its contact line
        logo = QHBoxLayout(self.logo)
        logo.setContentsMargins(18, 0, 12, 1)           # the slant; the contact line
        logo.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / mark)).pixmap(16, 16))
        icon.setStyleSheet('background: transparent;')
        logo.addWidget(icon)
        self.word = _label('account_header_word')
        self.word.setText(word)
        logo.addWidget(self.word)
        corner.addWidget(self.logo)
        corner.addStretch()
        self._buttons = QHBoxLayout()
        self._buttons.setContentsMargins(0, 0, 6, 0)
        self._buttons.setSpacing(2)
        self._buttons.addStretch()
        corner.addLayout(self._buttons)
        corner.addStretch()                             # centred between the tab's foot and the header's
        layout.addLayout(corner)

        self.connected = False
        self.hover_buttons = []

    def add_hover_button(self, icon):
        """An icon button under the tab, shown while the pointer is over the
        header and the account is connected. The caller connects it and sets
        its tooltip."""
        button = QPushButton()
        button.setObjectName('account_header_button')
        button.setIcon(QIcon(str(session.PATH_SUBTITLD_GRAPHICS / icon)))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(26, 22)
        button.setCursor(Qt.PointingHandCursor)
        button.setVisible(False)
        self._buttons.addWidget(button)
        self.hover_buttons.append(button)
        return button

    def show_account(self, connected, name='', second='', state='', can_connect=False):
        """`name` in bold (or `second` there, when there is no name), `second`
        under it, and the badge saying `state`, green when `connected`. Not
        connected and `can_connect`: the Connect button alone."""
        self.connected = connected
        self.name.setText(name or second)
        self.name.setVisible(bool(connected and (name or second)))
        self.handle.setText(second if name else '')
        self.handle.setVisible(bool(connected and name and second))
        offer = not connected and can_connect
        self.connect_button.setVisible(offer)
        self.badge.setText(state)
        self.badge.setVisible(bool(state) and not offer)
        self.badge.setProperty('connected', connected)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        for button in self.hover_buttons:
            button.setVisible(connected and self.underMouse())

    def set_warning(self, warning):
        """The badge in red, for an account that needs attention (a low balance)."""
        self.badge.setProperty('warning', warning)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), self.SHADE)

    def enterEvent(self, event):
        super().enterEvent(event)
        for button in self.hover_buttons:
            button.setVisible(self.connected)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        for button in self.hover_buttons:
            button.setVisible(False)


class HeaderShadow(QWidget):
    """A soft shadow falling from the header over the cards scrolling under
    it (or, `up`, rising from a footer over them): a child of the panel,
    laid over its scroll area (the panel places it in its resizeEvent)."""
    HEIGHT = 10

    def __init__(self, parent, up=False):
        super().__init__(parent)
        self.up = up
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(self.HEIGHT)

    def place_under(self, header):
        self.setGeometry(0, header.geometry().bottom() + 1, self.parentWidget().width(), self.HEIGHT)
        self.raise_()

    def place_over(self, footer):
        top = footer.geometry().top() + getattr(footer, 'RISE', 0)      # where its shade starts
        self.setGeometry(0, top - self.HEIGHT, self.parentWidget().width(), self.HEIGHT)
        self.raise_()

    def paintEvent(self, event):
        shadow = QLinearGradient(0, self.height(), 0, 0) if self.up else QLinearGradient(0, 0, 0, self.height())
        shadow.setColorAt(0, QColor(0, 0, 0, 70))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        QPainter(self).fillRect(self.rect(), shadow)
