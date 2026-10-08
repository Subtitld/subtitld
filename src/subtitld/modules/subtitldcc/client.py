"""HTTP client for the subtitld.cc API v1 (standard library only)."""

from __future__ import annotations

import json
import platform
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

try:
    from subtitld_videohash import hash_file
except ImportError:  # the copy bundled inside Subtitld keeps the hash module next to this one
    from .videohash import hash_file

DEFAULT_BASE_URL = "https://subtitld.cc"
CLIENT_ID = "subtitld"
RETRY_STATUSES = {429, 502, 503, 504}


class ApiError(Exception):
    """An error answer from the API: ``code`` is stable ("version_conflict"), ``message`` is for people
    (in the Accept-Language the client sent), ``body`` has any extra fields (such as ``latest``)."""

    def __init__(self, status: int, code: str, message: str = "", body: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.body = status, code, message, body or {}


def _multipart(fields: dict[str, Any], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "subtitld-" + secrets.token_hex(12)
    parts: list[bytes] = []
    for name, value in fields.items():
        for item in value if isinstance(value, (list, tuple)) else [value]:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{item}\r\n'.encode()
            )
    for name, (filename, data) in files.items():
        safe = filename.replace('"', "'").replace("\r", "").replace("\n", "")
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        parts.append(head.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Client:
    """One connection to subtitld.cc. Pass ``token`` (from :mod:`subtitld_cc.auth`) to act as a person.

    Reads work without a token (public subtitles). Writes need one with the right scope.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        token: str | None = None,
        app_version: str = "0",
        os_name: str | None = None,
        language: str | None = None,
        timeout: float = 30,
        retries: int = 3,
        opener=None,
        sleep=time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.app_version = app_version
        self.os_name = os_name or platform.system() or "Unknown"
        self.language = language
        self.timeout = timeout
        self.retries = retries
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    @property
    def user_agent(self) -> str:
        return f"Subtitld/{self.app_version} ({self.os_name})"

    # --- Transport ---------------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        form: dict | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
        idempotency_key: str | None = None,
        raw: bool = False,
    ):
        """Send one request (retrying network errors and 429/502/503/504 when that's safe) and return the
        decoded JSON, or the bytes with ``raw``."""
        url = f"{self.base_url}/api/v1{path}"
        if params:
            pairs = []
            for key, value in params.items():
                values = value if isinstance(value, list) else [value]
                pairs += [(key, item) for item in values if item is not None]
            url += "?" + urllib.parse.urlencode(pairs)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.language:
            headers["Accept-Language"] = self.language
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if files:
            data, headers["Content-Type"] = _multipart(form or {}, files)
        elif form is not None:
            data = urllib.parse.urlencode(form, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"

        # Retrying a POST is only safe when the server can recognize the repeat.
        safe = method in ("GET", "PUT", "DELETE") or idempotency_key is not None
        attempts = 1 + (self.retries if safe else 0)
        for attempt in range(attempts):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._open(request, timeout=self.timeout) as response:
                    body = response.read()
                    if raw:
                        return body
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as error:
                if error.code in RETRY_STATUSES and attempt + 1 < attempts:
                    self._sleep(self._delay(attempt, error.headers.get("Retry-After")))
                    continue
                raise self._error(error) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt + 1 < attempts:
                    self._sleep(self._delay(attempt))
                    continue
                raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _delay(attempt: int, retry_after: str | None = None) -> float:
        if retry_after and retry_after.isdigit():
            return min(60.0, float(retry_after))
        return min(30.0, 0.5 * 2**attempt)

    @staticmethod
    def _error(error: urllib.error.HTTPError) -> ApiError:
        try:
            body = json.loads(error.read() or b"{}")
        except ValueError:
            body = {}
        return ApiError(error.code, body.get("code", "http_error"), body.get("message", ""), body)

    # --- Reading -----------------------------------------------------------------------------------------

    def meta(self) -> dict:
        return self.request("GET", "/meta")

    def me(self) -> dict:
        return self.request("GET", "/me")

    def lookup(self, videos: list[dict], languages: list[str] | None = None) -> list[dict]:
        """Matches for up to 50 videos, each {subtitld_hash, opensubtitles_hash, size, duration_ms}."""
        body = {"videos": videos, "languages": languages or []}
        return self.request("POST", "/videos/lookup", json_body=body)["results"]

    def lookup_file(self, path, languages: list[str] | None = None, duration_ms: int | None = None) -> dict:
        """Fingerprint a local video (first and last megabyte only) and look it up. The name isn't sent."""
        return self.lookup([fingerprint(path, duration_ms)], languages)[0]

    def search(self, q: str, *, cursor: str | None = None, limit: int = 20, **filters) -> dict:
        """One page: {results, next_cursor, total and titles on the first page}. ``filters``: lang (list),
        hi, forced, hide_mt, hide_links, rating ("4" or "unrated"), type (list)."""
        params = {"q": q, "cursor": cursor, "limit": limit}
        params.update({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in filters.items()})
        return self.request("GET", "/search", params=params)

    def iter_search(self, q: str, **filters) -> Iterator[dict]:
        cursor = None
        while True:
            page = self.search(q, cursor=cursor, **filters)
            yield from page["results"]
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def suggest_titles(self, q: str) -> list[dict]:
        return self.request("GET", "/titles/suggest", params={"q": q})["titles"]

    def asset(self, share_id: str) -> dict:
        return self.request("GET", f"/assets/{share_id}")

    def versions(self, share_id: str, cursor: str | None = None) -> dict:
        return self.request("GET", f"/assets/{share_id}/versions", params={"cursor": cursor})

    def download(self, share_id: str, fmt: str = "usf", version: int | None = None) -> bytes:
        params = {"format": fmt, "version": version}
        return self.request("GET", f"/assets/{share_id}/download", params=params, raw=True)

    # --- Publishing --------------------------------------------------------------------------------------

    def check(self, filename: str, data: bytes, *, target: str | None = None, **options) -> dict:
        """Upload a subtitle file to be checked (problems, quality, suggestions). ``target`` makes it a new
        version of that share ID. ``options``: encoding, fps, fixes (list)."""
        form = {k: v for k, v in {"target": target, **options}.items() if v}
        return self.request("POST", "/uploads", form=form, files={"file": (filename, data)})

    def update_upload(self, upload_id: str, **options) -> dict:
        """Change encoding, fps, fixes (list) or fork (bool) of a checked upload."""
        return self.request("PATCH", f"/uploads/{upload_id}", json_body=options)

    def discard_upload(self, upload_id: str) -> None:
        self.request("DELETE", f"/uploads/{upload_id}")

    def publish(self, upload_id: str, *, idempotency_key: str | None = None, **fields) -> dict:
        """Publish a checked upload as a new subtitle. ``fields``: visibility, language, kind, work_title,
        year, series_title, season, episode, owner, license, description, flags, release_names, video."""
        body = {"upload_id": upload_id, **fields}
        key = idempotency_key or str(uuid.uuid4())
        return self.request("POST", "/assets", json_body=body, idempotency_key=key)

    def new_version(
        self,
        share_id: str,
        upload_id: str,
        *,
        parent: int,
        changelog: str,
        kind: str = "fix",
        video: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Publish the next version. Raises ApiError "version_conflict" (with body["latest"]) if someone
        published since ``parent``."""
        body = {"upload_id": upload_id, "parent": parent, "kind": kind, "changelog": changelog}
        if video:
            body["video"] = video
        key = idempotency_key or str(uuid.uuid4())
        return self.request("POST", f"/assets/{share_id}/versions", json_body=body, idempotency_key=key)

    # --- Community -----------------------------------------------------------------------------------------

    def rate(self, share_id: str, stars: int, tags: list[str] | None = None, note: str = "") -> dict:
        body = {"stars": stars, "tags": tags or [], "note": note}
        return self.request("PUT", f"/assets/{share_id}/rating", json_body=body)

    def unrate(self, share_id: str) -> None:
        self.request("DELETE", f"/assets/{share_id}/rating")

    def confirm_match(self, share_id: str, video: dict, release_name: str = "") -> dict:
        """ "Works with my file". ``release_name`` only if the person chose to show it."""
        body = {**video, "release_name": release_name}
        return self.request("POST", f"/assets/{share_id}/matches", json_body=body)

    # --- Signing out -------------------------------------------------------------------------------------

    def revoke(self) -> None:
        if self.token:
            self.request("POST", "/oauth/revoke", form={"token": self.token})
            self.token = None


def fingerprint(path, duration_ms: int | None = None) -> dict:
    """The lookup form of a local video: both hashes and the size (the file name stays here)."""
    hashed = hash_file(path)
    video = {"subtitld_hash": hashed["subtitld"], "size": hashed["size"]}
    if hashed.get("opensubtitles"):
        video["opensubtitles_hash"] = hashed["opensubtitles"]
    if duration_ms:
        video["duration_ms"] = duration_ms
    return video
