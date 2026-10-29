"""Opening a shared project from a subtitld:// link (subtitld.cc's "Open in Subtitld"), with no file of it
here: reading links, handing one to the Subtitld already running, and the start screen's card
(interface/subtitldcc_open.py) connecting, taking the project, getting the video from a collaborator and
opening the editor; with a project open, the subtitld.cc panel asking first. Against a fake subtitld.cc, a
fake connection and fake transfers: no network.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, threading, time, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ.pop('SUBTITLDCC_BASE_URL', None)
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
os.environ['USER'] = f'link-test-{os.getpid()}'  # a hand-over socket of its own
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QStackedLayout, QStackedWidget, QVBoxLayout, QWidget
app = QApplication([])
from subtitld.interface import left_panel, left_panel_subtitldcc as panel_module, startscreen, subtitldcc_open, top_bar
from subtitld.modules import collab_client, collab_media, collab_service, collab_state, history, link_handover, session
from subtitld.modules import subtitldcc_links as links
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
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        if condition():
            return True
        time.sleep(0.01)
    return False


print('reading links')
DOC = 'Dk3pQ9aZ2bT'
check('a shared project', links.parse(f'subtitld://subtitld.cc/project/{DOC}'), {'site': 'subtitld.cc', 'project': DOC})
check('a local subtitld.cc, with its port', links.parse(f'subtitld://localhost:8000/project/{DOC}/'),
      {'site': 'localhost:8000', 'project': DOC})
check('anything else is no link', [links.parse(text) for text in (
    'subtitld://subtitld.cc/project/short', f'subtitld://subtitld.cc/asset/{DOC}', f'subtitld:///project/{DOC}',
    f'https://subtitld.cc/project/{DOC}', f'subtitld://subtitld.cc/project/{DOC}/more')], [None] * 5)
check('only for the subtitld.cc this Subtitld connects to', (
    links.for_site(links.parse(f'subtitld://subtitld.cc/project/{DOC}'), 'https://subtitld.cc'),
    links.for_site(links.parse(f'subtitld://evil.example/project/{DOC}'), 'https://subtitld.cc'),
    links.for_site(links.parse(f'subtitld://localhost:8000/project/{DOC}'), 'http://localhost:8000/')), (True, False, True))
check('a file:// URI from a desktop entry is a path', links.file_path('file:///home/ana/Sintel%20(2010).usfx'),
      '/home/ana/Sintel (2010).usfx')
check('a path stays a path', links.file_path('/home/ana/a.srt'), '/home/ana/a.srt')

print('handing a link to the Subtitld already running')
check('no one to hand it to', link_handover.hand_over(['subtitld://x/project/y']), False)
listener = link_handover.Listener()
check('this one takes them', listener.listen(), True)
check('another one does not take them too', link_handover.Listener().listen(), False)
got = []
listener.received.connect(got.append)
link = f'subtitld://subtitld.cc/project/{DOC}'

# The other Subtitld blocks while it sends: it runs on a thread of its own here.
result = []
sender = threading.Thread(target=lambda: result.append(link_handover.hand_over([link, 'not a link'])))
sender.start()
wait(lambda: got, 5)
sender.join(5)
check('handed over', result, [True])
check('the running one gets the link, and only it', got, [link])
listener.close()

# --- A fake subtitld.cc and connection ---------------------------------------------------------------------

calls = []
HELD = set()
VIDEO_HASH = 'v' * 64
PROJECT = {'doc_id': DOC, 'title': 'Sintel', 'seq': 0, 'owner': {'handle': 'marina', 'name': 'Marina', 'kind': 'user'},
           'members': [{'handle': 'ana', 'name': 'Ana', 'kind': 'user'}], 'websocket': f'wss://subtitld.cc/ws/collab/{DOC}',
           'permissions': {'can_manage': False}, 'url': f'https://subtitld.cc/projects/{DOC}'}
MEDIA = []
ANSWER = {}


class FakeClient:
    user_agent = 'Subtitld/26.10 (Linux)'

    def meta(self):
        return {'languages': ['en'], 'language_names': {'en': 'English'}, 'licenses': []}

    def me(self):
        return {'handle': 'ana', 'name': 'Ana', 'publish_as': ['ana'], 'subtitle_languages': []}

    def project(self, doc_id):
        calls.append(('project', doc_id))
        if ANSWER.get('project'):
            raise ANSWER['project']
        return dict(PROJECT, doc_id=doc_id)

    def projects(self, cursor=None):
        return {'results': [], 'next_cursor': None}

    def project_media(self, doc_id):
        return [dict(item) for item in MEDIA]

    def project_media_file(self, doc_id, file_hash):
        return dict(next(item for item in MEDIA if item['hash'] == file_hash), chunks=['c' * 64])

    def add_project_media(self, doc_id, **description):
        return description

    def media_received(self, doc_id, file_hash, handle):
        calls.append(('received', file_hash, handle))


class FakeStore:
    account = 'subtitld.cc'
    token = None

    def load(self):
        return self.token

    def device_id(self):
        return 'test'

    def save(self, token):
        self.token = token

    def clear(self):
        self.token = None


class FakeConnection(QObject):
    changed = Signal(str)
    failed = Signal(str)

    def __init__(self, url, token, user_agent):
        super().__init__()
        calls.append(('connect', url))
        self.engine = types.SimpleNamespace(peers={}, conflicts=[], state='joining', media={}, have_hashes=[],
                                            listeners=[], holders=lambda file_hash: ['p1'] if file_hash in HELD else [],
                                            have=lambda hashes: None, rtc=lambda peer, payload: None,
                                            you={'handle': 'ana', 'name': 'Ana'}, peer='me0',
                                            presence=lambda state: None, ice=[], LIVE='live',
                                            chat=lambda message: True, chat_history_request=lambda peer: None,
                                            chat_history=lambda peer, messages: None)

    @property
    def state(self):
        return self.engine.state

    def start(self):
        pass

    def stop(self):
        calls.append(('disconnect',))


class FakeTransfers:
    """collab_transfer.Manager's side the service uses: downloads asked for, cancelled, and one arriving."""

    def __init__(self):
        self.downloads = {}
        self.bridge = types.SimpleNamespace()

    def download(self, media, destination):
        calls.append(('download', media['hash'], destination))
        self.downloads[media['hash']] = types.SimpleNamespace(
            state='downloading', kind=media['kind'], size=media['size'], chunk_size=media['chunk_size'],
            chunks=media['chunks'], sources={'p1': media['size']}, destination=destination, fraction=lambda: 0.4)

    def cancel(self, file_hash):
        calls.append(('cancel', file_hash))
        self.downloads.pop(file_hash, None)

    def arrive(self, file_hash):
        download = self.downloads[file_hash]
        Path(download.destination).write_bytes(b'video bytes')
        download.state = 'done'
        collab_service._received(file_hash, download.destination)

    def tick(self):
        pass

    def close(self):
        pass


