"""OP-47 (Free TV Australia, SMPTE RDD 8): teletext subtitles in each frame's
ancillary data. A subtitling distribution packet as the specification lays
it out (identifier, length, format code, five descriptors, the 45-byte VBI
packets, footer, sequence counter, a checksum that makes the sum 0), read
back, and inside a multipacket; a page's transmissions spread over the
frames, five packets a frame; an MCC file with OP-47 beside its captions,
and one with teletext alone, read back; FFmpeg's MacCaption reader handing
the packets on as SMPTE ST 436 ancillary data; an MXF written with its
captions and an OP-47 page beside them, and both read back out of it; the
export path and the dialog's choice.

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
from subtitld.modules import closed_captions, embedded_captions, mcc, session
from subtitld.modules import teletext as tt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('a subtitling distribution packet')
fields = tt.page_fields('Hello OP-47', '888', 'en')
packet = tt.op47_packet(fields, counter=0x1234)
words = packet[3:-1]
check('DID 43h, SDID 02h, the data count its words', (packet[0], packet[1], packet[2], len(words)),
      (0x43, 0x02, len(words), 9 + 45 * len(fields) + 4))
check('identifier 51h 15h, a length from the identifier to the checksum, format code 02h (WST subtitles)',
      (words[:2], words[2], words[3]), (b'\x51\x15', len(words), 0x02))
check('a descriptor for each packet (field 1, the line), the others 0',
      list(words[4:9]), [0xE7, 0xE8] + [0] * 3)
check('each packet: run-in 55h 55h, framing code 27h, then its address in Hamming 8/4',
      (words[9:12], tt.ham84(words[12]) & 7, words[9 + 45:12 + 45]), (b'\x55\x55\x27', 0, b'\x55\x55\x27'))
check('the footer: 74h and the sequence counter, most significant first', words[-4:-1], b'\x74\x12\x34')
check('the checksum makes the sum of the words 0', sum(words) & 0xFF, 0)
check('read back: the same teletext packets', tt.op47_fields(packet), fields)
check('five packets at most', len(tt.op47_fields(tt.op47_packet(fields * 4))), 5)
check('none: an empty one, its descriptors all 0', (tt.op47_fields(tt.op47_packet([])), tt.op47_packet([])[3:12]),
      ([], b'\x51\x15\x0d\x02\x00\x00\x00\x00\x00'))
inner = packet[:-1]
multipacket = bytes([0x43, 0x03, 1 + 1 + len(inner)]) + b'\x01' + bytes([0x29]) + inner + b'\x00'
check('inside a multipacket (SDID 03h): its priority word, then each packet\'s line and the packet',
      tt.op47_fields(multipacket), fields)
check('another packet is not teletext', (tt.op47_fields(b'\x61\x01\x03abc\x00'), tt.op47_fields(b'')), ([], []))

print('a page over the frames')
tracks = [{'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Line one\nLine two'},
                        {'start': 2.0, 'end': 3.0, 'text': 'Straight after'}], 'language': 'en'}]
frames = tt.op47_frames(tracks, 25)
check('erased at 0, then each transmission from its frame, five packets a frame',
      sorted((frame, len(found)) for frame, found in frames.items()), [(0, 1), (25, 3), (50, 2), (75, 1)])
check('read back from the frames', [(s['start'], s['end'], s['text']) for s in tt.op47_decode(
    sorted(frames.items()), 25)[0]], [(1.0, 2.0, 'Line one\nLine two'), (2.0, 3.0, 'Straight after')])
crowded = tt.op47_frames([{'segments': [{'start': 1.0, 'end': 2.0, 'text': 'A\nB\nC\nD\nE\nF\nG\nH'}]}], 25)
check('a transmission of more than five packets runs on into the frames after', sorted(crowded)[1:3], [25, 26])
russian = tt.op47_frames([{'segments': [{'start': 0.4, 'end': 1.2, 'text': 'Привет'}], 'language': 'ru'}], 25)
check('in its language\'s set, designated', tt.op47_decode(sorted(russian.items()), 25)[0][0]['text'], 'Привет')
check('nothing: no subtitles', tt.op47_decode([], 25), ([], None))

print('MacCaption')
segments = [{'start': 1.0, 'end': 2.0, 'text': 'The captions'}, {'start': 3.0, 'end': 4.0, 'text': 'And more'}]
teletext_tracks = [{'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Die Untertitel'},
                                 {'start': 3.0, 'end': 4.0, 'text': 'Grüße'}], 'language': 'de'}]
text = mcc.write(segments, rate='25', teletext_tracks=teletext_tracks)
lines = [line for line in text.split('\n') if line[:2].isdigit()]
check('a line of its own for the frames with teletext, at the same timecode',
      [line.split('\t')[0] for line in lines if line.split('\t')[1].startswith('4302')],
      ['00:00:00:00', '00:00:01:00', '00:00:02:00', '00:00:03:00', '00:00:04:00'])
read, settings = mcc.parse(text)
check('read: the captions first, and it carries OP-47', ([s['text'] for s in read], settings['captions']),
      (['The captions', 'And more'], 'op47'))
alone = '\n'.join(line for line in text.split('\n') if not line[:2].isdigit() or '\t4302' in line)
check('a file of teletext alone: its subtitle page',
      [(s['start'], s['end'], s['text']) for s in mcc.parse(alone)[0]],
      [(1.0, 2.0, 'Die Untertitel'), (3.0, 4.0, 'Grüße')])
every = mcc.write(segments, rate='25', frames=150, teletext_tracks=teletext_tracks)
check('for a video, a packet of each every frame', sum(1 for line in every.split('\n') if '\t4302' in line), 150)
check('without teletext, none', '\t4302' in mcc.write(segments, rate='25', frames=150), False)

print('the command')
packets = embedded_captions.command('in.mp4', 'out.mxf', 'caps.mcc', 'MXF', ffmpeg='ffmpeg', packets=True)
check('MXF with OP-47: the MacCaption file\'s packets copied as they are, a data stream',
      (packets[packets.index('-f') + 1:packets.index('-f') + 4], packets[-3:-1]),
      (['mcc', '-eia608_extract', '0'], ['-c:d', 'copy']))
check('without, as before', '-eia608_extract' in embedded_captions.command('i', 'o', 'c', 'MXF'), False)

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed: no MXF')
else:
    print('in an MXF')
    folder = Path(tempfile.mkdtemp())
    (folder / 'both.mcc').write_text(text)
    probe = subprocess.run([ffprobe, '-v', 'error', '-f', 'mcc', '-eia608_extract', '0', '-select_streams', '0',
                            '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', str(folder / 'both.mcc')],
                           capture_output=True, text=True).stdout.strip()
    check('FFmpeg reads the MacCaption file\'s packets as ST 436 ancillary data', probe, 'smpte_436m_anc')
    source = folder / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=5',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '5', '-c:v', 'libx264', '-c:a', 'aac',
                    str(source)], check=True)
    made = embedded_captions.write_video(source, folder / 'both.mxf', segments, fps=25, duration=5, container='MXF',
                                         teletext_tracks=teletext_tracks, ffmpeg=ffmpeg)
    check('the picture, the sound and one ancillary data track',
          subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', str(made)],
                         capture_output=True, text=True).stdout.split(), ['dnxhd', 'pcm_s16le', 'smpte_436m_anc'])
    dump = subprocess.run([ffprobe, '-v', 'error', '-select_streams', '2', '-show_packets', '-show_data',
                           '-read_intervals', '%+#1', str(made)], capture_output=True, text=True).stdout
    check('each frame two ancillary packets: the captions\' (DID 61h) and the teletext\'s (DID 43h)',
          ('0002 0009' in dump, '6101' in dump.replace(' ', ''), '4302' in dump.replace(' ', '')), (True, True, True))
    found, track = closed_captions.from_video(made, ffprobe=ffprobe)
    check('read: the captions, as before', ([s['text'] for s in found], track), (['The captions', 'And more'], '708-1'))
    found, track = closed_captions.from_video(made, 'teletext', ffprobe=ffprobe)
    check('and the teletext beside them, at its times',
          ([(s['start'], s['end'], s['text']) for s in found], track),
          ([(1.0, 2.0, 'Die Untertitel'), (3.0, 4.0, 'Grüße')], 'teletext'))
    made = embedded_captions.write_video(source, folder / 'mov.mov', segments, fps=25, duration=5, container='MOV',
                                         teletext_tracks=teletext_tracks, ffmpeg=ffmpeg)
    check('a QuickTime caption track has no room for it: its captions alone',
          closed_captions.from_video(made, 'teletext', ffprobe=ffprobe), ([], None))

    print('the export path')
    from subtitld.modules import bounce
    session.VIDEO = {'filepath': str(source), 'framerate': 25.0, 'duration': 5.0}
    session.SUBTITLE = {'segments': segments, 'language': 'en-us'}
    session.FFMPEG_EXECUTABLE = ffmpeg
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.mxf'), 'MXF', {'teletext': True, 'codec': 'mpeg2'})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    check('what the export runs writes the MXF with both',
          (said, [s['text'] for s in closed_captions.from_video(folder / 'wired.mxf', 'teletext', ffprobe=ffprobe)[0]]),
          ([(True, '')], ['The captions', 'And more']))

print('the dialog')
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.mp4'}
from subtitld.interface.export_dialog import _VideoPanel
panel = _VideoPanel()
check('QuickTime: no teletext to choose', (panel.captions_teletext.isHidden(), panel.get_config()['captions']['teletext']),
      (True, False))
panel.captions_format_combo.combobox.setCurrentIndex(1)
panel._refresh_captions()
panel.captions_teletext.setChecked(True)
check('MXF: teletext beside the captions, asked for', (panel.captions_teletext.isHidden(),
                                                       panel.get_config()['captions']['teletext']), (False, True))
again = _VideoPanel()
check('remembered', again.captions_teletext.isChecked(), True)
check('the captions engine offers it', 'teletext' in closed_captions.TRACKS, True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
