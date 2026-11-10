"""Text is readable: every text the interface shows, on the start screen and
in each panel of the left rail with a video open, has at least WCAG's AA
contrast with what is behind it (4.5:1, or 3:1 for large text). Disabled
controls are exempt, as WCAG has them, and so is the title bar's wordmark (a
logo).

What is behind a text is measured, not assumed: the panels' backgrounds are
translucent images over the window's, so the window is drawn (off-screen,
with its stylesheet and images) and the most common colour in each widget's
area is taken as its background. The text's colour is the one the stylesheet
gives the widget, its transparency blended over that background.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import collections, os, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtCore import QDir, QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QAbstractButton, QComboBox, QGroupBox, QLabel
app = QApplication([])
from subtitld.modules import session
from subtitld.modules.addons import installer
from subtitld.interface import left_panel, startscreen
from subtitld import __main__ as subtitld_main

QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
for font_file in os.listdir(session.PATH_SUBTITLD_GRAPHICS):
    if font_file.endswith('.ttf'):
        QFontDatabase.addApplicationFont(os.path.join(session.PATH_SUBTITLD_GRAPHICS, font_file))
app.setFont(QFont('Montserrat', 10))
installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}

AA, AA_LARGE = 4.5, 3.0


def wait(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_of(widget):
    if isinstance(widget, QGroupBox):
        return widget.title()
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QLabel) and widget.pixmap() is not None and not widget.pixmap().isNull():
        return ''
    if isinstance(widget, QLabel) and widget.text().lstrip().startswith('<img'):   # a logo, not text
        return ''
    return widget.text() if hasattr(widget, 'text') else ''


def background(image, widget, window):
    """The most common colour in the part of the widget that shows (not
    scrolled away or covered by its parents' edges) in the drawn window."""
    visible = widget.visibleRegion().boundingRect()
    if visible.width() < 4 or visible.height() < 4:
        return None
    top_left = widget.mapTo(window, visible.topLeft())
    counts = collections.Counter()
    w, h = visible.width(), visible.height()
    for y in range(top_left.y(), top_left.y() + h, 2):
        for x in range(top_left.x(), top_left.x() + w, 2):
            if 0 <= x < image.width() and 0 <= y < image.height():
                c = image.pixelColor(x, y)
                counts[(c.red(), c.green(), c.blue())] += 1
    return counts.most_common(1)[0][0] if counts else None


def check_texts(where, root, window, failures):
    image = window.grab().toImage()
    if os.environ.get('CONTRAST_SHOTS'):        # to look at what was measured
        image.save(os.path.join(os.environ['CONTRAST_SHOTS'], where.replace(' ', '-') + '.png'))
    seen = 0
    for widget in root.findChildren(QAbstractButton) + root.findChildren(QLabel) + root.findChildren(QGroupBox) + root.findChildren(QComboBox):
        text = (text_of(widget) or '').strip()
        if not text or not widget.isVisibleTo(window) or not widget.isEnabled() or widget.width() < 4 or widget.height() < 4:
            continue
        # A checked button is drawn with its :checked colour, which the
        # palette does not hold (here, white on the lighter fill).
        if isinstance(widget, QAbstractButton) and widget.isCheckable() and widget.isChecked():
            continue
        colour = widget.palette().color(widget.foregroundRole())
        behind = background(image, widget, window)
        if behind is None:
            continue
        alpha = colour.alphaF()
        shown = tuple(round(c * alpha + bg * (1 - alpha)) for c, bg in zip((colour.red(), colour.green(), colour.blue()), behind))
        font = widget.font()
        large = font.pointSizeF() >= 18 or (font.bold() and font.pointSizeF() >= 14)
        ratio = contrast(shown, behind)
        seen += 1
        if ratio < (AA_LARGE if large else AA):
            name = widget.objectName() or widget.property('class') or type(widget).__name__
            failures.append(f'{where}: {name} {text[:40]!r}: {ratio:.1f}:1 '
                            f'(text {colour.name(colour.NameFormat.HexArgb)} on {"#%02x%02x%02x" % behind})')
    return seen


failures = []
window = subtitld_main.Window()
window.resize(1600, 900)
window.show()
wait(2)
counted = check_texts('start screen', window, window, failures)

folder = Path(tempfile.mkdtemp())
video, srt = folder / 'clip.mp4', folder / 'clip.srt'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:size=640x360:rate=25',
                '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=48000', '-t', '6', '-c:v', 'mpeg4', '-c:a', 'aac',
                '-shortest', str(video)], check=True)
srt.write_text('1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:03,500 --> 00:00:05,500\nA second line.\n',
               encoding='utf-8')
startscreen.open_files(window, [str(srt), str(video)])
wait(4)
counted += check_texts('editor', window, window, failures)
for button in window.findChildren(left_panel.navigation_button):
    left_panel.show_panel(window, button.tab_name)
    wait(1)
    counted += check_texts(f'panel {button.tab_name}', window.left_panel_stackedwidgets.currentWidget(), window, failures)

print(f'{counted} texts checked')
for failure in sorted(set(failures)):
    print('  FAIL', failure)
print()
print('FAILED:' if failures else 'ALL PASS', len(set(failures)) if failures else '')
window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()
sys.exit(1 if failures else 0)
