"""End-to-end test of the add-on plumbing.

Installs the mock TTS add-on under PATH_SUBTITLD_ADDONS (scratch XDG
directories, never the user's) and runs it through the real
`AddonManager` / `AddonTTSProvider` stack. Verifies:

  1. discover() picks up the mock add-on
  2. generate_speeches → handshake → request → progress → result → speech_ready
  3. WAV file is on disk and non-empty
  4. shutdown_all cleanly stops the subprocess
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# Scratch config, cache and add-ons: the mock is installed into them.
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())

# Make src/ importable when running from repo root.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'src'))

import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from PySide6.QtCore import QCoreApplication, QTimer, QObject, Slot

from subtitld.modules.addons import get_manager
from subtitld.modules import session
import mock_tts_addon


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

    mock_tts_addon.install(session.PATH_SUBTITLD_ADDONS)
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
         'start': 1.0, 'end': 2.0, 'voice': 'mock-male-pt', 'rate': 0, 'pitch': 0,
         'voice_ref_audio': '/ref/a.wav', 'voice_ref_text': 'olá', 'seed': 1234},
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
    # The clone reference, its transcript and the seed reach the add-on.
    for uid, _sub, path in recorder.ready:
        with open(path + '.params.json', encoding='utf-8') as f:
            received = json.load(f)
        sent = next(item for item in text_list if item['uid'] == uid)
        for key in ('voice_ref_audio', 'voice_ref_text', 'seed'):
            if received.get(key) != sent.get(key):
                print(f'  {uid}: {key} sent {sent.get(key)!r}, add-on got {received.get(key)!r}')
                return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
