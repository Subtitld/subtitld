"""library_path: a native library found on LD_LIBRARY_PATH when ldconfig's
cache, gcc and ld know nothing of it, as in a strict snap on a system
without PortAudio of its own.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ctypes.util
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import library_path

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


folder = Path(tempfile.mkdtemp())
for name in ('libportaudio.so', 'libportaudio.so.2', 'libportaudio.so.2.0.0', 'libportaudiocpp.so.0'):
    (folder / name).write_bytes(b'')
os.environ['LD_LIBRARY_PATH'] = f'/nonexistent:{folder}'

# Nothing found the usual ways.
ctypes.util._findSoname_ldconfig = ctypes.util._findLib_gcc = ctypes.util._findLib_ld = lambda name: None
check('without it: not found', ctypes.util.find_library('portaudio'), None)

library_path.install()
library_path.install()                  # twice: still one wrapper
check('the soname, from LD_LIBRARY_PATH', ctypes.util.find_library('portaudio'), str(folder / 'libportaudio.so.2'))
check('another name: not found', ctypes.util.find_library('nothing'), None)
check('installed once', ctypes.util.find_library.__closure__[0].cell_contents._subtitld
      if hasattr(ctypes.util.find_library.__closure__[0].cell_contents, '_subtitld') else False, False)

ctypes.util._findSoname_ldconfig = lambda name: 'libportaudio.so.2' if name == 'portaudio' else None
check("find_library's own answer first", ctypes.util.find_library('portaudio'), 'libportaudio.so.2')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
