"""Mouth detection + cropping for the lip-sync helper.

Detects faces in a video frame (MediaPipe face detection — the same model
the Speakers panel already uses), locates each face's mouth, and produces a
crop rectangle centred on it. When several faces are present the caller can
match a detection to the *active speaker* using a cheap appearance descriptor
built from the reference face the Speakers panel captured per speaker.

Kept UI-agnostic: the QImage<->ndarray helpers are the only Qt touch-points,
guarded so the detection core can be unit-tested without a display.
"""

import math

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


def mouth_crop_geometry(face_box, mouth, zoom=1.0):
    """Unclamped float crop geometry ``(cx, cy, cw)`` for a detection.

    Split out of :func:`mouth_crop_rect` so temporal smoothing can run in
    float, continuous space and quantise only once at the end — filtering
    already-rounded values would feed the filter its own quantisation noise.
    Height is not returned because it is always ``cw * _CROP_ASPECT``: deriving
    it keeps the aspect fixed, so the panel's KeepAspectRatio letterbox can
    never wobble.
    """
    fw = max(1.0, face_box[2])
    zoom = max(0.2, float(zoom))
    cw = fw * _CROP_W_FACES / zoom
    mx, my = mouth
    return (float(mx), float(my), float(cw))


def crop_rect_from_geometry(cx, cy, cw, frame_w, frame_h):
    """Clamp float ``(cx, cy, cw)`` into the frame and round to an int rect.

    The rect must be integral because :func:`crop` is a numpy slice (which is
    also what keeps it a zero-copy view).
    """
    cw = max(2.0, min(float(frame_w), float(cw)))
    ch = max(2.0, min(float(frame_h), cw * _CROP_ASPECT))
    x = max(0.0, min(frame_w - cw, cx - cw / 2.0))
    y = max(0.0, min(frame_h - ch, cy - ch / 2.0))
    return (int(round(x)), int(round(y)), int(round(cw)), int(round(ch)))


def mouth_crop_rect(face_box, mouth, frame_w, frame_h, zoom=1.0):
    """Crop rect ``(x, y, w, h)`` (ints) centred on ``mouth``, sized from the
    face width and clamped inside the frame. Higher ``zoom`` → tighter crop."""
    cx, cy, cw = mouth_crop_geometry(face_box, mouth, zoom)
    return crop_rect_from_geometry(cx, cy, cw, frame_w, frame_h)


# --- temporal stabilisation -------------------------------------------------
# Defaults. `tau` is a real time constant in seconds, NOT a per-frame blend
# factor: the gain is recomputed from the actual dt each frame, so smoothing
# strength no longer depends on the frame cadence.
_TAU_CENTRE = 0.12          # head translation is real and fast — stay close
_TAU_SIZE = 0.45            # scale breathing is the worse artefact, damp hard
_DEADBAND_CENTRE = 0.010    # of crop width, floored below
_DEADBAND_CENTRE_FLOOR = 1.5
_DEADBAND_SIZE = 0.020      # relative; mediapipe box width jitters ~1-3%
_SNAP_CENTRE_FRAC = 0.55    # of crop width, per frame
_SNAP_SCALE_LOG = 0.5       # ~0.61x .. 1.65x
_CONFIRM_FRAMES = 2         # a single-frame outlier must not snap
_STALE_GAP = 0.5            # seconds; longer than this and the state is junk
_INT_HYSTERESIS = 0.75      # px, stops n <-> n+1 flicker at half-pixels


def _soft_threshold(error, deadband):
    """Shrink ``error`` toward zero by ``deadband``.

    Returns exactly 0 while |error| <= deadband, then grows *continuously*.
    That continuity is the point: a hard deadband (snap to the measurement
    once the error clears the threshold) ratchets — error accumulates, the
    filter releases the whole of it, then freezes again — which trades the
    tremble for a periodic twitch. Here the corrective velocity is zero at
    the boundary and rises smoothly, so there is no release transient.
    """
    if error > deadband:
        return error - deadband
    if error < -deadband:
        return error + deadband
    return 0.0


