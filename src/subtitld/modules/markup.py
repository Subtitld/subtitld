"""Formatting inside a subtitle's text: the tags SubRip uses, understood.

Subtitld keeps a subtitle's text as it is written, and that text may carry
the formatting tags SRT files use, which players such as VLC and mpv honour:

    <i>…</i>  <b>…</b>  <u>…</u>  <s>…</s>   italic, bold, underline, strikeout
    <font color="#rrggbb">…</font>           colour
    <font face="Arial">…</font>              the typeface it asks for, kept
                                             but not drawn: the style
                                             template says what it is set in
    <font background="#rrggbbaa">…</font>    a background behind the text,
                                             with an opacity if it says one
    <font size="80%">…</font>                its size against the normal one
    <font edge="uniform" edge-color="#000">  the edge drawn around it
    <sup>…</sup>  <sub>…</sub>               raised and lowered text
    <blink>…</blink>                         text that flashes, as broadcast
                                             captions flash a word
    <font kind="sound-effect">…</font>       what kind of text it is, where
                                             a file says (a speaker's name,
                                             lyrics, an expletive …)
    <font emphasis="filled sesame">…</font>  emphasis marks beside each
                                             character, as Japanese and
                                             Chinese stress a word
    <font combine="all">…</font>             set upright in one character's
                                             room, as vertical Japanese sets
                                             a number (tate-chū-yoko)
    {\\an8}  {\\i1}  …                         ASS override codes, which the
                                             same players read in SRT
    <ruby>漢字<rt>かんじ</rt></ruby>            a reading above the text
    <ruby position="after" align="start">    one below it, or spread another
                                             way across what it reads

Anything else that looks like a tag ("<3", "a < b", WebVTT's <v Name>) is
text. Tags may nest and may be left open (they run to the end); a closing
tag with nothing to close is dropped.

Beyond its colour, a run may say its size against the one the subtitle is
drawn at, whether it sits on the line or above or below it, and the edge
drawn around its letters and the colour of that edge. CEA-708's pen says
all of them; TTML has a font size and an outline. Nothing else does, so
they are left out where they cannot go.

A background is the box a run of text sits on, which broadcast captions
draw behind every character and TTML writes as tts:backgroundColor. It is
written as a <font> attribute, beside the colour, since the two go together
and a player that does not know it ignores it. Its value is "#rrggbb", or
"#rrggbbaa" where it says how opaque it is ("#00000000" being a background
asked for and then made invisible, as CEA-608's transparent code says).
This is a run's own background: the box the preview draws behind a whole
subtitle is a style template's setting (modules.subtitle_styles), not this.

A ruby is written the way HTML and WebVTT write one, which is also what
the preview draws, and it is what IMSC 1.1 calls a container holding a
base and its text: `Style.ruby` says which of the two a run is, so a
reading stays attached to what it reads through every read and write.
Where the reading sits and how it is spread across its base belong to
the container, so they are written on <ruby> and every run inside it
carries them: TTML's tts:rubyPosition (before the text, which is above it
across and to its right down; after it; outside, the default) and
tts:rubyAlign (centred by default; start, end, spread around or between).

An emphasis mark is TTML2's tts:textEmphasis and CSS's text-emphasis: a
filled or open circle, dot or sesame, or "auto", the one the writing asks
for (a filled circle across, a filled sesame down), before the text or
after it, outside being the default. Its colour and a quoted mark of its
own are left out: IMSC's text profile has neither (#textEmphasis-minimal).

A run set upright in one character's room is TTML2's tts:textCombine="all"
and CSS's text-combine-upright: a number or a short Latin word in a
subtitle written down the picture (the cue's `writing`) is laid across
the column instead of along it. In a line written across it means nothing
and is drawn as the text it is.

A face is kept as it is written and not drawn: a subtitle is set in what
its style template says, and a file that asks for Helvetica still asks for
it after a read and a write. A <font>'s size, on the other hand, is read
as a proportion of the size the subtitle is drawn at when it gives one
("80%"); a bare HTML font size ("3") says nothing about a proportion and
is left behind.

`plain` leaves readings out — they are a way of writing the text, not more
of it — so counting, speaking and translating a subtitle see what it says.
The plain-text panel's Markdown has no way to write a ruby and keeps the
tags themselves, which read back as the ruby they were.

The text is read here for each use: `plain` to count, speak and translate
it; `spans` and `to_html` to draw it; `to_ass` for ASS, which writes
formatting its own way; and `rebalance`, so both halves of a text cut in
two stay well formed.
"""

