"""appimage_integration: run from an AppImage, Subtitld asks once to join the applications menu; with a
yes it writes its own desktop entry (pointing at the AppImage, followed when it moves) and takes
subtitld:// links, unless another Subtitld that is still there already has them.

The desktop's tools are stand-ins (a fake xdg-mime on PATH that logs its calls); the files are real,
under a temporary XDG_DATA_HOME.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
from subtitld.modules import appimage_integration as ai, session
from subtitld.interface import appimage_prompt, utils

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


root = Path(tempfile.mkdtemp())
repo = Path(__file__).resolve().parents[1]

# The AppImage as its runtime presents it: the file, and the mounted AppDir with the build's entry.
appdir = root / 'mount'
(appdir / 'usr/share/icons/hicolor/256x256/apps').mkdir(parents=True)
(appdir / 'usr/share/mime/packages').mkdir(parents=True)
entry = (repo / 'snap/gui/subtitld.desktop').read_text().replace('Icon=${SNAP}/meta/gui/icon.png', 'Icon=subtitld')
(appdir / 'subtitld.desktop').write_text(entry)
(appdir / 'usr/share/icons/hicolor/256x256/apps/subtitld.png').write_bytes(b'png')
(appdir / 'usr/share/mime/packages/org.subtitld.Subtitld.xml').write_text('<mime-info/>')
appimage = root / 'Down loads' / 'Subtitld-26.11 "x"-x86_64.AppImage'
appimage.parent.mkdir()
appimage.write_bytes(b'')

# A fake xdg-mime: `query default` answers from a file, every call is logged.
bin_dir = root / 'bin'
bin_dir.mkdir()
log = root / 'calls.log'
handler_file = root / 'handler'
handler_file.write_text('')
(bin_dir / 'xdg-mime').write_text(f'#!/bin/sh\necho "xdg-mime $*" >> "{log}"\n'
                                  f'[ "$1" = query ] && cat "{handler_file}"\nexit 0\n')
for tool in ('update-desktop-database', 'update-mime-database'):
    (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
for tool in bin_dir.iterdir():
    tool.chmod(0o755)
os.environ['PATH'] = f'{bin_dir}:{os.environ["PATH"]}'
system_share = root / 'system'
(system_share / 'applications').mkdir(parents=True)
os.environ['XDG_DATA_DIRS'] = str(system_share)


def calls():
    return log.read_text().splitlines() if log.exists() else []


def defaults_set():
    return [c for c in calls() if c.startswith('xdg-mime default')]


print('Not an AppImage')
os.environ.pop('APPIMAGE', None)
os.environ.pop('APPDIR', None)
check('run from source: not an AppImage', ai.running_appimage(), None)
check('… nothing to do, even unasked', ai.decide(None), None)
os.environ['APPIMAGE'] = str(appimage)
os.environ['APPDIR'] = str(appdir)
check('run from the AppImage: its file', ai.running_appimage(), str(appimage))

print('The Exec line')
check('spaces, quotes and % kept as one argument', ai.quote_exec('/a b/"c" $d `e` 100%\\'),
      '"/a b/\\"c\\" \\$d \\`e\\` 100%%\\\\"')

print('The entry')
made = ai.desktop_entry(entry, str(appimage))
lines = made.splitlines()
check('starts the AppImage, with the link', [line for line in lines if line.startswith('Exec=')],
      [f'Exec={ai.quote_exec(str(appimage))} %u'])
check('its own icon', [line for line in lines if line.startswith('Icon=')], ['Icon=subtitld-appimage'])
check('the subtitld:// links among its types', any('x-scheme-handler/subtitld;' in line for line in lines), True)
check("the build's name and comment kept", 'GenericName=Subtitle Editor' in lines, True)
check('remembers the AppImage', f'X-AppImage-Path={appimage}' in lines, True)
try:
    ai.desktop_entry('nothing here', str(appimage))
    check('not an entry: refused', False, True)
except ValueError:
    check('not an entry: refused', True, True)

print('Asked once')
check('unasked, nobody has the links: ask', ai.decide(None), 'ask')
check('said no: never again', ai.decide('no'), None)
check('said yes, no entry yet: add it', ai.decide('yes'), 'register')

print('Added')
theme = Path(ai.icon_path()).parents[2]
theme.mkdir(parents=True, exist_ok=True)
os.utime(theme, (1, 1))
check('registered', ai.register(), True)
check('the icon theme told to look again', theme.stat().st_mtime > 1, True)
check('the entry points at the AppImage', ai.registered_path(), str(appimage))
check('icon copied', Path(ai.icon_path()).read_bytes(), b'png')
check('file types copied', Path(ai.mime_path()).read_text(), '<mime-info/>')
check('takes the links', defaults_set(), ['xdg-mime default subtitld-appimage.desktop x-scheme-handler/subtitld'])
check('desktop and type databases refreshed',
      sorted({c.split()[0] for c in calls() if c.startswith('update-')}),
      ['update-desktop-database', 'update-mime-database'])
check('said yes, entry in place: nothing', ai.decide('yes'), None)
check('unasked but already added (an earlier yes): nothing', ai.decide(None), None)

print('Moved')
moved = root / 'Apps' / 'Subtitld.AppImage'
moved.parent.mkdir()
appimage.rename(moved)
os.environ['APPIMAGE'] = str(moved)
check('said yes, the AppImage moved: add it again', ai.decide('yes'), 'register')
ai.register()
check('… now pointing at the new place', ai.registered_path(), str(moved))

print('Another Subtitld has the links')
log.unlink()
handler_file.write_text('subtitld_subtitld.desktop\n')
check("its entry gone (uninstalled): doesn't count", ai.other_handler(), '')
(system_share / 'applications' / 'subtitld_subtitld.desktop').write_text('[Desktop Entry]\n')
check('installed: it keeps them', ai.other_handler(), 'subtitld_subtitld.desktop')
ai.unregister()
check('unasked: not asked at all', ai.decide(None), None)
ai.register()
check('added anyway (a yes): the links are left alone', defaults_set(), [])
handler_file.write_text('subtitld-appimage.desktop\n')
check('our own entry is not "another"', ai.other_handler(), '')

print('Removed')
ai.unregister()
check('entry, icon and types gone',
      [os.path.exists(p) for p in (ai.desktop_path(), ai.icon_path(), ai.mime_path())], [False, False, False])
check('no entry: nothing registered', ai.registered_path(), None)
ai.unregister()
check('removing twice is fine', ai.registered_path(), None)

print("The desktop's tools run with the system's libraries")
os.environ['LD_LIBRARY_PATH'] = '/bundle/lib'
os.environ['LD_LIBRARY_PATH_ORIG'] = '/usr/local/lib'
check("PyInstaller's saved value restored", ai._host_env().get('LD_LIBRARY_PATH'), '/usr/local/lib')
del os.environ['LD_LIBRARY_PATH_ORIG']
check('none saved: dropped', 'LD_LIBRARY_PATH' in ai._host_env(), False)
del os.environ['LD_LIBRARY_PATH']
path = os.environ['PATH']
(root / 'empty').mkdir()
os.environ['PATH'] = str(root / 'empty')
os.environ['XDG_DATA_HOME'] = str(root / 'no-tools')
check('without xdg-mime: still added', ai.register(), True)
check('… and nobody else has the links', ai.other_handler(), '')
os.environ['PATH'] = path
os.environ['XDG_DATA_HOME'] = os.path.join(_xdg, 'xdg_data_home')

print('The question')
handler_file.write_text('')
ai.unregister()
session.CONFIG = __import__('subtitld.modules.config', fromlist=['Config']).Config(str(root / 'config.json'))
window = QWidget()
asked = []
answer = [0]


def exec_(dialog):
    asked.append((dialog.title_line.label.text(), dialog.accept_button.text(), dialog.reject_button.text()))
    return answer[0]


utils.SimpleDialog.exec = exec_
appimage_prompt.check(window)
check('asked, with Add and Don\'t add', asked, [("Add Subtitld to your applications?", 'Add', "Don't add")])
check('no: remembered', session.CONFIG['appimage'], {'integrate': 'no'})
check('… and saved', '"integrate": "no"' in (root / 'config.json').read_text(), True)
check('… nothing added', ai.registered_path(), None)
appimage_prompt.check(window)
check('not asked again', len(asked), 1)
session.CONFIG['appimage'] = {}
answer[0] = 1
appimage_prompt.check(window)
check('yes: remembered', session.CONFIG['appimage'], {'integrate': 'yes'})
check('… and added', ai.registered_path(), str(moved))
appimage_prompt.check(window)
check('next start: not asked', len(asked), 2)
check('config key accepted', 'appimage' in session.CONFIG.get_valid_keys(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
