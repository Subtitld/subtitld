"""Themes a project carries and themes it is missing (modules.themes.carry,
used, missing, to_embed; modules.theme_store's subtitle and title themes):
a USFX with the theme files it draws with under assets/themes/, the user's
own only when asked, one only the project has always; opened elsewhere, its
themes drawn from the file and never installed; a newer one the project
carries over an older one installed, never over Subtitld's own; a theme the
project names and nobody has kept by name (and its values) when saved, and
said missing; a subtitld.cc theme's id back to its share ID.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, sys, tempfile, zipfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, file_io, history, session, subtitle_styles, theme_store, themes, title_themes
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def theme_file(theme_id, kind, version, colour='#ffffffff'):
    manifest = {'format': 1, 'id': theme_id, 'kind': kind, 'name': theme_id.title(), 'version': version,
                'fields': [{'key': 'color', 'type': 'color', 'default': colour}]}
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
            '</head><body></body></html>')


mine = session.PATH_SUBTITLD_USER_DATA / 'themes'
mine.mkdir(parents=True, exist_ok=True)
(mine / 'boxed.html').write_text(theme_file('test.boxed', 'subtitle', '26.12.1'), encoding='utf-8')
(mine / 'banner.html').write_text(theme_file('test.banner', 'title', '26.12.1'), encoding='utf-8')
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
themes.reload_looks()
folder = tempfile.mkdtemp()


def new_project():
    session.SUBTITLE = Subtitle({'segments': [
        {'start': 0.0, 'end': 1.0, 'text': 'One', 'speaker': 'A'},
        {'start': 2.0, 'end': 3.0, 'text': 'Two', 'speaker': 'A', 'theme': 'subtitld.classic'},
        {'start': 4.0, 'end': 5.0, 'text': 'Banner', 'kind': 'title', 'title_theme': 'test.banner'},
        {'start': 6.0, 'end': 6.0, 'text': 'A mark', 'kind': 'mark'},
    ], 'language': 'en', 'filepath': ''})
    session.SPEAKERS.clear()
    session.FORMAT = {}
    session.VIDEO = {}
    history.history_clear()
    subtitle_styles.set_current('test.boxed')
    subtitle_styles.set_value('color', '#ff112233')


def save(name, include):
    session.CONFIG.setdefault('default_values', {})['usfx_options'] = {'include_themes': include}
    path = os.path.join(folder, name)
    file_io.save_file(path, 'USFX')
    with zipfile.ZipFile(path) as bundle:
        return path, sorted(n for n in bundle.namelist() if n.startswith('assets/themes/'))


def open_project(path):
    session.SUBTITLE.segments = file_io.process_subtitles_file(path)[0]


print('what a project draws with')
new_project()
check('used: its own, the cues\', the titles\'', themes.used(session.SUBTITLE.segments, session.SUBTITLE.theme),
      {'test.boxed': '26.12.1', 'subtitld.classic': '', 'test.banner': ''})
check('nothing missing', themes.missing(session.SUBTITLE.segments, session.SUBTITLE.theme), {})

print('saving')
path, carried = save('plain.usfx', False)
check('installed ones not asked for: none in it', carried, [])
path, carried = save('carrying.usfx', True)
check('asked for: the user\'s two, not Subtitld\'s', carried,
      ['assets/themes/test.banner.html', 'assets/themes/test.boxed.html'])
with zipfile.ZipFile(path) as bundle:
    manifest = bundle.read('manifest.xml').decode('utf-8')
check('listed in its manifest', 'path="assets/themes/test.boxed.html" type="text/html"' in manifest, True)

print('opened where they are not installed')
(mine / 'boxed.html').rename(Path(folder) / 'boxed.html')
(mine / 'banner.html').rename(Path(folder) / 'banner.html')
themes.reload_looks()
check('gone from here', 'test.boxed' in themes.available(), False)
open_project(path)
boxed = themes.available().get('test.boxed')
check('drawn from the file', (boxed is not None, boxed and boxed.carried), (True, True))
check('a subtitle style and a title theme', ('test.boxed' in subtitle_styles.available(),
                                              'test.banner' in title_themes.available()), (True, True))
check('the project\'s theme and values', (subtitle_styles.current_id(), subtitle_styles.values()['color']),
      ('test.boxed', '#ff112233'))
check('not installed', sorted(p.name for p in mine.iterdir()), [])
check('nothing missing', themes.missing(session.SUBTITLE.segments, session.SUBTITLE.theme), {})
path, carried = save('again.usfx', False)
check('saved again, not asked: still carried (only it has them)', carried,
      ['assets/themes/test.banner.html', 'assets/themes/test.boxed.html'])

print('another project opened')
open_project(os.path.join(folder, 'plain.usfx'))
check('the last one\'s themes gone', 'test.boxed' in themes.available(), False)
check('missing: by id, with the version it names', themes.missing(session.SUBTITLE.segments, session.SUBTITLE.theme),
      {'test.boxed': '26.12.1', 'test.banner': ''})
check('drawn with Classic meanwhile', subtitle_styles.current_id(), 'subtitld.classic')
path, carried = save('plain2.usfx', True)
check('nothing to carry', carried, [])
open_project(path)
check('saved: still names its theme', (session.SUBTITLE.theme.get('id'), session.SUBTITLE.theme.get('version')),
      ('test.boxed', '26.12.1'))
check('and keeps its values', session.SUBTITLE.theme['values'].get('test.boxed'), {'color': '#ff112233'})
check('the title its theme', [s.get('title_theme') for s in session.SUBTITLE.segments if s.get('kind') == 'title'],
      ['test.banner'])

print('versions')
(Path(folder) / 'boxed.html').rename(mine / 'boxed.html')
themes.reload_looks()
themes.carry({'assets/themes/test.boxed.html': theme_file('test.boxed', 'subtitle', '26.12.4').encode()})
check('a newer one carried over the installed one', (themes.available()['test.boxed'].version,
                                                     themes.available()['test.boxed'].carried), ('26.12.4', True))
themes.carry({'assets/themes/test.boxed.html': theme_file('test.boxed', 'subtitle', '26.11.1').encode()})
check('an older one: the installed one', (themes.available()['test.boxed'].version,
                                          themes.available()['test.boxed'].carried), ('26.12.1', False))
themes.carry({'assets/themes/classic.html': theme_file('subtitld.classic', 'subtitle', '99.1.1').encode()})
check('never over Subtitld\'s own', themes.available()['subtitld.classic'].builtin, True)
themes.carry({'assets/themes/bad.html': b'<html><script>alert(1)</script></html>'})
check('a file that is not a theme: not used, said why', list(themes.carried_problems), ['assets/themes/bad.html'])
themes.carry({})
check('none: none', (themes.carried_problems, [t for t in themes.available().values() if t.carried]), ({}, []))

print('subtitld.cc ids')
share_id = 'AbC-_dEf010'                  # 8 bytes: its last character's last bits are 0
check('back to the share ID', theme_store.share_id_from(theme_store.theme_id(share_id)), share_id)
check('another theme\'s: none', (theme_store.share_id_from('test.boxed'), theme_store.share_id_from('cc.zz')), (None, None))


class FakeClient:
    def theme(self, wanted):
        return {'version': 3, 'owner': 'someone'}

    def download_theme(self, wanted, version):
        return theme_file(theme_store.theme_id(wanted), 'title', '26.12.1').encode()


theme_store.service.client = lambda: FakeClient()
installed = theme_store.save_file(*theme_store.fetch_file(theme_store.theme_id(share_id)))
check('installed among the user\'s own', (installed.id, (mine / f'{installed.id}.html').is_file(), installed.carried),
      (theme_store.theme_id(share_id), True, False))
check('its version remembered', session.CONFIG['themes']['installed'][installed.id]['version'], 3)
check('a title theme now', installed.id in title_themes.available(), True)
try:
    theme_store.save_file('AAAAAAAAAAA', 1, theme_file('test.other', 'title', '1').encode())
    check('another id refused', False, True)
except theme_store.StoreError:
    check('another id refused', True, True)

print('the dialog')
from subtitld.interface import missing_themes_dialog
dialog = missing_themes_dialog.MissingThemesDialog(None, ['test.gone', theme_store.theme_id('ZZZZZZZZZZZ')])
check('only subtitld.cc\'s to download', dialog.downloadable, [theme_store.theme_id('ZZZZZZZZZZZ')])
check('a Download button', dialog.reject_button.isHidden(), False)
dialog = missing_themes_dialog.MissingThemesDialog(None, ['test.gone'])
check('none to download: only OK', dialog.reject_button.isHidden(), True)

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
