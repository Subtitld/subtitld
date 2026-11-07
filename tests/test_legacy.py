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
# A sniffer that claims a file belonging to another format is worse than
# one that claims nothing: .txt, .sub and .stl all reach it.
OTHERS = {
    'WebVTT': 'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n',
    'ASS': '[Script Info]\nTitle: x\n[V4+ Styles]\n[Events]\n'
           'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello\n',
    'SSA': '[Script Info]\n[V4 Styles]\n[Events]\n'
           'Dialogue: Marked=0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,Hello\n',
    'SAMI': '<SAMI>\n<BODY>\n<SYNC Start=1000><P Class=ENUSCC>Hello\n</BODY>\n</SAMI>\n',
    'TTML': "<tt xmlns='http://www.w3.org/ns/ttml'><body><div>"
            "<p begin='1s' end='3s'>Hello</p></div></body></tt>",
    'SBV': '0:00:01.000,0:00:03.000\nHello\n',
    'MicroDVD': '{25}{75}Hello\n',
    'SCC': 'Scenarist_SCC V1.0\n\n00:00:01;00\t942c 942c 9420\n',
    'YouTube srv3': '<timedtext format="3"><body><p t="1000" d="2000">Hello</p></body></timedtext>',
    'JSON': '{"segments": [{"start": 1.0, "end": 3.0, "text": "Hello"}]}',
}
for other, text in OTHERS.items():
    check(f'{other} is left to its own reader', legacy.kind(text), '')
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
print('ViPlay: its magic line, milliseconds, "|" between lines')
VSF = ('{* VIPLAY SUBTITLE FILE *}\n'
       '00:00:55,000-00:01:02,600=O PADRINHO II\n'
       '00:01:26,600-00:01:29,000=<u><b><i>O padrinho nasceu com o nome|de Vito Andolini,\n')
check('cues, with the tags kept', cues(VSF, 'viplay'),
      [(55.0, 62.6, 'O PADRINHO II'),
       (86.6, 89.0, '<u><b><i>O padrinho nasceu com o nome\nde Vito Andolini,')])

print('SBT: two time lines, then always two lines of text')
SBT = ('00:00:55\n00:01:02\n\nO PADRINHO II\n'
       '00:01:26\n00:01:29\nO padrinho nasceu com o nome\nde Vito Andolini,\n')
check('the empty line of a one-line subtitle', cues(SBT, 'sbt'),
      [(55.0, 62.0, 'O PADRINHO II'),
       (86.0, 89.0, 'O padrinho nasceu com o nome\nde Vito Andolini,')])

print('Captions 32: fixed columns, and not Spruce')
C32 = '00:01:26:60 , 00:01:29:00 , O padrinho nasceu com o nome     |de Vito Andolini,                \n'
check('hundredths and the two fields', cues(C32, 'captions32'),
      [(86.6, 89.0, 'O padrinho nasceu com o nome\nde Vito Andolini,')])
check('it is Captions 32, not Spruce', legacy.kind(C32), 'captions32')
check('and Spruce is still Spruce', legacy.kind('00:00:01:00 , 00:00:03:00 , Free|text\n'), 'spruce')

print('SubSonic: seconds that wrap every 256')
SUBSONIC = '1 55.00 \\ ~:\\O PADRINHO II\n1 62.60\n1 86.60 \\ ~:\\de Vito Andolini,\n1 89.00\n'
check('two cues, ended by the lines between', cues(SUBSONIC, 'subsonic'),
      [(55.0, 62.6, 'O PADRINHO II'), (86.6, 89.0, 'de Vito Andolini,')])
# The numbers fall where the count wraps; the times must keep climbing.
check('past 256 seconds', [s['start'] for s in legacy.parse(
      '1 250.00 \\ ~:\\before\n1 252.00\n1 10.00 \\ ~:\\after\n1 12.00\n', 'subsonic')], [250.0, 266.0])

print('Oresme: frames, braces for tags, {N} for a line break')
ORESME = ('00:00:00:00{BC}{W2710}\n'
          '10:00:17:06{Bottom}{Open Caption}{Center}{White}{Font Arial GVP Bold}We view\n'
          '10:00:19:06{Bottom}{Open Caption}{Center}{White}{Font Arial GVP Bold}Lufa Farms as{N}an agrotechnology business\n')
read_oresme = legacy.parse(ORESME, 'oresme')
check('the broadcast start and the frames', (read_oresme[0]['start'], read_oresme[0]['text']),
      (36017.24, 'We view'))
check('{N} breaks the line', read_oresme[1]['text'], 'Lufa Farms as\nan agrotechnology business')

print('JACOSub: its own TIMERES, thirtieths when it says nothing')
check('default timeres', cues('0:00:01.15 0:00:03.00 D First cue\n', 'jacosub'), [(1.5, 3.0, 'First cue')])
check('#TIMERES 100', cues('#TIMERES 100\n0:00:01.50 0:00:03.25 D First cue\n', 'jacosub'),
      [(1.5, 3.25, 'First cue')])
