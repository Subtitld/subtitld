"""Which graphics cards this computer has that add-ons can run on.

Add-ons say in their manifest what they run on (see `addons.compute`): some
need a graphics card, some are faster on one. The add-ons panel asks here
whether this computer has one, to tell the user what an add-on will do on it.

Two accelerators are checked, the ones add-ons use:

- ``cuda``: an NVIDIA card, asked through the NVIDIA driver's own library
  (``libcuda.so.1``, ``nvcuda.dll``) with ``ctypes``. It is what PyTorch,
  CTranslate2 and onnxruntime use, so a card found here is one they can use,
  and nothing has to be bundled for it: the library comes with the driver.
- ``mps``: Apple's GPU, on Apple Silicon with macOS 12.3 or newer. macOS is
  asked through ``sysctl``, not ``platform.machine()``, which says x86_64 for
  an Intel build running under Rosetta.

Each is ``found``, ``none`` or ``unknown``. A check that cannot tell (the
driver errs, or a sandbox may be hiding it: the Flatpak without its NVIDIA
GL extension, a snap without the driver's libraries) is ``unknown``, never
``none``: saying a computer has no card when it has one is worse than saying
nothing.

`detect()` checks once per run and keeps the answer. It can take a second
(the NVIDIA driver starts up), so callers on the GUI thread run it in a
worker; `cached()` gives the answer without checking.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import platform
import sys
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

ACCELERATOR_CUDA = 'cuda'
ACCELERATOR_MPS = 'mps'
ACCELERATORS = (ACCELERATOR_CUDA, ACCELERATOR_MPS)

FOUND = 'found'
NONE = 'none'
UNKNOWN = 'unknown'

# CUresult codes that mean "no CUDA here", as opposed to "could not tell".
_CUDA_SUCCESS = 0
_CUDA_ERROR_INSUFFICIENT_DRIVER = 35  # a driver too old for any CUDA use
_CUDA_ERROR_NO_DEVICE = 100

_MPS_MIN_MACOS = (12, 3)


@dataclass(frozen=True)
class Device:
    name: str
    memory_bytes: int | None = None


@dataclass
class Hardware:
    """Per accelerator, its state and the devices found."""

    states: dict[str, str] = field(default_factory=dict)
    devices: dict[str, tuple[Device, ...]] = field(default_factory=dict)

    def available(self, accelerator: str) -> bool | None:
        """True when found, False when not there, None when it could not
        be told (or is not one checked here)."""
        state = self.states.get(accelerator)
        if state == FOUND:
            return True
        if state == NONE:
            return False
        return None

    def found_devices(self) -> list[tuple[str, Device]]:
        return [(acc, dev) for acc in ACCELERATORS if self.states.get(acc) == FOUND
                for dev in self.devices.get(acc, ())]


# ---------------------------------------------------------------------------
# CUDA
# ---------------------------------------------------------------------------
def _in_sandbox() -> bool:
    """Flatpak and snap reach the host's NVIDIA driver only through an
    extension or an interface; without it, its library is just not there."""
    return os.path.exists('/.flatpak-info') or bool(os.environ.get('SNAP'))


def _cuda_library_candidates() -> list[str]:
    if sys.platform == 'win32':
        return ['nvcuda.dll']
    if sys.platform == 'darwin':
        return []
    machine = platform.machine() or 'x86_64'
    return (
        ['libcuda.so.1']
        # snapd puts the host driver's libraries here for snaps.
        + ['/var/lib/snapd/lib/gl/libcuda.so.1']
        # Flatpak's org.freedesktop.Platform.GL.nvidia-* extensions.
        + sorted(glob.glob(f'/usr/lib/{machine}-linux-gnu/GL/*/lib/libcuda.so.1'))
    )


def _load_cuda_library():
    """The NVIDIA driver's CUDA library, or None when it is not installed."""
    loader = ctypes.WinDLL if sys.platform == 'win32' else ctypes.CDLL
    for candidate in _cuda_library_candidates():
        try:
            return loader(candidate)
        except OSError:
            continue
    return None


