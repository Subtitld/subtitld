from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import QObject

from subtitld.modules import session

from subtitld.interface.playercontrols import playercontrols_playpause_button_clicked

def action(name, default_shortcut=None):
    def decorator(func):
        func.name = name
        func.default_shortcut = default_shortcut
        return func
    return decorator


@action("Toggles play/pause state.", default_shortcut="Space")
def play_pause(self):
    playercontrols_playpause_button_clicked(self)