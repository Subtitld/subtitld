"""The editors' chat as each Subtitld keeps it (modules.collab_chat): copies merging, edits and deletions
only by the message's author, unread counts, and the conversation kept in the project file when asked.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import collab_chat, collab_state

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def stamped(message, handle, at):
    return dict(message, author={'handle': handle, 'name': handle.title()}, at=at, sig='s')


print('messages')
hello = collab_chat.say('Hello', cue_id='0123456789abcdef', at_ms=1500.7)
check('an ID subtitld.cc takes', (len(hello['id']), set(hello['id']) <= set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')), (12, True))
check('the moment in whole milliseconds', hello['at_ms'], 1500)
check('long text cut to what subtitld.cc takes', len(collab_chat.say('x' * 5000)['text']), collab_chat.MAX_TEXT)

print('copies of the conversation merge')
a, b = stamped(collab_chat.say('First'), 'ana', 100), stamped(collab_chat.say('Second'), 'bob', 200)
kept = []
check('new ones join', collab_chat.merge(kept, [b, a]), 2)
check('in order of time', [m['text'] for m in kept], ['First', 'Second'])
check('ones already here do not, nor ones that are not messages', collab_chat.merge(kept, [a, {'id': 'x'}, 'junk']), 0)

print('edits and deletions')
kept.append(stamped(collab_chat.edit(a['id'], 'First, edited'), 'ana', 300))
kept.append(stamped(collab_chat.edit(b['id'], 'Bob never wrote this'), 'ana', 310))
kept.append(stamped(collab_chat.delete(b['id']), 'ana', 320))
shown = collab_chat.conversation(kept)
check("one's own, applied", (shown[0]['text'], shown[0]['edited']), ('First, edited', True))
check("someone else's, ignored", (shown[1]['text'], shown[1]['edited'], shown[1]['deleted']), ('Second', False, False))
kept.append(stamped(collab_chat.delete(b['id']), 'bob', 330))
check('deleted by its author', collab_chat.conversation(kept)[1]['deleted'], True)
late_edit = stamped(collab_chat.edit(a['id'], 'An older edit'), 'ana', 250)
collab_chat.merge(kept, [late_edit])
check('the latest edit wins, whatever order they came in', collab_chat.conversation(kept)[0]['text'], 'First, edited')
outbox = [collab_chat.say('Not sent yet')]
check('what waits to be sent shows last, pending', [(m['text'], m['pending']) for m in collab_chat.conversation(kept, outbox)][-1],
      ('Not sent yet', True))

print('unread')
check("others' messages after the last read", collab_chat.unread(kept, 0, 'ana'), 1)
check('none after reading', collab_chat.unread(kept, 200, 'ana'), 0)

print('in the project file')
collab_state.start('doc1', 'https://draft.subtitld.cc', seq=0, base={'segments': []})
collab_state.STATE['chat'] = list(kept)
collab_state.STATE['chat_read'] = 200
check('not kept unless asked', 'chat.json' in collab_state.export(), False)
collab_state.STATE['save_chat'] = True
members = collab_state.export()
state = collab_state.read(members['collab.json'], members.get('pending.jsonl', b''), members.get('collab-base.json'),
                          members['chat.json'])
check('kept, and back on opening', ([m['id'] for m in state['chat']], state['save_chat'], state['chat_read']),
      ([m['id'] for m in kept], True, 200))
check('a chat.json that does not read is no chat', collab_chat.read(b'not json'), [])
check('an older project has none', collab_state.read(members['collab.json'])['chat'], [])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
