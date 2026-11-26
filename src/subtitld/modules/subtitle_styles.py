"""Subtitle styles: how the preview draws a subtitle, picked by the user.

A style is a folder with a manifest (style.json) and an HTML template the
preview renders each subtitle with. The manifest names the style and lists
the settings it offers, in the fields add-ons use for theirs:

    {
      "id": "subtitld.classic",
      "name": "Classic",
      "version": "26.10.1",
      "template": "style.html",
      "animated": false,
      "fields": [
        {"key": "font_size", "type": "int", "label": "Size", "default": 40,
         "min": 8, "max": 200, "unit": "px"},
        {"key": "shadow_color", "type": "color", "default": "#ff000000",
         "visible_if": "shadow_enabled"},
        ...
      ]
    }

Field types: number and int (min, max, step, unit), bool, select (options:
values, or {"value", "label"}), text, color ("#aarrggbb", as Qt writes it)
and font (a family name). `visible_if` names a bool field the field only
matters with. `animated` says the template moves (CSS animations or
transitions): it is rendered a frame at a time, its animations held at
each frame's moment; otherwise once a subtitle. Labels are the manifest's own, unless the locale files have
`subtitle_styles.<style id>.<key>`.

The style a project's subtitles are drawn with, and what it set of the
styles' settings, are the project's (SUBTITLE.theme, saved in its USFX):

    {"id": "subtitld.classic", "version": "26.12.1",
     "values": {"subtitld.classic": {"font_size": 48}},
     "speakers": {"Ana": {"subtitld.classic": {"color": "#ffe5a823"}}}}

`values` is per style, shared by every subtitle drawn with it; `speakers`
holds a speaker's own value of a field its style marks `per_speaker`. A
subtitle may name a style of its own (segment['theme']). A project that
says nothing of a style (one opened from another format, an older one)
gets what the app has: CONFIG['subtitle_style'] (below), which also keeps
the latest choice and settings as where a new project starts.

Styles come from theme files of the subtitle kind (modules.themes: Classic
is one), then from folders in Subtitld itself (subtitld/subtitle_styles)
and in the user's data folder (subtitle_styles there); a style cannot take
an id one before it has. What the user set is kept per style in
CONFIG['subtitle_style'], so going to another style and back keeps it:

    {"current": "subtitld.classic", "settings": {"subtitld.classic": {...}}}
"""

import json
import re
from dataclasses import dataclass, field as dataclass_field

from subtitld.modules import session

DEFAULT_STYLE = 'subtitld.classic'
MANIFEST = 'style.json'
FIELD_TYPES = ('number', 'int', 'bool', 'select', 'text', 'color', 'font')
_COLOR = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})')

# The preview's style settings before styles: what Classic's fields are
# called, so they move over from CONFIG['videoplayer'] as they were.
_LEGACY_KEYS = ('font_family', 'font_size', 'color', 'shadow_enabled', 'shadow_x', 'shadow_y', 'shadow_color',
                'backgroundbox_enabled', 'backgroundbox_padding', 'backgroundbox_border_radius',
                'backgroundbox_color')


class StyleError(ValueError):
    """A manifest Subtitld cannot use, and why."""


@dataclass
class Style:
    id: str
    name: str
    path: object                    # the style's folder (pathlib.Path)
    template: str
    version: str = ''
    fields: list = dataclass_field(default_factory=list)
    builtin: bool = False
    animated: bool = False          # rendered a frame at a time, not once a subtitle
    file: object = None             # the modules.themes.Theme, for a style that is a theme file

    def field(self, key):
        return next((f for f in self.fields if f['key'] == key), None)


def _check_field(raw, keys):
    if not isinstance(raw, dict) or not isinstance(raw.get('key'), str) or not raw['key']:
        raise StyleError('a field without a key')
    key = raw['key']
    if key in keys:
        raise StyleError(f'field "{key}" twice')
    kind = raw.get('type')
    if kind not in FIELD_TYPES:
        raise StyleError(f'field "{key}": unknown type {kind!r}')
    if kind == 'select':
        options = raw.get('options')
        if not isinstance(options, list) or not options:
            raise StyleError(f'field "{key}": a select needs options')
        # One shape for the rest of Subtitld: {"value", "label"}.
        raw = dict(raw, options=[o if isinstance(o, dict) else {'value': o, 'label': str(o)} for o in options])
    if 'default' not in raw:
        raise StyleError(f'field "{key}": no default')
    field_ = dict(raw)
    default = coerce(field_, raw['default'])
    if default is None:
        raise StyleError(f'field "{key}": default {raw["default"]!r} is not a {kind}')
    field_['default'] = default
    return field_