check('#SHIFT moves them all', cues('#TIMERES 100\n#SHIFT 100\n0:00:01.00 0:00:03.00 D Cue\n', 'jacosub'),
      [(2.0, 4.0, 'Cue')])
check('\\n breaks the line', cues('#TIMERES 100\n0:00:01.00 0:00:03.00 D First\\nsecond\n', 'jacosub'),
      [(1.0, 3.0, 'First\nsecond')])

print('Sasami2k: a command script, milliseconds, a blank marker to clear')
S2K = (';Env.Movie.Width=320\n;Set.Font.Face=Tahoma\n;Buffer.Push=1\n'
       ';Buffer.Pop=1\n;Set.Time.Start=55000\n;Set.Start.Position.y=203\nO PADRINHO II\n'
       ';Buffer.Pop=1\n;Set.Time.Start=62600\n \n')
check('commands skipped, the marker ends it', cues(S2K, 'sasami'), [(55.0, 62.6, 'O PADRINHO II')])
check('its first line names it', legacy.kind(S2K), 'sasami')

print('Xombie: an ASS dialect in milliseconds, with an actor')
XSS = ('; a comment\nScreenHorizontal=1280\n'
       'Line=D,1,0,0:00:01.500,0:00:03.250,Default,Default,Alice,0,0,0,Hello\\Nthere\n'
       'Line=X,2,0,0:00:04.000,0:00:05.000,Default,Default,,0,0,0,not dialogue\n')
read_xss = legacy.parse(XSS, 'xombie')
check('only the D rows', len(read_xss), 1)
check('milliseconds, lines and the actor as the speaker',
      (read_xss[0]['start'], read_xss[0]['end'], read_xss[0]['text'], read_xss[0].get('speaker')),
      (1.5, 3.25, 'Hello\nthere', 'Alice'))

print('MacSUB: "/550" is 55 seconds, and tenths are all it keeps')
MAC = '/550\nO PADRINHO II\n/626\n]rem=a comment of its own\n/866\nprimeira\\nsegunda\n/890\n'
check('cues, comments left out, \\n broken', cues(MAC, 'macsub'),
      [(55.0, 62.6, 'O PADRINHO II'), (86.6, 89.0, 'primeira\nsegunda')])
check('a subtitle with no time of its own starts where the last ended',
      cues('/100\nfirst\n/200\nsecond\n/300\n', 'macsub'),
      [(10.0, 20.0, 'first'), (20.0, 30.0, 'second')])

print('Stream SubText: the player export, and the project titles')
SST = ('SST 2.0.73\n;\n[PROPERTIES]\nTitle=x\n[SCRIPT]\n'
       '55000\t62600\t{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0\\fnil\\fcharset0 Arial;}}~'
       '{\\colortbl ;\\red255\\green255\\blue255;}~\\fs24 O PADRINHO II\\par segunda~}~\n')
check('milliseconds, and the RTF read for its words alone', cues(SST, 'sst'),
      [(55.0, 62.6, 'O PADRINHO II\nsegunda')])
check('its font and colour tables stay out of the subtitle',
      'Arial' in legacy.parse(SST, 'sst')[0]['text'], False)
SSTS = ('SST 2.0.66\n[TITLES]\nfps=25.00\n'
        'LINE\tCHAPTER\tTIME-IN\tTIME-OUT\tTEXT\tSTYLE\n'
        '1\t\t00:00:55.00\t00:01:02.60\tO PADRINHO II~segunda\tDefault\n')
check('six fields, hundredths, "~" between lines', cues(SSTS, 'ssts'),
      [(55.0, 62.6, 'O PADRINHO II\nsegunda')])
check('the header row is not a subtitle', len(legacy.parse(SSTS, 'ssts')), 1)

print('DKS: whole seconds, "[br]", a bare timestamp to close')
DKS = ('[00:00:07]THERE IS A PLACE ON EARTH WHERE IT[br]IS STILL THE MORNING OF LIFE...\n'
       '[00:00:12]\n[00:00:13]AND THE GREAT HERDS RUN FREE.[br]SO WHAT?!\n[00:00:15]\n')
check('the two cues of GStreamer\'s own fixture', [(c[0], c[1]) for c in cues(DKS, 'dks')],
      [(7.0, 12.0), (13.0, 15.0)])
check('[br] breaks the line', cues(DKS, 'dks')[0][2].split('\n')[1], 'IS STILL THE MORNING OF LIFE...')
check('a file written back to back keeps every cue',
      [(c[0], c[1]) for c in cues('[00:00:01]one\n[00:00:03]two\n[00:00:05]three\n', 'dks')],
      [(1.0, 3.0), (3.0, 5.0), (5.0, 8.0)])

print('Power DivX: the hour is written one digit as often as two')
check('both forms read', cues('{00:00:01}{00:00:03}- Hall\u00e5!\n'
                             '{0:01:37}{0:01:40}Drama Club|second line\n', 'powerdivx'),
      [(1.0, 3.0, '- Hall\u00e5!'), (97.0, 100.0, 'Drama Club\nsecond line')])