import html
import re
from typing import NamedTuple

_TOKEN = re.compile(
    r'<\s*(?P<close>/?)\s*(?P<tag>ruby|rt|blink|sub|sup|[ibus])\s*>'
    r'|<\s*font\b(?P<font>[^>]*)>'
    r'|<\s*ruby\s+(?P<ruby>[^>]*)>'
    r'|<\s*/\s*font\s*>'
    r'|\{(?P<override>\\[^{}]*)\}',
    re.IGNORECASE)
_FONT_COLOR = re.compile(r'''(?<![-\w])color\s*=\s*["']?\s*([#\w]+)''', re.IGNORECASE)
# The names a file may give the same attribute: HTML's, SAMI's and USF's.
_FONT_BACKGROUND = re.compile(r'''(?:background(?:-color)?|bgcolor|back-color)\s*=\s*["']?\s*([#\w]+)''',
                              re.IGNORECASE)
_HEX = re.compile(r'#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$')
_HEX_ALPHA = re.compile(r'#?([0-9a-fA-F]{8}|[0-9a-fA-F]{4})$')
_PERCENT = re.compile(r'^(\d+(?:\.\d+)?)\s*%$')
_FONT_SIZE = re.compile(r'''(?<![-\w])size\s*=\s*["']?\s*([\d.]+\s*%?)''', re.IGNORECASE)
_FONT_EDGE = re.compile(r'''(?<![-\w])edge\s*=\s*["']?\s*([-\w]+)''', re.IGNORECASE)
_FONT_EDGE_COLOR = re.compile(r'''edge-colou?r\s*=\s*["']?\s*([#\w]+)''', re.IGNORECASE)
# A typeface may be several words, so an unquoted one is one word only.
_FONT_FACE = re.compile(r'''face\s*=\s*(?:"([^"]*)"|'([^']*)'|([\w-]+))''', re.IGNORECASE)
_FONT_COMBINE = re.compile(r'''combine\s*=\s*["']?\s*([-\w]+)''', re.IGNORECASE)
_FONT_KIND = re.compile(r'''kind\s*=\s*["']?\s*([-\w]+)''', re.IGNORECASE)
# Several words, so quoted; one word alone may go without.
_FONT_EMPHASIS = re.compile(r'''emphasis\s*=\s*(?:"([^"]*)"|'([^']*)'|([\w-]+))''', re.IGNORECASE)
_RUBY_POSITION = re.compile(r'''position\s*=\s*["']?\s*([-\w]+)''', re.IGNORECASE)
_RUBY_ALIGN = re.compile(r'''align\s*=\s*["']?\s*([-\w]+)''', re.IGNORECASE)
# The colours HTML names that subtitle files use; others are kept, not drawn.
_NAMED = {
    'white': 'ffffff', 'black': '000000', 'red': 'ff0000', 'lime': '00ff00', 'green': '008000',
    'blue': '0000ff', 'yellow': 'ffff00', 'cyan': '00ffff', 'aqua': '00ffff', 'magenta': 'ff00ff',
    'fuchsia': 'ff00ff', 'silver': 'c0c0c0', 'gray': '808080', 'grey': '808080', 'maroon': '800000',
    'olive': '808000', 'purple': '800080', 'teal': '008080', 'navy': '000080', 'orange': 'ffa500',
}
_ASS_CODE = re.compile(r'\\(?:(?P<flag>[ibus])(?P<value>\d+)|1?c&H(?P<bgr>[0-9a-fA-F]{1,6})&?|(?P<reset>r)(?![a-z])|(?P<colour_reset>1?c)(?![&\w]))')
_ALIGN = re.compile(r'\\an?\d+')


