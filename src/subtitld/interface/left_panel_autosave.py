import os
import datetime
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QCheckBox, QPushButton, QComboBox, QGroupBox, QTabWidget, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QLineEdit, QSizePolicy, QTableWidgetItem, QColorDialog, QListWidget, QListWidgetItem
from PySide6.QtCore import Qt, QThread, Signal, QSize, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QFontDatabase, QBrush

from subtitld.interface import left_panel
from subtitld.interface import utils as interface_utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import history
from subtitld.modules import utils
from subtitld.modules import shortcuts
from subtitld.modules import subtitles


def load(self):
    tab_name = 'autosave'
    
    left_panel_autosave_panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )

    left_panel_autosave_panel_scroll = QScrollArea()
    left_panel_autosave_panel_scroll.setObjectName('left_panel_autosave_panel_scroll')
    left_panel_autosave_panel_scroll.setWidgetResizable(True)
    left_panel_autosave_panel_scroll.setFrameShape(QScrollArea.NoFrame)
    left_panel_autosave_panel.layout().addWidget(left_panel_autosave_panel_scroll)

    left_panel_autosave_panel_widget = QWidget()
    left_panel_autosave_panel_widget.setProperty('class', 'transparent_panel')
    left_panel_autosave_panel_widget.setObjectName('left_panel_autosave_panel_widget')
    left_panel_autosave_panel_widget.setLayout(QVBoxLayout())
    left_panel_autosave_panel_widget.layout().setContentsMargins(0, 0, 0, 0)
    left_panel_autosave_panel_scroll.setWidget(left_panel_autosave_panel_widget)

    self.panel_autosave_backup_enabled_checkbox = QCheckBox()
    self.panel_autosave_backup_enabled_checkbox.clicked.connect(lambda: panel_autosave_backup_enabled_checkbox_clicked(self))
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_enabled_checkbox)

    self.panel_autosave_backup_interval_row = QWidget()
    self.panel_autosave_backup_interval_row.setLayout(QHBoxLayout())
    self.panel_autosave_backup_interval_row.layout().setContentsMargins(0, 0, 0, 0)
    self.panel_autosave_backup_interval_row.layout().setSpacing(5)
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_interval_row)

    self.panel_autosave_backup_interval_label = QLabel()
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_label, 0, Qt.AlignLeft)

    self.panel_autosave_backup_interval_spinbox = QSpinBox()
    self.panel_autosave_backup_interval_spinbox.setMinimum(1)
    self.panel_autosave_backup_interval_spinbox.setMaximum(1000)
    self.panel_autosave_backup_interval_spinbox.valueChanged.connect(lambda: panel_autosave_backup_interval_spinbox_changed(self))
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_spinbox, 0, Qt.AlignLeft)

    self.panel_autosave_backup_interval_seconds_label = QLabel()
    self.panel_autosave_backup_interval_seconds_label.setProperty('class', 'units_label')
    self.panel_autosave_backup_interval_row.layout().addWidget(self.panel_autosave_backup_interval_seconds_label, 0, Qt.AlignLeft)
    self.panel_autosave_backup_interval_row.layout().addStretch()

    self.panel_autosave_original_enabled_checkbox = QCheckBox()
    self.panel_autosave_original_enabled_checkbox.clicked.connect(lambda: panel_autosave_original_enabled_checkbox_clicked(self))
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_original_enabled_checkbox)

    self.panel_autosave_original_interval_row = QWidget()
    self.panel_autosave_original_interval_row.setLayout(QHBoxLayout())
    self.panel_autosave_original_interval_row.layout().setContentsMargins(0, 0, 0, 0)
    self.panel_autosave_original_interval_row.layout().setSpacing(5)
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_original_interval_row)

    self.panel_autosave_original_interval_label = QLabel()
    self.panel_autosave_original_interval_row.layout().addWidget(self.panel_autosave_original_interval_label, 0, Qt.AlignLeft)

    self.panel_autosave_original_interval_spinbox = QSpinBox()
    self.panel_autosave_original_interval_spinbox.setMinimum(1)
    self.panel_autosave_original_interval_spinbox.setMaximum(1000)
    self.panel_autosave_original_interval_spinbox.valueChanged.connect(lambda: panel_autosave_original_interval_spinbox_changed(self))
    self.panel_autosave_original_interval_row.layout().addWidget(self.panel_autosave_original_interval_spinbox, 0, Qt.AlignLeft)

    self.panel_autosave_original_interval_seconds_label = QLabel()
    self.panel_autosave_original_interval_seconds_label.setProperty('class', 'units_label')
    self.panel_autosave_original_interval_row.layout().addWidget(self.panel_autosave_original_interval_seconds_label, 0, Qt.AlignLeft)
    self.panel_autosave_original_interval_row.layout().addStretch()

    self.panel_autosave_backup_max_count_row = QWidget()
    self.panel_autosave_backup_max_count_row.setLayout(QHBoxLayout())
    self.panel_autosave_backup_max_count_row.layout().setContentsMargins(0, 0, 0, 0)
    self.panel_autosave_backup_max_count_row.layout().setSpacing(5)
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_max_count_row)

    self.panel_autosave_backup_max_count_label = QLabel()
    self.panel_autosave_backup_max_count_row.layout().addWidget(self.panel_autosave_backup_max_count_label, 0, Qt.AlignLeft)

    self.panel_autosave_backup_max_count_spinbox = QSpinBox()
    self.panel_autosave_backup_max_count_spinbox.setMinimum(1)
    self.panel_autosave_backup_max_count_spinbox.setMaximum(500)
    self.panel_autosave_backup_max_count_spinbox.valueChanged.connect(lambda: panel_autosave_backup_max_count_spinbox_changed(self))
    self.panel_autosave_backup_max_count_row.layout().addWidget(self.panel_autosave_backup_max_count_spinbox, 0, Qt.AlignLeft)
    self.panel_autosave_backup_max_count_row.layout().addStretch()

    self.panel_autosave_status_last_backup_label = QLabel()
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_status_last_backup_label)

    self.panel_autosave_status_last_original_label = QLabel()
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_status_last_original_label)

    self.panel_autosave_backup_now_button = QPushButton()
    self.panel_autosave_backup_now_button.clicked.connect(lambda: panel_autosave_backup_now_clicked(self))
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_now_button, 0, Qt.AlignLeft)

    self.panel_autosave_backup_listwidget_label = QLabel()
    self.panel_autosave_backup_listwidget_label.setProperty('class', 'widget_label')
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_listwidget_label)

    self.panel_autosave_backup_listwidget = QListWidget()
    self.panel_autosave_backup_listwidget.setObjectName('panel_autosave_backup_listwidget')
    self.panel_autosave_backup_listwidget.itemDoubleClicked.connect(lambda item: panel_autosave_backup_listwidget_item_double_clicked(self, item))
    left_panel_autosave_panel_widget.layout().addWidget(self.panel_autosave_backup_listwidget, 1)

    session._autosave_status_callbacks.append(lambda: update(self))

    left_panel_autosave_panel_widget.layout().addStretch()


    update(self)



