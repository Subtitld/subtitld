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


# Provider ids that USED to ship as built-ins and no longer do. Their config
# entries outlive the removal, which matters because an add-on may legitimately
# reuse the same id — the AssemblyAI cloud provider came back as an
# out-of-process add-on with id `assemblyai`. A stale `enabled: False` left by
# the removed built-in would then hide the freshly installed add-on from every
# engine picker, with no error and nothing on screen to explain it.
_REMOVED_BUILTIN_IDS = frozenset({'assemblyai'})

# Bump when `_REMOVED_BUILTIN_IDS` grows, so the prune runs again for the new
# entries. Without a revision marker this would either run once and miss later
# additions, or run every launch and wipe settings the user has since entered
# for a same-id add-on.
_PRUNE_REVISION = 1
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
    """Drop config left behind by providers that were removed from the app.

    Runs once per `_PRUNE_REVISION` (tracked by `_PRUNE_KEY`) rather than on
    every launch: once an add-on legitimately owns one of these ids, its
    settings are the user's and must not be cleared out from under them.

    Three things go:

      * `enabled[id]` — an explicit `False` from the removed built-in. This is
        the one that bites: `is_enabled` only falls back to its `True` default
        when the key is ABSENT, so a leftover `False` silently hides a
        same-id add-on.
      * `options[id]` — the removed provider's settings bag. For the cloud
        providers this held an API key, which is both stale and a credential
        we have no reason to keep sitting in the config file. Cloud-backed
        add-ons read auth from the shared SubtitldCloud slot anyway, so
        nothing the user still needs is lost.
      * any `defaults[task]` pointing at a removed id, which would otherwise
        resolve to a provider that no longer exists.
    """
    root = _root()
    if not isinstance(root, dict):
        return
    if int(root.get(_PRUNE_KEY, 0) or 0) >= _PRUNE_REVISION:
        return

    enabled = root.get('enabled')
    options = root.get('options')
    for addon_id in _REMOVED_BUILTIN_IDS:
        if isinstance(enabled, dict):
            enabled.pop(addon_id, None)
        if isinstance(options, dict):
            options.pop(addon_id, None)

    defaults = root.get('defaults')
    if isinstance(defaults, dict):
        for task, provider_id in list(defaults.items()):
            if provider_id in _REMOVED_BUILTIN_IDS:
                del defaults[task]

    root[_PRUNE_KEY] = _PRUNE_REVISION


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