# What a run's size is written against: the size the subtitle is drawn at.
SIZES = ('small', 'large')
# The edges CEA-708 names and CSS can draw: a bevel, an outline, a shadow.
EDGES = ('raised', 'depressed', 'uniform', 'left-shadow', 'right-shadow')
# Raised and lowered text, as HTML and WebVTT write it.
OFFSETS = {'sub': 'sub', 'sup': 'super'}
# What kind of text a run is, which CEA-708's pen says as its text tag:
# whose words they are or what sort of sound they stand for, in the order
# the standard numbers them. '' is ordinary dialogue, the first of them, and
# the three the standard leaves undefined are nothing here either.
KINDS = ('source-id', 'electronic-voice', 'other-language', 'voiceover', 'audible-translation',
         'subtitle-translation', 'voice-quality', 'lyrics', 'sound-effect', 'musical-score',
         'expletive', '', '', '', 'not-displayed')
# The one kind that changes what is drawn rather than only saying what the
# words are: the file asks for it not to be shown.
HIDDEN = 'not-displayed'

# Text that flashes. Both caption standards can ask for it and no other
# format can say it, so it is written as the tag HTML once had for it.
FLASH_TAG = 'blink'
# The class the preview's page draws it by (subtitle_styles/host.js), since
# a flash is an animation and a tag's own style cannot hold one.
FLASH_CLASS = 'subtitld-flash'
_OFFSET_TAGS = {role: tag for tag, role in OFFSETS.items()}
# An emphasis mark: filled or open, and a circle, a dot or a sesame.
EMPHASIS_FILLS = ('filled', 'open')
EMPHASIS_SHAPES = ('circle', 'dot', 'sesame')
# Where a reading or an emphasis mark sits against its text, beyond outside,
# TTML's default, which is written as nothing.
ANNOTATION_POSITIONS = ('before', 'after')
# How a reading is spread across its base, beyond centred, the default.
RUBY_ALIGNS = ('start', 'end', 'space-around', 'space-between', 'with-base')
# What a run is drawn in beyond its colours: kept together for the places
# that carry them all or none.
_PEN_FIELDS = ('size', 'offset', 'edge', 'edge_color', 'face', 'kind', 'emphasis', 'ruby_position', 'ruby_align',
               'combine')
# What a run set upright in one character's room is: all of it, or nothing.
COMBINE = 'all'


class Style(NamedTuple):
    italic: bool = False
    bold: bool = False
    underline: bool = False
    strike: bool = False
    color: str = ''          # '#rrggbb', or '' for the default
    ruby: str = ''           # '' ordinary text, 'base' read, 'text' its reading
    background: str = ''     # '#rrggbb' or '#rrggbbaa' behind the text, '' for none
    size: str = ''           # '80%' of the size it is drawn at, '' for all of it
    offset: str = ''         # '' on the line, 'super' raised, 'sub' lowered
    edge: str = ''           # one of EDGES drawn around the text, '' for none
    edge_color: str = ''     # '#rrggbb' that edge is drawn in, '' for black
    face: str = ''           # the typeface it asks for, kept but not drawn
    flash: bool = False      # text that flashes, as a caption flashes a word
    kind: str = ''           # one of KINDS: what sort of text it is, '' dialogue
    emphasis: str = ''       # its marks ('filled sesame', 'auto after'), '' none
    ruby_position: str = ''  # where the ruby it is in puts its reading, '' outside
    ruby_align: str = ''     # how that reading is spread, '' centred
    combine: str = ''        # COMBINE: set upright in one character's room down a column

    def any(self):
        return self != PLAIN


PLAIN = Style()
_FLAGS = {'i': 'italic', 'b': 'bold', 'u': 'underline', 's': 'strike'}
# What a run can be inside a ruby: what is read, and its reading.
RUBY_ROLES = ('base', 'text')
# <ruby> opens a container, and what it holds is the base until <rt>.
_RUBY = {'ruby': 'base', 'rt': 'text'}


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


# The eight typefaces CEA-708 names, which is as near as a caption comes to
# asking for one. A face read from a file is kept as it is written.
FACES = ('monospaced serif', 'proportional serif', 'monospaced sans-serif', 'proportional sans-serif',
         'casual', 'cursive', 'small capitals')


class Font(NamedTuple):
    """What one <font> tag says about a run: everything a Style holds that
    the tag carries, so one tag opens and closes for all of it."""

    color: str = ''
    background: str = ''
    size: str = ''
    edge: str = ''
    edge_color: str = ''
    face: str = ''
    kind: str = ''
    emphasis: str = ''
    combine: str = ''

    def any(self):
        return self != Font()


