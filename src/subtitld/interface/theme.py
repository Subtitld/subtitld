"""The interface's two looks: dark (the one everything is drawn in) and light.

There is one palette, the dark one: the stylesheet, the SVG icons and the
painted widgets are all written in its colours. The light look is that
palette turned over by what each colour is drawn as (text, icon, surface,
line; see "colours" below), so the stylesheet, the icons and the painters
only pass their colours through `color()`, `ink()` or `surface()`. Text is
kept at WCAG AA (4.5:1) or better on the light grounds.

What the turning over doesn't get right is set by hand in
graphics/stylesheet_light.qss, added after the turned-over stylesheet.

`CONFIG['interface_theme']` is 'system' (the default), 'dark' or 'light'.
"""

import colorsys
import os
import re

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from subtitld.modules import session

THEMES = ('system', 'dark', 'light')

_current = 'dark'
_system = None


class _Notifier(QObject):
    changed = Signal(str)


notifier = _Notifier()


def setting():
    return (session.CONFIG.get('interface_theme') if session.CONFIG else None) or 'system'


def system_theme():
    """The system's look. Once a look is picked Qt reports that one instead,
    so the system's is the one read when the pick was last applied."""
    if _system is not None:
        return _system
    app = QGuiApplication.instance()
    if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Light:
        return 'light'
    return 'dark'


def resolve(choice=None):
    choice = choice or setting()
    return system_theme() if choice == 'system' else choice


def current():
    return _current


def is_light():
    return _current == 'light'


def apply(choice=None):
    """Draw in the look picked (or the system's). Qt's own pieces the
    stylesheet doesn't reach (native dialogs, the palette) are told the
    picked look too, and left to the system's when that is the pick."""
    global _system
    choice = choice or setting()
    app = QGuiApplication.instance()
    if app is not None:
        _system = None
        app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
        _system = system_theme()
        app.styleHints().setColorScheme({'dark': Qt.ColorScheme.Dark,
                                         'light': Qt.ColorScheme.Light}.get(choice, Qt.ColorScheme.Unknown))
    set_current(resolve(choice))


def system_changed(scheme):
    """Qt's colorSchemeChanged: followed while the system's look is the pick
    (with a look picked, the changes are Qt reporting that pick)."""
    global _system
    if setting() == 'system':
        _system = 'light' if scheme == Qt.ColorScheme.Light else 'dark'
        set_current(_system)


def set_current(name):
    """Make `name` the look the stylesheet, icons and painters draw in."""
    global _current
    if name == _current:
        return
    _current = name
    _icon_cache.clear()
    if name == 'light':
        _ensure_light_graphics()
    notifier.changed.emit(name)


# --- colours ---
#
# A colour's light counterpart depends on what it is drawn as:
# - text: every text turns dark, the pale ones (the most
#   read) darkest, keeping the dark theme's order of emphasis; dark text on a
#   pale key stays dark;
# - an icon's pale strokes and fills: one blue, the palette's #566f84;
# - a surface: the dark grounds turn light, keeping their order (a raised
#   button is lighter than the panel under it, a well darker), so depth reads
#   the same; pale surfaces (keys, tonal buttons) stay pale; translucent
#   lightenings (hover) become translucent darkenings;
# - a line: dark separators become light-grey ones, pale outlines darker ones.
# Saturated colours keep their hue: as text and lines they get dark enough to
# read on white; as fills they stay as they are, the dark ones becoming pale
# tints. Black stays black (shadows, contact lines, the video), lighter.

TEXT, ICON, SOLID_ICON, SURFACE, LINE, AUTO = 'text', 'icon', 'solid icon', 'surface', 'line', 'auto'

# Surfaces darker than this are grounds (turn light); lighter ones (keys,
# handles) stay pale or mid.
_GROUND = 0.5
# Dark-theme lightness of a ground → light-theme lightness: rising, so the
# dark theme's raised pieces (lighter) are the light theme's lighter ones too.
_GROUNDS = ((0.0, 0.865), (0.10, 0.88), (0.15, 0.90), (0.22, 0.93), (0.30, 0.955), (0.40, 0.985),
            (0.50, 1.0))
# The light look's icons: the palette's #566f84, and #4a6376 for the white
# (emphasised) ones.
_ICON_BLUE = (0x56, 0x6f, 0x84)
_ICON_EMPHASIS = (0x4a, 0x63, 0x76)
# The hue of the palette's blue-greys (#b8cee0).
_INK_HUE = 0.575
# Above this chroma (max - min channel) a colour is an accent, not one of the
# blue-greys.
_ACCENT_CHROMA = 0.22


