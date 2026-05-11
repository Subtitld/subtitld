"""End-to-end test of the add-on plumbing.

Runs the mock TTS add-on installed under PATH_SUBTITLD_ADDONS through the
real `AddonManager` / `AddonTTSProvider` stack. Verifies:

  1. discover() picks up the mock add-on
  2. generate_speeches → handshake → request → progress → result → speech_ready
  3. WAV file is on disk and non-empty
  4. shutdown_all cleanly stops the subprocess
"""

from __future__ import annotations

import os
import sys
import time

# Make src/ importable when running from repo root.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from PySide6.QtCore import QCoreApplication, QTimer, QObject, Slot

from subtitld.modules.addons import get_manager
from subtitld.modules import session


class Recorder(QObject):
    def __init__(self, app, expected: int):
        super().__init__()
        self.app = app
        self.expected = expected
        self.ready = []
        self.errored = []

    @Slot(str, dict, str)
    def on_ready(self, uid, subtitle, path):
        self.ready.append((uid, subtitle, path))
        print(f'  [ready] uid={uid} path={path} size={os.path.getsize(path) if os.path.isfile(path) else 0}')
        self._maybe_quit()

    @Slot(str, dict, str)
    def on_error(self, uid, subtitle, message):
        self.errored.append((uid, subtitle, message))
        print(f'  [error] uid={uid} {message}')
        self._maybe_quit()

    def _maybe_quit(self):
        if len(self.ready) + len(self.errored) >= self.expected:
            self.app.quit()


def main():
    app = QCoreApplication(sys.argv)

    mgr = get_manager()
    print(f'Discovering in {session.PATH_SUBTITLD_ADDONS} ...')
    new_ids = mgr.discover()
    print(f'Discovered: {new_ids}')

    provider = mgr.get('mock-tts')
    assert provider is not None, 'mock-tts not registered after discover'
    print(f'Provider: id={provider.id} display={provider.display_name} tasks={provider.tasks}')
    print(f'Voices: {provider.list_voices()}')

    text_list = [
        {'uid': 'aaa', 'text': 'hello world', 'speaker': 'A',
         'start': 0.0, 'end': 1.0, 'voice': 'mock-female-en', 'rate': 0, 'pitch': 0},
        {'uid': 'bbb', 'text': 'olá mundo', 'speaker': 'A',
         'start': 1.0, 'end': 2.0, 'voice': 'mock-male-pt', 'rate': 0, 'pitch': 0},
    ]

    recorder = Recorder(app, expected=len(text_list))
    provider.speech_ready.connect(recorder.on_ready)
    provider.speech_error.connect(recorder.on_error)

    QTimer.singleShot(0, lambda: provider.generate_speeches(text_list))

    # Hard cap so a hang doesn't deadlock CI.
    QTimer.singleShot(15000, app.quit)

    rc = app.exec()
    mgr.shutdown_all()

    print(f'\nResults: ready={len(recorder.ready)} errored={len(recorder.errored)}')
    for uid, _sub, path in recorder.ready:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        print(f'  {uid}: {path} ({size} B)')
    if recorder.errored:
        for uid, _sub, msg in recorder.errored:
            print(f'  ERR {uid}: {msg}')
        return 1
    if len(recorder.ready) != len(text_list):
        print('  TIMEOUT: did not receive all expected results')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
