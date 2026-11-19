"""Transcripts: Praat's TextGrid (long and short text forms, UTF-16, the
tiers its names choose, word tiers grouped, point tiers left out), CHAT
(participants, bullets, CHAT's codes), CTM (words grouped, channels as
speakers, alternatives), round trips, and the files and the export dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, transcripts

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def refused(name, call, start):
    try:
        call()
        check(name, None, 'ValueError')
    except ValueError as error:
        check(name, str(error).startswith(start), True)


# The long text form, as the Praat manual's "TextGrid file formats" shows
# it: two speakers' interval tiers, a doubled quote, a text over two lines,
# a point tier (the bell), which is no subtitle.
LONG = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 2.3
tiers? <exists>
size = 3
item []:
    item [1]:
        class = "IntervalTier"
        name = "Mary"
        xmin = 0
        xmax = 2.3
        intervals: size = 3
        intervals [1]:
            xmin = 0
            xmax = 0.7
            text = "říkej ""ahoj"""
        intervals [2]:
            xmin = 0.7
            xmax = 1.6
            text = ""
        intervals [3]:
            xmin = 1.6
            xmax = 2.3
            text = "sp"
    item [2]:
        class = "IntervalTier"
        name = "spk2"
        xmin = 0
        xmax = 2.3
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 1.2
            text = ""
        intervals [2]:
            xmin = 1.2
            xmax = 2.3
            text = "two
lines"
    item [3]:
        class = "TextTier"
        name = "bell"
        xmin = 0
        xmax = 2.3
        points: size = 2
        points [1]:
            number = 0.9
            mark = "ding"
        points [2]:
            number = 1.3
            mark = "dong"
