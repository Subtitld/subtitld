"""The timeline theme authoring reference (timeline_themes/AUTHORING.md):
its starter file passes Subtitld's check, with every setting it writes
known to Subtitld and its options taking effect; its SVG examples are
shapes Subtitld draws and frames with the parts they say; and every
setting Subtitld has is in its tables.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, re, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session, timeline_frames, timeline_style, timeline_svg, timeline_themes
session.CONFIG = {}

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


document = (SRC / 'subtitld' / 'timeline_themes' / 'AUTHORING.md').read_text(encoding='utf-8')
starters = [b for b in re.findall(r'```json\n(.*?)```', document, re.DOTALL) if b.lstrip().startswith('{\n  "format"')]
starters = [b for b in starters if '…' not in b]
check('complete theme files in it', len(starters), 1)
raw = json.loads(starters[0])
theme = timeline_themes.parse(starters[0].encode('utf-8'))
check('starter parses', theme.id, 'yourname.slate')
check('starter: every timeline setting known', sorted(set(raw['timeline']) - set(timeline_style.TIMELINE_FIELDS_BY_KEY)), [])
for kind, box in raw['boxes'].items():
    check(f'starter: every {kind} setting known', sorted(set(box) - set(timeline_style.BOX_FIELDS_BY_KEY)), [])
check('starter: no value dropped', theme.look, {'timeline': raw['timeline'], 'boxes': raw['boxes']})
for option in theme.options:
    for target in option['sets']:
        settings = theme.look['boxes'][target['box']] if target['box'] else theme.look['timeline']
        current = settings[target['key']]
        check(f'option {option["key"]} default is the theme\'s {target["key"]}',
              current[3:] if target['part'] == 'color' else current, option['default'][3:])

svgs = re.findall(r'```svg\n(.*?)```', document, re.DOTALL)
check('SVG examples', len(svgs), 2)
for index, svg in enumerate(svgs):
    data = svg.encode('utf-8')
    timeline_svg.check(data)
    name = timeline_svg.keep(data)
    found = timeline_frames.get(name)
    check(f'SVG example {index + 1} is a frame', found is not None, True)
    if index == 1:
        check('grip frame: hits', sorted(p.hit for p in found.parts if p.hit), ['body', 'end', 'start'])

for field in timeline_style.TIMELINE_FIELDS + timeline_style.BOX_FIELDS:
    if f'`{field["key"]}`' not in document:
        fails.append(f'setting {field["key"]} not in the reference')
        print(f'  [FAIL] setting {field["key"]} not in the reference')

print('ALL PASS' if not fails else f'FAILED: {fails}')
sys.exit(1 if fails else 0)
