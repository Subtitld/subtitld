"""Add-on settings: numeric fields keep to their schema range, and saved
settings reach a translation add-on.

No add-on process is started: the provider's process is a stand-in that
records what would be sent.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QSpinBox, QDoubleSpinBox
from PySide6.QtCore import QObject, Signal
app = QApplication([])
from subtitld.modules.addons import schema, addon_provider
from subtitld.interface import addons_dialog

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


BEAM = {'key': 'beam_size', 'type': 'number', 'label': 'Beam size', 'default': 2, 'min': 1, 'max': 5, 'step': 1}
THRESH = {'key': 'det_thresh', 'type': 'number', 'default': 0.5, 'min': 0.1, 'max': 0.9, 'step': 0.05}
STEPS = {'key': 'total_steps', 'type': 'int', 'min': 5, 'max': 12}
MANIFEST = {'id': 'translate-offline', 'config_schema': {'fields': [BEAM]}}

print('a numeric setting is clamped to its range')
check('above the top', schema.coerce_number(BEAM, 8), 5)
check('below the bottom', schema.coerce_number(BEAM, 0), 1)
check('typed as text', schema.coerce_number(BEAM, '3'), 3)
check('not a number at all', schema.coerce_number(BEAM, 'abc'), None)
check('a decimal field stays decimal', schema.coerce_number(THRESH, 0.95), 0.9)
check('"int" and "number" are both numeric', (schema.is_numeric(STEPS), schema.is_numeric(BEAM)), (True, True))
check('a 0.05-step field is not whole', schema.is_integral(THRESH), False)
check('stored options are cleaned, others left alone',
      schema.normalize_options(MANIFEST, {'beam_size': 8, 'other': 'x'}), {'beam_size': 5, 'other': 'x'})
check('garbage is dropped so the default applies', schema.normalize_options(MANIFEST, {'beam_size': ''}), {})

print('the settings forms use a spinbox held to the range')
spin = addons_dialog._number_spinbox(BEAM, 8)
check('a whole-number spinbox', isinstance(spin, QSpinBox), True)
check('range from the schema', (spin.minimum(), spin.maximum()), (1, 5))
check('a saved 8 shows as 5', spin.value(), 5)
check('no saved value shows the default', addons_dialog._number_spinbox(BEAM, None).value(), 2)
dspin = addons_dialog._number_spinbox(THRESH, 0.35)
check('a decimal spinbox for a 0.05 step',
      (isinstance(dspin, QDoubleSpinBox), dspin.decimals(), dspin.singleStep(), dspin.value()),
      (True, 2, 0.05, 0.35))

print("the panel's inline settings show Beam size as a spinbox")
import types
from subtitld.modules.addons import registry as _real_registry
_real_registry.options_for = lambda addon_id: {'beam_size': 8}
inline = addons_dialog.AddonConfigInlineWidget.for_provider(
    types.SimpleNamespace(id='translate-offline', config_schema=MANIFEST['config_schema']))
spins = inline.findChildren(QSpinBox)
check('one spinbox, not a text box', len(spins), 1)
check('held to 1-5, the saved 8 shown as 5', (spins[0].minimum(), spins[0].maximum(), spins[0].value()), (1, 5, 5))

print('a translation add-on gets its saved settings, clamped')
addon_provider._registry.options_for = lambda addon_id: {'beam_size': 8}
sent = []


class FakeRequest(QObject):
    result = Signal(dict)
    error = Signal(str, str)


class FakeProcess:
    def request(self, task, params):
        sent.append((task, params))
        return FakeRequest()


provider = addon_provider.AddonTranslationProvider('translate-offline', MANIFEST, '/nonexistent')
provider._ensure_process = lambda: FakeProcess()
provider.translate('r1', 'Hello', 'en', 'pt')
check('one request', len(sent), 1)
check('it carries the saved beam size, clamped', sent[0][1]['options'], {'beam_size': 5})
provider.translate('r2', 'Hello', 'en', 'pt', options={'beam_size': 3})
check("a request's own value wins", sent[1][1]['options'], {'beam_size': 3})

print('the environment an add-on starts with is clamped too')
env = addon_provider._build_addon_env('translate-offline', {'beam_size': 8}, MANIFEST)
check('TRANSLATE_OFFLINE_BEAM_SIZE', env.get('TRANSLATE_OFFLINE_BEAM_SIZE'), '5')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
