"""A shared project's media files, as collaborators exchange them (LIVE_COLLABORATION_PLAN.md)

A file is known by the SHA-256 of its bytes, and checked chunk by chunk as it arrives (CHUNK_SIZE pieces, each
with its own SHA-256). subtitld.cc keeps the project's media list (what each file is), never the files; the
collaborators who have a file send it to those who ask.

Hashing a large original takes a while, so it runs in the background, once: the index remembers each file's
description by path, size and modification time, and which local file has which hash.

The project's media are its video and what Subtitld derived from it: the waveform and the separated audio
(original, vocals, background). Derived files live in the cache under the local video's key
(utils.get_cache_key), so a received one is stored under the key of the video it is for here.
"""

import hashlib
import json
import logging
import os
import threading

from subtitld.modules import session, utils

log = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024
STEM_ROLES = ('original', 'vocals', 'background')


def describe(path, cancelled=lambda: False, progress=None):
    """{'hash', 'size', 'chunk_size', 'chunks'} of a file, or None when cancelled. `progress(done, total)`."""
    size = os.path.getsize(path)
    whole = hashlib.sha256()
    chunks = []
    done = 0
    with open(path, 'rb') as handle:
        while True:
            if cancelled():
                return None
            block = handle.read(CHUNK_SIZE)
            if not block:
                break
            whole.update(block)
            chunks.append(hashlib.sha256(block).hexdigest())
            done += len(block)
            if progress is not None:
                progress(done, size)
    return {'hash': whole.hexdigest(), 'size': size, 'chunk_size': CHUNK_SIZE, 'chunks': chunks}


class Index:
    """Which local file has which hash, and the descriptions already worked out (by path, size and
    modification time). Kept in the cache folder."""

    def __init__(self, path=None):
        self.path = path or os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'collab', 'media.json')
        self._lock = threading.Lock()
        try:
            with open(self.path, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        self.files = data.get('files') if isinstance(data.get('files'), dict) else {}

    def _stamp(self, path):
        stat = os.stat(path)
        return stat.st_size, stat.st_mtime

    def known(self, path):
        """The description of `path` if it was worked out for the file as it is now."""
        entry = self.files.get(os.path.abspath(path))
        try:
            size, mtime = self._stamp(path)
        except OSError:
            return None
        if entry and entry.get('size') == size and entry.get('mtime') == mtime:
            return {key: entry[key] for key in ('hash', 'size', 'chunk_size', 'chunks')}
        return None

    def remember(self, path, description):
        size, mtime = self._stamp(path)
        with self._lock:
            self.files[os.path.abspath(path)] = dict(description, size=size, mtime=mtime)
            self._save()

    def path_for(self, file_hash):
        """A local file with these bytes, still as it was when described; or None."""
        for path, entry in list(self.files.items()):
            if entry.get('hash') == file_hash and self.known(path):
                return path
        return None

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + '.tmp'
            with open(temporary, 'w', encoding='utf-8') as handle:
                json.dump({'files': self.files}, handle)
            os.replace(temporary, self.path)
        except OSError:
            log.warning('collab media: could not save the index')


_index = None


def index():
    global _index
    if _index is None:
        _index = Index()
    return _index


def project_files():
    """The open project's media files here: [{'kind', 'name', 'path', 'fingerprint'}]. `name` is what the
    others see (a stem's is its role); `fingerprint` is the video's (subtitld.cc's lookup hash)."""
    video = (session.VIDEO or {}).get('filepath')
    if not video or not os.path.isfile(video):
        return []
    files = [{'kind': 'video', 'name': os.path.basename(video), 'path': video, 'fingerprint': ''}]
    key = utils.get_cache_key(video)
    if key:
        waveform = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{key}_waveform.npy')
        if os.path.isfile(waveform):
            files.append({'kind': 'waveform', 'name': 'waveform.npy', 'path': waveform, 'fingerprint': ''})
        for role in STEM_ROLES:
            stem = os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{key}_{role}.flac')
            if os.path.isfile(stem):
                files.append({'kind': 'stem', 'name': f'{role}.flac', 'path': stem, 'fingerprint': ''})
    return files


def video_fingerprint(path):
    """subtitld.cc's lookup hash of a video (its first and last megabyte), as the media list keeps it."""
    from subtitld.modules.subtitldcc import fingerprint
    try:
        return str(fingerprint(path).get('subtitld_hash') or '')[:64]
    except OSError:
        return ''


def free_path(folder, name):
    """Where a received file named `name` can go in `folder` without replacing another: "name (2).ext" and on
    when the name is taken. A file part-received there before keeps its name, so it resumes."""
    stem, extension = os.path.splitext(os.path.basename(name) or 'video')
    candidate, number = os.path.join(folder, stem + extension), 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f'{stem} ({number}){extension}')
        number += 1
    return candidate


