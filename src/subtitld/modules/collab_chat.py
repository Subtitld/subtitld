"""Editors' chat in a shared project (LIVE_COLLABORATION_PLAN.md, "Editors' chat")

subtitld.cc passes messages on and keeps none: each Subtitld keeps the conversation as it has seen it, a list
of the messages as the server stamped them ({id, action, text, cue_id, at_ms, reply_to, author, at, sig}), and
whoever wants to can keep it in the project file. Someone who joins late gets what they missed from a
collaborator who is online; the server checks the signatures on the way, so a copy can't carry invented
messages.

Three actions: `say` a message (which may point at a subtitle, `cue_id`, and a moment of the video, `at_ms`,
or answer another, `reply_to`); `edit` and `delete` one of your own (`reply_to` is the message). Edits and
deletions are messages too, so everyone applies them, in any order; one that isn't by the message's author
is ignored. Qt-free: modules.collab_service does the sending.
"""

import json
import secrets

MAX_TEXT = 2000
MAX_KEPT = 5000
VERSION = 1


def new_id():
    """A message's ID: 12 characters of [A-Za-z0-9_-], as subtitld.cc takes them."""
    return secrets.token_urlsafe(9)


def say(text, cue_id=None, at_ms=None, reply_to=None):
    return {'id': new_id(), 'action': 'say', 'text': text[:MAX_TEXT], 'cue_id': cue_id,
            'at_ms': None if at_ms is None else int(at_ms), 'reply_to': reply_to}


def edit(message_id, text):
    return {'id': new_id(), 'action': 'edit', 'text': text[:MAX_TEXT], 'cue_id': None, 'at_ms': None,
            'reply_to': message_id}


def delete(message_id):
    return {'id': new_id(), 'action': 'delete', 'text': '', 'cue_id': None, 'at_ms': None, 'reply_to': message_id}


def _well_formed(message):
    return (isinstance(message, dict) and isinstance(message.get('id'), str) and isinstance(message.get('at'), int)
            and message.get('action') in ('say', 'edit', 'delete') and isinstance(message.get('author'), dict))


def merge(messages, incoming):
    """Add the messages of `incoming` not in `messages` yet (in place, kept in order of time). Returns how many
    were new."""
    known = {message['id'] for message in messages}
    added = 0
    for message in incoming or []:
        if _well_formed(message) and message['id'] not in known:
            messages.append(message)
            known.add(message['id'])
            added += 1
    if added:
        messages.sort(key=lambda message: (message['at'], message['id']))
        del messages[:-MAX_KEPT]
    return added


def conversation(messages, outbox=()):
    """What the chat shows: each message said, as edited, with
    {'id', 'author', 'at', 'text', 'cue_id', 'at_ms', 'reply_to', 'edited', 'deleted', 'pending'}; messages
    not sent yet (`outbox`) at the end, `pending`. Edits and deletions apply only when their author wrote the
    message."""
    shown, order = {}, []
    for message in messages:
        if message['action'] == 'say':
            shown[message['id']] = dict(message, edited=False, deleted=False, pending=False)
            order.append(message['id'])
    for message in messages:
        target = shown.get(message.get('reply_to')) if message['action'] != 'say' else None
        if target is None or (target['author'] or {}).get('handle') != (message['author'] or {}).get('handle'):
            continue
        if message['action'] == 'delete':
            target.update(text='', deleted=True)
        elif not target['deleted'] and message['at'] >= target.get('_edited_at', 0):
            target.update(text=message['text'], edited=True, _edited_at=message['at'])
    result = [shown[message_id] for message_id in order]
    for message in result:
        message.pop('_edited_at', None)
    for message in outbox:
        if message['action'] == 'say':
            result.append(dict(message, author=None, at=None, edited=False, deleted=False, pending=True))
    return result


def unread(messages, read_at, me):
    """How many messages said by others came after `read_at` (ms)."""
    return sum(1 for message in messages
               if message['action'] == 'say' and message['at'] > (read_at or 0)
               and (message['author'] or {}).get('handle') != me)


def export(messages):
    """The conversation as the project file keeps it."""
    return json.dumps({'version': VERSION, 'messages': messages}, ensure_ascii=False).encode('utf-8')


def read(data):
    """The conversation in a project file's chat.json ([] when it doesn't read)."""
    try:
        content = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return []
    if not isinstance(content, dict) or content.get('version') != VERSION:
        return []
    messages = []
    merge(messages, content.get('messages') if isinstance(content.get('messages'), list) else [])
    return messages
