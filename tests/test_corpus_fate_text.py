"""FFmpeg's FATE text subtitles (fate/sub in the corpus), opened by
Subtitld and checked against what FFmpeg itself makes of them: the
expected outputs in fate/ref, tests/ref/fate/sub-* at FFmpeg aa7c0978,
which tests/fate/subtitles.mak there maps to each sample and command.

Most of those outputs are the ASS FFmpeg converts the sample to, so what
is compared is the times (FFmpeg writes hundredths) and the plain text;
styling and places only where FFmpeg's ASS clearly says the same thing.
Where Subtitld reads a sample differently from FFmpeg on purpose, the
check says so and asserts Subtitld's reading against the file itself.
Each sample is then saved in its own format, opened again, and its
subtitles must have survived.

Standalone script, like the other suites here; it skips when the corpus
has not been fetched (python tests/corpus/fetch.py).
"""
import os, re, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, markup, session
from corpus_common import need

NAMES = ['1ededcbd7b.ass', 'AQTitle_capability_tester.aqt', 'JACOsub_capability_tester.jss',
         'MPL2_capability_tester.txt', 'MPSub_capability_tester.sub', 'MPSub_capability_tester_frames.sub',
         'MicroDVD_capability_tester.srt', 'MicroDVD_capability_tester.sub', 'PJS_capability_tester.pjs',
         'RealText_capability_tester.rt', 'SAMI_capability_tester.smi', 'SAMI_multilang_tweak_tester.smi',
         'STL_capability_tester.stl', 'SubRip_capability_tester.srt', 'SubViewer1_capability_tester.sub',
         'SubViewer_capability_tester.sub', 'VPlayer_capability_tester.txt', 'WebVTT_capability_tester.vtt',
         'WebVTT_extended_tester.vtt', 'WebVTT_timestamp_map.vtt', 'WebVTT_timestamp_map_block.vtt',
         'WebVTT_timestamp_map_invalid.vtt', 'WebVTT_timestamp_map_nolocal.vtt', 'WebVTT_timestamp_map_range.vtt',
         'a9-misc.ssa', 'badsyntax.srt', 'cp1251-subtitles.sub', 'empty-events-2167.srt', 'madness.srt',
         'test-lrc-ms.lrc', 'test-lrc.lrc', 'ticket5032-rrn.srt', 'witch.scc']
REFS = ['sub-aqtitle', 'sub-ass-to-ass-transcode', 'sub-charenc', 'sub-jacosub', 'sub-lrc-ms-ms-remux',
        'sub-lrc-remux', 'sub-microdvd', 'sub-mpl2', 'sub-mpsub', 'sub-mpsub-frames', 'sub-pjs', 'sub-realtext',
        'sub-sami', 'sub-sami2', 'sub-scc', 'sub-srt', 'sub-srt-badsyntax', 'sub-srt-empty-events',
        'sub-srt-madness-timeshift', 'sub-srt-rrn-remux', 'sub-ssa-to-ass-remux', 'sub-stl', 'sub-subviewer',
        'sub-subviewer1', 'sub-vplayer', 'sub-webvtt', 'sub-webvtt2', 'sub-webvtt-timestamp-map']
need(*[f'fate/sub/{name}' for name in NAMES], *[f'fate/ref/{name}' for name in REFS])
from corpus_common import CORPUS

SUB = CORPUS / 'fate' / 'sub'
REF = CORPUS / 'fate' / 'ref'
SCRATCH = Path(tempfile.mkdtemp())
FOREVER = 9 * 3600 + 59 * 60 + 59.99      # FFmpeg's end for a subtitle with none
fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}'[:400] + ('' if ok else f'   (want {want!r})'[:400]))
    if not ok:
        fails.append(name)


def lines_of(text):
    return '\n'.join(line.strip() for line in str(text).split('\n') if line.strip())


