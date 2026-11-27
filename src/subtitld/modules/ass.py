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
  {\\fad} ...) goes; karaoke codes time it syllable by syllable
  (modules.karaoke);
- the look a line's style gives it beyond the file's own baseline — the
  style named Default, else the one most lines use — is the subtitle's
  look (modules.look): its font, size, outline, shadow, or the opaque box
  BorderStyle 3 draws. So are \\fn, \\fs, \\bord, \\shad, \\3c and \\4c in
  the code block a line starts with; further in, they would change only
  part of it, which a look cannot. The baseline itself is not a look:
  it is what the chosen style is for, and Subtitld's own scripts state
  one for the whole file;
- \\N is a line break; \\n one only under "WrapStyle: 2", a space
  otherwise; \\h a no-break space;
- the event's Name (the actor) is the subtitle's speaker;
- the subtitles come sorted by start;
- the fonts and pictures a script carries ([Fonts], [Graphics]) are the
  project's attachments: read_file gives them, decoded, as {'fonts':
  {name: bytes}, 'graphics': {name: bytes}}.

Writing gives a script sized to the video (PlayResX/PlayResY), with a
style, Default, scaled to that height: white with a black outline, or the
look of Subtitld's preview (its font, colour, shadow and background box).
A subtitle with a look of its own gets a style of its own, Default with
the look laid over it, one for each look the subtitles use: what ASS has
words for (font, size, outline, shadow, a box and its padding) and nothing
of what it has none for (line height, opacity, wrapping per line).
The subtitles go in order of start, the speaker as the Name, formatting
as override codes, line breaks as \\N. A file named .ssa is written as
SubStation Alpha v4, with SSA's codes for the position. The project's
attachments go back in [Fonts] and [Graphics], encoded again as they came,
so a script opened and saved keeps its fonts byte for byte.

