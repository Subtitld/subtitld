"""Live co-editing in Subtitld: sharing the open project, joining a shared one, who it is shared with, and its
connection (modules.collab_client), which runs while a shared project is open.

The calls to subtitld.cc run in the background (subtitldcc_service.call); everything else here is main-thread
work on the open project.
"""

import logging

from subtitld.modules import collab_state, docops, history, session
from subtitld.modules import subtitldcc_service as service

log = logging.getLogger(__name__)

_connection = None
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
    global _connection
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
    _connection.changed.connect(_tell)
    _connection.failed.connect(_failed)
    _connection.start()
    _tell('state')


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