def ffmpeg(name, soft_breaks=False):
    """[(start, end, text)] of the Dialogue lines FFmpeg wrote to fate/ref/`name`:
    override blocks out, "\\N" a line break, "\\h" a hard space, "\\{" a brace,
    empty lines gone. With `soft_breaks`, "\\n" a space, as ASS shows it."""
    cues = []
    for line in (REF / name).read_text(encoding='utf-8').split('\n'):
        if not line.startswith('Dialogue:'):
            continue
        fields = line.split(',', 9)
        text = fields[9].replace('\\{', '\x00')
        text = re.sub(r'\{[^{}]*\}', '', text).replace('\x00', '{')
        text = text.replace('\\N', '\n').replace('\\h', ' ')
        if soft_breaks:
            text = text.replace('\\n', ' ')
        cues.append((clock(fields[1]), clock(fields[2]), lines_of(text)))
    return cues


def clock(value):
    """Seconds for "H:MM:SS.ss"; None for the "0:00:-2.-47" FFmpeg prints
    for a time before the one it was asked to start from."""
    if '-' in value:
        return None
    hours, minutes, seconds = value.split(':')
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def srt_ref(name, shift=0.0):
    """[(start, end, text)] of an SRT FFmpeg wrote, moved back by `shift`."""
    blocks = (REF / name).read_text(encoding='utf-8').replace('\r\n', '\n').split('\n\n')
    cues = []
    for block in blocks:
        lines = block.strip('\n').split('\n')
        if len(lines) < 2 or '-->' not in lines[1]:
            continue
        start, end = (stamp.strip().replace(',', '.') for stamp in lines[1].split('-->'))
        cues.append((round(clock(start) - shift, 3), round(clock(end) - shift, 3), lines_of(markup.plain('\n'.join(lines[2:])))))
    return cues


def opened(name, folder=SUB):
    """(segments, format) Subtitld opens the file with."""
    session.VIDEO = {}
    session.FORMAT = {}
    segments, fmt = file_io.process_subtitles_file(str(folder / name))[:2]
    return segments, fmt


def cues(segments, digits=2):
    return [(round(float(s['start']), digits), round(float(s['end']), digits), lines_of(markup.plain(s['text'])))
            for s in segments]


def close(one, other, tolerance=0.011):
    return abs(one - other) <= tolerance


def same(name, got, want, ends=True, tolerance=0.011):
    """Cue by cue: the same count, start, end (unless not `ends`), text."""
    if len(got) != len(want):
        check(f'{name}: as many subtitles', len(got), len(want))
        return
    for index, (ours, theirs) in enumerate(zip(got, want)):
        good = close(ours[0], theirs[0], tolerance) and (not ends or close(ours[1], theirs[1], tolerance)) \
            and ours[2] == theirs[2]
        if not good:
            check(f'{name}: subtitle {index + 1}', ours, theirs)
            return
    check(f'{name}: {len(got)} subtitles, their times and text', True, True)


def round_trip(name, segments, fmt, extension, tolerance=0.011, digits=2):
    """Save `segments` as `fmt` and open them again: the same subtitles
    (in order of start, then end and text, for those starting together).
    The options opening the file set (its frame rate, its LRC times) stay,
    as they do when a file is opened and saved in Subtitld."""
    path = SCRATCH / f'{Path(name).stem}.{extension}'
    session.SUBTITLE['segments'] = [dict(segment) for segment in segments]
    session.VIDEO = {}
    file_io.save_file(str(path), fmt)
    session.SUBTITLE['segments'] = []
    again, found = opened(path.name, SCRATCH)
    check(f'{name}: saved as {fmt}, opened as {fmt}', found, fmt)
    same(f'{name}: saved and opened again', sorted(cues(again, digits)), sorted(cues(segments, digits)),
         tolerance=tolerance)
    return again


# --------------------------------------------------------------------------
print('AQTitle (sub-aqtitle)')
segments, fmt = opened('AQTitle_capability_tester.aqt')
check('opened as AQTitle', fmt, 'AQTITLE')
want = ffmpeg('sub-aqtitle')
# An empty "-->> N" marker ends the subtitle before it (FFmpeg: 3:29.92 to
# 3:31.28, not to the next subtitle's 3:33.36). FFmpeg was told the file is
# Windows-1250 (-sub_charenc cp1250); Subtitld guesses it, Czech letters
# and all ("pořádku").
same('times and text', cues(segments), want)
round_trip('AQTitle', segments, 'AQTITLE', 'aqt', tolerance=0.041)

