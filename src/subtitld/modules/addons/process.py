"""Subprocess management for out-of-process add-ons.

Each `AddonProcess` owns one child process (the PyInstaller-built add-on
binary) and presents it to callers as a request/response API on top of the
JSON-line protocol. Stdout reader runs in a daemon Python thread and routes
frames to the matching `AddonRequest` by `id`. Stderr reader logs everything
verbatim — that's where add-ons stash debug output without polluting the
protocol channel.

Threading note: we use `threading.Thread`, NOT `QThread`, for the readers.
Reasons:

  1. `AddonProcess` may live longer than any one Qt event loop and predates
     it on app startup (we want to be able to spawn add-ons from a CLI tool
     too).
  2. Crossing `QThread` boundaries needs an alive QApplication; raw threads
     are fine as long as we deliver results via Qt signals on the *Provider*
     side, which IS attached to the main thread.

`AddonRequest` is a plain `QObject` so its signals (`progress`, `partial`,
`result`, `error`) marshal correctly when emitted from the reader thread —
PySide6's queued-connection default does the cross-thread hop for us.
"""

from __future__ import annotations

import logging
import os
import signal as _signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from subtitld.modules import session
from subtitld.modules.addons import protocol

log = logging.getLogger(__name__)


# Default lifecycle timings. Overridable per-add-on via manifest fields.
DEFAULT_HANDSHAKE_TIMEOUT_SEC = 30.0
DEFAULT_REQUEST_TIMEOUT_SEC = 600.0     # 10 min — TTS can be slow on first model load
DEFAULT_SHUTDOWN_GRACE_SEC = 5.0
DEFAULT_IDLE_GC_SEC = 300.0             # auto-stop after 5 min idle


