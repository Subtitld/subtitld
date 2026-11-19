"""The export dialog's preview of a video export: a few seconds of the video
around the subtitle under the cursor (else the next) written as the export
writes them, in the background, and a frame of that file with its subtitles
read back out of it and drawn on it; the session left as it was.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import copy, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session, video_preview
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:70]!r}' + ('' if ok else f'   (want {str(want)[:70]!r})'))
    if not ok:
        fails.append(name)


print('the moment and the clip')
cue = {'start': 4.0, 'end': 6.0, 'text': 'a'}
check('the cursor, when it is on the subtitle', video_preview.moment(cue, 4.5), 4.5)
check('else the subtitle\'s middle', video_preview.moment(cue, 1.0), 5.0)
check('the clip\'s subtitles, counted from its start, cut to it',
      [(round(s['start'], 2), round(s['end'], 2)) for s in video_preview.shifted(
          [{'start': 0, 'end': 1, 'text': 'a'}, {'start': 2.5, 'end': 4.2, 'text': 'b'},
           {'start': 4.5, 'end': 9, 'text': 'c'}], 3.0, 3.0)],
      [(0.0, 1.2), (1.5, 3.0)])

ffmpeg = shutil.which('ffmpeg')
if not ffmpeg:
    print('  [SKIP] no FFmpeg to write a video with')
else:
    folder = Path(tempfile.mkdtemp())
    video = folder / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=640x360:r=30000/1001:d=8',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
    session.VIDEO = {'filepath': str(video), 'framerate': 30000 / 1001, 'duration': 8.0, 'width': 640, 'height': 360}
    segments = [{'start': 1.0, 'end': 2.5, 'text': 'First'}, {'start': 4.0, 'end': 6.0, 'text': 'Second line'}]

    print('the still')
    for container, options in (('MOV', {}), ('MKV', {'subtitle_codec': 'srt'})):
        out = str(folder / f'{container}.png')
        video_preview.still(container, options, segments, segments[1], 3.0, out, language='en-us')
        check(f'{container}: a frame', os.path.getsize(out) > 0, True)

    print('the dialog')
    from subtitld.interface import export_dialog
    session.SUBTITLE = Subtitle({'segments': segments, 'language': 'en-us', 'position': 4.4})
    session.CONFIG.pop('export_video', None)
    session.CONFIG.setdefault('selected_language', 'en')
    before = copy.deepcopy(session.SUBTITLE)
    dialog = export_dialog.ExportDialog(initial_category='video')
    dialog.show()
    check('drawing: a note while it is', dialog._still is not None, True)

    def settle():
        started = time.time()
        while dialog._still is not None or dialog._still_again or dialog._preview_timer.isActive():
            app.processEvents()
            time.sleep(0.02)
            if time.time() - started > 120:
                break
        app.processEvents()

    settle()
    preview = dialog.preview_panel
    check('the frame alone: no part of it under it, no well',
          (preview.picture.isVisible(), preview.crop.isVisible(), preview.file.isVisible()), (True, False, False))
    dialog.video_panel.choose('MKV')
    dialog.refresh_preview()
    dialog.video_panel.choose('MP4')                            # asked for while MKV is drawn: drawn next
    dialog.refresh_preview()
    check('one at a time, the last asked for after it', (dialog._still is not None, dialog._still_again), (True, True))
    settle()
    check('and then shown', (dialog._still, preview.picture.isVisible()), (None, True))
    session.VIDEO = {}
    dialog.refresh_preview()
    check('no video: nothing', (preview.picture.isVisible(), preview.file.toPlainText()), (False, ''))
    check('the session as it was', session.SUBTITLE, before)
    folder_used = dialog._still_folder
    dialog.done(0)
    check('the stills\' folder gone with the dialog', folder_used is None or not os.path.exists(folder_used), True)
    shutil.rmtree(folder, ignore_errors=True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
