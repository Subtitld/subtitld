"""Subtitles as documents people read, each laid out as the trade lays
it out: as plain text, a table (CSV), a Word or an OpenDocument text, or a
PDF. The export dialog's Documents tab picks the format and, under it,
what the document holds:

Transcript (``transcript``)
    The words as running text, a paragraph for each speaker's turn, and a
    new one after a pause; each starting, if asked, with its time and its
    speaker, as transcription services and accessible media (W3C WAI's
    transcripts) write them: the speaker in the margin, the words beside.

Line-numbered transcript (``numbered``)
    The words as courts and conversation analysts print them: 25 numbered
    lines a page, each turn opening on its speaker in capitals; and, if
    asked, the pauses timed to the tenth of a second, "(1.4)", and the
    talk that overlaps marked "[", after Gail Jefferson's notation.

Dialogue list (``script``)
    A table, a line for each subtitle: its time, its speaker, its text, as
    a dialogue list (as-broadcast script) for delivery; and, if asked, a
    column for notes (the dialogue list's annotations).

Spotting list (``spotting``)
    A table of the subtitles as they are cued: each one's number, in, out,
    duration and text, and how fast it reads (characters a second).

Translation sheet (``translation``)
    A table: the time, the subtitle, then its translations, one column
    for each language.

Dubbing script (``dubbing``)
    A table for the voices: each subtitle's number, character, in, out,
    duration, text and how many characters that is; grouped by character,
    if asked; above it, with the details, the characters and how much
    each one says.

Speaker list (``speakers``)
    Who speaks, and how much: subtitles, words, time speaking and its
    share, and when each first and last speaks.

Markers (``markers``)
    CSV only: Adobe Audition's marker list (modules.csv_export).

What each holds is built once (``build``) as a Document: a heading
(its title, the kind of document, its details and its speakers) and
sections of turns, tables or numbered lines; each format writes that. A
PDF is the document laid out in HTML by Qt and painted page by page with
a footer; the dialog's preview is its first page."""

import csv
import datetime
import io
import os
import re
import textwrap
import zipfile
from xml.sax.saxutils import escape

from subtitld.modules import csv_export, markup
from subtitld.modules.segment import text_of

TRANSCRIPT = 'transcript'
NUMBERED = 'numbered'
SCRIPT = 'script'
SPOTTING = 'spotting'
TRANSLATION = 'translation'
DUBBING = 'dubbing'
SPEAKERS = 'speakers'
MARKERS = 'markers'

# The formats, as the dialog lists them, and what each can hold (the
# first is the one chosen when none is).
_PAGES = (TRANSCRIPT, NUMBERED, SCRIPT, SPOTTING, TRANSLATION, DUBBING, SPEAKERS)
FORMATS = {
    'CSV': {'extension': 'csv', 'contents': (SCRIPT, SPOTTING, TRANSLATION, DUBBING, SPEAKERS, MARKERS)},
    'TXT': {'extension': 'txt', 'contents': (TRANSCRIPT, NUMBERED, SCRIPT, SPOTTING, SPEAKERS)},
    'DOCX': {'extension': 'docx', 'contents': _PAGES},
    'ODT': {'extension': 'odt', 'contents': _PAGES},
    'PDF': {'extension': 'pdf', 'contents': _PAGES},
}

# The times: none, where each starts, or where each starts and ends.
TIMES = ('none', 'start', 'start_end')
# How a time is written: a clock to the millisecond, or a timecode in the
# video's frames.
TIME_FORMATS = ('clock', 'frames')
# What the options are, when none are given.
DEFAULTS = {'times': 'start', 'time_format': 'clock', 'speakers': True, 'title': True,
            'languages': None, 'by_speaker': False, 'notes': False, 'pauses': False}
# A pause this long, in seconds, starts a new paragraph of a transcript.
PARAGRAPH_PAUSE = 2.0
# A pause shorter than this is not timed in a line-numbered transcript
# (Jefferson's micropause).
MICRO_PAUSE = 0.2
# A line-numbered transcript's page: its lines, and how many characters
# each holds (a court's 25 lines, of about 60 characters).
LINES_PER_PAGE = 25
LINE_WIDTH = 60
# Faster than this, in characters a second, a subtitle is hard to read
# (streaming services' limit for adults); the spotting list marks it.
READING_SPEED = 20.0

# The words a document is written with, by the key the dialog's texts
# know them by (export_dialog.document_*), and in English for CSV and when
# no texts are given.
TEXTS = {
    'content_transcript': 'Transcript', 'content_numbered': 'Line-numbered transcript',
    'content_script': 'Dialogue list', 'content_spotting': 'Spotting list',
    'content_translation': 'Translation sheet', 'content_dubbing': 'Dubbing script',
    'content_speakers': 'Speaker list',
    'column_number': '#', 'column_start': 'Start', 'column_end': 'End', 'column_time': 'Time',
    'column_speaker': 'Speaker', 'column_character': 'Character', 'column_text': 'Text',
    'column_duration': 'Duration', 'column_characters': 'Characters', 'column_cps': 'Chars/s',
    'column_notes': 'Notes', 'column_subtitles': 'Subtitles', 'column_words': 'Words',
    'column_speaking': 'Speaking', 'column_share': 'Share', 'column_first': 'First', 'column_last': 'Last',
    'detail_length': 'Running time', 'detail_subtitles': 'Subtitles', 'detail_speakers': 'Speakers',
    'detail_words': 'Words', 'detail_frame_rate': 'Frame rate', 'detail_languages': 'Languages',
    'detail_date': 'Date', 'section_cast': 'Characters', 'section_script': 'Script',
    'no_speaker': 'No speaker', 'page': 'Page',
}

# The colours of the page: its ink, the softer ink of what is said about
# the words, the hairlines, Subtitld's blue, its wash, and a warning.
INK = '#25323d'
MUTED = '#6f7f8c'
HAIRLINE = '#dce3e9'
ACCENT = '#1a73a8'
WASH = '#e8f1f8'
WARNING = '#c4472f'
# A speaker's colour, when the project gives it none.
_SPEAKER_COLORS = ('#1a73a8', '#c5653a', '#2e8b6e', '#8a5cb0', '#b88a1b', '#c2456e', '#4f7f9f', '#6b8e23')
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


def contents_of(fmt):
    return FORMATS[fmt]['contents']


def content_of(fmt, content=None):
    """`content` if `fmt` can hold it, else the first that it can."""
    contents = contents_of(fmt)
    return content if content in contents else contents[0]


def languages_of(segments):
    """The languages the subtitles are translated into, in the order they
    first come."""
    out = []
    for segment in segments or []:
        for language in (segment.get('translations') or {}):
            if language not in out and isinstance(segment['translations'][language], str):
                out.append(language)
    return out


def timestamp(seconds, time_format='clock', fps=None):
    """`seconds` as "HH:MM:SS.mmm", or, in frames, the timecode
    "HH:MM:SS:FF" of the frame then at `fps` (counted at the whole rate
    nearest it, non-drop)."""
    seconds = max(0.0, float(seconds))
    if time_format == 'frames':
        rate = max(1, int(round(float(fps or 25.0))))
        # The frame on screen at that moment.
        frames = int(seconds * float(fps or 25.0) + 1e-6)
        secs, frame = divmod(frames, rate)
        hours, rest = divmod(secs, 3600)
        return f'{hours:02d}:{rest // 60:02d}:{rest % 60:02d}:{frame:02d}'
    total = int(round(seconds * 1000))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def duration(start, end, time_format='clock', fps=None):
    """How long from `start` to `end` is: "1.50s", or, in frames, "SS:FF",
    the frames between their timecodes."""
    if time_format == 'frames':
        rate = max(1, int(round(float(fps or 25.0))))
        frame = lambda seconds: int(max(0.0, float(seconds)) * float(fps or 25.0) + 1e-6)
        secs, frames = divmod(max(0, frame(end) - frame(start)), rate)
        return f'{secs:02d}:{frames:02d}'
    return f'{max(0.0, float(end) - float(start)):.2f}s'


