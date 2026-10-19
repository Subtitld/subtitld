"""A video whose picture the player cannot decode (AV1 with no GPU that
decodes it, say, where PySide6's FFmpeg has no software AV1 decoder): the
player runs to the end without showing a frame. The preview then puts the
playhead back at the start instead of leaving it stuck at the end, and says
so once per video loaded; the video properties panel answers by offering the
proxy, or by using the one already made. A video that played and reached its
end the ordinary way is left to the usual step back from the end.

The player's state and the dialog are stand-ins; the preview's and the
panel's code is the real one.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtMultimedia import QMediaPlayer
app = QApplication([])
from subtitld.modules import audioengine, session
from subtitld.interface import preview_panel, left_panel_videoproperties as vp

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def no_device(**_kwargs):
    raise audioengine.sd.PortAudioError('no device in the test')


audioengine.sd.OutputStream = no_device
session.SUBTITLE = {'segments': [], 'selected': None, 'position': 0.0}

video = Path(tempfile.mkdtemp()) / 'clip.mkv'
video.write_bytes(b'not really a video')

player = preview_panel.PlayerWidget()
emitted = []
player.picture_unavailable_signal.connect(lambda: emitted.append(True))
seeks = []
player._audio_device.seek = lambda position: seeks.append(position)


class Stand(QMediaPlayer):
    """QMediaPlayer, as far as the end-of-media handling asks it."""
    video = True
    length = 2454921

    def hasVideo(self):
        return self.video

    def duration(self):
        return self.length


stand = Stand()
positions = []
stand.setPosition = lambda ms: positions.append(ms)
player._media_player = stand

print('the end, without a frame shown')
player.loadfile(str(video))
session.SUBTITLE['position'] = 2454.921
player._on_media_status_changed(QMediaPlayer.EndOfMedia)
check('said once', emitted, [True])
check('the playhead back at the start', session.SUBTITLE['position'], 0.0)
check('the sound engine there too', seeks[-1:], [0.0])
check('the player not stepped back from the end', positions, [])
player._on_media_status_changed(QMediaPlayer.EndOfMedia)
check('and not said again for the same video', emitted, [True])

print('a video loaded again starts over')
player.loadfile(str(video))
player._on_media_status_changed(QMediaPlayer.EndOfMedia)
check('said for it', emitted, [True, True])

print('the end of a video that played')
emitted.clear()
player.loadfile(str(video))
player._picture_seen = True
player._on_media_status_changed(QMediaPlayer.EndOfMedia)
check('nothing said', emitted, [])
check('stepped back from the end, the usual way', len(positions), 1)

print('the end of a sound-only file')
positions.clear()
player.loadfile(str(video))
stand.video = False
player._on_media_status_changed(QMediaPlayer.EndOfMedia)
check('nothing said', emitted, [])
stand.video = True
player._audio_device.shutdown()

print('the offer')


class Window(QWidget):
    """What offer_proxy asks of the window."""
    def __init__(self):
        super().__init__()
        self._videoproperties_encode_thread = None
        self.left_panel_videoproperties_tabwidget = type('Tabs', (), {'setCurrentIndex': lambda s, i: shown.append(('tab', i))})()


shown, started, activated = [], [], []
vp.left_panel.show_panel = lambda window, name: shown.append(('panel', name))
vp._start_encode = lambda window, original, scale: started.append((original, scale))
vp._activate = lambda window, path: activated.append(path)
vp._selected_scale = lambda window: 50
answer = [1]
vp.utils.SimpleDialog.exec = lambda dialog: answer[0]
session.VIDEO = {'filepath': str(video), 'codec': 'av1'}

vp.proxy.existing_proxy = lambda original, scale: '/cache/proxy.mp4'
vp.offer_proxy(Window())
check('a proxy already made is used, without asking', (activated, started), (['/cache/proxy.mp4'], []))

vp.proxy.existing_proxy = lambda original, scale: None
vp.offer_proxy(Window())
check('accepted: the proxy is made', started, [(str(video), 50)])
check('with its progress in view', shown, [('panel', 'videoproperties'), ('tab', vp._TAB_PROXY)])

started.clear()
answer[0] = 0
vp.offer_proxy(Window())
check('declined: nothing made', started, [])

session.VIDEO['proxy_filepath'] = '/cache/proxy.mp4'
answer[0] = 1
vp.offer_proxy(Window())
check('not offered while a proxy is already in use', started, [])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
