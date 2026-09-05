"""Abstract provider interfaces — the contract every TTS/ASR/translation
backend has to satisfy, regardless of whether it lives in-process (built-ins
like Edge TTS, AssemblyAI) or out-of-process (subprocess add-ons that
speak the JSON-line protocol from `protocol.py`).

The shapes here are deliberately aligned with the existing `EdgeTTSEngine`
signal layout in `left_panel_dubbing.py` so the rest of the app doesn't need
to be rewritten when we swap a hardcoded engine reference for a provider
lookup. In particular `speech_ready(uid, subtitle, path)` matches verbatim.
"""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QObject, Signal


# ---------------------------------------------------------------------------
# Capability tasks. Strings are the wire-level identifiers used in both the
# add-on manifest (`tasks: [...]`) and the protocol (`type` field of a request
# frame). Don't rename without bumping `protocol.PROTOCOL_VERSION`.
# ---------------------------------------------------------------------------
TASK_TTS_SYNTHESIZE = 'tts.synthesize'
TASK_ASR_TRANSCRIBE = 'asr.transcribe'
TASK_ASR_STREAM = 'asr.stream'
TASK_TRANSLATE = 'translate.text'
TASK_AUDIO_SEPARATE = 'audio.separate'
TASK_VIDEO_MANIPULATE = 'video.manipulate'
TASK_VIDEO_LIPSYNC = 'video.lipsync'


class Provider(QObject):
    """Common provider base.

    Subclasses (`TTSProvider`, `ASRProvider`, ...) add task-specific signals
    and methods. The `id` is what gets stored in saved projects (e.g.
    `subtitle['dubbing'][0]['engine']`) and what the UI uses for combobox
    selection — must be stable across versions.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

    # ---- identity -------------------------------------------------------
    @property
    def id(self) -> str:  # noqa: A003 — name mirrors manifest field
        raise NotImplementedError

    @property
    def display_name(self) -> str:
        return self.id

    @property
    def is_builtin(self) -> bool:
        """Built-in providers ship inside the Subtitld binary; external ones
        live under `PATH_SUBTITLD_ADDONS`. The UI uses this to gate the
        Uninstall button."""
        return False

    @property
    def tasks(self) -> list[str]:
        """Wire-level task identifiers this provider can serve."""
        raise NotImplementedError

    @property
    def languages(self) -> list[str]:
        """BCP-47 lowercased tags this provider can produce/consume.

        Used by the AddonsPanel filter row to surface providers matching a
        user-selected language. Empty list means "no language metadata
        declared" — the provider still appears under the "Any" filter.

        Default returns []; built-ins override with a hardcoded list,
        add-on providers read it from the manifest in `AddonProvider.__init__`.
        """
        return []

    @property
    def config_schema(self) -> dict | None:
        """Optional declarative schema describing per-instance settings (voice
        defaults, API keys, model selection). When present, the AddonsDialog
        and per-speaker panels render UI from it. `None` means no settings."""
        return None

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Lazy initialisation. For in-process providers this is a no-op; for
        subprocess add-ons it spawns the child and waits for the `hello` ack.
        Calling twice is safe."""

    def shutdown(self) -> None:
        """Tear down resources. Called on app quit."""

    # ---- introspection --------------------------------------------------
    def is_available(self) -> bool:
        """Whether this provider is ready to serve requests right now.

        Default `True`. Providers that need configuration before they can run
        (an API key, a reachable service) override this to reflect that state,
        so UI can offer only the engines that would actually work. It is a
        cheap, synchronous readiness check — no network calls."""
        return True

    def health(self) -> dict:
        """Return a status dict for diagnostics: `{state, last_error?, pid?}`.
        Default is `{'state': 'idle'}`."""
        return {'state': 'idle'}


