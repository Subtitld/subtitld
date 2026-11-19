"""Real styled and encoded subtitles from the corpus (tests/corpus):
pysubs2's Aegisub files with attached fonts and pictures and its TTML
examples (with the ASS pysubs2 makes of each beside them), and Subtitle
Edit's files for charset detection, Apple TV+'s WebVTT, MacCaption and an
SCC. Each is opened through file_io, compared with what its project's own
tests say of it or with what the file plainly holds, saved in its own
format and opened again.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. It passes, skipping, when the corpus has
not been fetched.
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
from subtitld.modules import file_io, karaoke, markup, session
from corpus_common import need

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


SCRATCH = Path(tempfile.mkdtemp())


def opened(path):
    """(subtitles, format) of a file opened as the app opens one, from a
    fresh project."""
    session.SUBTITLE = {'segments': []}
    session.FORMAT = {}
    session.VIDEO = {}
    session.SPEAKERS.clear()
    segments, fmt = file_io.process_subtitles_file(str(path))
    session.SUBTITLE['segments'] = segments
    return segments, fmt


def again(name, fmt):
    """The project saved as `fmt` (with the options opening it set) and
    opened again."""
    out = SCRATCH / name
    file_io.save_file(str(out), fmt)
    return opened(out)


def plain(segment):
    return markup.plain(segment['text']).strip()


def timed(segments, places=3):
    return [(round(s['start'], places), round(s['end'], places), s['text']) for s in segments]


def styled(segments):
    """(start, end, placement, runs of text and their style) of each: what
    is shown, however the tags that say it are nested."""
    return [(round(s['start'], 3), round(s['end'], 3), markup.split_lead(s['text'])[0],
             markup.spans(markup.split_lead(s['text'])[1])) for s in segments]


def close(got, want, within):
    """Whether two lists of (start, end, text) match, the times within `within` seconds."""
    return len(got) == len(want) and all(
        g[2] == w[2] and abs(g[0] - w[0]) <= within and abs(g[1] - w[1]) <= within for g, w in zip(got, want))


def ass_events(path):
    """(start, end, text) of an ASS file's Dialogue lines, read here on
    their own, as the reference to hold Subtitld's reading against."""
    out = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('Dialogue:'):
            fields = line.split(':', 1)[1].split(',', 9)
            h, m, s = fields[1].strip().split(':')
            start = int(h) * 3600 + int(m) * 60 + float(s)
            h, m, s = fields[2].strip().split(':')
            out.append((start, int(h) * 3600 + int(m) * 60 + float(s), fields[9].replace('\\N', '\n')))
    return out


NAMES = ['pysubs2/' + name for name in (
    'ttml_example.ttml', 'ttml_example.ass', 'ttml_example2.ttml', 'ttml_example2.ass', 'ttml_example_apple.ttml',
    'ttml_example_apple.ass', 'subtitle_with_attached_fonts_aegisub.ass', 'subtitle_with_attached_fonts_no_events.ass',
    'subtitle_with_attached_fonts_pysubs2_ref.ass', 'subtitle_with_attached_images_aegisub.ass',
    'subtitle_with_attached_images_pysubs2_ref.ass', 'EBGaramond08-Regular.ttf', 'EBGaramond08-Italic.ttf')] + ['subtitleedit/' + name for name in (
        'auto_detect_Danish.srt', 'auto_detect_Russian.srt', 'auto_detect_windows-1250.srt',
        'auto_detect_windows-1251.srt', 'auto_detect_windows-1254.srt', 'sample_WebVTT_AppleTV.webvtt',
        'sample_mcc_cea608_only.mcc', 'sample_mcc_cea608_708.mcc', 'scc_16038_pop_on.scc')]
found = dict(zip(NAMES, need(*NAMES)))

