"""Opening a video from a link (interface/open_url.py): what the link is, and getting the file when it is one.

A link straight to a video (or audio) file Subtitld downloads itself. A link to a web page, as on YouTube,
Vimeo and the many sites a downloader like yt-dlp knows, needs an add-on for `media.download`: a page is
not the video, and getting the video out of it is that add-on's job (and the risk it carries, not the app).
"""

import email.message
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from subtitld import __version__
from subtitld.modules import session
from subtitld.modules.net import https_context

TIMEOUT = 20
CHUNK = 256 * 1024
USER_AGENT = f'Subtitld/{__version__}'

# Types a server may give a file of any kind: then the name says what it is.
_ANY_FILE_TYPES = {'application/octet-stream', 'binary/octet-stream', 'application/force-download',
                   'application/x-download', 'application/download'}


class Cancelled(Exception):
    pass


def video_extensions():
    return {extension.lower() for kind in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS.values()
            for extension in kind['extensions']}


def parse(text):
    """The http(s) link in `text` (a scheme taken as https when left out), or None when it isn't one."""
    text = (text or '').strip()
    if not text or any(character.isspace() for character in text):
        return None
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', text):
        text = 'https://' + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or '.' not in parts.hostname.strip('.'):
        return None
    return text


def _request(url, method='GET', headers=None):
    request = urllib.request.Request(url, method=method, headers={'User-Agent': USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=TIMEOUT, context=https_context())


def _filename(response, url):
    """The name the server gives the file, else the last part of the link's path."""
    name = ''
    disposition = response.headers.get('Content-Disposition')
    if disposition:
        message = email.message.Message()
        message['Content-Disposition'] = disposition
        name = message.get_filename() or ''
    if not name:
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1])
    # Only a name: no folders, nothing a file system refuses.
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', os.path.basename(name.replace('\\', '/'))).strip(' .')
    return name[:200]


def _with_extension(name, content_type):
    stem, extension = os.path.splitext(name)
    if extension[1:].lower() in video_extensions():
        return name
    guessed = mimetypes.guess_extension(content_type or '') or ''
    if guessed[1:].lower() in video_extensions():
        return (stem or name or 'video') + guessed
    return name or 'video'


def probe(url):
    """What `url` leads to: ('file', {'url', 'name', 'size', 'type'}) for a video or audio file, or
    ('page', {'url'}) for anything else (a web page). Raises OSError (urllib's errors) when it can't be
    reached."""
    try:
        response = _request(url, 'HEAD')
    except urllib.error.HTTPError as error:
        if error.code not in (400, 403, 405, 501):
            raise
        # Servers that refuse HEAD: the first byte, then.
        response = _request(url, headers={'Range': 'bytes=0-0'})
    with response:
        final = response.geturl() or url
        # No type at all: the name says what it is.
        content_type = (response.headers.get_content_type().lower() if response.headers.get('Content-Type')
                        else 'application/octet-stream')
        name = _filename(response, final)
        size = response.headers.get('Content-Length')
        if response.status == 206:
            size = (response.headers.get('Content-Range') or '').rpartition('/')[2]
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
    extension = os.path.splitext(name)[1][1:].lower()
    is_file = (content_type.startswith(('video/', 'audio/'))
               or (content_type in _ANY_FILE_TYPES and extension in video_extensions()))
    # A playlist (HLS, DASH) is a list of pieces, not a file: the add-on puts them together.
    if content_type in ('application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl',
                        'audio/x-mpegurl', 'application/dash+xml') or extension in ('m3u8', 'mpd'):
        is_file = False
    if not is_file:
        return 'page', {'url': final}
    return 'file', {'url': final, 'name': _with_extension(name, content_type), 'size': size,
                    'type': content_type}


def free_path(folder, name):
    """`name` in `folder`, as "name (2).ext" and on when the name is taken (nothing is replaced)."""
    stem, extension = os.path.splitext(name or 'video')
    candidate, number = os.path.join(folder, stem + extension), 2
    while os.path.exists(candidate) or os.path.exists(candidate + '.part'):
        candidate = os.path.join(folder, f'{stem} ({number}){extension}')
        number += 1
    return candidate


def download(file, folder, progress=None, cancelled=None):
    """Download the file `probe` found into `folder`; its path once complete. It is written as
    "<name>.part" and named when whole, so a stopped download never looks like a video. `progress(done,
    total)` (total 0 when unknown); `cancelled()` True stops it (raising Cancelled, nothing left behind)."""
    os.makedirs(folder, exist_ok=True)
    path = free_path(folder, file['name'])
    partial = path + '.part'
    try:
        with _request(file['url']) as response, open(partial, 'wb') as output:
            total = int(response.headers.get('Content-Length') or 0) or file.get('size') or 0
            done = 0
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                output.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if total and done < total:
            raise OSError(f'the connection ended after {done} of {total} bytes')
        os.replace(partial, path)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    return path


def default_folder():
    """Where downloaded videos go unless the person chose a folder: their Videos folder."""
    from PySide6.QtCore import QStandardPaths

    return QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or os.path.expanduser('~')


def folder():
    chosen = (session.CONFIG.get('open_link') or {}).get('folder') if isinstance(session.CONFIG, dict) else None
    if chosen and os.path.isdir(chosen):
        return chosen
    return default_folder()


def set_folder(path):
    session.CONFIG.setdefault('open_link', {})['folder'] = path
