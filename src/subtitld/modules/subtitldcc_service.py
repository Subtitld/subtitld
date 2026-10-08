"""subtitld.cc from inside Subtitld: the connection, and the work behind the subtitld.cc panel.

- Signing in opens subtitld.cc in the browser (or shows a code to type at subtitld.cc/device); the
  token is kept by ``subtitldcc.TokenStore`` in Subtitld's config folder (or the system keyring).
- Looking up the open video sends two fingerprints of the file and its size and duration, never its
  name. It runs when a video opens, unless turned off.
- Every network call runs off the UI thread through ``call()``; its result comes back on the UI thread.

Settings live in ``session.CONFIG['subtitldcc']``: ``auto_lookup`` (bool), ``account`` (what /me said
last, to show the account without waiting for the network) and ``base_url`` (only for testing; the
``SUBTITLDCC_BASE_URL`` environment variable wins).
"""

import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import i18n
from PySide6.QtCore import QThread, Signal

from subtitld import __version__
from subtitld.modules import file_io, net, session, usf
from subtitld.modules import subtitldcc as api
from subtitld.modules.subtitldcc import ApiError, SignInError

DEFAULT_BASE_URL = 'https://subtitld.cc'
SCOPES = ('read', 'publish', 'rate')


# --- Settings and connection -----------------------------------------------------------------------------


def settings():
    section = session.CONFIG.get('subtitldcc') if isinstance(session.CONFIG, dict) else None
    if not isinstance(section, dict):
        section = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['subtitldcc'] = section
    section.setdefault('auto_lookup', True)
    return section


def base_url():
    return (os.environ.get('SUBTITLDCC_BASE_URL') or settings().get('base_url') or DEFAULT_BASE_URL).rstrip('/')


def site_url(path=''):
    return f'{base_url()}/{path.lstrip("/")}'


def ui_language():
    """The interface language as a BCP 47 tag ("pt-BR"), for the API's messages."""
    return str(i18n.get('locale') or 'en_US').replace('_', '-')


def _open(request, timeout):
    if request.full_url.startswith('https://'):
        return urllib.request.urlopen(request, timeout=timeout, context=net.https_context())
    return urllib.request.urlopen(request, timeout=timeout)


_store = None


def store():
    global _store
    account = urllib.parse.urlparse(base_url()).netloc
    if _store is None or _store.account != account:
        _store = api.TokenStore(base_url(), directory=session.PATH_SUBTITLD_USER_CONFIG)
    return _store


def client(token=None):
    """A client acting as the connected person (or anonymously when not connected)."""
    return api.Client(
        base_url(),
        token=token if token is not None else store().load(),
        app_version=__version__,
        language=ui_language(),
        opener=_open,
    )


def is_connected():
    return bool(store().load())


def account():
    """What /me said last: {'handle', 'name', 'groups', 'publish_as', 'subtitle_languages'} or {}."""
    return settings().get('account') or {}


def remember_account(me):
    settings()['account'] = {
        key: me.get(key) for key in ('handle', 'name', 'groups', 'publish_as', 'subtitle_languages')
    }
    _save_config()


def forget_account():
    store().clear()
    settings().pop('account', None)
    _save_config()


def _save_config():
    try:
        session.CONFIG.save()
    except Exception:  # the settings are saved again when Subtitld closes
        pass


def sign_in(device_code_shown=None, cancelled=lambda: False):
    """Sign in (blocking: run it with call()). With ``device_code_shown(code, uri, full_uri)`` it uses a
    code typed at subtitld.cc/device instead of the browser. Returns /me."""
    connection = client(token='')
    about = {'device_id': store().device_id(), 'scopes': SCOPES, 'cancelled': cancelled}
    if device_code_shown is None:
        token = api.sign_in_browser(connection, **about)
    else:
        token = api.sign_in_device(connection, show=device_code_shown, **about)
    store().save(token['access_token'])
    me = connection.me()
    return me


def sign_out():
    """Revoke the token on subtitld.cc (best effort) and forget it here. Blocking."""
    try:
        client().revoke()
    except Exception:
        pass
    forget_account()


def preferred_languages():
    """The connected person's subtitle languages, else the interface language: lookups list these first."""
    languages = list(account().get('subtitle_languages') or [])
    if not languages:
        languages = [ui_language()]
    return languages


_meta = None


def meta():
    """GET /meta (languages and their names, licenses, rating tags, limits), fetched once. Blocking."""
    global _meta
    if _meta is None:
        _meta = client().meta()
    return _meta


def cached_meta():
    """/meta if it was fetched already, else None. Never blocks."""
    return _meta


# --- Running calls off the UI thread ---------------------------------------------------------------------


class Call(QThread):
    """Runs ``function()`` off the UI thread and emits ``succeeded(result)`` or ``failed(error)``. Both
    arrive on the UI thread."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, function):
        super().__init__()
        self._function = function

    def run(self):
        try:
            result = self._function()
        except Exception as error:  # shown to the person by whoever asked
            self.failed.emit(error)
        else:
            self.succeeded.emit(result)


_running = set()


def call(function, on_success=None, on_failure=None):
    """Start ``function`` in the background. Returns the Call (keep it to ask ``isRunning()``)."""
    worker = Call(function)
    if on_success is not None:
        worker.succeeded.connect(on_success)
    if on_failure is not None:
        worker.failed.connect(on_failure)
    _running.add(worker)

    def finished():
        _running.discard(worker)
        worker.deleteLater()

    worker.finished.connect(finished)
    worker.start()
    return worker


def wait_for_calls(milliseconds=3000):
    """Before quitting: give running calls a moment to end (a QThread destroyed mid-run crashes)."""
    for worker in list(_running):
        worker.wait(milliseconds)


def error_code(error):
    """A short code for any failure: the API's ("version_conflict", "not_found"…), "unauthorized" when the
    token stopped working, "offline" when subtitld.cc can't be reached, or the sign-in error."""
    if isinstance(error, ApiError):
        return 'unauthorized' if error.status == 401 else error.code
    if isinstance(error, SignInError):
        return error.error
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return 'offline'
    return 'unknown'


