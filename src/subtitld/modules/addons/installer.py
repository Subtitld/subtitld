"""Catalog fetching + add-on install/uninstall.

The catalog is a single JSON file hosted on subtitld.org. v0 verifies
sha256 of each downloaded zip but does NOT cryptographically verify the
catalog itself — that's a v1 milestone (ed25519 with a key embedded in the
Subtitld binary).

Layout (post-install):
    <PATH_SUBTITLD_ADDONS>/<addon_id>/manifest.json
    <PATH_SUBTITLD_ADDONS>/<addon_id>/bin/<exe>
    <PATH_SUBTITLD_ADDONS>/<addon_id>/...

Install is atomic: we extract into `<addon_id>/_install/` then rename in
place. Half-extracted zips (network drop, disk full) never become a "live"
add-on directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from subtitld.modules import session

log = logging.getLogger(__name__)

import os as _os

# Production catalog: served by GitHub Pages out of Subtitld/addons-catalog
# (auto-rebuilt on every add-on release). When/if a CNAME like
# `addons.subtitld.org → subtitld.github.io` lands, swap this string —
# clients pick up the new URL on next launch (no schema change needed).
#
# Allow developers to point at a local mock catalog without editing source.
# `serve_local.py` in the catalog scaffold uses this when iterating on the
# install flow.
DEFAULT_CATALOG_URL = (
    _os.environ.get('SUBTITLD_ADDONS_CATALOG_URL')
    or 'https://subtitld.github.io/addons-catalog/catalog.json'
)
CATALOG_CACHE_TTL_SEC = 3600.0  # 1 h

ProgressCallback = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def _catalog_cache_path() -> Path:
    return Path(session.PATH_SUBTITLD_USER_CACHE) / 'catalog.json'


def fetch_catalog(force_refresh: bool = False, url: str = DEFAULT_CATALOG_URL,
                  timeout: float = 15.0) -> dict:
    """Return the catalog JSON, fetching from `url` if the cache is missing
    or older than `CATALOG_CACHE_TTL_SEC`. Falls back to the cache silently
    on network failure — the user keeps seeing whatever they had last."""
    cache = _catalog_cache_path()
    if not force_refresh and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < CATALOG_CACHE_TTL_SEC:
            try:
                return json.loads(cache.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                pass  # corrupted cache — fall through to refetch

    try:
        req = Request(url, headers={'User-Agent': 'subtitld-addons/1.0'})
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        catalog = json.loads(data.decode('utf-8'))
    except Exception as exc:
        log.warning('installer: catalog fetch failed: %s', exc)
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding='utf-8'))
            except Exception:
                pass
        raise

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    return catalog


def find_release(catalog: dict, addon_id: str, version: str | None = None) -> dict | None:
    """Pull a release entry out of `catalog['addons'][...]['releases']`. With
    `version=None` we return the first matching `latest_version`."""
    for entry in catalog.get('addons', []):
        if entry.get('id') != addon_id:
            continue
        target_version = version or entry.get('latest_version')
        for release in entry.get('releases', []):
            if release.get('version') == target_version:
                return release
        return None
    return None


def _download_for_platform(release: dict) -> dict | None:
    """Pick the download for the current platform out of a release entry."""
    plat = {'linux': 'linux', 'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, sys.platform)
    for dl in release.get('downloads', []):
        if dl.get('platform') == plat:
            return dl
    return None


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(64 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def install_from_zip(zip_path: Path, addon_id: str | None = None) -> str:
    """Extract a local zip into the addons directory. Returns the resolved
    addon_id. Used by both online install and "Install from file..." in the
    UI."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)

    addons_root = Path(session.PATH_SUBTITLD_ADDONS)
    addons_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        try:
            manifest_bytes = zf.read('manifest.json')
        except KeyError as exc:
            raise ValueError(f'{zip_path.name}: manifest.json missing from archive') from exc
        manifest = json.loads(manifest_bytes.decode('utf-8'))

        resolved_id = addon_id or manifest.get('id')
        if not resolved_id:
            raise ValueError(f'{zip_path.name}: manifest has no `id` field')

        # Stage to a sibling dir, then atomically swap in. If the target
        # exists we replace it (upgrade path).
        final_dir = addons_root / resolved_id
        staging_dir = addons_root / f'{resolved_id}._install'
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        try:
            zf.extractall(staging_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    # POSIX: ensure the executable bit is set on the declared executable.
    exe_rel = manifest.get('executable')
    if exe_rel:
        exe_path = staging_dir / exe_rel
        if exe_path.exists():
            try:
                exe_path.chmod(exe_path.stat().st_mode | 0o111)
            except OSError:
                pass

    if final_dir.exists():
        backup = addons_root / f'{resolved_id}._old'
        if backup.exists():
            shutil.rmtree(backup)
        final_dir.rename(backup)
        try:
            staging_dir.rename(final_dir)
        except Exception:
            backup.rename(final_dir)
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    else:
        staging_dir.rename(final_dir)

    log.info('installer: installed %s at %s', resolved_id, final_dir)
    return resolved_id


def download_and_install(catalog_entry: dict,
                         on_progress: ProgressCallback | None = None,
                         release_version: str | None = None) -> str:
    """Download the platform-specific zip for `catalog_entry`, verify sha256,
    and call `install_from_zip`. Returns the resolved addon_id."""
    addon_id = catalog_entry['id']
    release_version = release_version or catalog_entry.get('latest_version')
    target_release = None
    for r in catalog_entry.get('releases', []):
        if r.get('version') == release_version:
            target_release = r
            break
    if target_release is None:
        raise ValueError(f'no release {release_version!r} for {addon_id}')

    download = _download_for_platform(target_release)
    if download is None:
        raise ValueError(f'no download for current platform in {addon_id} {release_version}')

    url = download['url']
    expected_sha = download.get('sha256')
    expected_size = int(download.get('size_bytes', 0)) or None

    cache_dir = Path(session.PATH_SUBTITLD_USER_CACHE) / 'addon-downloads'
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f'{addon_id}-{release_version}.zip'

    # Resume-friendly skip: already-downloaded matching sha256 wins.
    if zip_path.exists() and expected_sha and _sha256(zip_path) == expected_sha:
        log.info('installer: reusing cached download %s', zip_path)
    else:
        if on_progress:
            on_progress(0.0, 'Starting download...')
        req = Request(url, headers={'User-Agent': 'subtitld-addons/1.0'})
        with urlopen(req, timeout=30.0) as resp, open(zip_path, 'wb') as out:
            total = expected_size or int(resp.headers.get('Content-Length') or 0)
            downloaded = 0
            chunk = 64 * 1024
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                downloaded += len(buf)
                if on_progress and total:
                    on_progress(min(downloaded / total, 0.999),
                                f'Downloading… {downloaded // (1024 * 1024)} MiB')
        if expected_sha:
            actual = _sha256(zip_path)
            if actual != expected_sha:
                zip_path.unlink(missing_ok=True)
                raise ValueError(
                    f'sha256 mismatch for {addon_id}: expected {expected_sha}, got {actual}'
                )

    if on_progress:
        on_progress(0.999, 'Installing...')
    resolved = install_from_zip(zip_path, addon_id=addon_id)
    if on_progress:
        on_progress(1.0, 'Done')
    return resolved


def uninstall(addon_id: str) -> bool:
    """Remove an installed add-on. Returns True if anything was removed."""
    target = Path(session.PATH_SUBTITLD_ADDONS) / addon_id
    if not target.exists():
        return False
    shutil.rmtree(target)
    log.info('installer: uninstalled %s', addon_id)
    return True


def list_installed() -> list[dict]:
    """Return manifest dicts for every installed add-on (manifests, not full
    runtime data — the UI uses this to populate the Installed tab)."""
    addons_root = Path(session.PATH_SUBTITLD_ADDONS)
    if not addons_root.exists():
        return []
    out = []
    for entry in sorted(addons_root.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / 'manifest.json'
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        manifest['_install_dir'] = str(entry)
        out.append(manifest)
    return out
