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


TAG_ORDER = ('font', 'i', 'b', 'u', 's')      # outermost first, when opened together


def tags_for(style):
    """The tags a run in `style` sits in: [(name, value)]."""
    wanted = [('font', style.color)] if style.color else []
    return wanted + [(name, True) for name in ('i', 'b', 'u', 's') if getattr(style, _FLAGS[name])]


def _open_tag(name, value):
    return f'<font color="{value}">' if name == 'font' else f'<{name}>'


def from_spans(runs):
    """Text with tags for [(text, Style)] runs: well nested, a tag opened
    once for as long as its style lasts."""
    out = []
    stack = []                                  # [(name, value)], outermost first
    for run, style in runs:
        if not run:
            continue
        wanted = tags_for(style)
        keep = 0
        while keep < len(stack) and stack[keep] in wanted:
            keep += 1
        for name, _value in reversed(stack[keep:]):
            out.append(f'</{name}>')
        del stack[keep:]
        for item in sorted((item for item in wanted if item not in stack), key=lambda item: TAG_ORDER.index(item[0])):
            out.append(_open_tag(*item))
            stack.append(item)
        out.append(run)
    for name, _value in reversed(stack):
        out.append(f'</{name}>')
    return ''.join(out)


_LEAD = re.compile(r'(?:\s*\{\\[^{}]*\})+')


def split_lead(text):
    """(codes, rest): the override codes a text starts with ("{\\an8}"),
    which are not text to edit, and the text after them."""
    match = _LEAD.match(text or '')
    if not match:
        return '', text or ''
    return match.group(0).strip(), (text or '')[match.end():]


def with_alignment(lead, keypad):
    """`lead` (override codes) placing the text at `keypad` (7 8 9 top, 4 5 6
    middle, 1 2 3 bottom): its \\an code set, or gone for 2, the default."""
    codes = ''.join(re.findall(r'\{(\\[^{}]*)\}', lead or ''))
    codes = _ALIGN.sub('', codes)
    if keypad and keypad != 2:
        codes = f'\\an{keypad}' + codes
    return '{' + codes + '}' if codes else ''


# USF writes a position as a name (its "alignment" attribute).
KEYPAD_NAMES = {
    7: 'TopLeft', 8: 'TopCenter', 9: 'TopRight',
    4: 'MiddleLeft', 5: 'MiddleCenter', 6: 'MiddleRight',
    1: 'BottomLeft', 2: 'BottomCenter', 3: 'BottomRight',
}

# Positions by name, for formats that write words (the plain-text panel's
# Markdown: {position=top}).
POSITION_NAMES = {
    7: 'top-left', 8: 'top', 9: 'top-right',
    4: 'left', 5: 'middle', 6: 'right',
    1: 'bottom-left', 2: 'bottom', 3: 'bottom-right',
}

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


# -- Markdown ----------------------------------------------------------------
#
# The plain-text panel's Markdown writes the formatting the Markdown way:
#
#     *italic*  **bold**  ***both***  ~~strikeout~~ (GitHub's)
#     [underlined]{.underline}  [coloured]{color=#ff0000}   (Pandoc's spans)
#
# read as CommonMark reads emphasis (a delimiter opens before a word and
# closes after one, "*sigh*" written as text is "\*sigh\*"), and a backslash
# before punctuation makes it text. Override codes other than the formatting
# ones stay as they are written.

_MD_PUNCT = frozenset('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')
_STYLE_CODE = re.compile(r'\\(?:[ibus]\d+|1?c&H[0-9a-fA-F]{1,6}&?|1?c(?![&\w])|r(?![a-z])\w*)')


def _bare_spaces(runs):
    """Runs whose italic, bold and strikeout neither start nor end with a
    space (Markdown cannot write that): a space at the edge of one leaves
    what starts or ends there, and keeps what goes on past it. A run may be
    (text, Style, literal): a literal one is a code written as it is, never
    split nor merged."""
    runs = [(run[0], run[1], run[2] if len(run) > 2 else False) for run in runs]
    out = []
    for index, (run, style, literal) in enumerate(runs):
        if literal or not (style.italic or style.bold or style.strike) or run.strip() == run:
            out.append((run, style, literal))
            continue
        before = runs[index - 1][1] if index else PLAIN
        after = runs[index + 1][1] if index + 1 < len(runs) else PLAIN

        def shared(*others):
            return style._replace(**{field: getattr(style, field) and all(getattr(other, field) for other in others)
                                     for field in ('italic', 'bold', 'strike')})
        core = run.strip()
        head, tail = run[:len(run) - len(run.lstrip())], run[len(run.rstrip()):]
        if not core:
            out.append((run, shared(before, after), False))
            continue
        out.extend((part, part_style, False) for part, part_style in
                   ((head, shared(before)), (core, style), (tail, shared(after))) if part)
    merged = []
    for run, style, literal in out:
        if merged and not literal and not merged[-1][2] and merged[-1][1] == style:
            merged[-1] = (merged[-1][0] + run, style, False)
        else:
            merged.append((run, style, literal))
    return merged


