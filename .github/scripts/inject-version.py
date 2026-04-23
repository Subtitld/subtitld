"""Inject a version string into every file that carries a hardcoded copy.

`src/subtitld/__init__.py` is the authoritative version (pyproject.toml and
snapcraft.yaml both read from it). Other files — PyInstaller specs, plist
entries, AppImage builder manifests — duplicate the value and need to be
kept in sync at build time so artifacts ship with a consistent version.

Usage: python inject-version.py <version>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TARGETS: list[tuple[str, str, str]] = [
    ('src/subtitld/__init__.py',
        r"^__version__ = '[^']*'",
        "__version__ = '{v}'"),
    ('subtitld-macos.spec',
        r"version='[^']*'",
        "version='{v}'"),
    ('subtitld-macos.spec',
        r"'CFBundleShortVersionString': '[^']*'",
        "'CFBundleShortVersionString': '{v}'"),
    ('subtitld-macos.spec',
        r"'CFBundleVersion': '[^']*'",
        "'CFBundleVersion': '{v}'"),
    ('AppImageBuilder.yml',
        r"^(\s*version:\s*)[0-9][0-9A-Za-z._-]*",
        r"\g<1>{v}"),
]


def main(version: str) -> int:
    for path, pattern, replacement in TARGETS:
        p = Path(path)
        if not p.exists():
            continue
        text = p.read_text()
        new = re.sub(pattern, replacement.format(v=version), text, flags=re.MULTILINE)
        if new != text:
            p.write_text(new)
            print(f"  patched {path}")
        else:
            print(f"  no change in {path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: inject-version.py <version>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
