#!/usr/bin/env python3
"""Render every Subtitld icon asset from the two sources in this folder.

    icon.svg   the mark alone, on transparency: the application icon
    logo.svg   the mark on the slate tile: the look of the Store tiles

Writes:

    src/subtitld/graphics/subtitld.svg        scalable app icon (icon.svg)
    src/subtitld/graphics/subtitld.png        512 px app icon: window icon,
                                              Flatpak, macOS build
    src/subtitld/graphics/subtitld.ico        16-256 px, Windows
    snap/gui/icon.png                         512 px: Snap, AppImage, the
                                              Windows builds
    src/subtitld/graphics/background_watermark.svg
                                              the start screen's outline mark
    src/subtitld/graphics/subtitld_mark.png   the mark alone, cropped, 11 px tall
                                              (and @2x): the title bar's label
    packaging/msix/tiles.svg                  the Store tiles, laid out for
    packaging/msix/assets/*.png               generate-assets.py, which this
                                              runs to render them
    packaging/nsis/installer.ico              the Windows installer and its
    packaging/nsis/uninstaller.ico            uninstaller: the mark with a
                                              badge (install / remove)

Usage:  python packaging/branding/render-icons.py

Needs Inkscape on the PATH (a Snap Inkscape can only reach files under
your home folder, so keep the checkout there) and Pillow for the .ico.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GRAPHICS = ROOT / 'src' / 'subtitld' / 'graphics'
MSIX = ROOT / 'packaging' / 'msix'

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# The Store tiles: (generate-assets.py name, width, height, mark width as a
# share of the tile's shorter side). logo.svg sets the mark at 41% of its
# tile; the small icons get a bigger mark to stay legible at 16-48 px.
TILES = (
    ('StoreLogo', 50, 50, 0.62),
    ('Square44x44Logo', 44, 44, 0.66),
    ('Square71x71Logo', 71, 71, 0.52),
    ('Square150x150Logo', 150, 150, 0.41),
    ('Square310x310Logo', 310, 310, 0.41),
    ('Wide310x150Logo', 310, 150, 0.41),
    ('SplashScreen', 620, 300, 0.41),
)


def inkscape_png(svg: Path, out: Path, width: int) -> None:
    subprocess.run(['inkscape', str(svg), '--export-type=png', f'--export-width={width}',
                    f'--export-filename={out}'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def query(svg: Path) -> dict[str, tuple[float, float, float, float]]:
    """Inkscape's visual bounding boxes, by id: (x, y, width, height)."""
    result = subprocess.run(['inkscape', str(svg), '--query-all'], capture_output=True, text=True, check=True)
    boxes = {}
    for line in result.stdout.splitlines():
        parts = line.split(',')
        if len(parts) == 5:
            boxes[parts[0]] = tuple(float(value) for value in parts[1:])
    return boxes


def mark_parts(icon_svg: str) -> tuple[str, str, str, str]:
    """(defs, mark, outline, flat) from icon.svg: its filter and gradients,
    the three paths in their layer (shadow, bar, arrow), the bar and arrow
    unstyled for the watermark, and the bar and arrow in their colours
    without the shadow, for small sizes."""
    # Inkscape's own attributes (sodipodi:..., inkscape:...) would be unbound
    # prefixes in the files written here, so they go.
    icon_svg = re.sub(r'\s(?:sodipodi|inkscape):[\w-]+="[^"]*"', '', icon_svg)
    defs = re.search(r'<defs[^>]*>(.*?)</defs>', icon_svg, re.S).group(1)
    layer = re.search(r'(<g\s[^>]*id="layer1"[^>]*>)(.*?)</g>\s*</svg>', icon_svg, re.S)
    transform = re.search(r'transform="([^"]+)"', layer.group(1)).group(1)
    body = layer.group(2)
    mark = f'<g transform="{transform}">{body}</g>'
    paths = re.findall(r'<path\b.*?/>', body, re.S)
    flat = [p for p in paths if 'id="path26"' not in p]          # no shadow
    # The watermark draws the outline twice: no ids, so none repeats.
    outline = [re.sub(r'\s(?:style|id)="[^"]*"', '', p) for p in flat]
    return (defs, mark, f'<g transform="{transform}">{"".join(outline)}</g>',
            f'<g transform="{transform}">{"".join(flat)}</g>')


