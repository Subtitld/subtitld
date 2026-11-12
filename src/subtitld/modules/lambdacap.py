"""Lambda CAP (.cap): the subtitle files of Japan's subtitling houses, which
Netflix and Amazon took Japanese subtitles in.

The format is Videotron Lambda's and has no public specification; Netflix's
"Lambda Cap Creation Guide" is only open to its partners. What is here is
what two independent readers agree on: Subtitle Edit's (libse,
LambdaCap.cs and its tests, GPL-3) and Skynav's cap2tt (ttt-cap2tt,
Converter.java and its test files, BSD), the converter Netflix's TTML
editor wrote from that guide. Where they differ, or only one says a thing,
the comment there says which was followed.

A file is text, Shift-JIS in the older files, UTF-8 (with or without a
byte order mark) or UTF-16 in newer ones (cap2tt reads all four). Its
first line is a header of three fields between tabs:

    Lambda字幕V4    DF0+1    SCENE"和文標準"

DF1 says the timecode is drop-frame, DF0 that it is not (both readers);
the rest is written as both write it. Then a line a screen (a subtitle):

    1    01002619/01002902    中国 ＠ルビ上［成都｜せいと］＠    ＠横下    ＠中頭
                              二行目

its number, its start and end as HHMMSSFF, its first line of text, then
the codes that place it; each further line of text on a line of its own,
after tabs. Fields are parted by tabs, and cap2tt by spaces too, so the
codes may follow the text after a space (as older Subtitle Edit wrote
them). A numbered screen without times (cap2tt) is shown with the one
before it, at its times.

Codes are "＠" and a name, full width. The ones for a whole screen:

    ＠横下 ＠横上 ＠横中     written across, at the bottom (the default),
                            top or middle; ＠横適 (across, full) is read
                            as the bottom
    ＠縦右 ＠縦左 ＠縦中     written down the picture, in columns, at the
                            right, the left or the middle; ＠縦適 as right
    ＠行頭 ＠行末            lines aligned to their start or end: left or
                            right across, the top or bottom of a column
    ＠中央 ＠中頭 ＠中末 ＠両端 centred (cap2tt's are all centred blocks)
    ＠横中央 ＠縦右頭 …      the two at once (cap2tt's list)
    ＠斜３ ＠斜 ＠正体       italic, at a slant of 0 to 5 (＠斜 is 3,
                            ＠斜０ upright), or upright
    ＠継続                  a screen continued from the one before

A code with "！" after it (＠縦右！) holds for the screens after it too
(cap2tt), until another with "！" takes its place; a screen's own code
without one is for that screen alone. Typefaces (＠丸ゴ …) and codes
neither reader knows are left out.

Codes inside the text hold what they are about between ［ and ］＠:

    ＠ルビ上［base｜reading］＠   a reading above (before) the base; ＠ルビ右
                                 the same down a column; ＠ルビ下 and ＠ルビ左
                                 below or left of it (after); ＠ルビ alone
                                 where the writing puts it
    ＠ルビ上［誰｜・］＠          a "reading" of marks alone (・ ･ ↓ ↑) is
                                 not one: it is an emphasis dot beside
                                 each character of the base. Above or to
                                 the right is before the text, below or to
                                 the left after it; a mark on the wrong
                                 side for the writing (cap2tt), or ＠ルビ,
                                 is wherever the writing puts it
    ＠組［12］＠                   set upright in one character's room down
                                 a column (tate-chū-yoko)
    ＠斜３［text］＠               italic for a stretch of text
    ＠幅広［―］＠                  a wide dash: cap2tt reads it as the two-em
                                 dash "⸺", which it looks like (Subtitle
                                 Edit as a plain "―"); other widths and
                                 sizes (＠倍角 ＠半角 ＠幅 ＠寸) keep their text,
                                 not their size

They nest (italics around a ruby). In text, "_" is a space and "＿" an
ideographic one, and ＠_＠, ＠＿＠ and ＠＠＠ stand for the characters
themselves (cap2tt; Subtitle Edit reads them as written).

Reading gives a subtitle a screen: the ruby, emphasis, tate-chū-yoko and
italics as Subtitld's markup; a column's 'writing' 'vertical-rl' (Lambda
writes columns from the right only) and its place on the picture as one
of the nine, from the placement and alignment. The frames count at the
video's rate, 30 without one (cap2tt's default; Japan's 29.97 counts 30
a second), at 29.97 (or 59.94) where the header says drop-frame.

Writing gives the header (DF1 at 29.97 and 59.94 unless asked otherwise,
as Subtitle Edit does), a screen a subtitle numbered from 1, its lines,
and its place as codes in fields of their own: ＠横上, ＠横中 or ＠横下 with
＠行頭, ＠中頭 or ＠行末 across; ＠縦右, ＠縦中 or ＠縦左, with ＠行頭 or ＠行末
for a column at the top or bottom, down. A subtitle italic all through
gets ＠斜３ for the screen, as Subtitle Edit writes it; italic stretches
inside get their own. An emphasis is a mark ("・") a character, as Subtitle
Edit writes it. Lines end in CRLF; the text is UTF-8 with a byte order
mark unless the file opened was in another encoding.

What is lost on the way: colours, bold, underline, sizes and typefaces,
which Lambda CAP has no codes for here; an emphasis's shape (Lambda's is
a dot); a reading's alignment; a column from the left ('vertical-lr'),
written from the right; where ＠ルビ alone or a mark on the other side put
a reading or a mark, written as before the text; ＠継続, ＠中央 and the other
centred alignments, all centred; a screen's slant beyond italic or not.
"""

