"""Formatting inside a subtitle's text: the tags SubRip uses, understood.

Subtitld keeps a subtitle's text as it is written, and that text may carry
the formatting tags SRT files use, which players such as VLC and mpv honour:

    <i>…</i>  <b>…</b>  <u>…</u>  <s>…</s>   italic, bold, underline, strikeout
    <font color="#rrggbb">…</font>           colour; a face or size is kept,
                                             not drawn
    {\\an8}  {\\i1}  …                         ASS override codes, which the
                                             same players read in SRT

Anything else that looks like a tag ("<3", "a < b", WebVTT's <v Name>) is
text. Tags may nest and may be left open (they run to the end); a closing
tag with nothing to close is dropped.

The text is read here for each use: `plain` to count, speak and translate
it; `spans` and `to_html` to draw it; `to_ass` and `caption_nodes` for the
formats that write formatting their own way; and `rebalance`, so both
halves of a text cut in two stay well formed.
"""

import html
import re
from typing import NamedTuple

_TOKEN = re.compile(
    r'<\s*(?P<close>/?)\s*(?P<tag>[ibus])\s*>'
    r'|<\s*font\b(?P<font>[^>]*)>'
    r'|<\s*/\s*font\s*>'
    r'|\{(?P<override>\\[^{}]*)\}',
    re.IGNORECASE)
_FONT_COLOR = re.compile(r'''color\s*=\s*["']?\s*([#\w]+)''', re.IGNORECASE)
_HEX = re.compile(r'#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$')
# The colours HTML names that subtitle files use; others are kept, not drawn.
_NAMED = {
    'white': 'ffffff', 'black': '000000', 'red': 'ff0000', 'lime': '00ff00', 'green': '008000',
    'blue': '0000ff', 'yellow': 'ffff00', 'cyan': '00ffff', 'aqua': '00ffff', 'magenta': 'ff00ff',
    'fuchsia': 'ff00ff', 'silver': 'c0c0c0', 'gray': '808080', 'grey': '808080', 'maroon': '800000',
    'olive': '808000', 'purple': '800080', 'teal': '008080', 'navy': '000080', 'orange': 'ffa500',
}
_ASS_CODE = re.compile(r'\\(?:(?P<flag>[ibus])(?P<value>\d+)|1?c&H(?P<bgr>[0-9a-fA-F]{1,6})&?|(?P<reset>r)(?![a-z])|(?P<colour_reset>1?c)(?![&\w]))')
_ALIGN = re.compile(r'\\an?\d+')


class Style(NamedTuple):
    italic: bool = False
    bold: bool = False
    underline: bool = False
    strike: bool = False
    color: str = ''          # '#rrggbb', or '' for the default

    def any(self):
        return self != PLAIN


PLAIN = Style()
_FLAGS = {'i': 'italic', 'b': 'bold', 'u': 'underline', 's': 'strike'}


def has_markup(text):
    """Whether `text` holds any tag or override this module understands."""
    return bool(text) and _TOKEN.search(text) is not None


def color_hex(value):
    """'#rrggbb' for a colour as SRT writes it (hex, short hex or a common
    name), or '' when it is not one."""
    value = (value or '').strip().lower()
    if value in _NAMED:
        return '#' + _NAMED[value]
    match = _HEX.match(value)
    if not match:
        return ''
    digits = match.group(1)
    if len(digits) == 3:
        digits = ''.join(c * 2 for c in digits)
    return '#' + digits


def _tokens(text):
    """('text', str) | ('open', name, color) | ('close', name) | ('override', codes)"""
    position = 0
    for match in _TOKEN.finditer(text or ''):
        if match.start() > position:
            yield 'text', text[position:match.start()]
        position = match.end()
        if match.group('tag'):
            name = match.group('tag').lower()
            yield ('close', name) if match.group('close') else ('open', name, '')
        elif match.group('font') is not None:
            found = _FONT_COLOR.search(match.group('font'))
            yield 'open', 'font', color_hex(found.group(1)) if found else ''
        elif match.group('override') is not None:
            yield 'override', match.group('override')
        else:
            yield 'close', 'font'
    if position < len(text or ''):
        yield 'text', text[position:]


def _apply_override(style, codes):
    """`style` after ASS override codes ("\\i1\\an8"): the ones that change
    the look; position and the rest leave it alone."""
    for match in _ASS_CODE.finditer(codes):
        if match.group('flag'):
            style = style._replace(**{_FLAGS[match.group('flag').lower()]: match.group('value') != '0'})
        elif match.group('bgr') is not None:
            bgr = match.group('bgr').rjust(6, '0')
            style = style._replace(color='#' + (bgr[4:6] + bgr[2:4] + bgr[0:2]).lower())
        elif match.group('reset'):
            style = PLAIN
        elif match.group('colour_reset'):
            style = style._replace(color='')
    return style


class _Reader:
    """Walks the tokens, keeping the style and the tags still open."""

    def __init__(self):
        self.style = PLAIN
        self.open = []       # [(name, color, the style before it, the tag as written)]

    def step(self, token, written=''):
        kind = token[0]
        if kind == 'open':
            name, color = token[1], token[2]
            self.open.append((name, color, self.style, written))
            if name == 'font':
                if color:
                    self.style = self.style._replace(color=color)
            else:
                self.style = self.style._replace(**{_FLAGS[name]: True})
        elif kind == 'close':
            name = token[1]
            for index in range(len(self.open) - 1, -1, -1):
                if self.open[index][0] == name:
                    previous = self.open[index][2]
                    del self.open[index]
                    if name == 'font':
                        self.style = self.style._replace(color=previous.color)
                    else:
                        self.style = self.style._replace(**{_FLAGS[name]: getattr(previous, _FLAGS[name])})
                    break
        elif kind == 'override':
            self.style = _apply_override(self.style, token[1])


