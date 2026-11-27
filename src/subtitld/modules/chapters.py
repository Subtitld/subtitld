"""Marks (modules.cue_kinds) written out: as chapters, for players and
video sites, and as the marker lists editors import.

FORMATS are the files the export's Marks page writes, each of the marks of
the categories it is asked for (chapters by default for a chapter format,
every category for a marker list), with their text in the project's
language or a translation's:

- WebVTT chapters (VTTCHAPTERS): a WebVTT file a player loads as a track of
  kind "chapters", a cue a mark (WebVTT, W3C Candidate Recommendation,
  "chapter title text");
- Matroska chapters (MKVCHAPTERS): the XML mkvmerge and MKVToolNix read,
  an <EditionEntry> of <ChapterAtom>s (matroska.org, "Chapters");
- FFmpeg metadata (FFMETADATA): the ;FFMETADATA1 file whose [CHAPTER]s
  FFmpeg puts in an MP4, a MOV or an MKV (ffmpeg-formats, "Metadata");
- YouTube chapters (YOUTUBECHAPTERS): the lines a video's description
  lists, "0:00 Title", the first at 0:00 as YouTube asks;
- EDIUS and Premiere marker lists and Audacity labels (MARKERS_EDIUS,
  MARKERS_PREMIERE, MARKERS_AUDACITY), as modules.markers and
  modules.audacity write them for subtitles.

`ffmetadata()` is also what puts the chapters into an exported video
(modules.bounce).
"""

import xml.etree.ElementTree as ET

from subtitld.modules import cue_kinds, markup
from subtitld.modules.segment import text_of

FORMATS = {
    'VTTCHAPTERS': {'description': 'WebVTT chapters', 'extensions': ['vtt'], 'chapters': True},
    'MKVCHAPTERS': {'description': 'Matroska chapters', 'extensions': ['xml'], 'chapters': True},
    'FFMETADATA': {'description': 'FFmpeg metadata chapters', 'extensions': ['txt', 'ffmeta'], 'chapters': True},
    'YOUTUBECHAPTERS': {'description': 'YouTube chapters', 'extensions': ['txt'], 'chapters': True},
    'MARKERS_EDIUS': {'description': 'EDIUS marker list', 'extensions': ['csv', 'xml'], 'chapters': False},
    'MARKERS_PREMIERE': {'description': 'Adobe Premiere Pro markers', 'extensions': ['csv'], 'chapters': False},
    'MARKERS_AUDACITY': {'description': 'Audacity labels', 'extensions': ['txt'], 'chapters': False},
}


def default_categories(fmt):
    """The categories a format writes unless asked otherwise: chapters for a
    chapter format, every one for a marker list."""
    if FORMATS.get(fmt, {}).get('chapters'):
        return [cue_kinds.DEFAULT_CATEGORY]
    return list(cue_kinds.categories())


def marks_for(segments, categories=None, language=None, duration=None):
    """The marks of `segments` of `categories` (every one when None), in
    order of start, as {'start', 'end', 'text'}: the text plain, in
    `language`'s translation where it has one; a mark with no end of its
    own ends where the next of its category starts, the last at the end of
    the media (`duration`, cue_kinds.ends)."""
    ends = cue_kinds.ends(segments, duration)
    out = []
    for segment in sorted(cue_kinds.of_kind(segments, cue_kinds.MARK), key=lambda s: float(s['start'])):
        if categories is not None and (segment.get('category') or cue_kinds.DEFAULT_CATEGORY) not in categories:
            continue
        text = text_of(segment)
        if language:
            text = (segment.get('translations') or {}).get(language) or text
        out.append({'start': float(segment['start']), 'end': max(ends[id(segment)], float(segment['start'])),
                    'text': markup.plain(text).replace('\n', ' ').strip(),
                    'category': segment.get('category') or cue_kinds.DEFAULT_CATEGORY})
    return out


def _clock(seconds, separator='.'):
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{whole:02d}{separator}{millis:03d}'


def _ends(marks):
    """Each mark's end, for a format where chapters follow each other: its
    own, else where the next starts."""
    ends = []
    for number, mark in enumerate(marks):
        end = mark['end']
        if end <= mark['start'] and number + 1 < len(marks):
            end = marks[number + 1]['start']
        ends.append(max(end, mark['start']))
    return ends


def write_vtt(marks):
    lines = ['WEBVTT', '']
    for number, (mark, end) in enumerate(zip(marks, _ends(marks)), start=1):
        if end <= mark['start']:
            end = mark['start'] + 0.001
        # A cue's text cannot hold "-->" (WebVTT).
        lines += [str(number), f'{_clock(mark["start"])} --> {_clock(end)}',
                  (mark['text'] or str(number)).replace('-->', '->'), '']
    return '\n'.join(lines)


def write_matroska(marks, language='und'):
    root = ET.Element('Chapters')
    edition = ET.SubElement(root, 'EditionEntry')
    for mark, end in zip(marks, _ends(marks)):
        atom = ET.SubElement(edition, 'ChapterAtom')
        ET.SubElement(atom, 'ChapterTimeStart').text = _clock(mark['start']) + '000000'
        if end > mark['start']:
            ET.SubElement(atom, 'ChapterTimeEnd').text = _clock(end) + '000000'
        display = ET.SubElement(atom, 'ChapterDisplay')
        ET.SubElement(display, 'ChapterString').text = mark['text']
        ET.SubElement(display, 'ChapterLanguage').text = 'und'
        if language and language != 'und':
            ET.SubElement(display, 'ChapLanguageIETF').text = language
    ET.indent(root)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">\n'
            + ET.tostring(root, encoding='unicode') + '\n')