import re
from fractions import Fraction

from subtitld.modules import markup
from subtitld.modules.avid_subcap import drop_rate, frame_of, rate, timecode

HEADER = 'Lambda字幕V4\t{drop}\tSCENE"和文標準"'
DEFAULT_FPS = 30.0
_PREAMBLE = 'Lambda字幕'
ENCODINGS = ('utf-8-sig', 'utf-8', 'utf-16', 'cp932')

_AT = '＠'
_OPEN = '［'
_CLOSE = '］＠'
# Both readers part a ruby's base from its reading with a full-width bar;
# Subtitle Edit takes the half-width one as well.
_BARS = '｜|'
# A "reading" of these alone is emphasis: Subtitle Edit's (↓ ↑ ・ ･) and
# cap2tt's tests (・, ↓ ↑).
_MARKS = '・･↓↑'
_MARK = '・'
_WIDE_DASH = '⸺'

# Placement: (across or down, row or column). Rows: 0 bottom, 1 middle,
# 2 top; columns: 0 left, 1 middle, 2 right.
_PLACEMENTS = {
    '横下': ('across', 0), '横上': ('across', 2), '横中': ('across', 1), '横適': ('across', 0),
    '縦右': ('down', 2), '縦左': ('down', 0), '縦中': ('down', 1), '縦適': ('down', 2),
}
# Alignment along a line: 'start', 'end' or centred.
_ALIGNMENTS = {'行頭': 'start', '行末': 'end', '中央': '', '中頭': '', '中末': '', '両端': ''}
# Both at once, as cap2tt lists them. "縦中頭" is the middle column, centred
# (cap2tt's region for it is 縦中).
_BOTH = {
    '横中央': ('横下', '中央'), '横中頭': ('横下', '中頭'), '横中末': ('横下', '中末'),
    '横行頭': ('横下', '行頭'), '横行末': ('横下', '行末'),
    '縦右頭': ('縦右', '行頭'), '縦左頭': ('縦左', '行頭'), '縦中頭': ('縦中', '中頭'),
}
# Typefaces and the rest a screen may name, kept to tell them from text.
_OTHER_CODES = ('継続', '丸ゴ', '丸ゴシック', '角ゴ', '太角ゴ', '太角ゴシック', '太明', '太明朝', 'シネマ', '詰')
_SCREEN_CODES = set(_PLACEMENTS) | set(_ALIGNMENTS) | set(_BOTH) | {'正体', '斜'} | set(_OTHER_CODES)
_RUBY = {'ルビ': '', 'ルビ上': 'above', 'ルビ下': 'below', 'ルビ右': 'right', 'ルビ左': 'left'}
# Inside the text: widths and sizes keep their text, not their size.
_WIDTHS = ('幅広', '倍角', '半角', '拗音', '幅', '寸', '詰')
_TEXT_CODES = set(_RUBY) | {'組', '斜', '正体'} | set(_WIDTHS)