def _interpolate(points, x):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def _is_shadow(r, g, b, a=255):
    """Black, or a translucent near-black: a shadow, a contact line or the
    video's ground, which stays dark in both looks. An opaque near-black is
    a background, and turns light."""
    return (r, g, b) == (0, 0, 0) or (a < 255 and max(r, g, b) <= 12)


def luminance(r, g, b):
    """WCAG relative luminance of an sRGB colour, 0–255 channels."""
    def linear(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def contrast(first, second):
    """WCAG contrast ratio of two (r, g, b) colours."""
    a, b = sorted((luminance(*first[:3]), luminance(*second[:3])), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _readable(h, l, s, ratio):
    """The lightness, at most `l`, at which hue `h` reaches `ratio` against
    white (asked well over 4.5:1, it keeps 4.5:1 on the light panels and
    their tinted badges)."""
    while l > 0:
        rgb = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
        if contrast(rgb, (255, 255, 255)) >= ratio:
            return l
        l -= 0.01
    return 0.0


def _text_lightness(l):
    """Text's lightness: the palette's own dark blues, white's text as
    #304251 (0.25), #b8cee0's as #3e5363 (0.32): dark blue, not black."""
    return 0.25 + (1 - l) * 0.35 if l >= 0.5 else min(l, 0.32)


# Dark-theme colours the light look draws in a colour chosen by hand rather
# than turned over: where turning over loses a difference that matters.
_LIGHT_SURFACES = {
    (0x33, 0x47, 0x5a): (0xd0, 0xda, 0xe3),   # shapes.NEST: a field's slanted label, a step off the field
}


def map_rgba(r, g, b, a=255, role=AUTO):
    """The light-theme counterpart of the dark-theme colour (r, g, b, a), 0–255,
    drawn as `role` (TEXT, SURFACE, LINE, or AUTO: light colours as text,
    dark ones as surfaces)."""
    if _is_shadow(r, g, b, a):
        # a darkening: a line (an edge) keeps some of it, a wash over an area little
        return (r, g, b, a if a == 255 else round(a * (0.15 if role == SURFACE else 0.4)))
    if role in (SURFACE, AUTO) and (r, g, b) in _LIGHT_SURFACES:
        return (*_LIGHT_SURFACES[(r, g, b)], a)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if role in (AUTO, ICON, SOLID_ICON) and l < 0.5:
        role = SURFACE
    elif role == AUTO:
        role = TEXT
    if a < 255 and min(r, g, b) >= 245:
        # translucent white: a lightening over a ground, a darkening on light;
        # a 1px highlight line stays one
        if role == SURFACE:
            return (0, 0, 0, round(a * 0.6))
        if role == LINE:
            return (r, g, b, a)
    accent = (max(r, g, b) - min(r, g, b)) / 255 > _ACCENT_CHROMA
    if role in (TEXT, ICON, SOLID_ICON) and not accent:
        # the palette's darks (#304251, #3e5363); white and the greys, with
        # no hue of their own, take theirs
        h, s = (h, min(s, 0.26)) if s > 0.05 else (_INK_HUE, 0.26)
    if role == TEXT:
        lightness = _readable(h, l, s, 6.0) if accent else _text_lightness(l)
        if 64 <= a < 255:
            # secondary and hint text: a dark ground lets a translucent text
            # read better than a light one does
            a = round(a + (255 - a) * 0.65)
    elif role in (ICON, SOLID_ICON):
        if accent:
            lightness = _readable(h, l, s, 3.4) if role == ICON else l
        else:
            # one blue for every icon, the palette's #566f84 (white ones,
            # the emphasised, #4a6376): filled or stroked, an icon's blue
            # is the same as its neighbours'
            return (*_ICON_EMPHASIS, a) if l > 0.95 else (*_ICON_BLUE, a)
    elif role == LINE:
        if accent:
            lightness = min(l, 0.45)
        else:
            lightness = 0.80 if l < _GROUND else 0.2 + (1 - l) * 0.9
    else:
        if accent:
            if l >= 0.35:
                return (r, g, b, a)
            lightness, s = 0.92, min(s, 0.5)
        elif a < 255 and l >= 0.45:
            # a pale wash over a ground (hover, selection): the same wash in
            # the palette's #566f84
            return (86, 111, 132, round(a * 0.7))
        elif l < _GROUND:
            lightness = _interpolate(_GROUNDS, l)
        else:
            lightness = l + (1 - l) * 0.3
    lightness = min(1.0, max(0.0, lightness))
    nr, ng, nb = colorsys.hls_to_rgb(h, lightness, s)
    return (round(nr * 255), round(ng * 255), round(nb * 255), a)


def color(value, role=AUTO):
    """A QColor for `value` (a QColor, '#rgb', '#rrggbb', '#aarrggbb', a name
    or an (r, g, b[, a]) tuple) in the current look, drawn as `role`.
    Painters pass their dark-theme colours through this; content colours
    (speakers', the video's, the subtitles' own) are not passed."""
    if isinstance(value, (tuple, list)):
        qcolor = QColor(*value)
    else:
        qcolor = QColor(value)
    if _current != 'light' or not qcolor.isValid() or qcolor.alpha() == 0:
        return qcolor
    return QColor(*map_rgba(qcolor.red(), qcolor.green(), qcolor.blue(), qcolor.alpha(), role))


def surface(value):
    """`color()` of a fill: pale fills (keys) stay pale."""
    return color(value, SURFACE)


def ink(value):
    """`color()` of text and strokes: dark ink (on pale keys) stays dark."""
    return color(value, TEXT)


# Defaults of the configurable colours that the light look draws otherwise:
# the waveform's dark navy shows through the subtitle boxes on a light ground.
_LIGHT_DEFAULTS = {'#ff153450': '#ff8fa7bb', '#cc153450': '#cc8fa7bb'}


def configured(value):
    """A colour from the settings: as set, unless it is a default the light
    look draws in its own colour."""
    if _current == 'light' and isinstance(value, str) and value.lower() in _LIGHT_DEFAULTS:
        return QColor(_LIGHT_DEFAULTS[value.lower()])
    return QColor(value)


def css(value, role=AUTO):
    """`color()` written for a stylesheet: rgba(r, g, b, a)."""
    qcolor = color(value, role)
    return f'rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {qcolor.alpha()})'


# --- stylesheets and SVGs ---

_NAMED = {'black': (0, 0, 0), 'white': (255, 255, 255), 'grey': (128, 128, 128), 'gray': (128, 128, 128),
          'red': (255, 0, 0)}
_COLOR_RE = re.compile(r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)'
                       r'|(?<![\w\-#])(?:black|white|grey|gray|red)\b(?![\w\-])')


def _parse(text, argb=True):
    """(r, g, b, a) of a colour written in a stylesheet or an SVG."""
    if text.startswith('#'):
        digits = text[1:]
        if len(digits) == 3:
            digits = ''.join(c * 2 for c in digits)
        if len(digits) == 8:
            if argb:
                a, rgb = int(digits[:2], 16), digits[2:]
            else:
                rgb, a = digits[:6], int(digits[6:], 16)
        else:
            a, rgb = 255, digits
        return int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), a
    if text.startswith('rgb'):
        parts = [p.strip() for p in text[text.index('(') + 1:-1].split(',')]
        r, g, b = (int(float(p)) for p in parts[:3])
        a = 255
        if len(parts) > 3:
            value = float(parts[3].rstrip('%'))
            a = round(value * 2.55) if parts[3].endswith('%') else round(value * 255) if value <= 1 and '.' in parts[3] else round(value)
        return r, g, b, a
    return (*_NAMED[text.lower()], 255)


