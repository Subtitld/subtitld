"""Module to handle subtitles

"""

from bisect import bisect
from subtitld.modules import history, session


def add_subtitle(position=0.0, duration=5.0, text='', from_last_subtitle=False):
    """Function to add a subtitle to the main subtitle list"""
    history.history_append(session.SUBTITLE['segments'])

    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)

    if index:
        if session.SUBTITLE['segments'][index - 1]['end'] > position:
            session.SUBTITLE['segments'][index - 1]['end'] -= (session.SUBTITLE['segments'][index - 1]['end']) - position
        elif from_last_subtitle:
            position = (session.SUBTITLE['segments'][index - 1]['end']) + .001

    if len(session.SUBTITLE['segments']) - 1 > index and session.SUBTITLE['segments'][index]['start'] - position < duration:
        duration = session.SUBTITLE['segments'][index]['start'] - position

    session.SUBTITLE['segments'].insert(index, {
        'start': position,
        'end': position + duration,
        'text': text
    })

    # return session.SUBTITLE['segments'][session.SUBTITLE['segments'].index([position, duration, text])]


def remove_subtitle(selected_subtitle=False):
    """Function to add a subtitle to the main subtitle list"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE['segments'])

        session.SUBTITLE['segments'].remove(selected_subtitle)
    return session.SUBTITLE['segments']


def slice_subtitle(selected_subtitle=False, position=0.0, last_text='', next_text='', strip_text=True):
    """Function to slice a subtitle in the main subtitle list"""
    if selected_subtitle: # and position > selected_subtitle['start'] and position < selected_subtitle['end']:
        history.history_append(session.SUBTITLE['segments'])

        index = session.SUBTITLE['segments'].index(selected_subtitle)

        if position > session.SUBTITLE['segments'][index]['start'] and position < session.SUBTITLE['segments'][index]['end']:
            position_to_cut = position
        else:
            position_to_cut = session.SUBTITLE['segments'][index]['start'] + ((session.SUBTITLE['segments'][index]['end'] - session.SUBTITLE['segments'][index]['start']) / 2)

        new_duration = session.SUBTITLE['segments'][index]['end'] - position_to_cut

        session.SUBTITLE['segments'][index]['end'] = position_to_cut - 0.001
        session.SUBTITLE['segments'][index]['text'] = last_text

        if strip_text:
            session.SUBTITLE['segments'][index]['text'] = session.SUBTITLE['segments'][index]['text'].strip()
            next_text = next_text.strip()

        add_subtitle(position=position_to_cut, duration=new_duration, text=next_text)


def merge_back_subtitle(selected_subtitle=False):
    """Function to merge a subtitle to one subtitle back in the main subtitle list"""
    if selected_subtitle and session.SUBTITLE['segments'].index(selected_subtitle):
        history.history_append(session.SUBTITLE['segments'])

        subt = sorted([subtitle['end'] for subtitle in session.SUBTITLE['segments'] if subtitle['end'] <= selected_subtitle['start']])
        nearest_subttile = [subtitle for subtitle in session.SUBTITLE['segments'] if subtitle['end'] == subt[-1]][0]
        
        nearest_subttile['end'] = selected_subtitle['end']
        nearest_subttile['text'] += ' ' + selected_subtitle['text']

        remove_subtitle(selected_subtitle=selected_subtitle)

        return nearest_subttile


def merge_next_subtitle(selected_subtitle=False):
    """Function to merge a subtitle to the next subtitle in the main subtitle list"""
    if selected_subtitle and session.SUBTITLE['segments'].index(selected_subtitle) < len(session.SUBTITLE['segments']) - 1:
        history.history_append(session.SUBTITLE['segments'])

        subt = sorted([item['start'] for item in session.SUBTITLE['segments'] if item['start'] >= selected_subtitle['end']])
        nearest_subttile = [subtitle for subtitle in session.SUBTITLE['segments'] if subtitle['start'] == subt[0]][0]

        selected_subtitle['end'] = nearest_subttile['end']
        selected_subtitle['text'] += ' ' + nearest_subttile['text']

        remove_subtitle(selected_subtitle=nearest_subttile)
        
        return selected_subtitle


def move_subtitle(selected_subtitle=False, amount=0.0, absolute_time=False):
    """Function to move a subtitle in the main subtitle list"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE['segments'])
        if absolute_time:
            duration = selected_subtitle['end'] - selected_subtitle['start']
            selected_subtitle['start'] = absolute_time
            selected_subtitle['end'] = absolute_time + duration
        else:
            selected_subtitle['start'] += amount
            selected_subtitle['end'] += amount


def move_start_subtitle(selected_subtitle=False, amount=0.0, absolute_time=False, move_nereast=False):
    """Function to move the start a subtitle in the main subtitle list"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE['segments'])
        if move_nereast:
            subt = [item['end'] for item in session.SUBTITLE['segments']]
            nearest = bisect(subt, selected_subtitle['start']) - 1
            if round(session.SUBTITLE['segments'][nearest]['end'], 3) == round(selected_subtitle['start'] - .001, 3):
                if absolute_time:
                    session.SUBTITLE['segments'][nearest]['end'] = absolute_time - 0.001
                else:
                    session.SUBTITLE['segments'][nearest]['end'] -= amount
        if absolute_time:
            selected_subtitle['start'] = absolute_time
        else:
            selected_subtitle['start'] += amount


def move_end_subtitle(selected_subtitle=False, amount=0.0, absolute_time=False, move_nereast=False):
    """Function to move the end of a subtitle in the main subtitle list"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE['segments'])
        if move_nereast:
            subt = [item['start'] for item in session.SUBTITLE['segments']]
            nearest = bisect(subt, selected_subtitle['end'])
            if round(session.SUBTITLE['segments'][nearest]['start'], 3) == round(selected_subtitle['end'] + .001, 3):
                if absolute_time:
                    session.SUBTITLE['segments'][nearest]['start'] = absolute_time + 0.001
                else:
                    session.SUBTITLE['segments'][nearest]['start'] += amount
        if absolute_time:
            selected_subtitle['end'] = absolute_time
        else:
            selected_subtitle['end'] += amount


