"""SubStation Alpha (.ssa) and Advanced SubStation Alpha (.ass): read the
files subtitlers and fansubbers write, write what players show.

pysubs2 reads and writes the files' sections; this module turns their
events into Subtitld's subtitles and back.

Reading:

- the bytes are decoded as SRT's are (a byte order mark, else UTF-8, else
  the detected encoding);
- Comment lines and drawings ({\\p1} ... {\\p0}) are not subtitles;
- an event's style gives its whole text italic, bold, underline,
  strikeout, a colour other than white and a place on the screen; the
  override codes in the text change them as it goes ({\\i1}, {\\b1},
  {\\u1}, {\\s1}, {\\c&H...&}, {\\r} back to the style), and {\\anN} (or
  SSA's {\\aN}) places it. What Subtitld has no place for ({\\pos},
  {\\fad}, karaoke, fonts, borders ...) goes;
- \\N is a line break; \\n one only under "WrapStyle: 2", a space
  otherwise; \\h a no-break space;
- the event's Name (the actor) is the subtitle's speaker;
- the subtitles come sorted by start.

Writing gives a script sized to the video (PlayResX/PlayResY), with one
style, Default, scaled to that height: white with a black outline, or the
look of Subtitld's preview (its font, colour, shadow and background box).
The subtitles go in order of start, the speaker as the Name, formatting
as override codes, line breaks as \\N. A file named .ssa is written as
SubStation Alpha v4, with SSA's codes for the position.
"""

import re

import pysubs2

from subtitld.modules import markup
from subtitld.modules.srt import decode

_BLOCK = re.compile(r'\{([^{}]*)\}')
_CODE = re.compile(r'\\(?:(?P<flag>[ibus])(?P<value>\d+)|(?P<cn>1?c)(?:&H(?P<bgr>[0-9a-fA-F]{1,8})&?)?(?![\w])|'
                   r'(?P<reset>r)(?P<style>[^\\]*)|an(?P<an>\d)|a(?P<a>\d{1,2})(?!\w)|p(?P<p>\d+))')
_LEGACY_ALIGN = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}
_TO_LEGACY = {value: key for key, value in _LEGACY_ALIGN.items()}

LOOKS = ('clean', 'preview')


def _color_hex(color):
    return '#{:02x}{:02x}{:02x}'.format(color.r, color.g, color.b)


def _style_look(style):
    """(Style, keypad) a pysubs2 style gives its events' text."""
    if style is None:
        return markup.PLAIN, 2
    color = _color_hex(style.primarycolor)
    look = markup.Style(bool(style.italic), bool(style.bold), bool(style.underline), bool(style.strikeout),
                        '' if color == '#ffffff' else color)
    return look, int(style.alignment) if 1 <= int(style.alignment) <= 9 else 2


def _text(event, styles, wrap):
    """(text with tags, keypad) for an event, or None for a drawing."""
    base, keypad = _style_look(styles.get(event.style) or styles.get('Default'))
    style = base
    keypad_set = None
    drawing = False
    runs = []
    position = 0

    def add(text):
        if drawing or not text:
            return
        text = text.replace('\\N', '\n').replace('\\h', '\u00a0').replace('\\n', '\n' if wrap == 2 else ' ')
        runs.append((text, style))

    for block in _BLOCK.finditer(event.text):
        add(event.text[position:block.start()])
        position = block.end()
        for code in _CODE.finditer(block.group(1)):
            if code.group('flag'):
                field = {'i': 'italic', 'b': 'bold', 'u': 'underline', 's': 'strike'}[code.group('flag')]
                value = int(code.group('value'))
                style = style._replace(**{field: value >= 600 if field == 'bold' and value > 1 else value == 1})
            elif code.group('cn'):
                if code.group('bgr'):
                    bgr = code.group('bgr')[-6:].rjust(6, '0')
                    rgb = '#' + (bgr[4:6] + bgr[2:4] + bgr[0:2]).lower()
                    style = style._replace(color='' if rgb == '#ffffff' else rgb)
                else:
                    style = style._replace(color=base.color)
            elif code.group('reset'):
                # Back to the event's style, or to another one, its place too.
                named = styles.get(code.group('style').strip()) if code.group('style').strip() else None
                style, named_keypad = _style_look(named) if named is not None else (base, None)
                if named_keypad is not None:
                    keypad = named_keypad
            elif code.group('an') and keypad_set is None and 1 <= int(code.group('an')) <= 9:
                keypad_set = int(code.group('an'))
            elif code.group('a') and keypad_set is None and int(code.group('a')) in _LEGACY_ALIGN:
                keypad_set = _LEGACY_ALIGN[int(code.group('a'))]
            elif code.group('p') is not None:
                drawing = int(code.group('p')) > 0
    add(event.text[position:])

    # Line breaks trimmed of the spaces around them, none doubled, none at the ends.
    tidy = []
    for text, run_style in runs:
        text = re.sub(r'[ \t]*\n[ \t]*', '\n', text)
        text = re.sub(r'\n{2,}', '\n', text)
        tidy.append((text, run_style))
    while tidy and not tidy[0][0].lstrip(' \t\n'):
        tidy.pop(0)
    while tidy and not tidy[-1][0].rstrip(' \t\n'):
        tidy.pop()
    if tidy:
        tidy[0] = (tidy[0][0].lstrip(' \t\n'), tidy[0][1])
        tidy[-1] = (tidy[-1][0].rstrip(' \t\n'), tidy[-1][1])
    text = markup.from_spans(tidy)
    if not markup.plain(text).strip():
        return None
    keypad = keypad_set or keypad
    return (markup.with_alignment('', keypad) if keypad != 2 else '') + text


