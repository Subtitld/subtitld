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
import html
import secrets
import socket
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .client import CLIENT_ID, ApiError, Client

SCOPES = ("read", "publish", "rate", "collab")
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# What the browser shows once subtitld.cc sends the person back to Subtitld: in English unless the app
# passes its own words (sign_in_browser's `page`). Self-contained, nothing loaded from anywhere:
# subtitld.cc's colours and type (Montserrat where it is installed), light and dark, the Subtitld mark.
PAGE_TEXT = {
    "lang": "en",
    "connected": "Subtitld is connected",
    "canceled": "Sign-in canceled",
    "close": "You can close this tab and go back to Subtitld.",
}
_MARK = (
    '<svg viewBox="96.380 142.800 18.630 18.630" aria-hidden="true"><path class="clip" d="m '
    "104.7372,150.17408 c 1.0705,6.5e-4 1.9378,0.86891 1.9373,1.93941 -6e-4,1.06969 -0.8676,1.9367 "
    "-1.9373,1.93735 h -5.5604 -0.3219 -0.023 -1.372 c -0.5902,0 -1.0795,-0.48975 -1.0795,-1.08003 v "
    "-1.71669 c 0,-0.59028 0.4893,-1.08004 1.0795,-1.08004 h 1.3787 0.017 0.3214 z m -5.5604,0.94154 "
    "h -1.3922 -0.325 c -0.084,0 -0.14,0.0536 -0.14,0.1385 v 1.71669 c 0,0.0848 0.055,0.13797 "
    "0.14,0.13797 h 0.325 1.3922 c 0.084,0 0.138,-0.0531 0.138,-0.13797 v -1.71669 c 0,-0.0848 "
    '-0.053,-0.1385 -0.138,-0.1385 z"/><path class="arrow" d="m 101.4413,144.35995 v 0.0203 c '
    "-0.037,0.002 -0.063,-0.0193 -0.1,-0.0174 -2.4693,0.17121 -2.3747,3.8288 0.1,3.87239 h 3.2938 c "
    "2.142,-8.3e-4 3.8789,1.73564 3.8786,3.8777 -0.026,1.88104 -1.5018,3.18505 -2.7275,4.44486 "
    "-1.7517,1.82704 0.9138,4.49276 2.7411,2.74112 l 5.8153,-5.81541 c 0.7567,-0.75704 "
    "0.7567,-1.98408 0,-2.74112 l -5.8153,-5.81541 c -0.3637,-0.36326 -0.8566,-0.56727 "
    '-1.3707,-0.56717 z"/></svg>'
)
_OK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
_NO_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg>'
_STYLE = """
:root{color-scheme:light dark;
--ff:Montserrat,'Noto Sans','Noto Sans Arabic','Noto Sans Hebrew','Noto Sans JP',system-ui,sans-serif;
--page:linear-gradient(180deg,light-dark(#f4f7f9,#07090b) 0,light-dark(#dde6ed,#1a232b) 100%);
--raised:light-dark(#ffffff,#26343f);--line:light-dark(rgba(48,66,81,.14),rgba(255,255,255,.07));
--fg:light-dark(#1a232b,#ffffff);--fg-2:light-dark(#3e5363,#b8cee0);--fg-3:light-dark(#536475,#8b9cad);
--accent:light-dark(#304251,#b8cee0);--ok:light-dark(#2a7a1c,#6be052);
--ok-bg:light-dark(rgba(85,212,63,.16),rgba(85,212,63,.13));
--no:light-dark(#a94444,#e08a8a);--no-bg:light-dark(rgba(197,82,82,.12),rgba(197,82,82,.16))}
*{box-sizing:border-box;margin:0}
html{min-height:100%;background:var(--page)}
body{min-height:100vh;display:grid;place-items:center;padding:24px;font:400 15px/24px var(--ff);
color:var(--fg);
-webkit-font-smoothing:antialiased}
main{width:100%;max-width:440px;background:var(--raised);border:1px solid var(--line);border-radius:4px;
padding:40px 36px 32px;text-align:center;
box-shadow:0 1px 2px rgba(7,9,11,.06),0 18px 48px -18px rgba(7,9,11,.35)}
.brand{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:32px;
font:700 11px/16px var(--ff);letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3)}
.brand svg{width:28px;height:28px}
.clip{fill:var(--accent)}.arrow{fill:#55d43f}
.status{width:64px;height:64px;margin:0 auto 20px;border-radius:50%;display:grid;place-items:center;
background:var(--ok-bg);color:var(--ok)}
.no .status{background:var(--no-bg);color:var(--no)}
.status svg{width:30px;height:30px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;
stroke-linejoin:round}
h1{font:600 clamp(22px,1.2vw + 16px,26px)/1.25 var(--ff);margin-bottom:10px;text-wrap:balance}
p{color:var(--fg-2);text-wrap:pretty}
footer{margin-top:28px;padding-top:16px;border-top:1px solid var(--line);font:500 13px/20px var(--ff);
color:var(--fg-3)}
"""


def done_page(ok: bool, page: dict | None = None) -> str:
    """The page the browser shows once the sign-in came back: connected (`ok`) or not."""
    text = {**PAGE_TEXT, **(page or {})}
    title = html.escape(text["connected"] if ok else text["canceled"])
    favicon = "data:image/svg+xml," + urllib.parse.quote(
        _MARK.replace('class="clip"', 'fill="#3e5363"')
        .replace('class="arrow"', 'fill="#55d43f"')
        .replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    )
    return (
        f'<!doctype html><html lang="{html.escape(text["lang"])}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title} · Subtitld</title>"
        f'<link rel="icon" href="{favicon}"><style>{_STYLE}</style></head>'
        f'<body class="{"ok" if ok else "no"}"><main>'
        f'<div class="brand">{_MARK}<span>Subtitld</span></div>'
        f'<div class="status">{_OK_ICON if ok else _NO_ICON}</div>'
        f"<h1>{title}</h1><p>{html.escape(text['close'])}</p>"
        "<footer>subtitld.cc</footer></main></body></html>"
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
    page: dict | None = None,
) -> dict:
    """Open subtitld.cc in the browser, wait for the person to approve, and get a token. `page`: the words of
    the page the browser shows at the end, in the app's language ({lang, connected, canceled, close}, see
    PAGE_TEXT; English for any left out)."""
    answer: dict = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            answer.update({key: values[0] for key, values in query.items()})
            body = done_page("code" in answer, page).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