def length(seconds):
    """A running time, "H:MM:SS", or "M:SS" under an hour."""
    total = int(round(max(0.0, float(seconds))))
    hours, rest = divmod(total, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


def _text(text):
    """A subtitle's text as it reads, its lines kept."""
    return '\n'.join(' '.join(line.split()) for line in markup.plain(str(text or '')).split('\n')).strip()


def _line(text):
    """A subtitle's text as it reads, on one line."""
    return ' '.join(_text(text).split())


class Section:
    """A part of a document, under its `heading` (or none): turns of
    speech, a table, or numbered lines.

    turns
        [(time, speaker, text)], the time and speaker maybe empty.
    table
        A `header`, what each column holds (`kinds`: 'time', 'speaker',
        'text', ...), `rows` of cells, and each column's share of the
        page's width in percent (`widths`).
    lines
        `pages` of [(time, label, text)]: a line each, numbered on its
        page; the time and the label (a turn's speaker) on a turn's first
        line only."""

    def __init__(self, kind, heading=None, turns=None, header=None, kinds=None, rows=None, pages=None):
        self.kind = kind
        self.heading = heading
        self.turns = turns
        self.header = header
        self.kinds = kinds or []
        self.rows = rows
        self.pages = pages
        self.widths = _widths(self.kinds) if kind == 'table' else None

    @property
    def speaker_at(self):
        """Which column is the speaker's, if one is."""
        return self.kinds.index('speaker') if 'speaker' in self.kinds else None


class Document:
    """What a document holds, before a format writes it: a heading (the
    `title`, what kind of document it is, its `details` as (label, value)
    and its `speakers` as (name, colour)), then its `sections`."""

    def __init__(self, content, title='', kind='', details=None, speakers=None, sections=None):
        self.content = content
        self.title = title
        self.kind = kind
        self.details = details or []
        self.speakers = speakers or []
        self.sections = sections or []
        # Each speaker's colour, by name.
        self.colors = dict(self.speakers)
        # The word for a page, for a text's pages.
        self.page = TEXTS['page']

    @property
    def table(self):
        """The document's main table (its last), for CSV."""
        tables = [section for section in self.sections if section.kind == 'table']
        return tables[-1] if tables else None


# The share of the page's width a column of each kind takes, in percent;
# the texts share what is left.
_WIDTH = {'number': 5, 'time': 13, 'speaker': 15, 'duration': 10, 'characters': 11, 'cps': 9, 'notes': 18,
          'subtitles': 10, 'words': 9, 'speaking': 11, 'share': 17, 'first': 12, 'last': 12}


def _widths(kinds):
    fixed = sum(_WIDTH.get(kind, 0) for kind in kinds)
    texts = sum(1 for kind in kinds if kind not in _WIDTH)
    if not texts:
        return [_WIDTH[kind] * 100 / fixed for kind in kinds]
    return [_WIDTH.get(kind, max(10, (100 - fixed) / texts)) for kind in kinds]


def _colors(names, speakers):
    """Each of `names` with its colour: the project's (`speakers`, as
    session.SPEAKERS), else one of ours."""
    out = []
    for index, name in enumerate(names):
        color = str(((speakers or {}).get(name) or {}).get('color') or '')
        out.append((name, color if _COLOR.match(color) else _SPEAKER_COLORS[index % len(_SPEAKER_COLORS)]))
    return out


def _stats(ordered, no_speaker):
    """Who speaks and how much, the one who speaks longest first: [(name,
    subtitles, words, seconds speaking, first, last)]."""
    found = {}
    for segment in ordered:
        name = str(segment.get('speaker') or '') or no_speaker
        start, end = float(segment['start']), float(segment['end'])
        entry = found.setdefault(name, [name, 0, 0, 0.0, start, end])
        entry[1] += 1
        entry[2] += len(_line(text_of(segment)).split())
        entry[3] += max(0.0, end - start)
        entry[4], entry[5] = min(entry[4], start), max(entry[5], end)
    return sorted((tuple(entry) for entry in found.values()), key=lambda entry: (-entry[3], entry[4]))


def build(content, segments, options=None, fps=None, title='', texts=None, speakers=None):
    """The Document of `content` for `segments`, with `options` (DEFAULTS
    for those not given), times in frames counted at `fps`, its words from
    `texts` (TEXTS for those not given), the speakers' colours from
    `speakers` (as session.SPEAKERS)."""
    options = dict(DEFAULTS, **{key: value for key, value in (options or {}).items() if value is not None})
    words = dict(TEXTS, **(texts or {}))
    when = lambda seconds: timestamp(seconds, options['time_format'], fps)
    long = lambda segment: duration(segment['start'], segment['end'], options['time_format'], fps)
    times = options['times']
    ordered = sorted((s for s in segments or [] if _text(text_of(s))),
                     key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))
    named = []
    for segment in ordered:
        if segment.get('speaker') and str(segment['speaker']) not in named:
            named.append(str(segment['speaker']))
    stats = _stats(ordered, words['no_speaker'])
    colors = dict(_colors(named, speakers))
    colors.setdefault(words['no_speaker'], MUTED)
    shown = options['speakers'] or content in (DUBBING, SPEAKERS)

    document = Document(content, title if options['title'] else '', words.get(f'content_{content}', ''),
                        speakers=[(name, colors[name]) for name in named] if shown and options['title'] else None)
    if options['title']:
        details = [(words['detail_length'], length(ordered[-1]['end'] if ordered else 0)),
                   (words['detail_subtitles'], str(len(ordered)))]
        if named:
            details.append((words['detail_speakers'], str(len(named))))
        if content in (TRANSCRIPT, NUMBERED, SPEAKERS):
            details.append((words['detail_words'], str(sum(entry[2] for entry in stats))))
        if content == TRANSLATION:
            details.append((words['detail_languages'],
                            ', '.join(options['languages'] or languages_of(ordered)) or '—'))
        if options['time_format'] == 'frames':
            details.append((words['detail_frame_rate'], f'{float(fps or 25.0):g} fps'))
        details.append((words['detail_date'], datetime.date.today().isoformat()))
        document.details = details

    def time_cells(segment):
        if times == 'start':
            return [when(segment['start'])]
        if times == 'start_end':
            return [when(segment['start']), when(segment['end'])]
        return []

    time_header = {'none': [], 'start': [words['column_time']],
                   'start_end': [words['column_start'], words['column_end']]}[times]
    time_kinds = ['time'] * len(time_header)
    notes = ([words['column_notes']], ['notes']) if options['notes'] else ([], [])

    def table(header, kinds, rows, heading=None):
        return Section('table', heading, header=header, kinds=kinds, rows=rows)

    if content == TRANSCRIPT:
        turns, turn, last = [], None, None
        for segment in ordered:
            speaker = str(segment.get('speaker') or '') if options['speakers'] else ''
            if turn is None or speaker != turn['speaker'] or \
                    float(segment['start']) - float(last['end']) >= PARAGRAPH_PAUSE:
                turn = {'speaker': speaker, 'start': segment['start'], 'words': []}
                turns.append(turn)
            turn['words'].append(_line(text_of(segment)))
            last = segment
        document.sections = [Section('turns', turns=[(when(turn['start']) if times != 'none' else '', turn['speaker'],
                                                      ' '.join(turn['words'])) for turn in turns])]

    elif content == NUMBERED:
        document.sections = [Section('lines', pages=_numbered(ordered, options, when))]

    elif content == SCRIPT:
        speaker = ([words['column_speaker']], ['speaker']) if options['speakers'] else ([], [])
        rows = [time_cells(s) + ([str(s.get('speaker') or '')] if options['speakers'] else []) + [_text(text_of(s))]
                + [''] * len(notes[0]) for s in ordered]
        document.sections = [table(time_header + speaker[0] + [words['column_text']] + notes[0],
                                   time_kinds + speaker[1] + ['text'] + notes[1], rows)]

    elif content == SPOTTING:
        rows = []
        for number, segment in enumerate(ordered, start=1):
            text = _text(text_of(segment))
            seconds = float(segment['end']) - float(segment['start'])
            rows.append([str(number), when(segment['start']), when(segment['end']), long(segment), text,
                         f'{len(text.replace(chr(10), " ")) / seconds:.1f}' if seconds > 0 else '—'])
        document.sections = [table([words['column_number'], words['column_start'], words['column_end'],
                                    words['column_duration'], words['column_text'], words['column_cps']],
                                   ['number', 'time', 'time', 'duration', 'text', 'cps'], rows)]

    elif content == TRANSLATION:
        languages = options['languages'] or languages_of(ordered)
        languages = [language for language in languages if language]
        rows = [time_cells(s) + [_text(text_of(s))] +
                [_text((s.get('translations') or {}).get(language, '')) for language in languages]
                + [''] * len(notes[0]) for s in ordered]
        document.sections = [table(time_header + [words['column_text']] + list(languages) + notes[0],
                                   time_kinds + ['text'] * (1 + len(languages)) + notes[1], rows)]

    elif content == DUBBING:
        header = [words['column_number'], words['column_character'], words['column_start'], words['column_end'],
                  words['column_duration'], words['column_text'], words['column_characters']] + notes[0]
        kinds = ['number', 'speaker', 'time', 'time', 'duration', 'text', 'characters'] + notes[1]
        rows = []
        for number, segment in enumerate(ordered, start=1):
            text = _text(text_of(segment))
            rows.append([str(number), str(segment.get('speaker') or ''), when(segment['start']),
                         when(segment['end']), long(segment), text,
                         str(len(text.replace('\n', ' ')))] + [''] * len(notes[0]))
        sections = []
        if options['title']:
            sections.append(table(
                [words['column_character'], words['column_subtitles'], words['column_words'],
                 words['column_speaking'], words['column_first']],
                ['speaker', 'subtitles', 'words', 'speaking', 'first'],
                [[name, str(count), str(said), length(seconds), when(first)]
                 for name, count, said, seconds, first, _last in stats], words['section_cast']))
        if options['by_speaker']:
            groups = {}
            for row in rows:
                groups.setdefault(row[1], []).append(row)
            sections += [table(header, kinds, group, name or words['no_speaker']) for name, group in groups.items()]
        else:
            sections.append(table(header, kinds, rows, words['section_script'] if sections else None))
        document.sections = sections

    elif content == SPEAKERS:
        total = sum(entry[3] for entry in stats) or 1.0
        rows = [[name, str(count), str(said), length(seconds), f'{100 * seconds / total:.0f}%', when(first),
                 when(last)] for name, count, said, seconds, first, last in stats]
        document.sections = [table([words['column_speaker'], words['column_subtitles'], words['column_words'],
                                    words['column_speaking'], words['column_share'], words['column_first'],
                                    words['column_last']],
                                   ['speaker', 'subtitles', 'words', 'speaking', 'share', 'first', 'last'], rows)]
    else:
        raise ValueError(f'unknown document content: {content!r}')

    document.colors = colors
    document.page = words['page']
    return document


