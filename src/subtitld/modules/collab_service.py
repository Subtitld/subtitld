"""Live co-editing in Subtitld: sharing the open project, joining a shared one, who it is shared with, and its
connection (modules.collab_client), which runs while a shared project is open.

The calls to subtitld.cc run in the background (subtitldcc_service.call); everything else here is main-thread
work on the open project.
"""

import hashlib
import logging
import time

from subtitld.modules import collab_state, cue_ids, docops, history, session
from subtitld.modules import subtitldcc_service as service

log = logging.getLogger(__name__)

# What this person is doing goes to the others at most this often, when it changes.
PRESENCE_MS = 500
# A text lock is asked for again this often while typing (the server lets it go after 15 s), and given up
# after this long without a keystroke.
LOCK_RENEW_SECONDS = 5
TYPING_IDLE_SECONDS = 10
# Collaborators' colours on the timeline: picked by handle, so each person keeps theirs everywhere.
COLORS = ('#ff9f43', '#2ed573', '#1e90ff', '#ff6b81', '#eccc68', '#a29bfe', '#7bed9f', '#70a1ff', '#ffa502', '#fd79a8')

_connection = None
_presence_timer = None
_last_presence = None
_editing = None          # the subtitle ID this person is typing in
_last_typed = 0.0
_last_lock = 0.0
_media_files = []        # the open project's media here, described (collab_media.scan)
_media_scan = None       # the background scan running, if one is
# callback(kind): 'state', 'document', 'peers', 'locks', 'conflicts' (from the connection), 'shared' (the
# project became shared or stopped being), 'failed' (the connection gave up: see failure()).
listeners = []
_failure = None


def _tell(kind):
    for callback in list(listeners):
        try:
            callback(kind)
        except Exception:
            log.exception('collab: a listener failed')


def connection():
    """The open shared project's connection, or None."""
    return _connection


def failure():
    """Why the connection gave up ('missing_scope': sign in again; 'not_found': not shared with you any more;
    'sign_in_required'), or None."""
    return _failure


def state():
    """'offline', 'joining', 'live', or None for a project that is not shared."""
    if not collab_state.shared():
        return None
    return _connection.state if _connection is not None else 'offline'


# --- The connection follows the open project ----------------------------------------------------------


def _stop():
    global _connection, _editing, _last_presence, _media_files
    _editing, _last_presence, _media_files = None, None, []
    if _presence_timer is not None:
        _presence_timer.stop()
    if _connection is not None:
        _connection.stop()
        _connection.deleteLater()
        _connection = None


def connect():
    """(Re)connect the open project if it is shared and someone is signed in."""
    global _connection, _failure
    from subtitld.modules import collab_client

    _stop()
    _failure = None
    token = service.store().load() if collab_state.shared() else None
    if not token:
        _tell('state')
        return
    url = collab_client.websocket_url(collab_state.STATE.get('server') or service.base_url(), collab_state.STATE['doc'])
    _connection = collab_client.Connection(url, token, service.client(token=token).user_agent)
    _connection.changed.connect(_connection_news)
    _connection.failed.connect(_failed)
    _connection.start()
    _start_presence()
    _tell('state')


def _connection_news(kind):
    if kind == 'state' and _connection is not None and _connection.state == 'live':
        share_media()
    _tell(kind)


def _failed(reason):
    global _failure
    _failure = reason
    log.warning('collab: the connection gave up: %s', reason)
    if reason == 'not_found':
        # Closed, or no longer shared with this person: the project stays here, as a project of their own.
        collab_state.clear()
        session.set_unsaved(True)
        _tell('shared')
    _tell('failed')


def _project_loaded():
    if collab_state.shared():
        connect()
    else:
        _stop()
        _tell('state')


session._document_loaded_callbacks.append(_project_loaded)


def shutdown():
    """Quitting: close the connection (what is queued stays in the project file)."""
    _stop()


# --- Media: described on subtitld.cc, sent between collaborators ---------------------------------------------


