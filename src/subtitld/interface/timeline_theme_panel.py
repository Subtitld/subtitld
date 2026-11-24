"""The timeline's themes, as cards (interface/timeline_theme_browser.py): every theme there is here
(modules.timeline_themes), Subtitld's own first. A card puts its theme on the timeline (Use), or says it
is there (In use); its options button opens the few options the theme lets its user change, in a dialog,
with a button to set them back to the theme's own; one installed from subtitld.cc or imported can be
removed, and one from subtitld.cc updated when subtitld.cc has a newer version.

Under the cards in the Timeline tab, the themes on subtitld.cc not installed here, to install.

Each card shows the theme's picture: the one shipped with Subtitld's own, the one subtitld.cc drew for
those installed from there (timeline_themes.preview_path), or the theme's key colours for others. Built-in
themes' names and their options' labels come from the locale files (`timeline_themes.<id>.name`,
`timeline_themes.<id>.options.<key>`), others' from their file.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from subtitld.interface import utils
from subtitld.interface.subtitle_style_panel import _text, field_control
from subtitld.interface.timeline_theme_browser import ThemeCard, failure_text, install, remote
from subtitld.interface.translation import _
from subtitld.modules import theme_store, timeline_style, timeline_themes
from subtitld.modules import subtitldcc_service as service

COLUMNS = 2
GROUND = {'dark': '#1a232b', 'light': '#dde4eb'}     # the panel's ground behind a timeline, per look
_pictures = {}                                       # (path, mtime) -> QPixmap


def theme_label(theme):
    """A theme's name: from the locale files for one of Subtitld's, its own otherwise."""
    return _text(f'timeline_themes.{theme.id}.name', theme.name) if theme.builtin else theme.name


def option_label(theme, option):
    """An option's label: from the locale files for one of Subtitld's themes, its own otherwise."""
    own = option.get('label') or option['key'].replace('_', ' ').capitalize()
    return _text(f'timeline_themes.{theme.id}.options.{option["key"]}', own) if theme.builtin else own


def choice_label(theme, option, choice):
    own = choice.get('label') or str(choice['value'])
    key = f'timeline_themes.{theme.id}.options.{option["key"]}.{choice["value"]}'
    return _text(key, own) if theme.builtin else own


def key_colors(theme):
    """The theme's ground, box, selected box and waveform, each as it shows: for a card with no picture."""
    look = theme.look or {}
    timeline = {**timeline_style.TIMELINE_DEFAULTS, **(look.get('timeline') or {})}
    box = {**timeline_style.BOX_DEFAULTS, **((look.get('boxes') or {}).get(timeline_style.SUBTITLE) or {})}
    base = QColor(GROUND.get(theme.look_for or 'dark', GROUND['dark']))

    def over(under, value):
        color = QColor(value)
        alpha = color.alphaF()
        return QColor.fromRgbF(*(c * alpha + u * (1 - alpha)
                                 for c, u in zip(color.getRgbF()[:3], under.getRgbF()[:3], strict=True)))

    ground = over(base, timeline['background_color'])
    return [ground, over(ground, box['fill_color']), over(ground, box['selected_fill_color']),
            over(ground, timeline['waveform_fill_color'])]


def picture(theme):
    path = timeline_themes.preview_path(theme)
    if path is None:
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _pictures:
        _pictures[key] = QPixmap(str(path))
    return _pictures[key]