'''
# The manual's human-readable short form: data together on lines, "!"
# comments; a sentence tier over a word tier and a phone tier.
SHORT = '''"ooTextFile"
"TextGrid"
0  2.3  ! time domain of TextGrid
<exists>
3 tiers
"IntervalTier"  "words"  ! type and name of tier 1
0  2.3
3 intervals coming
0  0.7  "hello"
0.7  0.8  ","
0.8  2.3  "world"
"IntervalTier"  "sentence"
0  2.3
1 interval coming
0  2.3  "Hello, world"
"IntervalTier"  "phones"
0  2.3
1
0  2.3  "h"
'''
print('TextGrid: reading')
check('the speakers\' tiers, a numbered one a letter; pauses and the point tier left out',
      transcripts.parse_textgrid(LONG),
      [{'start': 0.0, 'end': 0.7, 'text': 'říkej "ahoj"', 'speaker': 'Mary'},
       {'start': 1.2, 'end': 2.3, 'text': 'two\nlines', 'speaker': 'A'}])
check('UTF-16 with its byte order mark', transcripts.parse_textgrid(LONG.encode('utf-16')), transcripts.parse_textgrid(LONG))
check('the short form: the sentence tier before the words and phones', transcripts.parse_textgrid(SHORT),
      [{'start': 0.0, 'end': 2.3, 'text': 'Hello, world'}])
check('the tiers listed, to choose one', transcripts.textgrid_tiers(SHORT),
      [('words', 'IntervalTier', 3), ('sentence', 'IntervalTier', 1), ('phones', 'IntervalTier', 1)])
check('a word tier chosen: words grouped, punctuation attached', transcripts.parse_textgrid(SHORT, tier='words'),
      [{'start': 0.0, 'end': 2.3, 'text': 'hello, world'}])
MFA = SHORT.replace('"words"', '"Ann - words"').replace('"sentence"', '"Ann - phones"').replace('"phones"', '"notes"')
check("the Montreal Forced Aligner's tiers: the speaker's words", transcripts.parse_textgrid(MFA),
      [{'start': 0.0, 'end': 2.3, 'text': 'hello, world', 'speaker': 'Ann'}])
refused('a binary TextGrid: refused, saying what to do', lambda: transcripts.parse_textgrid(b'ooBinaryFile\x08TextGrid'),
        'This is a binary TextGrid')
refused('a file cut short: refused', lambda: transcripts.parse_textgrid(SHORT[:120]), 'This TextGrid ends')

print('TextGrid: writing')
trip = [{'start': 0.5, 'end': 2.0, 'text': 'Say "hi"', 'speaker': 'Mary'},
        {'start': 1.5, 'end': 3.0, 'text': 'Over\nher', 'speaker': 'Mary'},
        {'start': 3.5, 'end': 4.25, 'text': 'John now', 'speaker': 'John'}]
written = transcripts.write_textgrid(trip, speakers=True)
tiers = transcripts.parse_textgrid_tiers(written)
check('a tier a speaker, an overlap a further one; intervals meet and fill the tier',
      [(tier['name'], [(start, end) for start, end, _text in tier['items']]) for tier in tiers],
      [('Mary', [(0.0, 0.5), (0.5, 2.0), (2.0, 4.25)]), ('Mary (2)', [(0.0, 1.5), (1.5, 3.0), (3.0, 4.25)]),
       ('John', [(0.0, 3.5), (3.5, 4.25)])])
check('quotes doubled', 'text = "Say ""hi"""' in written, True)
check('read back as written', transcripts.parse_textgrid(written), trip)
check('without speakers: one tier, read back without them',
      transcripts.parse_textgrid(transcripts.write_textgrid(trip)),
      [{key: value for key, value in subtitle.items() if key != 'speaker'} for subtitle in trip])

print('CHAT')
CHA = ('@UTF8\n@Begin\n@Languages:\tpor, eng\n@Participants:\tCHI Ross Target_Child, MOT Mother\n'
       '@ID:\tpor|corpus|CHI|2;10.10||||Target_Child|||\n@ID:\tpor|corpus|MOT|||||Mother|||\n@Media:\tabe88, audio\n'
       '*CHI:\twhy [/] why isn\'t &=laughs <Mommy> [?] coming ? \x150_1073\x15\n%com:\tmother usually comes at 4.\n'
       '*MOT:\tdon\'t\n\tworry +... \x151100_2000\x15\n*MOT:\tuntimed .\n'
       '*CHI:\tone \x152100_2500\x15 two . \x152500_3000\x15\n@End\n')
check('main lines with bullets; the name or the code; codes out; continuations; multiple bullets; untimed left out',
      transcripts.parse_chat(CHA),
      ([{'start': 0.0, 'end': 1.073, 'text': "why why isn't Mommy coming?", 'speaker': 'Ross'},
        {'start': 1.1, 'end': 2.0, 'text': "don't worry...", 'speaker': 'MOT'},
        {'start': 2.1, 'end': 3.0, 'text': 'one two.', 'speaker': 'Ross'}], 'por'))
refused('no bullets at all: refused, saying why', lambda: transcripts.parse_chat('@Begin\n*CHI:\thi .\n@End\n'),
        'This CHAT transcript has no time marks')
chat = transcripts.write_chat(trip, 'pt-br', speakers=True, media='clip')
check('headers as the manual orders them, an ID each, a terminator added, the bullet in milliseconds',
      chat.split('\n'),
      ['@UTF8', '@Begin', '@Languages:\tpor', '@Participants:\tMAR Mary Participant, JOH John Participant',
       '@ID:\tpor|clip|MAR|||||Participant|||', '@ID:\tpor|clip|JOH|||||Participant|||', '@Media:\tclip, video',
       '*MAR:\tSay "hi" . \x15500_2000\x15', '*MAR:\tOver her . \x151500_3000\x15', '*JOH:\tJohn now . \x153500_4250\x15',
       '@End', ''])
check('read back: a terminator more, the lines one', transcripts.parse_chat(chat)[0],
      [{'start': 0.5, 'end': 2.0, 'text': 'Say "hi".', 'speaker': 'Mary'},
       {'start': 1.5, 'end': 3.0, 'text': 'Over her.', 'speaker': 'Mary'},
       {'start': 3.5, 'end': 4.25, 'text': 'John now.', 'speaker': 'John'}])
check('speakers left out: one unidentified participant', '@Participants:\tUNI Unidentified' in transcripts.write_chat(trip),
      True)
check('short names made IDs, made unique', transcripts._chat_codes(['A', 'B', 'Annie', 'Anne']),
      {'A': 'SPA', 'B': 'SPB', 'Annie': 'ANN', 'Anne': 'AN2'})

print('CTM')
CTM = (';; a comment\n\n7654 A 11.34 0.2  YES -6.763\n7654 A 11.60 0.34 YOU -12.38\n7654 A 11.94 0.1 . 0.9\n'
       '7654 A * * <ALT_BEGIN>\n7654 A 12.10 0.3 UM\n7654 A * * <ALT>\n7654 A 12.10 0.3 UH\n7654 A * * <ALT_END>\n'
       '7654 A 12.40 0.2 <unk>\n7654 B 1.34 0.2 I\n7654 B 2.00 0.34 CAN\n')
check('words grouped as speech recognisers\' are; channels as speakers; the first alternative; <unk> out',
      transcripts.parse_ctm(CTM),
      [{'start': 1.34, 'end': 2.34, 'text': 'I CAN', 'speaker': 'B'},
       {'start': 11.34, 'end': 12.4, 'text': 'YES YOU. UM', 'speaker': 'A'}])
check('the limits asked for', [s['text'] for s in transcripts.parse_ctm(CTM, {'characters': 4, 'lines': 1})],
      ['I', 'CAN', 'YES', 'YOU.', 'UM'])
check('looks like CTM, and an SRT does not', (transcripts.ctm_looks_like(CTM), transcripts.ctm_looks_like('1\n00:00:01,000 --> 00:00:02,000\nHi\n')),
      (True, False))
ctm = transcripts.write_ctm([{'start': 0, 'end': 2, 'text': '{\\k50}Hel{\\k30}lo {\\k20}{\\k60}world'},
                             {'start': 3, 'end': 4, 'text': '<i>two</i> words'}], 'my clip')
check('a word a line: karaoke times, else shared by length', ctm.split('\n'),
      ['my_clip A 0.000 0.800 Hello', 'my_clip A 1.000 0.600 world',
       'my_clip A 3.000 0.400 two', 'my_clip A 3.400 0.600 words', ''])
check('read back', [(s['start'], s['end'], s['text']) for s in transcripts.parse_ctm(ctm)],
      [(0.0, 1.6, 'Hello world'), (3.0, 4.0, 'two words')])

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.SPEAKERS.clear()
session.FORMAT = {}
(folder / 'in.TextGrid').write_text(LONG, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.TextGrid'))
check('a TextGrid opens; its speakers the project\'s, kept to save again',
      (fmt, [s['speaker'] for s in opened], sorted(session.SPEAKERS), session.FORMAT['options'].get('speakers')),
      ('TEXTGRID', ['Mary', 'A'], ['A', 'Mary'], 'voice'))
session.SUBTITLE['language'] = 'en-us'
(folder / 'in.cha').write_text(CHA, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.cha'))
check("CHAT opens, its language the project's", (fmt, len(opened), session.SUBTITLE['language']), ('CHAT', 3, 'pt-pt'))
(folder / 'in.ctm').write_text(CTM, encoding='utf-8')
check('CTM opens', file_io.process_subtitles_file(str(folder / 'in.ctm'))[1], 'CTM')
(folder / 'bad.TextGrid').write_text('nothing', encoding='utf-8')
try:
    file_io.process_subtitles_file(str(folder / 'bad.TextGrid'))
    check('not a TextGrid: says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('not a TextGrid: says so', str(error).startswith('Not a TextGrid'), True)
session.SUBTITLE.segments = trip
session.SUBTITLE['language'] = 'en-us'
session.VIDEO = {'width': 1280, 'height': 720, 'filepath': '/videos/clip.mp4'}
for fmt, name in (('TEXTGRID', 'out.TextGrid'), ('CHAT', 'out.cha'), ('CTM', 'out.ctm')):
    session.FORMAT = {'options': {'speakers': 'voice'}}
    file_io.save_file(str(folder / name), fmt)
    session.FORMAT = {}
    back, back_fmt = file_io.process_subtitles_file(str(folder / name))
    # CTM keeps words, not subtitles: they come back grouped anew.
    check(f'{fmt}: saved and opened again', (back_fmt, [(s['start'], s['speaker']) for s in back] if fmt != 'CTM'
                                             else ' '.join(s['text'] for s in back)),
          (fmt, [(0.5, 'Mary'), (1.5, 'Mary'), (3.5, 'John')] if fmt != 'CTM' else 'Say "hi" Over her John now'))
check('CHAT names the video', '@Media:\tclip, video' in (folder / 'out.cha').read_text(encoding='utf-8'), True)
check('the extensions', [session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[key]['extensions'] for key in ('TEXTGRID', 'CHAT', 'CTM')],
      [['TextGrid', 'textgrid'], ['cha'], ['ctm']])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
for fmt, voice in (('TEXTGRID', True), ('CHAT', True), ('CTM', False)):
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    combo = panel.speakers_combo.combobox
    check(f'{fmt} in the export menu, text options, no formatting, its own speakers or none',
          (fmt in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.formatting_combo.isHidden(),
           'voice' in [combo.itemData(index) for index in range(combo.count())]), (True, False, True, voice))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