def load(path, builtin=False):
    """The style in folder `path`; StyleError if its manifest is unusable."""
    try:
        manifest = json.loads((path / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise StyleError(f'{MANIFEST}: {error}') from error
    if not isinstance(manifest, dict):
        raise StyleError(f'{MANIFEST} is not an object')
    style_id, name = manifest.get('id'), manifest.get('name')
    if not isinstance(style_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', style_id):
        raise StyleError(f'id {style_id!r}: lowercase letters, digits, ".", "_" and "-" only')
    if not isinstance(name, str) or not name.strip():
        raise StyleError('no name')
    template = manifest.get('template', 'style.html')
    if not isinstance(template, str) or '/' in template or '\\' in template or not (path / template).is_file():
        raise StyleError(f'template {template!r} is not a file in the style\'s folder')
    fields, keys = [], set()
    for raw in manifest.get('fields') or []:
        checked = _check_field(raw, keys)
        keys.add(checked['key'])
        fields.append(checked)
    for checked in fields:
        condition = checked.get('visible_if')
        if condition is not None and not any(f['key'] == condition and f['type'] == 'bool' for f in fields):
            raise StyleError(f'field "{checked["key"]}": visible_if names no bool field')
    animated = manifest.get('animated', False)
    if not isinstance(animated, bool):
        raise StyleError('animated is not true or false')
    return Style(style_id, name.strip(), path, template, str(manifest.get('version', '')), fields, builtin, animated)


def folders():
    """Where styles are looked for: Subtitld's own, then the user's."""
    return [(session.PATH_SUBTITLD / 'subtitle_styles', True),
            (session.PATH_SUBTITLD_USER_DATA / 'subtitle_styles', False)]


_styles = None
problems = {}       # folder → why it was not loaded


def available(reload=False):
    """Every usable style by id, built-in ones first."""
    global _styles
    if _styles is None or reload:
        from subtitld.modules import themes
        _styles = {}
        problems.clear()
        for theme in themes.available('subtitle', reload=reload).values():
            _styles[theme.id] = Style(theme.id, theme.name, theme.path.parent, theme.path.name, theme.version,
                                      theme.fields, theme.builtin, theme.animated, theme)
        for root, builtin in folders():
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()):
                try:
                    style = load(path, builtin)
                except StyleError as error:
                    problems[path] = str(error)
                    continue
                if style.id in _styles:
                    problems[path] = f'id {style.id} is taken'
                    continue
                _styles[style.id] = style
    return _styles


def _project(create=False):
    """The open project's theme table (SUBTITLE.theme), made when `create`;
    None when it has none."""
    subtitle = getattr(session, 'SUBTITLE', None)
    if subtitle is None or not hasattr(subtitle, 'theme'):
        return None
    if not isinstance(subtitle.theme, dict):
        if not create:
            return None
        subtitle.theme = {}
    return subtitle.theme


def current_id():
    """The style the project's subtitles are drawn with: its own, else
    the app's."""
    table = _project() or {}
    style_id = table.get('id') or _config().get('current') or DEFAULT_STYLE
    return style_id if style_id in available() else DEFAULT_STYLE


def get(style_id=None):
    """The style `style_id` (the project's by default); the default style
    when there is no such style."""
    styles = available()
    return styles.get(style_id or current_id()) or styles[DEFAULT_STYLE]


def of(segment):
    """The style a subtitle is drawn with: its own, where it names one
    there is, else the project's."""
    own = segment.get('theme') if isinstance(segment, dict) else None
    return get(own if own in available() else None)


def coerce(field_, value):
    """`value` as `field_` holds it, or None when it cannot hold it."""
    kind = field_['type']
    if kind in ('number', 'int'):
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number:
            return None
        if field_.get('min') is not None:
            number = max(float(field_['min']), number)
        if field_.get('max') is not None:
            number = min(float(field_['max']), number)
        return int(round(number)) if kind == 'int' else number
    if kind == 'bool':
        return value if isinstance(value, bool) else None
    if kind == 'select':
        return value if any(o['value'] == value for o in field_['options']) else None
    if kind == 'color':
        if not isinstance(value, str) or not _COLOR.fullmatch(value):
            return None
        return ('#ff' + value[1:] if len(value) == 7 else value).lower()
    if kind in ('text', 'font'):
        return value if isinstance(value, str) else None
    return None


def _config():
    config = session.CONFIG.get('subtitle_style') if isinstance(session.CONFIG, dict) else None
    if not isinstance(config, dict):
        config = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['subtitle_style'] = config
    return config


def _saved(style_id):
    """What was set of a style's settings: the project's, where it says
    anything of the style, else the app's."""
    table = _project() or {}
    project = table.get('values') if isinstance(table.get('values'), dict) else {}
    if isinstance(project.get(style_id), dict):
        return project[style_id]
    return (_config().get('settings') or {}).get(style_id) or {}


def _speaker_saved(style_id, speaker):
    table = _project() or {}
    speakers = table.get('speakers') if isinstance(table.get('speakers'), dict) else {}
    own = (speakers.get(speaker) or {}).get(style_id) if isinstance(speakers.get(speaker), dict) else None
    return own if isinstance(own, dict) else {}


def values(style_id=None, speaker=None):
    """The style's settings: what was set, where the field can hold it,
    and the field's default otherwise; `speaker`'s own values of the
    fields the style marks `per_speaker` over them."""
    style = get(style_id)
    saved = _saved(style.id)
    own = _speaker_saved(style.id, speaker) if speaker else {}
    result = {}
    for f in style.fields:
        value = None
        if f.get('per_speaker') and f['key'] in own:
            value = coerce(f, own[f['key']])
        if value is None and f['key'] in saved:
            value = coerce(f, saved[f['key']])
        result[f['key']] = f['default'] if value is None else value
    return result


def speaker_values(style_id, speaker):
    """What `speaker` set for themself of a style's per-speaker fields."""
    style = get(style_id)
    own = _speaker_saved(style.id, speaker)
    return {f['key']: own[f['key']] for f in style.fields
            if f.get('per_speaker') and f['key'] in own and coerce(f, own[f['key']]) is not None}


def _store(saved, field_, value):
    # A default is not saved: a later version of the style may change it.
    if value == field_['default']:
        saved.pop(field_['key'], None)
    else:
        saved[field_['key']] = value


def set_value(key, value, style_id=None, speaker=None):
    """Save one setting of the style, in the project (and as where new
    projects start); `speaker`: that speaker's own, of a per-speaker field
    (None as `value` takes theirs away). ValueError when the field cannot
    hold `value`, KeyError when the style has no such field."""
    style = get(style_id)
    field_ = style.field(key)
    if field_ is None or (speaker and not field_.get('per_speaker')):
        raise KeyError(key)
    if speaker and value is None:
        table = _project() or {}
        own = (((table.get('speakers') or {}).get(speaker) or {}).get(style.id)) or {}
        own.pop(key, None)
        return None
    coerced = coerce(field_, value)
    if coerced is None:
        raise ValueError(f'{key}: {value!r} is not a {field_["type"]}')
    if speaker:
        speakers = _project(create=True).setdefault('speakers', {})
        speakers.setdefault(speaker, {}).setdefault(style.id, {})[key] = coerced
        return coerced
    table = _project(create=True)
    project = table.setdefault('values', {})
    if style.id not in project:
        # The project takes over the style with what it showed until now.
        project[style.id] = dict(_saved(style.id))
    _store(project[style.id], field_, coerced)
    _store(_config().setdefault('settings', {}).setdefault(style.id, {}), field_, coerced)
    return coerced


def set_current(style_id):
    """Draw the project's subtitles with `style_id` (and start new projects
    with it)."""
    if style_id not in available():
        raise KeyError(style_id)
    table = _project(create=True)
    table['id'] = style_id
    table['version'] = available()[style_id].version
    _config()['current'] = style_id


def rename_speaker(old, new):
    """A speaker renamed: their own values go with them."""
    speakers = (_project() or {}).get('speakers')
    if isinstance(speakers, dict) and old in speakers and old != new:
        speakers[new] = speakers.pop(old)


def project_table(segments=()):
    """The project's theme table as its file keeps it: the style it is
    drawn with, its version, what was set of it and of the styles its
    subtitles name (the app's settings, where the project says nothing of
    one yet), and the speakers' own values."""
    table = _project() or {}
    style = get()
    used = [style.id] + [s for s in dict.fromkeys(seg.get('theme') for seg in segments
                                                   if isinstance(seg, dict) and seg.get('theme') in available())
                         if s != style.id]
    # What it says of styles not here (one it names, not installed yet) is
    # kept as it was, for when they are.
    kept = {style_id: dict(own) for style_id, own in (table.get('values') or {}).items()
            if style_id not in available() and isinstance(own, dict)}
    result = {'id': style.id, 'version': style.version,
              'values': {**kept, **{style_id: dict(_saved(style_id)) for style_id in used}}}
    if table.get('id') and table['id'] not in available():
        result['id'], result['version'] = table['id'], str(table.get('version') or '')
    speakers = {name: {sid: dict(vals) for sid, vals in own.items() if isinstance(vals, dict) and vals}
                for name, own in (table.get('speakers') or {}).items() if isinstance(own, dict)}
    speakers = {name: own for name, own in speakers.items() if own}
    if speakers:
        result['speakers'] = speakers
    return result


def adopt(table):
    """A theme table read from a file, checked: the parts Subtitld can use
    (unknown styles' values are kept, for when they are installed), or
    None."""
    if not isinstance(table, dict):
        return None
    result = {}
    if isinstance(table.get('id'), str) and table['id']:
        result['id'] = table['id']
    if isinstance(table.get('version'), str):
        result['version'] = table['version']
    values_ = table.get('values')
    if isinstance(values_, dict):
        result['values'] = {sid: dict(v) for sid, v in values_.items() if isinstance(sid, str) and isinstance(v, dict)}
    speakers = table.get('speakers')
    if isinstance(speakers, dict):
        result['speakers'] = {name: {sid: dict(v) for sid, v in own.items() if isinstance(v, dict)}
                              for name, own in speakers.items() if isinstance(name, str) and isinstance(own, dict)}
    return result or None


def migrate(config):
    """Move the preview's style settings from CONFIG['videoplayer'], where
    they were before styles, to Classic's. Once: they leave videoplayer."""
    videoplayer = config.get('videoplayer')
    if not isinstance(videoplayer, dict):
        return
    old = {key: videoplayer.pop(key) for key in _LEGACY_KEYS if key in videoplayer}
    if not old:
        return
    style_config = config.get('subtitle_style')
    if not isinstance(style_config, dict):
        style_config = config['subtitle_style'] = {}
    settings = style_config.setdefault('settings', {}).setdefault(DEFAULT_STYLE, {})
    for key, value in old.items():
        settings.setdefault(key, value)


def css_color(value):
    """A "#aarrggbb" colour as CSS writes it."""
    alpha, rgb = int(value[1:3], 16), value[3:]
    return f'#{rgb}' if alpha == 255 else f'rgba({int(rgb[0:2], 16)}, {int(rgb[2:4], 16)}, {int(rgb[4:6], 16)}, {alpha / 255:.3f})'


def css_string(value):
    """`value` as a quoted CSS string. Not json.dumps: CSS escapes are
    a backslash and hex digits, so JSON's \\u2014 would read as "u2014"."""
    def escape(match):
        char = match.group()
        return '\\' + char if char in '"\\' else f'\\{ord(char):x} '
    return '"' + re.sub(r'["\\\x00-\x1f\x7f]', escape, str(value)) + '"'


def css_variables(style_id=None, settings=None):
    """The style's settings as the template reads them: a CSS custom
    property each (--key, with "_" as "-"), numbers with their unit,
    colours as CSS colours, bools as 1 or 0, text and fonts quoted."""
    style = get(style_id)
    settings = values(style.id) if settings is None else settings
    variables = {}
    for f in style.fields:
        value = settings[f['key']]
        name = '--' + f['key'].replace('_', '-')
        if f['type'] in ('number', 'int'):
            css = f'{value:g}{f.get("unit", "")}'
        elif f['type'] == 'bool':
            css = '1' if value else '0'
        elif f['type'] == 'color':
            css = css_color(value)
        elif f['type'] == 'select':
            css = str(value)
        else:
            css = css_string(value)
        variables[name] = css
    return variables


def html_attributes(style_id=None, settings=None):
    """The bool and select settings as attributes of the template's root
    element too (data-key, with "_" as "-"), for CSS to match: bools as
    "1" or "0", selects as their value."""
    style = get(style_id)
    settings = values(style.id) if settings is None else settings
    attributes = {}
    for f in style.fields:
        if f['type'] == 'bool':
            attributes['data-' + f['key'].replace('_', '-')] = '1' if settings[f['key']] else '0'
        elif f['type'] == 'select':
            attributes['data-' + f['key'].replace('_', '-')] = str(settings[f['key']])
    return attributes


def preview_look():
    """What the ASS and TTML exports' "preview" look is drawn from, in
    Classic's settings' names (what those formats can write): the project's
    style's, when it is Classic; another's fallback (modules.themes), with
    its own settings of Classic's names over it; Classic's, for a style
    with no fallback."""
    style = get()
    look = values(DEFAULT_STYLE)
    fallback = getattr(style.file, 'fallback', None) if style.file is not None else None
    if style.id == DEFAULT_STYLE or not fallback:
        return look
    for key in ('font_family', 'font_size', 'color'):
        if key in fallback:
            look[key] = fallback[key]
    look['shadow_enabled'] = 'shadow_color' in fallback
    if 'shadow_color' in fallback:
        look['shadow_color'] = fallback['shadow_color']
    look['backgroundbox_enabled'] = 'box_color' in fallback
    if 'box_color' in fallback:
        look['backgroundbox_color'] = fallback['box_color']
    if 'box_padding' in fallback:
        look['backgroundbox_padding'] = fallback['box_padding']
    own = values(style.id)
    for key in look:
        if style.field(key) is not None:
            look[key] = own[key]
    return look
