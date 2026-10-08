"""Left-panel "Autosave" tab.

Two cards set the two kinds of automatic saving — copies in Subtitld's
backup folder, and saving the project file itself — each with when it last
ran. Under them, the backups: grouped by day, newest first, for this project
or for all of them. A row opens its backup (double-click, or the OPEN button
that shows on hover); its menu also shows it in the folder.
"""

import os
import re
import datetime

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel, QSpinBox,
                               QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle, QMenu, QAbstractItemView)
from PySide6.QtCore import Qt, QSize, QRectF, QEvent, QTimer, QUrl, QDate, QLocale
from PySide6.QtGui import QColor, QPainter, QFont, QFontMetrics, QDesktopServices

import i18n

from subtitld.interface import left_panel
from subtitld.interface import utils as interface_utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import file_io
from subtitld.modules import history

# Backups are written as "<project>_<YYYYMMDDHHMMSS>.usfx".
_BACKUP_NAME = re.compile(r'^(?P<project>.*)_(?P<stamp>\d{14})\.usfx$', re.IGNORECASE)

# Item data roles for the list.
_ROLE_KIND = Qt.UserRole          # 'header' | 'backup'
_ROLE_PATH = Qt.UserRole + 1
_ROLE_INFO = Qt.UserRole + 2      # dict: when, project, size, latest / title, count


