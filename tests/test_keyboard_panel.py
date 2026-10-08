"""Keyboard shortcuts panel: the list, the capture area and what a capture does.

Geometry is checked against the design handoff (row 35px, list well 420px,
keycaps 26/62/112px, …) because those numbers are the design, not decoration.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget
from PySide6.QtCore import QDir, Qt, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QKeyEvent, QAction
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, shortcuts
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
for _f in os.listdir(session.PATH_SUBTITLD_GRAPHICS):
    if _f.lower().endswith('.ttf'):
        QFontDatabase.addApplicationFont(os.path.join(session.PATH_SUBTITLD_GRAPHICS, _f))
app.setFont(QFont('Montserrat', 10))
app.setStyleSheet((session.PATH_SUBTITLD_GRAPHICS / 'stylesheet.qss').read_text())
from subtitld.interface import left_panel_keyboard as lpk

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


COMMANDS = [
    ('toggle_lock_current_subtitle', 'Toggle lock on current subtitle', ['L']),
    ('slice_current_subtitle', 'Slice current subtitle', ['/']),
    ('timeline_escape_action', 'Escape timeline actions', ['Escape']),
    ('show_find_replace_dialog', 'Open the Find & Replace dialog', ['Ctrl+H', 'Ctrl+F']),
]


class Host(QWidget):
    """Stands in for the main window: what the panel and shortcuts touch."""

    def __init__(self):
        super().__init__()
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)


class FakeConfig(dict):
    """session.CONFIG, minus the disk."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saves = 0

    def save(self):
        self.saves += 1


def fresh():
    shortcuts.shortcuts_dict.clear()
    shortcuts.default_shortcuts_dict.clear()
    shortcuts.shortcuts_dict.update({c: d for c, d, _k in COMMANDS})
    shortcuts.default_shortcuts_dict.update({c: list(k) for c, _d, k in COMMANDS})
    session.CONFIG = FakeConfig({'shortcuts': {c: list(k) for c, _d, k in COMMANDS}})
    host = Host()
    host.resize(762, 760)
    host.show()
    # A QAction per command, as shortcuts.load() leaves behind.
    host._shortcut_actions = {}
    for command_id, description, keys in COMMANDS:
        action = QAction(description, host)
        action.setShortcuts(keys)
        host.addAction(action)
        host._shortcut_actions[command_id] = action
    lpk.load(host)
    lpk.translate(host)
    host.left_panel_stackedwidgets.setCurrentWidget(host.left_panel_stackedwidgets.widget(0))
    app.processEvents()
    return host


def press(host, key, modifiers=Qt.NoModifier):
    event = QKeyEvent(QEvent.KeyPress, key, modifiers)
    return lpk.keyboard_panel_capture_keys(host, event)


print('the list shows every command with its keys')
host = fresh()
check('a row per command', len(host.keyboard_panel_rows), len(COMMANDS))
row = host.keyboard_panel_rows['show_find_replace_dialog']
caps = [w.text() for w in row.keys_widget.findChildren(lpk._Keycap)]
check('a keycap per key', caps, ['Ctrl', 'H'])
check("the command's other bindings are in the tooltip", row.toolTip(), 'Ctrl+H, Ctrl+F')