def _written_tokens(text):
    """The tokens with the text each was written as."""
    position = 0
    for match in _TOKEN.finditer(text or ''):
        if match.start() > position:
            yield ('text', text[position:match.start()]), text[position:match.start()]
        position = match.end()
        yield next(_tokens(match.group(0))), match.group(0)
    if position < len(text or ''):
        yield ('text', text[position:]), text[position:]


def spans(text):
    """[(text, Style)]: the visible text in runs of one style, tags gone."""
    reader = _Reader()
    runs = []
    for token in _tokens(text):
        if token[0] == 'text':
            if runs and runs[-1][1] == reader.style:
                runs[-1] = (runs[-1][0] + token[1], reader.style)
            else:
                runs.append((token[1], reader.style))
        else:
            reader.step(token)
    return runs


def plain(text):
    """The text as it reads: without its tags and override codes."""
    if not has_markup(text):
        return text or ''
    return ''.join(run for run, _style in spans(text))


def to_html(text, colors=True):
    """The text as the HTML subset Qt's rich text draws. Without `colors`,
    every run takes the default colour (for a shadow)."""
    parts = []
    for run, style in spans(text):
        chunk = html.escape(run, quote=False).replace('\n', '<br/>')
        if style.strike:
            chunk = f'<s>{chunk}</s>'
        if style.underline:
            chunk = f'<u>{chunk}</u>'
        if style.bold:
            chunk = f'<b>{chunk}</b>'
        if style.italic:
            chunk = f'<i>{chunk}</i>'
        if colors and style.color:
            chunk = f'<span style="color:{style.color}">{chunk}</span>'
        parts.append(chunk)
    return ''.join(parts)


def _ass_colour(color):
    """'#rrggbb' as ASS writes a colour: blue, green, red."""
    rgb = color.lstrip('#').upper()
    return f'&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&'


def _ass_changes(before, after):
    codes = []
    for flag, field in _FLAGS.items():
        if getattr(before, field) != getattr(after, field):
            codes.append(f'\\{flag}{int(getattr(after, field))}')
    if before.color != after.color:
        codes.append(f'\\c{_ass_colour(after.color)}' if after.color else '\\c')
    return '{' + ''.join(codes) + '}' if codes else ''


def to_ass(text):
    """The text as ASS writes it: tags as override codes, the overrides it
    already has kept, line breaks as \\N."""
    reader = _Reader()
    out = []
    for token, written in _written_tokens(text):
        if token[0] == 'text':
            out.append(token[1].replace('\n', '\\N'))
        elif token[0] == 'override':
            out.append(written)
            reader.step(token)
        else:
            before = reader.style
            reader.step(token)
            out.append(_ass_changes(before, reader.style))
    return ''.join(out)


def caption_nodes(text):
    """The text as pycaption nodes, for the formats pycaption writes:
    italic, bold, underline and colour as its styles, line breaks as
    breaks. Strikeout has no pycaption style and is left out."""
    from pycaption import CaptionNode
    nodes = []
    for run, style in spans(text):
        styles = {}
        if style.italic:
            styles['italics'] = True
        if style.bold:
            styles['bold'] = True
        if style.underline:
            styles['underline'] = True
        if style.color:
            styles['color'] = style.color
        if styles:
            nodes.append(CaptionNode.create_style(True, dict(styles)))
        for index, line in enumerate(run.split('\n')):
            if index:
                nodes.append(CaptionNode.create_break())
            if line:
                nodes.append(CaptionNode.create_text(line))
        if styles:
            nodes.append(CaptionNode.create_style(False, dict(styles)))
    return nodes


_LEGACY_ALIGN = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}


def alignment(text):
    """Where the text asks to be, as a keypad number (7 8 9 top, 4 5 6
    middle, 1 2 3 bottom; left, centre, right), from an {\\anN} code (or
    the older {\\aN}); 2, bottom centre, when it does not say."""
    found = 2
    for token in _tokens(text):
        if token[0] == 'override':
            for code in _ALIGN.findall(token[1]):
                number = int(re.sub(r'\D', '', code))
                if code.startswith('\\an'):
                    found = number if 1 <= number <= 9 else found
                else:
                    found = _LEGACY_ALIGN.get(number, found)
    return found


def rebalance(left, right):
    """(left, right) for a text cut in two, each well formed: the tags still
    open where it was cut are closed at the end of `left` and opened again
    at the start of `right`, and a position code ({\\an8}) carries over."""
    reader = _Reader()
    alignment = ''
    for token, written in _written_tokens(left):
        if token[0] != 'text':
            reader.step(token, written)
        if token[0] == 'override':
            found = _ALIGN.findall(token[1])
            if found:
                alignment = found[-1]
    closing = ''.join(f'</{name}>' for name, _color, _style, _written in reversed(reader.open))
    opening = ''.join(written for _name, _color, _style, written in reader.open)
    prefix = '{' + alignment + '}' if alignment and not right.lstrip().startswith('{' + alignment) else ''
    return left + closing, prefix + opening + right
