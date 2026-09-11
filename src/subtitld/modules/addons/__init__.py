"""Subtitld add-on system.

Public surface (intentionally tiny — most code goes through the manager):

    from subtitld.modules import addons
    mgr = addons.get_manager()
    mgr.discover()
    providers = mgr.providers_for_task('tts.synthesize')

Built-in providers (Edge TTS for TTS, whisper.cpp for ASR) live under
`subtitld.modules.addons.builtin` and are registered explicitly during app
startup. External add-ons — including Vosk, which used to be a built-in —
are discovered from `session.PATH_SUBTITLD_ADDONS/<id>/manifest.json`.
"""

import logging as _logging
import sys as _sys

# Make the addons subsystem visible by default. Subtitld doesn't call
# `logging.basicConfig`, so without this every `log.info(...)` in
# process.py / manager.py / installer.py vanishes into the root logger's
# WARNING-level filter — exactly the wrong default for "addon X did not
# work, what happened?" debugging. We attach our own StreamHandler so the
# config doesn't depend on whatever (if anything) the host eventually sets
# on the root logger.
_addons_logger = _logging.getLogger('subtitld.modules.addons')
if not _addons_logger.handlers:
    _h = _logging.StreamHandler(_sys.stderr)
    _h.setFormatter(_logging.Formatter('[subtitld.addons] %(levelname)s %(message)s'))
    _addons_logger.addHandler(_h)
    _addons_logger.setLevel(_logging.INFO)
    # Don't double-print if someone later configures the root logger.
    _addons_logger.propagate = False

from subtitld.modules.addons.manager import AddonManager, get_manager  # noqa: F401
