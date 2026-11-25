"""Module to handle subtitles

"""

import os
import secrets
from bisect import bisect
from subtitld.modules import cue_ids, cue_kinds, history, markup, session
from subtitld.modules import segment as segment_text
from subtitld.modules.segment import set_text, text_of, timed_pieces


def _subtitles():
    """The subtitles alone, without the marks and titles among them
    (modules.cue_kinds): what "the subtitle before" and "the next one" are
    counted in."""
    return cue_kinds.subtitles(session.SUBTITLE.segments)


def _insert(cue):
    """Put `cue` in the list where its start puts it, among every kind."""
    starts = [item['start'] for item in session.SUBTITLE.segments]
    index = bisect(starts, cue['start'])
    session.SUBTITLE.segments.insert(index, cue)
    # The list holds a Segment made of it: that one is the cue now.
    return session.SUBTITLE.segments[index]


def add_subtitle(position=0.0, duration=3.0, text='', from_last_subtitle=False, kind_fields=None):
    """Function to add a subtitle to the main subtitle list. `kind_fields`
    makes it a cue of another kind (modules.cue_kinds: 'kind' and what that
    kind keeps), placed among the cues of its kind."""
    history.history_append(session.SUBTITLE.segments)

    kind = cue_kinds.kind_of(kind_fields or {})
    segments = cue_kinds.of_kind(session.SUBTITLE.segments, kind)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)

    if index:
        if segments[index - 1]['end'] > position:
            segments[index - 1]['end'] -= (segments[index - 1]['end']) - position
        elif from_last_subtitle:
            position = (segments[index - 1]['end']) + .001

    if len(segments) - 1 > index and segments[index]['start'] - position < duration:
        duration = segments[index]['start'] - position

    cue = dict(kind_fields or {})
    cue.update({
        'id': cue_ids.new_id(),
        'start': position,
        'end': position + duration,
        'text': text
    })
    return _insert(cue)

    # return session.SUBTITLE.segments[session.SUBTITLE.segments.index([position, duration, text])]