def next_start_to_current_position(position=0.0):
    """Function to set next start to position of a subtitle in the main subtitle list"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index < len(subt):
        end = session.SUBTITLE['segments'][index]['end']
        session.SUBTITLE['segments'][index]['start'] = position
        session.SUBTITLE['segments'][index]['end'] = end - position
    if index and session.SUBTITLE['segments'][index - 1]['end'] > position:
        last_end_to_current_position(position=position - 0.001)


def subtitle_start_to_current_position(position=0.0):
    """Function to set start to position of a subtitle in the main subtitle list"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index and position < (session.SUBTITLE['segments'][index - 1]['end']):
        end = session.SUBTITLE['segments'][index - 1]['end']
        session.SUBTITLE['segments'][index - 1]['start'] = position
        session.SUBTITLE['segments'][index - 1]['end'] = end - position
    else:
        end = session.SUBTITLE['segments'][index]['end']
        session.SUBTITLE['segments'][index]['start'] = position
        session.SUBTITLE['segments'][index]['end'] = end - position


def subtitle_end_to_current_position(position=0.0):
    """Function to set end to position of a subtitle in the main subtitle list"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index:
        if position < (session.SUBTITLE['segments'][index - 1]['end']):
            session.SUBTITLE['segments'][index - 1]['end'] = position - session.SUBTITLE['segments'][index - 1]['start']
        else:
            session.SUBTITLE['segments'][index - 1]['end'] = position - session.SUBTITLE['segments'][index - 1]['start']


def subtitle_under_current_position(position=False):
    """Function to return subtitle under position"""
    if not position:
        position = session.SUBTITLE.get('position', 0.0)
    current_subtitle = False
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index - 1 > -1 and position > session.SUBTITLE['segments'][index - 1]['start'] and position < (session.SUBTITLE['segments'][index - 1]['end']):
        current_subtitle = session.SUBTITLE['segments'][index - 1]
    return current_subtitle


def last_subtitle_current_position(position=0.0):
    """Function to return the last subtitle of position"""
    last_subtitle = False
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index:
        if position > (session.SUBTITLE['segments'][index - 1]['end']):
            last_subtitle = session.SUBTITLE['segments'][index - 1]
    return last_subtitle


def next_subtitle_current_position(position=0.0):
    """Function to return the next subtitle of position"""
    next_subtitle = False
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index < len(subt):
        next_subtitle = session.SUBTITLE['segments'][index]
    return next_subtitle


def next_end_to_current_position(position=0.0):
    """Function set next end to position"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index:
        end = session.SUBTITLE['segments'][index - 1]['end']
        if end > position:
            session.SUBTITLE['segments'][index - 1]['end'] = position - session.SUBTITLE['segments'][index - 1]['start']


def last_end_to_current_position(position=0.0):
    """Function set last end to position"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)

    if index - 1 < len(subt) - 1:
        session.SUBTITLE['segments'][index - 1]['end'] = position - session.SUBTITLE['segments'][index - 1]['start']


def last_start_to_current_position(position=0.0):
    """Function set last start to position"""
    history.history_append(session.SUBTITLE['segments'])
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)
    if index and session.SUBTITLE['segments'][index - 1]['start'] < position and not (session.SUBTITLE['segments'][index - 1]['end']) < position:
        end = session.SUBTITLE['segments'][index - 1]['end']
        session.SUBTITLE['segments'][index - 1]['start'] = position
        session.SUBTITLE['segments'][index - 1]['end'] = end - position


def send_text_to_next_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the next subtitle"""
    if selected_subtitle and session.SUBTITLE['segments'].index(selected_subtitle) + 1 < len(session.SUBTITLE['segments']):
        history.history_append(session.SUBTITLE['segments'])
        index = session.SUBTITLE['segments'].index(selected_subtitle)
        session.SUBTITLE['segments'][index]['text'] = last_text
        session.SUBTITLE['segments'][index + 1]['text'] = next_text + ' ' + session.SUBTITLE['segments'][index + 1]['text']


def send_text_to_last_subtitle(selected_subtitle=False, last_text='', next_text=''):
    """Function send text to the last subtitle"""
    if selected_subtitle and session.SUBTITLE['segments'].index(selected_subtitle):
        history.history_append(session.SUBTITLE['segments'])
        index = session.SUBTITLE['segments'].index(selected_subtitle)
        session.SUBTITLE['segments'][index]['text'] = next_text
        session.SUBTITLE['segments'][index - 1]['text'] += ' ' + last_text


def set_gap(position=0.0, gap=0.0):
    """Function to set gap in subtitles"""
    history.history_append(session.SUBTITLE['segments'])
    for subtitle in session.SUBTITLE['segments']:
        if subtitle['start'] > position:
            subtitle['start'] += gap
            subtitle['end'] += gap


def change_subtitle_text(selected_subtitle=False, text=''):
    """Function to change text of selected subtitle"""
    if selected_subtitle:
        history.history_append(session.SUBTITLE['segments'])
        session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(selected_subtitle)]['text'] = text


def is_current_position_above_subtitle(position=0.0):
    subt = [item['start'] for item in session.SUBTITLE['segments']]
    index = bisect(subt, position)

    if position < session.SUBTITLE['segments'][index - 1]['end']:
        return True

    return False