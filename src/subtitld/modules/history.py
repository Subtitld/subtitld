"""History module (Ctrl+Z / Ctrl+Shift+Z).

Change-based undo/redo. `history_append()` is called from `subtitles.py`
helpers (and from interface code that edits state outside those helpers)
just before an edit, and starts an undo step. The journal (modules.journal)
records what the edit then changes, and the changes that undo it go into
that step. Undoing a step applies them; what undoes *that* is its redo step.

Undoing a step changes only what the step changed. Edits made since by
anything else stay as they are: a transcript arriving for an earlier take
(see amending()), and in a shared project a collaborator's. Subtitles keep
their dicts through undo and redo (the selection, the audio engine's clips
hold them); one brought back gets its runtime keys back (journal.runtime()).

Each step also remembers the subtitle selected when it began, by ID, and
undo selects it again.

Bounded at `MAX_HISTORY` steps so long editing sessions don't grow memory
without limit.
"""

import contextlib
import logging

from subtitld.modules import collab_media, collab_state, cue_ids, docops, journal, session

log = logging.getLogger(__name__)

MAX_HISTORY = 100

ALL_HISTORY = []
REDO_HISTORY = []


class Step:
    """One undo or redo step: the batches of changes that undo it, oldest
    first, the ID of the subtitle selected when it began and the speakers'
    pictures then (the live QImages: they are not part of the document)."""
    __slots__ = ('undo', 'selected_id', 'pictures')

    def __init__(self, selected_id=None):
        self.undo = []
        self.selected_id = selected_id
        self.pictures = {name: data['image'] for name, data in session.SPEAKERS.items()
                         if isinstance(data, dict) and data.get('image') is not None}


# Where recorded changes go instead of the newest step (see amending()).
_target = None
_NOWHERE = object()
# Undo and redo record their own changes into the step they make.
_replaying = False


def _on_changes(ops, undo):
    if _replaying:
        return
    step = _target if _target is not None else (ALL_HISTORY[-1] if ALL_HISTORY else None)
    if step is None or step is _NOWHERE:
        return
    step.undo.append(undo)


journal.add_listener(_on_changes)


def _selected_id():
    selected = session.SUBTITLE.get('selected')
    return selected.get('id') if isinstance(selected, dict) else None


def _select(sid):
    session.SUBTITLE['selected'] = cue_ids.find(session.SUBTITLE.get('segments'), sid) or False


def _show_pictures(step):
    """Each speaker's picture as it was when `step` began. One that had none
    then keeps its own: it may have finished loading since."""
    for name, data in session.SPEAKERS.items():
        if isinstance(data, dict) and step.pictures.get(name) is not None:
            data['image'] = step.pictures[name]


def _apply(document, ops):
    """Apply `ops` live, skipping any that no longer fit (what it changes is
    gone); returns the changes that undo the rest."""
    undo = []
    for op in ops:
        try:
            undo.extend(docops.apply(document, [op]))
        except docops.OpError as error:
            log.info('history: skipped a change that no longer fits: %s', error)
            continue
        if op.get('op') == 'cue.insert':
            segment = cue_ids.find(document['segments'], op['cue'].get('id'))
            if segment is not None:
                segment.update(journal.runtime('cue', segment['id']))
        elif op.get('op') == 'speaker.insert' and isinstance(document['speakers'].get(op['name']), dict):
            document['speakers'][op['name']].update(journal.runtime('speaker', op['name']))
    undo.reverse()
    return undo


def _replay(step):
    """Undo `step` live, newest batch first; returns the step that undoes
    that."""
    global _replaying
    document = {'segments': session.SUBTITLE.setdefault('segments', []),
                'speakers': session.SPEAKERS, 'meta': session.SUBTITLE}
    reverse = Step(_selected_id())
    _replaying = True
    try:
        for batch in reversed(step.undo):
            reverse.undo.append(_apply(document, batch))
        # The document names dub files by content: each finds its copy here again.
        collab_media.resolve_dubs(document['segments'])
        # The journal takes note of them, for anyone else listening.
        session.capture_changes()
    finally:
        _replaying = False
    return reverse


def history_append(_segments=None):
    """Start a new undo step: the changes the edit about to be made records
    go into it. The optional `_segments` argument is ignored — kept for
    backward compatibility with the dozens of
    `history_append(session.SUBTITLE['segments'])` call sites scattered
    through `subtitles.py` and the interface modules."""
    # What came before this edit is the step before's.
    session.capture_changes()
    ALL_HISTORY.append(Step(_selected_id()))
    if len(ALL_HISTORY) > MAX_HISTORY:
        ALL_HISTORY.pop(0)
    REDO_HISTORY.clear()