print('pysubs2: TTML examples against the ASS pysubs2 makes of them')
# pysubs2's test_ttml: each .ttml converts to the .ass beside it (times to
# the hundredth, so within 5 ms here; text plain, \N a line break).
for name, count in (('ttml_example', 11), ('ttml_example2', 10), ('ttml_example_apple', 38)):
    segments, fmt = opened(found[f'pysubs2/{name}.ttml'])
    reference = ass_events(found[f'pysubs2/{name}.ass'])
    got = [(s['start'], s['end'], plain(s)) for s in segments]
    check(f'{name}: {count} subtitles, the times and words pysubs2 reads', (fmt, len(segments), close(got, reference, 0.0051)),
          ('TTML', count, True))
    if not close(got, reference, 0.0051):
        print('     got', got[:3], '\n     want', reference[:3])
    saved, _fmt = again(f'{name}.ttml', 'TTML')
    check(f'{name}: saved as TTML and opened again', timed(saved), timed(segments))

example, _fmt = opened(found['pysubs2/ttml_example.ttml'])
# The file: paragraphs in style s2 (and s2Left, which is based on it) are
# yellow, s1Right ones go to the right ("end"), under a region shown at
# the bottom (displayAlign "after"), the region's background black.
check('ttml_example: s2 paragraphs yellow', [plain(s) for s in example if '<font color="#ffff00">' in s['text']],
      ['It is puzzling, why is it\nwe do not see things upside-down?', 'No indeed! What a beautiful fact!',
       'But how is it proved?', 'How truly delightful!'])
check('ttml_example: s1Right ones bottom right', [plain(s) for s in example if s['text'].startswith('{\\an3}')],
      ['Thus: what we call', 'the vertex of the Brain\nis really its base', 'and what we call its base\nis really its vertex,'])
# s2Left, "a style based on s2 but justified to the left" as the file's own
# comment says, textAlign "start": bottom left. (pysubs2's .ass beside it
# keeps no alignment at all, so it says nothing here.)
check('ttml_example: the s2Left one bottom left', [plain(s) for s in example if s['text'].startswith('{\\an1}')],
      ['But how is it proved?'])
check('ttml_example: the region\'s black background', {s.get('look', {}).get('background') for s in example}, {'#ff000000'})
example2, _fmt = opened(found['pysubs2/ttml_example2.ttml'])
# The file: subtitles 1 and 3 in region "top" (displayAlign "before").
check('ttml_example2: the ones in the top region at the top', [plain(s) for s in example2 if s['text'].startswith('{\\an8}')],
      ['TOP SAMPLE TEXT', 'Top Hello'])
apple, _fmt = opened(found['pysubs2/ttml_example_apple.ttml'])
# The file: Apple Music lyrics (itunes:timing="Word"), every line by
# ttm:agent v1, whose ttm:name is Radiohead; times in bare seconds, from
# the start of the song.
check('Apple lyrics: the agent\'s name the speaker', {s.get('speaker') for s in apple}, {'Radiohead'})
check('Apple lyrics: first and last line', [(s['start'], s['end'], plain(s)) for s in (apple[0], apple[-1])],
      [(19.764, 21.9, 'When you were here be fore'), (227.741, 230.693, "I don't be long here")])
# Each <span begin end> a word's time, as karaoke codes (modules.karaoke):
# the spans of the first and the last <p>, to the hundredth the codes keep.
# Every span stands on a line of its own, so whitespace parts all of them,
# and "be" "fore" stay two words, as TTML's whitespace says (and pysubs2
# reads them).
spans = [[(19.764, 20.105, 'When'), (20.105, 20.326, 'you were'), (20.326, 20.607, 'here'), (20.607, 20.841, 'be'),
          (20.841, 21.9, 'fore')],
         [(227.741, 228.24, 'I'), (228.24, 228.609, "don't"), (228.609, 228.859, 'be'), (228.859, 229.695, 'long'),
          (229.695, 230.693, 'here')]]
words = [[(start, end, word.strip()) for start, end, word in karaoke.timed(s['text'], s['start'])] for s in (apple[0], apple[-1])]
check('Apple lyrics: every word timed as its span, within the hundredth the codes keep',
      [[(word, abs(start - want[0]) <= 0.011 and abs(end - want[1]) <= 0.011) for (start, end, word), want in zip(line, wanted)]
       for line, wanted in zip(words, spans)],
      [[(word, True) for _start, _end, word in wanted] for wanted in spans])