# --------------------------------------------------------------------------
print('JACOsub (sub-jacosub)')
segments, fmt = opened('JACOsub_capability_tester.jss')
check('opened as JACOsub', fmt, 'JACOSUB')
want = ffmpeg('sub-jacosub')
# "#S 0.12" shifts the whole script by 12 units of its TIMERES 100, the
# lines ending in "\" go on on the next, "@0007600" is a time in units, and
# {comments} are not shown (FFmpeg keeps them in its ASS, where braces hide
# them, so its plain text lacks them, leaving the two spaces around one).
same('times and text', cues(segments), [(start, end, re.sub(' +', ' ', text)) for start, end, text in want])
# FFmpeg's {\anN} for VT, VM, JL, JR ...; none for VB and JC, the defaults.
check('places as FFmpeg gives them', [markup.alignment(segment['text']) for segment in segments],
      [5, 8, 5, 2, 5, 1, 2, 3, 2, 5, 2])
# FFmpeg: "(Normal, {\b1}Bold{\r}, {\i1}Italic{\r})": \B, \I and \N (normal).
check('\\B bold, \\I italic, \\N back to normal', segments[8]['text'],
      'Text may appear in different styles\n(Normal, <b>Bold</b>, <i>Italic</i>)')
round_trip('JACOsub', segments, 'JACOSUB', 'jss')

# --------------------------------------------------------------------------
print('MPL2 (sub-mpl2)')
segments, fmt = opened('MPL2_capability_tester.txt')
check('opened as MPL2', fmt, 'MPL2')
# "[41][]" lasts until the next subtitle (FFmpeg: 4.10 to 5.30).
same('times and text', cues(segments), ffmpeg('sub-mpl2'))
# FFmpeg: "{\i1}italic{\r}\N{\b1}bold{\r}\N{\b1}{\i1}italicbold" and
# "{\u1}underline{\r}\Nnormal": "/" italic, "\" bold, "_" underline.
check('/ italic, \\ bold, both', segments[1]['text'], '<i>italic</i>\n<b>bold</b>\n<i><b>italicbold</b></i>')
check('_ underline', segments[2]['text'], '<u>underline</u>\nnormal')
again = round_trip('MPL2', segments, 'MPL2', 'mpl')
check('MPL2: the styles saved too', [markup.spans(segment['text']) for segment in again[1:3]],
      [markup.spans(segment['text']) for segment in segments[1:3]])

# --------------------------------------------------------------------------
print('MPSub (sub-mpsub, sub-mpsub-frames)')
segments, fmt = opened('MPSub_capability_tester.sub')
check('opened as MPSub', fmt, 'MPSUB')
same('in seconds: times and text', cues(segments), ffmpeg('sub-mpsub'))
round_trip('MPSub', segments, 'MPSUB', 'sub')
segments, fmt = opened('MPSub_capability_tester_frames.sub')
# "FORMAT=25 #fps": a rate, the rest a comment (FFmpeg: 1.00 to 2.50).
same('in frames: times and text', cues(segments), ffmpeg('sub-mpsub-frames'))
round_trip('MPSub in frames', segments, 'MPSUB', 'sub')

# --------------------------------------------------------------------------
print('PJS (sub-pjs)')
segments, fmt = opened('PJS_capability_tester.pjs')
check('opened as PJS', fmt, 'PJS')
same('times and text', cues(segments), ffmpeg('sub-pjs'))
round_trip('PJS', segments, 'PJS', 'pjs')

# --------------------------------------------------------------------------
print('VPlayer (sub-vplayer)')
segments, fmt = opened('VPlayer_capability_tester.txt')
check('opened as VPlayer', fmt, 'VPLAYER')
want = ffmpeg('sub-vplayer')
# "0:00:00.12 Hello", a space after the clock, is a subtitle too. FFmpeg
# shows the last one forever; Subtitld for the three seconds it gives any
# subtitle with no end.
same('times and text', cues(segments)[:-1], want[:-1])
check('the last one', cues(segments)[-1], (want[-1][0], round(want[-1][0] + 3, 2), want[-1][2]))
# VPlayer is written in whole seconds, as its players read it.
path = SCRATCH / 'vplayer.txt'
session.SUBTITLE['segments'] = [dict(segment) for segment in segments]
file_io.save_file(str(path), 'VPLAYER')
again, found = opened('vplayer.txt', SCRATCH)
check('VPlayer: saved and opened again, to the second',
      (found, [(int(s['start']), s['text']) for s in again]),
      ('VPLAYER', [(int(s['start']), s['text']) for s in segments]))