class Ruby(NamedTuple):
    """What a <ruby> says about the reading it holds: where it sits and how
    it is spread across its base."""

    position: str = ''
    align: str = ''


def emphasis_value(value):
    """An emphasis as markup keeps it, for one as TTML, CSS or markup writes
    it: its fill and shape, or 'auto', then 'before' or 'after' where it is
    not outside; '' for none. A colour and a quoted mark are left out (see
    the module's notes); a value with nothing but a colour is 'auto', as
    TTML reads one with no style."""
    words = re.sub(r'"[^"]*"|\'[^\']*\'', ' ', str(value or '')).replace(',', ' ').lower().split()
    if not words or words == ['none'] or ('none' in words and not set(words) & set(EMPHASIS_FILLS + EMPHASIS_SHAPES)):
        return ''
    fill = next((word for word in words if word in EMPHASIS_FILLS), '')
    shape = next((word for word in words if word in EMPHASIS_SHAPES), '')
    where = annotation_position(next((word for word in words if annotation_position(word)), ''))
    return (' '.join(part for part in (fill, shape) if part) or 'auto') + (f' {where}' if where else '')


def combine_value(value):
    """COMBINE for a run TTML or CSS sets upright in one character's room
    ('all'), '' for anything else ('none', and TTML2's digits, which IMSC
    leaves out)."""
    return COMBINE if str(value or '').strip().lower() == COMBINE else ''


def annotation_position(value):
    """'before' or 'after' for a position as TTML (before, after) or CSS
    (over, under) writes it; '' for outside, the default, or anything else."""
    return {'before': 'before', 'over': 'before', 'after': 'after', 'under': 'after'}.get(
        str(value or '').strip().lower(), '')


def ruby_align_value(value):
    """One of RUBY_ALIGNS for an alignment as TTML (spaceAround) or CSS
    (space-around) writes it; '' for centred, the default, or anything else."""
    folded = str(value or '').strip().lower().replace('-', '')
    return next((name for name in RUBY_ALIGNS if name.replace('-', '') == folded), '')


def size_percent(value):
    """A size as a percentage of the one the subtitle is drawn at ('80%'),
    or '' when it says none. A bare number is an HTML font size, which says
    nothing about a proportion, and is kept as it is written, not drawn."""
    found = _PERCENT.match(str(value or '').strip())
    if not found or float(found.group(1)) <= 0 or float(found.group(1)) == 100:
        return ''
    return _number(float(found.group(1))) + '%'


def _number(value):
    return str(int(value)) if float(value).is_integer() else str(round(float(value), 2))


def background_hex(value):
    """'#rrggbb', or '#rrggbbaa' where it says how opaque it is, for a
    background as a file writes it; '' when it is not a colour. "transparent"
    is a background made invisible, which is not the same as none at all."""
    value = (value or '').strip().lower()
    if value == 'transparent':
        return '#00000000'
    match = _HEX_ALPHA.match(value)
    if match:
        digits = match.group(1)
        if len(digits) == 4:
            digits = ''.join(c * 2 for c in digits)
        return '#' + digits
    return color_hex(value)


def css_color(value):
    """A colour as CSS takes it: '#rrggbb', or rgba() where it carries an
    opacity, which Qt's rich text reads and eight hex digits are not."""
    digits = (value or '').lstrip('#')
    if len(digits) != 8:
        return value or ''
    red, green, blue, alpha = (int(digits[at:at + 2], 16) for at in (0, 2, 4, 6))
    return f'rgba({red}, {green}, {blue}, {round(alpha / 255, 3)})'


