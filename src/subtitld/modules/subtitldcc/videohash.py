"""Video fingerprints for matching subtitles. See SPEC.md for the exact algorithms.

    from subtitld_videohash import hash_file
    hash_file("/videos/sintel.mkv")
    # {"size": 129241752, "subtitld": "9f2c…", "opensubtitles": "8e245d9679d31e12"}

Only the first and last bytes of the file are read.
"""

import hashlib
import os
import struct
from typing import BinaryIO

VERSION = 1
PREFIX = b"subtitld-videohash-v1\x00"
EDGE = 1024 * 1024  # bytes read from each end for the subtitld hash
OS_CHUNK = 64 * 1024  # bytes summed from each end for the OpenSubtitles hash
_MASK = 0xFFFFFFFFFFFFFFFF


def _read_at(handle: BinaryIO, offset: int, length: int) -> bytes:
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise OSError("File shrank while it was being read")
    return data


def subtitld_hash(handle: BinaryIO, size: int) -> str:
    edge = min(size, EDGE)
    digest = hashlib.sha256(PREFIX)
    digest.update(struct.pack("<Q", size))
    digest.update(_read_at(handle, 0, edge))
    digest.update(_read_at(handle, size - edge, edge))
    return digest.hexdigest()


def opensubtitles_hash(handle: BinaryIO, size: int) -> str | None:
    if size < 2 * OS_CHUNK:
        return None
    value = size
    for offset in (0, size - OS_CHUNK):
        for (number,) in struct.iter_unpack("<Q", _read_at(handle, offset, OS_CHUNK)):
            value = (value + number) & _MASK
    return f"{value:016x}"


def hash_handle(handle: BinaryIO, size: int | None = None) -> dict:
    """Both fingerprints for an open binary file (must be seekable)."""
    if size is None:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
    return {
        "size": size,
        "subtitld": subtitld_hash(handle, size),
        "opensubtitles": opensubtitles_hash(handle, size),
    }


def hash_file(path: str | os.PathLike) -> dict:
    with open(path, "rb") as handle:
        return hash_handle(handle, os.fstat(handle.fileno()).st_size)