_NAME = r'([^\s0-9０-９！＠［］｜]+)'
_CODE = re.compile('＠' + _NAME + r'([0-9０-９]*)(！*)$')
_TEXT_CODE = re.compile('＠' + _NAME + r'([0-9０-９]*)［')
_ESCAPES = {'＠_＠': '_', '＠＿＠': '＿', '＠＠＠': '＠'}
_SCREEN = re.compile(r'([0-9０-９]+)[A-Ia-iＡ-Ｉａ-ｉ]?(?=[\t ]|$)')
_TIMES = re.compile(r'[\t ]*([0-9０-９]{8})[/／]([0-9０-９]{8})(?=[\t ]|$)')
_PARTS = re.compile(r'([\t ]+)')
_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')


def _number(digits):
    return int(digits.translate(_DIGITS)) if digits else None


def is_lambda(data):
    """Whether `data` (the first bytes of a file) is a Lambda CAP: its text
    begins with the Lambda字幕 header, in any of the encodings it is written
    in. Cheetah's .cap is binary and never does."""
    head = bytes(data[:256])
    if head.startswith(b'\xef\xbb\xbf'):
        head = head[3:]
    elif head.startswith((b'\xff\xfe', b'\xfe\xff')) or head[1:2] == b'\x00' or head[:1] == b'\x00':
        # UTF-16: an even number of bytes, so none is cut in two.
        return decode(head[:len(head) // 2 * 2])[0].lstrip('\ufeff').startswith(_PREAMBLE)
    if not head.startswith(b'Lambda'):
        return False
    # The first bytes may end inside a character, which is not to count.
    return any(head.decode(encoding, 'ignore').startswith(_PREAMBLE) for encoding in ('utf-8', 'cp932'))


def decode(data):
    """(text, encoding) for a file's bytes: UTF-16 or UTF-8 by its byte
    order mark, UTF-8 when it decodes as such, else Shift-JIS (Windows'
    cp932, which the older files are in)."""
    data = bytes(data)
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', 'replace'), 'utf-8-sig'
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', 'replace'), 'utf-16'
    if data[1:2] == b'\x00' and data[:1] != b'\x00':
        return data.decode('utf-16-le', 'replace'), 'utf-16'
    if data[:1] == b'\x00':
        return data.decode('utf-16-be', 'replace'), 'utf-16'
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        # A file cut short inside a character still decodes as far as it goes.
        try:
            return data.decode('cp932'), 'cp932'
        except UnicodeDecodeError:
            return data.decode('cp932', 'replace'), 'cp932'


# Reading.
def _plain_text(text):
    """Text with Lambda's space escapes undone: "_" a space, "＿" an
    ideographic one (cap2tt)."""
    return text.replace('\t', ' ').replace('_', ' ').replace('＿', '\u3000')


def _emphasis(code, writing):
    """The emphasis for marks set by a ruby code: before or after the text
    when the code's side is one the writing has, else wherever it puts them
    (cap2tt: above and below across, right and left down)."""
    side = _RUBY.get(code, '')
    if writing:
        where = {'right': ' before', 'left': ' after'}.get(side, '')
    else:
        where = {'above': ' before', 'below': ' after'}.get(side, '')
    return 'filled dot' + where


def _runs(text, writing):
    """[(text, Style)] for a line of Lambda text and its codes."""
    runs = []
    stack = [(markup.PLAIN, False)]               # (style, inside a wide dash)
    position = 0
    plain = []

    def flush():
        if plain:
            style, wide = stack[-1]
            piece = _plain_text(''.join(plain))
            runs.append((piece.replace('―', _WIDE_DASH) if wide else piece, style))
            plain.clear()

    while position < len(text):
        escape = text[position:position + 3]
        if escape in _ESCAPES:
            flush()
            runs.append((_ESCAPES[escape], stack[-1][0]))
            position += 3
            continue
        opened = _TEXT_CODE.match(text, position) if text[position] == _AT else None
        if opened and opened.group(1) in _TEXT_CODES:
            name, count = opened.group(1), _number(opened.group(2))
            flush()
            style, wide = stack[-1]
            if name in _RUBY:
                end = text.find(_CLOSE, opened.end())
                if end < 0:
                    plain.append(text[position:])
                    break
                content = text[opened.end():end]
                bar = min((content.find(bar) for bar in _BARS if bar in content), default=-1)
                base, reading = (content[:bar], content[bar + 1:]) if bar >= 0 else (content, '')
                base = _plain_text(base)
                if reading and all(char in _MARKS for char in reading):
                    runs.append((base, style._replace(emphasis=_emphasis(name, writing))))
                elif reading:
                    where = {'above': 'before', 'right': 'before', 'below': 'after', 'left': 'after'}.get(_RUBY[name], '')
                    runs.append((base, style._replace(ruby='base', ruby_position=where)))
                    runs.append((_plain_text(reading), style._replace(ruby='text', ruby_position=where)))
                else:
                    runs.append((base, style))
                position = end + len(_CLOSE)
                continue
            if name == '組':
                style = style._replace(combine=markup.COMBINE)
            elif name == '斜':
                style = style._replace(italic=count != 0)
            elif name == '正体':
                style = style._replace(italic=False)
            stack.append((style, wide or name == '幅広'))
            position = opened.end()
            continue
        if text.startswith(_CLOSE, position) and len(stack) > 1:
            flush()
            stack.pop()
            position += len(_CLOSE)
            continue
        plain.append(text[position])
        position += 1
    flush()
    return runs


def _split(rest):
    """(text, [codes]) for what a line holds after its number and times:
    its text up to the first field that is a code outside a code's
    brackets, and the codes after it. Codes neither reader knows are left
    out."""
    text, codes = [], []
    depth = 0
    in_codes = False
    for part in _PARTS.split(rest):
        if not part:
            continue
        if part.isspace():
            if not in_codes:
                text.append(part)
            continue
        found = _CODE.match(part) if depth == 0 else None
        if found:
            in_codes = True
            name = found.group(1)
            if name in _SCREEN_CODES:
                codes.append((name, _number(found.group(2)), bool(found.group(3))))
            continue
        in_codes = False
        text.append(part)
        depth += len(_TEXT_CODE.findall(part)) - part.count(_CLOSE)
        depth = max(depth, 0)
    return ''.join(text).strip(' \t'), codes


def _screen_codes(codes, held):
    """(placement, alignment, italic or None) for a screen's codes, with
    what earlier screens held ("！") where it says nothing; `held` is
    updated with what it holds."""
    placement, alignment, italic = held.get('placement'), held.get('alignment'), None
    for name, count, keep in codes:
        pair = _BOTH.get(name)
        if pair or name in _PLACEMENTS:
            placement = pair[0] if pair else name
            if keep:
                held['placement'] = placement
        if pair or name in _ALIGNMENTS:
            alignment = pair[1] if pair else name
            if keep:
                held['alignment'] = alignment
        if name == '斜':
            # ＠斜 alone is a slant of 3 (cap2tt's default); ＠斜０ upright.
            italic = count != 0
        elif name == '正体':
            italic = False
    return placement or '横下', alignment or '中頭', italic


def _keypad(placement, alignment):
    """(keypad, writing) for a screen's placement and alignment."""
    direction, where = _PLACEMENTS.get(placement, ('across', 0))
    along = _ALIGNMENTS.get(alignment, '')
    if direction == 'across':
        column = {'start': 0, 'end': 2}.get(along, 1)
        return (1, 4, 7)[where] + column, ''
    row = {'start': 2, 'end': 0}.get(along, 1)
    return (1, 4, 7)[row] + where, 'vertical-rl'


def parse(text, fps=None):
    """(subtitles, settings) in Lambda CAP text: [{'start', 'end', 'text'}]
    by start ('writing' for a column), and {'fps', 'drop'}, the rate the
    frames were read at and whether the timecode is drop-frame. Raises
    ValueError for text that is not Lambda CAP."""
    lines = str(text or '').lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].startswith(_PREAMBLE):
        raise ValueError('Not a Lambda CAP file: it does not begin with its Lambda字幕 header')
    flags = re.search(r'DF([01])', lines[0])
    drop = bool(flags) and flags.group(1) == '1'
    fps = drop_rate(fps) if drop else float(fps or DEFAULT_FPS)
    nominal, frame_length = rate(fps)

    def seconds(digits):
        digits = digits.translate(_DIGITS)
        frame = frame_of(digits[0:2], digits[2:4], digits[4:6], digits[6:8], nominal, drop)
        return round(float(frame * frame_length), 3)

    screens = []                               # [start, end, [lines], [codes]]
    times = None
    for line in lines[1:]:
        if not line.strip():
            continue
        rest = line
        number = _SCREEN.match(line)
        if number:
            rest = line[number.end():]
        found = _TIMES.match(rest)
        if found:
            times = (seconds(found.group(1)), seconds(found.group(2)))
            rest = rest[found.end():]
        body, codes = _split(rest)
        if (number or found) and times:
            # A numbered screen without times is shown with the one before
            # it (cap2tt).
            screens.append([times[0], times[1], [body] if body else [], codes])
        elif screens:
            if body:
                screens[-1][2].append(body)
            screens[-1][3].extend(codes)

    segments = []
    held = {}
    for start, end, body, codes in screens:
        placement, alignment, italic = _screen_codes(codes, held)
        keypad, writing = _keypad(placement, alignment)
        runs = []
        for index, line in enumerate(body):
            if index:
                runs.append(('\n', markup.PLAIN))
            runs.extend(_runs(line, writing))
        if italic:
            runs = [(run, style._replace(italic=True)) for run, style in runs]
        content = markup.from_spans([(run, style) for run, style in runs if run])
        if not markup.plain(content).strip():
            continue
        segment = {'start': start, 'end': max(start, end),
                   'text': markup.with_alignment('', keypad) + content}
        if writing:
            segment['writing'] = writing
        segments.append(segment)
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, {'fps': fps, 'drop': drop}


def read(path, fps=None):
    """(subtitles, settings) in the Lambda CAP file at `path`, as parse()
    gives them, with the 'encoding' it was in."""
    with open(path, 'rb') as cap_file:
        text, encoding = decode(cap_file.read())
    segments, settings = parse(text, fps)
    settings['encoding'] = encoding
    return segments, settings


# Writing.
def _escaped(text):
    """Text as Lambda writes it: the characters that are codes escaped, a
    tab a space, the two-em dash the wide one."""
    out = []
    for char in text:
        if char == '_':
            out.append('＠_＠')
        elif char == '＿':
            out.append('＠＿＠')
        elif char == _AT:
            out.append('＠＠＠')
        elif char == _WIDE_DASH:
            out.append('＠幅広［―］＠')
        elif char in '\t\r':
            out.append(' ')
        else:
            out.append(char)
    return ''.join(out)


def _ruby_code(position, writing):
    """The ruby code for a reading or mark before or after the text."""
    if writing:
        return 'ルビ左' if position == 'after' else 'ルビ右'
    return 'ルビ下' if position == 'after' else 'ルビ上'


def _line(runs, writing, italic_all):
    """A line of Lambda text for runs of one line."""
    pieces = []                                 # (text, italic)
    for group in markup.ruby_groups(runs):
        if group[0] == 'ruby':
            base = ''.join(run for run, _style in group[1])
            reading = ''.join(run for run, _style in group[2])
            italic = any(style.italic for run, style in group[1] if run.strip())
            if base and reading:
                code = _ruby_code(markup.ruby_attributes(group).position, writing)
                pieces.append((f'＠{code}［{_escaped(base)}｜{_escaped(reading)}］＠', italic))
            else:
                pieces.append((_escaped(base or reading), italic))
            continue
        for run, style in group[1]:
            if style.combine and run.strip():
                piece = f'＠組［{_escaped(run)}］＠'
            elif style.emphasis:
                where = 'after' if style.emphasis.endswith(' after') else 'before'
                code = _ruby_code(where, writing)
                piece = ''.join(f'＠{code}［{_escaped(char)}｜{_MARK}］＠' if char.strip() else _escaped(char)
                                for char in run)
            else:
                piece = _escaped(run)
            pieces.append((piece, style.italic))
    if italic_all:
        return ''.join(piece for piece, _italic in pieces).strip()
    out = []
    for piece, italic in pieces:
        if italic and out and out[-1][1]:
            out[-1][0] += piece
        else:
            out.append([piece, italic])
    return ''.join(f'＠斜３［{piece}］＠' if italic and piece.strip() else piece for piece, italic in out).strip()


def _codes(keypad, writing):
    """The placement and alignment codes for a subtitle's place."""
    row, column = (keypad - 1) // 3, (keypad - 1) % 3
    if writing:
        codes = [('縦左', '縦中', '縦右')[column]]
        if row != 1:
            codes.append('行頭' if row == 2 else '行末')
        return codes
    # Centred lines as Subtitle Edit writes them: a centred block, each line
    # from its start.
    return [('横下', '横中', '横上')[row], ('行頭', '中頭', '行末')[column]]


def write(segments, fps=DEFAULT_FPS, drop=True):
    """Lambda CAP text for `segments`, frames at `fps`, drop-frame when
    `drop` and the rate is 29.97 or 59.94."""
    nominal, frame_length = rate(fps or DEFAULT_FPS)
    drop = bool(drop) and nominal in (30, 60) and frame_length.numerator == 1001
    out = [HEADER.format(drop='DF1+1' if drop else 'DF0+1'), '']
    number = 0
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        lead, rest = markup.split_lead(str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n'))
        runs = markup.spans(rest)
        if not markup.plain(rest).strip():
            continue
        writing = 'vertical-rl' if segment.get('writing') in ('vertical-rl', 'vertical-lr') else ''
        italic_all = all(style.italic for run, style in runs if run.strip())
        lines = [[]]
        for run, style in runs:
            for index, part in enumerate(run.split('\n')):
                if index:
                    lines.append([])
                if part:
                    lines[-1].append((part, style._replace(italic=False) if italic_all else style))
        written = [_line(line, writing, italic_all) for line in lines]
        written = [line for line in written if line]
        if not written:
            continue
        codes = _codes(markup.alignment(lead), writing) + (['斜３'] if italic_all else [])
        first = int(round(Fraction(float(segment.get('start', 0))).limit_denominator(1000000) / frame_length))
        last = int(round(Fraction(float(segment.get('end', 0))).limit_denominator(1000000) / frame_length))
        last = max(first + 1, last)
        times = '/'.join(re.sub(r'\D', '', timecode(frame, nominal, drop)) for frame in (first, last))
        number += 1
        out.append(f'{number}\t{times}\t{written[0]}' + ''.join(f'\t＠{code}' for code in codes))
        out.extend('\t\t\t\t' + line for line in written[1:])
    return '\r\n'.join(out) + '\r\n'


def save(path, segments, fps=DEFAULT_FPS, drop=True, encoding='utf-8-sig'):
    """Write `segments` to `path` as Lambda CAP, in `encoding` (one of
    ENCODINGS; characters Shift-JIS has not become "?")."""
    encoding = encoding if encoding in ENCODINGS else 'utf-8-sig'
    with open(path, 'w', encoding=encoding, errors='replace', newline='') as cap_file:
        cap_file.write(write(segments, fps, drop))