class TTSProvider(Provider):
    """Text-to-speech provider.

    Signals
    -------
    speech_ready(uid: str, subtitle: dict, path: str)
        Emitted when one synthesis job finishes successfully. `path` points
        to a WAV file under `PATH_SUBTITLD_USER_CACHE/dubbing/`.
    speech_error(uid: str, subtitle: dict, message: str)
        Emitted on a per-job failure. The provider stays usable — only this
        single subtitle's dub failed.
    voices_updated()
        Emitted when `list_voices()` finishes populating its cache. UI panels
        listen to refresh their voice combobox.
    """

    speech_ready = Signal(str, dict, str)
    speech_error = Signal(str, dict, str)
    voices_updated = Signal()

    @property
    def tasks(self) -> list[str]:
        return [TASK_TTS_SYNTHESIZE]

    # ---- voice catalog --------------------------------------------------
    def list_voices(self) -> list[dict]:
        """Return the currently-known voice list. May be empty until the
        provider has finished its initial fetch (signal `voices_updated`)."""
        return []

    def refresh_voices(self) -> None:
        """Trigger a (possibly async) refresh of `list_voices()`. Default is
        a no-op; cloud providers like Edge TTS override to pull the catalog
        on-demand."""

    # ---- synthesis ------------------------------------------------------
    def generate_speeches(self, text_list: list[dict]) -> None:
        """Kick off TTS for a batch of subtitles. Each item:

            {
                'uid': str,         # caller-allocated, echoed in speech_ready
                'text': str,
                'speaker': str,     # key into session.SPEAKERS for fallbacks
                'start': float, 'end': float,
                'voice': str,       # provider-specific voice id
                'rate': int,        # provider-specific (often -100..+100 %)
                'pitch': int,
                # ... provider-specific extras (e.g. voice_ref_audio for XTTS)
            }

        Implementations MUST emit either `speech_ready` or `speech_error` per
        input item. Order is not guaranteed."""
        raise NotImplementedError

    def stretch(self, subtitle: dict, ratio: float) -> bool:
        """Re-render a subtitle's dub at a different speech rate to fit a new
        visual width. Returns True if a job was actually scheduled (False if
        the ratio is a no-op or the provider doesn't support time-stretching).

        Default implementation is a no-op so providers that lack rate control
        (e.g. naïve TTS engines) just decline silently."""
        return False


class ASRProvider(Provider):
    """Automatic-speech-recognition provider.

    Signals
    -------
    transcript_started()
        Emitted when transcription begins (UI shows a spinner).
    partial(segment: dict)
        Emitted per-segment as soon as one is finalized. Useful for streaming
        the timeline as transcription progresses on long files. Shape:
        `{'start': float, 'end': float, 'text': str}`.
    transcript_finished(segments: list)
        Emitted exactly once after the last `partial`. `segments` is the full
        ordered list (caller can ignore individual `partial`s and just use
        this).
    error(message: str)
        Emitted on terminal failure. No `transcript_finished` follows.
    progress(value: float, message: str)
        0..1 progress; multiple emits permitted. `message` is freeform.
    """

    transcript_started = Signal()
    partial = Signal(dict)
    transcript_finished = Signal(list)
    error = Signal(str)
    progress = Signal(float, str)

    # ---- live streaming (asr.stream) -----------------------------------
    # A continuous session the host feeds raw audio to, receiving segments as
    # they are recognised. Distinct from the batch `transcribe` path above.
    stream_segment = Signal(dict, bool)   # (segment, is_final)
    stream_finished = Signal(list)        # committed segments, on stop
    stream_error = Signal(str)

    @property
    def tasks(self) -> list[str]:
        return [TASK_ASR_TRANSCRIBE]

    def transcribe(self, audio_path: str, language: str, options: dict | None = None) -> None:
        """Start transcription. `audio_path` is host-allocated 16kHz mono WAV
        (host pre-converts before calling). `options` is provider-specific
        (e.g. `{'model': 'small', 'beam_size': 5}` for whisper)."""
        raise NotImplementedError

    def cancel(self) -> None:
        """Best-effort cancellation. Implementations should emit `error` with
        a `cancelled` message if a job was actually aborted."""

    # ---- streaming API (default: unsupported) --------------------------
    def supports_streaming(self) -> bool:
        """True if this provider can serve a live `asr.stream` session. Batch
        engines (whisper.cpp, cloud) return False and the host falls back to
        cutting per-phrase clips through `transcribe`."""
        return False

    def stream_start(self, language: str, options: dict | None = None) -> None:
        """Open a live session. Segments arrive via `stream_segment`; the final
        committed list via `stream_finished`; failures via `stream_error`."""
        raise NotImplementedError

    def stream_feed(self, pcm_bytes: bytes) -> None:
        """Feed a chunk of 16 kHz mono int16 PCM into the open session."""

    def stream_stop(self) -> None:
        """End the session; flush and emit `stream_finished`."""