def _ffmeta_escaped(text):
    for special in ('\\', '=', ';', '#', '\n'):
        text = text.replace(special, '\\' + special)
    return text


def ffmetadata(marks, duration=None):
    """An ;FFMETADATA1 file of `marks` as chapters, in milliseconds; the
    last ends at `duration` when it has no end of its own."""
    lines = [';FFMETADATA1']
    ends = _ends(marks)
    for number, (mark, end) in enumerate(zip(marks, ends)):
        if end <= mark['start'] and number + 1 == len(marks) and duration:
            end = max(mark['start'], float(duration))
        lines += ['', '[CHAPTER]', 'TIMEBASE=1/1000', f'START={int(round(mark["start"] * 1000))}',
                  f'END={int(round(max(end, mark["start"]) * 1000))}', f'title={_ffmeta_escaped(mark["text"])}']
    return '\n'.join(lines) + '\n'


def _youtube_time(seconds):
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


def write_youtube(marks):
    """A line a chapter, "M:SS Title", the first at 0:00: YouTube starts the
    first chapter there whatever the list says."""
    lines = []
    for number, mark in enumerate(marks):
        start = 0.0 if number == 0 else mark['start']
        lines.append(f'{_youtube_time(start)} {mark["text"]}'.rstrip())
    return '\n'.join(lines) + '\n'


def youtube_problems(marks):
    """What keeps YouTube from showing these as chapters: fewer than three,
    or one shorter than ten seconds (as its help says)."""
    problems = []
    if len(marks) < 3:
        problems.append('fewer_than_three')
    starts = [0.0] + [mark['start'] for mark in marks[1:]]
    if any(later - earlier < 10 for earlier, later in zip(starts, starts[1:])):
        problems.append('shorter_than_ten_seconds')
    return problems


def save(path, fmt, segments, options=None, duration=None):
    """Write the marks of `segments` to `path` in `fmt` (FORMATS); options:
    'categories' (ids; default_categories when absent), 'language' (a
    translation's, for the text), 'fps', 'drop' (marker lists' frames)."""
    from subtitld.modules import audacity, markers
    options = dict(options or {})
    categories = options.get('categories')
    if categories is None:
        categories = default_categories(fmt)
    marks = marks_for(segments, categories, options.get('language') or None, duration)
    if fmt == 'MARKERS_EDIUS':
        markers.save(path, marks, kind='edius', fps=float(options.get('fps') or markers.DEFAULT_FPS),
                     version=4 if str(path).lower().endswith('.xml') else 3)
        return
    if fmt == 'MARKERS_PREMIERE':
        markers.save(path, marks, kind='premiere', fps=float(options.get('fps') or markers.DEFAULT_FPS),
                     drop=bool(options.get('drop')))
        return
    if fmt == 'MARKERS_AUDACITY':
        audacity.save(path, marks)
        return
    if fmt == 'VTTCHAPTERS':
        text = write_vtt(marks)
    elif fmt == 'MKVCHAPTERS':
        text = write_matroska(marks, options.get('language_tag') or 'und')
    elif fmt == 'FFMETADATA':
        text = ffmetadata(marks, duration)
    elif fmt == 'YOUTUBECHAPTERS':
        text = write_youtube(marks)
    else:
        raise ValueError(f'not a format of marks: {fmt}')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)


def preview(fmt, segments, options=None, duration=None):
    """The text `save` would write, for the export's preview; None for a
    format whose file is not text to show."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory(prefix='subtitld-marks-') as folder:
        path = os.path.join(folder, 'marks.' + FORMATS[fmt]['extensions'][0])
        save(path, fmt, segments, options, duration)
        with open(path, 'rb') as handle:
            data = handle.read()
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


# The files FFmpeg writes chapters in.
CHAPTER_EXTENSIONS = ('.mp4', '.m4v', '.mov', '.mkv', '.webm')


def embed(video, segments, duration=None, ffmpeg='ffmpeg', startupinfo=None, categories=None):
    """Put the chapter marks of `segments` (of `categories`: chapters by
    default) into the video file `video` as its chapters, copying the rest
    as it is. Nothing for a file of a kind that holds none, or no marks."""
    import os
    import shutil
    import subprocess
    import tempfile
    base, extension = os.path.splitext(str(video))
    if extension.lower() not in CHAPTER_EXTENSIONS:
        return False
    marks = marks_for(segments, categories or [cue_kinds.DEFAULT_CATEGORY], duration=duration)
    if not marks:
        return False
    folder = tempfile.mkdtemp(prefix='subtitld-chapters-')
    try:
        metadata = os.path.join(folder, 'chapters.txt')
        with open(metadata, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(ffmetadata(marks, duration))
        written = os.path.join(folder, 'with-chapters' + extension)
        run = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(video),
                              '-i', metadata, '-map', '0', '-map_metadata', '0', '-map_chapters', '1', '-c', 'copy',
                              written], capture_output=True, startupinfo=startupinfo, check=False)
        if run.returncode != 0 or not os.path.getsize(written):
            raise RuntimeError('FFmpeg could not write the chapters: '
                               + run.stderr.decode('utf-8', 'replace').strip()[-400:])
        shutil.move(written, str(video))
        return True
    finally:
        shutil.rmtree(folder, ignore_errors=True)