The attachments' encoding is SSA v4's (its specification, "Appendix B -
embedded font/picture encoding", which Aegisub's ass_attachment and libass
follow): a group of three bytes as four characters, six bits each plus
33, a last group of one or two bytes as two or three characters; lines of
80 characters; under "fontname: <name>" in [Fonts] and "filename: <name>"
in [Graphics]. pysubs2 keeps the lines as they stand.
"""

import json
import re

import pysubs2

from subtitld.modules import karaoke, look as subtitld_look, markup, position
from subtitld.modules.srt import decode

_BLOCK = re.compile(r'\{([^{}]*)\}')
_CODE = re.compile(r'\\(?:(?P<flag>[ibus])(?P<value>\d+)|(?P<cn>1?c)(?:&H(?P<bgr>[0-9a-fA-F]{1,8})&?)?(?![\w])|'
                   r'(?P<reset>r)(?P<style>[^\\]*)|an(?P<an>\d)|a(?P<a>\d{1,2})(?!\w)|p(?P<p>\d+)|'
                   r'fr(?P<axis>[xyz])(?P<turn>-?\d+(?:\.\d+)?)|(?P<karaoke>kf|ko|k|K)(?P<length>\d+))')
# The codes a line's look comes from, in the block it starts with.
_LOOK_CODE = re.compile(r'\\(?:fn(?P<fn>[^\\]+)|fs(?P<fs>\d+(?:\.\d+)?)|bord(?P<bord>\d+(?:\.\d+)?)|'
                        r'shad(?P<shad>\d+(?:\.\d+)?)|(?P<which>[34])c&H(?P<bgr>[0-9a-fA-F]{1,8})&?)')
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
    """((text with tags, its place as a code), the rotations the line is
    turned by, its karaoke codes) for an event, or None for a drawing. The
    text has karaoke.MARK where each karaoke code stood, the codes their
    lengths and effects (karaoke.marked_pieces)."""
    base, keypad = _style_look(styles.get(event.style) or styles.get('Default'))
    style = base
    keypad_set = None
    drawing = False
    runs = []
    turns = {}
    marks = []
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
            elif code.group('karaoke') and not drawing:
                runs.append((karaoke.MARK, style))
                effect = karaoke.effect_of(code.group('karaoke'))
                marks.append((int(code.group('length')) / 100, {'effect': effect} if effect else {}))
            elif code.group('axis'):
                # A rotation the whole line is turned by; one inside the
                # text turns the rest of it, which Subtitld cannot keep,
                # so the first is the subtitle's.
                turns.setdefault(code.group('axis'), float(code.group('turn')))
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
    return (markup.with_alignment('', keypad) if keypad != 2 else '') + text, turns, marks


def _hex(color):
    """A pysubs2 Color as a look's "#aarrggbb": ASS counts alpha the other
    way, 0 for opaque."""
    return f'#{255 - color.a:02x}{color.r:02x}{color.g:02x}{color.b:02x}'


def _look_of_style(style, height):
    """What a style says a line looks like, as a look holds it: lengths in
    % of the script's height, colours as "#aarrggbb". BorderStyle 3 draws
    an opaque box in the outline's colour, padded by the outline's width;
    1 draws an outline and a shadow."""
    if style is None:
        return {}
    found = {'font': style.fontname, 'size': float(style.fontsize) * 100 / height}
    if int(style.borderstyle) == 3:
        found['background'] = _hex(style.outlinecolor)
        found['padding'] = [float(style.outline) * 100 / height] * 4
    else:
        if style.outline:
            found['outline'] = {'width': float(style.outline) * 100 / height, 'color': _hex(style.outlinecolor)}
        if style.shadow:
            depth = float(style.shadow) * 100 / height
            found['shadow'] = [{'x': depth, 'y': depth, 'color': _hex(style.backcolor)}]
    return found


def _with_codes(found, text, height):
    """`found` changed by the look codes in the block a line starts with."""
    lead = _BLOCK.match(text)
    if not lead:
        return found
    found = dict(found)
    for code in _LOOK_CODE.finditer(lead.group(1)):
        if code.group('fn'):
            found['font'] = code.group('fn').strip()
        elif code.group('fs'):
            found['size'] = float(code.group('fs')) * 100 / height
        elif code.group('bord') is not None and 'background' not in found:
            width = float(code.group('bord')) * 100 / height
            if width:
                found['outline'] = dict(found.get('outline') or {}, width=width)
            else:
                found.pop('outline', None)
        elif code.group('shad') is not None and 'background' not in found:
            depth = float(code.group('shad')) * 100 / height
            if depth:
                kept = (found.get('shadow') or [{}])[0]
                found['shadow'] = [dict(kept, x=depth, y=depth)]
            else:
                found.pop('shadow', None)
        elif code.group('which'):
            bgr = code.group('bgr')[-6:].rjust(6, '0')
            colour = '#ff' + (bgr[4:6] + bgr[2:4] + bgr[0:2]).lower()
            if code.group('which') == '3':
                if 'background' in found:
                    found['background'] = colour
                elif found.get('outline'):
                    found['outline'] = dict(found['outline'], color=colour)
            elif found.get('shadow'):
                found['shadow'] = [dict(found['shadow'][0], color=colour)]
    return found


def _opening_style(event, styles):
    """The style a line is drawn in from its start: its own, or the one a
    {\\rName} in the block it starts with switches it to."""
    lead = _BLOCK.match(event.text)
    if lead:
        switched = re.search(r'\\r([^\\}]+)', lead.group(1))
        if switched and switched.group(1).strip() in styles:
            return switched.group(1).strip()
    return event.style


def _baseline(subfile):
    """The style that is the file's own look rather than any line's: the
    one named Default, else the one most lines use."""
    if 'Default' in subfile.styles:
        return 'Default'
    used = [event.style for event in subfile.events if not event.is_comment]
    return max(set(used), key=used.count) if used else None


def _play_res(subfile):
    """The picture a script's lengths are counted in."""
    def number(key, fallback):
        try:
            return int(float(str(subfile.info.get(key, '')).strip() or fallback)) or fallback
        except ValueError:
            return fallback
    return number('PlayResX', 1920), number('PlayResY', 1080)


def _as_percent(pixels, size):
    """A margin in pixels of the script as the percentage USF writes, or
    '' for none."""
    if not pixels or not size:
        return ''
    percent = round(float(pixels) * 100.0 / float(size), 3)
    return f'{int(percent)}%' if float(percent).is_integer() else f'{percent}%'