def _tokens(text):
    """('text', str) | ('open', name, value) | ('close', name) | ('override',
    codes). A font's value is (colour, background), another tag's is ''."""
    position = 0
    for match in _TOKEN.finditer(text or ''):
        if match.start() > position:
            yield 'text', text[position:match.start()]
        position = match.end()
        if match.group('tag'):
            name = match.group('tag').lower()
            yield ('close', name) if match.group('close') else ('open', name, '')
        elif match.group('font') is not None:
            written = match.group('font')
            found = {name: pattern.search(written) for name, pattern in
                     (('color', _FONT_COLOR), ('background', _FONT_BACKGROUND), ('size', _FONT_SIZE),
                      ('edge', _FONT_EDGE), ('edge_color', _FONT_EDGE_COLOR), ('face', _FONT_FACE),
                      ('kind', _FONT_KIND), ('emphasis', _FONT_EMPHASIS), ('combine', _FONT_COMBINE))}
            edge = (found['edge'].group(1).lower() if found['edge'] else '')
            kind = (found['kind'].group(1).lower() if found['kind'] else '')
            face = next((part for part in found['face'].groups() if part), '') if found['face'] else ''
            emphasis = next((part for part in found['emphasis'].groups() if part), '') if found['emphasis'] else ''
            yield ('open', 'font', Font(
                color=color_hex(found['color'].group(1)) if found['color'] else '',
                background=background_hex(found['background'].group(1)) if found['background'] else '',
                size=size_percent(found['size'].group(1)) if found['size'] else '',
                edge=edge if edge in EDGES else '',
                edge_color=color_hex(found['edge_color'].group(1)) if found['edge_color'] else '',
                face=face.strip(),
                kind=kind if kind in KINDS else '',
                emphasis=emphasis_value(emphasis),
                combine=combine_value(found['combine'].group(1) if found['combine'] else '')))
        elif match.group('ruby') is not None:
            written = match.group('ruby')
            placed, spread = _RUBY_POSITION.search(written), _RUBY_ALIGN.search(written)
            yield ('open', 'ruby', Ruby(annotation_position(placed.group(1)) if placed else '',
                                        ruby_align_value(spread.group(1)) if spread else ''))
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
            name, value = token[1], token[2]
            self.open.append((name, value, self.style, written))
            if name == 'font':
                said = {field: held for field, held in (value or Font())._asdict().items() if held}
                self.style = self.style._replace(**said)
            elif name == 'ruby':
                # A container: what it says about its reading goes to every
                # run in it, so it is there whichever of them is written out.
                said = value or Ruby()
                self.style = self.style._replace(ruby=_RUBY[name], ruby_position=said.position,
                                                 ruby_align=said.align)
            elif name in _RUBY:
                self.style = self.style._replace(ruby=_RUBY[name])
            elif name in OFFSETS:
                self.style = self.style._replace(offset=OFFSETS[name])
            elif name == FLASH_TAG:
                self.style = self.style._replace(flash=True)
            else:
                self.style = self.style._replace(**{_FLAGS[name]: True})
        elif kind == 'close':
            name = token[1]
            for index in range(len(self.open) - 1, -1, -1):
                if self.open[index][0] == name:
                    previous = self.open[index][2]
                    del self.open[index]
                    if name == 'font':
                        self.style = self.style._replace(**{field: getattr(previous, field)
                                                            for field in Font()._fields})
                    elif name in OFFSETS:
                        self.style = self.style._replace(offset=previous.offset)
                    elif name == FLASH_TAG:
                        self.style = self.style._replace(flash=previous.flash)
                    elif name == 'ruby':
                        # </ruby> leaves the ruby altogether, and what it said
                        # about its reading with it.
                        self.style = self.style._replace(ruby=previous.ruby, ruby_position=previous.ruby_position,
                                                         ruby_align=previous.ruby_align)
                    elif name in _RUBY:
                        # </rt> leaves the reading, so what follows is the
                        # container's base again.
                        self.style = self.style._replace(ruby=previous.ruby)
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


def ruby_groups(runs):
    """The runs in order as ['plain', runs] and ['ruby', base, text] groups.
    A base after a reading starts the next ruby; the runs inside a group
    carry no role of their own any more."""
    groups = []
    for run, style in runs:
        role = style.ruby if style.ruby in _RUBY.values() else ''
        if not role:
            if groups and groups[-1][0] == 'plain':
                groups[-1][1].append((run, style))
            else:
                groups.append(['plain', [(run, style)]])
            continue
        if not groups or groups[-1][0] != 'ruby' or (role == 'base' and groups[-1][2]):
            groups.append(['ruby', [], []])
        groups[-1][1 if role == 'base' else 2].append((run, style._replace(ruby='')))
    return groups


def ruby_attributes(group):
    """Ruby(position, align) for a ['ruby', base, text] group: what its
    container says, which every run in it carries; the reading's own first,
    since TTML may say a position on the reading alone."""
    for _run, style in group[2] + group[1]:
        if style.ruby_position or style.ruby_align:
            return Ruby(style.ruby_position, style.ruby_align)
    return Ruby()


