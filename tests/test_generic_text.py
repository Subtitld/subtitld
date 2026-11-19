"""Text no named format claims (modules.generic_text): each shape read from
hand-built files laid out as Subtitle Edit's nameless readers write them
(the UnknownSubtitleNN, Csv, Tsv and JsonType readers, cited at each),
text that must NOT come out as subtitles (prose, logs, a chat, a table and
JSON of other things, code), and the .txt cascade in file_io: the named
formats still opened as themselves, the shapes next, plain text last.

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
from subtitld.modules import csv_export, file_io, generic_text, session, srt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fps=None):
    found = generic_text.detect(text, fps)
    if found is None:
        return None
    return found.shape, [(s['start'], s['end'], s['text']) for s in found.segments]


TWO = [(1.0, 3.0, 'Hello'), (4.0, 6.0, 'World')]

# --------------------------------------------------------------------------
# Times and text on one line
# --------------------------------------------------------------------------
print('inline: the times, then the text, on one line')
check('Unknown 1: "m:ss – m:ss text"',
      cues('0:01 – 0:11 "My vengeance needs blood!" -Marquis De Sade\n0:12 – 0:15 Second one\n'),
      ('inline', [(1.0, 11.0, '"My vengeance needs blood!" -Marquis De Sade'), (12.0, 15.0, 'Second one')]))
check('Unknown 48: "hh:mm:ss.mmm hh:mm:ss.mmm text"',
      cues('00:00:01.000 00:00:03.500 Hello there\n00:00:04.000 00:00:06.000 General Kenobi\n'),
      ('inline', [(1.0, 3.5, 'Hello there'), (4.0, 6.0, 'General Kenobi')]))
check('Unknown 7: frames, the text on the lines under, the end on its own line',
      cues('00:00:57:16 Benar, tepatnya\nsudah mendapat 24 jam\n00:01:02:00\t\n00:01:03:00 Next one\n00:01:05:00\t\n'),
      ('inline', [(57.64, 62.0, 'Benar, tepatnya\nsudah mendapat 24 jam'), (63.0, 65.0, 'Next one')]))
check('Unknown 51: Spruce-like commas between, wide',
      cues('00:00:01:00  ,  00:00:03:00  , Hello\n00:00:04:00  ,  00:00:06:00  , World\n'), ('table', TWO))
check('Unknown 80: the end at the end of the line, "[tc]text[tc]"',
      cues('[00:00:01.00]Hello[00:00:03.00]\n[00:00:04.00]World[00:00:06.00]\n'), ('inline', TWO))
check('Unknown 59: "tc<tab>text<tab>tc", the next line more of it',
      cues('00:00:01\tHello\t00:00:03\n\tthere\n00:00:04\tWorld\t00:00:06\n'),
      ('inline', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'World')]))
check('Unknown 93: "[seconds seconds] text"', cues('[1.5 3.25] Hello\n[4.0 6.0] World\n'),
      ('inline', [(1.5, 3.25, 'Hello'), (4.0, 6.0, 'World')]))
# Rows of one width also read as a table, which then reads them (quotes
# and all): the same subtitles either way.
check('Unknown 94: "m:ss<tab>m:ss<tab>text"', cues('0:01\t0:03\tHello\n0:04\t0:06\tWorld\n'), ('table', TWO))
check('Unknown 94 with a line of its own under one', cues('0:01\t0:03\tHello\nthere\n0:04\t0:06\tWorld\n'),
      ('inline', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'World')]))
check('Unknown 106: start only, ends at the next, the last for TAIL',
      cues('0:00:01\tHello\n0:00:04\tWorld\n'), ('inline', [(1.0, 4.0, 'Hello'), (4.0, 7.0, 'World')]))
check('Unknown 86: "[hh.mm.ss] text"', cues('[00.00.01] Hello\n[00.00.04] World\n'),
      ('inline', [(1.0, 4.0, 'Hello'), (4.0, 7.0, 'World')]))
check('Unknown 97: start, text, continuation lines tab-indented',
      cues('00:00:01 Hello\n\t\tthere\n00:00:04 World\n'),
      ('inline', [(1.0, 4.0, 'Hello\nthere'), (4.0, 7.0, 'World')]))
check('Unknown 11: "{h:mm:ss}{h:mm:ss}text"', cues('{0:00:01}{0:00:03}Hello\n{0:00:04}{0:00:06}World\n'),
      ('inline', TWO))
check('Unknown 68: "hh:mm:ssFff" frames', cues('00:00:01F00  Hello\n00:00:04F00  World\n'),
      ('inline', [(1.0, 4.0, 'Hello'), (4.0, 7.0, 'World')]))
check('a dialogue dash after the times is text',
      cues('00:00:01.000 00:00:03.000 - Hi.\n00:00:04.000 00:00:06.000 - Bye.\n')[1][0][2], '- Hi.')

# --------------------------------------------------------------------------
# Times on a line of their own, the text under them
# --------------------------------------------------------------------------
print('block: the times on their own line, the text under')
check('Unknown 8 and 72: start only, a blank line between',
      cues('00:00:01.500\nFirst line\n\n00:00:04.000\nSecond line\n'),
      ('block', [(1.5, 4.0, 'First line'), (4.0, 7.0, 'Second line')]))
check('Unknown 74: whole seconds, start only', cues('00:00:01\nHello\n00:00:04\nWorld\n'),
      ('block', [(1.0, 4.0, 'Hello'), (4.0, 7.0, 'World')]))
check('Unknown 54: the start on one line, the end on the next',
      cues('00:00:01:00\n00:00:03:00\nHello\n\n00:00:04:00\n00:00:06:00\nWorld\n\n'), ('block', TWO))
check('Unknown 17: "[n]" numbers, section marks left out, frames',
      cues('[HEADER]\n[BODY]\n[1]\n00:00:01:00\n00:00:03:12\nHello\n[2]\n00:00:04:00\n00:00:06:00\nWorld\n[/BODY]\n'),
      ('block', [(1.0, 3.48, 'Hello'), (4.0, 6.0, 'World')]))
check('Unknown 64: a count line, then the times, "h:mm:ss:ff"',
      cues('1\n0:00:01:00\n0:00:03:00\nHello\n2\n0:00:04:00\n0:00:06:00\nWorld\n'), ('block', TWO))
check('Unknown 31: "n." numbers', cues('1.\n00:00:01:00\n00:00:03:00\nHello\n2.\n00:00:04:00\n00:00:06:00\nWorld\n'),
      ('block', TWO))
check('Unknown 18: number, times, a blank line, the text',
      cues('0001 00:00:01:00 00:00:03:00 \n\nHello\n0002 00:00:04:00 00:00:06:00 \n\nWorld\n'), ('block', TWO))
check('Unknown 22: number, start, length, end; the header line skipped',
      cues('#\tAppearance\tCaption\t\n\n1\t00:00:01:00\t02:00\t00:00:03:00\nHello\n2\t00:00:04:00\t02:00\t00:00:06:00\nWorld\n'),
      ('block', TWO))
check('Unknown 66: number, start, end, length',
      cues('1  00:00:01:00  00:00:03:00  00:02\nHello\n2  00:00:04:00  00:00:06:00  00:02\nWorld\n'), ('block', TWO))
check('Unknown 26 and 32: "n:  hh:mm:ss.cc  hh:mm:ss.cc"',
      cues('Title\n1ab\n\n1:  00:00:01.00  00:00:03.00\nHello\n\n2:  00:00:04.00  00:00:06.00\nWorld\n'), ('block', TWO))
check('Unknown 29 and TimeCodesOnly2\'s line: "tc<tab>tc", then the text',
      cues('00:00:01:00\t00:00:03:00\nHello\n\n00:00:04:00\t00:00:06:00\nWorld\n'), ('block', TWO))
check('Unknown 40: "m:ss – m:ss", then the text', cues('0:01 – 0:03\nHello\n0:04 – 0:06\nWorld\n'), ('block', TWO))
check('Unknown 50: "hh.mm.ss.ff-hh.mm.ss.ff"', cues('00.00.01.00-00.00.03.00\nHello\n\n00.00.04.00-00.00.06.00\nWorld\n'),
      ('block', TWO))
check('Unknown 77: "tc -> tc"', cues('00:00:01:00 -> 00:00:03:00\nHello\n\n00:00:04:00 -> 00:00:06:00\nWorld\n'),
      ('block', TWO))
check('Unknown 107: drop-frame semicolons, Closed Caption Converter\'s marks',
      cues('@ This file written with Closed Caption Converter V3\n\n<begin subtitles>\n'
           '00;00;01;00 00;00;03;00\nHello\n\n00;00;04;00 00;00;06;00\nWorld\n\n<end subtitles>\n'), ('block', TWO))
check('Unknown 12: seconds, two tabs between', cues('1.00\t\t3.00\t\t\nHello\n\n4.00\t\t6.00\t\t\nWorld\n'), ('block', TWO))
check('Unknown 85: number lines, then start and text on one line',
      cues('ISUBTITLES MASTER\n\n1\n00:00:01 Hello\n\n2\n00:00:04 World\n\n[END]\n'),
      ('inline', [(1.0, 4.0, 'Hello'), (4.0, 7.0, 'World')]))
check('two lines of text', cues('00:00:01.000 00:00:03.000\nLine one\nLine two\n\n00:00:04.000 00:00:06.000\nWorld\n')[1][0][2],
      'Line one\nLine two')

print('frames: the video\'s rate, else 25; a field past it says the rate')
check('25 by default', cues('00:00:01:12 00:00:03:00 Hi\n00:00:04:00 00:00:06:00 There\n')[1][0][0], 1.48)
check('the video\'s rate', cues('00:00:01:12 00:00:03:00 Hi\n00:00:04:00 00:00:06:00 There\n', 24.0)[1][0][0], 1.5)
check('a frame 29 in a 25 file is 30 a second',
      cues('00:00:01:29 00:00:03:00 Hi\n00:00:04:00 00:00:06:00 There\n')[1][0][0], 1.967)

print('named formats saved as .txt go to their own readers')
check('SRT', cues('1\n00:00:01,000 --> 00:00:03,000\nHello\n\n2\n00:00:04,000 --> 00:00:06,000\nWorld\n'), ('srt', TWO))
check('WebVTT', cues('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n\n00:00:04.000 --> 00:00:06.000\nWorld\n'),
      ('webvtt', TWO))
check('MicroDVD frames, "|" between lines (and Unknown 70\'s braces)', cues('{25}{75}Hello\n{100}{150}World|two\n'),
      ('frames', [(1.0, 3.0, 'Hello'), (4.0, 6.0, 'World\ntwo')]))

# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
print('table: columns, with or without a header')
check('Csv: milliseconds under its header, quoted text with a comma',
      cues('Number,Start time in milliseconds,End time in milliseconds,Text\n1,1000,3000,"Hello, there"\n2,4000,6000,"World"\n'),
      ('table', [(1.0, 3.0, 'Hello, there'), (4.0, 6.0, 'World')]))
check('Tsv1: tabs, milliseconds', cues('Number\tStart time in milliseconds\tEnd time in milliseconds\tText\n'
                                       '1\t1000\t3000\tHello\n2\t4000\t6000\tWorld\n'), ('table', TWO))
check('Csv2: semicolons, "hh:mm:ss:ff"', cues('Number;Start time (hh:mm:ss:ff);End time (hh:mm:ss:ff);Text\n'
                                              '1;00:00:01:00;00:00:03:00;"Hello"\n2;00:00:04:00;00:00:06:00;"World"\n'),
      ('table', TWO))
check('Csv3: two text columns are two lines',
      cues('Start time (hh:mm:ss:ff),End time (hh:mm:ss:ff),Line 1,Line 2\n'
           '00:00:01:00,00:00:03:00,"Hello","there"\n00:00:04:00,00:00:06:00,"World",""\n'),
      ('table', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'World')]))
check('Csv4: no header, "///" for a line break',
      cues('00:00:01:00,00:00:03:00,"Hello///there"\n00:00:04:00,00:00:06:00,"World"\n'),
      ('table', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'World')]))
found = generic_text.detect('Character,Timecode In,Timecode Out,Dialogue\n"ANA",00:00:01:00,00:00:03:00,"Hello"\n'
                            '"BEA",00:00:04:00,00:00:06:00,"World"\n')
check('CsvNuendo: character as the speaker', (found.shape, [(s['start'], s['end'], s['text'], s['speaker']) for s in found.segments]),
      ('table', [(1.0, 3.0, 'Hello', 'ANA'), (4.0, 6.0, 'World', 'BEA')]))
check('Audition markers (Subtitld\'s own CSV export): start and duration',
      cues('Name\tStart\tDuration\tTime Format\tType\tDescription\r\nHello there\t0:03.550\t0:01.200\tdecimal\tCue\tALICE\r\n'
           'Again\t1:05.000\t0:01.000\tdecimal\tCue\tBOB\r\n'),
      ('table', [(3.55, 4.75, 'Hello there'), (65.0, 66.0, 'Again')]))
# Subtitle Edit's writers (src/libse/SubtitleFormats/Csv.cs ... Csv5.cs):
# Csv quotes the text, doubles its quotes and keeps its line break inside
# the quotes; Csv2 quotes it but writes its quotes as they are, and its
# reader takes the rest of the line (unquoted commas and all); Csv5 is
# Csv4 with "hh:mm:ss.ff", frames after the dot, in a .csv.
check('Csv: semicolons, a line break and doubled quotes inside the quotes',
      cues('Number;Start time in milliseconds;End time in milliseconds;"Text"\n1;1000;3000;"Hello\nthere"\n'
           '2;4000;6000;"Say ""hi""; then go"\n'),
      ('table', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'Say "hi"; then go')]))
check('Csv2: its quotes as written, a line break inside the quotes',
      cues('Number,Start time (hh:mm:ss:ff),End time (hh:mm:ss:ff),"Text"\n1,00:00:01:00,00:00:03:00,"He said "hi" there"\n'
           '2,00:00:04:00,00:00:06:00,"Two\nlines"\n'),
      ('table', [(1.0, 3.0, 'He said "hi" there'), (4.0, 6.0, 'Two\nlines')]))
check('Csv2: unquoted text with commas is the rest of the line',
      cues('1,00:00:01:00,00:00:03:00,I thought I should let her know.\n2,00:00:04:00,00:00:06:00,Yes, and then, more\n'
           '3,00:00:07:00,00:00:08:00,Plain\n'),
      ('table', [(1.0, 3.0, 'I thought I should let her know.'), (4.0, 6.0, 'Yes, and then, more'), (7.0, 8.0, 'Plain')]))
check('Csv5: frames after the dot in a .csv',
      [(s['start'], s['end'], s['text']) for s in generic_text.detect(
          '00:00:01.12,00:00:03.00,"Hello, you///there"\n00:00:04.00,00:00:06.24,World\n', extension='csv').segments],
      [(1.48, 3.0, 'Hello, you\nthere'), (4.0, 6.96, 'World')])
check('...hundredths anywhere else', cues('00:00:01.12,00:00:03.00,Hello\n00:00:04.00,00:00:06.24,World\n'),
      ('table', [(1.12, 3.0, 'Hello'), (4.0, 6.24, 'World')]))
check('a quote never closed is the text', cues('00:00:01:00,00:00:03:00,"Hello\n00:00:04:00,00:00:06:00,World\n'),
      ('table', [(1.0, 3.0, '"Hello'), (4.0, 6.0, 'World')]))
check('seconds under a header that says so', cues('start (s)|end (s)|text\n1.0|3.0|Hello\n4.0|6.0|World\n'), ('table', TWO))

# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------
print('json: an array of timed objects')
check('seconds, start/end/text', cues('[{"start": 1.0, "end": 3.0, "text": "Hello"}, {"start": 4, "end": 6, "text": "World"}]'),
      ('json', TWO))
check('under a key, whole milliseconds', cues('{"captions": [{"startTime": 1000, "endTime": 3000, "content": "Hello"},'
                                             ' {"startTime": 4000, "endTime": 6000, "content": "World"}]}'), ('json', TWO))
check('a key that says milliseconds, and a duration', cues('[{"tStartMs": 1000, "dDurationMs": 2000, "caption": "Hello"},'
                                                          ' {"tStartMs": 4000, "dDurationMs": 2000, "caption": "World"}]'),
      ('json', TWO))
check('clock strings, from/to', cues('{"items": [{"from": "00:00:01,000", "to": "00:00:03,000", "subtitle": "Hello"},'
                                    ' {"from": "00:00:04,000", "to": "00:00:06,000", "subtitle": "World"}]}'), ('json', TWO))
check('a list of lines as the text', cues('[{"begin": 1, "end": 3, "lines": ["Hello", "there"]}, {"begin": 4, "end": 6, "lines": ["World"]}]'),
      ('json', [(1.0, 3.0, 'Hello\nthere'), (4.0, 6.0, 'World')]))

# --------------------------------------------------------------------------
# What must not come out as subtitles
# --------------------------------------------------------------------------
print('refused: text that is not subtitles')
NOT_SUBTITLES = {
    'prose with times in it': 'It was a dark and stormy night.\nAt 10:30 we left the house and walked.\n'
                              'Nobody said anything for a while.\nThen 11:15 came and went, and still nothing.\n'
                              'We sat down at last by the river and waited for the others to arrive.\n',
    'a log with levels': '12:00:01 INFO Server started on port 8080\n12:00:01 DEBUG Loading config from /etc/app.conf\n'
                         '12:00:02 WARN Cache miss for key user:42\n12:00:05 ERROR Connection refused\n',
    'a log with dates': '2024-03-01 12:00:01 started\n2024-03-01 12:00:04 ready\n2024-03-01 12:00:09 stopped\n',
    'a log stamped by the second': '10:00:01 started worker\n10:00:01 worker ready\n10:00:01 queue empty\n'
                                   '10:00:02 got job\n10:00:02 job done\n10:00:03 idle\n',
    'an IRC chat': '[12:00:01] <alice> hi\n[12:00:04] <bob> hello\n[12:00:09] <alice> how are you\n',
    'a CSV of other things': 'id,name,price,qty\n1,Widget,9.99,3\n2,Gadget,19.50,1\n3,Doohickey,4.25,10\n',
    'a timetable CSV with dates': 'date,start,end,event\n2024-03-01,09:00,10:00,Standup\n2024-03-01,10:30,11:00,Review\n',
    'numbers with no header': '1,9.99,3,Widget\n2,19.50,1,Gadget\n',
    'JSON with no times': '[{"name": "x", "age": 3}, {"name": "y", "age": 4}]',
    'JSON with times and no text': '[{"start": 1, "end": 3}, {"start": 4, "end": 6}]',
    'code': 'def main():\n    x = 1\n    return x\n\nif __name__ == "__main__":\n    main()\n',
    'one timed line only': '00:00:01.000 00:00:03.000 Hello\n',
    'times going every which way': '00:05:00 One\n00:01:00 Two\n00:09:00 Three\n00:02:00 Four\n00:07:00 Five\n',
    'timecodes with no text (TimeCodesOnly2)': '0:00:01:00\t0:00:03:00\n0:00:04:00\t0:00:06:00\n',
    'a numbered list': '1. Buy milk\n2. Call Ana at 10:30\n3. Write the report\n',
    'empty': '',
}
for name, text in NOT_SUBTITLES.items():
    check(name, generic_text.detect(text), None)
check('looks_like says the same, from text', generic_text.looks_like(NOT_SUBTITLES['a log with levels']), False)
check('looks_like from bytes', generic_text.looks_like('0:01\t0:03\tHello\n0:04\t0:06\tWorld\n'.encode('utf-8')), True)
try:
    generic_text.parse(NOT_SUBTITLES['prose with times in it'])
    check('parse refuses prose', 'read', 'ValueError')
except ValueError:
    check('parse refuses prose', 'ValueError', 'ValueError')
check('a confidence comes with the shape', 0.7 <= generic_text.detect('0:01\t0:03\tHello\n0:04\t0:06\tWorld\n').confidence <= 1, True)

# --------------------------------------------------------------------------
# The .txt cascade in file_io
# --------------------------------------------------------------------------
folder = Path(tempfile.mkdtemp())


def opened(name, text, video=None):
    path = folder / name
    path.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
    session.FORMAT = {}
    session.VIDEO = dict(video or {})
    session.SUBTITLE.pop('metadata', None)
    segments, fmt = file_io.process_subtitles_file(str(path))
    return fmt, [(s['start'], s['end'], s['text']) for s in segments]


print('the named .txt formats are still themselves')
NAMED = {
    'ULTRASTAR': '#TITLE:Song\n#ARTIST:Band\n#BPM:300\n#GAP:1200\n: 0 4 5 Hold\n: 6 3 7  on\n- 34\n: 40 4 0 We\nE\n',
    'AVID': ('@ This file written with the Avid Caption plugin, version 1\r\n\r\n<begin subtitles>\r\n'
             '00:00:01:00 00:00:03:00\r\nHello\r\n\r\n00:00:04:00 00:00:06:00\r\nWorld\r\n\r\n<end subtitles>\r\n'),
    'ENCORE': '1 00:00:01:00 00:00:03:12 First line\nsecond line\n2 00:00:04:00 00:00:06:00 Second cue\n',
    'VPLAYER': '00:00:01:Hello\n00:00:05:There\n',
    'MPL2': '[12][35]First|second\n[40][60]Another\n',
    'CAPTIONS32': '00:01:26:60 , 00:01:29:00 , O padrinho nasceu com o nome     |de Vito Andolini,                \n',
    'DVDSS': '00:00:01:00 00:00:03:00 Hello\n00:00:04:00 00:00:06:00 World\n',
}
for want, text in NAMED.items():
    check(f'{want} opens as {want}', opened(f'{want.lower()}.txt', text)[0], want)

print('the shapes next')
check('inline', opened('inline.txt', '0:01\t0:03\tHello\n0:04\t0:06\tWorld\n'), ('SRT', TWO))
check('block, frames at the video\'s rate', opened('block.txt', '00:00:01:12\t00:00:03:00\nHello\n\n00:00:04:00\t00:00:06:00\nWorld\n',
                                                   {'framerate': 24.0}), ('SRT', [(1.5, 3.0, 'Hello'), (4.0, 6.0, 'World')]))
check('an SRT saved as .txt', opened('subrip.txt', '1\n00:00:01,000 --> 00:00:03,000\n<i>Hello</i>\n\n2\n00:00:04,000 --> 00:00:06,000\nWorld\n'),
      ('SRT', [(1.0, 3.0, '<i>Hello</i>'), (4.0, 6.0, 'World')]))
check('a WebVTT saved as .txt', opened('webvtt.txt', 'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n\n00:00:04.000 --> 00:00:06.000\nWorld\n'),
      ('VTT', TWO))
check('a table', opened('table.txt', 'Number,Start time in milliseconds,End time in milliseconds,Text\n1,1000,3000,Hello\n2,4000,6000,World\n'),
      ('SRT', TWO))
check('JSON in a .txt', opened('json.txt', '[{"start": 1, "end": 3, "text": "Hello"}, {"start": 4, "end": 6, "text": "World"}]'),
      ('SRT', TWO))
check('UTF-16 with a byte order mark', opened('utf16.txt', '0:01\t0:03\tOlá\n0:04\t0:06\tMundo\n'.encode('utf-16')),
      ('SRT', [(1.0, 3.0, 'Olá'), (4.0, 6.0, 'Mundo')]))

print('plain text last, as the Import panel reads it')
check('prose', opened('prose.txt', 'First sentence. Second sentence.'),
      ('TXT', [(0.0, 5.0, 'First sentence.'), (5.0, 10.0, 'Second sentence..')]))
check('a log is plain text', opened('server.txt', NOT_SUBTITLES['a log with levels'])[0], 'TXT')
check('a CSV of other things is plain text', opened('prices.txt', NOT_SUBTITLES['a CSV of other things'])[0], 'TXT')
check('JSON with no times is plain text', opened('people.txt', NOT_SUBTITLES['JSON with no times'])[0], 'TXT')
check('an empty file has nothing in it', opened('empty.txt', ''), ('TXT', []))

print('.csv and .tsv: tables of times and text, read only')
check('Csv in a .csv', opened('se.csv', 'Number;Start time in milliseconds;End time in milliseconds;"Text"\n'
                                     '1;1000;3000;"Hello"\n2;4000;6000;"World"\n'), ('TABLE', TWO))
check('Csv5 in a .csv: frames', opened('csv5.csv', '00:00:01.12,00:00:03.00,Hello\n00:00:04.00,00:00:06.00,World\n'),
      ('TABLE', [(1.48, 3.0, 'Hello'), (4.0, 6.0, 'World')]))
check('Tsv in a .tsv', opened('se.tsv', 'Number\tStart time in milliseconds\tEnd time in milliseconds\tText\n'
                                     '1\t1000\t3000\tHello\n2\t4000\t6000\tWorld\n'), ('TABLE', TWO))
check('a .tsv with no header, clocks', opened('plain.tsv', '00:00:01,000\t00:00:03,000\tHello\n00:00:04,000\t00:00:06,000\tWorld\n'),
      ('TABLE', TWO))
# Subtitld's own CSV export (modules.csv_export, Audition's markers) read
# back exactly: times to the millisecond, the text on one line, the
# speaker from the description it is written to.
written = [{'start': 3.55, 'end': 4.75, 'text': 'Hello there', 'speaker': 'ALICE'},
           {'start': 65.0, 'end': 66.001, 'text': '"Quoted", with commas', 'speaker': 'BOB'},
           {'start': 3725.5, 'end': 3727.25, 'text': 'An hour in', 'speaker': ''}]
csv_export.save(str(folder / 'audition.csv'), written)
session.FORMAT, session.VIDEO, session.SPEAKERS = {}, {}, {}
read_back, fmt = file_io.process_subtitles_file(str(folder / 'audition.csv'))
check("Subtitld's CSV export read back", (fmt, [(s['start'], s['end'], s['text'], s.get('speaker')) for s in read_back]),
      ('TABLE', [(3.55, 4.75, 'Hello there', 'ALICE'), (65.0, 66.001, '"Quoted", with commas', 'BOB'),
                 (3725.5, 3727.25, 'An hour in', None)]))
check('...its speakers listed', sorted(session.SPEAKERS), ['ALICE', 'BOB'])
try:
    opened('other.tsv', 'id\tname\tprice\n1\tWidget\t9.99\n2\tGadget\t19.50\n')
    check('a .tsv of other things says so', 'opened', 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('a .tsv of other things says so', 'CorruptedProjectFileError', 'CorruptedProjectFileError')
check('registered', session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS['TABLE']['extensions'], ['csv', 'tsv'])

print('.json: timed objects no tool claims')
check('opened', opened('unknown.json', '{"subs": [{"startTime": "00:00:01.000", "endTime": "00:00:03.000", "text": "Hello"},'
                                       ' {"startTime": "00:00:04.000", "endTime": "00:00:06.000", "text": "World"}]}'),
      ('JSON', TWO))
try:
    opened('people.json', NOT_SUBTITLES['JSON with no times'])
    check('JSON with no times still says so', 'opened', 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('JSON with no times still says so', 'CorruptedProjectFileError', 'CorruptedProjectFileError')

print('saved: read only, so exported as another format')
fmt, read_back = opened('export.txt', '00:00:01.000 00:00:03.000 Hello\n00:00:04.000 00:00:06.000 World\n')
session.SUBTITLE['segments'] = file_io.process_subtitles_file(str(folder / 'export.txt'))[0]
file_io.save_file(str(folder / 'export.srt'), fmt)
check('as SRT through save_file', [(s['start'], s['end'], s['text']) for s in srt.read(str(folder / 'export.srt'))], TWO)

print()
if fails:
    print(f'{len(fails)} FAILED: {fails}')
    sys.exit(1)
print('ALL PASS')
