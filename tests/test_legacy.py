"""The player-era formats: reading each one's grammar, telling them apart
when they share an extension, round-tripping the ones Subtitld writes, and
checking the readers against FFmpeg's own demuxers where it has one.

The FFmpeg checks are the point of this suite: these formats have no
standards, so a reader is only as good as the implementation it agrees
with. They skip themselves where ffmpeg is missing.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, re, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import ebustl, legacy

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fmt, fps=25.0):
    return [(s['start'], s['end'], s['text']) for s in legacy.parse(text, fmt, fps)]


# --------------------------------------------------------------------------
# Each format's grammar
# --------------------------------------------------------------------------
print('MPL2: tenths of a second, "|" between lines')
check('two cues', cues('[12][35]First|second\n[40][60]Another\n', 'mpl2'),
      [(1.2, 3.5, 'First\nsecond'), (4.0, 6.0, 'Another')])

print('PJS: tenths of a second too, text in quotes')
# The numbers look like frames and are not: 25 is 2.5 seconds, not one.
check('two cues', cues('1,25,"First cue"\n30,60,"Second cue"\n', 'pjs'),
      [(0.1, 2.5, 'First cue'), (3.0, 6.0, 'Second cue')])

print('MPSub: counted from the end of the subtitle before')
check('seconds', cues('FORMAT=TIME\n\n0 3\nFirst cue\n\n2 4\nSecond cue\n', 'mpsub'),
      [(0.0, 3.0, 'First cue'), (5.0, 9.0, 'Second cue')])
check('frames', cues('FORMAT=25.00\n\n0 75\nFirst cue\n\n25 50\nSecond cue\n', 'mpsub'),
      [(0.0, 3.0, 'First cue'), (4.0, 6.0, 'Second cue')])

print('VPlayer and TMPlayer: a clock, no end times')
check('ends at the next', cues('00:00:01:First cue\n00:00:05:Second cue\n', 'vplayer'),
      [(1.0, 5.0, 'First cue'), (5.0, 8.0, 'Second cue')])
check('"=" separator', cues('00:00:01=First cue\n00:00:05=Second cue\n', 'tmplayer'),
      [(1.0, 5.0, 'First cue'), (5.0, 8.0, 'Second cue')])

print('AQTitle: frames, the next marker ends the subtitle')
check('at 25 fps', cues('-->> 25\nFirst cue\n\n-->> 75\nSecond cue\n', 'aqtitle'),
      [(1.0, 3.0, 'First cue'), (3.0, 6.0, 'Second cue')])
check('at 30 fps', cues('-->> 30\nFirst cue\n\n-->> 90\nSecond\n', 'aqtitle', fps=30.0),
      [(1.0, 3.0, 'First cue'), (3.0, 6.0, 'Second')])

print('SubViewer 1: marks around the text')
check('two cues', cues('[00:00:01]\nFirst cue\n[00:00:03]\n\n[00:00:04]\nSecond cue\n[00:00:06]\n', 'subviewer1'),
      [(1.0, 3.0, 'First cue'), (4.0, 6.0, 'Second cue')])

print('Spruce: frames after the seconds, and NOT EBU STL')
check('two cues', cues('00:00:01:00 , 00:00:03:00 , First|second\n00:00:04:00 , 00:00:06:00 , Second cue\n', 'spruce'),
      [(1.0, 3.0, 'First\nsecond'), (4.0, 6.0, 'Second cue')])
check('settings skipped', len(legacy.parse('$TapeOffset = FALSE\n// a comment\n'
                                           '00:00:01:00 , 00:00:03:00 , Only cue\n', 'spruce')), 1)

print('RealText: each <time> begins a subtitle, the next one ends it')
RT = ('<window type="generic">\n<time begin="00:00:01"/>First cue<clear/>\n'
      '<time begin="00:00:04"/>Second <b>bold</b> cue<clear/>\n</window>\n')
check('cues and kept tags', cues(RT, 'realtext'),
      [(1.0, 4.0, 'First cue'), (4.0, 7.0, 'Second <b>bold</b> cue')])

print('TurboTitle: hundredths, the name of whoever speaks')
# Lines from TurboTitle 0.80's own ExampleA-2.TTS (Robert Jenks, 1991).
TTS = ('0:00:00.80,0:00:02.28,Narrator This is an example of an Un-Timed TTS script. \n'
       '0:00:06.28,0:00:08.80,~ \n'
       '0:00:08.82,0:00:11.08,~ The name field for this line is a tilde.\n')
read_tts = legacy.parse(TTS, 'turbotitle')
check('the empty line is left out', len(read_tts), 2)
check('times in hundredths', (read_tts[0]['start'], read_tts[0]['end']), (0.8, 2.28))
check('the name is the speaker', read_tts[0].get('speaker'), 'Narrator')
check('"~" is no speaker', 'speaker' in read_tts[1], False)
check('a script still being timed holds no subtitles',
      legacy.parse('9:59:59.99,9:59:59.99,Narrator Not timed yet\n', 'turbotitle'), [])

print('ZeroG: E lines, hundredths, "\\n" between lines')
ZEG = ('% Zero G 1.0\n# a comment\nS DEFAULT DEFAULT 3 0 0 0 0 3 2 14 1\n'
       'E 1 0:00:01.00 0:00:03.50 DEFAULT Narrator First\\nsecond\n'
       'E 1 0:00:04.00 0:00:06.00 DEFAULT ~ Second cue\n')
read_zeg = legacy.parse(ZEG, 'zerog')
check('styles and comments skipped', len(read_zeg), 2)
check('cue, lines and speaker', (read_zeg[0]['start'], read_zeg[0]['end'], read_zeg[0]['text'],
                                 read_zeg[0].get('speaker')), (1.0, 3.5, 'First\nsecond', 'Narrator'))

# --------------------------------------------------------------------------
# Telling them apart: the extensions collide, so the bytes decide
# --------------------------------------------------------------------------
print('kind(): what a file actually is')
check('mpl2', legacy.kind('[12][35]Hello\n'), 'mpl2')
check('pjs', legacy.kind('1,25,"Hello"\n'), 'pjs')
check('aqtitle', legacy.kind('-->> 25\nHello\n'), 'aqtitle')
check('mpsub', legacy.kind('FORMAT=TIME\n\n0 3\nHello\n'), 'mpsub')
check('subviewer1', legacy.kind('[00:00:01]\nHello\n[00:00:03]\n'), 'subviewer1')
check('spruce', legacy.kind('00:00:01:00 , 00:00:03:00 , Hello\n'), 'spruce')
check('vplayer', legacy.kind('00:00:01:Hello\n00:00:05:There\n'), 'vplayer')
check('turbotitle', legacy.kind('0:00:00.80,0:00:02.28,Narrator Hello\n'), 'turbotitle')
check('zerog', legacy.kind('% Zero G 1.0\nE 1 0:00:01.00 0:00:03.00 DEFAULT ~ Hello\n'), 'zerog')
check('realtext', legacy.kind('<window><time begin="1"/>Hello<clear/></window>'), 'realtext')
check('an SRT is none of them', legacy.kind('1\n00:00:01,000 --> 00:00:03,000\nHello\n'), '')
check('plain text is none of them', legacy.kind('Just some notes\nabout nothing\n'), '')

print('.stl is three formats: the bytes say which')
check('Spruce is not EBU', ebustl.is_stl(b'00:00:01:00 , 00:00:03:00 , Hello'), False)
check('Spruce text sniffs as Spruce', legacy.kind('00:00:01:00 , 00:00:03:00 , Hello\n'), 'spruce')
_gsi = b'850' + b'STL25.01' + b'1' + b'00' + b' ' * 1013
check('EBU STL is EBU', ebustl.is_stl(_gsi), True)
check('EBU STL is not one of these', legacy.kind(_gsi), '')

# --------------------------------------------------------------------------
# Round trips, with the losses each format forces
# --------------------------------------------------------------------------
print('round trips')
SAMPLE = [{'start': 1.0, 'end': 3.5, 'text': 'First line\nsecond line'},
          {'start': 4.0, 'end': 6.25, 'text': 'Second cue'}]
for fmt in legacy.WRITABLE:
    written = legacy.write(SAMPLE, fmt)
    back = legacy.parse(written, fmt)
    # TurboTitle holds one subtitle a line and has no way to write a line
    # break, so its two lines come back as one. Everything else keeps them.
    want_text = ['First line second line', 'Second cue'] if fmt == 'turbotitle' \
        else ['First line\nsecond line', 'Second cue']
    # The clock formats write whole seconds and carry no end time.
    want_starts = [1.0, 4.0]
    check(f'{fmt}: cues', len(back), 2)
    check(f'{fmt}: text', [s['text'] for s in back], want_text)
    check(f'{fmt}: starts', [s['start'] for s in back], want_starts)
    check(f'{fmt}: sniffs as itself', legacy.kind(written) in (fmt, 'vplayer' if fmt == 'tmplayer' else fmt), True)

print('speakers survive the formats that have a name field')
for fmt in ('turbotitle', 'zerog'):
    spoken = [{'start': 1.0, 'end': 2.0, 'text': 'Hello', 'speaker': 'Alice'}]
    back = legacy.parse(legacy.write(spoken, fmt), fmt)
    check(f'{fmt}', back[0].get('speaker'), 'Alice')

# --------------------------------------------------------------------------
# Against FFmpeg, which demuxes eight of these itself
# --------------------------------------------------------------------------
STAMP = re.compile(r'(\d+):(\d{2}):(\d{2}),(\d{3}) --> (\d+):(\d{2}):(\d{2}),(\d{3})')
ORACLE = [
    ('mpl2', 'mpl2', '[12][35]First cue\n[40][60]Second cue\n'),
    ('pjs', 'pjs', '1,25,"First cue"\n30,60,"Second cue"\n'),
    ('mpsub', 'mpsub', 'FORMAT=TIME\n\n0 3\nFirst cue\n\n2 4\nSecond cue\n'),
    ('vplayer', 'vplayer', '00:00:01:First cue\n00:00:05:Second cue\n'),
    ('aqtitle', 'aqtitle', '-->> 25\nFirst cue\n\n-->> 75\nSecond cue\n'),
    ('subviewer1', 'subviewer1', '[00:00:01]\nFirst cue\n[00:00:03]\n\n[00:00:04]\nSecond cue\n[00:00:06]\n'),
    ('spruce', 'stl', '00:00:01:00 , 00:00:03:00 , First cue\n00:00:04:00 , 00:00:06:00 , Second cue\n'),
]


def ffmpeg_cues(path, demuxer):
    out = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', demuxer,
                          '-i', str(path), '-f', 'srt', '-'],
                         capture_output=True, text=True, timeout=60).stdout
    found, block = [], []
    for line in out.split('\n') + ['']:
        if line.strip():
            block.append(line)
            continue
        stamp = next((STAMP.search(one) for one in block if STAMP.search(one)), None)
        if stamp:
            numbers = [int(part) for part in stamp.groups()]
            start = numbers[0] * 3600 + numbers[1] * 60 + numbers[2] + numbers[3] / 1000
            end = numbers[4] * 3600 + numbers[5] * 60 + numbers[6] + numbers[7] / 1000
            text = '\n'.join(block[block.index(stamp.string) + 1:]).strip()
            if text:
                found.append((round(start, 3), round(end, 3), text))
        block = []
    return found


if subprocess.run(['which', 'ffmpeg'], capture_output=True).returncode != 0:
    print('against FFmpeg: skipped, ffmpeg is not installed')
else:
    print('against FFmpeg, which demuxes these itself')
    work = Path(tempfile.mkdtemp())
    for fmt, demuxer, text in ORACLE:
        path = work / f'sample.{fmt}'
        path.write_text(text, encoding='utf-8')
        theirs = ffmpeg_cues(path, demuxer)
        ours = [(s['start'], s['end'], s['text']) for s in legacy.parse(text, fmt)]
        check(f'{fmt}: same starts', [c[0] for c in ours], [c[0] for c in theirs])
        check(f'{fmt}: same text', [c[2] for c in ours], [c[2] for c in theirs])
        # FFmpeg leaves the last cue of a format with no end times running
        # to the end of time; Subtitld gives it `TAIL`. Compare the rest.
        pairs = [(t[1], o[1]) for t, o in zip(theirs, ours) if t[1] < 86400]
        check(f'{fmt}: same ends', all(abs(a - b) < 0.051 for a, b in pairs), True)

print()
print('FAILURES: ' + ', '.join(fails) if fails else 'all good')
sys.exit(1 if fails else 0)
