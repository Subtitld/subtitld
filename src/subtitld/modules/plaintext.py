"""Subtitles as plain text — SRT and Markdown — with a strict checker.

This backs the plain-text panel: the subtitles are written out as text, the
user edits that text, and every edit is checked before it may touch the
timeline. A checked text becomes cues; ``apply_cues`` then folds those cues
back into the live subtitle list.

SRT
    The usual blocks — number, ``00:00:01,000 --> 00:00:02,500``, text — in
    Subtitld's dialect: everything else a subtitle carries (speaker,
    translations, dubs, …) is written as ``# key: value`` comment lines
    directly before the subtitle's number, so none of it is lost. Comments
    go *before* the number on purpose: a subtitle line that happens to start
    with ``#`` (a hashtag) can then never be mistaken for one. A value is
    JSON, except a plain string, which is written bare when that is not
    ambiguous (``# speaker: A``).

Markdown
    One line for the timing, then the text, then the translations::

        [00:00:01.000 - 00:00:02.500] A {style=italic}
        AI never sleeps.
        > pt-br: A IA nunca dorme.

    After the timing come the speaker and, in braces, every other field
    (``key=value``; a value is bare when that is unambiguous, JSON
    otherwise). Translations are ``> language: text`` lines; a ``>`` line
    with no language goes on with the translation above it. A text line that
    would read as one of those starts with a backslash, as in Markdown.

    Fields of machine data nobody edits by hand (``MD_KEPT``: the dubs) are
    not written; ``apply_cues`` keeps them by matching each edited cue to
    the subtitle it came from.

JSON
    Whisper's format: ``{"language": "en-us", "segments": [{"id": 0,
    "start": 1.0, "end": 2.5, "text": "..."}, ...], "text": "..."}``, every
    other field of a subtitle as a key of its segment. ``language`` is the
    project's; the top-level ``text`` is all the subtitles' text in one line,
    written for other tools and never read back (the segments hold the text).
    Whisper's own output reads as it is, minus its decoder statistics
    (``WHISPER_DECODER_KEYS``). It goes last, out of the way of the
    segments; key order means nothing to JSON.

Every problem is an ``Issue`` with a 1-based line and column. Errors stop a
text from being applied; warnings are shown but do not.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

SRT = 'srt'
MD = 'md'
JSON = 'json'
FORMATS = (SRT, MD, JSON)

ERROR = 'error'
WARNING = 'warning'

CORE_KEYS = ('start', 'end', 'text')
# Metadata written first, in this order; anything else follows by name.
_META_ORDER = ('speaker', 'translations', 'dubbing')
# Kept with the subtitle but not written in Markdown: file paths, ids and
# clip timings, which would bury the text and cannot be edited by hand.
MD_KEPT = ('dubbing',)
# What Whisper writes about its own decoding of a segment; not subtitle data.
WHISPER_DECODER_KEYS = ('seek', 'tokens', 'temperature', 'avg_logprob', 'compression_ratio', 'no_speech_prob')
# Read in a JSON segment but not stored as fields: the numbering, and the
# decoder statistics.
_JSON_SKIPPED = ('id',) + WHISPER_DECODER_KEYS

_TIME = {
    SRT: re.compile(r'(\d{2,}):(\d{2}):(\d{2}),(\d{3})$'),
    MD: re.compile(r'(\d{2,}):(\d{2}):(\d{2})\.(\d{3})$'),
}
_EXAMPLE = {SRT: '00:00:00,000', MD: '00:00:00.000'}
_SEPARATOR = {SRT: ',', MD: '.'}

_KEY = r'[A-Za-z_][\w.\-]*'
_KEY_ONLY = re.compile(_KEY + '$')
_COMMENT = re.compile(r'^(\s*#\s*)(' + _KEY + r')(\s*:\s?)(.*)$')
# A Markdown line is a timing line when it opens with "[" and a digit;
# "[MUSIC]" and "[laughs]" stay text.
_MD_TIMING_CANDIDATE = re.compile(r'^\s*\[\s*\d')
_MD_ATTR_KEY = re.compile(_KEY + '=')
_MD_TAG = re.compile(r'([a-z]{2,3}(?:-[a-z0-9]{2,8})*):')
# A bare attribute value: one token that JSON would not read as anything.
_MD_BARE = re.compile(r'[^\s{}\[\]"=]+')


@dataclass
class Issue:
    line: int       # 1-based
    column: int     # 1-based
    length: int
    code: str
    message: str
    severity: str = ERROR
    params: dict = field(default_factory=dict)


@dataclass
class Cue:
    start: float
    end: float
    text: str
    meta: dict          # the fields this format shows (see shown_meta)
    line: int           # the timing line
    first_line: int = 0  # the cue's whole block, comments included
    last_line: int = 0
    fmt: str = SRT


@dataclass
class ParseResult:
    cues: list
    issues: list
    line_kinds: dict    # line -> 'index' | 'timing' | 'comment' | 'text' | 'translation'
    language: str | None = None     # JSON: the document's "language"

    @property
    def errors(self):
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def ok(self):
        return not self.errors


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def format_time(seconds, fmt):
    ms = int(round(max(0.0, float(seconds)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}{_SEPARATOR[fmt]}{ms:03d}'


def _is_json(text):
    try:
        json.loads(text)
    except (ValueError, TypeError):
        return False
    return True


def _encode_value(value):
    """A metadata value as comment text: bare when it is an unambiguous
    plain string, JSON otherwise (so it reads back as exactly this value)."""
    if isinstance(value, str) and value and value == value.strip() \
            and '\n' not in value and not _is_json(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _hidden(key, value):
    """Keys the text cannot carry: runtime-only ones (``_``-prefixed), names
    that cannot be written as a key, and values JSON cannot represent.
    They are kept as they are on apply."""
    if not isinstance(key, str) or key.startswith('_') or not _KEY_ONLY.match(key):
        return True
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return True
    return False


def visible_meta(segment):
    """The metadata a subtitle shows as comments, in writing order."""
    keys = [k for k in segment if k not in CORE_KEYS and not _hidden(k, segment[k])]
    keys.sort(key=lambda k: (_META_ORDER.index(k) if k in _META_ORDER else len(_META_ORDER), k))
    return {k: segment[k] for k in keys}


def shown_meta(segment, fmt):
    """The fields `fmt` writes for a subtitle — and so the ones an edited
    text of that format sets or removes. The rest are left as they are."""
    meta = visible_meta(segment)
    if fmt == JSON:
        for key in _JSON_SKIPPED:
            meta.pop(key, None)
    if fmt == MD:
        for key in MD_KEPT:
            meta.pop(key, None)
        if meta.get('translations') == {}:
            del meta['translations']
    return meta


def _primary_tags(languages):
    return None if languages is None else {code.split('-')[0] for code in languages}


def _is_language(tag, primaries):
    """A language tag as Subtitld writes them (``pt-br``), of a language it
    knows when `primaries` is given — so "> ok: fine" stays a line of text."""
    return isinstance(tag, str) and bool(_MD_TAG.fullmatch(tag + ':')) and (primaries is None or tag.split('-')[0] in primaries)


def _encode_attr(value):
    if isinstance(value, str) and _MD_BARE.fullmatch(value) and not _is_json(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _md_escape(line):
    """A text line that would read as a timing or a translation line, or
    that starts with a backslash, gets a backslash in front."""
    if line.startswith(('>', '\\')) or _MD_TIMING_CANDIDATE.match(line):
        return '\\' + line
    return line


def _md_translation_lines(translations, primaries):
    """The ``> language: text`` lines, or None when the translations cannot
    be written that way (then they go in the braces, as JSON)."""
    if not isinstance(translations, dict):
        return None
    lines = []
    for language, value in translations.items():
        if not isinstance(value, str) or not _is_language(language, primaries):
            return None
        first, *rest = value.split('\n')
        lines.append(f'> {language}: {first}' if first else f'> {language}:')
        for part in rest:
            tag = _MD_TAG.match(part)
            if tag and _is_language(tag.group(1), primaries):
                return None     # would read as the start of another translation
            lines.append(f'> {part}' if part else '>')
    return lines


def _md_block(segment, start, end, text, primaries):
    meta = shown_meta(segment, MD)
    head = f'[{start} - {end}]'
    attrs = {}
    quotes = []
    for key, value in meta.items():
        if key == 'speaker' and isinstance(value, str) and value and value == value.strip() \
                and '\n' not in value and '{' not in value:
            head += f' {value}'
        elif key == 'translations' and _md_translation_lines(value, primaries) is not None:
            quotes = _md_translation_lines(value, primaries)
        else:
            attrs[key] = value
    if attrs:
        head += ' {' + ' '.join(f'{key}={_encode_attr(value)}' for key, value in attrs.items()) + '}'
    lines = [head] + ([_md_escape(line) for line in text.split('\n')] if text else []) + quotes
    return '\n'.join(lines)


def ordered(segments):
    """Subtitles in the order the text lists them: by start, stable."""
    return sorted(segments, key=lambda s: float(s.get('start', 0.0) or 0.0))


def full_text(segments):
    """All the subtitles' text in one line, as Whisper's top-level "text"."""
    return ' '.join(' '.join(str(segment.get('text', '') or '').split())
                    for segment in ordered(segments) if str(segment.get('text', '') or '').strip())


def json_text_line(segments):
    """The top-level "text" line exactly as serialize() writes it."""
    return f'  "text": {json.dumps(full_text(segments), ensure_ascii=False)}'


def _json_text(segments, language):
    """Whisper's layout, one key per line; a field's value stays on its line."""
    head = f'  "language": {json.dumps(language, ensure_ascii=False)},\n' if isinstance(language, str) else ''
    items = []
    for index, segment in enumerate(ordered(segments)):
        fields = {'id': index,
                  'start': round(float(segment.get('start', 0.0) or 0.0), 3),
                  'end': round(float(segment.get('end', 0.0) or 0.0), 3),
                  'text': str(segment.get('text', '') or '')}
        fields.update(shown_meta(segment, JSON))
        lines = [f'      {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}' for key, value in fields.items()]
        items.append('    {\n' + ',\n'.join(lines) + '\n    }')
    body = '[\n' + ',\n'.join(items) + '\n  ]' if items else '[]'
    return '{\n' + head + '  "segments": ' + body + ',\n' + json_text_line(segments) + '\n}\n'


def serialize(segments, fmt, languages=None, language=None):
    """The subtitles as text. `languages`: the language codes translations
    may be written under in Markdown (any well-formed tag when None).
    `language`: the project's, for JSON."""
    if fmt == JSON:
        return _json_text(segments, language)
    primaries = _primary_tags(languages)
    blocks = []
    for number, segment in enumerate(ordered(segments), 1):
        start = format_time(segment.get('start', 0.0), fmt)
        end = format_time(segment.get('end', 0.0), fmt)
        text = str(segment.get('text', '') or '')
        if fmt == SRT:
            # A blank line ends an SRT subtitle, so one inside the text
            # cannot be written; it is dropped.
            text = '\n'.join(line for line in text.split('\n') if line.strip())
            comments = [f'# {k}: {_encode_value(v)}' for k, v in visible_meta(segment).items()]
            blocks.append('\n'.join(comments + [str(number), f'{start} --> {end}'] + ([text] if text else [])))
        else:
            blocks.append(_md_block(segment, start, end, text, primaries))
    return '\n\n'.join(blocks) + ('\n' if blocks else '')


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, text, fmt, languages=None):
        self.fmt = fmt
        self.languages = None if languages is None else set(languages)
        self.primaries = _primary_tags(languages)
        self.language = None
        self.lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        self.cues = []
        self.issues = []
        self.kinds = {}
        self._previous_start = None

    def issue(self, line, column, length, code, message, severity=ERROR, **params):
        self.issues.append(Issue(line, max(1, column), max(1, length), code, message, severity, params))

    def time(self, token, line, column):
        """Seconds for `token` (1-based `column`), or None after an issue."""
        match = _TIME[self.fmt].match(token)
        if not match:
            other = _SEPARATOR[MD if self.fmt == SRT else SRT]
            if re.fullmatch(r'\d{2,}:\d{2}:\d{2}' + re.escape(other) + r'\d{3}', token):
                at = token.rindex(other)
                self.issue(line, column + at, 1, 'time_separator',
                           f"Use '{_SEPARATOR[self.fmt]}' before the milliseconds",
                           separator=_SEPARATOR[self.fmt])
            else:
                self.issue(line, column, len(token), 'time_format',
                           f"'{token}' is not a time; write it as {_EXAMPLE[self.fmt]}",
                           token=token, example=_EXAMPLE[self.fmt])
            return None
        h, m, s, ms = (int(g) for g in match.groups())
        bad = False
        if m > 59:
            self.issue(line, column + match.start(2), 2, 'time_minutes', 'Minutes must be 00 to 59')
            bad = True
        if s > 59:
            self.issue(line, column + match.start(3), 2, 'time_seconds', 'Seconds must be 00 to 59')
            bad = True
        return None if bad else h * 3600 + m * 60 + s + ms / 1000.0

    def finish_cue(self, start, end, text, meta, line, end_column, end_length, first_line=0, last_line=0,
                   end_line=None):
        if start is None or end is None:
            return
        if end <= start:
            self.issue(end_line or line, end_column, end_length, 'end_before_start',
                       'The end time must be after the start time')
            return
        if not text.strip():
            self.issue(line, 1, len(self.lines[line - 1]), 'empty_text',
                       'This subtitle has no text', WARNING)
        if self._previous_start is not None and start < self._previous_start:
            self.issue(line, 1, len(self.lines[line - 1]), 'out_of_order',
                       'Starts before the previous subtitle; it will be put in order', WARNING)
        self._previous_start = start
        self.cues.append(Cue(start, end, text, meta, line, first_line or line, last_line or line, self.fmt))

    # -- SRT ------------------------------------------------------------------

    def srt(self):
        lines = self.lines
        index = 0
        expected = 1
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            stop = index
            while stop < len(lines) and lines[stop].strip():
                stop += 1
            self.srt_block(list(range(index + 1, stop + 1)), expected)
            index = stop
            expected = self._next_expected
        return self

    def srt_block(self, numbers, expected):
        """`numbers`: the block's 1-based line numbers."""
        self._next_expected = expected + 1
        line_of = lambda n: self.lines[n - 1]
        position = 0
        meta = {}
        while position < len(numbers) and line_of(numbers[position]).lstrip().startswith('#'):
            self.srt_comment(numbers[position], meta)
            position += 1
        if position == len(numbers):
            last = numbers[-1]
            self.issue(last, 1, len(line_of(last)), 'comment_detached',
                       'A comment must come directly before a subtitle; remove the blank line after it')
            self._next_expected = expected
            return

        number_line = numbers[position]
        content = line_of(number_line)
        if '-->' in content:
            self.issue(number_line, 1, len(content), 'number_missing',
                       'Write the subtitle number on the line above the timing')
        elif not content.strip().isdigit():
            self.issue(number_line, 1 + len(content) - len(content.lstrip()), len(content.strip()),
                       'number_expected', 'Expected the subtitle number here')
            for n in numbers[position:]:
                self.kinds[n] = 'text'
            return
        else:
            self.kinds[number_line] = 'index'
            value = int(content.strip())
            if value != expected:
                self.issue(number_line, 1 + len(content) - len(content.lstrip()), len(content.strip()),
                           'number_sequence', f'Expected subtitle number {expected}',
                           WARNING, expected=expected)
            self._next_expected = value + 1
            position += 1

        if position == len(numbers):
            self.issue(number_line, len(content) + 1, 1, 'timing_missing',
                       'Expected the timing line (00:00:00,000 --> 00:00:00,000) after the number')
            return
        timing_line = numbers[position]
        self.kinds[timing_line] = 'timing'
        start, end, end_column, end_length = self.srt_timing(timing_line)
        position += 1
        for n in numbers[position:]:
            self.kinds[n] = 'text'
        text = '\n'.join(line_of(n) for n in numbers[position:])
        self.finish_cue(start, end, text, meta, timing_line, end_column, end_length, numbers[0], numbers[-1])

    def srt_timing(self, line):
        content = self.lines[line - 1]
        arrow = content.find('-->')
        if arrow < 0:
            self.issue(line, 1, len(content), 'timing_format',
                       'Write the timing as 00:00:00,000 --> 00:00:00,000',
                       example='00:00:00,000 --> 00:00:00,000')
            return None, None, 1, 1
        left, right = content[:arrow], content[arrow + 3:]
        start_token = left.strip()
        if not start_token:
            self.issue(line, 1, 1, 'start_missing', 'The start time is missing')
            start = None
        else:
            start = self.time(start_token, line, left.index(start_token) + 1)
        tokens = right.split()
        if not tokens:
            self.issue(line, arrow + 4, 1, 'end_missing', 'The end time is missing')
            return start, None, arrow + 4, 1
        end_column = arrow + 3 + right.index(tokens[0]) + 1
        end = self.time(tokens[0], line, end_column)
        if len(tokens) > 1:
            extra = right.index(tokens[1], right.index(tokens[0]) + len(tokens[0]))
            self.issue(line, arrow + 3 + extra + 1, len(right[extra:].rstrip()), 'timing_extra',
                       'Unexpected text after the end time')
        return start, end, end_column, len(tokens[0])

    def srt_comment(self, line, meta):
        self.kinds[line] = 'comment'
        content = self.lines[line - 1]
        match = _COMMENT.match(content)
        if not match:
            hash_at = content.index('#')
            self.issue(line, hash_at + 1, len(content) - hash_at, 'comment_format',
                       "Write comments as '# key: value'")
            return
        key = match.group(2)
        key_column = match.start(2) + 1
        raw = match.group(4)
        value_column = match.start(4) + 1
        if key in CORE_KEYS:
            self.issue(line, key_column, len(key), 'comment_reserved',
                       f"'{key}' comes from the timing and text lines, not a comment", key=key)
            return
        if key in meta:
            self.issue(line, key_column, len(key), 'comment_duplicate',
                       f"'{key}' is set twice for this subtitle", key=key)
            return
        try:
            value = json.loads(raw)
        except ValueError as exc:
            if raw.lstrip()[:1] in ('{', '[', '"'):
                column = value_column + max(0, getattr(exc, 'pos', 0))
                self.issue(line, column, 1, 'comment_json',
                           f'Invalid value: {getattr(exc, "msg", str(exc))}',
                           detail=getattr(exc, 'msg', str(exc)))
                return
            value = raw
        meta[key] = value

    # -- Markdown ---------------------------------------------------------------

    def md(self):
        current = None
        stray = False
        for number, content in enumerate(self.lines, 1):
            if _MD_TIMING_CANDIDATE.match(content):
                self.md_close(current)
                self.kinds[number] = 'timing'
                current = self.md_timing(number)
                continue
            if current is None:
                if content.strip():
                    self.kinds[number] = 'text'
                    if not stray:   # one issue for the whole stretch
                        self.issue(number, 1 + len(content) - len(content.lstrip()), len(content.strip()),
                                   'text_before_first', 'Text before the first timing line')
                    stray = True
                continue
            if content.startswith('>') and self.md_translation(current, number, content):
                continue
            if current['open'] is not None:
                # Translations close a subtitle: only blank lines may follow.
                if content.strip():
                    self.kinds[number] = 'text'
                    current['last'] = number
                    self.issue(number, 1 + len(content) - len(content.lstrip()), len(content.strip()),
                               'text_after_translation', 'Write the subtitle text above its translations')
                continue
            current['text'].append(content[1:] if content.startswith('\\') else content)
            if content.strip():
                self.kinds[number] = 'text'
                current['last'] = number
        self.md_close(current)
        return self

    def md_translation(self, current, number, content):
        """Read a '>' line into `current`; False when it is a line of text
        (a quote with no language before any translation)."""
        rest = content[1:]
        if rest.startswith(' '):
            rest = rest[1:]
        tag = _MD_TAG.match(rest)
        if tag and _is_language(tag.group(1), self.primaries):
            language = tag.group(1)
            if language in current['translations']:
                column = len(content) - len(rest) + 1
                self.issue(number, column, len(language), 'translation_duplicate',
                           f"The '{language}' translation is written twice", language=language)
            value = rest[tag.end():]
            current['translations'][language] = [value[1:] if value.startswith(' ') else value]
            current['open'] = language
        elif current['open'] is not None:
            current['translations'][current['open']].append(rest)
        else:
            return False
        self.kinds[number] = 'translation'
        current['last'] = number
        return True

    def md_close(self, current):
        if current is None:
            return
        text = current['text']
        while text and not text[0].strip():
            text.pop(0)
        while text and not text[-1].strip():
            text.pop()
        meta = dict(current['attrs'])
        line = current['line']
        if current['speaker']:
            if 'speaker' in meta:
                self.issue(line, current['speaker_column'], len(current['speaker']), 'attr_duplicate',
                           "'speaker' is set twice for this subtitle", key='speaker')
            meta['speaker'] = current['speaker']
        if current['translations']:
            if 'translations' in meta:
                self.issue(line, 1, len(self.lines[line - 1]), 'attr_duplicate',
                           "'translations' is set twice for this subtitle", key='translations')
            meta['translations'] = {language: '\n'.join(lines) for language, lines in current['translations'].items()}
        self.finish_cue(current['start'], current['end'], '\n'.join(text), meta,
                        line, current['end_column'], current['end_length'], line, current['last'])

    def md_timing(self, line):
        """The timing line read into a fresh cue in progress."""
        content = self.lines[line - 1]
        current = {'start': None, 'end': None, 'line': line, 'text': [], 'last': line,
                   'end_column': 1, 'end_length': 1, 'speaker': '', 'speaker_column': 1,
                   'attrs': {}, 'translations': {}, 'open': None}
        bracket = content.index('[')
        close = content.find(']', bracket)
        if close < 0:
            self.issue(line, len(content) + 1, 1, 'md_bracket', "Close the timing with ']'")
            return current
        inner = content[bracket + 1:close]
        parts = re.match(r'^(\s*)(\S+)(\s+-\s+)(\S+)\s*$', inner)
        if not parts:
            self.issue(line, bracket + 1, close - bracket + 1, 'timing_format',
                       'Write the timing as [00:00:00.000 - 00:00:00.000]',
                       example='[00:00:00.000 - 00:00:00.000]')
        else:
            base = bracket + 1      # 0-based index of the first character inside
            current['start'] = self.time(parts.group(2), line, base + parts.start(2) + 1)
            current['end_column'] = base + parts.start(4) + 1
            current['end_length'] = len(parts.group(4))
            current['end'] = self.time(parts.group(4), line, current['end_column'])
        # After the timing: the speaker, then the other fields in braces.
        brace = content.find('{', close)
        speaker_part = content[close + 1:brace if brace >= 0 else len(content)]
        current['speaker'] = speaker_part.strip()
        if current['speaker']:
            current['speaker_column'] = close + 2 + len(speaker_part) - len(speaker_part.lstrip())
        if brace >= 0:
            current['attrs'] = self.md_attrs(line, content, brace)
        return current

    def md_attrs(self, line, content, brace):
        """``{key=value ...}`` starting at index `brace`, up to the line's end."""
        attrs = {}
        decoder = json.JSONDecoder()
        size = len(content)
        index = brace + 1
        while True:
            while index < size and content[index] in ' \t':
                index += 1
            if index >= size:
                self.issue(line, size + 1, 1, 'attr_unclosed', "Close the fields with '}'")
                return attrs
            if content[index] == '}':
                index += 1
                break
            key_match = _MD_ATTR_KEY.match(content, index)
            if not key_match:
                stop = index
                while stop < size and content[stop] not in ' \t}':
                    stop += 1
                self.issue(line, index + 1, stop - index, 'attr_format', 'Write fields as {key=value}')
                return attrs
            key = key_match.group(0)[:-1]
            key_column = index + 1
            index = key_match.end()
            if index >= size or content[index] in ' \t}':
                self.issue(line, key_column, len(key), 'attr_value_missing', f"'{key}' has no value", key=key)
                return attrs
            if content[index] in '"[{':
                try:
                    value, index = decoder.raw_decode(content, index)
                except ValueError as exc:
                    detail = getattr(exc, 'msg', str(exc))
                    self.issue(line, getattr(exc, 'pos', index) + 1, 1, 'attr_json',
                               f'Invalid value: {detail}', detail=detail)
                    return attrs
            else:
                stop = index
                while stop < size and content[stop] not in ' \t}':
                    stop += 1
                token = content[index:stop]
                try:
                    value = json.loads(token)   # numbers, true, false, null
                except ValueError:
                    value = token
                index = stop
            if index < size and content[index] not in ' \t}':
                self.issue(line, index + 1, 1, 'attr_format', 'Write fields as {key=value}')
                return attrs
            if key in CORE_KEYS:
                self.issue(line, key_column, len(key), 'attr_reserved',
                           f"'{key}' comes from the timing and text lines", key=key)
            elif key in MD_KEPT:
                self.issue(line, key_column, len(key), 'attr_kept',
                           f"'{key}' is kept with the subtitle but cannot be edited in Markdown; use SRT", key=key)
            elif key in attrs:
                self.issue(line, key_column, len(key), 'attr_duplicate',
                           f"'{key}' is set twice for this subtitle", key=key)
            else:
                attrs[key] = value
        trailing = content[index:]
        if trailing.strip():
            at = index + len(trailing) - len(trailing.lstrip())
            self.issue(line, at + 1, len(trailing.strip()), 'timing_extra', 'Unexpected text after the fields')
        return attrs


    # -- JSON -------------------------------------------------------------------

    def json_document(self, text):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        starts = [0]
        for index, char in enumerate(text):
            if char == '\n':
                starts.append(index + 1)

        def at(offset):
            line = max(0, _bisect(starts, offset) - 1)
            return line + 1, offset - starts[line] + 1

        def issue_at(offset, length, code, message, severity=ERROR, **params):
            line, column = at(offset)
            self.issue(line, column, length, code, message, severity, **params)

        try:
            root = _JsonReader(text).document()
        except _JsonError as exc:
            issue_at(exc.offset, 1, exc.code, exc.message, detail=exc.message)
            return self
        if root is None:
            return self     # an empty text: no subtitles
        if isinstance(root.value, list):
            items = root
        elif isinstance(root.value, dict) and 'segments' in root.keys:
            items = root.keys['segments'][1]
            if 'language' in root.keys:
                language_node = root.keys['language'][1]
                code = language_node.value
                if not isinstance(code, str):
                    issue_at(language_node.start, language_node.end - language_node.start, 'json_string',
                             "'language' must be text", key='language')
                elif self.languages is not None and code not in self.languages:
                    issue_at(language_node.start, language_node.end - language_node.start, 'json_language',
                             f"'{code}' is not one of Subtitld's languages; the project's stays as it is",
                             WARNING, language=code)
                else:
                    self.language = code
            for key, (key_offset, _node) in root.keys.items():
                if key not in ('segments', 'text', 'language'):
                    issue_at(key_offset, len(key) + 2, 'json_ignored',
                             f"'{key}' is not read; only \"segments\" is", WARNING, key=key)
            for key, offset in root.duplicates:
                issue_at(offset, len(key) + 2, 'json_duplicate', f"'{key}' is set twice", key=key)
        else:
            issue_at(root.start, 1, 'json_root', 'Write an object with a "segments" list')
            return self
        if not isinstance(items.value, list):
            issue_at(items.start, 1, 'json_root', 'Write an object with a "segments" list')
            return self

        for index, node in enumerate(items.items):
            first_line = at(node.start)[0]
            last_line = at(max(node.start, node.end - 1))[0]
            for line in range(first_line, last_line + 1):
                self.kinds[line] = 'json'
            if not isinstance(node.value, dict):
                issue_at(node.start, 1, 'json_segment', 'Each segment must be an object')
                continue
            for key, offset in node.duplicates:
                issue_at(offset, len(key) + 2, 'json_duplicate', f"'{key}' is set twice", key=key)
            fields = node.keys
            values = {}
            bad = False
            for key, kind in (('start', 'number'), ('end', 'number'), ('text', 'string')):
                if key not in fields:
                    issue_at(node.start, 1, 'json_missing', f"'{key}' is missing", key=key)
                    bad = True
                    continue
                value_node = fields[key][1]
                value = value_node.value
                if kind == 'number' and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    issue_at(value_node.start, value_node.end - value_node.start, 'json_number',
                             f"'{key}' must be a number of seconds", key=key)
                    bad = True
                elif kind == 'number' and value < 0:
                    issue_at(value_node.start, value_node.end - value_node.start, 'json_negative',
                             f"'{key}' cannot be negative", key=key)
                    bad = True
                elif kind == 'string' and not isinstance(value, str):
                    issue_at(value_node.start, value_node.end - value_node.start, 'json_string',
                             f"'{key}' must be text", key=key)
                    bad = True
                else:
                    values[key] = value
            if 'id' in fields:
                id_node = fields['id'][1]
                if isinstance(id_node.value, bool) or not isinstance(id_node.value, int):
                    issue_at(id_node.start, id_node.end - id_node.start, 'json_integer',
                             "'id' must be a whole number")
                    bad = True
                elif id_node.value != index:
                    issue_at(id_node.start, id_node.end - id_node.start, 'number_sequence',
                             f'Expected subtitle number {index}', WARNING, expected=index)
            if bad:
                continue
            meta = {key: value_node.value for key, (_offset, value_node) in fields.items()
                    if key not in CORE_KEYS and key not in _JSON_SKIPPED}
            end_node = fields['end'][1]
            end_line, end_column = at(end_node.start)
            self.finish_cue(float(values['start']), float(values['end']), values['text'].strip(), meta,
                            first_line, end_column, end_node.end - end_node.start, first_line, last_line,
                            end_line=end_line)
        return self


