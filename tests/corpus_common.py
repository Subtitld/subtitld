"""Shared by the test_corpus_* suites: where the real-file corpus is, and
skipping a suite when it has not been fetched (tests/corpus/fetch.py).

Import it after the suite has set up its off-screen Qt and scratch config
directories, like the other suites do.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'corpus'))
from fetch import folder  # noqa: E402

CORPUS = folder()


def need(*paths):
    """The corpus files a suite reads, as Paths; the suite stops here, passing, when any is missing."""
    found = [CORPUS / path for path in paths]
    missing = [str(path) for path in found if not path.is_file()]
    if missing:
        print(f'SKIP: corpus not fetched (python tests/corpus/fetch.py); missing {missing[0]}')
        sys.exit(0)
    return found