def destination(kind, name):
    """Where a received file of this kind goes here, for the video open now (None when there is none)."""
    video = (session.VIDEO or {}).get('filepath')
    key = utils.get_cache_key(video) if video else None
    if kind == 'waveform' and key:
        return os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'waveform', f'{key}_waveform.npy')
    if kind == 'stem' and key:
        role = os.path.splitext(name)[0]
        if role in STEM_ROLES:
            return os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{key}_{role}.flac')
    return None


def scan(cancelled=lambda: False, progress=None):
    """Describe the open project's media files, working out only those not described before. Blocking: run
    it in the background. Returns [{'kind', 'name', 'fingerprint', 'path', 'hash', 'size', 'chunk_size',
    'chunks'}]."""
    found = []
    for item in project_files():
        description = index().known(item['path'])
        if description is None:
            description = describe(item['path'], cancelled, progress)
            if description is None:
                return found
            index().remember(item['path'], description)
        if item['kind'] == 'video':
            item['fingerprint'] = video_fingerprint(item['path'])
        found.append(dict(item, **description))
    return found


# --- Dub clips: named by content in the shared document, found here by that name ------------------------------
#
# A dub's files ("path", "raw_path", and each subclip's) are this computer's: the shared document leaves them
# out (docops.LOCAL_KEYS) and names them by the hash of their bytes ("hash", "raw_hash"). stamp_dubs() keeps the
# hashes up to date before each change is recorded; resolve_dubs() finds the local file for each hash after
# changes from elsewhere (a collaborator, undo), and says which are not here.

DUB_FILES = (('path', 'hash'), ('raw_path', 'raw_hash'))


def _dub_holders(segments):
    """Every dict that names dub files: each dub of each subtitle, and each of its subclips."""
    for segment in segments or []:
        for dub in segment.get('dubbing') or []:
            if isinstance(dub, dict):
                yield dub
                for part in dub.get('segments') or []:
                    if isinstance(part, dict):
                        yield part


def stamp_dubs(segments):
    """Give each dub file its hash (worked out once: the index remembers it)."""
    for holder in _dub_holders(segments):
        for path_key, hash_key in DUB_FILES:
            path = holder.get(path_key)
            if not path or not os.path.isfile(path):
                continue
            description = index().known(path)
            if description is None:
                description = describe(path)
                index().remember(path, description)
            if holder.get(hash_key) != description['hash']:
                holder[hash_key] = description['hash']


def resolve_dubs(segments):
    """Point each dub file named by hash at its copy here. Returns the hashes with no copy here."""
    missing = []
    for holder in _dub_holders(segments):
        for path_key, hash_key in DUB_FILES:
            file_hash = holder.get(hash_key)
            if not file_hash:
                continue
            path = holder.get(path_key)
            if path and os.path.isfile(path) and (index().known(path) or {}).get('hash') == file_hash:
                continue
            found = index().path_for(file_hash) or _in_dub_cache(file_hash)
            if found:
                holder[path_key] = found
            else:
                holder.pop(path_key, None)
                missing.append(file_hash)
    return missing


def dub_files(segments):
    """(hash, path) of every dub file here."""
    found = {}
    for holder in _dub_holders(segments):
        for path_key, hash_key in DUB_FILES:
            if holder.get(hash_key) and holder.get(path_key) and os.path.isfile(holder[path_key]):
                found[holder[hash_key]] = holder[path_key]
    return found


def dub_destination(file_hash, name='dub.wav'):
    """Where a received dub file goes: the dubbing cache, by its hash."""
    extension = os.path.splitext(name)[1] or '.wav'
    folder = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f'{file_hash}{extension}')


def _in_dub_cache(file_hash):
    folder = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
    for extension in ('.wav', '.flac', '.mp3', '.ogg'):
        path = os.path.join(folder, f'{file_hash}{extension}')
        if os.path.isfile(path):
            if index().known(path) is None:
                index().remember(path, describe(path))
            return path
    return None


session._before_capture_callbacks.append(lambda: stamp_dubs(session.SUBTITLE.get('segments')))
