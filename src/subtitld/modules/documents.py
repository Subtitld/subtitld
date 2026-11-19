"""Subtitles as documents people read: a transcript, a script, a sheet
for a translator, a script for a dub; as plain text, a table (CSV), a
Word or an OpenDocument text, or a PDF. The export dialog's Documents tab
picks the format and, under it, what the document holds:

Transcript (``transcript``)
    The words as running text, a paragraph for each speaker's turn, and a
    new one after a pause; each starting, if asked, with its time and its
    speaker.

Script (``script``)
    A table, a line for each subtitle: its time, its speaker, its text.

Translation sheet (``translation``)
    A table: the time, the subtitle, then its translations, one column
    for each language.

Dubbing script (``dubbing``)
    A table for the voices: each subtitle's number, speaker, start, how
    long it has, its text and how many characters that is; grouped by
    speaker, if asked.

Markers (``markers``)
    CSV only: Adobe Audition's marker list (modules.csv_export).

What each holds is built once (``build``) as a document of paragraphs or
of a table, and each format writes that. A PDF is the document laid out
in HTML and printed by Qt; the dialog's preview is its first page."""

import csv
import io
import os
import zipfile
from xml.sax.saxutils import escape

from subtitld.modules import csv_export, markup
from subtitld.modules.segment import text_of

TRANSCRIPT = 'transcript'
SCRIPT = 'script'
TRANSLATION = 'translation'
DUBBING = 'dubbing'
MARKERS = 'markers'

# The formats, as the dialog lists them, and what each can hold (the
# first is the one chosen when none is).
FORMATS = {
    'CSV': {'extension': 'csv', 'contents': (SCRIPT, TRANSLATION, MARKERS)},
    'TXT': {'extension': 'txt', 'contents': (TRANSCRIPT, SCRIPT)},
    'DOCX': {'extension': 'docx', 'contents': (TRANSCRIPT, SCRIPT, TRANSLATION, DUBBING)},
    'ODT': {'extension': 'odt', 'contents': (TRANSCRIPT, SCRIPT, TRANSLATION, DUBBING)},
    'PDF': {'extension': 'pdf', 'contents': (TRANSCRIPT, SCRIPT, TRANSLATION, DUBBING)},
}

# The times: none, where each starts, or where each starts and ends.
TIMES = ('none', 'start', 'start_end')
# How a time is written: a clock to the millisecond, or a timecode in the
# video's frames.
TIME_FORMATS = ('clock', 'frames')
# What the options are, when none are given.
DEFAULTS = {'times': 'start', 'time_format': 'clock', 'speakers': True, 'title': True,
            'languages': None, 'by_speaker': False}
# A pause this long, in seconds, starts a new paragraph of a transcript.
PARAGRAPH_PAUSE = 2.0

# The headers of the tables, by the key the dialog's texts know them by
# (export_dialog.document_column_*), and in English for CSV and when no
# texts are given.
COLUMNS = {'number': '#', 'start': 'Start', 'end': 'End', 'time': 'Time', 'speaker': 'Speaker', 'text': 'Text',
           'duration': 'Duration', 'characters': 'Characters'}


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


def _text(text):
    """A subtitle's text as it reads, its lines kept."""
    return '\n'.join(' '.join(line.split()) for line in markup.plain(str(text or '')).split('\n')).strip()


def _line(text):
    """A subtitle's text as it reads, on one line."""
    return ' '.join(_text(text).split())


class Document:
    """What a document holds, before a format writes it: a title, then
    paragraphs (each a label, maybe empty, and its text) or a table (a
    header and rows of cells)."""

    def __init__(self, title='', paragraphs=None, header=None, rows=None, groups=None, kinds=None):
        self.title = title
        # What each column of the table holds ('time', 'speaker', 'text', ...).
        self.kinds = kinds or []
        # Each column's share of the page's width, in percent.
        self.widths = None
        self.paragraphs = paragraphs
        self.header = header
        self.rows = rows
        # A table split by speaker: [(speaker, rows)], the header above each.
        self.groups = groups

    @property
    def is_table(self):
        return self.paragraphs is None

    @property
    def speaker_at(self):
        """Which column is the speaker's, if one is."""
        return self.kinds.index('speaker') if 'speaker' in self.kinds else None


