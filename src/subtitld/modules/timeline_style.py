"""How the timeline looks: its colours, fonts and the shape of its cues' boxes.

Every setting is a field in the shape subtitle styles' manifests use
(modules.subtitle_styles): a key, a type (number, int, bool, select,
color, font), a default and, for numbers, a range and a unit. `group`
says which box of the settings it is shown in; `visible_if` names a bool
it only matters with, `hidden_if` one it does not matter with (or a
[key, value] pair: while that setting is that value), `visible_when` a
[key, value] pair it only matters with. A text field with `file` names
a file (an SVG, modules.timeline_svg) by the name it is kept under.

The look has two parts: the timeline's own (its ruler, grid, waveform,
playhead and marks) and a box look for each kind of cue (KINDS):
subtitles now, titles, on-screen text and others as they come. A kind
takes the subtitles' box look for what it does not set itself, so a look
made before it existed still gives it one that matches.

What the user set is kept in CONFIG['timeline']['style'], defaults and
what a kind inherits left out (a later Subtitld may change them):

    {"timeline": {"cursor_color": "#ff00e5ff", ...},
     "boxes": {"subtitle": {"corner_radius": 8, ...}, "title": {...}}}

That dict alone is the look: a timeline theme is one of them with a name.
A kind this Subtitld does not know is kept as it is, for the one that will.

The painting code asks current() for the look: values with their QColors
and QFonts made once, until a setting changes.
"""

from PySide6.QtGui import QColor, QFont

from subtitld.modules import session
from subtitld.modules.subtitle_styles import _check_field, coerce

TIMELINE_GROUPS = ('background', 'ruler', 'ruler_ticks', 'grid', 'waveform', 'cursor', 'cursor_badge', 'marks')
BOX_GROUPS = ('box', 'fill', 'text', 'edges')
SUBTITLE = 'subtitle'
TIME_FORMATS = ('clock', 'seconds', 'timecode', 'frames')
# Seconds between two of the ruler's labels: the first of these far enough apart.
LABEL_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)

