# Timed text: words, syllables and letters inside a subtitle

A subtitle's text can carry its own timing: each word, syllable or letter
with when it starts and ends. Karaoke lyrics, word-by-word captions and ASR
word timestamps all land here, and the user edits them, first in the
subtitles list.

To get there, `session.SUBTITLE` stops being a plain dict and becomes a
`Subtitle` object, its segments a list that makes every segment a `Segment`,
and `Segment.text()` is how the code reads a subtitle's words.

## Decisions taken

- **The timing lives in `text` itself.** A subtitle's `text` is either a
  string, as today, or a list of pieces. No separate field.
- **`session.SUBTITLE` becomes an instance of a `Subtitle` class** with
  attributes (`SUBTITLE.segments`, `SUBTITLE.selected`, …), not a dict.
- **Segments are `Segment(dict)` objects** with `.text()`, which joins a
  list or returns the string. They stay dicts, so `seg['start']`, JSON and
  `copy.deepcopy` keep working.
- **The ~300 reads of a subtitle's text change** to `seg.text()`, and the
  ~720 uses of `SUBTITLE['x']` change to `SUBTITLE.x`, a field at a time.
- **The subtitles list is the first place to edit timed text.** The
  timeline comes later.
- **Piece times are relative to the subtitle's start.**
- **The highlight effect is chosen per piece**, as ASS allows, with the
  style giving the default.
- **Letters are a level of their own**: words, syllables, letters.
- **Translations stay untimed strings**; a timed subtitle's translation is
  untimed until it is split itself.

## The shape

```python
{'start': 12.0, 'end': 14.5, 'text': [
    {'text': 'Hel',    'start': 0.00, 'end': 0.50},
    {'text': 'lo ',    'start': 0.50, 'end': 0.80},
    {'text': 'world',  'start': 1.00, 'end': 1.60},
]}
```

- The pieces joined spell the subtitle's text, spaces and line breaks
  included (a space belongs to the piece before it, as in `{\k}`).
- Pieces are in order and do not overlap; a gap between two is a pause.
- **Times are relative to the subtitle's start.** Moving a subtitle on the timeline, nudging it, or shifting the
  whole file, then needs no change to its pieces; dozens of places set
  `['start']` today and none of them has to learn about pieces. This is also
  how `{\k}` counts.
- A piece past the subtitle's end (the subtitle was shortened) is kept, not
  cut; the preview stops at the end and quality check flags it.
- Markup (`{\i1}`, colours) stays inside a piece's text, as it does inside
  the string today; `markup.plain` works on each piece and on `.text()`.
- Optional per piece: `'effect': 'fill' | 'wipe' | 'outline' | 'pop'`, the
  highlight for that piece (ASS `\k`, `\kf`, `\ko`); without it, the style's
  effect. Opening ASS keeps each piece's code; writing ASS gives it back.
- Optional per piece: `'level': 'word' | 'syllable' | 'letter'`, the level
  it was split at, so the timeline can draw words when zoomed out and the
  subtitles list knows what "Join" goes back to.

## The classes (`modules/segment.py`)

```python
class Segment(dict):
    def text(self): ...          # str always: the string, or the pieces joined
    def is_timed(self): ...
    def pieces(self): ...        # [] for a plain subtitle
    def set_text(self, new): ... # new words, timings kept where they still fit
    def split(self, level): ...  # 'words' | 'syllables' | 'letters'
    def unsplit(self): ...       # back to a string, timing dropped

class SegmentList(list):
    # append, insert, extend, +=, [i] = …, [a:b] = … wrap dicts into Segment

class Subtitle:
    segments  # property: stores a SegmentList whatever list it is given
    selected, position, language, filepath, metadata,
    source_language, origin, translations, current
    def reset(self): ...          # what opening/closing a file does today, once
    def to_dict(self) / from_dict(cls, d): ...  # for saving, autosave, co-editing
    # temporary: __getitem__, __setitem__, get, __contains__, setdefault
```

### What makes it hold

