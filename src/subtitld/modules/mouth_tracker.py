"""Mouth detection + cropping for the lip-sync helper.

Detects faces in a video frame (MediaPipe face detection — the same model
the Speakers panel already uses), locates each face's mouth, and produces a
crop rectangle centred on it. When several faces are present the caller can
match a detection to the *active speaker* using a cheap appearance descriptor
built from the reference face the Speakers panel captured per speaker.

Kept UI-agnostic: the QImage<->ndarray helpers are the only Qt touch-points,
guarded so the detection core can be unit-tested without a display.
"""

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 is a hard dep of the app
    cv2 = None

try:
    import mediapipe as _mp
except Exception:  # pragma: no cover
    _mp = None

# MediaPipe face-detection keypoint order: 0 right-eye, 1 left-eye, 2 nose,
# 3 mouth-center, 4 right-ear, 5 left-ear.
_MOUTH_KEYPOINT = 3

# Crop geometry, expressed relative to the detected face width so the mouth
# fills the view at a consistent scale regardless of how big the face is.
_CROP_W_FACES = 0.95          # crop width  = 0.95 * face width (at zoom 1.0)
_CROP_ASPECT = 0.62           # crop height = 0.62 * crop width (mouth is wide)


def available():
    """True when the detector's dependencies are importable and real (a test
    stub of ``mediapipe`` has no ``solutions`` attribute)."""
    return _mp is not None and hasattr(_mp, 'solutions') and cv2 is not None


class MouthTracker:
    """Wraps a MediaPipe FaceDetection graph. One instance per worker thread
    (the graph is not thread-safe). Call ``close()`` when done."""

    def __init__(self, min_confidence=0.5):
        self._detector = None
        if _mp is not None:
            # model_selection=0 is the short-range model — faster and a better
            # fit for the framed faces typical of dialogue shots.
            self._detector = _mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=min_confidence,
            )

    def close(self):
        det = self._detector
        self._detector = None
        if det is not None:
            try:
                det.close()
            except Exception:
                pass

    def detect(self, rgb):
        """Detect faces in an ``(H, W, 3)`` uint8 RGB array.

        Returns a list of dicts sorted largest-face-first::

            {'face_box': (x, y, w, h), 'mouth': (mx, my), 'score': float}

        all in pixel coordinates of ``rgb``.
        """
        if self._detector is None or rgb is None or rgb.size == 0:
            return []
        h, w = rgb.shape[:2]
        try:
            results = self._detector.process(rgb)
        except Exception:
            return []
        out = []
        for det in (results.detections or []):
            box = det.location_data.relative_bounding_box
            fw = max(0.0, box.width * w)
            fh = max(0.0, box.height * h)
            fx = box.xmin * w
            fy = box.ymin * h
            kps = det.location_data.relative_keypoints
            if kps and len(kps) > _MOUTH_KEYPOINT:
                mk = kps[_MOUTH_KEYPOINT]
                mouth = (mk.x * w, mk.y * h)
            else:
                # Fall back to the lower-centre of the face box.
                mouth = (fx + fw * 0.5, fy + fh * 0.78)
            score = float(det.score[0]) if det.score else 0.0
            out.append({'face_box': (fx, fy, fw, fh), 'mouth': mouth, 'score': score})
        out.sort(key=lambda d: d['face_box'][2] * d['face_box'][3], reverse=True)
        return out


def mouth_crop_rect(face_box, mouth, frame_w, frame_h, zoom=1.0):
    """Crop rect ``(x, y, w, h)`` (ints) centred on ``mouth``, sized from the
    face width and clamped inside the frame. Higher ``zoom`` → tighter crop."""
    fw = max(1.0, face_box[2])
    zoom = max(0.2, float(zoom))
    cw = min(float(frame_w), fw * _CROP_W_FACES / zoom)
    ch = min(float(frame_h), cw * _CROP_ASPECT)
    mx, my = mouth
    x = mx - cw / 2.0
    y = my - ch / 2.0
    x = max(0.0, min(frame_w - cw, x))
    y = max(0.0, min(frame_h - ch, y))
    return (int(round(x)), int(round(y)), int(round(cw)), int(round(ch)))


def crop(rgb, rect):
    """Slice ``rect`` out of ``rgb`` with bounds safety."""
    x, y, w, h = rect
    x2 = min(rgb.shape[1], x + w)
    y2 = min(rgb.shape[0], y + h)
    x = max(0, x)
    y = max(0, y)
    if x2 <= x or y2 <= y:
        return None
    return rgb[y:y2, x:x2]


def face_descriptor(rgb_crop):
    """Cheap appearance descriptor for speaker matching: a mean-removed,
    L2-normalised 32x32 grayscale patch. Comparing two descriptors with a dot
    product yields normalised cross-correlation in ``[-1, 1]``. Not a true
    face embedding — good enough to disambiguate a handful of on-screen faces
    against a stored reference, and cheap enough to run every frame."""
    if cv2 is None or rgb_crop is None or rgb_crop.size == 0:
        return None
    try:
        gray = cv2.cvtColor(rgb_crop, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, (32, 32)).astype(np.float32)
    except Exception:
        return None
    gray -= float(gray.mean())
    norm = float(np.linalg.norm(gray))
    if norm < 1e-6:
        return None
    return gray / norm


def descriptor_similarity(a, b):
    """Normalised cross-correlation of two descriptors, in ``[-1, 1]``."""
    if a is None or b is None:
        return -1.0
    return float(np.dot(a.ravel(), b.ravel()))


def pick_face(detections, rgb, reference_descriptor=None, match_threshold=0.35):
    """Choose which detection to track.

    With a ``reference_descriptor`` (the active speaker's stored face), score
    every detection's face crop against it and return the best match above
    ``match_threshold``. Otherwise — or if nothing matches well enough — fall
    back to the largest face (``detections`` is already largest-first).
    Returns ``(detection, matched: bool)`` or ``(None, False)``.
    """
    if not detections:
        return None, False
    if reference_descriptor is not None:
        best = None
        best_score = match_threshold
        for det in detections:
            fx, fy, fw, fh = det['face_box']
            face_rect = (int(fx), int(fy), int(fw), int(fh))
            desc = face_descriptor(crop(rgb, face_rect))
            score = descriptor_similarity(desc, reference_descriptor)
            if score >= best_score:
                best_score = score
                best = det
        if best is not None:
            return best, True
    return detections[0], False


# --- Qt interop (optional; only used by the UI layer) --------------------

def qimage_to_rgb(qimage):
    """QImage -> contiguous ``(H, W, 3)`` uint8 RGB ndarray (a private copy)."""
    from PySide6.QtGui import QImage
    if qimage is None or qimage.isNull():
        return None
    img = qimage.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    buf = np.frombuffer(bytes(ptr), dtype=np.uint8, count=bpl * h)
    arr = buf.reshape((h, bpl))[:, : w * 3].reshape((h, w, 3))
    return np.ascontiguousarray(arr)


def rgb_to_qimage(rgb):
    """Contiguous ``(H, W, 3)`` uint8 RGB ndarray -> a standalone QImage."""
    from PySide6.QtGui import QImage
    if rgb is None or rgb.size == 0:
        return None
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