# --------------------------------------------------------------------------
print('SubViewer 1 (sub-subviewer1)')
segments, fmt = opened('SubViewer1_capability_tester.sub')
check('opened as SubViewer 1', fmt, 'SUBVIEWER1')
want = ffmpeg('sub-subviewer1')
# "[DELAY] 4" moves every subtitle four seconds (FFmpeg: 3:45, not the
# file's 3:41), and the last one, with no closing mark, is kept: FFmpeg
# shows it forever, Subtitld for three seconds.
same('times and text', cues(segments)[:-1], want[:-1])
check('the last one', cues(segments)[-1], (want[-1][0], round(want[-1][0] + 3, 2), want[-1][2]))
round_trip('SubViewer 1', segments, 'SUBVIEWER1', 'sub')

# --------------------------------------------------------------------------
print('SubViewer 2 (sub-subviewer)')
segments, fmt = opened('SubViewer_capability_tester.sub')
# Subtitld reads SubViewer 2 as SBV, which is YouTube's SubViewer.
check('opened as SBV', fmt, 'SBV')
# "[SIZE]20" alone on a line is a style code, not text (FFmpeg drops it);
# the leading "[br]" is an empty first line, which Subtitld leaves out.
same('times and text', cues(segments), ffmpeg('sub-subviewer'))
round_trip('SubViewer 2', segments, 'SBV', 'sub')

# --------------------------------------------------------------------------
print('RealText (sub-realtext)')
segments, fmt = opened('RealText_capability_tester.rt')
check('opened as RealText', fmt, 'REALTEXT')
want = ffmpeg('sub-realtext')
# The text before the first <time> shows from the start. FFmpeg ends each
# subtitle the window's duration="15" after it starts (and the first at 0);
# Subtitld runs each until the next <time>, as the window shows it, and the
# one with end="23" until then.
same('starts and text', cues(segments), want, ends=False)
check('ends: the next <time>, or its own end', [cue[1] for cue in cues(segments)], [3.0, 6.99, 9.0, 23.0, 15.34])
round_trip('RealText', segments, 'REALTEXT', 'rt', tolerance=0.06, digits=1)

# --------------------------------------------------------------------------
print('Spruce STL (sub-stl)')
segments, fmt = opened('STL_capability_tester.stl')
# Its "$Bold = TRUE" settings make it DVD Studio Pro's flavour of Spruce.
check('opened as DVD Studio Pro', fmt, 'DVDSTUDIOPRO')
want = ffmpeg('sub-stl')
# FFmpeg takes the last field of "00:00:31:02" for hundredths; Spruce's
# own and Subtitle Edit's reading is frames, at 25 when there is no video:
# FFmpeg's 31.02 is 31 seconds and 2 frames here.
in_frames = [(int(start) + round((start - int(start)) * 100) / 25, int(end) + round((end - int(end)) * 100) / 25, text)
             for start, end, text in want]
same('times (as frames) and text', cues(segments), in_frames)
# The file's settings, as they change between its lines: bold and italic,
# then "$Italic = FALSE", then "$UnderLined = TRUE"; "$HorzAlign = Left"
# until "$HorzAlign = Center" before the 15th.
check('bold and italic', segments[0]['text'], '{\\an1}<b><i>Hello, my name is Axel Kornmesser.</i></b>')
check('then not italic', segments[3]['text'], '{\\an1}<b>was our second collaboration</b>')
check('then underlined', segments[4]['text'], '{\\an1}<u><b>after a great\nexperience in 2005,</b></u>')
check('left until centred', [markup.alignment(segment['text']) for segment in segments], [1] * 14 + [2] * 3)
round_trip('Spruce STL', segments, 'DVDSTUDIOPRO', 'stl', tolerance=0.041)

