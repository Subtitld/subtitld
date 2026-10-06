"""Built-in *video manipulation* plugin: mouth crop for lip-sync.

Detects the face(s) in each video frame, locates the mouth, and returns a
zoomed crop centred on it so the user can concentrate on lip-sync while
dubbing. When several faces are on screen it tries to follow the *active
speaker* (the speaker of the subtitle under the playhead) using the reference
face the Speakers panel captured, falling back to the largest face.

This is the first member of the ``video.manipulate`` plugin type. It ships
in-process (the detector runs per frame, so subprocess IPC is not viable);
whether it stays bundled or becomes separately installable is a packaging
decision, not an architectural one — the host only knows it through the
``VideoProvider`` interface.

Provider id: ``mouth_crop``.
"""

from __future__ import annotations

from subtitld.modules.addons.provider import VideoProvider
import time

from subtitld.modules import mouth_tracker


class MouthCropProvider(VideoProvider):
    """Follows a speaker's mouth and returns a zoomed crop of it per frame."""

    @property
    def id(self) -> str:  # noqa: A003
        return 'mouth_crop'

    @property
    def display_name(self) -> str:
        return 'Mouth (lip-sync)'

    @property
    def is_builtin(self) -> bool:
        return True

    def is_available(self) -> bool:
        # Needs MediaPipe + OpenCV; degrades to "unavailable" (rather than
        # crashing) when either can't be imported.
        return mouth_tracker.available()

    @property
    def config_schema(self) -> dict | None:
        return {
            'fields': [
                {
                    'key': 'zoom', 'type': 'number', 'label': 'Zoom',
                    'default': 1.0, 'min': 0.5, 'max': 3.0, 'step': 0.1,
                    'help': 'Higher zooms tighter on the mouth.',
                },
                {
                    'key': 'match_speaker', 'type': 'bool',
                    'label': 'Follow the active speaker', 'default': True,
                    'help': 'When several faces are visible, track the '
                            'speaker of the subtitle under the playhead '
                            '(using their Speakers-panel face); otherwise '
                            'track the largest face.',
                },
            ]
        }

    # ---- lifecycle ------------------------------------------------------
    def __init__(self, parent=None):
        super().__init__(parent)
        # Everything below is touched only on the host's worker thread.
        self._tracker = None
        self._ref_speaker = None
        self._ref_descriptor = None
        # Temporal smoothing state. Lives here, beside _tracker and
        # _ref_descriptor, and is touched ONLY on the worker thread. The host
        # never reaches in to reset it — it just bumps an integer `epoch` in
        # the context, which we compare below. That avoids mutating
        # worker-owned state from the GUI thread while a frame is in flight.
        self._stab = mouth_tracker.CropStabiliser()

    def shutdown(self) -> None:
        tracker = self._tracker
        self._tracker = None
        if tracker is not None:
            tracker.close()

    def _ensure_tracker(self):
        # Lazily built on the worker thread — MediaPipe graphs are thread-
        # affine, and this method only ever runs there.
        if self._tracker is None and mouth_tracker.available():
            self._tracker = mouth_tracker.MouthTracker()
        return self._tracker

    def _reference_descriptor(self, speaker, reference_rgb):
        """Cache the active speaker's appearance descriptor, recomputing only
        when the speaker changes or the reference face first becomes
        available (face extraction is asynchronous)."""
        if speaker != self._ref_speaker or (
                self._ref_descriptor is None and reference_rgb is not None):
            self._ref_speaker = speaker
            self._ref_descriptor = (
                mouth_tracker.face_descriptor(reference_rgb)
                if reference_rgb is not None else None)
        return self._ref_descriptor

    # ---- per-frame work -------------------------------------------------
    def process_frame(self, frame, context: dict) -> dict | None:
        tracker = self._ensure_tracker()
        if tracker is None or frame is None:
            return None

        cfg = context.get('config') or {}
        zoom = float(cfg.get('zoom', 1.0) or 1.0)
        match_speaker = bool(cfg.get('match_speaker', True))

        detections = tracker.detect(frame)
        if not detections:
            # No face: show the whole picture. Returning None here used to make
            # the view re-display its last good crop, which froze on a stale
            # mouth while the video carried on.
            self._stab.reset()
            return {'image': frame, 'label': '', 'found': False}

        ref = None
        if match_speaker:
            ref = self._reference_descriptor(
                context.get('active_speaker'), context.get('reference_rgb'))

        det, matched = mouth_tracker.pick_face(detections, frame, ref)
        if det is None:
            self._stab.reset()
            return {'image': frame, 'label': '', 'found': False}

        h, w = frame.shape[:2]
        # Smooth in float, continuous space and quantise once at the end —
        # filtering already-rounded rects would feed the filter its own
        # quantisation noise.
        cx, cy, cw = mouth_tracker.mouth_crop_geometry(
            det['face_box'], det['mouth'], zoom)
        self._stab.set_strength(cfg.get('stabilise', 1.0))
        rect = self._stab.update(
            cx, cy, cw, w, h,
            now=float(context.get('t') or time.perf_counter()),
            epoch=context.get('epoch'),
            zoom=zoom,
            speaker=context.get('active_speaker') if match_speaker else None,
        )
        crop = mouth_tracker.crop(frame, rect)
        if crop is None:
            return {'image': frame, 'label': '', 'found': False}

        speaker = context.get('active_speaker')
        label = (speaker or '') if matched else ''
        return {'image': crop, 'label': label, 'found': True, 'matched': matched}


_PROVIDER = None


def get_provider() -> MouthCropProvider:
    """Singleton factory used by the app's builtin-registration block."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = MouthCropProvider()
    return _PROVIDER