def _bisect(starts, offset):
    low, high = 0, len(starts)
    while low < high:
        middle = (low + high) // 2
        if starts[middle] <= offset:
            low = middle + 1
        else:
            high = middle
    return low


class _JsonError(Exception):
    def __init__(self, code, message, offset):
        super().__init__(message)
        self.code = code
        self.message = message
        self.offset = offset


class _JsonNode:
    """A JSON value with where it is: `start`/`end` offsets; for an object,
    `keys` maps each key to (key offset, value node) and `duplicates` lists
    the repeated ones; for an array, `items` are the element nodes."""

    def __init__(self, value, start, end, keys=None, items=None, duplicates=None):
        self.value = value
        self.start = start
        self.end = end
        self.keys = keys or {}
        self.items = items or []
        self.duplicates = duplicates or []


_JSON_SPACE = re.compile(r'[ \t\n\r]*')
_JSON_NUMBER = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?')


class _JsonReader:
    """Strict JSON (as the json module reads it) that keeps positions, so a
    problem in a value can be pointed at — the json module only places its
    own syntax errors."""

    def __init__(self, text):
        self.text = text

    def space(self, index):
        return _JSON_SPACE.match(self.text, index).end()

    def document(self):
        index = self.space(0)
        if index >= len(self.text):
            return None
        node = self.value(index)
        rest = self.space(node.end)
        if rest < len(self.text):
            raise _JsonError('json_trailing', 'Unexpected text after the JSON', rest)
        return node

    def value(self, index):
        text = self.text
        char = text[index:index + 1]
        if char == '{':
            return self.object(index)
        if char == '[':
            return self.array(index)
        if char == '"':
            try:
                value, end = json.decoder.scanstring(text, index + 1)
            except ValueError as exc:
                raise _JsonError('json_bad_string', getattr(exc, 'msg', str(exc)), getattr(exc, 'pos', index))
            return _JsonNode(value, index, end)
        number = _JSON_NUMBER.match(text, index)
        if number and number.end() > index:
            literal = number.group(0)
            value = float(literal) if any(c in literal for c in '.eE') else int(literal)
            return _JsonNode(value, index, number.end())
        for literal, value in (('true', True), ('false', False), ('null', None)):
            if text.startswith(literal, index):
                return _JsonNode(value, index, index + len(literal))
        raise _JsonError('json_expect_value', 'Expecting a value', index)

    def object(self, start):
        text = self.text
        keys, values, duplicates = {}, {}, []
        index = self.space(start + 1)
        if text[index:index + 1] == '}':
            return _JsonNode({}, start, index + 1)
        while True:
            if text[index:index + 1] != '"':
                raise _JsonError('json_expect_key', 'Expecting a key in double quotes', index)
            key_node = self.value(index)
            index = self.space(key_node.end)
            if text[index:index + 1] != ':':
                raise _JsonError('json_expect_colon', "Expecting ':' after the key", index)
            value_node = self.value(self.space(index + 1))
            if key_node.value in keys:
                duplicates.append((key_node.value, key_node.start))
            keys[key_node.value] = (key_node.start, value_node)
            values[key_node.value] = value_node.value
            index = self.space(value_node.end)
            char = text[index:index + 1]
            if char == '}':
                return _JsonNode(values, start, index + 1, keys=keys, duplicates=duplicates)
            if char != ',':
                raise _JsonError('json_expect_object_end', "Expecting ',' or '}'", index)
            index = self.space(index + 1)

    def array(self, start):
        text = self.text
        items = []
        index = self.space(start + 1)
        if text[index:index + 1] == ']':
            return _JsonNode([], start, index + 1)
        while True:
            node = self.value(index)
            items.append(node)
            index = self.space(node.end)
            char = text[index:index + 1]
            if char == ']':
                return _JsonNode([item.value for item in items], start, index + 1, items=items)
            if char != ',':
                raise _JsonError('json_expect_array_end', "Expecting ',' or ']'", index)
            index = self.space(index + 1)


