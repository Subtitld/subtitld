"""SimpleDialog: the 15px text edge and message-dialog sizing.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QStyle, QStyleOptionButton
from PySide6.QtCore import QDir, QPoint
from PySide6.QtGui import QFont, QFontDatabase
app = QApplication([])
from subtitld.modules import session
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
for f in os.listdir(session.PATH_SUBTITLD_GRAPHICS):
    if f.lower().endswith('.ttf'):
        QFontDatabase.addApplicationFont(os.path.join(session.PATH_SUBTITLD_GRAPHICS, f))
app.setFont(QFont('Montserrat', 10))
app.setStyleSheet((session.PATH_SUBTITLD_GRAPHICS / 'stylesheet.qss').read_text())
from subtitld.interface import utils

EDGE = 15
fails = []


def check(name, cond, extra=''):
    print(f'  [{"OK " if cond else "FAIL"}] {name}{(" - " + str(extra)) if extra else ""}')
    if not cond:
        fails.append(name)


def shown(dialog):
    dialog.show()
    for _ in range(3):
        app.processEvents()
    return dialog


def x_in(dialog, widget):
    return widget.mapTo(dialog, QPoint(0, 0)).x()


def cancel_text_x(dialog):
    button = dialog.reject_button
    opt = QStyleOptionButton()
    button.initStyleOption(opt)
    contents = button.style().subElementRect(QStyle.SE_PushButtonContents, opt, button)
    return x_in(dialog, button) + contents.x()


def label_text_x(dialog, label):
    return x_in(dialog, label) + label.contentsRect().x()


print('form dialog: title, body and Cancel share the text edge')
d = utils.SimpleDialog(None, title='Rename speaker')
field = QLineEdit('Speaker A')
d.content.layout().addWidget(field)
shown(d)
check('title text at 15', label_text_x(d, d.title_line.label) == EDGE, label_text_x(d, d.title_line.label))
check('body widget at 15', x_in(d, field) == EDGE, x_in(d, field))
check('body keeps 15 on the right too', d.width() - (x_in(d, field) + field.width()) == EDGE,
      d.width() - (x_in(d, field) + field.width()))
check('Cancel text at 15', cancel_text_x(d) == EDGE, cancel_text_x(d))
d.close()

print('message dialogs: text on the edge, sized for the width they get')
words = ('Could not save the project because the destination folder is not writable or the disk '
         'is full; check the permissions and the free space, then try saving again').split()
messages = [' '.join(words[:n]) for n in range(1, len(words) + 1, 3)]
messages += ["HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded with url: "
             f"https://example.com/{'a' * k} (Caused by NameResolutionError)" for k in (40, 100, 160)]
edges, clipped, spare, widths = set(), [], [], set()
for text in messages:
    for detail in (None, '[Errno 28] No space left on device'):
        d = utils.SimpleDialog(None, title='Save error')
        labels = [QLabel(text)] + ([QLabel(detail)] if detail else [])
        for label in labels:
            d.content.layout().addWidget(label)
        shown(d)
        widths.add(d.width())
        for label in labels:
            edges.add(label_text_x(d, label))
            need = label.heightForWidth(label.width())
            if label.height() < need:
                clipped.append((len(text), label.height(), need))
            elif label.height() - need >= label.fontMetrics().lineSpacing():
                spare.append((len(text), label.height(), need))
        d.close()
check('message text at 15', edges == {EDGE}, edges)
check('no label is cut short', not clipped, clipped[:3])
check('no label keeps a spare line', not spare, spare[:3])
check('widths stay within 400..520', min(widths) >= 400 and max(widths) <= utils.SimpleDialog._TEXT_DIALOG_MAX_WIDTH,
      sorted(widths))
check('the long messages reach the width cap', max(widths) == utils.SimpleDialog._TEXT_DIALOG_MAX_WIDTH, max(widths))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