print('pysubs2: Aegisub files with attached fonts and pictures')
# pysubs2's test_attachment: the Aegisub file and pysubs2's own writing of
# it are the same subtitles; one event, 0:00:00.00-0:00:05.00
# "Test subtitle." (the files); the third file has the fonts and no events.
for name in ('subtitle_with_attached_fonts_aegisub.ass', 'subtitle_with_attached_fonts_pysubs2_ref.ass'):
    fonts, fmt = opened(found['pysubs2/' + name])
    check(f'{name}: the one event, the fonts\' encoded lines not read as any',
          (fmt, timed(fonts)), ('ASS', [(0.0, 5.0, 'Test subtitle.')]))
saved, _fmt = again('fonts.ass', 'ASS')
check('saved as ASS and opened again', timed(saved), [(0.0, 5.0, 'Test subtitle.')])
check('no events: none read from the fonts', opened(found['pysubs2/subtitle_with_attached_fonts_no_events.ass'])[0], [])
# Its one event has no text: nothing to show, so no subtitle.
for name in ('subtitle_with_attached_images_aegisub.ass', 'subtitle_with_attached_images_pysubs2_ref.ass'):
    check(f'{name}: the pictures not read as events', opened(found['pysubs2/' + name])[0], [])


def section(path, name):
    """The lines of a script's [Fonts] or [Graphics] section, as text."""
    lines = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').split('\n')
    start = lines.index(f'[{name}]')
    end = next(index for index in range(start + 1, len(lines)) if lines[index].startswith('['))
    return '\n'.join(lines[start:end]).strip()


print('pysubs2: the attached fonts and pictures kept')
# The fonts Aegisub attached are pysubs2's EBGaramond08-Regular.ttf and
# -Italic.ttf (beside them in its test data, the same sizes), named with
# Aegisub's "_0": decoded, byte for byte those files.
fonts, _fmt = opened(found['pysubs2/subtitle_with_attached_fonts_aegisub.ass'])
kept = session.SUBTITLE.get('attachments') or {}
check('the fonts decoded are the font files', {name: data == found['pysubs2/' + name.replace('_0', '')].read_bytes()
                                               for name, data in (kept.get('fonts') or {}).items()},
      {'EBGaramond08-Regular_0.ttf': True, 'EBGaramond08-Italic_0.ttf': True})
# pysubs2's test_attachment: what it writes of the Aegisub file is its
# _pysubs2_ref.ass, the [Fonts] section byte for byte (names sorted, the
# lines as they came). Subtitld writes its own script around it.
again('fonts.ass', 'ASS')
check('saved: the [Fonts] section pysubs2\'s reference has', section(SCRATCH / 'fonts.ass', 'Fonts'),
      section(found['pysubs2/subtitle_with_attached_fonts_pysubs2_ref.ass'], 'Fonts'))
opened(found['pysubs2/subtitle_with_attached_images_aegisub.ass'])
again('images.ass', 'ASS')
check('saved: the [Graphics] section pysubs2\'s reference has', section(SCRATCH / 'images.ass', 'Graphics'),
      section(found['pysubs2/subtitle_with_attached_images_pysubs2_ref.ass'], 'Graphics'))
check('a picture decoded: a PNG and a JPEG', sorted((name, data[:4]) for name, data in
                                                   (session.SUBTITLE.get('attachments') or {})['graphics'].items()),
      [('github.jpg', b'\xff\xd8\xff\xe0'), ('github.png', b'\x89PNG')])
# A USFX keeps them as members of its own, and an ASS saved from it has
# them again.
opened(found['pysubs2/subtitle_with_attached_fonts_aegisub.ass'])
again('fonts.usfx', 'USFX')
check('a USFX keeps the fonts', {name: len(data) for name, data in (session.SUBTITLE.get('attachments') or {})['fonts'].items()},
      {'EBGaramond08-Regular_0.ttf': 280540, 'EBGaramond08-Italic_0.ttf': 180220})
