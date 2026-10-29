"""Changes to a subtitle document

A document is a dict of three parts:

    {'segments': [subtitle dict, ...],    # each with a stable 'id' (modules.cue_ids)
     'speakers': {name: speaker dict},
     'meta': {'language': ..., ...}}      # only META_FIELDS are its document's

A change ("op") is a small JSON-able dict naming one subtitle, speaker or
document field and what to do with it:

    {'op': 'cue.insert', 'cue': {...}}                      # with its 'id'
    {'op': 'cue.delete', 'id': ...}
    {'op': 'cue.set', 'id': ..., 'field': ..., 'value': ...}
    {'op': 'cue.unset', 'id': ..., 'field': ...}
    {'op': 'speaker.insert', 'name': ..., 'speaker': {...}}
    {'op': 'speaker.delete', 'name': ...}
    {'op': 'speaker.set', 'name': ..., 'field': ..., 'value': ...}
    {'op': 'speaker.unset', 'name': ..., 'field': ...}
    {'op': 'meta.set', 'field': ..., 'value': ...}
    {'op': 'meta.unset', 'field': ...}

A subtitle's translations change one language at a time: the field
'translations.pt-br' is that language's text. A list of changes is applied
together, and `apply` returns the list that undoes it.

What a document holds is only what collaborators share: keys starting with
'_' (at any depth) are the app's runtime data, and so are a speaker's
picture and its bytes (SPEAKER_RUNTIME), which travel as media. A file's
place on someone's computer (LOCAL_KEYS: a dub's "path" and "raw_path", at
any depth) is theirs alone: a dub names its files by content ("hash",
"raw_hash"), and each app finds or fetches its own copy.

Pure Python, standard library only: the subtitld.cc server and Subtitld apply
the same changes with this same module. Its home is the subtitld.cc
repository (packages/collab); Subtitld bundles a copy as modules/docops.py,
made with packages/collab/copy_into_subtitld.py. Change it there, then copy.
"""

import copy
from bisect import bisect

META_FIELDS = ("language", "source_language", "translations")
SPEAKER_RUNTIME = ("image", "image_bytes")
LOCAL_KEYS = ("path", "raw_path")
# A subtitle's fields changed one key at a time, as 'field.key'.
SPLIT_FIELDS = ("translations",)


class OpError(ValueError):
    """A change that does not fit the document: its subtitle or speaker is
    not there, or is there already."""


def plain(value):
    """`value` as a document holds it: a deep copy without runtime keys or local paths."""
    if isinstance(value, dict):
        return {
            key: plain(item)
            for key, item in value.items()
            if not (isinstance(key, str) and (key.startswith("_") or key in LOCAL_KEYS))
        }
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def plain_speaker(speaker):
    if not isinstance(speaker, dict):
        return {}
    return {key: value for key, value in plain(speaker).items() if key not in SPEAKER_RUNTIME}


def snapshot(segments, speakers, meta):
    """A document copied from live state, as a document holds it."""
    return {
        "segments": [plain(segment) for segment in segments or []],
        "speakers": {name: plain_speaker(speaker) for name, speaker in (speakers or {}).items()},
        "meta": {field: plain(meta[field]) for field in META_FIELDS if field in (meta or {})},
    }


# --------------------------------------------------------------------------- #
# Applying                                                                     #
# --------------------------------------------------------------------------- #
def _find(segments, sid):
    for index, segment in enumerate(segments):
        if segment.get("id") == sid:
            return index, segment
    return None, None


def _get(target, field):
    """(present, value) of `field` in `target`, 'field.key' one level down."""
    head, _, key = field.partition(".")
    if key and head in SPLIT_FIELDS:
        inner = target.get(head)
        if not isinstance(inner, dict) or key not in inner:
            return False, None
        return True, inner[key]
    if field not in target:
        return False, None
    return True, target[field]


def _put(target, field, value):
    head, _, key = field.partition(".")
    if key and head in SPLIT_FIELDS:
        inner = target.get(head)
        if not isinstance(inner, dict):
            inner = target[head] = {}
        inner[key] = copy.deepcopy(value)
    else:
        target[field] = copy.deepcopy(value)


def _drop(target, field):
    head, _, key = field.partition(".")
    if key and head in SPLIT_FIELDS:
        inner = target.get(head)
        if isinstance(inner, dict):
            inner.pop(key, None)
    else:
        target.pop(field, None)


def _undo_field(kind, ref, target, field):
    """The change that puts `field` of `target` back as it is now."""
    head, _, key = field.partition(".")
    if key and head in SPLIT_FIELDS and not isinstance(target.get(head), dict):
        # No translations yet: undoing the first one leaves none, not an
        # empty set of them.
        field = head
    present, value = _get(target, field)
    if present:
        return {"op": f"{kind}.set", **ref, "field": field, "value": copy.deepcopy(value)}
    return {"op": f"{kind}.unset", **ref, "field": field}


