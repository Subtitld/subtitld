"""Strip MSIX-incompatible filenames from a PyInstaller staging directory.

MakeAppx rejects paths with '+' or square brackets (ERROR_INVALID_NAME
0x8007007b, without pointing at a specific file). Two known offenders
come from PyInstaller-bundled Python packages:

  1. vosk/libstdc++-6.dll — mingw C++ runtime bundled by pip's vosk
     wheel. We rename the file to libstdcxx-6.dll and patch every DLL
     and EXE in the tree that imports it to use the new name.

  2. docx/templates/default-docx-template/ — unpacked development
     template. python-docx uses default.docx (the zip) at runtime;
     the directory is safe to remove.

Same-length rename (15 chars both) lets us patch PE import tables with
a flat byte replace instead of rewriting the import directory.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

OLD = b"libstdc++-6.dll"
NEW = b"libstdcxx-6.dll"
assert len(OLD) == len(NEW), "rename must be length-preserving"


def patch_binary(path: Path) -> int:
    data = path.read_bytes()
    if OLD not in data:
        return 0
    count = data.count(OLD)
    path.write_bytes(data.replace(OLD, NEW))
    return count


def main(staging: Path) -> int:
    if not staging.is_dir():
        print(f"staging dir {staging} not found", file=sys.stderr)
        return 1

    docx_tpl = staging / "docx" / "templates" / "default-docx-template"
    if docx_tpl.exists():
        print(f"Removing unpacked docx template: {docx_tpl}")
        shutil.rmtree(docx_tpl)

    old_dll = staging / "vosk" / "libstdc++-6.dll"
    new_dll = staging / "vosk" / "libstdcxx-6.dll"
    if old_dll.exists():
        patched = 0
        for ext in ("*.dll", "*.exe", "*.pyd"):
            for binary in staging.rglob(ext):
                n = patch_binary(binary)
                if n:
                    print(f"  patched {n}x in {binary.relative_to(staging)}")
                    patched += 1
        print(f"Patched {patched} binaries; renaming {old_dll.name} -> {new_dll.name}")
        old_dll.rename(new_dll)

    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / "build" / "msix-staging"
    sys.exit(main(target))
