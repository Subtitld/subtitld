"""Face detection on video frames, through whichever MediaPipe API is installed.

MediaPipe 0.10.30 dropped the legacy ``mp.solutions`` API; later releases only
have the Tasks API, which loads its model from a file. That file ships in
``subtitld/models``: BlazeFace full-range sparse, byte for byte the model
0.10.21 kept inside its wheel and ran for ``FaceDetection(model_selection=1)``.
The Tasks API can run it from 0.10.33 on, and the legacy API runs its own copy,
so both find the same faces.
"""
import logging
import pathlib

import numpy as np
try:
    import mediapipe as mp
except Exception:  # optional: unavailable on some platforms (e.g. Haiku)
    mp = None

log = logging.getLogger(__name__)

MODEL_PATH = pathlib.Path(__file__).parent.parent / 'models' / 'blaze_face_full_range_sparse.tflite'
MIN_CONFIDENCE = 0.5

# Why detection cannot run here, once a first attempt has found out.
_unavailable = None


class Unavailable(Exception):
    """Face detection cannot run here; the message says why."""


class _TasksDetector:
    def __init__(self):
        vision = mp.tasks.vision
        try:
            model = MODEL_PATH.read_bytes()
        except OSError as e:
            raise Unavailable(f'its model is missing ({e})')
        options = vision.FaceDetectorOptions(
            # The bytes rather than the path: the file is read here, so a
            # non-ASCII install path never reaches MediaPipe's C++ loader.
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model),
            running_mode=vision.RunningMode.IMAGE,
            min_detection_confidence=MIN_CONFIDENCE)
        self._detector = vision.FaceDetector.create_from_options(options)
        # Up to 0.10.32 the Tasks graph only fits the short-range model, and
        # fails on its first frame with this one. Find out now.
        try:
            self.detect(np.zeros((128, 128, 3), np.uint8))
        except Exception:
            try:
                self.close()  # fails too, once the graph has
            except Exception:
                pass
            raise Unavailable(f'MediaPipe {mp.__version__} cannot run its model; 0.10.33 or newer is needed')

    def detect(self, rgb):
        result = self._detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        return [(d.categories[0].score, d.bounding_box.origin_x, d.bounding_box.origin_y,
                 d.bounding_box.width, d.bounding_box.height) for d in result.detections]

    def close(self):
        self._detector.close()


class _LegacyDetector:
    def __init__(self):
        self._detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=MIN_CONFIDENCE)

    def detect(self, rgb):
        h, w = rgb.shape[:2]
        found = []
        for det in self._detector.process(rgb).detections or []:
            box = det.location_data.relative_bounding_box
            found.append((det.score[0], int(box.xmin * w), int(box.ymin * h),
                          int(box.width * w), int(box.height * h)))
        return found

    def close(self):
        self._detector.close()


class FaceDetector:
    """Finds faces in RGB frames. Raises Unavailable when it cannot run."""

    def __init__(self):
        global _unavailable
        if _unavailable is None:
            try:
                self._backend = self._open_backend()
                return
            except Unavailable as e:
                _unavailable = str(e)
            except Exception as e:
                _unavailable = f'MediaPipe failed to start ({e})'
            log.warning('Face detection is unavailable: %s', _unavailable)
        raise Unavailable(_unavailable)

    @staticmethod
    def _open_backend():
        # MediaPipe 0.10.21 and older: the legacy API, with its own copy of the model.
        if mp is not None and hasattr(mp, 'solutions'):
            return _LegacyDetector()
        if mp is not None and hasattr(mp, 'tasks'):
            return _TasksDetector()
        # Absent, or a test stub of mediapipe, which has neither API.
        raise Unavailable('MediaPipe is not installed on this platform')

    def detect(self, rgb):
        """Faces in an RGB uint8 frame, most confident first, as (x1, y1, x2, y2)
        pixel corners clamped to the frame."""
        h, w = rgb.shape[:2]
        found = sorted(self._backend.detect(np.ascontiguousarray(rgb)), key=lambda f: f[0], reverse=True)
        return [(max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)) for _score, x, y, bw, bh in found]

    def close(self):
        self._backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