def load(self):
    tab_name = 'autosave'

    panel = left_panel.left_panel(
        parent=self,
        tab_name=tab_name,
        update_callback=update,
        translate_callback=translate
    )
    column = panel.layout()
    column.setSpacing(8)

    # Two checkable groups, as "Background box" in the Interface panel: the
    # title is the switch, and an unchecked group dims what is in it.

    # -- automatic backups ---------------------------------------------------
    self.panel_autosave_backup_group = _group()
    self.panel_autosave_backup_group.clicked.connect(lambda: panel_autosave_backup_enabled_checkbox_clicked(self))
    body = self.panel_autosave_backup_group.layout()
    self.panel_autosave_backup_description = QLabel(objectName='autosave_card_description')
    self.panel_autosave_backup_description.setWordWrap(True)
    body.addWidget(self.panel_autosave_backup_description)
    fields = QHBoxLayout()
    fields.setSpacing(20)
    (self.panel_autosave_backup_interval_label, self.panel_autosave_backup_interval_spinbox,
     self.panel_autosave_backup_interval_unit) = _field(fields, 1, 1000)
    self.panel_autosave_backup_interval_spinbox.valueChanged.connect(lambda: panel_autosave_backup_interval_spinbox_changed(self))
    (self.panel_autosave_backup_max_count_label, self.panel_autosave_backup_max_count_spinbox,
     self.panel_autosave_backup_max_count_unit) = _field(fields, 1, 500)
    self.panel_autosave_backup_max_count_spinbox.valueChanged.connect(lambda: panel_autosave_backup_max_count_spinbox_changed(self))
    fields.addStretch(1)
    body.addLayout(fields)
    # When it last ran, and the button to run it now (also when automatic
    # backups are off).
    foot = QHBoxLayout()
    foot.setContentsMargins(0, 4, 0, 0)
    self.panel_autosave_status_last_backup_label = QLabel(objectName='autosave_card_last')
    foot.addWidget(self.panel_autosave_status_last_backup_label, 1)
    self.panel_autosave_backup_now_button = QPushButton(objectName='autosave_backup_now_button')
    self.panel_autosave_backup_now_button.setCursor(Qt.PointingHandCursor)
    self.panel_autosave_backup_now_button.clicked.connect(lambda: panel_autosave_backup_now_clicked(self))
    foot.addWidget(self.panel_autosave_backup_now_button)
    body.addLayout(foot)
    column.addWidget(self.panel_autosave_backup_group)

    # -- the project file itself -----------------------------------------------
    self.panel_autosave_original_group = _group()
    self.panel_autosave_original_group.clicked.connect(lambda: panel_autosave_original_enabled_checkbox_clicked(self))
    body = self.panel_autosave_original_group.layout()
    self.panel_autosave_original_description = QLabel(objectName='autosave_card_description')
    self.panel_autosave_original_description.setWordWrap(True)
    body.addWidget(self.panel_autosave_original_description)
    fields = QHBoxLayout()
    fields.setSpacing(20)
    (self.panel_autosave_original_interval_label, self.panel_autosave_original_interval_spinbox,
     self.panel_autosave_original_interval_unit) = _field(fields, 1, 1000)
    self.panel_autosave_original_interval_spinbox.valueChanged.connect(lambda: panel_autosave_original_interval_spinbox_changed(self))
    fields.addStretch(1)
    body.addLayout(fields)
    self.panel_autosave_status_last_original_label = QLabel(objectName='autosave_card_last')
    self.panel_autosave_status_last_original_label.setContentsMargins(0, 4, 0, 0)
    body.addWidget(self.panel_autosave_status_last_original_label)
    column.addWidget(self.panel_autosave_original_group)

    # -- backups list --------------------------------------------------------
    column.addSpacing(4)
    list_head = QHBoxLayout()
    list_head.setSpacing(0)
    self.panel_autosave_backup_listwidget_label = QLabel()
    self.panel_autosave_backup_listwidget_label.setProperty('class', 'widget_label')
    list_head.addWidget(self.panel_autosave_backup_listwidget_label, 1)
    self.panel_autosave_filter_buttons = {}
    for position, key in (('first', 'project'), ('last', 'all')):
        button = QPushButton(objectName='autosave_filter_button')
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty('position', position)
        button.clicked.connect(lambda _c=False, k=key: _set_filter(self, k))
        list_head.addWidget(button)
        self.panel_autosave_filter_buttons[key] = button
    column.addLayout(list_head)

    self.panel_autosave_backup_listwidget = QListWidget()
    self.panel_autosave_backup_listwidget.setObjectName('panel_autosave_backup_listwidget')
    self.panel_autosave_backup_listwidget.setMouseTracking(True)
    self.panel_autosave_backup_listwidget.setSelectionMode(QAbstractItemView.SingleSelection)
    self.panel_autosave_backup_listwidget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    self.panel_autosave_backup_listwidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.panel_autosave_backup_listwidget.setContextMenuPolicy(Qt.CustomContextMenu)
    self.panel_autosave_backup_listwidget.customContextMenuRequested.connect(lambda pos: _backup_menu(self, pos))
    self.panel_autosave_backup_listwidget.itemDoubleClicked.connect(lambda item: panel_autosave_backup_listwidget_item_double_clicked(self, item))
    self.panel_autosave_backup_delegate = _BackupDelegate(self.panel_autosave_backup_listwidget,
                                                          lambda path: _request_open_backup(self, path))
    self.panel_autosave_backup_listwidget.setItemDelegate(self.panel_autosave_backup_delegate)
    column.addWidget(self.panel_autosave_backup_listwidget, 1)
    self.panel_autosave_empty_label = QLabel(objectName='autosave_empty_label')
    self.panel_autosave_empty_label.setWordWrap(True)
    self.panel_autosave_empty_label.setAlignment(Qt.AlignCenter)
    column.addWidget(self.panel_autosave_empty_label, 1)

    session._autosave_status_callbacks.append(lambda: update(self))

    # Relative times ("3 min ago") age: repaint them now and then while shown.
    self.panel_autosave_clock = QTimer(self)
    self.panel_autosave_clock.setInterval(30000)
    self.panel_autosave_clock.timeout.connect(lambda: _refresh_ages(self))
    self.panel_autosave_clock.start()

    update(self)


def _group():
    group = QGroupBox()
    group.setCheckable(True)
    body = QVBoxLayout(group)
    body.setContentsMargins(10, 10, 10, 10)
    body.setSpacing(8)
    return group


