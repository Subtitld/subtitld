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
either way.

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


def text_of(value):
    """The words of `value`: a subtitle (any dict) or its 'text', a string or
    a list of pieces. '' for none."""
    if isinstance(value, dict):
        value = value.get('text')
    if isinstance(value, list):
        return ''.join(str(piece.get('text') or '') for piece in value if isinstance(piece, dict))
    return value or ''


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
        text = self.get('text')
        return text if isinstance(text, list) else []


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
