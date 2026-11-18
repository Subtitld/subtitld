"""XSUB: decoding hand-built DivX subtitle packets (DXSB and DXSA, both
fields, runs of every length), writing a video out as an AVI with an XSUB
stream for each language through FFmpeg's encoder, reading those streams
back packet by packet as a video's picture track (times, places, colours
against FFmpeg's own decoder), the track listed and read through the OCR
add-on, the export path, and the dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, file_io, picture_ocr, session, soft_subtitles, video_subtitles, vobsub, xsub
from subtitld.modules.addons.provider import OCRProvider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


class FakeOCR(OCRProvider):
    def __init__(self, texts):
        super().__init__()
        self.texts = texts
        self.calls = []

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        self.calls.append((len(image_paths), language))
        QTimer.singleShot(0, lambda: self.finished.emit(self.texts[:len(image_paths)], [0.95] * len(image_paths)))


def packet(start, end, rows, x, y, colours, alphas=None):
    """An XSUB packet as xsubenc.c lays one out, by hand: the even rows'
    field, then the odd rows', coded as a DVD subpicture's runs."""
    rows = numpy.asarray(rows, numpy.uint8)
    height, width = rows.shape
    top, bottom = vobsub._encode_field(rows[0::2]), vobsub._encode_field(rows[1::2])

    def stamp(seconds):
        ms = round(seconds * 1000)
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}'

    out = f'[{stamp(start)}-{stamp(end)}]'.encode('ascii')
    out += struct.pack('<7H', width, height, x, y, x + width - 1, y + height - 1, len(top))
    out += b''.join(bytes(colour) for colour in colours)
    if alphas is not None:
        out += bytes(alphas)
    return out + top + bottom


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

print('packets')
rows = numpy.zeros((7, 300), numpy.uint8)
rows[1:6, 2:298] = 1                 # a run of 296: longer than one code holds
rows[2:5, 10:13] = 2                 # runs of 3,
rows[3, 20:30] = 3                   # 10,
rows[4, 40:100] = 3                  # 60
data = packet(61.5, 63.25, rows, 100, 400, [(0, 0, 0), (255, 255, 255), (16, 16, 16), (255, 255, 0)])
picture = xsub.decode_packet(data, screen=(720, 576))
check('its times, place and size', (picture.start, picture.end, picture.x, picture.y, picture.width, picture.height,
                                    picture.screen_height), (61.5, 63.25, 100, 400, 300, 7, 576))
check('both fields, every run, back as they were', picture.indices, rows.tobytes())
check('colour 0 transparent, the others opaque (DXSB)', picture.colours,
      [(0, 0, 0, 0), (255, 255, 255, 255), (16, 16, 16, 255), (255, 255, 0, 255)])
alpha = xsub.decode_packet(packet(1, 2, rows, 0, 0, [(0, 0, 0)] * 4, [0, 255, 128, 64]), has_alpha=True)
check('DXSA: an opacity for each colour', ([c[3] for c in alpha.colours], alpha.indices == rows.tobytes()),
      ([0, 255, 128, 64], True))
try:
    xsub.decode_packet(b'[nope')
    check('a packet that is none is said to be', None, 'XSubError')
except xsub.XSubError:
    check('a packet that is none is said to be, and left out of a stream\'s',
          [p.start for p in xsub.pictures([b'[nope', data], screen=(720, 576))], [61.5])

FFMPEG, FFPROBE = shutil.which('ffmpeg'), shutil.which('ffprobe')
print('written and read back through FFmpeg')
if not FFMPEG or not FFPROBE:
    print('  (no ffmpeg: skipped)')
else:
    source = folder / 'source.avi'
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x808080:s=640x360:r=25:d=6',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '6', '-c:v', 'mpeg4', '-c:a', 'mp3',
                    str(source)], check=True)
    from subtitld.interface import subtitle_pictures
    segments = [{'start': 1.0, 'end': 2.5, 'text': 'Hello there'}, {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign'}]
    out = folder / 'out.avi'
    xsub.write_video(str(source), str(out), [{'segments': segments, 'language': 'pt-br', 'title': 'Português'},
                                            {'segments': segments[:1], 'language': 'en', 'title': 'English'}],
                     width=640, height=360, ffmpeg=FFMPEG)
    import json
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(out)],
                                      capture_output=True, check=True).stdout)
    tracks = video_subtitles.tracks(probe, language_of=file_io._known_language)
    check('an AVI: the picture and sound copied, an XSUB stream for each language',
          ([s['codec_name'] for s in probe['streams']], [(t['codec'], t['kind']) for t in tracks]),
          (['mpeg4', 'mp3', 'xsub', 'xsub'], [('xsub', 'pictures'), ('xsub', 'pictures')]))
    check('readable as a picture track', 'xsub' in video_subtitles.READABLE_PICTURE_CODECS, True)
    drawn = subtitle_pictures.draw(segments, (640, 360), str(folder / 'drawn'))
    pictures = video_subtitles.extract_pictures(str(out), tracks[0]['index'], 'xsub', ffprobe=FFPROBE)
    # FFmpeg's encoder takes the end from its dvdsub decoder, in ticks of 1024/90000 s.
    check('read back at their times (the end within a DVD tick), on the video\'s frame',
          [(p.start, abs(p.end - d['end']) <= vobsub.SPU_TICK, p.screen_width) for p, d in zip(pictures, drawn)],
          [(1.0, True, 640), (3.0, True, 640)])
    check('the sign at the top, the line at the bottom', [p.y < 120 for p in pictures], [False, True])
    # Each laid on grey by FFmpeg's own decoder and by Subtitld's reading.
    for p in pictures:
        moment = (p.start + p.end) / 2
        result = subprocess.run([FFMPEG, '-v', 'error', '-i', str(out), '-filter_complex', '[0:v][0:s:0]overlay',
                                 '-ss', f'{moment:.3f}', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                capture_output=True)
        frame = numpy.frombuffer(result.stdout, numpy.uint8).reshape(360, 640, 3).astype(numpy.float64)
        ours = numpy.array([p.colours[i][:3] if p.colours[i][3] else (128, 128, 128) for i in p.indices],
                           numpy.float64).reshape(p.height, p.width, 3)
        region = frame[p.y:p.y + p.height, p.x:p.x + p.width]
        error = float(numpy.abs(region - ours).mean())
        print(f'    at {moment}: mean difference from FFmpeg\'s decoding {error:.2f}')
        check(f'decoded as FFmpeg decodes it, at {moment}', error < 6, True)

    print('opened and read')
    session.VIDEO = file_io.process_video_file(str(out))
    # An AVI keeps an XSUB stream's language in its name, as DivX does.
    check('the video lists both tracks, their languages from their names ("Subtitle - pt-xx;02")',
          [(t['codec'], t['language'], t['title']) for t in session.VIDEO['subtitle_tracks']],
          [('xsub', 'pt-pt', 'Subtitle - pt-xx;02'), ('xsub', 'en-us', 'Subtitle - en-xx;02')])
    engine = FakeOCR(['Hello there', 'A sign'])
    job = picture_ocr.OCRJob.for_track(str(out), tracks[0]['index'], 'xsub', 'pt-br', engine)
    loop, heard = QEventLoop(), {}
    job.finished.connect(lambda found, info: (heard.update(found=found), loop.quit()))
    job.failed.connect(lambda message: (heard.update(failed=message), loop.quit()))
    job.start()
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    check('read through the OCR add-on', [(s['start'], s['text']) for s in heard.get('found', [])],
          [(1.0, 'Hello there'), (3.0, '{\\an8}A sign')])

    print('the export path')
    from subtitld.modules import bounce
    session.VIDEO = {'filepath': str(source), 'framerate': 25.0, 'duration': 6.0, 'width': 640, 'height': 360}
    session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Um', 'translations': {'en-us': 'One'}}],
                        'language': 'pt-br'}
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.avi'), 'AVI XSUB', {'translations': True, 'forced': True})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', str(folder / 'wired.avi')],
                                      capture_output=True, check=True).stdout)
    check('what the export runs writes the file, a stream for each language',
          (said, [s['codec_name'] for s in probe['streams'] if s['codec_type'] == 'subtitle']),
          ([(True, '')], ['xsub', 'xsub']))

print('the dialog')
from subtitld.interface.export_dialog import _VideoPanel
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.avi'}
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'}
panel = _VideoPanel()
panel.video_tabwidget.setCurrentWidget(panel.tracks_panel)
panel.tracks_format_combo.combobox.setCurrentIndex(panel.tracks_format_combo.combobox.findData('AVI XSUB'))
panel._refresh_tracks()
check('an AVI with XSUB: no codec, nor forced, to choose; what it does said',
      (panel.get_config()['format'], panel.tracks_codec_combo.isHidden(), panel.tracks_forced.isHidden(),
       soft_subtitles.is_soft('AVI XSUB'), 'XSUB' in panel.tracks_note.text()), ('AVI XSUB', True, True, True, True))
check('its file name', session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS['AVI XSUB']['extensions'], ['avi'])

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