def _ruby_open(said):
    """The <ruby> tag for what a container says."""
    attributes = (f' position="{said.position}"' if said.position else '') + \
                 (f' align="{said.align}"' if said.align else '')
    return f'<ruby{attributes}>'


def without_readings(runs):
    """`runs` with the readings left out and their bases ordinary text, for
    a format with nowhere to put a reading. `plain`'s rule: a reading is a
    way of writing the text, not more of it."""
    return [(run, style._replace(ruby='')) for run, style in runs if style.ruby != 'text']


def plain(text):
    """The text as it reads: without its tags and override codes, and
    without a ruby's readings, which say how the text is read, not what
    it says."""
    if not has_markup(text):
        return text or ''
    out = []
    for group in ruby_groups(spans(text)):
        if group[0] == 'plain':
            out.extend(run for run, _style in group[1])
            continue
        # A reading with nothing to read is the text itself, as from_spans
        # writes it back: a lone <rt> is not a ruby.
        base = ''.join(run for run, _style in group[1])
        out.append(base or ''.join(run for run, _style in group[2]))
    return ''.join(out)


def to_html(text, colors=True):
    """The text as HTML: the subset Qt's rich text draws, and the ruby the
    preview's renderer draws (Qt shows a reading beside its base instead,
    which is as near as its rich text goes). Without `colors`, every run
    takes the default colour (for a shadow)."""
    def chunk_of(run, style):
        if style.kind == HIDDEN:
            # The file says this is not to be shown. It stays in the text,
            # where it can be read and edited; it is only not drawn.
            return ''
        chunk = html.escape(run, quote=False).replace('\n', '<br/>')
        if style.offset in _OFFSET_TAGS:
            chunk = f'<{_OFFSET_TAGS[style.offset]}>{chunk}</{_OFFSET_TAGS[style.offset]}>'
        if style.flash:
            # An animation cannot go in a style attribute: the page it is
            # drawn on gives the class its keyframes.
            chunk = f'<span class="{FLASH_CLASS}">{chunk}</span>'
        if style.strike:
            chunk = f'<s>{chunk}</s>'
        if style.underline:
            chunk = f'<u>{chunk}</u>'
        if style.bold:
            chunk = f'<b>{chunk}</b>'
        if style.italic:
            chunk = f'<i>{chunk}</i>'
        looks = ([f'color:{style.color}'] if colors and style.color else []) + \
               ([f'background-color:{css_color(style.background)}'] if style.background else []) + \
               ([f'font-size:{style.size}'] if style.size else []) + \
               ([f'text-shadow:{_shadow(style)}'] if colors and style.edge else []) + \
               _emphasis_css(style.emphasis) + \
               (['text-combine-upright:all'] if style.combine else [])
        if looks:
            chunk = f'<span style="{";".join(looks)}">{chunk}</span>'
        return chunk

    parts = []
    for group in ruby_groups(spans(text)):
        if group[0] == 'plain':
            parts.extend(chunk_of(run, style) for run, style in group[1])
            continue
        base = ''.join(chunk_of(run, style) for run, style in group[1])
        reading = ''.join(chunk_of(run, style) for run, style in group[2])
        if not (base and reading):
            parts.append(base + reading)
            continue
        placed = _ruby_css(ruby_attributes(group))
        parts.append(f'<ruby style="{placed}">' if placed else '<ruby>')
        parts.append(f'{base}<rt>{reading}</rt></ruby>')
    return ''.join(parts)


# A reading's place as CSS says it: over is before the text (above it, or
# right of it down), under is after. Outside, the default, is over.
_RUBY_CSS_POSITION = {'before': 'over', 'after': 'under'}
# The ones CSS can spread a reading by; end and with-base it has no word
# for, so those draw centred, and are kept for the file all the same.
_RUBY_CSS_ALIGN = {'start': 'start', 'space-around': 'space-around', 'space-between': 'space-between'}


def _ruby_css(said):
    """The inline CSS for a <ruby> that says where its reading goes."""
    looks = ([f'ruby-position:{_RUBY_CSS_POSITION[said.position]}'] if said.position in _RUBY_CSS_POSITION else []) + \
            ([f'ruby-align:{_RUBY_CSS_ALIGN[said.align]}'] if said.align in _RUBY_CSS_ALIGN else [])
    return ';'.join(looks)


