"""Minimal mock `media.download` add-on: "gets the video" of any page by writing a small file named after
it, with progress on the way. A link with "nothing" in it has no video (unsupported_url); one with "slow" in
it waits for a cancel, and removes what it wrote when it comes.

Tests that go through discovery call `install(session.PATH_SUBTITLD_ADDONS)`
first: it lays the add-on out as the installer does.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import time
from pathlib import Path

ADDON_ID = 'mock-media'


def install(addons_dir) -> Path:
    root = Path(addons_dir) / ADDON_ID
    exe = root / 'bin' / 'mock-media-addon'
    exe.parent.mkdir(parents=True, exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps({
        'manifest_version': 1,
        'id': ADDON_ID,
        'version': '0.0.1',
        'display_name': 'Mock media',
        'executable': 'bin/mock-media-addon',
        'protocol': 1,
        'startup_timeout_sec': 10,
        'tasks': ['media.download'],
    }, indent=2), encoding='utf-8')
    exe.write_text('#!/bin/sh\n'
                   f'exec {shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} "$@"\n',
                   encoding='utf-8')
    exe.chmod(0o755)
    return root


_lock = threading.Lock()
_cancelled = set()


def emit(frame):
    with _lock:
        sys.stdout.write(json.dumps(frame) + '\n')
        sys.stdout.flush()


def download(req_id, params):
    url, folder = params['url'], params['output_dir']
    if 'nothing' in url:
        emit({'id': req_id, 'type': 'error', 'code': 'unsupported_url', 'message': f'Unsupported URL: {url}'})
        return
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'mock video [abc].mp4')
    with open(path + '.part', 'wb') as out:
        out.write(b'\0' * 1000)
    steps = 200 if 'slow' in url else 3
    for step in range(steps):
        if req_id in _cancelled:
            os.unlink(path + '.part')
            emit({'id': req_id, 'type': 'error', 'code': 'cancelled', 'message': 'cancelled'})
            return
        emit({'id': req_id, 'type': 'progress', 'value': (step + 1) / steps, 'message': ''})
        time.sleep(0.05)
    os.replace(path + '.part', path)
    emit({'id': req_id, 'type': 'result', 'data': {'path': path, 'title': 'mock video'}})


def main():
    emit({'type': 'hello', 'protocol': 1, 'addon': ADDON_ID, 'version': '0.0.1',
          'capabilities': [{'task': 'media.download'}]})
    for line in sys.stdin:
        frame = json.loads(line)
        if frame.get('type') == 'shutdown':
            return
        if frame.get('type') == 'cancel':
            _cancelled.add(frame.get('target'))
        elif frame.get('type') == 'media.download':
            threading.Thread(target=download, args=(frame['id'], frame['params']), daemon=True).start()


if __name__ == '__main__':
    main()
