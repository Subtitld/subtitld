from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QTabWidget, QHBoxLayout, QSpinBox, QGroupBox, QColorDialog, QPushButton, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from subtitld.interface import left_panel, theme
from subtitld.interface.subtitle_style_panel import SubtitleStylePanel
from subtitld.interface import timeline_theme_browser
from subtitld.interface.timeline_theme_panel import TimelineThemePanel
from subtitld.interface.translation import _
from subtitld.modules import session, timeline_themes


def load(self):
    tab_name = 'interface'
    
    left_panel_interface_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    # Override the shared `left_panel` 10px margin so the tab widget hosted
    # inside this panel sits flush with the panel's edges.
    left_panel_interface_panel.layout().setContentsMargins(0, 0, 0, 0)

    left_panel_interface_panel_scroll = QScrollArea()
    left_panel_interface_panel_scroll.setObjectName('left_panel_interface_panel_scroll')
    left_panel_interface_panel_scroll.setWidgetResizable(True)
    left_panel_interface_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_interface_panel.layout().addWidget(left_panel_interface_panel_scroll)

    self.left_panel_interface_panel_widget = QWidget()
    self.left_panel_interface_panel_widget.setProperty('class', 'transparent_panel')
    self.left_panel_interface_panel_widget.setObjectName('left_panel_interface_panel_widget')
    self.left_panel_interface_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_interface_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_interface_panel_scroll.setWidget(self.left_panel_interface_panel_widget)

    self.global_panel_interface_tabwidget = QTabWidget()

    self.global_panel_interface_tabwidget_videoplayer = QWidget()
    self.global_panel_interface_tabwidget_videoplayer.setLayout(QVBoxLayout())
    self.global_panel_interface_tabwidget_videoplayer.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_interface_tabwidget_videoplayer.layout().setSpacing(20)

    # The subtitle style, and its own settings (built from its manifest).
    self.global_panel_interface_style_panel = SubtitleStylePanel()
    self.global_panel_interface_style_panel.changed.connect(lambda: refresh_preview(self))
    self.global_panel_interface_tabwidget_videoplayer.layout().addWidget(self.global_panel_interface_style_panel)

    self.global_panel_interface_videoplayer_safe_margin_action_group = QGroupBox()
    self.global_panel_interface_videoplayer_safe_margin_action_group.setCheckable(True)
    self.global_panel_interface_videoplayer_safe_margin_action_group.setLayout(QHBoxLayout())
    self.global_panel_interface_videoplayer_safe_margin_action_group.toggled.connect(lambda: global_panel_interface_videoplayer_safe_margin_action_group_toggled(self))
    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().setSpacing(20)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_x_margin_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line = QHBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line.setSpacing(5)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin = QSpinBox()
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin.setMinimum(0)
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin.setMaximum(50)
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin.valueChanged.connect(lambda: global_panel_interface_videoplayer_safe_margin_action_x_margin_changed(self))
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_x_margin, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_pixels_label = QLabel('%')
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_pixels_label.setProperty('class', 'units_label')
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_x_margin_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox.addLayout(self.global_panel_interface_videoplayer_safe_margin_action_x_margin_line)

    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_action_x_margin_vbox)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_y_margin_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line = QHBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line.setSpacing(5)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin = QSpinBox()
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin.setMinimum(0)
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin.setMaximum(50)
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin.valueChanged.connect(lambda: global_panel_interface_videoplayer_safe_margin_action_y_margin_changed(self))
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_y_margin, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_pixels_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_pixels_label.setProperty('class', 'units_label')
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_y_margin_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox.addLayout(self.global_panel_interface_videoplayer_safe_margin_action_y_margin_line)

    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_action_y_margin_vbox)

    self.global_panel_interface_videoplayer_safe_margin_action_color_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_action_color_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_action_color_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_action_color_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_action_color_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_action_color_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_color_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_color_button = QPushButton()
    self.global_panel_interface_videoplayer_safe_margin_action_color_button.setProperty('class', 'color_pick_button')
    self.global_panel_interface_videoplayer_safe_margin_action_color_button.setFixedWidth(80)
    self.global_panel_interface_videoplayer_safe_margin_action_color_button.clicked.connect(lambda: global_panel_interface_videoplayer_safe_margin_action_color_button_clicked(self))

    self.global_panel_interface_videoplayer_safe_margin_action_color_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_action_color_button, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_action_color_vbox)

    self.global_panel_interface_videoplayer_safe_margin_action_group.layout().addStretch()

    self.global_panel_interface_tabwidget_videoplayer.layout().addWidget(self.global_panel_interface_videoplayer_safe_margin_action_group)

    self.global_panel_interface_videoplayer_safe_margin_title_group = QGroupBox()
    self.global_panel_interface_videoplayer_safe_margin_title_group.setCheckable(True)
    self.global_panel_interface_videoplayer_safe_margin_title_group.setLayout(QHBoxLayout())
    self.global_panel_interface_videoplayer_safe_margin_title_group.toggled.connect(lambda: global_panel_interface_videoplayer_safe_margin_title_group_toggled(self))
    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().setSpacing(20)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_x_margin_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line = QHBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line.setSpacing(5)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin = QSpinBox()
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin.setMinimum(0)
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin.setMaximum(50)
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin.valueChanged.connect(lambda: global_panel_interface_videoplayer_safe_margin_title_x_margin_changed(self))
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_x_margin, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_pixels_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_pixels_label.setProperty('class', 'units_label')
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_x_margin_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox.addLayout(self.global_panel_interface_videoplayer_safe_margin_title_x_margin_line)

    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_title_x_margin_vbox)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_y_margin_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line = QHBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line.setSpacing(5)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin = QSpinBox()
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin.setMinimum(0)
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin.setMaximum(50)
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin.valueChanged.connect(lambda: global_panel_interface_videoplayer_safe_margin_title_y_margin_changed(self))
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_y_margin, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_pixels_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_pixels_label.setProperty('class', 'units_label')
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_y_margin_pixels_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox.addLayout(self.global_panel_interface_videoplayer_safe_margin_title_y_margin_line)

    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_title_y_margin_vbox)

    self.global_panel_interface_videoplayer_safe_margin_title_color_vbox = QVBoxLayout()
    self.global_panel_interface_videoplayer_safe_margin_title_color_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_interface_videoplayer_safe_margin_title_color_vbox.setSpacing(2)

    self.global_panel_interface_videoplayer_safe_margin_title_color_label = QLabel()
    self.global_panel_interface_videoplayer_safe_margin_title_color_label.setProperty('class', 'widget_label')
    self.global_panel_interface_videoplayer_safe_margin_title_color_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_color_label, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_color_button = QPushButton()
    self.global_panel_interface_videoplayer_safe_margin_title_color_button.setProperty('class', 'color_pick_button')
    self.global_panel_interface_videoplayer_safe_margin_title_color_button.setFixedWidth(80)
    self.global_panel_interface_videoplayer_safe_margin_title_color_button.clicked.connect(lambda: global_panel_interface_videoplayer_safe_margin_title_color_button_clicked(self))

    self.global_panel_interface_videoplayer_safe_margin_title_color_vbox.addWidget(self.global_panel_interface_videoplayer_safe_margin_title_color_button, 0, Qt.AlignLeft)

    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().addLayout(self.global_panel_interface_videoplayer_safe_margin_title_color_vbox)

    self.global_panel_interface_videoplayer_safe_margin_title_group.layout().addStretch()

    self.global_panel_interface_tabwidget_videoplayer.layout().addWidget(self.global_panel_interface_videoplayer_safe_margin_title_group)

    self.global_panel_interface_tabwidget_videoplayer.layout().addStretch()

    self.global_panel_interface_tabwidget.addTab(self.global_panel_interface_tabwidget_videoplayer, '')

    # The timeline's look: its theme.
    self.global_panel_interface_tabwidget_timeline = QWidget()
    self.global_panel_interface_tabwidget_timeline.setLayout(QVBoxLayout())
    self.global_panel_interface_tabwidget_timeline.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_interface_timeline_theme_panel = TimelineThemePanel()
    self.global_panel_interface_timeline_theme_panel.changed.connect(lambda: refresh_timeline(self))
    theme.notifier.changed.connect(lambda name: timeline_follow_look(self, name))
    self.global_panel_interface_tabwidget_timeline.layout().addWidget(self.global_panel_interface_timeline_theme_panel)
    # Under it, the themes on subtitld.cc, to install; fetched the first time the tab is shown.
    self.global_panel_interface_tabwidget_timeline.layout().addSpacing(24)
    self.global_panel_interface_theme_browser = timeline_theme_browser.ThemeBrowser()
    self.global_panel_interface_theme_browser.installed.connect(lambda: timeline_theme_browser.follow(self))
    # What subtitld.cc lists tells the installed themes' cards of their updates.
    self.global_panel_interface_theme_browser.listed.connect(self.global_panel_interface_timeline_theme_panel.build)
    self.global_panel_interface_tabwidget_timeline.layout().addWidget(self.global_panel_interface_theme_browser)
    self.global_panel_interface_tabwidget_timeline.layout().addStretch()
    self.global_panel_interface_tabwidget.addTab(self.global_panel_interface_tabwidget_timeline, '')

    self.left_panel_interface_panel_widget.layout().addWidget(self.global_panel_interface_tabwidget)
    # Only the page shown sets the panel's width: a tab widget is as wide as its widest page, and the
    # Video subtitles page is wider than the panel, which made the Timeline tab scroll sideways too.
    self.global_panel_interface_tabwidget.currentChanged.connect(lambda index: fit_shown_tab(self))
    fit_shown_tab(self)


    update(self)

    