# --------------------------------------------------------------------------
print('SAMI (sub-sami)')
segments, fmt = opened('SAMI_capability_tester.smi')
check('opened as SAMI', fmt, 'SAMI')
want = ffmpeg('sub-sami')
# FFmpeg puts the Source paragraph ("Pres. John F. Kennedy") over each
# caption, and shows it alone for the first 10 ms; Subtitld makes it the
# speaker of the captions after it. The last caption lasts until the
# Metrics' duration (73 s, its own start) plus four seconds, FFmpeg's forever.
check('the speaker', [segment.get('speaker') for segment in segments], ['Pres. John F. Kennedy'] * 7 + ['End of:'])
same('times, and the speaker and text', [(start, end, f"{s['speaker']}\n{text}")
                                         for (start, end, text), s in zip(cues(segments), segments)][:-1], want[1:-1])
check('the last one', cues(segments)[-1], (73.0, 77.0, 'President John F. Kennedy Speech'))
path = SCRATCH / 'sami.smi'
session.SUBTITLE['segments'] = [dict(segment) for segment in segments]
session.FORMAT = {'options': {'speakers': 'source'}}
file_io.save_file(str(path), 'SAMI')
again, found = opened('sami.smi', SCRATCH)
same('SAMI: saved and opened again', cues(again), cues(segments))

print('SAMI, two languages (sub-sami2)')
segments, fmt = opened('SAMI_multilang_tweak_tester.smi')
want = ffmpeg('sub-sami2')
# FFmpeg gives the ENCC captions their times and the KRCC ones none (each
# shows at its start for no time at all, its own end being an &nbsp; sync
# FFmpeg pairs with the English). The English, defined first, are
# Subtitld's subtitles; the Korean their translations.
english = [cue for cue in want if cue[1] > cue[0]]
same('the English: times and text', cues(segments), english)
korean = {start: text for start, end, text in want if end == start}
check('a Korean caption as the translation', lines_of(markup.plain(segments[1]['translations']['ko-kr'])),
      korean[10.11])
check('<font color="purple">', markup.spans(segments[1]['text'])[0], ('The', markup.PLAIN._replace(color='#800080', bold=True)))
round_trip('SAMI, two languages', segments, 'SAMI', 'smi')

# --------------------------------------------------------------------------
print('SubRip (sub-srt)')
segments, fmt = opened('SubRip_capability_tester.srt')
check('opened as SRT', fmt, 'SRT')
want = ffmpeg('sub-srt')
same('times', [cue[:2] + ('',) for cue in cues(segments)], [cue[:2] + ('',) for cue in want])
# FFmpeg hides tags it does not know (<invalid_tag>, <wwww>), MicroDVD's
# {Y:...} codes and ASS's \h; Subtitld keeps text it does not understand
# as written, so those seven differ. The rest is the same text.
differ = [index for index, (ours, theirs) in enumerate(zip(cues(segments), want)) if ours[2] != theirs[2]]
check('the same text but where tags it does not know are', differ, [6, 7, 28, 29, 30, 31, 32, 33, 34, 35])
# Saving leaves out a subtitle that ends at 0, which no player shows: the
# first one, "Don't show this text", from 0 to 0.
round_trip('SubRip', [segment for segment in segments if segment['end'] > 0], 'SRT', 'srt')

print('SubRip, bad syntax (sub-srt-badsyntax)')
segments, fmt = opened('badsyntax.srt')
want = ffmpeg('sub-srt-badsyntax')
# "<E9>pisode" (a byte written out as a tag): FFmpeg hides it as a tag.
check('<E9> kept as written', cues(segments)[4][2].split('\n')[1], '<E9>pisode 4')
same('times and text', [cue for index, cue in enumerate(cues(segments)) if index != 4],
     [cue for index, cue in enumerate(want) if index != 4])
round_trip('SubRip, bad syntax', segments, 'SRT', 'srt')