def _numbered(ordered, options, when):
    """The lines of a line-numbered transcript, in pages: each turn opens
    on its speaker in capitals; with `options['pauses']`, a pause between
    turns its own line, "(1.4)", one inside a turn in the words, and talk
    that starts before the last ends opens with "[" (Jefferson)."""
    pauses = options['pauses']
    turns, turn, last = [], None, None
    for segment in ordered:
        speaker = str(segment.get('speaker') or '').upper() if options['speakers'] else ''
        gap = float(segment['start']) - float(last['end']) if last is not None else 0.0
        if turn is None or speaker != turn['speaker'] or gap >= PARAGRAPH_PAUSE:
            if pauses and last is not None and gap >= MICRO_PAUSE:
                turns.append({'pause': f'({gap:.1f})'})
            turn = {'speaker': speaker, 'start': segment['start'], 'words': []}
            turns.append(turn)
        elif pauses and gap >= MICRO_PAUSE:
            turn['words'].append(f'({gap:.1f})')
        turn['words'].append(('[' if pauses and last is not None and gap < 0 else '') + _line(text_of(segment)))
        last = segment
    lines = []
    for turn in turns:
        if 'pause' in turn:
            lines.append(('', '', turn['pause']))
            continue
        label = f'{turn["speaker"]}:' if turn['speaker'] else ''
        indent = ' ' * (len(label) + 1) if label else ''
        wrapped = textwrap.wrap(' '.join(turn['words']), LINE_WIDTH, initial_indent=indent,
                                break_on_hyphens=False) or ['']
        time = when(turn['start']) if options['times'] != 'none' else ''
        lines.append((time, label, wrapped[0][len(indent):]))
        lines += [('', '', line) for line in wrapped[1:]]
    return [lines[index:index + LINES_PER_PAGE] for index in range(0, len(lines), LINES_PER_PAGE)] or [[]]


def title_for(subtitle, video, path=''):
    """A document's title: the subtitles', else the video's name, else
    the subtitles' file's, else `path`'s."""
    title = ((subtitle or {}).get('metadata') or {}).get('title')
    if title:
        return str(title)
    for named in ((video or {}).get('filepath'), (subtitle or {}).get('filepath'), path):
        if named:
            return os.path.splitext(os.path.basename(named))[0]
    return ''


# The formats, each writing a Document.

def txt(document):
    """The text of `document`: its title, its kind and details on a line,
    then its sections: turns as paragraphs, "[time] Speaker: text", with a
    blank line between them; tables a line a row, their columns aligned
    and the texts last; numbered lines by page."""
    lines = []
    if document.title:
        lines.append(document.title)
    if document.details:
        lines.append(' · '.join([document.kind] + [f'{label} {value}' for label, value in document.details]))
    if lines:
        lines.append('')
    for section in document.sections:
        if section.heading:
            lines += [section.heading.upper(), '']
        if section.kind == 'turns':
            for time, speaker, text in section.turns:
                label = ' '.join(part for part in (f'[{time}]' if time else '', speaker) if part)
                lines += [f'{label}: {text}' if label else text, '']
        elif section.kind == 'lines':
            timed = any(time for page in section.pages for time, _label, _text in page)
            for number, page in enumerate(section.pages, start=1):
                if len(section.pages) > 1:
                    lines += [f'{document.page} {number}', '']
                for line, (time, label, text) in enumerate(page, start=1):
                    said = f'{label} {text}' if label else text
                    lines.append(f'{line:>2}  ' + (f'{time:<12}  ' if timed else '') + said.rstrip())
                lines.append('')
        else:
            # The texts last, and no room for notes on a text.
            order = [index for index, kind in enumerate(section.kinds) if kind not in ('text', 'notes')] + \
                    [index for index, kind in enumerate(section.kinds) if kind == 'text']
            said = 'text' in section.kinds
            table = [section.header] + [[' / '.join(part for part in cell.split('\n') if part) for cell in row]
                                        for row in section.rows]
            widths = {index: max(len(row[index]) for row in table) for index in order}
            for at, row in enumerate(table):
                cells = []
                for index in order:
                    cell = row[index]
                    if index == section.speaker_at and cell and at and said:
                        cell += ':'
                    last = index == order[-1]
                    colon = 1 if index == section.speaker_at and said else 0
                    cells.append(cell if last else cell.ljust(widths[index] + colon))
                lines.append('  '.join(cells).rstrip())
                if not at:
                    lines.append('-' * len(lines[-1]))
            lines.append('')
    return '\n'.join(lines).rstrip('\n') + '\n'


