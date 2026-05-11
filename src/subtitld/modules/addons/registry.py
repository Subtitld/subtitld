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
    }
"""

from __future__ import annotations

from subtitld.modules import session


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
