#!/usr/bin/env python3
"""Render MSIX Store assets from a source SVG via Inkscape.

The source SVG arranges each target tile as a group sized at the
tile's base dimensions in millimetres. We auto-detect each group by
matching its bounding-box size, then export scale-100/125/150/200/400
and (for Square44x44) targetsize-16/24/32/48/256 variants.

Usage:
    python generate-assets.py path/to/source.svg [--clean]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ASSETS: list[tuple[str, int, int]] = [
    ('StoreLogo',          50,  50),
    ('Square44x44Logo',    44,  44),
    ('Square71x71Logo',    71,  71),
    ('Square150x150Logo',  150, 150),
    ('Square310x310Logo',  310, 310),
    ('Wide310x150Logo',    310, 150),
    ('SplashScreen',       620, 300),
]

SCALES = [100, 125, 150, 200, 400]
TARGETSIZES_44 = [16, 24, 32, 48, 256]


def query_all(svg: Path) -> list[tuple[str, float, float, float, float]]:
    result = subprocess.run(
        ['inkscape', str(svg), '--query-all'],
        capture_output=True, text=True, check=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split(',')
        if len(parts) != 5:
            continue
        try:
            rows.append((parts[0], float(parts[1]), float(parts[2]),
                         float(parts[3]), float(parts[4])))
        except ValueError:
            continue
    return rows


def find_group(rows, target_w: int, target_h: int) -> str:
    for obj_id, _x, _y, w, h in rows:
        if not obj_id.startswith('g') or obj_id in ('svg1', 'layer1'):
            continue
        if abs(w - target_w) < 0.5 and abs(h - target_h) < 0.5:
            return obj_id
    raise SystemExit(f'No group matching {target_w}x{target_h} in SVG')


def render(svg: Path, group_id: str, out: Path, width: int) -> None:
    subprocess.run(
        [
            'inkscape', str(svg),
            '--export-type=png',
            f'--export-id={group_id}',
            '--export-id-only',
            f'--export-width={width}',
            f'--export-filename={out}',
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('svg', type=Path)
    p.add_argument('--out', type=Path,
                   default=Path(__file__).parent / 'assets')
    p.add_argument('--clean', action='store_true',
                   help='Delete all PNGs in --out before regenerating')
    args = p.parse_args()

    if not args.svg.is_file():
        sys.exit(f'SVG not found: {args.svg}')

    args.out.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for png in args.out.glob('*.png'):
            png.unlink()

    rows = query_all(args.svg)
    mapping = {name: find_group(rows, bw, bh) for name, bw, bh in ASSETS}
    print('Source groups:', mapping)

    for name, bw, bh in ASSETS:
        gid = mapping[name]
        for scale in SCALES:
            # Half-up rounding (Microsoft's published tile sizes use it:
            # 50x1.25=62.5 -> 63, 71x1.5=106.5 -> 107). Python's built-in
            # round() is half-to-even and would produce 62 / 106.
            w = int(bw * scale / 100 + 0.5)
            out = args.out / f'{name}.scale-{scale}.png'
            render(args.svg, gid, out, w)
            print(f'  {out.name}')
        shutil.copyfile(
            args.out / f'{name}.scale-100.png',
            args.out / f'{name}.png',
        )

    for ts in TARGETSIZES_44:
        out = args.out / f'Square44x44Logo.targetsize-{ts}.png'
        render(args.svg, mapping['Square44x44Logo'], out, ts)
        print(f'  {out.name}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
