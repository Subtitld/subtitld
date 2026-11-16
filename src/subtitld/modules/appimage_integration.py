"""A Subtitld AppImage added to the desktop, as installing a package would: an entry in the applications
menu that opens subtitle files and subtitld.cc's "Open in Subtitld" links (subtitld://).

An AppImage is never installed, so nothing tells the desktop about it. With the person's agreement (asked
once, interface/appimage_prompt.py) it writes its own entry under ~/.local/share, pointing at the AppImage's
file, and writes it again when that file moves. A Subtitld installed another way (snap, Flatpak, a distro
package) that already opens the links keeps them.
"""

import os
import shutil
import subprocess

DESKTOP_ID = 'subtitld-appimage.desktop'
ICON_NAME = 'subtitld-appimage'
MIME_FILE = 'subtitld-appimage.xml'
SCHEME = 'x-scheme-handler/subtitld'
PATH_KEY = 'X-AppImage-Path'


def running_appimage():
    """The AppImage file this Subtitld runs from (its runtime sets both variables), or None."""
    path = os.environ.get('APPIMAGE')
    if path and os.environ.get('APPDIR') and os.path.isfile(path):
        return os.path.abspath(path)
    return None


def data_home():
    return os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')


def desktop_path():
    return os.path.join(data_home(), 'applications', DESKTOP_ID)


def icon_path():
    return os.path.join(data_home(), 'icons', 'hicolor', '256x256', 'apps', f'{ICON_NAME}.png')


def mime_path():
    return os.path.join(data_home(), 'mime', 'packages', MIME_FILE)


def registered_path():
    """The AppImage the desktop entry points at, or None when there is no entry."""
    try:
        with open(desktop_path(), encoding='utf-8') as handle:
            for line in handle:
                if line.startswith(f'{PATH_KEY}='):
                    return line.split('=', 1)[1].strip()
    except OSError:
        pass
    return None


def _host_env():
    """The environment as it was before the bundle changed it: the desktop's own tools run with the
    system's libraries, not the AppImage's."""
    env = dict(os.environ)
    original = env.pop('LD_LIBRARY_PATH_ORIG', None)
    if original is not None:
        env['LD_LIBRARY_PATH'] = original
    else:
        env.pop('LD_LIBRARY_PATH', None)
    return env


def _run(*command):
    """A desktop tool's output; '' when it is missing or fails (a desktop without it lacks nothing)."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, env=_host_env(), check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def _application_dirs():
    dirs = [data_home()] + (os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':')
    return [os.path.join(d, 'applications') for d in dirs if d]


def other_handler():
    """The desktop entry of another Subtitld that opens subtitld:// links, or ''. A default whose entry is
    gone (an uninstalled package) opens nothing, so it doesn't count."""
    handler = _run('xdg-mime', 'query', 'default', SCHEME)
    if not handler or handler == DESKTOP_ID:
        return ''
    if not any(os.path.isfile(os.path.join(d, handler)) for d in _application_dirs()):
        return ''
    return handler


def quote_exec(path):
    """`path` as one argument of a desktop entry's Exec line (Desktop Entry spec, "The Exec key")."""
    escaped = ''.join('\\' + c if c in '"`$\\' else c for c in path)
    return '"' + escaped.replace('%', '%%') + '"'


def desktop_entry(template, appimage):
    """The AppImage's own desktop entry, made to start `appimage` and show the icon copied next to it."""
    lines = []
    for line in template.splitlines():
        key = line.split('=', 1)[0].strip()
        if key in ('Exec', 'TryExec', 'Icon', PATH_KEY):
            continue
        lines.append(line)
        if line.strip() == '[Desktop Entry]':
            lines += [f'Exec={quote_exec(appimage)} %u', f'TryExec={appimage}', f'Icon={ICON_NAME}',
                      f'{PATH_KEY}={appimage}']
    if not any(line.strip() == '[Desktop Entry]' for line in lines):
        raise ValueError('not a desktop entry')
    return '\n'.join(lines) + '\n'


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.part'
    with open(temporary, 'w', encoding='utf-8') as handle:
        handle.write(data)
    os.replace(temporary, path)


def _copy(source, path):
    if os.path.isfile(source):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(source, path)


def _refresh():
    # Icon themes look again only when their own folder changes, not the size folder the icon went in (as
    # xdg-icon-resource does): without this GNOME shows the entry with no icon until the next login.
    try:
        os.utime(os.path.join(data_home(), 'icons', 'hicolor'))
    except OSError:
        pass
    _run('update-mime-database', os.path.join(data_home(), 'mime'))
    _run('update-desktop-database', os.path.join(data_home(), 'applications'))


def register(appimage=None, appdir=None):
    """Add (or point again at `appimage`) the desktop entry; take the links unless another Subtitld has
    them. False when the AppImage's own entry can't be read or the files can't be written."""
    appimage = appimage or running_appimage()
    appdir = appdir or os.environ.get('APPDIR')
    if not appimage or not appdir:
        return False
    try:
        with open(os.path.join(appdir, 'subtitld.desktop'), encoding='utf-8') as handle:
            entry = desktop_entry(handle.read(), appimage)
        _copy(os.path.join(appdir, 'usr', 'share', 'icons', 'hicolor', '256x256', 'apps', 'subtitld.png'),
              icon_path())
        _copy(os.path.join(appdir, 'usr', 'share', 'mime', 'packages', 'org.subtitld.Subtitld.xml'),
              mime_path())
        _write(desktop_path(), entry)
    except (OSError, ValueError):
        return False
    _refresh()
    if not other_handler():
        _run('xdg-mime', 'default', DESKTOP_ID, SCHEME)
    return True


def unregister():
    """Take the entry, its icon and its file types away again."""
    for path in (desktop_path(), icon_path(), mime_path()):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _refresh()


def decide(choice):
    """What to do on starting, from the saved `choice` ('yes', 'no' or None for not asked yet):
    'register', 'ask', or None for nothing."""
    appimage = running_appimage()
    if not appimage or choice == 'no':
        return None
    if choice == 'yes':
        return 'register' if registered_path() != appimage else None
    if registered_path() == appimage or other_handler():
        return None
    return 'ask'