def show(self):
    update(self)


def update(self):
    backup_enabled = session.CONFIG.get('autosave', {}).get('backup_enabled', True)
    original_enabled = session.CONFIG.get('autosave', {}).get('original_enabled', True)
    project_is_usfx = bool(session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'))

    self.panel_autosave_backup_enabled_checkbox.setChecked(backup_enabled)
    if backup_enabled and not self.autosave_backup_timer.isActive():
        self.autosave_backup_timer.start()
    elif not backup_enabled and self.autosave_backup_timer.isActive():
        self.autosave_backup_timer.stop()

    self.panel_autosave_backup_interval_row.setEnabled(backup_enabled)
    self.panel_autosave_backup_interval_spinbox.setValue(session.CONFIG.get('autosave', {}).get('backup_interval', 300000) / (60 * 1000))

    self.panel_autosave_backup_max_count_row.setEnabled(backup_enabled)
    self.panel_autosave_backup_max_count_spinbox.setValue(session.CONFIG.get('autosave', {}).get('backup_max_count', 20))

    self.panel_autosave_original_enabled_checkbox.setEnabled(project_is_usfx)
    self.panel_autosave_original_enabled_checkbox.setToolTip('' if project_is_usfx else _('autosave_panel.requires_usfx_tooltip'))
    self.panel_autosave_original_enabled_checkbox.setChecked(original_enabled and project_is_usfx)
    should_run_original = original_enabled and project_is_usfx
    if should_run_original and not self.autosave_original_timer.isActive():
        self.autosave_original_timer.start()
    elif not should_run_original and self.autosave_original_timer.isActive():
        self.autosave_original_timer.stop()

    self.panel_autosave_original_interval_row.setEnabled(should_run_original)
    self.panel_autosave_original_interval_spinbox.setValue(session.CONFIG.get('autosave', {}).get('original_interval', 300000) / (60 * 1000))

    self.panel_autosave_backup_now_button.setEnabled(bool(session.SUBTITLE))

    last_backup = session.AUTOSAVE_LAST_BACKUP.strftime('%Y-%m-%d %H:%M:%S') if session.AUTOSAVE_LAST_BACKUP else _('autosave_panel.never')
    last_original = session.AUTOSAVE_LAST_ORIGINAL.strftime('%Y-%m-%d %H:%M:%S') if session.AUTOSAVE_LAST_ORIGINAL else _('autosave_panel.never')
    self.panel_autosave_status_last_backup_label.setText(_('autosave_panel.last_backup').format(time=last_backup))
    self.panel_autosave_status_last_original_label.setText(_('autosave_panel.last_original').format(time=last_original))

    _refresh_backup_list(self)


def _refresh_backup_list(self):
    """Populate the autosave panel's list widget with the available backup
    files, sorted from newest to oldest."""
    self.panel_autosave_backup_listwidget.clear()
    backup_dir = str(session.PATH_SUBTITLD_DATA_BACKUP)
    try:
        entries = []
        for fname in os.listdir(backup_dir):
            if not fname.lower().endswith('.usfx'):
                continue
            full = os.path.join(backup_dir, fname)
            if not os.path.isfile(full):
                continue
            entries.append((full, os.path.getmtime(full), fname))
    except OSError:
        return
    entries.sort(key=lambda item: item[1], reverse=True)
    for full_path, mtime, fname in entries:
        when = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        item = QListWidgetItem(f'{fname}\n{when}')
        item.setData(Qt.UserRole, full_path)
        self.panel_autosave_backup_listwidget.addItem(item)

def hide(self):
    pass


def panel_autosave_original_enabled_checkbox_clicked(self):
    session.CONFIG['autosave']['original_enabled'] = self.panel_autosave_original_enabled_checkbox.isChecked()
    update(self)

def panel_autosave_original_interval_spinbox_changed(self):
    session.CONFIG['autosave']['original_interval'] = self.panel_autosave_original_interval_spinbox.value() * 60 * 1000
    update(self)


def panel_autosave_backup_enabled_checkbox_clicked(self):
    session.CONFIG['autosave']['backup_enabled'] = self.panel_autosave_backup_enabled_checkbox.isChecked()
    update(self)

def panel_autosave_backup_interval_spinbox_changed(self):
    session.CONFIG['autosave']['backup_interval'] = self.panel_autosave_backup_interval_spinbox.value() * 60 * 1000
    update(self)


def panel_autosave_backup_max_count_spinbox_changed(self):
    session.CONFIG['autosave']['backup_max_count'] = self.panel_autosave_backup_max_count_spinbox.value()


def panel_autosave_backup_now_clicked(self):
    file_io.autosave_backup_timer_timeout(force=True)
    update(self)


def _open_backup_into_session(self, backup_path):
    """Replace the active project with the contents of `backup_path` (a USFX
    bundle from the backup folder). Reuses the existing USFX loader so the
    embedded video reference and dub clips are resolved the same way as
    opening a project from the start screen."""
    window = self.window()

    # Reset session containers so segments/speakers from the previous project
    # don't leak in.
    history.history_clear()
    session.SUBTITLE['segments'] = []
    session.SUBTITLE['selected'] = None
    session.SPEAKERS.clear()
    session.SUBTITLE['filepath'] = backup_path
    session.set_unsaved(False)

    previous_video = session.VIDEO.get('filepath') if isinstance(session.VIDEO, dict) else None
    # The USFX loader only fills `session.VIDEO['filepath']` from the bundle's
    # manifest when no path is currently set (so it doesn't clobber a user
    # choice). For a backup-open we explicitly want the backup's video, so
    # wipe the path first to force the loader to re-resolve from the manifest.
    if isinstance(session.VIDEO, dict):
        session.VIDEO.pop('filepath', None)
    else:
        session.VIDEO = {}

    segments, format_to_save = file_io.process_subtitles_file(backup_path)
    session.SUBTITLE['segments'] = segments
    session.CONFIG['format_to_save'] = format_to_save
    for name in {segment.get('speaker', 'A') for segment in segments}:
        session.SPEAKERS.setdefault(name, {})

    new_video = session.VIDEO.get('filepath') if isinstance(session.VIDEO, dict) else None
    if not new_video:
        new_video = previous_video
        if new_video:
            session.VIDEO['filepath'] = new_video
    if new_video and new_video != previous_video:
        window.preview_panel_player.loadfile(new_video)
        session.VIDEO = file_io.process_video_file(new_video)

    if hasattr(window, 'preview_panel_player') and hasattr(window.preview_panel_player, '_audio_device'):
        window.preview_panel_player._audio_device.sync_subtitle_dubs(segments)

    if hasattr(window, 'timeline_widget'):
        # The timeline's geometry and `width_proportion` are derived from
        # `session.VIDEO['duration']`. If duration changed with the new
        # video, resize the widget so click-to-seek math (which divides by
        # widget.width()) stays consistent with the new duration.
        window.timeline_widget.update_size()
        window.timeline_widget.load_waveform()
        window.timeline_widget.update()
    from subtitld.interface import left_panel as _left_panel
    from subtitld.interface import top_bar as _top_bar
    _left_panel.update(window)
    _top_bar.update(window)


def panel_autosave_backup_listwidget_item_double_clicked(self, item):
    backup_path = item.data(Qt.UserRole)
    if not backup_path or not os.path.isfile(backup_path):
        return

    if not session.UNSAVED:
        _open_backup_into_session(self, backup_path)
        return

    dialog = interface_utils.SimpleDialog(self.window(), title=_('autosave_panel.unsaved_changes_title'))
    dialog.content.layout().addWidget(QLabel(_('autosave_panel.unsaved_changes_text')))
    dialog.accept_button.setText(_('autosave_panel.save_and_open'))
    dialog.reject_button.setText(_('autosave_panel.cancel'))

    discard_button = QPushButton(_('autosave_panel.discard_and_open'))
    discard_button.setProperty('class', 'danger')
    dialog.accept_button.parent().layout().insertWidget(1, discard_button)

    chosen = {'action': 'cancel'}

    def _save():
        chosen['action'] = 'save'
        dialog.accept()

    def _discard():
        chosen['action'] = 'discard'
        dialog.accept()

    try:
        dialog.accept_button.clicked.disconnect()
    except Exception:
        pass
    dialog.accept_button.clicked.connect(_save)
    discard_button.clicked.connect(_discard)

    dialog.exec()

    if chosen['action'] == 'cancel':
        return
    if chosen['action'] == 'discard':
        _open_backup_into_session(self, backup_path)
        return

    # action == 'save': flush the current project first, then open the backup
    # only after the save thread reports success.
    current_filepath = session.SUBTITLE.get('filepath') or ''
    if not current_filepath.lower().endswith('.usfx'):
        from subtitld.interface import top_bar as _top_bar
        _top_bar.toppanel_save_button_clicked(self.window())
        return

    def _on_done(_path, success, _error):
        if success:
            _open_backup_into_session(self, backup_path)

    file_io.save_file_async(
        current_filepath,
        'USFX',
        session.CONFIG['selected_language'],
        on_done=_on_done,
        parent=self.window(),
    )


def translate(self):
    self.panel_autosave_backup_enabled_checkbox.setText(_('autosave_panel.autosave_backup_enable'))
    self.panel_autosave_backup_interval_label.setText(_('autosave_panel.autosave_backup_interval'))
    self.panel_autosave_backup_interval_seconds_label.setText(_('units.minutes'))
    self.panel_autosave_original_enabled_checkbox.setText(_('autosave_panel.autosave_original_enable'))
    self.panel_autosave_original_interval_label.setText(_('autosave_panel.autosave_original_interval'))
    self.panel_autosave_original_interval_seconds_label.setText(_('units.minutes'))
    self.panel_autosave_backup_max_count_label.setText(_('autosave_panel.backup_max_count'))
    self.panel_autosave_backup_now_button.setText(_('autosave_panel.backup_now'))
    self.panel_autosave_backup_listwidget_label.setText(_('autosave_panel.backup_list'))


    