def _md_pieces(text):
    """[(text, Style, literal)]: the text's runs, its override codes that
    are not formatting kept as literal pieces (written as they are)."""
    reader = _Reader()
    pieces = []
    for token, written in _written_tokens(text):
        if token[0] == 'text':
            pieces.append((token[1], reader.style, False))
            continue
        if token[0] == 'override':
            rest = _STYLE_CODE.sub('', token[1])
            if rest:
                pieces.append(('{' + rest + '}', reader.style, True))
        reader.step(token, written)
    return pieces


def _md_escape(run, after=''):
    """Text as Markdown text: what would read as formatting escaped."""
    out = []
    for index, char in enumerate(run):
        following = run[index + 1] if index + 1 < len(run) else after[:1]
        previous = run[index - 1] if index else ''
        if char == '\\' and following in _MD_PUNCT:
            out.append('\\\\')
        elif char == '*':
            out.append('\\*')
        elif char == '_' and not (previous.isalnum() and following.isalnum()):
            out.append('\\_')
        elif char == '~' and (following == '~' or previous == '~'):
            out.append('\\~')
        elif char == ']' and following == '{':
            out.append('\\]')
        else:
            out.append(char)
    return ''.join(out)


def _md_span_attrs(style):
    attrs = []
    if style.underline:
        attrs.append('.underline')
    if style.color:
        attrs.append(f'color={style.color}')
    return ' '.join(attrs)


def to_markdown(text):
    """The text with its formatting written the Markdown way. The override
    codes it starts with ({\\an8}) are left out: the caller writes the
    position its own way (alignment(), split_lead())."""
    _lead, rest = split_lead(text)
    pieces = _bare_spaces([piece for piece in _md_pieces(rest) if piece[0]])
    out = []
    stack = []                                  # [(kind, value)], outermost first
    for index, (run, style, literal) in enumerate(pieces):
        if not run:
            continue
        wanted = []
        if _md_span_attrs(style):
            wanted.append(('span', _md_span_attrs(style)))
        if style.strike:
            wanted.append(('~~', True))
        if style.bold:
            wanted.append(('**', True))
        if style.italic:
            wanted.append(('*', True))
        keep = 0
        while keep < len(stack) and stack[keep] in wanted:
            keep += 1
        for kind, value in reversed(stack[keep:]):
            out.append(']{' + value + '}' if kind == 'span' else kind)
        del stack[keep:]
        for kind, value in wanted:
            if (kind, value) not in stack:
                out.append('[' if kind == 'span' else kind)
                stack.append((kind, value))
        following = next((piece[0] for piece in pieces[index + 1:] if piece[0]), '')
        out.append(run if literal else _md_escape(run, following))
    for kind, value in reversed(stack):
        out.append(']{' + value + '}' if kind == 'span' else kind)
    return ''.join(out)


def _md_flanking(source, start, end):
    """(left, right) flanking, CommonMark's, for the delimiter run source[start:end]."""
    before = source[start - 1] if start > 0 else ' '
    after = source[end] if end < len(source) else ' '
    left = not after.isspace() and (after not in _MD_PUNCT or before.isspace() or before in _MD_PUNCT)
    right = not before.isspace() and (before not in _MD_PUNCT or after.isspace() or after in _MD_PUNCT)
    return left, right, before, after


def _md_bracket_end(source, start):
    """The index of the "]" closing the "[" at `start`, or -1."""
    depth = 0
    index = start
    while index < len(source):
        char = source[index]
        if char == '\\':
            index += 2
            continue
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


_MD_ATTR = re.compile(r'''\.underline\b|color\s*=\s*["']?([#\w]+)["']?|style\s*=\s*["'][^"']*?color\s*:\s*([#\w]+)[^"']*["']''')


def _md_span_style(attrs):
    """What a span's {attributes} change, as {field: value}; None when it
    says nothing this knows (then it is text)."""
    change = {}
    for match in _MD_ATTR.finditer(attrs):
        if match.group(0).startswith('.underline'):
            change['underline'] = True
        else:
            color = color_hex(match.group(1) or match.group(2))
            if color:
                change['color'] = color
    return change or None