class CropStabiliser:
    """Temporal smoothing for the mouth crop, in float crop space.

    Deliberately Qt-free and numpy-free: three float channels (centre x,
    centre y, crop width), a handful of arithmetic ops per frame. It runs on
    the same worker thread as the detector, where a heavy per-frame addition
    would risk the GIL/audio-dropout failure mode this project has hit before.

    Height is never a channel — it is derived from the width — so the crop's
    aspect is constant by construction.
    """

    def __init__(self, stabilise=1.0):
        self.set_strength(stabilise)
        self.reset()

    def set_strength(self, stabilise):
        """0 disables smoothing entirely; 1 is the shipped default."""
        try:
            k = float(stabilise)
        except (TypeError, ValueError):
            k = 1.0
        # Explicit bounds rather than `or` — `0.0` is a meaningful value and a
        # truthiness fallback would silently turn "off" back into the default.
        self._k = max(0.0, min(2.0, k))

    def reset(self):
        """Forget all history. The next frame is adopted verbatim."""
        self._cx = None
        self._cy = None
        self._cw = None
        self._last_t = None
        self._out = None            # last emitted int rect, for hysteresis
        self._confirm = 0
        self._epoch = None
        self._shape = None
        self._zoom = None
        self._speaker = None

    # -- helpers ------------------------------------------------------------
    def _discontinuous(self, epoch, shape, zoom, speaker, dt):
        """True when the world changed, so gliding would be wrong."""
        if self._cx is None:
            return True
        if epoch != self._epoch:
            return True
        if shape != self._shape:
            return True
        if self._zoom is not None and zoom != self._zoom:
            return True
        # A different named speaker means the crop is moving to another face.
        if speaker and self._speaker and speaker != self._speaker:
            return True
        if dt is not None and dt > _STALE_GAP:
            return True
        return False

    def _quantise(self, value, previous):
        if previous is not None and abs(value - previous) < _INT_HYSTERESIS:
            return previous
        return value

    # -- main ---------------------------------------------------------------
    def update(self, cx, cy, cw, frame_w, frame_h, now,
               epoch=None, zoom=None, speaker=None):
        """Feed one measurement, get back the stabilised int crop rect."""
        dt = None
        if self._last_t is not None:
            dt = max(1e-4, float(now) - float(self._last_t))
        self._last_t = now
        shape = (int(frame_h), int(frame_w))

        snap = self._discontinuous(epoch, shape, zoom, speaker, dt)

        if not snap and self._k > 0.0:
            # Measurement-derived snap needs confirmation: one outlier frame
            # that snapped would produce two jumps instead of none.
            far = math.hypot(cx - self._cx, cy - self._cy) > _SNAP_CENTRE_FRAC * self._cw
            rescaled = abs(math.log(max(1e-6, cw) / max(1e-6, self._cw))) > _SNAP_SCALE_LOG
            if far or rescaled:
                self._confirm += 1
                if self._confirm >= _CONFIRM_FRAMES:
                    snap = True
                else:
                    # Awaiting confirmation: HOLD rather than track. Without
                    # this the one-pole still chases a large outlier a good
                    # fraction of the way, so a single bad detection produces
                    # a visible lurch even though it never snaps.
                    self._epoch = epoch
                    self._shape = shape
                    self._zoom = zoom
                    rect = crop_rect_from_geometry(
                        self._cx, self._cy, self._cw, frame_w, frame_h)
                    if self._out is not None:
                        rect = tuple(self._quantise(v, pv)
                                     for v, pv in zip(rect, self._out))
                    self._out = rect
                    return rect
            else:
                self._confirm = 0

        if snap or self._k <= 0.0 or dt is None:
            self._cx, self._cy, self._cw = float(cx), float(cy), float(cw)
            self._confirm = 0
            self._out = None
        else:
            # Exact discretisation of d(out)/dt = (m - out) / tau, so two
            # half-steps equal one whole step and the smoothing no longer
            # varies with the frame cadence.
            tau_c = _TAU_CENTRE * self._k
            tau_w = _TAU_SIZE * self._k
            a_c = 1.0 - math.exp(-dt / tau_c) if tau_c > 0 else 1.0
            a_w = 1.0 - math.exp(-dt / tau_w) if tau_w > 0 else 1.0
            db_c = max(_DEADBAND_CENTRE_FLOOR, _DEADBAND_CENTRE * self._cw)
            db_w = max(1.0, _DEADBAND_SIZE * self._cw)
            self._cx += a_c * _soft_threshold(cx - self._cx, db_c)
            self._cy += a_c * _soft_threshold(cy - self._cy, db_c)
            self._cw += a_w * _soft_threshold(cw - self._cw, db_w)

        self._epoch = epoch
        self._shape = shape
        self._zoom = zoom
        if speaker:
            self._speaker = speaker

        rect = crop_rect_from_geometry(self._cx, self._cy, self._cw,
                                       frame_w, frame_h)
        if self._out is not None:
            rect = tuple(self._quantise(v, p) for v, p in zip(rect, self._out))
        self._out = rect
        return rect


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
