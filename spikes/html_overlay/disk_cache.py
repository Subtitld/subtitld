"""Spike: on-disk cache of rendered overlay layers.

A layer is one cue at one time inside it: (cue key, offset in ms). The cue key
hashes everything that changes the cue's pixels, so an edit is simply a new key
and undo finds the old one again. Each layer is a crop rect plus a reference to
an image stored once by content hash, so a cue's static hold is one image.

Images are stored as raw premultiplied ARGB32, the format Qt draws from,
compressed with zstd (level 1) when available, else zlib (level 1). On these
mostly transparent strips that beats PNG, WebP, QOI and JPEG XL on speed and
most of them on size, and loading needs no pixel conversion. Each image records
its codec, so a cache written with one still reads where the other is in use.

SQLite holds both tables (WAL, so the reader threads don't wait on the writer).
Encoding and writing run on one writer thread, loading and decoding on reader
threads; results come back to the GUI thread through a queued signal.
"""
import hashlib
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QImage

# zstd: in the standard library from Python 3.14, else the zstandard package.
try:
    from compression import zstd as _zstd
    _zstd_compress, _zstd_decompress = (lambda b: _zstd.compress(b, 1)), _zstd.decompress
except ImportError:
    try:
        import zstandard as _zstd
        _zstd_compress = _zstd.ZstdCompressor(level=1).compress
        _zstd_decompress = _zstd.ZstdDecompressor().decompress
    except ImportError:
        _zstd_compress = _zstd_decompress = None

CODECS = {'zlib': (lambda b: zlib.compress(b, 1), zlib.decompress)}
if _zstd_compress is not None:
    CODECS['zstd'] = (_zstd_compress, _zstd_decompress)
WRITE_CODEC = 'zstd' if 'zstd' in CODECS else 'zlib'

SCHEMA_VERSION = 2       # 1: PNG blobs
SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY, codec TEXT NOT NULL, w INTEGER NOT NULL, h INTEGER NOT NULL,
    data BLOB NOT NULL, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS layers (
    key TEXT NOT NULL, off INTEGER NOT NULL,
    x REAL, y REAL, w REAL, h REAL,        -- normalised to the stage; w = 0 for an empty layer
    hash TEXT,                             -- blob, NULL for an empty layer
    used REAL NOT NULL,
    PRIMARY KEY (key, off));
