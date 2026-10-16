"""Native libraries a package carries beside the app, found by name.

sounddevice (PortAudio) and other ctypes bindings look their library up with
ctypes.util.find_library, which on Linux asks ldconfig's cache, then gcc,
then ld. A bundle that carries its own copy only puts it on LD_LIBRARY_PATH:
the snap's libportaudio is under $SNAP/usr/lib, and the cache a strict snap
reads is the host's /etc/ld.so.cache, which lists PortAudio only when the
host happens to have it too; gcc and ld are not there. So on a system
without PortAudio the snap failed to start: "PortAudio library not found".

install() keeps find_library's answer when it has one, and otherwise looks
for lib<name>.so* in the folders of LD_LIBRARY_PATH, as the dynamic linker
would load it.
"""

import ctypes.util
import glob
import os
import sys


def _in_library_path(name):
    for folder in os.environ.get('LD_LIBRARY_PATH', '').split(os.pathsep):
        if not folder:
            continue
        # The soname (libportaudio.so.2) before the bare development link.
        found = sorted(glob.glob(os.path.join(glob.escape(folder), f'lib{glob.escape(name)}.so.*')),
                       key=len) or glob.glob(os.path.join(glob.escape(folder), f'lib{glob.escape(name)}.so'))
        if found:
            return found[0]
    return None


def install():
    """Give ctypes.util.find_library the LD_LIBRARY_PATH fallback (Linux)."""
    if not sys.platform.startswith('linux') or getattr(ctypes.util.find_library, '_subtitld', False):
        return
    original = ctypes.util.find_library

    def find_library(name):
        return original(name) or _in_library_path(name)

    find_library._subtitld = True
    ctypes.util.find_library = find_library