_TIMELINE_FIELDS = [
    # The timeline behind everything; transparent shows the window's own.
    {'key': 'background_color', 'type': 'color', 'default': '#00000000', 'group': 'background'},
    {'key': 'ruler_background_color', 'type': 'color', 'default': '#00000000', 'group': 'background'},

    {'key': 'show_ruler', 'type': 'bool', 'default': True, 'group': 'ruler'},
    {'key': 'time_text_color', 'type': 'color', 'default': '#b8b8cee0', 'group': 'ruler', 'visible_if': 'show_ruler'},
    {'key': 'ruler_font', 'type': 'font', 'default': 'Ubuntu Mono', 'group': 'ruler', 'visible_if': 'show_ruler'},
    {'key': 'ruler_font_size', 'type': 'int', 'default': 8, 'min': 5, 'max': 24, 'unit': 'pt', 'group': 'ruler',
     'visible_if': 'show_ruler'},
    {'key': 'ruler_time_format', 'type': 'select', 'default': 'clock', 'options': list(TIME_FORMATS),
     'group': 'ruler', 'visible_if': 'show_ruler'},
    # Along the top or the bottom of the timeline, `ruler_offset` from that edge.
    {'key': 'ruler_position', 'type': 'select', 'default': 'top', 'options': ['top', 'bottom'], 'group': 'ruler',
     'visible_if': 'show_ruler'},
    {'key': 'ruler_offset', 'type': 'int', 'default': 27, 'min': 0, 'max': 200, 'unit': 'px', 'group': 'ruler',
     'visible_if': 'show_ruler'},
    # The least room between two labels; never less than a label is wide.
    {'key': 'ruler_label_spacing', 'type': 'int', 'default': 75, 'min': 30, 'max': 400, 'unit': 'px',
     'group': 'ruler', 'visible_if': 'show_ruler'},

    # A tick at each label, and `ruler_subdivisions` shorter ones between.
    {'key': 'ruler_ticks', 'type': 'bool', 'default': False, 'group': 'ruler_ticks'},
    {'key': 'ruler_tick_color', 'type': 'color', 'default': '#99b8cee0', 'group': 'ruler_ticks',
     'visible_if': 'ruler_ticks'},
    {'key': 'ruler_tick_length', 'type': 'int', 'default': 8, 'min': 2, 'max': 60, 'unit': 'px',
     'group': 'ruler_ticks', 'visible_if': 'ruler_ticks'},
    {'key': 'ruler_subdivisions', 'type': 'int', 'default': 4, 'min': 0, 'max': 10, 'group': 'ruler_ticks',
     'visible_if': 'ruler_ticks'},

    {'key': 'grid_color', 'type': 'color', 'default': '#336a7483', 'group': 'grid'},
    {'key': 'grid_width', 'type': 'number', 'default': 1.0, 'min': 0.5, 'max': 6.0, 'step': 0.5, 'unit': 'px',
     'group': 'grid'},

    {'key': 'show_waveform', 'type': 'bool', 'default': True, 'group': 'waveform'},
    {'key': 'waveform_fill_color', 'type': 'color', 'default': '#cc153450', 'group': 'waveform',
     'visible_if': 'show_waveform'},
    {'key': 'waveform_border_color', 'type': 'color', 'default': '#ff153450', 'group': 'waveform',
     'visible_if': 'show_waveform'},
    {'key': 'waveform_border_width', 'type': 'number', 'default': 1.0, 'min': 0.0, 'max': 6.0, 'step': 0.5,
     'unit': 'px', 'group': 'waveform', 'visible_if': 'show_waveform'},
    {'key': 'waveform_top', 'type': 'int', 'default': 45, 'min': 0, 'max': 400, 'unit': 'px', 'group': 'waveform',
     'visible_if': 'show_waveform'},
    {'key': 'waveform_bottom', 'type': 'int', 'default': 20, 'min': 0, 'max': 400, 'unit': 'px', 'group': 'waveform',
     'visible_if': 'show_waveform'},
    {'key': 'waveform_scale', 'type': 'int', 'default': 90, 'min': 10, 'max': 300, 'unit': '%', 'group': 'waveform',
     'visible_if': 'show_waveform'},

    {'key': 'cursor_color', 'type': 'color', 'default': '#ccff0000', 'group': 'cursor'},
    {'key': 'cursor_width', 'type': 'number', 'default': 2.0, 'min': 0.5, 'max': 8.0, 'step': 0.5, 'unit': 'px',
     'group': 'cursor'},
    # A line Subtitld draws, or an SVG stretched to the line's width and the timeline's height.
    {'key': 'cursor_line_style', 'type': 'select', 'default': 'solid',
     'options': ['solid', 'dashed', 'dotted', 'custom'], 'group': 'cursor'},
    {'key': 'cursor_line_svg', 'type': 'text', 'default': '', 'file': 'svg', 'group': 'cursor',
     'visible_when': ['cursor_line_style', 'custom']},
    # A wider, fainter line behind it, this much past it on each side.
    {'key': 'cursor_glow', 'type': 'int', 'default': 0, 'min': 0, 'max': 20, 'unit': 'px', 'group': 'cursor'},
    # A shape on the line where it crosses the ruler's edge.
    {'key': 'cursor_head', 'type': 'select', 'default': 'none',
     'options': ['none', 'triangle', 'circle', 'flag', 'custom'], 'group': 'cursor'},
    # An SVG as the head: as tall as the head's size, as wide as its shape, the line through its middle.
    {'key': 'cursor_head_svg', 'type': 'text', 'default': '', 'file': 'svg', 'group': 'cursor',
     'visible_when': ['cursor_head', 'custom']},
    {'key': 'cursor_head_size', 'type': 'int', 'default': 10, 'min': 4, 'max': 30, 'unit': 'px', 'group': 'cursor',
     'hidden_if': ['cursor_head', 'none']},
    # The playhead's time beside it, in the ruler's format, to the millisecond or frame.
    {'key': 'cursor_show_time', 'type': 'bool', 'default': False, 'group': 'cursor'},
    {'key': 'cursor_time_background_color', 'type': 'color', 'default': '#ccff0000', 'group': 'cursor',
     'visible_if': 'cursor_show_time'},
    {'key': 'cursor_time_text_color', 'type': 'color', 'default': '#ffffffff', 'group': 'cursor',
     'visible_if': 'cursor_show_time'},

    # The badge saying the playback repeats or is sped up.
    {'key': 'cursor_badge_shape', 'type': 'select', 'default': 'slanted',
     'options': ['slanted', 'rectangle', 'rounded'], 'group': 'cursor_badge'},
    {'key': 'cursor_badge_side', 'type': 'select', 'default': 'left', 'options': ['left', 'right'],
     'group': 'cursor_badge'},
    {'key': 'cursor_badge_top', 'type': 'int', 'default': 25, 'min': 0, 'max': 400, 'unit': 'px',
     'group': 'cursor_badge'},
    {'key': 'cursor_badge_color', 'type': 'color', 'default': '#96ff0000', 'group': 'cursor_badge'},
    {'key': 'cursor_text_indicator', 'type': 'color', 'default': '#ffffffff', 'group': 'cursor_badge'},

    {'key': 'selected_subtitle_arrow_color', 'type': 'color', 'default': '#ff969696', 'group': 'marks'},
    {'key': 'dub_waveform_color', 'type': 'color', 'default': '#ffffffff', 'group': 'marks'},
    {'key': 'onset_marker_color', 'type': 'color', 'default': '#ffffd9a0', 'group': 'marks'},
    # The stretch repeat playback plays: fading downwards, or even.
    {'key': 'repeat_area_color', 'type': 'color', 'default': '#50ff0000', 'group': 'marks'},
    {'key': 'repeat_area_gradient', 'type': 'bool', 'default': True, 'group': 'marks'},
    # Where the playhead is, when it is out of view.
    {'key': 'offscreen_arrow_color', 'type': 'color', 'default': '#dcff0000', 'group': 'marks'},
]