def csv_text(document):
    """`document`'s table as CSV, its header first."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\r\n')
    table = document.table
    if table is not None:
        writer.writerow(table.header)
        for row in table.rows:
            writer.writerow([cell.replace('\n', ' ') for cell in row])
    return out.getvalue()


# HTML, for the PDF and the preview. Qt's text layout reads a subset of CSS:
# no flex or grid, so the page is tables; letter-spacing, text-transform,
# line-height and one-sided borders it does read.
_SANS = "'Ubuntu', 'DejaVu Sans', sans-serif"
_DISPLAY = "'Montserrat', 'Ubuntu', 'DejaVu Sans', sans-serif"
_MONO = "'Ubuntu Mono', 'DejaVu Sans Mono', monospace"


def html(document, size=10.5):
    """`document` as HTML for Qt's text layout (QTextDocument), in a type
    `size` points tall."""
    small = size - 1.5
    # Its colour said: Qt lays out text in the application's palette, which
    # a dark theme makes light, on a white page.
    style = (f'body, p, td, th, span {{ color: {INK}; }}'
             f'body {{ font-family: {_SANS}; font-size: {size}pt; }}'
             f'.kind {{ font-family: {_DISPLAY}; font-size: {size - 3:.1f}pt; font-weight: 600; color: {ACCENT}; '
             'letter-spacing: 2px; text-transform: uppercase; }'
             f'.title {{ font-family: {_DISPLAY}; font-size: {size * 2.3:.0f}pt; font-weight: 300; }}'
             f'.label {{ font-family: {_DISPLAY}; font-size: {size - 4:.1f}pt; font-weight: 600; color: {MUTED}; '
             'letter-spacing: 1px; text-transform: uppercase; }'
             f'.value {{ font-size: {small}pt; }}'
             f'.heading {{ font-family: {_DISPLAY}; font-size: {size - 2:.1f}pt; font-weight: 600; '
             'letter-spacing: 1.5px; text-transform: uppercase; }'
             f'.time {{ font-family: {_MONO}; font-size: {small}pt; color: {MUTED}; }}'
             f'.muted {{ color: {MUTED}; }} .warning {{ color: {WARNING}; font-weight: 600; }}'
             f'th {{ font-family: {_DISPLAY}; font-size: {size - 4:.1f}pt; font-weight: 600; color: {MUTED}; '
             f'letter-spacing: 1px; text-transform: uppercase; text-align: left; padding: 0 6px 5px 6px; '
             'white-space: nowrap; }'
             f'td.cell {{ font-size: {small}pt; padding: 5px 6px; '
             'vertical-align: top; }'
             f'td.gutter {{ padding: 0 12px 12px 0; vertical-align: top; }}'
             f'td.said {{ padding: 0 0 12px 0; vertical-align: top; }}'
             f'td.ln {{ font-family: {_MONO}; font-size: {small}pt; color: {MUTED}; text-align: right; '
             f'padding: 3px 10px 3px 0; border-right: 1px solid {ACCENT}; '
             'white-space: nowrap; }'
             f'td.lt {{ font-family: {_MONO}; font-size: {small}pt; color: {MUTED}; padding: 3px 0 3px 10px; white-space: nowrap; }}'
             f'td.lx {{ font-family: {_MONO}; font-size: {size}pt; padding: 3px 0 3px 12px; white-space: nowrap; }}')
    parts = [f'<html><head><meta charset="utf-8"><style>{style}</style></head><body>']
    parts.append(_html_heading(document))
    for number, section in enumerate(document.sections):
        if section.heading:
            parts.append(f'<p class="heading" style="margin-top: {18 if number or document.title else 0}px; '
                         f'margin-bottom: 8px;">{escape(section.heading)}</p>')
        if section.kind == 'turns':
            parts.append(_html_turns(document, section))
        elif section.kind == 'lines':
            parts.append(_html_lines(section))
        else:
            parts.append(_html_table(document, section))
    parts.append('</body></html>')
    return ''.join(parts)


def _dot(color, size=None):
    return f'<span style="color: {color};{f" font-size: {size}pt;" if size else ""}">&#9679;</span>'


def _html_heading(document):
    """The kind of document, small and blue; the title, large and light; a
    rule, blue for a stretch; the details, each its label over its value;
    the speakers, each in its colour."""
    if not (document.title or document.details):
        return ''
    parts = [f'<p class="kind" style="margin: 0 0 4px 0;">{escape(document.kind)}</p>']
    if document.title:
        parts.append(f'<p class="title" style="margin: 0 0 10px 0;">{escape(document.title)}</p>')
    parts.append('<table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 10px;"><tr>'
                 f'<td width="9%" style="background-color: {ACCENT}; font-size: 1pt;">&nbsp;</td>'
                 f'<td style="background-color: {HAIRLINE}; font-size: 1pt;">&nbsp;</td></tr></table>')
    if document.details:
        parts.append('<table cellspacing="0" cellpadding="0" style="margin-bottom: 8px;"><tr>' + ''.join(
            f'<td style="padding: 0 26px 0 0;"><span class="label">{escape(label)}</span><br>'
            f'<span class="value">{escape(value)}</span></td>' for label, value in document.details) + '</tr></table>')
    if document.speakers:
        parts.append('<p style="margin: 2px 0 6px 0; line-height: 160%;">' + '&nbsp;&nbsp;&nbsp;&nbsp; '.join(
            f'{_dot(color)}&nbsp;<span class="value">{escape(name)}</span>' for name, color in document.speakers)
                     + '</p>')
    parts.append('<p style="margin: 0 0 16px 0; font-size: 1pt;">&nbsp;</p>')
    return ''.join(parts)


def _html_turns(document, section):
    """Turns as a transcript sets them, the speaker in the margin (named
    when the speaker changes) over the time, the words beside."""
    parts = ['<table width="100%" cellspacing="0" cellpadding="0">']
    last = None
    for time, speaker, text in section.turns:
        gutter = []
        if speaker and speaker != last:
            gutter.append(f'{_dot(document.colors.get(speaker, MUTED))}&nbsp;<b>{escape(speaker)}</b>')
        if time:
            gutter.append(f'<span class="time">{escape(time)}</span>')
        last = speaker
        parts.append(f'<tr><td class="gutter" width="22%">{"<br>".join(gutter)}</td>'
                     f'<td class="said"><p style="margin: 0; line-height: 150%;">{escape(text)}</p></td></tr>')
    parts.append('</table>')
    return ''.join(parts)


def _html_lines(section):
    """Numbered lines, a page each, the numbers in a margin ruled blue."""
    parts = []
    timed = any(time for page in section.pages for time, _label, _text in page)
    for number, page in enumerate(section.pages):
        parts.append('<table width="100%" cellspacing="0" cellpadding="0"'
                     + (' style="page-break-before: always;"' if number else '') + '>')
        for line, (time, label, text) in enumerate(page, start=1):
            said = (f'<b>{escape(label)}</b> ' if label else '') + escape(text)
            parts.append(f'<tr><td class="ln" width="6%">{line}</td>'
                         + (f'<td class="lt" width="14%">{escape(time)}</td>' if timed else '')
                         + f'<td class="lx">{said or "&nbsp;"}</td></tr>')
        parts.append('</table>')
    return ''.join(parts)


def _html_table(document, section):
    """A table ruled only between its rows: a header in small capitals over
    a dark rule, times in a quiet monospace, speakers by their colour, a
    reading speed too fast in red, a share as a bar."""
    parts = ['<table width="100%" cellspacing="0" cellpadding="0"><thead><tr>']
    parts += [f'<th width="{width:.0f}%">{escape(cell)}</th>' for cell, width in zip(section.header, section.widths)]
    # The rules are rows of their own: Qt rules a cell at the height of
    # what it holds, not of its row. Each is a table in its row, which Qt
    # stretches to the foot of a page it breaks.
    rule = lambda color, size: (f'<tr><td colspan="{len(section.header)}" style="padding: 0;">'
                                '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
                                f'<td style="background-color: {color}; font-size: {size}pt;">&nbsp;</td>'
                                '</tr></table></td></tr>')
    parts.append('</tr>' + rule(INK, 1.5) + '</thead>')
    for row in section.rows:
        cells = []
        for cell, kind in zip(row, section.kinds):
            text = escape(cell).replace('\n', '<br>')
            if kind in ('time', 'duration'):
                cells.append(f'<td class="cell" style="white-space: nowrap;"><span class="time">{text}</span></td>')
            elif kind == 'number':
                cells.append(f'<td class="cell"><span class="muted">{text}</span></td>')
            elif kind == 'speaker' and cell:
                cells.append(f'<td class="cell">{_dot(document.colors.get(cell, MUTED))}&nbsp;{text}</td>')
            elif kind == 'cps' and _over(cell):
                cells.append(f'<td class="cell"><span class="warning">{text}</span></td>')
            elif kind == 'share':
                share = max(0, min(100, int(cell.rstrip('%') or 0)))
                bar = (f'<td width="{max(1, share)}%" style="background-color: {ACCENT}; font-size: 1pt;">&nbsp;</td>'
                       f'<td style="background-color: {WASH}; font-size: 1pt;">&nbsp;</td>' if share < 100 else
                       f'<td style="background-color: {ACCENT}; font-size: 1pt;">&nbsp;</td>')
                cells.append(f'<td class="cell">{text}<table width="100%" cellspacing="0" cellpadding="0" '
                             f'style="margin-top: 3px;"><tr>{bar}</tr></table></td>')
            else:
                cells.append(f'<td class="cell">{text}</td>')
        parts.append('<tr>' + ''.join(cells) + '</tr>' + rule(HAIRLINE, 0.6))
    parts.append('</table>')
    return ''.join(parts)


def _over(cps):
    try:
        return float(cps) > READING_SPEED
    except ValueError:
        return False


# Word. python-docx says the page; what it has no words for (borders,
# spacing between letters, the page's number) is said in its XML.
_DOCX_SANS = 'Calibri'
_DOCX_DISPLAY = 'Calibri Light'
_DOCX_MONO = 'Consolas'


def save_docx(path, document):
    """`document` as a Word document (python-docx), on A4."""
    from docx import Document as Word
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_BREAK, WD_TAB_ALIGNMENT
    from docx.shared import Cm, Inches, Pt
    word = Word()
    section = word.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(1.8)
    section.top_margin, section.bottom_margin = Cm(1.6), Cm(1.6)
    page = section.page_width - section.left_margin - section.right_margin
    normal = word.styles['Normal']
    normal.font.name, normal.font.size = _DOCX_SANS, Pt(10.5)
    normal.font.color.rgb = _rgb(INK)
    normal.paragraph_format.space_after = Pt(0)

    def paragraph(after=0, before=0, line=None):
        made = word.add_paragraph()
        made.paragraph_format.space_after, made.paragraph_format.space_before = Pt(after), Pt(before)
        if line:
            made.paragraph_format.line_spacing = line
        return made

    if document.title or document.details:
        _run(paragraph(after=2), document.kind.upper(), 7.5, bold=True, color=ACCENT, font=_DOCX_DISPLAY,
             spacing=2)
        if document.title:
            _run(paragraph(after=6), document.title, 26, color=INK, font=_DOCX_DISPLAY)
        rule = paragraph(after=10)
        _border(rule._p.get_or_add_pPr(), 'w:pBdr', bottom=(12, ACCENT))
        if document.details:
            details = paragraph(after=6)
            for index, (label, value) in enumerate(document.details):
                if index:
                    details.add_run('      ')
                _run(details, label.upper() + '  ', 6.5, bold=True, color=MUTED, spacing=1)
                _run(details, value, 9.5)
        if document.speakers:
            legend = paragraph(after=6)
            for index, (name, color) in enumerate(document.speakers):
                _run(legend, ('      ' if index else '') + '●  ', 9, color=color)
                _run(legend, name, 9.5)
        paragraph(after=10)

    for number, part in enumerate(document.sections):
        if part.heading:
            _run(paragraph(after=6, before=14 if number or document.title else 0), part.heading.upper(), 8.5,
                 bold=True, font=_DOCX_DISPLAY, spacing=1.5)
        if part.kind == 'turns':
            table = word.add_table(rows=0, cols=2)
            table.autofit = False
            _table_borders(table)
            table.columns[0].width, table.columns[1].width = int(page * 0.22), int(page * 0.78)
            last = None
            for time, speaker, text in part.turns:
                gutter, said = table.add_row().cells
                gutter.width, said.width = int(page * 0.22), int(page * 0.78)
                first = gutter.paragraphs[0]
                if speaker and speaker != last:
                    _run(first, '●  ', 8, color=document.colors.get(speaker, MUTED))
                    _run(first, speaker, 10, bold=True)
                if time:
                    target = gutter.add_paragraph() if first.runs else first
                    _run(target, time, 8.5, color=MUTED, font=_DOCX_MONO)
                last = speaker
                said.paragraphs[0].paragraph_format.line_spacing = 1.3
                said.paragraphs[0].paragraph_format.space_after = Pt(9)
                _run(said.paragraphs[0], text, 10.5)
        elif part.kind == 'lines':
            timed = any(time for page_lines in part.pages for time, _label, _text in page_lines)
            for index, page_lines in enumerate(part.pages):
                if index:
                    breaker = paragraph()
                    breaker.add_run().add_break(WD_BREAK.PAGE)
                table = word.add_table(rows=0, cols=3 if timed else 2)
                table.autofit = False
                _table_borders(table)
                shares = (0.06, 0.15, 0.79) if timed else (0.06, 0.94)
                for column, share in zip(table.columns, shares):
                    column.width = int(page * share)
                for line, (time, label, text) in enumerate(page_lines, start=1):
                    cells = table.add_row().cells
                    for cell, share in zip(cells, shares):
                        cell.width = int(page * share)
                        cell.paragraphs[0].paragraph_format.line_spacing = Pt(20)
                    cells[0].paragraphs[0].alignment = 2
                    _run(cells[0].paragraphs[0], str(line), 9, color=MUTED, font=_DOCX_MONO)
                    _border(cells[0]._tc.get_or_add_tcPr(), 'w:tcBorders', right=(8, ACCENT))
                    if timed:
                        _run(cells[1].paragraphs[0], time, 9, color=MUTED, font=_DOCX_MONO)
                    if label:
                        _run(cells[-1].paragraphs[0], label + ' ', 10.5, bold=True, font=_DOCX_MONO)
                    _run(cells[-1].paragraphs[0], text, 10.5, font=_DOCX_MONO)
        else:
            table = word.add_table(rows=1, cols=len(part.header))
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            _table_borders(table, rows=True)
            _repeat(table.rows[0])
            for cell, text in zip(table.rows[0].cells, part.header):
                _run(cell.paragraphs[0], text.upper(), 7, bold=True, color=MUTED, spacing=0.5)
                _border(cell._tc.get_or_add_tcPr(), 'w:tcBorders', bottom=(12, INK))
            for row in part.rows:
                for cell, text, kind in zip(table.add_row().cells, row, part.kinds):
                    lines = text.split('\n')
                    target = cell.paragraphs[0]
                    for at, line in enumerate(lines):
                        if at:
                            target.add_run().add_break()
                        if kind in ('time', 'duration'):
                            _run(target, line, 8.5, color=MUTED, font=_DOCX_MONO)
                        elif kind == 'number':
                            _run(target, line, 9.5, color=MUTED)
                        elif kind == 'speaker' and line:
                            _run(target, '●  ', 8, color=document.colors.get(line, MUTED))
                            _run(target, line, 9.5)
                        elif kind == 'cps' and _over(line):
                            _run(target, line, 9.5, bold=True, color=WARNING)
                        else:
                            _run(target, line, 9.5)
                    target.paragraph_format.space_before = target.paragraph_format.space_after = Pt(2)
            # Word takes each cell's width, LibreOffice each column's.
            for column, width in zip(table.columns, part.widths):
                column.width = int(page * width / 100)
                for cell in column.cells:
                    cell.width = int(page * width / 100)

    footer = section.footer.paragraphs[0]
    # The footer style's own stops (centre, and right at 6.5") cleared.
    for position in (Inches(3.25), Inches(6.5)):
        footer.paragraph_format.tab_stops.add_tab_stop(position, WD_TAB_ALIGNMENT.CLEAR)
    footer.paragraph_format.tab_stops.add_tab_stop(page, WD_TAB_ALIGNMENT.RIGHT)
    _run(footer, ' · '.join(part for part in (document.title, document.kind) if part), 7.5, color=MUTED)
    footer.add_run('\t')
    _page_number(footer)
    word.save(path)


def _rgb(color):
    from docx.shared import RGBColor
    return RGBColor.from_string(color.lstrip('#').upper())


def _run(paragraph, text, size, bold=False, color=None, font=None, spacing=None):
    """A run of `text` in `paragraph`, `size` points, its letters `spacing`
    points apart."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold or None
    if color:
        run.font.color.rgb = _rgb(color)
    if font:
        run.font.name = font
    if spacing:
        element = OxmlElement('w:spacing')
        element.set(qn('w:val'), str(int(spacing * 20)))
        run._r.get_or_add_rPr().append(element)
    return run