def _probe_cuda(lib) -> tuple[str, tuple[Device, ...]]:
    """Ask the driver library for its devices: (state, devices)."""
    result = lib.cuInit(0)
    if result in (_CUDA_ERROR_NO_DEVICE, _CUDA_ERROR_INSUFFICIENT_DRIVER):
        return NONE, ()
    if result != _CUDA_SUCCESS:
        return UNKNOWN, ()
    count = ctypes.c_int(0)
    if lib.cuDeviceGetCount(ctypes.byref(count)) != _CUDA_SUCCESS:
        return UNKNOWN, ()
    devices = []
    for ordinal in range(count.value):
        device = ctypes.c_int(0)
        if lib.cuDeviceGet(ctypes.byref(device), ordinal) != _CUDA_SUCCESS:
            continue
        name = ctypes.create_string_buffer(256)
        if lib.cuDeviceGetName(name, len(name), device) != _CUDA_SUCCESS:
            name.value = b''
        memory = ctypes.c_size_t(0)
        total_mem = getattr(lib, 'cuDeviceTotalMem_v2', None)
        if total_mem is None or total_mem(ctypes.byref(memory), device) != _CUDA_SUCCESS:
            memory.value = 0
        devices.append(Device(
            name=name.value.decode('utf-8', 'replace').strip() or 'NVIDIA GPU',
            memory_bytes=memory.value or None,
        ))
    if not devices:
        return (NONE if count.value == 0 else UNKNOWN), ()
    return FOUND, tuple(devices)


def _detect_cuda() -> tuple[str, tuple[Device, ...]]:
    if sys.platform == 'darwin':
        return NONE, ()  # no NVIDIA driver for macOS since 10.13
    lib = _load_cuda_library()
    if lib is None:
        return (UNKNOWN if _in_sandbox() else NONE), ()
    return _probe_cuda(lib)


# ---------------------------------------------------------------------------
# Apple GPU (MPS)
# ---------------------------------------------------------------------------
def _sysctl(name: str, kind):
    """A sysctl value through libc, or None when macOS has no such key.
    `kind` is int or str."""
    libc = ctypes.CDLL('/usr/lib/libc.dylib')
    if kind is int:
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            return None
        return value.value
    buffer = ctypes.create_string_buffer(256)
    size = ctypes.c_size_t(len(buffer))
    if libc.sysctlbyname(name.encode(), buffer, ctypes.byref(size), None, 0) != 0:
        return None
    return buffer.value.decode('utf-8', 'replace').strip()


def _macos_version() -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in platform.mac_ver()[0].split('.') if part)
    except ValueError:
        return ()


def _detect_mps() -> tuple[str, tuple[Device, ...]]:
    if sys.platform != 'darwin':
        return NONE, ()
    if _sysctl('hw.optional.arm64', int) != 1:
        return NONE, ()  # an Intel Mac
    version = _macos_version()
    if not version:
        return UNKNOWN, ()
    if version < _MPS_MIN_MACOS:
        return NONE, ()
    name = _sysctl('machdep.cpu.brand_string', str) or 'Apple GPU'
    return FOUND, (Device(name=name),)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
_DETECTORS = {
    ACCELERATOR_CUDA: _detect_cuda,
    ACCELERATOR_MPS: _detect_mps,
}

_lock = threading.Lock()
_cached: Hardware | None = None


def _run_detectors() -> Hardware:
    hardware = Hardware()
    for accelerator, detector in _DETECTORS.items():
        try:
            state, devices = detector()
        except Exception:
            # A driver that crashes its own query is one we cannot vouch for.
            log.warning('hardware: checking for %s failed', accelerator, exc_info=True)
            state, devices = UNKNOWN, ()
        hardware.states[accelerator] = state
        hardware.devices[accelerator] = devices
    log.info('hardware: %s', ', '.join(
        f'{acc} {hardware.states[acc]}'
        + ''.join(f' ({dev.name})' for dev in hardware.devices[acc])
        for acc in ACCELERATORS))
    return hardware


def detect() -> Hardware:
    """This computer's accelerators, checked on the first call."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = _run_detectors()
        return _cached


def cached() -> Hardware | None:
    """The answer `detect()` found, or None before it has run."""
    return _cached