def segments_of(subfile):
    """Subtitld's subtitles for a pysubs2 file, sorted by start."""
    try:
        wrap = int(str(subfile.info.get('WrapStyle', '0')).strip() or 0)
    except ValueError:
        wrap = 0
    width, height = _play_res(subfile)
    baseline = _baseline(subfile)
    base_look = _look_of_style(subfile.styles.get(baseline), height)
    segments = []
    for event in subfile.events:
        if event.is_comment:
            continue
        read_text = _text(event, subfile.styles, wrap)
        if read_text is None:
            continue
        text, turns, marks = read_text
        start = round(event.start / 1000, 3)
        segment = {'start': start, 'end': max(start, round(event.end / 1000, 3)), 'text': text}
        if (event.name or '').strip():
            segment['speaker'] = event.name.strip()
        # An event's own margins stand it off the edge it is aligned to;
        # 0 means the style's, which is where a subtitle sits by default.
        keypad = markup.alignment(text)
        across = (event.marginl if (keypad - 1) % 3 == 0 else
                  event.marginr if (keypad - 1) % 3 == 2 else 0)
        placed = position.Place(
            h=_as_percent(across, width), v=_as_percent(event.marginv, height),
            rotate=tuple(float(turns.get(axis) or 0) for axis in position.AXES))
        if placed.any():
            position.store(segment, placed)
        # Its look: what its style and its opening codes say beyond the
        # file's baseline, so a file of Default lines brings none.
        style_name = _opening_style(event, subfile.styles)
        styled = _with_codes(_look_of_style(subfile.styles.get(style_name) or subfile.styles.get(baseline),
                                            height), event.text, height)
        subtitld_look.store(segment, {key: value for key, value in styled.items() if base_look.get(key) != value})
        if karaoke.MARK in text:
            # Its karaoke: the subtitle timed syllable by syllable.
            segment['text'] = karaoke.marked_pieces(text, marks) or text.replace(karaoke.MARK, '')
        segments.append(segment)
    segments.sort(key=lambda segment: segment['start'])
    return segments


def is_v4plusplus(text):
    """Whether this is SSA v4.00++, the format MPC's splitter writes for
    the timed text of an MP4 and nothing else ever wrote to disk.

    Either signal is enough, as VSFilter takes either: a ScriptType whose
    last six characters are "4.00++", or the section header that goes
    with it.
    """
    for line in text.split('\n')[:80]:
        bare = line.strip().lower()
        if bare.startswith('scripttype:') and bare.rstrip()[-6:] == '4.00++':
            return True
        if bare.replace(' ', '') == '[v4++styles]':
            return True
    return False


