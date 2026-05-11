"""Top-level registry of providers — built-ins + discovered add-ons.

The `AddonManager` is a singleton accessed via `addons.get_manager()`. It:

  - holds a list of all currently-registered providers
  - is queried by the UI when populating engine comboboxes
  - knows which provider is the user's default for each task
  - is hooked to `app.aboutToQuit` so subprocess add-ons get a clean shutdown

Discovery is filesystem-based: anything under
`session.PATH_SUBTITLD_ADDONS/<id>/manifest.json` becomes one
`AddonTTSProvider` / `AddonASRProvider` / `AddonTranslationProvider`
depending on the tasks declared in its manifest. We don't spawn the
subprocess at discovery time — that's lazy, on first request.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Signal

from subtitld.modules import session
from subtitld.modules.addons import registry
from subtitld.modules.addons.addon_provider import (
    AddonASRProvider,
    AddonAudioSeparatorProvider,
    AddonTranslationProvider,
    AddonTTSProvider,
)
from subtitld.modules.addons.provider import (
    ASRProvider,
    AudioSeparatorProvider,
    Provider,
    TASK_ASR_TRANSCRIBE,
    TASK_AUDIO_SEPARATE,
    TASK_TRANSLATE,
    TASK_TTS_SYNTHESIZE,
    TTSProvider,
    TranslationProvider,
)

log = logging.getLogger(__name__)

_PLATFORM_KEYS = {
    'linux': 'linux',
    'darwin': 'macos',
    'win32': 'windows',
}


def _current_platform_key() -> str:
    return _PLATFORM_KEYS.get(sys.platform, sys.platform)


class AddonManager(QObject):
    """Single source of truth for registered providers.

    Emits `providers_changed` whenever the registered set is mutated
    (register / unregister / discover that adds something new). UI panels
    that show a list of providers — the dubbing engine combobox, the
    transcription engine combobox, the addons panel itself — connect to
    this signal so a runtime install/uninstall rebuilds them without
    requiring an app restart.
    """

    providers_changed = Signal()

    def __init__(self):
        super().__init__()
        self._providers: dict[str, Provider] = {}
        self._lock = threading.Lock()
        registry.ensure_seeded()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, provider: Provider) -> None:
        """Add a provider to the registry. Replacing an existing id is OK —
        useful for hot-reloading during development."""
        pid = provider.id
        with self._lock:
            existing = self._providers.get(pid)
            if existing is not None and existing is not provider:
                log.info('AddonManager: replacing provider %s', pid)
                try:
                    existing.shutdown()
                except Exception:
                    log.exception('AddonManager: shutdown of replaced provider %s failed', pid)
            self._providers[pid] = provider
        # Emit OUTSIDE the lock — listeners may re-enter the manager
        # (e.g. call `providers_for_task`) while handling the signal.
        self.providers_changed.emit()

    def register_builtin(self, provider: Provider) -> None:
        """Sugar for `register` that just makes intent obvious at the call
        site."""
        self.register(provider)

    def unregister(self, provider_id: str) -> None:
        with self._lock:
            provider = self._providers.pop(provider_id, None)
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                log.exception('AddonManager: shutdown of %s failed', provider_id)
            self.providers_changed.emit()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, provider_id: str) -> Provider | None:
        with self._lock:
            return self._providers.get(provider_id)

    def all_providers(self) -> list[Provider]:
        with self._lock:
            return list(self._providers.values())

    def providers_for_task(self, task: str) -> list[Provider]:
        with self._lock:
            return [p for p in self._providers.values()
                    if task in p.tasks and registry.is_enabled(p.id)]

    def default_for_task(self, task: str) -> Provider | None:
        """Resolve the configured default for `task`, with a sensible fallback
        chain: explicit setting → first built-in → first discovered → None."""
        configured = registry.default_for_task(task)
        if configured:
            provider = self.get(configured)
            if provider is not None and task in provider.tasks and registry.is_enabled(provider.id):
                return provider
        # Fall back to first built-in, then first add-on.
        candidates = self.providers_for_task(task)
        if not candidates:
            return None
        candidates.sort(key=lambda p: (not p.is_builtin, p.id))
        return candidates[0]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(self, addons_dir: Path | None = None) -> list[str]:
        """Scan the addons directory and register one provider per declared
        task. Returns the list of newly-registered provider ids. Idempotent —
        skips ids that are already registered.
        """
        addons_root = Path(addons_dir) if addons_dir else Path(session.PATH_SUBTITLD_ADDONS)
        if not addons_root.exists():
            return []

        registered: list[str] = []
        for entry in sorted(addons_root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / 'manifest.json'
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning('AddonManager: skipping %s — bad manifest: %s', entry.name, exc)
                continue

            addon_id = manifest.get('id') or entry.name
            if self.get(addon_id) is not None:
                log.debug('AddonManager: %s already registered, skipping', addon_id)
                continue

            # Verify platform support before instantiating.
            platforms = manifest.get('platforms')
            if isinstance(platforms, list) and platforms:
                if _current_platform_key() not in platforms:
                    log.info('AddonManager: %s declares platforms=%s — skipping on %s',
                             addon_id, platforms, _current_platform_key())
                    continue

            exe_rel = manifest.get('executable')
            if not exe_rel:
                log.warning('AddonManager: %s has no `executable` field — skipping', addon_id)
                continue
            exe_path = entry / exe_rel
            if not exe_path.exists():
                log.warning('AddonManager: %s executable %s missing — skipping',
                            addon_id, exe_path)
                continue

            tasks = manifest.get('tasks') or []
            if not tasks:
                log.warning('AddonManager: %s declares no tasks — skipping', addon_id)
                continue

            # Pick the right provider class based on declared tasks. An add-on
            # advertising multiple tasks gets ONE provider object that
            # implements whichever ABC matches the *first* task — for v0 we
            # punt on multi-task add-ons; the manager will only expose it for
            # that one task. Multi-capability is tracked in the open-issues
            # list of the plan.
            primary = tasks[0]
            provider: Provider
            if primary == TASK_TTS_SYNTHESIZE:
                provider = AddonTTSProvider(addon_id, manifest, exe_path)
            elif primary == TASK_ASR_TRANSCRIBE:
                provider = AddonASRProvider(addon_id, manifest, exe_path)
            elif primary == TASK_TRANSLATE:
                provider = AddonTranslationProvider(addon_id, manifest, exe_path)
            elif primary == TASK_AUDIO_SEPARATE:
                provider = AddonAudioSeparatorProvider(addon_id, manifest, exe_path)
            else:
                log.warning('AddonManager: %s primary task %r not supported',
                            addon_id, primary)
                continue

            self.register(provider)
            registered.append(addon_id)
            log.info('AddonManager: registered add-on %s (%s)', addon_id, primary)

        return registered

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown_all(self) -> None:
        with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
        for provider in providers:
            try:
                provider.shutdown()
            except Exception:
                log.exception('AddonManager: shutdown of %s failed', provider.id)


_manager_instance: AddonManager | None = None


def get_manager() -> AddonManager:
    """Module-level singleton accessor. Lazy on first call so unit tests can
    monkeypatch by reassigning `manager._manager_instance`."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = AddonManager()
    return _manager_instance
