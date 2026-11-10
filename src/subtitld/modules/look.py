"""A subtitle's own look: what the file it came from says it should look like.

Subtitld's look is one thing the subtitle styles own and the HTML renderer
draws. A broadcast file brings its own — a font, a size, a line height, a
box behind the text, an outline, a shadow — and what it brings is drawn:
the file's look goes over the chosen style, property by property, so a file
that states only a size keeps the style's colours rather than blanking
everything it did not mention.

A look sits on the subtitle as an optional `look` dict, the way `position`
does, and is absent unless the file said something, so a file that styles
itself nowhere is written back untouched. Its lengths are percentages of
the picture's height, as positions are percentages of the picture, so a
look means the same at any size; its colours are "#aarrggbb", as the
styles' settings are.

    font            the family, as CSS names them ("Arial, sans-serif")
    size            the letters' height
    line_height     the distance between lines, or 'normal'
    background      the box behind the text
    padding         [top, right, bottom, left] around it
    outline         {'color', 'width', 'blur'} around each letter
    shadow          [{'x', 'y', 'blur', 'color'}, ...] under the text
    wrap            'wrap' or 'nowrap'
    opacity         0 to 1, for a subtitle a file only part shows
    line_padding    space at both ends of every line (EBU-TT-D asks for it
                    so a box does not touch the letters)
    row_align       where the shorter lines of a block sit: 'start',
                    'center' or 'end'
    fill_line_gap   whether the box behind each line reaches the next, so a
                    subtitle of two lines is one block and not two stripes
                    (IMSC's itts:fillLineGap)
    ruby_reserve    room kept on every line for a reading above or below
                    it, so lines with one and lines without sit alike:
                    {'position': 'both', 'before', 'after' or 'outside',
                    'length'?}, the length the room a reading takes when it
                    is not given (TTML's tts:rubyReserve)

`css` turns one into the inline CSS the renderer draws it with, in pixels
of a 1080-tall picture, which is the stage host.js lays the subtitle on.
"""

KEYS = ('font', 'size', 'line_height', 'background', 'padding', 'outline', 'shadow', 'wrap',
        'opacity', 'line_padding', 'row_align', 'fill_line_gap', 'ruby_reserve')

# Where a reserve may keep its room: both sides of a line, or the one a
# reading goes on (TTML's <annotation-position>).
RESERVE_POSITIONS = ('both', 'before', 'after', 'outside')

# The picture the renderer's stage is: a look's percentages are of its height.
PICTURE_HEIGHT = 1080.0

_ROW_ALIGN = {'start': 'left', 'center': 'center', 'end': 'right'}


def of(segment):
    """The subtitle's look, or {} where it has none."""
    look = segment.get('look')
    return look if isinstance(look, dict) else {}


def store(segment, look):
    """Keep `look` on the subtitle, or take the key away when it says
    nothing: a subtitle no file styled carries no look at all."""
    look = {key: value for key, value in (look or {}).items()
            if key in KEYS and value is not None and value != ''}
    if look:
        segment['look'] = look
    else:
        segment.pop('look', None)
    return look


def _px(value):
    """A length in % of the picture's height, as pixels of the stage."""
    return f'{value / 100 * PICTURE_HEIGHT:.4g}px'


def css(look):
    """The look as inline CSS for the cue: {property: value}.

    Inline wins over the style's own rules for the properties it names and
    leaves the rest to the style, which is what it means for a file's look
    to go over a style property by property.
    """
    look = look if isinstance(look, dict) else {}
    out = {}
    if look.get('font'):
        out['font-family'] = str(look['font'])
    if look.get('size') is not None:
        out['font-size'] = _px(look['size'])
    if look.get('line_height') is not None:
        height = look['line_height']
        out['line-height'] = 'normal' if height == 'normal' else _px(height)
    if look.get('background'):
        out['background'] = _css_color(look['background'])
    padding = look.get('padding')
    if padding:
        out['padding'] = ' '.join(_px(side) for side in padding)
    outline = look.get('outline') or {}
    if outline.get('width'):
        # Chromium paints a stroke over the letters unless told the order,
        # which thins them; the text under its own outline keeps its shape.
        # A stroke is centred on the letter's edge, so half of it falls
        # inside; twice the thickness leaves the outer half the file asked
        # for, and the fill over it keeps the letter its own shape.
        out['-webkit-text-stroke'] = f'{_px(outline["width"] * 2)} {_css_color(outline.get("color") or "#ff000000")}'
        out['paint-order'] = 'stroke fill'
        # An outline's blur is not drawn: CSS blurs a whole element, not one
        # of its paints, and blurring the letters with it would be worse
        # than a hard edge. It is kept so the file can be written back.
    shadow = look.get('shadow') or []
    if shadow:
        out['text-shadow'] = ', '.join(
            f'{_px(one.get("x") or 0)} {_px(one.get("y") or 0)} {_px(one.get("blur") or 0)} '
            f'{_css_color(one.get("color") or "#ff000000")}' for one in shadow)
    if look.get('wrap'):
        out['white-space'] = 'pre' if look['wrap'] == 'nowrap' else 'pre-wrap'
    if look.get('opacity') is not None:
        out['opacity'] = f'{look["opacity"]:.4g}'
    if look.get('line_padding'):
        # Space at both ends of every line, inside the box: a box drawn per
        # line (box-decoration-break) is the style's business, not this.
        out['padding-left'] = _px(look['line_padding'])
        out['padding-right'] = _px(look['line_padding'])
    if look.get('row_align') in _ROW_ALIGN:
        out['text-align'] = _ROW_ALIGN[look['row_align']]
    reserve = look.get('ruby_reserve') or {}
    if isinstance(reserve, dict) and reserve.get('position') in RESERVE_POSITIONS:
        # Room for a reading on every line, as more space between lines: a
        # line's leading is shared above and below it, so the room asked for
        # is added twice, and a line that has a reading then sits where one
        # without would. That is the point of a reserve; drawing exactly one
        # side of it is more than CSS's line boxes say. With no length, the
        # room a reading takes, which is half the size of what it reads.
        room = _px(reserve['length']) if reserve.get('length') else '0.5em'
        height = look.get('line_height')
        base = _px(height) if isinstance(height, (int, float)) else '1.2em'
        out['line-height'] = f'calc({base} + 2 * {room})'
    # fill_line_gap is not drawn: the box goes behind the whole cue here, so
    # it already reaches from one line to the next and a file asking for
    # that gets it. It is kept so the file can say so again, and so a file
    # that asked for the gaps is not written back as one that did not.
    return out


def _css_color(value):
    """A "#aarrggbb" colour as CSS writes it. subtitle_styles has the same
    turn for a style's settings; a look must not import the styles."""
    value = str(value or '').strip()
    if not (value.startswith('#') and len(value) == 9):
        return value
    alpha, rgb = int(value[1:3], 16), value[3:]
    if alpha == 255:
        return f'#{rgb}'
    return f'rgba({int(rgb[0:2], 16)}, {int(rgb[2:4], 16)}, {int(rgb[4:6], 16)}, {alpha / 255:.3f})'


def style_attribute(look):
    """The look as one CSS declaration list, for a style="..." attribute."""
    return '; '.join(f'{name}: {value}' for name, value in css(look).items())
