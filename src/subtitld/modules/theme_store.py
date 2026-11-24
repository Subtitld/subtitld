"""Timeline themes from subtitld.cc: installed among the user's own themes (modules.timeline_themes).

A theme downloaded from subtitld.cc names itself ``cc.<share ID in hex>`` and its counterpart the same way
(theme ids are lowercase; a share ID's case matters), so installing it again (a new version) replaces it and
keeps the options its user chose. Which version of each
is installed is kept in ``CONFIG['timeline']['installed']`` ({theme id: {'share_id', 'version'}}), so the
browser can offer an update when subtitld.cc has a newer one.

Installing a theme installs its counterpart too, when it has one: Subtitld swaps between the two when the
interface's look changes. The network calls block; run them with subtitldcc_service.call().
"""

import base64

from subtitld.modules import session, timeline_themes
from subtitld.modules import subtitldcc_service as service

PREFIX = 'cc.'


class StoreError(Exception):
    """subtitld.cc sent something that isn't a theme this Subtitld can install."""


def theme_id(share_id):
    """The id subtitld.cc gives a theme's file: its share ID's 8 bytes, in hex (themes/downloads.py there)."""
    return PREFIX + base64.urlsafe_b64decode(share_id + '=').hex()


def _installed():
    timeline = session.CONFIG.setdefault('timeline', {}) if isinstance(session.CONFIG, dict) else {}
    installed = timeline.get('installed')
    if not isinstance(installed, dict):
        installed = timeline['installed'] = {}
    return installed


def installed_version(share_id):
    """The subtitld.cc version of the theme installed here, or None (not installed, or its file is gone)."""
    record = _installed().get(theme_id(share_id))
    if not isinstance(record, dict) or theme_id(share_id) not in timeline_themes.available():
        return None
    return record.get('version')


def share_id_of(theme):
    """The subtitld.cc share ID a theme was installed from, or None."""
    record = _installed().get(theme.id)
    return record.get('share_id') if isinstance(record, dict) else None


def owner_of(theme):
    """Who published on subtitld.cc a theme installed from there ('' when not known)."""
    record = _installed().get(theme.id)
    return (record.get('owner') or '') if isinstance(record, dict) else ''


def state(info):
    """What the browser offers for a theme subtitld.cc lists: 'install', 'update', 'use' or 'in_use'."""
    version = installed_version(info['share_id'])
    if version is None:
        return 'install'
    if info.get('version') and version < info['version']:
        return 'update'
    current = timeline_themes.current()
    return 'in_use' if current is not None and current.id == theme_id(info['share_id']) else 'use'


def save(share_id, version, data, preview=b'', owner=''):
    """Install a theme file subtitld.cc sent (bytes), with its preview (PNG) beside it and who published
    it there. Returns the Theme."""
    try:
        theme = timeline_themes.parse(data)
    except timeline_themes.ThemeError as error:
        raise StoreError(str(error)) from error
    if theme.id != theme_id(share_id):
        raise StoreError(f'id {theme.id}: expected {theme_id(share_id)}')
    folder = timeline_themes.folders()[1][0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{theme.id}.json').write_bytes(data)
    picture = folder / f'{theme.id}.png'
    if preview:
        picture.write_bytes(preview)
    else:
        picture.unlink(missing_ok=True)
    _installed()[theme.id] = {'share_id': share_id, 'version': version, 'owner': owner or ''}
    timeline_themes.available(reload=True)
    return timeline_themes.available()[theme.id]


def fetch(share_id, with_pair=True):
    """Download a theme (and its counterpart) from subtitld.cc, with their previews. Blocking. Returns
    [(share_id, version, data, preview, owner)], the theme first."""
    connection = service.client()
    info = connection.theme(share_id)
    wanted = [(share_id, info['version'], info.get('owner'))]
    pair = info.get('pair') if with_pair else None
    if pair:
        wanted.append((pair['share_id'], pair['version'], pair.get('owner')))
    files = []
    for wanted_id, version, owner in wanted:
        try:
            preview = connection.theme_preview(wanted_id, version)
        except Exception:  # a theme without its picture still installs
            preview = b''
        files.append((wanted_id, version, connection.download_theme(wanted_id, version), preview, owner))
    return files


def install(files, use=True):
    """Install what fetch() brought. With ``use``, the timeline takes the theme, or its counterpart when
    that one is drawn for the interface's current look. Returns the first Theme."""
    from subtitld.interface import theme as interface_theme

    themes = [save(*file) for file in files]
    _save_config()
    if use:
        timeline_themes.apply(themes[0].id)
        timeline_themes.follow_look(interface_theme.current())
    return themes[0]


def use(chosen):
    """Put a theme (by its id) on the timeline: the one picked, whatever the look (it follows the look
    from the next change of it, as any theme does)."""
    timeline_themes.apply(chosen)
    _save_config()


def remove(removed):
    """Remove one of the user's themes (from subtitld.cc or imported), by its id; never one of Subtitld's.
    The timeline goes back to the default theme if it used it."""
    current = timeline_themes.current()
    theme = timeline_themes.available().get(removed)
    if theme is None or theme.builtin:
        return
    if theme.path is not None:
        theme.path.unlink(missing_ok=True)
        theme.path.with_suffix('.png').unlink(missing_ok=True)
    _installed().pop(removed, None)
    options = session.CONFIG.get('timeline', {}).get('theme_options') if isinstance(session.CONFIG, dict) else None
    if isinstance(options, dict):
        options.pop(removed, None)
    timeline_themes.available(reload=True)
    if current is not None and current.id == removed:
        from subtitld.interface import theme as interface_theme

        timeline_themes.reset()
        timeline_themes.follow_look(interface_theme.current())
    _save_config()


def _save_config():
    try:
        session.CONFIG.save()
    except Exception:  # saved again when Subtitld closes
        pass