# The share of the page's width a column of each kind takes, in percent;
# the texts share what is left.
_WIDTH = {'number': 5, 'time': 18, 'speaker': 14, 'duration': 12, 'characters': 16}


def build(content, segments, options=None, fps=None, title='', columns=None):
    """The Document of `content` for `segments`, with `options` (DEFAULTS
    for those not given), times in frames counted at `fps`, the tables'
    headers from `columns` (COLUMNS for those not given)."""
    document = _build(content, segments, options, fps, title, columns)
    if document.is_table:
        kinds = document.kinds
        fixed = sum(_WIDTH.get(kind, 0) for kind in kinds)
        texts = sum(1 for kind in kinds if kind not in _WIDTH) or 1
        document.widths = [_WIDTH.get(kind, max(10, (100 - fixed) / texts)) for kind in kinds]
    return document


def _build(content, segments, options, fps, title, columns):
    options = dict(DEFAULTS, **{key: value for key, value in (options or {}).items() if value is not None})
    names = dict(COLUMNS, **(columns or {}))
    title = title if options['title'] else ''
    when = lambda seconds: timestamp(seconds, options['time_format'], fps)
    times = options['times']
    ordered = sorted((s for s in segments or [] if _text(text_of(s))),
                     key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0))))

    def time_cells(segment):
        if times == 'start':
            return [when(segment['start'])]
        if times == 'start_end':
            return [when(segment['start']), when(segment['end'])]
        return []

    time_header = {'none': [], 'start': [names['time']], 'start_end': [names['start'], names['end']]}[times]
    time_kinds = ['time'] * len(time_header)

    if content == TRANSCRIPT:
        paragraphs, turn, last = [], None, None
        for segment in ordered:
            speaker = str(segment.get('speaker') or '') if options['speakers'] else ''
            new = turn is None or speaker != turn['speaker'] or \
                float(segment['start']) - float(last['end']) >= PARAGRAPH_PAUSE
            if new:
                turn = {'speaker': speaker, 'start': segment['start'], 'words': []}
                paragraphs.append(turn)
            turn['words'].append(_line(text_of(segment)))
            last = segment
        out = []
        for turn in paragraphs:
            label = ' '.join(part for part in ((f'[{when(turn["start"])}]' if times != 'none' else ''),
                                               turn['speaker']) if part)
            out.append((label, ' '.join(turn['words'])))
        return Document(title, paragraphs=out)

    if content == SCRIPT:
        header = time_header + ([names['speaker']] if options['speakers'] else []) + [names['text']]
        rows = [time_cells(s) + ([str(s.get('speaker') or '')] if options['speakers'] else []) + [_text(text_of(s))]
                for s in ordered]
        return Document(title, header=header, rows=rows,
                        kinds=time_kinds + (['speaker'] if options['speakers'] else []) + ['text'])

    if content == TRANSLATION:
        languages = options['languages'] or languages_of(ordered)
        languages = [language for language in languages if language]
        header = time_header + [names['text']] + list(languages)
        rows = [time_cells(s) + [_text(text_of(s))] +
                [_text((s.get('translations') or {}).get(language, '')) for language in languages] for s in ordered]
        return Document(title, header=header, rows=rows, kinds=time_kinds + ['text'] * (1 + len(languages)))

    if content == DUBBING:
        header = [names['number'], names['speaker'], names['start'], names['duration'], names['text'],
                  names['characters']]
        rows = []
        for number, segment in enumerate(ordered, start=1):
            text = _text(text_of(segment))
            rows.append([str(number), str(segment.get('speaker') or ''), when(segment['start']),
                         f'{max(0.0, float(segment["end"]) - float(segment["start"])):.1f}s', text,
                         str(len(text.replace('\n', ' ')))])
        groups = None
        if options['by_speaker']:
            groups = {}
            for row in rows:
                groups.setdefault(row[1], []).append(row)
            groups = list(groups.items())
        return Document(title, header=header, rows=rows, groups=groups,
                        kinds=['number', 'speaker', 'time', 'duration', 'text', 'characters'])

    raise ValueError(f'unknown document content: {content!r}')


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
    """The text of `document`: paragraphs with a blank line between them;
    a table a line a row, its cells apart by two spaces, the speaker's
    followed by a colon."""
    lines = [document.title, ''] if document.title else []
    if not document.is_table:
        for label, text in document.paragraphs:
            lines += [f'{label}: {text}' if label else text, '']
        return '\n'.join(lines).rstrip('\n') + '\n'
    for row in document.rows:
        cells = [' '.join(cell.split()) for cell in row]
        if document.speaker_at is not None and cells[document.speaker_at]:
            cells[document.speaker_at] += ':'
        lines.append('  '.join(cell for cell in cells if cell))
    return '\n'.join(lines) + '\n'


