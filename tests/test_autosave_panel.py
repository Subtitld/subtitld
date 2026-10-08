"""Autosave panel: the backups list (grouped by day, filtered to the project,
opened from a row) and the two settings cards.

Backups are empty files in the scratch data folder; opening one is
recorded, not done.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, datetime
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget, QStyleOptionViewItem
from PySide6.QtCore import QTimer, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
app = QApplication([])
from subtitld.modules import session
from subtitld.interface import left_panel_autosave as lpa

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


folder = str(session.PATH_SUBTITLD_DATA_BACKUP)
os.makedirs(folder, exist_ok=True)
now = datetime.datetime.now().replace(microsecond=0)


def make(project, when, size=1000):
    path = os.path.join(folder, f'{project}_{when.strftime("%Y%m%d%H%M%S")}.usfx')
    with open(path, 'wb') as handle:
        handle.write(b'0' * size)
    return path


today_noon = now.replace(hour=12, minute=0, second=0)
if today_noon > now:          # keep "today" in the past
    today_noon = now - datetime.timedelta(seconds=30)
newest = make('talk', today_noon, 2_400_000)
make('talk', today_noon - datetime.timedelta(seconds=20))
make('talk', today_noon - datetime.timedelta(days=1))
make('talk', today_noon - datetime.timedelta(days=5))
make('other project', today_noon - datetime.timedelta(seconds=10))
with open(os.path.join(folder, 'notes.txt'), 'w') as handle:
    handle.write('not a backup')

print('reading the backup folder')
entries = lpa._backups('talk')
check('only this project, newest first', [e[0] for e in entries][0], newest)
check('four of them', len(entries), 4)
check('time from the name, project, size', (entries[0][1], entries[0][2], entries[0][3]), (today_noon, 'talk', 2_400_000))
check('every project when not filtered', len(lpa._backups()), 5)

print('times and sizes')
check('just now', lpa._ago(now - datetime.timedelta(seconds=10), now), lpa._('autosave_panel.just_now'))
check('minutes', lpa._ago(now - datetime.timedelta(minutes=7), now), lpa._('autosave_panel.minutes_ago').format(count=7))
check('hours', lpa._ago(now - datetime.timedelta(hours=3, minutes=5), now), lpa._('autosave_panel.hours_ago').format(count=3))
check('sizes', (lpa._size(2_400_000), lpa._size(5_000), lpa._size(12)), ('2.3 MB', '5 KB', '12 B'))
check('today and yesterday by name', (lpa._day_title(now.date(), now.date()),
                                       lpa._day_title(now.date() - datetime.timedelta(days=1), now.date())),
      (lpa._('autosave_panel.today'), lpa._('autosave_panel.yesterday')))


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(420, 900)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.autosave_backup_timer = QTimer(self)
        self.autosave_original_timer = QTimer(self)


session.CONFIG = {'autosave': {'backup_enabled': True, 'original_enabled': True, 'backup_interval': 300000,
                               'original_interval': 60000, 'backup_max_count': 20}}
session.SUBTITLE = {'filepath': '/projects/talk.srt', 'segments': []}
session.VIDEO = {}
session.AUTOSAVE_LAST_BACKUP = now - datetime.timedelta(minutes=4)
session.AUTOSAVE_LAST_ORIGINAL = None
host = Host()
lpa.load(host)
lpa.translate(host)
host.show()
app.processEvents()
listwidget = host.panel_autosave_backup_listwidget


def rows():
    out = []
    for i in range(listwidget.count()):
        item = listwidget.item(i)
        info = item.data(lpa._ROLE_INFO)
        if item.data(lpa._ROLE_KIND) == 'header':
            out.append(('header', info['title'], info['count']))
        else:
            out.append(('backup', info['project'], info['latest']))
    return out


print('the list: this project, grouped by day')
check('filter on this project', (host.panel_autosave_filter_buttons['project'].isChecked(),
                                 host.panel_autosave_backup_delegate.show_project), (True, False))
listed = rows()
check('a header per day, with its count', [r for r in listed if r[0] == 'header'][:2],
      [('header', lpa._('autosave_panel.today'), 2), ('header', lpa._('autosave_panel.yesterday'), 1)])
check('backups of this project only', {r[1] for r in listed if r[0] == 'backup'}, {'talk'})
check('the newest is marked', [r[2] for r in listed if r[0] == 'backup'], [True, False, False, False])
check('headers cannot be selected', bool(listwidget.item(0).flags() & Qt.ItemIsSelectable), False)

print('all projects')
host.panel_autosave_filter_buttons['all'].click()
check('every project, with its name shown', ({r[1] for r in rows() if r[0] == 'backup'},
                                             host.panel_autosave_backup_delegate.show_project),
      ({'talk', 'other project'}, True))
check('remembered', session.CONFIG['autosave']['list'], 'all')
host.panel_autosave_filter_buttons['project'].click()

print('opening a backup: the OPEN button and double-click')
opened = []
lpa._open_backup_into_session = lambda self, path: opened.append(path)
session.UNSAVED = False
item = listwidget.item(1)
option = QStyleOptionViewItem()
option.rect = listwidget.visualItemRect(item)
button = host.panel_autosave_backup_delegate._open_rect(option.rect.toRectF() if hasattr(option.rect, 'toRectF') else option.rect)
click = QMouseEvent(QEvent.MouseButtonRelease, button.center(), QPointF(), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
host.panel_autosave_backup_delegate.editorEvent(click, listwidget.model(), option, listwidget.indexFromItem(item))
check('OPEN opens it', opened, [newest])
listwidget.itemDoubleClicked.emit(item)
check('so does a double-click', opened, [newest, newest])
listwidget.itemDoubleClicked.emit(listwidget.item(0))
check('a day header opens nothing', len(opened), 2)

print('the settings cards')
check('last backup, relative', host.panel_autosave_status_last_backup_label.text(),
      lpa._('autosave_panel.last_run').format(time=lpa._('autosave_panel.minutes_ago').format(count=4)))
check('never saved in place', host.panel_autosave_status_last_original_label.text(),
      lpa._('autosave_panel.last_run').format(time=lpa._('autosave_panel.never')))
check('both are the app\'s checkable groups', (host.panel_autosave_backup_group.isCheckable(),
                                                   host.panel_autosave_original_group.isCheckable()), (True, True))
check('not a USFX project: in-place saving is off, and says why',
      (host.panel_autosave_original_group.isEnabled(), host.panel_autosave_original_description.text()),
      (False, lpa._('autosave_panel.requires_usfx_tooltip')))
host.panel_autosave_backup_interval_spinbox.setValue(10)
check('interval saved, in ms', session.CONFIG['autosave']['backup_interval'], 600000)
host.panel_autosave_backup_group.setChecked(False)      # as a click on its title does
host.panel_autosave_backup_group.clicked.emit()
check('turning backups off disables their settings and stops the timer',
      (session.CONFIG['autosave']['backup_enabled'], host.panel_autosave_backup_interval_spinbox.isEnabled(),
       host.autosave_backup_timer.isActive()), (False, False, False))
check('backing up by hand still works', host.panel_autosave_backup_now_button.isEnabled(), True)
host.panel_autosave_backup_group.setChecked(True)
host.panel_autosave_backup_group.clicked.emit()
check('and back on', (session.CONFIG['autosave']['backup_enabled'], host.panel_autosave_backup_interval_spinbox.isEnabled(),
                      host.autosave_backup_timer.isActive()), (True, True, True))
session.SUBTITLE['filepath'] = '/projects/talk.usfx'
lpa.update(host)
check('a USFX project can save in place', (host.panel_autosave_original_group.isEnabled(),
                                            host.autosave_original_timer.isActive()), (True, True))

print('no backups')
session.SUBTITLE['filepath'] = '/projects/brand new.usfx'
lpa.update(host)
check('says so instead of an empty list', (listwidget.isHidden(), host.panel_autosave_empty_label.isHidden(),
                                           host.panel_autosave_empty_label.text()),
      (True, False, lpa._('autosave_panel.no_backups_project')))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
