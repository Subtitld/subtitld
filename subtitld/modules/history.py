"""History module (Ctrl+Z)

"""

import copy

from subtitld.modules import globals

ALL_HISTORY = []
REDO_HISTORY = []


def history_append(subtitles):
    """Append subtitles list to history"""
    ALL_HISTORY.append(copy.deepcopy(subtitles))
    REDO_HISTORY.clear()


def history_undo():
    """Revert to last subtitle on the history list"""
    if ALL_HISTORY:
        REDO_HISTORY.append(copy.deepcopy(globals.SESSION['segments']))
        globals.SESSION['segments'].clear()
        globals.SESSION['segments'].extend(copy.deepcopy(ALL_HISTORY.pop()))


def history_redo():
    """Redo subtitle on the history list"""
    if REDO_HISTORY:
        globals.SESSION['segments'].clear()
        globals.SESSION['segments'].extend(copy.deepcopy(REDO_HISTORY.pop()))
