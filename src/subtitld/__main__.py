import sys
import os
import argparse
import inspect

from subtitld import __version__


def _existing_file(value):
    """A path on the command line, checked but not opened: argparse's
    FileType kept every file open, and Windows will not replace an open
    file, which is how a project is saved over itself."""
    if not os.path.isfile(value):
        raise argparse.ArgumentTypeError(f"can't open '{value}'")
    return os.path.abspath(value)


# Before the heavy imports below (MediaPipe, Qt): --version and --help
# answer at once, and exit without starting the app.
parser = argparse.ArgumentParser(prog='subtitld',
                                 description='Subtitld is a software to create, edit and transcribe subtitles')
parser.add_argument('file', type=_existing_file, help='The path for video or subtitle file', nargs='*', default=False)
parser.add_argument('--version', action='version', version=f'Subtitld {__version__}',
                    help='Prints the version of Subtitld and exits.')
args = parser.parse_args()

# Before anything loads PortAudio through ctypes (sounddevice): find the
# copy a snap or another bundle carries on LD_LIBRARY_PATH.
from subtitld.modules import library_path
library_path.install()

# MediaPipe (used by the Speakers face detection) must be imported before
# PySide6/Qt when it is a release with the legacy `solutions` API (0.10.21
# and older, built on protobuf; 0.10.33 and newer do not care). Importing
# one of those *after* Qt has loaded fails to build its
# bundled proto descriptors ("Invalid default '0.5' ... into descriptor
# pool"); importing it first registers them cleanly, and the later
# `import mediapipe` in modules/face_detection reuses this cached, working
# module. Guarded so a missing/broken install degrades gracefully instead of
# blocking app start.
try:
    import mediapipe as _mediapipe_boot  # noqa: F401
except Exception:
    pass

from PySide6.QtWidgets import QApplication, QWidget, QStackedLayout, QHBoxLayout, QLabel, QDialog
from PySide6.QtGui import QFont, QFontDatabase, QShortcut, QKeySequence, QIcon
from PySide6.QtCore import QDir, QEvent, QObject, QTimer
# Haiku draws every window with its own native decorator (the yellow tab),
# which already provides minimise/zoom/close. Using the frameless window
# there would either hide that decorator or duplicate its buttons, so fall
# back to a plain QMainWindow and let the system draw the frame.
IS_HAIKU = sys.platform.startswith('haiku')
if IS_HAIKU:
    from PySide6.QtWidgets import QMainWindow as _MainWindowBase
else:
    from qframelesswindow import FramelessMainWindow as _MainWindowBase

from subtitld.interface import top_bar
from subtitld.interface import startscreen
from subtitld.interface import productionscreen
from subtitld.interface import actionmanager
from subtitld.interface import utils
from subtitld.interface import translation
from subtitld.interface.translation import _
# Imported for the side-effect of registering its ``@shortcut`` handler
# at module load time — `shortcuts.load(self, ...)` below picks it up
# from the global registry and wires the Ctrl+H / Ctrl+F binding.
from subtitld.interface import find_replace_dialog as _find_replace_dialog  # noqa: F401

from subtitld.modules import session
from subtitld.modules import config
from subtitld.modules import file_io
from subtitld.modules import shortcuts
from subtitld.modules import addons
from subtitld.modules.addons import registry as addons_registry
from subtitld.modules.addons.builtin import captions_provider as _captions_provider
from subtitld.modules.addons.builtin import edge_tts_provider as _edge_tts_provider
from subtitld.modules.addons.builtin import ffmpeg_separator_provider as _ffmpeg_separator_provider
from subtitld.modules.addons.builtin import gtts_provider as _gtts_provider
from subtitld.modules.addons.builtin import import_provider as _import_provider
from subtitld.modules.addons.builtin import subtitles_provider as _subtitles_provider