def _apply_one(document, op):
    """Apply `op` in place; return the change that undoes it."""
    kind = op.get("op")
    segments = document["segments"]

    if kind == "cue.insert":
        cue = op["cue"]
        if not cue.get("id"):
            raise OpError("A subtitle to insert needs an id")
        if _find(segments, cue["id"])[1] is not None:
            raise OpError(f"Subtitle {cue['id']} is there already")
        new = copy.deepcopy(cue)
        # In time order, after any starting at the same time (as
        # modules.subtitles.add_subtitle places a new one).
        index = bisect([segment.get("start", 0.0) for segment in segments], new.get("start", 0.0))
        segments.insert(index, new)
        return {"op": "cue.delete", "id": cue["id"]}

    if kind in ("cue.delete", "cue.set", "cue.unset"):
        index, segment = _find(segments, op.get("id"))
        if segment is None:
            raise OpError(f"Subtitle {op.get('id')} is not there")
        if kind == "cue.delete":
            del segments[index]
            return {"op": "cue.insert", "cue": plain(segment)}
        field = op["field"]
        if field == "id":
            raise OpError("A subtitle's id does not change")
        undo = _undo_field("cue", {"id": op["id"]}, segment, field)
        if kind == "cue.set":
            _put(segment, field, op["value"])
        else:
            _drop(segment, field)
        return undo

    speakers = document["speakers"]
    if kind == "speaker.insert":
        if op["name"] in speakers:
            raise OpError(f"Speaker {op['name']} is there already")
        speakers[op["name"]] = copy.deepcopy(op.get("speaker") or {})
        return {"op": "speaker.delete", "name": op["name"]}

    if kind in ("speaker.delete", "speaker.set", "speaker.unset"):
        speaker = speakers.get(op.get("name"))
        if speaker is None:
            raise OpError(f"Speaker {op.get('name')} is not there")
        if kind == "speaker.delete":
            del speakers[op["name"]]
            return {"op": "speaker.insert", "name": op["name"], "speaker": plain_speaker(speaker)}
        field = op["field"]
        undo = _undo_field("speaker", {"name": op["name"]}, speaker, field)
        if kind == "speaker.set":
            _put(speaker, field, op["value"])
        else:
            _drop(speaker, field)
        return undo

    if kind in ("meta.set", "meta.unset"):
        meta = document["meta"]
        field = op["field"]
        undo = _undo_field("meta", {}, meta, field)
        if kind == "meta.set":
            _put(meta, field, op["value"])
        else:
            _drop(meta, field)
        return undo

    raise OpError(f"Unknown change {kind!r}")


def apply(document, ops):
    """Apply `ops`, in order, to `document` in place. Returns the changes that
    undo them (apply those to get the document back). Raises OpError on a
    change that does not fit, leaving the ones before it applied."""
    undo = [_apply_one(document, op) for op in ops]
    undo.reverse()
    return undo


# --------------------------------------------------------------------------- #
# Diffing                                                                      #
# --------------------------------------------------------------------------- #
def _diff_fields(kind, ref, old, new, runtime=()):
    """Changes making the fields of `old` (as a document holds them) those of
    `new` (live)."""
    ops = []
    keys = [key for key in new if not (isinstance(key, str) and key.startswith("_")) and key not in runtime]
    for key in keys:
        if key == "id":
            continue
        value = new[key]
        if key in old and old[key] == value:
            continue
        if not isinstance(value, (str, int, float, bool, type(None))):
            value = plain(value)
        if key in SPLIT_FIELDS and isinstance(value, dict) and isinstance(old.get(key), dict):
            before = old[key]
            for inner, text in value.items():
                if inner not in before or before[inner] != text:
                    ops.append({"op": f"{kind}.set", **ref, "field": f"{key}.{inner}", "value": text})
            for inner in before:
                if inner not in value:
                    ops.append({"op": f"{kind}.unset", **ref, "field": f"{key}.{inner}"})
        elif key not in old or old[key] != value:
            ops.append({"op": f"{kind}.set", **ref, "field": key, "value": value})
    for key in old:
        if key != "id" and key not in keys:
            ops.append({"op": f"{kind}.unset", **ref, "field": key})
    return ops


def diff(old, segments, speakers, meta):
    """The changes that turn document `old` into the live state given.

    `old` is a document as `snapshot` makes it; the live state is what the
    app holds (runtime keys and all). Every live subtitle needs an id."""
    ops = []

    old_by_id = {segment["id"]: segment for segment in old["segments"]}
    new_ids = set()
    for segment in segments:
        sid = segment["id"]
        new_ids.add(sid)
        before = old_by_id.get(sid)
        if before is None:
            ops.append({"op": "cue.insert", "cue": plain(segment)})
        else:
            ops.extend(_diff_fields("cue", {"id": sid}, before, segment))
    deleted = [{"op": "cue.delete", "id": sid} for sid in old_by_id if sid not in new_ids]

    for name, speaker in speakers.items():
        before = old["speakers"].get(name)
        if before is None:
            ops.append({"op": "speaker.insert", "name": name, "speaker": plain_speaker(speaker)})
        else:
            ops.extend(
                _diff_fields(
                    "speaker",
                    {"name": name},
                    before,
                    speaker if isinstance(speaker, dict) else {},
                    SPEAKER_RUNTIME,
                )
            )
    for name in old["speakers"]:
        if name not in speakers:
            ops.append({"op": "speaker.delete", "name": name})

    live_meta = {field: meta[field] for field in META_FIELDS if field in meta}
    ops.extend(_diff_fields("meta", {}, old["meta"], live_meta))

    return deleted + ops
