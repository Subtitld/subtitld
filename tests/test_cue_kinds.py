"""Marks and titles: cues of other kinds in the subtitles' list
(modules.cue_kinds). Open-ended marks run to the next of their category,
categories are the app's and travel with the project, USF keeps every kind
and matches a translation to its kind's cue, and the formats of subtitles
get the subtitles (and the titles, where they can and the export asks).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
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
from subtitld.modules import cue_kinds, file_io, session, usf
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = {}
session.VIDEO = {'duration': 100.0}
session.SUBTITLE = Subtitle(segments=[])

print('kinds')
cues = [{'start': 1, 'end': 2, 'text': 'a'}, {'kind': 'mark', 'start': 0, 'end': 0, 'text': 'm'},
        {'kind': 'title', 'start': 0, 'end': 5, 'text': 't'}, {'kind': 'nonsense', 'start': 3, 'end': 4, 'text': 'b'}]
check('kind_of', [cue_kinds.kind_of(c) for c in cues], ['subtitle', 'mark', 'title', 'subtitle'])
check('subtitles()', [c['text'] for c in cue_kinds.subtitles(cues)], ['a', 'b'])
check('Segment.kind()', [s.kind() for s in Subtitle(segments=cues).segments], ['subtitle', 'mark', 'title', 'subtitle'])

print('open ends: none kept; the next of the same category where a format needs one')
marks = [cue_kinds.new_mark(10, 'One'), cue_kinds.new_mark(40, 'Two'), cue_kinds.new_mark(20, 'Note', 'note'),
         cue_kinds.new_mark(30, 'Closed', 'chapter', end=35), cue_kinds.new_mark(50, 'Note 2', 'note')]
check('a mark with no end is a point', [(m['end'], cue_kinds.has_end(m)) for m in marks],
      [(10.0, False), (40.0, False), (20.0, False), (35.0, True), (50.0, False)])
resolved = cue_kinds.ends(marks)
check('ends for a format', [resolved[id(m)] for m in marks], [30.0, 100.0, 50.0, 35.0, 100.0])
check('nothing changed', [m['end'] for m in marks], [10.0, 40.0, 20.0, 35.0, 50.0])
check('no media: a last mark ends where it starts', cue_kinds.ends([marks[0]], duration=0)[id(marks[0])], 10.0)

print('categories')
check('built-in four', list(cue_kinds.categories()), ['chapter', 'note', 'todo', 'comment'])
mine = cue_kinds.add_category('Music cue', '#ff123456')
check('a new one', (mine, cue_kinds.categories()[mine]['color']), ('music-cue', '#ff123456'))
check('stored in the app', session.CONFIG['marks']['categories'][mine]['name'], 'Music cue')
cue_kinds.set_category('chapter', color='#ff00ff00')
check('a built-in recoloured', cue_kinds.category('chapter')['color'], '#ff00ff00')
cue_kinds.set_category('chapter', color=cue_kinds.BUILTIN_CATEGORIES['chapter']['color'])
check('set back: nothing kept', session.CONFIG['marks'].get('categories', {}).get('chapter'), None)
check('builtin name translated', cue_kinds.display_name('todo', lambda key: {'marks.category.todo': 'A fazer'}.get(key, key)),
      'A fazer')
cue_kinds.set_category('todo', name='Fix')
check('renamed: not translated', cue_kinds.display_name('todo', lambda key: 'A fazer'), 'Fix')
cue_kinds.set_category('todo', name='To-do')
check('unknown category', (cue_kinds.category('zzz')['name'], cue_kinds.category('zzz')['color']),
      ('zzz', cue_kinds.UNKNOWN_COLOR))

print('the project carries its categories')
project = [cue_kinds.new_mark(1, 'x', mine), cue_kinds.new_mark(2, 'y', 'note'), {'start': 0, 'end': 1, 'text': 's'}]
table = cue_kinds.project_table(project)
check('table', table, {mine: {'name': 'Music cue', 'color': '#ff123456'},
                       'note': {'name': 'Note', 'color': cue_kinds.BUILTIN_CATEGORIES['note']['color']}})
session.CONFIG = {}
adopted = cue_kinds.adopt(table)
check('another computer learns it', cue_kinds.categories()[mine]['name'], 'Music cue')
session.SUBTITLE.mark_categories = {mine: {'name': 'Music cue', 'color': '#ffabcdef'}}
check("the project's colour draws its marks", cue_kinds.category(mine)['color'], '#ffabcdef')
cue_kinds.set_category(mine, color='#ff111111')
check('recolouring in the app reaches the open project', session.SUBTITLE.mark_categories[mine]['color'], '#ff111111')
session.SUBTITLE.mark_categories = None

print('USF keeps every kind')
segments = [
    {'id': 's1', 'start': 1.0, 'end': 3.0, 'text': 'Hello', 'speaker': 'A', 'translations': {'pt-br': 'Olá'}},
    dict(cue_kinds.new_mark(1.0, 'Opening'), id='m1', translations={'pt-br': 'Abertura'}),
    {'id': 't1', 'kind': 'title', 'start': 0.5, 'end': 4.0, 'text': 'Lisbon', 'speaker': 'A',
     'title_theme': 'subtitld.plain', 'title_settings': {'font_size': 72}},
    dict(cue_kinds.new_mark(5.0, 'Fix this', 'todo', end=6.0), id='m2'),
]
content = usf.USFWriter().write(segments, language='en-us', mark_categories=cue_kinds.project_table(segments))
reader = usf.USFReader()
back = {s['id']: s for s in reader.read(content)}
check('mark', {k: back['m1'].get(k) for k in ('kind', 'category', 'open_end', 'text', 'end')},
      {'kind': 'mark', 'category': 'chapter', 'open_end': True, 'text': 'Opening', 'end': 1.0})
check('closed mark', (back['m2'].get('open_end'), back['m2']['end'], back['m2']['category']), (None, 6.0, 'todo'))
check('title', {k: back['t1'].get(k) for k in ('kind', 'title_theme', 'title_settings')},
      {'kind': 'title', 'title_theme': 'subtitld.plain', 'title_settings': {'font_size': 72}})
check('subtitle', back['s1'].get('kind'), None)
check('translations go to their kind', (back['s1'].get('translations'), back['m1'].get('translations')),
      ({'por': 'Olá'}, {'por': 'Abertura'}))  # no language_of: the file's code
check('categories in the metadata', sorted(reader.mark_categories), ['chapter', 'todo'])

print('formats of subtitles get the subtitles')
session.SUBTITLE = Subtitle(segments=segments)
folder = tempfile.mkdtemp()
session.FORMAT = {'format': 'SRT', 'options': {}}
file_io.save_file(os.path.join(folder, 'a.srt'), 'SRT')
srt_text = open(os.path.join(folder, 'a.srt'), encoding='utf-8').read()
check('SRT: only the subtitle', ('Hello' in srt_text, 'Opening' in srt_text, 'Lisbon' in srt_text), (True, False, False))
session.FORMAT = {'format': 'ASS', 'options': {}}
file_io.save_file(os.path.join(folder, 'a.ass'), 'ASS')
check('ASS without titles asked', 'Lisbon' in open(os.path.join(folder, 'a.ass'), encoding='utf-8').read(), False)
session.FORMAT = {'format': 'ASS', 'options': {'titles': True}}
file_io.save_file(os.path.join(folder, 'b.ass'), 'ASS')
ass_text = open(os.path.join(folder, 'b.ass'), encoding='utf-8').read()
check('ASS with titles', ('Lisbon' in ass_text, 'Opening' in ass_text), (True, False))
check('the session keeps them all', len(session.SUBTITLE.segments), 4)

print('marks as chapters')
from subtitld.modules import chapters
project = [cue_kinds.new_mark(0, 'Intro'), cue_kinds.new_mark(65, 'Middle'), cue_kinds.new_mark(30, 'A note', 'note'),
           cue_kinds.new_mark(80, 'End=part; #2')]
found = chapters.marks_for(project, ['chapter'], duration=120)
check('chapters run to the next, the last to the end', [(m['start'], m['end']) for m in found],
      [(0.0, 65.0), (65.0, 80.0), (80.0, 120.0)])
check('WebVTT', chapters.write_vtt(found).split('\n')[:4], ['WEBVTT', '', '1', '00:00:00.000 --> 00:01:05.000'])
check('FFmpeg metadata escapes', 'title=End\\=part\\; \\#2' in chapters.ffmetadata(found), True)
check('YouTube', chapters.write_youtube(found), '0:00 Intro\n1:05 Middle\n1:20 End=part; #2\n')
check('YouTube: all long enough, three', chapters.youtube_problems(found), [])
check('YouTube: too few', chapters.youtube_problems(found[:2]), ['fewer_than_three'])
xml = chapters.write_matroska(found)
check('Matroska', ('<ChapterTimeStart>00:01:05.000000000</ChapterTimeStart>' in xml, '<ChapterString>Middle' in xml),
      (True, True))
check('the marks keep no end', project[0]['end'], 0.0)

print()
print('FAILED: ' + ', '.join(fails) if fails else 'all passed')
sys.exit(1 if fails else 0)
