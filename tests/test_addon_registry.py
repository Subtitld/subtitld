"""Config left behind by removed built-ins (AssemblyAI, whisper.cpp).

Standalone script, like the other suites here; puts this checkout's src/ on
the path itself (the venv holds a non-editable install).
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import session
from subtitld.modules.addons import registry

fails = []


def check(name, got, want):
    ok = got == want
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f'\n     got  {got!r}\n     want {want!r}'))
    if not ok:
        fails.append(name)


def prune(addons):
    session.CONFIG = {'addons': copy.deepcopy(addons)}
    registry.prune_removed_builtins()
    return session.CONFIG['addons']


# An existing user: AssemblyAI already pruned under the old single-revision
# marker, and a whisper.cpp built-in that left a disabled flag and a model.
legacy = {
    '_removed_builtins_pruned': 1,
    'enabled': {'whispercpp': False, 'assemblyai': False, 'vosk': False},
    'options': {'whispercpp': {'model': 'large-v3', 'threads': 4},
                'assemblyai': {'api_key': 'typed-into-the-add-on'}},
    'defaults': {'asr.transcribe': 'whispercpp', 'tts.synthesize': 'edge-tts'},
}
after = prune(legacy)
check('legacy: stale whisper.cpp flag gone, other flags kept',
      after['enabled'], {'assemblyai': False, 'vosk': False})
check('legacy: whisper.cpp model kept (threads as the add-on stores it)',
      after['options']['whispercpp'], {'model': 'large-v3', 'threads': '4'})
check('legacy: AssemblyAI is not pruned a second time',
      after['options']['assemblyai'], {'api_key': 'typed-into-the-add-on'})
check('legacy: default naming the removed engine dropped',
      after['defaults'], {'tts.synthesize': 'edge-tts'})
check('legacy: marker lists both', after['_removed_builtins_pruned'], ['assemblyai', 'whispercpp'])

# Running again changes nothing, even after the add-on stored new settings.
after['enabled']['whispercpp'] = False          # the user disabled the add-on
after['options']['whispercpp']['threads'] = '8'
again = prune(after)
check('second run: the add-on\'s own settings are left alone', again, after)

# A config that never saw a prune: both ids pruned once, as their rules say.
fresh = prune({
    'enabled': {'whispercpp': False, 'assemblyai': False},
    'options': {'whispercpp': {'model': 'base'}, 'assemblyai': {'api_key': 'stale'}},
    'defaults': {'asr.transcribe': 'assemblyai'},
})
check('fresh: both flags gone', fresh['enabled'], {})
check('fresh: AssemblyAI\'s stale options gone, whisper.cpp\'s kept',
      fresh['options'], {'whispercpp': {'model': 'base'}})
check('fresh: defaults cleared', fresh['defaults'], {})
check('fresh: marker', fresh['_removed_builtins_pruned'], ['assemblyai', 'whispercpp'])

# A brand-new config: nothing to prune, marker written, nothing invented.
empty = prune({})
check('empty config', empty, {'_removed_builtins_pruned': ['assemblyai', 'whispercpp']})

# Odd shapes do not crash it.
odd = prune({'enabled': None, 'options': {'whispercpp': 'x'}, 'defaults': []})
check('odd shapes survive', odd['options'], {'whispercpp': 'x'})
check('a boolean threads value is not mistaken for a number',
      prune({'_removed_builtins_pruned': 1, 'options': {'whispercpp': {'threads': True}}})['options'],
      {'whispercpp': {'threads': True}})

# threads values the old field allowed but the add-on does not list.
for saved, want in ((6, '4'), (3, '2'), (12, '8'), (32, '16'), (16, '16'), (1, '1'), (0, '0'), (-1, '0')):
    got = prune({'options': {'whispercpp': {'threads': saved}}})['options']['whispercpp']['threads']
    check(f'threads {saved} becomes a listed choice', got, want)

print('\n' + ('FAIL: ' + ', '.join(fails) if fails else 'ALL REGISTRY TESTS PASS'))
sys.exit(1 if fails else 0)