def _field(row, low, high):
    """A field as the app's groups lay them out: the label on top, the
    spinbox and its unit under it. Returns (label, spinbox, unit)."""
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    label = QLabel()
    label.setProperty('class', 'widget_label')
    column.addWidget(label, 0, Qt.AlignLeft)
    line = QHBoxLayout()
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(5)
    spinbox = QSpinBox()
    spinbox.setRange(low, high)
    line.addWidget(spinbox, 0, Qt.AlignLeft)
    unit = QLabel()
    unit.setProperty('class', 'units_label')
    line.addWidget(unit, 0, Qt.AlignLeft)
    column.addLayout(line)
    row.addLayout(column)
    return label, spinbox, unit


# --------------------------------------------------------------------------- #
# Times and sizes                                                              #
# --------------------------------------------------------------------------- #
def _ago(when, now=None):
    """`when` as "just now", "4 min ago", "3 h ago" or "2 d ago"."""
    seconds = max(0, int(((now or datetime.datetime.now()) - when).total_seconds()))
    if seconds < 45:
        return _('autosave_panel.just_now')
    if seconds < 3600:
        return _('autosave_panel.minutes_ago').format(count=max(1, round(seconds / 60)))
    if seconds < 86400:
        return _('autosave_panel.hours_ago').format(count=seconds // 3600)
    return _('autosave_panel.days_ago').format(count=seconds // 86400)


def _size(size):
    if size >= 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    if size >= 1024:
        return f'{size / 1024:.0f} KB'
    return f'{size} B'


def _day_title(day, today):
    if day == today:
        return _('autosave_panel.today')
    if day == today - datetime.timedelta(days=1):
        return _('autosave_panel.yesterday')
    locale = QLocale(str(i18n.get('locale') or 'en'))
    return locale.toString(QDate(day.year, day.month, day.day), 'd MMMM yyyy')


def _backups(stem=None):
    """[(path, when, project, size)] newest first; only `stem`'s backups when
    given — the same rule the pruning goes by."""
    backup_dir = str(session.PATH_SUBTITLD_DATA_BACKUP)
    found = []
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return found
    for name in names:
        if not name.lower().endswith('.usfx'):
            continue
        if stem is not None and not name.startswith(stem + '_'):
            continue
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path):
            continue
        match = _BACKUP_NAME.match(name)
        when = None
        project = name[:-5]
        if match:
            project = match.group('project')
            try:
                when = datetime.datetime.strptime(match.group('stamp'), '%Y%m%d%H%M%S')
            except ValueError:
                when = None
        try:
            if when is None:
                when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            size = os.path.getsize(path)
        except OSError:
            continue
        found.append((path, when, project, size))
    found.sort(key=lambda entry: entry[1], reverse=True)
    return found


# --------------------------------------------------------------------------- #
# The list                                                                     #
# --------------------------------------------------------------------------- #
class _BackupDelegate(QStyledItemDelegate):
    """Paints the day headers and the backup rows; a click on a row's OPEN
    button opens it."""

    ROW, HEADER = 46, 28

    def __init__(self, view, open_backup):
        super().__init__(view)
        self.view = view
        self.open_backup = open_backup
        self.show_project = False

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.HEADER if index.data(_ROLE_KIND) == 'header' else self.ROW)

    def _open_rect(self, rect):
        return QRectF(rect.right() - 62, rect.center().y() - 10, 52, 20)

    def _font(self, size, bold=False, mono=False):
        font = QFont('Ubuntu Mono' if mono else self.view.font().family())
        font.setPixelSize(size)
        font.setBold(bold)
        return font

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect)
        info = index.data(_ROLE_INFO) or {}
        if index.data(_ROLE_KIND) == 'header':
            painter.setFont(self._font(9, bold=True))
            painter.setPen(QColor('#b8cee0'))
            title = info.get('title', '').upper()
            metrics = QFontMetrics(painter.font())
            text_rect = QRectF(rect.left() + 10, rect.top() + 8, rect.width() - 20, rect.height() - 8)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
            painter.setPen(QColor(184, 206, 224, 110))
            count_x = text_rect.left() + metrics.horizontalAdvance(title) + 8
            painter.drawText(QRectF(count_x, text_rect.top(), 40, text_rect.height()), Qt.AlignLeft | Qt.AlignVCenter,
                             str(info.get('count', '')))
            painter.restore()
            return

        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#304251') if selected else QColor('#212c36'))
            painter.drawRoundedRect(rect.adjusted(4, 1, -4, -1), 3, 3)

        when = info['when']
        left = rect.left() + 12
        right_edge = rect.right() - (72 if hovered else 12)
        painter.setFont(self._font(15, mono=True))
        painter.setPen(QColor('#e8f1f8'))
        time_text = when.strftime('%H:%M:%S')
        time_width = QFontMetrics(painter.font()).horizontalAdvance(time_text)
        painter.drawText(QRectF(left, rect.top() + 6, time_width + 2, 20), Qt.AlignLeft | Qt.AlignVCenter, time_text)
        if info.get('today'):
            painter.setFont(self._font(11))
            painter.setPen(QColor(184, 206, 224, 150))
            painter.drawText(QRectF(left + time_width + 8, rect.top() + 6, right_edge - left - time_width - 8, 20),
                             Qt.AlignLeft | Qt.AlignVCenter, _ago(when))

        second = _size(info['size'])
        if self.show_project:
            second = f'{info["project"]}  ·  {second}'
        painter.setFont(self._font(11))
        painter.setPen(QColor(184, 206, 224, 120))
        metrics = QFontMetrics(painter.font())
        width = right_edge - left
        painter.drawText(QRectF(left, rect.top() + 25, width, 16), Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(second, Qt.ElideMiddle, int(width)))

        if hovered:
            button = self._open_rect(rect)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#c8dbe9'))
            painter.drawRoundedRect(button, 3, 3)
            painter.setFont(self._font(9, bold=True))
            painter.setPen(QColor('#304251'))
            painter.drawText(button, Qt.AlignCenter, _('autosave_panel.open').upper())
        elif info.get('latest'):
            badge = QRectF(rect.right() - 62, rect.top() + 9, 50, 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(95, 196, 138, 40))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setFont(self._font(8, bold=True))
            painter.setPen(QColor('#5fc48a'))
            painter.drawText(badge, Qt.AlignCenter, _('autosave_panel.latest').upper())
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (index.data(_ROLE_KIND) == 'backup' and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
                and self._open_rect(QRectF(option.rect)).contains(event.position())):
            self.open_backup(index.data(_ROLE_PATH))
            return True
        return super().editorEvent(event, model, option, index)