def _emphasis_css(emphasis):
    """CSS for an emphasis: text-emphasis and where its marks go. "auto" is
    CSS's bare "filled", whose shape follows the writing as TTML's auto
    does; before is over the text (and right of it down), after is under."""
    if not emphasis:
        return []
    words = emphasis.split()
    where = words[-1] if words[-1] in ANNOTATION_POSITIONS else ''
    marks = ' '.join(word for word in words if word not in ANNOTATION_POSITIONS)
    return [f'text-emphasis:{"filled" if marks == "auto" else marks}',
            f'text-emphasis-position:{"under left" if where == "after" else "over right"}']


# How each edge is drawn: offsets in em, around the text for an outline and
# a bevel, to one side for a shadow. CEA-708 names them; CSS draws them.
_SHADOWS = {
    'uniform': ((-0.06, 0), (0.06, 0), (0, -0.06), (0, 0.06)),
    'raised': ((-0.06, -0.06),),
    'depressed': ((0.06, 0.06),),
    'left-shadow': ((-0.08, 0.08),),
    'right-shadow': ((0.08, 0.08),),
}


def _shadow(style):
    """A CSS text-shadow for a Style's edge, in the colour it asks for,
    else black."""
    color = style.edge_color or '#000000'
    return ', '.join(f'{across}em {down}em 0 {color}' for across, down in _SHADOWS.get(style.edge, ()))


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
    already has kept, line breaks as \\N. ASS has no ruby, so a reading is
    left out rather than run into the text it reads."""
    reader = _Reader()
    out = []
    for token, written in _written_tokens(text):
        if token[0] == 'text':
            if reader.style.ruby == 'text':
                continue
            out.append(token[1].replace('\n', '\\N'))
        elif token[0] == 'override':
            out.append(written)
            reader.step(token)
        else:
            before = reader.style
            reader.step(token)
            out.append(_ass_changes(before, reader.style))
    return ''.join(out)


# Outermost first, when opened together.
TAG_ORDER = ('font', FLASH_TAG, 'sup', 'sub', 'i', 'b', 'u', 's')


def tags_for(style):
    """The tags a run in `style` sits in: [(name, value)]. A font's value is
    (colour, background), so one tag carries both."""
    font = Font(style.color, style.background, style.size, style.edge, style.edge_color, style.face,
                style.kind, style.emphasis, style.combine)
    wanted = [('font', font)] if font.any() else []
    if style.flash:
        wanted.append((FLASH_TAG, True))
    if style.offset in _OFFSET_TAGS:
        wanted.append((_OFFSET_TAGS[style.offset], True))
    return wanted + [(name, True) for name in ('i', 'b', 'u', 's') if getattr(style, _FLAGS[name])]


_FONT_ATTRIBUTES = (('color', 'color'), ('background', 'background'), ('size', 'size'),
                    ('edge', 'edge'), ('edge_color', 'edge-color'), ('face', 'face'), ('kind', 'kind'),
                    ('emphasis', 'emphasis'), ('combine', 'combine'))


def _open_tag(name, value):
    if name != 'font':
        return f'<{name}>'
    attributes = ''.join(f' {written}="{getattr(value, field)}"' for field, written in _FONT_ATTRIBUTES
                         if getattr(value, field))
    return f'<font{attributes}>'


def _nested(runs):
    """Text with tags for runs of one stretch: well nested, a tag opened
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


def from_spans(runs):
    """Text with tags for [(text, Style)] runs. A ruby is written around
    its own runs, so the formatting inside it is closed before it ends; a
    base with no reading, or a reading with no base, is written as the text
    it is."""
    out = []
    for group in ruby_groups(runs):
        if group[0] == 'plain':
            out.append(_nested(group[1]))
            continue
        base, reading = _nested(group[1]), _nested(group[2])
        out.append(f'{_ruby_open(ruby_attributes(group))}{base}<rt>{reading}</rt></ruby>'
                   if base and reading else base + reading)
    return ''.join(out)