class AudioSeparatorProvider(Provider):
    """Audio source-separation provider — splits a media file into a
    vocals stem and a background (instrumental) stem.

    Used by the player's music/voice slider, the export-vocals dialog,
    and the clone-ref reference picker (which prefers the isolated
    vocals track when available so XTTS/F5/Qwen3 clone voices aren't
    contaminated by music).

    Two implementations ship by default:
      - `ffmpeg` (built-in) — fast mid/side stereo trick. No model, runs
        on any media in seconds; quality is mediocre on songs with
        center-panned instruments.
      - subprocess add-on `audio-separator` — wraps
        nomadkaraoke/python-audio-separator (UVR, MDX, Demucs models).
        High quality but heavy: model download on first use, GPU-friendly.

    Signals
    -------
    separation_ready(input_path: str, vocals_path: str, background_path: str)
        Emitted exactly once per successful job. Both output paths point
        at FLAC/WAV files the host can map for playback or export.
    separation_error(input_path: str, message: str)
        Emitted on terminal failure. No `separation_ready` follows.
    progress(value: float, message: str)
        0..1 progress; multiple emits permitted.
    """

    separation_ready = Signal(str, str, str)
    separation_error = Signal(str, str)
    progress = Signal(float, str)

    @property
    def tasks(self) -> list[str]:
        return [TASK_AUDIO_SEPARATE]

    def separate(self, input_path: str, output_dir: str,
                 options: dict | None = None) -> None:
        """Kick off separation of `input_path`. Output files land under
        `output_dir`; the provider is free to choose the exact filenames
        but MUST report them via `separation_ready`. `options` is
        provider-specific (e.g. `{'model': 'UVR-MDX-NET-Inst_HQ_3'}`)."""
        raise NotImplementedError

    def cancel(self) -> None:
        """Best-effort cancellation. Default is a no-op."""


class TranslationProvider(Provider):
    """Translation provider stub for v1. Declared now so v0 add-on manifests
    can already advertise `translate.text` capability without breaking the
    discovery code."""

    translation_ready = Signal(str, str)  # (request_id, translated_text)
    error = Signal(str, str)              # (request_id, message)

    @property
    def tasks(self) -> list[str]:
        return [TASK_TRANSLATE]

    def translate(self, request_id: str, text: str, source: str, target: str,
                  options: dict | None = None) -> None:
        raise NotImplementedError


class VideoProvider(Provider):
    """Real-time video-frame manipulation provider — the first member of the
    *video manipulation* plugin family (e.g. the lip-sync mouth crop).

    Unlike the audio/text providers, these run **per frame** while the video
    plays, so the contract is a single synchronous ``process_frame`` the host
    calls on a dedicated worker thread. There is deliberately no subprocess
    IPC path here (per-frame round-trips would be far too slow) — video
    plugins are in-process. The host feeds frames and displays whatever image
    the plugin returns in its output panel, so a plugin is free to crop, zoom,
    annotate, or otherwise transform the frame.

    Implementations must be safe to construct on the main thread (registration)
    but may lazily build heavy/thread-affine resources (ML models) on first
    ``process_frame`` call, which always happens on the host's worker thread.
    """

    @property
    def tasks(self) -> list[str]:
        return [TASK_VIDEO_MANIPULATE]

    def process_frame(self, frame, context: dict) -> dict | None:
        """Transform one video frame.

        Parameters
        ----------
        frame : numpy.ndarray
            The current video frame as an ``(H, W, 3)`` uint8 RGB array.
        context : dict
            Playback context the host assembles on the main thread::

                {
                  'playhead': float,            # seconds
                  'active_speaker': str | None, # speaker at the playhead
                  'reference_rgb': ndarray|None,# that speaker's stored face
                  'config': dict,               # this provider's settings
                }

        Returns
        -------
        dict | None
            ``{'image': (h, w, 3) uint8 RGB ndarray, 'label': str,
            'found': bool}`` — the panel shows ``image`` and, optionally,
            ``label``. Return ``None`` (or ``found=False`` with no image) to
            leave the panel showing its previous output.
        """
        raise NotImplementedError


class VideoLipsyncProvider(Provider):
    """Generative lip-sync: given a video and a (dubbed) audio track, produce a
    new video whose mouth movements match the audio ("visual dubbing").

    A batch/offline task — unlike the real-time ``VideoProvider``, the add-on
    reads the whole clip, runs its model, and writes the result to
    ``output_path`` (mirrors ``audio.separate``, which also produces a file).

    Signals
    -------
    lipsync_ready(request_id: str, output_path: str)
        Emitted when the synced video is written. ``output_path`` exists and is
        non-empty by the time this fires.
    error(request_id: str, message: str)
        Terminal failure for that request.
    progress(request_id: str, value: float, message: str)
        0..1 progress with a free-form message (model download, per-frame, …).
    """

    lipsync_ready = Signal(str, str)
    error = Signal(str, str)
    progress = Signal(str, float, str)

    @property
    def tasks(self) -> list[str]:
        return [TASK_VIDEO_LIPSYNC]

    def lipsync(self, request_id: str, video_path: str, audio_path: str,
                output_path: str, options: dict | None = None) -> None:
        """Kick off lip-sync of ``video_path`` against ``audio_path``, writing
        the synced video to ``output_path``. ``options`` is provider-specific
        (from the manifest ``config_schema``) and may carry a ``face_box`` the
        host detected, quality/model knobs, etc. Report progress and finish
        with ``lipsync_ready`` or ``error``."""
        raise NotImplementedError

    def cancel(self, request_id: str | None = None) -> None:
        """Best-effort cancellation. Default is a no-op."""
