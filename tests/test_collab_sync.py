"""Keeping a shared project in step (modules.collab_sync): collaborators' batches go into the base and the
screen follows with this app's own on top; batches apply in number order; a refused batch leaves the screen;
words both sides changed are listed as conflicts; a batch whose acknowledgement was lost is recognised when it
comes back. And, with a simulated server and three collaborators whose messages arrive late, out of order and
not at all when they go offline, everyone ends with the server's document.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import copy, os, random, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import logging
from subtitld.modules import docops
from subtitld.modules.collab_sync import Engine, Live

# Refused batches are expected here, by the hundred.
logging.getLogger('subtitld.modules.collab_sync').setLevel(logging.ERROR)

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def document(*texts):
    return {'segments': [{'id': f'c{i}', 'start': float(i), 'end': i + 0.5, 'text': t} for i, t in enumerate(texts)],
            'speakers': {}, 'meta': {}}


def norm(doc):
    return {'segments': sorted(doc['segments'], key=lambda s: s['id']), 'speakers': doc['speakers'], 'meta': doc['meta']}


class FakeLive(Live):
    """A collaborator's screen: edits are recorded (queued) as they are made."""

    def __init__(self, name, doc, seq=0):
        self.name = name
        self.screen = copy.deepcopy(doc)
        self.store = {'doc': 'd', 'seq': seq, 'base': copy.deepcopy(doc), 'pending': []}
        self.counter = 0

    def edit(self, ops):
        docops.apply(self.screen, ops)
        self.counter += 1
        self.store['pending'].append({'client_op_id': f'{self.name}-{self.counter}', 'base_seq': self.store['seq'],
                                      'ops': copy.deepcopy(ops)})

    def snapshot(self):
        return copy.deepcopy(self.screen)

    def apply(self, ops):
        for op in ops:
            try:
                docops.apply(self.screen, [op])
            except docops.OpError:
                pass


def client(name, doc, seq=0):
    live = FakeLive(name, doc, seq)
    sent = []
    engine = Engine(sent.append, live, store=live.store)
    return live, engine, sent


def set_text(cue, text):
    return [{'op': 'cue.set', 'id': cue, 'field': 'text', 'value': text}]


def texts(live):
    return [s['text'] for s in sorted(live.screen['segments'], key=lambda s: s['start'])]


print('joining')
live, engine, sent = client('a', document('one'), seq=4)
engine.connected()
check('with a base, from its change', sent[-1], {'type': 'hello', 'last_seq': 4})
live.store['base'] = None
engine.connected()
check('without one, from scratch', sent[-1], {'type': 'hello', 'last_seq': None})
engine.on_message({'type': 'snapshot', 'seq': 9, 'state': document('server one', 'server two')})
check('the whole document comes', (texts(live), live.store['seq'], engine.state), (['server one', 'server two'], 9, 'live'))

print('a collaborator\'s batch')
live, engine, sent = client('a', document('one', 'two'))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
live.edit(set_text('c0', 'mine'))
engine.local_changed()
check('this app\'s goes out', sent[-1]['type'], 'op')
engine.on_message({'type': 'batch', 'seq': 1, 'author': 'bob', 'client_op_id': 'b-1', 'ops': set_text('c1', 'theirs')})
check('both on screen', texts(live), ['mine', 'theirs'])
check('theirs in the base, mine still waiting', (live.store['base']['segments'][1]['text'], len(live.store['pending'])),
      ('theirs', 1))
engine.on_message({'type': 'ack', 'client_op_id': 'a-1', 'seq': 2, 'duplicate': False})
check('acknowledged: in the base, no longer waiting', (live.store['seq'], live.store['pending'], texts(live)),
      (2, [], ['mine', 'theirs']))