def segments_of(subfile):
    """Subtitld's subtitles for a pysubs2 file, sorted by start."""
    try:
        wrap = int(str(subfile.info.get('WrapStyle', '0')).strip() or 0)
    except ValueError:
        wrap = 0
    segments = []
    for event in subfile.events:
        if event.is_comment:
            continue
        text = _text(event, subfile.styles, wrap)
        if text is None:
            continue
        start = round(event.start / 1000, 3)
        segment = {'start': start, 'end': max(start, round(event.end / 1000, 3)), 'text': text}
        if (event.name or '').strip():
            segment['speaker'] = event.name.strip()
        segments.append(segment)
    segments.sort(key=lambda segment: segment['start'])
    return segments


def read(path):
    """The subtitles in the .ass or .ssa file at `path`."""
    with open(path, 'rb') as ass_file:
        text = decode(ass_file.read())
    return segments_of(pysubs2.SSAFile.from_string(text))


def _argb(value, default):
    """pysubs2 Color for a '#rrggbb' or '#aarrggbb' (Qt's) colour; ASS's
    alpha counts the other way (0 opaque)."""
    value = str(value or default).lstrip('#')
    if len(value) == 6:
        value = 'ff' + value
    alpha, red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4, 6))
    return pysubs2.Color(red, green, blue, 255 - alpha)


def default_style(height, look='clean', preview=None):
    """The Default style for a script `height` pixels tall: 'clean' white
    with a black outline, or 'preview', Subtitld's preview settings
    (`preview`, CONFIG['videoplayer']): its font, colour, shadow and
    background box."""
    scale = height / 1080
    style = pysubs2.SSAStyle(
        fontname='Arial', fontsize=round(60 * scale), primarycolor=pysubs2.Color(255, 255, 255, 0),
        outlinecolor=pysubs2.Color(0, 0, 0, 0), backcolor=pysubs2.Color(0, 0, 0, 128),
        outline=round(3 * scale, 1), shadow=round(1 * scale, 1), borderstyle=1,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=round(96 * scale), marginr=round(96 * scale), marginv=round(54 * scale))
    if look == 'preview':
        preview = preview or {}
        style.fontname = preview.get('font_family') or 'Arial'
        style.primarycolor = _argb(preview.get('color'), '#ffffffff')
        style.outline = 0
        style.shadow = round(float(preview.get('shadow_x', 2)) * scale, 1) if preview.get('shadow_enabled', True) else 0
        style.backcolor = _argb(preview.get('shadow_color'), '#ff000000')
        if preview.get('backgroundbox_enabled', True):
            # An opaque box, as the preview draws one: its colour, and its
            # padding as the box's outline.
            style.borderstyle = 3
            style.outlinecolor = _argb(preview.get('backgroundbox_color'), '#55000000')
            style.outline = round(float(preview.get('backgroundbox_padding', 10)) * scale, 1)
            # With a box, ASS's shadow is a second box (in the shadow's
            # colour) behind it, which would hide the first one's
            # transparency: none.
            style.shadow = 0
    return style


def write(segments, width=None, height=None, fmt='ass', look='clean', preview=None):
    """ASS (or, with fmt 'ssa', SSA v4) text for `segments`."""
    width, height = int(width or 1920), int(height or 1080)
    subfile = pysubs2.SSAFile()
    subfile.info['PlayResX'] = str(width)
    subfile.info['PlayResY'] = str(height)
    subfile.info['ScaledBorderAndShadow'] = 'yes'
    subfile.styles['Default'] = default_style(height, look, preview)
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
        if not markup.plain(rest).strip():
            continue
        text = markup.to_ass(lead + rest)
        if fmt == 'ssa':
            # SSA places a line with its own numbers: \a6 is top centre.
            text = re.sub(r'\\an(\d)', lambda m: f'\\a{_TO_LEGACY.get(int(m.group(1)), 2)}', text)
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        subfile.append(pysubs2.SSAEvent(start=int(round(start * 1000)), end=int(round(end * 1000)), text=text,
                                        name=str(segment.get('speaker') or '').strip()))
    return subfile.to_string(fmt)


def save(path, segments, width=None, height=None, look='clean', preview=None):
    """Write `segments` to `path`: SubStation Alpha v4 when it ends in .ssa,
    Advanced SubStation Alpha otherwise. UTF-8."""
    fmt = 'ssa' if str(path).lower().endswith('.ssa') else 'ass'
    with open(path, 'w', encoding='utf-8', newline='\n') as ass_file:
        ass_file.write(write(segments, width, height, fmt, look, preview))
