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
    packaging/nsis/wizard.bmp                 the installer's Welcome and
                                              Finish pages: the mark and the
                                              name on the slate glow
    packaging/nsis/header.bmp                 the installer's other pages:
                                              the mark, in the header
    .github/readme/banner.png                 the top of the README: the
                                              mark, the name and a line
                                              on the slate glow
    packaging/msix/store-listing/tile-icon.png
    packaging/msix/store-listing/hero.png
                                              the Microsoft Store listing's
                                              app tile icon and hero art,
                                              uploaded in Partner Center
    packaging/snap/store-listing/icon.png
    packaging/snap/store-listing/banner.png
                                              the Snap Store listing's icon
                                              and featured banner, uploaded
                                              at snapcraft.io
    flatpak/icons/hicolor/...                 the .usfx project file's icon, for
    packaging/nsis/usfx.ico                   Linux, Windows (installer and
    packaging/msix/assets/UsfxFile*.png       MSIX), macOS and the app's own
    packaging/macos/usfx.icns                 recent files list
    src/subtitld/graphics/usfx_file*.png
    packaging/haiku/media-video/subtitld/additional-files/
        subtitld.hvif                         the Haiku icon (HVIF, Haiku's
                                              vector format), for HaikuDepot
        subtitld.rdef.in                      the launcher's resources, the
                                              icon included, for the recipe
        usfx.hvif, subtitld-filetypes.sh      the .usfx type's icon, and the
                                              script adding the type

Usage:  python packaging/branding/render-icons.py

Needs Inkscape on the PATH (a Snap Inkscape can only reach files under
your home folder, so keep the checkout there), Pillow for the .ico and
.icns, and fontTools for the label of the .usfx icon.
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

# The line under the name on the README's banner.
README_TAGLINE = 'Create, edit, transcribe, translate and dub subtitles'

# The height, as a share of the Microsoft Store's hero art, of the band its
# waveform, subtitles and mark run along; the Snap Store's banner is cut
# about it.
HERO_BAND = 0.40

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


