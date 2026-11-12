"""Closed captions in a video's own frames, as ATSC carries them (A/53 Part
4): the user data ('GA94', type 3, the count, the entries), the SEI NAL unit
of H.264 and HEVC (ITU-T T.35, USA, ATSC's provider code) with its
emulation prevention, MPEG-2's user data; a picture's data with its
captions put before its first slice and those it had taken out; and, with
FFmpeg, H.264 (with B-frames), HEVC and MPEG-2 video written out as MP4 and
as a transport stream with captions in every frame, the picture and sound
copied and in step, read back by the closed captions engine (FFmpeg's
decoders, through lavfi's subcc), a video captioned again keeping the new
captions alone; the export path and the dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import a53, closed_captions, embedded_captions, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('the caption data')
triplets = [(0xFC, 0x94, 0x2C), (0xFD, 0x80, 0x80), (0xFF, 0x02, 0x21), (0xFA, 0x00, 0x00)]
data = a53.user_data(triplets)
check('GA94, user_data_type_code 3, the count with process_cc_data set, em_data, the entries, the marker',
      data, b'GA94\x03\xc4\xff' + bytes.fromhex('fc942cfd8080ff0221fa0000') + b'\xff')
sei = a53.caption_unit('h264', triplets)
check('H.264: an SEI NAL unit, registered user data (type 4) of the USA, ATSC\'s provider code',
      (sei[:6], sei[7:10]), (b'\x00\x00\x00\x01\x06\x04', b'\xb5\x00\x31'))
check('its size, the message, and the RBSP\'s stop bit', (sei[6], sei[10:-1], sei[-1]),
      (len(b'\xb5\x00\x31' + data), data, 0x80))
check('HEVC: a prefix SEI NAL unit (type 39)', a53.caption_unit('hevc', triplets)[4:7], b'\x4e\x01\x04')
check('MPEG-2: the picture\'s user data', a53.caption_unit('mpeg2video', triplets), b'\x00\x00\x01\xb2' + data)
check('emulation prevention: a 03h after two zeros before a byte of 3 or less',
      a53._escaped(b'\x00\x00\x01\x00\x00\x00\x04'), b'\x00\x00\x03\x01\x00\x00\x03\x00\x04')
check('so an SEI never holds a start code', b'\x00\x00\x01' in a53.caption_unit(
    'h264', [(0xFE, 0x00, 0x00), (0xFE, 0x01, 0x00), (0xFE, 0x00, 0x00)])[4:], False)

print('a picture with its captions')
aud, sps, pps = b'\x00\x00\x00\x01\x09\xf0', b'\x00\x00\x00\x01\x67\x64\x00\x1e', b'\x00\x00\x00\x01\x68\xee\x3c\x80'
timing = b'\x00\x00\x01\x06\x01\x01\x00\x80'
old = a53.caption_unit('h264', [(0xFC, 0x80, 0x80)])
idr, slice_ = b'\x00\x00\x01\x65\x88\x84\x00', b'\x00\x00\x01\x41\x9a\x00'
unit = a53.caption_unit('h264', triplets)
check('H.264: before the first slice, after the parameter sets and the SEI there was',
      a53.with_captions('h264', aud + sps + pps + timing + idr, triplets), aud + sps + pps + timing + unit + idr)
check('the captions it had taken out, once whatever the slices',
      a53.with_captions('h264', aud + old + slice_ + slice_, triplets), aud + unit + slice_ + slice_)
check('data with no picture in it left as it is', a53.with_captions('h264', aud + sps, triplets), aud + sps)
vps, hevc_slice = b'\x00\x00\x00\x01\x40\x01\x0c', b'\x00\x00\x01\x26\x01\xaf'
check('HEVC: before its first slice', a53.with_captions('hevc', vps + hevc_slice, triplets),
      vps + a53.caption_unit('hevc', triplets) + hevc_slice)
header = b'\x00\x00\x01\x00\x00\x0f\xff\xf8' + b'\x00\x00\x01\xb5\x8f\xff'
mpeg2_slice = b'\x00\x00\x01\x01\x12\x34'
check('MPEG-2: after the picture\'s header and extension, its old captions out',
      a53.with_captions('mpeg2video', header + b'\x00\x00\x01\xb2GA94\x03\xc1\xff\xfc\x80\x80\xff' + mpeg2_slice,
                        triplets), header + a53.caption_unit('mpeg2video', triplets) + mpeg2_slice)
check('the kinds of file', (sorted(a53.CONTAINERS), sorted(a53.CODECS)),
      (['MP4 A53', 'TS A53'], ['h264', 'hevc', 'mpeg2video']))
check('as the export lists them', [session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS[key]['extensions']
                                   for key in ('MP4 A53', 'TS A53')], [['mp4'], ['ts']])

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed: no video')
else:
    print('in a video')
    folder = Path(tempfile.mkdtemp())
    RATE = 30000 / 1001
    segments = [{'start': 1.0, 'end': 2.5, 'text': 'Into the frames'}, {'start': 3.0, 'end': 4.0, 'text': '<i>And on</i>'}]

    def make(name, *codec):
        path = folder / name
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=30000/1001:duration=6',
                        '-f', 'lavfi', '-i', 'sine=f=440:r=48000', '-t', '6', *codec, str(path)], check=True)
        return path

    def streams(path):
        out = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_name,start_time', '-of', 'csv=p=0',
                              str(path)], capture_output=True, text=True).stdout.split()
        return list(dict.fromkeys(out))            # a transport stream lists them under its programme too

    def carried(path):
        """How many frames carry caption data, as FFmpeg's decoder finds it."""
        return len(subprocess.run([ffprobe, '-v', 'error', '-f', 'lavfi', '-i', f'movie={path}[out0+subcc]',
                                   '-select_streams', 's', '-show_entries', 'packet=pts', '-of', 'csv=p=0'],
                                  capture_output=True, text=True).stdout.split())

    def clean(path):
        return subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'null', '-'],
                              capture_output=True, text=True).stderr.strip()

    want = [(1.0, 2.5, 'Into the frames'), (3.0, 4.0, '<i>And on</i>')]
    sources = {'H.264 with B-frames': make('h264.mp4', '-c:v', 'libx264', '-bf', '3', '-c:a', 'aac'),
               'HEVC': make('hevc.mp4', '-c:v', 'libx265', '-x265-params', 'log-level=error', '-c:a', 'aac'),
               'MPEG-2': make('mpeg2.ts', '-c:v', 'mpeg2video', '-bf', '2', '-c:a', 'mp2')}
    for label, source in sources.items():
        for container in ('MP4 A53', 'TS A53'):
            output = folder / f'{source.stem}_{container[:2].lower()}.{a53.CONTAINERS[container]["extension"]}'
            embedded_captions.write_video(source, output, segments, fps=RATE, duration=6.0, container=container,
                                          ffmpeg=ffmpeg, ffprobe=ffprobe)
            found, track = closed_captions.from_video(output, ffprobe=ffprobe)
            # The captions sit on the frames they were written for, counted
            # from when the picture starts after the file does (a transport
            # stream from an MP4 starts its AAC earlier, by the priming an
            # MP4's edit list hides, as any copy into one does).
            offset = a53.probe(output, ffprobe)[2]
            check(f'{label} as {container[:2]}: the captions read back, on their frames, CEA-708 first',
                  ([(s['start'], s['end'], s['text']) for s in found], track),
                  ([tuple(round(round((offset + round(t * RATE) / RATE) * RATE) / RATE, 3) for t in (a, b)) + (text,)
                    for a, b, text in want], '708-1'))
            if output.suffix == source.suffix:
                check(f'{label}: the picture copied, the sound with it, in step', streams(output), streams(source))
        check(f'{label}: every frame carries caption data', carried(output), 180)
        check(f'{label}: the video decodes without a complaint', clean(output), '')
    again = folder / 'again.mp4'
    embedded_captions.write_video(folder / 'h264_mp.mp4', again, [{'start': 2.0, 'end': 3.0, 'text': 'Only this'}],
                                  fps=RATE, duration=6.0, container='MP4 A53', ffmpeg=ffmpeg, ffprobe=ffprobe)
    check('captioned again: the new captions alone', [s['text'] for s in closed_captions.from_video(again)[0]],
          ['Only this'])
    other = make('mpeg4.mp4', '-c:v', 'mpeg4', '-c:a', 'aac')
    try:
        embedded_captions.write_video(other, folder / 'no.mp4', segments, fps=RATE, duration=6.0,
                                      container='MP4 A53', ffmpeg=ffmpeg, ffprobe=ffprobe)
        check('a video whose codec takes none: an error', None, 'ValueError')
    except ValueError as error:
        check('a video whose codec takes none: an error that says which do', 'H.264' in str(error), True)

    print('the export path')
    from subtitld.modules import bounce
    session.VIDEO = {'filepath': str(sources['H.264 with B-frames']), 'framerate': RATE, 'duration': 6.0}
    session.SUBTITLE = {'segments': segments, 'language': 'en-us'}
    session.FFMPEG_EXECUTABLE, session.FFPROBE_EXECUTABLE = ffmpeg, ffprobe
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.mp4'), 'MP4 A53', {'caption_mode': 'pop', 'caption_channel': 1})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    check('what the export runs writes the file with the captions in its frames',
          (said, [s['text'] for s in closed_captions.from_video(folder / 'wired.mp4')[0]]),
          ([(True, '')], ['Into the frames', '<i>And on</i>']))

print('the dialog')
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 30000 / 1001, 'duration': 10.0, 'filepath': 'video.mp4'}
from subtitld.interface.export_dialog import _VideoPanel
from subtitld.interface.translation import _
panel = _VideoPanel()
combo = panel.captions_format_combo.combobox
check('offered beside QuickTime and MXF', [combo.itemData(index) for index in range(combo.count())],
      ['MOV', 'MXF', 'MP4 A53', 'TS A53'])
combo.setCurrentIndex(2)
panel._refresh_captions()
config = panel.get_config()
check('the picture copied: no codec, no teletext, a note of its own',
      (config['format'], panel.captions_codec_combo.isHidden(), panel.captions_teletext.isHidden(),
       panel.captions_note.text() == _('export_dialog.video_captions_note_a53')), ('MP4 A53', True, True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