def _write(rgba, original, argb=True):
    r, g, b, a = rgba
    if original.startswith('#') and len(original) in (4, 7):
        return f'#{r:02x}{g:02x}{b:02x}'
    if original.startswith('#') and not argb:
        return f'#{r:02x}{g:02x}{b:02x}{a:02x}'
    return f'rgba({r}, {g}, {b}, {a})'


def _map_text(match, argb=True, role=AUTO):
    text = match.group(0)
    try:
        r, g, b, a = _parse(text, argb)
    except (ValueError, KeyError, IndexError):
        return text
    return _write(map_rgba(r, g, b, a, role), text, argb)


def _role(prop):
    if prop in ('color', 'selection-color'):
        return TEXT
    if prop.startswith(('border', 'outline', 'gridline')):
        return LINE
    return SURFACE


_DECLARATION_RE = re.compile(r'([a-z\-]+)(\s*:\s*)([^;{}]+)')


def _map_gradient(value, role):
    """A gradient's stops, keeping the depth they have on the dark look:
    around the light counterpart of their mean, they differ in lightness as
    they did (the grounds' curve would flatten them), the lightest at most
    white."""
    stops = list(_COLOR_RE.finditer(value))
    parsed = [_parse(m.group(0)) for m in stops]
    opaque = [p for p in parsed if p[3] == 255 and not _is_shadow(*p)]
    if role != SURFACE or len(opaque) < 2:
        return _COLOR_RE.sub(lambda m: _map_text(m, role=role), value)
    own = [colorsys.rgb_to_hls(*(c / 255 for c in p[:3]))[1] for p in opaque]
    mean = sum(own) / len(own)
    centre = colorsys.rgb_to_hls(*(c / 255 for c in map_rgba(*_mean(opaque), role=SURFACE)[:3]))[1]
    centre = min(centre, 1.0 - (max(own) - mean))
    out, last = [], 0
    for m, p in zip(stops, parsed):
        out.append(value[last:m.start()])
        if p in opaque:
            h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in p[:3]))
            r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(h, min(1.0, centre + (l - mean)), s))
            out.append(_write((r, g, b, 255), m.group(0)))
        else:
            out.append(_map_text(m, role=role))
        last = m.end()
    out.append(value[last:])
    return ''.join(out)