print('in number order')
live, engine, sent = client('a', document('one', 'two'))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
engine.on_message({'type': 'batch', 'seq': 2, 'author': 'bob', 'client_op_id': 'b-2', 'ops': set_text('c0', 'second')})
check('an early one waits', (texts(live), engine.needs_resync), (['one', 'two'], True))
engine.on_message({'type': 'batch', 'seq': 1, 'author': 'bob', 'client_op_id': 'b-1', 'ops': set_text('c0', 'first')})
check('then both, in order', (texts(live), live.store['seq'], engine.needs_resync), (['second', 'two'], 2, False))

print('conflicts')
live, engine, sent = client('a', document('one'))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
live.edit(set_text('c0', 'mine'))
engine.on_message({'type': 'batch', 'seq': 1, 'author': 'bob', 'client_op_id': 'b-1', 'ops': set_text('c0', 'theirs')})
check('mine stays on screen (it reaches the server later)', texts(live), ['mine'])
check('and the conflict is listed', engine.conflicts,
      [{'cue_id': 'c0', 'field': 'text', 'mine': 'mine', 'theirs': 'theirs', 'author': 'bob'}])

print('a refused batch')
live, engine, sent = client('a', document('one', 'two'))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
live.edit(set_text('c1', 'mine'))
engine.on_message({'type': 'batch', 'seq': 1, 'author': 'bob', 'client_op_id': 'b-1', 'ops': [{'op': 'cue.delete', 'id': 'c1'}]})
check('a change to a deleted subtitle is not on screen', texts(live), ['one'])
engine.on_message({'type': 'reject', 'client_op_id': 'a-1', 'reason': 'conflict', 'detail': 'Subtitle c1 is not there'})
check('refused: no longer waiting, listed', (live.store['pending'], engine.conflicts[-1]['reason']), ([], 'conflict'))

print('a lost acknowledgement')
live, engine, sent = client('a', document('one'))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
live.edit(set_text('c0', 'mine'))
engine.local_changed()
engine.disconnected()
engine.connected()
engine.on_message({'type': 'changes', 'batches': [{'seq': 1, 'author': 'a', 'client_op_id': 'a-1', 'ops': set_text('c0', 'mine')}]})
check('recognised as this app\'s, not sent again', (live.store['pending'], [m for m in sent[-1:] if m['type'] == 'op']), ([], []))
check('and nothing listed', engine.conflicts, [])

print('presence and locks')
live, engine, sent = client('a', document('one'))
seen = []
engine.listeners.append(lambda kind, *args: seen.append(kind))
engine.on_message({'type': 'presence', 'peer': 'p1', 'user': {'handle': 'bob'}, 'state': {'selected': 'c0'}})
engine.on_message({'type': 'locked', 'cue_id': 'c0', 'peer': 'p1', 'user': {'handle': 'bob'}})
check('known', (engine.peers['p1']['state'], engine.locks), ({'selected': 'c0'}, {'c0': 'p1'}))
engine.on_message({'type': 'left', 'peer': 'p1'})
check('and gone with them', (engine.peers, engine.locks), ({}, {}))
check('the screen was told', {'peers', 'locks'} <= set(seen), True)


print('what this person is doing, and typing in')
live, engine, sent = client('a', document('one', 'two'))
engine.presence({'selected': 'c0', 'playhead': 1.5, 'editing': None})
engine.lock('c0')
check('nothing goes before the connection is live', sent, [])
engine.connected()
relay = {'urls': ['turn:t.example.org:3478'], 'username': '9:1', 'credential': 'x'}
engine.on_message({'type': 'welcome', 'peer': 'me', 'you': {'handle': 'ana'}, 'seq': 0, 'ice': [relay, 'junk']})
engine.on_message({'type': 'changes', 'batches': []})
check('then presence and the lock go', [m['type'] for m in sent], ['hello', 'presence', 'lock'])
check("the relay subtitld.cc offers is kept", engine.ice, [relay])
heard = []
engine.listeners.append(lambda kind, *args: heard.append((kind, *args)))
engine.on_message({'type': 'chat', 'message': {'id': 'm1'}})
engine.on_message({'type': 'chat_history_request', 'from': 'p2'})
engine.on_message({'type': 'chat_history', 'from': 'p2', 'messages': [{'id': 'm0'}]})
check('chat messages go to the listeners', [h for h in heard if h[0].startswith('chat')],
      [('chat', {'id': 'm1'}), ('chat_history_request', 'p2'), ('chat_history', 'p2', [{'id': 'm0'}])])
