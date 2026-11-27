"""This computer's platform as add-on manifests and the catalog name it, and
the system packages an add-on needs on Haiku.

Manifests and catalog downloads say `linux`, `macos`, `windows` or `haiku`.
A Haiku add-on is not frozen (PyInstaller has no Haiku bootloader): it runs
on HaikuPorts' Python, and the large runtimes it needs (onnxruntime, PyTorch…)
are hpkgs, from HaikuPorts or from Subtitld's own repository. The manifest
lists them in `haiku_packages`; the installer checks they are active and
offers to install the missing ones with pkgman.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PLATFORM_KEYS = {
    'linux': 'linux',
    'darwin': 'macos',
    'win32': 'windows',
}

# Subtitld's own package repository: QtWebEngine, Subtitld itself, and the
# runtimes add-ons need that HaikuPorts doesn't have.
HPKG_REPO_URL = 'https://hpkg.subtitld.org'

# Where Haiku lists the packages it has activated, system-wide and per user.
_ACTIVATED_LISTS = (
    Path('/boot/system/packages/administrative/activated-packages'),
    Path('/boot/home/config/packages/administrative/activated-packages'),
)


def current_platform_key() -> str:
    # Haiku's Python says `haiku` (HaikuPorts' 3.14) or `haiku1` (older ones).
    if sys.platform.startswith('haiku'):
        return 'haiku'
    return PLATFORM_KEYS.get(sys.platform, sys.platform)


def is_haiku() -> bool:
    return current_platform_key() == 'haiku'


def _package_name(filename: str) -> str:
    """`onnxruntime_python3.14-1.31.0-1-x86_64.hpkg` → `onnxruntime_python3.14`.
    Haiku package names have no dashes; the version follows the first one."""
    return filename.strip().split('-', 1)[0]


def active_packages() -> set[str]:
    """Names of the packages Haiku has active, system-wide and for the user."""
    names = set()
    for listing in _ACTIVATED_LISTS:
        try:
            lines = listing.read_text(encoding='utf-8').splitlines()
        except OSError:
            # No list (nothing installed there yet): the folder's own files.
            folder = listing.parent.parent
            lines = [p.name for p in folder.glob('*.hpkg')] if folder.is_dir() else []
        names.update(_package_name(line) for line in lines if line.strip())
    return names


def missing_packages(manifest: dict) -> list[str]:
    """The `haiku_packages` an add-on needs that aren't active. Empty
    everywhere but on Haiku."""
    if not is_haiku():
        return []
    wanted = [str(name) for name in manifest.get('haiku_packages') or []]
    if not wanted:
        return []
    active = active_packages()
    return [name for name in wanted if name not in active]


def _run_pkgman(args: list[str], on_line: Callable[[str], None] | None) -> None:
    command = ['pkgman', *args]
    log.info('platforms: %s', ' '.join(command))
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors='replace')
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail = (tail + [line])[-8:]
        if on_line:
            on_line(line)
    if proc.wait() != 0:
        raise RuntimeError('\n'.join(tail) or f'pkgman {args[0]} failed ({proc.returncode})')


def _has_subtitld_repo() -> bool:
    try:
        listing = subprocess.run(['pkgman', 'list-repos'], capture_output=True, text=True,
                                 errors='replace', timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return HPKG_REPO_URL.split('//', 1)[1] in listing


def install_packages(names: list[str], on_line: Callable[[str], None] | None = None) -> None:
    """Install Haiku packages with pkgman, adding Subtitld's repository first
    when it isn't there. Blocks until pkgman ends; raises RuntimeError with
    pkgman's last lines when it fails."""
    if not names:
        return
    if not _has_subtitld_repo():
        _run_pkgman(['add-repo', HPKG_REPO_URL], on_line)
    _run_pkgman(['install', '-y', *names], on_line)