def show(self):
    update(self)

def update(self):
    self.global_panel_interface_videoplayer_safe_margin_action_group.setChecked(session.CONFIG['videoplayer'].get('safe_margin_action_enabled', False))
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin.setValue(session.CONFIG['videoplayer'].get('safe_margin_action_x', 5))
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin.setValue(session.CONFIG['videoplayer'].get('safe_margin_action_y', 5))
    self.global_panel_interface_videoplayer_safe_margin_action_color_button.setStyleSheet('QPushButton { background-color: ' + session.CONFIG['videoplayer'].get('safe_margin_action_color', '#dd67FF4D') + ' }')
    self.global_panel_interface_videoplayer_safe_margin_title_group.setChecked(session.CONFIG['videoplayer'].get('safe_margin_title_enabled', False))
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin.setValue(session.CONFIG['videoplayer'].get('safe_margin_title_x', 10))
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin.setValue(session.CONFIG['videoplayer'].get('safe_margin_title_y', 10))
    self.global_panel_interface_videoplayer_safe_margin_title_color_button.setStyleSheet('QPushButton { background-color: ' + session.CONFIG['videoplayer'].get('safe_margin_title_color', '#ddff0000') + ' }')




def refresh_preview(self):
    """Repaint the subtitle drawn over the video with the new settings."""
    if hasattr(self, 'preview_panel_player'):
        self.preview_panel_player._graphics_video_item.update()


