"""Cross-module Qt signal hub.

Some background workers (the USFX heavy-asset extractor, the speaker
thumbnail decoder, …) produce data the UI needs to observe. The workers
are born inside `file_io` before the production-screen widgets exist, so
we can't pass widget references in. Instead they emit on the
process-wide singleton declared here; the UI connects once at startup
and never has to track per-load thread instances.

Connections are auto-promoted to `Qt.QueuedConnection` by Qt when the
emitting object (the worker QThread) and the receiver (a UI widget on
the main thread) live in different threads — so receivers can mutate
widgets safely without locks.
"""

from PySide6.QtCore import QObject, Signal


class _SessionSignals(QObject):
    # Speaker thumbnail finished decoding off the main thread. The
    # downscaled QImage is already written into
    # `session.SPEAKERS[name]['image']` before this fires; listeners
    # only need to re-render. Argument: speaker name.
    speaker_image_ready = Signal(str)

    # USFX Phase 2 finished (success or best-effort failure).
    # Listeners use this to flip "extracting" UI back to normal (the
    # Save-button gate is the only one left now that the progress bar
    # was removed).
    usfx_background_load_finished = Signal()

    # USFX Phase 2 about to start streaming heavy assets. Fires
    # synchronously from the main thread, right before
    # `extractor.start()`. Used by the Save button to disable itself
    # while assets are still inside the original zip — saving mid-stream
    # would re-zip dubs whose source bytes aren't on disk yet.
    usfx_background_load_started = Signal()

    # `session.SPEAKERS` was mutated (added / removed / renamed). The
    # left-panel speakers list listens so it can re-render after a
    # background job — typically transcription — populates speakers
    # without the user having to click the speakers panel for a
    # refresh. Emit AFTER the dict mutation so listeners see the new
    # state when they re-render.
    speakers_changed = Signal()

    # A single zip member finished extracting to its target path. Arguments:
    # `arcname` (the in-zip path, e.g. 'assets/dubs/abc123.wav') and
    # `target_path` (the absolute on-disk path the bytes now live at).
    #
    # Connected by the timeline (to repaint the matching dub clip out of
    # its hatched-placeholder state) and by the audio engine (to preload
    # the dub WAV proactively, instead of waiting for an audio-callback
    # miss). The two share one signal — receivers filter by arcname.
    #
    # NOTE: fires from the extractor worker thread; Qt auto-promotes the
    # delivery to QueuedConnection for main-thread receivers, so widget
    # mutations + `update()` calls in slots are safe without locks.
    usfx_member_ready = Signal(str, str)


SIGNALS = _SessionSignals()
