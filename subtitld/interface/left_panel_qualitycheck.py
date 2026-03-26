from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel, QScrollArea, QCheckBox, QGroupBox, QHBoxLayout, QSpinBox, QDoubleSpinBox, QSlider
from PySide6.QtCore import Qt

from subtitld.interface import left_panel
from subtitld.interface.translation import _
from subtitld.modules import session


def load(self):
    tab_name = 'qualitycheck'
    
    left_panel_qualitycheck_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    left_panel_qualitycheck_panel_scroll = QScrollArea()
    left_panel_qualitycheck_panel_scroll.setObjectName('left_panel_qualitycheck_panel_scroll')
    left_panel_qualitycheck_panel_scroll.setWidgetResizable(True)
    left_panel_qualitycheck_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_qualitycheck_panel.layout().addWidget(left_panel_qualitycheck_panel_scroll)

    self.left_panel_qualitycheck_panel_widget = QWidget()
    self.left_panel_qualitycheck_panel_widget.setProperty('class', 'transparent_panel')
    self.left_panel_qualitycheck_panel_widget.setObjectName('left_panel_qualitycheck_panel_widget')
    self.left_panel_qualitycheck_panel_widget.setLayout(QVBoxLayout())
    self.left_panel_qualitycheck_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_qualitycheck_panel_scroll.setWidget(self.left_panel_qualitycheck_panel_widget)

    self.global_panel_tabwidget_show_statistics_checkbox = QCheckBox()
    self.global_panel_tabwidget_show_statistics_checkbox.setChecked(False)
    self.global_panel_tabwidget_show_statistics_checkbox.clicked.connect(lambda: save_quality_settings(self))
    self.left_panel_qualitycheck_panel_widget.layout().addWidget(self.global_panel_tabwidget_show_statistics_checkbox)

    self.global_panel_tabwidget_quality_enable_groupbox = QGroupBox()
    self.global_panel_tabwidget_quality_enable_groupbox.setCheckable(True)
    self.global_panel_tabwidget_quality_enable_groupbox.setLayout(QVBoxLayout())
    self.global_panel_tabwidget_quality_enable_groupbox.clicked.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_enable_groupbox.layout().setContentsMargins(10, 10, 10, 10)
    self.global_panel_tabwidget_quality_enable_groupbox.layout().setSpacing(20)

    self.global_panel_tabwidget_quality_readingspeed_vbox = QVBoxLayout()
    self.global_panel_tabwidget_quality_readingspeed_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_readingspeed_vbox.setSpacing(2)

    self.global_panel_tabwidget_quality_readingspeed_label = QLabel()
    self.global_panel_tabwidget_quality_readingspeed_label.setProperty('class', 'widget_label')
    self.global_panel_tabwidget_quality_readingspeed_vbox.addWidget(self.global_panel_tabwidget_quality_readingspeed_label, 0, Qt.AlignLeft)

    self.global_panel_tabwidget_quality_readingspeed_line = QHBoxLayout()
    self.global_panel_tabwidget_quality_readingspeed_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_readingspeed_line.setSpacing(5)

    self.global_panel_tabwidget_quality_readingspeed_cps = QSpinBox()
    self.global_panel_tabwidget_quality_readingspeed_cps.setMinimum(0)
    self.global_panel_tabwidget_quality_readingspeed_cps.setMaximum(999)
    self.global_panel_tabwidget_quality_readingspeed_cps.setValue(21)
    self.global_panel_tabwidget_quality_readingspeed_cps.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_readingspeed_line.addWidget(self.global_panel_tabwidget_quality_readingspeed_cps)

    self.global_panel_tabwidget_quality_readingspeed_cps_label = QLabel()
    self.global_panel_tabwidget_quality_readingspeed_cps_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_readingspeed_line.addWidget(self.global_panel_tabwidget_quality_readingspeed_cps_label, 0, Qt.AlignLeft)

    self.global_panel_tabwidget_quality_readingspeed_wpm = QSpinBox()
    self.global_panel_tabwidget_quality_readingspeed_wpm.setMinimum(0)
    self.global_panel_tabwidget_quality_readingspeed_wpm.setMaximum(999)
    self.global_panel_tabwidget_quality_readingspeed_wpm.setValue(140)
    self.global_panel_tabwidget_quality_readingspeed_wpm.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_readingspeed_line.addWidget(self.global_panel_tabwidget_quality_readingspeed_wpm)

    self.global_panel_tabwidget_quality_readingspeed_wpm_label = QLabel()
    self.global_panel_tabwidget_quality_readingspeed_wpm_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_readingspeed_line.addWidget(self.global_panel_tabwidget_quality_readingspeed_wpm_label, 0, Qt.AlignLeft)

    self.global_panel_tabwidget_quality_readingspeed_line.addStretch()

    self.global_panel_tabwidget_quality_readingspeed_vbox.addLayout(self.global_panel_tabwidget_quality_readingspeed_line)

    self.global_panel_tabwidget_quality_enable_groupbox.layout().addLayout(self.global_panel_tabwidget_quality_readingspeed_vbox)

    self.global_panel_tabwidget_quality_duration_vbox = QVBoxLayout()
    self.global_panel_tabwidget_quality_duration_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_duration_vbox.setSpacing(2)

    self.global_panel_tabwidget_quality_duration_label = QLabel()
    self.global_panel_tabwidget_quality_duration_label.setProperty('class', 'widget_label')
    self.global_panel_tabwidget_quality_duration_vbox.addWidget(self.global_panel_tabwidget_quality_duration_label, 0, Qt.AlignLeft)

    self.global_panel_tabwidget_quality_duration_line = QHBoxLayout()
    self.global_panel_tabwidget_quality_duration_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_duration_line.setSpacing(5)

    self.global_panel_tabwidget_quality_duration_minimum = QDoubleSpinBox()
    self.global_panel_tabwidget_quality_duration_minimum.setMinimum(0.1)
    self.global_panel_tabwidget_quality_duration_minimum.setMaximum(999.999)
    self.global_panel_tabwidget_quality_duration_minimum.setValue(.7)
    self.global_panel_tabwidget_quality_duration_minimum.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_duration_line.addWidget(self.global_panel_tabwidget_quality_duration_minimum)

    self.global_panel_tabwidget_quality_duration_minimum_label = QLabel()
    self.global_panel_tabwidget_quality_duration_minimum_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_duration_line.addWidget(self.global_panel_tabwidget_quality_duration_minimum_label)

    self.global_panel_tabwidget_quality_duration_maximum = QDoubleSpinBox()
    self.global_panel_tabwidget_quality_duration_maximum.setMinimum(0.2)
    self.global_panel_tabwidget_quality_duration_maximum.setMaximum(999.999)
    self.global_panel_tabwidget_quality_duration_maximum.setValue(7)
    self.global_panel_tabwidget_quality_duration_maximum.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_duration_line.addWidget(self.global_panel_tabwidget_quality_duration_maximum)

    self.global_panel_tabwidget_quality_duration_maximum_label = QLabel()
    self.global_panel_tabwidget_quality_duration_maximum_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_duration_line.addWidget(self.global_panel_tabwidget_quality_duration_maximum_label)

    self.global_panel_tabwidget_quality_duration_line.addStretch()

    self.global_panel_tabwidget_quality_duration_vbox.addLayout(self.global_panel_tabwidget_quality_duration_line)

    self.global_panel_tabwidget_quality_enable_groupbox.layout().addLayout(self.global_panel_tabwidget_quality_duration_vbox)

    self.global_panel_tabwidget_quality_lines_vbox = QVBoxLayout()
    self.global_panel_tabwidget_quality_lines_vbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_lines_vbox.setSpacing(2)

    self.global_panel_tabwidget_quality_lines_label = QLabel()
    self.global_panel_tabwidget_quality_lines_label.setProperty('class', 'widget_label')
    self.global_panel_tabwidget_quality_lines_vbox.addWidget(self.global_panel_tabwidget_quality_lines_label, 0, Qt.AlignLeft)

    self.global_panel_tabwidget_quality_lines_line = QHBoxLayout()
    self.global_panel_tabwidget_quality_lines_line.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_lines_line.setSpacing(5)

    self.global_panel_tabwidget_quality_lines_maximum = QSpinBox()
    self.global_panel_tabwidget_quality_lines_maximum.setMinimum(1)
    self.global_panel_tabwidget_quality_lines_maximum.setMaximum(10)
    self.global_panel_tabwidget_quality_lines_maximum.setValue(2)
    self.global_panel_tabwidget_quality_lines_maximum.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_lines_line.addWidget(self.global_panel_tabwidget_quality_lines_maximum)

    self.global_panel_tabwidget_quality_lines_maximum_label = QLabel()
    self.global_panel_tabwidget_quality_lines_maximum_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_lines_line.addWidget(self.global_panel_tabwidget_quality_lines_maximum_label)

    self.global_panel_tabwidget_quality_lines_maximumcharacters = QSpinBox()
    self.global_panel_tabwidget_quality_lines_maximumcharacters.setMinimum(1)
    self.global_panel_tabwidget_quality_lines_maximumcharacters.setMaximum(999)
    self.global_panel_tabwidget_quality_lines_maximumcharacters.setValue(42)
    self.global_panel_tabwidget_quality_lines_maximumcharacters.editingFinished.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_lines_line.addWidget(self.global_panel_tabwidget_quality_lines_maximumcharacters)

    self.global_panel_tabwidget_quality_lines_maximumcharacters_label = QLabel()
    self.global_panel_tabwidget_quality_lines_maximumcharacters_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_lines_line.addWidget(self.global_panel_tabwidget_quality_lines_maximumcharacters_label)

    self.global_panel_tabwidget_quality_lines_line.addStretch()

    self.global_panel_tabwidget_quality_lines_vbox.addLayout(self.global_panel_tabwidget_quality_lines_line)

    self.global_panel_tabwidget_quality_lines_vbox.addSpacing(5)

    self.global_panel_tabwidget_quality_prefer_compact_checkbox = QCheckBox()
    self.global_panel_tabwidget_quality_prefer_compact_checkbox.clicked.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_lines_vbox.addWidget(self.global_panel_tabwidget_quality_prefer_compact_checkbox)

    self.global_panel_tabwidget_quality_lines_vbox.addSpacing(5)

    self.global_panel_tabwidget_quality_balanceratio_checkbox = QCheckBox()
    self.global_panel_tabwidget_quality_balanceratio_checkbox.clicked.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_lines_vbox.addWidget(self.global_panel_tabwidget_quality_balanceratio_checkbox)

    self.global_panel_tabwidget_quality_lines_vbox.addSpacing(5)

    self.global_panel_tabwidget_quality_balanceratio_hbox = QHBoxLayout()
    self.global_panel_tabwidget_quality_balanceratio_hbox.setContentsMargins(0, 0, 0, 0)
    self.global_panel_tabwidget_quality_balanceratio_hbox.setSpacing(5)

    self.global_panel_tabwidget_quality_balanceratio_slider = QSlider(orientation=Qt.Horizontal)
    self.global_panel_tabwidget_quality_balanceratio_slider.setObjectName('global_panel_tabwidget_quality_balanceratio_slider')
    self.global_panel_tabwidget_quality_balanceratio_slider.setMinimum(0)
    self.global_panel_tabwidget_quality_balanceratio_slider.setMaximum(100)
    self.global_panel_tabwidget_quality_balanceratio_slider.setValue(50)
    self.global_panel_tabwidget_quality_balanceratio_slider.setMaximumWidth(200)
    self.global_panel_tabwidget_quality_balanceratio_slider.sliderReleased.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_balanceratio_hbox.addWidget(self.global_panel_tabwidget_quality_balanceratio_slider)

    self.global_panel_tabwidget_quality_balanceratio_slider_label = QLabel()
    self.global_panel_tabwidget_quality_balanceratio_slider_label.setProperty('class', 'units_label')
    self.global_panel_tabwidget_quality_balanceratio_hbox.addWidget(self.global_panel_tabwidget_quality_balanceratio_slider_label)

    self.global_panel_tabwidget_quality_balanceratio_hbox.addStretch()

    self.global_panel_tabwidget_quality_lines_vbox.addLayout(self.global_panel_tabwidget_quality_balanceratio_hbox)

    self.global_panel_tabwidget_quality_enable_groupbox.layout().addLayout(self.global_panel_tabwidget_quality_lines_vbox)

    self.left_panel_qualitycheck_break_text_when_sending_text_to_adjacent_checkbox = QCheckBox()
    self.left_panel_qualitycheck_break_text_when_sending_text_to_adjacent_checkbox.clicked.connect(lambda: save_quality_settings(self))
    self.global_panel_tabwidget_quality_lines_vbox.addWidget(self.left_panel_qualitycheck_break_text_when_sending_text_to_adjacent_checkbox)

    self.left_panel_qualitycheck_panel_widget.layout().addWidget(self.global_panel_tabwidget_quality_enable_groupbox)

    self.left_panel_qualitycheck_panel_widget.layout().addStretch()

    