def start_transfers():
    collab_service._transfers = FakeTransfers()


service.client = lambda token=None: FakeClient()
service._store = FakeStore()
collab_client.Connection = FakeConnection
collab_service._start_transfers = start_transfers
collab_service.transfers_available = lambda: True
signed_in = []


def fake_sign_in(device_code_shown=None, cancelled=lambda: False):
    signed_in.append(True)
    service._store.save('sct_test')
    return FakeClient().me()


service.sign_in = fake_sign_in
top_bar.update = lambda window: None
folder = tempfile.mkdtemp()
opened = []


class Host(QWidget):
    """The window: the start screen and the editor in one stack, and the left panel."""

    def __init__(self):
        super().__init__()
        self.resize(900, 900)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.layout().addWidget(self.left_panel_navigation)
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.titleBar_left_information_label = QLabel()
        self.preview_panel_player = types.SimpleNamespace(_audio_device=types.SimpleNamespace(duck=1.0))
        self.central_widget = QWidget()
        self.central_widget.setLayout(QStackedLayout())
        self.layout().addWidget(self.central_widget)
        self.start_screen = QWidget()
        self.start_screen.setLayout(QVBoxLayout())
        self.start_screen.layout().addStretch()
        self.start_screen.layout().addWidget(QLabel('recent files'))
        self.central_widget.layout().addWidget(self.start_screen)
        self.main_vertical_splitter = QWidget()
        self.central_widget.layout().addWidget(self.main_vertical_splitter)


