"""Real transcripts from the corpus (tests/corpus): praatIO's TextGrids and
the other Praat files beside them, and pympi's ELAN documents. Each is
opened through file_io, compared with what the file plainly holds or with
what praatIO's and pympi's own tests say of it, saved in its own format
and opened again.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. It passes, skipping, when the corpus has
not been fetched.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
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


def cues(segments):
    return [(round(s['start'], 3), round(s['end'], 3), s['text'], s.get('speaker')) for s in segments]


TEXTGRIDS = ['praatio/' + name for name in (
    'mary.TextGrid', 'mary_longfile.TextGrid', 'mary_with_negative_zero.TextGrid',
    'mary_longfile_with_negative_zero.TextGrid', 'mary_with_constrained_tier_times.TextGrid',
    'textgrid_to_merge.TextGrid', 'textgrid_to_merge_longfile.TextGrid', 'all_tiers_have_the_same_name.TextGrid',
    'bobby_phones_elan.TextGrid', 'bobby_words_with_newlines.TextGrid', 'bobby_words_with_newlines_longfile.TextGrid',
    'bobby_words_with_newlines_longfile_elan.TextGrid')]
OTHER_PRAAT = ['praatio/' + name for name in (
    'bobby.KlattGrid', 'bobby.PointProcess', 'bobby_longfile.PointProcess', 'mary.DurationTier', 'mary.PitchTier',
    'mary_longfile.PitchTier')]
EAFS = ['pympi/sample_2.7.eaf', 'pympi/sample_2.8.eaf', 'pympi/sample_3.0.eaf']
found = dict(zip(TEXTGRIDS + OTHER_PRAAT + EAFS, need(*(TEXTGRIDS + OTHER_PRAAT + EAFS))))

print('praatIO: mary (a phone tier, a word tier, a pitch point tier)')
mary, fmt = opened(found['praatio/mary.TextGrid'])
# The word tier's four labels, mary 0.315-0.676, rolled, the, barrel
# ...1.518 (the file): one subtitle of them, as words are grouped; the
# phones and the pitch points are no subtitles.
check('mary: its words, one subtitle', (fmt, cues(mary)), ('TEXTGRID', [(0.315, 1.518, 'mary rolled the barrel', 'A')]))
# praatIO's tests: the short and long forms are the same TextGrid, and so
# are the ones whose start times are written "-0".
for name in ('mary_longfile.TextGrid', 'mary_with_negative_zero.TextGrid', 'mary_longfile_with_negative_zero.TextGrid',
             'mary_with_constrained_tier_times.TextGrid'):
    check(f'{name}: the same as mary.TextGrid', cues(opened(found['praatio/' + name])[0]), cues(mary))
opened(found['praatio/mary.TextGrid'])
check('saved as a TextGrid and opened again', cues(again('mary.TextGrid', 'TEXTGRID')[0]), cues(mary))

print('praatIO: textgrid_to_merge (three people\'s tiers)')
merge, _fmt = opened(found['praatio/textgrid_to_merge.TextGrid'])
# The file: bob 01a-01d, mary 02a-02d, sarah 03a-03c, labels on 11 intervals.
check('a subtitle a label, the tier its speaker', (len(merge), sorted({s['speaker'] for s in merge})),
      (11, ['bob', 'mary', 'sarah']))
check('first and last (by start)', (cues(merge)[0], cues(merge)[-1]),
      ((0.736, 1.56, '01a', 'bob'), (9.219, 9.83, '01d', 'bob')))
check('sarah\'s 03a, 3.774-4.137', [c for c in cues(merge) if c[2] == '03a'], [(3.774, 4.137, '03a', 'sarah')])
# praatIO's test_tg_io_long_vs_short: the long form is the same TextGrid.
check('the long form reads the same', cues(opened(found['praatio/textgrid_to_merge_longfile.TextGrid'])[0]), cues(merge))
opened(found['praatio/textgrid_to_merge.TextGrid'])
check('opening asks to keep the speakers', session.FORMAT['options'].get('speakers'), 'voice')
saved, _fmt = again('merge.TextGrid', 'TEXTGRID')
check('saved with a tier a speaker and opened again', cues(saved), cues(merge))

print('praatIO: all_tiers_have_the_same_name')
# Three tiers all named "Mary" (praatIO refuses them, or renames them Mary,
# Mary_2, Mary_3): one person, whose three labels overlap.
same, _fmt = opened(found['praatio/all_tiers_have_the_same_name.TextGrid'])
check('three labels, all Mary\'s', [(c[2], c[3]) for c in cues(same)], [('Mary', 'Mary'), ('Maary', 'Mary'), ('Bob', 'Mary')])
saved, _fmt = again('same.TextGrid', 'TEXTGRID')
check('saved (overlaps to "Mary (2)" tiers) and opened again', cues(saved), cues(same))

print('praatIO: bobby_phones_elan (phones alone)')
phones, _fmt = opened(found['praatio/bobby_phones_elan.TextGrid'])
# The file: one tier, "phone", 13 labels B ... ER0. Phones are left out
# where a tier of words or text is there; here there is nothing else.
check('the phones, there being nothing else', (len(phones), cues(phones)[0][2], cues(phones)[-1][2]), (13, 'B', 'ER0'))

print('praatIO: bobby_words_with_newlines (quotes and line breaks in labels)')
bobby, _fmt = opened(found['praatio/bobby_words_with_newlines.TextGrid'])
# The file writes the label """""""BOBBY""""""<newline>Noun": a quote in
# a Praat text is doubled, so it reads """BOBBY""", a line break, Noun; the
# tier is named """word""", that is "word" in quotes.
check('quotes undoubled, the line break kept', (len(bobby), bobby[0]['text'], bobby[-1]['text']),
      (4, '"""BOBBY"""\nNoun', 'LEDGER\nNoun'))
check('the first label\'s time', cues(bobby)[0][:2], (0.065, 0.412))
# praatIO's tests: the long form, and the long form as ELAN writes it,
# are the same TextGrid.
for name in ('bobby_words_with_newlines_longfile.TextGrid', 'bobby_words_with_newlines_longfile_elan.TextGrid'):
    check(f'{name}: the same', cues(opened(found['praatio/' + name])[0]), cues(bobby))
opened(found['praatio/bobby_words_with_newlines.TextGrid'])
saved, _fmt = again('bobby.TextGrid', 'TEXTGRID')
check('saved and opened again: quotes and line breaks survive', cues(saved), cues(bobby))

print('Praat files that are not TextGrids')
# A PitchTier, DurationTier, PointProcess and KlattGrid hold numbers, no
# labels: refused, saying what the file is, rather than opened empty.
for name in OTHER_PRAAT:
    kind = name.rsplit('.', 1)[1]
    try:
        opened(found[name])
        check(f'{name}: refused', 'opened', 'refused')
    except file_io.CorruptedProjectFileError as error:
        check(f'{name}: refused, naming what it is', str(error).startswith(f'This is a Praat {kind}, which holds no text'), True)
unknown = SCRATCH / 'notes.xyz'
unknown.write_text('nothing to read\n')
try:
    opened(unknown)
    check('a kind of file no reader takes: refused', 'opened', 'refused')
except file_io.CorruptedProjectFileError as error:
    check('a kind of file no reader takes: refused', str(error), 'Subtitld does not read .xyz files.')

print('pympi: ELAN sample_2.7, 2.8 and 3.0')
# The files: a top tier "text" of 399 annotations "The quick brown fox 001"
# (2.0-5.0 s) ... "399" (1196-1199 s), word tiers under it, and a top tier
# "gestures" of 1197 phases (prep, stroke, hold) from the controlled
# vocabulary "Gesture". pympi's tests only read them and write them back
# valid: the subtitles are what is said, the text tier, not the gestures.
first = None
for name in EAFS:
    elan, fmt = opened(found[name])
    check(f'{name}: the text tier\'s 399 annotations', (fmt, len(elan), cues(elan)[0], cues(elan)[-1]),
          ('EAF', 399, (2.0, 5.0, 'The quick brown fox 001', 'A'), (1196.0, 1199.0, 'The quick brown fox 399', 'A')))
    check(f'{name}: no gesture codes', [c for c in cues(elan) if c[2] in ('prep', 'stroke', 'hold')], [])
    if first is None:
        first = cues(elan)
    else:
        check(f'{name}: the same as sample_2.7.eaf', cues(elan), first)
opened(found['pympi/sample_3.0.eaf'])
check('3.0: its tiers\' language (eng), English, the project\'s', session.SUBTITLE.get('language', '').split('-')[0], 'en')
saved, _fmt = again('sample.eaf', 'EAF')
check('saved as EAF and opened again', cues(saved), first)

if fails:
    print(f'\n{len(fails)} FAILED: {fails}')
    sys.exit(1)
print('\nall OK')