CREATE INDEX IF NOT EXISTS layers_used ON layers (used);
CREATE INDEX IF NOT EXISTS layers_hash ON layers (hash);
"""


class DiskCache(QObject):
    loaded = Signal(object, object, object)    # layer, QRectF or None, QImage or None

    def __init__(self, path, limit_mb, readers=2):
        super().__init__()
        self.path = str(path)
        self.limit = limit_mb * 2**20
        self.codec = WRITE_CODEC
        self.local = threading.local()
        db = self.conn()
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            # A cache: older layouts are dropped, not migrated.
            db.executescript('DROP TABLE IF EXISTS layers; DROP TABLE IF EXISTS blobs;')
            db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        db.executescript(SCHEMA)
        db.commit()
        self.index = set(db.execute('SELECT key, off FROM layers'))
        self.writer = ThreadPoolExecutor(1, thread_name_prefix='overlay-disk-w')
        self.readers = ThreadPoolExecutor(readers, thread_name_prefix='overlay-disk-r')
        # Decoded images by blob hash: a cue's hold is one blob loaded many times.
        self.decoded = OrderedDict()
        self.decoded_lock = threading.Lock()
        self.touched = set()
        self.stats_lock = threading.Lock()
        self.stats = {'loads': 0, 'load_ms': [], 'writes': 0, 'write_ms': [], 'new_blobs': 0,
                      'blob_bytes': 0, 'trimmed': 0}

    def conn(self):
        db = getattr(self.local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=30)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            self.local.db = db
        return db

    def _stat(self, name, ms=None, add=1):
        with self.stats_lock:
            self.stats[name] += add
            if ms is not None:
                self.stats[name.rstrip('s') + '_ms'].append(ms)

    # --- write -------------------------------------------------------------
    def put(self, layer, rect, img):
        """rect: QRectF normalised to the stage, or None for an empty layer."""
        self.index.add(layer)
        self.writer.submit(self._put, layer, rect, img)

    def _put(self, layer, rect, img):
        t0 = time.perf_counter()
        db = self.conn()
        digest = None
        if img is not None:
            # 32-bit rows are always 4-byte aligned: no padding to strip.
            img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            raw = bytes(img.constBits())[:img.width() * img.height() * 4]
            digest = hashlib.sha1(raw).hexdigest()
            if db.execute('SELECT 1 FROM blobs WHERE hash = ?', (digest,)).fetchone() is None:
                data = CODECS[self.codec][0](raw)
                db.execute('INSERT OR IGNORE INTO blobs VALUES (?, ?, ?, ?, ?, ?)',
                           (digest, self.codec, img.width(), img.height(), data, len(data)))
                self._stat('new_blobs')
                self._stat('blob_bytes', add=len(data))
        x, y, w, h = (rect.x(), rect.y(), rect.width(), rect.height()) if rect is not None else (0, 0, 0, 0)
        db.execute('INSERT OR REPLACE INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   (layer[0], layer[1], x, y, w, h, digest, time.time()))
        db.commit()
        self._stat('writes', (time.perf_counter() - t0) * 1000)

    # --- read --------------------------------------------------------------
    def get(self, layer):
        self.touched.add(layer)
        self.readers.submit(self._get, layer)

    def _get(self, layer):
        t0 = time.perf_counter()
        db = self.conn()
        row = db.execute('SELECT x, y, w, h, hash FROM layers WHERE key = ? AND off = ?', layer).fetchone()
        if row is None:
            self.loaded.emit(layer, False, None)   # gone (trimmed): render it again
            return
        x, y, w, h, digest = row
        if digest is None:
            self.loaded.emit(layer, None, None)
        else:
            with self.decoded_lock:
                img = self.decoded.get(digest)
                if img is not None:
                    self.decoded.move_to_end(digest)
            if img is None:
                codec, bw, bh, data = db.execute('SELECT codec, w, h, data FROM blobs WHERE hash = ?',
                                                 (digest,)).fetchone()
                raw = CODECS[codec][1](data)
                # copy(): the QImage must own its pixels, not borrow `raw`.
                img = QImage(raw, bw, bh, bw * 4, QImage.Format_ARGB32_Premultiplied).copy()
                with self.decoded_lock:
                    self.decoded[digest] = img
                    while len(self.decoded) > 64:
                        self.decoded.popitem(last=False)
            self.loaded.emit(layer, QRectF(x, y, w, h), img)
        self._stat('loads', (time.perf_counter() - t0) * 1000)

    # --- housekeeping ------------------------------------------------------
    def maintain(self):
        """Record what was read since last time, and trim to the size limit
        (least recently used layers first, then blobs nothing points at)."""
        touched, self.touched = self.touched, set()
        self.writer.submit(self._maintain, touched)

    def _maintain(self, touched):
        db = self.conn()
        now = time.time()
        db.executemany('UPDATE layers SET used = ? WHERE key = ? AND off = ?', [(now, k, o) for k, o in touched])
        total = db.execute('SELECT COALESCE(SUM(size), 0) FROM blobs').fetchone()[0]
        while total > self.limit:
            gone = db.execute('SELECT key, off FROM layers ORDER BY used LIMIT 100').fetchall()
            if not gone:
                break
            db.executemany('DELETE FROM layers WHERE key = ? AND off = ?', gone)
            db.execute('DELETE FROM blobs WHERE hash NOT IN (SELECT hash FROM layers WHERE hash IS NOT NULL)')
            for layer in gone:
                self.index.discard(tuple(layer))
            self._stat('trimmed', add=len(gone))
            total = db.execute('SELECT COALESCE(SUM(size), 0) FROM blobs').fetchone()[0]
        db.commit()

    def summary(self):
        db = self.conn()
        size, blobs = db.execute('SELECT COALESCE(SUM(size), 0), COUNT(*) FROM blobs').fetchone()
        layers = db.execute('SELECT COUNT(*) FROM layers').fetchone()[0]
        return size, blobs, layers

    def close(self):
        self.writer.shutdown(wait=True)
        self.readers.shutdown(wait=True)
