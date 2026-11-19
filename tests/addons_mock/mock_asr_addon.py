"""Minimal mock speech recognition add-on: transcribes and aligns, with the
words timed, for testing word timings end to end (no audio is listened to).

    asr.transcribe  two segments, each with its `words`
    asr.align       the segments asked about, their words shared out over
                    each segment's time by letters

Tests that go through discovery call `install(session.PATH_SUBTITLD_ADDONS)`
first: it lays the add-on out as the installer does.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

ADDON_ID = 'mock-asr'
TRANSCRIPT = [
    {'start': 1.0, 'end': 2.5, 'text': 'Hello, big world.',
     'words': [{'text': 'Hello', 'start': 1.0, 'end': 1.4}, {'text': 'big', 'start': 1.5, 'end': 1.8},
               {'text': 'world', 'start': 1.9, 'end': 2.5}]},
    {'start': 3.0, 'end': 4.0, 'text': 'Goodbye.', 'words': [{'text': 'Goodbye', 'start': 3.0, 'end': 4.0}]},
]


def install(addons_dir) -> Path:
    root = Path(addons_dir) / ADDON_ID
    exe = root / 'bin' / 'mock-asr-addon'
    exe.parent.mkdir(parents=True, exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps({
        'manifest_version': 1,
        'id': ADDON_ID,
        'version': '0.0.1',
        'display_name': 'Mock ASR',
        'executable': 'bin/mock-asr-addon',
        'protocol': 1,
        'startup_timeout_sec': 10,
        'tasks': ['asr.transcribe', 'asr.align'],
        'languages': ['en'],
    }, indent=2), encoding='utf-8')
    exe.write_text('#!/bin/sh\n'
                   f'exec {shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} "$@"\n',
                   encoding='utf-8')
    exe.chmod(0o755)
    return root


def _send(frame):
    sys.stdout.write(json.dumps(frame, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def _aligned(segment):
    words = [word for word in str(segment.get('text') or '').split() if any(c.isalnum() for c in word)]
    start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
    total = sum(len(word) for word in words) or 1
    out, at = [], start
    for word in words:
        until = at + (end - start) * len(word) / total
        out.append({'text': word.strip('.,!?"'), 'start': round(at, 3), 'end': round(until, 3)})
        at = until
    return dict(segment, words=out)


def main():
    _send({'type': 'hello', 'protocol': 1, 'addon': ADDON_ID, 'version': '0.0.1',
           'capabilities': [{'task': 'asr.transcribe', 'languages': ['en']}, {'task': 'asr.align', 'languages': ['en']}]})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        frame = json.loads(line)
        kind, req_id = frame.get('type'), frame.get('id')
        params = frame.get('params', {}) or {}
        if kind == 'shutdown':
            return 0
        if kind == 'asr.transcribe':
            for segment in TRANSCRIPT:
                _send({'id': req_id, 'type': 'partial', 'data': {'segment': segment}})
            _send({'id': req_id, 'type': 'result', 'data': {'segments': TRANSCRIPT}})
        elif kind == 'asr.align':
            _send({'id': req_id, 'type': 'result',
                   'data': {'segments': [_aligned(segment) for segment in params.get('segments') or []]}})
    return 0


if __name__ == '__main__':
    sys.exit(main())
