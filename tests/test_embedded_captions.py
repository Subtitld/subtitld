"""Closed captions written into a video: the FFmpeg command each container
takes, and a round trip — captions written into a QuickTime caption track
and into an MXF's ancillary data, then read back out of the file.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import closed_captions, embedded_captions, mcc

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('the command')
RATE = 30000 / 1001
quicktime = embedded_captions.command('in.mp4', 'out.mov', 'caps.mcc', 'MOV', ffmpeg='ffmpeg')
check('QuickTime: the captions a second input, the picture and sound copied',
      (quicktime[quicktime.index('-f') + 1], quicktime[-7:-1]),
      ('mcc', ['-c:v', 'copy', '-c:a', 'copy', '-c:s', 'copy']))
check('every stream mapped, sound only where there is some',
      [quicktime[index + 1] for index, part in enumerate(quicktime) if part == '-map'],
      ['0:v:0', '0:a?', '1:0'])
broadcast = embedded_captions.command('in.mp4', 'out.mxf', 'caps.mcc', 'MXF', ffmpeg='ffmpeg')
check('MXF: the picture re-encoded, the sound PCM at 48 kHz',
      (broadcast[broadcast.index('-c:v') + 1], broadcast[broadcast.index('-profile:v') + 1],
       broadcast[broadcast.index('-c:a') + 1], broadcast[broadcast.index('-ar') + 1]),
      ('dnxhd', 'dnxhr_hq', 'pcm_s16le', '48000'))
check('its other codecs: Sony\'s and Apple\'s',
      (embedded_captions.command('i', 'o', 'c', 'MXF', 'mpeg2')[embedded_captions.command(
           'i', 'o', 'c', 'MXF', 'mpeg2').index('-c:v') + 1],
       embedded_captions.command('i', 'o', 'c', 'MXF', 'prores')[embedded_captions.command(
           'i', 'o', 'c', 'MXF', 'prores').index('-c:v') + 1]),
      ('mpeg2video', 'prores'))
check('a container it does not know is QuickTime', embedded_captions.command('i', 'o', 'c', 'AVI')[-7:-1],
      ['-c:v', 'copy', '-c:a', 'copy', '-c:s', 'copy'])
check('what each container holds',
      (embedded_captions.CONTAINERS['MOV']['captions'], embedded_captions.CONTAINERS['MXF']['captions']),
      ('608', 'both'))
check('the rates captions can be written at', (embedded_captions.rate_of(RATE), embedded_captions.rate_of(25.0),
                                               embedded_captions.rate_of(60000 / 1001), embedded_captions.rate_of(48.0)),
      ('29.97DF', '25', '59.94DF', None))

print('in a video')
ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed')
else:
    folder = Path(tempfile.mkdtemp())
    source = folder / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=30000/1001:d=6',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:r=44100', '-t', '6', '-c:v', 'libx264', '-c:a', 'aac',
                    '-pix_fmt', 'yuv420p', str(source)], check=True)
    segments = [{'start': 1.0, 'end': 3.0, 'text': 'Written into the video'},
                {'start': 3.5, 'end': 5.0, 'text': '<i>And another</i>'}]

    made = embedded_captions.write_video(source, folder / 'out.mov', segments, fps=RATE, duration=6.0,
                                         container='MOV', ffmpeg=ffmpeg)
    read, track = closed_captions.from_video(made, ffprobe=ffprobe)
    check('QuickTime: a caption track of its own, CEA-608',
          (track, [(round(s['start'], 3), s['text']) for s in read]),
          ('cc1', [(1.001, 'Written into the video'), (3.504, '<i>And another</i>')]))

    made = embedded_captions.write_video(source, folder / 'out.mxf', segments, fps=RATE, duration=6.0,
                                         container='MXF', ffmpeg=ffmpeg)
    read, track = closed_captions.from_video(made, ffprobe=ffprobe)
    check('MXF: ancillary data, read back as CEA-708',
          (track, [(round(s['start'], 3), s['text']) for s in read]),
          ('708-1', [(1.001, 'Written into the video'), (3.504, '<i>And another</i>')]))
    check('and CEA-608 beside it, for the decoders that read that',
          [s['text'] for s in closed_captions.from_video(made, track='cc1', ffprobe=ffprobe)[0]],
          ['Written into the video', '<i>And another</i>'])
    check('the picture and sound an MXF takes',
          subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0',
                          str(made)], capture_output=True, text=True).stdout.split(),
          ['dnxhd', 'pcm_s16le', 'smpte_436m_anc'])

    try:
        embedded_captions.write_video(source, folder / 'odd.mov', segments, fps=48.0, duration=6.0, ffmpeg=ffmpeg)
        check('a frame rate captions cannot be written at: an error', None, 'ValueError')
    except ValueError:
        check('a frame rate captions cannot be written at: an error', True, True)
    try:
        embedded_captions.write_video(folder / 'missing.mp4', folder / 'no.mov', segments, fps=RATE, duration=6.0,
                                      ffmpeg=ffmpeg)
        check('a video that is not there: what FFmpeg said', None, 'RuntimeError')
    except RuntimeError:
        check('a video that is not there: what FFmpeg said', True, True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