def _md_items(source, offset=0, marks=None):
    """The source as items, with emphasis matched (CommonMark's "process
    emphasis"): ['text', str, at] | ['delim', char, count, can_open,
    can_close, original count, at, used from the left] | ['wrap', field,
    items] | ['span', change, items]. `at` is where an item starts in the
    whole text (`offset` that of `source`); `marks` collects the (start, end)
    of the syntax: delimiters used, escaping backslashes, span brackets."""
    marks = [] if marks is None else marks
    items = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == '\\' and index + 1 < len(source) and source[index + 1] in _MD_PUNCT:
            marks.append((offset + index, offset + index + 1))
            items.append(['text', source[index + 1], offset + index + 1])
            index += 2
            continue
        if char == '[':
            close = _md_bracket_end(source, index)
            if close != -1 and source[close + 1:close + 2] == '{':
                attrs_end = source.find('}', close + 2)
                change = _md_span_style(source[close + 2:attrs_end]) if attrs_end != -1 else None
                if change is not None:
                    marks.append((offset + index, offset + index + 1))
                    marks.append((offset + close, offset + attrs_end + 1))
                    items.append(['span', change, _md_items(source[index + 1:close], offset + index + 1, marks)])
                    index = attrs_end + 1
                    continue
            items.append(['text', '[', offset + index])
            index += 1
            continue
        if char in '*_~':
            end = index
            while end < len(source) and source[end] == char:
                end += 1
            count = end - index
            if char == '~' and count != 2:
                items.append(['text', source[index:end], offset + index])
                index = end
                continue
            left, right, before, after = _md_flanking(source, index, end)
            if char == '_':
                can_open = left and (not right or before in _MD_PUNCT)
                can_close = right and (not left or after in _MD_PUNCT)
            else:
                can_open, can_close = left, right
            items.append(['delim', char, count, can_open, can_close, count, offset + index, 0])
            index = end
            continue
        end = index + 1
        while end < len(source) and source[end] not in '\\[*_~':
            end += 1
        items.append(['text', source[index:end], offset + index])
        index = end

    position = 0
    while position < len(items):
        closer = items[position]
        if closer[0] != 'delim' or not closer[4] or closer[2] <= 0:
            position += 1
            continue
        found = None
        for back in range(position - 1, -1, -1):
            opener = items[back]
            if opener[0] != 'delim' or opener[1] != closer[1] or not opener[3] or opener[2] <= 0:
                continue
            if closer[1] in '*_' and (opener[4] or closer[3]) and (opener[5] + closer[5]) % 3 == 0 \
                    and not (opener[5] % 3 == 0 and closer[5] % 3 == 0):
                continue
            found = back
            break
        if found is None:
            position += 1
            continue
        opener = items[found]
        use = 2 if closer[1] == '~' or (opener[2] >= 2 and closer[2] >= 2) else 1
        field = 'strike' if closer[1] == '~' else ('bold' if use == 2 else 'italic')
        # An opener gives up its innermost (rightmost) characters, a closer
        # its leftmost.
        marks.append((opener[6] + opener[2] - use, opener[6] + opener[2]))
        marks.append((closer[6] + closer[7], closer[6] + closer[7] + use))
        opener[2] -= use
        closer[2] -= use
        closer[7] += use
        items[found + 1:position] = [['wrap', field, items[found + 1:position]]]
        position = found + 2 if closer[2] > 0 else found + 3
    return items


def _md_render(items, style, runs):
    """[(text, Style, at)] for the items."""
    for item in items:
        if item[0] == 'text':
            runs.append((item[1], style, item[2]))
        elif item[0] == 'delim':
            if item[2] > 0:
                # What no other delimiter matched is text: an opener's
                # leftmost characters, a closer's rightmost.
                at = item[6] + item[7] if item[7] else item[6]
                runs.append((item[1] * item[2], style, at))
        elif item[0] == 'wrap':
            _md_render(item[2], style._replace(**{item[1]: True}), runs)
        else:
            _md_render(item[2], style._replace(**item[1]), runs)
    return runs


def markdown_layout(source):
    """(styled, marks) for drawing Markdown text: [(start, end, Style)] of
    what it reads, and [(start, end)] of its syntax (delimiters, escaping
    backslashes, span brackets and attributes)."""
    marks = []
    runs = _md_render(_md_items(source or '', 0, marks), PLAIN, [])
    return [(at, at + len(run), style) for run, style, at in runs if run], sorted(marks)


def from_markdown(source):
    """Text with tags for text whose formatting is written the Markdown way."""
    return from_spans([(run, style) for run, style, _at in _md_render(_md_items(source or ''), PLAIN, [])])


def key(text):
    """What the text is, for comparing: the codes it starts with, what it
    reads and how, and its other codes; spelling aside ("<i>a </i>b" is
    "<i>a</i> b", as Markdown writes it)."""
    lead, rest = split_lead(text or '')
    return (alignment(lead), with_alignment(lead, 2), tuple(_bare_spaces([piece for piece in _md_pieces(rest) if piece[0]])))
