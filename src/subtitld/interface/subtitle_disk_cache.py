"""Rendered subtitle layers on disk, so a project's subtitles are not
rendered again the next time it is opened (subtitle_renderer keeps them in
memory meanwhile).

A layer is kept by the renderer's key (everything that changes its pixels)
as a rect on the stage and a reference to an image stored once, by the hash
of its pixels: the same subtitle at two moments of an animated style, or
the same words in two places, is one image.

Images are raw premultiplied ARGB32, the format Qt draws from, compressed
with zstd (level 1) where Python has it (3.14 on), zlib (level 1) otherwise:
on these mostly transparent strips that beats PNG, WebP, QOI and JPEG XL on
speed and most of them on size, and loading needs no pixel conversion. Each
image says which it was written with.

SQLite holds both tables, in WAL mode so reading never waits for writing.
Writing runs on a thread of its own, reading on two; what is read comes back
to the GUI thread by a queued signal. Past the size limit the layers used
longest ago go, with the images nothing else points at, down to 90% of it. The cache is only
a cache: a file it cannot read, or one from an older layout, is started
over.
"""

import hashlib
import os
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QImage

try:
    from compression import zstd as _zstd
    _CODECS = {'zstd': (lambda data: _zstd.compress(data, 1), _zstd.decompress)}
except ImportError:
    _CODECS = {}
_CODECS['zlib'] = (lambda data: zlib.compress(data, 1), zlib.decompress)
WRITE_CODEC = next(iter(_CODECS))

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    hash TEXT PRIMARY KEY, codec TEXT NOT NULL, w INTEGER NOT NULL, h INTEGER NOT NULL,
    data BLOB NOT NULL, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS layers (
    key TEXT PRIMARY KEY,
    x REAL, y REAL, w REAL, h REAL,        -- on the stage, 0..1; w = 0 for an empty layer
    hash TEXT,                             -- the image, NULL for an empty layer
    used REAL NOT NULL);
