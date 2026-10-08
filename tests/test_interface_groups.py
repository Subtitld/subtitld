"""Interface panel: each checkable group saves its switch to the same setting
it is shown from.

"Background box" used to save to the action-safe-margin setting: it could
not be turned off, and clicking it switched the safe-margin overlay instead.
This reads the panel's source and pairs, for every group, the setting its
toggle handler writes with the one update() checks it from.

Standalone script, like the other suites here.
"""
import re
import sys
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'src' / 'subtitld' / 'interface' / 'left_panel_interface.py').read_text()

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


written = dict(re.findall(
    r"def global_panel_interface_(\w+)_group_toggled\(self\):\s*\n\s*session\.CONFIG\['videoplayer'\]\['(\w+)'\]", source))
shown = dict(re.findall(
    r"self\.global_panel_interface_(\w+)_group\.setChecked\(session\.CONFIG\['videoplayer'\]\.get\('(\w+)'", source))

print('every checkable group writes the setting it is shown from')
check('groups found', sorted(written), sorted(shown))
for group in sorted(written):
    check(group, written[group], shown.get(group))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
