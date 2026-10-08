"""Signing in from Subtitld.

- :func:`sign_in_browser`: opens the browser on subtitld.cc/authorize and waits on 127.0.0.1 for the
  answer (OAuth authorization code with PKCE, RFC 8252). Preferred on desktops.
- :func:`sign_in_device`: shows a short code to enter at subtitld.cc/device on any device (RFC 8628),
  for when no browser can reach this computer.

Both return the token answer {access_token, scope, user} and set ``client.token``. Both block while they
wait: run them in a worker thread, and pass ``cancelled`` (a function returning True once the person
gave up) to stop waiting early.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .client import CLIENT_ID, ApiError, Client

SCOPES = ("read", "publish", "rate")
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Subtitld</title>"
    "<body style='font-family:sans-serif;padding:3em'><h1>{title}</h1><p>{text}</p>"
)


class SignInError(Exception):
    """Signing in didn't finish: ``error`` is the OAuth error ("access_denied", "expired_token"…)."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _about(client: Client, device_name: str | None, device_id: str | None) -> dict:
    return {
        "device_name": device_name or socket.gethostname(),
        "device_id": device_id or "",
        "platform": client.os_name,
        "app_version": client.app_version,
    }


def sign_in_browser(
    client: Client,
    *,
    scopes=SCOPES,
    device_name: str | None = None,
    device_id: str | None = None,
    open_browser=webbrowser.open,
    timeout: float = 300,
    cancelled=lambda: False,
) -> dict:
    """Open subtitld.cc in the browser, wait for the person to approve, and get a token."""
    answer: dict = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            answer.update({key: values[0] for key, values in query.items()})
            ok = "code" in answer
            page = DONE_PAGE.format(
                title="Subtitld is connected" if ok else "Sign-in canceled",
                text="You can close this tab and go back to Subtitld.",
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *args):  # keep the console quiet
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": " ".join(scopes),
        **_about(client, device_name, device_id),
    }
    try:
        open_browser(f"{client.base_url}/authorize?{urllib.parse.urlencode(params)}")
        deadline = time.monotonic() + timeout
        while not answer and time.monotonic() < deadline and not cancelled():
            server.handle_request()
    finally:
        server.server_close()
    if not answer:
        raise SignInError("canceled" if cancelled() else "timeout")
    if answer.get("state") != state:
        raise SignInError("state_mismatch")
    if "code" not in answer:
        raise SignInError(answer.get("error", "access_denied"))
    token = client.request(
        "POST",
        "/oauth/token",
        form={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": answer["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )
    client.token = token["access_token"]
    return token


def sign_in_device(
    client: Client,
    *,
    show,
    scopes=SCOPES,
    device_name: str | None = None,
    device_id: str | None = None,
    sleep=time.sleep,
    cancelled=lambda: False,
) -> dict:
    """Get a code, let ``show(user_code, verification_uri, verification_uri_complete)`` display it, then
    wait until the person approves it at subtitld.cc/device (codes last 10 minutes)."""
    start = client.request(
        "POST",
        "/oauth/device",
        form={"client_id": CLIENT_ID, "scope": " ".join(scopes), **_about(client, device_name, device_id)},
    )
    show(start["user_code"], start["verification_uri"], start["verification_uri_complete"])
    interval = start["interval"]
    deadline = time.monotonic() + start["expires_in"]
    while time.monotonic() < deadline:
        for _ in range(int(interval)):  # a second at a time, to notice cancellation
            if cancelled():
                raise SignInError("canceled")
            sleep(1)
        try:
            token = client.request(
                "POST",
                "/oauth/token",
                form={
                    "grant_type": DEVICE_GRANT,
                    "client_id": CLIENT_ID,
                    "device_code": start["device_code"],
                },
            )
        except ApiError as error:
            if error.code == "authorization_pending":
                continue
            if error.code == "slow_down":
                interval += 5
                continue
            raise SignInError(error.code) from error
        client.token = token["access_token"]
        return token
    raise SignInError("expired_token")