_BOX_FIELDS = [
    # Where the boxes are: from top to bottom of the timeline, or a band
    # `top` from its top and `bottom` from its bottom; a kind of cue in its
    # own band is a lane of its own.
    {'key': 'full_height', 'type': 'bool', 'default': False, 'group': 'box'},
    {'key': 'top', 'type': 'int', 'default': 55, 'min': 0, 'max': 400, 'unit': 'px', 'group': 'box',
     'hidden_if': 'full_height'},
    {'key': 'bottom', 'type': 'int', 'default': 25, 'min': 0, 'max': 400, 'unit': 'px', 'group': 'box',
     'hidden_if': 'full_height'},
    # Space between two boxes that touch: half of it off each side.
    {'key': 'gap', 'type': 'int', 'default': 0, 'min': 0, 'max': 20, 'unit': 'px', 'group': 'box'},
    {'key': 'corner_radius', 'type': 'int', 'default': 3, 'min': 0, 'max': 40, 'unit': 'px', 'group': 'box'},
    {'key': 'border_width', 'type': 'number', 'default': 0.0, 'min': 0.0, 'max': 6.0, 'step': 0.5, 'unit': 'px',
     'group': 'box'},
    {'key': 'border_color', 'type': 'color', 'default': '#ff6a7483', 'group': 'box'},
    {'key': 'selected_border_color', 'type': 'color', 'default': '#ffb8cee0', 'group': 'box'},
    {'key': 'speaker_strip_height', 'type': 'int', 'default': 3, 'min': 1, 'max': 20, 'unit': 'px', 'group': 'box'},

    {'key': 'fill_color', 'type': 'color', 'default': '#e6c8dbe9', 'group': 'fill'},
    {'key': 'selected_fill_color', 'type': 'color', 'default': '#cc3e5363', 'group': 'fill'},
    # A gradient from the fill colour at the top to these at the bottom.
    {'key': 'fill_gradient', 'type': 'bool', 'default': False, 'group': 'fill'},
    {'key': 'fill_gradient_color', 'type': 'color', 'default': '#ff8fa9bc', 'group': 'fill',
     'visible_if': 'fill_gradient'},
    {'key': 'selected_fill_gradient_color', 'type': 'color', 'default': '#cc1f2a33', 'group': 'fill',
     'visible_if': 'fill_gradient'},

    {'key': 'text_color', 'type': 'color', 'default': '#ff304251', 'group': 'text'},
    {'key': 'selected_text_color', 'type': 'color', 'default': '#ffb8cee0', 'group': 'text'},
    {'key': 'failed_check_text_color', 'type': 'color', 'default': '#ff9e1a1a', 'group': 'text'},
    {'key': 'font', 'type': 'font', 'default': 'Montserrat', 'group': 'text'},
    {'key': 'font_size', 'type': 'int', 'default': 10, 'min': 5, 'max': 32, 'unit': 'pt', 'group': 'text'},
    {'key': 'font_bold', 'type': 'bool', 'default': False, 'group': 'text'},
    {'key': 'text_vertical_alignment', 'type': 'select', 'default': 'top', 'options': ['top', 'center', 'bottom'],
     'group': 'text'},
    {'key': 'padding_x', 'type': 'int', 'default': 26, 'min': 0, 'max': 100, 'unit': 'px', 'group': 'text'},
    {'key': 'padding_y', 'type': 'int', 'default': 6, 'min': 0, 'max': 100, 'unit': 'px', 'group': 'text'},

    # The start and end edges, dragged to retime a cue: a glow that fades
    # inwards (the outline at the edge), a bar along it, or nothing; on
    # the edge under the mouse, or on every box's both edges.
    {'key': 'edge_style', 'type': 'select', 'default': 'glow', 'options': ['glow', 'bar', 'none'], 'group': 'edges'},
    {'key': 'edge_show', 'type': 'select', 'default': 'hover', 'options': ['hover', 'always'], 'group': 'edges',
     'hidden_if': ['edge_style', 'none']},
    {'key': 'edge_handle_width', 'type': 'int', 'default': 20, 'min': 2, 'max': 80, 'unit': 'px', 'group': 'edges',
     'visible_when': ['edge_style', 'glow']},
    {'key': 'edge_handle_thickness', 'type': 'number', 'default': 2.0, 'min': 0.5, 'max': 12.0, 'step': 0.5,
     'unit': 'px', 'group': 'edges', 'hidden_if': ['edge_style', 'none']},
    {'key': 'edge_start_color', 'type': 'color', 'default': '#ffffffff', 'group': 'edges',
     'hidden_if': ['edge_style', 'none']},
    {'key': 'edge_end_color', 'type': 'color', 'default': '#ffffffff', 'group': 'edges',
     'hidden_if': ['edge_style', 'none']},
    # How near an edge the mouse grabs it, whatever is drawn there.
    {'key': 'edge_grab_width', 'type': 'int', 'default': 20, 'min': 4, 'max': 80, 'unit': 'px', 'group': 'edges'},
]