again('from_usfx.ass', 'ASS')
check('an ASS saved from the USFX: the same [Fonts]', section(SCRATCH / 'from_usfx.ass', 'Fonts'),
      section(found['pysubs2/subtitle_with_attached_fonts_pysubs2_ref.ass'], 'Fonts'))
opened(found['pysubs2/ttml_example.ttml'])
check('the next file opened has none of them', session.SUBTITLE.get('attachments'), None)

print('Subtitle Edit: charset detection')
# Subtitle Edit's LanguageAutoDetectTest: the Danish and Russian files
# (UTF-8 with a byte order mark) and files in windows-1250, -1251 and
# -1254 (its DetectAnsiEncoding answers). Decoded here by that encoding,
# every line Subtitld reads is in the file, and the cues are as many as
# the timing lines.
for name, encoding, sample in (
        ('auto_detect_Danish.srt', 'utf-8-sig', 'Danske underteksster'),
        ('auto_detect_Russian.srt', 'utf-8-sig', 'Ричмонде, штат Вирджиния 1865'),
        ('auto_detect_windows-1250.srt', 'cp1250', 'Đino, dođavola.'),
        ('auto_detect_windows-1251.srt', 'cp1251', 'Из зоре времена долазимо,'),
        ('auto_detect_windows-1254.srt', 'cp1254', 'Kardeşinin ruhu artık benim!')):
    text = found['subtitleedit/' + name].read_bytes().decode(encoding).replace('\r\n', '\n')
    segments, fmt = opened(found['subtitleedit/' + name])
    lines = [line.strip() for s in segments for line in s['text'].split('\n') if line.strip()]
    check(f'{name}: every cue, every line as {encoding} has it',
          (fmt, len(segments), [line for line in lines if line not in text], any(sample in s['text'] for s in segments)),
          ('SRT', text.count('-->'), [], True))
    saved, _fmt = again(name, 'SRT')
    check(f'{name}: saved as SRT and opened again', timed(saved), timed(segments))

print('Subtitle Edit: Apple TV+ WebVTT')
vtt, fmt = opened(found['subtitleedit/sample_WebVTT_AppleTV.webvtt'])
# WebVttTest: the first cue 00:00:30.030-00:00:34.243 as the cue says (the
# X-TIMESTAMP-MAP offset is for an HLS segment, not a file); cue 1's class
# is bold and #AB9216, cue 7's bold, italic and #AB9216 (the STYLE block).
check('first cue\'s times, as its timing line', (fmt, vtt[0]['start'], vtt[0]['end']), ('VTT', 30.03, 34.243))
check('cue 1 coloured and bold', vtt[0]['text'], '<font color="#ab9216"><b>SEVEROVÝCHODNÍ AUSTRÁLIE</b></font>')
check('cue 7 coloured, bold and italic', vtt[6]['text'],
      '<font color="#ab9216"><b><i>Jste mistr bagatelizace, Reddicku.</i></b></font>')
# The file: cue 3 is placed at line 0%, the top.
check('cue 3 at the top', vtt[2]['text'].startswith('{\\an8}'), True)
check('as many cues as timing lines', len(vtt), found['subtitleedit/sample_WebVTT_AppleTV.webvtt'].read_text(
    encoding='utf-8-sig').count('-->'))
saved, _fmt = again('appletv.vtt', 'VTT')
check('saved as WebVTT and opened again, styles and all', styled(saved) == styled(vtt), True)

print('Subtitle Edit: MacCaption')
# MacCaptionCea608Test (the files are FFmpeg's roll-up sample): with only
# CEA-608, the first caption is "(<i>inaudible radio chatter</i>)" and the
# next line ">> Safety remains our number one" (Subtitle Edit gives that
# one with the line rolled up above it; Subtitld a roll-up line a
# subtitle); with CEA-708 as well, the 708 text, its first caption with a
# space inside each bracket. The last line, "io", is where the sample stops.
only, fmt = opened(found['subtitleedit/sample_mcc_cea608_only.mcc'])
check('608 only: the captions', (fmt, [markup.split_lead(s['text'])[1] for s in only]),
      ('MCC', ['(<i>inaudible radio chatter</i>)', '>> Safety remains our number one', 'io']))