def _mean(colours):
    return tuple(round(sum(c[i] for c in colours) / len(colours)) for i in range(3)) + (255,)


# A hovered or selected item drawn as a lighter ground on the dark look is a
# tint of the palette's #566f84 on the light one (a lighter ground than
# white there is none).
_HOVER_TINT = 'rgba(86, 111, 132, 26)'
_SELECTED_TINT = 'rgba(86, 111, 132, 64)'
_STATE_RE = re.compile(r':hover|:selected|:checked|\[selected="?true"?\]|\[selected_\w+="?true"?\]')


def map_stylesheet(text):
    """The light-theme version of a dark-theme stylesheet: each colour by the
    property it is given to."""
    def declaration(match, state):
        role = _role(match.group(1))
        value = match.group(3)
        if state and role == SURFACE and match.group(1) in ('background', 'background-color'):
            colour = _COLOR_RE.fullmatch(value.strip())
            if colour:
                r, g, b, a = _parse(colour.group(0))
                if a == 255 and not _is_shadow(r, g, b) and colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[1] < _GROUND \
                        and (max(r, g, b) - min(r, g, b)) / 255 <= _ACCENT_CHROMA:
                    tint = _HOVER_TINT if state == 'hover' else _SELECTED_TINT
                    return match.group(1) + match.group(2) + value.replace(colour.group(0), tint)
        if 'gradient' in value:
            return match.group(1) + match.group(2) + _map_gradient(value, role)
        value = _COLOR_RE.sub(lambda m: _map_text(m, role=role), value)
        return match.group(1) + match.group(2) + value
    out, last = [], 0
    for block in re.finditer(r'([^{}]*)\{([^{}]*)\}', text):
        selectors = block.group(1)
        states = set(m.group(0) for m in _STATE_RE.finditer(selectors))
        state = None if not states else 'hover' if states == {':hover'} else 'selected'
        out.append(text[last:block.start()] + selectors)
        out.append('{' + _DECLARATION_RE.sub(lambda m: declaration(m, state), block.group(2)) + '}')
        last = block.end()
    out.append(text[last:])
    return ''.join(out)


_SVG_SKIP_RE = re.compile(r'<(mask|clipPath)\b.*?</\1>', re.S)
_SVG_COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)'
                           r'|(?<=["\':])\s*(?:black|white)\b')


def map_svg(text, role=ICON):
    """The light-theme version of a dark-theme SVG, its pale colours drawn as
    `role` (ICON, SOLID_ICON, or SURFACE for a pale piece, which stays pale);
    masks and clip paths keep their colours (they are coverage, not colour)."""
    def mapped(part):
        return _SVG_COLOR_RE.sub(lambda m: _map_svg_color(m, role), _SVG_SHAPE_RE.sub(_lighter_black, part))
    parts, last = [], 0
    for skip in _SVG_SKIP_RE.finditer(text):
        parts.append(mapped(text[last:skip.start()]))
        parts.append(skip.group(0))
        last = skip.end()
    parts.append(mapped(text[last:]))
    return ''.join(parts)


_SVG_SHAPE_RE = re.compile(r'<(?:path|rect|circle|ellipse|polygon|polyline|line|stop|g)\b[^>]*>')
_SVG_OPACITY_RE = re.compile(r'((?:fill-|stroke-|stop-)?opacity\s*[=:]\s*["\']?)([0-9.]+)')


