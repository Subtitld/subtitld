"""The open subtitle file and its subtitles (modules.segment): session.SUBTITLE
is a Subtitle that still answers as the dict it was, its segments a list
whose every item is a Segment, however it got there — a file of every format
opened, a backup restored, a subtitle from subtitld.cc, an edit, undo, the
plain-text panel, a collaborator's change.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ast, copy, json, os, pickle, re, sys, tempfile, types, zipfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import collab_client, export_options, file_io, history, karaoke, plaintext, quality_check, session, subtitles, subtitldcc_service
from subtitld.modules.segment import (Segment, SegmentList, Subtitle, cut, hyphenator, join_pieces, joined, letters_of,
                                      set_effect, set_text, split_piece, text_of, time_piece, timed_by_words, timed_pieces)
from subtitld.interface import left_panel, left_panel_autosave, left_panel_subtitldcc, top_bar

fails = []


def check(name, got, want):
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f'  (got {got!r}, want {want!r})'))
    if not ok:
        fails.append(name)


def all_segments(segments=None):
    segments = session.SUBTITLE.segments if segments is None else segments
    return bool(segments) and isinstance(segments, SegmentList) and all(type(s) is Segment for s in segments)


TIMED = [{'text': 'Hel', 'start': 0.0, 'end': 0.5}, {'text': 'lo ', 'start': 0.5, 'end': 0.8},
         {'text': 'world', 'start': 1.0, 'end': 1.6}]


def sample():
    return [{'start': 1.0, 'end': 2.5, 'text': 'Hello there', 'speaker': 'A'},
            {'start': 3.0, 'end': 4.5, 'text': 'General Kenobi', 'speaker': 'A'}]


print('text')
check('a string', text_of({'text': 'Hi'}), 'Hi')
check('pieces joined', text_of({'text': TIMED}), 'Hello world')
check('no text', text_of({}), '')
check('None', text_of({'text': None}), '')
check('a text given alone', text_of(TIMED), 'Hello world')
segment = Segment({'start': 0, 'end': 2, 'text': TIMED})
check('Segment.text()', segment.text(), 'Hello world')
check('timed', segment.is_timed(), True)
check('its pieces', segment.pieces(), TIMED)
plain = Segment({'text': 'Hi'})
check('not timed', plain.is_timed(), False)
check('no pieces', plain.pieces(), [])
check('still a dict', isinstance(segment, dict) and segment['start'] == 0, True)

print('the list')
segments = SegmentList([{'text': 'a'}])
segments.append({'text': 'b'})
segments.insert(0, {'text': 'c'})
segments.extend([{'text': 'd'}])
segments += [{'text': 'e'}]
segments[0] = {'text': 'f'}
segments[1:2] = [{'text': 'g'}, {'text': 'h'}]
check('every way in gives a Segment', all_segments(segments), True)
check('in order', [s.text() for s in segments], ['f', 'g', 'h', 'b', 'd', 'e'])
kept = segments[0]
segments.append(kept)
check('a Segment goes in as itself', segments[-1] is kept, True)
check('deep copy', all_segments(copy.deepcopy(segments)), True)
check('pickle', all_segments(pickle.loads(pickle.dumps(segments))), True)
check('JSON', json.loads(json.dumps(segments))[0], {'text': 'f'})

print('the dict it was')
subtitle = Subtitle(segments=sample(), language='en')
check('segments', all_segments(subtitle['segments']), True)
check('the same list as the attribute', subtitle['segments'] is subtitle.segments, True)
check('a field', subtitle['language'], 'en')
check('a missing one', subtitle.get('origin'), None)
check('a missing one, with a default', subtitle.get('metadata', {}), {})
check('in', ('language' in subtitle, 'origin' in subtitle, 'segments' in subtitle), (True, False, True))
try:
    subtitle['origin']
    check('a missing key raises', False, True)
except KeyError:
    check('a missing key raises', True, True)
check('setdefault segments is the list', subtitle.setdefault('segments', []) is subtitle.segments, True)
check('setdefault a field', subtitle.setdefault('metadata', {'title': 'T'}), {'title': 'T'})
check('pop', (subtitle.pop('metadata'), 'metadata' in subtitle), ({'title': 'T'}, False))
check('pop a missing one', subtitle.pop('origin', None), None)
check('keys', sorted(subtitle), ['language', 'segments', 'selected'])
subtitle['segments'] = [{'text': 'x'}]
check('a list given is made Segments', all_segments(subtitle.segments), True)
subtitle['segments'] += [{'text': 'y'}]
check('+= on the key', (all_segments(subtitle.segments), len(subtitle.segments)), (True, 2))
subtitle.segments = sample()
check('the attribute too', all_segments(subtitle.segments), True)
check('to_dict is plain', type(subtitle.to_dict()['segments'][0]) is dict, True)
check('from_dict', all_segments(Subtitle.from_dict(subtitle.to_dict()).segments), True)
subtitle.segments = [dict(s, id=f'id{n}') for n, s in enumerate(sample())]
subtitle.selected = subtitle.segments[1]
detached = file_io._detached(subtitle)
check('a save takes a copy', (type(detached) is Subtitle, detached == subtitle, detached.segments[0] is subtitle.segments[0]),
      (True, True, False))
check('its selected one is among its own', any(s is detached.selected for s in detached.segments), True)
check('equal to the dict it was', Subtitle(language='en') == {'segments': [], 'selected': None, 'language': 'en'}, True)

print('selected')
subtitle = Subtitle(segments=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}])
subtitle.selected = subtitle.segments[1]
check('one in the list', subtitle.selected is subtitle.segments[1], True)
subtitle.selected = {'id': 'a', 'text': 'A'}
check('a copy picks the one with its id', subtitle.selected is subtitle.segments[0], True)
subtitle['selected'] = dict(subtitle.segments[1])
check('as a key too', subtitle.selected is subtitle.segments[1], True)
stray = {'id': 'z', 'text': 'Z'}
subtitle.selected = stray
check('one with no match stays as given', subtitle.selected is stray, True)
subtitle.selected = False
check('False for none', subtitle.selected, False)
check('built with both, the selected one found', Subtitle({'selected': {'id': 'b'}, 'segments': [{'id': 'b'}]}).selected.get('id'), 'b')
check('in and get', ('selected' in Subtitle(), Subtitle().get('selected', 'x')), (True, None))
subtitle.selected = subtitle.segments[0]
subtitle.reset()
check('reset', (list(subtitle.segments), subtitle.selected), ([], None))

print('the file\'s fields')
subtitle = Subtitle(language='en', position=2.5)
check('attributes, None while not set', (subtitle.language, subtitle.position, subtitle.metadata), ('en', 2.5, None))
check('as a mapping, one not set is not there', ('metadata' in subtitle, subtitle.get('metadata', {}), 'language' in subtitle),
      (False, {}, True))
subtitle['translations'] = {'pt': 'x'}
check('set by name, as docops does', subtitle.translations, {'pt': 'x'})
check('popped by name: None again', (subtitle.pop('translations'), subtitle.translations), ({'pt': 'x'}, None))
del subtitle.language
check('deleted: unset, not gone', (subtitle.language, 'language' in subtitle), (None, False))
try:
    subtitle['anything'] = 1
    check('no other name', False, True)
except KeyError:
    check('no other name', True, True)

# The app reads and writes the file's fields as attributes; its mapping is for
# what edits it as a document (modules.docops) and functions given any file.
keyed = []
for path in sorted((Path(__file__).resolve().parents[1] / 'src' / 'subtitld').rglob('*.py')):
    if path.name == 'segment.py':
        continue
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if re.search(r"(?<![\w.])(?:session\.)?SUBTITLE(?:\[|\.get\(|\.pop\(|\.setdefault\()", line) \
                and not line.lstrip().startswith('#'):
            keyed.append(f'{path.name}:{number}')
check('no session.SUBTITLE read by key in the app', keyed, [])

print('session')
check('session.SUBTITLE is a Subtitle', isinstance(session.SUBTITLE, Subtitle), True)

print('every format opened')
folder = tempfile.mkdtemp()
opened, skipped = [], []
for fmt, info in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS.items():
    if fmt == 'BDN':
        continue  # pictures only: written from rendered images, not text
    session.SUBTITLE.segments = sample()
    path = os.path.join(folder, f'{fmt}.{info["extensions"][0]}')
    try:
        file_io.save_file(path, fmt)
        # As the start screen opens a file.
        session.SUBTITLE.segments, _format = file_io.process_subtitles_file(path, fmt)
    except Exception as error:
        skipped.append(f'{fmt} ({error})')
        continue
    if not all_segments():
        check(f'{fmt}: every subtitle a Segment', False, True)
    opened.append(fmt)
print(f'  {len(opened)} formats opened' + (f', skipped: {", ".join(skipped)}' if skipped else ''))
check('every writable format opened', skipped, [])

print('a backup restored')
session.SUBTITLE.segments = sample()
backup = os.path.join(folder, 'backup.usfx')
file_io.save_file(backup, 'USFX')
for module in (left_panel, top_bar):
    module.update = lambda window: None
left_panel_subtitldcc.video_opened = lambda window: None
panel = types.SimpleNamespace(window=lambda: types.SimpleNamespace())
left_panel_autosave._open_backup_into_session(panel, backup)
check('restored', all_segments(), True)

print('a subtitle from subtitld.cc')
usf = os.path.join(folder, 'cloud.usf')
session.SUBTITLE.segments = sample()
file_io.save_file(usf, 'USF')
returned = subtitldcc_service.open_in_editor(types.SimpleNamespace(), Path(usf).read_bytes())
check('opened', all_segments(), True)
check('what it returns is the session\'s', returned is session.SUBTITLE.segments, True)

print('editing and undo')
history.history_clear()
session.SUBTITLE.segments = sample()
session.SUBTITLE.selected = None
subtitles.add_subtitle(position=10.0, text='new')
check('a new subtitle', (all_segments(), len(session.SUBTITLE.segments)), (True, 3))
subtitles.slice_subtitle(selected_subtitle=session.SUBTITLE.segments[0], position=2.0,
                         last_text='Hello', next_text='there')
check('a split', (all_segments(), len(session.SUBTITLE.segments)), (True, 4))
history.history_undo()
check('undone', (all_segments(), len(session.SUBTITLE.segments)), (True, 3))
history.history_redo()
check('redone', (all_segments(), len(session.SUBTITLE.segments)), (True, 4))

print('the plain-text panel')
session.SUBTITLE.segments = sample()
text = plaintext.serialize(session.SUBTITLE.segments, plaintext.SRT) + \
    '\n3\n00:00:06,000 --> 00:00:07,000\nAdded in the panel\n'
plaintext.apply_cues(session.SUBTITLE.segments, plaintext.parse(text, plaintext.SRT).cues)
check('applied', (all_segments(), len(session.SUBTITLE.segments)), (True, 3))

print('a collaborator\'s change')
history.history_clear()
session.SUBTITLE.segments = sample()
collab_client.SessionLive.apply(None, [{'op': 'cue.insert', 'cue': {'id': 'remote0000000001', 'start': 5.0,
                                                                     'end': 5.5, 'text': 'From afar'}}])
check('inserted', (all_segments(), len(session.SUBTITLE.segments)), (True, 3))
check('the snapshot is plain', type(collab_client.SessionLive.snapshot(None)['segments'][0]) is dict, True)

print('timed text read as words')
timed = Segment({'start': 0.0, 'end': 2.0, 'text': TIMED, 'speaker': 'A'})
check('an export\'s copies', [s['text'] for s in export_options.prepare([timed])], ['Hello world'])
check('the plain-text panel', 'Hello world' in plaintext.serialize([timed], plaintext.SRT), True)
check('quality check', quality_check.check_subtitle(timed) is not None, True)

# The subtitles that reach these files and functions may be timed: they read
# a subtitle's words with text_of() or Segment.text(), never its 'text' key.
# Readers of a format, and writers fed by export_options.prepare (which gives
# them the words), may read the key: what they read is always a string.
SUBTITLE_READERS = {
    'interface/left_panel_subtitleslist.py': None, 'interface/subtitle_pictures.py': None,
    'interface/project_thumbnail.py': None, 'interface/subtitle_renderer.py': None,
    'interface/preview_panel.py': None, 'interface/left_panel_translation.py': None,
    'interface/find_replace_dialog.py': None, 'interface/left_panel_vtimeline.py': None,
    'interface/left_panel_dubbing.py': None, 'interface/timeline.py': None, 'modules/subtitles.py': None,
    'modules/quality_check.py': None, 'modules/clone_ref.py': None, 'modules/audio_report.py': None,
    'modules/csv_export.py': None, 'modules/markers.py': None, 'modules/shotcut_project.py': None,
    'modules/openshot_project.py': None, 'modules/ardour_project.py': None, 'modules/video_subtitles.py': None,
    'modules/documents.py': None,
    'interface/export_dialog.py': {'ExportDialog._preview_picture'},
    'modules/plaintext.py': {'serialize', '_json_text', 'full_text', '_differs', '_pair', 'apply_cues'},
    'modules/usf.py': {'USFWriter._build_subtitle'},
    'modules/subtitldcc_service.py': {'usf_for_publishing'},
    'modules/transcripts.py': {'words_of', 'plain_text'},
    'modules/export_options.py': {'prepare'},
    'modules/addons/addon_provider.py': {'AddonTTSProvider.generate_speeches'},
    'modules/addons/builtin/gtts_provider.py': {'_GTTSSpeechThread._generate'},
    'modules/addons/builtin/edge_tts_provider.py': {'_EdgeTTSSpeechThread._generate'},
    'interface/record_controls.py': {'RecordController._untouched', 'RecordController._fill',
                                     'RecordController._fill._apply', 'RecordController._create_placeholder'},
}
# A 'text' key that is not a subtitle's: an export's choice, a theme's colour...
NOT_SUBTITLES = {'names', 'theme', 'self.formats', 'options', 'settings', 'message', 'problem', 'remembered', 'piece'}
source = Path(__file__).resolve().parents[1] / 'src' / 'subtitld'
direct = []
for rel, functions in SUBTITLE_READERS.items():
    code = (source / rel).read_text(encoding='utf-8')
    tree = ast.parse(code)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
                and isinstance(node.slice, ast.Constant) and node.slice.value == 'text':
            receiver = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' \
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'text':
            receiver = node.func.value
        else:
            continue
        if ast.get_source_segment(code, receiver) in NOT_SUBTITLES:
            continue
        if functions is not None:
            names, up = [], node
            while up in parents:
                up = parents[up]
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(up.name)
            if '.'.join(reversed(names)) not in functions:
                continue
        direct.append(f'{rel}:{node.lineno}')
check('no subtitle\'s text read by its key where it may be timed', direct, [])


def spans(value):
    return [(piece['text'], piece['start'], piece['end']) for piece in timed_pieces(value)]


print('split')
segment = Segment({'start': 10.0, 'end': 12.0, 'text': 'Hello <i>big</i> world'})
segment.split('word')
check('words, spaces with the word before, a tag with its word', [p['text'] for p in segment.pieces()],
      ['Hello ', '<i>big</i> ', 'world'])
check('the whole subtitle, shared by letters', spans(segment),
      [('Hello ', 0.0, 0.769), ('<i>big</i> ', 0.769, 1.231), ('world', 1.231, 2.0)])
check('its level', {p['level'] for p in segment.pieces()}, {'word'})
check('the words unchanged', segment.text(), 'Hello <i>big</i> world')
segment.split('letter')
check('letters inside the words\' times', (len(segment.pieces()), spans(segment)[5], spans(segment)[-1][2]),
      (13, ('<i>b', 0.769, 0.923), 2.0))
segment.split('word')
check('and words again from them', spans(segment),
      [('Hello ', 0.0, 0.769), ('<i>big</i> ', 0.769, 1.231), ('world', 1.231, 2.0)])
segment = Segment({'start': 0.0, 'end': 2.0, 'text': 'karaoke time'})
segment.split('syllable', hyphenate=lambda word: list(range(2, len(word), 2)))
check('syllables where hyphenate says', [p['text'] for p in segment.pieces()], ['ka', 'ra', 'ok', 'e ', 'ti', 'me'])
segment = Segment({'start': 0.0, 'end': 2.0, 'text': 'karaoke time'})
segment.split('syllable')
check('without it, words', [p['text'] for p in segment.pieces()], ['karaoke ', 'time'])
segment.unsplit()
check('unsplit', segment['text'], 'karaoke time')
segment = Segment({'start': 0.0, 'end': 1.0, 'text': [dict(TIMED[0], effect='wipe'), *TIMED[1:]]})
segment.split('letter')
check('a piece cut keeps its effect', [p.get('effect') for p in segment.pieces()][:4], ['wipe', 'wipe', 'wipe', None])

print('syllables by the subtitle\'s language')
segment = Segment({'start': 0.0, 'end': 3.0, 'text': 'Cantando junto a palavra'})
segment.split('syllable', hyphenator('pt-br'))
check('Portuguese', [p['text'] for p in segment.pieces()], ['Can', 'tan', 'do ', 'jun', 'to ', 'a ', 'pa', 'la', 'vra'])
check('a region without a dictionary of its own: its language\'s', hyphenator('de-de')('Liederabend'), [3, 6])
check('a language without one: none (split gives words)', (hyphenator('ja-jp'), hyphenator(''), hyphenator(None)),
      (None, None, None))
check('a one-letter syllable at a word\'s start, not at its end', hyphenator('en-us')('along'), [1])

print('a piece cut, joined, timed')
segment = Segment({'start': 0.0, 'end': 3.0, 'text': [{'text': '<i>Hello</i> ', 'start': 0.0, 'end': 1.0, 'level': 'word'},
                                                      {'text': 'world', 'start': 1.5, 'end': 2.5}]})
check('its letters, where each is in the text', letters_of(segment.pieces()[0]['text'])[:2], [(3, 'H'), (4, 'e')])
check('cut before a letter', split_piece(segment, 0, 6), True)
check('its time shared by letters, the tag kept with each half', spans(segment)[:2],
      [('<i>Hel', 0.0, 0.6), ('lo</i> ', 0.6, 1.0)])
check('not at its very start or end', (split_piece(segment, 2, 0), split_piece(segment, 2, 5), split_piece(segment, 9, 1)),
      (False, False, False))
check('joined again', (join_pieces(segment, 0), spans(segment)[0]), (True, ('<i>Hello</i> ', 0.0, 1.0)))
check('the last has no next to join', join_pieces(segment, 1), False)
check('a start kept after the piece before', time_piece(segment, 1, start=0.2), (1.0, 2.5))
check('an end kept within the subtitle', time_piece(segment, 1, end=9.0), (1.0, 3.0))
check('an end kept before the next piece', time_piece(segment, 0, end=5.0), (0.0, 1.0))
check('a start past its end takes its end', time_piece(segment, 1, start=2.0), (2.0, 3.0))
set_effect(segment, 0, 'wipe')
check('an effect', segment.pieces()[0].get('effect'), 'wipe')
set_effect(segment, 0, None)
check('and the style\'s again', 'effect' in segment.pieces()[0], False)

print('timed by an engine\'s words')
WORDS = [{'text': 'hello', 'start': 10.0, 'end': 10.4}, {'text': 'big', 'start': 10.5, 'end': 10.7},
         {'text': 'World', 'start': 11.0, 'end': 11.6}]
check('each word found in the text, punctuation and markup kept with it',
      spans({'text': timed_by_words('"Hello, <i>big</i> world!"', WORDS, 10.0)}),
      [('"Hello, ', 0.0, 0.4), ('<i>big</i> ', 0.5, 0.7), ('world!"', 1.0, 1.6)])
check('a word the engine left out stays with the one before',
      [p['text'] for p in timed_by_words('Hello there big world', WORDS, 10.0)], ['Hello there ', 'big ', 'world'])
check('none found: none', timed_by_words('nothing alike', WORDS, 10.0), None)
check('overlapping times kept in order', spans({'text': timed_by_words('a b', [{'text': 'a', 'start': 0, 'end': 1},
                                                                                {'text': 'b', 'start': 0.5, 'end': 2}])}),
      [('a ', 0.0, 1.0), ('b', 1.0, 2.0)])

print('set_text')
segment = Segment({'start': 0.0, 'end': 3.0, 'text': [dict(piece) for piece in TIMED]})
segment.set_text('Hello, world!')
check('typed inside and at the end of pieces', spans(segment),
      [('Hel', 0.0, 0.5), ('lo, ', 0.5, 0.8), ('world!', 1.0, 1.6)])
segment.set_text('ello, world!')
check('a letter deleted', spans(segment)[0], ('el', 0.0, 0.5))
segment.set_text('Oh, ello, world!')
check('typed at the very start: the first piece', spans(segment)[0], ('Oh, el', 0.0, 0.5))
segment = Segment({'start': 0.0, 'end': 3.0, 'text': [dict(piece) for piece in TIMED]})
segment.set_text('Hel world')
check('a piece deleted whole: its time to the one before', spans(segment), [('Hel ', 0.0, 0.8), ('world', 1.0, 1.6)])
segment.set_text('world')
check('the first deleted: its time to the next', spans(segment), [('world', 0.0, 1.6)])
segment = Segment({'start': 0.0, 'end': 2.0, 'text': 'Hello big world'})
segment.split('word')
segment.set_text('Hello big wide world')
check('a word typed inside a word piece is cut into its own', [p['text'] for p in segment.pieces()],
      ['Hello ', 'big ', 'wide ', 'world'])
segment.set_text('Totally different text here')
check('mostly new words: timed again over the old span', (spans(segment)[0][1], spans(segment)[-1][2],
                                                          len(segment.pieces())), (0.0, 2.0, 4))
segment.set_text('')
check('no words: a string', segment['text'], '')
plain = {'text': 'old'}
set_text(plain, 'new')
check('a text not timed is just set', plain['text'], 'new')

print('merged and cut')
first = Segment({'start': 1.0, 'end': 2.0, 'text': [{'text': 'one', 'start': 0.0, 'end': 0.5}]})
second = Segment({'start': 3.0, 'end': 4.0, 'text': 'two'})
check('merged: the later one moved, not timed one piece over its subtitle', spans({'text': joined(first, second)}),
      [('one ', 0.0, 0.5), ('two', 2.0, 3.0)])
check('neither timed: a string', joined({'text': 'a'}, {'text': 'b'}), 'a b')
whole = Segment({'start': 0.0, 'end': 3.0, 'text': [dict(piece) for piece in TIMED]})
before, after = cut(whole, 0.9)
check('cut: the pieces that start before', spans({'text': before}), [('Hel', 0.0, 0.5), ('lo', 0.5, 0.8)])
check('and after, from where it is cut', spans({'text': after}), [('world', 0.1, 0.7)])
check('nothing on one side: not cut', cut(whole, 5.0), None)

print('karaoke codes')
check('read as pieces', karaoke.pieces_of(r'{\k50}Hel{\k30}lo {\k20}{\k60}world'),
      [{'text': 'Hel', 'start': 0.0, 'end': 0.5}, {'text': 'lo ', 'start': 0.5, 'end': 0.8},
       {'text': 'world', 'start': 1.0, 'end': 1.6}])
check('effects and the placing code', karaoke.pieces_of(r'{\an8}{\kf50}Hi {\ko30}<i>there</i>'),
      [{'text': r'{\an8}Hi ', 'start': 0.0, 'end': 0.5, 'effect': 'wipe'},
       {'text': '<i>there</i>', 'start': 0.5, 'end': 0.8, 'effect': 'outline'}])
check('karaoke mixed with another code in a block: left as written', karaoke.pieces_of(r'{\k50\i1}x'), None)
for written in (r'{\k50}Hel{\k30}lo {\k20}{\k60}world', r'{\an8}{\kf50}Hi {\ko30}<i>there</i>'):
    check(f'written back: {written}', karaoke.codes_of(karaoke.pieces_of(written)), written)
timed = Segment({'start': 1.0, 'end': 3.0, 'speaker': 'Ann', 'text': [dict(TIMED[0], effect='wipe'), *TIMED[1:]]})
check('an export to a karaoke format: codes', export_options.prepare([timed], {}, karaoke_codes=True)[0]['text'],
      r'{\kf50}Hel{\k30}lo {\k20}{\k60}world')
check('the speaker\'s name before them', export_options.prepare([timed], {'speakers': 'prefix'}, karaoke_codes=True)[0]['text'],
      r'Ann: {\kf50}Hel{\k30}lo {\k20}{\k60}world')
check('to another format: the words', export_options.prepare([timed], {})[0]['text'], 'Hello world')


def saved_and_opened(fmt, extension):
    path = os.path.join(folder, f'timed.{extension}')
    file_io.save_file(path, fmt)
    return file_io.process_subtitles_file(path, fmt)[0], path


KARAOKE = [{'id': 'aaaa0000aaaa0001', 'start': 1.0, 'end': 3.0, 'speaker': 'A',
            'text': [{'text': '{\\an8}<i>Hel', 'start': 0.0, 'end': 0.5, 'effect': 'wipe', 'level': 'syllable'},
                     {'text': 'lo</i> ', 'start': 0.5, 'end': 0.8, 'level': 'syllable'},
                     {'text': 'world', 'start': 1.0, 'end': 1.6, 'level': 'syllable'}]},
           {'id': 'aaaa0000aaaa0002', 'start': 4.0, 'end': 5.0, 'speaker': 'A', 'text': 'Not timed'}]
print('kept in a .usfx')
session.FORMAT = {}
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('USFX', 'usfx')
check('timed as it was, effect and level', [s['text'] for s in opened], [s['text'] for s in KARAOKE])
check('a <karaoke> with a <k> a piece and a pause', '<k subtitldcc:effect="wipe"' in Path(path).read_bytes().decode('latin-1')
      or b'<k' in zipfile.ZipFile(path).read('subtitles.usf'), True)
usf_text = zipfile.ZipFile(path).read('subtitles.usf').decode('utf-8')
check('lengths in milliseconds adding up to the subtitle\'s', [int(t) for t in re.findall(r' t="(\d+)"', usf_text)],
      [500, 300, 200, 600, 400])

print('ASS, LRC and UltraStar')
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('ASS', 'ass')
check('ASS: written as codes', r'{\an8}{\kf50}{\i1}Hel{\k30}lo{\i0} {\k20}{\k60}world' in Path(path).read_text(encoding='utf-8-sig'),
      True)
check('ASS: opened as pieces', spans(opened[0]), spans(KARAOKE[0]))
check('ASS: their effect', opened[0]['text'][0].get('effect'), 'wipe')
session.SUBTITLE.segments = opened
again, path = saved_and_opened('USFX', 'usfx')
session.SUBTITLE.segments = again
again, path = saved_and_opened('ASS', 'ass')
check('ASS to USFX to ASS', spans(again[0]), spans(KARAOKE[0]))
session.SUBTITLE.segments = [dict(KARAOKE[0], text='Hello world', id='x1')]
session.SUBTITLE.segments[0].split('word')
words = spans(session.SUBTITLE.segments[0])
opened, path = saved_and_opened('LRC', 'lrc')
check('LRC: word stamps opened as pieces', [(t, round(a, 2)) for t, a, _b in spans(opened[0])], [(t, round(a, 2)) for t, a, _b in words])
session.SUBTITLE.segments = opened
again, path = saved_and_opened('USFX', 'usfx')
session.SUBTITLE.segments = again
again, path = saved_and_opened('LRC', 'lrc')
check('LRC to USFX to LRC', [(t, round(a, 2)) for t, a, _b in spans(again[0])], [(t, round(a, 2)) for t, a, _b in words])
session.SUBTITLE.segments = [dict(KARAOKE[0], text='Hello world', id='x1')]
session.SUBTITLE.segments[0].split('word')
opened, path = saved_and_opened('ULTRASTAR', 'txt')
check('UltraStar: its notes opened as pieces', [p['text'].strip() for p in timed_pieces(opened[0])], ['Hello', 'world'])

print('WebVTT, TTML and the others')
session.FORMAT = {}
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('VTT', 'vtt')
check('WebVTT: a timestamp before each piece but the first', '<i>Hel<00:00:01.500>lo</i> <00:00:02.000>world'
      in Path(path).read_text(encoding='utf-8'), True)
check('read as pieces, each to the next (WebVTT has no pause)', spans(opened[0]),
      [('{\\an8}<i>Hel', 0.0, 0.5), ('lo</i> ', 0.5, 1.0), ('world', 1.0, 2.0)])
check('a subtitle not timed stays a string', opened[1]['text'], 'Not timed')
session.FORMAT = {'options': {'formatting': 'remove'}}
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('VTT', 'vtt')
check('without formatting, no timing', ('<00:' in Path(path).read_text(encoding='utf-8'), opened[0]['text']),
      (False, 'Hello world'))
session.FORMAT = {}
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('TTML', 'ttml')
check('TTML: the words, as a timed span would hide them', ('begin="00:00:00.500"' in Path(path).read_text(encoding='utf-8'),
                                                            text_of(opened[0])), (False, '{\\an8}<i>Hello</i> world'))
session.FORMAT = {'options': {'timed': 'reveal'}}
opened, path = saved_and_opened('TTML', 'ttml')
written = Path(path).read_text(encoding='utf-8')
check('revealed: a span a piece, begun when it is, to the paragraph\'s end',
      ('<span begin="00:00:00.500">' in written, '<span begin="00:00:01.000">world</span>' in written,
       'end="00:00:00.800"' in written), (True, True, False))
check('read back as pieces', [(t, a, b) for t, a, b in spans(opened[0])],
      [('{\\an8}<i>Hel</i>', 0.0, 0.5), ('<i>lo</i> ', 0.5, 1.0), ('world', 1.0, 2.0)])
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
opened, path = saved_and_opened('SRT', 'srt')
check('SRT revealed: a subtitle a piece, the words up to it', [(s['start'], s['end'], s['text']) for s in opened],
      [(1.0, 1.5, '{\\an8}<i>Hel</i>'), (1.5, 2.0, '{\\an8}<i>Hello</i>'), (2.0, 3.0, '{\\an8}<i>Hello</i> world'),
       (4.0, 5.0, 'Not timed')])
session.FORMAT = {'options': {'timed': 'reveal', 'text': 'translation', 'language': 'pt'}}
session.SUBTITLE.segments = [dict(KARAOKE[0], translations={'pt': 'Olá mundo'})]
opened, path = saved_and_opened('SRT', 'srt')
check('a translation is not revealed', [s['text'] for s in opened], ['Olá mundo'])
session.FORMAT = {}

print('the export dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
panel = _SubtitlesPanel()
shown = {}
for fmt in ('SRT', 'TTML', 'ASS', 'VTT', 'LRC'):
    panel.format_combo.setCurrentText(fmt)
    panel._refresh_options()
    shown[fmt] = not panel.timed_combo.isHidden()
check('offered where the format has no timing of its own', shown,
      {'SRT': True, 'TTML': True, 'ASS': False, 'VTT': False, 'LRC': False})
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
panel.timed_combo.combobox.setCurrentIndex(1)
check('the choice is an option of the export', panel.get_config()['options'].get('timed'), 'reveal')
check('remembered', _SubtitlesPanel().timed_combo.combobox.currentData(), 'reveal')
session.SUBTITLE.segments = [{'start': 0.0, 'end': 1.0, 'text': 'Not timed'}]
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not offered for a project with nothing timed', panel.timed_combo.isHidden(), True)

print('merging and cutting in the editor')
history.history_clear()
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
subtitles.merge_next_subtitle(selected_subtitle=session.SUBTITLE.segments[0])
merged = session.SUBTITLE.segments[0]
check('merged: one subtitle, both timed', (len(session.SUBTITLE.segments), spans(merged)[-1]),
      (1, ('Not timed', 3.0, 4.0)))
check('its words', merged.text(), '{\\an8}<i>Hello</i> world Not timed')
history.history_clear()
session.SUBTITLE.segments = copy.deepcopy(KARAOKE)
subtitles.slice_subtitle(selected_subtitle=session.SUBTITLE.segments[0], position=1.9, last_text='x', next_text='y')
check('cut where the time is', [spans(s) for s in session.SUBTITLE.segments[:2]],
      [[('{\\an8}<i>Hel', 0.0, 0.5), ('lo</i>', 0.5, 0.8)], [('{\\an8}world', 0.1, 0.7)]])
session.SUBTITLE.segments = [dict(KARAOKE[0], text='Hello world')]
session.SUBTITLE.segments[0].split('word')
plaintext.apply_cues(session.SUBTITLE.segments,
                     plaintext.parse(plaintext.serialize(session.SUBTITLE.segments, plaintext.SRT).replace('world', 'wide world'),
                                     plaintext.SRT).cues)
check('edited in the plain-text panel: still timed', [p['text'] for p in timed_pieces(session.SUBTITLE.segments[0])][-2:],
      ['wide ', 'world'])

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