def parse(text, fmt, languages=None):
    """Check `text`. `languages`: as for serialize()."""
    reader = _Reader(text, fmt, languages)
    if fmt == JSON:
        reader.json_document(text)
    else:
        (reader.srt if fmt == SRT else reader.md)()
    reader.issues.sort(key=lambda issue: (issue.line, issue.column))
    return ParseResult(reader.cues, reader.issues, reader.kinds, reader.language)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def _ms(seconds):
    return int(round(float(seconds or 0.0) * 1000))


def _key(start, end, text):
    return (_ms(start), _ms(end), text or '')


def _same(a, b):
    if a == b:
        return True
    # Unequal in Python can still be the same JSON (a tuple and a list).
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _differs(segment, cue):
    if _key(segment.get('start'), segment.get('end'), segment.get('text')) != _key(cue.start, cue.end, cue.text):
        return True
    current = shown_meta(segment, cue.fmt)
    return set(current) != set(cue.meta) or any(not _same(current[k], cue.meta[k]) for k in current)


def _pair(segments, cues):
    """(pairs, removed): each cue with the subtitle it came from (None
    for a new one), in cue order, and the subtitles no cue matched."""
    current = ordered(segments)
    matcher = difflib.SequenceMatcher(
        None, [_key(s.get('start'), s.get('end'), s.get('text')) for s in current],
        [_key(c.start, c.end, c.text) for c in cues], autojunk=False)
    pairs, removed = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('equal', 'replace'):
            shared = min(i2 - i1, j2 - j1)
            pairs.extend((current[i1 + k], cues[j1 + k]) for k in range(shared))
            removed.extend(current[i1 + shared:i2])
            pairs.extend((None, cues[j1 + k]) for k in range(shared, j2 - j1))
        elif tag == 'delete':
            removed.extend(current[i1:i2])
        else:
            pairs.extend((None, cue) for cue in cues[j1:j2])
    return pairs, removed


