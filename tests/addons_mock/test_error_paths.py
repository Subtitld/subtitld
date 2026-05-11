"""Error-path tests.

Exercises:
  1. bad_params (missing output_path) → speech_error
  2. process death mid-conversation → pending request gets PROCESS_EXITED
  3. handshake timeout (binary that never emits hello)
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

import logging
logging.basicConfig(level=logging.INFO)

from PySide6.QtCore import QCoreApplication, QTimer, Slot, QObject

from subtitld.modules.addons import get_manager
from subtitld.modules.addons.process import AddonProcess
from subtitld.modules.addons.protocol import ErrorCode
from subtitld.modules import session


def case_bad_params():
    print('\n=== case 1: bad_params ===')
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    mgr = get_manager()
    mgr.discover()
    provider = mgr.get('mock-tts')
    assert provider is not None

    errored = []
    ready = []
    provider.speech_error.connect(lambda u, s, m: errored.append(m) or app.quit())
    provider.speech_ready.connect(lambda *a: ready.append(a) or app.quit())

    def kick():
        # Pass an unwritable output_path by overriding the cache to a non-existent path.
        # Easier: rely on mock's bad_params branch by forcing empty output_path.
        # Our AddonTTSProvider always sets output_path, so we test from raw process.
        ap = AddonProcess({'id': 'mock-tts', 'startup_timeout_sec': 5},
                          str(session.PATH_SUBTITLD_ADDONS / 'mock-tts/bin/mock-tts-addon'))
        ap.start()
        req = ap.request('tts.synthesize', {})
        req.error.connect(lambda code, msg: (
            print(f'  got error: {code} / {msg}'),
            errored.append((code, msg)),
            ap.shutdown(),
            app.quit()
        ))
        req.result.connect(lambda d: (ready.append(d), ap.shutdown(), app.quit()))

    QTimer.singleShot(0, kick)
    QTimer.singleShot(10000, app.quit)
    app.exec()
    assert errored, 'expected an error'
    assert errored[0][0] == 'bad_params', f'expected bad_params, got {errored[0][0]}'
    print('  PASS')


def case_process_death():
    print('\n=== case 2: process death mid-request ===')
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    # A fake binary that emits hello, then sleeps forever — we kill -9 it
    # while a request is pending.
    with tempfile.TemporaryDirectory() as td:
        bin_path = os.path.join(td, 'sleeper')
        with open(bin_path, 'w') as f:
            f.write(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, sys, time
                sys.stdout.write(json.dumps({"type":"hello","protocol":1,
                    "addon":"sleeper","version":"0","capabilities":[]}) + "\\n")
                sys.stdout.flush()
                # Read frames forever, do nothing.
                for _ in sys.stdin:
                    pass
                time.sleep(3600)
            '''))
        os.chmod(bin_path, 0o755)

        ap = AddonProcess({'id': 'sleeper', 'startup_timeout_sec': 5}, bin_path)
        ap.start()
        req = ap.request('tts.synthesize', {'output_path': '/tmp/never.wav'})

        got = []
        req.error.connect(lambda code, msg: (got.append((code, msg)), app.quit()))
        req.result.connect(lambda d: (got.append(('result', d)), app.quit()))

        # Kill the process forcefully.
        QTimer.singleShot(200, lambda: os.kill(ap._proc.pid, signal.SIGKILL))
        QTimer.singleShot(5000, app.quit)
        app.exec()

        ap.shutdown()
        assert got, 'expected an error frame after process death'
        code = got[0][0]
        assert code in (ErrorCode.PROCESS_EXITED, ErrorCode.INTERNAL), f'got {code}'
        print(f'  PASS — got {code} / {got[0][1]!r}')


def case_handshake_timeout():
    print('\n=== case 3: handshake timeout ===')
    with tempfile.TemporaryDirectory() as td:
        bin_path = os.path.join(td, 'silent')
        with open(bin_path, 'w') as f:
            f.write('#!/usr/bin/env python3\nimport time; time.sleep(60)\n')
        os.chmod(bin_path, 0o755)

        ap = AddonProcess({'id': 'silent', 'startup_timeout_sec': 1.0}, bin_path)
        try:
            ap.start()
        except TimeoutError as exc:
            print(f'  PASS — timed out as expected: {exc}')
            return
        finally:
            ap.shutdown()
        raise AssertionError('expected TimeoutError')


def main():
    case_bad_params()
    case_process_death()
    case_handshake_timeout()
    print('\nAll error-path cases passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
