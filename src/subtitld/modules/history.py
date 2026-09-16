"""History module (Ctrl+Z / Ctrl+Shift+Z).

Snapshot-based undo/redo. `history_append()` is called from `subtitles.py`
helpers (and from interface code that mutates state outside those helpers)
just before a mutation. Each snapshot captures:

  - `session.SUBTITLE['segments']` (deep copy)
  - the index of the currently selected segment, so we can restore the
    selection to a fresh dict after a deepcopy round-trip
  - `session.SPEAKERS` (deep copy)
  - `session.SUBTITLE['language']` and `['translations']`

The previous version snapshotted only `segments` and replaced them with new
dicts on undo, which silently broke `session.SUBTITLE['selected']`
(`selected` kept pointing at the old, now-discarded dict).

Bounded at `MAX_HISTORY` entries so long editing sessions don't grow memory
without limit.
"""

import copy

from subtitld.modules import session


MAX_HISTORY = 100

ALL_HISTORY = []
REDO_HISTORY = []


def _copy_speakers(speakers):
    """Deep-copy SPEAKERS minus the runtime-only `image` field, which holds a
    QImage that copy.deepcopy can't pickle. The image is re-derived from the
    speaker's source on demand, so dropping it from snapshots is safe."""
    result = {}
    for name, data in speakers.items():
        if isinstance(data, dict):
            entry = {k: v for k, v in data.items() if k != 'image'}
            result[name] = copy.deepcopy(entry)
            if 'image' in data:
                # Preserve the live QImage reference so the visible UI doesn't
                # blank out after an undo/redo. Snapshots share the same
                # QImage across all entries — that's intentional, the image
                # cache is shallow on purpose.
                result[name]['image'] = data['image']
        else:
            result[name] = copy.deepcopy(data)
    return result


def _snapshot():
    segments = session.SUBTITLE.get('segments') or []
    selected = session.SUBTITLE.get('selected')
    selected_index = None
    if selected:
        try:
            selected_index = segments.index(selected)
        except ValueError:
            selected_index = None
    return {
        'segments': copy.deepcopy(segments),
        'selected_index': selected_index,
        'speakers': _copy_speakers(session.SPEAKERS),
        'language': session.SUBTITLE.get('language'),
        'translations': copy.deepcopy(session.SUBTITLE.get('translations')),
    }


def _restore(snapshot):
    segments = session.SUBTITLE.setdefault('segments', [])
    segments.clear()
    segments.extend(copy.deepcopy(snapshot['segments']))

    session.SPEAKERS.clear()
    session.SPEAKERS.update(_copy_speakers(snapshot.get('speakers') or {}))

    if snapshot.get('language') is not None:
        session.SUBTITLE['language'] = snapshot['language']
    if snapshot.get('translations') is not None:
        session.SUBTITLE['translations'] = copy.deepcopy(snapshot['translations'])

    selected_index = snapshot.get('selected_index')
    if selected_index is not None and 0 <= selected_index < len(segments):
        session.SUBTITLE['selected'] = segments[selected_index]
    else:
        session.SUBTITLE['selected'] = False


def history_append(_segments=None):
    """Push a snapshot of the current document state onto the undo stack.
    The optional `_segments` argument is ignored — kept for backward
    compatibility with the dozens of `history_append(session.SUBTITLE['segments'])`
    call sites scattered through `subtitles.py` and the interface modules."""
    ALL_HISTORY.append(_snapshot())
    if len(ALL_HISTORY) > MAX_HISTORY:
        ALL_HISTORY.pop(0)
    REDO_HISTORY.clear()


def top_snapshot():
    """The most recent undo snapshot object, or None. Identity only — callers
    use it to tell whether anything was pushed since they last looked."""
    return ALL_HISTORY[-1] if ALL_HISTORY else None


def amend_snapshots(update, stop_at=None):
    """Apply `update(segments)` to stored snapshots, in place.

    For changes that *complete* an earlier edit rather than being a new one —
    e.g. transcription text arriving for a placeholder the recorder created —
    so that undoing an unrelated later edit does not also roll the completion
    back. `update` mutates the snapshot's segment list; snapshots that do not
    hold its target are simply left alone.

    Redo snapshots are always all visited (there are only as many as recent
    undos). Undo snapshots are walked newest-first, down to `stop_at` (a
    snapshot from `top_snapshot()`, not itself visited): older ones predate
    the thing being completed. A missing gap does not end the walk, since
    the target may have been deleted and later brought back by undo.
    """
    for snap in REDO_HISTORY:
        update(snap['segments'])
    for snap in reversed(ALL_HISTORY):
        if stop_at is not None and snap is stop_at:
            break
        update(snap['segments'])


def history_undo():
    """Pop the latest snapshot, push current state onto redo, restore it.
    Returns True if the undo happened, False if the stack was empty."""
    if not ALL_HISTORY:
        return False
    REDO_HISTORY.append(_snapshot())
    _restore(ALL_HISTORY.pop())
    session.set_unsaved(True)
    return True


def history_redo():
    """Pop the latest redo snapshot, push current onto undo, restore it.
    Returns True if the redo happened, False if the stack was empty."""
    if not REDO_HISTORY:
        return False
    ALL_HISTORY.append(_snapshot())
    _restore(REDO_HISTORY.pop())
    session.set_unsaved(True)
    return True


def history_clear():
    """Wipe both stacks. Call after loading a fresh project so the user
    can't 'undo' into the previously-loaded document."""
    ALL_HISTORY.clear()
    REDO_HISTORY.clear()
