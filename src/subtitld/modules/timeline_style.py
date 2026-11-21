"""How the timeline looks: its colours, fonts and the shape of its subtitles.

Every setting is a field in the shape subtitle styles' manifests use
(modules.subtitle_styles): a key, a type (number, int, bool, select,
color, font), a default and, for numbers, a range and a unit. `group`
says which box of the settings it is shown in; `visible_if` names a bool
it only matters with, `hidden_if` one it does not matter with.

What the user set is kept in CONFIG['timeline']['style'], a value a key,
defaults left out (a later Subtitld may change them). That dict alone is
the look: a timeline theme is one of them with a name.

The painting code asks current() for the look: values with their QColors
and QFonts made once, until a setting changes.
"""

from PySide6.QtGui import QColor, QFont

from subtitld.modules import session
from subtitld.modules.subtitle_styles import _check_field, coerce

GROUPS = ('background', 'ruler', 'grid', 'waveform', 'subtitle_box', 'subtitle_fill', 'subtitle_text', 'cursor', 'marks')

_FIELDS = [
    # The timeline behind everything; transparent shows the window's own.
    {'key': 'background_color', 'type': 'color', 'default': '#00000000', 'group': 'background'},
    {'key': 'ruler_background_color', 'type': 'color', 'default': '#00000000', 'group': 'background'},

    {'key': 'show_ruler', 'type': 'bool', 'default': True, 'group': 'ruler'},
    {'key': 'time_text_color', 'type': 'color', 'default': '#b8b8cee0', 'group': 'ruler', 'visible_if': 'show_ruler'},
    {'key': 'ruler_font', 'type': 'font', 'default': 'Ubuntu Mono', 'group': 'ruler', 'visible_if': 'show_ruler'},
    {'key': 'ruler_font_size', 'type': 'int', 'default': 8, 'min': 5, 'max': 24, 'unit': 'pt', 'group': 'ruler',
     'visible_if': 'show_ruler'},
    {'key': 'ruler_top', 'type': 'int', 'default': 27, 'min': 0, 'max': 200, 'unit': 'px', 'group': 'ruler',
     'visible_if': 'show_ruler'},

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

    # Where the subtitles' boxes are: from top to bottom of the timeline, or
    # a band `subtitle_top` from its top and `subtitle_bottom` from its bottom.
    {'key': 'subtitle_full_height', 'type': 'bool', 'default': False, 'group': 'subtitle_box'},
    {'key': 'subtitle_top', 'type': 'int', 'default': 55, 'min': 0, 'max': 400, 'unit': 'px', 'group': 'subtitle_box',
     'hidden_if': 'subtitle_full_height'},
    {'key': 'subtitle_bottom', 'type': 'int', 'default': 25, 'min': 0, 'max': 400, 'unit': 'px',
     'group': 'subtitle_box', 'hidden_if': 'subtitle_full_height'},
    # Space between two subtitles that touch: half of it off each side.
    {'key': 'subtitle_gap', 'type': 'int', 'default': 0, 'min': 0, 'max': 20, 'unit': 'px',
     'group': 'subtitle_box'},
    {'key': 'subtitle_corner_radius', 'type': 'int', 'default': 3, 'min': 0, 'max': 40,
     'unit': 'px', 'group': 'subtitle_box'},
    {'key': 'subtitle_border_width', 'type': 'number', 'default': 0.0, 'min': 0.0, 'max': 6.0, 'step': 0.5,
     'unit': 'px', 'group': 'subtitle_box'},
    {'key': 'subtitle_border_color', 'type': 'color', 'default': '#ff6a7483', 'group': 'subtitle_box'},
    {'key': 'selected_subtitle_border_color', 'type': 'color', 'default': '#ffb8cee0', 'group': 'subtitle_box'},
    {'key': 'speaker_strip_height', 'type': 'int', 'default': 3, 'min': 1, 'max': 20,
     'unit': 'px', 'group': 'subtitle_box'},

    {'key': 'subtitle_fill_color', 'type': 'color', 'default': '#e6c8dbe9', 'group': 'subtitle_fill'},
    {'key': 'selected_subtitle_fill_color', 'type': 'color', 'default': '#cc3e5363', 'group': 'subtitle_fill'},
    # A gradient from the fill colour at the top to these at the bottom.
    {'key': 'subtitle_fill_gradient', 'type': 'bool', 'default': False, 'group': 'subtitle_fill'},
    {'key': 'subtitle_fill_gradient_color', 'type': 'color', 'default': '#ff8fa9bc', 'group': 'subtitle_fill',
     'visible_if': 'subtitle_fill_gradient'},
    {'key': 'selected_subtitle_fill_gradient_color', 'type': 'color', 'default': '#cc1f2a33', 'group': 'subtitle_fill',
     'visible_if': 'subtitle_fill_gradient'},

    {'key': 'subtitle_text_color', 'type': 'color', 'default': '#ff304251', 'group': 'subtitle_text'},
    {'key': 'selected_subtitle_text_color', 'type': 'color', 'default': '#ffb8cee0', 'group': 'subtitle_text'},
    {'key': 'failed_check_text_color', 'type': 'color', 'default': '#ff9e1a1a', 'group': 'subtitle_text'},
    {'key': 'subtitle_font', 'type': 'font', 'default': 'Montserrat', 'group': 'subtitle_text'},
    {'key': 'subtitle_font_size', 'type': 'int', 'default': 10, 'min': 5, 'max': 32, 'unit': 'pt',
     'group': 'subtitle_text'},
    {'key': 'subtitle_font_bold', 'type': 'bool', 'default': False, 'group': 'subtitle_text'},
    {'key': 'subtitle_text_vertical_alignment', 'type': 'select', 'default': 'top',
     'options': ['top', 'center', 'bottom'], 'group': 'subtitle_text'},
    {'key': 'subtitle_padding_x', 'type': 'int', 'default': 26, 'min': 0, 'max': 100, 'unit': 'px',
     'group': 'subtitle_text'},
    {'key': 'subtitle_padding_y', 'type': 'int', 'default': 6, 'min': 0, 'max': 100, 'unit': 'px',
     'group': 'subtitle_text'},

    {'key': 'cursor_color', 'type': 'color', 'default': '#ccff0000', 'group': 'cursor'},
    {'key': 'cursor_width', 'type': 'number', 'default': 2.0, 'min': 0.5, 'max': 8.0, 'step': 0.5, 'unit': 'px',
     'group': 'cursor'},
    {'key': 'cursor_text_indicator', 'type': 'color', 'default': '#ffffffff', 'group': 'cursor'},

    {'key': 'selected_subtitle_arrow_color', 'type': 'color', 'default': '#ff969696', 'group': 'marks'},
    {'key': 'dub_waveform_color', 'type': 'color', 'default': '#ffffffff', 'group': 'marks'},
    {'key': 'onset_marker_color', 'type': 'color', 'default': '#ffffd9a0', 'group': 'marks'},
]


