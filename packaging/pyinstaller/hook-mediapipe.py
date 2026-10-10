# PyInstaller hook for MediaPipe.
#
# From 0.10.30 on, MediaPipe's Python side is a ctypes binding over
# mediapipe/tasks/c/libmediapipe.so (.dll, .dylib), opened by a path it builds
# at run time. PyInstaller's analysis cannot see that, so without this hook a
# build carries only the Python side, and the Speakers face detection reports
# itself unavailable.
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs('mediapipe')
