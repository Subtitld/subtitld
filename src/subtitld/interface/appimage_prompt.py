"""Run from an AppImage, Subtitld asks once whether to add itself to the applications menu
(modules/appimage_integration.py); the answer is kept, and Settings > General changes it."""

from PySide6.QtWidgets import QLabel

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import appimage_integration, session


def choice():
    return (session.CONFIG['appimage'] or {}).get('integrate')


def set_choice(value):
    session.CONFIG['appimage'] = {**(session.CONFIG['appimage'] or {}), 'integrate': value}
    session.CONFIG.save()


def check(window):
    """On starting: ask, or point the entry at this AppImage again when it moved."""
    action = appimage_integration.decide(choice())
    if action == 'register':
        appimage_integration.register()
    elif action == 'ask':
        ask(window)


def ask(window):
    dialog = utils.SimpleDialog(window, title=_('appimage.ask_title'))
    message = QLabel(_('appimage.ask_message'))
    message.setWordWrap(True)
    dialog.content.layout().addWidget(message)
    dialog.accept_button.setText(_('appimage.ask_add'))
    dialog.reject_button.setText(_('appimage.ask_not_add'))
    if dialog.exec():
        set_choice('yes')
        appimage_integration.register()
    else:
        set_choice('no')
