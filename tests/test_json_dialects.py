"""The platforms' JSON (modules.json_dialects): reading each shape as
Subtitle Edit's readers take it, writing the ones it writes and reading them
back, leaving alone what json_subtitles reads, Subtitld's own JSON and
Aeneas's sync maps, and the file and the export dialog.

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
from subtitld.modules import file_io, json_dialects as jd, plaintext, session
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(data, fps=None):
    segments, language, dialect = jd.read(data, fps=fps)
    return dialect, [(s['start'], s['end'], s['text']) for s in segments], language


# Each shape as the comment, the reader or the writer of Subtitle Edit's
# class for it lays one out.
SAMPLES = {
    'wistia': ({'captions': [{'bcp47LanguageTag': 'de', 'hash': {'lines': [
        {'start': 0.17, 'end': 0.62, 'text': ['line 1', 'line 2']}, {'start': 1, 'end': 2, 'text': 'old string'}]}},
        {'bcp47LanguageTag': 'fr', 'hash': {'lines': [{'start': 5, 'end': 6, 'text': ['autre']}]}}]},
        [(0.17, 0.62, 'line 1\nline 2'), (1.0, 2.0, 'old string')], 'de'),
    'ted': ({'captions': [{'duration': 2500, 'content': 'Tom<br />talks', 'startOfParagraph': True, 'startTime': 1000}]},
            [(1.0, 3.5, 'Tom\ntalks')], None),
    'holistar': ({'SubtitleLanguages': [{'ClassName': 'en', 'SubtitleItems': [
        {'TextLines': ['One', 'Two'], 'ClassName': 'en', 'ShowTime': 1000, 'HideTime': 2000}]}]},
        [(1.0, 2.0, 'One\nTwo')], 'en'),
    'twentythree': ({'subtitles': [{'text': ['a', 'b'], 'timestamp_begin': 1.5, 'timestamp_end': 2.5}]},
                    [(1.5, 2.5, 'a\nb')], None),
    'twentythree-embed': ({'data': '{ "subtitles": [ { "text": [ "x" ], "timestamp_begin": 1, "timestamp_end": 2 } ] }'},
                          [(1.0, 2.0, 'x')], None),
    'twentythree-websrt': ({'data': {'websrt': '1\n00:00:01,000 --> 00:00:02,000\nSRT inside\n'}},
                           [(1.0, 2.0, 'SRT inside')], None),
    'podcastindex': ({'version': '1.0.0', 'segments': [{'speaker': 'Ann', 'startTime': 0.5, 'endTime': 1.5, 'body': 'Hi'}]},
                     [(0.5, 1.5, 'Hi')], None),
    'suburbia': ({'header': {'subtitleId': 'x'}, 'subtitles': [{'start': 1000, 'end': 1500, 'text': 'Sub', 'comment': 'c'}]},
                 [(1.0, 1.5, 'Sub')], None),
    'videoindexer': ({'videos': [{'insights': {'transcript': [
        {'id': 1, 'text': 'Indexed', 'instances': [{'start': '0:00:01.5', 'end': '0:00:03.25'}]}]}}]},
        [(1.5, 3.25, 'Indexed')], None),
    'archtime': ({'atpjfile_info': {}, 'BLOCKS': [{'time_start': 1000, 'time_end': 2000, 'text': 'Arc%20time'}]},
                 [(1.0, 2.0, 'Arc time')], None),
    'clqtt': ({'meta': {'timeFormat': 'FRAMES', 'fps': 'FPS_2500'}, 'events': [
        {'eventType': 'TIMED_TEXT_EVENT', 'start': 25, 'end': 75, 'txt': 'Top words', 'rgn': 'top',
         'styles': [{'type': 'italic', 'from': 4, 'to': 9}]}]},
        [(1.0, 3.0, '{\\an8}Top <i>words</i>')], None),
    'capcut': ({'canvas_config': {}, 'materials': {'texts': [{'id': 'A', 'content': 'Cut'}]},
                'tracks': [{'type': 'text', 'segments': [{'material_id': 'A',
                                                          'target_timerange': {'start': 1000000, 'duration': 500000}}]}]},
               [(1.0, 1.5, 'Cut')], None),
    'seriencamp': ({'captiondata': [{'data': [{'fromms': 1000, 'toms': 2000, 'caption': 'Serie<br />camp'}]}]},
                   [(1.0, 2.0, 'Serie\ncamp')], None),
    'gts': ({'subtitles': [{'content': [{'text': ['GTS', 'pro']}], 'tc_in': {'value': '00:00:01:00'},
                            'tc_out': {'value': '00:00:02:12'}}]},
            [(1.0, 2.48, 'GTS pro')], None),
    'ooona': ({'Paragraphs': [{'Start': 1, 'End': 2, 'VAlign': 'Top', 'Lines': [
        {'Text': '<span style="font-style:italic">Ooona</span>'}, {'Text': 'two'}]}]},
        [(1.0, 2.0, '{\\an8}<i>Ooona</i>\ntwo')], None),
    'type4': ([{'hitType': 'tag', 'startTime': 1, 'endTime': 2, 'metadata': {'Text': 'Four', 'Language': 'en'}}],
              [(1.0, 2.0, 'Four')], 'en'),
    'type5': ({'text_tees': [1000, 2000, 3000, 4000], 'text_content': [['Five', 'a'], ['', ''], ['Six']]},
              [(1.0, 2.0, 'Five\na'), (3.0, 4.0, 'Six')], None),
    'words-time-duration': ({'words': [{'time': 1, 'duration': 0.5, 'name': 'Word'}, {'time': 1.5, 'duration': 0.2, 'name': '.'}]},
                            [(1.0, 1.7, 'Word.')], None),
    'type7': ({'start': [1000], 'end': [2000], 'text': ['Seven']}, [(1.0, 2.0, 'Seven')], None),
    'type10': ({'vostf': [{'startTime': 1, 'endTime': 2, 'text': 'Vostf'}]}, [(1.0, 2.0, 'Vostf')], None),
    'type13': ({'job': {'lang': 'en'}, 'speakers': [{'duration': '1', 'name': 'Job', 'time': '2'}]},
               [(2.0, 3.0, 'Job')], 'en'),
    'type14': ({'captionGroups': [{'t': 'Group', 's': 1, 'e': 2}]}, [(1.0, 2.0, 'Group')], None),
    'type16': ([{'startTime': {'h': 0, 'm': 0, 's': 1, 'f': 0}, 'endTime': {'h': 0, 'm': 0, 's': 2, 'f': 5}, 'text': 'Frames'}],
               [(1.0, 2.2, 'Frames')], None),
    'type23': ({'titulky': [{'s': 1, 'd': 1.5, 'n': 'Titulky'}]}, [(1.0, 2.5, 'Titulky')], None),
    'ted-list': ([{'duration': 1000, 'content': 'Three', 'startOfParagraph': False, 'startTime': 1000}],
                 [(1.0, 2.0, 'Three')], None),
    'type2': ([{'startMillis': 1000, 'endMillis': 2000, 'text': 'Two'}], [(1.0, 2.0, 'Two')], None),
    'type24': ([{'id': 13077087, 'start_time': 192665, 'end_time': 193887, 'subtitle_language_code': 'en',
                 'subtitle_id': 27992065, 'subtitle_content': 'Hi.'}], [(192.665, 193.887, 'Hi.')], None),
    'type8': ([{'start_time': 1.5, 'end_time': 3, 'text': 'Seconds'}], [(1.5, 3.0, 'Seconds')], None),
    'type9': ([{'index': 1, 'start': '00:00:01,000', 'end': '00:00:02,500', 'text': ['CBT', 'Nuggets']}],
              [(1.0, 2.5, 'CBT\nNuggets')], None),
    'type11': ([{'milliseconds': 1000, 'line': 'First'}, {'milliseconds': 1500, 'line': 'Second'}],
               [(1.0, 1.5, 'First'), (1.5, 2.5, 'Second')], None),
    'type12': ([{'t1': 1000, 't2': 2000, 't': 'Twelve'}], [(1.0, 2.0, 'Twelve')], None),
    'type15': ([{'displayTimeOffset': 1, 'text': 'Offset'}], [(1.0, 2.0, 'Offset')], None),
    'type17': ([{'text': ['Seven', 'teen'], 'index': 0, 'start': 1000, 'end': 2000}], [(1.0, 2.0, 'Seven\nteen')], None),
    'type18': ([{'s': 1, 'd': 2, 'n': 'Eighteen'}], [(1.0, 3.0, 'Eighteen')], None),
    'type19': ([{'tStartMs': 1000, 'dDurationMs': 500, 'segs': [{'utf8': 'Nine'}, {'utf8': 'teen'}]}],
               [(1.0, 1.5, 'Nine teen')], None),
    'type21': ([{'timestamp': [1, 2], 'text': 'Chunk'}], [(1.0, 2.0, 'Chunk')], None),
    'gremots': ([{'st': 1000, 'et': 2000, 'tt': 'Gremots'}], [(1.0, 2.0, 'Gremots')], None),
}

print('reading')
for dialect, (data, want, language) in SAMPLES.items():
    check(f'{dialect}: found by its keys and read', cues(data), (dialect, want, language))
check('JSON Type 8 in milliseconds (8b): told by the length a subtitle would have',
      cues([{'start_time': 1500, 'end_time': 3000, 'text': 'Millis'}])[1], [(1.5, 3.0, 'Millis')])
check("JSON Type 6: words of their start, ended by the next, joined into subtitles",
      cues({'words': [['1000', 'Hello'], ['1400', 'there'], ['1800', '']], 'paragraphs': [], 'speakers': {}})[1],
      [(1.0, 1.8, 'Hello there')])
check("PodcastIndex: the speaker kept", jd.read(SAMPLES['podcastindex'][0])[0][0].get('speaker'), 'Ann')
check("frames at the rate given (JSON Type 16 at 30)", cues(SAMPLES['type16'][0], fps=30)[1], [(1.0, 2.167, 'Frames')])
try:
    jd.read({'meta': {'timeFormat': 'MILLISECONDS'}, 'events': [{'eventType': 'TIMED_TEXT_EVENT', 'start': 1, 'end': 2}]})
    check('CLQTT in another time format: refused', None, 'ValueError')
except ValueError as error:
    check('CLQTT in another time format: refused', 'MILLISECONDS' in str(error), True)

print('left alone')
OTHERS = {
    "Subtitld's own": json.loads(plaintext.serialize([{'start': 1, 'end': 2, 'text': 'x'}], plaintext.JSON, language='en')),
    'a bare list of start, end and text in seconds': [{'start': 1.0, 'end': 2.0, 'text': 'x'}],
    "YouTube's json3 (and Google Play's)": {'events': [{'tStartMs': 1000, 'dDurationMs': 500, 'segs': [{'utf8': 'x'}]}]},
    "Bilibili's BCC": {'body': [{'from': 1, 'to': 2, 'content': 'x'}]},
    'whisper.cpp': {'transcription': [{'offsets': {'from': 0, 'to': 1000}, 'text': 'x'}]},
    "Google's Speech-to-Text (JSON Type 20)": {'results': [{'alternatives': [{'words': [{'startTime': '1s', 'endTime': '2s', 'word': 'x'}]}]}]},
    'Amara': {'subtitles': [{'start': 1000, 'end': 2000, 'text': 'x'}], 'version_number': 1},
    'an Aeneas sync map': {'fragments': [{'begin': '0.000', 'end': '1.000', 'id': 'f000001', 'language': 'en',
                                          'lines': ['x'], 'children': []}]},
    'a JSON with no subtitles': {'name': 'x', 'items': [1, 2]},
}
check('none of these is a dialect', {name: jd.kind(data) for name, data in OTHERS.items()}, {name: None for name in OTHERS})

print('writing')
trip = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>you</i>\nsecond "line"', 'speaker': 'Ann'},
        {'start': 3.0, 'end': 4.0, 'text': '{\\an8}Top'}]
plain = [(1.0, 2.5, 'Hello you\nsecond "line"'), (3.0, 4.0, 'Top')]
for dialect in jd.WRITABLE:
    data = json.loads(jd.write(trip, dialect, language='pt-BR', fps=25))
    if dialect == 'google-play':
        from subtitld.modules import json_subtitles
        segments, _language, tool = json_subtitles.read(data)
        check("Google Play's: YouTube's json3, which json_subtitles reads", (tool, [(s['start'], s['end'], s['text']) for s in segments]),
              ('youtube', plain))
        continue
    found = jd.kind(data)
    got = [(s['start'], s['end'], s['text']) for s in jd.read(data, fps=25)[0]]
    want = plain
    if dialect == 'type6':
        want = [(1.0, 4.0, 'Hello you second "line" Top')]       # words, joined again
    if dialect == 'type16':
        want = [(1.0, 2.48, plain[0][2]), plain[1]]               # 12.5 frames, rounded to even
    if dialect in ('type11', 'type15'):
        want = [(1.0, 2.467, plain[0][2]), (3.0, 4.0, plain[1][2])]  # starts only: 15 characters a second
    check(f'{dialect}: told apart and read back', (found, got), ('type8' if dialect == 'type8b' else dialect, want))
check('lines as Subtitle Edit joins them: "<br />" in TED, a list in Wistia, "\\n" in JSON Type 18',
      (json.loads(jd.write(trip, 'ted'))['subtitle'][0]['captions'][0]['content'],
       json.loads(jd.write(trip, 'wistia'))['captions'][0]['hash']['lines'][0]['text'],
       json.loads(jd.write(trip, 'type18'))[0]['n']),
      ('Hello you<br />second "line"', ['Hello you', 'second "line"'], 'Hello you\nsecond "line"'))
check('the language where the dialect has one', (json.loads(jd.write(trip, 'holistar', language='pt-BR'))['SubtitleLanguages'][0]['ClassName'],
                                                 jd.read(json.loads(jd.write(trip, 'type4', language='es')))[1]), ('pt-BR', 'es'))
check('the read-only ones are not offered', [name for name in jd.READ_ONLY if name in jd.WRITABLE], [])

print('through file_io')
folder = Path(tempfile.mkdtemp())
session.VIDEO = {}
for name, data, want in (('wistia.json', SAMPLES['wistia'][0], ('JSONDIALECT', 2, 'wistia')),
                         ('project.atpj', SAMPLES['archtime'][0], ('JSONDIALECT', 1, 'archtime')),
                         ('show.clqtt', SAMPLES['clqtt'][0], ('JSONDIALECT', 1, 'clqtt')),
                         ('song.gst', SAMPLES['gremots'][0], ('JSONDIALECT', 1, 'gremots'))):
    (folder / name).write_text(json.dumps(data), encoding='utf-8')
    session.SUBTITLE = Subtitle({'language': 'en'})
    session.FORMAT = {}
    opened, fmt = file_io.process_subtitles_file(str(folder / name))
    check(f'{name} opens as its dialect, which is kept', (fmt, len(opened), session.FORMAT['options'].get('json_dialect')), want)
(folder / 'own.json').write_text(json.dumps(OTHERS["Subtitld's own"]), encoding='utf-8')
session.FORMAT = {}
check("Subtitld's own JSON is JSON still", file_io.process_subtitles_file(str(folder / 'own.json'))[1], 'JSON')
(folder / 'yt.json3').write_text(json.dumps(OTHERS["YouTube's json3 (and Google Play's)"]), encoding='utf-8')
session.FORMAT = {}
check("YouTube's json3 is JSON still", file_io.process_subtitles_file(str(folder / 'yt.json3'))[1], 'JSON')
session.SUBTITLE = Subtitle({'segments': trip, 'language': 'en'})
session.FORMAT = {'options': {'json_dialect': 'podcastindex'}}
file_io.save_file(str(folder / 'out.json'), 'JSONDIALECT')
saved = json.loads((folder / 'out.json').read_text(encoding='utf-8'))
check('saved in the dialect kept, the speaker with it', (jd.kind(saved), saved['segments'][0].get('speaker')),
      ('podcastindex', 'Ann'))
session.FORMAT = {'options': {'json_dialect': 'clqtt'}}
file_io.save_file(str(folder / 'fallback.json'), 'JSONDIALECT')
check("a read-only dialect is saved as Wistia's", jd.kind(json.loads((folder / 'fallback.json').read_text(encoding='utf-8'))),
      'wistia')

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.FORMAT = {'options': {'json_dialect': 'ted'}}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('JSONDIALECT')
panel._refresh_options()
config = panel.get_config()
check('in the export menu: the writable dialects, the one opened chosen',
      ('JSONDIALECT' in SUBTITLE_FORMATS,
       [panel.dialect_combo.combobox.itemData(i) for i in range(panel.dialect_combo.combobox.count())],
       config['options'].get('json_dialect')),
      (True, list(jd.WRITABLE), 'ted'))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
