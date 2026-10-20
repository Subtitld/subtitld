"""Keeping a shared project in step with the server (the protocol in subtitld.cc's collab/consumers.py)

The server numbers every batch of changes it accepts. The app keeps, in modules.collab_state, the document
as of the last number it has (`base`, at `seq`) and its own batches the server has not confirmed yet
(`pending`). What the person sees is the base with the pending batches on top: it is what the server's
document will be once they arrive, since the server applies them after everything it already has.

So when a collaborator's batch comes in, it goes into the base, and the document on screen becomes the base
with the pending batches applied again. Only what differs is changed on screen, as changes from the server:
not undoable here, not sent back (Live.apply).

Batches are applied in number order: one that arrives early waits for the ones before it, and a gap that
stays open means saying hello again. A batch the server refuses is dropped, and so are its changes on
screen. Text that a collaborator changed while this app had its own change to it waiting is a conflict:
this app's text wins (it reaches the server later), and the conflict is listed for the person to look at.

No Qt here: modules.collab_client connects it to a websocket and to the open project.
"""

import copy
import logging

from subtitld.modules import collab_state, docops

log = logging.getLogger(__name__)

# Fields whose concurrent changes are worth showing the person: their words.
CONFLICT_FIELDS = ('text',)
CONFLICT_PREFIXES = ('translations.',)


class Live:
    """The open project as the engine sees it. modules.collab_client gives the session's; tests give their
    own."""

    def flush(self):
        """Record edits made since the last time (the journal), so they are queued before anything else."""

    def snapshot(self):
        """The document on screen, as docops.snapshot makes it."""
        raise NotImplementedError

    def apply(self, ops):
        """Change the document on screen by `ops`, as changes from the server."""
        raise NotImplementedError


def _touches(op):
    """(subtitle ID, field) a change sets, when it is words."""
    if op.get('op') != 'cue.set':
        return None
    field = op.get('field') or ''
    if field in CONFLICT_FIELDS or field.startswith(CONFLICT_PREFIXES):
        return op.get('id'), field
    return None