def top_snapshot():
    """The newest undo step, or None. Identity only — callers use it to tell
    whether a step was started since they last looked."""
    return ALL_HISTORY[-1] if ALL_HISTORY else None


@contextlib.contextmanager
def amending(step):
    """Changes made inside this block go into `step`, not the newest one.

    For changes that *complete* an earlier edit rather than being a new one —
    e.g. transcription text arriving for a placeholder the recorder created
    in `step` — so that undoing an unrelated later edit does not also roll
    the completion back, and undoing `step` takes the completion with it.
    When `step` is no longer in the undo history (undone, or too old), the
    changes are not undoable at all."""
    global _target
    session.capture_changes()
    previous = _target
    _target = step if step is not None and step in ALL_HISTORY else _NOWHERE
    try:
        yield
    finally:
        session.capture_changes()
        _target = previous


def amend_stored(cue_id, update, stop_at=None):
    """Apply `update(subtitle)` to the stored copies of subtitle `cue_id`:
    those undo and redo steps would bring back. `update` changes the copy in
    place, or returns False to have the step not bring it back at all.

    For a completion (see amending()) arriving when its subtitle is not live:
    deleted, or its creation undone. Redo steps are always all visited;
    undo steps newest-first, down to `stop_at` (not itself visited): older
    ones predate the subtitle."""
    def visit(step):
        for batch in step.undo:
            for op in list(batch):
                if op.get('op') == 'cue.insert' and op['cue'].get('id') == cue_id:
                    if update(op['cue']) is False:
                        batch.remove(op)

    for step in REDO_HISTORY:
        visit(step)
    for step in reversed(ALL_HISTORY):
        if stop_at is not None and step is stop_at:
            break
        visit(step)


def _field_of(op):
    """(kind, key, field) an undo change sets, or (kind, key, None) for a whole subtitle or speaker."""
    kind = (op.get('op') or '').partition('.')[0]
    key = op.get('id') if kind == 'cue' else op.get('name') if kind == 'speaker' else None
    if op.get('op') == 'cue.insert':
        key = op['cue'].get('id')
    field = op.get('field')
    return kind, key, field


def forget(ops):
    """Changes from elsewhere (`ops`, from the server) replaced what they set: undo and redo here leave those
    fields alone from now on, rather than putting back what the person had before someone else's change.
    A field a change sets as a whole ('translations') also covers its parts ('translations.pt')."""
    touched = set()
    for op in ops:
        kind, key, field = _field_of(op)
        if field:
            touched.add((kind, key, field))

    def covered(op):
        kind, key, field = _field_of(op)
        if not field:
            return False
        return (kind, key, field) in touched or (kind, key, field.partition('.')[0]) in touched

    if not touched:
        return
    for step in ALL_HISTORY + REDO_HISTORY:
        step.undo = [[op for op in batch if not covered(op)] for batch in step.undo]


def history_undo():
    """Undo the newest step, making its redo step. Returns True if the undo
    happened, False if there was nothing to undo."""
    if not ALL_HISTORY:
        return False
    # Anything not recorded yet belongs to the step being undone.
    session.capture_changes()
    step = ALL_HISTORY.pop()
    REDO_HISTORY.append(_replay(step))
    _select(step.selected_id)
    _show_pictures(step)
    session.set_unsaved(True)
    return True


def history_redo():
    """Redo the newest undone step. Returns True if the redo happened, False
    if there was nothing to redo."""
    if not REDO_HISTORY:
        return False
    session.capture_changes()
    step = REDO_HISTORY.pop()
    ALL_HISTORY.append(_replay(step))
    if len(ALL_HISTORY) > MAX_HISTORY:
        ALL_HISTORY.pop(0)
    _select(step.selected_id)
    _show_pictures(step)
    session.set_unsaved(True)
    return True


def history_clear():
    """Wipe both stacks. Call after loading a fresh project so the user
    can't 'undo' into the previously-loaded document."""
    global _target
    ALL_HISTORY.clear()
    REDO_HISTORY.clear()
    _target = None
    # A new project: nothing read for an earlier open is its.
    collab_state.forget_opening()
    session.document_loaded()
