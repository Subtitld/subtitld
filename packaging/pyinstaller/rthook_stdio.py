# PyInstaller runtime hook (windowed builds, Windows).
#
# A windowed app on Windows has no console, so sys.stdout and sys.stderr are
# None, and the first write to either raises AttributeError. Code all over
# the app writes diagnostics to stderr (the audio engine, for one, when there
# is no output device), so give both a sink that takes them quietly.
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