def _checked_fields():
    fields, keys = [], set()
    for raw in _FIELDS:
        field = _check_field(raw, keys)
        keys.add(field['key'])
        fields.append(field)
    return fields


FIELDS = _checked_fields()
FIELDS_BY_KEY = {f['key']: f for f in FIELDS}
DEFAULTS = {f['key']: f['default'] for f in FIELDS}


def _config():
    timeline = session.CONFIG.get('timeline') if isinstance(session.CONFIG, dict) else None
    if not isinstance(timeline, dict):
        timeline = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['timeline'] = timeline
    saved = timeline.get('style')
    if not isinstance(saved, dict):
        saved = timeline['style'] = {}
    return saved


def clean(values):
    """`values` as a look holds them: the keys it knows, each a value its
    field can hold. What a theme from elsewhere is read through."""
    result = {}
    if not isinstance(values, dict):
        return result
    for key, value in values.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            continue
        coerced = coerce(field, value)
        if coerced is not None:
            result[key] = coerced
    return result


def values():
    """Every setting: what the user set, or its default."""
    return {**DEFAULTS, **clean(_config())}


def changes():
    """Only what differs from the defaults: the look as a theme keeps it."""
    return {key: value for key, value in clean(_config()).items() if value != DEFAULTS[key]}


def set_value(key, value):
    """Save one setting; KeyError for a key there is no such setting,
    ValueError for a value its field cannot hold. The value as saved."""
    field = FIELDS_BY_KEY[key]
    coerced = coerce(field, value)
    if coerced is None:
        raise ValueError(f'{key}: {value!r} is not a {field["type"]}')
    saved = _config()
    if coerced == field['default']:
        saved.pop(key, None)
    else:
        saved[key] = coerced
    _changed()
    return coerced


def set_values(new_values):
    """Replace the whole look (a theme's values); unknown keys and values
    their field cannot hold are left out."""
    saved = _config()
    saved.clear()
    saved.update({key: value for key, value in clean(new_values).items() if value != DEFAULTS[key]})
    _changed()


def reset():
    set_values({})


def migrate(config):
    """The colours used to be kept loose in CONFIG['timeline']: into its
    style, once."""
    timeline = config.get('timeline')
    if not isinstance(timeline, dict):
        return
    loose = {key: timeline.pop(key) for key in list(timeline) if key in FIELDS_BY_KEY}
    if not loose:
        return
    saved = timeline.get('style')
    if not isinstance(saved, dict):
        saved = timeline['style'] = {}
    for key, value in clean(loose).items():
        saved.setdefault(key, value)


class Look:
    """The settings, with their colours and fonts made once. color() gives
    a copy, free to change; value() and [] the setting as it is saved."""

    def __init__(self, settings):
        self._values = settings
        self._colors = {key: QColor(value) for key, value in settings.items()
                        if FIELDS_BY_KEY[key]['type'] == 'color'}
        self.ruler_font = QFont(settings['ruler_font'], settings['ruler_font_size'])
        self.subtitle_font = QFont(settings['subtitle_font'], settings['subtitle_font_size'])
        self.subtitle_font.setBold(settings['subtitle_font_bold'])

    def __getitem__(self, key):
        return self._values[key]

    def value(self, key):
        return self._values[key]

    def color(self, key):
        return QColor(self._colors[key])


_look = None


def _changed():
    global _look
    _look = None


def current():
    """The look the timeline is painted with."""
    global _look
    if _look is None:
        _look = Look(values())
    return _look