def write_tiles(defs: str, mark: str, box: tuple[float, float, float, float]) -> Path:
    """The Store tiles, one group each, sized exactly to the tile, so
    generate-assets.py finds them by size. The slate glow of logo.svg
    behind the mark; the mark a little left of centre, as logo.svg sets it
    (it points right, and centred it looks pushed)."""
    mark_x, mark_y, mark_w, mark_h = box
    groups, y = [], 0.0
    for name, width, height, share in TILES:
        size = share * min(width, height)
        scale = size / mark_w
        centre_x = width / 2 - 0.0835 * size
        tx = centre_x - (mark_x + mark_w / 2) * scale
        ty = y + height / 2 - (mark_y + mark_h / 2) * scale
        groups.append(
            f'<g id="g{name}">'
            f'<rect x="0" y="{y}" width="{width}" height="{height}" fill="url(#tileGlow)"/>'
            f'<use href="#mark" transform="translate({tx:.4f},{ty:.4f}) scale({scale:.6f})"/>'
            f'</g>')
        y += height + 20
    canvas_h = y - 20
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="620" height="{canvas_h}" viewBox="0 0 620 {canvas_h}">\n'
        f'<!-- Generated by packaging/branding/render-icons.py from icon.svg; do not edit. -->\n'
        f'<defs>{defs}\n'
        '<radialGradient id="tileGlow" cx="0.5" cy="0.42" r="0.79" gradientUnits="objectBoundingBox">'
        '<stop offset="0" stop-color="#566f84"/><stop offset="1" stop-color="#1a232b"/></radialGradient>\n'
        f'<g id="mark">{mark}</g></defs>\n'
        + '\n'.join(groups) + '\n</svg>\n')
    path = MSIX / 'tiles.svg'
    path.write_text(svg)
    return path


def write_watermark(outline: str, box: tuple[float, float, float, float]) -> Path:
    """The start screen's mark, cut into the background like a groove, as
    the old watermark was: the outline twice, a black one (10%) and a white
    one (5%) a pixel lower — the shadow and the light of an engraved edge.
    About as wide as the old one (260 px).

    The window centres it, but the start screen's open area is not the
    window: the bottom bar (113 px) is much taller than the title bar
    (37 px), which puts the area's middle 38 px higher, and a mark looks
    centred a little above the middle. A stylesheet cannot offset a
    background, so the file carries empty space under the mark: centred,
    the mark sits LIFT px higher."""
    x, y, width, height = box
    pad = 4
    scale = 1.2                     # drawn at 1.2x the icon's size
    lift = 48                       # px
    stroke = 1.0 / scale            # 1 px once drawn: the light
    shadow = 1.4 / scale            # 1.4 px: the shadow, as wide as the old one's
    below = 2 * lift / scale        # the empty space, in the mark's units
    view = f'{x - pad:.3f} {y - pad:.3f} {width + 2 * pad:.3f} {height + 2 * pad + below:.3f}'
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{(width + 2 * pad) * scale:.0f}" '
        f'height="{(height + 2 * pad) * scale + 2 * lift:.0f}" viewBox="{view}">\n'
        f'<!-- Generated by packaging/branding/render-icons.py from icon.svg; do not edit. -->\n'
        f'<g fill="none" stroke="#fff" stroke-opacity=".05" stroke-width="{stroke:.3f}" '
        f'stroke-linejoin="round" transform="translate(0,{1 / scale:.4f})">{outline}</g>\n'
        f'<g fill="none" stroke="#000" stroke-opacity=".1" stroke-width="{shadow:.3f}" '
        f'stroke-linejoin="round">{outline}</g>\n</svg>\n')
    path = GRAPHICS / 'background_watermark.svg'
    path.write_text(svg)
    return path


