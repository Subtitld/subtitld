"""Closed captions: which to read from caption data (CEA-708 services first,
then CEA-608's CC1 to CC4, or the one asked for), the entries that count,
and the captions of a video file, read through ffprobe: a QuickTime
caption track, a video with none, a file name full of what filtergraphs
escape. Then the "Closed captions in the video" engine, start to finish,
and its place among the engines.

The video checks make their files with FFmpeg, and are left out (said so)
where it is not installed.

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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import cea708, closed_captions, scc

fails = []
RATE = 30000 / 1001


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def pop_on(text, channel=1):
    """CEA-608 words (load, show, clear) for `text` on row 15 in a field's
    `channel`, parity bits and all."""
    first = 0x14 if channel == 1 else 0x1C
    codes = [scc._TO_BASIC[c] for c in text] + ([0] if len(text) % 2 else [])
    load = [(first, 0x20)] * 2 + [(first, 0x70)] * 2 + list(zip(codes[::2], codes[1::2]))
    return ([(scc._odd(a), scc._odd(b)) for a, b in load], [(scc._odd(first), scc._odd(0x2F))] * 2,
            [(scc._odd(first), scc._odd(0x2C))] * 2)


def words_at(text, shown, cleared, channel=1):
    """{frame: word} for `text` loaded from frame 0, shown and cleared."""
    load, show, clear = pop_on(text, channel)
    out = dict(enumerate(load))
    out.update({shown + offset: word for offset, word in enumerate(show)})
    out.update({cleared + offset: word for offset, word in enumerate(clear)})
    return out


def service2(text, shown):
    """CEA-708 packets for service 2: `text` loaded and shown at `shown`."""
    block = [0x98, 0x18, 60, 65, 0x00, len(text) - 1, 0x09] + list(text.encode('latin-1'))
    data = [2 << 5 | len(block)] + block + [2 << 5 | 2, 0x89, 0x01]
    if len(data) % 2 == 0:
        data.append(0)
    data = [0x40 | (len(data) + 1) // 2] + data
    return {shown: [(3 if i == 0 else 2, data[i], data[i + 1]) for i in range(0, len(data), 2)]}


def batches_of(cc1=None, cc3=None, dtvcc=None, cc2=None):
    """Caption data a frame at a time: CEA-608 field 1 (CC1, CC2), field 2
    (CC3), and CEA-708 entries."""
    frames = {}
    for cc_type, words in ((0, cc1), (0, cc2), (1, cc3)):
        for frame, word in (words or {}).items():
            frames.setdefault(frame, []).append((cc_type, *word))
    for frame, entries in (dtvcc or {}).items():
        frames.setdefault(frame, []).extend(entries)
    return sorted(frames.items())


english = words_at('ENGLISH', 40, 90)
spanish = words_at('ESPANOL', 40, 90)
second_channel = words_at('SECOND', 40, 90, channel=2)
service1 = cea708.frame_data([('Service one', 40, 90)], 18)
service_two = service2('Servicio dos', 45)

print('which captions')
ALL = batches_of(cc1=english, cc3=spanish, dtvcc={**service1, **{f: service1.get(f, []) + e for f, e in service_two.items()}})
for track, want in (('auto', ('Service one', '708-1')), ('708-2', ('Servicio dos', '708-2')),
                    ('cc1', ('{\\an1}ENGLISH', 'cc1')), ('cc3', ('{\\an1}ESPANOL', 'cc3'))):
    segments, used = closed_captions.decode(ALL, RATE, track)
    check(f'{track}: {want[0]}', (segments[0]['text'] if segments else None, used), want)
check('one that is not there: none', closed_captions.decode(ALL, RATE, 'cc4'), ([], 'cc4'))
check('without CEA-708: CC1', closed_captions.decode(batches_of(cc1=english, cc3=spanish), RATE)[1], 'cc1')
check('CC2, when CC1 has none', closed_captions.decode(batches_of(cc2=second_channel), RATE)[0][0]['text'], '{\\an1}SECOND')
check('only field 2: CC3', closed_captions.decode(batches_of(cc3=spanish), RATE)[1], 'cc3')
check('nothing: none', closed_captions.decode(batches_of(), RATE), ([], None))
check('the entries that count: valid ones, their type', closed_captions.triplets(bytes.fromhex('fc9420 fd8080 fa0000 f81111 ff4222 fe8901')),
      [(0, 0x94, 0x20), (1, 0x80, 0x80), (3, 0x42, 0x22), (2, 0x89, 0x01)])
check('a file name as a filtergraph takes it', closed_captions._filter_path("C:\\it's [a], b; c.mov"),
      "C\\\\:\\\\\\\\it\\\\\\'s \\[a\\]\\, b\\; c.mov")

print('in a video')
ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed')
else:
    folder = Path(tempfile.mkdtemp())
    segments = [{'start': 1.0, 'end': 2.5, 'text': 'In a <i>QuickTime</i> track'},
                {'start': 3.0, 'end': 4.5, 'text': '{\\an8}At the top'},
                # The last packet of a track FFmpeg's QuickTime muxer leaves out: this one's clearing.
                {'start': 5.0, 'end': 5.5, 'text': 'Last'}]
    scc.save(folder / 'in.scc', segments)
    weird = folder / "it's [a], b; c: d"
    weird.mkdir()
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=30000/1001:d=6', '-i',
                    str(folder / 'in.scc'), '-map', '0:v', '-map', '1:s', '-c:v', 'mpeg4', '-c:s', 'copy',
                    str(weird / 'track.mov')], check=True)
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=25:d=2', '-c:v', 'mpeg4',
                    str(weird / 'plain.mp4')], check=True)
    seen = []
    read, used = closed_captions.from_video(weird / 'track.mov', ffprobe=ffprobe, progress=seen.append)
    check("a QuickTime caption track (c608): read as it is; how far it got", (
        [(s['start'], s['end'], s['text']) for s in read][:2], used, bool(seen) and all(0 <= v <= 1 for v in seen)),
          ([(1.001, 2.502, 'In a <i>QuickTime</i> track'), (3.003, 4.505, '{\\an8}At the top')], 'cc1', True))
    check('a video with none, its name full of what filtergraphs escape: none, and no error',
          closed_captions.from_video(weird / 'plain.mp4', ffprobe=ffprobe), ([], None))
    try:
        closed_captions.from_video(folder / 'missing.mp4', ffprobe=ffprobe)
        check('a file that is not there: an error', None, 'RuntimeError')
    except RuntimeError:
        check('a file that is not there: an error', True, True)

    print('the engine')
    from subtitld.modules import session
    session.FFPROBE_EXECUTABLE = ffprobe
    from subtitld.modules.addons.builtin import captions_provider
    provider = captions_provider.get_provider()
    results = []

    def run(path, track):
        loop = QEventLoop()
        outcome = {}
        provider.transcript_finished.connect(lambda segs: (outcome.setdefault('segments', segs), loop.quit()))
        provider.error.connect(lambda message: (outcome.setdefault('error', message), loop.quit()))
        QTimer.singleShot(20000, loop.quit)
        provider.transcribe(str(path), 'en-us', {'track': track})
        loop.exec()
        provider.transcript_finished.disconnect()
        provider.error.disconnect()
        return outcome

    outcome = run(weird / 'track.mov', 'auto')
    check('reads the video in a thread, then hands the subtitles on', [s['text'] for s in outcome.get('segments', [])],
          ['In a <i>QuickTime</i> track', '{\\an8}At the top', 'Last'])
    check('captions it does not have: none', run(weird / 'track.mov', 'cc3'), {'segments': []})
    check('a file it cannot read: an error', sorted(run(folder / 'missing.mp4', 'auto')), ['error'])

from subtitld.modules.addons.manager import AddonManager
from subtitld.modules.addons.builtin import captions_provider, import_provider
from subtitld.modules.addons.provider import SOURCE_ENGINES, TASK_ASR_TRANSCRIBE
manager = AddonManager()
manager.register_builtin(import_provider.get_provider())
manager.register_builtin(captions_provider.get_provider())
check('an engine that brings subtitles in, never the default transcription engine', (
    'captions' in SOURCE_ENGINES, [p.id for p in manager.providers_for_task(TASK_ASR_TRANSCRIBE)],
    manager.default_for_task(TASK_ASR_TRANSCRIBE)), (True, ['import', 'captions'], None))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