def _lighter_black(match):
    """A shape or a gradient stop in translucent black (an SVG's default
    colour) is a darkening: on a light ground it keeps a fifth of it."""
    tag = match.group(0)
    paints = [m.group(1).lower() for m in re.finditer(
        r'(?:fill|stroke|stop-color)\s*[=:]\s*["\']?\s*(#[0-9a-fA-F]{3,6}|black|none|url)', tag)]
    paints = [p for p in paints if p != 'none'] or (['#000'] if 'stroke' not in tag else [])
    if not paints or any(p not in ('#000', '#000000', 'black') for p in paints):
        return tag
    if not _SVG_OPACITY_RE.search(tag):
        if tag.startswith('<stop'):
            # a black stop, opaque: a shadow's dark end
            return tag[:-2] + ' stop-opacity="0.2"/>' if tag.endswith('/>') else tag[:-1] + ' stop-opacity="0.2">'
        return tag
    return _SVG_OPACITY_RE.sub(lambda m: m.group(1) + f'{float(m.group(2)) * 0.2:.3g}', tag)


def _map_svg_color(match, role=ICON):
    text = match.group(0)
    stripped = text.strip()
    try:
        rgba = _parse(stripped, argb=False)
    except (ValueError, KeyError, IndexError):
        return text
    if role == SURFACE:
        rgba = _piece_colour(*rgba)
    else:
        rgba = map_rgba(*rgba, role=role)
    return text[:len(text) - len(text.lstrip())] + _write(rgba, stripped, argb=False)


def _piece_colour(r, g, b, a):
    """A pale piece's colour on the light look: the palette's pale blues as
    they are, a neutral grey given their hue (grey reads as disabled on a
    light ground), its dark ink as it is."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if l < 0.6 or (max(r, g, b) - min(r, g, b)) / 255 > _ACCENT_CHROMA:
        return (r, g, b, a)
    if s >= 0.05:
        return (r, g, b, a)
    r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(_INK_HUE, l, 0.39))
    return (r, g, b, a)


# --- files ---

def _light_dir():
    return session.PATH_SUBTITLD_USER_CACHE / 'theme-light'


def _ensure_light_graphics():
    """Write the light SVGs into the cache, once per change of the originals."""
    target = _light_dir()
    target.mkdir(parents=True, exist_ok=True)
    for source in session.PATH_SUBTITLD_GRAPHICS.glob('*.svg'):
        light = target / source.name
        drawn = session.PATH_SUBTITLD_GRAPHICS / 'light' / source.name
        # (graphics/light/'s own date changes when a drawn file is added or removed)
        newest = max(source.stat().st_mtime, _module_mtime(), (session.PATH_SUBTITLD_GRAPHICS / 'light').stat().st_mtime,
                     drawn.stat().st_mtime if drawn.exists() else 0)
        if light.exists() and light.stat().st_mtime >= newest:
            continue
        if drawn.exists():
            light.write_text(drawn.read_text(encoding='utf-8'), encoding='utf-8')
            continue
        text = source.read_text(encoding='utf-8')
        kind = _kind(source)
        light.write_text(text if kind == 'ink' else map_svg(text, {'piece': SURFACE, 'solid': SOLID_ICON}.get(kind, ICON)),
                         encoding='utf-8')
    for drawn in (session.PATH_SUBTITLD_GRAPHICS / 'light').glob('*.svg'):
        # drawn for the light look only (no dark original)
        light = target / drawn.name
        if not (session.PATH_SUBTITLD_GRAPHICS / drawn.name).exists() and \
                (not light.exists() or light.stat().st_mtime < drawn.stat().st_mtime):
            light.write_text(drawn.read_text(encoding='utf-8'), encoding='utf-8')
    return target


def _kind(path):
    """What an SVG is, for its light version:
    - 'piece': a key, a tonal button, a tab, a handle: mostly a pale surface
      (named so, or with dark ink on it); it stays pale;
    - 'ink': an icon drawn in dark ink (it sits on a pale key); kept;
    - 'solid': a pale icon filled, its figure cut out of it; mid-tone;
    - 'icon': other icons (pale strokes, dark) and dark grounds (light).
    The save button's green (unsaved changes) is kept as it is."""
    if path.name.startswith('titleBar_left_save_button_unsaved'):
        return 'ink'
    image = QImage(48, 48, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(path)).render(painter)
    painter.end()
    covered, pale, ink = 0, 0, 0
    for y in range(48):
        for x in range(48):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 200:
                covered += 1
                pale += pixel.lightnessF() > 0.6
                ink += pixel.lightnessF() < 0.4
    if not covered:
        return 'icon'
    if pale > covered * 0.6:
        if covered > 48 * 48 * 0.5 and (ink > covered * 0.02 or _PIECE_RE.search(path.name)):
            return 'piece'
        if covered > 48 * 48 * 0.4 and path.name not in _stylesheet_grounds():
            return 'solid'
        return 'icon'
    if ink > covered * 0.6 and covered < 48 * 48 * 0.85 and path.name not in _stylesheet_grounds():
        return 'ink'
    return 'icon'


