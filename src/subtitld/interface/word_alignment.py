"""Timing a subtitle's words against the audio: forced alignment, by an
add-on that offers `asr.align` (modules.addons.provider).

The subtitle's stretch of the audio goes to the add-on, 16 kHz mono, with
its words; the add-on says when each is said, and the subtitle is timed
word by word with them (modules.segment.timed_by_words), as one undo step.
The text stays as written: only its timing comes from the audio.
"""

import os

from subtitld.interface.translation import _
from subtitld.modules import cue_ids, history, markup, session
from subtitld.modules.addons.provider import TASK_ASR_ALIGN
from subtitld.modules.segment import text_of, timed_by_words

_busy = {'provider': None}


def provider():
    """The add-on that aligns, the default one if there are several."""
    from subtitld.modules.addons import get_manager
    return get_manager().default_for_task(TASK_ASR_ALIGN)


def available():
    """Whether a subtitle can be aligned: an add-on that aligns, and a
    video or audio file to hear it in."""
    return provider() is not None and bool((session.VIDEO or {}).get('filepath'))


def align(window, subtitle, on_done=None):
    """Time `subtitle`'s words against the audio. `on_done(ok, message)` is
    called when the add-on answers. False if it could not start."""
    aligner = provider()
    media = (session.VIDEO or {}).get('filepath')
    if aligner is None or not media or _busy['provider'] is not None or not isinstance(subtitle, dict):
        return False
    from subtitld.interface.left_panel_import import _extract_audio_slice
    start, end = float(subtitle['start']), float(subtitle['end'])
    audio = _extract_audio_slice(media, start, end)
    if not audio:
        if on_done:
            on_done(False, _('timed_text.align_no_audio'))
        return False
    sid = subtitle.get('id')

    def finish(ok, message=''):
        for signal, slot in ((aligner.aligned, done), (aligner.align_error, failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        _busy['provider'] = None
        try:
            os.remove(audio)
        except OSError:
            pass
        if on_done:
            on_done(ok, message)

    def done(segments):
        target = cue_ids.find(session.SUBTITLE.segments, sid) if sid else subtitle
        words = segments[0].get('words') if segments and isinstance(segments[0], dict) else None
        pieces = timed_by_words(text_of(target), words, 0.0) if target is not None else None
        if not pieces:
            finish(False, _('timed_text.align_nothing'))
            return
        history.history_append()
        target['text'] = pieces
        session.set_unsaved(True)
        _refresh(window)
        finish(True)

    def failed(message):
        finish(False, message)

    _busy['provider'] = aligner
    aligner.aligned.connect(done)
    aligner.align_error.connect(failed)
    aligner.align(audio, session.SUBTITLE.get('language') or 'en-us',
                  [{'start': 0.0, 'end': round(end - start, 3), 'text': markup.plain(text_of(subtitle))}])
    return True


def _refresh(window):
    from subtitld.interface import left_panel
    timeline = getattr(window, 'timeline_widget', None)
    if timeline is not None:
        timeline.update()
    try:
        left_panel.update(window)
    except AttributeError:
        pass
