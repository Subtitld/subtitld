"""JSON from other tools: telling them apart, and reading each as its
documentation shows it (whisper.cpp, WhisperX, YouTube's json3, Bilibili's
BCC, AssemblyAI, Deepgram, AWS Transcribe, Rev.ai, Google Speech-to-Text,
Azure Speech, Speechmatics, Amara); turning words and long turns into
subtitles of the length asked for; naming speakers; writing BCC; and
opening and exporting through file_io and the dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, json_subtitles as js, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data, limits=None):
    segments, language, which = js.read(data, limits)
    return [(s['start'], s['end'], s['text'], s.get('speaker')) for s in segments], language, which


def words_of(text, start=0.0, step=0.4, speaker=None):
    return [{'start': round(start + i * step, 3), 'end': round(start + i * step + 0.3, 3), 'text': word,
             'speaker': speaker} for i, word in enumerate(text.split())]


print('which tool')
check("Subtitld's own and openai-whisper's: none (the plain-text module reads them)", (
    js.kind({'segments': [{'start': 0, 'end': 1, 'text': 'a'}]}), js.kind([{'start': 0, 'end': 1, 'text': 'a'}]),
    js.kind({'segments': [{'id': 0, 'seek': 0, 'start': 0, 'end': 1, 'text': ' a', 'words': []}], 'language': 'en'}),
    js.is_subtitlds({'segments': [{'start': 0, 'end': 1, 'text': 'a'}]}), js.is_subtitlds({'foo': 1})),
      (None, None, None, True, False))
check('nothing known: refused', js.kind({'hello': 'world'}), None)
try:
    js.read({'hello': 'world'})
    check('read refuses it', None, 'ValueError')
except ValueError as error:
    check('read refuses it', str(error), 'This JSON holds no subtitles or transcript Subtitld knows')

print('speakers')
names = js.Speakers()
check('numbers and ids as letters, in order; names kept', [names(v) for v in (
    0, 'spk_1', 0, 'SPEAKER_02', 'Guest-1', 'S4', 'Jane Doe', None, '')],
      ['A', 'B', 'A', 'C', 'D', 'E', 'Jane Doe', None, None])

print('words into subtitles')
text = ("Look, Celia, we have to follow our passions. You have your robotics, and I just want to be awesome in "
        "space. Why don't you just admit that you're freaked out by my robot hand?")
check('within 42 a line, 2 lines, 7 s; at punctuation when full', [s['text'] for s in js.from_words(words_of(text, step=0.35))],
      ['Look, Celia, we have to\nfollow our passions.', 'You have your robotics, and I\njust want to be awesome in space.',
       "Why don't you just admit that you're\nfreaked out by my robot hand?"])
check('other limits', [s['text'] for s in js.from_words(words_of(text, step=0.35), {'characters': 20, 'lines': 1})][:3],
      ['Look, Celia,', 'we have to follow', 'our passions.'])
paused = words_of('One two three', step=0.4) + words_of('after a pause', start=3.0)
check('a pause, a new speaker: a new subtitle', (
    [s['text'] for s in js.from_words(paused)],
    [(s['text'], s['speaker']) for s in js.from_words(words_of('Hi there', speaker='A') + words_of('Hello', start=0.8, speaker='B'))]),
      (['One two three', 'after a pause'], [('Hi there', 'A'), ('Hello', 'B')]))
check('punctuation attached, no space', js.from_words(words_of('Hello') + [
    {'start': 0.3, 'end': 0.3, 'text': ',', 'attached': True}] + words_of('world', start=0.4))[0]['text'], 'Hello, world')
check('a turn without word times: in parts, its time shared', [(s['start'], s['end'], s['text']) for s in js.from_text(
    10, 20, 'A turn with no word times but far too long for one subtitle, so it is shared out by the length of its parts.')],
      [(10, 15.596, 'A turn with no word times but\nfar too long for one subtitle,'),
       (15.596, 20.0, 'so it is shared out by\nthe length of its parts.')])

print('each tool')
WHISPER_CPP = js.loads('{"systeminfo": "x", "model": {"type": "base"}, "params": {"language": "auto"},'
                       ' "result": {"language": "pt"}, "transcription": ['
                       '{"timestamps": {"from": "00:00:00,000", "to": "00:00:02,400"}, "offsets": {"from": 0, "to": 2400},'
                       ' "text": " Olá\tmundo.", "speaker": "0" "speaker_turn_next": true},'
                       '{"timestamps": {"from": "00:00:02,400", "to": "00:00:04,000"}, "text": " Tchau.", "speaker": "?"}]}')
check('whisper.cpp: offsets in ms or its timestamps; channel speakers; its missing comma and raw tab', cues(WHISPER_CPP),
      ([(0.0, 2.4, 'Olá\tmundo.', 'A'), (2.4, 4.0, 'Tchau.', None)], 'pt', 'whisper.cpp'))
WHISPERX = {'segments': [{'start': 0.031, 'end': 2.5, 'text': ' Hello world.', 'speaker': 'SPEAKER_00', 'words': [
    {'word': 'Hello', 'start': 0.031, 'end': 0.4, 'speaker': 'SPEAKER_00'}]},
    {'start': 2.6, 'end': 3.5, 'text': ' Hi.', 'speaker': 'SPEAKER_01', 'words': []}], 'word_segments': [], 'language': 'en'}
check('WhisperX: its segments, its speakers as letters', cues(WHISPERX),
      ([(0.031, 2.5, 'Hello world.', 'A'), (2.6, 3.5, 'Hi.', 'B')], 'en', 'whisperx'))
YOUTUBE = {'wireMagic': 'pb3', 'pens': [{}, {'bAttr': 1, 'fcForeColor': 16776960}, {'iAttr': 1, 'uAttr': 1}],
           'wpWinPositions': [{}, {'apPoint': 1, 'ahHorPos': 50, 'avVerPos': 0}],
           'events': [{'tStartMs': 1000, 'dDurationMs': 1500, 'segs': [{'utf8': 'Bold yellow', 'pPenId': 1}, {'utf8': ' and '},
                                                                       {'utf8': 'italic', 'pPenId': 2}]},
                      {'tStartMs': 3000, 'dDurationMs': 1000, 'wpWinPosId': 1, 'segs': [{'utf8': 'Top​\nline'}]},
                      {'tStartMs': 5000, 'segs': [{'utf8': 'No duration: 5 s'}]},
                      {'tStartMs': 11000, 'dDurationMs': 900, 'segs': [{'utf8': '&lt;i&gt;Marked up&lt;/i&gt;'}]},
                      {'tStartMs': 12000, 'dDurationMs': 900, 'segs': [{'utf8': '​'}]}]}
check("YouTube's json3: pens as tags, window positions as places, 5 s when no duration, zero-width spaces gone, "
      "HTML in its text", cues(YOUTUBE), ([
          (1.0, 2.5, '<font color="#ffff00"><b>Bold yellow</b></font> and <i><u>italic</u></i>', None),
          (3.0, 4.0, '{\\an8}Top\nline', None), (5.0, 10.0, 'No duration: 5 s', None),
          (11.0, 11.9, '<i>Marked up</i>', None)], None, 'youtube'))
AUTOMATIC = {'wireMagic': 'pb3', 'wsWinStyles': [{}, {'mhModeHint': 2, 'juJustifCode': 0, 'sdScrollDir': 3}],
             'wpWinPositions': [{}, {'apPoint': 6, 'ahHorPos': 20, 'avVerPos': 100}],
             'events': [{'tStartMs': 0, 'dDurationMs': 60000, 'id': 1, 'wpWinPosId': 1, 'wsWinStyleId': 1},
                        {'tStartMs': 160, 'dDurationMs': 3840, 'wWinId': 1, 'segs': [{'utf8': 'hi'}, {'utf8': " i'm", 'tOffsetMs': 400}]},
                        {'tStartMs': 1990, 'dDurationMs': 2010, 'wWinId': 1, 'aAppend': 1, 'segs': [{'utf8': '\n'}]},
                        {'tStartMs': 2000, 'dDurationMs': 4000, 'wWinId': 1, 'segs': [{'utf8': 'taylor'}]}]}
check('automatic captions roll: each line until the next, no place', cues(AUTOMATIC)[0],
      [(0.16, 2.0, "hi i'm", None), (2.0, 6.0, 'taylor', None)])
BILIBILI = {'font_size': 0.4, 'font_color': '#FFFFFF', 'background_alpha': 0.5, 'background_color': '#9C27B0', 'Stroke': 'none',
            'type': 'AIsubtitle', 'lang': 'zh', 'body': [{'from': 4.43, 'to': 7.41, 'sid': 1, 'location': 2, 'content': '大家好'},
                                                          {'from': 8, 'to': 9, 'location': 8, 'content': 'Up\nhere'}]}
check("Bilibili's BCC: seconds; location 2 the default, as a keypad", cues(BILIBILI),
      ([(4.43, 7.41, '大家好', None), (8.0, 9.0, '{\\an8}Up\nhere', None)], 'zh', 'bilibili'))
ASSEMBLYAI = {'id': 'x', 'status': 'completed', 'language_code': 'en_us', 'text': 'Smoke from Canada. Yes.',
              'words': [{'text': 'Smoke', 'start': 250, 'end': 650, 'confidence': 0.97, 'speaker': 'A'},
                        {'text': 'from', 'start': 700, 'end': 900, 'speaker': 'A'},
                        {'text': 'Canada.', 'start': 950, 'end': 1500, 'speaker': 'A'},
                        {'text': 'Yes.', 'start': 2000, 'end': 2400, 'speaker': 'B'}],
              'utterances': [{'speaker': 'A', 'start': 250, 'end': 1500, 'text': 'Smoke from Canada.', 'words': []}]}
check('AssemblyAI: words in ms, its speaker letters', cues(ASSEMBLYAI),
      ([(0.25, 1.5, 'Smoke from Canada.', 'A'), (2.0, 2.4, 'Yes.', 'B')], 'en_us', 'assemblyai'))
check('AssemblyAI without words: its utterances', cues({**ASSEMBLYAI, 'words': [], 'utterances': [
    {'speaker': 'A', 'start': 0, 'end': 2000, 'text': 'Only utterances.', 'words': []}]})[0],
      [(0.0, 2.0, 'Only utterances.', 'A')])
DEEPGRAM = {'metadata': {'duration': 25.9, 'channels': 1}, 'results': {'channels': [{'detected_language': 'en', 'alternatives': [{
    'transcript': 'yeah as much', 'words': [
        {'word': 'yeah', 'start': 0.08, 'end': 0.32, 'speaker': 0, 'punctuated_word': 'Yeah,'},
        {'word': 'as', 'start': 0.4, 'end': 0.5, 'speaker': 0, 'punctuated_word': 'as'},
        {'word': 'much', 'start': 0.5, 'end': 0.8, 'speaker': 1, 'punctuated_word': 'much.'}]}]}]}}
check('Deepgram: punctuated words, numbered speakers as letters', cues(DEEPGRAM),
      ([(0.08, 0.5, 'Yeah, as', 'A'), (0.5, 0.8, 'much.', 'B')], 'en', 'deepgram'))
check('Deepgram without words: its paragraphs', cues({'results': {'channels': [{'alternatives': [{'transcript': 'x', 'paragraphs': {
    'paragraphs': [{'speaker': 0, 'sentences': [{'text': 'A sentence.', 'start': 0.34, 'end': 3.77}]}]}}]}]}})[0],
      [(0.34, 3.77, 'A sentence.', 'A')])
AWS = {'jobName': 'x', 'status': 'COMPLETED', 'results': {
    'language_code': 'en-GB', 'transcripts': [{'transcript': "I've been on hold. Sorry."}],
    'items': [{'start_time': '4.87', 'end_time': '5.02', 'speaker_label': 'spk_0', 'type': 'pronunciation',
               'alternatives': [{'confidence': '1.0', 'content': "I've"}]},
              {'start_time': '5.02', 'end_time': '5.2', 'speaker_label': 'spk_0', 'type': 'pronunciation', 'alternatives': [{'content': 'been'}]},
              {'start_time': '5.2', 'end_time': '5.4', 'speaker_label': 'spk_0', 'type': 'pronunciation', 'alternatives': [{'content': 'on'}]},
              {'start_time': '5.4', 'end_time': '5.9', 'speaker_label': 'spk_0', 'type': 'pronunciation', 'alternatives': [{'content': 'hold'}]},
              {'speaker_label': 'spk_0', 'type': 'punctuation', 'alternatives': [{'content': '.'}]},
              {'start_time': '6.5', 'end_time': '7.0', 'type': 'pronunciation', 'alternatives': [{'content': 'Sorry'}]},
              {'type': 'punctuation', 'alternatives': [{'content': '.'}]}],
    'speaker_labels': {'speakers': 2, 'segments': [{'start_time': '6.5', 'end_time': '7.0', 'speaker_label': 'spk_1',
                                                     'items': [{'start_time': '6.5', 'end_time': '7.0', 'speaker_label': 'spk_1'}]}]}}}
check('AWS Transcribe: time strings, punctuation on the word before, speakers by item or by segment', cues(AWS),
      ([(4.87, 5.9, "I've been on hold.", 'A'), (6.5, 7.0, 'Sorry.', 'B')], 'en-GB', 'aws'))
REV = {'monologues': [{'speaker': 1, 'speaker_info': {'id': 1, 'display_name': 'Jane Doe'}, 'elements': [
    {'type': 'text', 'value': 'Hi', 'ts': 0.27, 'end_ts': 0.32}, {'type': 'punct', 'value': ','}, {'type': 'punct', 'value': ' '},
    {'type': 'text', 'value': 'my', 'ts': 0.35, 'end_ts': 0.46}, {'type': 'punct', 'value': '.'}]},
    {'speaker': 2, 'speaker_info': None, 'elements': [{'type': 'text', 'value': 'Hello', 'ts': 1.0, 'end_ts': 1.4}]}]}
check('Rev.ai: elements, spaces and punctuation; a named speaker kept, a numbered one a letter', cues(REV),
      ([(0.27, 0.46, 'Hi, my.', 'Jane Doe'), (1.0, 1.4, 'Hello', 'A')], None, 'rev'))
GOOGLE_V1 = {'results': [
    {'alternatives': [{'transcript': 'okay so', 'words': [{'startTime': '1.400s', 'endTime': '1.800s', 'word': 'okay'},
                                                         {'startTime': '1.800s', 'endTime': '2s', 'word': 'so'}]}],
     'languageCode': 'en-us'},
    {'alternatives': [{'transcript': '', 'words': [
        {'startTime': '1.400s', 'endTime': '1.800s', 'word': 'okay', 'speakerTag': 1},
        {'startTime': '1.800s', 'endTime': '2s', 'word': 'so', 'speakerTag': 1},
        {'startTime': '3s', 'endTime': '3.500s', 'word': 'right', 'speakerTag': 2}]}]}]}
check('Google v1: "1.400s" times; the words its diarization repeats, once', cues(GOOGLE_V1)[0],
      [(1.4, 2.0, 'okay so', None), (3.0, 3.5, 'right', 'A')])
GOOGLE_V2 = {'name': 'op', 'done': True, 'response': {'results': [{'alternatives': [{'transcript': 'hi', 'words': [
    {'startOffset': '0s', 'endOffset': '1.100s', 'word': 'hi', 'speakerLabel': '2'}]}], 'languageCode': 'pt-br'}]}}
check('Google v2, in its long-running answer: offsets, speaker labels', cues(GOOGLE_V2), ([(0.0, 1.1, 'hi', 'A')], 'pt-br', 'google'))
AZURE = {'source': 'x', 'durationInTicks': 25800000, 'recognizedPhrases': [
    {'recognitionStatus': 'Success', 'channel': 0, 'speaker': 1, 'locale': 'en-US', 'offsetInTicks': 7600000.0,
     'durationInTicks': 13200000.0, 'nBest': [{'display': 'Hello world.', 'displayWords': [
         {'displayText': 'Hello', 'offsetInTicks': 7600000.0, 'durationInTicks': 7600000.0},
         {'displayText': 'world.', 'offsetInTicks': 15200000.0, 'durationInTicks': 5600000.0}]}]},
    {'recognitionStatus': 'Success', 'channel': 0, 'speaker': 2, 'offset': 'PT3S', 'duration': 'PT1.5S',
     'nBest': [{'display': 'No words here.'}]},
    {'recognitionStatus': 'NoMatch', 'channel': 0, 'offsetInTicks': 0, 'durationInTicks': 1, 'nBest': [{'display': 'x'}]}]}
check('Azure: ticks or ISO 8601; display words, else its display text; speakers', cues(AZURE),
      ([(0.76, 2.08, 'Hello world.', 'A'), (3.0, 4.5, 'No words here.', 'B')], 'en-US', 'azure'))
SPEECHMATICS = {'format': '2.9', 'metadata': {'transcription_config': {'language': 'en'},
                                              'language_pack_info': {'word_delimiter': ' '}},
                'results': [{'type': 'word', 'start_time': 0.72, 'end_time': 1.36,
                             'alternatives': [{'content': 'Welcome', 'speaker': 'S1'}]},
                            {'type': 'punctuation', 'start_time': 1.36, 'end_time': 1.36, 'attaches_to': 'previous',
                             'alternatives': [{'content': ',', 'speaker': 'S1'}]},
                            {'type': 'punctuation', 'start_time': 1.4, 'end_time': 1.4, 'attaches_to': 'next',
                             'alternatives': [{'content': '"', 'speaker': 'S1'}]},
                            {'type': 'word', 'start_time': 1.4, 'end_time': 1.9, 'alternatives': [{'content': 'friends', 'speaker': 'S1'}]},
                            {'type': 'word', 'start_time': 2.0, 'end_time': 2.3, 'alternatives': [{'content': 'Hm', 'speaker': 'UU'}]}]}
check('Speechmatics: punctuation on the side it attaches to; S1 a letter, UU nobody', cues(SPEECHMATICS),
      ([(0.72, 1.9, 'Welcome, "friends', 'A'), (2.0, 2.3, 'Hm', None)], 'en', 'speechmatics'))
CJK = {'format': '2.9', 'metadata': {'language_pack_info': {'word_delimiter': ''}}, 'results': [
    {'type': 'word', 'start_time': 0.1, 'end_time': 0.3, 'alternatives': [{'content': '你好'}]},
    {'type': 'word', 'start_time': 0.3, 'end_time': 0.5, 'alternatives': [{'content': '世界'}]}]}
check('Speechmatics in Chinese: no spaces between words', cues(CJK)[0][0][2], '你好世界')
AMARA = [{'start': 830, 'end': 3153, 'text': 'Amara makes video &amp; <i>more</i><br>accessible', 'position': 2,
          'meta': {'new_paragraph': True, 'region': None}},
         {'start': 0, 'end': 800, 'text': 'First by position', 'position': 1, 'meta': {'region': 'top'}},
         {'start': None, 'end': None, 'text': 'Not synced', 'position': 3, 'meta': {}}]
check('Amara: ms, by position, <br> and its tags, HTML entities; at the top; unsynced left out', cues(AMARA),
      ([(0.0, 0.8, '{\\an8}First by position', None), (0.83, 3.153, 'Amara makes video & <i>more</i>\naccessible', None)],
       None, 'amara'))
check("Amara's API answer: its language", cues({'version_number': 3, 'sub_format': 'json', 'subtitles': AMARA[:1],
                                                'language': {'code': 'en', 'name': 'English'}})[1], 'en')

print('writing BCC')
written = json.loads(js.bilibili([
    {'start': 1.0, 'end': 3.0, 'text': '{\\an8}<i>Hello</i>\nthere'},
    {'start': 2.0, 'end': 4.0, 'text': 'Overlapping'},
    {'start': 5.0, 'end': 5.0, 'text': 'Empty in time'},
    {'start': 6.0, 'end': 7.0, 'text': '  '}]))
check("the player's look, as Subtitle Edit writes it", {key: written[key] for key in written if key != 'body'},
      {'font_size': 0.4, 'font_color': '#FFFFFF', 'background_alpha': 0.5, 'background_color': '#9C27B0', 'Stroke': 'none'})
check('plain text, location 2, overlaps made one at a time', written['body'], [
    {'from': 1.0, 'to': 2.0, 'location': 2, 'content': 'Hello\nthere'},
    {'from': 2.0, 'to': 3.0, 'location': 2, 'content': 'Hello\nthere\nOverlapping'},
    {'from': 3.0, 'to': 4.0, 'location': 2, 'content': 'Overlapping'}])
check('read back', cues(written)[0], [(1.0, 2.0, 'Hello\nthere', None), (2.0, 3.0, 'Hello\nthere\nOverlapping', None),
                                      (3.0, 4.0, 'Overlapping', None)])

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.FORMAT, session.SUBTITLE = {}, {'language': 'pt-br', 'segments': []}
(folder / 'assembly.json').write_text(json.dumps(ASSEMBLYAI), encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'assembly.json'))
check("a service's transcript opens; its en_us the project's language", (
    fmt, [(s['text'], s['speaker']) for s in opened], session.SUBTITLE['language']),
      ('JSON', [('Smoke from Canada.', 'A'), ('Yes.', 'B')], 'en-us'))
session.SUBTITLE = {'language': 'pt-br', 'segments': []}
(folder / 'pt.json').write_text(json.dumps({**WHISPER_CPP, 'result': {'language': 'pt'}}), encoding='utf-8')
file_io.process_subtitles_file(str(folder / 'pt.json'))
check('"pt" in a pt-br project: pt-br kept', session.SUBTITLE['language'], 'pt-br')
session.SUBTITLE = {'language': 'pt-br', 'segments': []}
(folder / 'video.en.json3').write_text(json.dumps(YOUTUBE), encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'video.en.json3'))
check("YouTube's .json3: its language from its name; no speaker, Subtitld's A", (
    fmt, len(opened), session.SUBTITLE['language'], opened[0]['speaker']), ('JSON', 4, 'en-us', 'A'))
session.SUBTITLE = {'language': 'en-us', 'segments': []}
(folder / 'whisper.json').write_text(json.dumps({'segments': [{'start': 0, 'end': 1, 'text': ' Hi'}], 'language': 'English'}),
                                     encoding='utf-8')
file_io.process_subtitles_file(str(folder / 'whisper.json'))
session.SUBTITLE['language'] = 'pt-br'
file_io.process_subtitles_file(str(folder / 'whisper.json'))
check('Whisper\'s language by name ("English")', session.SUBTITLE['language'], 'en-us')
(folder / 'in.bcc').write_text(json.dumps(BILIBILI, ensure_ascii=False), encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.bcc'))
check('.bcc: BCC', (fmt, len(opened)), ('BCC', 2))
for name, content, want in (
        ('something else: says so', '{"hello": "world"}', 'This JSON holds no subtitles or transcript Subtitld knows'),
        ('not JSON: where', '{"segments": [\n  oops\n]}', 'Line 2, column 3: Expecting value')):
    (folder / 'bad.json').write_text(content, encoding='utf-8')
    try:
        file_io.process_subtitles_file(str(folder / 'bad.json'))
        check(name, None, 'CorruptedProjectFileError')
    except file_io.CorruptedProjectFileError as error:
        check(name, str(error), want)
session.SUBTITLE = {'language': 'pt-br', 'segments': [
    {'start': 1.0, 'end': 2.0, 'text': '<b>Olá</b>', 'speaker': 'Ana', 'translations': {'en-us': 'Hello'}}]}
session.FORMAT = {'options': {'text': 'both', 'language': 'en-us', 'speakers': 'prefix'}}
file_io.save_file(str(folder / 'out.bcc'), 'BCC')
check('exported: original and translation, speaker before, plain', json.loads((folder / 'out.bcc').read_text(encoding='utf-8'))['body'],
      [{'from': 1.0, 'to': 2.0, 'location': 2, 'content': 'Ana: Olá\nHello'}])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('BCC')
panel._refresh_options()
check('BCC: its text options, no formatting to keep', (panel.format_combo.currentText(), panel.text_options.isHidden(),
                                                       panel.formatting_combo.isHidden()), ('BCC', False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