- `session.SUBTITLE` itself is never reassigned in the code today; only its
  keys are. So once it is a `Subtitle`, every `SUBTITLE.segments = …` (and,
  through the temporary layer, every `SUBTITLE['segments'] = …`) gives a
  `SegmentList`, and every dict put into that list becomes a `Segment`.
  That covers opening every format, autosave restore, import and merge,
  the start screen, co-editing and Subtitld Cloud without touching them.
- Format readers keep building plain dicts; they are wrapped on arrival.
- `SUBTITLE.selected` holds the selected segment itself (code writes
  `SUBTITLE['selected']['text'] = …`), so it must be the `Segment` in the
  list, not a copy; its setter checks that.

### Where it leaks

- **Wrapping is a copy.** Python cannot turn a dict into a Segment in place,
  so a dict put into the list, or a list given as `segments`, is stored as
  new Segment objects. Code that puts its own dict or list in and keeps
  using it afterwards must read it back from `SUBTITLE`. M1 found two such
  places (restoring a backup, opening from subtitld.cc: both handed their
  local list to the audio device) and fixed them; the rest find subtitles
  by 'id', as undo already requires.
- Most existing tests replace `session.SUBTITLE` with a plain dict, so
  they do not run through `Subtitle` at all; M2 changes them to
  `Subtitle(...)` as it switches the reads.
- A copy taken out (`{**seg}`, `dict(seg)`) is a plain dict until it goes
  back in. These are few; each is looked at when the reads change.
- Lists that never enter `SUBTITLE` (a reader's own list, an exporter's
  copies, history and docops documents) hold plain dicts. Code there uses
  `segment.text_of(value)`, the same logic as a function on any dict or
  value, or wraps its input first.
- `dict.update()` and `setdefault` on a segment bypass `__setitem__`; no
  rule depends on `__setitem__` for segments, so this is fine.

## Saving, co-editing, undo

- **The app's own file (USFX).** USF has a `<karaoke>` element; a timed
  subtitle is written with it and read back into pieces. To check against
  the USF spec and `usf.py` (line ~484 already reads `<karaoke>` tags, as
  text only). This also fixes `{\k}` timing being lost on save today.
- **Opening a file with `{\k}` codes** (ASS, and every format that goes
  through `modules/karaoke.py`) gives pieces instead of codes in the string.
  `karaoke.timed` already does the reading; it moves to relative times.
- **Autosave and co-editing** send `text` as JSON, list or string, through
  `to_dict()` / `docops.snapshot`. The protocol does not change; a text edit
  is still one `text` field set. Two people retiming the same subtitle at
  once: the last one wins, as with text today.
- **Undo** (`modules/history.py`) records `text` changes whole, list or
  string, so retiming a piece is one undo step.

## Milestones

**M1. Classes and the temporary layer.** *(Done on branch `timed-text`;
`reset()` and the `selected` check moved to M2, where `selected` becomes
an attribute.)* `modules/segment.py` with
`Segment`, `SegmentList`, `Subtitle` (+ `to_dict`/`from_dict`/`reset`) and
`text_of()`. `session.SUBTITLE = Subtitle()`. Nothing else changes: the
temporary dict layer keeps all 720 uses working.
*Done when:* the full test suite passes unchanged, and a new test opens a
file of every format, restores an autosave, imports and merges, applies a
co-editing snapshot and an undo, and finds every item of
`SUBTITLE.segments` a `Segment` and `SUBTITLE.selected` one of them.

**M2. Segments switched over.** *(Done on branch `timed-text`: 502 reads
in 37 app files, 288 in the tests, 144 test assignments wrapped in
`Subtitle(...)`; `selected` set to a copy finds the subtitle with its
'id', and `Subtitle({'segments': [...], 'selected': d})` the copy of `d`;
`reset()` where the list and the selection were cleared together.)* `SUBTITLE['segments']` and
`SUBTITLE.get('segments')` (~320) → `SUBTITLE.segments`; `selected` (~175)
with it, since the two go together. Mechanical, file by file.
*Done when:* no `SUBTITLE['segments']`/`['selected']` left; tests pass.

**M3. Reads of text switched over.** *(Done on branch `timed-text`: 68
reads where session subtitles arrive (interface, editing, documents,
quality check, plain-text panel, USF writer, publishing, TTS, translation,
project exports, recording) and `export_options.prepare`, which gives
every writer in `TEXT_FORMATS` the words, so the ~100 reads in those
writers stay. Format readers read their own strings and stay too. Writes
are M4's, with `set_text()`. The guard is in `tests/test_segment.py`.)* The ~300 `seg['text']` /
`seg.get('text')` → `seg.text()` (session segments) or `text_of()` (anything
else). Writes go through `set_text()`. Still no timed subtitles exist, so
behaviour must not change.
*Done when:* tests pass; a grep test fails on new `['text']` reads of a
segment outside `segment.py`, the readers and the writers that handle
pieces.

**M4. Timed text stored.** *(Done on branch `timed-text`. USF's karaoke is
`<k t="ms"/>`, the length of the text after it, the lengths adding up to
the subtitle's; effect and level are `subtitldcc:` attributes on `<k>`.
The ASS reader now keeps its karaoke (it dropped it before). Every writer
of karaoke codes gets them from export_options.prepare(karaoke_codes=True).
Merging keeps both timings (`joined`), cutting shares the pieces by time
(`cut`). Syllables come from pyphen, a dependency since (LibreOffice's
hyphenation dictionaries, 84 files; `segment.hyphenator(language)`).)* `split`/`unsplit`/`set_text` with their rules
(below), USFX `<karaoke>` read and write, `{\k}` opening into pieces, and
the `{\k}` writers (ASS, LRC, UltraStar, KAR, KBP, CDG) writing from pieces.
If USF's `<karaoke>` has no place for a piece's effect, USFX adds a
Subtitld attribute for it, as it already does for its own looks.
*Done when:* round trips keep pieces and their effects (USFX → USFX, ASS → USFX → ASS, LRC
word stamps → USFX → LRC), `set_text` rules have unit tests.

**M5. Editing in the subtitles list.** A selected subtitle gets "Split into
words / syllables / letters" and "Join". Its pieces show as a row (chips
or a small table) under the text box: each piece's text, start and end,
editable; click between two letters to split a piece there, click a split
to remove it. Syllables use `segment.hyphenator()` for the subtitle's
language (pyphen), times spread by letter count as a first pass.
*Done when:* tested in a real GUI session (window opened on Wayland, not
offscreen): split, retime, edit text, undo, save, reopen.

**M6. Exporters.** WebVTT inline timestamps, TTML/IMSC `<span begin end>`,
enhanced LRC; for formats with no timing inside a line, an export option
"reveal word by word" (one cue per piece, text up to it).

**M7. Preview.** The HTML renderer highlights the current piece with its
effect (fill, wipe, outline or pop-in), the style's when it has none; the
style setting for the default effect is added here.

**M8. Timeline.** A strip of pieces along the bottom of the selected
subtitle; drag a line between pieces to move both, Alt-drag for a gap;
double-click to go a level down; snapping to waveform onsets; tap mode
(each key press ends a piece during playback).

**M9. Sources.** ASR add-ons may return `words` in their results (protocol
addition, optional); a forced-alignment add-on task timing text the user
already has.

**M10. The other `SUBTITLE` fields** (`position`, `language`, `filepath`,
`metadata`, …) switched to attributes, at any pace; then the temporary dict
layer is removed.

## `set_text` rules

Old pieces and new text are compared character by character (difflib on
the joined text):

- Typed inside a piece: the piece grows, its times stay.
- Typed at the boundary of two pieces: goes to the piece on the left
  (to the right one at the very start).
- A piece deleted whole: its time goes to the piece before it (after it,
  for the first).
- A space typed inside a piece at the words level splits it; the time is
  shared by letter count.
- Most of the text replaced (less than about a third of the characters
  kept): pieces made again at the same level, spread over the old span by
  letter count.

## Effects in the subtitles list and preview

Each piece's row in the subtitles list (M5) has an effect choice, "Style's
default" first. The preview (M7) draws each piece with its own effect.
Formats that cannot say an effect per piece (WebVTT, TTML, LRC) write the
timing only.