def _checked(raw_fields):
    fields, keys = [], set()
    for raw in raw_fields:
        field = _check_field(raw, keys)
        keys.add(field['key'])
        fields.append(field)
    return fields


TIMELINE_FIELDS = _checked(_TIMELINE_FIELDS)
BOX_FIELDS = _checked(_BOX_FIELDS)
TIMELINE_FIELDS_BY_KEY = {f['key']: f for f in TIMELINE_FIELDS}
BOX_FIELDS_BY_KEY = {f['key']: f for f in BOX_FIELDS}
TIMELINE_DEFAULTS = {f['key']: f['default'] for f in TIMELINE_FIELDS}
BOX_DEFAULTS = {f['key']: f['default'] for f in BOX_FIELDS}

# The kinds of cue the timeline draws a box for, each with what its boxes
# look like by default where that is not the subtitles' look. Subtitles
# only, for now; titles, on-screen text and the rest register themselves.
KINDS = {SUBTITLE: {}}


def register_kind(kind, defaults=None):
    """A kind of cue with boxes of its own look: the subtitles' look but for
    `defaults`, until a theme or the user sets it."""
    KINDS[kind] = clean_box(defaults or {})
    _changed()


def kind_of(segment):
    """The kind of cue `segment` is drawn as: its own if registered, a
    subtitle otherwise."""
    kind = segment.get('kind') if hasattr(segment, 'get') else None
    return kind if kind in KINDS else SUBTITLE


