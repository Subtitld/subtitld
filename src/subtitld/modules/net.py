"""HTTPS for the standard library's urllib.

A frozen Subtitld (PyInstaller) carries its own OpenSSL, which looks for
trusted certificates where the *build* machine keeps them. On a user's Mac,
or a Linux system laid out differently from the build host (Fedora, Arch, a
Flatpak runtime), that path is empty and every download fails with
CERTIFICATE_VERIFY_FAILED. The context below trusts the system's
certificates where it can find them, plus certifi's bundle, which is always
shipped with the app.
"""

import functools
import ssl


@functools.lru_cache(maxsize=1)
def https_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx
