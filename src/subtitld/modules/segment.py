"""The open subtitle file and its subtitles

`session.SUBTITLE` is a `Subtitle`; its `segments` are a `SegmentList`, and
every subtitle in it is a `Segment`. A Segment is still the dict every module
knows ('start', 'end', 'text', 'speaker', ...), with the methods that read it.

A subtitle's 'text' is a string, or, for a subtitle timed word by word (or
syllable, or letter), a list of pieces, each its own text and times in
seconds from the subtitle's start:

    {'start': 12.0, 'end': 14.5, 'text': [
        {'text': 'Hel', 'start': 0.0, 'end': 0.5},
        {'text': 'lo ', 'start': 0.5, 'end': 0.8},
        {'text': 'world', 'start': 1.0, 'end': 1.6}]}

`Segment.text()`, or `text_of()` for a dict that is not one, gives the words
either way. The pieces joined are the subtitle's text, spaces, line breaks
and formatting included; they are in order and do not overlap, and a gap
between two is a pause. A piece may say its 'effect', how it lights up
(EFFECTS; the style's when it says none), and the 'level' it was cut at
(LEVELS).

`split()` cuts a text into words, syllables or letters, its times shared by
letters; `unsplit()` makes it a string again; `set_text()` gives a subtitle
new words, keeping each piece's times where its text is still there.

Nothing has to build a Segment itself: a dict put into the list, or a list
given as `segments`, comes out as Segments. That is a copy (a dict cannot
become a Segment in place), so code that puts its own dict in and keeps using
that dict afterwards must take it back from the list. Subtitles are found by
their 'id' anyway (modules.cue_ids): undo replaces them with copies too.

`SUBTITLE.selected` is the selected subtitle, one in the list: set to a copy
of one, it is the one in the list with the same 'id'. None, or False, for no
selection.

The other fields of `Subtitle` ('position', 'language', ...) are still read
and written as keys, `SUBTITLE['language']`; until they all become
attributes, Subtitle answers as the dict it was, a missing key included.
"""

import difflib
import re

from subtitld.modules import markup

LEVELS = ('word', 'syllable', 'letter')
EFFECTS = ('fill', 'wipe', 'outline', 'pop')

_CLOSING = re.compile(r'<\s*/')


def text_of(value):
    """The words of `value`: a subtitle (any dict) or its 'text', a string or
    a list of pieces. '' for none."""
    if isinstance(value, dict):
        value = value.get('text')
    if isinstance(value, list):
        return ''.join(str(piece.get('text') or '') for piece in value if isinstance(piece, dict))
    return str(value or '')


def timed_pieces(value):
    """The timed pieces of `value`, a subtitle (any dict) or its 'text';
    [] for a text that is not timed."""
    if isinstance(value, dict):
        value = value.get('text')
    return value if isinstance(value, list) else []


class Segment(dict):
    """One subtitle: the dict, with what reads it."""

    __slots__ = ()

    def text(self):
        """Its words, a string whether its text is timed or not."""
        return text_of(self.get('text'))

    def is_timed(self):
        """Whether its text is a list of timed pieces."""
        return isinstance(self.get('text'), list)

    def pieces(self):
        """Its timed pieces, [] for a text that is not timed."""
        return timed_pieces(self)

    def set_text(self, new):
        set_text(self, new)

    def split(self, level, hyphenate=None):
        split(self, level, hyphenate)

    def unsplit(self):
        unsplit(self)


# --------------------------------------------------------------------------- #
# Timed text                                                                   #
# --------------------------------------------------------------------------- #
def _visible(text):
    """[(index, character)] of `text` outside its markup."""
    out, position = [], 0
    for match in markup._TOKEN.finditer(text):
        out += [(index, text[index]) for index in range(position, match.start())]
        position = match.end()
    return out + [(index, text[index]) for index in range(position, len(text))]


def _letters(text):
    return sum(1 for _index, character in _visible(text) if not character.isspace())


