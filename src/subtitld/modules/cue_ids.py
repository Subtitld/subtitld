"""Stable IDs for subtitles

Every subtitle carries an 'id' that stays the same for its whole life: across
saves, undo and redo, and between collaborators editing the same project. A
subtitle is found by it, not by its place in the list or by the dict itself
(undo replaces the dicts with copies).

IDs are random, so collaborators can make new subtitles offline without
asking anyone for a number.
"""

import secrets


def new_id():
    """A fresh subtitle ID: 16 hex digits (64 random bits)."""
    return secrets.token_hex(8)


def ensure_ids(segments):
    """Give every subtitle in `segments` an ID, in place.

    A subtitle without one gets a new one; so does a later subtitle sharing
    an earlier one's ID (a copied dict). Returns `segments`."""
    seen = set()
    for segment in segments or []:
        sid = segment.get('id')
        if not sid or not isinstance(sid, str) or sid in seen:
            sid = new_id()
            while sid in seen:
                sid = new_id()
            segment['id'] = sid
        seen.add(sid)
    return segments


def find(segments, sid):
    """The subtitle in `segments` with ID `sid`, or None."""
    if not sid:
        return None
    for segment in segments or []:
        if segment.get('id') == sid:
            return segment
    return None
