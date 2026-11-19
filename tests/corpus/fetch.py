"""Fetch the real-file corpus the test_corpus_* suites read.

Files made by the tools themselves (FFmpeg's FATE samples and its expected
outputs, Subtitle Edit's, praatIO's, pympi's and pysubs2's test files), each
pinned by URL and checked against its SHA-256 in manifest.json. They are not
kept in this repository: their licences differ from Subtitld's and each
other's, so only the list is.

    python tests/corpus/fetch.py            # into ~/.cache/subtitld/corpus
    SUBTITLD_CORPUS=/elsewhere python tests/corpus/fetch.py

Files already there with the right checksum are left alone.
"""
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def folder():
    """Where the corpus lives: $SUBTITLD_CORPUS, or ~/.cache/subtitld/corpus."""
    return Path(os.environ.get('SUBTITLD_CORPUS') or Path.home() / '.cache' / 'subtitld' / 'corpus')


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = folder()
    entries = json.loads((HERE / 'manifest.json').read_text())
    fetched = bad = 0
    for entry in entries:
        path = target / entry['path']
        if path.is_file() and _sha256(path) == entry['sha256']:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + '.part')
        with urllib.request.urlopen(entry['url'], timeout=60) as response, open(partial, 'wb') as out:
            out.write(response.read())
        if _sha256(partial) != entry['sha256']:
            partial.unlink()
            print(f'checksum mismatch: {entry["url"]}', file=sys.stderr)
            bad += 1
            continue
        partial.replace(path)
        fetched += 1
    print(f'{len(entries)} files in {target}: {fetched} fetched, {bad} failed')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