def tidy(runs, spaces=' \t\r'):
    """Text with tags for [(text, Style)] runs read from a markup format
    (TTML, SAMI): each of `spaces` a single space, none at the ends of a
    line, no empty line, nothing before the first line or after the last."""
    out = []
    for text, style in runs:
        for char in text:
            if char in spaces:
                if out and out[-1][0] not in ' \n':
                    out.append((' ', style))
            elif char == '\n':
                while out and out[-1][0] == ' ':
                    out.pop()
                if out and out[-1][0] != '\n':
                    out.append(('\n', style))
            else:
                out.append((char, style))
    while out and out[-1][0] in ' \n':
        out.pop()
    grouped = []
    for char, style in out:
        if grouped and grouped[-1][1] == style:
            grouped[-1][0].append(char)
        else:
            grouped.append(([char], style))
    return from_spans([(''.join(chars), style) for chars, style in grouped])


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
#     [flashing]{.flash}
#     [on a box]{background=#000000ff}
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
        elif token[1] in _RUBY:
            # Markdown has no ruby: the tags are kept as they are written,
            # and read back as the ruby they were.
            pieces.append((written, reader.style, True))
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
    if style.flash:
        attrs.append('.flash')
    if style.color:
        attrs.append(f'color={style.color}')
    if style.background:
        attrs.append(f'background={style.background}')
    if style.size:
        attrs.append(f'size={style.size}')
    if style.edge:
        attrs.append(f'edge={style.edge}')
    if style.edge_color:
        attrs.append(f'edge-color={style.edge_color}')
    if style.face:
        attrs.append(f'face="{style.face}"')
    if style.kind:
        attrs.append(f'kind={style.kind}')
    if style.emphasis:
        attrs.append(f'emphasis="{style.emphasis}"')
    if style.combine:
        attrs.append(f'combine={style.combine}')
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


_MD_ATTR = re.compile(
    r'''\.underline\b|(?P<flash>\.flash)\b'''
    r'''|(?:background(?:-color)?|bgcolor)\s*=\s*["']?(?P<back>[#\w]+)["']?'''
    r'''|(?<![-\w])color\s*=\s*["']?(?P<fore>[#\w]+)["']?'''
    r'''|style\s*=\s*["'][^"']*?(?<![-\w])color\s*:\s*(?P<css>[#\w]+)[^"']*["']'''
    r'''|(?<![-\w])size\s*=\s*["']?(?P<size>[\d.]+\s*%)["']?'''
    r'''|edge-colou?r\s*=\s*["']?(?P<edgecolor>[#\w]+)["']?'''
    r'''|(?<![-\w])edge\s*=\s*["']?(?P<edge>[-\w]+)["']?'''
    r'''|face\s*=\s*(?:"(?P<facequoted>[^"]*)"|(?P<face>[\w-]+))'''
    r'''|kind\s*=\s*["']?(?P<kind>[-\w]+)["']?'''
    r'''|emphasis\s*=\s*(?:"(?P<emphasisquoted>[^"]*)"|(?P<emphasis>[\w-]+))'''
    r'''|combine\s*=\s*["']?(?P<combine>[-\w]+)["']?''')


def _md_span_style(attrs):
    """What a span's {attributes} change, as {field: value}; None when it
    says nothing this knows (then it is text)."""
    change = {}
    for match in _MD_ATTR.finditer(attrs):
        if match.group(0).startswith('.underline'):
            change['underline'] = True
        elif match.group('flash'):
            change['flash'] = True
        elif match.group('back'):
            background = background_hex(match.group('back'))
            if background:
                change['background'] = background
        elif match.group('size'):
            size = size_percent(match.group('size'))
            if size:
                change['size'] = size
        elif match.group('edgecolor'):
            edge_color = color_hex(match.group('edgecolor'))
            if edge_color:
                change['edge_color'] = edge_color
        elif match.group('edge'):
            if match.group('edge').lower() in EDGES:
                change['edge'] = match.group('edge').lower()
        elif match.group('facequoted') or match.group('face'):
            change['face'] = (match.group('facequoted') or match.group('face')).strip()
        elif match.group('kind'):
            if match.group('kind').lower() in KINDS:
                change['kind'] = match.group('kind').lower()
        elif match.group('emphasisquoted') or match.group('emphasis'):
            emphasis = emphasis_value(match.group('emphasisquoted') or match.group('emphasis'))
            if emphasis:
                change['emphasis'] = emphasis
        elif match.group('combine'):
            if combine_value(match.group('combine')):
                change['combine'] = COMBINE
        else:
            color = color_hex(match.group('fore') or match.group('css'))
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
