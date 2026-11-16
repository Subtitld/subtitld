"""Documents to read (modules.documents): what each holds (transcript,
script, translation sheet, dubbing script), each format written and read
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
check('transcript: a paragraph a turn, a new one after a pause, in order, the empty left out',
      transcript.paragraphs, [('[00:00:01.000] Ana', 'Hello there. Hi. How are you? Fine.'),
                              ('[00:00:09.000] Bo', 'Me & you')])
check('no times, no speakers: the words alone, a paragraph after a pause',
      documents.build('transcript', segments, {'times': 'none', 'speakers': False}).paragraphs,
      [('', 'Hello there. Hi. How are you? Fine.'), ('', 'Me & you')])
script = documents.build('script', segments, {'times': 'start_end'})
check('script: start, end, speaker, text, its lines kept', (script.header, script.rows[2]),
      (['Start', 'End', 'Speaker', 'Text'], ['00:00:04.000', '00:00:05.500', 'Ana', 'How are you?\nFine.']))
sheet = documents.build('translation', segments)
check('translation sheet: a column for each language, empty where none', (sheet.header, sheet.rows[0], sheet.rows[1]),
      (['Time', 'Text', 'pt-br', 'es'], ['00:00:01.000', 'Hello there.', 'Olá.', 'Hola.'],
       ['00:00:02.600', 'Hi.', '', '']))
check('or one language', documents.build('translation', segments, {'languages': ['es']}).header, ['Time', 'Text', 'es'])
dubbing = documents.build('dubbing', segments, {'by_speaker': True})
check('dubbing script: number, speaker, start, time it has, text, characters; by speaker',
      (dubbing.rows[0], [name for name, _rows in dubbing.groups]),
      (['1', 'Ana', '00:00:01.000', '1.5s', 'Hello there.', '12'], ['Ana', 'Bo']))
check('the columns share the page, the text the most', (sum(dubbing.widths) <= 100, max(dubbing.widths) == dubbing.widths[4]),
      (True, True))
check('a format holds what it can', (documents.content_of('TXT', 'dubbing'), documents.content_of('CSV', 'markers')),
      ('transcript', 'markers'))

print('each format')
folder = Path(tempfile.mkdtemp())
check('TXT', documents.txt(documents.build('script', segments, title='Film')).splitlines(),
      ['Film', '', '00:00:01.000  Ana:  Hello there.', '00:00:02.600  Ana:  Hi.',
       '00:00:04.000  Ana:  How are you? Fine.', '00:00:09.000  Bo:  Me & you'])
documents.save(str(folder / 'sheet.csv'), 'CSV', 'translation', segments, columns={'text': 'Texto'})
rows = list(csv.reader(io.StringIO((folder / 'sheet.csv').read_text('utf-8-sig'))))
check('CSV: with a byte order mark, the header in English, a row each', (
    (folder / 'sheet.csv').read_bytes()[:3], rows[0], len(rows)), (b'\xef\xbb\xbf', ['Time', 'Text', 'pt-br', 'es'], 5))

from docx import Document as Word
documents.save(str(folder / 'script.docx'), 'DOCX', 'script', segments, title='Film',
               columns={'speaker': 'Locutor'})
word = Word(str(folder / 'script.docx'))
table = word.tables[0]
check('Word: the title, a table, its header as given', (word.paragraphs[0].text, len(table.rows),
                                                         [cell.text for cell in table.rows[0].cells]),
      ('Film', 5, ['Time', 'Locutor', 'Text']))
documents.save(str(folder / 'transcript.docx'), 'DOCX', 'transcript', segments, title='Film')
check('Word: the transcript\'s paragraphs', [p.text for p in Word(str(folder / 'transcript.docx')).paragraphs][1:],
      ['[00:00:01.000] Ana: Hello there. Hi. How are you? Fine.', '[00:00:09.000] Bo: Me & you'])

documents.save(str(folder / 'dubbing.odt'), 'ODT', 'dubbing', segments, {'by_speaker': True}, title='Film & co')
with zipfile.ZipFile(folder / 'dubbing.odt') as odt:
    names = odt.namelist()
    first = odt.infolist()[0]
    from lxml import etree
    content = etree.fromstring(odt.read('content.xml'))
    etree.fromstring(odt.read('styles.xml'))
ns = {'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0', 'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
check('OpenDocument: the mimetype first and stored, the parts well formed, a table each speaker',
      (first.filename, first.compress_type, 'META-INF/manifest.xml' in names,
       len(content.findall('.//table:table', ns)), content.find('.//text:h', ns).text),
      ('mimetype', zipfile.ZIP_STORED, True, 2, 'Film & co'))

documents.save(str(folder / 'sheet.pdf'), 'PDF', 'translation', segments, title='Film')
pdf = (folder / 'sheet.pdf').read_bytes()
check('PDF: one A4 page', (pdf[:5], pdf.count(b'/Type /Page\n') + pdf.count(b'/Type /Page '), b'595' in pdf),
      (b'%PDF-', 1, True))
if shutil.which('pdftotext'):
    said = subprocess.run(['pdftotext', str(folder / 'sheet.pdf'), '-'], capture_output=True, text=True).stdout
    check('PDF: its text', all(word in said for word in ('Film', 'Hello there.', 'Olá.', 'Hola.', '00:00:09.000')), True)
image = documents.page_image(documents.build('script', segments, title='Film'), 400)
check('the first page, for the preview: A4 in shape, something on it', (image.width(), image.height(),
                                                                       image.pixelColor(10, 10).name()), (400, 566, '#ffffff'))

print('in a dark theme')
from PySide6.QtGui import QPalette, QColor
palette = app.palette()
saved_palette = QPalette(palette)
palette.setColor(QPalette.Text, QColor('#e9eef2'))
palette.setColor(QPalette.WindowText, QColor('#e9eef2'))
app.setPalette(palette)
page = documents.page_image(documents.build('translation', segments, title='Film'), 400)
dark = sum(1 for y in range(0, page.height(), 2) for x in range(0, page.width(), 2) if page.pixelColor(x, y).lightness() < 100)
check('the text dark on the white page, whatever the application\'s palette', dark > 50, True)
app.setPalette(saved_palette)

print('through file_io')
session.SUBTITLE = {'segments': segments, 'language': 'en-us', 'metadata': {}}
session.VIDEO = {'filepath': '/films/My film.mp4', 'framerate': 25.0}
session.FORMAT = {'options': {'document': {'content': 'script', 'time_format': 'frames', 'times': 'start'}}}
file_io.save_file(str(folder / 'saved.txt'), 'TXT')
check('TXT: titled after the video, in frames', (folder / 'saved.txt').read_text().splitlines()[:3],
      ['My film', '', '00:00:01:00  Ana:  Hello there.'])
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
check('TXT: a transcript or a script', listed(contents), ['transcript', 'script'])
panel.choose('PDF', 'translation')
check('a translation sheet: the times and the languages, no speakers', (
    panel.times_combo.isHidden(), panel.languages_combo.isHidden(), panel.speakers_check.isHidden(),
    listed(panel.languages_combo.combobox)), (False, False, True, ['', 'pt-br', 'es']))
panel.choose('PDF', 'dubbing')
check('a dubbing script: the time\'s format, by speaker, no times to choose', (
    panel.times_combo.isHidden(), panel.time_format_combo.isHidden(), panel.by_speaker_check.isHidden()),
    (True, False, False))
_times = panel.times_combo.combobox
panel.choose('DOCX', 'script')
_times.setCurrentIndex(_times.findData('none'))
panel._refresh_content()
check('no times: no format for them', panel.time_format_combo.isHidden(), True)
panel.choose('CSV', 'script')
check('CSV: no title', panel.title_check.isHidden(), True)
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
