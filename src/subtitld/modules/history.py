"""History module (Ctrl+Z)

"""

import copy

from subtitld.modules import session

ALL_HISTORY = []
REDO_HISTORY = []


def history_append(subtitles):
    """Append subtitles list to history"""
    ALL_HISTORY.append(copy.deepcopy(subtitles))
    REDO_HISTORY.clear()


def history_undo():
    """Revert to last subtitle on the history list"""
    if ALL_HISTORY:
        REDO_HISTORY.append(copy.deepcopy(session.SUBTITLE['segments']))
        session.SUBTITLE['segments'].clear()
        session.SUBTITLE['segments'].extend(copy.deepcopy(ALL_HISTORY.pop()))


def history_redo():
    """Redo subtitle on the history list"""
    if REDO_HISTORY:
        session.SUBTITLE['segments'].clear()
        session.SUBTITLE['segments'].extend(copy.deepcopy(REDO_HISTORY.pop()))
