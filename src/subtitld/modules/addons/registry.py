"""Helpers around `session.CONFIG['addons']`.

Centralises the schema so the rest of the codebase doesn't have to know which
keys live in CONFIG — they call helper functions here. All helpers are
defensive against a missing or freshly-initialised CONFIG.

Schema
------
    CONFIG['addons'] = {
        'enabled':  {<addon_id>: bool},   # explicit on/off toggle (default True for installed)
        'defaults': {                      # which provider is the *default* per task
            'tts.synthesize': '<provider_id>',
            'asr.transcribe': '<provider_id>',
            'translate.text': '<provider_id>',
        },
        'options':  {<addon_id>: {...}},  # per-add-on free-form settings (rendered via config_schema)
        'last_catalog_fetch': float,      # epoch seconds; consumed by installer
        '_removed_builtins_pruned': int,  # see prune_removed_builtins()
    }
"""

from __future__ import annotations

from subtitld.modules import session


# Providers that USED to ship as built-ins, and which of their config entries
# go once they are gone. Their config outlives the removal, which matters
# because an add-on may legitimately reuse the same id — AssemblyAI and
# whisper.cpp both came back as out-of-process add-ons under their old ids.
#
#   'enabled'  — always. `is_enabled` only falls back to True when the key is
#                ABSENT, so a leftover `False` from the built-in silently hides
#                the same-id add-on from every engine picker.
#   'options'  — only when the settings bag is stale: AssemblyAI's held an API
#                key the add-on never reads. whisper.cpp's holds the chosen
#                model, backed by a multi-GB download the add-on reuses, and
#                its keys mean the same there: kept.
#   'defaults' — always: a defaults[task] naming a provider that is gone.
_REMOVED_BUILTINS = {
    'assemblyai': frozenset({'enabled', 'options', 'defaults'}),
    'whispercpp': frozenset({'enabled', 'defaults'}),
}

# Which ids have been pruned, so each is pruned exactly once: once an add-on
# owns the id, its settings are the user's. A list of ids; the legacy value 1
# (a single revision counter) meant `assemblyai`.
_PRUNE_KEY = '_removed_builtins_pruned'


def _root() -> dict:
    if not isinstance(session.CONFIG, dict):
        return {}
    return session.CONFIG.setdefault('addons', {})


def ensure_seeded() -> None:
    """Idempotently insert empty subdicts so callers can `[key]` without
    ceremony. Called from the manager during startup."""
    root = _root()
    root.setdefault('enabled', {})
    root.setdefault('defaults', {})
    root.setdefault('options', {})
    root.setdefault('last_catalog_fetch', 0.0)
    prune_removed_builtins()


def prune_removed_builtins() -> None:
    """Drop config left behind by providers that were removed from the app,
    once per removed id (see `_REMOVED_BUILTINS` for what goes)."""
    root = _root()
    if not isinstance(root, dict):
        return
    marker = root.get(_PRUNE_KEY)
    if isinstance(marker, list):
        done = {str(x) for x in marker}
    elif marker:
        done = {'assemblyai'}      # the old single-revision marker
    else:
        done = set()
    pending = [i for i in _REMOVED_BUILTINS if i not in done]
    if not pending and isinstance(marker, list):
        return

    enabled = root.get('enabled')
    options = root.get('options')
    defaults = root.get('defaults')
    for addon_id in pending:
        drop = _REMOVED_BUILTINS[addon_id]
        if 'enabled' in drop and isinstance(enabled, dict):
            enabled.pop(addon_id, None)
        if 'options' in drop and isinstance(options, dict):
            options.pop(addon_id, None)
        if 'defaults' in drop and isinstance(defaults, dict):
            for task, provider_id in list(defaults.items()):
                if provider_id == addon_id:
                    del defaults[task]
        done.add(addon_id)
    _migrate_whispercpp_options(options)
    root[_PRUNE_KEY] = sorted(done)


# The whisper.cpp add-on's `threads` choices ("0" is auto).
_WHISPERCPP_THREADS = (0, 1, 2, 4, 8, 16)


def _migrate_whispercpp_options(options) -> None:
    """The built-in stored `threads` as any number; the add-on's Configure
    field is a list of strings, which shows a value it does not list as its
    first entry and saves that back. Snap to the nearest choice not above it
    (0 and below: auto). Idempotent."""
    bag = options.get('whispercpp') if isinstance(options, dict) else None
    value = bag.get('threads') if isinstance(bag, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        bag['threads'] = str(max(t for t in _WHISPERCPP_THREADS if t <= max(value, 0)))


def is_enabled(addon_id: str, default: bool = True) -> bool:
    return bool(_root().get('enabled', {}).get(addon_id, default))


def set_enabled(addon_id: str, enabled: bool) -> None:
    _root().setdefault('enabled', {})[addon_id] = bool(enabled)


def default_for_task(task: str) -> str | None:
    return _root().get('defaults', {}).get(task)


def set_default_for_task(task: str, provider_id: str | None) -> None:
    defaults = _root().setdefault('defaults', {})
    if provider_id is None:
        defaults.pop(task, None)
    else:
        defaults[task] = provider_id


def options_for(addon_id: str) -> dict:
    """Per-add-on free-form settings dict. Always returns the live mutable
    dict — caller can edit in place."""
    return _root().setdefault('options', {}).setdefault(addon_id, {})


def set_options_for(addon_id: str, options: dict) -> None:
    _root().setdefault('options', {})[addon_id] = options