def load_productionscreen(window, collab=None):
    """What opening the editor does to the project: it is loaded anew (startscreen.load_productionscreen)."""
    opened.append((session.VIDEO.get('filepath'), session.SUBTITLE.get('filepath')))
    history.history_clear()
    if collab is not None:
        collab_state.opening(collab)
    session.document_loaded()
    window.central_widget.layout().setCurrentWidget(window.main_vertical_splitter)


startscreen.load_productionscreen = load_productionscreen
session.CONFIG = {'subtitldcc': {'media_folder': folder}}
session.SUBTITLE = {'segments': [], 'filepath': ''}
session.SPEAKERS = {}
session.VIDEO = {}
history.history_clear()
host = Host()
panel_module.load(host)
panel_module.translate(host)
subtitldcc_open.load(host)
host.show()
card = host.open_link_card
live_card = host.subtitldcc_panel.live_card


def shown(button):
    return button.isVisible() and button.text()


print('a link to another site, or no link')
subtitldcc_open.open_link(host, f'subtitld://evil.example/project/{DOC}')
check('not followed: its token stays here', (card.isVisible(), card.status.text(), calls),
      (True, 'This link is for evil.example, but this Subtitld connects to subtitld.cc.', []))
card.cancel_button.click()
check('closed', card.isVisible(), False)

print('not connected to subtitld.cc')
subtitldcc_open.open_link(host, f'subtitld://subtitld.cc/project/{DOC}')
check('connect first', (card.status.text(), shown(card.primary)),
      ('Connect to subtitld.cc to open this project. Your browser opens to confirm it.', 'Connect'))
card.primary.click()
check('connected, the project comes', wait(lambda: collab_state.shared()), True)
check('signed in once, then asked for the project', (signed_in, calls[0]), ([True], ('project', DOC)))
check('it is the open project now, connected', (collab_state.STATE['doc'], calls[1]),
      (DOC, ('connect', f'wss://subtitld.cc/ws/collab/{DOC}')))
check('saving asks where: no file yet', (session.SUBTITLE['filepath'], session.UNSAVED), ('', True))
check('the card says whose it is', (card.title.text(), card.status.text()),
      ('Sintel', 'Shared by @marina<br>Getting the subtitles…'))

print('live, looking for the video')
connection = collab_service.connection()
session.SUBTITLE['segments'] = [{'id': 'a', 'start': 1.0, 'end': 2.0, 'text': 'Hello'}]
connection.engine.state = 'live'
MEDIA.append({'hash': VIDEO_HASH, 'kind': 'video', 'name': 'Sintel.2010.mkv', 'size': 3 * 1024 ** 3,
              'chunk_size': 4 * 1024 ** 2, 'fingerprint': 'f' * 64})
connection.changed.emit('state')
check('the subtitles are here', wait(lambda: '1 subtitles from subtitld.cc' in card.status.text()), True)
check('no one who shares the video is online: waiting', wait(lambda: 'Waiting for one to be online' in card.video.text()), True)
check('a copy of one\'s own is offered', shown(card.secondary), 'Use a video I have')
check('where it will go', card.folder.text(), f'Into {folder}')

print('a collaborator who shares it comes online')
HELD.add(VIDEO_HASH)
connection.changed.emit('peers')
check('the video is asked for at once, into the folder', wait(lambda: any(c[0] == 'download' for c in calls)), True)
download = next(c for c in calls if c[0] == 'download')
check('under its own name', download[1:], (VIDEO_HASH, os.path.join(folder, 'Sintel.2010.mkv')))
collab_service._tell('media')
check('receiving, with progress', wait(lambda: 'Receiving Sintel.2010.mkv (3.0 GB) from collaborators: 40%' in card.video.text()), True)
check('the bar', (card.progress.isVisible(), card.progress.value()), (True, 400))
check('collaborators\' edits wait for the editor', host.central_widget.layout().currentWidget() is host.start_screen, True)