def time_label(seconds, time_format, framerate=25.0, precise=False):
    """`seconds` as the ruler writes it: clock (mm:ss, hh:mm:ss past an
    hour), seconds, timecode (hh:mm:ss:ff) or frames. `precise` adds the
    milliseconds where the format has no frames."""
    seconds = max(0.0, float(seconds))
    fps = max(1, int(round(framerate or 25.0)))
    if time_format == 'frames':
        return str(int(seconds * fps + 1e-6))
    if time_format == 'timecode':
        frames = int(seconds * fps + 1e-6)
        total, frame = divmod(frames, fps)
        return f'{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}:{frame:02d}'
    if time_format == 'seconds':
        return f'{seconds:.3f}s' if precise else f'{int(seconds)}s'
    milliseconds = int(round(seconds * 1000)) if precise else int(seconds) * 1000
    total, milliseconds = divmod(milliseconds, 1000)
    clock = f'{total // 60 % 60:02d}:{total % 60:02d}'
    if total >= 3600:
        clock = f'{total // 3600:02d}:' + clock
    return clock + (f'.{milliseconds:03d}' if precise else '')


def label_step(pixels_a_second, spacing):
    """Seconds between the ruler's labels for them to be `spacing` apart."""
    return next((step for step in LABEL_STEPS if step * pixels_a_second >= spacing), LABEL_STEPS[-1])


def _config():
    timeline = session.CONFIG.get('timeline') if isinstance(session.CONFIG, dict) else None
    if not isinstance(timeline, dict):
        timeline = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['timeline'] = timeline
    saved = timeline.get('style')
    if not isinstance(saved, dict):
        saved = timeline['style'] = {}
    if not isinstance(saved.get('timeline'), dict):
        saved['timeline'] = {}
    if not isinstance(saved.get('boxes'), dict):
        saved['boxes'] = {}
    return saved


def _clean_fields(values, fields_by_key):
    result = {}
    if not isinstance(values, dict):
        return result
    for key, value in values.items():
        field = fields_by_key.get(key)
        if field is None:
            continue
        coerced = coerce(field, value)
        if coerced is not None:
            result[key] = coerced
    return result


def clean_box(values):
    """A kind's box look as it can be held: known keys, values their field holds."""
    return _clean_fields(values, BOX_FIELDS_BY_KEY)


def clean(look):
    """`look` as it can be held: {"timeline": {...}, "boxes": {kind: {...}}},
    the keys it knows, each a value its field can hold. A kind Subtitld
    does not know (yet) is kept as it is set: a theme made for a later
    Subtitld loses nothing here. What a theme from elsewhere is read
    through."""
    if not isinstance(look, dict):
        return {'timeline': {}, 'boxes': {}}
    boxes = look.get('boxes') if isinstance(look.get('boxes'), dict) else {}
    return {'timeline': _clean_fields(look.get('timeline'), TIMELINE_FIELDS_BY_KEY),
            'boxes': {kind: clean_box(values) for kind, values in boxes.items()
                      if isinstance(kind, str) and isinstance(values, dict)}}


def values(kind=None):
    """Every setting of the timeline, or of a kind's boxes: what the user
    set, or its default. A kind's box takes the subtitles' look for what
    neither it nor its defaults set."""
    saved = clean(_config())
    if kind is None:
        return {**TIMELINE_DEFAULTS, **saved['timeline']}
    result = {**BOX_DEFAULTS, **saved['boxes'].get(SUBTITLE, {})}
    if kind != SUBTITLE:
        result.update(KINDS.get(kind, {}))
        result.update(saved['boxes'].get(kind, {}))
    return result


def changes():
    """Only what differs from the defaults: the look as a theme keeps it."""
    saved = clean(_config())
    return {'timeline': {k: v for k, v in saved['timeline'].items() if v != TIMELINE_DEFAULTS[k]},
            'boxes': {kind: box for kind, box in saved['boxes'].items() if box}}


def _inherited(kind):
    """What a kind's box looks like with nothing of its own set."""
    if kind == SUBTITLE:
        return dict(BOX_DEFAULTS)
    return {**values(SUBTITLE), **KINDS.get(kind, {})}