def show(self):
    update(self)


def update(self):
    self.global_panel_tabwidget_show_statistics_checkbox.setChecked(session.CONFIG['quality_check'].get('show_statistics', False))
    self.global_panel_tabwidget_quality_enable_groupbox.setChecked(session.CONFIG['quality_check'].get('enabled', False))
    self.global_panel_tabwidget_quality_readingspeed_cps.setValue(session.CONFIG['quality_check'].get('reading_speed_cps', 21))
    self.global_panel_tabwidget_quality_readingspeed_wpm.setValue(session.CONFIG['quality_check'].get('reading_speed_wpm', 140))
    self.global_panel_tabwidget_quality_duration_minimum.setValue(session.CONFIG['quality_check'].get('minimum_duration', .7))
    self.global_panel_tabwidget_quality_duration_maximum.setValue(session.CONFIG['quality_check'].get('maximum_duration', 7))
    self.global_panel_tabwidget_quality_lines_maximum.setValue(session.CONFIG['quality_check'].get('maximum_lines', 2))
    self.global_panel_tabwidget_quality_lines_maximumcharacters.setValue(session.CONFIG['quality_check'].get('maximum_characters_per_line', 42))
    self.global_panel_tabwidget_quality_prefer_compact_checkbox.setChecked(session.CONFIG['quality_check'].get('prefer_compact', False))
    self.global_panel_tabwidget_quality_balanceratio_checkbox.setChecked(session.CONFIG['quality_check'].get('balance_ratio_enabled', False))
    self.global_panel_tabwidget_quality_balanceratio_slider.setValue(session.CONFIG['quality_check'].get('balance_ratio', 50))
    self.global_panel_tabwidget_quality_balanceratio_slider_label.setText('Ratio ({p}% the shortest should be of the largest)'.format(p=session.CONFIG['quality_check'].get('balance_ratio', 50)))

    