def csv_text(document):
    """`document`'s table as CSV, its header first."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow(document.header)
    for row in document.rows:
        writer.writerow([cell.replace('\n', ' ') for cell in row])
    return out.getvalue()


def html(document, size=11):
    """`document` as HTML for Qt's text layout (QTextDocument), in a type
    `size` points tall."""
    parts = [f'<html><head><meta charset="utf-8"><style>'
             # Its colour said: Qt lays out text in the application's palette,
             # which a dark theme makes light, on a white page.
             f'body, td, th, p, h1, h2 {{ color: #111111; }}'
             f'body {{ font-size: {size}pt; }} h1 {{ font-size: {size * 1.6:.0f}pt; font-weight: 600; }}'
             f'th {{ font-weight: 600; text-align: left; background: #e8e8e8; white-space: nowrap; }}'
             f'td, th {{ padding: 3px 6px; vertical-align: top; font-size: {size - 1}pt; }} p {{ margin-bottom: 8px; }}'
             f'h2 {{ font-size: {size * 1.2:.0f}pt; font-weight: 600; }}</style></head><body>']
    if document.title:
        parts.append(f'<h1>{escape(document.title)}</h1>')
    if not document.is_table:
        for label, text in document.paragraphs:
            parts.append(f'<p>{f"<b>{escape(label)}:</b> " if label else ""}{escape(text)}</p>')
    else:
        for heading, rows in (document.groups or [(None, document.rows)]):
            if heading is not None:
                parts.append(f'<h2>{escape(heading)}</h2>')
            parts.append('<table width="100%" cellspacing="0" border="1" style="border-collapse: collapse; '
                         'border-color: #b0b0b0;"><thead><tr>')
            parts += [f'<th width="{width:.0f}%">{escape(cell)}</th>'
                      for cell, width in zip(document.header, document.widths)]
            parts.append('</tr></thead>')
            # No backslash inside the f-strings: Python before 3.12 (the snap's 3.10) cannot read one.
            nowrap = ' style="white-space: nowrap;"'
            for row in rows:
                parts.append('<tr>' + ''.join(
                    f'<td{nowrap if kind == "time" else ""}>'
                    f'{escape(cell).replace(chr(10), "<br>")}</td>' for cell, kind in zip(row, document.kinds))
                             + '</tr>')
            parts.append('</table>')
    parts.append('</body></html>')
    return ''.join(parts)


def save_docx(path, document):
    """`document` as a Word document (python-docx)."""
    from docx import Document as Word
    from docx.shared import Pt
    word = Word()
    if document.title:
        word.add_heading(document.title, level=1)
    if not document.is_table:
        for label, text in document.paragraphs:
            paragraph = word.add_paragraph()
            if label:
                paragraph.add_run(label + ': ').bold = True
            paragraph.add_run(text)
    else:
        for heading, rows in (document.groups or [(None, document.rows)]):
            if heading is not None:
                word.add_heading(heading, level=2)
            table = word.add_table(rows=1, cols=len(document.header))
            table.style = 'Table Grid'
            table.autofit = False
            for cell, text in zip(table.rows[0].cells, document.header):
                cell.text = ''
                cell.paragraphs[0].add_run(text).bold = True
            for row in rows:
                for cell, text in zip(table.add_row().cells, row):
                    cell.text = text
            # Word takes each cell's width, LibreOffice each column's.
            page = word.sections[0].page_width - word.sections[0].left_margin - word.sections[0].right_margin
            for column, width in zip(table.columns, document.widths):
                column.width = int(page * width / 100)
                for cell in column.cells:
                    cell.width = int(page * width / 100)
    word.styles['Normal'].font.size = Pt(11)
    word.styles['Table Grid'].font.size = Pt(10)
    word.save(path)


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
                   'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.3"')
_ODT_STYLES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<office:document-styles {_ODT_NAMESPACES}><office:styles>'
               '<style:default-style style:family="paragraph"><style:text-properties fo:font-size="11pt"/>'
               '</style:default-style>'
               '<style:style style:name="Standard" style:family="paragraph"/>'
               '<style:style style:name="Heading_20_1" style:display-name="Heading 1" style:family="paragraph" '
               'style:default-outline-level="1"><style:paragraph-properties fo:margin-bottom="0.25cm"/>'
               '<style:text-properties fo:font-size="18pt" fo:font-weight="bold"/></style:style>'
               '<style:style style:name="Heading_20_2" style:display-name="Heading 2" style:family="paragraph" '
               'style:default-outline-level="2"><style:paragraph-properties fo:margin-top="0.4cm" '
               'fo:margin-bottom="0.2cm"/><style:text-properties fo:font-size="13pt" fo:font-weight="bold"/>'
               '</style:style>'
               '<style:style style:name="Text_20_body" style:display-name="Text body" style:family="paragraph">'
               '<style:paragraph-properties fo:margin-bottom="0.2cm"/></style:style>'
               '</office:styles></office:document-styles>')


def save_odt(path, document):
    """`document` as an OpenDocument text: content and styles, zipped, the
    mimetype first and not compressed, as the format asks."""
    body = []
    widths = document.widths or []
    if document.title:
        body.append(f'<text:h text:style-name="Heading_20_1" text:outline-level="1">{_odt(document.title)}</text:h>')
    if not document.is_table:
        for label, text in document.paragraphs:
            body.append('<text:p text:style-name="Text_20_body">'
                        + (f'<text:span text:style-name="Bold">{_odt(label)}: </text:span>' if label else '')
                        + f'{_odt(text)}</text:p>')
    else:
        for number, (heading, rows) in enumerate(document.groups or [(None, document.rows)], start=1):
            if heading is not None:
                body.append(f'<text:h text:style-name="Heading_20_2" text:outline-level="2">{_odt(heading)}</text:h>')
            body.append(f'<table:table table:name="Table{number}" table:style-name="Table">'
                        + ''.join(f'<table:table-column table:style-name="Column{index}"/>'
                                  for index in range(len(document.header)))
                        + '<table:table-header-rows><table:table-row>')
            body += [f'<table:table-cell table:style-name="Head" office:value-type="string">'
                     f'<text:p text:style-name="Small"><text:span text:style-name="Bold">{_odt(cell)}</text:span></text:p>'
                     '</table:table-cell>'
                     for cell in document.header]
            body.append('</table:table-row></table:table-header-rows>')
            for row in rows:
                body.append('<table:table-row>' + ''.join(
                    '<table:table-cell table:style-name="Cell" office:value-type="string">'
                    + ''.join(f'<text:p text:style-name="Small">{_odt(line)}</text:p>' for line in cell.split('\n'))
                    + '</table:table-cell>' for cell in row) + '</table:table-row>')
            body.append('</table:table>')
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<office:document-content {_ODT_NAMESPACES}><office:automatic-styles>'
               '<style:style style:name="Bold" style:family="text"><style:text-properties fo:font-weight="bold"/>'
               '</style:style>'
               '<style:style style:name="Table" style:family="table"><style:table-properties style:width="17cm" '
               'table:align="margins" table:border-model="collapsing"/></style:style>'
               + ''.join(f'<style:style style:name="Column{index}" style:family="table-column">'
                         f'<style:table-column-properties style:column-width="{17 * width / 100:.2f}cm"/>'
                         '</style:style>' for index, width in enumerate(widths))
               + '<style:style style:name="Small" style:family="paragraph"><style:text-properties fo:font-size="10pt"/>'
               '</style:style>'
               '<style:style style:name="Cell" style:family="table-cell"><style:table-cell-properties '
               'fo:padding="0.08cm" fo:border="0.5pt solid #b0b0b0"/></style:style>'
               '<style:style style:name="Head" style:family="table-cell"><style:table-cell-properties '
               'fo:padding="0.08cm" fo:border="0.5pt solid #b0b0b0" fo:background-color="#e8e8e8"/></style:style>'
               '</office:automatic-styles><office:body><office:text>'
               + ''.join(body) + '</office:text></office:body></office:document-content>')
    with zipfile.ZipFile(path, 'w') as odt:
        odt.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.oasis.opendocument.text',
                     compress_type=zipfile.ZIP_STORED)
        odt.writestr('META-INF/manifest.xml', _ODT_MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
        odt.writestr('styles.xml', _ODT_STYLES, compress_type=zipfile.ZIP_DEFLATED)
        odt.writestr('content.xml', content, compress_type=zipfile.ZIP_DEFLATED)


def _odt(text):
    """Text for ODF: escaped, its runs of spaces and its tabs kept."""
    text = escape(text)
    return text.replace('\t', '<text:tab/>').replace('  ', ' <text:s/>')


def _layout(document):
    """`document` laid out on A4 pages by Qt: a QTextDocument."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
    layout = QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(18, 18, 18, 18),
                         QPageLayout.Millimeter)
    text = QTextDocument()
    text.setHtml(html(document))
    # In points, as QPdfWriter's resolution is set to 72 below.
    rect = layout.paintRectPoints()
    text.setPageSize(QSizeF(rect.width(), rect.height()))
    return text, layout


