"""What add-ons run on: the graphics-card check, the verdict for each kind of
add-on, and what the add-ons panel shows and filters with it.

- The CUDA check reads devices through the NVIDIA driver's library; with a
  stand-in library: a card with its name and memory, no card, a driver too
  old, a driver that errs or crashes (unknown, not none), and no library at
  all (none, but unknown inside the Flatpak or a snap).
- On this machine, the real check runs and answers without failing.
- `compute.read` keeps only the four values, and accelerators lowercased.
- `compute.assess`: CPU and cloud add-ons whatever the hardware; a GPU one
  uses a card it names, falls back to the CPU or is missing when none is
  here, and says only what it asks for when the check could not tell or
  the card here is not one it names.
- The panel: a chip per add-on that says it, none for one that doesn't;
  the catalog's word for an installed add-on whose manifest predates the
  field; the warning chip; "Runs on this computer" hides only the missing;
  the line on this computer's cards.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ctypes, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])
from subtitld.modules import hardware
from subtitld.modules.addons import compute, installer
from subtitld.interface import addons_dialog
from subtitld import __main__ as subtitld_main  # noqa: F401  (sets up session paths)

installer.fetch_catalog = lambda force_refresh=False, **_kwargs: {}

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


class FakeCuda:
    """The driver API calls the check makes, answering from a device list."""

    def __init__(self, devices=(), init=0, crash=False):
        self.devices = list(devices)
        self.init = init
        self.crash = crash

    def cuInit(self, _flags):
        if self.crash:
            raise OSError('access violation')
        return self.init

    def cuDeviceGetCount(self, ref):
        ref._obj.value = len(self.devices)
        return 0

    def cuDeviceGet(self, ref, ordinal):
        ref._obj.value = ordinal
        return 0

    def cuDeviceGetName(self, buffer, _length, device):
        buffer.value = self.devices[device.value][0].encode()
        return 0

    def cuDeviceTotalMem_v2(self, ref, device):
        ref._obj.value = self.devices[device.value][1]
        return 0


GB = 1024 ** 3

print('CUDA, through the driver library')
state, devices = hardware._probe_cuda(FakeCuda([('NVIDIA GeForce RTX 3060', 12 * GB)]))
check('a card: found', state, hardware.FOUND)
check('its name and memory', devices, (hardware.Device('NVIDIA GeForce RTX 3060', 12 * GB),))
state, devices = hardware._probe_cuda(FakeCuda([('A', 8 * GB), ('B', 24 * GB)]))
check('two cards', [d.name for d in devices], ['A', 'B'])
check('the driver, no card', hardware._probe_cuda(FakeCuda(init=100)), (hardware.NONE, ()))
check('a driver too old', hardware._probe_cuda(FakeCuda(init=35)), (hardware.NONE, ()))
check('a driver that errs: unknown', hardware._probe_cuda(FakeCuda(init=304)), (hardware.UNKNOWN, ()))
check('cuInit fine, no device', hardware._probe_cuda(FakeCuda([])), (hardware.NONE, ()))

real_load, real_sandbox, real_platform = hardware._load_cuda_library, hardware._in_sandbox, sys.platform
try:
    hardware._load_cuda_library = lambda: None
    hardware._in_sandbox = lambda: False
    sys.platform = 'linux'
    check('no library: none', hardware._detect_cuda(), (hardware.NONE, ()))
    hardware._in_sandbox = lambda: True
    check('no library in a sandbox: unknown', hardware._detect_cuda(), (hardware.UNKNOWN, ()))
finally:
    hardware._load_cuda_library, hardware._in_sandbox, sys.platform = real_load, real_sandbox, real_platform

real_detectors = dict(hardware._DETECTORS)
try:
    hardware._DETECTORS[hardware.ACCELERATOR_CUDA] = lambda: hardware._probe_cuda(FakeCuda(crash=True))
    hardware._DETECTORS[hardware.ACCELERATOR_MPS] = lambda: (hardware.NONE, ())
    crashed = hardware._run_detectors()
    check('a driver that crashes its query: unknown', crashed.states[hardware.ACCELERATOR_CUDA], hardware.UNKNOWN)
    check('...and the other check still answers', crashed.states[hardware.ACCELERATOR_MPS], hardware.NONE)
finally:
    hardware._DETECTORS.clear()
    hardware._DETECTORS.update(real_detectors)

print('on this machine')
here = hardware.detect()
check('every accelerator answered', sorted(here.states), sorted(hardware.ACCELERATORS))
check('states are the three', all(s in (hardware.FOUND, hardware.NONE, hardware.UNKNOWN)
                                  for s in here.states.values()), True)
check('checked once, kept', hardware.detect() is here and hardware.cached() is here, True)
print('    ', here.states, here.found_devices())

print('compute.read')
check('a value', compute.read({'compute': 'GPU_Preferred', 'accelerators': ['CUDA', 'cuda', 'mps']}),
      ('gpu_preferred', ['cuda', 'mps']))
check('nothing', compute.read({}), (None, []))
check('not one of the four', compute.read({'compute': 'fast'}), (None, []))
check('accelerators junk dropped', compute.read({'compute': 'cpu', 'accelerators': [1, '', 'rocm']}),
      ('cpu', ['rocm']))

print('compute.assess')
cuda_here = hardware.Hardware({'cuda': 'found', 'mps': 'none'},
                              {'cuda': (hardware.Device('RTX 3060', 12 * GB),), 'mps': ()})
no_gpu = hardware.Hardware({'cuda': 'none', 'mps': 'none'}, {})
cuda_unknown = hardware.Hardware({'cuda': 'unknown', 'mps': 'none'}, {})
mac = hardware.Hardware({'cuda': 'none', 'mps': 'found'}, {'mps': (hardware.Device('Apple M2'),)})


def kind(c, accs, hw):
    verdict = compute.assess(c, accs, hw)
    return verdict and (verdict.kind, verdict.accelerator)


check('no field: no verdict', compute.assess(None, [], cuda_here), None)
check('cpu, any hardware', [kind('cpu', [], hw) for hw in (None, no_gpu, cuda_here)], [('cpu', None)] * 3)
check('remote', kind('remote', [], no_gpu), ('remote', None))
check('preferred, cuda here', kind('gpu_preferred', ['cuda'], cuda_here), ('gpu', 'cuda'))
check('preferred, no gpu', kind('gpu_preferred', ['cuda'], no_gpu), ('cpu_fallback', None))
check('required, no gpu', kind('gpu_required', ['cuda'], no_gpu), ('missing', None))
check('required cuda, on a Mac', kind('gpu_required', ['cuda'], mac), ('missing', None))
check('preferred mps, on a Mac', kind('gpu_preferred', ['mps'], mac), ('gpu', 'mps'))
check('preferred mps, cuda here: falls back', kind('gpu_preferred', ['mps'], cuda_here), ('cpu_fallback', None))
check('either, cuda here', kind('gpu_preferred', ['mps', 'cuda'], cuda_here), ('gpu', 'cuda'))
check('not checked yet', kind('gpu_required', ['cuda'], None), ('unchecked', None))
check('check unknown', kind('gpu_required', ['cuda'], cuda_unknown), ('unchecked', None))
check('an accelerator not checked here', kind('gpu_required', ['rocm'], no_gpu), ('unchecked', None))
check('no cards named, none of any kind', kind('gpu_required', [], no_gpu), ('missing', None))
check('no cards named, a card here', kind('gpu_preferred', [], cuda_here), ('unchecked', None))
check('runs_here', [compute.runs_here(compute.assess('gpu_required', ['cuda'], hw))
                    for hw in (None, no_gpu, cuda_here)], [True, False, True])

print('the add-ons panel')
manifests = [
    {'id': 'fast-cpu', 'display_name': 'Fast CPU', 'tasks': ['asr.transcribe'], 'compute': 'cpu'},
    {'id': 'old-one', 'display_name': 'Old one', 'tasks': ['asr.transcribe']},
    {'id': 'silent', 'display_name': 'Silent', 'tasks': ['asr.transcribe']},
]
catalog = {'addons': [
    {'id': 'old-one', 'display_name': 'Old one', 'tasks': ['asr.transcribe'],
     'compute': 'gpu_preferred', 'accelerators': ['cuda'], 'latest_version': '1'},
    {'id': 'big-gpu', 'display_name': 'Big GPU', 'tasks': ['video.lipsync'],
     'compute': 'gpu_required', 'accelerators': ['cuda'], 'latest_version': '1'},
    {'id': 'cloudy', 'display_name': 'Cloudy', 'tasks': ['asr.transcribe'],
     'compute': 'remote', 'latest_version': '1'},
]}
installer.list_installed = lambda: manifests
installer.fetch_catalog = lambda force_refresh=False, **_kwargs: catalog
from subtitld.modules import addons as _addons
from subtitld.modules.addons.builtin import edge_tts_provider, ffmpeg_separator_provider
_addons.get_manager().register_builtin(edge_tts_provider.get_provider())
_addons.get_manager().register_builtin(ffmpeg_separator_provider.get_provider())
hardware._cached = no_gpu  # the panel takes what was found, with no check of its own
panel = addons_dialog.AddonsPanel()
panel._on_catalog(catalog, None)
check('no check started when one was kept', panel._hardware_thread, None)


def chips():
    out = {}
    for addon_id, card in panel._cards_by_id.items():
        labels = [l for l in card.findChildren(QLabel)
                  if l.property('class') in ('addon_card_compute', 'addon_card_compute_warning')]
        out[addon_id] = [(l.text(), l.property('class')) for l in labels]
    return out


rows = {r.addon_id: r for r in panel._rows}
check('installed, from its manifest', (rows['fast-cpu'].compute, rows['fast-cpu'].accelerators), ('cpu', []))
check('installed, manifest silent: the catalog\'s', (rows['old-one'].compute, rows['old-one'].accelerators),
      ('gpu_preferred', ['cuda']))
check('says nothing: nothing', rows['silent'].compute, None)
shown = chips()
check('cpu chip', shown['fast-cpu'], [('CPU', 'addon_card_compute')])
check('fallback chip', shown['old-one'], [('Runs on the CPU here (slower)', 'addon_card_compute')])
check('missing chip, a warning', shown['big-gpu'],
      [('Needs NVIDIA GPU: none found', 'addon_card_compute_warning')])
check('cloud chip', shown['cloudy'], [('Cloud', 'addon_card_compute')])
check('no chip when it says nothing', shown['silent'], [])
check('built-ins say theirs', {k: v for k, v in shown.items() if k in ('edge-tts', 'ffmpeg-separator')},
      {'edge-tts': [('Cloud', 'addon_card_compute')], 'ffmpeg-separator': [('CPU', 'addon_card_compute')]})
check('the line on this computer', panel._hardware_info.text(),
      'No graphics card add-ons can use: they run on the processor.')

panel._hardware_combo.setCurrentIndex(1)
check('"Runs on this computer" hides only the missing',
      ('big-gpu' in panel._cards_by_id, all(a in panel._cards_by_id for a in ('fast-cpu', 'old-one', 'silent', 'cloudy'))),
      (False, True))
panel._hardware_combo.setCurrentIndex(0)

panel._on_hardware(cuda_here)
shown = chips()
check('with a card: uses it', shown['old-one'], [('Uses your NVIDIA GPU', 'addon_card_compute')])
check('...and the required one too', shown['big-gpu'], [('Uses your NVIDIA GPU', 'addon_card_compute')])
tooltip = [l.toolTip() for l in panel._cards_by_id['big-gpu'].findChildren(QLabel)
           if l.property('class') == 'addon_card_compute'][0]
check('its tooltip names the card', tooltip, "Runs on this computer's graphics card: RTX 3060 (12 GB).")
check('the line names it', panel._hardware_info.text(), 'Graphics card for add-ons: RTX 3060 (12 GB)')

panel._on_hardware(cuda_unknown)
check('could not tell: what it asks for', chips()['big-gpu'], [('Needs NVIDIA GPU', 'addon_card_compute')])
check('...and the line says so', panel._hardware_info.text(), "Couldn't check this computer's graphics cards.")
panel.shutdown()

print()
print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