del sent[:]
check('a chat message goes while live', (engine.chat({'id': 'm2'}), sent[-1]), (True, {'type': 'chat', 'message': {'id': 'm2'}}))
engine.on_message({'type': 'lock', 'cue_id': 'c1', 'granted': False, 'holder': 'p2'})
engine.lock('c1')
engine.on_message({'type': 'lock', 'cue_id': 'c1', 'granted': False, 'holder': 'p2'})
check('a refused lock is not held', engine.held, {'c0'})
engine.on_message({'type': 'locked', 'cue_id': 'c1', 'peer': 'p2', 'user': {'handle': 'bob'}})
check('someone else typing', (engine.locked_by('c1'), engine.locked_by('c0')), ({'handle': 'bob'}, None))
engine.unlock('c0')
check('letting go', (sent[-1], engine.held), ({'type': 'unlock', 'cue_id': 'c0'}, set()))
engine.disconnected()
del sent[:]
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
check('after reconnecting, presence again', [m['type'] for m in sent], ['hello', 'presence'])

print('typing a word is one batch to send')
live, engine, sent = client('a', document('one'))
for text in ('o', 'on', 'one!'):
    live.edit(set_text('c0', text))
engine.connected()
engine.on_message({'type': 'changes', 'batches': []})
ops = [m for m in sent if m['type'] == 'op']
check('one batch, every change in it', (len(ops), [op['value'] for op in ops[0]['ops']]), (1, ['o', 'on', 'one!']))
check('one waiting', len(live.store['pending']), 1)
live.edit(set_text('c0', 'one!!'))
engine.local_changed()
check('one sent but unconfirmed stays as it is: the next goes on its own',
      [len(m['ops']) for m in sent if m['type'] == 'op'], [3, 1])


# --- Everyone ends with the server's document --------------------------------------------------------


class Server:
    """subtitld.cc's rules (collab.documents and collab.consumers): numbered batches, each accepted once,
    refused whole when they do not fit. Answers go straight to their connection; everyone else's batches come
    through the channel layer, so one can overtake the other."""

    def __init__(self, doc):
        self.doc = copy.deepcopy(doc)
        self.log = []                      # (seq, author, client_op_id, ops)
        self.queues = {}                   # name -> {'direct': [...], 'group': [...]}

    def connect(self, name):
        self.queues[name] = {'direct': [], 'group': []}

    def disconnect(self, name):
        self.queues.pop(name, None)       # what was on its way is lost

    def receive(self, name, message):
        direct = self.queues[name]['direct']
        if message['type'] == 'hello':
            last = message['last_seq']
            direct.append({'type': 'welcome', 'peer': name, 'you': {'handle': name}, 'seq': len(self.log)})
            if last is None:
                direct.append({'type': 'snapshot', 'seq': len(self.log), 'state': copy.deepcopy(self.doc)})
            else:
                direct.append({'type': 'changes', 'batches': [
                    {'seq': s, 'author': a, 'client_op_id': c, 'ops': copy.deepcopy(o)} for s, a, c, o in self.log if s > last]})
        elif message['type'] == 'op':
            done = next((s for s, a, c, o in self.log if c == message['client_op_id']), None)
            if done is not None:
                direct.append({'type': 'ack', 'client_op_id': message['client_op_id'], 'seq': done, 'duplicate': True})
                return
            trial = copy.deepcopy(self.doc)
            try:
                docops.apply(trial, message['ops'])
            except docops.OpError as error:
                direct.append({'type': 'reject', 'client_op_id': message['client_op_id'], 'reason': 'conflict', 'detail': str(error)})
                return
            self.doc = trial
            seq = len(self.log) + 1
            self.log.append((seq, name, message['client_op_id'], copy.deepcopy(message['ops'])))
            direct.append({'type': 'ack', 'client_op_id': message['client_op_id'], 'seq': seq, 'duplicate': False})
            for other, queues in self.queues.items():
                if other != name:
                    queues['group'].append({'type': 'batch', 'seq': seq, 'author': name,
                                            'client_op_id': message['client_op_id'], 'ops': copy.deepcopy(message['ops'])})


