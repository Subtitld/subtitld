"""The open subtitle file and its subtitles (modules.segment): session.SUBTITLE
is a Subtitle that still answers as the dict it was, its segments a list
whose every item is a Segment, however it got there — a file of every format
opened, a backup restored, a subtitle from subtitld.cc, an edit, undo, the
plain-text panel, a collaborator's change.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ast, copy, json, os, pickle, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import collab_client, export_options, file_io, history, plaintext, quality_check, session, subtitles, subtitldcc_service
from subtitld.modules.segment import Segment, SegmentList, Subtitle, text_of
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
NOT_SUBTITLES = {'names', 'theme', 'self.formats', 'options', 'settings', 'message', 'problem', 'remembered'}
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

print()
print('FAILED: ' + ', '.join(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
