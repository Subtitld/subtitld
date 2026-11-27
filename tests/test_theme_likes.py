"""Liking and reporting themes from their cards (interface/timeline_theme_browser.py: ThemeCard's heart and
flag, take_part, ReportDialog; used by the Timeline, Subtitles and Titles tabs): a card from subtitld.cc
has a heart with its likes and a flag, one of Subtitld's own has neither; the heart likes the theme and
takes the like back, every card of that theme (installed or not) shows it, and what subtitld.cc said is
kept for the cards built after; signed out, both ask to sign in first and then go on; the report dialog
offers subtitld.cc's reasons, says what it is sent from, and sends nothing until a reason is picked; a
report sent twice in a day says so; the sort names the most liked.
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
from PySide6.QtWidgets import QApplication, QRadioButton
app = QApplication([])
from subtitld import __version__
from subtitld.modules import config, session, theme_store, themes
from subtitld.modules import subtitldcc_service as service
from subtitld.modules.subtitldcc.client import ApiError
from subtitld.interface import html_theme_browser, timeline_theme_browser
from subtitld.interface.timeline_theme_browser import ReportDialog, ThemeCard

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def theme_file(share_id, name):
    manifest = {'format': 1, 'id': theme_store.theme_id(share_id), 'kind': 'subtitle', 'name': name,
                'version': '1', 'fields': []}
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
            '</head><body></body></html>').encode()


def info(share_id, title, likes=2, liked=None):
    return {'share_id': share_id, 'theme_id': theme_store.theme_id(share_id), 'kind': 'subtitle',
            'url': f'https://subtitld.cc/{share_id}', 'title': title, 'owner': 'marina', 'version': 3,
            'updated_at': '2026-11-25T00:00:00Z', 'downloads': 4, 'rating': None, 'likes': likes, 'liked': liked,
            'options': [], 'preview': {'width': 1920, 'height': 1080}, 'animated': False, 'video': False,
            'slots': [], 'look': None, 'counterpart': None, 'colors': {}}


SITE = {'Boxed000000': info('Boxed000000', 'Boxed'), 'Neon0000000': info('Neon0000000', 'Neon', likes=0)}
likes = {'Boxed000000': 2, 'Neon0000000': 0}
liked = set()
asked, sent = [], []
connected = [False]


class FakeClient:
    def themes(self, look='', sort='popular', q='', cursor=None, limit=24, kind=''):
        asked.append(sort)
        signed = connected[0]
        results = [{**entry, 'likes': likes[entry['share_id']],
                    'liked': (entry['share_id'] in liked) if signed else None} for entry in SITE.values()]
        return {'results': results, 'next_cursor': None, 'total': len(results)}

    def theme(self, share_id):
        return dict(SITE[share_id])

    def download_theme(self, share_id, version=None):
        return theme_file(share_id, SITE[share_id]['title'])

    def theme_preview(self, share_id, version=None):
        return b''

    def like_theme(self, share_id):
        if share_id not in liked:
            liked.add(share_id)
            likes[share_id] += 1
        return {'likes': likes[share_id], 'liked': True}

    def unlike_theme(self, share_id):
        if share_id in liked:
            liked.discard(share_id)
            likes[share_id] -= 1
        return {'likes': likes[share_id], 'liked': False}

    def report_theme(self, share_id, reason, details='', *, version=None, client=''):
        if any(entry[0] == share_id for entry in sent):
            raise ApiError(409, 'already_reported', 'You already reported this theme today.')
        sent.append((share_id, reason, details, version, client))
        return {'status': 'received'}


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


signing_in = []
service.client = lambda token=None: FakeClient()
service.call = call_now
service.is_connected = lambda: connected[0]
service.sign_in = lambda *args, **kwargs: (signing_in.append(1), connected.__setitem__(0, True)) and {'handle': 'bob'}
service.remember_account = lambda me: None
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
themes.reload_looks()

print('the cards')
section = html_theme_browser.HtmlThemeBrowser('subtitle')
section.resize(420, 900)
section.reload()
cards = {card.info['share_id']: card for card in section.cards}
boxed = cards['Boxed000000']
check('a heart with its likes, not filled while signed out', (boxed.like.text(), boxed.info['liked']), ('2', None))
check('a flag beside the trash, while the pointer is on the card',
      (boxed.report is not None, boxed.report in boxed._hover_buttons), (True, True))
check('no stars in what is said of it', '★' in ' '.join(label.text() for label in boxed.findChildren(
      type(boxed.main)) if hasattr(label, 'text')), False)
own = next(card for card in section.mine if not card.info['share_id'])
check('one of Subtitld\'s own: no heart, no flag', (own.like, own.report), (None, None))
check('sorts: popular, new, most liked', [section.sort.combobox.itemData(i) for i in range(section.sort.combobox.count())],
      ['popular', 'new', 'liked'])

print('signed out')
asked_to = []
timeline_theme_browser.utils.SimpleDialog.exec = lambda self: asked_to.append(self.windowTitle() or 1) or 1
boxed.like.click()
cards = {card.info['share_id']: card for card in section.cards}
check('the heart asks to sign in, then likes it', (len(asked_to), signing_in, cards['Boxed000000'].info['liked'],
                                                   cards['Boxed000000'].like.text()), (1, [1], True, '3'))
check('what subtitld.cc said is kept', (html_theme_browser.remote['Boxed000000']['likes'],
                                         html_theme_browser.remote['Boxed000000']['liked']), (3, True))

print('signed in')
cards['Boxed000000'].like.click()
check('again: the like taken back', (cards['Boxed000000'].info['liked'], cards['Boxed000000'].like.text(),
                                     len(asked_to)), (False, '2', 1))
cards['Boxed000000'].like.click()
cards['Boxed000000'].main.click()            # Install: its card goes up among the installed ones
mine = next(card for card in section.mine if card.info['share_id'] == 'Boxed000000')
check('installed, its card still knows its likes', (mine.like.text(), mine.info['liked']), ('3', True))
mine.like.click()
check('liked from the installed card too', (mine.info['liked'], likes['Boxed000000']), (False, 2))

print('the report dialog')
dialog = ReportDialog(section, cards['Neon0000000'].info)
reasons = [radio.property('reason') for radio in dialog.findChildren(QRadioButton)]
check('subtitld.cc\'s reasons', reasons, ['broken', 'version', 'abuse', 'license', 'other'])
check('nothing to send before a reason is picked', dialog.accept_button.isEnabled(), False)
check('what it is sent from, said before', dialog.client.startswith(f'Subtitld {__version__} · ')
      and dialog.client.endswith('version 3 of the theme'), True)
dialog.findChild(QRadioButton, 'theme_report_broken').click()
dialog.details.setPlainText('The last line is cut off.')
check('a reason picked: it can be sent', dialog.accept_button.isEnabled(), True)
dialog.accept()
check('sent to subtitld.cc', sent, [('Neon0000000', 'broken', 'The last line is cut off.', 3, dialog.client)])
check('said so', (section.status.text(), section.status.isHidden()), ('Report sent. A moderator will review it.', False))

print('the flag')


def exec_report(self):
    self.findChild(QRadioButton, 'theme_report_other').click()
    self.accept()
    return 1


ReportDialog.exec = exec_report
cards = {card.info['share_id']: card for card in section.cards}
cards['Neon0000000'].report.click()
check('twice in a day: subtitld.cc says so', section.status.text(),
      'The report couldn’t be sent: You already reported this theme today.')

print('a timeline theme\'s card')
card = ThemeCard(timeline_theme_browser.remote_info({**info('Night000000', 'Night'), 'look': 'dark'}))
check('a heart there too, its likes beside it', (card.like is not None, card.like.text()), (True, '2'))

print()
print('ALL PASS' if not fails else f'FAILED: {fails}')
sys.exit(1 if fails else 0)
