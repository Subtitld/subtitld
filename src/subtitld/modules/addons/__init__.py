"""Subtitld add-on system.

Public surface (intentionally tiny — most code goes through the manager):

    from subtitld.modules import addons
    mgr = addons.get_manager()
    mgr.discover()
    providers = mgr.providers_for_task('tts.synthesize')

Built-in providers (Edge TTS and gTTS for TTS, the FFmpeg separator) live
under `subtitld.modules.addons.builtin` and are registered explicitly during
app startup. No speech-to-text engine is built in. External add-ons —
including Vosk and whisper.cpp, which used to be built-ins — are discovered from `session.PATH_SUBTITLD_ADDONS/<id>/manifest.json`.
"""

# The add-ons' log lines (process.py, manager.py, installer.py) go where
# the rest of Subtitld's do: warnings by default, everything with --debug
# (modules/console).

from subtitld.modules.addons.manager import AddonManager, get_manager  # noqa: F401