def share_media():
    """Describe the open project's media here (in the background, hashing only what is new), put on the
    project's media list what is not on it yet, and tell the others what this app can send."""
    global _media_scan
    if _connection is None or not collab_state.shared():
        return
    if _media_scan is not None and _media_scan.isRunning():
        return
    from subtitld.modules import collab_media

    doc_id = collab_state.STATE['doc']

    def work():
        files = collab_media.scan()
        client = service.client()
        listed = {entry['hash']: entry for entry in client.project_media(doc_id)}
        for item in files:
            if item['hash'] not in listed:
                description = {key: item[key] for key in ('hash', 'kind', 'size', 'chunk_size', 'chunks', 'fingerprint', 'name')}
                listed[item['hash']] = client.add_project_media(doc_id, **description)
        return files, listed

    def done(result):
        global _media_files
        files, listed = result
        _media_files = files
        if _connection is not None and collab_state.STATE.get('doc') == doc_id:
            _connection.engine.media.update(listed)
            _connection.engine.have([item['hash'] for item in files])
        _tell('media')

    def failed(error):
        log.warning('collab: could not share the media list: %s', error)

    _media_scan = service.call(work, done, failed)


def media_files():
    """The open project's media here, described: [{'kind', 'name', 'path', 'hash', ...}]."""
    return list(_media_files)


def media_list():
    """The project's media list (what subtitld.cc describes), by hash."""
    return dict(_connection.engine.media) if _connection is not None else {}


# --- Presence, text locks, conflicts -----------------------------------------------------------------------


def _start_presence():
    global _presence_timer
    from PySide6.QtCore import QTimer

    if _presence_timer is None:
        _presence_timer = QTimer()
        _presence_timer.setInterval(PRESENCE_MS)
        _presence_timer.timeout.connect(_presence_tick)
    _presence_timer.start()


def _presence_tick():
    """Say what changed in what this person is doing; let go of a text they stopped typing in."""
    global _last_presence
    if _connection is None:
        return
    selected = session.SUBTITLE.get('selected')
    selected_id = selected.get('id') if isinstance(selected, dict) else None
    if _editing and (selected_id != _editing or time.monotonic() - _last_typed > TYPING_IDLE_SECONDS):
        stopped_typing()
    position = session.SUBTITLE.get('position')
    state = {'selected': selected_id, 'playhead': round(float(position), 2) if position is not None else None,
             'editing': _editing}
    if state != _last_presence:
        _last_presence = state
        _connection.engine.presence(state)


def typing(cue_id):
    """This person is typing in subtitle `cue_id`'s text: hold its lock, so the others leave it be."""
    global _editing, _last_typed, _last_lock
    if _connection is None or not cue_id:
        return
    now = time.monotonic()
    _last_typed = now
    if cue_id != _editing:
        if _editing:
            _connection.engine.unlock(_editing)
        _editing = cue_id
        _last_lock = 0.0
    if now - _last_lock >= LOCK_RENEW_SECONDS:
        _last_lock = now
        _connection.engine.lock(cue_id)


def stopped_typing():
    global _editing
    if _connection is not None and _editing:
        _connection.engine.unlock(_editing)
    _editing = None


def locked_by(cue_id):
    """The collaborator typing in subtitle `cue_id` right now ({'handle', 'name'}), or None."""
    if _connection is None or not cue_id:
        return None
    return _connection.engine.locked_by(cue_id)


def color_for(handle):
    digest = hashlib.sha1((handle or '').encode()).digest()
    return COLORS[digest[0] % len(COLORS)]


def collaborators():
    """The others in the project now: [{'peer', 'handle', 'name', 'color', 'selected', 'playhead',
    'editing'}]."""
    if _connection is None:
        return []
    people = []
    for peer, info in _connection.engine.peers.items():
        user = info.get('user') or {}
        state = info.get('state') or {}
        handle = user.get('handle') or ''
        people.append({'peer': peer, 'handle': handle, 'name': user.get('name') or handle,
                       'color': color_for(handle), 'selected': state.get('selected'),
                       'playhead': state.get('playhead'), 'editing': state.get('editing')})
    return sorted(people, key=lambda person: person['handle'])


def conflicts():
    """What crossed someone else's changes (collab_sync.Engine.conflicts)."""
    return list(_connection.engine.conflicts) if _connection is not None else []


def resolve(conflict, keep):
    """Settle a conflict: 'mine' keeps what is there (this person's text won), 'theirs' puts back the other
    person's, as an edit of this person's (undoable, sent to everyone). A refused batch is only dismissed."""
    if _connection is None:
        return
    engine = _connection.engine
    if conflict in engine.conflicts:
        engine.conflicts.remove(conflict)
    if keep == 'theirs' and conflict.get('cue_id'):
        segment = cue_ids.find(session.SUBTITLE.get('segments'), conflict['cue_id'])
        if segment is not None:
            history.history_append()
            head, _dot, key = conflict['field'].partition('.')
            if key:
                segment.setdefault(head, {})[key] = conflict['theirs']
            else:
                segment[head] = conflict['theirs']
            session.set_unsaved()
    _tell('conflicts')