def _from_v4plusplus(text):
    """v4.00++ as v4.00+, which is how VSFilter itself saves it down.

    v4++ splits MarginV into a top and a bottom margin and adds
    RelativeTo at the end, so its style lines carry 25 fields and its
    events 11 where v4+ has 23 and 10. The two margins become the one
    they are the halves of; RelativeTo goes, having nowhere to live.
    Format lines are ignored in v4++ and both readers count commas, so
    the fields are taken by position.
    """
    out = []
    for line in text.split('\n'):
        bare = line.strip().lower()
        if bare.replace(' ', '') == '[v4++styles]':
            out.append('[V4+ Styles]')
            continue
        if bare.startswith('scripttype:'):
            out.append('ScriptType: v4.00+')
            continue
        if bare.startswith('format:'):
            continue                # meaningless here, and wrong for v4+
        keyword, _, rest = line.partition(':')
        name = keyword.strip().lower()
        if name == 'style':
            fields = rest.split(',')
            if len(fields) >= 25:
                top, bottom = _margin(fields[21]), _margin(fields[22])
                fields = fields[:21] + [str((top + bottom) // 2)] + [fields[23]]
            out.append(f'Style:{",".join(fields)}')
            continue
        if name in ('dialogue', 'comment'):
            fields = rest.split(',', 10)
            if len(fields) >= 11:
                top, bottom = _margin(fields[6]), _margin(fields[7])
                fields = fields[:6] + [str((top + bottom) // 2)] + fields[8:]
            out.append(f'{keyword.strip()}:{",".join(fields)}')
            continue
        out.append(line)
    return '\n'.join(out)


def _margin(value):
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def uudecode(lines):
    """The bytes of an attachment's encoded lines (SSA v4, Appendix B)."""
    text = ''.join(str(line).strip() for line in lines)
    out = bytearray()
    for at in range(0, len(text), 4):
        group = [(ord(char) - 33) & 63 for char in text[at:at + 4]]
        if len(group) < 2:
            break
        value = 0
        for six in group + [0] * (4 - len(group)):
            value = value << 6 | six
        out += value.to_bytes(3, 'big')[:len(group) - 1]
    return bytes(out)


def uuencode(data):
    """An attachment's encoded lines, 80 characters each (SSA v4, Appendix B)."""
    chars = []
    for at in range(0, len(data), 3):
        chunk = data[at:at + 3]
        value = int.from_bytes(chunk + b'\0' * (3 - len(chunk)), 'big')
        sixes = ((value >> 18) & 63, (value >> 12) & 63, (value >> 6) & 63, value & 63)
        chars.append(''.join(chr(six + 33) for six in sixes[:len(chunk) + 1]))
    text = ''.join(chars)
    return [text[at:at + 80] for at in range(0, len(text), 80)]


def attachments_of(subfile):
    """{'fonts': {name: bytes}, 'graphics': {name: bytes}} a script carries,
    the kinds it has none of left out."""
    found = {}
    for kind, opaque in (('fonts', subfile.fonts_opaque), ('graphics', subfile.graphics_opaque)):
        decoded = {str(name): uudecode(lines) for name, lines in opaque.items()}
        if decoded:
            found[kind] = decoded
    return found


def read_file(path):
    """(subtitles, attachments) of the .ass or .ssa file at `path`."""
    with open(path, 'rb') as ass_file:
        text = decode(ass_file.read())
    if is_v4plusplus(text):
        text = _from_v4plusplus(text)
    subfile = pysubs2.SSAFile.from_string(text)
    return segments_of(subfile), attachments_of(subfile)


def read(path):
    """The subtitles in the .ass or .ssa file at `path`."""
    return read_file(path)[0]


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


def _style_with_look(base, styling, height):
    """`base` with a look laid over it, as ASS can say it: a box (in
    BorderStyle 3, the outline's colour, padded by the outline's width), or
    an outline and a shadow; a font and a size."""
    style = base.copy()
    if styling.get('font'):
        style.fontname = str(styling['font']).split(',')[0].strip().strip('"\'')
    if styling.get('size'):
        style.fontsize = round(styling['size'] * height / 100, 3)
    if styling.get('background'):
        style.borderstyle = 3
        style.outlinecolor = _argb(styling['background'], '#ff000000')
        style.outline = round(max(styling.get('padding') or [0]) * height / 100, 3)
        style.shadow = 0
        return style
    outline = styling.get('outline') or {}
    if outline.get('width'):
        style.borderstyle = 1
        style.outline = round(outline['width'] * height / 100, 3)
        if outline.get('color'):
            style.outlinecolor = _argb(outline['color'], '#ff000000')
    shadows = styling.get('shadow') or []
    if shadows:
        # ASS has one shadow, as deep across as down.
        first = shadows[0]
        style.shadow = round(max(abs(first.get('x') or 0), abs(first.get('y') or 0)) * height / 100, 3)
        if first.get('color'):
            style.backcolor = _argb(first['color'], '#ff000000')
    return style


def title_style(segment, height):
    """The style a title (modules.cue_kinds) is written in: its theme's
    export look as ASS can say it (modules.title_themes.export_look): the
    font, its size and weight, the colour, where it is placed, a box behind
    it or a shadow. Its look beyond that is its theme's, which ASS cannot
    draw."""
    from subtitld.modules import title_themes
    settings = title_themes.export_look(segment) or dict(title_themes.EXPORT_DEFAULTS)
    scale = height / 1080
    style = pysubs2.SSAStyle(
        fontname=str(settings.get('font_family') or 'Arial'), fontsize=round(float(settings.get('font_size') or 72) * scale),
        primarycolor=_argb(settings.get('color'), '#ffffffff'), outlinecolor=pysubs2.Color(0, 0, 0, 0),
        backcolor=_argb(settings.get('shadow_color'), '#aa000000'), outline=0,
        shadow=round(2 * scale, 1) if settings.get('shadow_enabled') else 0, borderstyle=1,
        bold=bool(settings.get('bold')), italic=bool(settings.get('italic')),
        alignment=pysubs2.Alignment(title_themes.PLACEMENTS.get(settings.get('placement'), 5)),
        marginl=round(96 * scale), marginr=round(96 * scale), marginv=round(54 * scale))
    if settings.get('box_enabled'):
        style.borderstyle = 3
        style.outlinecolor = _argb(settings.get('box_color'), '#cc000000')
        style.outline = round(float(settings.get('box_padding') or 0) * scale, 1)
        style.shadow = 0
    if settings.get('letter_spacing'):
        style.spacing = round(float(settings['letter_spacing']) * float(settings.get('font_size') or 72) * scale, 1)
    return style


def _turn(degrees):
    return str(int(degrees)) if float(degrees).is_integer() else str(float(degrees))


def write(segments, width=None, height=None, fmt='ass', look='clean', preview=None, attachments=None):
    """ASS (or, with fmt 'ssa', SSA v4) text for `segments`, with the
    fonts and pictures in `attachments` (as attachments_of gives them)."""
    width, height = int(width or 1920), int(height or 1080)
    subfile = pysubs2.SSAFile()
    for kind, opaque in (('fonts', subfile.fonts_opaque), ('graphics', subfile.graphics_opaque)):
        for name, data in ((attachments or {}).get(kind) or {}).items():
            # A name is one word in the file ("fontname: <name>"); what
            # follows a space would not be read back.
            name = re.sub(r'\s+', '_', str(name).strip())
            if name and data:
                opaque[name] = uuencode(bytes(data))
    subfile.info['PlayResX'] = str(width)
    subfile.info['PlayResY'] = str(height)
    subfile.info['ScaledBorderAndShadow'] = 'yes'
    subfile.styles['Default'] = default_style(height, look, preview)
    # One style for each look the subtitles use, Default with it laid
    # over: the file keeps the structure though the project holds a look
    # per subtitle.
    lookings = {}
    titles = {}
    for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
        raw = str(segment.get('text') or '')
        if segment.get('kind') == 'title':
            # Its other text slots too, a line each (modules.title_themes).
            from subtitld.modules import title_themes
            raw = title_themes.export_text(segment)
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')
        lead, rest = markup.split_lead(raw)
        rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
        if not markup.plain(rest).strip():
            continue
        if segment.get('kind') == 'title':
            # A title its theme writes in capitals, written so (its text has
            # no formatting to keep from it).
            if (title_themes.export_look(segment) or {}).get('uppercase'):
                rest = markup.plain(rest).upper()
        text = markup.to_ass(lead + rest)
        if fmt == 'ssa':
            # SSA places a line with its own numbers: \a6 is top centre.
            text = re.sub(r'\\an(\d)', lambda m: f'\\a{_TO_LEGACY.get(int(m.group(1)), 2)}', text)
        placed = position.of(segment)
        turned = ''.join(f'\\fr{axis}{_turn(degrees)}'
                         for axis, degrees in zip(position.AXES, placed.rotate) if degrees)
        if turned:
            text = '{' + turned + '}' + text
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        event = pysubs2.SSAEvent(start=int(round(start * 1000)), end=int(round(end * 1000)), text=text,
                                 name=str(segment.get('speaker') or '').strip())
        styling = subtitld_look.of(segment)
        if segment.get('kind') == 'title':
            # A title in a style of its own, one for each way titles look.
            styled = title_style(segment, height)
            key = json.dumps(styled.as_dict(), sort_keys=True, default=str)
            if key not in titles:
                titles[key] = 'Title' if not titles else f'Title{len(titles) + 1}'
                subfile.styles[titles[key]] = styled
            event.style = titles[key]
            event.name = ''
        elif styling:
            key = json.dumps(styling, sort_keys=True)
            if key not in lookings:
                styled = _style_with_look(subfile.styles['Default'], styling, height)
                # A look of nothing ASS has words for is Default again.
                lookings[key] = (f'Look{len(lookings) + 1}' if styled.as_dict() != subfile.styles['Default'].as_dict()
                                 else 'Default')
                if lookings[key] != 'Default':
                    subfile.styles[lookings[key]] = styled
            event.style = lookings[key]
        # Margins stand the line off the edge it is aligned to. ASS centres
        # a line between its left and right margins, so a subtitle nudged
        # off the middle has nowhere to say so and keeps the style's.
        keypad = markup.alignment(lead)
        across, down = position.as_percent(placed.h), position.as_percent(placed.v)
        if down is not None:
            event.marginv = max(0, int(round(down * height / 100.0)))
        if across is not None and (keypad - 1) % 3 != 1:
            pixels = max(0, int(round(across * width / 100.0)))
            if (keypad - 1) % 3 == 0:
                event.marginl = pixels
            else:
                event.marginr = pixels
        subfile.append(event)
    return subfile.to_string(fmt)


def save(path, segments, width=None, height=None, look='clean', preview=None, attachments=None):
    """Write `segments` to `path`: SubStation Alpha v4 when it ends in .ssa,
    Advanced SubStation Alpha otherwise. UTF-8."""
    fmt = 'ssa' if str(path).lower().endswith('.ssa') else 'ass'
    with open(path, 'w', encoding='utf-8', newline='\n') as ass_file:
        ass_file.write(write(segments, width, height, fmt, look, preview, attachments))
