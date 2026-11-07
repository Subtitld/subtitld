"""Subtitles as CSV, a table of times other tools take in. The export
dialog's Documents tab picks the format CSV and one of its types:

Audition markers (``audition_markers``)
    Adobe Audition's marker list, as its Markers panel exports and imports
    it: tab-separated, UTF-8 with a byte order mark, Windows line ends, a
    header line, then one marker for each subtitle::

        Name	Start	Duration	Time Format	Type	Description
        Hello there	0:03.550	0:01.200	decimal	Cue	ALICE

    The name is the subtitle's plain text on one line, the duration its
    length (a range marker, from its start to its end), the description its
    speaker. Times are Audition's "decimal" format: "M:SS.mmm", and
    "H:MM:SS.mmm" from an hour on. Empty subtitles are left out.
"""

from subtitld.modules import markup

AUDITION_MARKERS = 'audition_markers'
TYPES = (AUDITION_MARKERS,)

_AUDITION_HEADER = ('Name', 'Start', 'Duration', 'Time Format', 'Type', 'Description')


def audition_time(seconds):
    """Audition's decimal time for `seconds`, to the nearest millisecond:
    "M:SS.mmm", or "H:MM:SS.mmm" from an hour on."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}.{millis:03d}'
    return f'{minutes}:{secs:02d}.{millis:03d}'


def _cell(text):
    """`text` as one cell: plain, on one line, no tabs."""
    return ' '.join(markup.plain(str(text or '')).split())


def audition_markers(segments):
    """Audition's marker list for `segments`, as text (line ends '\\r\\n')."""
    rows = [_AUDITION_HEADER]
    for segment in sorted(segments or [], key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        name = _cell(segment.get('text'))
        if not name:
            continue
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        # The length in whole milliseconds, as both times are written.
        length = (round(end * 1000) - round(start * 1000)) / 1000
        rows.append((name, audition_time(start), audition_time(length), 'decimal', 'Cue',
                     _cell(segment.get('speaker'))))
    return ''.join('\t'.join(row) + '\r\n' for row in rows)


def write(segments, csv_type=AUDITION_MARKERS):
    """The CSV text of `csv_type` (one of TYPES) for `segments`."""
    if csv_type == AUDITION_MARKERS:
        return audition_markers(segments)
    raise ValueError(f'unknown CSV type: {csv_type!r}')


def save(path, segments, csv_type=AUDITION_MARKERS):
    """Write `segments` to `path` as CSV of `csv_type`."""
    # Audition reads its own exports: UTF-8 with a byte order mark.
    with open(path, 'w', encoding='utf-8-sig', newline='') as csv_file:
        csv_file.write(write(segments, csv_type))