def _set_class_icon(window):
    """Windows: Subtitld's icon on the window's class too, not only on the
    window. The taskbar asks the window for its icon as the button appears;
    started with a file, the app is busy opening it right then, the taskbar
    gives up waiting and takes the class's icon instead: Windows' generic
    one, as Qt looks for an IDI_ICON1 resource the PyInstaller build does
    not have. Before the window is shown, so the button never sees another."""
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT)
    user32.SetClassLongPtrW.restype = ctypes.c_void_p
    user32.SetClassLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
    SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50
    GCLP_HICON, GCLP_HICONSM = -14, -34
    path = os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'subtitld.ico')
    hwnd = int(window.winId())
    for index, width, height in ((GCLP_HICON, SM_CXICON, SM_CYICON), (GCLP_HICONSM, SM_CXSMICON, SM_CYSMICON)):
        icon = user32.LoadImageW(None, path, IMAGE_ICON, user32.GetSystemMetrics(width),
                                 user32.GetSystemMetrics(height), LR_LOADFROMFILE)
        if icon:
            user32.SetClassLongPtrW(hwnd, index, icon)


class FileOpenFilter(QObject):
    """macOS (Finder) and Haiku (Tracker) open a file with an event, not on
    the command line. On the start screen it opens as the Open button opens
    it; with a project open, the window comes to the front instead, so the
    project is not replaced unasked."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, watched, event):
        if event.type() != QEvent.FileOpen:
            return False
        path = event.file()
        if path and os.path.isfile(path):
            QTimer.singleShot(0, lambda: self.open(path))
        return True

    def open(self, path):
        window = self.window
        if window.central_widget.layout().currentWidget() is window.start_screen:
            startscreen.open_files(window, [path])
        else:
            window.raise_()
            window.activateWindow()


class Window(_MainWindowBase):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("Subtitld")
        self.setStyleSheet(open(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'stylesheet.qss')).read())
        
        session.CONFIG = config.Config()

        # The language picked in the global settings, or else the system's.
        # Set before any widget is built: some take their texts as they are.
        translation.set_language(translation.pick_language(session.CONFIG.get('interface_language')))

        # Normalise the add-ons config against the CONFIG that was just loaded
        # from disk. The manager seeds it too, but the manager is built back in
        # `main()` BEFORE this line runs, when `session.CONFIG` is still the
        # empty placeholder from `session.py` — so anything it did there was
        # written to a dict that this assignment throws away. `ensure_seeded`
        # is idempotent, and its one-shot migration is revision-guarded, so
        # calling it from both places is safe; this is the call that reaches
        # the dict `CONFIG.save()` actually writes back.
        addons_registry.ensure_seeded()

        self.autosave_backup_timer = QTimer(self)
        self.autosave_backup_timer.setInterval(int(session.CONFIG['autosave'].get('backup_interval', 300000)))
        self.autosave_backup_timer.timeout.connect(lambda: file_io.autosave_backup_timer_timeout())

        self.autosave_original_timer = QTimer(self)
        self.autosave_original_timer.setInterval(int(session.CONFIG['autosave'].get('original_interval', 300000)))
        self.autosave_original_timer.timeout.connect(lambda: file_io.autosave_original_timer_timeout())

        if IS_HAIKU:
            # No frameless base class here, so stand in for the `titleBar`
            # widget that top_bar.load() expects. It overlays the top of the
            # window; resizeEvent() keeps it full-width.
            self.titleBar = QWidget(self)
            self.titleBar.setLayout(QHBoxLayout())
            self.titleBar.layout().setContentsMargins(0, 0, 0, 0)
            self.titleBar.layout().setSpacing(0)

        top_bar.load(self)

        self.central_widget = QWidget(self)
        self.central_widget.setLayout(QStackedLayout())
        self.central_widget.layout().setContentsMargins(0, self.titleBar.minimumHeight(), 0, 0)
        self.setCentralWidget(self.central_widget)

        startscreen.load(self)
        try:
            productionscreen.load(self)
        except BaseException:
            # The add-ons panel is built first and starts its catalog fetch
            # at once. If a later panel fails, this half-built window is
            # destroyed with that QThread still running, and Qt aborts the
            # process over it, burying the real traceback: stop it first.
            addons_panel = getattr(self, 'left_panel_global_addons_panel', None)
            if addons_panel is not None:
                addons_panel.shutdown()
            raise

        shortcuts.load(self, session.CONFIG['shortcuts'])

        self.titleBar.raise_()
        _set_class_icon(self)
        self.showMaximized()

        # The files on the command line, which is how a file association
        # passes the one file double-clicked: opened as the Open button opens
        # them. A subtitle and its video, a video, or a .usfx (it names its
        # video) open at once; a subtitle alone asks for its video once the
        # start screen is up.
        # `load_productionscreen` itself runs `process_subtitles_file`
        # + `process_video_file` + `sync_subtitle_dubs`. Don't run them
        # here first — doing so would double the Phase 1 zip extract,
        # ffprobe, and USF parse on the main thread before the screen
        # transition. See the matching note in
        # `start_screen_recent_listwidget_item_clicked`.
        files = list(args.file) if args.file else []
        if not (files and startscreen.open_files(self, files, ask=False)):
            startscreen.show(self)
            if files:
                QTimer.singleShot(0, lambda: startscreen.open_files(self, files))

        # self.action_manager.register("open", "open_file", "Ctrl+O", self.open_file)
        # self.action_manager.register("save", "save_file", "Ctrl+S", self.save_file)
        
        # session.action_manager = actionmanager.ActionManager(parent=self.central_widget)

        # actionmanager.register(self, "play_pause", "play_pause", "Space", playercontrols_playpause_button_clicked(self))


        
        
        # qshortcut = QShortcut(QKeySequence("Space"), self)
        
        # qshortcut.activated.connect(lambda: playercontrols_playpause_button_clicked(self))

        # self.register_shortcut("Ctrl+O", self.open_button_clicked, self.title_widget_load_line_button, "Open")
        # self.register_shortcut("Ctrl+S", self.save_entities, None, "Save entities")
        # self.register_shortcut("Left", self.previous_button_clicked, self.previous_button, "Previous")
        # self.register_shortcut("Right", self.next_button_clicked, self.next_button, "Next")
        # self.register_shortcut("Esc", self.close, self.close_button, "Close window")
        # self.register_shortcut("1", lambda: self.type_button_clicked('blue'), self.blue_button, "Blue type")
        # self.register_shortcut("2", lambda: self.type_button_clicked('red'), self.red_button, "Red type")
        # self.register_shortcut("3", lambda: self.type_button_clicked('vip'), self.vip_button, "VIP type")
        # self.register_shortcut("4", lambda: self.type_button_clicked('noncombatant'), self.noncombatant_button, "Non combatant type")
        # self.register_shortcut("Space", playercontrols_playpause_button_clicked(self), self.playercontrols_playpause_button, "Play/Pause")


        # def register_shortcut(self, keyseq, handler, button, description):
        #     shortcut = QShortcut(QKeySequence(keyseq), self)
        #     shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        #     shortcut.activated.connect(handler)

        #     if button:
        #         text = button.toolTip() or description or ""
        #         key_hint = f" ({keyseq})"
        #         if key_hint not in text:
        #             button.setToolTip(text + key_hint)
            
        #     return shortcut


        # def keyPressEvent(self, event):
        #     if event.key() == Qt.Key_Space:
        #         playercontrols_playpause_button_clicked(self)
                # self.player_widget.pause()
                # self.playercontrols_playpause_button.setChecked(not self.playercontrols_playpause_button.isChecked())
                # playercontrols.playercontrols_playpause_button_update(self)

        # if event.key() == Qt.Key_F1:
        #     playercontrols.add_subtitle_button_clicked(self)

        # if event.key() == Qt.Key_F12:
        #     playercontrols.add_subtitle_button_clicked(self)

        # if event.key() == Qt.Key_Z:
        #     if event.modifiers() == Qt.ControlModifier | Qt.ShiftModifier:
        #         history.history_redo()
        #     elif event.modifiers() == Qt.ControlModifier:
        #         history.history_undo()
        #     session.SUBTITLE['selected'] = None
        #     subtitles_panel.update_subtitles_panel_widget_vision_content(self)
        #     # self.properties.update_properties_widget(self)
        #     timeline.update(self)

        # if event.key() == Qt.Key_Left:
        #     self.player_widget.frameBackStep()

        # if event.key() == Qt.Key_Right:
        #     self.player_widget.frameStep()

        # methods = [name for name in dir(actionmanager) if callable(getattr(actionmanager, name))]

        # for name, fn in inspect.getmembers(actionmanager, inspect.ismethod):
        #     function_name = getattr(fn, "name", None)
        #     function_default_shortcut = getattr(fn, "default_shortcut", None)

        for name, fn in inspect.getmembers(actionmanager, inspect.isfunction):
            function_name = getattr(fn, "name", None)
            function_default_shortcut = getattr(fn, "default_shortcut", None)
        
        self.confirm_exit_dialog = confirm_exit_dialog(self)



        self.translate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if IS_HAIKU and getattr(self, 'titleBar', None) is not None:
            # A plain QMainWindow does not lay the overlay out for us.
            self.titleBar.setGeometry(0, 0, self.width(), self.titleBar.height())

    def translate(self):
        startscreen.translate(self)
        productionscreen.translate(self)

        self.confirm_exit_dialog.set_title(_('confirm_exit_dialog.title'))
        self.confirm_exit_dialog.main_label.setText(_('confirm_exit_dialog.text'))
        self.confirm_exit_dialog.accept_button.setText(_('confirm_exit_dialog.save_button'))
        self.confirm_exit_dialog.reject_button.setText(_('confirm_exit_dialog.dont_save_button'))
        

    def closeEvent(self, event):
        if session.UNSAVED:
            ret = self.confirm_exit_dialog.exec_()
            if ret:
                top_bar.toppanel_save_button_clicked(self)

        # self.thread_get_waveform.quit()
        # self.thread_get_qimages.quit()
        # # self.thread_extract_scene_time_positions.quit()
        # self.thread_generated_burned_video.quit()
        # self.thread_extract_waveform.quit()
        # if session.SUBTITLE.get('subtitle_filepath', False) and 'hash' in session.VIDEO:
        #     self.player_widget.grab().save(os.path.join(session.PATH_SUBTITLD_DATA_THUMBNAILS, session.VIDEO['hash'] + '.png'))

        # session.CONFIG['window_position'] = {'x': self.x(), 'y': self.y(), 'width': self.width(), 'height': self.height()}

        # config.save(session.CONFIG, session.PATH_SUBTITLD_USER_CONFIG_FILE)
        # self.player_widget.close()

        self.hide()

        if self.timeline_widget.audio_thread.isRunning():
            self.timeline_widget.audio_thread.cancel()
            self.timeline_widget.audio_thread.wait()

        file_io.wait_for_save_threads()

        from subtitld.modules import proxy
        proxy.cancel_all_proxy_encodes()
        proxy.wait_for_proxy_threads()

        if hasattr(self, 'record_controller'):
            self.record_controller.shutdown()

        session.persist_current_playback_position()
        session.CONFIG.save()

        event.accept()


class confirm_exit_dialog(utils.SimpleDialog):
    def __init__(self, parent=None, title=''):
        super().__init__(parent, title)

        self.input_line = QWidget()
        self.input_line.setLayout(QHBoxLayout())
        self.input_line.layout().setContentsMargins(0, 0, 0, 0)

        self.main_label = QLabel()
        self.input_line.layout().addWidget(self.main_label)

        self.content.layout().addWidget(self.input_line)


def main():
    app = QApplication(sys.argv)

    # ---- Paint-conflict guard -----------------------------------------
    # If any widget's paintEvent ever leaves a QPainter open on its device
    # (e.g. a paint body that raised before end()), Qt's backing-store
    # compositor then fails to paint that widget every frame and floods the
    # console with "A paint device can only be painted by one painter at a
    # time" / "Painter not active". That spam is useless once seen. This
    # handler swallows exactly those two messages (passing everything else
    # through unchanged) and, on the FIRST occurrence, logs which widget is
    # holding the stuck painter so the real leak can be fixed.
    from PySide6.QtCore import qInstallMessageHandler as _install_msg_handler

    _paint_conflict_state = {'diagnosed': False}

    def _subtitld_message_handler(mode, context, message):
        if ('one painter at a time' in message) or ('Painter not active' in message):
            if not _paint_conflict_state['diagnosed']:
                _paint_conflict_state['diagnosed'] = True
                try:
                    culprits = []
                    for _wdg in QApplication.allWidgets():
                        try:
                            if _wdg.paintingActive():
                                _chain, _x = [], _wdg
                                for _ in range(5):
                                    if _x is None:
                                        break
                                    _chain.append('{}({})'.format(type(_x).__name__, _x.objectName() or '-'))
                                    _x = _x.parentWidget()
                                culprits.append(' < '.join(_chain))
                        except Exception:
                            pass
                    sys.stderr.write(
                        '[subtitld] paint-conflict suppressed. Widget holding a '
                        'stuck painter: {}\n'.format(culprits or ['<none found>']))
                    sys.stderr.flush()
                except Exception:
                    pass
            return  # swallow the repeated flood
        try:
            sys.stderr.write(message + '\n')
        except Exception:
            pass

    _install_msg_handler(_subtitld_message_handler)

    # ---- Add-on registry bootstrap ------------------------------------
    # Built-in providers are registered first so they're always available
    # right after install, even before the user discovers the add-ons
    # panel. Today's set:
    #   - Edge TTS — speech synthesis, no credentials required
    #   - FFmpeg separator — audio source separation, no model required
    # No speech-to-text engine ships as a built-in. Offline whisper.cpp and
    # RealtimeSTT are add-ons, which keeps the app download small; the cloud
    # ones are add-ons so a stock Subtitld carries no third-party service
    # integration the user did not ask for. The cloud plumbing itself
    # (subtitld_cloud_shared, the account dashboard) stays — it is what an
    # installed cloud add-on authenticates through.
    # Subprocess add-ons under ~/.local/share/subtitld/addons/ are
    # discovered next; they may depend on a QApplication being live
    # (provider QObjects are auto-parented to it), so this happens
    # after `QApplication(sys.argv)`.
    addon_manager = addons.get_manager()
    addon_manager.register_builtin(_edge_tts_provider.get_provider())
    addon_manager.register_builtin(_ffmpeg_separator_provider.get_provider())
    addon_manager.register_builtin(_gtts_provider.get_provider())
    addon_manager.register_builtin(_import_provider.get_provider())
    addon_manager.register_builtin(_captions_provider.get_provider())
    addon_manager.register_builtin(_subtitles_provider.get_provider())
    try:
        discovered = addon_manager.discover()
        if discovered:
            print(f'Loaded add-ons: {", ".join(discovered)}')
    except Exception as exc:
        print(f'Add-on discovery failed: {exc}')
    app.aboutToQuit.connect(addon_manager.shutdown_all)

    QDir.addSearchPath('graphics', session.PATH_SUBTITLD_GRAPHICS)

    for font_file in os.listdir(session.PATH_SUBTITLD_GRAPHICS):
        if font_file.endswith('.ttf'):
            QFontDatabase.addApplicationFont(os.path.join(session.PATH_SUBTITLD_GRAPHICS, font_file))

    app.setApplicationName("Subtitld")
    app.setFont(QFont('Montserrat', 10))
    # The window's icon (title bar, taskbar, alt-tab). Packaged builds also
    # name it in their desktop entry; run from source, this is the only one.
    app.setWindowIcon(QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'subtitld.png')))

    main_window = Window()
    main_window.show()
    app.installEventFilter(FileOpenFilter(main_window))

    sys.exit(app.exec())

if __name__ == '__main__':
    main()