def remove_subtitle(selected_subtitle=False):
    """Function to add a subtitle to the main subtitle list"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE.segments)

        session.SUBTITLE.segments.remove(selected_subtitle)
    return session.SUBTITLE.segments


def slice_subtitle(selected_subtitle=False, position=0.0, last_text='', next_text='', strip_text=True):
    """Function to slice a subtitle in the main subtitle list"""
    if selected_subtitle: # and position > selected_subtitle['start'] and position < selected_subtitle['end']:
        history.history_append(session.SUBTITLE.segments)

        index = session.SUBTITLE.segments.index(selected_subtitle)

        if position > session.SUBTITLE.segments[index]['start'] and position < session.SUBTITLE.segments[index]['end']:
            position_to_cut = position
        else:
            position_to_cut = session.SUBTITLE.segments[index]['start'] + ((session.SUBTITLE.segments[index]['end'] - session.SUBTITLE.segments[index]['start']) / 2)

        new_duration = session.SUBTITLE.segments[index]['end'] - position_to_cut

        session.SUBTITLE.segments[index]['end'] = position_to_cut - 0.001

        # A subtitle timed word by word is cut where the time is: each
        # half takes the pieces that start in it.
        halves = segment_text.cut(session.SUBTITLE.segments[index], position_to_cut - session.SUBTITLE.segments[index]['start'])
        if halves:
            last_text, next_text = text_of(halves[0]), text_of(halves[1])
        if strip_text:
            last_text = last_text.strip()
            next_text = next_text.strip()
        # Formatting tags open where the text was cut close on this side and
        # open again on the other.
        last_text, next_text = markup.rebalance(last_text, next_text)
        if halves:
            later = {'text': halves[1]}
            set_text(later, next_text)
            session.SUBTITLE.segments[index]['text'] = halves[0]
            set_text(session.SUBTITLE.segments[index], last_text)
            next_text = timed_pieces(later) or text_of(later)
        else:
            session.SUBTITLE.segments[index]['text'] = last_text

        add_subtitle(position=position_to_cut, duration=new_duration, text=next_text,
                     kind_fields=_kind_fields(session.SUBTITLE.segments[index]))


def _kind_fields(cue):
    """What makes `cue` the kind it is, for a cue cut from it."""
    if cue_kinds.kind_of(cue) == cue_kinds.SUBTITLE:
        return None
    fields = {key: cue[key] for key in ('kind', 'category', 'title_theme') if key in cue}
    if isinstance(cue.get('title_settings'), dict):
        fields['title_settings'] = dict(cue['title_settings'])
    return fields


def _concatenate_first_dubs(first_subtitle, second_subtitle):
    """Concatenate the first dub of `second_subtitle` onto the first dub of
    `first_subtitle` and return the resulting dub dict (a fresh wav written to
    the dubbing cache). Returns None when neither subtitle has any dubs; falls
    back to whichever single dub exists when only one is present."""
    first_dubs = first_subtitle.get('dubbing') or []
    second_dubs = second_subtitle.get('dubbing') or []

    if not first_dubs and not second_dubs:
        return None
    if not second_dubs:
        return dict(first_dubs[0])
    if not first_dubs:
        return dict(second_dubs[0])

    a, b = first_dubs[0], second_dubs[0]
    a_path, b_path = a.get('path'), b.get('path')
    if not (a_path and b_path and os.path.exists(a_path) and os.path.exists(b_path)):
        return dict(a)

    try:
        import soundfile as sf
        import numpy as np
    except ImportError:
        return dict(a)

    a_data, a_sr = sf.read(a_path, always_2d=False)
    b_data, b_sr = sf.read(b_path, always_2d=False)
    if a_sr != b_sr or a_data.ndim != b_data.ndim:
        return dict(a)

    combined = np.concatenate([a_data, b_data])
    new_uid = secrets.token_hex(4)
    cache_dir = os.path.join(session.PATH_SUBTITLD_USER_CACHE, 'dubbing')
    os.makedirs(cache_dir, exist_ok=True)
    new_path = os.path.join(cache_dir, f'{new_uid}.wav')
    sf.write(new_path, combined, a_sr)

    result = dict(a)
    result['uid'] = new_uid
    result['path'] = new_path
    duration = combined.shape[0] / float(a_sr)
    result['end'] = a.get('start', first_subtitle['start']) + duration
    # The concatenated WAV is a fresh master, not derived from the first
    # dub's raw or its prior stretch. Inheriting `raw_path` / `segments`
    # / `rate` from `a` would make a later retime re-render only the
    # first raw (silently dropping the second dub's audio) and would
    # leave segment source coords pointing at the old file's duration.
    result.pop('segments', None)
    result.pop('raw_path', None)
    result['rate'] = 0
    result['pitch'] = 0
    return result


def _merge_translations(first_subtitle, second_subtitle):
    """Merge the per-language translations of two subtitles being joined, in
    reading order (`first` then `second`).

    Returns a dict keyed by the UNION of both languages: where both have text
    it's joined with a space; where only one does, that one is kept. Tolerates
    the two subtitles having *different* sets of translation languages — the
    old merge code assumed identical keys and raised ``KeyError`` (e.g.
    'pt-br') when they differed, aborting the merge half-done (text/end already
    mutated, the next subtitle not yet removed → the overlapping mess).
    """
    first_tr = first_subtitle.get('translations') or {}
    second_tr = second_subtitle.get('translations') or {}
    languages = list(first_tr) + [lang for lang in second_tr if lang not in first_tr]
    merged = {}
    for language in languages:
        parts = [str(first_tr.get(language, '')).strip(),
                 str(second_tr.get(language, '')).strip()]
        merged[language] = ' '.join(part for part in parts if part)
    return merged


def merge_back_subtitle(selected_subtitle=False):
    """Function to merge a subtitle to one subtitle back in the main subtitle list"""
    if selected_subtitle and session.SUBTITLE.segments.index(selected_subtitle):
        history.history_append(session.SUBTITLE.segments)

        # Among its own kind.
        same = cue_kinds.of_kind(session.SUBTITLE.segments, cue_kinds.kind_of(selected_subtitle))
        subt = sorted([subtitle['end'] for subtitle in same if subtitle['end'] <= selected_subtitle['start']])
        if not subt:
            return None
        nearest_subttile = [subtitle for subtitle in same if subtitle['end'] == subt[-1]][0]

        nearest_subttile['end'] = selected_subtitle['end']
        nearest_subttile['text'] = segment_text.joined(nearest_subttile, selected_subtitle)

        # Reading order: nearest (earlier) then selected (later).
        if 'translations' in nearest_subttile or 'translations' in selected_subtitle:
            nearest_subttile['translations'] = _merge_translations(nearest_subttile, selected_subtitle)

        new_dub = _concatenate_first_dubs(nearest_subttile, selected_subtitle)
        if new_dub is not None:
            nearest_subttile['dubbing'] = [new_dub]

        remove_subtitle(selected_subtitle=selected_subtitle)

        return nearest_subttile


def merge_next_subtitle(selected_subtitle=False):
    """Function to merge a subtitle to the next subtitle in the main subtitle list"""
    if selected_subtitle and session.SUBTITLE.segments.index(selected_subtitle) < len(session.SUBTITLE.segments) - 1:
        history.history_append(session.SUBTITLE.segments)

        same = cue_kinds.of_kind(session.SUBTITLE.segments, cue_kinds.kind_of(selected_subtitle))
        subt = sorted([item['start'] for item in same if item['start'] >= selected_subtitle['end']])
        if not subt:
            return None
        nearest_subttile = [subtitle for subtitle in same if subtitle['start'] == subt[0]][0]

        selected_subtitle['end'] = nearest_subttile['end']
        selected_subtitle['text'] = segment_text.joined(selected_subtitle, nearest_subttile)

        # Reading order: selected (earlier) then nearest (later).
        if 'translations' in selected_subtitle or 'translations' in nearest_subttile:
            selected_subtitle['translations'] = _merge_translations(selected_subtitle, nearest_subttile)

        new_dub = _concatenate_first_dubs(selected_subtitle, nearest_subttile)
        if new_dub is not None:
            selected_subtitle['dubbing'] = [new_dub]

        remove_subtitle(selected_subtitle=nearest_subttile)

        return selected_subtitle


def _shift_locked_dubs(subtitle, delta):
    """Shift any dubs flagged as locked-to-subtitle by `delta` seconds."""
    if not delta:
        return
    for dub in subtitle.get('dubbing', []) or []:
        if dub.get('locked'):
            if 'start' in dub:
                dub['start'] = dub['start'] + delta
            if 'end' in dub:
                dub['end'] = dub['end'] + delta


def move_subtitle(selected_subtitle=False, amount=0.0, absolute_time=None, record=True):
    """Function to move a subtitle in the main subtitle list. `record=False`
    leaves the undo history alone: a drag records one step when it starts,
    not one for every move of the mouse."""
    if selected_subtitle:
        if record:
            history.history_append(session.SUBTITLE.segments)
        old_start = selected_subtitle['start']
        if absolute_time is not None:
            duration = selected_subtitle['end'] - selected_subtitle['start']
            selected_subtitle['start'] = absolute_time
            selected_subtitle['end'] = absolute_time + duration
        else:
            selected_subtitle['start'] += amount
            selected_subtitle['end'] += amount
        _shift_locked_dubs(selected_subtitle, selected_subtitle['start'] - old_start)


def speaker_lane(tracks_shown):
    """A subtitle's lane when speakers have tracks of their own, as a
    function of the subtitle: its speaker's track (the first for a speaker
    not listed). None when every subtitle is on the one line."""
    if not (tracks_shown and session.SPEAKERS):
        return None
    speakers = list(session.SPEAKERS.keys())

    def lane(subtitle):
        speaker = subtitle.get('speaker', 'A')
        return speakers.index(speaker) if speaker in speakers else 0
    return lane


def lane_segments(subtitle, lane=None):
    """The cues on the same lane as `subtitle`, in order: those of its kind
    (modules.cue_kinds), and for a subtitle, those `lane` puts with it
    (the timeline's speaker tracks; without it every subtitle shares one)."""
    kind = cue_kinds.kind_of(subtitle)
    segments = cue_kinds.of_kind(session.SUBTITLE.segments, kind)
    if lane is None or kind != cue_kinds.SUBTITLE:
        return segments
    key = lane(subtitle)
    return [item for item in segments if lane(item) == key]


def move_start_subtitle(selected_subtitle=False, amount=0.0, absolute_time=None, move_nereast=False, record=True, lane=None):
    """Function to move the start a subtitle in the main subtitle list.
    With `move_nereast`, the touching subtitle before it on its lane gives way."""
    if selected_subtitle:
        if record:
            history.history_append(session.SUBTITLE.segments)
        old_start = selected_subtitle['start']
        if move_nereast:
            segments = lane_segments(selected_subtitle, lane)
            subt = [item['end'] for item in segments]
            nearest = bisect(subt, selected_subtitle['start']) - 1
            if nearest >= 0 and round(segments[nearest]['end'], 3) == round(selected_subtitle['start'] - .001, 3):
                if absolute_time is not None:
                    segments[nearest]['end'] = absolute_time - 0.001
                else:
                    segments[nearest]['end'] += amount
        if absolute_time is not None:
            selected_subtitle['start'] = absolute_time
        else:
            selected_subtitle['start'] += amount
        _shift_locked_dubs(selected_subtitle, selected_subtitle['start'] - old_start)


def move_end_subtitle(selected_subtitle=False, amount=0.0, absolute_time=None, move_nereast=False, record=True, lane=None):
    """Function to move the end of a subtitle in the main subtitle list.
    With `move_nereast`, the touching subtitle after it on its lane gives way."""
    if selected_subtitle:
        if record:
            history.history_append(session.SUBTITLE.segments)
        # A mark given an end is no longer open-ended (modules.cue_kinds).
        selected_subtitle.pop('open_end', None)
        if move_nereast:
            segments = lane_segments(selected_subtitle, lane)
            subt = [item['start'] for item in segments]
            nearest = bisect(subt, selected_subtitle['end'])
            if nearest < len(subt) and round(segments[nearest]['start'], 3) == round(selected_subtitle['end'] + .001, 3):
                if absolute_time is not None:
                    segments[nearest]['start'] = absolute_time + 0.001
                else:
                    segments[nearest]['start'] += amount
        if absolute_time is not None:
            selected_subtitle['end'] = absolute_time
        else:
            selected_subtitle['end'] += amount


def next_start_to_current_position(position=0.0):
    """Function to set next start to position of a subtitle in the main subtitle list"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index < len(subt):
        segments[index]['start'] = position
    # if index and segments[index - 1]['end'] > position:
    #     last_end_to_current_position(position=position - 0.001)


def subtitle_start_to_current_position(position=0.0):
    """Function to set start to position of a subtitle in the main subtitle list"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index and position < (segments[index - 1]['end']):
        # end = segments[index - 1]['end']
        segments[index - 1]['start'] = position
        # segments[index - 1]['end'] = end - position
    # else:
    #     end = segments[index]['end']
    #     segments[index]['start'] = position
    #     segments[index]['end'] = end - position


def subtitle_end_to_current_position(position=0.0):
    """Function to set end to position of a subtitle in the main subtitle list"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index:
        if position < (segments[index - 1]['end']):
            segments[index - 1]['end'] = position # - segments[index - 1]['start']
        # else:
        #     segments[index - 1]['end'] = position - segments[index - 1]['start']


def subtitle_under_current_position(position=False):
    """Function to return subtitle under position"""
    segments = _subtitles()
    if not position:
        position = session.SUBTITLE.position or 0.0
    # current_subtitle = False

    return next((item for item in segments if item['start'] <= position < item['end']), False)


    # subt = [item['start'] for item in segments]
    # index = bisect(subt, position)
    # if index - 1 > -1 and position > segments[index - 1]['start'] and position < (segments[index - 1]['end']):
    #     current_subtitle = segments[index - 1]
    # return current_subtitle


def get_adjacent_subtitles(position=False, subtitle=False, segments=None):
    """The subtitles either side of the one at `position`, among `segments`
    (one lane of the timeline) or all of them."""
    if subtitle:
        position = subtitle['start']

    if not position:
        position = session.SUBTITLE.position or 0.0

    if segments is None:
        segments = session.SUBTITLE.segments
    # The most efficient way to get the adjacent subtitles is to use binary search
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)

    return segments[index - 2] if index > 1 else False, segments[index] if index < len(subt) else False
    
    

def last_subtitle_current_position(position=0.0):
    """Function to return the last subtitle of position"""
    segments = _subtitles()
    last_subtitle = False
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index:
        if position > (segments[index - 1]['end']):
            last_subtitle = segments[index - 1]
    return last_subtitle


def next_subtitle_current_position(position=0.0):
    """Function to return the next subtitle of position"""
    segments = _subtitles()
    next_subtitle = False
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index < len(subt):
        next_subtitle = segments[index]
    return next_subtitle


def next_end_to_current_position(position=0.0):
    """Function set next end to position"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index:
        end = segments[index - 1]['end']
        if end > position:
            segments[index - 1]['end'] = position # - segments[index - 1]['start']


def last_end_to_current_position(position=0.0):
    """Function set last end to position"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)

    if index - 1 < len(subt) - 1:
        segments[index - 1]['end'] = position # - segments[index - 1]['start']


def last_start_to_current_position(position=0.0):
    """Function set last start to position"""
    segments = _subtitles()
    history.history_append(session.SUBTITLE.segments)
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)
    if index and segments[index - 1]['start'] < position and not (segments[index - 1]['end']) < position:
        # end = segments[index - 1]['end']
        segments[index - 1]['start'] = position
        # segments[index - 1]['end'] = end - position


def send_text_to_next_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the next subtitle"""
    segments = _subtitles()
    if selected_subtitle and segments.index(selected_subtitle) + 1 < len(segments):
        history.history_append(session.SUBTITLE.segments)
        index = segments.index(selected_subtitle)
        last_text, next_text = markup.rebalance(last_text, next_text)
        set_text(segments[index], last_text)

        next_text = next_text.strip() + ' ' + segments[index + 1].text()
        if session.CONFIG['quality_check'].get('break_text_when_sending_text_to_adjacent', False) and '\n' in next_text:
            next_text = next_text.replace('\n', ' ')
            mid = len(next_text) // 2
            left = next_text.rfind(' ', 0, mid)
            right = next_text.find(' ', mid)
            if left == -1:
                split = right
            elif right == -1:
                split = left
            else:
                split = left if (mid - left) < (right - mid) else right
            next_text = next_text[:split] + '\n' + next_text[split+1:]

        set_text(segments[index + 1], next_text)

def send_translated_text_to_next_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the last subtitle"""
    segments = _subtitles()
    if selected_subtitle and segments.index(selected_subtitle):
        history.history_append(session.SUBTITLE.segments)
        index = segments.index(selected_subtitle)
        last_text, next_text = markup.rebalance(last_text, next_text)
        segments[index]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] = last_text

        next_text = next_text.strip() + ' ' + segments[index + 1]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')]
        if session.CONFIG['quality_check'].get('break_text_when_sending_text_to_adjacent', False) and '\n' in next_text:
            next_text = next_text.replace('\n', ' ')
            mid = len(next_text) // 2
            left = next_text.rfind(' ', 0, mid)
            right = next_text.find(' ', mid)
            if left == -1:
                split = right
            elif right == -1:
                split = left
            else:
                split = left if (mid - left) < (right - mid) else right
            next_text = next_text[:split] + '\n' + next_text[split+1:]

        segments[index + 1]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] = next_text


def send_text_to_last_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the last subtitle"""
    segments = _subtitles()
    if selected_subtitle and segments.index(selected_subtitle):
        history.history_append(session.SUBTITLE.segments)
        index = segments.index(selected_subtitle)
        last_text, next_text = markup.rebalance(last_text, next_text)
        set_text(segments[index], next_text)

        last_text = segments[index - 1].text() + ' ' + last_text
        if session.CONFIG['quality_check'].get('break_text_when_sending_text_to_adjacent', False) and '\n' in last_text:
            last_text = last_text.replace('\n', ' ')
            mid = len(last_text) // 2
            left = last_text.rfind(' ', 0, mid)
            right = last_text.find(' ', mid)
            if left == -1:
                split = right
            elif right == -1:
                split = left
            else:
                split = left if (mid - left) < (right - mid) else right
            last_text = last_text[:split] + '\n' + last_text[split+1:]


        set_text(segments[index - 1], last_text)


def send_translated_text_to_last_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the last subtitle"""
    segments = _subtitles()
    if selected_subtitle and segments.index(selected_subtitle):
        history.history_append(session.SUBTITLE.segments)
        index = segments.index(selected_subtitle)
        last_text, next_text = markup.rebalance(last_text, next_text)
        segments[index]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] = next_text

        last_text = segments[index - 1]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] + ' ' + last_text
        if session.CONFIG['quality_check'].get('break_text_when_sending_text_to_adjacent', False) and '\n' in last_text:
            last_text = last_text.replace('\n', ' ')
            mid = len(last_text) // 2
            left = last_text.rfind(' ', 0, mid)
            right = last_text.find(' ', mid)
            if left == -1:
                split = right
            elif right == -1: 
                split = left
            else:
                split = left if (mid - left) < (right - mid) else right
            last_text = last_text[:split] + '\n' + last_text[split+1:]

        segments[index - 1]['translations'][session.CONFIG['translation'].get('engine_options', {}).get('target_language', 'en-us')] = last_text


def set_gap(position=0.0, gap=0.0):
    """Function to set gap in subtitles"""
    history.history_append(session.SUBTITLE.segments)
    for subtitle in session.SUBTITLE.segments:
        if subtitle['start'] > position:
            subtitle['start'] += gap
            subtitle['end'] += gap


def change_subtitle_text(selected_subtitle=False, text=''):
    """Function to change text of selected subtitle"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE.segments)
        set_text(session.SUBTITLE.segments[session.SUBTITLE.segments.index(selected_subtitle)], text)


def is_current_position_above_subtitle(position=0.0):
    segments = _subtitles()
    subt = [item['start'] for item in segments]
    index = bisect(subt, position)

    if position < segments[index - 1]['end']:
        return True

    return False

def get_speaker_intervals(speaker_name):
    segments = _subtitles()
    intervals = []
    for subtitle in segments:
        speaker = subtitle.get('speaker', None)
        if speaker == speaker_name:
            start = subtitle['start']
            end = subtitle['end']
            intervals.append((start, end))
    return intervals

def add_mark(position=0.0, category=None, text=''):
    """A mark at `position` (modules.cue_kinds), open-ended: it lasts until
    the next of its category. Its category is the one marked with last
    when none is given."""
    history.history_append(session.SUBTITLE.segments)
    marks = session.CONFIG.setdefault('marks', {}) if isinstance(session.CONFIG, dict) else {}
    category = category or marks.get('last_category') or cue_kinds.DEFAULT_CATEGORY
    if category not in cue_kinds.categories():
        category = cue_kinds.DEFAULT_CATEGORY
    marks['last_category'] = category
    cue = cue_kinds.new_mark(position, text, category)
    cue['id'] = cue_ids.new_id()
    return _insert(cue)


def add_title(position=0.0, duration=3.0, text=''):
    """A title from `position`, drawn by the current title theme
    (modules.title_themes), among the titles as a subtitle is among
    subtitles."""
    fields = cue_kinds.new_title(position, position + duration)
    return add_subtitle(position=position, duration=duration, text=text,
                        kind_fields={key: fields[key] for key in ('kind', 'title_theme', 'title_settings')})
