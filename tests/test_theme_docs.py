"""The theme authoring reference (themes/AUTHORING.md): every complete theme
file in it passes Subtitld's check and draws something, at its middle and
through its intro; and `subtitld --check-theme` says so, or says why not.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, re, subprocess, sys, tempfile
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
from subtitld.modules import theme_check, themes

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


document = (SRC / 'subtitld' / 'themes' / 'AUTHORING.md').read_text(encoding='utf-8')
blocks = [b for b in re.findall(r'```html\n(.*?)```', document, re.DOTALL) if b.lstrip().lower().startswith('<!doctype')]
check('complete theme files in it', len(blocks), 5)
folder = Path(tempfile.mkdtemp())


def ink(path, tile):
    """How many pixels of a sheet's tile differ from its checked backdrop."""
    image = QImage(str(path))
    x0, y0 = (tile % 4) * theme_check.TILE[0], (tile // 4) * theme_check.TILE[1]
    count = 0
    for y in range(y0 + 30, y0 + theme_check.TILE[1], 3):
        for x in range(x0, x0 + theme_check.TILE[0], 3):
            colour = image.pixelColor(x, y).name()
            if colour not in ('#59606a', '#4a5058'):
                count += 1
    return count


def differ(path, one, other):
    """How many pixels two of a sheet's tiles differ in (the labels left out)."""
    image = QImage(str(path))
    corners = [((t % 4) * theme_check.TILE[0], (t // 4) * theme_check.TILE[1]) for t in (one, other)]
    return sum(1 for y in range(30, theme_check.TILE[1], 2) for x in range(0, theme_check.TILE[0], 2)
               if image.pixel(corners[0][0] + x, corners[0][1] + y) != image.pixel(corners[1][0] + x, corners[1][1] + y))


for block in blocks:
    theme = themes.parse(block)
    print(theme.id)
    path = folder / f'{theme.id}.html'
    path.write_text(block, encoding='utf-8')
    theme = themes.load(path)
    check('passes the check', theme.id.split('.')[0] in ('yourname', 'example'), True)
    sheet = folder / f'{theme.id}.png'
    count = theme_check.preview(theme, sheet)
    middle = [label for _at, label in theme_check.moments(theme, 4.0)].index('middle' if theme.timing else 'still')
    check('drawn at its middle', ink(sheet, middle) > 200, True)
    if theme.timing:
        check('the intro moves: 25% and the middle differ', differ(sheet, 1, middle) > 100, True)
    themes.carry({})

print('the command')
good = folder / 'yourname.my_title.html'
run = subprocess.run([sys.executable, '-m', 'subtitld', '--check-theme', str(good)], capture_output=True, text=True,
                     env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=120)
check('a good file: 0, its summary', (run.returncode, run.stdout.startswith('OK: yourname.my_title (title)')), (0, True))
bad = folder / 'bad.html'
bad.write_text(blocks[0].replace('<style>', '<style>@import "x.css";').replace('"kind": "subtitle"', '"kind": "caption"'),
               encoding='utf-8')
run = subprocess.run([sys.executable, '-m', 'subtitld', '--check-theme', str(bad)], capture_output=True, text=True,
                     env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=120)
check('a bad one: 1, every reason', (run.returncode, run.stdout.startswith('REFUSED'), '@import' in run.stdout,
                                     "kind 'caption'" in run.stdout), (1, True, True, True))

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