class AddonRequest(QObject):
    """One in-flight request. Lives until a terminal frame arrives.

    Signals
    -------
    progress(value: float, message: str)
        Emitted 0+ times during execution.
    partial(data: dict)
        Emitted 0+ times for streaming results (e.g. per-segment ASR).
    result(data: dict)
        Emitted once on success — terminal.
    error(code: str, message: str)
        Emitted once on failure — terminal.
    """

    progress = Signal(float, str)
    partial = Signal(dict)
    result = Signal(dict)
    error = Signal(str, str)

    def __init__(self, req_id: str, task: str, parent: QObject | None = None):
        super().__init__(parent)
        self.id = req_id
        self.task = task
        self._terminated = False
        self._lock = threading.Lock()
        self._completion = threading.Event()  # for blocking waits in tests

    def _deliver(self, frame: dict) -> bool:
        """Route an incoming frame to the right signal. Returns True if this
        was the terminal frame (caller should retire the request)."""
        with self._lock:
            if self._terminated:
                return True
            ftype = frame.get('type')
            if ftype == 'progress':
                value = float(frame.get('value', 0.0))
                message = str(frame.get('message', ''))
                self.progress.emit(value, message)
                return False
            if ftype == 'partial':
                self.partial.emit(frame.get('data', {}) or {})
                return False
            if ftype == 'result':
                self._terminated = True
                self.result.emit(frame.get('data', {}) or {})
                self._completion.set()
                return True
            if ftype == 'error':
                self._terminated = True
                self.error.emit(
                    str(frame.get('code', protocol.ErrorCode.INTERNAL)),
                    str(frame.get('message', '')),
                )
                self._completion.set()
                return True
            log.warning('AddonRequest %s: unknown frame type %r', self.id, ftype)
            return False

    def _fail(self, code: str, message: str) -> None:
        """Synthesise a terminal error from the host side (process death,
        timeout, ...). Idempotent."""
        with self._lock:
            if self._terminated:
                return
            self._terminated = True
        self.error.emit(code, message)
        self._completion.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until terminal frame or timeout. Mostly useful in tests."""
        return self._completion.wait(timeout)


class AddonProcess(QObject):
    """Owns one add-on subprocess.

    Lifecycle
    ---------
        ap = AddonProcess(manifest, exe_path)
        ap.start()                       # spawn + handshake
        req = ap.request('tts.synthesize', {...})
        req.result.connect(my_slot)
        # ... or ap.shutdown() to stop gracefully

    Why a QObject? We parent each `AddonRequest` to the AddonProcess so Qt's
    parent-child mechanism keeps requests alive long enough for queued
    cross-thread signal deliveries to land. Letting the request get GC'd
    by Python the moment we pop it from `_requests` is a guaranteed segfault
    on the GUI thread when it goes to dispatch the queued `result`/`error`
    metacall — the C++ QObject is gone but Qt's metacall validates the
    sender pointer first.
    """

    def __init__(
        self,
        manifest: dict,
        exe_path: str | Path,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.manifest = manifest
        self.addon_id = manifest.get('id', '<unknown>')
        self.exe_path = str(exe_path)
        self.cwd = str(cwd) if cwd else None
        self.env = env

        self._proc: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._requests: dict[str, AddonRequest] = {}
        self._requests_lock = threading.Lock()

        self._hello_event = threading.Event()
        self._hello_frame: dict | None = None
        self._hello_error: str | None = None

        self._shutdown_requested = False
        self._last_activity = time.monotonic()

        self._handshake_timeout = float(
            manifest.get('startup_timeout_sec', DEFAULT_HANDSHAKE_TIMEOUT_SEC)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Spawn the child process and complete the handshake. Raises on
        handshake failure or timeout — caller is expected to surface the
        error to the UI."""
        if self.is_running():
            return

        log.info('AddonProcess[%s]: starting %s', self.addon_id, self.exe_path)

        # Cross-platform process group hygiene: on POSIX we put the child in
        # its own session so SIGTERM from us doesn't escape to the parent's
        # tty. On Windows we use CREATE_NEW_PROCESS_GROUP for the same reason
        # plus to enable Ctrl+Break delivery later if we want it.
        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            bufsize=0,  # unbuffered binary I/O — readline does its own buffering
        )
        if os.name == 'posix':
            popen_kwargs['start_new_session'] = True
        else:
            popen_kwargs['creationflags'] = getattr(
                subprocess, 'CREATE_NEW_PROCESS_GROUP', 0
            )
            if session.STARTUPINFO is not None:
                popen_kwargs['startupinfo'] = session.STARTUPINFO

        try:
            self._proc = subprocess.Popen([self.exe_path], **popen_kwargs)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(
                f'failed to spawn add-on {self.addon_id} at {self.exe_path}: {exc}'
            ) from exc

        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name=f'addon-{self.addon_id}-stdout',
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f'addon-{self.addon_id}-stderr',
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        # Wait for hello — the reader fills `_hello_frame` or `_hello_error`.
        if not self._hello_event.wait(self._handshake_timeout):
            self._kill('handshake timeout')
            raise TimeoutError(
                f'add-on {self.addon_id} did not send hello within '
                f'{self._handshake_timeout:.0f}s'
            )

        if self._hello_error is not None:
            err = self._hello_error
            self._kill('handshake error')
            raise RuntimeError(f'add-on {self.addon_id} handshake failed: {err}')

        # Send `ready` ack.
        self._write_frame(protocol.make_ready(host_version=self._host_version()))
        log.info('AddonProcess[%s]: ready', self.addon_id)

    def shutdown(self, grace_sec: float = DEFAULT_SHUTDOWN_GRACE_SEC) -> None:
        """Send `shutdown`, then SIGTERM, then SIGKILL. Safe to call multiple
        times or before `start`."""
        if self._proc is None:
            return
        if self._shutdown_requested:
            return
        self._shutdown_requested = True

        if self.is_running():
            try:
                self._write_frame(protocol.make_shutdown())
            except Exception:
                pass

            try:
                self._proc.wait(timeout=grace_sec)
            except subprocess.TimeoutExpired:
                log.warning('AddonProcess[%s]: ignored shutdown, sending SIGTERM', self.addon_id)
                self._terminate()
                try:
                    self._proc.wait(timeout=grace_sec)
                except subprocess.TimeoutExpired:
                    log.warning('AddonProcess[%s]: ignored SIGTERM, killing', self.addon_id)
                    self._kill('shutdown forced')

        # Fail any still-pending requests.
        self._fail_all_pending(
            protocol.ErrorCode.PROCESS_EXITED,
            'add-on shut down with pending request',
        )

    # ------------------------------------------------------------------
    # Request API
    # ------------------------------------------------------------------
    def request(
        self,
        task: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> AddonRequest:
        """Send a request frame and return an `AddonRequest` to attach signals
        to. Auto-spawns the process if it's not running yet."""
        if not self.is_running():
            self.start()

        req_id = protocol.new_request_id()
        # Parent the request to this AddonProcess (a QObject). This keeps it
        # alive for the duration of any queued cross-thread metacalls even
        # after we pop it from `_requests`. Memory is bounded because we
        # call `deleteLater()` on the terminal frame in `_dispatch`.
        req = AddonRequest(req_id, task, parent=self)
        with self._requests_lock:
            self._requests[req_id] = req
        self._last_activity = time.monotonic()

        # Trim params for the log line — `text` and `voice_ref_audio` can be
        # huge, and we don't want to spam the terminal with the full request
        # payload (especially for ASR audio paths where someone might pipe
        # binary). Just key/value-summarize.
        log_params = {}
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, str) and len(v) > 80:
                    log_params[k] = f'<{len(v)} chars>'
                else:
                    log_params[k] = v
        log.info('AddonProcess[%s]: -> request id=%s task=%s params=%r',
                 self.addon_id, req_id, task, log_params)
        try:
            self._write_frame(protocol.make_request(req_id, task, params))
        except Exception as exc:
            log.warning('AddonProcess[%s]: write failed for request %s: %s',
                        self.addon_id, req_id, exc)
            with self._requests_lock:
                self._requests.pop(req_id, None)
            req._fail(protocol.ErrorCode.INTERNAL, f'failed to send request: {exc}')
            return req

        if timeout is not None:
            t = threading.Timer(timeout, lambda: self._timeout_request(req_id, timeout))
            t.daemon = True
            t.start()

        return req

    def cancel(self, req_id: str) -> None:
        """Best-effort cancel of a pending request."""
        if req_id not in self._requests:
            return
        try:
            self._write_frame(protocol.make_cancel(req_id))
        except Exception as exc:
            log.warning('AddonProcess[%s]: failed to send cancel: %s', self.addon_id, exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _host_version(self) -> str:
        try:
            from subtitld import __version__
            return str(__version__)
        except Exception:
            return 'unknown'

    def _write_frame(self, frame: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f'add-on {self.addon_id} not started')
        data = protocol.encode(frame)
        with self._write_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(f'add-on {self.addon_id} stdin closed: {exc}') from exc

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            for raw in iter(stdout.readline, b''):
                if not raw:
                    break
                try:
                    frame = protocol.decode(raw)
                except protocol.ProtocolError as exc:
                    log.warning('AddonProcess[%s]: bad frame %r: %s',
                                self.addon_id, raw, exc)
                    continue
                self._dispatch(frame)
        except Exception:
            log.exception('AddonProcess[%s]: stdout reader crashed', self.addon_id)
        finally:
            # Process exited (or pipe closed). Fail anything still pending so
            # callers don't hang.
            log.info('AddonProcess[%s]: stdout closed', self.addon_id)
            self._fail_all_pending(
                protocol.ErrorCode.PROCESS_EXITED,
                'add-on process exited',
            )
            # If we hadn't gotten hello yet, unblock start().
            if not self._hello_event.is_set():
                self._hello_error = 'process exited before hello'
                self._hello_event.set()

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        # We deliberately write to sys.stderr instead of going through
        # `logging`. The host's root logger has no `basicConfig`, so its
        # default level is WARNING and every `log.info` from the addon would
        # be silently filtered out — exactly the opposite of what's useful
        # for debugging an addon. Direct stderr writes also can't get
        # buffered behind a queued log handler when the addon crashes.
        import sys as _sys
        prefix = f'[addon:{self.addon_id}] '.encode('utf-8')
        try:
            for raw in iter(stderr.readline, b''):
                if not raw:
                    break
                # Pass through verbatim (already utf-8 from the addon).
                # Strip a single trailing newline so our prefix lines up.
                if raw.endswith(b'\n'):
                    raw = raw[:-1]
                _sys.stderr.buffer.write(prefix + raw + b'\n')
                _sys.stderr.flush()
        except Exception:
            log.exception('AddonProcess[%s]: stderr reader crashed', self.addon_id)

    def _dispatch(self, frame: dict) -> None:
        ftype = frame.get('type')

        # Hello arrives before any request response, exactly once.
        if ftype == 'hello':
            try:
                addon_id, version, _caps = protocol.validate_hello(frame)
            except protocol.ProtocolError as exc:
                self._hello_error = str(exc)
                self._hello_event.set()
                return
            # Tolerate manifest/hello id mismatch with a warning rather than
            # a hard fail — useful when the manifest was edited but the
            # binary wasn't rebuilt.
            if self.addon_id and addon_id != self.addon_id:
                log.warning('AddonProcess[%s]: hello id %r differs from manifest',
                            self.addon_id, addon_id)
            self._hello_frame = frame
            self._hello_event.set()
            return

        if ftype == 'hello_error':
            self._hello_error = str(frame.get('message', 'unknown error'))
            self._hello_event.set()
            return

        req_id = frame.get('id')
        if not req_id:
            log.warning('AddonProcess[%s]: frame without id: %r', self.addon_id, frame)
            return

        with self._requests_lock:
            req = self._requests.get(req_id)
        if req is None:
            log.warning('AddonProcess[%s]: frame for unknown request %s',
                        self.addon_id, req_id)
            return

        # Echo what came back, but keep the line short. `result` data can
        # contain wav metadata (small, safe), `error` is small, `progress`
        # is just a value+message, `partial` we summarise.
        ftype = frame.get('type')
        data = frame.get('data')
        if ftype in ('result', 'error'):
            log.info('AddonProcess[%s]: <- %s id=%s data=%r',
                     self.addon_id, ftype, req_id, data)
        elif ftype == 'progress' and isinstance(data, dict):
            log.info('AddonProcess[%s]: <- progress id=%s value=%.2f msg=%r',
                     self.addon_id, req_id,
                     float(data.get('value', 0.0)), data.get('message', ''))
        elif ftype == 'partial':
            log.info('AddonProcess[%s]: <- partial id=%s', self.addon_id, req_id)

        terminal = req._deliver(frame)
        if terminal:
            with self._requests_lock:
                self._requests.pop(req_id, None)
            # Schedule destruction on the request's home thread, AFTER any
            # queued result/error metacall has been delivered to its slot.
            # This is the safe Qt-idiomatic counterpart to popping the
            # Python reference: the parent (AddonProcess) keeps the QObject
            # alive in the C++ sense, and `deleteLater` releases it when
            # the event loop next idles on that thread.
            req.deleteLater()
        self._last_activity = time.monotonic()

    def _fail_all_pending(self, code: str, message: str) -> None:
        with self._requests_lock:
            pending = list(self._requests.items())
            self._requests.clear()
        for _id, req in pending:
            req._fail(code, message)

    def _timeout_request(self, req_id: str, timeout: float) -> None:
        with self._requests_lock:
            req = self._requests.pop(req_id, None)
        if req is None:
            return
        log.warning('AddonProcess[%s]: request %s timed out after %.1fs',
                    self.addon_id, req_id, timeout)
        try:
            self._write_frame(protocol.make_cancel(req_id))
        except Exception:
            pass
        req._fail(protocol.ErrorCode.TIMEOUT, f'timed out after {timeout:.0f}s')

    def _terminate(self) -> None:
        if self._proc is None:
            return
        try:
            if os.name == 'posix':
                # killpg so the whole session goes (in case the add-on spawned
                # subprocesses of its own — common with PyTorch / numpy
                # multiprocessing).
                os.killpg(os.getpgid(self._proc.pid), _signal.SIGTERM)
            else:
                self._proc.terminate()
        except (ProcessLookupError, OSError):
            pass

    def _kill(self, reason: str) -> None:
        if self._proc is None:
            return
        log.warning('AddonProcess[%s]: killing (%s)', self.addon_id, reason)
        try:
            if os.name == 'posix':
                os.killpg(os.getpgid(self._proc.pid), _signal.SIGKILL)
            else:
                self._proc.kill()
        except (ProcessLookupError, OSError):
            pass