def error_message(error, translate):
    """What to tell the person. The API already answers in their language; the rest is translated here."""
    code = error_code(error)
    if isinstance(error, ApiError) and error.message and code != 'unauthorized':
        return error.message
    key = f'subtitldcc.error_{code}'
    text = translate(key)
    return text if text != key else translate('subtitldcc.error_unknown')


# --- Looking up the open video ---------------------------------------------------------------------------


def video_fingerprint(path, duration_seconds=None):
    """Both hashes and the size of a local video (reads its first and last megabyte). Blocking."""
    duration_ms = int(duration_seconds * 1000) if duration_seconds else None
    return api.fingerprint(path, duration_ms)


def lookup(video):
    """{exact: [...], probable: [...]} for one fingerprint. Blocking."""
    return client().lookup([video], preferred_languages())[0]


# --- Opening a subtitle in the editor --------------------------------------------------------------------


def download_usf(share_id, version=None):
    """The subtitle as USF (it names its origin: share ID and version). Blocking."""
    return client().download(share_id, 'usf', version)


def open_in_editor(window, data):
    """Replace the subtitles in the editor with ``data`` (USF bytes from subtitld.cc). Main thread only.
    The result isn't a file yet: saving asks where to put it."""
    from subtitld.modules import history
    from subtitld.interface import left_panel, top_bar

    handle, path = tempfile.mkstemp(suffix='.usf', dir=session.PATH_TEMP)
    with os.fdopen(handle, 'wb') as temporary:
        temporary.write(data)
    try:
        history.history_clear()
        session.SUBTITLE['segments'] = []
        session.SUBTITLE['selected'] = None
        session.SPEAKERS.clear()
        segments, _format = file_io.process_subtitles_file(path)
    finally:
        os.unlink(path)
    session.SUBTITLE['segments'] = segments
    session.SUBTITLE['filepath'] = ''
    session.CONFIG['format_to_save'] = 'USFX'
    for name in {segment.get('speaker', 'A') for segment in segments}:
        session.SPEAKERS.setdefault(name, {})
    session.set_unsaved(False)

    if hasattr(window, 'preview_panel_player') and hasattr(window.preview_panel_player, '_audio_device'):
        window.preview_panel_player._audio_device.sync_subtitle_dubs(segments)
    if hasattr(window, 'timeline_widget'):
        window.timeline_widget.update_size()
        window.timeline_widget.update()
    left_panel.update(window)
    top_bar.update(window)
    return segments


def origin():
    """{'share_id', 'version'} of the subtitld.cc subtitle the editor holds, or None."""
    value = session.SUBTITLE.get('origin')
    return value if isinstance(value, dict) and value.get('share_id') else None


def set_origin(share_id, version):
    session.SUBTITLE['origin'] = {'share_id': share_id, 'version': version}


# --- Publishing ------------------------------------------------------------------------------------------


def text_languages():
    """The texts the editor can publish: [(language or None, is_translation)], the original first."""
    languages = [(session.SUBTITLE.get('language') or None, False)]
    seen = set()
    for segment in session.SUBTITLE.get('segments', []):
        for language in (segment.get('translations') or {}):
            if language and language not in seen:
                seen.add(language)
                languages.append((language, True))
    return languages


def usf_for_publishing(translation=None, title=''):
    """The subtitles as USF to publish: one language (the original text, or the translation in
    ``translation``), speaker names and colors. Never dubbing, speaker images or file paths."""
    segments = []
    for segment in session.SUBTITLE.get('segments', []):
        text = segment.get('text', '')
        if translation:
            text = (segment.get('translations') or {}).get(translation, '')
        if not str(text).strip():
            continue
        segments.append({
            'start': segment['start'],
            'end': segment['end'],
            'text': text,
            'speaker': segment.get('speaker') or 'A',
        })
    speakers = {
        name: {'color': data['color']}
        for name, data in session.SPEAKERS.items()
        if isinstance(data, dict) and data.get('color')
    }
    language = translation or session.SUBTITLE.get('language') or None
    metadata = {'title': title} if title else None
    content = usf.USFWriter().write(segments, speakers=speakers, language=language, metadata=metadata)
    return content.encode('utf-8')


def bcp47(code, known=()):
    """Subtitld's language code ("pt-br", "en_US") as subtitld.cc expects it ("pt-BR"), matched against
    ``known`` (the site's list; the base language when the regional one isn't there). "" if unknown."""
    if not code:
        return ''
    parts = str(code).replace('_', '-').split('-')
    tag = [parts[0].lower()]
    for part in parts[1:]:
        tag.append(part.title() if len(part) == 4 else part.upper() if len(part) == 2 else part.lower())
    value = '-'.join(tag)
    if not known or value in known:
        return value
    return tag[0] if tag[0] in known else ''


def video_for_publishing(video, show_name=False):
    """The open video's fingerprint, as the publish and match calls take it (the name only if chosen)."""
    if not video:
        return None
    payload = dict(video)
    if show_name and session.VIDEO.get('filepath'):
        payload['name'] = os.path.basename(session.VIDEO['filepath'])
    return payload