def _cuts(text, level, hyphenate=None):
    """Where `text` is cut at `level`: indexes of the characters that start a
    piece, the first excepted. Spaces go with the piece before them;
    `hyphenate(word)` gives the places inside a word its syllables start."""
    cuts, word, seen, after_space = [], [], False, False
    for index, character in _visible(text) + [(len(text), ' ')]:
        if character.isspace():
            if level == 'syllable' and hyphenate and len(word) > 1:
                letters = ''.join(letter for _at, letter in word)
                cuts += [word[place][0] for place in hyphenate(letters) if 0 < place < len(word)]
            word, after_space = [], seen
            continue
        if seen and (after_space or level == 'letter'):
            cuts.append(index)
        seen, after_space = True, False
        word.append((index, character))
    return sorted(set(cuts))


def _cut_at(text, cuts):
    """`text` in pieces, cut before each index of `cuts`; an opening tag or
    override code just before a cut goes with the text after it."""
    openings = {match.end(): match.start() for match in markup._TOKEN.finditer(text)
                if not _CLOSING.match(match.group(0))}
    places = set()
    for cut in cuts:
        while cut in openings:
            cut = openings[cut]
        if 0 < cut < len(text):
            places.add(cut)
    bounds = [0, *sorted(places), len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _recut(pieces, level, hyphenate=None, cuts=None):
    """`pieces` cut again at `level` (or at `cuts`, indexes in their joined
    text): a new piece takes its times from those it covers, shared by
    letters inside one, and keeps the effect of the one it starts in."""
    text = ''.join(piece['text'] for piece in pieces)
    spans, at = [], 0
    for piece in pieces:
        spans.append((at, at + len(piece['text']), piece))
        at += len(piece['text'])

    def time(offset, ending):
        for first, last, piece in spans:
            if first <= offset - ending < last or (not ending and offset == first == last):
                if offset == (last if ending else first):
                    return piece['end'] if ending else piece['start']
                whole = _letters(piece['text'])
                part = _letters(text[first:offset]) / whole if whole else 0.0
                return piece['start'] + (piece['end'] - piece['start']) * part
        return spans[-1][2]['end'] if ending else spans[0][2]['start']

    def extras(offset):
        piece = next((piece for first, last, piece in spans if first <= offset < last), spans[-1][2])
        return {key: value for key, value in piece.items() if key not in ('text', 'start', 'end', 'level')}

    out, at = [], 0
    for part in _cut_at(text, _cuts(text, level, hyphenate) if cuts is None else cuts):
        new = dict(extras(at), text=part, start=round(time(at, False), 3),
                   end=round(time(at + len(part), True), 3))
        if level:
            new['level'] = level
        out.append(new)
        at += len(part)
    return out


def hyphenator(language):
    """`hyphenate(word)` for split(): where the syllables of a word start, by
    the hyphenation dictionary (pyphen's, LibreOffice's) of `language`, a
    code as Subtitld writes one ('pt-br', 'en'). None when there is none for
    it. A syllable may be one letter at a word's start ("a-long"), not at
    its end, where the dictionaries would cut a lone consonant off."""
    try:
        import pyphen
    except ImportError:
        return None
    parts = str(language or '').replace('-', '_').split('_')
    code = parts[0].lower() + (f'_{parts[1].upper()}' if len(parts) > 1 and parts[1] else '')
    found = pyphen.language_fallback(code) if code else None
    if not found:
        return None
    dictionary = pyphen.Pyphen(lang=found, left=1, right=2)
    return lambda word: [int(place) for place in dictionary.positions(word)]


def split(segment, level, hyphenate=None):
    """Cut the text of `segment` (any dict) into timed pieces at `level`, a
    word, syllable or letter each: a text not timed yet over the whole
    subtitle, a timed one each piece over its own times, so cutting finer
    keeps what was timed and cutting coarser joins it. Syllables need
    `hyphenate(word)`, the places in a word its syllables start; without
    it they are words."""
    if level not in LEVELS:
        raise ValueError(f'not a level: {level!r}')
    pieces = segment.get('text')
    if not isinstance(pieces, list) or not pieces:
        text = text_of(segment)
        if not text:
            return
        length = max(0.0, float(segment.get('end', 0) or 0) - float(segment.get('start', 0) or 0))
        pieces = [{'text': text, 'start': 0.0, 'end': round(length, 3)}]
    segment['text'] = _recut(pieces, level, hyphenate)


def unsplit(segment):
    """The text of `segment` a string again, its timing gone."""
    segment['text'] = text_of(segment)


def set_text(segment, new):
    """Give `segment` (any dict) the words `new`. A timed text keeps its
    pieces where their text is still there: text typed inside a piece goes
    into it, at a boundary into the piece before (the first, at the very
    start), a piece deleted whole gives its time to the one before (after,
    for the first); a word-level piece typed a space into is cut in two. With
    mostly new words, they are timed again over the old span."""
    new = str(new or '')
    pieces = segment.get('text')
    if not isinstance(pieces, list) or not pieces or not new.strip():
        segment['text'] = new
        return
    old = ''.join(piece['text'] for piece in pieces)
    if new == old:
        return
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    # Mostly new words (letters in common here and there do not count):
    # timed again. Words deleted are not a rewrite.
    kept = sum(block.size for block in matcher.get_matching_blocks() if block.size >= min(3, len(new)))
    if kept * 2 < len(new):
        whole = {'text': new, 'start': pieces[0]['start'], 'end': pieces[-1]['end']}
        segment['text'] = _recut([whole], pieces[0].get('level') or 'word')
        return
    owner = [number for number, piece in enumerate(pieces) for _character in piece['text']]
    texts = [''] * len(pieces)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for step in range(i2 - i1):
                texts[owner[i1 + step]] += new[j1 + step]
        elif tag == 'replace':
            texts[owner[i1]] += new[j1:j2]
        elif tag == 'insert':
            texts[owner[i1 - 1] if i1 else 0] += new[j1:j2]
    # A piece left with no words goes into the one before it; the first
    # ones, into the first that has words.
    out, lead, lead_start = [], '', None
    for piece, text in zip(pieces, texts):
        if not text.strip():
            if out:
                out[-1].update(text=out[-1]['text'] + text, end=max(out[-1]['end'], piece['end']))
            else:
                lead += text
                lead_start = piece['start'] if lead_start is None else lead_start
            continue
        piece = dict(piece, text=lead + text)
        if lead_start is not None:
            piece['start'] = lead_start
        lead, lead_start = '', None
        if piece.get('level') == 'word' and _cuts(piece['text'], 'word'):
            out += _recut([piece], 'word')
        else:
            out.append(piece)
    segment['text'] = out or new


def _segment(value):
    if isinstance(value, Segment) or not isinstance(value, dict):
        return value
    return Segment(value)


class SegmentList(list):
    """The subtitles of a Subtitle: whatever goes in comes out a Segment."""

    __slots__ = ()

    def __init__(self, values=()):
        super().__init__(_segment(value) for value in values)

    def append(self, value):
        super().append(_segment(value))

    def insert(self, index, value):
        super().insert(index, _segment(value))

    def extend(self, values):
        super().extend(_segment(value) for value in values)

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            value = [_segment(item) for item in value]
        else:
            value = _segment(value)
        super().__setitem__(index, value)


class Subtitle:
    """The open subtitle file."""

    _ATTRIBUTES = ('segments', 'selected')

    def __init__(self, fields=None, **more):
        self._segments = SegmentList()
        self._selected = None
        self._fields = {}
        values = dict(fields or {}, **more)
        # The subtitles first: the selected one is found among them, the
        # copy of the very dict given if it is one of them.
        if 'segments' in values:
            given = list(values.pop('segments') or [])
            self.segments = given
            selected = values.get('selected')
            for original, segment in zip(given, self._segments):
                if original is selected:
                    values['selected'] = segment
                    break
        for key, value in values.items():
            self[key] = value

    @property
    def segments(self):
        return self._segments

    @segments.setter
    def segments(self, value):
        self._segments = value if isinstance(value, SegmentList) else SegmentList(value or [])

    @property
    def selected(self):
        return self._selected

    @selected.setter
    def selected(self, value):
        if isinstance(value, dict) and not any(segment is value for segment in self._segments):
            sid = value.get('id')
            value = next((segment for segment in self._segments if sid and segment.get('id') == sid), value)
        self._selected = value

    def reset(self):
        """No subtitles and none selected, as a file closing leaves it."""
        self.segments = []
        self.selected = None

    def to_dict(self):
        """Itself as plain dicts and lists, for JSON."""
        selected = dict(self._selected) if isinstance(self._selected, dict) else self._selected
        return {'segments': [dict(segment) for segment in self._segments], 'selected': selected, **self._fields}

    @classmethod
    def from_dict(cls, values):
        return cls(values)

    # The dict it was, for the fields that are not attributes yet.

    def __getitem__(self, key):
        if key in self._ATTRIBUTES:
            return getattr(self, key)
        return self._fields[key]

    def __setitem__(self, key, value):
        if key in self._ATTRIBUTES:
            setattr(self, key, value)
        else:
            self._fields[key] = value

    def __delitem__(self, key):
        if key in self._ATTRIBUTES:
            setattr(self, key, [] if key == 'segments' else None)
        else:
            del self._fields[key]

    def __contains__(self, key):
        return key in self._ATTRIBUTES or key in self._fields

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self._ATTRIBUTES) + len(self._fields)

    def __eq__(self, other):
        if isinstance(other, (Subtitle, dict)):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    __hash__ = None

    def __repr__(self):
        return f'Subtitle({self.to_dict()!r})'

    def keys(self):
        return [*self._ATTRIBUTES, *self._fields]

    def values(self):
        return [self[key] for key in self.keys()]

    def items(self):
        return [(key, self[key]) for key in self.keys()]

    def get(self, key, default=None):
        return self[key] if key in self else default

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        if key in self._ATTRIBUTES:
            value = self[key]
            del self[key]
            return value
        return self._fields.pop(key, *default)

    def update(self, values=(), **more):
        for key, value in dict(values, **more).items():
            self[key] = value

    def clear(self):
        self.reset()
        self._fields.clear()


