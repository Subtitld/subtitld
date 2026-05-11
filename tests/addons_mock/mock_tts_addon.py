"""Minimal mock TTS add-on for end-to-end testing of the add-on protocol.

This is what a "real" PyInstaller-built add-on (Piper, Coqui, etc.) would
emit, except it skips actual speech synthesis and just writes a 0.5s sine
wave to the requested output path. Enough to exercise:

  - hello / ready handshake
  - request → progress → result frames
  - error reporting
  - graceful shutdown

Run as:
    python3 tests/addons_mock/mock_tts_addon.py
(then write JSON-line frames on stdin)

The pyinstaller equivalent would be `pyinstaller --onedir mock_tts_addon.py`.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import wave


def _send(frame):
    sys.stdout.write(json.dumps(frame, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def _log(msg):
    sys.stderr.write(f'[mock-tts] {msg}\n')
    sys.stderr.flush()


def _write_sine_wav(path, duration_sec=0.5, sample_rate=16000, freq=440.0):
    n_frames = int(duration_sec * sample_rate)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for i in range(n_frames):
            sample = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            w.writeframesraw(struct.pack('<h', sample))


def main():
    _log('starting up')
    _send({
        'type': 'hello',
        'protocol': 1,
        'addon': 'mock-tts',
        'version': '0.0.1',
        'capabilities': [
            {
                'task': 'tts.synthesize',
                'languages': ['en', 'pt-br'],
                'voices': [
                    {'id': 'mock-female-en', 'language': 'en'},
                    {'id': 'mock-male-pt', 'language': 'pt-br'},
                ],
            },
        ],
    })

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f'bad frame: {exc}')
            continue

        ftype = frame.get('type')
        _log(f'got frame: {ftype}')

        if ftype == 'ready':
            continue
        if ftype == 'shutdown':
            _log('shutdown received, exiting')
            return 0
        if ftype == 'cancel':
            # Mock doesn't have long-running work to cancel; ignore.
            continue
        if ftype == 'tts.synthesize':
            req_id = frame.get('id')
            params = frame.get('params', {}) or {}
            output_path = params.get('output_path')
            voice = params.get('voice', '')

            if not output_path:
                _send({
                    'id': req_id, 'type': 'error',
                    'code': 'bad_params', 'message': 'output_path required',
                })
                continue

            # Emit a couple of progress frames first.
            _send({'id': req_id, 'type': 'progress', 'value': 0.25, 'message': 'warming up'})
            _send({'id': req_id, 'type': 'progress', 'value': 0.75, 'message': 'rendering'})

            try:
                _write_sine_wav(output_path)
            except OSError as exc:
                _send({
                    'id': req_id, 'type': 'error',
                    'code': 'internal', 'message': f'write failed: {exc}',
                })
                continue

            _send({
                'id': req_id, 'type': 'result',
                'data': {
                    'path': output_path,
                    'duration_sec': 0.5,
                    'sample_rate': 16000,
                    'channels': 1,
                    'voice': voice,
                },
            })
        else:
            _log(f'unknown frame type: {ftype!r}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