print('SubRip, empty subtitles (sub-srt-empty-events)')
segments, fmt = opened('empty-events-2167.srt')
want = srt_ref('sub-srt-empty-events')
# Subtitld keeps a subtitle with no text, timed, for the editor to fill;
# FFmpeg drops it. Saved, it goes (SRT has no empty subtitles), and what
# is written is what FFmpeg writes.
check('the empty ones kept', [cue[2] for cue in cues(segments)].count(''), 3)
same('the others', [cue for cue in cues(segments, 3) if cue[2]], want, tolerance=0.0011)
path = SCRATCH / 'empty.srt'
session.SUBTITLE['segments'] = [dict(segment) for segment in segments]
file_io.save_file(str(path), 'SRT')
same('saved: as FFmpeg writes it', cues(opened('empty.srt', SCRATCH)[0], 3), want, tolerance=0.0011)

print('SubRip, madness (sub-srt-madness-timeshift, moved back by its 3.14 s)')
segments, fmt = opened('madness.srt')
want = srt_ref('sub-srt-madness-timeshift', shift=3.14)
# The times: "00:00:07,777 --> 00:00:06,666", ending before it starts,
# lasts until the next subtitle, as FFmpeg has it.
same('times', [cue[:2] + ('',) for cue in cues(segments, 3)], [cue[:2] + ('',) for cue in want], tolerance=0.0011)
# FFmpeg takes any line beginning with a number just before a timing for
# that subtitle's number ("21 lol jk", "44 yes we can", '0 now this is the
# "id" of the next event'); Subtitld only a number alone, so those three
# lines stay in the text. Everything else is the same.
differ = [index for index, (ours, theirs) in enumerate(zip(cues(segments), want)) if ours[2] != theirs[2]]
check('the same text but those three lines', differ, [0, 3, 4])
check('...which are the only difference', [lines_of('\n'.join(line for line in ours[2].split('\n') if line not in (
    '21 lol jk', '44 yes we can', '0 now this is the "id" of the next event'))) for ours in cues(segments)],
    [theirs[2] for theirs in want])
round_trip('SubRip, madness', segments, 'SRT', 'srt')

print('SubRip, \\r\\r\\n line ends (sub-srt-rrn-remux)')
segments, fmt = opened('ticket5032-rrn.srt')
want = srt_ref('sub-srt-rrn-remux')
check('the last, empty, kept', cues(segments)[-1][2], '')
same('the others', [cue for cue in cues(segments, 3) if cue[2]], want, tolerance=0.0011)
round_trip('SubRip, \\r\\r\\n', [segment for segment in segments if segment['text']], 'SRT', 'srt')

# --------------------------------------------------------------------------
print('WebVTT (sub-webvtt, sub-webvtt2)')
segments, fmt = opened('WebVTT_capability_tester.vtt')
check('opened as WebVTT', fmt, 'VTT')
same('times and text', cues(segments), ffmpeg('sub-webvtt'))
round_trip('WebVTT', segments, 'VTT', 'vtt')
segments, fmt = opened('WebVTT_extended_tester.vtt')
want = ffmpeg('sub-webvtt2')
same('extended: times', [cue[:2] + ('',) for cue in cues(segments)], [cue[:2] + ('',) for cue in want])
# FFmpeg flattens a ruby, its reading after its base ("愛あい"), and drops
# voices; Subtitld keeps the ruby, and names the second voice of a cue
# that has two ("<v.loud Yumiko>") in its text, the first being its speaker.
check('a ruby kept as one', segments[6]['text'].split('\n')[0], '{\\an4}I want to <ruby>愛<rt>あい</rt></ruby> love you')
check('the cue\'s second voice', (segments[6].get('speaker'), segments[6]['text'].split('\n')[1]),
      ('Aika', "Yumiko: That's not proper English!"))
differ = [index for index, (ours, theirs) in enumerate(zip(cues(segments), want)) if ours[2] != theirs[2]]
check('extended: the same text but those two', differ, [6, 7])
round_trip('WebVTT extended', segments, 'VTT', 'vtt')

print('WebVTT, X-TIMESTAMP-MAP (sub-webvtt-timestamp-map)')
# FFmpeg applies the map only when told to (-timestamp_map 1); without it,
# its stream 5, the file's own times: 1 s and 3 s, lasting 1 s and 0.5 s
# (framecrc, milliseconds). Subtitld does not apply it either: the map
# ties a segment of a stream to the stream's clock, not to a video file.
crc = [line.split(',') for line in (REF / 'sub-webvtt-timestamp-map').read_text().split('\n')
       if line.startswith('5,')]