def _border(properties, name, **sides):
    """Borders on paragraph or cell `properties`: each side (eighths of a
    point, colour), or None for none."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    borders = properties.find(qn(name))
    if borders is None:
        borders = OxmlElement(name)
        properties.append(borders)
    for side, value in sides.items():
        element = OxmlElement(f'w:{side}')
        if value is None:
            element.set(qn('w:val'), 'nil')
        else:
            element.set(qn('w:val'), 'single')
            element.set(qn('w:sz'), str(value[0]))
            element.set(qn('w:color'), value[1].lstrip('#').upper())
            element.set(qn('w:space'), '0')
        borders.append(element)


def _table_borders(table, rows=False):
    """No borders on `table`, but, with `rows`, a hairline between them."""
    hairline = (4, HAIRLINE) if rows else None
    _border(table._tbl.tblPr, 'w:tblBorders', top=None, left=None, bottom=hairline, right=None,
            insideH=hairline, insideV=None)


def _repeat(row):
    """`row` repeated at the top of each page the table runs onto."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    element = OxmlElement('w:tblHeader')
    element.set(qn('w:val'), 'true')
    row._tr.get_or_add_trPr().append(element)


def _page_number(paragraph):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    run = _run(paragraph, '1', 7.5, color=MUTED)
    paragraph._p.remove(run._r)
    field.append(run._r)
    paragraph._p.append(field)