def set_value(key, value, kind=None):
    """Save one setting of the timeline, or of a kind's boxes; KeyError for
    a key there is no such setting, ValueError for a value its field
    cannot hold. The value as saved. What a kind inherits is not saved for
    it: it follows what it is inherited from."""
    field = (TIMELINE_FIELDS_BY_KEY if kind is None else BOX_FIELDS_BY_KEY)[key]
    coerced = coerce(field, value)
    if coerced is None:
        raise ValueError(f'{key}: {value!r} is not a {field["type"]}')
    config = _config()
    if kind is None:
        saved, default = config['timeline'], field['default']
    else:
        saved, default = config['boxes'].setdefault(kind, {}), _inherited(kind)[key]
    if coerced == default:
        saved.pop(key, None)
    else:
        saved[key] = coerced
    if kind is not None and not saved:
        config['boxes'].pop(kind, None)
    _changed()
    return coerced


def set_values(look):
    """Replace the whole look (a theme's); what cannot be held is left out."""
    look = clean(look)
    config = _config()
    config['timeline'] = {k: v for k, v in look['timeline'].items() if v != TIMELINE_DEFAULTS[k]}
    config['boxes'] = {kind: box for kind, box in look['boxes'].items() if box}
    _changed()


def reset():
    set_values({})


# The colours kept loose in CONFIG['timeline'] before it had a style: the
# subtitles' box ones by their names then.
_LEGACY_BOX_KEYS = {'subtitle_fill_color': 'fill_color', 'selected_subtitle_fill_color': 'selected_fill_color',
                    'subtitle_border_color': 'border_color', 'subtitle_text_color': 'text_color',
                    'selected_subtitle_text_color': 'selected_text_color'}


def migrate(config):
    """The colours used to be kept loose in CONFIG['timeline']: into its
    style, once."""
    timeline = config.get('timeline')
    if not isinstance(timeline, dict):
        return
    loose_timeline = {key: timeline.pop(key) for key in list(timeline) if key in TIMELINE_FIELDS_BY_KEY}
    loose_box = {_LEGACY_BOX_KEYS[key]: timeline.pop(key) for key in list(timeline) if key in _LEGACY_BOX_KEYS}
    if not (loose_timeline or loose_box):
        return
    saved = timeline.get('style')
    if not isinstance(saved, dict):
        saved = timeline['style'] = {}
    for key, value in _clean_fields(loose_timeline, TIMELINE_FIELDS_BY_KEY).items():
        saved.setdefault('timeline', {}).setdefault(key, value)
    for key, value in clean_box(loose_box).items():
        saved.setdefault('boxes', {}).setdefault(SUBTITLE, {}).setdefault(key, value)


class _Settings:
    """Settings with their colours made once. color() gives a copy, free to
    change; [] the setting as it is saved."""

    def __init__(self, settings, fields_by_key):
        self._values = settings
        self._colors = {key: QColor(value) for key, value in settings.items()
                        if fields_by_key[key]['type'] == 'color'}

    def __getitem__(self, key):
        return self._values[key]

    def color(self, key):
        return QColor(self._colors[key])


class BoxLook(_Settings):
    """A kind's boxes: their settings, and the font of their text."""

    def __init__(self, settings):
        super().__init__(settings, BOX_FIELDS_BY_KEY)
        self.font = QFont(settings['font'], settings['font_size'])
        self.font.setBold(settings['font_bold'])


class Look(_Settings):
    """The timeline's look: its own settings, and box(kind) for each kind of
    cue's boxes."""

    def __init__(self):
        super().__init__(values(), TIMELINE_FIELDS_BY_KEY)
        self.ruler_font = QFont(self['ruler_font'], self['ruler_font_size'])
        self._boxes = {}

    def box(self, kind=SUBTITLE):
        if kind not in self._boxes:
            self._boxes[kind] = BoxLook(values(kind if kind in KINDS else SUBTITLE))
        return self._boxes[kind]


_look = None


def _changed():
    global _look
    _look = None


def current():
    """The look the timeline is painted with."""
    global _look
    if _look is None:
        _look = Look()
    return _look
