"""The subtitles in the picture: burned into the video, or alone on a clear
video (burned_video), drawn in a subtitle style as the preview draws them;
the export's thread writing both, and the dialog's choices for them.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
app = QApplication([])
from subtitld.modules import burned_video, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:70]!r}' + ('' if ok else f'   (want {str(want)[:70]!r})'))
    if not ok:
        fails.append(name)


print('the picture track')
folder = Path(tempfile.mkdtemp())
drawn = folder / 'drawn.png'
picture = QImage(40, 10, QImage.Format_ARGB32)
picture.fill(0xFFFFFFFF)
picture.save(str(drawn))
listed = burned_video.concat_list([{'start': 1.0, 'end': 2.0, 'path': str(drawn), 'x': 5, 'y': 30},
                                   {'start': 1.5, 'end': 3.0, 'path': str(drawn), 'x': 5, 'y': 30}],
                                  (64, 48), 5.0, str(folder))
lines = Path(listed).read_text().splitlines()
durations = [round(float(line.split()[1]), 3) for line in lines if line.startswith('duration')]
check('nothing, each subtitle for its time (one after another when they overlap), nothing to the end',
      durations, [1.0, 1.0, 1.0, 2.0])
check('the last file named again, as the concat demuxer asks', lines[-1], lines[-3])
frame = QImage(str(folder / 'frame00001.png'))
check('each drawing at its place on a clear frame', (frame.size().toTuple(), frame.pixelColor(10, 35).alpha(),
                                                    frame.pixelColor(10, 10).alpha()), ((64, 48), 255, 0))

print('the subtitles drawn')
from subtitld.interface import subtitle_pictures, subtitle_renderer
if not subtitle_renderer.available():
    print('  [SKIP] no QtWebEngine to draw with')
else:
    same = [{'start': 0.0, 'end': 1.0, 'text': 'Yes.'}, {'start': 2.0, 'end': 3.0, 'text': 'Yes.'}]
    pictures = subtitle_pictures.draw(same, (320, 180), str(folder / 'same'))
    check('the same words twice: two pictures, from one layer', [p['start'] for p in pictures], [0.0, 2.0])

ffmpeg = shutil.which('ffmpeg')
if not ffmpeg or not subtitle_renderer.available():
    print('  [SKIP] no FFmpeg, or nothing to draw with')
else:
    def run(arguments):
        return subprocess.run([ffmpeg, '-v', 'error', '-y'] + arguments, check=True, capture_output=True)

    def frame_at(path, seconds, alpha=False, webm=False):
        out = str(folder / 'frame.png')
        run((['-c:v', 'libvpx-vp9'] if webm else []) + ['-i', str(path), '-ss', f'{seconds}', '-frames:v', '1']
            + (['-pix_fmt', 'rgba'] if alpha else []) + ['-update', '1', out])
        return QImage(out)

    video = folder / 'source.mp4'
    run(['-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=25:d=4', '-f', 'lavfi', '-i', 'sine=d=4',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
    segments = [{'start': 1.0, 'end': 2.0, 'text': 'Burned'}]

    def lit(image):
        """How many pixels of the frame's lower half are bright."""
        return sum(1 for y in range(image.height() // 2, image.height()) for x in range(image.width())
                   if image.pixelColor(x, y).lightness() > 128)

    def codecs_of(path):
        return subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0',
                               str(path)], capture_output=True, text=True).stdout.split()

    print('burned in: every format and codec')
    for container, holder in burned_video.CONTAINERS.items():
        if holder['alpha']:
            continue
        for codec in holder['codecs']:
            out = folder / f'burned_{codec}.{holder["extension"]}'
            burned_video.write_video(str(out), container, segments, video=str(video), size=(640, 360), duration=4.0,
                                     codec=codec)
            check(f'{container}, {codec}: the subtitle in the picture while it is on, not before; the sound kept',
                  (lit(frame_at(out, 1.5)) > 20, lit(frame_at(out, 0.5)), len(set(codecs_of(out)))), (True, 0, 2))
    check('the codec asked for', codecs_of(folder / 'burned_hevc.mp4')[0], 'hevc')
    try:
        burned_video.write_video(str(folder / 'none.mp4'), 'BURN MP4', segments)
        check('burned into nothing: an error', None, 'ValueError')
    except ValueError:
        check('burned into nothing: an error', True, True)

    print('alone: every format and codec')
    for container, holder in burned_video.CONTAINERS.items():
        if not holder['alpha']:
            continue
        for codec in holder['codecs']:
            out = folder / f'alone_{codec}.{holder["extension"]}'
            burned_video.write_video(str(out), container, segments, size=(320, 180), fps=25, duration=3.0, codec=codec)
            vp9 = codec == 'vp9'
            on, off = frame_at(out, 1.5, True, vp9), frame_at(out, 0.5, True, vp9)
            opaque = sum(1 for y in range(0, 180, 2) for x in range(0, 320, 2) if on.pixelColor(x, y).alpha() > 200)
            check(f'{container}, {codec}: clear but for the subtitle, and clear before it',
                  (0 < opaque < 320 * 180 // 8, off.pixelColor(160, 160).alpha()), (True, 0))

    print('the export')
    from subtitld.modules import bounce
    session.VIDEO = {}
    session.SUBTITLE = {'segments': segments, 'language': 'en-us'}
    out = folder / 'thread.webm'
    worker = bounce.CaptionsThread(str(out), 'ALPHA WEBM', {'width': 320, 'height': 180, 'fps': 25})
    result = []
    worker.captions_finished.connect(lambda path, ok, error: result.append((ok, error)))
    worker.run()
    check('alone, written with no video loaded, as long as the subtitles', (result, round(float(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(out)],
        capture_output=True, text=True).stdout), 1)), ([(True, '')], 2.0))
    session.VIDEO = {'filepath': str(video), 'framerate': 25.0, 'duration': 4.0, 'width': 640, 'height': 360}
    out = folder / 'thread.mov'
    worker = bounce.CaptionsThread(str(out), 'BURN MOV', {'style': 'subtitld.classic'})
    result = []
    worker.captions_finished.connect(lambda path, ok, error: result.append((ok, error)))
    worker.run()
    check('burned into the video loaded, ProRes', (result, lit(frame_at(out, 1.5)) > 20), ([(True, '')], True))

    print('the dialog')
    from subtitld.interface import export_dialog
    session.CONFIG.pop('export_video', None)
    panel = export_dialog._VideoPanel()
    ways, codecs = panel.way_combo.combobox, panel.codec_combo.combobox

    def listed(combobox):
        return [combobox.itemData(index) for index in range(combobox.count())]

    panel.choose('MOV')
    check('QuickTime: every way', listed(ways), ['cc', 'track', 'burn', 'alpha'])
    panel.choose('MKV')
    check('Matroska: no captions', listed(ways), ['track', 'burn', 'alpha'])
    panel.choose('MXF')
    check('MXF: captions alone, nothing to ask', (listed(ways), panel.way_combo.isHidden()), (['cc'], True))
    panel.choose('BURN MP4')
    check('MP4 burned in: H.264 or HEVC, a style and a quality, no size',
          (listed(codecs), panel.video_pages.currentWidget() is panel.burn_panel, panel.quality_row.isHidden(),
           panel.alpha_options.isHidden(), panel.export_choices()),
          (['h264', 'hevc'], True, False, True,
           ('BURN MP4', {'video_codec': 'h264', 'style': 'subtitld.classic', 'quality': 'balanced'})))
    panel.choose('BURN MOV')
    check('QuickTime burned in: ProRes first, no quality for it', (listed(codecs), panel.quality_row.isHidden()),
          (['prores', 'h264'], True))
    codecs.setCurrentIndex(codecs.findData('h264'))
    panel._codec_chosen()
    check('H.264 in QuickTime: a quality', panel.quality_row.isHidden(), False)
    panel.choose('BURN TS')
    check('one codec: not asked', (listed(codecs), panel.codec_combo.isHidden()), (['h264'], True))
    panel.choose('ALPHA MKV')
    check('Matroska alone: FFV1 first, no quality for it', (listed(codecs), panel.quality_row.isHidden()),
          (['ffv1', 'vp9', 'prores4444', 'png'], True))
    def back_to(panel, video_file):
        combo = panel.format_combo.combobox
        combo.setCurrentIndex(combo.findData(video_file))
        panel._refresh_format()

    back_to(panel, 'MOV')
    check('QuickTime again: the way and codec chosen for it kept', (panel._container(), codecs.currentData()),
          ('BURN MOV', 'h264'))
    panel.choose('ALPHA WEBM')
    check('alone: the video\'s size and rate first', panel.export_choices(),
          ('ALPHA WEBM', {'video_codec': 'vp9', 'style': 'subtitld.classic', 'quality': 'balanced', 'width': 640,
                          'height': 360, 'fps': 25.0}))
    panel.alpha_size_combo.combobox.setCurrentIndex(panel.alpha_size_combo.combobox.findData('1920x1080'))
    config = panel.get_config()
    session.VIDEO = {}
    again = export_dialog._VideoPanel()
    check('remembered, with no video: the size chosen', (again.export_choices()[0], again.export_choices()[1]['width']),
          ('ALPHA WEBM', 1920))
    back_to(again, 'MOV')
    check('and the way and codec chosen for each format', (again._container(), again.codec_combo.combobox.currentData()),
          ('BURN MOV', 'h264'))

    print('its preview')
    session.SUBTITLE = {'segments': segments, 'language': 'en-us', 'position': 1.2}
    dialog = export_dialog.ExportDialog(initial_category='video')
    dialog.show()
    started = time.time()
    while dialog._still is not None or dialog._still_again or dialog._preview_timer.isActive():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - started > 120:
            break
    app.processEvents()
    check('alone, with no video: a frame shown', dialog.preview_panel.picture.isVisible(), True)
    dialog.done(0)

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