segments, fmt = opened('WebVTT_timestamp_map.vtt')
check('map: the times FFmpeg reads unmapped', [(s['start'], s['end']) for s in segments],
      [(int(row[1]) / 1000, (int(row[1]) + int(row[3])) / 1000) for row in crc])
# "X-TIMESTAMP-MAP" alone on the line before a cue is its identifier (FFmpeg:
# "WebVTT ID", 15 bytes), and "line:1" puts the second at the top.
check('map: the text, the identifier no part of it', [s['text'] for s in segments], ['one', '{\\an8}two'])
for name, text in (('block', 'three'), ('invalid', 'four'), ('nolocal', 'five'), ('range', 'six')):
    segments, fmt = opened(f'WebVTT_timestamp_map_{name}.vtt')
    times = (2.5, 3.0) if name == 'block' else (1.0, 2.0)
    check(f'{name}: the times as written', [(s['start'], s['end'], s['text']) for s in segments], [times + (text,)])

# --------------------------------------------------------------------------
print('LRC (sub-lrc-remux, sub-lrc-ms-ms-remux)')
for name, ref, digits in (('test-lrc.lrc', 'sub-lrc-remux', 2), ('test-lrc-ms.lrc', 'sub-lrc-ms-ms-remux', 3)):
    segments, fmt = opened(name)
    check(f'{name}: opened as LRC', fmt, 'LRC')
    lines = [re.match(r'\[(-?)(\d+):(\d+\.\d+)\](.*)', line) for line in
             (REF / ref).read_text(encoding='utf-8').split('\n')]
    lines = [((-1 if m.group(1) else 1) * (int(m.group(2)) * 60 + float(m.group(3))), m.group(4)) for m in lines if m]
    lines = [(round(seconds, digits), text) for seconds, text in lines]
    # FFmpeg writes a line a time, the repeated ones apart, in order. Each
    # line lasts until the next time (an empty one too). The one at
    # -00:01.02 (FFmpeg keeps it) is before the start: Subtitld leaves it out.
    shown = [(seconds, text) for seconds, text in lines if text and seconds >= 0]
    check(f'{name}: the times and lines', [(round(s['start'], digits), s['text']) for s in segments], shown)
    nexts = [seconds for seconds, _text in lines if seconds >= 0]
    check(f'{name}: each until the next time', [round(s['end'], digits) for s in segments],
          [nexts[nexts.index(round(s['start'], digits)) + 1] for s in segments])
    round_trip(f'LRC {name}', segments, 'LRC', 'lrc', tolerance=0.0011 if digits == 3 else 0.011, digits=digits)

# --------------------------------------------------------------------------
print('SCC (sub-scc)')
segments, fmt = opened('witch.scc')
check('opened as SCC', fmt, 'SCC')
# A pop-on caption shows when its End Of Caption (942f) arrives: the
# line's timecode, plus a frame for each word before it (CEA-608 sends one
# a frame, at 29.97). FFmpeg dates the caption by the line's timecode.
shown = set()
for line in (SUB / 'witch.scc').read_text(encoding='utf-8').split('\n'):
    found = re.match(r'(\d\d):(\d\d):(\d\d)[:;](\d\d)\s+(.*)', line)
    if found and '942f' in found.group(5).split():
        hours, minutes, seconds, frames = map(int, found.groups()[:4])
        frame = (hours * 3600 + minutes * 60 + seconds) * 30 + frames + found.group(5).split().index('942f')
        shown.add(round(frame * 1001 / 30000, 3))
check('every caption shows at its End Of Caption',
      all(any(abs(s['start'] - at) < 0.002 for at in shown) for s in segments), True)