class Engine:
    OFFLINE, JOINING, LIVE = 'offline', 'joining', 'live'

    def __init__(self, send, live: Live, store=None):
        self.send = send
        self.live = live
        # The project's shared state (self.store); tests give each simulated collaborator its own.
        self.store = store if store is not None else collab_state.STATE
        self.state = self.OFFLINE
        self.peer = None
        self.you = None
        self.in_flight = set()
        self.early = {}          # seq -> batch that arrived before the ones it follows
        self.peers = {}          # peer -> {'user', 'state'}
        self.locks = {}          # cue ID -> peer holding it
        self.conflicts = []      # {'cue_id', 'field', 'mine', 'theirs', 'author'} or {'client_op_id', 'reason', ...}
        self.needs_resync = False
        self.listeners = []      # callback(kind): 'state', 'document', 'peers', 'locks', 'conflicts', 'lock'

    # --- Connection ------------------------------------------------------------------------------

    def _tell(self, kind, *args):
        for callback in list(self.listeners):
            callback(kind, *args)

    def resync(self):
        """A batch stayed missing (needs_resync): catch up again from the last one in line."""
        if self.state != self.OFFLINE and self.needs_resync:
            has_base = self.store.get('base') is not None
            self.send({'type': 'hello', 'last_seq': self.store['seq'] if has_base else None})

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self._tell('state')

    def connected(self):
        """The websocket is open: catch up from what this app has (or from scratch without a base)."""
        self.in_flight.clear()
        self.early.clear()
        self.needs_resync = False
        has_base = self.store.get('base') is not None
        self.send({'type': 'hello', 'last_seq': self.store['seq'] if has_base else None})
        self._set_state(self.JOINING)

    def disconnected(self):
        """Batches sent and not acknowledged go again on the next connection (the server accepts each
        once). Everyone else's presence is unknown until then."""
        self.in_flight.clear()
        self.peers.clear()
        self.locks.clear()
        self._set_state(self.OFFLINE)
        self._tell('peers')
        self._tell('locks')

    def local_changed(self):
        """The journal queued a batch of this app's (collab_state): send it when connected."""
        if self.state == self.LIVE:
            self._send_pending()

    def _send_pending(self):
        for batch in self.store['pending']:
            if batch['client_op_id'] not in self.in_flight:
                self.in_flight.add(batch['client_op_id'])
                self.send({'type': 'op', 'client_op_id': batch['client_op_id'], 'base_seq': batch['base_seq'],
                           'ops': batch['ops']})

    # --- From the server ---------------------------------------------------------------------------

    def on_message(self, message):
        kind = message.get('type') if isinstance(message, dict) else None
        handler = getattr(self, f'_on_{kind}', None) if isinstance(kind, str) else None
        if handler is None:
            log.info('collab: ignored a %r message', kind)
            return
        handler(message)

    def _on_welcome(self, message):
        self.peer, self.you = message.get('peer'), message.get('you')

    def _on_snapshot(self, message):
        """The whole document as of `seq`: the base starts again from it."""
        self.live.flush()
        self.store['base'] = copy.deepcopy(message['state'])
        self.store['seq'] = int(message['seq'])
        self.early = {seq: batch for seq, batch in self.early.items() if seq > self.store['seq']}
        self._drain(force_rebase=True)
        self._go_live()

    def _on_changes(self, message):
        self.live.flush()
        for batch in message.get('batches') or []:
            self._early(batch['seq'], batch)
        self._drain()
        self._go_live()

    def _go_live(self):
        if self.state != self.LIVE:
            self._set_state(self.LIVE)
        self._send_pending()

    def _on_batch(self, message):
        self.live.flush()
        self._early(message['seq'], message)
        self._drain()

    def _on_ack(self, message):
        cid, seq = message.get('client_op_id'), int(message['seq'])
        self.in_flight.discard(cid)
        batch = next((b for b in self.store['pending'] if b['client_op_id'] == cid), None)
        if batch is None:
            return  # already in the base (it came back in the changes after a reconnect)
        self._early(seq, {'seq': seq, 'client_op_id': cid, 'ops': batch['ops'], 'author': (self.you or {}).get('handle')})
        self._drain()

    def _on_reject(self, message):
        """The server will never take this batch: its changes go from the screen too."""
        cid = message.get('client_op_id')
        self.in_flight.discard(cid)
        batch = next((b for b in self.store['pending'] if b['client_op_id'] == cid), None)
        if batch is None:
            return
        log.warning('collab: the server refused a batch (%s: %s)', message.get('reason'), message.get('detail'))
        self.live.flush()
        self.store['pending'].remove(batch)
        self.conflicts.append({'client_op_id': cid, 'reason': message.get('reason'),
                               'detail': message.get('detail'), 'ops': batch['ops']})
        self._rebase()
        self._tell('conflicts')

    def _on_presence(self, message):
        self.peers[message['peer']] = {'user': message.get('user'), 'state': message.get('state') or {}}
        self._tell('peers')

    def _on_left(self, message):
        self.peers.pop(message.get('peer'), None)
        for cue_id in [c for c, p in self.locks.items() if p == message.get('peer')]:
            del self.locks[cue_id]
        self._tell('peers')
        self._tell('locks')

    def _on_locked(self, message):
        self.locks[message['cue_id']] = message['peer']
        self.peers.setdefault(message['peer'], {'user': message.get('user'), 'state': {}})
        self._tell('locks')

    def _on_unlocked(self, message):
        if self.locks.get(message.get('cue_id')) == message.get('peer'):
            del self.locks[message['cue_id']]
            self._tell('locks')

    def _on_lock(self, message):
        """The answer to this app's own lock request."""
        self._tell('lock', message.get('cue_id'), bool(message.get('granted')), message.get('holder'))

    def _on_pong(self, message):
        pass

    def _on_error(self, message):
        log.warning('collab: the server said %s', message.get('code'))

    # --- The document ------------------------------------------------------------------------------

    def _early(self, seq, batch):
        seq = int(seq)
        if seq > self.store['seq']:
            self.early[seq] = batch

    def _drain(self, force_rebase=False):
        """Apply to the base every batch that is next in line; then the screen follows."""
        state = self.store
        if state.get('base') is None:
            return  # waiting for the whole document: the batches keep until it comes
        remote = force_rebase
        while state['seq'] + 1 in self.early:
            batch = self.early.pop(state['seq'] + 1)
            try:
                docops.apply(state['base'], batch['ops'])
            except docops.OpError as error:
                # The base no longer matches the server's document: start again from the server's.
                log.warning('collab: a batch from the server did not fit (%s); asking for the document', error)
                state['base'] = None
                self.early.clear()
                self.needs_resync = True
                self.send({'type': 'hello', 'last_seq': None})
                return
            state['seq'] += 1
            mine = next((b for b in state['pending'] if b['client_op_id'] == batch.get('client_op_id')), None)
            if mine is not None:
                state['pending'].remove(mine)
                self.in_flight.discard(mine['client_op_id'])
            else:
                self._note_conflicts(batch)
                remote = True
        self.needs_resync = bool(self.early)
        if remote:
            self._rebase()

    def _note_conflicts(self, batch):
        theirs = {}
        for op in batch['ops']:
            key = _touches(op)
            if key:
                theirs[key] = op.get('value')
        if not theirs:
            return
        found = False
        for pending in self.store['pending']:
            for op in pending['ops']:
                key = _touches(op)
                if key in theirs:
                    self.conflicts.append({'cue_id': key[0], 'field': key[1], 'mine': op.get('value'),
                                           'theirs': theirs[key], 'author': batch.get('author')})
                    found = True
        if found:
            self._tell('conflicts')

    def desired(self):
        """The base with the pending batches applied: what the screen should show. A pending batch that
        does not fit any more is left out here; the server will refuse it."""
        document = copy.deepcopy(self.store['base'])
        for batch in self.store['pending']:
            trial = copy.deepcopy(document)
            try:
                docops.apply(trial, batch['ops'])
            except docops.OpError:
                continue
            document = trial
        return document

    def _rebase(self):
        if self.store.get('base') is None:
            return
        target = self.desired()
        ops = docops.diff(self.live.snapshot(), target['segments'], target['speakers'], target['meta'])
        if ops:
            self.live.apply(ops)
            self._tell('document')
