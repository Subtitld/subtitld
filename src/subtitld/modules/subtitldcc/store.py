"""Where Subtitld keeps its subtitld.cc token: the system keyring when available (pip extra "keyring"),
otherwise a file only the user can read. A keyring that is installed but doesn't work (common on Linux
without a Secret Service) falls back to the file too. Also a stable installation ID, so signing in again
on the same computer replaces its old token instead of adding one."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

SERVICE = "subtitld.cc"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (
        os.environ.get("APPDATA") if os.name == "nt" else str(Path.home() / ".config")
    )
    return Path(base) / "subtitld"


class TokenStore:
    def __init__(
        self, base_url: str = "https://subtitld.cc", directory: Path | None = None, use_keyring=True
    ):
        self.account = urlparse(base_url).netloc or base_url
        self.directory = directory or config_dir()
        self.keyring = None
        if use_keyring:
            try:
                import keyring  # noqa: PLC0415 (optional dependency)

                self.keyring = keyring
            except ImportError:
                pass

    @property
    def _file(self) -> Path:
        return self.directory / f"token-{self.account.replace(':', '_')}"

    def load(self) -> str | None:
        if self.keyring is not None:
            try:
                token = self.keyring.get_password(SERVICE, self.account)
            except Exception:  # noqa: BLE001 (no usable backend: each one raises its own errors)
                token = None
            if token:
                return token
        try:
            return self._file.read_text().strip() or None
        except FileNotFoundError:
            return None

    def save(self, token: str) -> None:
        if self.keyring is not None:
            try:
                self.keyring.set_password(SERVICE, self.account, token)
            except Exception:  # noqa: BLE001, S110 (fall back to the file below)
                pass
            else:
                self._file.unlink(missing_ok=True)
                return
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(token)

    def clear(self) -> None:
        if self.keyring is not None:
            try:
                self.keyring.delete_password(SERVICE, self.account)
            except Exception:  # noqa: BLE001, S110 (keyring backends raise their own errors for "not found")
                pass
        self._file.unlink(missing_ok=True)

    def device_id(self) -> str:
        """A random ID for this installation, created once."""
        path = self.directory / "device-id"
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            self.directory.mkdir(parents=True, exist_ok=True)
            value = str(uuid.uuid4())
            path.write_text(value)
            return value