class ThemeOptionsDialog(utils.SimpleDialog):
    """A theme's options; `changed` after each. Reset sets them back to the theme's own."""

    changed = Signal()

    def __init__(self, theme, parent=None):
        super().__init__(parent, title=_('theme_store.options_title').format(name=theme_label(theme)))
        self.setObjectName('timeline_theme_options_dialog')
        self.theme = theme
        self.controls = {}
        self.fields = None
        self.reject_button.setText(_('timeline_style.reset_options'))
        self.reject_button.setObjectName('timeline_theme_reset_button')
        self.reject_button.clicked.disconnect()
        self.reject_button.clicked.connect(self.reset)
        self.accept_button.setText(_('theme_store.close'))
        self.build()

    def build(self):
        if self.fields is not None:
            self.content.layout().removeWidget(self.fields)
            self.fields.setParent(None)
            self.fields.deleteLater()
        self.fields = QWidget()
        self.fields.setObjectName('timeline_theme_options')
        grid = QGridLayout(self.fields)
        grid.setContentsMargins(0, 6, 0, 6)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        self.controls.clear()
        values = timeline_themes.option_values(self.theme)
        for index, option in enumerate(self.theme.options):
            cell_layout, control = field_control(
                option, values[option['key']], option_label(self.theme, option),
                lambda choice, option=option: choice_label(self.theme, option, choice), self._set, self)
            cell = QWidget()
            cell.setLayout(cell_layout)
            grid.addWidget(cell, index // COLUMNS, index % COLUMNS, Qt.AlignLeft | Qt.AlignTop)
            self.controls[option['key']] = control
        grid.setColumnStretch(COLUMNS, 1)
        self.content.layout().addWidget(self.fields)

    def _set(self, key, value):
        try:
            value = timeline_themes.set_option(key, value, self.theme.id)
        except (KeyError, ValueError):
            return None
        self.changed.emit()
        return value

    def reset(self):
        timeline_themes.reset_options(self.theme.id)
        self.build()
        self.changed.emit()


class TimelineThemePanel(QWidget):
    """The themes there are, as cards; `changed` after the timeline's theme, or its options, changed."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 0, 10, 0)
        self.layout().setSpacing(10)
        self.label = QLabel()
        self.label.setProperty('class', 'widget_label')
        self.layout().addWidget(self.label)
        self.custom = QLabel()
        self.custom.setObjectName('timeline_theme_custom')
        self.custom.setProperty('class', 'addon_card_aside')
        self.custom.setWordWrap(True)
        self.layout().addWidget(self.custom)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        self.layout().addWidget(self.status)
        self.list = QVBoxLayout()
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        self.layout().addLayout(self.list)
        self.cards = []
        self.calls = []
        self.dialog = None
        self.build()

    def build(self):
        """A card per theme there is, in the interface's language."""
        self.label.setText(_('theme_store.yours'))
        current = timeline_themes.current()
        self.custom.setText(_('theme_store.custom_note'))
        self.custom.setVisible(current is None)
        for card in self.cards:
            self.list.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        themes = timeline_themes.available()
        for theme in themes.values():
            share_id = theme_store.share_id_of(theme)
            listed = remote.get(share_id) if share_id else None
            installed = theme_store.installed_version(share_id) if share_id else None
            info = {
                'theme_id': theme.id,
                'share_id': share_id,
                'title': theme_label(theme),
                'owner': theme_store.owner_of(theme),
                'author': theme.author,
                'look': theme.look_for,
                'counterpart': bool(theme.counterpart and theme.counterpart in themes),
                'url': listed['url'] if listed else (service.site_url(f'/{share_id}') if share_id else None),
                'options': bool(theme.options),
                'removable': not theme.builtin,
                'state': 'in_use' if current is not None and current.id == theme.id else 'use',
                'update': listed['version'] if listed and installed and listed['version'] > installed else None,
            }
            card = ThemeCard(info, [] if picture(theme) else key_colors(theme))
            card.set_preview(picture(theme))
            card.act.connect(self._act)
            self.list.addWidget(card)
            self.cards.append(card)

    def _act(self, info, action):
        theme_id = info['theme_id']
        if action == 'use':
            theme_store.use(theme_id)
        elif action == 'remove':
            theme_store.remove(theme_id)
        elif action == 'options':
            self.dialog = ThemeOptionsDialog(timeline_themes.available()[theme_id], self.window())
            self.dialog.changed.connect(self.changed.emit)
            self.dialog.exec()
            return
        elif action == 'update':
            card = next(card for card in self.cards if card.info['theme_id'] == theme_id)
            in_use = info['state'] == 'in_use'
            install(self, card, info['share_id'], lambda: (self.build(), self.changed.emit()), use=in_use)
            return
        self.build()
        self.changed.emit()

    def install_failed(self, card, error):
        self.status.setText(_('theme_store.install_failed').format(error=failure_text(error)))
        self.status.show()

    def apply_theme(self, theme_id):
        theme_store.use(theme_id)
        self.build()
        self.changed.emit()

    def reset_options(self):
        timeline_themes.reset_options()
        self.build()
        self.changed.emit()

