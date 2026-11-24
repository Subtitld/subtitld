"""Timeline themes from subtitld.cc: subtitld:// theme links, installing a theme and its counterpart under
cc.<share ID in hex>, what the browser offers for each (install, update, use, in use), removing one, and the
browser dialog itself (modules/theme_store.py, interface/timeline_theme_browser.py). subtitld.cc is a fake
here: its client answers from memory, and background calls run at once.

Standalone script, like the other suites here; puts this checkout's src/ on the path itself.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import json
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, session, subtitldcc_links, theme_store, timeline_themes
from subtitld.modules import subtitldcc_service as service
from subtitld.interface import theme as interface_theme
from subtitld.interface import timeline_theme_browser

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


CLASSIC = json.loads((Path(__file__).resolve().parents[1] / 'src/subtitld/timeline_themes/classic.json').read_text())


def theme_file(share_id, name, look, counterpart=None, version='1'):
    data = dict(CLASSIC, id=theme_store.theme_id(share_id), name=name, look=look, version=version)
    if counterpart:
        data['counterpart'] = theme_store.theme_id(counterpart)
    else:
        data.pop('counterpart', None)
    return json.dumps(data).encode()


def info(share_id, title, look, version=1, counterpart=None):
    return {'share_id': share_id, 'theme_id': theme_store.theme_id(share_id), 'url': f'https://subtitld.cc/{share_id}',
            'title': title, 'owner': 'marina', 'look': look, 'counterpart': counterpart, 'version': version,
            'downloads': 3, 'rating': None, 'colors': {}, 'options': [], 'preview': {'width': 1800, 'height': 400}}


SITE = {
    'Night000001': (info('Night000001', 'Night', 'dark', counterpart='Day00000001'),
                    theme_file('Night000001', 'Night', 'dark', 'Day00000001')),
    'Day00000001': (info('Day00000001', 'Day', 'light', counterpart='Night000001'),
                    theme_file('Day00000001', 'Day', 'light', 'Night000001')),
    'Alone000001': (info('Alone000001', 'Alone', 'dark'), theme_file('Alone000001', 'Alone', 'dark')),
}
downloaded = []


class FakeClient:
    def themes(self, look='', sort='popular', q='', cursor=None, limit=24):
        results = [entry for entry, _data in SITE.values() if not look or entry['look'] == look]
        return {'results': [dict(r) for r in results], 'next_cursor': None, 'total': len(results)}

    def theme(self, share_id):
        entry = dict(SITE[share_id][0])
        pair = entry.get('counterpart')
        entry['pair'] = dict(SITE[pair][0]) if pair else None
        return entry

    def download_theme(self, share_id, version=None):
        downloaded.append(share_id)
        return SITE[share_id][1]

    def theme_preview(self, share_id, version=None):
        return b''


class Done:
    def isRunning(self):
        return False


def call_now(function, on_success=None, on_failure=None):
    try:
        result = function()
    except Exception as error:  # noqa: BLE001
        if on_failure:
            on_failure(error)
    else:
        if on_success:
            on_success(result)
    return Done()


service.client = lambda token=None: FakeClient()
service.call = call_now
config_path = Path(tempfile.mkdtemp()) / 'subtitld.config'
config_path.write_text(json.dumps({'interface_theme': 'dark'}))
session.CONFIG = config.Config(config_path)
interface_theme.set_current('dark')
timeline_themes.use_default('dark')


print('links')
check('a theme link', subtitldcc_links.parse('subtitld://subtitld.cc/theme/Night000001'),
      {'site': 'subtitld.cc', 'theme': 'Night000001'})
check('not a share ID, or more after it', (subtitldcc_links.parse('subtitld://subtitld.cc/theme/short'),
                                           subtitldcc_links.parse('subtitld://subtitld.cc/theme/Night000001/2')),
      (None, None))

print('installing')
check('nothing installed yet', theme_store.state(SITE['Night000001'][0]), 'install')
files = theme_store.fetch('Night000001')
check('the theme and its counterpart come down', [share_id for share_id, _version, _data in files],
      ['Night000001', 'Day00000001'])
installed = theme_store.install(files)
check('both installed, under cc.<share ID in hex>', (theme_store.theme_id('Night000001') in timeline_themes.available(),
                                              theme_store.theme_id('Day00000001') in timeline_themes.available()), (True, True))
check('and the timeline takes it (the look is dark)', timeline_themes.current().id, theme_store.theme_id('Night000001'))
check('the browser says so', (theme_store.state(SITE['Night000001'][0]), theme_store.state(SITE['Day00000001'][0])),
      ('in_use', 'use'))
check('the versions are kept in the config', session.CONFIG['timeline']['installed'][theme_store.theme_id('Day00000001')],
      {'share_id': 'Day00000001', 'version': 1})
newer = dict(SITE['Night000001'][0], version=2)
check('a newer version on subtitld.cc is an update', theme_store.state(newer), 'update')

interface_theme.set_current('light')
theme_store.install(theme_store.fetch('Night000001'))
check('in the light look, its light counterpart takes the timeline', timeline_themes.current().id,
      theme_store.theme_id('Day00000001'))
interface_theme.set_current('dark')
timeline_themes.follow_look('dark')

try:
    theme_store.save('Alone000001', 1, SITE['Night000001'][1])
    check('a file under another id is refused', 'saved', 'refused')
except theme_store.StoreError:
    check('a file under another id is refused', 'refused', 'refused')

print('removing')
theme_store.use('Night000001')
theme_store.remove('Night000001')
check('gone', (theme_store.theme_id('Night000001') in timeline_themes.available(),
               theme_store.theme_id('Night000001') in session.CONFIG['timeline']['installed']), (False, False))
check('the timeline goes back to the default theme', timeline_themes.current().id, timeline_themes.DEFAULT)
check('and it can be installed again', theme_store.state(SITE['Night000001'][0]), 'install')

print('the browser, in the Timeline tab')
dialog = timeline_theme_browser.ThemeBrowser()
check('nothing fetched before it is shown', dialog.cards, [])
dialog.show()
check('a card per theme subtitld.cc lists', [card.info['share_id'] for card in dialog.cards],
      ['Night000001', 'Day00000001', 'Alone000001'])
check('each with what it offers', [card.state for card in dialog.cards], ['install', 'use', 'install'])
card = dialog.cards[1]
check('the main action is the add-on cards\' slanted tab', card.main.property('class'), 'addon_card_tab')
check('the trash only while the pointer is on the card, the globe always',
      (card.remove.isHidden(), card.site.isHidden()), (True, False))
check('nothing to remove on one not installed', hasattr(dialog.cards[0], 'remove'), False)
changes = []
dialog.installed.connect(lambda: changes.append(1))
downloaded.clear()
dialog.cards[2].main.click()
check('Install downloads it and puts it on the timeline', (downloaded, timeline_themes.current().id),
      (['Alone000001'], theme_store.theme_id('Alone000001')))
check('and every card follows', [card.state for card in dialog.cards], ['install', 'use', 'in_use'])
check('the Timeline tab hears of it', len(changes), 1)
dialog.look.combobox.setCurrentIndex(dialog.look.combobox.findData('light'))
dialog.reload()
check('the look filter', [card.info['share_id'] for card in dialog.cards], ['Day00000001'])
dialog.show_theme('Night000001')
check('a theme link shows that theme and its pair', [card.info['share_id'] for card in dialog.cards],
      ['Night000001', 'Day00000001'])
check('without the search bar, with a way back', (dialog.bar.isHidden(), dialog.showing_one.isHidden()),
      (True, False))
dialog.show_all.click()
check('back to the list', (dialog.bar.isHidden(), len(dialog.cards)), (False, 1))

print()
print('ALL PASS' if not fails else f'FAILED: {fails}')
sys.exit(1 if fails else 0)
