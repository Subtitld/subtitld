"""Buttons that show only an icon have a name a screen reader can read out:
their tooltip, in the interface's language, without its markup. Checked on
the main window with a video open, in every panel of the left rail, and
again after the interface language changes. The tooltips that once said
"Stpo" or "Zoom outline+" read as they should.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import QApplication, QAbstractButton
app = QApplication([])
from subtitld.modules.addons import installer
from subtitld.interface import left_panel, startscreen, translation
from subtitld.interface.translation import _
from subtitld import __main__ as subtitld_main

installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}
fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def unnamed(root, window):
    """Visible icon-only buttons with no accessible name."""
    return sorted({b.objectName() or b.toolTip() or type(b).__name__ for b in root.findChildren(QAbstractButton)
                   if b.isVisibleTo(window) and not b.text().strip() and not b.accessibleName().strip()})


translation.system_language = lambda ui_languages=None: 'en_US'   # not this machine's language
window = subtitld_main.Window()
window.resize(1600, 900)
window.show()
folder = Path(tempfile.mkdtemp())
video, srt = folder / 'clip.mp4', folder / 'clip.srt'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:size=640x360:rate=25',
                '-t', '3', '-c:v', 'mpeg4', str(video)], check=True)
srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nHello.\n', encoding='utf-8')
startscreen.open_files(window, [str(srt), str(video)])
wait(3)

print('every icon-only button has a name, in every panel')
missing = {}
for button in window.findChildren(left_panel.navigation_button):
    left_panel.show_panel(window, button.tab_name)
    wait(0.5)
    found = unnamed(window, window)
    if found:
        missing[button.tab_name] = found
check('icon-only buttons without a name', sorted({n for found in missing.values() for n in found}), [])

print('the rail buttons are named after what they open')
rail = {b.tab_name: b.accessibleName() for b in window.findChildren(left_panel.navigation_button)}
check('Import', rail['import'], 'Import')
check('Global settings', rail['global'], 'Global settings')

print('the tooltips that were garbled')
check('stop', _('playercontrols.stop'), 'Stop')
check('zoom out', _('playercontrols.zoom_out'), 'Zoom out')
check('previous subtitle start and end', _('playercontrols.last_start_last_end_to_current_position'),
      'Move previous subtitle start and end to current position')

print('a new interface language renames them')
translation.set_language('pt_BR')
window.translate()
rail = {b.tab_name: b.accessibleName() for b in window.findChildren(left_panel.navigation_button)}
check('Plain text editor, in Portuguese', rail['plaintext'], _('left_panel.tab_plaintext'))
check('...which is not the English name', rail['plaintext'] != 'Plain text editor', True)

window.left_panel_global_addons_panel.shutdown()
window.preview_panel_player._audio_device.shutdown()
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
