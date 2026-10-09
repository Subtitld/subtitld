# PyInstaller runtime hook (Linux).
#
# The sounddevice wheel carries PortAudio for Windows and macOS only; on Linux
# it asks ctypes.util.find_library, which only searches the system. A system
# without libportaudio2 then fails at startup ("PortAudio library not found")
# even though the build bundles a copy. Fall back to that copy, while still
# preferring the system's, which is built for the system's audio setup.
import ctypes.util
import os
import sys

_bundled = os.path.join(sys._MEIPASS, 'libportaudio.so.2')

if os.path.exists(_bundled):
    _find_library = ctypes.util.find_library

    def find_library(name):
        found = _find_library(name)
        if found is None and name == 'portaudio':
            return _bundled
        return found

    ctypes.util.find_library = find_library