saved, _fmt = again('608.mcc', 'MCC')
check('608 only: saved as MCC and opened again', [(round(s['start'], 2), plain(s)) for s in saved],
      [(round(s['start'], 2), plain(s)) for s in only])
both, fmt = opened(found['subtitleedit/sample_mcc_cea608_708.mcc'])
check('608 and 708: the 708 text', [plain(s) for s in both][:2],
      ['( inaudible radio chatter )', '>> Safety remains our number one'])
check('608 and 708: the same times', [(s['start'], s['end']) for s in both], [(s['start'], s['end']) for s in only])
saved, _fmt = again('708.mcc', 'MCC')
check('608 and 708: saved as MCC and opened again', [(round(s['start'], 2), plain(s)) for s in saved],
      [(round(s['start'], 2), plain(s)) for s in both])

print('Subtitle Edit: SCC (issue 16038)')
scc_path = found['subtitleedit/scc_16038_pop_on.scc']
scc_text = scc_path.read_text()
scc_cues, fmt = opened(scc_path)
# The file shows 74 captions: 74 "End of caption" (942f) codes, each
# showing a caption loaded with text. Subtitle Edit's test counts 73, as
# it reads the line at 01:01:41;21, which loads and shows two captions,
# as one.
check('a caption a 942f', (fmt, len(scc_cues), scc_text.count('942f')), ('SCC', 74, 74))
# The file: "Behind me is" loaded at 01:00:00;01 and shown at its 942f,
# 12 words (frames) on; the last "and click on the news tab."
check('first and last', (plain(scc_cues[0]), round(scc_cues[0]['start'], 2), plain(scc_cues[-1])),
      ('Behind me is', round(3600 + 13 * 1001 / 30000, 2), 'and click on the news tab.'))
check('the two captions on one line', [plain(s) for s in scc_cues if 3702 < s['start'] < 3703],
      ['are not subject to', 'that kind of action right now'])
saved, _fmt = again('16038.scc', 'SCC')
# Subtitle Edit's SaveKeepsTimelineMonotonicAndLoadsAfterPreviousDisplay:
# the text read back the same, the timecodes never going back, and no
# caption loaded (94ae, erasing the memory being loaded) before the one
# before it is shown.
check('saved as SCC and opened again: the same captions', [plain(s) for s in saved], [plain(s) for s in scc_cues])
# Times move by a frame here and there, and by up to 6 frames where a
# caption follows another too closely to be loaded (two bytes a frame) in
# the time between them: Subtitld sends each control code twice, as
# broadcast SCC does, where the original sends them once.
check('saved and opened again: the same times, within 7 frames', close(
    [(s['start'], s['end'], plain(s)) for s in saved], [(s['start'], s['end'], plain(s)) for s in scc_cues], 0.234), True)
check('saved and opened again: no caption shown early', [plain(s) for s, t in zip(saved, scc_cues) if s['start'] < t['start'] - 0.001], [])


def frame(label):
    h, m, s, f = (int(part) for part in re.split('[:;]', label))
    minutes = h * 60 + m
    return (h * 3600 + m * 60 + s) * 30 + f - 2 * (minutes - minutes // 10)


written = (SCRATCH / '16038.scc').read_text()
previous_end = display_end = -1
problems = []
for line in written.splitlines():
    if '\t' not in line:
        continue
    label, words = line.split('\t')
    words = words.split()
    at = frame(label)
    if at < previous_end:
        problems.append(f'back in time: {label}')
    if words[0] == '94ae' and at < display_end:
        problems.append(f'loaded before the last was shown: {label}')
    if words[0] == '942f':
        display_end = at + len(words)
    previous_end = at + len(words)
check('written: the timeline never goes back, no load before the last caption shows', problems, [])

if fails:
    print(f'\n{len(fails)} FAILED: {fails}')
    sys.exit(1)
print('\nall OK')