def _list_filter():
    value = session.CONFIG.setdefault('autosave', {}).get('list', 'project')
    return value if value in ('project', 'all') else 'project'


def _set_filter(self, key):
    session.CONFIG.setdefault('autosave', {})['list'] = key
    _refresh_backup_list(self)


def _refresh_backup_list(self):
    """Fill the list: day headers, then that day's backups, newest first."""
    listwidget = self.panel_autosave_backup_listwidget
    listwidget.clear()
    which = _list_filter()
    stem = file_io._autosave_filename_stem() or None
    if which == 'project' and stem is None:
        which = 'all'       # no project to filter by
    for key, button in self.panel_autosave_filter_buttons.items():
        button.setChecked(key == which)
    self.panel_autosave_filter_buttons['project'].setEnabled(stem is not None)
    self.panel_autosave_backup_delegate.show_project = which == 'all'

    entries = _backups(stem if which == 'project' else None)
    today = datetime.date.today()
    days = {}
    for entry in entries:
        days.setdefault(entry[1].date(), []).append(entry)
    for day in sorted(days, reverse=True):
        header = QListWidgetItem()
        header.setData(_ROLE_KIND, 'header')
        header.setData(_ROLE_INFO, {'title': _day_title(day, today), 'count': len(days[day])})
        header.setFlags(Qt.NoItemFlags)
        listwidget.addItem(header)
        for path, when, project, size in days[day]:
            item = QListWidgetItem()
            item.setData(_ROLE_KIND, 'backup')
            item.setData(_ROLE_PATH, path)
            item.setData(_ROLE_INFO, {'when': when, 'project': project, 'size': size, 'today': day == today,
                                      'latest': path == entries[0][0]})
            item.setToolTip(f'{project}\n{when.strftime("%Y-%m-%d %H:%M:%S")}\n{path}')
            listwidget.addItem(item)
    listwidget.setVisible(bool(entries))
    self.panel_autosave_empty_label.setVisible(not entries)
    self.panel_autosave_empty_label.setText(
        _('autosave_panel.no_backups_project') if which == 'project' else _('autosave_panel.no_backups'))