def hide(self):
    pass

    
def translate(self):    
    self.global_panel_tabwidget_show_statistics_checkbox.setText(_('qualitycontrol.show_statistics'))
    self.global_panel_tabwidget_quality_enable_groupbox.setTitle(_('qualitycontrol.quality_check'))
    self.global_panel_tabwidget_quality_readingspeed_label.setText(_('qualitycontrol.reading_speed'))
    self.global_panel_tabwidget_quality_readingspeed_cps_label.setText(_('qualitycontrol.characters_per_second'))
    self.global_panel_tabwidget_quality_readingspeed_wpm_label.setText(_('qualitycontrol.words_per_minute'))
    self.global_panel_tabwidget_quality_duration_label.setText(_('qualitycontrol.subtitle_duration'))
    self.global_panel_tabwidget_quality_duration_minimum_label.setText(_('qualitycontrol.minimum_in_seconds'))
    self.global_panel_tabwidget_quality_duration_maximum_label.setText(_('qualitycontrol.maximum_in_seconds'))
    self.global_panel_tabwidget_quality_lines_label.setText(_('units.lines'))
    self.global_panel_tabwidget_quality_lines_maximum_label.setText(_('units.maximum'))
    self.global_panel_tabwidget_quality_lines_maximumcharacters_label.setText(_('qualitycontrol.maximum_characters_per_line'))
    self.global_panel_tabwidget_quality_prefer_compact_checkbox.setText(_('qualitycontrol.prefer_compact_subtitles'))
    self.global_panel_tabwidget_quality_balanceratio_checkbox.setText(_('qualitycontrol.balance_line_length'))
    self.left_panel_qualitycheck_break_text_when_sending_text_to_adjacent_checkbox.setText(_('qualitycontrol.break_text_when_sending_text_to_adjacent'))



