import sys
import os
import argparse
import pathlib
import inspect

from PySide6.QtWidgets import QApplication, QWidget, QStackedLayout, QHBoxLayout, QLabel, QDialog
from PySide6.QtGui import QFont, QFontDatabase, QShortcut, QKeySequence
from PySide6.QtCore import QDir, QTimer
from qframelesswindow import FramelessMainWindow

from subtitld.interface import top_bar
from subtitld.interface import startscreen
from subtitld.interface import productionscreen
from subtitld.interface import actionmanager
from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import config
from subtitld.modules import file_io
from subtitld.modules import shortcuts


parser = argparse.ArgumentParser(description='Subtitld is a software to create, edit and transcribe subtitles')
parser.add_argument('file', type=argparse.FileType('r'), help='The path for video or subtitle file', nargs='*', default=False)
parser.add_argument('--version', help='Prints the actual version of Subtitld.', action='store_true')
args = parser.parse_args()


class Window(FramelessMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("Subtitld")
        self.setStyleSheet(open(os.path.join(session.PATH_SUBTITLD_GRAPHICS, 'stylesheet.qss')).read())
        
        session.CONFIG = config.Config()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(int(session.CONFIG['autosave'].get('interval', 300000)))
        self.autosave_timer.timeout.connect(lambda: file_io.autosave_timer_timeout())

        top_bar.load(self)

        self.central_widget = QWidget(self)
        self.central_widget.setLayout(QStackedLayout())
        self.central_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self.central_widget)

        startscreen.load(self)
        productionscreen.load(self)
        
        shortcuts.load(self, session.CONFIG['shortcuts'])

        self.titleBar.raise_()
        self.showMaximized()

        if session.SUBTITLE.get('filepath', False) and session.VIDEO.get('filepath', False):
            session.SUBTITLE['segments'], session.CONFIG['format_to_save'] = file_io.process_subtitles_file(session.SUBTITLE['filepath'])
            session.VIDEO = file_io.process_video_file(session.VIDEO['filepath'])
            startscreen.load_productionscreen(self)
        else:
            startscreen.show(self)

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
        #     session.CONFIG['recent_files'][session.SUBTITLE['filepath']]['last_position'] = session.SUBTITLE.get('position', 0)
        
        # session.CONFIG['window_position'] = {'x': self.x(), 'y': self.y(), 'width': self.width(), 'height': self.height()}

        # config.save(session.CONFIG, session.PATH_SUBTITLD_USER_CONFIG_FILE)
        # self.player_widget.close()

        self.hide()

        if self.timeline_widget.audio_thread.isRunning():
            self.timeline_widget.audio_thread.cancel()
            self.timeline_widget.audio_thread.wait()

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
    if args.file:
        for filepath in args.file:
            filepath = pathlib.Path(filepath.name)            
            if filepath.suffix[1:].upper() in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS.keys():
                session.SUBTITLE['filepath'] = str(filepath)
                break 
        
        for filepath in args.file:
            filepath = pathlib.Path(filepath.name)
            if filepath.suffix[1:].upper() in session.LIST_OF_SUPPORTED_VIDEO_EXTENSIONS.keys():
                session.VIDEO['filepath'] = str(filepath)
                break
        
    app = QApplication(sys.argv)

    QDir.addSearchPath('graphics', session.PATH_SUBTITLD_GRAPHICS)

    for font_file in os.listdir(session.PATH_SUBTITLD_GRAPHICS):
        if font_file.endswith('.ttf'):
            QFontDatabase.addApplicationFont(os.path.join(session.PATH_SUBTITLD_GRAPHICS, font_file))

    app.setApplicationName("Subtitld")
    app.setFont(QFont('Montserrat', 10))

    main_window = Window()
    main_window.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()