def write_installer_images(defs: str, mark: str, flat: str, box: tuple[float, float, float, float]) -> None:
    """The installer's two pictures, at twice Modern UI's size (164 x 314 and
    150 x 57), so they stay sharp on high-DPI screens; NSIS fits them to the
    page. 24-bit BMPs, which is what it takes.

    wizard.bmp, beside the Welcome and Finish pages: the mark on the Store
    tiles' slate glow, the name below it in Montserrat Light, spaced as in
    the app's title. header.bmp, in the header of the other pages, which is
    white: the mark without its shadow, to the right."""
    from PIL import Image, ImageDraw, ImageFont

    x, y, width, height = box
    out_dir = ROOT / 'packaging' / 'nsis'
    out_dir.mkdir(exist_ok=True)

    def render(name: str, w: int, h: int, body: str) -> Image.Image:
        source = HERE / f'.{name}.svg'
        png = HERE / f'.{name}.png'
        source.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs>{defs}<radialGradient id="wizardGlow" cx="0.5" cy="0.36" r="0.75" '
            'gradientUnits="objectBoundingBox"><stop offset="0" stop-color="#566f84"/>'
            f'<stop offset="1" stop-color="#1a232b"/></radialGradient></defs>{body}</svg>\n')
        try:
            inkscape_png(source, png, w)
            return Image.open(png).convert('RGB')
        finally:
            source.unlink()
            png.unlink(missing_ok=True)

    # Beside the Welcome and Finish pages.
    w, h = 328, 628
    mark_w = 0.56 * w
    scale = mark_w / width
    tx = (w - mark_w) / 2 - 0.05 * mark_w - x * scale      # a little left: it points right
    ty = 0.36 * h - height * scale / 2 - y * scale
    image = render('wizard', w, h,
                   f'<rect width="{w}" height="{h}" fill="url(#wizardGlow)"/>'
                   f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{mark}</g>')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(GRAPHICS / 'Montserrat-Light.ttf'), 30)
    text, spacing = 'SUBTITLD', 0.34 * 30
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    cursor, baseline = (w - total) / 2, 0.36 * h + height * scale / 2 + 46
    for ch, advance in zip(text, widths):
        draw.text((cursor, baseline), ch, font=font, fill='#dfe8f0')
        cursor += advance + spacing
    image.save(out_dir / 'wizard.bmp')
    print('packaging/nsis/wizard.bmp')

    # The header.
    w, h = 300, 114
    mark_h = 0.62 * h
    scale = mark_h / height
    tx = w - 0.10 * h - width * scale - x * scale
    ty = (h - mark_h) / 2 - y * scale
    render('header', w, h,
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>'
           f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{flat}</g>'
           ).save(out_dir / 'header.bmp')
    print('packaging/nsis/header.bmp')


def write_store_listing(defs: str, mark: str, box: tuple[float, float, float, float]) -> None:
    """The pictures for the Microsoft Store listing, uploaded in Partner
    Center rather than packaged:

    tile-icon.png, 300 x 300: the 1:1 app tile icon, which the Store shows
    in place of the package's StoreLogo. The Store tiles' slate glow, the
    mark a little bigger than on the big tiles.

    hero.png, 3840 x 2160: the 16:9 super hero art, at the top of the
    listing and in promotions. The Store wants no text and no screenshots
    of the app in it, its key image in the centre (some layouts crop) and
    nothing that matters in the bottom third (a gradient may cover it). The
    mark, large, over a waveform and subtitle blocks in the timeline's
    speaker colours drifting across it, faint behind the mark."""
    import math
    import random

    x, y, width, height = box
    out = MSIX / 'store-listing'
    out.mkdir(exist_ok=True)

    def render(name: str, w: int, h: int, body: str) -> None:
        source = HERE / f'.{name}.svg'
        source.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs>{defs}</defs>{body}</svg>\n')
        try:
            inkscape_png(source, out / f'{name}.png', w)
        finally:
            source.unlink()
        print((out / f'{name}.png').relative_to(ROOT))

    def place(size: float, centre_x: float, centre_y: float) -> str:
        scale = size / width
        tx = centre_x - 0.0835 * size - (x + width / 2) * scale     # a little left: it points right
        ty = centre_y - (y + height / 2) * scale
        return f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{mark}</g>'

    glow = ('<radialGradient id="{id}" cx="{cx}" cy="{cy}" r="{r}" gradientUnits="objectBoundingBox">'
            '<stop offset="0" stop-color="#566f84"/><stop offset="1" stop-color="#1a232b"/></radialGradient>')

    w = h = 300
    render('tile-icon', w, h,
           glow.format(id='tileIconGlow', cx=0.5, cy=0.42, r=0.79)
           + f'<rect width="{w}" height="{h}" fill="url(#tileIconGlow)"/>{place(0.5 * w, w / 2, h / 2)}')

    # The hero: everything that matters on a band at 40% of the height.
    w, h = 3840, 2160
    band = HERO_BAND * h
    rng = random.Random(26)                    # the same picture every run
    layers = []

    # The waveform: a mirrored envelope of a few sines and some grain,
    # fading out to the sides.
    steps = 480
    tops = []
    for k in range(steps + 1):
        t = k / steps
        fade = math.sin(math.pi * t) ** 0.6
        level = (0.55 + 0.25 * math.sin(t * 23.0) + 0.20 * math.sin(t * 61.0 + 1.3)) * (0.6 + 0.4 * rng.random())
        tops.append((t * w, band - fade * level * 0.17 * h))
    outline = ' '.join(f'{px:.1f},{py:.1f}' for px, py in tops)
    mirrored = ' '.join(f'{px:.1f},{2 * band - py:.1f}' for px, py in reversed(tops))
    layers.append(f'<polygon points="{outline} {mirrored}" fill="#8fb0c9" fill-opacity="0.16"/>')

    # Subtitle blocks along it, as the timeline draws them: pale, with the
    # speaker's colour along the top, further ones fainter.
    colours = ('#ef6c95', '#4e9cf0', '#ef6c95', '#55d43f', '#4e9cf0', '#f0b94e', '#4e9cf0', '#ef6c95')
    cursor = 0.03 * w
    for k, colour in enumerate(colours):
        block_w = rng.uniform(0.07, 0.14) * w
        block_h = 0.15 * h
        top = band - block_h / 2 + rng.uniform(-0.05, 0.05) * h
        # Faint behind the mark and at the edges, clearest between.
        distance = abs((cursor + block_w / 2) / w - 0.5) * 2            # 0 in the middle, 1 at the sides
        opacity = 0.06 + 0.34 * math.sin(math.pi * min(1.0, distance * 1.15))
        layers.append(
            f'<g opacity="{opacity:.3f}"><rect x="{cursor:.1f}" y="{top:.1f}" width="{block_w:.1f}" '
            f'height="{block_h:.1f}" rx="10" fill="#b8cee0"/>'
            f'<rect x="{cursor:.1f}" y="{top:.1f}" width="{block_w:.1f}" height="12" rx="4" fill="{colour}"/></g>')
        cursor += block_w + rng.uniform(0.012, 0.05) * w
        if cursor > 0.97 * w:
            break

    # A playhead on the left, and the mark over it all.
    layers.append(f'<rect x="{0.29 * w:.1f}" y="{band - 0.2 * h:.1f}" width="6" height="{0.4 * h:.1f}" '
                  'fill="#ef5b5b" fill-opacity="0.5"/>')
    render('hero', w, h,
           glow.format(id='heroGlow', cx=0.5, cy=0.40, r=0.75)
           + f'<rect width="{w}" height="{h}" fill="url(#heroGlow)"/>' + ''.join(layers)
           + place(0.34 * h, w / 2, band))


def write_snap_listing(defs: str, mark: str, box: tuple[float, float, float, float]) -> None:
    """The pictures for the Snap Store listing, uploaded on the snap's
    Listing page at snapcraft.io rather than packaged:

    icon.png, 512 x 512: the store's icon, on snapcraft.io and in Ubuntu's
    App Center. The desktop's icon (snap/gui/icon.png) is the mark alone,
    whose pale bar fades on the store's white pages: here the mark is on the
    Store tiles' slate glow, a rounded square.

    banner.png, 3840 x 1280: the featured banner, 3:1. The Microsoft
    Store's hero art, which write_store_listing draws first, cut about its
    band: the mark over the waveform and the subtitle blocks, centred."""
    from PIL import Image

    x, y, width, height = box
    out = ROOT / 'packaging' / 'snap' / 'store-listing'
    out.mkdir(parents=True, exist_ok=True)

    # The icon: as the Microsoft Store's tile icon, on a rounded square.
    w = h = 512
    inset, radius = 8, 104
    size = 0.5 * (w - 2 * inset)
    scale = size / width
    tx = w / 2 - 0.0835 * size - (x + width / 2) * scale     # a little left: it points right
    ty = h / 2 - (y + height / 2) * scale
    source = HERE / '.snap-icon.svg'
    source.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<defs>{defs}<radialGradient id="snapIconGlow" cx="0.5" cy="0.42" r="0.79" '
        'gradientUnits="objectBoundingBox"><stop offset="0" stop-color="#566f84"/>'
        '<stop offset="1" stop-color="#1a232b"/></radialGradient></defs>'
        f'<rect x="{inset}" y="{inset}" width="{w - 2 * inset}" height="{h - 2 * inset}" rx="{radius}" '
        'fill="url(#snapIconGlow)"/>'
        f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{mark}</g></svg>\n')
    try:
        inkscape_png(source, out / 'icon.png', w)
    finally:
        source.unlink()
    print((out / 'icon.png').relative_to(ROOT))

    # The banner.
    hero = Image.open(MSIX / 'store-listing' / 'hero.png').convert('RGB')
    w, h = hero.width, hero.width // 3
    band = round(HERO_BAND * hero.height)
    hero.crop((0, band - h // 2, w, band + h // 2)).save(out / 'banner.png', optimize=True)
    print((out / 'banner.png').relative_to(ROOT))


def text_outline(text: str, font_file: Path, height: float, tracking: float) -> tuple[str, float]:
    """`text` as SVG paths from a font's outlines, its capitals `height`
    tall, letters `tracking` (a share of the height) apart: the file needs
    no font where it is drawn. Returns (paths, width)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_file))
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    scale = height / font['OS/2'].sCapHeight
    x, paths = 0.0, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        paths.append(f'<path transform="translate({x:.2f},{height:.2f}) scale({scale:.5f},{-scale:.5f})" '
                     f'd="{pen.getCommands()}"/>')
        x += glyphs[name].width * scale + tracking * height
    return ''.join(paths), x - tracking * height


# The .usfx document icon's sizes: (size, with its label). Below 48 px the
# label cannot be read: the mark alone, larger, without its shadow.
USFX_PNG_SIZES = ((16, False), (24, False), (32, False), (48, True), (64, True), (128, True), (256, True))
USFX_ICON_NAME = 'org.subtitld.Subtitld.usfx'      # Flatpak exports only icons named after the app


def usfx_svg(defs: str, mark: str, flat: str, box: tuple[float, float, float, float], label: bool) -> str:
    """The .usfx icon on a 256 canvas: a page with its corner folded, in
    the Store tiles' slate glow, the mark on it and, large, "USFX" below."""
    x, y, width, height = box
    if label:
        x0, y0, x1, y1, fold, r = 34, 10, 222, 246, 58, 16
        mark_w, mark_cy, the_mark = 112, 104, mark
    else:
        x0, y0, x1, y1, fold, r = 26, 4, 230, 252, 64, 18
        mark_w, mark_cy, the_mark = 150, 140, flat
    page = (f'M{x0 + r},{y0} H{x1 - fold} L{x1},{y0 + fold} V{y1 - r} Q{x1},{y1} {x1 - r},{y1} '
            f'H{x0 + r} Q{x0},{y1} {x0},{y1 - r} V{y0 + r} Q{x0},{y0} {x0 + r},{y0} Z')
    fold_d = f'M{x1 - fold},{y0} V{y0 + fold - 10} Q{x1 - fold},{y0 + fold} {x1 - fold + 10},{y0 + fold} H{x1} Z'
    scale = mark_w / width
    cx = (x0 + x1) / 2
    tx = cx - 0.0835 * mark_w - (x + width / 2) * scale      # a little left: it points right
    ty = mark_cy - (y + height / 2) * scale
    body = [
        '<radialGradient id="pageGlow" cx="0.5" cy="0.36" r="0.8" gradientUnits="objectBoundingBox">'
        '<stop offset="0" stop-color="#5d778c"/><stop offset="1" stop-color="#1a232b"/></radialGradient>',
        '<linearGradient id="foldFill" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#a9bccb"/><stop offset="1" stop-color="#7d93a5"/></linearGradient>',
        f'<path d="{page}" fill="url(#pageGlow)" stroke="#0b1015" stroke-opacity="0.55" stroke-width="3"/>',
        f'<path d="M{x1 - fold},{y0 + fold} L{x1},{y0 + fold} L{x1},{y0 + fold + 14} Z" fill="#000" fill-opacity="0.25"/>',
        f'<path d="{fold_d}" fill="url(#foldFill)"/>',
        f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{the_mark}</g>',
    ]
    if label:
        letters, text_w = text_outline('USFX', GRAPHICS / 'Montserrat-SemiBold.ttf', 30, 0.16)
        body.append(f'<rect x="{x0 + 18}" y="182" width="{x1 - x0 - 36}" height="46" rx="8" fill="#0b1015" fill-opacity="0.45"/>')
        body.append(f'<g fill="#dfe8f0" transform="translate({cx - text_w / 2:.2f},190)">{letters}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">\n'
            f'<!-- Generated by packaging/branding/render-icons.py from icon.svg; do not edit. -->\n'
            f'<defs>{defs}</defs>{"".join(body)}</svg>\n')


def write_usfx_icons(defs: str, mark: str, flat: str, box: tuple[float, float, float, float]) -> None:
    """The icon of Subtitld's project files, .usfx, wherever the system
    shows one:

    flatpak/icons/hicolor/...      the MIME type's icon on Linux (Flatpak,
                                   AUR, AppImage): scalable, and drawn at
                                   16-48 px
    packaging/nsis/usfx.ico        Windows, for the installer's file type
    packaging/msix/assets/UsfxFile.targetsize-*.png
                                   the MSIX package's file type
    packaging/macos/usfx.icns      the macOS document type
    src/subtitld/graphics/usfx_file.png (and @2x)
                                   Subtitld's own recent files list
    """
    from PIL import Image

    sources = {}
    for label in (True, False):
        source = HERE / f'.usfx-{"large" if label else "small"}.svg'
        source.write_text(usfx_svg(defs, mark, flat, box, label))
        sources[label] = source
    renders = {}
    try:
        for size, label in USFX_PNG_SIZES + ((512, True), (1024, True)):
            png = HERE / f'.usfx-{size}.png'
            inkscape_png(sources[label], png, size)
            renders[size] = Image.open(png).convert('RGBA')
            png.unlink()
        hicolor = ROOT / 'flatpak' / 'icons' / 'hicolor'
        scalable = hicolor / 'scalable' / 'mimetypes' / f'{USFX_ICON_NAME}.svg'
        scalable.parent.mkdir(parents=True, exist_ok=True)
        scalable.write_text(sources[True].read_text())
        for size in (16, 24, 32, 48):
            out = hicolor / f'{size}x{size}' / 'mimetypes' / f'{USFX_ICON_NAME}.png'
            out.parent.mkdir(parents=True, exist_ok=True)
            renders[size].save(out, optimize=True)
        print('flatpak/icons/hicolor/*/mimetypes/' + USFX_ICON_NAME)

        ico_sizes = [size for size, _ in USFX_PNG_SIZES]
        renders[256].save(ROOT / 'packaging' / 'nsis' / 'usfx.ico', format='ICO', sizes=[(s, s) for s in ico_sizes],
                          append_images=[renders[s] for s in ico_sizes if s != 256])
        print('packaging/nsis/usfx.ico')

        assets = MSIX / 'assets'
        for size in (16, 24, 32, 48, 256):
            renders[size].save(assets / f'UsfxFile.targetsize-{size}.png', optimize=True)
            # Explorer draws file type icons without these small, on a page.
            renders[size].save(assets / f'UsfxFile.targetsize-{size}_altform-unplated.png', optimize=True)
        renders[48].save(assets / 'UsfxFile.png', optimize=True)
        print('packaging/msix/assets/UsfxFile*.png')

        macos = ROOT / 'packaging' / 'macos'
        macos.mkdir(exist_ok=True)
        renders[1024].save(macos / 'usfx.icns', format='ICNS',
                           append_images=[renders[s] for s in (16, 32, 64, 128, 256, 512)])
        print('packaging/macos/usfx.icns')

        renders[16].save(GRAPHICS / 'usfx_file.png', optimize=True)
        renders[32].save(GRAPHICS / 'usfx_file@2x.png', optimize=True)
        print('usfx_file.png, usfx_file@2x.png')
    finally:
        for source in sources.values():
            source.unlink(missing_ok=True)


def write_readme_banner(defs: str, mark: str, box: tuple[float, float, float, float]) -> None:
    """The banner at the top of the README: the installer's picture laid out
    wide. The mark on the slate glow, the name beside it in Montserrat Light,
    spaced as in the app's title, and a line on what Subtitld does below.
    Twice the size it is shown at, with rounded corners."""
    from PIL import Image, ImageDraw, ImageFont

    x, y, width, height = box
    w, h, radius = 1760, 440, 28
    out = ROOT / '.github' / 'readme' / 'banner.png'
    out.parent.mkdir(parents=True, exist_ok=True)

    name_font = ImageFont.truetype(str(GRAPHICS / 'Montserrat-Light.ttf'), 112)
    name, name_spacing = 'SUBTITLD', 0.34 * 112
    line_font = ImageFont.truetype(str(GRAPHICS / 'Montserrat-Regular.ttf'), 38)
    line = README_TAGLINE
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    name_widths = [probe.textlength(ch, font=name_font) for ch in name]
    name_w = sum(name_widths) + name_spacing * (len(name) - 1)
    line_w = probe.textlength(line, font=line_font)

    # The mark and the text side by side, centred together.
    mark_h = 0.52 * h
    scale = mark_h / height
    mark_w = width * scale
    gap = 0.38 * mark_h
    left = (w - (mark_w + gap + max(name_w, line_w))) / 2
    tx = left - x * scale
    ty = (h - mark_h) / 2 - y * scale

    source, png = HERE / '.banner.svg', HERE / '.banner.png'
    source.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<defs>{defs}<radialGradient id="bannerGlow" cx="{(left + mark_w / 2) / w:.3f}" cy="0.45" '
        f'r="0.8" gradientUnits="objectBoundingBox">'
        '<stop offset="0" stop-color="#566f84"/><stop offset="1" stop-color="#1a232b"/>'
        '</radialGradient></defs>'
        f'<rect width="{w}" height="{h}" fill="url(#bannerGlow)"/>'
        f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">{mark}</g></svg>\n')
    try:
        inkscape_png(source, png, w)
        image = Image.open(png).convert('RGBA')
    finally:
        source.unlink()
        png.unlink(missing_ok=True)

    # The name's capitals and the line below, as one block on the mark's
    # middle.
    draw = ImageDraw.Draw(image)
    cap = name_font.getbbox('S', anchor='ls')[1] * -1
    line_cap = line_font.getbbox('C', anchor='ls')[1] * -1
    between = 0.62 * cap
    name_base = h / 2 - (cap + between + line_cap) / 2 + cap
    cursor = left + mark_w + gap
    for ch, advance in zip(name, name_widths):
        draw.text((cursor, name_base), ch, font=name_font, fill='#dfe8f0', anchor='ls')
        cursor += advance + name_spacing
    draw.text((left + mark_w + gap + 4, name_base + between + line_cap), line, font=line_font,
              fill='#9fb3c4', anchor='ls')

    # Rounded corners, drawn large and scaled down so their edge is smooth.
    mask = Image.new('L', (w * 4, h * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), radius * 4, fill=255)
    image.putalpha(mask.resize((w, h), Image.LANCZOS))
    image.save(out, optimize=True)
    print(out.relative_to(ROOT))


# Haiku's vector icon format, HVIF: as much of it as the mark needs. Solid
# colours; closed paths of lines and cubic curves on a 64 x 64 canvas;
# shapes, which fill paths with a style, optionally through a stroke.
HVIF_STYLE_COLOR, HVIF_STYLE_COLOR_NO_ALPHA = 1, 3
HVIF_SHAPE_PATH_SOURCE = 10
HVIF_TRANSFORMER_STROKE = 23
HVIF_PATH_CLOSED, HVIF_PATH_NO_CURVES = 1 << 1, 1 << 3
HVIF_SHAPE_HAS_TRANSFORMERS = 1 << 4
HVIF_ROUND = 2                      # round join, round cap


def svg_contours(d: str, matrix: tuple[float, ...]) -> list[list[list[float]]]:
    """The closed contours of an SVG path (M, L, H, V, C, Z, absolute or
    relative), mapped through matrix (a, b, c, d, e, f), as HVIF points:
    [x, y, x_in, y_in, x_out, y_out], the controls of the curves into and
    out of each point."""
    tokens = re.findall(r'[MmLlHhVvCcZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
    contours: list[list[list[float]]] = []
    nodes: list[list[float]] = []
    x = y = start_x = start_y = 0.0
    command, i = 'M', 0

    def number() -> float:
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    def close() -> None:
        nonlocal nodes
        if len(nodes) > 1 and abs(nodes[-1][0] - nodes[0][0]) < 1e-6 and abs(nodes[-1][1] - nodes[0][1]) < 1e-6:
            nodes[0][2:4] = nodes[-1][2:4]          # the last point is the first again
            nodes.pop()
        if nodes:
            contours.append(nodes)
        nodes = []

    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command in 'Zz':
                close()
                x, y = start_x, start_y
                continue
        rel = command.islower()
        dx, dy = (x, y) if rel else (0.0, 0.0)
        kind = command.upper()
        if kind == 'M':
            close()
            x, y = number() + dx, number() + dy
            start_x, start_y = x, y
            nodes = [[x, y, x, y, x, y]]
            command = 'l' if rel else 'L'           # further pairs are lines
        elif kind in 'LHV':
            nx = number() + dx if kind in 'LH' else x
            ny = number() + dy if kind in 'LV' else y
            x, y = nx, ny
            nodes.append([x, y, x, y, x, y])
        elif kind == 'C':
            x1, y1, x2, y2, nx, ny = (number() + (dx, dy)[k % 2] for k in range(6))
            nodes[-1][4:6] = [x1, y1]
            x, y = nx, ny
            nodes.append([x, y, x2, y2, x, y])
    close()

    a, b, c, dd, e, f = matrix
    return [[[a * p[k] + c * p[k + 1] + e if j == 0 else b * p[k] + dd * p[k + 1] + f
              for k in (0, 2, 4) for j in (0, 1)] for p in contour] for contour in contours]


def hvif_coord(value: float) -> bytes:
    """A coordinate: one byte for a whole number from -32 to 95, else two
    (a 1/102 resolution from -128 to 192)."""
    if value == int(value) and -32 <= value <= 95:
        return bytes([int(value) + 32])
    if not -128 <= value <= 192:
        raise ValueError(f'HVIF coordinate out of range: {value}')
    stored = round((value + 128) * 102)
    return bytes([(stored >> 8) | 0x80, stored & 0xff])


def hvif_path(points: list[list[float]]) -> bytes:
    straight = all(p[0:2] == p[2:4] == p[4:6] for p in points)
    data = bytes([HVIF_PATH_CLOSED | (HVIF_PATH_NO_CURVES if straight else 0), len(points)])
    for point in points:
        for value in (point[:2] if straight else point):
            data += hvif_coord(round(value, 2))
    return data


def hvif_shape(style: int, paths: list[int], stroke: int = 0) -> bytes:
    """A shape: the paths filled with the style, or, with stroke, outlined
    stroke units wide (round joins and caps)."""
    data = bytes([HVIF_SHAPE_PATH_SOURCE, style, len(paths), *paths,
                  HVIF_SHAPE_HAS_TRANSFORMERS if stroke else 0])
    if stroke:
        data += bytes([1, HVIF_TRANSFORMER_STROKE, stroke + 128, HVIF_ROUND | HVIF_ROUND << 4, 4])
    return data


def write_haiku_icon(icon_svg: str) -> None:
    """The Haiku icon, from icon.svg's three paths: the shadow, flat and
    faint (HVIF has no blur), and the bar and the arrow, each in its colour
    over a darker outline, the way Haiku's icons are drawn, which also keeps
    the pale bar visible on Haiku's light backgrounds. Written as an .hvif,
    and into the launcher's resources (subtitld.rdef.in, for the recipe)."""
    def element(path_id: str) -> tuple[str, tuple[float, ...]]:
        tag = re.search(rf'<path\b[^>]*\bid="{path_id}"[^>]*>', icon_svg, re.S).group(0)
        transform = re.search(r'\stransform="matrix\(([^)]+)\)"', tag)
        matrix = tuple(float(v) for v in re.split(r'[\s,]+', transform.group(1).strip())) if transform \
            else (1, 0, 0, 1, 0, 0)
        return re.search(r'\sd="([^"]+)"', tag).group(1), matrix

    layer = re.search(r'<g\b[^>]*\bid="layer1"[^>]*>', icon_svg, re.S).group(0)
    tx, ty = (float(v) for v in re.search(r'translate\(([^,]+),([^)]+)\)', layer).groups())
    canvas = 64 / float(re.search(r'<svg\b[^>]*\bwidth="([\d.]+)"', icon_svg, re.S).group(1))

    def to_canvas(matrix: tuple[float, ...]) -> tuple[float, ...]:
        a, b, c, d, e, f = matrix                   # then the layer's translation, then the scale
        return (a * canvas, b * canvas, c * canvas, d * canvas, (e + tx) * canvas, (f + ty) * canvas)

    shadow, bar, arrow = (svg_contours(d, to_canvas(m)) for d, m in map(element, ('path26', 'path22', 'path23')))
    paths = shadow + bar + arrow
    shadow_ids = list(range(len(shadow)))
    bar_ids = list(range(len(shadow), len(shadow) + len(bar)))
    arrow_ids = list(range(len(shadow) + len(bar), len(paths)))

    styles = [
        bytes([HVIF_STYLE_COLOR, 0, 0, 0, 44]),                 # the shadow, as faint as icon.svg's
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x5b, 0x70, 0x83]),   # the bar's outline
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0xb8, 0xce, 0xe0]),   # the bar
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x2a, 0x84, 0x1d]),   # the arrow's outline
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x55, 0xd4, 0x3f]),   # the arrow
    ]
    shapes = [
        hvif_shape(0, shadow_ids),
        hvif_shape(1, bar_ids, stroke=2), hvif_shape(2, bar_ids),
        hvif_shape(3, arrow_ids, stroke=2), hvif_shape(4, arrow_ids),
    ]
    hvif = (b'ncif' + bytes([len(styles)]) + b''.join(styles)
            + bytes([len(paths)]) + b''.join(hvif_path(p) for p in paths)
            + bytes([len(shapes)]) + b''.join(shapes))

    out = ROOT / 'packaging' / 'haiku' / 'media-video' / 'subtitld' / 'additional-files'
    (out / 'subtitld.hvif').write_bytes(hvif)
    write_haiku_usfx_type(bar, arrow, out)
    hex_lines = '\n'.join(f'\t$"{hvif[k:k + 32].hex().upper()}"' for k in range(0, len(hvif), 32))
    (out / 'subtitld.rdef.in').write_text(
        '/* Subtitld\'s launcher on Haiku: its signature, version and icon.\n'
        '   Generated by packaging/branding/render-icons.py; the recipe fills in\n'
        '   the version. */\n\n'
        # Qt names a Haiku application "application/x-vnd.qt6-" and its file
        # name; the resources must say the same.
        'resource app_signature "application/x-vnd.qt6-Subtitld";\n\n'
        'resource app_flags B_MULTIPLE_LAUNCH;\n\n'
        'resource app_version {\n'
        '\tmajor = @MAJOR@,\n\tmiddle = @MIDDLE@,\n\tminor = @MINOR@,\n\n'
        '\tvariety = B_APPV_FINAL,\n\tinternal = 0,\n\n'
        '\tshort_info = "Subtitld",\n'
        '\tlong_info = "Subtitld: create, edit, transcribe, translate and dub subtitles"\n'
        '};\n\n'
        f'resource vector_icon array {{\n{hex_lines}\n}};\n\n'
        # What Tracker offers Subtitld for, in "Open with".
        '/* The types it opens. */\n'
        'resource file_types message {\n'
        + ',\n'.join(f'\t"types" = "{mime}"' for mime in HAIKU_FILE_TYPES)
        + '\n};\n')
    print(f'subtitld.hvif ({len(hvif)} bytes), subtitld.rdef.in')


