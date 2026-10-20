"""A shared project's co-editing state

A project shared through subtitld.cc for live co-editing remembers, in its
.usfx:

    collab.json     which shared document it is (`doc`, on `server`) and the
                    server's change number its copy is up to (`seq`)
    pending.jsonl   the changes made here that the server has not yet
                    confirmed, one batch per line, oldest first

so that changes made offline, or before a crash, are sent when it is next
connected. Each batch is {'client_op_id', 'base_seq', 'ops'}: an ID the
server knows it by if it arrives twice, the change number it was made on top
of, and the changes (modules.docops).

While a project is shared, every batch the journal records is queued here,
undo and redo included; changes that come from the server are applied
inside `remote()` and are not.
"""

import contextlib
import copy
import json
import secrets

from subtitld.modules import journal

VERSION = 1
STATE_MEMBER = 'collab.json'
PENDING_MEMBER = 'pending.jsonl'

# Names the batches made in this run of the app: with a counter, unique.
_RUN = secrets.token_hex(6)

STATE = {'doc': None, 'server': None, 'seq': 0, 'pending': []}
_counter = 0
_muted = 0
# What the project file being opened holds, until its open is done.
_opened = None


def shared():
    """Whether the open project is shared for co-editing."""
    return bool(STATE.get('doc'))


def start(doc, server, seq=0):
    """The open project is now shared document `doc` on `server`, as of the
    server's change `seq`."""
    STATE.update({'doc': doc, 'server': server, 'seq': int(seq), 'pending': []})


def clear():
    """The open project is not shared (any more)."""
    STATE.update({'doc': None, 'server': None, 'seq': 0, 'pending': []})


def _on_changes(ops, _undo):
    global _counter
    if _muted or not shared():
        return
    _counter += 1
    STATE['pending'].append({'client_op_id': f'{_RUN}-{_counter}', 'base_seq': STATE['seq'],
                             'ops': copy.deepcopy(ops)})


journal.add_listener(_on_changes)


@contextlib.contextmanager
def remote():
    """Changes made inside this block came from the server: the journal
    still records them, but they are not queued to be sent back."""
    global _muted
    _muted += 1
    try:
        yield
    finally:
        _muted -= 1


def acknowledge(client_op_id, seq):
    """The server confirmed the batch `client_op_id` as its change `seq`."""
    STATE['pending'] = [batch for batch in STATE['pending'] if batch['client_op_id'] != client_op_id]
    STATE['seq'] = max(STATE['seq'], int(seq))


# --------------------------------------------------------------------------- #
# In the project file                                                          #
# --------------------------------------------------------------------------- #
def export():
    """The state as the project file keeps it: {member name: bytes}, or {}
    for a project that is not shared. A copy, safe to write from another
    thread."""
    if not shared():
        return {}
    head = {'version': VERSION, 'doc': STATE['doc'], 'server': STATE['server'], 'seq': STATE['seq']}
    lines = ''.join(json.dumps(batch, ensure_ascii=False) + '\n' for batch in STATE['pending'])
    return {STATE_MEMBER: json.dumps(head, ensure_ascii=False, indent=2).encode('utf-8'),
            PENDING_MEMBER: lines.encode('utf-8')}


def read(state_bytes, pending_bytes=b''):
    """The state in a project file's members, or None when they do not hold
    one this version understands. A pending line that does not read (a write
    cut short) ends the queue there."""
    try:
        head = json.loads(state_bytes.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if not isinstance(head, dict) or head.get('version') != VERSION or not head.get('doc'):
        return None
    pending = []
    for line in (pending_bytes or b'').decode('utf-8', 'replace').splitlines():
        if not line.strip():
            continue
        try:
            batch = json.loads(line)
        except ValueError:
            break
        if not isinstance(batch, dict) or not isinstance(batch.get('ops'), list) or not batch.get('client_op_id'):
            break
        pending.append(batch)
    return {'doc': head['doc'], 'server': head.get('server'), 'seq': int(head.get('seq') or 0),
            'pending': pending}


def opening(state):
    """The project file being opened holds `state` (from read(), or None)."""
    global _opened
    _opened = state


def opened():
    """The open is done: the project takes the state its file held, or is
    not shared."""
    global _opened
    state, _opened = _opened, None
    if state:
        STATE.update(copy.deepcopy(state))
    else:
        clear()


def forget_opening():
    """A new open starts: nothing read earlier belongs to it."""
    global _opened
    _opened = None
