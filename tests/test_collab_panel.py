"""The live co-editing card in the subtitld.cc panel (interface/subtitldcc_live.py), against a fake
subtitld.cc and a fake connection, so no network is used: sharing the open project, what it says when live,
offline or signed in from before co-editing, who is here, adding and removing people, opening a project
shared with you, and leaving.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QVBoxLayout, QWidget
app = QApplication([])
from subtitld.interface import left_panel, left_panel_subtitldcc as panel_module, top_bar
from subtitld.modules import collab_client, collab_service, collab_state, history, session
from subtitld.modules import subtitldcc_service as service

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def wait(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        # Rows rebuilt go with deleteLater, which the event loop does and processEvents() doesn't.
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        if condition():
            return True
        time.sleep(0.01)
    return False


calls = []
PROJECT = {'doc_id': 'Dk3pQ9aZ2bT', 'title': 'Sintel', 'seq': 0, 'owner': {'handle': 'marina', 'name': 'Marina', 'kind': 'user'},
           'members': [], 'websocket': 'wss://subtitld.cc/ws/collab/Dk3pQ9aZ2bT', 'permissions': {'can_manage': True}}


class FakeClient:
    user_agent = 'Subtitld/26.10 (Linux)'

    def meta(self):
        return {'languages': ['en'], 'language_names': {'en': 'English'}, 'licenses': []}

    def me(self):
        return {'handle': 'marina', 'name': 'Marina', 'publish_as': ['marina', 'subbers'], 'subtitle_languages': []}

    def share_project(self, title, state, *, owner='', idempotency_key=None):
        calls.append(('share', title, owner, [s['text'] for s in state['segments']], all(s.get('id') for s in state['segments'])))
        return dict(PROJECT, title=title)

    def project(self, doc_id):
        return PROJECT

    def projects(self, cursor=None):
        return {'results': [dict(PROJECT, doc_id='Other0000aa', title='Tears of Steel', owner={'handle': 'joao'},
                                 permissions={'can_manage': False})], 'next_cursor': None}

    def add_member(self, doc_id, handle):
        calls.append(('add', handle))
        PROJECT['members'] = PROJECT['members'] + [{'handle': handle, 'name': handle, 'kind': 'user'}]
        return PROJECT

    def remove_member(self, doc_id, handle):
        calls.append(('remove', doc_id, handle))
        PROJECT['members'] = [m for m in PROJECT['members'] if m['handle'] != handle]

    def close_project(self, doc_id):
        calls.append(('close', doc_id))

    def project_published(self, doc_id, share_id, version):
        calls.append(('published', doc_id, share_id, version))
        PROJECT['subtitle'] = {'share_id': share_id, 'title': 'Sintel <2010>', 'version': version, 'can_edit': True}
        return dict(PROJECT, coeditors=['joao', 'marina'])

    def asset(self, share_id):
        return {'share_id': share_id, 'title': 'Sintel', 'version': 2, 'language': 'en', 'owner': 'marina',
                'permissions': {'can_edit': True, 'can_share': True, 'can_rate': False, 'can_delete': True},
                'my_rating': None, 'rating': None, 'downloads': 0}


class FakeStore:
    account = 'subtitld.cc'
    token = 'sct_test'

    def load(self):
        return self.token

    def device_id(self):
        return 'test'

    def clear(self):
        self.token = None


class FakeConnection(QObject):
    changed = Signal(str)
    failed = Signal(str)

    def __init__(self, url, token, user_agent):
        super().__init__()
        calls.append(('connect', url))
        self.engine = types.SimpleNamespace(peers={}, conflicts=[], state='live')

    @property
    def state(self):
        return self.engine.state

    def start(self):
        pass

    def stop(self):
        calls.append(('disconnect',))


service.client = lambda token=None: FakeClient()
service._store = FakeStore()
collab_client.Connection = FakeConnection


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(420, 900)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.layout().addWidget(self.left_panel_navigation)
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.titleBar_left_information_label = QLabel()


session.CONFIG = {}
session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Hello'}, {'start': 3.0, 'end': 4.0, 'text': 'Bye'}],
                    'filepath': '', 'language': 'en'}
session.SPEAKERS = {}
session.VIDEO = {'filepath': '/videos/Sintel.2010.mkv'}
history.history_clear()
service.remember_account(FakeClient().me())
top_bar.update = lambda window: None
host = Host()
panel_module.load(host)
panel_module.translate(host)
host.show()
panel = host.subtitldcc_panel
card = panel.live_card
panel.refresh()

def buttons(text):
    return [b for b in card.findChildren(type(card.add_button)) if b.text() == text and b.isVisible()]


print('signed in, not shared')
check('the card shows', card.isVisible(), True)
check('the share form, named after the video', (card.share_box.isVisible(), card.title_field.text()), (True, 'Sintel.2010'))
check('yourself or a group you publish as', [card.owner_field.itemText(i) for i in range(card.owner_field.count())],
      ['@marina', '@subbers'])

print('sharing')
card.owner_field.setCurrentIndex(1)
card.title_field.setText('Sintel')
card.share_button.click()
check('subtitld.cc gets the document, every subtitle with its ID', wait(lambda: collab_state.shared()), True)
check('as the group, with the subtitles', calls[0], ('share', 'Sintel', 'subbers', ['Hello', 'Bye'], True))
check('it connects', calls[1][0], 'connect')
check('shared: the members part, not the form', wait(lambda: card.members_title.isVisible()), True)
check('connected', card.status.text(), 'Connected. Changes reach everyone as you make them.')
check('the owner manages it', (card.add_row.isVisible(), card.stop_button.text()), (True, 'Stop sharing'))

print('who is here')
connection = collab_service.connection()
connection.engine.peers['p1'] = {'user': {'handle': 'joao'}, 'state': {}}
connection.changed.emit('peers')
check('someone joined', card.here.text(), 'Here now: @joao')
connection.engine.conflicts.append({'cue_id': 'x', 'field': 'text', 'theirs': 'x', 'author': 'joao'})
connection.changed.emit('conflicts')
check('a change crossed', (card.conflicts.isVisible(), card.conflicts.text()), (True, '1 of your changes crossed someone else’s.'))
connection.engine.conflicts.clear()

print('collaborators in the editor')
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QTextEdit
from subtitld.interface import left_panel_subtitleslist, timeline
first = session.SUBTITLE['segments'][0]
connection.engine.peers['p1']['state'] = {'selected': first['id'], 'playhead': 3.5, 'editing': first['id']}
check('each with a colour of their own, the same everywhere',
      (collab_service.collaborators()[0]['color'], collab_service.color_for('joao')) ,
      (collab_service.color_for('joao'), collab_service.color_for('joao')))
canvas = QImage(800, 120, QImage.Format_ARGB32)
canvas.fill(QColor('#000000'))
stub = types.SimpleNamespace(width_proportion=100.0, subtitle_y=40, subtitle_height=40, height=lambda: 120)
painter = QPainter(canvas)
timeline.Timeline._paint_collaborators(stub, painter)
painter.end()
mine = QColor(collab_service.color_for('joao'))
drawn = {canvas.pixelColor(x, y).name() for x in range(800) for y in range(120)}
check('the timeline draws them in their colour', mine.name() in drawn or any(
    abs(QColor(c).red() - mine.red()) < 30 and abs(QColor(c).green() - mine.green()) < 30 and abs(QColor(c).blue() - mine.blue()) < 30
    for c in drawn if c != '#000000'), True)

connection.engine.locks = {first['id']: 'p1'}
connection.engine.locked_by = lambda cue: {'handle': 'joao'} if connection.engine.locks.get(cue) == 'p1' else None
editor = types.SimpleNamespace(left_panel_subtitleslist_textedit=QTextEdit(),
                               left_panel_subtitleslist_formatting_bar=QWidget(),
                               left_panel_subtitleslist_locked_notice=QLabel())
session.SUBTITLE['selected'] = first
left_panel_subtitleslist.refresh_lock(editor)
check('someone typing: the text can\'t be typed in here, and says who',
      (editor.left_panel_subtitleslist_textedit.isReadOnly(), editor.left_panel_subtitleslist_locked_notice.text()),
      (True, '@joao is typing in this subtitle. You can type in it once they stop.'))
connection.engine.locks = {}
left_panel_subtitleslist.refresh_lock(editor)
check('and can be once they stop', editor.left_panel_subtitleslist_textedit.isReadOnly(), False)

print('conflicts')
connection.engine.conflicts[:] = [{'cue_id': first['id'], 'field': 'text', 'mine': first['text'], 'theirs': 'Olá <b>', 'author': 'joao'},
                                  {'client_op_id': 'x-1', 'reason': 'conflict', 'detail': '', 'ops': []}]
connection.changed.emit('conflicts')
wait(lambda: False, 0.2)
check('both listed', wait(lambda: len(buttons('Use theirs')) == 1 and buttons('OK')), True)
check('their text shown as text', any('Olá &lt;b&gt;' in w.text() for w in card.findChildren(QLabel)), True)
undo_steps = len(history.ALL_HISTORY)
buttons('Use theirs')[0].click()
check('theirs, as an edit here (undoable)', (first['text'], len(history.ALL_HISTORY)), ('Olá <b>', undo_steps + 1))
buttons('OK')[0].click() if wait(lambda: buttons('OK')) else None
check('settled', collab_service.conflicts(), [])
session.SUBTITLE['selected'] = None

print('publishing from the project')
card.published('Xk3pQ9aZ2bT', 2)
check('subtitld.cc hears of it', wait(lambda: ('published', 'Dk3pQ9aZ2bT', 'Xk3pQ9aZ2bT', 2) in calls), True)
check('and the others are credited', wait(lambda: panel.message.text() == 'Published, crediting @joao.'), True)
check('the editor holds that subtitle now', service.origin(), {'share_id': 'Xk3pQ9aZ2bT', 'version': 2})
check('the card says where it publishes, the title as text', wait(lambda: card.publishes_to.isVisible()
                                                                and '“Sintel &lt;2010&gt;”' in card.publishes_to.text()), True)
PROJECT['subtitle'] = dict(PROJECT['subtitle'], version=3)
card._project = None
card.refresh()
check('a newer version, published by someone else: the editor follows', wait(lambda: (service.origin() or {}).get('version') == 3), True)
PROJECT.pop('subtitle')
card._project = None

print('offline')
connection.engine.state = 'offline'
collab_state.queue([{'op': 'meta.set', 'field': 'language', 'value': 'pt'}])
connection.changed.emit('state')
waiting = len(collab_state.STATE['pending'])
check('changes wait (the one above, and using their text)', (waiting, card.status.text()),
      (2, f'Offline. Your changes are kept ({waiting} waiting) and sent when the connection is back.'))

print('a sign-in from before co-editing')
connection.failed.emit('missing_scope')
check('asks to sign in again', (card.sign_in_button.isVisible(), card.status.text().startswith('Your subtitld.cc sign-in')), (True, True))

print('who it is shared with')
card.member_field.setText('@joao')
card.add_button.click()
check('added', wait(lambda: ('add', 'joao') in calls), True)
check('listed', wait(lambda: '@joao' in [w.text() for w in card.findChildren(QLabel)]), True)
check('with a Remove button for the owner', wait(lambda: buttons('Remove')), True)
buttons('Remove')[0].click()
check('removing asks first', card.confirm.isVisible(), True)
card.confirm_yes.click()
check('removed', wait(lambda: ('remove', 'Dk3pQ9aZ2bT', 'joao') in calls), True)

print('stop sharing')
card.stop_button.click()
card.confirm_yes.click()
check('closed for everyone', wait(lambda: ('close', 'Dk3pQ9aZ2bT') in calls), True)
check('the project stays, not shared', wait(lambda: not collab_state.shared()) and len(session.SUBTITLE['segments']), 2)
check('disconnected', ('disconnect',) in calls, True)
check('the share form again', wait(lambda: card.share_box.isVisible()), True)

print('opening a project shared with you')
card.list_button.click()
check('listed', wait(lambda: buttons('Open')), True)
buttons('Open')[0].click()
check('asks first: it replaces the subtitles', card.confirm.isVisible(), True)
card.confirm_yes.click()
check('joined: shared, empty until the server sends it, not a file yet',
      (collab_state.STATE['doc'], session.SUBTITLE['segments'], session.SUBTITLE['filepath']), ('Other0000aa', [], ''))
check('connects to it', calls[-1], ('connect', collab_client.websocket_url(service.base_url(), 'Other0000aa')))

print('leaving it')
PROJECT['permissions'] = {'can_manage': False}
card._project = None
card.refresh()
wait(lambda: card.stop_button.text() == 'Leave')
card.stop_button.click()
card.confirm_yes.click()
check('leaving is removing oneself', wait(lambda: ('remove', 'Other0000aa', 'marina') in calls), True)

print('signed out')
service._store.clear()
panel.refresh()
check('the card hides', card.isVisible(), False)

collab_service.shutdown()
print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
