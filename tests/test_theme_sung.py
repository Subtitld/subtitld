"""Sung words: a subtitle theme that moves each word (or letter) as it is
sung, in a subtitle timed word by word (AUTHORING.md, "Sung words"). Its
manifest's timing "sung", the moments an export draws (a picture from each
word's start, a frame each while a word moves) and what the page gives each
word (--k-at, data-timed), seen in a drawn sheet.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, re, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.interface import subtitle_pictures
from subtitld.modules import config, session, subtitle_styles, theme_check, themes
from subtitld.modules.segment import Segment, Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


document = (SRC / 'subtitld' / 'themes' / 'AUTHORING.md').read_text(encoding='utf-8')
example = next(b for b in re.findall(r'```html\n(.*?)```', document, re.DOTALL) if '"example.sung_pop"' in b)

print('the manifest')
theme = themes.parse(example)
check('timing keeps "sung"', theme.timing, {'intro': 300, 'outro': 200, 'sung': 300})
check('without it, no "sung"', themes.parse(example.replace(', "sung": 300', '')).timing, {'intro': 300, 'outro': 200})
for bad in ('"sung": 3001', '"sung": -1', '"sung": 1.5', '"sung": true'):
    try:
        themes.parse(example.replace('"sung": 300', bad))
        check(f'{bad} refused', False, True)
    except themes.ThemeError as error:
        check(f'{bad} refused', any('timing sung' in problem for problem in error.problems), True)

print('the moments an export draws')
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'test.config')
session.SUBTITLE = Subtitle(segments=[])
folder = Path(tempfile.mkdtemp())
path = folder / 'example.sung_pop.html'
path.write_text(example, encoding='utf-8')
themes.carry({path.name: example.encode('utf-8')})
style = 'example.sung_pop'
pieces = [{'text': 'One ', 'start': 0.5, 'end': 1.0, 'level': 'word'},
          {'text': 'two ', 'start': 1.0, 'end': 1.1, 'level': 'word'},
          {'text': 'three', 'start': 2.0, 'end': 2.5, 'level': 'word'}]
timed = Segment({'start': 10.0, 'end': 13.0, 'text': pieces})
plain = Segment({'start': 10.0, 'end': 13.0, 'text': 'One two three'})
check('sung, in seconds', themes.sung(timed, style), 0.3)
shots = subtitle_pictures.moments(plain, 25, style)
check('not timed: one picture for the middle', [s for s in shots if 10.3 <= s[0] < 12.8 - 1e-6], [(10.3, 10.3, 12.8)])
shots = subtitle_pictures.moments(timed, 25, style)
middle = [s for s in shots if 10.3 - 1e-6 <= s[0] < 12.8 - 1e-6]
check('timed: pictures cover the middle without gaps', all(abs(a[2] - b[1]) < 1e-6 for a, b in zip(middle, middle[1:]))
      and abs(middle[0][1] - 10.3) < 1e-6 and abs(middle[-1][2] - 12.8) < 1e-6, True)
moving = [s for s in middle if s[2] - s[1] <= 1 / 25 + 1e-6]
check('a frame each while a word moves: 10.5, 11.0 and 12.0, 300 ms each', (round(moving[0][0], 2), round(moving[-1][2], 2),
      [sum(1 for s in moving if start - 1e-6 <= s[0] < start + 0.3 - 1e-6) for start in (10.5, 11.0, 12.0)], len(moving)),
      (10.5, 12.3, [8, 8, 8], 24))
held = [round(s[1], 2) for s in middle if s[2] - s[1] > 1 / 25 + 1e-6]
check('held between them', held, [10.3, 10.8, 11.3, 12.3])
no_sung = example.replace(', "sung": 300', '').replace('example.sung_pop', 'example.no_sung')
themes.carry({'example.no_sung.html': no_sung.encode('utf-8')})
shots = subtitle_pictures.moments(timed, 25, 'example.no_sung')
check('without "sung": a picture from each word\'s start', [round(s[1], 2) for s in shots if 10.3 - 1e-6 <= s[0] < 12.8],
      [10.3, 10.5, 11.0, 12.0])

print('drawn: each word as it is sung')
themes.carry({path.name: example.encode('utf-8')})
theme = themes.load(path)
sheet = folder / 'sung.png'
theme_check.preview(theme, sheet, text='Every word pops', sung=True)
labels = [label for _at, label in theme_check.moments(theme, 4.0, theme_check.sung_pieces('Every word pops', 0.4, 3.0))]
image = QImage(str(sheet))


def tile(number):
    x0, y0 = (number % 4) * theme_check.TILE[0], (number // 4) * theme_check.TILE[1]
    return [image.pixel(x0 + x, y0 + y) for y in range(30, theme_check.TILE[1], 2) for x in range(0, theme_check.TILE[0], 2)]


def gold(number):
    return sum(1 for pixel in tile(number) if (pixel >> 16 & 255) > 200 and (pixel >> 8 & 255) > 160 and (pixel & 255) < 120)


before, first, popped, everything = (labels.index(label) for label in ('before it is sung', 'word 1 sung', 'word 1 +99 ms', 'all sung'))
check('nothing gold before it is sung', gold(before) < 20, True)
check('the first word gold as it pops', gold(popped) > 100, True)
check('it moves: popping differs from its start', sum(a != b for a, b in zip(tile(first), tile(popped))) > 100, True)
second = labels.index('word 2 sung')
check('every word gold once all are sung', gold(everything) > gold(second) * 1.5, True)

print('drawn: only the word sung now (--k-for)')
now = (example.replace('example.sung_pop', 'example.sung_now')
       .replace('[data-timed] .word { animation: pop 300ms cubic-bezier(.3, 1.6, .5, 1) both; animation-delay: var(--k-at); }',
                '[data-timed] .word { animation: now var(--k-for) step-end both; animation-delay: var(--k-at); }\n'
                '  @keyframes now { from { color: var(--karaoke-color); } to { color: #fff; } }'))
check('the "now" rule is in', '@keyframes now' in now, True)
now_path = folder / 'example.sung_now.html'
now_path.write_text(now, encoding='utf-8')
themes.carry({now_path.name: now.encode('utf-8')})
sheet = folder / 'now.png'
theme_check.preview(themes.load(now_path), sheet, text='Every word pops', sung=True)
popped_all = gold(everything)
image = QImage(str(sheet))
check('the first word gold while it is sung', gold(first) > 60, True)
check('then let go: only the last word gold at the end', 0 < gold(everything) < popped_all * 0.6, True)

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