print('DVD Subtitle System: frames, a space between, "//" between lines')
check('at 25 fps', cues('00:03:15:22 00:03:23:10 This is line one.//This is line two.\n', 'dvdss'),
      [(195.88, 203.4, 'This is line one.\nThis is line two.')])

print('QTtext: the fraction counts units of {timeScale}, not decimals')
check('at a scale of 100, ".30" is three tenths',
      cues('{QTtext} {timeScale:100} {timeStamps:absolute}\n[00:00:09.30]\nFirst\n[00:00:12.00]\n', 'qttext'),
      [(9.3, 12.0, 'First')])
check('at a scale of 30, ".15" is half a second',
      cues('{QTtext} {timescale: 30}\n[00:00:05.15]\nFirst\n[00:00:07.00]\n', 'qttext'),
      [(5.5, 7.0, 'First')])
check('with no scale named, the fraction is milliseconds',
      cues('{QTtext}\n[00:00:01.500]\nFirst\n[00:00:03.000]\n', 'qttext'),
      [(1.5, 3.0, 'First')])
check('{timeStamps:relative} adds each to the last',
      [c[0] for c in cues('{QTtext} {timeScale:1000} {timeStamps:relative}\n'
                          '[00:00:01.000]\nFirst\n[00:00:02.000]\nSecond\n[00:00:01.000]\n', 'qttext')],
      [1.0, 3.0])

print('round trips')
SAMPLE = [{'start': 1.0, 'end': 3.5, 'text': 'First line\nsecond line'},
          {'start': 4.0, 'end': 6.25, 'text': 'Second cue'}]
for fmt in legacy.WRITABLE:
    written = legacy.write(SAMPLE, fmt)
    back = legacy.parse(written, fmt)
    # TurboTitle holds one subtitle a line and has no way to write a line
    # break, so its two lines come back as one. Everything else keeps them.
    # TurboTitle and SubSonic hold one subtitle a line and have no way to
    # write a line break, so two lines come back as one. The rest keep them.
    want_text = ['First line second line', 'Second cue'] if fmt in ('turbotitle', 'subsonic') \
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
    ('jacosub', 'jacosub', '#TIMERES 100\n0:00:01.00 0:00:03.00 D First cue\n0:00:04.00 0:00:06.00 D Second cue\n'),
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

# --------------------------------------------------------------------------
# Through the app: opening and saving each one as a user does
# --------------------------------------------------------------------------
print('through file_io')
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session

folder = Path(tempfile.mkdtemp())
WRITTEN = [{'start': 1.0, 'end': 3.5, 'text': 'First line\nsecond line'},
           {'start': 4.0, 'end': 6.25, 'text': 'Second cue'}]

for name, fmt in sorted(legacy.SESSION_FORMATS.items()):
    path = folder / f'sub.{legacy.EXTENSIONS[fmt]}'
    session.SUBTITLE['segments'] = [dict(segment) for segment in WRITTEN]
    session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/tears.mov'}
    session.FORMAT = {}
    file_io.save_file(str(path), name)
    session.SUBTITLE['segments'] = []
    opened, found = file_io.process_subtitles_file(str(path))
    check(f'{name}: saved and opened again', (found, len(opened)), (name, 2))

print('the extensions three formats share')
# A .sub is MicroDVD, SubViewer 2, MPSub or SubViewer 1, and only the
# bytes say which. A .stl is EBU STL, Spruce, or a 3D model.
(folder / 'micro.sub').write_text('{25}{75}MicroDVD here\n', encoding='utf-8')
check('.sub: MicroDVD', file_io.process_subtitles_file(str(folder / 'micro.sub'))[1], 'SUB')
(folder / 'mp.sub').write_text('FORMAT=TIME\n\n0 3\nMPSub here\n', encoding='utf-8')
check('.sub: MPSub', file_io.process_subtitles_file(str(folder / 'mp.sub'))[1], 'MPSUB')
(folder / 'sv1.sub').write_text('[00:00:01]\nSubViewer 1 here\n[00:00:03]\n', encoding='utf-8')
check('.sub: SubViewer 1', file_io.process_subtitles_file(str(folder / 'sv1.sub'))[1], 'SUBVIEWER1')
(folder / 'spruce.stl').write_text('00:00:01:00 , 00:00:03:00 , Spruce here\n', encoding='utf-8')
check('.stl: Spruce', file_io.process_subtitles_file(str(folder / 'spruce.stl'))[1], 'SPRUCE')
(folder / 'model.stl').write_bytes(b'solid cube\nfacet normal 0 0 1\n' * 100)
try:
    file_io.process_subtitles_file(str(folder / 'model.stl'))
    check('.stl: a 3D model still says what it is not', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('.stl: a 3D model still says what it is not', str(error).startswith('Not an EBU STL file'), True)

print()
print('FAILURES: ' + ', '.join(fails) if fails else 'all good')
sys.exit(1 if fails else 0)