# OpenDocument: the styles said once, in styles.xml (the page and its
# footer) and in content.xml (the paragraphs, cells and colours used).
_ODT_SANS = 'Carlito'
_ODT_MONO = 'Liberation Mono'
_ODT_MANIFEST = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
                 'manifest:version="1.3">'
                 '<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
                 'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                 '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                 '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                 '</manifest:manifest>')
_ODT_NAMESPACES = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                   'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
                   'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                   'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                   'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
                   'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.3"')
_ODT_FONTS = ('<office:font-face-decls>'
              f'<style:font-face style:name="Sans" svg:font-family="{_ODT_SANS}" style:font-family-generic="swiss"/>'
              f'<style:font-face style:name="Mono" svg:font-family="&apos;{_ODT_MONO}&apos;" '
              'style:font-family-generic="modern" style:font-pitch="fixed"/></office:font-face-decls>')
# The width the page leaves the text, in centimetres.
_ODT_WIDTH = 17.4


def _odt_styles(footer):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<office:document-styles {_ODT_NAMESPACES}>{_ODT_FONTS}<office:styles>'
            '<style:default-style style:family="paragraph"><style:paragraph-properties fo:margin-top="0cm" '
            f'fo:margin-bottom="0cm"/><style:text-properties style:font-name="Sans" fo:font-size="10.5pt" '
            f'fo:color="{INK}"/></style:default-style>'
            '<style:style style:name="Standard" style:family="paragraph"/>'
            '<style:style style:name="Footer" style:family="paragraph"><style:paragraph-properties>'
            f'<style:tab-stops><style:tab-stop style:position="{_ODT_WIDTH}cm" style:type="right"/></style:tab-stops>'
            f'</style:paragraph-properties><style:text-properties fo:font-size="7.5pt" fo:color="{MUTED}"/>'
            '</style:style></office:styles><office:automatic-styles>'
            '<style:page-layout style:name="Page"><style:page-layout-properties fo:page-width="21cm" '
            'fo:page-height="29.7cm" fo:margin-top="1.6cm" fo:margin-bottom="1.2cm" fo:margin-left="1.8cm" '
            'fo:margin-right="1.8cm"/><style:footer-style><style:header-footer-properties fo:min-height="0.5cm" '
            'fo:margin-top="0.4cm"/></style:footer-style></style:page-layout></office:automatic-styles>'
            '<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="Page">'
            f'<style:footer><text:p text:style-name="Footer">{_odt(footer)}<text:tab/>'
            '<text:page-number text:select-page="current">1</text:page-number></text:p></style:footer>'
            '</style:master-page></office:master-styles></office:document-styles>')


