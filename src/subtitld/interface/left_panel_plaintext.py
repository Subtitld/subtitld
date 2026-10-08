"""The plain-text tab: the subtitles as SRT or Markdown, editable.

It is a tab of the left panel, next to the subtitle list. The text and the
subtitles stay in step both ways:

* Typing here is checked a moment after the last key (`modules.plaintext`).
  A text that checks out goes to the timeline as one undo step; one with
  errors does not — the timeline keeps the last good version, and the
  errors are marked where they are (underline, gutter, the list below).
* A change made anywhere else (the timeline, the list, undo) rewrites the
  text, unless it holds edits that are not applied yet. Those are never
  thrown away silently: the panel says the subtitles changed and offers to
  reload them or to keep the text.

The cursor and the selection follow each other too: the subtitle under the
cursor becomes the selected one, and the selected one is marked in the
gutter.
"""

import re

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QLabel,
                               QPushButton, QButtonGroup, QListWidget, QListWidgetItem, QTextEdit, QFrame,
                               QSizePolicy, QFontComboBox, QSpinBox)
from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPoint, Signal, QEvent
from PySide6.QtGui import (QFont, QColor, QPainter, QTextCharFormat, QSyntaxHighlighter, QTextCursor,
                           QTextFormat, QTextOption, QIcon, QPixmap)

from subtitld.interface import left_panel
from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import history
from subtitld.modules import plaintext

APPLY_DELAY_MS = 400
# The language codes a Markdown translation line may carry.
_LANGUAGES = frozenset(session.LANGUAGE_DICT_LIST.values())

DEFAULT_FONT = 'Ubuntu Mono'
DEFAULT_SIZE = 14
FONT_SIZES = (8, 32)

# The editor's colour themes. Every theme names every role; colours with
# an alpha are #AARRGGBB, as Qt reads them.
THEMES = {
    'subtitld': {
        'name': 'Subtitld',
        'background': '#24000000', 'text': '#d4e1eb', 'selection': '#3e5363', 'selection_text': '#ffffff',
        'timing': '#8fd0ea', 'punctuation': '#56707f', 'index': '#5f7889',
        'comment_key': '#d0b27a', 'comment_value': '#91a8b8', 'speaker': '#c9b3f0', 'translation': '#a3c4b0',
        'error': '#ff6b6b', 'warning': '#e7b85a',
        'gutter_background': '#12000000', 'gutter_text': '#4a6070', 'gutter_current': '#b8cee0',
        'selected_bar': '#b8cee0', 'current_line': '#0bffffff', 'error_line': '#1fff5050',
    },
    'monokai': {
        'name': 'Monokai',
        'background': '#272822', 'text': '#f8f8f2', 'selection': '#49483e', 'selection_text': '#f8f8f2',
        'timing': '#66d9ef', 'punctuation': '#75715e', 'index': '#ae81ff',
        'comment_key': '#fd971f', 'comment_value': '#e6db74', 'speaker': '#a6e22e', 'translation': '#c2c2b0',
        'error': '#f92672', 'warning': '#e6db74',
        'gutter_background': '#21221d', 'gutter_text': '#75715e', 'gutter_current': '#f8f8f2',
        'selected_bar': '#a6e22e', 'current_line': '#3e3d32', 'error_line': '#40f92672',
    },
    'dracula': {
        'name': 'Dracula',
        'background': '#282a36', 'text': '#f8f8f2', 'selection': '#44475a', 'selection_text': '#f8f8f2',
        'timing': '#8be9fd', 'punctuation': '#6272a4', 'index': '#bd93f9',
        'comment_key': '#ffb86c', 'comment_value': '#f1fa8c', 'speaker': '#ff79c6', 'translation': '#50fa7b',
        'error': '#ff5555', 'warning': '#f1fa8c',
        'gutter_background': '#21222c', 'gutter_text': '#6272a4', 'gutter_current': '#f8f8f2',
        'selected_bar': '#bd93f9', 'current_line': '#9944475a', 'error_line': '#33ff5555',
    },
    'nord': {
        'name': 'Nord',
        'background': '#2e3440', 'text': '#d8dee9', 'selection': '#434c5e', 'selection_text': '#eceff4',
        'timing': '#88c0d0', 'punctuation': '#4c566a', 'index': '#81a1c1',
        'comment_key': '#ebcb8b', 'comment_value': '#a3be8c', 'speaker': '#b48ead', 'translation': '#8fbcbb',
        'error': '#bf616a', 'warning': '#ebcb8b',
        'gutter_background': '#2a2f3a', 'gutter_text': '#4c566a', 'gutter_current': '#d8dee9',
        'selected_bar': '#88c0d0', 'current_line': '#3b4252', 'error_line': '#40bf616a',
    },
    'solarized_light': {
        'name': 'Solarized Light',
        'background': '#fdf6e3', 'text': '#586e75', 'selection': '#e0d9c2', 'selection_text': '#073642',
        'timing': '#268bd2', 'punctuation': '#93a1a1', 'index': '#6c71c4',
        'comment_key': '#b58900', 'comment_value': '#2aa198', 'speaker': '#d33682', 'translation': '#859900',
        'error': '#dc322f', 'warning': '#cb4b16',
        'gutter_background': '#eee8d5', 'gutter_text': '#93a1a1', 'gutter_current': '#586e75',
        'selected_bar': '#268bd2', 'current_line': '#eee8d5', 'error_line': '#26dc322f',
    },
}
DEFAULT_THEME = 'subtitld'