CREATE INDEX IF NOT EXISTS layers_used ON layers (used);
CREATE INDEX IF NOT EXISTS layers_hash ON layers (hash);
"""
DECODED = 64            # images kept decoded: a subtitle's layers are often one image


class SubtitleDiskCache(QObject):
    # key, then (QRectF or None, QImage or None), or None when the layer is gone
    loaded = Signal(str, object)

    def __init__(self, path, limit_mb=512, readers=2):
        super().__init__()
        self.path = str(path)
        self.limit = limit_mb * 2**20
        self.local = threading.local()
        self.connections = []
        self.connections_lock = threading.Lock()
        try:
            self.index = self._open()
        except sqlite3.DatabaseError:
            # Not a database any more (a crash mid-write, a stray file): anew.
            self._close_connections()
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.remove(self.path + suffix)
                except OSError:
                    pass
            self.local = threading.local()
            self.index = self._open()
        self.writer = ThreadPoolExecutor(1, thread_name_prefix='subtitle-cache-write')
        self.readers = ThreadPoolExecutor(readers, thread_name_prefix='subtitle-cache-read')
        self.decoded = OrderedDict()
        self.decoded_lock = threading.Lock()
        self.touched = set()
        self.closed = False
        self.stats = {'loads': 0, 'writes': 0, 'trimmed': 0}

    def _open(self):
        db = self.connection()
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            db.executescript('DROP TABLE IF EXISTS layers; DROP TABLE IF EXISTS images;')
            db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        db.executescript(SCHEMA)
        db.commit()
        return {key for (key,) in db.execute('SELECT key FROM layers')}

    def connection(self):
        db = getattr(self.local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            self.local.db = db
            with self.connections_lock:
                self.connections.append(db)
        return db

    def _close_connections(self):
        with self.connections_lock:
            for db in self.connections:
                try:
                    db.close()
                except sqlite3.Error:
                    pass
            self.connections.clear()

    # --- write -------------------------------------------------------------
    def put(self, key, rect, image):
        """Keep a layer: `rect` on the stage (0..1) and its image, or None
        and None for an empty one."""
        if self.closed or key in self.index:
            return
        self.index.add(key)
        if image is not None:
            image = image.copy()        # the writer reads it on its thread
        self.writer.submit(self._put, key, rect, image)

    def _put(self, key, rect, image):
        try:
            db = self.connection()
            digest = None
            if image is not None:
                image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
                # 32-bit rows are always 4-byte aligned: no padding to strip.
                raw = bytes(image.constBits())[:image.width() * image.height() * 4]
                digest = hashlib.sha1(raw).hexdigest()
                if db.execute('SELECT 1 FROM images WHERE hash = ?', (digest,)).fetchone() is None:
                    data = _CODECS[WRITE_CODEC][0](raw)
                    db.execute('INSERT OR IGNORE INTO images VALUES (?, ?, ?, ?, ?, ?)',
                               (digest, WRITE_CODEC, image.width(), image.height(), data, len(data)))
            x, y, w, h = (rect.x(), rect.y(), rect.width(), rect.height()) if rect is not None else (0, 0, 0, 0)
            db.execute('INSERT OR REPLACE INTO layers VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (key, x, y, w, h, digest, time.time()))
            db.commit()
            self.stats['writes'] += 1
        except sqlite3.Error as error:
            self.index.discard(key)
            print(f'[subtitles] could not keep a layer on disk: {error}', flush=True)

    # --- read --------------------------------------------------------------
    def get(self, key):
        """Read a layer; `loaded` brings it."""
        if self.closed:
            return
        self.touched.add(key)
        self.readers.submit(self._get, key)

    def _get(self, key):
        try:
            db = self.connection()
            row = db.execute('SELECT x, y, w, h, hash FROM layers WHERE key = ?', (key,)).fetchone()
            if row is None:
                self.index.discard(key)
                self.loaded.emit(key, None)       # trimmed meanwhile: render it again
                return
            x, y, w, h, digest = row
            if digest is None:
                self.loaded.emit(key, (None, None))
                return
            with self.decoded_lock:
                image = self.decoded.get(digest)
                if image is not None:
                    self.decoded.move_to_end(digest)
            if image is None:
                found = db.execute('SELECT codec, w, h, data FROM images WHERE hash = ?', (digest,)).fetchone()
                if found is None or found[0] not in _CODECS:
                    self.index.discard(key)
                    self.loaded.emit(key, None)
                    return
                codec, width, height, data = found
                raw = _CODECS[codec][1](data)
                # copy(): the image owns its pixels, not `raw`.
                image = QImage(raw, width, height, width * 4, QImage.Format_ARGB32_Premultiplied).copy()
                with self.decoded_lock:
                    self.decoded[digest] = image
                    while len(self.decoded) > DECODED:
                        self.decoded.popitem(last=False)
            self.stats['loads'] += 1
            self.loaded.emit(key, (QRectF(x, y, w, h), image))
        except (sqlite3.Error, zlib.error, ValueError) as error:
            self.index.discard(key)
            print(f'[subtitles] could not read a layer from disk: {error}', flush=True)
            self.loaded.emit(key, None)

    # --- housekeeping ------------------------------------------------------
    def maintain(self):
        """Note what was read since last time, and trim to the size limit."""
        if self.closed:
            return
        touched, self.touched = self.touched, set()
        self.writer.submit(self._maintain, touched)

    def _maintain(self, touched):
        try:
            db = self.connection()
            now = time.time()
            db.executemany('UPDATE layers SET used = ? WHERE key = ?', [(now, key) for key in touched])
            total = db.execute('SELECT COALESCE(SUM(size), 0) FROM images').fetchone()[0]
            if total > self.limit:
                # The layers used longest ago, until their images (those no
                # other layer shows) free enough: down to 90% of the limit,
                # so the next few layers written do not trim again.
                users = dict(db.execute('SELECT hash, COUNT(*) FROM layers WHERE hash IS NOT NULL GROUP BY hash'))
                sizes = dict(db.execute('SELECT hash, size FROM images'))
                gone = []
                for key, digest in db.execute('SELECT key, hash FROM layers ORDER BY used'):
                    if total <= self.limit * 0.9:
                        break
                    gone.append(key)
                    if digest is not None:
                        users[digest] -= 1
                        if users[digest] == 0:
                            total -= sizes.get(digest, 0)
                db.executemany('DELETE FROM layers WHERE key = ?', [(key,) for key in gone])
                db.execute('DELETE FROM images WHERE hash NOT IN (SELECT hash FROM layers WHERE hash IS NOT NULL)')
                for key in gone:
                    self.index.discard(key)
                self.stats['trimmed'] += len(gone)
            db.commit()
        except sqlite3.Error as error:
            print(f'[subtitles] could not trim the layers on disk: {error}', flush=True)

    def size(self):
        """Bytes of images, images and layers kept."""
        db = self.connection()
        size, images = db.execute('SELECT COALESCE(SUM(size), 0), COUNT(*) FROM images').fetchone()
        return size, images, db.execute('SELECT COUNT(*) FROM layers').fetchone()[0]

    def close(self):
        """Write what is waiting, then stop."""
        if self.closed:
            return
        self.maintain()
        self.closed = True
        self.writer.shutdown(wait=True)
        self.readers.shutdown(wait=True)
        self._close_connections()