# The types Subtitld offers to open on Haiku: its projects, the subtitle
# formats with a type of their own, and subtitld:// links (subtitld.cc's
# "Open in Subtitld"; Haiku opens a URL with its scheme's type's app).
HAIKU_FILE_TYPES = ('application/x-usfx', 'application/x-subrip', 'text/vtt', 'text/x-ssa',
                    'application/x-vnd.Be.URL.subtitld')


def write_haiku_usfx_type(bar: list, arrow: list, out: Path) -> None:
    """Subtitld's projects as a file type of their own on Haiku: their icon
    in HVIF (a page, its corner folded, the mark on it, as on the other
    systems), and the script the package runs once installed to add the type
    to Haiku's MIME database with setmime (subtitld-filetypes.sh), the icon in
    it."""
    points = [p for contour in bar + arrow for p in contour]
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    top, bottom = min(p[1] for p in points), max(p[1] for p in points)
    scale = 28 / (right - left)
    cx, cy = (left + right) / 2, (top + bottom) / 2

    def placed(contours: list) -> list:
        # The mark on the page: 28 units wide, a little above its middle,
        # a little left (it points right).
        return [[[(30.5 + (p[k] - cx) * scale) if k % 2 == 0 else (35 + (p[k] - cy) * scale) for k in range(6)]
                 for p in contour] for contour in contours]

    def straight(*xy: tuple[float, float]) -> list:
        return [[x, y, x, y, x, y] for x, y in xy]

    page = straight((11, 4), (40, 4), (53, 17), (53, 60), (11, 60))
    fold = straight((40, 4), (40, 17), (53, 17))
    bar_on, arrow_on = placed(bar), placed(arrow)
    paths = [page, fold] + bar_on + arrow_on
    bar_ids = list(range(2, 2 + len(bar_on)))
    arrow_ids = list(range(2 + len(bar_on), len(paths)))
    styles = [
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x16, 0x1e, 0x25]),   # the page's outline
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x3a, 0x4c, 0x5a]),   # the page, slate
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0xa9, 0xbc, 0xcb]),   # its folded corner
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x5b, 0x70, 0x83]),   # the bar's outline
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0xb8, 0xce, 0xe0]),   # the bar
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x2a, 0x84, 0x1d]),   # the arrow's outline
        bytes([HVIF_STYLE_COLOR_NO_ALPHA, 0x55, 0xd4, 0x3f]),   # the arrow
    ]
    shapes = [
        hvif_shape(0, [0], stroke=2), hvif_shape(1, [0]), hvif_shape(2, [1]),
        hvif_shape(3, bar_ids, stroke=2), hvif_shape(4, bar_ids),
        hvif_shape(5, arrow_ids, stroke=2), hvif_shape(6, arrow_ids),
    ]
    hvif = (b'ncif' + bytes([len(styles)]) + b''.join(styles)
            + bytes([len(paths)]) + b''.join(hvif_path(p) for p in paths)
            + bytes([len(shapes)]) + b''.join(shapes))
    (out / 'usfx.hvif').write_bytes(hvif)
    (out / 'subtitld-filetypes.sh').write_text(
        '#!/bin/sh\n'
        '# Subtitld\'s projects (.usfx) as a file type of their own: a description,\n'
        '# the extension, Subtitld to open them, and their icon (usfx.hvif).\n'
        '# Run once the package is installed (POST_INSTALL_SCRIPTS in the recipe).\n'
        '# Generated by packaging/branding/render-icons.py; do not edit.\n'
        '#\n'
        '# The package already carries the type, read-only, with only Subtitld as its\n'
        '# preferred app (data/mime_db, from the file types the app declares), so\n'
        '# -add lays the rest over it in the user\'s MIME database (as on a\n'
        '# reinstall); -force would try to replace the read-only entry and fail.\n'
        '# -set if the type is not there at all. The sniffer rule: a zip whose first\n'
        '# entry is Subtitld\'s, above the zip\'s own rule (0.40), which would\n'
        '# otherwise win over the extension.\n'
        'if setmime -dump application/x-usfx > /dev/null 2>&1; then mode=-add; else mode=-set; fi\n'
        'setmime $mode application/x-usfx \\\n'
        '\t-short "Subtitld project" \\\n'
        '\t-long "Subtitld project: subtitles, speakers and dubs" \\\n'
        '\t-extension usfx \\\n'
        '\t-sniffRule \'0.60 ("PK\\003\\004") [30] ("subtitles.usf" | "manifest.xml")\' \\\n'
        '\t-preferredAppSig application/x-vnd.qt6-Subtitld \\\n'
        f'\t-vectorIcon {hvif.hex().upper()}\n')
    print(f'usfx.hvif ({len(hvif)} bytes), subtitld-filetypes.sh')


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
    write_installer_images(defs, mark, flat, box)
    write_readme_banner(defs, mark, box)
    write_store_listing(defs, mark, box)
    write_snap_listing(defs, mark, box)
    write_haiku_icon(text)
    tiles = write_tiles(defs, mark, box)
    print(tiles.relative_to(ROOT))
    subprocess.run([sys.executable, str(MSIX / 'generate-assets.py'), str(tiles), '--clean'], check=True)
    # After the tiles: generate-assets.py --clean empties the MSIX assets.
    write_usfx_icons(defs, mark, flat, box)
    return 0


if __name__ == '__main__':
    sys.exit(main())
