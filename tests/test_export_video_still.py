"""The export dialog's still of a video export (modules.video_preview):
taken at a subtitle or a title under the cursor, the next, the last before
it, or the cursor itself when there is none; a moving bar while it is drawn,
the last still dimmed meanwhile; and a cue whose theme is animated drawn in
the still at the moment of its own animation, not from its start again.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import os, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, export_options, session, video_preview
from subtitld.modules.segment import Segment, Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def spin(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


folder = Path(tempfile.mkdtemp())
video = folder / 'in.mp4'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=gray:size=640x360:rate=25:duration=10', '-pix_fmt', 'yuv420p', str(video)], check=True)
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.VIDEO = {'filepath': str(video), 'framerate': 25, 'duration': 10, 'width': 640, 'height': 360}
title = Segment({'kind': 'title', 'start': 2, 'end': 6, 'text': 'Maria Souza', 'title_theme': 'subtitld.lower_third',
                 'title_slots': {'role': 'Field reporter'}})
early = Segment({'start': 0.5, 'end': 1.5, 'text': 'Early'})
late = Segment({'start': 7, 'end': 8, 'text': 'Late'})
mark = Segment({'kind': 'mark', 'start': 9, 'end': 9, 'text': 'Chapter'})

print('what the still is taken at')
from subtitld.interface.export_dialog import ExportDialog


def cue_at(segments, position):
    session.SUBTITLE = Subtitle(segments=segments)
    session.SUBTITLE.position = position
    dialog = ExportDialog(initial_category='subtitles')
    cue = dialog._video_cue()
    dialog.close()
    return cue


check('a title under the cursor', cue_at([early, title, late], 3)['text'], 'Maria Souza')
check('else the next', cue_at([early, title, late], 6.5)['text'], 'Late')
check('else the last before', cue_at([early, title, mark], 8.5)['text'], 'Maria Souza')
check('not a mark', cue_at([mark], 9.5)['text'], '')
check('none: the cursor itself', (cue_at([], 4)['start'], cue_at([], 4)['text']), (4.0, ''))

print('times in the clip')
clipped = video_preview.shifted([title], 3.8, 0.7)
whole = video_preview.shifted([title], 3.8, 0.7, clip=False)
check('cut to the clip', (clipped[0]['start'], clipped[0]['end']), (0.0, 0.7))
check('whole, for a burn-in', (round(whole[0]['start'], 3), round(whole[0]['end'], 3)), (-1.8, 2.2))
check('prepare keeps a start before 0 when asked', export_options.prepare(whole, clamp=False)[0]['start'], -1.8)
check('and clamps it otherwise', export_options.prepare(whole)[0]['start'], 0.0)

print('a still of a burned-in title, at its middle')
session.SUBTITLE = Subtitle(segments=[title])
output = folder / 'still.png'
video_preview.still('BURN MP4', {}, [title], dict(title), 0.0, str(output))
image = QImage(str(output))
dark = sum(1 for y in range(200, 340, 2) for x in range(0, 320, 2) if image.pixelColor(x, y).lightness() < 60)
check('the Lower third settled in it, not its first frame', dark > 500, True)

print('the dialog while it draws')
session.SUBTITLE = Subtitle(segments=[title])
session.SUBTITLE.position = 0
dialog = ExportDialog(initial_category='video')
dialog.show()
spin(0.2)
panel = dialog.preview_panel
check('a moving bar and what it does', (not panel.busy.isHidden(), panel.busy.maximum(), bool(panel.busy_note.text())),
      (True, 0, True))
deadline = time.monotonic() + 60
while dialog._still is not None and time.monotonic() < deadline:
    spin(0.1)
spin(0.2)
check('then the still, the bar gone', (not panel.picture.isHidden(), panel.busy.isHidden()), (True, True))
dialog._preview_still()
check('asked again: the still kept, dimmed', (not panel.picture.isHidden(), panel.picture.graphicsEffect() is not None),
      (True, True))
deadline = time.monotonic() + 60
while dialog._still is not None and time.monotonic() < deadline:
    spin(0.1)
spin(0.2)
check('the new one undimmed', panel.picture.graphicsEffect(), None)
session.SUBTITLE = Subtitle(segments=[])
dialog.refresh_preview()
check('no cues: still a picture on its way (the video alone)', not panel.busy.isHidden(), True)
deadline = time.monotonic() + 60
while dialog._still is not None and time.monotonic() < deadline:
    spin(0.1)
spin(0.2)
check('drawn', not panel.picture.isHidden(), True)
dialog.close()

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