def _config():
    config = session.CONFIG.setdefault('plaintext_panel', {})
    if config.get('format') not in plaintext.FORMATS:
        config['format'] = plaintext.SRT
    if config.get('theme') not in THEMES:
        config['theme'] = DEFAULT_THEME
    if not isinstance(config.get('font_family'), str) or not config['font_family']:
        config['font_family'] = DEFAULT_FONT
    try:
        config['font_size'] = max(FONT_SIZES[0], min(FONT_SIZES[1], int(config.get('font_size', DEFAULT_SIZE))))
    except (TypeError, ValueError):
        config['font_size'] = DEFAULT_SIZE
    return config


def _css(color):
    """A theme colour for a stylesheet, which reads #AARRGGBB differently."""
    value = QColor(color)
    return f'rgba({value.red()}, {value.green()}, {value.blue()}, {value.alpha()})'


def _format(color):
    text_format = QTextCharFormat()
    text_format.setForeground(QColor(color))
    return text_format


def issue_text(issue):
    """An issue's message in the interface language; the checker's own
    English text when there is no translation for it."""
    key = f'plaintext_panel.issue_{issue.code}'
    text = _(key)
    if not text or text == key:
        return issue.message
    try:
        return text.format(**issue.params)
    except (KeyError, IndexError, ValueError):
        return text


# ---------------------------------------------------------------------------
# Highlighting
# ---------------------------------------------------------------------------

class Highlighter(QSyntaxHighlighter):
    """Colours each line by what it is. It reads the lines on its own, so it
    keeps up with every key; the checker's errors are drawn on top by the
    editor (they wait for the checker).

    The block state remembers where in a subtitle the previous line was:
    for SRT, so a subtitle line that starts with '#' or is a number stays
    text; for Markdown, so a '>' line under a translation goes on with it.
    """

    _AFTER_BLANK, _AFTER_INDEX, _IN_TEXT = 0, 1, 2
    _MD_TEXT, _MD_TRANSLATION = 10, 11

    def __init__(self, document):
        super().__init__(document)
        self.fmt = plaintext.SRT
        self.primaries = plaintext._primary_tags(_LANGUAGES)
        self.set_theme(THEMES[DEFAULT_THEME], rehighlight=False)

    def set_theme(self, theme, rehighlight=True):
        self.formats = {name: _format(color) for name, color in theme.items() if name != 'name'}
        self.formats['timing'].setFontWeight(QFont.Bold)
        self.formats['comment_key'].setFontItalic(True)
        self.formats['comment_value'].setFontItalic(True)
        self.formats['translation'].setFontItalic(True)
        if rehighlight:
            self.rehighlight()

    def set_format(self, fmt):
        if fmt != self.fmt:
            self.fmt = fmt
            self.rehighlight()

    def highlightBlock(self, text):
        if self.fmt == plaintext.MD:
            self._markdown(text)
            return
        if self.fmt == plaintext.JSON:
            self._json(text)
            return
        previous = self.previousBlockState()
        if previous < 0:
            previous = self._AFTER_BLANK
        if not text.strip():
            self.setCurrentBlockState(self._AFTER_BLANK)
            return
        if previous == self._AFTER_BLANK:
            if text.lstrip().startswith('#'):
                self._comment(text)
                self.setCurrentBlockState(self._AFTER_BLANK)
                return
            if text.strip().isdigit():
                self.setFormat(0, len(text), self.formats['index'])
                self.setCurrentBlockState(self._AFTER_INDEX)
                return
        if previous in (self._AFTER_BLANK, self._AFTER_INDEX) and '-->' in text:
            self._timing(text)
            self.setCurrentBlockState(self._IN_TEXT)
            return
        self.setCurrentBlockState(self._IN_TEXT)

    _JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"(\s*:)?|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|true|false|null|[{}\[\],:]')

    def _json(self, text):
        """JSON is read a line at a time: keys, text, numbers, the rest."""
        for match in self._JSON_TOKEN.finditer(text):
            token = match.group(0)
            if token.startswith('"'):
                if match.group(1):
                    key_end = match.start(1)
                    self.setFormat(match.start(), key_end - match.start(), self.formats['comment_key'])
                    self.setFormat(key_end, match.end() - key_end, self.formats['punctuation'])
                else:
                    self.setFormat(match.start(), len(token), self.formats['text'])
            elif token in ('true', 'false', 'null'):
                self.setFormat(match.start(), len(token), self.formats['speaker'])
            elif token[0] in '-0123456789':
                self.setFormat(match.start(), len(token), self.formats['timing'])
            else:
                self.setFormat(match.start(), len(token), self.formats['punctuation'])

    def _markdown(self, text):
        previous = self.previousBlockState()
        punctuation = self.formats['punctuation']
        if plaintext._MD_TIMING_CANDIDATE.match(text):
            self._md_timing(text)
            self.setCurrentBlockState(self._MD_TEXT)
            return
        if text.startswith('>'):
            rest = 2 if text[1:2] == ' ' else 1
            tag = plaintext._MD_TAG.match(text, rest)
            if tag and plaintext._is_language(tag.group(1), self.primaries):
                self.setFormat(0, rest, punctuation)
                self.setFormat(tag.start(1), len(tag.group(1)), self.formats['comment_key'])
                self.setFormat(tag.end() - 1, 1, punctuation)
                self.setFormat(tag.end(), len(text) - tag.end(), self.formats['translation'])
                self.setCurrentBlockState(self._MD_TRANSLATION)
                return
            if previous == self._MD_TRANSLATION:
                self.setFormat(0, 1, punctuation)
                self.setFormat(1, len(text) - 1, self.formats['translation'])
                self.setCurrentBlockState(self._MD_TRANSLATION)
                return
        if text.startswith('\\'):
            self.setFormat(0, 1, punctuation)
        # A blank line does not close the translations; only a timing does.
        self.setCurrentBlockState(self._MD_TRANSLATION if previous == self._MD_TRANSLATION else self._MD_TEXT)

    def _md_timing(self, text):
        close = text.find(']')
        end = len(text) if close < 0 else close + 1
        self.setFormat(0, end, self.formats['timing'])
        for index in range(end):
            if text[index] in '[]- ':
                self.setFormat(index, 1, self.formats['punctuation'])
        if close < 0:
            return
        brace = text.find('{', close)
        speaker_end = len(text) if brace < 0 else brace
        self.setFormat(close + 1, speaker_end - close - 1, self.formats['speaker'])
        if brace < 0:
            return
        self.setFormat(brace, len(text) - brace, self.formats['comment_value'])
        self.setFormat(brace, 1, self.formats['punctuation'])
        if text.rstrip().endswith('}'):
            self.setFormat(len(text.rstrip()) - 1, 1, self.formats['punctuation'])
        for match in re.finditer(r'(?:(?<=[{\s]))(' + plaintext._KEY + r')=', text[brace:]):
            self.setFormat(brace + match.start(1), len(match.group(1)), self.formats['comment_key'])
            self.setFormat(brace + match.end(1), 1, self.formats['punctuation'])

    def _timing(self, text):
        self.setFormat(0, len(text), self.formats['timing'])
        for index, char in enumerate(text):
            if char in '[]-> ':
                self.setFormat(index, 1, self.formats['punctuation'])

    def _comment(self, text):
        self.setFormat(0, len(text), self.formats['punctuation'])
        match = plaintext._COMMENT.match(text)
        if match:
            self.setFormat(match.start(2), len(match.group(2)), self.formats['comment_key'])
            self.setFormat(match.start(4), len(match.group(4)), self.formats['comment_value'])


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