def write_small_mark(flat: str, box: tuple[float, float, float, float]) -> None:
    """The mark cropped to its edges, for beside text: 11 px tall, and a
    22 px @2x that Qt picks on high-DPI screens. No shadow: at this size it
    would only smudge the edges."""
    x, y, width, height = box
    source = HERE / '.mark.svg'
    source.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}" height="{height:.3f}" '
        f'viewBox="{x:.3f} {y:.3f} {width:.3f} {height:.3f}">{flat}</svg>\n')
    try:
        for name, size in (('subtitld_mark.png', 11), ('subtitld_mark@2x.png', 22)):
            subprocess.run(['inkscape', str(source), '--export-type=png', f'--export-height={size}',
                            f'--export-filename={GRAPHICS / name}'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(name)
    finally:
        source.unlink()


# The installer's icons: (size, mark width, badge diameter), as shares of the
# icon. The badge grows as the icon shrinks, so its glyph stays legible.
INSTALLER_LAYOUT = ((16, 0.80, 0.62), (24, 0.80, 0.58), (32, 0.80, 0.54), (48, 0.80, 0.50),
                    (64, 0.80, 0.47), (128, 0.80, 0.44), (256, 0.80, 0.44))

# A slate disc, and on it a pale arrow into a tray (install) or a red cross
# (remove), drawn on a 100-unit disc.
BADGE_GLYPHS = {
    'installer': ('<g fill="none" stroke="#b8cee0" stroke-width="11" stroke-linecap="round" '
                  'stroke-linejoin="round"><path d="M50 24V58"/><path d="M33 43 50 60 67 43"/>'
                  '<path d="M28 74H72"/></g>'),
    'uninstaller': ('<g fill="none" stroke="#ef5b5b" stroke-width="12" stroke-linecap="round">'
                    '<path d="M34 34 66 66"/><path d="M66 34 34 66"/></g>'),
}


def write_installer_icons(defs: str, mark: str, flat: str, box: tuple[float, float, float, float]) -> None:
    """The NSIS installer's and uninstaller's icons: the mark, up and to the
    right, with a badge in the lower left, below the bar. Not the lower
    right: Windows draws its admin shield there on the installer, which asks
    for elevation. Each size drawn at that size; from 48 px down, without
    the mark's soft shadow."""
    from PIL import Image

    x, y, width, _ = box
    out_dir = ROOT / 'packaging' / 'nsis'
    out_dir.mkdir(exist_ok=True)
    for name, glyph in BADGE_GLYPHS.items():
        renders = {}
        for size, mark_share, badge_share in INSTALLER_LAYOUT:
            scale = mark_share * size / width
            radius = badge_share * size / 2
            centre_x, centre_y = radius + 0.02 * size, size - radius - 0.02 * size
            unit = radius / 50
            source = HERE / f'.{name}-{size}.svg'
            source.write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 {size} {size}"><defs>{defs}'
                '<linearGradient id="badgeFill" x1="0" y1="0" x2="0" y2="1">'
                '<stop offset="0" stop-color="#3a4a58"/><stop offset="1" stop-color="#1a232b"/>'
                '</linearGradient></defs>'
                f'<g transform="translate({0.98 * size - (x + width) * scale:.3f},{0.02 * size - y * scale:.3f}) '
                f'scale({scale:.5f})">{mark if size > 48 else flat}</g>'
                f'<circle cx="{centre_x:.3f}" cy="{centre_y:.3f}" r="{radius:.3f}" fill="url(#badgeFill)" '
                f'stroke="#0c1014" stroke-width="{3 * unit:.3f}"/>'
                f'<g transform="translate({centre_x - 50 * unit:.3f},{centre_y - 50 * unit:.3f}) '
                f'scale({unit:.5f})">{glyph}</g></svg>\n')
            png = HERE / f'.{name}-{size}.png'
            try:
                inkscape_png(source, png, size)
                renders[size] = Image.open(png).convert('RGBA')
            finally:
                source.unlink()
                png.unlink(missing_ok=True)
        sizes = [size for size, _, _ in INSTALLER_LAYOUT]
        renders[max(sizes)].save(out_dir / f'{name}.ico', format='ICO', sizes=[(s, s) for s in sizes],
                                 append_images=[renders[s] for s in sizes if s != max(sizes)])
        print(f'packaging/nsis/{name}.ico')


def main() -> int:
    if shutil.which('inkscape') is None:
        sys.exit('Inkscape is needed on the PATH.')
    try:
        from PIL import Image
    except ImportError:
        sys.exit('Pillow is needed for the .ico.')

    icon = HERE / 'icon.svg'
    shutil.copyfile(icon, GRAPHICS / 'subtitld.svg')
    print('subtitld.svg')

    inkscape_png(icon, GRAPHICS / 'subtitld.png', 512)
    shutil.copyfile(GRAPHICS / 'subtitld.png', ROOT / 'snap' / 'gui' / 'icon.png')
    print('subtitld.png, snap/gui/icon.png')

    # Each .ico size drawn at that size, rather than scaled down from one.
    renders = {}
    for size in ICO_SIZES:
        out = GRAPHICS / f'.ico-{size}.png'
        inkscape_png(icon, out, size)
        renders[size] = Image.open(out).convert('RGBA')
    largest = renders[max(ICO_SIZES)]
    largest.save(GRAPHICS / 'subtitld.ico', format='ICO', sizes=[(s, s) for s in ICO_SIZES],
                 append_images=[renders[s] for s in ICO_SIZES if s != max(ICO_SIZES)])
    for size in ICO_SIZES:
        (GRAPHICS / f'.ico-{size}.png').unlink()
    print('subtitld.ico')

    text = icon.read_text()
    defs, mark, outline, flat = mark_parts(text)
    boxes = query(icon)
    # The bar and the arrow together, without the soft shadow.
    parts = [boxes['path22'], boxes['path23']]
    left = min(b[0] for b in parts)
    top = min(b[1] for b in parts)
    right = max(b[0] + b[2] for b in parts)
    bottom = max(b[1] + b[3] for b in parts)
    box = (left, top, right - left, bottom - top)

    print(write_watermark(outline, box).relative_to(ROOT))
    write_small_mark(flat, box)
    write_installer_icons(defs, mark, flat, box)
    tiles = write_tiles(defs, mark, box)
    print(tiles.relative_to(ROOT))
    subprocess.run([sys.executable, str(MSIX / 'generate-assets.py'), str(tiles), '--clean'], check=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