# The paragraph, text and cell styles content.xml uses, by name.
_ODT_PARAGRAPHS = {
    'Kind': f'<style:paragraph-properties fo:margin-bottom="0.1cm"/><style:text-properties fo:font-size="7.5pt" '
            f'fo:font-weight="bold" fo:color="{ACCENT}" fo:letter-spacing="0.07cm" fo:text-transform="uppercase"/>',
    'Title': '<style:paragraph-properties fo:margin-bottom="0.2cm"/><style:text-properties fo:font-size="26pt" '
             'fo:font-weight="300"/>',
    'Rule': f'<style:paragraph-properties fo:margin-bottom="0.35cm" fo:border-bottom="1.5pt solid {ACCENT}" '
            'fo:padding="0cm"/><style:text-properties fo:font-size="2pt"/>',
    'Details': '<style:paragraph-properties fo:margin-bottom="0.2cm"/>',
    'Gap': '<style:paragraph-properties fo:margin-bottom="0.35cm"/><style:text-properties fo:font-size="2pt"/>',
    'Heading': '<style:paragraph-properties fo:margin-top="0.5cm" fo:margin-bottom="0.2cm"/><style:text-properties '
               'fo:font-size="8.5pt" fo:font-weight="bold" fo:letter-spacing="0.05cm" fo:text-transform="uppercase"/>',
    'Said': '<style:paragraph-properties fo:line-height="130%" fo:margin-bottom="0.3cm"/>',
    'Gutter': '<style:paragraph-properties fo:margin-right="0.3cm"/>',
    'Cell': '<style:paragraph-properties fo:margin-top="0.05cm" fo:margin-bottom="0.05cm"/>'
            '<style:text-properties fo:font-size="9.5pt"/>',
    'Head': f'<style:text-properties fo:font-size="7pt" fo:font-weight="bold" fo:color="{MUTED}" '
            'fo:letter-spacing="0.03cm" fo:text-transform="uppercase"/>',
    'Line': '<style:paragraph-properties fo:line-height="0.7cm"/><style:text-properties style:font-name="Mono" '
            'fo:font-size="10.5pt"/>',
    'LineNumber': f'<style:paragraph-properties fo:line-height="0.7cm" fo:text-align="end" '
                  f'fo:margin-right="0.25cm"/><style:text-properties style:font-name="Mono" fo:font-size="9pt" '
                  f'fo:color="{MUTED}"/>',
}
_ODT_TEXTS = {
    'Bold': '<style:text-properties fo:font-weight="bold"/>',
    'Label': f'<style:text-properties fo:font-size="6.5pt" fo:font-weight="bold" fo:color="{MUTED}" '
             'fo:letter-spacing="0.03cm" fo:text-transform="uppercase"/>',
    'Value': '<style:text-properties fo:font-size="9.5pt"/>',
    'Time': f'<style:text-properties style:font-name="Mono" fo:font-size="8.5pt" fo:color="{MUTED}"/>',
    'Muted': f'<style:text-properties fo:color="{MUTED}"/>',
    'Warning': f'<style:text-properties fo:font-weight="bold" fo:color="{WARNING}"/>',
}
_ODT_CELLS = {
    'Plain': 'fo:padding="0cm"',
    'HeadCell': f'fo:padding-left="0.1cm" fo:padding-right="0.1cm" fo:padding-bottom="0.1cm" '
                f'fo:border-bottom="1pt solid {INK}"',
    'BodyCell': f'fo:padding-left="0.1cm" fo:padding-right="0.1cm" fo:padding-top="0.06cm" '
                f'fo:padding-bottom="0.06cm" fo:border-bottom="0.5pt solid {HAIRLINE}"',
    'NumberCell': f'fo:padding="0cm" fo:border-right="0.75pt solid {ACCENT}"',
    'TextCell': 'fo:padding-left="0.3cm"',
}


def save_odt(path, document):
    """`document` as an OpenDocument text: content and styles, zipped, the
    mimetype first and not compressed, as the format asks."""
    colors = {}
    tables = []

    def dot(color):
        name = colors.setdefault(color, f'Color{len(colors)}')
        return f'<text:span text:style-name="{name}">●</text:span><text:s/>'

    def p(style, inner):
        return f'<text:p text:style-name="{style}">{inner}</text:p>'

    def span(style, text):
        return f'<text:span text:style-name="{style}">{_odt(text)}</text:span>'

    def table(widths, body, page_break=False):
        number = len(tables) + 1
        tables.append((widths, page_break))
        return (f'<table:table table:name="Table{number}" table:style-name="Table{number}">'
                + ''.join(f'<table:table-column table:style-name="Table{number}.{index}"/>'
                          for index in range(len(widths))) + body + '</table:table>')

    def cell(style, inner):
        return f'<table:table-cell table:style-name="{style}" office:value-type="string">{inner}</table:table-cell>'

    body = []
    if document.title or document.details:
        body.append(p('Kind', _odt(document.kind)))
        if document.title:
            body.append(f'<text:h text:style-name="Title" text:outline-level="1">{_odt(document.title)}</text:h>')
        body.append(p('Rule', ''))
        if document.details:
            body.append(p('Details', '<text:s text:c="6"/>'.join(
                span('Label', label) + '<text:s text:c="2"/>' + span('Value', value)
                for label, value in document.details)))
        if document.speakers:
            body.append(p('Details', '<text:s text:c="6"/>'.join(
                dot(color) + span('Value', name) for name, color in document.speakers)))
        body.append(p('Gap', ''))

    for part in document.sections:
        if part.heading:
            body.append(f'<text:h text:style-name="Heading" text:outline-level="2">{_odt(part.heading)}</text:h>')
        if part.kind == 'turns':
            rows, last = [], None
            for time, speaker, text in part.turns:
                gutter = []
                if speaker and speaker != last:
                    gutter.append(p('Gutter', dot(document.colors.get(speaker, MUTED)) + span('Bold', speaker)))
                if time:
                    gutter.append(p('Gutter', span('Time', time)))
                last = speaker
                rows.append('<table:table-row>' + cell('Plain', ''.join(gutter) or p('Gutter', ''))
                            + cell('Plain', p('Said', _odt(text))) + '</table:table-row>')
            body.append(table([22, 78], ''.join(rows)))
        elif part.kind == 'lines':
            timed = any(time for page_lines in part.pages for time, _label, _text in page_lines)
            for index, page_lines in enumerate(part.pages):
                rows = []
                for line, (time, label, text) in enumerate(page_lines, start=1):
                    rows.append('<table:table-row>' + cell('NumberCell', p('LineNumber', str(line)))
                                + (cell('TextCell', p('Line', span('Time', time))) if timed else '')
                                + cell('TextCell', p('Line', (span('Bold', label + ' ') if label else '')
                                                     + _odt(text))) + '</table:table-row>')
                body.append(table([6, 15, 79] if timed else [6, 94], ''.join(rows), page_break=bool(index)))
        else:
            head = ''.join(cell('HeadCell', p('Head', _odt(text))) for text in part.header)
            rows = []
            for row in part.rows:
                cells = []
                for text, kind in zip(row, part.kinds):
                    lines = text.split('\n')
                    if kind in ('time', 'duration'):
                        inner = ''.join(p('Cell', span('Time', line)) for line in lines)
                    elif kind == 'number':
                        inner = p('Cell', span('Muted', text))
                    elif kind == 'speaker' and text:
                        inner = p('Cell', dot(document.colors.get(text, MUTED)) + _odt(text))
                    elif kind == 'cps' and _over(text):
                        inner = p('Cell', span('Warning', text))
                    else:
                        inner = ''.join(p('Cell', _odt(line)) for line in lines)
                    cells.append(cell('BodyCell', inner))
                rows.append('<table:table-row>' + ''.join(cells) + '</table:table-row>')
            body.append(table(part.widths, '<table:table-header-rows><table:table-row>' + head
                              + '</table:table-row></table:table-header-rows>' + ''.join(rows)))

    styles = []
    for name, properties in _ODT_PARAGRAPHS.items():
        styles.append(f'<style:style style:name="{name}" style:family="paragraph">{properties}</style:style>')
    for name, properties in _ODT_TEXTS.items():
        styles.append(f'<style:style style:name="{name}" style:family="text">{properties}</style:style>')
    for color, name in colors.items():
        styles.append(f'<style:style style:name="{name}" style:family="text"><style:text-properties '
                      f'fo:color="{color}" fo:font-size="8pt"/></style:style>')
    for name, properties in _ODT_CELLS.items():
        styles.append(f'<style:style style:name="{name}" style:family="table-cell">'
                      f'<style:table-cell-properties {properties}/></style:style>')
    for number, (widths, page_break) in enumerate(tables, start=1):
        styles.append(f'<style:style style:name="Table{number}" style:family="table"><style:table-properties '
                      f'style:width="{_ODT_WIDTH}cm" table:align="margins" table:border-model="collapsing"'
                      + (' fo:break-before="page"' if page_break else '') + '/></style:style>')
        styles += [f'<style:style style:name="Table{number}.{index}" style:family="table-column">'
                   f'<style:table-column-properties style:column-width="{_ODT_WIDTH * width / 100:.2f}cm"/>'
                   '</style:style>' for index, width in enumerate(widths)]
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<office:document-content {_ODT_NAMESPACES}>{_ODT_FONTS}<office:automatic-styles>'
               + ''.join(styles) + '</office:automatic-styles><office:body><office:text>'
               + ''.join(body) + '</office:text></office:body></office:document-content>')
    footer = ' · '.join(part for part in (document.title, document.kind) if part)
    with zipfile.ZipFile(path, 'w') as odt:
        odt.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.oasis.opendocument.text',
                     compress_type=zipfile.ZIP_STORED)
        odt.writestr('META-INF/manifest.xml', _ODT_MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
        odt.writestr('styles.xml', _odt_styles(footer), compress_type=zipfile.ZIP_DEFLATED)
        odt.writestr('content.xml', content, compress_type=zipfile.ZIP_DEFLATED)


def _odt(text):
    """Text for ODF: escaped, its runs of spaces and its tabs kept."""
    text = escape(text)
    return text.replace('\t', '<text:tab/>').replace('  ', ' <text:s/>')


# The PDF: Qt lays the HTML out in pages a footer shorter than A4's text
# area, and each page is painted with its footer: the title and the kind
# of document, and the page's number.
_FOOTER = 26                                                   # points
_fonts_added = False


def _add_fonts():
    """Subtitld's fonts, for a document made outside the app (the app adds
    them as it starts)."""
    global _fonts_added
    if _fonts_added:
        return
    _fonts_added = True
    from PySide6.QtGui import QFontDatabase
    if 'Montserrat' in QFontDatabase.families():
        return
    graphics = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'graphics')
    for name in sorted(os.listdir(graphics)) if os.path.isdir(graphics) else []:
        if name.endswith('.ttf') and name.startswith(('Ubuntu', 'Montserrat')):
            QFontDatabase.addApplicationFont(os.path.join(graphics, name))