# --- Sharing and joining ------------------------------------------------------------------------------------


def _live():
    return session.SUBTITLE.get('segments') or [], session.SPEAKERS, session.SUBTITLE


def _document():
    session.capture_changes()
    return docops.snapshot(*_live())


def share(title, owner, on_success, on_failure):
    """Share the open project for live co-editing, as `owner` (a handle: yourself or a group you may publish
    as). The project becomes shared once subtitld.cc has it; saving keeps that with it."""
    document = _document()

    def shared(project):
        collab_state.start(project['doc_id'], service.base_url(), int(project.get('seq') or 0), base=document)
        # Edits made while subtitld.cc was taking it: the first changes to send.
        since = docops.diff(document, *_live())
        if since:
            collab_state.queue(since)
        session.set_unsaved(True)
        connect()
        _tell('shared')
        on_success(project)

    return service.call(lambda: service.client().share_project(title, document, owner=owner), shared, on_failure)


def join(window, project):
    """Open shared project `project` (from projects()) in the editor, in place of what is there: the
    subtitles come from subtitld.cc as the connection catches up. Like a subtitle opened from subtitld.cc,
    it isn't a file yet: saving asks where to put it. Main thread only."""
    from subtitld.interface import left_panel, top_bar

    _stop()
    history.history_clear()
    session.SUBTITLE['segments'] = []
    session.SUBTITLE['selected'] = None
    session.SUBTITLE['filepath'] = ''
    session.SUBTITLE.pop('origin', None)
    session.SPEAKERS.clear()
    session.CONFIG['format_to_save'] = 'USFX'
    session.document_loaded()
    collab_state.start(project['doc_id'], service.base_url(), 0, base=None)
    session.set_unsaved(True)
    connect()
    _tell('shared')
    if hasattr(window, 'timeline_widget'):
        window.timeline_widget.update_size()
        window.timeline_widget.update()
    left_panel.update(window)
    top_bar.update(window)


def projects(on_success, on_failure):
    """The shared projects the signed-in person co-edits (one page: {results, next_cursor})."""
    return service.call(lambda: service.client().projects(), on_success, on_failure)


def project(on_success, on_failure):
    """The open shared project as subtitld.cc has it: title, owner, members, permissions."""
    doc_id = collab_state.STATE.get('doc')
    return service.call(lambda: service.client().project(doc_id), on_success, on_failure)


def report_published(share_id, version, on_success, on_failure):
    """This person published version `version` of `share_id` from the open shared project: subtitld.cc
    links the project to that subtitle and credits everyone who co-edited it."""
    doc_id = collab_state.STATE.get('doc')
    return service.call(lambda: service.client().project_published(doc_id, share_id, version), on_success, on_failure)


def follow_subtitle(project):
    """The subtitle the shared project publishes to (its `subtitle`) is the editor's origin, at its latest
    version: publishing from here offers its next version to those who may. True when that changed."""
    subtitle = (project or {}).get('subtitle')
    if not subtitle or not collab_state.shared():
        return False
    origin = service.origin() or {}
    if origin.get('share_id') == subtitle['share_id'] and (origin.get('version') or 0) >= (subtitle.get('version') or 0):
        return False
    service.set_origin(subtitle['share_id'], subtitle.get('version'))
    session.set_unsaved(True)
    return True


def add_member(handle, on_success, on_failure):
    doc_id = collab_state.STATE.get('doc')
    return service.call(lambda: service.client().add_member(doc_id, handle.strip().lstrip('@')), on_success, on_failure)


def remove_member(handle, on_success, on_failure):
    doc_id = collab_state.STATE.get('doc')
    return service.call(lambda: service.client().remove_member(doc_id, handle), on_success, on_failure)


def stop_sharing(manage, on_success, on_failure):
    """Leave the project (a member) or close it for everyone (`manage`: its owner). Either way it stays here
    as a project of one's own."""
    doc_id = collab_state.STATE.get('doc')
    me = (service.account() or {}).get('handle') or ''

    def left(_result):
        _stop()
        collab_state.clear()
        session.set_unsaved(True)
        _tell('shared')
        on_success(None)

    def work():
        client = service.client()
        if manage:
            return client.close_project(doc_id)
        # Leaving is removing oneself: by handle, which subtitld.cc tells when it isn't remembered yet.
        return client.remove_member(doc_id, me or client.me()['handle'])

    return service.call(work, left, on_failure)