check('...and nearly every End Of Caption is one', len(segments) >= len(shown) - 1, True)
# The text, against FFmpeg's from 57 s on (it read from -ss 57): the same
# captions in the same order. FFmpeg writes CEA-608's apostrophe as "’"
# and keeps the spaces a row starts with; compared without either.
want = [re.sub(r'\s+', ' ', text.replace('’', "'")).strip() for start, end, text in ffmpeg('sub-scc')]
want = want[1:]                     # the first began before 57 s, and Subtitld has it whole
got = [re.sub(r'\s+', ' ', text).strip() for start, end, text in cues(segments) if start >= 57]
check('from 57 s: the same captions', got[:len(want)], want)
# FFmpeg's places: row 15 and 14 at column 0 is {\pos(38,...)}, bottom left.
check('a caption on the left', segments[[cue[2] for cue in cues(segments)].index('BRING HER FORWARD.')]['text'],
      '{\\an1}BRING HER FORWARD.')
round_trip('SCC', segments, 'SCC', 'scc', tolerance=0.04)

# --------------------------------------------------------------------------
print('ASS and SSA (sub-ass-to-ass-transcode, sub-ssa-to-ass-remux)')
segments, fmt = opened('1ededcbd7b.ass')
check('opened as ASS', fmt, 'ASS')
same('ASS: times and text', cues(segments), ffmpeg('sub-ass-to-ass-transcode'))
round_trip('ASS', segments, 'ASS', 'ass')
segments, fmt = opened('a9-misc.ssa')
# FFmpeg copies the events in the file's order; Subtitld sorts them by
# start. "\n" is ASS's soft break, a space unless the script wraps lines
# that way; this one does not.
want = sorted(ffmpeg('sub-ssa-to-ass-remux', soft_breaks=True), key=lambda cue: cue[0])
same('SSA: times and text', sorted(cues(segments), key=lambda cue: cue[0]), want)
round_trip('SSA', segments, 'ASS', 'ass')

# --------------------------------------------------------------------------
print('MicroDVD (sub-microdvd, sub-charenc)')
for name in ('MicroDVD_capability_tester.sub', 'MicroDVD_capability_tester.srt'):
    segments, fmt = opened(name)
    # The .srt is the same MicroDVD under another name, which FFmpeg reads
    # by its lines as MicroDVD.
    check(f'{name}: opened as MicroDVD', fmt, 'SUB')
    want = ffmpeg('sub-microdvd')
    # "{0}{}25.000 FPS" declares the rate (no subtitle); the last, with no
    # end, FFmpeg shows forever and Subtitld for four seconds.
    same(f'{name}: times and text', cues(segments)[:-1], want[:-1])
    check(f'{name}: the last one', cues(segments)[-1], (want[-1][0], want[-1][0] + 4, want[-1][2]))
    # FFmpeg: {\c&H345678&}foo, ASS's BGR as MicroDVD's {c:$345678}.
    check(f'{name}: colours', [run for run in markup.spans(segments[0]['text']) if run[0].strip()][:2],
          [('foo', markup.PLAIN._replace(color='#785634')), ('bar', markup.PLAIN._replace(color='#efcdab'))])
    # FFmpeg: {\u1}{\s1}{\i1}{\b1} for the first line, {\s0}{\u0} after it:
    # {Y:ib} is the whole subtitle's, {y:us} the line's.
    check(f'{name}: {{Y:...}} and {{y:...}}', segments[1]['text'],
          '<i><b><u><s>italic bold underline strike</s></u>\nitalic bold no-underline no-strike</b></i>')
round_trip('MicroDVD', opened('MicroDVD_capability_tester.sub')[0], 'SUB', 'sub', tolerance=0.041)

segments, fmt = opened('cp1251-subtitles.sub')
want = ffmpeg('sub-charenc')
# The file declares no rate: FFmpeg counts its frames at 23.976, Subtitld
# with no video at 25. Compared as the frames they both read; the text is
# Windows-1251, as FFmpeg was told (-sub_charenc cp1251), and keeps its
# spaces ("Т И Г Ъ Р   И   Д Р А К О Н").
same('frames and text', [(round(start * 25), round(end * 25), text) for start, end, text in cues(segments, 3)],
     [(round(start * 24000 / 1001), round(end * 24000 / 1001), text) for start, end, text in want], tolerance=0)
round_trip('MicroDVD, Windows-1251', segments, 'SUB', 'sub', tolerance=0.041)

# --------------------------------------------------------------------------
print()
if fails:
    print(f'{len(fails)} FAILED: {fails}')
    sys.exit(1)
print('all good')
