"""Shared helpers for Subtitld Cloud-backed providers.

No cloud-backed provider ships as a built-in — they are distributed as
add-ons — but auth + base URL are global to the Subtitld Cloud account
rather than per upstream brand, so they live here once instead of being
duplicated. The account dashboard (`interface/cloud_dashboard.py`) and the
global settings panel read them from here too, which is why this module
stays even with no cloud provider bundled.

Backward-compatibility contract — DON'T BREAK
=============================================
Older desktop builds must keep working as the cloud evolves. The two
guardrails this module enforces:

1. ``http_get`` / ``http_post_json`` return raw dicts. The provider modules
   read response fields with ``.get(key, default)``, never ``[key]``. New
   cloud-side fields are silently ignored; missing ones don't crash.

2. Catalog rows aren't validated. Whatever the cloud returns, we pass
   through. If the cloud one day adds a new metadata key (say,
   ``"supports_diarization": True``), older desktops see the key and
   ignore it. They don't need a release.

The public ID format ``subtitld-cloud:<provider>/<native_id>`` is part
of the cloud API contract — saved project files contain these IDs. It
will never change for any reason on ``/api/v1/``; breaking changes
would land on ``/api/v2/`` with a separate code path.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request

import subtitld
from subtitld.modules import session

log = logging.getLogger(__name__)


# Production cloud endpoint — the only URL end users ever talk to. There
# is intentionally no UI for changing this: a self-hosted/draft server is
# a developer concern, not a user-facing setting.
PRODUCTION_BASE_URL = 'https://cloud.subtitld.org'

# Environment-variable override for development. When
# ``SUBTITLD_CLOUD_BASE_URL`` is set (e.g. to
# ``https://draft-cloud.subtitld.org``), every cloud request goes there
# instead of production. Unset in shipped builds, so users always hit
# production. This replaces the old user-facing "Server URL" field.
_ENV_BASE_URL_VAR = 'SUBTITLD_CLOUD_BASE_URL'

# Effective default, resolved at import time: env override if present,
# else production. Kept as a module constant so other modules
# (config_schema help text, etc.) can reference the active endpoint.
DEFAULT_BASE_URL = os.environ.get(_ENV_BASE_URL_VAR) or PRODUCTION_BASE_URL

# Public-ID prefix the cloud uses to namespace all of its providers. Part
# of the API contract — DO NOT change without bumping the API version.
PUBLIC_ID_PREFIX = 'subtitld-cloud:'

# Per-account auth + URL live here. ONE config slot shared across every
# cloud-backed provider (assemblyai-cloud, elevenlabs-cloud, ...). When
# the user pastes their API key once, all cloud-backed providers see it.
_CONFIG_ROOT_KEYS = ('transcription', 'engine_options', 'SubtitldCloud')

# User-Agent sent on every cloud request. Cloudflare's bot protection
# in front of draft-cloud.subtitld.org blocks ``Python-urllib/<v>`` (the
# stdlib default) with HTTP 403 — every catalog/transcribe call from a
# stock urllib client gets dropped before reaching the origin, so a
# valid api_key reads as "no models available" to the user. Sending the
# app's own UA both gets us through the WAF and gives the cloud team a
# clean signal for usage analytics + rate-limiting per app version.
USER_AGENT = f'Subtitld/{subtitld.__version__}'


# ---------------------------------------------------------------------------
# Config helpers — all cloud-backed providers read auth through these
# ---------------------------------------------------------------------------


def get_config_root() -> dict:
    """Return the SubtitldCloud sub-dict under ``session.CONFIG``,
    creating it (and parents) on first access."""
    node = session.CONFIG
    for key in _CONFIG_ROOT_KEYS:
        node = node.setdefault(key, {})
    return node


def read_base_url() -> str:
    """Active cloud endpoint.

    Resolution order:
      1. ``SUBTITLD_CLOUD_BASE_URL`` env var (development override),
      2. a ``base_url`` stored in config (legacy / advanced manual
         edits — no UI writes this anymore),
      3. the production default.

    The env var is read live (not just at import) so a dev can point a
    running instance at a different server by relaunching with the var
    set, without code changes.
    """
    env = os.environ.get(_ENV_BASE_URL_VAR)
    if env:
        return env
    return get_config_root().get('base_url') or PRODUCTION_BASE_URL


def read_api_key() -> str:
    """User-configured API key. Empty string when not configured —
    callers should error out cleanly rather than send an empty Bearer."""
    return get_config_root().get('api_key') or ''


def is_configured() -> bool:
    """Convenience check used by `language` / `list_models` accessors that
    short-circuit when the user hasn't set up auth yet."""
    return bool(read_api_key())