def _stylesheet_grounds():
    """The SVGs the stylesheet lays under widgets (border and background
    images): grounds, whatever their shape."""
    text = (session.PATH_SUBTITLD_GRAPHICS / 'stylesheet.qss').read_text(encoding='utf-8')
    return set(re.findall(r"(?:border|background)-image\s*:\s*url\(['\"]?graphics:([^)'\"]+\.svg)", text))


# Backgrounds of buttons, tabs and handles (as opposed to icons).
_PIECE_RE = re.compile(r'^button_|_tab(_|\.)|^scrollbar_handle|^titleBar_left_\w+_button|^checkbox_checked')


def _module_mtime():
    return os.path.getmtime(__file__)


def graphic(name):
    """The path of graphics/`name` in the current look, as a string."""
    if _current == 'light' and name.endswith('.svg'):
        light = _light_dir() / name
        if light.exists():
            return str(light)
    return str(session.PATH_SUBTITLD_GRAPHICS / name)


def stylesheet():
    """The application's stylesheet in the current look; its icons point at
    the current look's files."""
    text = (session.PATH_SUBTITLD_GRAPHICS / 'stylesheet.qss').read_text(encoding='utf-8')
    if _current != 'light':
        return text
    folder = _ensure_light_graphics().as_posix()
    text = map_stylesheet(re.sub(r'/\*.*?\*/', '', text, flags=re.S))
    text += '\n' + (session.PATH_SUBTITLD_GRAPHICS / 'stylesheet_light.qss').read_text(encoding='utf-8')
    return re.sub(r'url\((["\']?)graphics:([^)\'"]+?\.svg)\1\)', lambda m: f'url({folder}/{m.group(2)})', text)


# --- icons ---

class _ThemedIconEngine(QIconEngine):
    """Draws graphics/`name` from the look current when it is painted, so a
    button set up once follows a change of look."""

    def __init__(self, name):
        super().__init__()
        self.name = name

    def paint(self, painter, rect, mode, state):
        svg = renderer(self.name)
        if not svg.isValid():
            return
        size = svg.defaultSize()
        target = QRectF(rect)
        if size.width() > 0 and size.height() > 0:
            scaled = size.scaled(rect.size(), Qt.KeepAspectRatio)
            target = QRectF(rect.x() + (rect.width() - scaled.width()) / 2,
                            rect.y() + (rect.height() - scaled.height()) / 2, scaled.width(), scaled.height())
        if mode == QIcon.Disabled:
            painter.setOpacity(painter.opacity() * 0.4)
        svg.render(painter, target)

    def pixmap(self, size, mode, state):
        image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.paint(painter, image.rect(), mode, state)
        painter.end()
        return QPixmap.fromImage(image)

    def actualSize(self, size, mode, state):
        svg = renderer(self.name)
        default = svg.defaultSize() if svg.isValid() else QSize()
        if default.isEmpty():
            return size
        return default.scaled(size, Qt.KeepAspectRatio)

    def clone(self):
        return _ThemedIconEngine(self.name)


_icon_cache = {}


def icon(name):
    """A QIcon of graphics/`name` that follows the look. Non-SVG files are
    plain icons: they look the same in both."""
    if not name.endswith('.svg'):
        return QIcon(str(session.PATH_SUBTITLD_GRAPHICS / name))
    return QIcon(_ThemedIconEngine(name))


def pixmap(name, width, height=None):
    """graphics/`name` as a pixmap of the given size, in the current look."""
    return icon(name).pixmap(width, height if height is not None else width)


def renderer(name):
    """A QSvgRenderer of graphics/`name` in the current look (cached per look)."""
    key = (_current, name)
    if key not in _icon_cache:
        _icon_cache[key] = QSvgRenderer(graphic(name))
    return _icon_cache[key]