print('the video is here')
collab_service._transfers.arrive(VIDEO_HASH)
check('the editor opens with it', wait(lambda: opened), True)
check('the received video, the project not a file', opened, [(os.path.join(folder, 'Sintel.2010.mkv'), '')])
check('still the shared project, connected again', (collab_state.shared(), collab_state.STATE['doc'], calls[-1][0]),
      (True, DOC, 'connect'))
check('the card is gone', card.isVisible(), False)
check('known here from now on', collab_media.index().path_for(VIDEO_HASH), os.path.join(folder, 'Sintel.2010.mkv'))
check('the subtitld.cc panel shows', wait(lambda: host.left_panel_stackedwidgets.currentWidget() is not None and
                                      host.left_panel_stackedwidgets.currentWidget().property('tab_name') == 'subtitldcc'), True)
check('subtitld.cc heard who sent it', wait(lambda: ('received', VIDEO_HASH, '') in calls), True)

print('with a project open, a link to it again')
live_card._project = PROJECT
live_card.refresh(load=False)
subtitldcc_open.open_link(host, f'subtitld://subtitld.cc/project/{DOC}')
check('already open', host.subtitldcc_panel.message.text(), 'This project is already open.')
check('copy its link', shown(live_card.copy_link_button), 'Copy link')
live_card.copy_link_button.click()
check('the page\'s address', QApplication.clipboard().text(), f'https://subtitld.cc/projects/{DOC}')

print('with a project open, a link to another one')
OTHER = 'Other0000aa'
subtitldcc_open.open_link(host, f'subtitld://subtitld.cc/project/{OTHER}')
check('asked first, in the panel', wait(lambda: live_card.confirm.isVisible()), True)
check('what it asks', live_card.confirm_text.text(),
      'Open “Sintel”? It takes the place of the subtitles in the editor, and your unsaved changes are lost.')
live_card.confirm_yes.click()
check('then it opens', wait(lambda: collab_state.STATE['doc'] == OTHER), True)

print('a project not shared with you')
from subtitld.modules.subtitldcc import ApiError
host.central_widget.layout().setCurrentWidget(host.start_screen)  # as when Subtitld starts
collab_service.drop()
session.VIDEO = {}
ANSWER['project'] = ApiError(404, 'not_found', 'Not found.')
subtitldcc_open.open_link(host, f'subtitld://subtitld.cc/project/{DOC}')
check('said so', wait(lambda: 'isn’t shared with you' in card.status.text()), True)
check('nothing is open', (collab_state.shared(), session.UNSAVED), (False, False))
del ANSWER['project']

print('giving up while it waits for the video')
HELD.clear()
calls.clear()
os.remove(os.path.join(folder, 'Sintel.2010.mkv'))  # else it is here, and the editor opens with it
card.secondary.click()  # Try again
check('taken again', wait(lambda: collab_state.shared()), True)
collab_service.connection().engine.state = 'live'
collab_service.connection().changed.emit('state')
check('waiting for the video', wait(lambda: 'Waiting for one to be online' in card.video.text()), True)
card.cancel_button.click()
check('nothing stays open, nothing to save', (collab_state.shared(), session.SUBTITLE['segments'], session.UNSAVED,
                                              card.isVisible()), (False, [], False, False))
check('disconnected', calls[-1], ('disconnect',))

print('using a video of one\'s own')
own = os.path.join(folder, 'mine.mkv')
Path(own).write_bytes(b'mine')
subtitldcc_open.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (own, ''))
opened.clear()
subtitldcc_open.open_link(host, f'subtitld://subtitld.cc/project/{DOC}')
check('taken', wait(lambda: collab_state.shared()), True)
card.secondary.click()
check('the editor opens with it', opened, [(own, '')])
check('shared still', collab_state.shared(), True)

print('a free name for a received file')
check('a name already taken', collab_media.free_path(folder, 'mine.mkv'), os.path.join(folder, 'mine (2).mkv'))
check('a free one', collab_media.free_path(folder, 'Other.mkv'), os.path.join(folder, 'Other.mkv'))

collab_service.shutdown()
print('\nALL PASS' if not fails else f'\nFAILED: {fails}')
sys.exit(1 if fails else 0)