class _Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setObjectName('plaintext_panel_gutter')

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        # A click on a marker jumps to its issue.
        block = self.editor.cursorForPosition(QPoint(0, int(event.position().y()))).block()
        self.editor.marker_clicked.emit(block.blockNumber() + 1)


class Editor(QPlainTextEdit):
    """A QPlainTextEdit with line numbers, problem markers in the gutter,
    and the selected subtitle marked beside its lines."""

    marker_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('plaintext_panel_editor')
        # The left panel is narrow: wrap, so a subtitle line is seen whole.
        # Line numbers count text lines, not wrapped rows.
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setFrameShape(QFrame.NoFrame)
        self.document().setDocumentMargin(6)
        self.theme = THEMES[DEFAULT_THEME]
        self.highlighter = Highlighter(self.document())
        self.gutter = _Gutter(self)
        # line -> 'error' | 'warning' (the worst on that line)
        self.markers = {}
        self.issues = []
        self.issue_selections = []
        self.selected_lines = None   # (first, last) of the selected subtitle
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self.refresh_selections)
        self.set_appearance(DEFAULT_FONT, DEFAULT_SIZE, DEFAULT_THEME)

    def set_appearance(self, family, size, theme_name):
        """Font and colour theme, as chosen in the tab's options."""
        font = QFont(family)
        font.setStyleHint(QFont.Monospace)
        font.setPixelSize(int(size))
        self.setFont(font)
        self.gutter.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        self.theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])
        theme = self.theme
        # Set on the widget, so it wins over the application stylesheet.
        self.setStyleSheet(
            'QPlainTextEdit#plaintext_panel_editor {'
            f' background-color: {_css(theme["background"])}; color: {_css(theme["text"])};'
            f' selection-background-color: {_css(theme["selection"])};'
            f' selection-color: {_css(theme["selection_text"])}; border: 0; }}')
        self.highlighter.set_theme(theme)
        self._update_gutter_width()
        self.set_issues(self.issues)

    # -- gutter --------------------------------------------------------------

    def gutter_width(self):
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 22 + self.fontMetrics().horizontalAdvance('9') * digits

    def _update_gutter_width(self, *_args):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.gutter.setGeometry(QRect(contents.left(), contents.top(), self.gutter_width(), contents.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(self.theme['gutter_background']))
        painter.setFont(self.font())
        width = self.gutter.width()
        line_height = self.fontMetrics().height()
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                line = block.blockNumber() + 1
                if self.selected_lines and self.selected_lines[0] <= line <= self.selected_lines[1]:
                    painter.fillRect(QRect(0, top, 3, bottom - top), QColor(self.theme['selected_bar']))
                severity = self.markers.get(line)
                if severity:
                    painter.setRenderHint(QPainter.Antialiasing)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(self.theme[severity]))
                    painter.drawEllipse(QPoint(10, top + line_height // 2), 3, 3)
                painter.setPen(QColor(self.theme['gutter_current'] if block.blockNumber() == current else self.theme['gutter_text']))
                painter.drawText(0, top, width - 8, line_height, Qt.AlignRight | Qt.AlignVCenter, str(line))
            block = block.next()
            top = bottom

    # -- marks ---------------------------------------------------------------

    def cursor_at(self, line, column, length=0):
        """A cursor over `length` characters from 1-based line/column."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        start = min(max(0, column - 1), max(0, block.length() - 1))
        cursor.setPosition(block.position() + start)
        if length:
            end = min(block.position() + start + length, block.position() + max(0, block.length() - 1))
            cursor.setPosition(max(end, block.position() + start), QTextCursor.KeepAnchor)
        return cursor

    def set_issues(self, issues):
        self.issues = list(issues)
        self.markers = {}
        self.issue_selections = []
        for issue in issues:
            if self.markers.get(issue.line) != plaintext.ERROR:
                self.markers[issue.line] = issue.severity
            color = QColor(self.theme['error' if issue.severity == plaintext.ERROR else 'warning'])
            mark = QTextEdit.ExtraSelection()
            mark.cursor = self.cursor_at(issue.line, issue.column, issue.length)
            if not mark.cursor.hasSelection():
                # An issue at the end of a line (something missing): mark
                # the last character instead, so there is something to see.
                mark.cursor = self.cursor_at(issue.line, max(1, issue.column - 1), 1)
            mark.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            mark.format.setUnderlineColor(color)
            self.issue_selections.append(mark)
            if issue.severity == plaintext.ERROR:
                line_mark = QTextEdit.ExtraSelection()
                line_mark.cursor = self.cursor_at(issue.line, 1)
                line_mark.format.setBackground(QColor(self.theme['error_line']))
                line_mark.format.setProperty(QTextFormat.FullWidthSelection, True)
                self.issue_selections.insert(0, line_mark)
        self.refresh_selections()
        self.gutter.update()

    def set_selected_lines(self, lines):
        if lines != self.selected_lines:
            self.selected_lines = lines
            self.gutter.update()

    def refresh_selections(self):
        current = QTextEdit.ExtraSelection()
        current.cursor = self.textCursor()
        current.cursor.clearSelection()
        current.format.setBackground(QColor(self.theme['current_line']))
        current.format.setProperty(QTextFormat.FullWidthSelection, True)
        self.setExtraSelections([current] + self.issue_selections)
        self.gutter.update()


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------

class PlainTextPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.setObjectName('plaintext_panel')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        self.fmt = _config()['format']
        self.model_text = None      # the text the subtitles were last written as
        self.pending = False        # edits here that the subtitles do not have
        self.stale = False          # the subtitles changed under pending edits
        self.result = None          # the last check of the text
        self.cue_segments = []      # the subtitle of each checked cue
        self._applying = False
        self._setting_text = False

        format_switch = QWidget(objectName='plaintext_panel_format_switch')
        format_switch.setLayout(QHBoxLayout())
        format_switch.layout().setContentsMargins(0, 0, 0, 0)
        format_switch.layout().setSpacing(0)
        self.format_group = QButtonGroup(self)
        self.format_buttons = {}
        for position, fmt in enumerate(plaintext.FORMATS):
            button = QPushButton(fmt.upper(), objectName='plaintext_panel_format_button')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty('position', 'first' if position == 0 else
                               'last' if position == len(plaintext.FORMATS) - 1 else 'middle')
            button.setChecked(fmt == self.fmt)
            button.clicked.connect(lambda _checked=False, f=fmt: self.set_format(f))
            self.format_group.addButton(button)
            self.format_buttons[fmt] = button
            format_switch.layout().addWidget(button)

        self.stale_bar = QWidget(objectName='plaintext_panel_stale_bar')
        self.stale_bar.setLayout(QHBoxLayout())
        self.stale_bar.layout().setContentsMargins(15, 6, 10, 6)
        self.stale_bar.layout().setSpacing(6)
        self.stale_label = QLabel(objectName='plaintext_panel_stale_label')
        self.stale_label.setWordWrap(True)
        self.stale_bar.layout().addWidget(self.stale_label, 1)
        self.reload_button = QPushButton(objectName='plaintext_panel_stale_button')
        self.reload_button.setCursor(Qt.PointingHandCursor)
        self.reload_button.clicked.connect(self.reload_from_subtitles)
        self.stale_bar.layout().addWidget(self.reload_button)
        self.keep_button = QPushButton(objectName='plaintext_panel_stale_button')
        self.keep_button.setCursor(Qt.PointingHandCursor)
        self.keep_button.clicked.connect(self.keep_text)
        self.stale_bar.layout().addWidget(self.keep_button)
        self.stale_bar.hide()
        self.layout().addWidget(self.stale_bar)

        self.editor = Editor()
        self.editor.textChanged.connect(self._text_changed)
        self.editor.cursorPositionChanged.connect(self._cursor_moved)
        self.editor.marker_clicked.connect(self._marker_clicked)
        self.editor.highlighter.set_format(self.fmt)
        self.layout().addWidget(self.editor, 1)

        footer = QWidget(objectName='plaintext_panel_footer')
        footer.setLayout(QVBoxLayout())
        footer.layout().setContentsMargins(0, 0, 0, 0)
        footer.layout().setSpacing(0)
        footer.layout().addWidget(self._options_strip())
        status = QWidget(objectName='plaintext_panel_status')
        status.setLayout(QHBoxLayout())
        status.layout().setContentsMargins(15, 0, 10, 0)
        status.layout().setSpacing(8)
        self.status_icon = QLabel(objectName='plaintext_panel_status_icon')
        self.status_icon.setFixedSize(8, 8)
        status.layout().addWidget(self.status_icon, 0, Qt.AlignVCenter)
        self.status_label = _ElidedLabel(objectName='plaintext_panel_status_label')
        status.layout().addWidget(self.status_label, 1)
        self.position_label = QLabel(objectName='plaintext_panel_position_label')
        status.layout().addWidget(self.position_label)
        status.layout().addWidget(format_switch, 0, Qt.AlignVCenter)
        self.format_switch = format_switch
        # The options chip, as the scope chip over "Start transcription": a
        # tab in the strip's colour, hanging from the top of this line, so
        # the open strip above and the chip read as one shape.
        self.options_chip = QPushButton(objectName='plaintext_panel_options_chip')
        self.options_chip.setCursor(Qt.PointingHandCursor)
        self.options_chip.setFixedHeight(24)   # the width is in the stylesheet
        self.options_chip.setCheckable(True)
        self.options_chip.setIcon(_options_icon())
        self.options_chip.setIconSize(QSize(16, 16))
        self.options_chip.clicked.connect(lambda: self.set_options_open(self.options.isHidden()))
        status.layout().addWidget(self.options_chip, 0, Qt.AlignTop)
        status.installEventFilter(self)
        self.status = status
        footer.layout().addWidget(status)
        self.issue_list = QListWidget(objectName='plaintext_panel_issue_list')
        self.issue_list.setFrameShape(QFrame.NoFrame)
        self.issue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.issue_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.issue_list.itemClicked.connect(self._issue_clicked)
        self.issue_list.hide()
        footer.layout().addWidget(self.issue_list)
        self.layout().addWidget(footer)

        self.apply_timer = QTimer(self, singleShot=True, interval=APPLY_DELAY_MS)
        self.apply_timer.timeout.connect(self.check_and_apply)
        self.refresh_timer = QTimer(self, singleShot=True, interval=120)
        self.refresh_timer.timeout.connect(self.refresh_from_subtitles)
        self.select_timer = QTimer(self, singleShot=True, interval=150)
        self.select_timer.timeout.connect(self._select_under_cursor)
        # The selection changes without any notice (a click on the timeline),
        # so it is looked at a few times a second — a cheap identity check.
        self._selected_id = None
        self.selection_poll = QTimer(self, interval=250)
        self.selection_poll.timeout.connect(self._sync_selected_mark)

        self.apply_appearance()
        self.translate()
        self._update_status()
        self.set_options_open(bool(_config().get('options_open', False)))

    # -- options -------------------------------------------------------------

    def eventFilter(self, watched, event):
        if watched is self.status and event.type() == QEvent.Resize:
            self._fit_status_line()
        return super().eventFilter(watched, event)

    def _fit_status_line(self):
        """The line holds the status, the cursor position, the format switch
        and the options chip. When it is too narrow for all of them, the
        position goes first: the status says whether the text is applied,
        and each problem still gives its line and column in the list."""
        layout = self.status.layout()
        margins = layout.contentsMargins()
        others = [self.status_icon, self.format_switch, self.options_chip]
        needed = (margins.left() + margins.right() + layout.spacing() * (len(others) + 1)
                  + sum(widget.sizeHint().width() for widget in others)
                  + self.status_label.fontMetrics().horizontalAdvance(self.status_label.text()) + 4)
        position = self.position_label.sizeHint().width() + layout.spacing()
        self.position_label.setVisible(self.status.width() >= needed + position)

    def set_options_open(self, open_):
        _config()['options_open'] = bool(open_)
        self.options.setVisible(bool(open_))
        self.status.setProperty('options_open', bool(open_))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._update_options_chip()

    def _update_options_chip(self):
        # The settings icon, dim when closed and lit when open, like the
        # scope chip's text. The tooltip names it.
        self.options_chip.setChecked(not self.options.isHidden())

    def _options_strip(self):
        """Font, size and colour theme; opened from the bottom line. The
        app's labelled comboboxes, the label inside the block."""
        self.options = QWidget(objectName='plaintext_panel_options')
        box = QVBoxLayout(self.options)
        box.setContentsMargins(15, 10, 15, 12)
        box.setSpacing(8)
        config = _config()

        # Font: the family and, in the same block, the size.
        self.font_block = utils.LabeledComboBox()
        stock = self.font_block.combobox
        self.font_block.bottom_line.removeWidget(stock)
        stock.setParent(None)
        stock.deleteLater()
        self.font_combobox = QFontComboBox(objectName='plaintext_panel_font_combobox')
        self.font_combobox.setFontFilters(QFontComboBox.MonospacedFonts)
        self.font_combobox.setCurrentFont(QFont(config['font_family']))
        self.font_combobox.currentFontChanged.connect(lambda font: self._option_changed('font_family', font.family()))
        self.font_block.combobox = self.font_combobox
        self.font_block.bottom_line.addWidget(self.font_combobox, 1)
        self.size_spinbox = QSpinBox(objectName='plaintext_panel_size_spinbox')
        self.size_spinbox.setRange(*FONT_SIZES)
        self.size_spinbox.setSuffix(' px')
        self.size_spinbox.setValue(config['font_size'])
        self.size_spinbox.valueChanged.connect(lambda size: self._option_changed('font_size', size))
        self.font_block.bottom_line.addWidget(self.size_spinbox)
        box.addWidget(self.font_block)

        self.theme_block = utils.LabeledComboBox()
        self.theme_combobox = self.theme_block.combobox
        self.theme_combobox.setObjectName('plaintext_panel_theme_combobox')
        for key, theme in THEMES.items():
            self.theme_combobox.addItem(theme['name'], key)
        self.theme_combobox.setCurrentIndex(list(THEMES).index(config['theme']))
        self.theme_combobox.activated.connect(
            lambda index: self._option_changed('theme', self.theme_combobox.itemData(index)))
        box.addWidget(self.theme_block)
        self.options.hide()
        return self.options

    def _option_changed(self, key, value):
        _config()[key] = value
        self.apply_appearance()

    def apply_appearance(self):
        config = _config()
        # Re-colouring reports the text as changed; it is not an edit.
        self._setting_text = True
        try:
            self.editor.set_appearance(config['font_family'], config['font_size'], config['theme'])
        finally:
            self._setting_text = False
        self._fill_issue_list()     # its dots take the theme's colours

    def showEvent(self, event):
        super().showEvent(event)
        self.selection_poll.start()
        self.refresh_from_subtitles()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.selection_poll.stop()

    def translate(self):
        self.font_block.setLabel(_('plaintext_panel.font').upper())
        self.size_spinbox.setToolTip(_('plaintext_panel.font_size'))
        self.theme_block.setLabel(_('plaintext_panel.theme').upper())
        self.options_chip.setToolTip(_('plaintext_panel.options'))
        self._update_options_chip()
        self.stale_label.setText(_('plaintext_panel.stale'))
        self.reload_button.setText(_('plaintext_panel.reload'))
        self.reload_button.setToolTip(_('plaintext_panel.reload_tooltip'))
        self.keep_button.setText(_('plaintext_panel.keep'))
        self.keep_button.setToolTip(_('plaintext_panel.keep_tooltip'))
        for fmt, button in self.format_buttons.items():
            button.setToolTip(_(f'plaintext_panel.format_{fmt}'))
        self._update_status()

    # -- subtitles -> text ---------------------------------------------------

    def schedule_refresh(self):
        if self.isVisible():
            self.refresh_timer.start()

    def refresh_from_subtitles(self, force=False):
        """Write the subtitles out again if they changed since this text
        was made from them — unless the text has edits of its own."""
        if self._applying:
            return
        text = self._serialize()
        if text == self.model_text and not force:
            self._sync_selected_mark()
            return
        if (self.pending or self.apply_timer.isActive()) and not force:
            if self.model_text is not None:
                self.stale = True
                self.stale_bar.show()
                self._update_status()
            return
        self._set_text(text)
        self.model_text = text
        self.pending = False
        self.stale = False
        self.stale_bar.hide()
        self._check(apply=False)

    def _set_text(self, text):
        """Replace the editor's text, keeping the cursor's line and column
        and the scroll position as far as they still exist."""
        editor = self.editor
        cursor = editor.textCursor()
        line, column = cursor.blockNumber(), cursor.positionInBlock()
        scroll = editor.verticalScrollBar().value(), editor.horizontalScrollBar().value()
        self._setting_text = True
        try:
            editor.setPlainText(text)
        finally:
            self._setting_text = False
        block = editor.document().findBlockByNumber(min(line, editor.blockCount() - 1))
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + min(column, max(0, block.length() - 1)))
        editor.setTextCursor(cursor)
        editor.verticalScrollBar().setValue(scroll[0])
        editor.horizontalScrollBar().setValue(scroll[1])

    def reset(self):
        """Forget the text: a different project is open now, and edits
        made for the last one must not reach it."""
        self.apply_timer.stop()
        self.refresh_timer.stop()
        self.model_text = None
        self.pending = False
        self.stale = False
        self.stale_bar.hide()
        self.result = None
        self.cue_segments = []
        self._selected_id = None
        self._setting_text = True
        try:
            self.editor.setPlainText('')
        finally:
            self._setting_text = False
        self.editor.set_issues([])
        self.editor.set_selected_lines(None)
        self._fill_issue_list()
        if self.isVisible():
            self.refresh_from_subtitles(force=True)
        self._update_status()

    def reload_from_subtitles(self):
        self.apply_timer.stop()
        self.refresh_from_subtitles(force=True)

    def keep_text(self):
        """Use the text as it is, over what changed in the subtitles."""
        self.stale = False
        self.stale_bar.hide()
        self.check_and_apply()

    # -- text -> subtitles ---------------------------------------------------

    def _text_changed(self):
        if self._setting_text:
            return
        self.pending = True
        self.apply_timer.start()
        self._update_status(checking=True)

    def check_and_apply(self):
        self.apply_timer.stop()
        self._check(apply=True)

    def _check(self, apply):
        text = self.editor.toPlainText()
        self.result = plaintext.parse(text, self.fmt, _LANGUAGES)
        self.editor.set_issues(self.result.issues)
        self._fill_issue_list()
        if self.result.ok and apply and not self.stale:
            self._apply(self.result)
        if self.result.ok and not self.stale:
            self.pending = False
            self.cue_segments = plaintext.segments_for(session.SUBTITLE.get('segments') or [], self.result.cues)
        else:
            self.cue_segments = []
        self._update_status()
        self._sync_selected_mark(force=True)
        if apply and self.editor.hasFocus():
            # A move made while the text was waiting to be checked could
            # not select anything then.
            self._select_under_cursor()

    def _serialize(self):
        return plaintext.serialize(session.SUBTITLE.get('segments') or [], self.fmt, _LANGUAGES,
                                   session.SUBTITLE.get('language'))

    def _apply(self, result):
        segments = session.SUBTITLE.setdefault('segments', [])
        language = result.language if result.language not in (None, session.SUBTITLE.get('language')) else None
        if not plaintext.apply_cues(segments, result.cues, dry_run=True) and language is None:
            self._refresh_full_text()
            return
        history.history_append()
        plaintext.apply_cues(segments, result.cues)
        if language is not None:
            session.SUBTITLE['language'] = language
        selected = session.SUBTITLE.get('selected')
        if selected is not None and not any(segment is selected for segment in segments):
            session.SUBTITLE['selected'] = None
        self.model_text = self._serialize()
        self._applying = True
        try:
            _subtitles_changed(self.window_ref)
        finally:
            self._applying = False
        self._refresh_full_text()

    def _refresh_full_text(self):
        """JSON's top-level "text" is every subtitle's text in one line, so it
        is rewritten after each edit. The rewrite joins the edit's undo step:
        undoing the edit takes it back too, and leaves nothing to rewrite —
        so redo keeps working."""
        if self.fmt != plaintext.JSON:
            return
        block = self.editor.document().lastBlock()
        while block.isValid() and not block.text().startswith('  "text":'):
            block = block.previous()
        if not block.isValid():
            return
        expected = plaintext.json_text_line(session.SUBTITLE.get('segments') or [])
        if block.text().rstrip().endswith(','):
            expected += ','
        if block.text() == expected:
            return
        cursor = QTextCursor(block)
        cursor.joinPreviousEditBlock()
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        self._setting_text = True
        try:
            cursor.insertText(expected)
        finally:
            self._setting_text = False
            cursor.endEditBlock()

    # -- format --------------------------------------------------------------

    def set_format(self, fmt):
        if fmt == self.fmt:
            return
        if self.pending and not self._confirm_discard():
            self.format_buttons[self.fmt].setChecked(True)
            return
        self.fmt = fmt
        _config()['format'] = fmt
        self.format_buttons[fmt].setChecked(True)
        self._setting_text = True
        try:
            self.editor.highlighter.set_format(fmt)
        finally:
            self._setting_text = False
        self.model_text = None
        self.apply_timer.stop()
        self.refresh_from_subtitles(force=True)

    def _confirm_discard(self):
        dialog = utils.SimpleDialog(self.window(), title=_('plaintext_panel.discard_title'))
        label = QLabel(_('plaintext_panel.discard_text'))
        label.setWordWrap(True)
        dialog.content.layout().addWidget(label)
        dialog.accept_button.setText(_('plaintext_panel.discard'))
        return bool(dialog.exec())

    # -- selection -----------------------------------------------------------

    def _cue_index_at(self, line):
        if not self.result:
            return None
        for index, cue in enumerate(self.result.cues):
            if cue.first_line <= line <= cue.last_line:
                return index
        return None

    def _cursor_moved(self):
        self._update_position()
        if self.editor.hasFocus():
            self.select_timer.start()

    def _select_under_cursor(self):
        if self.pending or not self.cue_segments:
            return
        index = self._cue_index_at(self.editor.textCursor().blockNumber() + 1)
        if index is None or index >= len(self.cue_segments):
            return
        segment = self.cue_segments[index]
        if segment is None or segment is session.SUBTITLE.get('selected'):
            return
        session.SUBTITLE['selected'] = segment
        self._selected_id = id(segment)
        self._sync_selected_mark(force=True)
        _selection_changed(self.window_ref)

    def _sync_selected_mark(self, force=False):
        """Mark the selected subtitle beside its lines. When the selection
        was changed elsewhere (the timeline, the list), also take the
        cursor and the view to the start of its text; a selection made
        from here (`_select_under_cursor`) is already where the cursor is."""
        selected = session.SUBTITLE.get('selected')
        changed = id(selected) != self._selected_id
        if not force and not changed:
            return
        self._selected_id = id(selected)
        cue = None
        if selected is not None and self.result and not self.pending:
            cue = next((cue for segment, cue in zip(self.cue_segments, self.result.cues) if segment is selected), None)
        self.editor.set_selected_lines((cue.first_line, cue.last_line) if cue else None)
        if changed and cue is not None:
            self.editor.setTextCursor(self.editor.cursor_at(cue.text_line, cue.text_column))
            self.editor.centerCursor()

    # -- problems ------------------------------------------------------------

    def _fill_issue_list(self):
        self.issue_list.clear()
        issues = self.result.issues if self.result else []
        for issue in issues:
            item = QListWidgetItem(f'{_("plaintext_panel.position").format(line=issue.line, column=issue.column)}   {issue_text(issue)}')
            item.setData(Qt.UserRole, issue)
            item.setIcon(_dot_icon(self.editor.theme['error' if issue.severity == plaintext.ERROR else 'warning']))
            item.setToolTip(issue_text(issue))
            self.issue_list.addItem(item)
        self.issue_list.setVisible(bool(issues))
        if issues:
            row = self.issue_list.sizeHintForRow(0)
            self.issue_list.setFixedHeight(min(len(issues), 4) * row + 8)

    def _go_to(self, line, column):
        self.editor.setTextCursor(self.editor.cursor_at(line, column))
        self.editor.centerCursor()
        self.editor.setFocus()

    def _issue_clicked(self, item):
        issue = item.data(Qt.UserRole)
        self._go_to(issue.line, issue.column)

    def _marker_clicked(self, line):
        for issue in (self.result.issues if self.result else []):
            if issue.line == line:
                self._go_to(issue.line, issue.column)
                return

    def _update_position(self):
        cursor = self.editor.textCursor()
        self.position_label.setText(_('plaintext_panel.position').format(
            line=cursor.blockNumber() + 1, column=cursor.positionInBlock() + 1))

    def _update_status(self, checking=False):
        """The line under the editor: whether the timeline has this text."""
        errors = self.result.errors if self.result else []
        warnings = [i for i in (self.result.issues if self.result else []) if i.severity != plaintext.ERROR]
        # Short, to share the line with the format switch; the tooltip says more.
        if self.stale:
            state, text, tip = 'warning', _('plaintext_panel.status_stale'), _('plaintext_panel.stale')
        elif checking:
            state, text, tip = 'checking', _('plaintext_panel.status_checking'), ''
        elif errors:
            state = 'error'
            text = _('plaintext_panel.status_error' if len(errors) == 1 else 'plaintext_panel.status_errors').format(count=len(errors))
            tip = _('plaintext_panel.status_errors_tooltip')
        elif warnings:
            state = 'warning'
            text = _('plaintext_panel.status_warning' if len(warnings) == 1 else 'plaintext_panel.status_warnings').format(count=len(warnings))
            tip = _('plaintext_panel.status_warnings_tooltip')
        else:
            state, text, tip = 'ok', _('plaintext_panel.status_ok'), _('plaintext_panel.status_ok_tooltip')
        self.status_label.setText(text)
        self.status.setToolTip(tip)
        self._fit_status_line()
        if self.status.property('state') != state:
            self.status.setProperty('state', state)
            for widget in (self.status, self.status_icon, self.status_label):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self._update_position()


class _ElidedLabel(QLabel):
    """A one-line label that ends in "…" when it does not fit, instead of
    widening the panel or being cut off mid-letter."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, rect.width())
        self.style().drawItemText(painter, rect, Qt.AlignLeft | Qt.AlignVCenter, self.palette(),
                                  self.isEnabled(), text, self.foregroundRole())
        painter.end()


def _options_icon():
    """The settings icon: dim while the options are closed, full when open
    (the chip is checked then). Built from pixmaps so both states are kept."""
    source = QIcon(str(session.PATH_SUBTITLD_GRAPHICS / 'plaintext_options_icon.svg'))
    icon = QIcon()
    for size in (16, 32):
        full = source.pixmap(QSize(size, size))
        dim = QPixmap(full.size())
        dim.fill(Qt.transparent)
        painter = QPainter(dim)
        painter.setOpacity(0.6)
        painter.drawPixmap(0, 0, full)
        painter.end()
        icon.addPixmap(dim, QIcon.Normal, QIcon.Off)
        icon.addPixmap(full, QIcon.Normal, QIcon.On)
        icon.addPixmap(full, QIcon.Active, QIcon.Off)    # hovered
        icon.addPixmap(full, QIcon.Active, QIcon.On)
    return icon


def _dot_icon(color):
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(3, 3, 6, 6)
    painter.end()
    return QIcon(pixmap)


def _subtitles_changed(window):
    """Everything that shows the subtitles, told they changed — as after
    an edit in the subtitle list."""
    session.set_unsaved(True)
    try:
        left_panel.update(window)
    except Exception:
        pass
    window.timeline_widget.update()
    player = getattr(window, 'preview_panel_player', None)
    if player is not None:
        player.update()
        device = getattr(player, '_audio_device', None)
        if device is not None and hasattr(device, 'sync_subtitle_dubs'):
            device.sync_subtitle_dubs(session.SUBTITLE.get('segments') or [])


def _selection_changed(window):
    try:
        left_panel.update(window)
    except Exception:
        pass
    window.timeline_widget.update()


# ---------------------------------------------------------------------------
# Window wiring
# ---------------------------------------------------------------------------

def load(self):
    tab = left_panel.left_panel(
        parent=self,
        tab_name='plaintext',
        update_callback=update,
        translate_callback=translate
    )
    # Header, editor and status bar run edge to edge.
    tab.layout().setContentsMargins(0, 0, 0, 0)
    self.plaintext_panel = PlainTextPanel(self)
    tab.layout().addWidget(self.plaintext_panel)
    session._document_change_callbacks.append(self.plaintext_panel.schedule_refresh)


def update(self):
    """The left panel's refresh, while this tab is the one shown — run on
    every change of the selection, so the tab follows it straight away."""
    self.plaintext_panel.schedule_refresh()
    if self.plaintext_panel.isVisible():
        self.plaintext_panel._sync_selected_mark()


def reset(self):
    """A project was just opened."""
    self.plaintext_panel.reset()


def translate(self):
    self.plaintext_panel.translate()
