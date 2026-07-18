"""JSON-line protocol helpers for the Subtitld add-on system.

Each frame is one JSON object terminated by a single `\n`. Stdout carries
frames; stderr is reserved for free-form add-on logging. Frames are NEVER
pretty-printed or split across lines.

Frame types
-----------
hello            add-on -> host    capability advertisement (sent once)
hello_error      add-on -> host    handshake failure (terminal)
ready            host -> add-on    handshake ack
<task>           host -> add-on    request (e.g. `tts.synthesize`)
progress         add-on -> host    in-flight progress (0..1)
partial          add-on -> host    streaming partial result (e.g. ASR segment)
result           add-on -> host    terminal success
error            add-on -> host    terminal failure
shutdown         host -> add-on    graceful exit (no response expected)
cancel           host -> add-on    abort a pending request (best-effort)

The protocol version is bumped only on breaking changes. Backwards-compatible
additions (new optional fields, new error codes) keep version 1.
"""

from __future__ import annotations

import json
import uuid
from typing import Any


PROTOCOL_VERSION = 1


# ---------------------------------------------------------------------------
# Stable error codes. Add-ons may emit any of these in `error.code`. The host
# matches strings, so adding new codes is non-breaking. Keep in sync with the
# documented list in the manifest schema.
# ---------------------------------------------------------------------------
class ErrorCode:
    MODEL_MISSING = 'model_missing'
    UNSUPPORTED_VOICE = 'unsupported_voice'
    UNSUPPORTED_LANGUAGE = 'unsupported_language'
    BAD_PARAMS = 'bad_params'
    INTERNAL = 'internal'
    CANCELLED = 'cancelled'
    OOM = 'oom'
    DISK_FULL = 'disk_full'
    NETWORK_UNAVAILABLE = 'network_unavailable'
    PROCESS_EXITED = 'process_exited'  # synthesised host-side when add-on dies mid-request
    TIMEOUT = 'timeout'                 # synthesised host-side


class ProtocolError(Exception):
    """Raised when a malformed frame is read from an add-on."""


def new_request_id() -> str:
    """UUID4 hex (no dashes, 32 chars). Cheap to log, collision-free in practice."""
    return uuid.uuid4().hex


def encode(frame: dict) -> bytes:
    """Serialize a frame as one line of UTF-8 JSON terminated by `\\n`.

    Add-ons read with `readline()`, so the trailing newline is mandatory.
    `ensure_ascii=False` keeps non-Latin characters readable in stderr dumps,
    but they're still safely encoded as UTF-8 bytes.
    """
    return (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def decode(line: bytes | str) -> dict:
    """Parse one frame from a `readline()` result.

    Tolerates leading/trailing whitespace and BOM. Empty lines (after stripping)
    raise `ProtocolError` rather than returning an empty dict — they almost
    always indicate the add-on flushed prematurely.
    """
    if isinstance(line, bytes):
        line = line.decode('utf-8', errors='replace')
    line = line.strip().lstrip('\ufeff')
    if not line:
        raise ProtocolError('empty frame')
    try:
        frame = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f'malformed JSON: {exc.msg}') from exc
    if not isinstance(frame, dict):
        raise ProtocolError(f'frame must be a JSON object, got {type(frame).__name__}')
    return frame


def make_request(req_id: str, task: str, params: dict | None = None) -> dict:
    """Build a host->add-on request frame."""
    return {'id': req_id, 'type': task, 'params': params or {}}


def make_cancel(target_id: str) -> dict:
    """Build a host->add-on cancellation frame for a pending request."""
    return {'id': new_request_id(), 'type': 'cancel', 'target': target_id}


def make_stream_audio(req_id: str, pcm_b64: str) -> dict:
    """Build a host->add-on audio frame for an in-flight `asr.stream` request.

    `pcm_b64` is base64-encoded 16 kHz mono int16 PCM. Repeatable — the add-on
    feeds each chunk to its recognizer and streams back `partial` frames.
    """
    return {'id': req_id, 'type': 'asr.audio', 'data': {'pcm': pcm_b64}}


def make_stream_stop(req_id: str) -> dict:
    """Build a host->add-on frame ending an `asr.stream` request. The add-on
    flushes and emits a terminal `result` with the committed segments."""
    return {'id': req_id, 'type': 'asr.stop'}


def make_shutdown() -> dict:
    """Build a host->add-on graceful-shutdown frame.

    The add-on is expected to drain pending requests within ~5s. After that
    the host sends SIGTERM, then SIGKILL.
    """
    return {'type': 'shutdown'}


def make_ready(host_version: str) -> dict:
    """Build the host->add-on handshake ack frame."""
    return {'type': 'ready', 'host': 'subtitld', 'host_version': host_version, 'protocol': PROTOCOL_VERSION}


def is_terminal(frame: dict) -> bool:
    """True if this frame ends a request (one `result` or `error` per id)."""
    return frame.get('type') in ('result', 'error')


def is_progress(frame: dict) -> bool:
    return frame.get('type') == 'progress'


def is_partial(frame: dict) -> bool:
    return frame.get('type') == 'partial'


def validate_hello(frame: dict) -> tuple[str, str, list[dict[str, Any]]]:
    """Sanity-check a `hello` frame and return (addon_id, version, capabilities).

    Raises `ProtocolError` if required fields are missing or have the wrong
    shape. We deliberately don't validate capability internals here — that's
    the manager's job, since it depends on which tasks the host knows about.
    """
    if frame.get('type') != 'hello':
        raise ProtocolError(f"expected 'hello', got {frame.get('type')!r}")
    proto = frame.get('protocol')
    if proto != PROTOCOL_VERSION:
        # We could be lenient (accept older minor versions) once we have any,
        # but for v1 we hard-fail to surface mismatches early.
        raise ProtocolError(f'protocol mismatch: add-on speaks v{proto}, host speaks v{PROTOCOL_VERSION}')
    addon_id = frame.get('addon')
    version = frame.get('version')
    capabilities = frame.get('capabilities', [])
    if not isinstance(addon_id, str) or not addon_id:
        raise ProtocolError('hello.addon must be a non-empty string')
    if not isinstance(version, str) or not version:
        raise ProtocolError('hello.version must be a non-empty string')
    if not isinstance(capabilities, list):
        raise ProtocolError('hello.capabilities must be a list')
    return addon_id, version, capabilities