print('geometry follows the handoff')
escape_row = host.keyboard_panel_rows['timeline_escape_action']
check('row is 35px (34 + its hairline)', escape_row.height(), 35)
check('the shortkeys column is 200px', escape_row.keys_widget.width(), 200)
check('the list well is 420px', host.keyboard_panel_list_scroll.height(), 420)
single = host.keyboard_panel_rows['toggle_lock_current_subtitle'].keys_widget.findChildren(lpk._Keycap)[0]
check('a one-character keycap is 26x21', (single.width(), single.height()), (26, 21))
lpk.keyboard_panel_row_clicked(host, 'timeline_escape_action')
app.processEvents()
big = host.keyboard_panel_keycaps.findChildren(lpk._BigKeycap)
check('a long label gets the wide keycap', (big[0].width(), big[0].height()), (112, 62))
check('its legend is uppercase and top-left',
      (big[0].label.text(), big[0].label.y() < big[0].height() // 2), ('ESCAPE', True))
lpk.keyboard_panel_row_clicked(host, 'toggle_lock_current_subtitle')
app.processEvents()
big = host.keyboard_panel_keycaps.findChildren(lpk._BigKeycap)
check('a short label gets the square one', (big[0].width(), big[0].height()), (62, 62))

print('pressing a big keycap sinks it and moves nothing else')
before = host.keyboard_panel_hint.y()
QTest.mousePress(big[0], Qt.LeftButton)
app.processEvents()
check('the key is pressed', bool(big[0].property('pressed')), True)
check('its side shrinks by sinking it', big[0].parentWidget().layout().contentsMargins().top(),
      lpk._BIG_KEY_TRAVEL)
check('the hint below has not moved', host.keyboard_panel_hint.y(), before)
QTest.mouseRelease(big[0], Qt.LeftButton)
app.processEvents()
check('releasing restores it', bool(big[0].property('pressed')), False)

print('CHANGE listens, and the next combination is the new shortcut')
host = fresh()
lpk.keyboard_panel_row_clicked(host, 'slice_current_subtitle')
lpk.keyboard_panel_change_clicked(host)
app.processEvents()
check('it is listening', host.keyboard_panel_listening, True)
check('the keycaps give way to the prompt',
      (host.keyboard_panel_keycaps.isVisible(), host.keyboard_panel_listening_label.isVisible()),
      (False, True))
check('the app\'s own shortcuts are off while listening',
      [action.isEnabled() for action in host.actions()], [False] * len(COMMANDS))
check('CHANGE offers to cancel', host.keyboard_panel_change_button.text(), 'Cancel')
check('a bare modifier is ignored', (press(host, Qt.Key_Shift, Qt.ShiftModifier),
                                     host.keyboard_panel_listening), (True, True))
press(host, Qt.Key_S, Qt.ControlModifier | Qt.ShiftModifier)
app.processEvents()
check('the combination is stored', session.CONFIG['shortcuts']['slice_current_subtitle'], ['Ctrl+Shift+S'])
check('and put on the live action',
      host._shortcut_actions['slice_current_subtitle'].shortcuts()[0].toString(), 'Ctrl+Shift+S')
check('it was saved', session.CONFIG.saves, 1)
check('listening ended', host.keyboard_panel_listening, False)
check('with the shortcuts back on',
      all(action.isEnabled() for action in host.actions()), True)
check('and the row redrawn',
      [w.text() for w in host.keyboard_panel_rows['slice_current_subtitle'].keys_widget.findChildren(lpk._Keycap)],
      ['Ctrl', 'Shift', 'S'])

print('Escape is captured as a key, not as a way out')
host = fresh()
# Free it first: the command that ships with Escape would otherwise be the
# conflict the capture refuses (covered below).
lpk.keyboard_panel_row_clicked(host, 'timeline_escape_action')
lpk.keyboard_panel_clear_clicked(host)
lpk.keyboard_panel_row_clicked(host, 'slice_current_subtitle')
lpk.keyboard_panel_change_clicked(host)
press(host, Qt.Key_Escape)
check('Escape became the shortcut', session.CONFIG['shortcuts']['slice_current_subtitle'], ['Escape'])
check('and listening ended with it', host.keyboard_panel_listening, False)

print('a combination already in use is refused, not stolen')
host = fresh()
lpk.keyboard_panel_row_clicked(host, 'slice_current_subtitle')
lpk.keyboard_panel_change_clicked(host)
press(host, Qt.Key_H, Qt.ControlModifier)
app.processEvents()
check('the other command keeps it', session.CONFIG['shortcuts']['show_find_replace_dialog'][0], 'Ctrl+H')
check('this one is unchanged', session.CONFIG['shortcuts']['slice_current_subtitle'], ['/'])
check('the hint says who has it', host.keyboard_panel_hint.text(),
      'Ctrl+H is already used by Open the Find & Replace dialog')
check('and it keeps listening', host.keyboard_panel_listening, True)

print('CANCEL leaves the shortcuts working')
lpk.keyboard_panel_change_clicked(host)
check('listening ended', host.keyboard_panel_listening, False)
check('every action is live again', all(action.isEnabled() for action in host.actions()), True)
check('CHANGE is CHANGE again', host.keyboard_panel_change_button.text(), 'Change')

print('CLEAR empties the shortcut')
host = fresh()
lpk.keyboard_panel_row_clicked(host, 'timeline_escape_action')
lpk.keyboard_panel_clear_clicked(host)
app.processEvents()
check('nothing is stored', session.CONFIG['shortcuts']['timeline_escape_action'], [''])
check('the action has no keys', host._shortcut_actions['timeline_escape_action'].shortcuts(), [])
check('the row says so',
      [w.text() for w in host.keyboard_panel_rows['timeline_escape_action'].keys_widget.findChildren(lpk._Keycap)],
      [])
check('and the hint explains', host.keyboard_panel_hint.text(), 'No shortcut assigned')

print('a second binding survives an edit of the first')
host = fresh()
lpk.keyboard_panel_row_clicked(host, 'show_find_replace_dialog')
lpk.keyboard_panel_change_clicked(host)
press(host, Qt.Key_J, Qt.ControlModifier)
check('the first is replaced, the rest kept',
      session.CONFIG['shortcuts']['show_find_replace_dialog'], ['Ctrl+J', 'Ctrl+F'])

print('leaving the panel never strands the app without shortcuts')
host = fresh()
lpk.keyboard_panel_row_clicked(host, 'slice_current_subtitle')
lpk.keyboard_panel_change_clicked(host)
lpk.hide(host)
check('hiding stops listening', host.keyboard_panel_listening, False)
check('and re-enables everything', all(action.isEnabled() for action in host.actions()), True)

print('key names follow the design, not QKeySequence')
host = fresh()
for key, modifiers, want in ((Qt.Key_Escape, Qt.NoModifier, 'Escape'),
                             (Qt.Key_Space, Qt.NoModifier, 'Space'),
                             (Qt.Key_Up, Qt.NoModifier, 'Up'),
                             (Qt.Key_H, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier,
                              'Ctrl+Alt+Shift+H')):
    event = QKeyEvent(QEvent.KeyPress, key, modifiers)
    check(f'{want}', lpk._combination(event), want)

print('a combination splits into the keys it draws')
check('Ctrl+H', lpk._key_labels('Ctrl+H'), ['Ctrl', 'H'])
check('a bare plus', lpk._key_labels('+'), ['+'])
check('Ctrl++', lpk._key_labels('Ctrl++'), ['Ctrl', '+'])
check('nothing', lpk._key_labels(''), [])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