def _layout(document):
    """`document` laid out on A4 pages by Qt: a QTextDocument, measured in
    points as a PDF at 72 dots an inch is, and the page."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QImage, QPageLayout, QPageSize, QTextDocument
    _add_fonts()
    layout = QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(18, 16, 18, 10),
                         QPageLayout.Millimeter)
    text = QTextDocument()
    # Laid out for a device of 72 dots an inch, a dot a point: the PDF's,
    # and the preview's, scaled.
    device = QImage(1, 1, QImage.Format_RGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    text.documentLayout().setPaintDevice(device)
    text._device = device
    text.setDocumentMargin(0)
    text.setHtml(html(document))
    rect = layout.paintRectPoints()
    text.setPageSize(QSizeF(rect.width(), rect.height() - _FOOTER))
    return text, layout


def _paint_page(painter, text, layout, document, page):
    """Page `page` of `text` with its footer, by `painter`, its origin at
    the page's text area, in points."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPen
    rect = layout.paintRectPoints()
    height = rect.height() - _FOOTER
    painter.save()
    painter.translate(0, -page * height)
    text.drawContents(painter, QRectF(0, page * height, rect.width(), height))
    painter.restore()
    top = rect.height() - _FOOTER + 10
    painter.setPen(QPen(QColor(HAIRLINE), 0.5))
    painter.drawLine(QPointF(0, top), QPointF(rect.width(), top))
    font = QFont('Ubuntu')
    font.setPointSizeF(7)
    painter.setFont(font)
    painter.setPen(QColor(MUTED))
    line = QRectF(0, top + 4, rect.width(), 12)
    painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter,
                     ' · '.join(part for part in (document.title, document.kind) if part))
    painter.drawText(line, Qt.AlignRight | Qt.AlignVCenter, f'{page + 1} / {text.pageCount()}')


def save_pdf(path, document):
    """`document` printed as a PDF on A4."""
    from PySide6.QtGui import QPainter, QPdfWriter
    text, layout = _layout(document)
    writer = QPdfWriter(path)
    writer.setResolution(72)
    writer.setPageLayout(layout)
    writer.setTitle(document.title or document.kind or '')
    writer.setCreator('Subtitld')
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.TextAntialiasing)
    for page in range(text.pageCount()):
        if page:
            writer.newPage()
        _paint_page(painter, text, layout, document, page)
    painter.end()


def page_image(document, width=600):
    """The first page of `document` as the PDF prints it: a QImage `width`
    pixels wide, white, for the dialog's preview."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    text, layout = _layout(document)
    full = layout.fullRectPoints()
    paint = layout.paintRectPoints()
    scale = width / full.width()
    image = QImage(int(width), int(full.height() * scale), QImage.Format_RGB32)
    image.setDotsPerMeterX(2835)
    image.setDotsPerMeterY(2835)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.scale(scale, scale)
    painter.translate(paint.left(), paint.top())
    _paint_page(painter, text, layout, document, 0)
    painter.end()
    return image


def save(path, fmt, content, segments, options=None, fps=None, title='', texts=None, speakers=None):
    """Write to `path` the `content` document of `segments` in `fmt` (one
    of FORMATS), with `options`; see `build`."""
    content = content_of(fmt, content)
    if content == MARKERS:
        csv_export.save(path, segments, csv_export.AUDITION_MARKERS)
        return path
    if fmt == 'CSV':
        # A table alone, its header in English: for other programs to read;
        # every row in it, in time order (a sheet sorts them by speaker).
        options, texts = dict(options or {}, title=False, by_speaker=False), None
    document = build(content, segments, options, fps, title, texts, speakers)
    if fmt == 'TXT':
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(txt(document))
    elif fmt == 'CSV':
        # With a byte order mark, so that spreadsheets read it as UTF-8.
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write(csv_text(document))
    elif fmt == 'DOCX':
        save_docx(path, document)
    elif fmt == 'ODT':
        save_odt(path, document)
    elif fmt == 'PDF':
        save_pdf(path, document)
    else:
        raise ValueError(f'unknown document format: {fmt!r}')
    if not os.path.isfile(path):
        raise RuntimeError('The document could not be written')
    return path
