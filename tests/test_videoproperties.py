"""Video properties panel: Source cards and the Proxy tab's states.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import QDir, QSize
app = QApplication([])
from subtitld.modules import session, proxy, file_io, waveform
QDir.addSearchPath('graphics', session.PATH_SUBTITLD_GRAPHICS)
from subtitld.interface import left_panel_videoproperties as vp

fails = []


def check(name, cond, extra=''):
    print(f'  [{"OK " if cond else "FAIL"}] {name}{(" - " + str(extra)) if extra else ""}')
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp())
session.PATH_SUBTITLD_DATA_PROXY = tmp / 'proxy'
session.PATH_SUBTITLD_DATA_PROXY.mkdir()
source = tmp / 'movie.mp4'
source.write_bytes(b'x' * 1000)
session.CONFIG = {'proxy': {'scale': 50}}
session.SUBTITLE = {'segments': [], 'position': 12.5}


class Player:
    def __init__(self):
        self.loaded = []

    def loadfile(self, path):
        self.loaded.append(path)

    def seek(self, pos):
        self.sought = pos


class FakeThread:
    cancelled = False

    def cancel(self):
        self.cancelled = True


# The panel registers itself with the window's stacked widget; a plain
# container stands in for it.
def _fake_panel(parent, tab_name, update_callback, translate_callback):
    panel = QWidget()
    panel.setLayout(QVBoxLayout())
    parent._panel = panel
    return panel


vp.left_panel.left_panel = _fake_panel

host = QWidget()
host.preview_panel_player = Player()
vp.load(host)
# The real stylesheet: widths and folding depend on it.
host._panel.setStyleSheet((session.PATH_SUBTITLD_GRAPHICS / 'stylesheet.qss').read_text())
host._panel.resize(780, 500)
host._panel.show()
encodes = []


def fake_encode(original, scale, duration, on_progress, on_done, parent):
    thread = FakeThread()
    encodes.append((original, scale, on_done, thread))
    return thread


vp.proxy.encode_proxy_async = fake_encode


def load_video(**extra):
    session.VIDEO = {'filepath': str(source), 'width': 1920, 'height': 1080,
                     'framerate': 30.0013, 'duration': 1476, 'codec': 'h264', **extra}
    vp.translate(host)


def status():
    return host.videoproperties_status_title.text(), host.videoproperties_status_detail.text()


def settle():
    for _ in range(3):
        app.processEvents()


print('[1] tabs and source cards')
load_video()
tabs = host.left_panel_videoproperties_tabwidget
check('two tabs: Source, Proxy', [tabs.tabText(i) for i in range(tabs.count())] == ['Source', 'Proxy'],
      [tabs.tabText(i) for i in range(tabs.count())])
check('resolution frame carries the dimensions',
      (host.videoproperties_resolution_card.frame._w, host.videoproperties_resolution_card.frame._h) == (1920, 1080))
check('frame hugs a 16:9 drawing', host.videoproperties_resolution_card.frame.size() == QSize(142, 80),
      host.videoproperties_resolution_card.frame.size())
check('frame rate', host.videoproperties_framerate_card.value.text() == '30.0013fps',
      host.videoproperties_framerate_card.value.text())
check('duration', host.videoproperties_duration_card.value.text() == '00:24:36')
check('codec', host.videoproperties_codec_card.value.text() == 'h264')
check('card titles have no trailing colon', host.videoproperties_framerate_card.title() == 'Frame rate')
session.VIDEO.pop('codec')
vp.update(host)
check('codec missing (older session) shows a dash', host.videoproperties_codec_card.value.text() == '—')

print('[2] codec comes from the probe')
real_probe = waveform.ffmpeg_load_metadata
waveform.ffmpeg_load_metadata = lambda path: {
    'format': {'duration': '10'},
    'streams': [{'codec_type': 'video', 'codec_name': 'hevc', 'width': 3840,
                 'height': 2160, 'r_frame_rate': '24000/1001'}]}
try:
    meta = file_io.process_video_file(str(source))
finally:
    waveform.ffmpeg_load_metadata = real_probe
check('process_video_file records the codec', meta.get('codec') == 'hevc', meta.get('codec'))

print('[3] proxy: not generated')
load_video()
tabs.setCurrentIndex(vp._TAB_PROXY)
settle()
check('50% is selected', host.videoproperties_scale_buttons[50].isChecked())
check('status says not generated', status()[0] == 'Not generated yet.', status())
check('hint names the button', 'Create proxy' in status()[1], status()[1])
check('primary says Create proxy', host.videoproperties_primary_button.text() == 'Create proxy')
check('no delete button without a proxy', host.videoproperties_delete_button.isHidden())
check('proxy card shows the target size',
      (host.videoproperties_proxy_resolution_card.frame._w,
       host.videoproperties_proxy_resolution_card.frame._h) == (960, 540))

host.videoproperties_scale_buttons[25].click()
check('picking 25% is saved', session.CONFIG['proxy']['scale'] == 25)
check('...and resizes the proxy card', host.videoproperties_proxy_resolution_card.frame._w == 480)
host.videoproperties_scale_buttons[50].click()

print('[4] encoding')
host.videoproperties_primary_button.click()
check('Create starts one encode at the chosen scale', len(encodes) == 1 and encodes[0][1] == 50)
check('status says creating', status()[0] == 'Creating proxy…', status())
check('progress bar shown in the detail slot',
      host.videoproperties_status_detail_stack.currentIndex() == vp._STATUS_PAGE_PROGRESS)
check('primary turns into Cancel', host.videoproperties_primary_button.text() == 'Cancel')
check('scale is locked while encoding',
      not any(b.isEnabled() for b in host.videoproperties_scale_buttons.values()))
host.videoproperties_primary_button.click()
check('Cancel cancels the encode', encodes[0][3].cancelled)
check('...and does not start another', len(encodes) == 1)
encodes[0][2](False, '', 'cancelled')
check('a cancel is not reported as a failure', status()[0] == 'Not generated yet.', status())

print('[5] failure is remembered for its own scale only')
host.videoproperties_primary_button.click()
encodes[-1][2](False, '', 'ffmpeg failed (rc=1): boom')
check('failure shown', status() == ('Proxy failed', 'ffmpeg failed (rc=1): boom'), status())
host.videoproperties_scale_buttons[75].click()
check('another scale does not show it', status()[0] == 'Not generated yet.', status())
host.videoproperties_scale_buttons[50].click()
check('back on 50% it is still there', status()[0] == 'Proxy failed', status())

print('[6] success, in use, back to the original')
host.videoproperties_primary_button.click()
path = proxy.proxy_path_for(str(source), 50)
Path(path).write_bytes(b'x' * (3 * 1024 * 1024))
encodes[-1][2](True, path, '')
check('fresh proxy is loaded into the player', host.preview_panel_player.loaded[-1] == path)
check('status says in use with the size', status() == ('In use', '3.0 MB'), status())
check('primary offers the original', host.videoproperties_primary_button.text() == 'Use original video')
check('delete button shown', not host.videoproperties_delete_button.isHidden())
check('the old failure is gone', status()[0] != 'Proxy failed')
host.videoproperties_primary_button.click()
check('original reloaded', host.preview_panel_player.loaded[-1] == str(source))
check('status says ready', status() == ('Ready', '3.0 MB'), status())
check('primary offers the proxy', host.videoproperties_primary_button.text() == 'Use proxy')
host.videoproperties_primary_button.click()
check('existing proxy is reused, not re-encoded',
      host.preview_panel_player.loaded[-1] == path and len(encodes) == 3, len(encodes))

print('[7] delete')
host.videoproperties_delete_button.click()
check('file removed', not os.path.exists(path))
check('player went back to the original first', host.preview_panel_player.loaded[-1] == str(source))
check('proxy no longer active', 'proxy_filepath' not in session.VIDEO)
check('back to not generated', status()[0] == 'Not generated yet.', status())
check('delete button hidden again', host.videoproperties_delete_button.isHidden())
check('deleting a missing proxy is not an error', proxy.delete_proxy(str(source), 50) is True)

print('[8] primary button keeps one width across states')
widths = set()
for sc, active in ((50, False), (50, True)):
    Path(path).write_bytes(b'x')
    if active:
        session.VIDEO['proxy_filepath'] = path
    vp.update(host)
    settle()
    widths.add(host.videoproperties_primary_button.width())
os.unlink(path)
session.VIDEO.pop('proxy_filepath', None)
vp.update(host)
settle()
widths.add(host.videoproperties_primary_button.width())
check('same width for Create / Use proxy / Use original', len(widths) == 1, widths)

print('[9] no video')
session.VIDEO = {}
vp.update(host)
check('source tab says no video', not host.videoproperties_no_video_label.isHidden()
      and host.videoproperties_source_cards.isHidden())
check('proxy status says no video', status()[0] == 'No video loaded', status())
check('primary disabled', not host.videoproperties_primary_button.isEnabled())
check('scale disabled', not any(b.isEnabled() for b in host.videoproperties_scale_buttons.values()))
host.videoproperties_primary_button.click()
check('nothing starts', len(encodes) == 3)

print('[10] the controls row folds instead of widening the panel')
load_video()
row = host.videoproperties_controls_row
for width, mode in ((780, row._ONE_LINE), (420, row._TWO_LINES), (320, row._THREE_LINES)):
    host._panel.resize(width, 500)
    settle()
    check(f'{width}px -> {("one", "two", "three")[mode]} line(s)', row._mode == mode,
          (row._mode, row.width(), row._widths()))
    inner = row.parentWidget()
    check(f'{width}px: content fits the panel', inner.width() <= width, (inner.width(), width))
host._panel.resize(780, 500)
settle()
check('widening again goes back to one line', row._mode == row._ONE_LINE)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
