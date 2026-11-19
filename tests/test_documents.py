"""Documents to read (modules.documents): what each holds (transcript,
line-numbered transcript, dialogue list, spotting list, translation sheet,
dubbing script, speaker list), each format written and read
back (TXT, CSV, Word, OpenDocument, PDF), through file_io's save thread,
and the Documents tab's choices and preview.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import csv, io, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import documents, file_io, session
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:90]!r}' + ('' if ok else f'   (want {str(want)[:90]!r})'))
    if not ok:
        fails.append(name)


segments = [
    {'start': 4.0, 'end': 5.5, 'text': 'How are you?\nFine.', 'speaker': 'Ana', 'translations': {'pt-br': 'Como vai?'}},
    {'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>.', 'speaker': 'Ana',
     'translations': {'pt-br': 'Olá.', 'es': 'Hola.'}},
    {'start': 2.6, 'end': 3.9, 'text': 'Hi.', 'speaker': 'Ana'},
    {'start': 9.0, 'end': 10.0, 'text': 'Me & you', 'speaker': 'Bo'},
    {'start': 11.0, 'end': 12.0, 'text': ' <i> </i> '},
]

print('times')
check('milliseconds; frames at the video\'s rate', (documents.timestamp(3725.5), documents.timestamp(3725.5, 'frames', 25),
                                                   documents.timestamp(1.5, 'frames', 30000 / 1001)),
      ('01:02:05.500', '01:02:05:12', '00:00:01:14'))

print('what each holds')
transcript = documents.build('transcript', segments, title='Film')
check('transcript: a turn a speaker, a new one after a pause, in order, the empty left out',
      transcript.sections[0].turns, [('00:00:01.000', 'Ana', 'Hello there. Hi. How are you? Fine.'),
                                     ('00:00:09.000', 'Bo', 'Me & you')])
check('its heading: the title, the kind, the details, the speakers in colour', (
    transcript.title, transcript.kind, [label for label, _value in transcript.details],
    transcript.speakers), ('Film', 'Transcript', ['Running time', 'Subtitles', 'Speakers', 'Words', 'Date'],
                           [('Ana', '#1a73a8'), ('Bo', '#c5653a')]))
check('the project\'s colour for a speaker', documents.build('transcript', segments,
                                                              speakers={'Bo': {'color': '#123456'}}).colors['Bo'],
      '#123456')
check('no times, no speakers, no title: the words alone, a paragraph after a pause',
      [(d.title, d.details, d.sections[0].turns) for d in [documents.build(
          'transcript', segments, {'times': 'none', 'speakers': False, 'title': False}, title='Film')]][0],
      ('', [], [('', '', 'Hello there. Hi. How are you? Fine.'), ('', '', 'Me & you')]))
script = documents.build('script', segments, {'times': 'start_end'}).sections[0]
check('dialogue list: start, end, speaker, text, its lines kept', (script.header, script.rows[2]),
      (['Start', 'End', 'Speaker', 'Text'], ['00:00:04.000', '00:00:05.500', 'Ana', 'How are you?\nFine.']))
check('and a column for notes', documents.build('script', segments, {'notes': True}).sections[0].header,
      ['Time', 'Speaker', 'Text', 'Notes'])
spotting = documents.build('spotting', segments + [{'start': 20.0, 'end': 20.5, 'text': 'Far too many words, here.'}],
                           {'time_format': 'frames'}, fps=25).sections[0]
check('spotting list: number, in, out, duration, text, reading speed', (spotting.header, spotting.rows[0],
                                                                         spotting.rows[-1][-1]),
      (['#', 'Start', 'End', 'Duration', 'Text', 'Chars/s'],
       ['1', '00:00:01:00', '00:00:02:12', '01:12', 'Hello there.', '8.0'], '50.0'))
sheet = documents.build('translation', segments).sections[0]
check('translation sheet: a column for each language, empty where none', (sheet.header, sheet.rows[0], sheet.rows[1]),
      (['Time', 'Text', 'pt-br', 'es'], ['00:00:01.000', 'Hello there.', 'Olá.', 'Hola.'],
       ['00:00:02.600', 'Hi.', '', '']))
check('or one language', documents.build('translation', segments, {'languages': ['es']}).sections[0].header,
      ['Time', 'Text', 'es'])
dubbing = documents.build('dubbing', segments, {'by_speaker': True})
check('dubbing script: the characters, then number, character, in, out, duration, text, characters; by character',
      ([s.heading for s in dubbing.sections], dubbing.sections[0].rows[0], dubbing.sections[1].rows[0]),
      (['Characters', 'Ana', 'Bo'], ['Ana', '3', '7', '0:04', '00:00:01.000'],
       ['1', 'Ana', '00:00:01.000', '00:00:02.500', '1.50s', 'Hello there.', '12']))
check('the columns share the page, the text the most', (round(sum(dubbing.sections[1].widths)) <= 100,
                                                        max(dubbing.sections[1].widths) == dubbing.sections[1].widths[5]),
      (True, True))
listed_speakers = documents.build('speakers', segments + [{'start': 13.0, 'end': 14.0, 'text': 'Who?'}]).sections[0]
check('speaker list: who speaks longest first, the share of the speaking, someone with no name',
      (listed_speakers.header, listed_speakers.rows),
      (['Speaker', 'Subtitles', 'Words', 'Speaking', 'Share', 'First', 'Last'],
       [['Ana', '3', '7', '0:04', '68%', '00:00:01.000', '00:00:05.500'],
        ['Bo', '1', '3', '0:01', '16%', '00:00:09.000', '00:00:10.000'],
        ['No speaker', '1', '1', '0:01', '16%', '00:00:13.000', '00:00:14.000']]))
numbered = documents.build('numbered', segments, {'pauses': True, 'times': 'none'}).sections[0]
check('line-numbered: a turn opens on its speaker in capitals; pauses timed, (Jefferson)', numbered.pages,
      [[('', 'ANA:', 'Hello there. (0.1) Hi. (0.1) How are you? Fine.'.replace(' (0.1)', '')),
        ('', '', '(3.5)'), ('', 'BO:', 'Me & you')]])
long_talk = [{'start': n * 3.0, 'end': n * 3.0 + 2.5, 'speaker': 'Q' if n % 2 else 'A',
              'text': 'A rather long answer that goes on and on, more than a line can hold. ' * 2} for n in range(20)]
pages = documents.build('numbered', long_talk).sections[0].pages
check('25 lines a page, of at most 60 characters, the time on a turn\'s first', (
    [len(page) for page in pages], max(len(label) + 1 + len(text) for page in pages for _t, label, text in page),
    pages[0][0][0], pages[0][1][0]), ([25, 25, 10], 57, '00:00:00.000', ''))
check('a format holds what it can', (documents.content_of('TXT', 'dubbing'), documents.content_of('CSV', 'markers')),
      ('transcript', 'markers'))

print('each format')
folder = Path(tempfile.mkdtemp())
check('TXT', documents.txt(documents.build('script', segments, title='Film')).splitlines()[2:],
      ['', 'Time          Speaker   Text', '----------------------------', '00:00:01.000  Ana:      Hello there.',
       '00:00:02.600  Ana:      Hi.', '00:00:04.000  Ana:      How are you? / Fine.', '00:00:09.000  Bo:       Me & you'])
check('TXT: its title, then its kind and details', documents.txt(documents.build('script', segments, title='Film'))
      .splitlines()[:2][1].split(' · ')[:3], ['Dialogue list', 'Running time 0:10', 'Subtitles 4'])
check('TXT: a transcript\'s paragraphs', documents.txt(documents.build('transcript', segments, {'title': False}))
      .splitlines(), ['[00:00:01.000] Ana: Hello there. Hi. How are you? Fine.', '', '[00:00:09.000] Bo: Me & you'])
check('TXT: numbered lines, a page heading each page', [line for line in documents.txt(
    documents.build('numbered', long_talk, {'title': False})).splitlines() if line.startswith(('Page', ' 1 '))][:3],
      ['Page 1', ' 1  00:00:00.000  A: A rather long answer that goes on and on, more than a', 'Page 2'])
documents.save(str(folder / 'sheet.csv'), 'CSV', 'translation', segments, texts={'column_text': 'Texto'})
rows = list(csv.reader(io.StringIO((folder / 'sheet.csv').read_text('utf-8-sig'))))
check('CSV: with a byte order mark, the header in English, a row each', (
    (folder / 'sheet.csv').read_bytes()[:3], rows[0], len(rows)), (b'\xef\xbb\xbf', ['Time', 'Text', 'pt-br', 'es'], 5))
documents.save(str(folder / 'dubbing.csv'), 'CSV', 'dubbing', segments)
check('CSV: a dubbing script, its table alone', next(csv.reader(io.StringIO(
    (folder / 'dubbing.csv').read_text('utf-8-sig')))), ['#', 'Character', 'Start', 'End', 'Duration', 'Text', 'Characters'])
documents.save(str(folder / 'grouped.csv'), 'CSV', 'dubbing', segments, {'by_speaker': True})
check('CSV: a dubbing script grouped by speaker, every line still, in time order', [row[0] for row in csv.reader(
    io.StringIO((folder / 'grouped.csv').read_text('utf-8-sig')))][1:], ['1', '2', '3', '4'])

from docx import Document as Word
documents.save(str(folder / 'script.docx'), 'DOCX', 'script', segments, title='Film',
               texts={'column_speaker': 'Locutor'})
word = Word(str(folder / 'script.docx'))
table = word.tables[0]
check('Word: the kind, the title, a table, its header as given', (word.paragraphs[0].text, word.paragraphs[1].text,
                                                                   len(table.rows),
                                                                   [cell.text for cell in table.rows[0].cells]),
      ('DIALOGUE LIST', 'Film', 5, ['TIME', 'LOCUTOR', 'TEXT']))
check('Word: the page\'s number in the footer', 'PAGE' in word.sections[0].footer._element.xml, True)
documents.save(str(folder / 'transcript.docx'), 'DOCX', 'transcript', segments, title='Film')
check('Word: the transcript\'s turns, the speaker beside the words',
      [[cell.text for cell in row.cells] for row in Word(str(folder / 'transcript.docx')).tables[0].rows],
      [['●  Ana\n00:00:01.000', 'Hello there. Hi. How are you? Fine.'], ['●  Bo\n00:00:09.000', 'Me & you']])
documents.save(str(folder / 'numbered.docx'), 'DOCX', 'numbered', long_talk)
check('Word: a table a page of numbered lines', [len(t.rows) for t in Word(str(folder / 'numbered.docx')).tables],
      [25, 25, 10])

documents.save(str(folder / 'dubbing.odt'), 'ODT', 'dubbing', segments, {'by_speaker': True}, title='Film & co')
with zipfile.ZipFile(folder / 'dubbing.odt') as odt:
    names = odt.namelist()
    first = odt.infolist()[0]
    from lxml import etree
    content = etree.fromstring(odt.read('content.xml'))
    styles = etree.fromstring(odt.read('styles.xml'))
ns = {'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0', 'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
check('OpenDocument: the mimetype first and stored, the parts well formed, the characters and a table each',
      (first.filename, first.compress_type, 'META-INF/manifest.xml' in names,
       len(content.findall('.//table:table', ns)), content.find('.//text:h', ns).text,
       styles.find('.//text:page-number', ns) is not None),
      ('mimetype', zipfile.ZIP_STORED, True, 3, 'Film & co', True))
for content_kind in documents.contents_of('ODT'):
    documents.save(str(folder / f'{content_kind}.odt'), 'ODT', content_kind, segments, {'notes': True, 'pauses': True})
    with zipfile.ZipFile(folder / f'{content_kind}.odt') as odt:
        etree.fromstring(odt.read('content.xml'))

documents.save(str(folder / 'sheet.pdf'), 'PDF', 'translation', segments, title='Film')
pdf = (folder / 'sheet.pdf').read_bytes()
check('PDF: one A4 page', (pdf[:5], pdf.count(b'/Type /Page\n') + pdf.count(b'/Type /Page '), b'595' in pdf),
      (b'%PDF-', 1, True))
if shutil.which('pdftotext'):
    said = subprocess.run(['pdftotext', str(folder / 'sheet.pdf'), '-'], capture_output=True, text=True).stdout
    check('PDF: its text, and its footer', all(word in said for word in (
        'Film', 'Hello there.', 'Olá.', 'Hola.', '00:00:09.000', 'Film · Translation sheet')), True)
documents.save(str(folder / 'numbered.pdf'), 'PDF', 'numbered', long_talk, title='Film')
if shutil.which('pdfinfo'):
    info = subprocess.run(['pdfinfo', str(folder / 'numbered.pdf')], capture_output=True, text=True).stdout
    check('PDF: a page each 25 lines', 'Pages:           3' in info, True)
for content_kind in documents.contents_of('PDF'):
    documents.save(str(folder / f'{content_kind}.pdf'), 'PDF', content_kind, segments)
image = documents.page_image(documents.build('script', segments, title='Film'), 400)
check('the first page, for the preview: A4 in shape, something on it', (image.width(), image.height(),
                                                                       image.pixelColor(10, 10).name()), (400, 566, '#ffffff'))

print('in a dark theme')
from PySide6.QtGui import QPalette, QColor
palette = app.palette()
saved_palette = QPalette(palette)
light = documents.page_image(documents.build('translation', segments, title='Film'), 400)
palette.setColor(QPalette.Text, QColor('#e9eef2'))
palette.setColor(QPalette.WindowText, QColor('#e9eef2'))
app.setPalette(palette)
page = documents.page_image(documents.build('translation', segments, title='Film'), 400)
check('the page the same, whatever the application\'s palette', page == light, True)
app.setPalette(saved_palette)

print('through file_io')
session.SUBTITLE = Subtitle({'segments': segments, 'language': 'en-us', 'metadata': {}})
session.VIDEO = {'filepath': '/films/My film.mp4', 'framerate': 25.0}
session.FORMAT = {'options': {'document': {'content': 'script', 'time_format': 'frames', 'times': 'start'}}}
file_io.save_file(str(folder / 'saved.txt'), 'TXT')
check('TXT: titled after the video, in frames', [(lines[0], lines[5]) for lines in [
    (folder / 'saved.txt').read_text().splitlines()]][0], ('My film', '00:00:01:00  Ana:      Hello there.'))
result = []
file_io.save_file_async(str(folder / 'saved.pdf'), 'PDF', on_done=lambda path, ok, error: result.append((ok, error)))
started = time.time()
while not result and time.time() - started < 30:
    app.processEvents()
    time.sleep(0.02)
check('PDF, written by the save thread', (result, (folder / 'saved.pdf').read_bytes()[:5]), ([(True, '')], b'%PDF-'))
for fmt in documents.FORMATS:
    check(f'the file picker knows {fmt}', session.LIST_OF_SUPPORTED_EXPORT_EXTENSIONS[fmt]['extensions'],
          [documents.FORMATS[fmt]['extension']])

print('the dialog')
from subtitld.interface import export_dialog
session.CONFIG.pop('export_documents', None)
session.CONFIG.setdefault('selected_language', 'en')
panel = export_dialog._DocumentsPanel()
contents = panel.content_combo.combobox


def listed(combobox):
    return [combobox.itemData(index) for index in range(combobox.count())]


check('the formats, CSV first', listed(panel.format_combo.combobox), ['CSV', 'TXT', 'DOCX', 'ODT', 'PDF'])
panel.choose('TXT')
check('TXT: what it can hold', listed(contents), ['transcript', 'numbered', 'script', 'spotting', 'speakers'])
panel.choose('PDF', 'numbered')
check('a line-numbered transcript: the times, the speakers, the pauses', (
    panel.times_combo.isHidden(), panel.speakers_check.isHidden(), panel.pauses_check.isHidden(),
    panel.notes_check.isHidden()), (False, False, False, True))
panel.choose('DOCX', 'spotting')
check('a spotting list: the time\'s format alone', (
    panel.times_combo.isHidden(), panel.time_format_combo.isHidden(), panel.speakers_check.isHidden()),
    (True, False, True))
panel.choose('PDF', 'translation')
check('a translation sheet: the times and the languages, no speakers', (
    panel.times_combo.isHidden(), panel.languages_combo.isHidden(), panel.speakers_check.isHidden(),
    listed(panel.languages_combo.combobox)), (False, False, True, ['', 'pt-br', 'es']))
panel.choose('PDF', 'dubbing')
check('a dubbing script: the time\'s format, by speaker, notes, no times to choose', (
    panel.times_combo.isHidden(), panel.time_format_combo.isHidden(), panel.by_speaker_check.isHidden(),
    panel.notes_check.isHidden()), (True, False, False, False))
_times = panel.times_combo.combobox
panel.choose('DOCX', 'script')
_times.setCurrentIndex(_times.findData('none'))
panel._refresh_content()
check('no times: no format for them', panel.time_format_combo.isHidden(), True)
panel.choose('CSV', 'script')
check('CSV: no title', panel.title_check.isHidden(), True)
panel.choose('CSV', 'dubbing')
check('CSV: a dubbing script in time order, not by speaker', panel.by_speaker_check.isHidden(), True)
panel.choose('CSV', 'script')
panel.choose('PDF')
check('back to PDF: the dubbing script chosen for it', panel.export_choices()[1]['content'], 'dubbing')
config = panel.get_config()
check('the export\'s options', (config['format'], config['options']['document']['content']), ('PDF', 'dubbing'))
again = export_dialog._DocumentsPanel()
check('remembered: the format, and what each held', (again.export_choices()[0], again.export_choices()[1]['content'],
                                                     again._contents.get('CSV')), ('PDF', 'dubbing', 'script'))

print('its preview')
dialog = export_dialog.ExportDialog(initial_category='documents')
dialog.show()
dialog.refresh_preview()
preview = dialog.preview_panel
check('PDF: its first page', (preview.picture.isVisible(), preview.file.isVisible()), (True, False))
dialog.documents_panel.choose('TXT', 'script')
dialog.refresh_preview()
check('TXT: its text', (preview.file.isVisible(), preview.file.toPlainText().splitlines()[0]), (True, 'My film'))
dialog.done(0)

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
