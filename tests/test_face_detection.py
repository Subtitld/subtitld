"""Speaker face detection: the bundled model ships in every build, and finds
the two faces in the Speakers screenshot's video frame.

Standalone, like the other suites here; puts this checkout's src/ on the path.
The detection part needs MediaPipe and is skipped without it.
"""
import hashlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import cv2
import numpy as np
from subtitld.modules import face_detection as fd

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


print('the model, and every build carrying it')
check('the model is the BlazeFace full-range sparse file',
      hashlib.sha256(fd.MODEL_PATH.read_bytes()).hexdigest() if fd.MODEL_PATH.is_file() else None,
      '2c3728e6da56f21e21a320433396fb06d40d9088f2247c05e5635a688d45dfe1')
check('pip package data', '"models/*"' in (ROOT / 'pyproject.toml').read_text(), True)
check('sdist', 'recursive-include src/subtitld/models *' in (ROOT / 'MANIFEST.in').read_text(), True)
for spec in sorted(ROOT.glob('subtitld*.spec')):
    check(f'PyInstaller, {spec.name}', "('src/subtitld/models', 'subtitld/models')" in spec.read_text(), True)

print('without MediaPipe')
real_mp = fd.mp
for label, stub in (('absent', None), ('a test stub', types.ModuleType('mediapipe'))):
    fd.mp, fd._unavailable = stub, None
    try:
        fd.FaceDetector()
        check(f'{label}: unavailable', 'opened', 'Unavailable')
    except fd.Unavailable as e:
        check(f'{label}: unavailable, and says why', str(e), 'MediaPipe is not installed on this platform')
fd.mp, fd._unavailable = real_mp, None

if real_mp is None:
    print('MediaPipe is not installed: detection not tested')
else:
    print(f'with MediaPipe {real_mp.__version__}')
    shot = cv2.imread(str(ROOT / 'flatpak/screenshots/speakers.png'))
    frame = np.ascontiguousarray(cv2.cvtColor(shot[108:706, 483:1916], cv2.COLOR_BGR2RGB))
    with fd.FaceDetector() as detector:
        faces = detector.detect(frame)
        check('two faces in the video frame', len(faces), 2)
        # Thom on the left, Celia on the right, both in the upper half.
        centres = sorted(((x1 + x2) // 2, (y1 + y2) // 2) for x1, y1, x2, y2 in faces)
        check('one each side, near the top', [(x < 716, y < 300) for x, y in centres], [(True, True), (False, True)])
        check('none in a blank frame', detector.detect(np.zeros_like(frame)), [])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