def timeline_follow_look(self, name):
    """The interface's look changed: the timeline's theme to its counterpart, if it has one."""
    if timeline_themes.follow_look(name):
        self.global_panel_interface_timeline_theme_panel.build()
        refresh_timeline(self)


def refresh_timeline(self):
    """Paint the timelines again with the new look."""
    if hasattr(self, 'timeline_widget'):
        self.timeline_widget.apply_style()
    vtimeline_panel = getattr(self, 'vtimeline_panel', None)
    if vtimeline_panel is not None:
        vtimeline_panel.scroll.canvas.update()


def global_panel_interface_videoplayer_safe_margin_action_group_toggled(self):
    session.CONFIG['videoplayer']['safe_margin_action_enabled'] = self.global_panel_interface_videoplayer_safe_margin_action_group.isChecked()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_action_x_margin_changed(self):
    session.CONFIG['videoplayer']['safe_margin_action_x'] = self.global_panel_interface_videoplayer_safe_margin_action_x_margin.value()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_action_y_margin_changed(self):
    session.CONFIG['videoplayer']['safe_margin_action_y'] = self.global_panel_interface_videoplayer_safe_margin_action_y_margin.value()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_action_color_button_clicked(self):
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['videoplayer']['safe_margin_action_color'] = color.name(QColor.HexArgb)
    refresh_preview(self)
    update(self)


