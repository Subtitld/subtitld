"""Subtitle and title themes from subtitld.cc (modules/theme_store.py, its part for modules.themes;
interface/html_theme_browser.py): the client asks for a kind, the browse helper lists them, installing
writes the file (and its picture) under cc.<share ID in hex> and keeps its version, what a card offers
(install, update, use, in use) as that changes, removing one (never one of Subtitld's), a theme id back to
its share ID and forth, and the sections themselves: every theme there is here as a card (Subtitld's own
first, with their pictures, never removable), built lazily, cards for the themes not installed, Install
moving a card up among the others and into the pickers, the trash taking it out again.
subtitld.cc is a fake here: its client answers from memory, and background calls run at once.

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
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget
app = QApplication([])
from subtitld.modules import config, session, subtitle_styles, theme_store, themes, title_themes
from subtitld.modules import subtitldcc_service as service
from subtitld.modules.subtitldcc import client as api
from subtitld.interface import html_theme_browser

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def png():
    image = QImage(160, 90, QImage.Format_RGB32)
    image.fill(QColor('#335577'))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


PNG = png()


def theme_file(share_id, kind, name, version='1', animated=False):
    manifest = {'format': 1, 'id': theme_store.theme_id(share_id), 'kind': kind, 'name': name, 'version': version,
                'fields': [{'key': 'color', 'type': 'color', 'default': '#ffffffff'}]}
    body = ''
    if kind == 'title':
        manifest['slots'] = [{'key': 'main', 'type': 'text', 'label': 'Name'},
                             {'key': 'role', 'type': 'text', 'label': 'Role'}]
        body = '<div data-slot="main">Name</div><div data-slot="role">Role</div>'
    if animated:
        manifest['timing'] = {'intro': 300, 'outro': 200}
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
            f'</head><body>{body}</body></html>').encode()


def info(share_id, kind, title, version=1, animated=False, slots=()):
    return {'share_id': share_id, 'theme_id': theme_store.theme_id(share_id), 'kind': kind,
            'url': f'https://subtitld.cc/{share_id}', 'title': title, 'owner': 'marina', 'version': version,
            'updated_at': '2026-11-25T00:00:00Z', 'downloads': 4, 'rating': None, 'options': ['Colour'],
            'preview': {'width': 1920, 'height': 1080}, 'animated': animated, 'video': animated,
            'slots': list(slots), 'look': None, 'counterpart': None, 'colors': {}}


SITE = {
    'Boxed000000': (info('Boxed000000', 'subtitle', 'Boxed'), theme_file('Boxed000000', 'subtitle', 'Boxed')),
    'Karaoke0000': (info('Karaoke0000', 'subtitle', 'Karaoke', animated=True),
                     theme_file('Karaoke0000', 'subtitle', 'Karaoke', animated=True)),
    'Banner00000': (info('Banner00000', 'title', 'Banner', animated=True, slots=['Name', 'Role']),
                     theme_file('Banner00000', 'title', 'Banner', animated=True)),
}
asked = []
downloaded = []


class FakeClient:
    def themes(self, look='', sort='popular', q='', cursor=None, limit=24, kind=''):
        asked.append({'kind': kind, 'sort': sort, 'q': q})
        results = [dict(entry) for entry, _data in SITE.values() if entry['kind'] == (kind or 'timeline')
                   and (not q or q.lower() in entry['title'].lower())]
        return {'results': results, 'next_cursor': None, 'total': len(results)}

    def theme(self, share_id):
        return dict(SITE[share_id][0])

    def download_theme(self, share_id, version=None):
        downloaded.append((share_id, version))
        return SITE[share_id][1]

    def theme_preview(self, share_id, version=None):
        return PNG


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
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
themes.reload_looks()
BOXED, BANNER = theme_store.theme_id('Boxed000000'), theme_store.theme_id('Banner00000')

print('the client')
sent = []
real = api.Client('https://subtitld.example')
real.request = lambda method, path, **kwargs: sent.append((method, path, kwargs.get('params'))) or b''
real.themes(kind='title', sort='new', q='lower')
real.themes()
real.theme_video('Banner00000', 2)
check('a kind is passed when given', sent[0][2]['kind'], 'title')
check('and left out when not (old clients: timeline)', sent[1][2]['kind'], None)
check('an animated theme\'s video', sent[2][:2] + (sent[2][2]['version'],), ('GET', '/themes/Banner00000/video', 2))

print('ids')
check('a share ID to a theme id and back', theme_store.share_id_from(theme_store.theme_id('Boxed000000')),
      'Boxed000000')
check('not one from subtitld.cc', (theme_store.share_id_from('subtitld.classic'), theme_store.share_id_from('cc.12')),
      (None, None))

print('browsing')
answer = theme_store.browse('subtitle', sort='top', q='box')
check('only that kind, by name', [entry['share_id'] for entry in answer['results']], ['Boxed000000'])
check('asked of subtitld.cc so', asked[-1], {'kind': 'subtitle', 'sort': 'top', 'q': 'box'})
check('nothing installed yet', theme_store.file_state(SITE['Boxed000000'][0]), 'install')

print('installing')
file = theme_store.fetch_file(BOXED)
check('the latest version, with who published it', (file[0], file[1], file[3]), ('Boxed000000', 1, 'marina'))
check('a version asked for', theme_store.fetch_file(BOXED, 3)[1], 3)
check('fetch_file still feeds save_file as the missing-themes dialog does', theme_store.save_file(*file).id, BOXED)
check('installed among the user\'s own, under its id', (themes.folders()[1][0] / f'{BOXED}.html').is_file(), True)
check('its version kept in the config', session.CONFIG['themes']['installed'][BOXED],
      {'share_id': 'Boxed000000', 'version': 1, 'owner': 'marina'})
check('in the subtitle styles\' list', BOXED in subtitle_styles.available(), True)
check('to use', theme_store.file_state(SITE['Boxed000000'][0]), 'use')
check('a newer version on subtitld.cc is an update', theme_store.file_state(dict(SITE['Boxed000000'][0], version=2)),
      'update')
theme_store.use_file(BOXED)
check('Use: the project\'s subtitles drawn with it', (subtitle_styles.current_id(),
                                                      theme_store.file_state(SITE['Boxed000000'][0])), (BOXED, 'in_use'))
theme_store.save_file('Banner00000', 1, SITE['Banner00000'][1], 'marina', preview=PNG)
check('a title theme, with its picture beside it', (BANNER in title_themes.available(),
                                                    theme_store.preview_file(BANNER) is not None), (True, True))
check('installed, not the theme new titles get', theme_store.file_state(SITE['Banner00000'][0]), 'use')
theme_store.use_file(BANNER)
check('Use: new titles get it', (title_themes.current_id(), theme_store.file_state(SITE['Banner00000'][0])),
      (BANNER, 'in_use'))
check('the installed ones, by kind', ([t.id for t in theme_store.installed_files('subtitle')],
                                      [t.id for t in theme_store.installed_files('title')]), ([BOXED], [BANNER]))
try:
    theme_store.save_file('Karaoke0000', 1, SITE['Boxed000000'][1])
    check('a file under another id is refused', 'saved', 'refused')
except theme_store.StoreError:
    check('a file under another id is refused', 'refused', 'refused')

print('removing')
check('never one of Subtitld\'s', (theme_store.remove_file(subtitle_styles.DEFAULT_STYLE),
                                   subtitle_styles.DEFAULT_STYLE in subtitle_styles.available()), (False, True))
check('one from subtitld.cc', theme_store.remove_file(BOXED), True)
check('gone, with its record', (BOXED in subtitle_styles.available(), BOXED in session.CONFIG['themes']['installed'],
                                (themes.folders()[1][0] / f'{BOXED}.html').exists()), (False, False, False))
check('the project draws with Subtitld\'s own in its place', subtitle_styles.get().id, subtitle_styles.DEFAULT_STYLE)
check('and it can be installed again', theme_store.file_state(SITE['Boxed000000'][0]), 'install')

print('the section, fetched once scrolled to')
area = QScrollArea()
area.setWidgetResizable(True)
area.resize(420, 300)
page = QWidget()
QVBoxLayout(page)
spacer = QWidget()
spacer.setFixedHeight(2000)
page.layout().addWidget(spacer)
section = html_theme_browser.HtmlThemeBrowser('subtitle')
page.layout().addWidget(section)
area.setWidget(page)
area.show()
app.processEvents()
app.processEvents()
check('out of sight: nothing asked of subtitld.cc', (section.loaded, section.cards), (False, []))
area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
app.processEvents()
check('scrolled to: the subtitle themes not installed', [card.info['share_id'] for card in section.cards],
      ['Boxed000000', 'Karaoke0000'])
check('asked for subtitle themes', asked[-1]['kind'], 'subtitle')
check('no look to pick', section.look.isHidden(), True)
cards = {card.info['title']: card for card in section.cards}
check('Install on the slanted tab, its picture 16:9', (cards['Boxed'].main.text(), cards['Boxed'].preview.heightForWidth(320),
                                                     cards['Boxed'].preview.pixmap is not None), ('Install', 180, True))
check('an animated one says so on its label', (cards['Karaoke'].info['mark'], cards['Boxed'].info['mark']),
      ('Animated', ''))
BUILTIN_SUBTITLES = [theme.id for theme in themes.available('subtitle').values() if theme.builtin]
check('Subtitld\'s own first, each with its picture, none removable',
      ([card.info['theme_id'] for card in section.mine], all(card.preview.pixmap is not None for card in section.mine),
       any(card.remove is not None for card in section.mine), section.installed_box.isHidden()),
      (BUILTIN_SUBTITLES, True, False, False))
check('one in use: the project\'s', [card.state for card in section.mine].count('in_use'), 1)
changes, used = [], []
section.changed.connect(lambda: changes.append(1))
section.used.connect(lambda: used.append(1))
downloaded.clear()
cards['Karaoke'].main.click()
check('Install downloads it', downloaded, [('Karaoke0000', 1)])
KARAOKE = theme_store.theme_id('Karaoke0000')
check('into the picker\'s list, not in use yet', (KARAOKE in subtitle_styles.available(),
                                                  subtitle_styles.current_id() == KARAOKE), (True, False))
check('its card goes up after Subtitld\'s own', ([card.info['share_id'] for card in section.cards],
                                              [card.info['share_id'] for card in section.mine]),
      (['Boxed000000'], [None] * len(BUILTIN_SUBTITLES) + ['Karaoke0000']))
check('the panel hears of it', (len(changes), len(used)), (1, 0))
mine = section.mine[-1]
check('installed: Use, removable, its picture kept, its page', (mine.state, mine.remove is not None,
      mine.preview.pixmap is not None, mine.site is not None, mine.remove.isVisible()), ('use', True, True, True, False))
mine.main.click()
check('Use: the project draws with it', (subtitle_styles.current_id(), section.mine[-1].state, len(used)),
      (KARAOKE, 'in_use', 1))
html_theme_browser.remote['Karaoke0000'] = dict(SITE['Karaoke0000'][0], version=2)
section.build_installed()
check('a newer version on subtitld.cc: an update button', section.mine[-1].update_button is not None, True)
html_theme_browser.remote['Karaoke0000'] = dict(SITE['Karaoke0000'][0])
section.build_installed()
section.mine[-1].remove.click()
check('the trash takes it out', (KARAOKE in subtitle_styles.available(),
                                 [card.info['theme_id'] for card in section.mine]), (False, BUILTIN_SUBTITLES))
check('and it is on subtitld.cc to install again', [card.info['share_id'] for card in section.cards],
      ['Boxed000000', 'Karaoke0000'])
section.search.setText('box')
section.reload()
check('the name search', [card.info['share_id'] for card in section.cards], ['Boxed000000'])
section.search.setText('nothing like it')
section.reload()
check('no matches, said so', (section.cards, section.status.isHidden()), ([], False))
area.hide()

print('the title themes\' section and the theme new titles get')
titles = html_theme_browser.HtmlThemeBrowser('title')
check('the installed title theme has its card at once, after Subtitld\'s own',
      [card.info['share_id'] for card in titles.mine if card.info['share_id']], ['Banner00000'])
banner = next(card for card in titles.mine if card.info['share_id'])
check('in use: new titles get it; its texts said', (banner.state, banner.info['slots'], banner.info['mark']),
      ('in_use', ['Name', 'Role'], 'Animated'))
titles.show()
app.processEvents()
check('nothing else on subtitld.cc: said so', (titles.cards, titles.status.text()),
      ([], 'Every theme on subtitld.cc is installed here.'))
check('it is the theme new titles get', title_themes.current_id(), BANNER)
own = next(card for card in titles.mine if card.info['theme_id'] == title_themes.DEFAULT_THEME)
check('Subtitld\'s own: its name from the locale files, no page', (own.info['title'], own.info['url']),
      ('Plain', None))
own.main.click()
banner = next(card for card in titles.mine if card.info['share_id'])
check('Use on one of Subtitld\'s: new titles get it; Use again on the other', (title_themes.current_id(), banner.state),
      (title_themes.DEFAULT_THEME, 'use'))
titles.hide()

print()
print('ALL PASS' if not fails else f'FAILED: {fails}')
sys.exit(1 if fails else 0)