def segments_for(segments, cues):
    """The subtitle each cue stands for, or None (a cue not applied yet)."""
    return [segment for segment, _cue in _pair(segments, cues)[0]]


def apply_cues(segments, cues, dry_run=False):
    """Fold `cues` into the `segments` list in place; True when it changed.

    Each cue is matched to the subtitle it came from — unchanged ones by
    timing and text, edited ones by position within the edited stretch — so
    the subtitle objects survive (the selection, the audio engine's clips
    and the undo history all hold them) and so does anything the text does
    not show: runtime keys, and the fields its format keeps unwritten
    (``MD_KEPT``). The fields it does show are set to exactly the cue's.
    """
    pairs, removed = _pair(segments, cues)
    changed = bool(removed) or any(segment is None or _differs(segment, cue) for segment, cue in pairs)
    if not changed:
        # The same subtitles; at most their order in the list differs.
        changed = [id(s) for s in ordered(segments)] != [id(s) for s in segments]
    if dry_run or not changed:
        return changed

    result = []
    for segment, cue in pairs:
        if segment is None:
            segment = {'start': cue.start, 'end': cue.end, 'text': cue.text}
            segment.update(cue.meta)
        else:
            if _ms(segment.get('start')) != _ms(cue.start):
                segment['start'] = cue.start
            if _ms(segment.get('end')) != _ms(cue.end):
                segment['end'] = cue.end
            if (segment.get('text') or '') != cue.text:
                segment['text'] = cue.text
            for key in list(shown_meta(segment, cue.fmt)):
                if key not in cue.meta:
                    del segment[key]
            for key, value in cue.meta.items():
                if key not in segment or not _same(segment[key], value):
                    segment[key] = value
        result.append(segment)
    # In time order, as the timeline keeps them; text order breaks ties.
    segments[:] = sorted(result, key=lambda s: float(s.get('start', 0.0) or 0.0))
    return True
