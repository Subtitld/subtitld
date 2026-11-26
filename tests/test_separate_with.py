"""Separating the open video again with an add-on, from the music ↔ voice balance's menu.

- The menu is there only while the default separator is the built-in one and an
  add-on separates too; it lists both, the one whose stems are loaded checked.
- A run picked there goes to that separator whatever the default is, in a folder
  of its own (the separators hand back stems already in theirs), and its stems
  take the cache's place; the default stays as it was.
- The player swaps the separated tracks rather than adding to them, and keeps
  the balance.
- A run that fails leaves the stems and tracks there were.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget
import numpy as np
import soundfile as sf
app = QApplication([])
from subtitld.modules import session, utils
from subtitld.modules.addons import manager as addon_manager, registry
from subtitld.modules.addons.builtin.ffmpeg_separator_provider import FfmpegSeparatorProvider
from subtitld.modules.addons.provider import AudioSeparatorProvider, TASK_AUDIO_SEPARATE
from subtitld.interface import player_widgets, playercontrols

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


class FakeSeparator(AudioSeparatorProvider):
    """Writes a constant tone as both stems, under its own names, or fails."""

    def __init__(self, level, fail=False):
        super().__init__()
        self.level, self.fail, self.calls = level, fail, []

    @property
    def id(self):
        return 'fake-separator'

    @property
    def display_name(self):
        return 'Fake Separator'

    def separate(self, input_path, output_dir, options=None):
        self.calls.append(output_dir)
        if self.fail:
            self.separation_error.emit(input_path, 'out of memory')
            return
        self.progress.emit(0.5, 'half')
        paths = []
        for kind in ('Vocals', 'Instrumental'):
            path = os.path.join(output_dir, f'out_({kind}).flac')
            sf.write(path, np.full(4800, self.level, np.float32), 48000)
            paths.append(path)
        self.separation_ready.emit(input_path, *paths)


work = Path(tempfile.mkdtemp())
media = work / 'film.wav'
sf.write(media, np.zeros((4800, 2), np.float32), 48000)
session.VIDEO = {'filepath': str(media)}
os.makedirs(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, exist_ok=True)
key = utils.get_cache_key(str(media))
stem = lambda kind: os.path.join(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION, f'{key}_{kind}.flac')
for kind in ('vocals', 'background'):
    sf.write(stem(kind), np.full(4800, 0.1, np.float32), 48000)

mgr = addon_manager.AddonManager()
mgr.register_builtin(FfmpegSeparatorProvider())
addon_manager._manager_instance = mgr

# A window as far as the menu and the run need it.
window = QWidget()
window.music_voice_separation_box = player_widgets.BalanceSlider('music_voice_separation_box', window)
window.music_voice_separation_slider = window.music_voice_separation_box
window.music_voice_separation_thread = playercontrols.MusicAudioExtractorThread(parent=window)
shown = []


class ShownMenu(QMenu):
    def exec(self, pos=None):
        shown.append([(a.text(), a.isEnabled(), a.isChecked()) for a in self.actions()])


playercontrols.QMenu = ShownMenu


def menu():
    shown.clear()
    playercontrols.music_voice_separation_menu(window, QPoint())
    return shown[0] if shown else None


print('no menu with only the built-in separator')
check('menu', menu(), None)

fake = FakeSeparator(0.7)
mgr.register(fake)
print('the menu while the built-in separator is the default and an add-on separates too')
items = menu()
check('items', [text for text, _enabled, _checked in items], ['Separate this video with', FfmpegSeparatorProvider().display_name, 'Fake Separator'])
check('title disabled', items[0][1], False)
check('the loaded stems are the built-in one\'s', [checked for _t, _e, checked in items[1:]], [True, False])

registry.set_default_for_task(TASK_AUDIO_SEPARATE, 'fake-separator')
print('no menu once the add-on is the default')
check('menu', menu(), None)
registry.set_default_for_task(TASK_AUDIO_SEPARATE, FfmpegSeparatorProvider.PROVIDER_ID)

print('a run with the add-on, for this video only')
ready, progress = [], []
thread = window.music_voice_separation_thread
thread.run_stems_ready.connect(ready.append)
thread.run_progress.connect(progress.append)
thread.filename = str(media)
thread.separate_with(fake)
check('ran in its own empty folder', fake.calls[-1] != str(session.PATH_SUBTITLD_DATA_AUDIOSEPARATION), True)
check('progress', progress, [0.5])
check('stems handed over', len(ready), 1)
check('cache not touched yet', round(float(sf.read(stem('vocals'))[0][0]), 2), 0.1)
paths = thread.install_stems(ready[0])
check('paths are the cache\'s', paths, {'vocals': stem('vocals'), 'background': stem('background')})
check('the add-on\'s vocals in the cache', round(float(sf.read(stem('vocals'))[0][0]), 2), 0.7)
check('the add-on\'s background in the cache', round(float(sf.read(stem('background'))[0][0]), 2), 0.7)
check('run folder gone', os.path.exists(ready[0]['dir']), False)
check('provider recorded', ready[0]['provider'], 'fake-separator')
check('default unchanged', registry.default_for_task(TASK_AUDIO_SEPARATE), FfmpegSeparatorProvider.PROVIDER_ID)
check('not running', thread.running, False)

print('a run that fails leaves the stems there were')
errors = []
thread.run_error.connect(errors.append)
failing = FakeSeparator(0.9, fail=True)
thread.separate_with(failing)
check('error', errors, ['out of memory'])
check('vocals kept', round(float(sf.read(stem('vocals'))[0][0]), 2), 0.7)
check('not running', thread.running, False)

print('the player swaps the separated tracks rather than adding to them')
engine = SimpleNamespace(tracks=[])
window.preview_panel_player = SimpleNamespace(_audio_device=engine)
original, old_background, old_vocals = object(), object(), object()
engine.tracks += [original, old_background, old_vocals]
engine.background_sound, engine.vocals_sound = old_background, old_vocals
playercontrols.music_voice_separation_drop_tracks(window)
check('only the original left', engine.tracks, [original])

print('the error is shown')
warned = []
playercontrols.QMessageBox = SimpleNamespace(warning=lambda *args: warned.append(args[2]))
window._separation_run_name = 'Fake Separator'
playercontrols.music_voice_separation_run_error(window, 'out of memory')
check('message', warned, ['Fake Separator could not separate this video:\nout of memory'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