def save_quality_settings(self):
    session.CONFIG['quality_check']['show_statistics'] = self.global_panel_tabwidget_show_statistics_checkbox.isChecked()
    session.CONFIG['quality_check']['enabled'] = self.global_panel_tabwidget_quality_enable_groupbox.isChecked()
    session.CONFIG['quality_check']['reading_speed_cps'] = self.global_panel_tabwidget_quality_readingspeed_cps.value()
    session.CONFIG['quality_check']['reading_speed_wpm'] = self.global_panel_tabwidget_quality_readingspeed_wpm.value()
    session.CONFIG['quality_check']['minimum_duration'] = self.global_panel_tabwidget_quality_duration_minimum.value()
    session.CONFIG['quality_check']['maximum_duration'] = self.global_panel_tabwidget_quality_duration_maximum.value()
    session.CONFIG['quality_check']['maximum_lines'] = self.global_panel_tabwidget_quality_lines_maximum.value()
    session.CONFIG['quality_check']['maximum_characters_per_line'] = self.global_panel_tabwidget_quality_lines_maximumcharacters.value()
    session.CONFIG['quality_check']['prefer_compact'] = self.global_panel_tabwidget_quality_prefer_compact_checkbox.isChecked()
    session.CONFIG['quality_check']['balance_ratio_enabled'] = self.global_panel_tabwidget_quality_balanceratio_checkbox.isChecked()
    session.CONFIG['quality_check']['balance_ratio'] = self.global_panel_tabwidget_quality_balanceratio_slider.value()
    session.CONFIG['quality_check']['break_text_when_sending_text_to_adjacent'] = self.left_panel_qualitycheck_break_text_when_sending_text_to_adjacent_checkbox.isChecked()
    
