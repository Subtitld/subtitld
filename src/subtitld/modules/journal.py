"""The document's changes as they happen

Every edit, wherever in the app it is made, ends with session.set_unsaved();
history.history_append() starts one. At both the journal compares the live
subtitles, speakers and document fields with the copy it holds of how they
were last, and turns the difference into changes (modules.docops). Those go
to its listeners, with the changes that undo them: undo (modules.history)
and, in a shared project, the other collaborators.

The document leaves out the app's runtime data: a subtitle's '_' keys (the
recorder's '_rec'), a speaker's picture. The journal keeps the latest of each
by subtitle ID and speaker name, so that a subtitle or speaker brought back
by undo gets them back too (runtime()).

Opening a project is not an edit: whatever opens one calls
session.document_loaded() once it is in place, and the journal starts from
there.
"""

import logging

from subtitld.modules import docops

log = logging.getLogger(__name__)

_shadow = None
_listeners = []
_runtime = {'cue': {}, 'speaker': {}}


def add_listener(callback):
    """Call `callback(ops, undo)` with each batch of changes the journal
    finds and the changes that undo them."""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback):
    if callback in _listeners:
        _listeners.remove(callback)


def _keep(table, key, extra):
    if extra:
        table[key] = extra
    else:
        table.pop(key, None)


def _keep_runtime(segments, speakers):
    """Note the runtime data of what is live now. What is gone keeps its
    last, for when undo brings it back."""
    for segment in segments or []:
        _keep(_runtime['cue'], segment.get('id'),
              {key: value for key, value in segment.items() if isinstance(key, str) and key.startswith('_')})
    for name, speaker in (speakers or {}).items():
        if isinstance(speaker, dict):
            _keep(_runtime['speaker'], name,
                  {key: value for key, value in speaker.items()
                   if key in docops.SPEAKER_RUNTIME or (isinstance(key, str) and key.startswith('_'))})


def runtime(kind, key):
    """The runtime data last seen on subtitle ID `key` (kind 'cue') or
    speaker `key` (kind 'speaker'): a dict to update it with."""
    return dict(_runtime[kind].get(key) or {})


def reset(segments, speakers, meta):
    """Take the live state as it is now as the journal's starting point."""
    global _shadow
    _shadow = docops.snapshot(segments, speakers, meta)
    _runtime['cue'].clear()
    _runtime['speaker'].clear()
    _keep_runtime(segments, speakers)


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
        undo = docops.apply(_shadow, ops)
        _keep_runtime(segments, speakers)
    except Exception:
        # Never in the way of the edit itself: start again from here.
        log.exception('journal: could not record a change; starting again from the current document')
        reset(segments, speakers, meta)
        return []
    if not ops:
        return ops
    for callback in list(_listeners):
        callback(ops, undo)
    return ops
