"""Subtitles as plain text: writing SRT and Markdown, checking them, and
folding an edited text back into the subtitles.

Pure module — no Qt.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import plaintext as pt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


LANGUAGES = {'pt-br', 'en-us', 'es-es', 'fr-fr'}


def issues(text, fmt):
    return [(i.line, i.column, i.code, i.severity) for i in pt.parse(text, fmt, LANGUAGES).issues]


def project():
    return [
        {'start': 0.199, 'end': 5.729, 'text': 'AI never sleeps.', 'speaker': 'A',
         'translations': {'pt-br': 'A IA nunca dorme.'},
         'dubbing': [{'engine': 'edge-tts', 'path': 'a.wav', 'start': 0.3, 'uid': 'd1'}],
         '_clip_cache': object()},
        {'start': 6.559, 'end': 11.009, 'text': '#Anthropic ships\na new model', 'speaker': 'true'},
        {'start': 11.5, 'end': 15.0, 'text': 'And [MUSIC]', 'speaker': 'B'},
    ]


print('SRT is written with the metadata as comments before each number')
srt = pt.serialize(project(), pt.SRT)
check('first block', srt.split('\n\n')[0].split('\n'), [
    '# speaker: A',
    '# translations: {"pt-br": "A IA nunca dorme."}',
    '# dubbing: [{"engine": "edge-tts", "path": "a.wav", "start": 0.3, "uid": "d1"}]',
    '1',
    '00:00:00,199 --> 00:00:05,729',
    'AI never sleeps.'])
check('a string that reads as JSON is quoted', '# speaker: "true"' in srt, True)
check('runtime keys are left out', '_clip_cache' in srt, False)

print('Markdown: timing and speaker, the text, then the translations; dubs left out')
md = pt.serialize(project(), pt.MD)
check('blocks', md.split('\n\n')[:2], [
    '[00:00:00.199 - 00:00:05.729] A\nAI never sleeps.\n> pt-br: A IA nunca dorme.',
    '[00:00:06.559 - 00:00:11.009] true\n#Anthropic ships\na new model'])

print('both read back to exactly what was written')
for fmt in pt.FORMATS:
    result = pt.parse(pt.serialize(project(), fmt), fmt)
    check(f'{fmt}: no issues', result.issues, [])
    check(f'{fmt}: timings and text', [(c.start, c.end, c.text) for c in result.cues],
          [(s['start'], s['end'], s['text']) for s in project()])
result = pt.parse(srt, pt.SRT)
check('srt: metadata', [c.meta for c in result.cues], [
    {'speaker': 'A', 'translations': {'pt-br': 'A IA nunca dorme.'},
     'dubbing': [{'engine': 'edge-tts', 'path': 'a.wav', 'start': 0.3, 'uid': 'd1'}]},
    {'speaker': 'true'}, {'speaker': 'B'}])
check('srt: a subtitle line starting with # is text, not a comment', result.cues[1].text, '#Anthropic ships\na new model')
check('md: [MUSIC] in the text is text', pt.parse(md, pt.MD).cues[2].text, 'And [MUSIC]')
check('srt: line ranges', [(c.first_line, c.line, c.last_line) for c in result.cues], [(1, 5, 6), (8, 10, 12), (14, 16, 17)])

print('SRT problems carry their line and column')
check('wrong millisecond separator', issues('1\n00:00:01.000 --> 00:00:02,000\nHi\n', 'srt'), [(2, 9, 'time_separator', 'error')])
check('end before start, at the end time', issues('1\n00:00:03,000 --> 00:00:02,000\nHi\n', 'srt'), [(2, 18, 'end_before_start', 'error')])
check('61 minutes, at the minutes', issues('1\n00:00:01,000 --> 00:61:02,000\nHi\n', 'srt'), [(2, 21, 'time_minutes', 'error')])
check('seconds too', issues('1\n00:00:75,000 --> 00:01:02,000\nHi\n', 'srt'), [(2, 7, 'time_seconds', 'error')])
check('not a time', issues('1\n00:00:1,000 --> 00:00:02,000\nHi\n', 'srt'), [(2, 1, 'time_format', 'error')])
check('no arrow', issues('1\n00:00:01,000 -> 00:00:02,000\nHi\n', 'srt'), [(2, 1, 'timing_format', 'error')])
check('text after the end time', issues('1\n00:00:01,000 --> 00:00:02,000 x\nHi\n', 'srt'), [(2, 31, 'timing_extra', 'error')])
check('no number', issues('00:00:01,000 --> 00:00:02,000\nHi\n', 'srt'), [(1, 1, 'number_missing', 'error')])
check('text where the number goes', issues('Hi\n00:00:01,000 --> 00:00:02,000\n', 'srt'), [(1, 1, 'number_expected', 'error')])
check('number and nothing else', issues('1\n', 'srt'), [(1, 2, 'timing_missing', 'error')])
check('numbering off is only a warning',
      issues('1\n00:00:01,000 --> 00:00:02,000\nA\n\n5\n00:00:03,000 --> 00:00:04,000\nB\n', 'srt'),
      [(5, 1, 'number_sequence', 'warning')])
check('empty text is only a warning', issues('1\n00:00:01,000 --> 00:00:02,000\n', 'srt'), [(2, 1, 'empty_text', 'warning')])
check('broken JSON in a comment, at the break', issues('# translations: {"pt": \n1\n00:00:01,000 --> 00:00:02,000\nHi\n', 'srt'),
      [(1, 24, 'comment_json', 'error')])
check('a core key in a comment', issues('# start: 3\n1\n00:00:01,000 --> 00:00:02,000\nHi\n', 'srt'), [(1, 3, 'comment_reserved', 'error')])
check('a key twice', issues('# speaker: A\n# speaker: B\n1\n00:00:01,000 --> 00:00:02,000\nHi\n', 'srt'), [(2, 3, 'comment_duplicate', 'error')])
check('a comment cut off from its subtitle', issues('# speaker: A\n\n1\n00:00:01,000 --> 00:00:02,000\nHi\n', 'srt'),
      [(1, 1, 'comment_detached', 'error')])
check('Windows line endings are fine', issues('1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n', 'srt'), [])

print('Markdown problems')
check('comma separator', issues('[00:00:01,000 - 00:00:02.000]\nHi', 'md'), [(1, 10, 'time_separator', 'error')])
check('text before the first timing', issues('Intro\n[00:00:01.000 - 00:00:02.000]\nHi', 'md'), [(1, 1, 'text_before_first', 'error')])
check('no closing bracket', issues('[00:00:01.000 - 00:00:02.000\nHi', 'md'), [(1, 29, 'md_bracket', 'error')])
check('no dash', issues('[00:00:01.000 00:00:02.000]\nHi', 'md'), [(1, 1, 'timing_format', 'error')])
check('out of order is a warning', issues('[00:00:05.000 - 00:00:06.000]\nB\n[00:00:01.000 - 00:00:02.000]\nA', 'md'),
      [(3, 1, 'out_of_order', 'warning')])
check('blank lines inside a text are kept', pt.parse('[00:00:01.000 - 00:00:02.000]\nA\n\nB\n\n', 'md').cues[0].text, 'A\n\nB')

print('an unchanged text changes nothing')
for fmt in pt.FORMATS:
    segments = project()
    check(f'{fmt}: not changed', pt.apply_cues(segments, pt.parse(pt.serialize(segments, fmt), fmt).cues), False)

print('editing the text keeps every subtitle object')
segments = project()
originals = list(segments)
text = pt.serialize(segments, pt.SRT).replace('AI never sleeps.', 'AI never rests.').replace('00:00:11,500', '00:00:12,000')
check('changed', pt.apply_cues(segments, pt.parse(text, pt.SRT).cues), True)
check('same objects, same order', [id(s) for s in segments], [id(s) for s in originals])
check('text edited', segments[0]['text'], 'AI never rests.')
check('start edited', segments[2]['start'], 12.0)
check('untouched values are not rewritten (no float drift)', segments[1]['start'] is originals[1]['start'], True)
check('runtime keys survive', '_clip_cache' in segments[0], True)

print('SRT comments set the metadata')
segments = project()
text = pt.serialize(segments, pt.SRT).replace('# speaker: B', '# speaker: C\n# style: {"italic": true}')
pt.apply_cues(segments, pt.parse(text, pt.SRT).cues)
check('changed and added', (segments[2]['speaker'], segments[2]['style']), ('C', {'italic': True}))
text = pt.serialize(segments, pt.SRT).replace('# style: {"italic": true}\n', '')
pt.apply_cues(segments, pt.parse(text, pt.SRT).cues)
check('a removed comment removes the key', 'style' in segments[2], False)

print('Markdown: other fields go in braces, translations may span lines')
rich = [{'start': 1.0, 'end': 2.0, 'text': 'Hello', 'speaker': 'John Smith', 'locked': True, 'n': '12',
         'note': 'check this', 'style': {'italic': True},
         'translations': {'pt-br': 'Olá', 'es-es': 'Hola,\namigo'}, 'dubbing': [{'path': 'a.wav'}]}]
md = pt.serialize(rich, pt.MD, LANGUAGES)
check('written', md.split('\n'), [
    '[00:00:01.000 - 00:00:02.000] John Smith {locked=true n="12" note="check this" style={"italic": true}}',
    'Hello', '> pt-br: Olá', '> es-es: Hola,', '> amigo', ''])
result = pt.parse(md, pt.MD, LANGUAGES)
check('read back exactly', (result.issues, result.cues[0].meta),
      ([], {key: value for key, value in rich[0].items() if key not in ('start', 'end', 'text', 'dubbing')}))
check('no change to apply', pt.apply_cues(rich, result.cues, dry_run=True), False)

print('Markdown: what cannot be written plainly falls back to the braces')
odd = [{'start': 1.0, 'end': 2.0, 'text': 'x', 'speaker': 'weird {name}', 'translations': {'custom': 'y'}}]
md = pt.serialize(odd, pt.MD, LANGUAGES)
check('written', md.split('\n')[0], '[00:00:01.000 - 00:00:02.000] {speaker="weird {name}" translations={"custom": "y"}}')
check('read back', pt.parse(md, pt.MD, LANGUAGES).cues[0].meta, {'speaker': 'weird {name}', 'translations': {'custom': 'y'}})

print('Markdown: text lines that would read as something else are escaped')
tricky = [{'start': 1.0, 'end': 2.0, 'text': '> quoted\n[1] footnote\n\\back\n  [2] indented'}]
md = pt.serialize(tricky, pt.MD, LANGUAGES)
check('escaped', md.split('\n')[1:5], ['\\> quoted', '\\[1] footnote', '\\\\back', '\\  [2] indented'])
check('read back', pt.parse(md, pt.MD, LANGUAGES).cues[0].text, tricky[0]['text'])
check('a quote line of no known language is text',
      pt.parse('[00:00:01.000 - 00:00:02.000]\n> ok: fine', pt.MD, LANGUAGES).cues[0].text, '> ok: fine')
check('a blank line between translations is spacing',
      pt.parse('[00:00:01.000 - 00:00:02.000]\nHi\n\n> pt-br: Oi\n\n> es-es: Hola\n', pt.MD, LANGUAGES).cues[0].meta,
      {'translations': {'pt-br': 'Oi', 'es-es': 'Hola'}})

print('Markdown problems in the new lines')
head = '[00:00:01.000 - 00:00:02.000] A '
check('unclosed braces', issues(head + '{style=italic\nHi', 'md'), [(1, 46, 'attr_unclosed', 'error')])
check('a field with no value', issues(head + '{style}\nHi', 'md'), [(1, 34, 'attr_format', 'error')])
check('a field with an empty value', issues(head + '{style= x}\nHi', 'md'), [(1, 34, 'attr_value_missing', 'error')])
check('broken JSON, at the break', issues(head + '{style={"a": }\nHi', 'md'), [(1, 46, 'attr_json', 'error')])
check('a core field', issues(head + '{start=3}\nHi', 'md'), [(1, 34, 'attr_reserved', 'error')])
check('the dubs cannot be set here', issues(head + '{dubbing=[]}\nHi', 'md'), [(1, 34, 'attr_kept', 'error')])
check('the speaker twice', issues(head + '{speaker=B}\nHi', 'md'), [(1, 31, 'attr_duplicate', 'error')])
check('a field twice', issues(head + '{x=1 x=2}\nHi', 'md'), [(1, 38, 'attr_duplicate', 'error')])
check('text after the braces', issues(head + '{x=1} tail\nHi', 'md'), [(1, 39, 'timing_extra', 'error')])
check('a translation twice', issues(head + '\nHi\n> pt-br: Oi\n> pt-br: Olá', 'md'), [(4, 3, 'translation_duplicate', 'error')])
check('text under the translations', issues(head + '\nHi\n> pt-br: Oi\nMore', 'md'), [(4, 1, 'text_after_translation', 'error')])

print('Markdown edits set what they show and keep the dubs')
segments = project()
text = pt.serialize(segments, pt.MD, LANGUAGES)
text = text.replace('AI never sleeps.', 'AI never naps.').replace('] A\n', '] C {style=italic}\n')
text = text.replace('> pt-br: A IA nunca dorme.', '> fr-fr: L’IA ne dort jamais.')
pt.apply_cues(segments, pt.parse(text, pt.MD, LANGUAGES).cues)
check('text, speaker, a new field', (segments[0]['text'], segments[0]['speaker'], segments[0]['style']), ('AI never naps.', 'C', 'italic'))
check('translations as written', segments[0]['translations'], {'fr-fr': 'L’IA ne dort jamais.'})
check('the dubs are kept', segments[0]['dubbing'], project()[0]['dubbing'])
text = pt.serialize(segments, pt.MD, LANGUAGES).replace('] C {style=italic}\n', ']\n')
pt.apply_cues(segments, pt.parse(text, pt.MD, LANGUAGES).cues)
check('removing the speaker and field removes them', ('speaker' in segments[0], 'style' in segments[0]), (False, False))
empty = [{'start': 1.0, 'end': 2.0, 'text': 'x', 'translations': {}}]
check('an empty translations list is not shown, and kept', (pt.serialize(empty, pt.MD), pt.apply_cues(empty, pt.parse(pt.serialize(empty, pt.MD), pt.MD).cues)),
      ('[00:00:01.000 - 00:00:02.000]\nx\n', False))

print('JSON is Whisper\'s layout, with every field as a key')
js = pt.serialize(project(), pt.JSON, language='en-us')
check('first segment', js.split('\n')[:13], [
    '{', '  "language": "en-us",', '  "segments": [', '    {',
    '      "id": 0,', '      "start": 0.199,', '      "end": 5.729,', '      "text": "AI never sleeps.",',
    '      "speaker": "A",', '      "translations": {"pt-br": "A IA nunca dorme."},',
    '      "dubbing": [{"engine": "edge-tts", "path": "a.wav", "start": 0.3, "uid": "d1"}]', '    },', '    {'])
result = pt.parse(js, pt.JSON)
check('read back: all fields, dubs included', [c.meta for c in result.cues], [
    {'speaker': 'A', 'translations': {'pt-br': 'A IA nunca dorme.'},
     'dubbing': [{'engine': 'edge-tts', 'path': 'a.wav', 'start': 0.3, 'uid': 'd1'}]},
    {'speaker': 'true'}, {'speaker': 'B'}])
check('a line break in the text is \\n', '"#Anthropic ships\\na new model"' in js, True)
check('line ranges cover each segment', [(c.first_line, c.last_line) for c in result.cues], [(4, 12), (13, 19), (20, 26)])
check('the whole text last, in one line', js.split('\n')[-4:-1],
      ['  ],', '  "text": "AI never sleeps. #Anthropic ships a new model And [MUSIC]"', '}'])
check('the language is read', pt.parse(js, pt.JSON, LANGUAGES).language, 'en-us')
check('no subtitles', (pt.serialize([], pt.JSON, language='pt-br'), pt.parse(pt.serialize([], pt.JSON), pt.JSON).issues),
      ('{\n  "language": "pt-br",\n  "segments": [],\n  "text": ""\n}\n', []))
edited = js.replace('"text": "AI never sleeps. #Anthropic', '"text": "Edited here, ignored #Anthropic')
check('the whole text is never read back', pt.apply_cues(project(), pt.parse(edited, pt.JSON).cues, dry_run=True), False)
check('a language Subtitld lacks is a warning, and not taken',
      (issues('{"language": "en", "segments": []}', 'json'), pt.parse('{"language": "en", "segments": []}', pt.JSON, LANGUAGES).language),
      ([(1, 14, 'json_language', 'warning')], None))
check('a language that is not text', issues('{"language": 3, "segments": []}', 'json'), [(1, 14, 'json_string', 'error')])

print('Whisper\'s own output can be pasted in')
whisper = ('{"text": " Hello there. General Kenobi.", "segments": ['
           '{"id": 0, "seek": 0, "start": 0.0, "end": 2.0, "text": " Hello there.", "tokens": [50364, 2425],'
           ' "temperature": 0.0, "avg_logprob": -0.3, "compression_ratio": 0.8, "no_speech_prob": 0.01},'
           '{"id": 1, "seek": 0, "start": 2.0, "end": 4.5, "text": " General Kenobi.", "speaker": "SPEAKER_01",'
           ' "words": [{"word": " General", "start": 2.0, "end": 2.6}]}], "language": "en"}')
result = pt.parse(whisper, pt.JSON)
check('no issues', result.issues, [])
check('texts without the leading space, decoder statistics left out',
      [(c.start, c.end, c.text, c.meta) for c in result.cues],
      [(0.0, 2.0, 'Hello there.', {}),
       (2.0, 4.5, 'General Kenobi.', {'speaker': 'SPEAKER_01', 'words': [{'word': ' General', 'start': 2.0, 'end': 2.6}]})])
check('a bare list of segments reads too', len(pt.parse('[{"start": 1, "end": 2, "text": "a"}]', pt.JSON).cues), 1)

print('JSON problems carry their line and column')
broken = """{
  "segments": [
    {
      "id": 0,
      "start": 1.0,
      "end": 0.5,
      "text": "Hi"
    },
    {
      "id": 3,
      "start": "2",
      "end": 3.0,
      "text": 5,
      "text": "x"
    },
    {"start": 4.0, "text": "no end"},
    42
  ],
  "extra": 1
}"""
check('each where it is', issues(broken, 'json'), [
    (6, 14, 'end_before_start', 'error'), (10, 13, 'number_sequence', 'warning'), (11, 16, 'json_number', 'error'),
    (14, 7, 'json_duplicate', 'error'), (16, 5, 'json_missing', 'error'), (17, 5, 'json_segment', 'error'),
    (19, 3, 'json_ignored', 'warning')])
check('a trailing comma', issues('{"segments": [{"start": 1, "end": 2, "text": "a",}]}', 'json'), [(1, 50, 'json_expect_key', 'error')])
check('cut short', issues('{"segments": [', 'json'), [(1, 15, 'json_expect_value', 'error')])
check('an unterminated string', issues('{"segments": [{"text": "a}]}', 'json'), [(1, 24, 'json_bad_string', 'error')])
check('no segments list', issues('{"segments": 3}', 'json'), [(1, 14, 'json_root', 'error')])
check('a negative time', issues('{"segments": [{"start": -1, "end": 2, "text": "a"}]}', 'json'), [(1, 25, 'json_negative', 'error')])
check('a fractional id', issues('{"segments": [{"id": 0.5, "start": 1, "end": 2, "text": "a"}]}', 'json'), [(1, 22, 'json_integer', 'error')])
check('an empty text is no subtitles, no problem', issues('', 'json'), [])

print('JSON edits set every field')
segments = project()
originals = list(segments)
text = pt.serialize(segments, pt.JSON).replace('"speaker": "B"', '"speaker": "C", "style": {"italic": true}')
text = text.replace('"end": 5.729', '"end": 5.9')
check('changed', pt.apply_cues(segments, pt.parse(text, pt.JSON).cues), True)
check('same objects', [id(s) for s in segments], [id(s) for s in originals])
check('end, speaker, a new field', (segments[0]['end'], segments[2]['speaker'], segments[2]['style']), (5.9, 'C', {'italic': True}))
text = pt.serialize(segments, pt.JSON).replace(',\n      "dubbing": [{"engine": "edge-tts", "path": "a.wav", "start": 0.3, "uid": "d1"}]', '')
pt.apply_cues(segments, pt.parse(text, pt.JSON).cues)
check('a removed key removes the field (JSON shows everything)', 'dubbing' in segments[0], False)
check('runtime keys survive', '_clip_cache' in segments[0], True)

print('adding and removing subtitles')
segments = project()
first, second, third = segments
blocks = pt.serialize(segments, pt.MD).strip().split('\n\n')
text = '\n\n'.join([blocks[0], '[00:00:05.800 - 00:00:06.400] true\nRewritten', blocks[2]])
pt.apply_cues(segments, pt.parse(text, pt.MD).cues)
check('a block rewritten in place is still that subtitle',
      (segments[1] is second, segments[1]['text'], segments[1]['start']), (True, 'Rewritten', 5.8))
text = '\n\n'.join([blocks[0], '[00:00:05.800 - 00:00:06.400]\nNew one', blocks[1], blocks[2]])
segments = project()
first, second, third = segments
pt.apply_cues(segments, pt.parse(text, pt.MD).cues)
check('an inserted block is a new subtitle',
      ([s['text'] for s in segments][1], 'speaker' in segments[1]), ('New one', False))
check('the others are the same objects', (segments[0] is first, segments[2] is second, segments[3] is third), (True, True, True))
text = '\n\n'.join([blocks[0], blocks[2]])
pt.apply_cues(segments, pt.parse(text, pt.MD).cues)
check('deleted blocks are gone', [s['text'] for s in segments], ['AI never sleeps.', 'And [MUSIC]'])
check('and the rest are kept', (segments[0] is first, segments[1] is third), (True, True))

print('the subtitles end up in time order')
segments = project()
text = '[00:00:20.000 - 00:00:21.000]\nLast\n\n' + pt.serialize(segments, pt.MD)
pt.apply_cues(segments, pt.parse(text, pt.MD).cues)
check('sorted', [s['text'] for s in segments][-1], 'Last')

print('each cue maps to its subtitle')
segments = project()
result = pt.parse(pt.serialize(segments, pt.SRT) + '\n4\n00:00:30,000 --> 00:00:31,000\nNot applied\n', pt.SRT)
mapped = pt.segments_for(segments, result.cues)
check('existing ones found, the new one None', [m is s for m, s in zip(mapped, segments)] + [mapped[3]], [True, True, True, None])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
