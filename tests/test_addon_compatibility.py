"""Catalog add-ons this Subtitld can use: `min_subtitld` and known tasks.

Standalone script, like the other suites here; puts this checkout's src/ on
the path itself (the venv holds a non-editable install).
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import session
from subtitld.modules.addons import installer

fails = []


def check(name, got, want):
    ok = got == want
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f'\n     got  {got!r}\n     want {want!r}'))
    if not ok:
        fails.append(name)


def raises(function):
    try:
        function()
    except ValueError as exc:
        return str(exc)
    return None


new_enough = installer.subtitld_is_new_enough

# ---- Version comparison ----
check('26.10 runs a 0.6.0 floor', new_enough('0.6.0', '26.10'), True)
check('26.10 runs 26.10', new_enough('26.10', '26.10'), True)
check('26.10 runs 26.10.0 (missing parts are zeros)', new_enough('26.10.0', '26.10'), True)
check('26.10 does not run 26.11', new_enough('26.11', '26.10'), False)
check('26.10 does not run 26.10.1', new_enough('26.10.1', '26.10'), False)
check('26.10.2 runs 26.10.1', new_enough('26.10.1', '26.10.2'), True)
check('26.9 does not run 26.10 (numeric, not text)', new_enough('26.10', '26.9'), False)
check('no minimum is the catalog default', new_enough(None, '26.10'), True)
check('development builds run anything', new_enough('99.12', '0.0.0.dev0'), True)
check('a junk minimum is satisfied', new_enough('soon', '26.10'), True)
check('a junk running version is satisfied', new_enough('26.11', 'unknown'), True)

# ---- Release selection ----
entry = {
    'id': 'example', 'tasks': ['tts.synthesize'], 'latest_version': '26.12.1',
    'releases': [
        {'version': '26.12.1', 'min_subtitld': '26.12'},
        {'version': '26.11.1', 'min_subtitld': '26.11'},
        {'version': '26.10.1', 'min_subtitld': '0.6.0'},
    ],
}
check('newest release the running version may use',
      installer.compatible_release(entry, '26.11')['version'], '26.11.1')
check('entry offered at that release',
      installer.compatible_entry(entry, '26.11')['latest_version'], '26.11.1')
check('catalog entry itself untouched', entry['latest_version'], '26.12.1')
check('development build gets the newest',
      installer.compatible_entry(entry, '0.0.0.dev0')['latest_version'], '26.12.1')
too_new = {'id': 'later', 'tasks': ['tts.synthesize'], 'latest_version': '27.1.1',
           'releases': [{'version': '27.1.1', 'min_subtitld': '27.1'}]}
check('nothing to offer when every release needs a newer Subtitld',
      installer.compatible_entry(too_new, '26.10'), None)
check('a release with no min_subtitld is offered',
      installer.compatible_entry({'id': 'x', 'tasks': ['asr.transcribe'],
                                  'releases': [{'version': '1.0.0'}]}, '26.10')['latest_version'],
      '1.0.0')

# ---- Unknown tasks ----
from subtitld.modules.addons.manager import ADDON_PROVIDER_CLASSES
check('known tasks are the manager\'s', installer.supported_tasks(), set(ADDON_PROVIDER_CLASSES))
for task in ('tts.synthesize', 'asr.transcribe', 'translate.text', 'audio.separate', 'video.lipsync'):
    check(f'{task} supported', installer.task_supported({'tasks': [task]}), True)
unknown = {'id': 'ocr', 'tasks': ['some.future.task', 'tts.synthesize'], 'latest_version': '1.0.0',
           'releases': [{'version': '1.0.0', 'min_subtitld': '0.6.0'}]}
check('unknown primary task not offered', installer.compatible_entry(unknown, '0.0.0.dev0'), None)
check('no tasks not offered', installer.task_supported({'tasks': []}), False)

# ---- Installer refuses (before any download) ----
with tempfile.TemporaryDirectory() as tmp:
    session.PATH_SUBTITLD_USER_CACHE = tmp
    session.PATH_SUBTITLD_ADDONS = tmp
    error = raises(lambda: installer.download_and_install(unknown))
    check('installer refuses an unknown task', bool(error and 'task' in error), True)

    later = {'id': 'later', 'tasks': ['tts.synthesize'], 'latest_version': '99.1.1',
             'releases': [{'version': '99.1.1', 'min_subtitld': '99.1',
                           'downloads': [{'platform': p, 'url': 'http://invalid.test/x.zip'}
                                         for p in ('linux', 'macos', 'windows')]}]}
    running = installer.__version__
    installer.__version__ = '26.10'
    try:
        error = raises(lambda: installer.download_and_install(later))
        check('installer refuses a release needing a newer Subtitld',
              bool(error and 'needs Subtitld 99.1' in error), True)
    finally:
        installer.__version__ = running

# ---- Platforms ----
from subtitld.modules.addons import platforms
real_platform = sys.platform
try:
    for name, key in (('haiku', 'haiku'), ('haiku1', 'haiku'), ('linux', 'linux'),
                      ('darwin', 'macos'), ('win32', 'windows')):
        sys.platform = name
        check(f'{name} is {key}', platforms.current_platform_key(), key)
finally:
    sys.platform = real_platform
check('package name from an hpkg file name',
      platforms._package_name('onnxruntime_python3.14-1.31.0-1-x86_64.hpkg'), 'onnxruntime_python3.14')

here = platforms.current_platform_key()
other = 'haiku' if here != 'haiku' else 'linux'
split = {'id': 'split', 'tasks': ['tts.synthesize'], 'latest_version': '2.0.0',
         'releases': [{'version': '2.0.0', 'downloads': [{'platform': other, 'url': 'x'}]},
                      {'version': '1.0.0', 'downloads': [{'platform': here, 'url': 'x'}]}]}
check('newest release with a zip for this platform',
      installer.compatible_entry(split, '0.0.0.dev0')['latest_version'], '1.0.0')
elsewhere = {**split, 'releases': split['releases'][:1]}
check('nothing to offer without a zip for this platform',
      installer.compatible_entry(elsewhere, '0.0.0.dev0'), None)


def make_zip(path, manifest, files):
    import json
    import zipfile
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('manifest.json', json.dumps(manifest))
        for name, mode in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | mode) << 16
            zf.writestr(info, '#!/bin/sh\n')


with tempfile.TemporaryDirectory() as tmp:
    session.PATH_SUBTITLD_ADDONS = str(Path(tmp) / 'addons')
    manifest = {'id': 'modes', 'executable': 'bin/modes-addon', 'haiku_packages': ['onnxruntime_python3.14']}
    make_zip(Path(tmp) / 'modes.zip', manifest,
             {'bin/modes-addon': 0o755, 'native/helper': 0o755, 'lib/data.txt': 0o644})

    real_haiku, real_active = platforms.is_haiku, platforms.active_packages
    platforms.is_haiku = lambda: True
    platforms.active_packages = lambda: {'python3.14'}
    try:
        try:
            installer.install_from_zip(Path(tmp) / 'modes.zip')
            missing = None
        except installer.MissingPackagesError as exc:
            missing = exc
        check('Haiku: missing packages stop the install', missing and missing.packages,
              ['onnxruntime_python3.14'])
        check('Haiku: nothing extracted', (Path(tmp) / 'addons' / 'modes').exists(), False)
        platforms.active_packages = lambda: {'python3.14', 'onnxruntime_python3.14'}
        check('Haiku: installs once the packages are there',
              installer.install_from_zip(Path(tmp) / 'modes.zip'), 'modes')
        platforms.is_haiku = lambda: False
        platforms.active_packages = lambda: set()
        check('elsewhere haiku_packages are ignored', platforms.missing_packages(manifest), [])
    finally:
        platforms.is_haiku, platforms.active_packages = real_haiku, real_active
    if os.name == 'posix':
        root = Path(tmp) / 'addons' / 'modes'
        check('bundled helper keeps its executable bit', os.access(root / 'native/helper', os.X_OK), True)
        check('data file stays not executable', os.access(root / 'lib/data.txt', os.X_OK), False)

# ---- The add-ons dialog's rows ----
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from subtitld.interface import addons_dialog

catalog = {'addons': [entry, too_new, unknown,
                      {**too_new, 'id': 'installed-later', 'releases': too_new['releases']}]}
installed = [{'id': 'installed-later', 'version': '26.10.1', 'tasks': ['tts.synthesize']}]
installer.__version__ = '26.11'
try:
    rows = {row.addon_id: row for row in addons_dialog._build_rows(installed, catalog)}
finally:
    installer.__version__ = running
check('rows: only usable catalog add-ons, installed ones kept',
      sorted(rows), ['example', 'installed-later'])
check('rows: offered at the release this Subtitld runs', rows['example'].latest_version, '26.11.1')
check('rows: installed add-on gets no update it cannot run',
      (rows['installed-later'].catalog_entry, rows['installed-later'].has_update), (None, False))

print()
print(f'{len(fails)} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
