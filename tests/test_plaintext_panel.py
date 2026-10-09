"""The plain-text tab: typing reaches the timeline only when the text is
valid, problems are shown where they are, and changes made elsewhere come
back into the text without overwriting edits in progress.

The window around the tab is a stand-in (a left panel holding it and a
second tab); the timeline and the player only count their refreshes.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QStackedWidget
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
app = QApplication([])
from subtitld.modules import session, history
from subtitld.interface import left_panel
from subtitld.interface import left_panel_plaintext as ptp

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


calls = {'timeline': 0, 'list': 0, 'history': 0, 'dubs': 0}
left_panel.update = lambda window: calls.__setitem__('list', calls['list'] + 1)
history.history_append = lambda *a: calls.__setitem__('history', calls['history'] + 1)


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(420, 800)
        self.setLayout(QVBoxLayout())
        self.left_panel_navigation = QWidget()
        self.left_panel_navigation.setLayout(QVBoxLayout())
        self.left_panel_navigation.layout().addStretch()
        self.layout().addWidget(self.left_panel_navigation)
        self.left_panel_stackedwidgets = QStackedWidget()
        self.layout().addWidget(self.left_panel_stackedwidgets)
        self.timeline_widget = types.SimpleNamespace(update=lambda: calls.__setitem__('timeline', calls['timeline'] + 1))
        device = types.SimpleNamespace(sync_subtitle_dubs=lambda segments: calls.__setitem__('dubs', calls['dubs'] + 1))
        self.preview_panel_player = types.SimpleNamespace(update=lambda: None, _audio_device=device)


def segments():
    return [
        {'start': 1.0, 'end': 2.0, 'text': 'First', 'speaker': 'A'},
        {'start': 3.0, 'end': 4.5, 'text': 'Second\nline two', 'speaker': 'B'},
        {'start': 5.0, 'end': 6.0, 'text': 'Third', 'speaker': 'A'},
    ]


session.SUBTITLE = {'segments': segments(), 'selected': None, 'language': 'en-us'}
session.CONFIG = {'plaintext_panel': {'format': 'srt'}}
session.UNSAVED = False
host = Host()
other_tab = left_panel.left_panel(parent=host, tab_name='subtitles', update_callback=lambda window: None,
                                  translate_callback=lambda window: None)
ptp.load(host)
ptp.translate(host)
host.show()
panel = host.plaintext_panel
editor = panel.editor
tab = panel.parentWidget()
app.processEvents()


def show_tab(widget):
    host.left_panel_stackedwidgets.setCurrentWidget(widget)
    app.processEvents()


def settle(ms=ptp.APPLY_DELAY_MS + 150):
    QTest.qWait(ms)


def replace_line(line, text):
    """Type over a whole line, the way a user would (an undoable edit)."""
    cursor = editor.cursor_at(line, 1)
    cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.insertPlainText(text)


def reset_calls():
    for key in calls:
        calls[key] = 0


print('no title line: the format switch and the options are on the bottom line')
check('format buttons in the status line', all(panel.status.isAncestorOf(b) for b in panel.format_buttons.values()), True)
check('the options chip too', panel.status.isAncestorOf(panel.options_chip), True)

print('a tab of the left panel, after the subtitle list, showing the subtitles as SRT')
check('second tab', [host.left_panel_stackedwidgets.widget(i).property('tab_name')
                     for i in range(host.left_panel_stackedwidgets.count())], ['subtitles', 'plaintext'])
check('not shown while another tab is', (panel.isVisible(), editor.toPlainText()), (False, ''))
show_tab(tab)
check('shown', panel.isVisible(), True)
check('text', editor.toPlainText().split('\n')[:4], ['# speaker: A', '1', '00:00:01,000 --> 00:00:02,000', 'First'])
check('status', host.plaintext_panel.status.property('state'), 'ok')

print('a valid edit reaches the subtitles after a pause, as one undo step')
first = session.SUBTITLE['segments'][0]
reset_calls()
replace_line(4, 'First, edited')
check('not yet: the text is still being typed', first['text'], 'First')
check('status says checking', panel.status.property('state'), 'checking')
settle()
check('applied', first['text'], 'First, edited')
check('to the same subtitle object', session.SUBTITLE['segments'][0] is first, True)
check('one undo step', calls['history'], 1)
check('the timeline and list were refreshed', (calls['timeline'] > 0, calls['list'] > 0), (True, True))
check('the dub engine resynced', calls['dubs'], 1)
check('marked unsaved', session.UNSAVED, True)
check('status ok', panel.status.property('state'), 'ok')

print('an invalid edit is not applied, and says where the problem is')
reset_calls()
replace_line(8, '00:00:03.000 --> 00:00:04,500')
settle()
second = session.SUBTITLE['segments'][1]
check('the subtitle kept its last valid timing', second['start'], 3.0)
check('nothing applied', calls['history'], 0)
check('status error', panel.status.property('state'), 'error')
check('listed with line and column', [panel.issue_list.item(i).text() for i in range(panel.issue_list.count())],
      ["Ln 8, Col 9   Use ',' before the milliseconds"])
check('the gutter marks the line', editor.markers, {8: 'error'})
check('the span is underlined', [(s.cursor.blockNumber() + 1, s.cursor.selectedText()) for s in editor.issue_selections
                                 if s.cursor.hasSelection()], [(8, '.')])

print('a second edit while it is broken still waits')
replace_line(9, 'Second, changed')
settle()
check('the text is not applied either', second['text'], 'Second\nline two')

print('clicking the problem puts the cursor on it')
panel._issue_clicked(panel.issue_list.item(0))
check('cursor', (editor.textCursor().blockNumber() + 1, editor.textCursor().positionInBlock() + 1), (8, 9))

print('a change elsewhere does not overwrite edits that are not applied')
typed = editor.toPlainText()
session.SUBTITLE['segments'][2]['text'] = 'Third, from the timeline'
session.set_unsaved(True)
settle(250)
check('the text is kept', editor.toPlainText(), typed)
check('the panel says so', (panel.stale_bar.isVisible(), panel.status.property('state')), (True, 'warning'))

print('fixing the text does not overwrite the other change on its own')
replace_line(8, '00:00:03,000 --> 00:00:04,500')
settle()
check('not applied while the subtitles changed under it', session.SUBTITLE['segments'][2]['text'], 'Third, from the timeline')

print('Reload shows the subtitles as they are now')
panel.reload_button.click()
app.processEvents()
check('the text has the change from elsewhere', 'Third, from the timeline' in editor.toPlainText(), True)
check('and not the unapplied edit', 'Second, changed' in editor.toPlainText(), False)
check('notice gone, all clean', (panel.stale_bar.isVisible(), panel.pending, panel.status.property('state')), (False, False, 'ok'))

print('Keep mine applies the text over the other change')
replace_line(10, 'line two, mine')
replace_line(8, '00:00:03,000 -> 00:00:04,500')   # broken: no apply yet
settle()
session.SUBTITLE['segments'][0]['text'] = 'Elsewhere'
session.set_unsaved(True)
settle(250)
replace_line(8, '00:00:03,000 --> 00:00:04,500')
settle()
check('waiting for a decision', second['text'], 'Second\nline two')
panel.keep_button.click()
app.processEvents()
check('applied', (second['text'], session.SUBTITLE['segments'][0]['text']), ('Second\nline two, mine', 'First, edited'))
check('notice gone', panel.stale_bar.isVisible(), False)

print('with no edits pending, changes elsewhere come straight in')
cursor = editor.cursor_at(10, 3)
editor.setTextCursor(cursor)
session.SUBTITLE['segments'][1]['end'] = 4.75
session.set_unsaved(True)
settle(250)
check('the new end time is in the text', '00:00:03,000 --> 00:00:04,750' in editor.toPlainText(), True)
check('the cursor stays where it was', (editor.textCursor().blockNumber() + 1, editor.textCursor().positionInBlock() + 1), (10, 3))

print('undo in the editor goes back through the subtitles too')
replace_line(4, 'Typo')
settle()
check('applied', first['text'], 'Typo')
editor.undo()
settle()
check('undone', first['text'], 'First, edited')

print('selecting a subtitle elsewhere marks it, and takes the cursor and view to its text')
editor.setTextCursor(editor.cursor_at(1, 1))
editor.verticalScrollBar().setValue(0)
settle(300)     # a cursor put in the first subtitle selects it; then the timeline picks the second
session.SUBTITLE['selected'] = second
settle(300)
check('lines of the second block', editor.selected_lines, (6, 10))
check('cursor at the start of its text', (editor.textCursor().blockNumber() + 1, editor.textCursor().positionInBlock() + 1), (9, 1))
check('the line is in view', editor.viewport().rect().contains(editor.cursorRect()), True)
check('the subtitle is not re-selected from here', session.SUBTITLE['selected'] is second, True)

print('putting the cursor in a subtitle selects it, and leaves the cursor there')
editor.setTextCursor(editor.cursor_at(14, 5))
panel._select_under_cursor()
settle(300)
check('third selected', session.SUBTITLE['selected'] is session.SUBTITLE['segments'][2], True)
check('marked', editor.selected_lines, (12, 15))
check('the cursor did not jump', (editor.textCursor().blockNumber() + 1, editor.textCursor().positionInBlock() + 1), (14, 5))

print('deleting the selected subtitle in the text clears the selection')
text = editor.toPlainText()
start = text.index('# speaker: A\n3\n')
cursor = editor.textCursor()
cursor.setPosition(start - 1)
cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
editor.setTextCursor(cursor)
editor.insertPlainText('\n')
settle()
check('two left', [s['text'] for s in session.SUBTITLE['segments']], ['First, edited', 'Second\nline two, mine'])
check('selection cleared', session.SUBTITLE['selected'], None)

print('Markdown')
panel.format_buttons['md'].click()
app.processEvents()
check('remembered', session.CONFIG['plaintext_panel']['format'], 'md')
check('text, with the speaker after the timing', editor.toPlainText(),
      '[00:00:01.000 - 00:00:02.000] A\nFirst, edited\n\n[00:00:03.000 - 00:00:04.750] B\nSecond\nline two, mine\n')
session.SUBTITLE['segments'][0]['dubbing'] = [{'path': 'a.wav', 'uid': 'd1'}]
replace_line(1, '[00:00:00.500 - 00:00:02.000] C')
editor.moveCursor(QTextCursor.End)
editor.insertPlainText('> pt-br: Segunda\n\n[00:00:08.000 - 00:00:09.000] A {style=italic}\nAdded in Markdown\n')
settle()
segments_now = session.SUBTITLE['segments']
check('applied', [(s['start'], s['text'], s.get('speaker')) for s in segments_now],
      [(0.5, 'First, edited', 'C'), (3.0, 'Second\nline two, mine', 'B'), (8.0, 'Added in Markdown', 'A')])
check('the translation and the field', (segments_now[1].get('translations'), segments_now[2].get('style')),
      ({'pt-br': 'Segunda'}, 'italic'))
check('the dubs, not shown, are kept', segments_now[0]['dubbing'], [{'path': 'a.wav', 'uid': 'd1'}])
replace_line(1, 'Intro')
settle()
check('text before the first timing is an error', [i.code for i in panel.result.errors], ['text_before_first'])

print('switching format with broken edits asks first')
asked = []
panel._confirm_discard = lambda: asked.append(True) or False
panel.format_buttons['srt'].click()
app.processEvents()
check('asked, and declining keeps Markdown', (asked, panel.fmt, panel.format_buttons['md'].isChecked()), ([True], 'md', True))

print('opening another project drops edits made for the last one')
check('broken edits pending', panel.pending, True)
session.SUBTITLE['segments'] = [{'start': 0.0, 'end': 1.0, 'text': 'Another project'}]
ptp.reset(host)
app.processEvents()
check('the new project is shown', editor.toPlainText(), '[00:00:00.000 - 00:00:01.000]\nAnother project\n')
check('nothing pending, no notice', (panel.pending, panel.stale_bar.isVisible(), panel.issue_list.isVisible()), (False, False, False))
settle()
check('and nothing from the old text reached it', [s['text'] for s in session.SUBTITLE['segments']], ['Another project'])

print('JSON, in Whisper\'s layout')
panel.format_buttons['json'].click()
app.processEvents()
check('three formats, JSON chosen', (list(panel.format_buttons), panel.fmt), (['srt', 'md', 'json'], 'json'))
check('the middle button is square', panel.format_buttons['md'].property('position'), 'middle')
check('text', editor.toPlainText(), '{\n  "language": "en-us",\n  "segments": [\n    {\n      "id": 0,\n      "start": 0.0,\n'
                                     '      "end": 1.0,\n      "text": "Another project"\n    }\n  ],\n  "text": "Another project"\n}\n')
replace_line(8, '      "text": "Edited as JSON", "speaker": "A"')
settle()
check('applied', (session.SUBTITLE['segments'][0]['text'], session.SUBTITLE['segments'][0].get('speaker')), ('Edited as JSON', 'A'))
check('the whole text follows the edit', editor.document().findBlockByNumber(10).text(), '  "text": "Edited as JSON"')
editor.undo()
settle()
check('undo takes back the edit and the whole text together',
      (session.SUBTITLE['segments'][0]['text'], editor.document().findBlockByNumber(10).text()),
      ('Another project', '  "text": "Another project"'))
editor.redo()
settle()
check('and redo still works', (session.SUBTITLE['segments'][0]['text'], editor.document().findBlockByNumber(10).text()),
      ('Edited as JSON', '  "text": "Edited as JSON"'))
replace_line(7, '      "end": "soon",')
settle()
check('a wrong value is not applied', session.SUBTITLE['segments'][0]['end'], 1.0)
check('and is pointed at', [panel.issue_list.item(i).text() for i in range(panel.issue_list.count())],
      ["Ln 7, Col 14   'end' must be a number of seconds"])
replace_line(7, '      "end": 1.0,')
settle()
check('fixed', panel.status.property('state'), 'ok')
replace_line(2, '  "language": "pt-br",')
settle()
check('editing the language changes the project\'s', session.SUBTITLE['language'], 'pt-br')
panel.format_buttons['md'].click()
app.processEvents()

print('options: font, size and colour theme')
check('closed at first', panel.options.isVisible(), False)
panel.options_chip.click()
app.processEvents()
check('opened from the bottom line', panel.options.isVisible(), True)
check('the chip says so, and it is remembered', (panel.options_chip.isChecked(), session.CONFIG['plaintext_panel']['options_open'],
                                                  panel.status.property('options_open')), (True, True, True))
check('defaults', (session.CONFIG['plaintext_panel']['font_size'], session.CONFIG['plaintext_panel']['theme'],
                   editor.font().pixelSize()), (ptp.DEFAULT_SIZE, 'subtitld', ptp.DEFAULT_SIZE))
panel.size_spinbox.setValue(18)
check('size applied and remembered', (editor.font().pixelSize(), session.CONFIG['plaintext_panel']['font_size']), (18, 18))
panel.size_spinbox.setValue(99)
check('held to the range', panel.size_spinbox.value(), ptp.FONT_SIZES[1])
panel.theme_combobox.setCurrentIndex(list(ptp.THEMES).index('solarized_light'))
panel.theme_combobox.activated.emit(panel.theme_combobox.currentIndex())
check('theme remembered', session.CONFIG['plaintext_panel']['theme'], 'solarized_light')
check('the editor takes its colours', ('rgba(253, 246, 227, 255)' in editor.styleSheet(),
                                      editor.highlighter.formats['timing'].foreground().color().name()), (True, '#268bd2'))
# Another font than the current one, or nothing changes (and nothing is saved).
panel.font_combobox.setCurrentIndex(1 if panel.font_combobox.currentIndex() == 0 else 0)
check('font remembered', session.CONFIG['plaintext_panel']['font_family'], panel.font_combobox.currentFont().family())
check('changing them is not an edit', (panel.pending, panel.apply_timer.isActive()), (False, False))
session.CONFIG['plaintext_panel'].update({'font_size': 'big', 'theme': 'nope'})
check('bad saved values fall back', (ptp._config()['font_size'], ptp._config()['theme']), (ptp.DEFAULT_SIZE, 'subtitld'))
panel.apply_appearance()
panel.options_chip.click()
app.processEvents()
check('closed again', (panel.options.isVisible(), panel.options_chip.isChecked(),
                       session.CONFIG['plaintext_panel']['options_open']), (False, False, False))

print('while another tab is shown, the text catches up when it comes back')
show_tab(other_tab)
session.SUBTITLE['segments'][0]['text'] = 'Changed in the list'
session.set_unsaved(True)
settle(250)
check('not rewritten while hidden', 'Changed in the list' in editor.toPlainText(), False)
show_tab(tab)
check('rewritten on coming back', 'Changed in the list' in editor.toPlainText(), True)

print('the left panel refresh reaches the tab while it is shown')
session.SUBTITLE['segments'][0]['end'] = 1.5
ptp.update(host)
settle(250)
check('new end time', '00:00:01.500' in editor.toPlainText(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