def save_pdf(path, document):
    """`document` printed as a PDF on A4."""
    from PySide6.QtGui import QPdfWriter
    text, layout = _layout(document)
    writer = QPdfWriter(path)
    writer.setResolution(72)
    writer.setPageLayout(layout)
    writer.setTitle(document.title or '')
    writer.setCreator('Subtitld')
    text.print_(writer)


def page_image(document, width=600):
    """The first page of `document` as the PDF prints it: a QImage `width`
    pixels wide, white, for the dialog's preview."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    text, layout = _layout(document)
    full = layout.fullRectPoints()
    paint = layout.paintRectPoints()
    scale = width / full.width()
    image = QImage(int(width), int(full.height() * scale), QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.scale(scale, scale)
    painter.translate(paint.left(), paint.top())
    text.drawContents(painter, QRectF(0, 0, paint.width(), paint.height()))
    painter.end()
    return image


def save(path, fmt, content, segments, options=None, fps=None, title='', columns=None):
    """Write to `path` the `content` document of `segments` in `fmt` (one
    of FORMATS), with `options`; see `build`."""
    content = content_of(fmt, content)
    if content == MARKERS:
        csv_export.save(path, segments, csv_export.AUDITION_MARKERS)
        return path
    document = build(content, segments, options, fps, title, columns if fmt != 'CSV' else None)
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
