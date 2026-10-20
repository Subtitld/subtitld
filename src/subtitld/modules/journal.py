"""The document's changes as they happen

Every edit, wherever in the app it is made, ends with session.set_unsaved();
history.history_append() starts one. At both the journal compares the live
subtitles, speakers and document fields with the copy it holds of how they
were last, and turns the difference into changes (modules.docops). Those go
to its listeners: change-based undo and, in a shared project, the other
collaborators.

Opening a project is not an edit: whatever opens one calls
session.document_loaded() once it is in place, and the journal starts from
there.
"""

import logging

from subtitld.modules import docops

log = logging.getLogger(__name__)

_shadow = None
_listeners = []


def add_listener(callback):
    """Call `callback(ops)` with each batch of changes the journal finds."""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback):
    if callback in _listeners:
        _listeners.remove(callback)


def reset(segments, speakers, meta):
    """Take the live state as it is now as the journal's starting point."""
    global _shadow
    _shadow = docops.snapshot(segments, speakers, meta)


def document():
    """The journal's copy of the document (do not change it)."""
    return _shadow


def capture(segments, speakers, meta):
    """Record what changed since the last capture; returns those changes."""
    if _shadow is None:
        reset(segments, speakers, meta)
        return []
    try:
        ops = docops.diff(_shadow, segments or [], speakers or {}, meta or {})
        docops.apply(_shadow, ops)
    except Exception:
        # Never in the way of the edit itself: start again from here.
        log.exception('journal: could not record a change; starting again from the current document')
        reset(segments, speakers, meta)
        return []
    if not ops:
        return ops
    for callback in list(_listeners):
        callback(ops)
    return ops