def _refresh_ages(self):
    if self.panel_autosave_backup_listwidget.isVisible():
        self.panel_autosave_backup_listwidget.viewport().update()
    _show_last_runs(self)


def _backup_menu(self, pos):
    item = self.panel_autosave_backup_listwidget.itemAt(pos)
    if item is None or item.data(_ROLE_KIND) != 'backup':
        return
    path = item.data(_ROLE_PATH)
    menu = QMenu(self.panel_autosave_backup_listwidget)
    menu.addAction(_('autosave_panel.open_backup'), lambda: _request_open_backup(self, path))
    menu.addAction(_('autosave_panel.show_in_folder'),
                   lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
    menu.exec(self.panel_autosave_backup_listwidget.viewport().mapToGlobal(pos))


# --------------------------------------------------------------------------- #
# Settings                                                                     #
# --------------------------------------------------------------------------- #
def _show_last_runs(self):
    for label, when in ((self.panel_autosave_status_last_backup_label, session.AUTOSAVE_LAST_BACKUP),
                        (self.panel_autosave_status_last_original_label, session.AUTOSAVE_LAST_ORIGINAL)):
        if when:
            label.setText(_('autosave_panel.last_run').format(time=_ago(when)))
            label.setToolTip(when.strftime('%Y-%m-%d %H:%M:%S'))
        else:
            label.setText(_('autosave_panel.last_run').format(time=_('autosave_panel.never')))
            label.setToolTip('')


def show(self):
    update(self)


def update(self):
    backup_enabled = session.CONFIG.get('autosave', {}).get('backup_enabled', True)
    original_enabled = session.CONFIG.get('autosave', {}).get('original_enabled', True)
    project_is_usfx = bool(session.SUBTITLE.get('filepath', '').lower().endswith('.usfx'))

    self.panel_autosave_backup_group.setChecked(backup_enabled)
    if backup_enabled and not self.autosave_backup_timer.isActive():
        self.autosave_backup_timer.start()
    elif not backup_enabled and self.autosave_backup_timer.isActive():
        self.autosave_backup_timer.stop()

    self.panel_autosave_backup_interval_spinbox.blockSignals(True)
    self.panel_autosave_backup_interval_spinbox.setValue(int(session.CONFIG.get('autosave', {}).get('backup_interval', 300000) / (60 * 1000)))
    self.panel_autosave_backup_interval_spinbox.blockSignals(False)
    self.panel_autosave_backup_max_count_spinbox.blockSignals(True)
    self.panel_autosave_backup_max_count_spinbox.setValue(int(session.CONFIG.get('autosave', {}).get('backup_max_count', 20)))
    self.panel_autosave_backup_max_count_spinbox.blockSignals(False)

    self.panel_autosave_original_group.setEnabled(project_is_usfx)
    self.panel_autosave_original_group.setChecked(original_enabled and project_is_usfx)
    should_run_original = original_enabled and project_is_usfx
    if should_run_original and not self.autosave_original_timer.isActive():
        self.autosave_original_timer.start()
    elif not should_run_original and self.autosave_original_timer.isActive():
        self.autosave_original_timer.stop()
    self.panel_autosave_original_interval_spinbox.blockSignals(True)
    self.panel_autosave_original_interval_spinbox.setValue(int(session.CONFIG.get('autosave', {}).get('original_interval', 300000) / (60 * 1000)))
    self.panel_autosave_original_interval_spinbox.blockSignals(False)
    # Not a USFX project: say why the option is off, instead of hiding it in
    # a tooltip on a disabled checkbox.
    self.panel_autosave_original_description.setText(
        _('autosave_panel.original_description') if project_is_usfx else _('autosave_panel.requires_usfx_tooltip'))
    self.panel_autosave_original_description.setProperty('warning', not project_is_usfx)
    self.panel_autosave_original_description.style().unpolish(self.panel_autosave_original_description)
    self.panel_autosave_original_description.style().polish(self.panel_autosave_original_description)

    # An unchecked group disables all it holds; backing up by hand and the
    # time of the last one stay available.
    self.panel_autosave_backup_now_button.setEnabled(bool(session.SUBTITLE))
    self.panel_autosave_status_last_backup_label.setEnabled(True)
    _show_last_runs(self)
    _refresh_backup_list(self)


def hide(self):
    pass


def panel_autosave_original_enabled_checkbox_clicked(self):
    session.CONFIG['autosave']['original_enabled'] = self.panel_autosave_original_group.isChecked()
    update(self)


def panel_autosave_original_interval_spinbox_changed(self):
    session.CONFIG['autosave']['original_interval'] = self.panel_autosave_original_interval_spinbox.value() * 60 * 1000
    update(self)


def panel_autosave_backup_enabled_checkbox_clicked(self):
    session.CONFIG['autosave']['backup_enabled'] = self.panel_autosave_backup_group.isChecked()
    update(self)


def panel_autosave_backup_interval_spinbox_changed(self):
    session.CONFIG['autosave']['backup_interval'] = self.panel_autosave_backup_interval_spinbox.value() * 60 * 1000
    update(self)


def panel_autosave_backup_max_count_spinbox_changed(self):
    session.CONFIG['autosave']['backup_max_count'] = self.panel_autosave_backup_max_count_spinbox.value()


def panel_autosave_backup_now_clicked(self):
    file_io.autosave_backup_timer_timeout(force=True)
    update(self)


# --------------------------------------------------------------------------- #
# Opening a backup                                                             #
# --------------------------------------------------------------------------- #
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
    if item.data(_ROLE_KIND) == 'backup':
        _request_open_backup(self, item.data(_ROLE_PATH))


def _request_open_backup(self, backup_path):
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
    self.panel_autosave_backup_group.setTitle(_('autosave_panel.autosave_backup_enable'))
    self.panel_autosave_backup_description.setText(_('autosave_panel.backup_description'))
    self.panel_autosave_backup_interval_label.setText(_('autosave_panel.interval'))
    self.panel_autosave_backup_interval_unit.setText(_('units.minutes'))
    self.panel_autosave_backup_max_count_label.setText(_('autosave_panel.keep_at_most'))
    self.panel_autosave_backup_max_count_unit.setText(_('autosave_panel.backups_unit'))
    self.panel_autosave_backup_now_button.setText(_('autosave_panel.backup_now').upper())
    self.panel_autosave_original_group.setTitle(_('autosave_panel.autosave_original_enable'))
    self.panel_autosave_original_interval_label.setText(_('autosave_panel.interval'))
    self.panel_autosave_original_interval_unit.setText(_('units.minutes'))
    self.panel_autosave_backup_listwidget_label.setText(_('autosave_panel.backup_list'))
    self.panel_autosave_filter_buttons['project'].setText(_('autosave_panel.filter_project').upper())
    self.panel_autosave_filter_buttons['all'].setText(_('autosave_panel.filter_all').upper())
    update(self)