def random_ops(rng, live, name, n):
    cues = live.screen['segments']
    roll = rng.random()
    if roll < 0.15 or not cues:
        return [{'op': 'cue.insert', 'cue': {'id': f'{name}-new-{n}', 'start': rng.uniform(0, 50), 'end': 60.0, 'text': f'{name} {n}'}}]
    cue = rng.choice(cues)['id']
    if roll < 0.25:
        return [{'op': 'cue.delete', 'id': cue}]
    if roll < 0.4:
        return [{'op': 'cue.set', 'id': cue, 'field': 'start', 'value': round(rng.uniform(0, 50), 3)}]
    if roll < 0.5:
        return [{'op': 'cue.set', 'id': cue, 'field': 'translations.pt', 'value': f'{name} pt {n}'}]
    return set_text(cue, f'{name} {n}')


def converge(seed, steps=400):
    rng = random.Random(seed)
    start = document(*[f'line {i}' for i in range(8)])
    server = Server(start)
    names = ('ana', 'bob', 'cai')
    peers = {}
    for name in names:
        live = FakeLive(name, start)
        outbox = []
        engine = Engine(outbox.append, live, store=live.store)
        peers[name] = {'live': live, 'engine': engine, 'outbox': outbox, 'online': False}

    def go_online(name):
        peer = peers[name]
        server.connect(name)
        peer['online'] = True
        peer['engine'].connected()

    def go_offline(name):
        peer = peers[name]
        server.disconnect(name)
        peer['online'] = False
        peer['outbox'].clear()
        peer['engine'].disconnected()

    def pump(name, settle=False):
        """One step of traffic for a collaborator: something it sent reaches the server, or something the
        server sent reaches it (straight answers and the others' batches in any order between them). While
        settling, what it sent always goes."""
        peer = peers[name]
        if not peer['online']:
            return False
        moved = False
        if peer['outbox'] and (settle or rng.random() < 0.7):
            server.receive(name, peer['outbox'].pop(0))
            moved = True
        queues = server.queues.get(name)
        if queues:
            ready = [q for q in ('direct', 'group') if queues[q]]
            if ready:
                peer['engine'].on_message(queues[rng.choice(ready)].pop(0))
                moved = True
        return moved

    for name in names:
        go_online(name)
    for n in range(steps):
        name = rng.choice(names)
        peer = peers[name]
        roll = rng.random()
        if roll < 0.35:
            ops = random_ops(rng, peer['live'], name, n)
            try:
                peer['live'].edit(ops)
            except docops.OpError:
                continue
            peer['engine'].local_changed()
        elif roll < 0.4:
            go_offline(name) if peer['online'] else go_online(name)
        else:
            pump(name)
            if peer['engine'].needs_resync and rng.random() < 0.2:
                peer['engine'].resync()

    # Everyone back online, and the traffic settles.
    for name in names:
        if not peers[name]['online']:
            go_online(name)
    for _round in range(10000):
        if not [moved for moved in [pump(name, settle=True) for name in names] if moved]:
            stuck = [name for name in names if peers[name]['engine'].needs_resync]
            for name in stuck:
                peers[name]['engine'].resync()
            if not stuck:
                break
    screens_match = all(norm(peers[name]['live'].screen) == norm(server.doc) for name in names)
    nothing_waiting = all(not peers[name]['live'].store['pending'] for name in names)
    return screens_match and nothing_waiting, len(server.log)


print('everyone ends with the server\'s document')
results = [converge(seed) for seed in range(60)]
check('60 runs of 400 steps: every screen is the server\'s, nothing left waiting', all(ok for ok, _ in results), True)
print(f'  ({sum(n for _, n in results)} batches accepted in all)')

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