def _as_pieces(segment, shift=0.0):
    """The pieces of `segment`, moved by `shift` seconds; a text not timed,
    one piece over the whole subtitle."""
    text = segment.get('text')
    if not isinstance(text, list):
        text = text_of(segment)
        length = max(0.0, float(segment.get('end', 0) or 0) - float(segment.get('start', 0) or 0))
        text = [{'text': text, 'start': 0.0, 'end': length}] if text else []
    return [dict(piece, start=round(piece['start'] + shift, 3), end=round(piece['end'] + shift, 3)) for piece in text]


def joined(first, second, separator=' '):
    """The text `first` has once `second` is merged into it, `separator`
    between them: a string, or, if either is timed, pieces, `second`'s
    moved to where they fall from `first`'s start and a text not timed one
    piece over its own subtitle."""
    if not isinstance(first.get('text'), list) and not isinstance(second.get('text'), list):
        return text_of(first) + separator + text_of(second)
    pieces = _as_pieces(first)
    later = _as_pieces(second, float(second.get('start', 0) or 0) - float(first.get('start', 0) or 0))
    if pieces and later:
        pieces[-1]['text'] += separator
    return pieces + later or text_of(first) + separator + text_of(second)


def cut(segment, at):
    """The texts of `segment` cut `at` seconds from its start, for its two
    halves: the pieces that start before `at`, and those after it moved to
    start from there. None if it is not timed or a half would have none."""
    pieces = segment.get('text')
    if not isinstance(pieces, list):
        return None
    before = [dict(piece) for piece in pieces if piece['start'] < at]
    after = [dict(piece, start=round(max(0.0, piece['start'] - at), 3), end=round(max(0.0, piece['end'] - at), 3))
             for piece in pieces if piece['start'] >= at]
    if not before or not after:
        return None
    before[-1]['text'] = before[-1]['text'].rstrip()
    return before, after