def global_panel_interface_videoplayer_safe_margin_title_group_toggled(self):
    session.CONFIG['videoplayer']['safe_margin_title_enabled'] = self.global_panel_interface_videoplayer_safe_margin_title_group.isChecked()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_title_x_margin_changed(self):
    session.CONFIG['videoplayer']['safe_margin_title_x'] = self.global_panel_interface_videoplayer_safe_margin_title_x_margin.value()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_title_y_margin_changed(self):
    session.CONFIG['videoplayer']['safe_margin_title_y'] = self.global_panel_interface_videoplayer_safe_margin_title_y_margin.value()
    refresh_preview(self)


def global_panel_interface_videoplayer_safe_margin_title_color_button_clicked(self):
    color = QColorDialog.getColor(options=QColorDialog.ShowAlphaChannel)
    if color.isValid():
        session.CONFIG['videoplayer']['safe_margin_title_color'] = color.name(QColor.HexArgb)
    refresh_preview(self)
    update(self)

    
def fit_shown_tab(self):
    tabs = self.global_panel_interface_tabwidget
    for index in range(tabs.count()):
        shown = index == tabs.currentIndex()
        tabs.widget(index).setSizePolicy(QSizePolicy.Preferred if shown else QSizePolicy.Ignored,
                                         QSizePolicy.Preferred if shown else QSizePolicy.Ignored)
    tabs.updateGeometry()


def hide(self):
    pass

    
def translate(self):    
    self.global_panel_interface_style_panel.build()
    self.global_panel_interface_timeline_theme_panel.build()
    self.global_panel_interface_theme_browser.translate()
    self.global_panel_interface_videoplayer_safe_margin_action_group.setTitle(_('interface_panel.action_safe_margin'))
    self.global_panel_interface_videoplayer_safe_margin_action_x_margin_label.setText(_('interface_panel.horizontal_margin'))
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_label.setText(_('interface_panel.vertical_margin'))
    self.global_panel_interface_videoplayer_safe_margin_action_y_margin_pixels_label.setText(_('units.pixels'))
    self.global_panel_interface_videoplayer_safe_margin_action_color_label.setText(_('units.color'))
    self.global_panel_interface_videoplayer_safe_margin_title_group.setTitle(_('interface_panel.title_safe_margin'))
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_label.setText(_('interface_panel.horizontal_margin'))
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_label.setText(_('interface_panel.vertical_margin'))
    self.global_panel_interface_videoplayer_safe_margin_title_y_margin_pixels_label.setText(_('units.pixels'))
    self.global_panel_interface_videoplayer_safe_margin_title_color_label.setText(_('units.color'))
    # The colour swatches show no text: named after their margin.
    self.global_panel_interface_videoplayer_safe_margin_action_color_button.setAccessibleName(
        f"{_('interface_panel.action_safe_margin')}: {_('units.color')}")
    self.global_panel_interface_videoplayer_safe_margin_title_color_button.setAccessibleName(
        f"{_('interface_panel.title_safe_margin')}: {_('units.color')}")
    self.global_panel_interface_tabwidget.setTabText(0, _('interface_panel.video_player'))
    self.global_panel_interface_tabwidget.setTabText(1, _('interface_panel.timeline'))
    self.global_panel_interface_videoplayer_safe_margin_title_x_margin_pixels_label.setText(_('units.pixels'))



    