def read_dashboard_url() -> str:
    """Web portal landing page for the user's account — where they manage
    API keys, top up balance, view usage. Derived from the configured
    base URL so a self-hosted / draft env points at its own portal."""
    return f'{read_base_url().rstrip("/")}/dashboard/'


def read_topup_url() -> str:
    """Billing / top-up page in the web portal. Opened by the dashboard's
    "Top up balance" button."""
    return f'{read_base_url().rstrip("/")}/dashboard/billing/'


def read_signup_url() -> str:
    """Account creation page. Opened by the not-connected empty state's
    "Create an account" link."""
    return f'{read_base_url().rstrip("/")}/accounts/signup/'


def write_config(api_key: str | None = None, base_url: str | None = None) -> None:
    """Persist auth fields into the shared cloud config slot. Passing
    ``None`` leaves a field untouched; passing an empty string clears it.
    Every cloud-backed provider reads through `read_api_key` /
    `read_base_url`, so a single write here updates them all at once."""
    root = get_config_root()
    if api_key is not None:
        root['api_key'] = api_key
    if base_url is not None:
        root['base_url'] = base_url


# ---------------------------------------------------------------------------
# HTTP helpers — plain stdlib so we don't add a desktop runtime dependency
# ---------------------------------------------------------------------------


def http_get(url: str, *, api_key: str, timeout: float = 15.0) -> dict:
    """GET with bearer auth. Returns the decoded JSON dict.

    Raises ``urllib.error.HTTPError`` on non-2xx so callers can inspect
    the status code; ``urllib.error.URLError`` on socket / DNS errors.
    """
    req = urllib.request.Request(url, method='GET')
    # ``User-Agent`` must come before ``urlopen`` — see the USER_AGENT
    # block comment in this module for why the stdlib default 403s.
    req.add_header('User-Agent', USER_AGENT)
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def http_post_json(
    url: str,
    body: dict,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> dict:
    """POST a JSON body with bearer auth. Returns the decoded JSON dict.

    Same exception semantics as ``http_get``.
    """
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', USER_AGENT)  # see USER_AGENT comment
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def http_post_multipart(
    url: str,
    *,
    fields: dict | None = None,
    files: dict,
    api_key: str,
    timeout: float = 120.0,
) -> dict:
    """POST a ``multipart/form-data`` body with bearer auth.

    ``files`` is a mapping ``{field_name: (filename, bytes, content_type)}``
    — one entry is the common case (a single audio upload). ``fields`` is
    an optional mapping of plain text form fields to send alongside.

    Returns the decoded JSON dict on 2xx. Same exception semantics as
    ``http_get`` — non-2xx raises ``urllib.error.HTTPError`` so the
    caller can read ``.read()`` for the server's error detail.

    Why hand-rolled instead of ``requests`` or ``email.mime.multipart``:
    the desktop ships no third-party HTTP dependency, and ``email.mime``
    produces output the cloud's web framework rejects under strict mode
    (folded headers, base64 transfer encoding). The wire format here is
    the same one ``curl --form`` writes — explicit boundary, ``CRLF``
    line endings, raw binary payload — which every server-side
    framework parses cleanly.

    Memory note: the file payload is held fully in memory while the
    request body is assembled. Opus at 24 kbps mono is ~10 MB per hour,
    so even multi-hour recordings stay well under any realistic limit
    — chunked streaming would be premature.
    """
    boundary = f'----SubtitldBoundary{uuid.uuid4().hex}'
    boundary_bytes = boundary.encode('ascii')

    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(b'--' + boundary_bytes + b'\r\n')
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8')
        )
        parts.append(str(value).encode('utf-8'))
        parts.append(b'\r\n')

    for name, (filename, content, content_type) in files.items():
        parts.append(b'--' + boundary_bytes + b'\r\n')
        # filename is quoted but NOT escaped — the cloud's upload field
        # is a passthrough and a quote in the filename would only ever
        # come from a user file path under their control. If we later
        # need to defend against that, percent-encode here.
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode('utf-8')
        )
        parts.append(f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8'))
        parts.append(content)
        parts.append(b'\r\n')

    parts.append(b'--' + boundary_bytes + b'--\r\n')
    body = b''.join(parts)

    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('User-Agent', USER_AGENT)  # see USER_AGENT comment
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_account(timeout: float = 15.0) -> dict:
    """Fetch the authenticated user's account summary from
    ``GET /api/v1/account``.

    Returns the decoded JSON dict — shape is read defensively by callers
    with ``.get()``, since the cloud may grow the payload over time. The
    account dashboard (Global → Subtitld Cloud) reads any subset of:

        ``email``          str    — account login (drives avatar initial)
        ``plan``           str    — plan/tier label (optional)
        ``balance``        num     — remaining credit, MAJOR units
                                     (e.g. 5.59, NOT cents)
        ``currency``       str     — ISO code for ``balance`` (e.g. "USD")
        ``credits``        num     — remaining prepaid units (alt to balance)
        ``ring_fraction``  num     — 0..1 fill for the balance ring
                                     (optional; e.g. balance ÷ a top-up
                                     target). Falls back to a fixed fill.
        ``low_balance``    bool    — force the red "low balance" treatment
                                     (optional; else derived from balance)
        ``usage``          list    — recent jobs for the history table, each
                                     ``{file, kind, minutes, cost}``:
                                       ``file``    str  — source filename
                                       ``kind``    str  — "Transcription" /
                                                          "Dubbing · ES" …
                                                          (Transcription →
                                                          wave mark, else
                                                          dub mark)
                                       ``minutes`` num  — duration in minutes
                                       ``cost``    num  — debit, MAJOR units

    Every field is optional; the dashboard degrades (hides the usage band
    when empty, shows "—" for an absent balance, etc.).

    Raises ``urllib.error.HTTPError`` (so 401 → "bad key", 404 → "your
    cloud build doesn't expose /account yet") and ``urllib.error.URLError``
    on network failure. The caller — a background QThread in the settings
    panel — turns these into a user-facing status line. We DON'T swallow
    them here the way `fetch_catalog_filtered` does, because the user
    explicitly asked to check their balance and deserves the reason it
    failed.
    """
    url = f'{read_base_url().rstrip("/")}/api/v1/account'
    return http_get(url, api_key=read_api_key(), timeout=timeout)


def fetch_catalog_filtered(provider: str = '', task: str = '') -> list[dict]:
    """Fetch ``/api/v1/catalog`` with optional ``?provider=`` and ``?task=`` filters.

    Returns ``[]`` on any error (auth not set, network down, server 5xx, ...).
    Callers don't differentiate "no entries" from "fetch failed" — both
    surface as an empty picker, which the UI handles cleanly.
    """
    if not is_configured():
        return []
    qs_pairs = {k: v for k, v in (('provider', provider), ('task', task)) if v}
    qs = urllib.parse.urlencode(qs_pairs)
    url = f'{read_base_url().rstrip("/")}/api/v1/catalog'
    if qs:
        url = f'{url}?{qs}'
    try:
        data = http_get(url, api_key=read_api_key())
    except urllib.error.HTTPError as exc:
        log.info('subtitld-cloud: catalog HTTP %s — likely bad api_key', exc.code)
        return []
    except Exception as exc:  # noqa: BLE001 — degrade silently
        log.debug('subtitld-cloud: catalog fetch failed: %s', exc)
        return []
    if isinstance(data, list):
        return data
    return []
