"""Title themes: how a title (modules.cue_kinds) is drawn over the picture.

A title theme is a folder, as a subtitle style is (modules.subtitle_styles):
a manifest (theme.json) and an HTML template the preview renders each title
with, with subtitle_styles/host.js in it. The manifest names the theme, the
parts of a title it fills (`slots`) and the settings it offers (`fields`,
in the subtitle styles' field format):

    {
      "id": "subtitld.plain",
      "name": "Plain",
      "version": "26.12.1",
      "template": "theme.html",
      "slots": [{"key": "main", "label": "Title"}],
      "fields": [
        {"key": "font_size", "type": "int", "label": "Size", "default": 72,
         "min": 8, "max": 400, "unit": "px"},
        {"key": "placement", "type": "select", "default": "center",
         "options": ["top", "center", "bottom", "lower_third"]},
        ...
      ]
    }

The title's text is its "main" slot; a theme may have more (a second
line, a logo, a list of names), each a part of the title of its own: what
the title puts in them is 'title_slots' ({key: text, [texts] or a picture's
data: URL}), their translations 'title_slot_translations' ({language:
{key: …}}). They are kept through a change of theme. A title keeps
its theme's id ('title_theme') and only what it changed of the theme's
fields ('title_settings'), so a later version of the theme changes the
defaults of every title that kept them. A setting a title has that its
theme has not (another theme's) is kept, unused, for that theme.

A select field named "placement" places the title where its value says
(PLACEMENTS: a keypad number, as {\\anN} places a subtitle); the title's own
margins (modules.position) move it from there.

Themes come from theme files of the title kind (modules.themes: Plain is
one), then from folders in Subtitld itself (subtitld/title_themes) and the
user's data folder (title_themes), as subtitle styles do. The theme new titles get
is CONFIG['title_theme']['current'].
"""

import json
import re
from dataclasses import dataclass, field as dataclass_field

from subtitld.modules import session
from subtitld.modules.subtitle_styles import StyleError, _check_field, coerce, css_color

DEFAULT_THEME = 'subtitld.plain'
MANIFEST = 'theme.json'
# Where a "placement" puts a title: a keypad number (host.js's data-align).
PLACEMENTS = {'top': 8, 'center': 5, 'bottom': 2, 'lower_third': 1, 'upper_third': 7}


@dataclass
class Theme:
    id: str
    name: str
    path: object                    # the theme's folder (pathlib.Path)
    template: str
    version: str = ''
    slots: list = dataclass_field(default_factory=list)
    fields: list = dataclass_field(default_factory=list)
    builtin: bool = False
    animated: bool = False
    file: object = None             # the modules.themes.Theme, for a theme that is a theme file

    def field(self, key):
        return next((f for f in self.fields if f['key'] == key), None)


def load(path, builtin=False):
    """The theme in folder `path`; StyleError if its manifest is unusable."""
    try:
        manifest = json.loads((path / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise StyleError(f'{MANIFEST}: {error}') from error
    if not isinstance(manifest, dict):
        raise StyleError(f'{MANIFEST} is not an object')
    theme_id, name = manifest.get('id'), manifest.get('name')
    if not isinstance(theme_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', theme_id):
        raise StyleError(f'id {theme_id!r}: lowercase letters, digits, ".", "_" and "-" only')
    if not isinstance(name, str) or not name.strip():
        raise StyleError('no name')
    template = manifest.get('template', 'theme.html')
    if not isinstance(template, str) or '/' in template or '\\' in template or not (path / template).is_file():
        raise StyleError(f'template {template!r} is not a file in the theme\'s folder')
    slots = []
    for raw in manifest.get('slots') or [{'key': 'main'}]:
        if not isinstance(raw, dict) or not isinstance(raw.get('key'), str) or not raw['key']:
            raise StyleError('a slot without a key')
        slots.append({'key': raw['key'], 'label': str(raw.get('label') or raw['key'])})
    if not any(slot['key'] == 'main' for slot in slots):
        raise StyleError('no "main" slot: the title\'s text goes there')
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
    return Theme(theme_id, name.strip(), path, template, str(manifest.get('version', '')), slots, fields, builtin,
                 animated)


def folders():
    """Where themes are looked for: Subtitld's own, then the user's."""
    return [(session.PATH_SUBTITLD / 'title_themes', True),
            (session.PATH_SUBTITLD_USER_DATA / 'title_themes', False)]


_themes = None
problems = {}       # folder → why it was not loaded


def available(reload=False):
    """Every usable theme by id, built-in ones first."""
    global _themes
    if _themes is None or reload:
        from subtitld.modules import themes
        _themes = {}
        problems.clear()
        for theme in themes.available('title', reload=reload).values():
            _themes[theme.id] = Theme(theme.id, theme.name, theme.path.parent, theme.path.name, theme.version,
                                      theme.slots, theme.fields, theme.builtin, theme.animated, theme)
        for root, builtin in folders():
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()):
                try:
                    theme = load(path, builtin)
                except StyleError as error:
                    problems[path] = str(error)
                    continue
                if theme.id in _themes:
                    problems[path] = f'id {theme.id} is taken'
                    continue
                _themes[theme.id] = theme
    return _themes


def _config():
    config = session.CONFIG.get('title_theme') if isinstance(session.CONFIG, dict) else None
    if not isinstance(config, dict):
        config = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['title_theme'] = config
    return config


def current_id():
    """The theme new titles are drawn with."""
    theme_id = _config().get('current') or DEFAULT_THEME
    return theme_id if theme_id in available() else DEFAULT_THEME


def set_current(theme_id):
    if theme_id not in available():
        raise KeyError(theme_id)
    _config()['current'] = theme_id


def get(theme_id=None):
    """The theme `theme_id` (the current one by default); the default theme
    when there is no such theme (a title made where it was installed)."""
    themes = available()
    return themes.get(theme_id or current_id()) or themes[DEFAULT_THEME]


def of(title):
    """The theme a title is drawn with."""
    return get(title.get('title_theme') if isinstance(title, dict) else None)


def values(title):
    """A title's settings: what it set, where its theme's field can hold it,
    and the field's default otherwise."""
    theme = of(title)
    saved = title.get('title_settings') if isinstance(title.get('title_settings'), dict) else {}
    result = {}
    for f in theme.fields:
        value = coerce(f, saved[f['key']]) if f['key'] in saved else None
        result[f['key']] = f['default'] if value is None else value
    return result


def set_value(title, key, value):
    """Set one of a title's settings; ValueError when its theme's field
    cannot hold `value`, KeyError when the theme has no such field. A
    default is not kept: the theme's, whatever it becomes."""
    theme = of(title)
    field_ = theme.field(key)
    if field_ is None:
        raise KeyError(key)
    coerced = coerce(field_, value)
    if coerced is None:
        raise ValueError(f'{key}: {value!r} is not a {field_["type"]}')
    settings = dict(title.get('title_settings') or {})
    if coerced == field_['default']:
        settings.pop(key, None)
    else:
        settings[key] = coerced
    title['title_settings'] = settings
    return coerced


# A picture in an image slot: kept in the title as a data: URL (what a
# theme's page may load), so it is no bigger than this.
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
               '.webp': 'image/webp', '.svg': 'image/svg+xml', '.avif': 'image/avif'}
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def extra_slots(title):
    """The slots of a title's theme besides "main" (its text), each a dict
    as the manifest has it (modules.themes); [] for a theme without."""
    return [slot for slot in of(title).slots if slot.get('key') != 'main' and slot.get('type')]


def shown_slots(title):
    """extra_slots() that its settings show (a slot `visible_if` a bool
    that is off is not)."""
    settings = values(title)
    return [slot for slot in extra_slots(title) if not slot.get('visible_if') or settings.get(slot['visible_if'])]


def _fit(slot, value):
    """`value` as `slot` holds it, or None when it cannot: text cut to its
    max_length (a line, unless multiline), a list of texts, a picture's
    data: URL."""
    kind = slot.get('type', 'text')
    limit = slot.get('max_length')
    if kind == 'image':
        return value if isinstance(value, str) and value.startswith('data:image/') else None
    if kind == 'list':
        if isinstance(value, str):
            value = value.split('\n')
        if not isinstance(value, list):
            return None
        items = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        return [item[:limit] if limit else item for item in items]
    if not isinstance(value, str):
        return None
    if not slot.get('multiline'):
        value = ' '.join(value.splitlines())
    return value[:limit] if limit else value


def slot_value(title, key, language=None):
    """What a title has in slot `key` (its translation in `language`, where
    it has one), else the slot's default; None for nothing."""
    slot = next((s for s in extra_slots(title) if s['key'] == key), None)
    if slot is None:
        return None
    own = title.get('title_slots') if isinstance(title.get('title_slots'), dict) else {}
    value = own.get(key, slot.get('default'))
    if language and slot.get('type') != 'image':
        translated = (title.get('title_slot_translations') or {}).get(language)
        if isinstance(translated, dict) and translated.get(key):
            value = translated[key]
    return _fit(slot, value) if value is not None else None


def set_slot(title, key, value, language=None):
    """Put `value` in a title's slot `key` (its translation in `language`:
    text and list slots only); None or empty takes it out. ValueError when
    the slot cannot hold it, KeyError when the theme has no such slot. A
    title keeps a slot's value through a change of theme: another theme
    with the slot shows it again."""
    slot = next((s for s in extra_slots(title) if s['key'] == key), None)
    if slot is None:
        raise KeyError(key)
    if language and slot.get('type') == 'image':
        raise ValueError('a picture is the same in every language')
    fitted = None if value in (None, '', []) else _fit(slot, value)
    if value not in (None, '', []) and fitted is None:
        raise ValueError(f'{key}: {value!r} does not fit a {slot.get("type", "text")} slot'[:200])
    if language:
        table = dict(title.get('title_slot_translations') or {})
        own = dict(table.get(language) or {})
        if fitted in (None, '', []):
            own.pop(key, None)
        else:
            own[key] = fitted
        if own:
            table[language] = own
        else:
            table.pop(language, None)
        if table:
            title['title_slot_translations'] = table
        else:
            title.pop('title_slot_translations', None)
        return fitted
    own = dict(title.get('title_slots') or {})
    if fitted in (None, '', []):
        own.pop(key, None)
    else:
        own[key] = fitted
    if own:
        title['title_slots'] = own
    else:
        title.pop('title_slots', None)
    return fitted


def image_data_url(path):
    """The picture in file `path` as a data: URL for an image slot;
    ValueError when it is not a picture Subtitld takes or is too big."""
    import base64
    from pathlib import Path
    path = Path(path)
    kind = IMAGE_TYPES.get(path.suffix.lower())
    if kind is None:
        raise ValueError(f'{path.suffix or path.name}: not a picture ({", ".join(sorted(IMAGE_TYPES))})')
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f'larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB')
    return f'data:{kind};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def css_variables(theme, settings):
    """The settings as the template reads them (subtitle_styles.css_variables)."""
    variables = {}
    for f in theme.fields:
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
            css = json.dumps(value)
        variables[name] = css
    return variables


def html_attributes(theme, settings):
    """Bools and selects as the root element's data- attributes, for CSS
    to match (subtitle_styles.html_attributes); data-kind="title"."""
    attributes = {'data-kind': 'title'}
    for f in theme.fields:
        if f['type'] == 'bool':
            attributes['data-' + f['key'].replace('_', '-')] = '1' if settings[f['key']] else '0'
        elif f['type'] == 'select':
            attributes['data-' + f['key'].replace('_', '-')] = str(settings[f['key']])
    return attributes


def alignment(theme, settings):
    """The keypad number a title is placed at: its "placement", else the
    middle of the picture."""
    return PLACEMENTS.get(settings.get('placement'), 5) if theme.field('placement') else 5


# What a title looks like in a format of subtitles (ASS, TTML), as those
# can say it: export_look()'s keys, and where it starts from.
EXPORT_DEFAULTS = {'placement': 'center', 'font_family': 'Arial', 'font_size': 72, 'bold': True, 'italic': False,
                   'color': '#ffffffff', 'shadow_enabled': False, 'shadow_color': '#aa000000', 'box_enabled': False,
                   'box_color': '#cc000000', 'box_padding': 0, 'letter_spacing': 0, 'uppercase': False}


def export_look(title):
    """How a title is written in a format of subtitles: its theme's
    fallback (modules.themes), with the title's own settings of the same
    names over it (a font, a colour, a box...). None when its theme is a
    theme file with no fallback: it is drawn only, burned into a video."""
    theme = of(title)
    fallback = getattr(theme.file, 'fallback', None) if theme.file is not None else {}
    if fallback is None:
        return None
    look = dict(EXPORT_DEFAULTS)
    for key in ('placement', 'font_family', 'font_size', 'bold', 'italic', 'color', 'box_padding'):
        if key in fallback:
            look[key] = fallback[key]
    if 'shadow_color' in fallback:
        look.update(shadow_enabled=True, shadow_color=fallback['shadow_color'])
    if 'box_color' in fallback:
        look.update(box_enabled=True, box_color=fallback['box_color'])
    settings = values(title)
    for key in EXPORT_DEFAULTS:
        if theme.field(key) is not None:
            look[key] = settings[key]
    if theme.field('font_weight') is not None:
        look['bold'] = int(str(settings['font_weight'] or '400')) >= 600
    return look


def exportable(title):
    """Whether a title can be written in a format of subtitles at all."""
    return export_look(title) is not None


def export_text(title):
    """A title's text as a format of subtitles carries it: its own, then
    what its other text slots show (a role, a list of names), a line each."""
    from subtitld.modules.segment import text_of
    lines = [text_of(title)]
    for slot in shown_slots(title):
        value = slot_value(title, slot['key'])
        if slot['type'] == 'list' and value:
            lines.extend(value)
        elif slot['type'] == 'text' and value:
            lines.append(value)
    return '\n'.join(line for line in lines if line)


def as_subtitle(title):
    """A title as a format of subtitles can carry it (TTML): its text and
    its other text slots' at its place ({\\anN}), in its export look's
    colour and weight, with its font, size, box and shadow as its look
    (modules.look). Its theme's look beyond that is not there to write."""
    from subtitld.modules import look as subtitld_look, markup
    settings = export_look(title) or dict(EXPORT_DEFAULTS)
    lead, body = markup.split_lead(export_text(title))
    body = markup.plain(body)
    if settings.get('uppercase'):
        body = body.upper()
    color = settings.get('color')
    if isinstance(color, str) and len(color) == 9 and color[3:].lower() != 'ffffff':
        body = f'<font color="#{color[3:]}">{body}</font>'
    if settings.get('bold'):
        body = f'<b>{body}</b>'
    if settings.get('italic'):
        body = f'<i>{body}</i>'
    copy = {key: value for key, value in title.items()
            if key not in ('kind', 'title_theme', 'title_settings', 'title_slots', 'title_slot_translations')}
    copy['text'] = (lead or f'{{\\an{PLACEMENTS.get(settings.get("placement"), 5)}}}') + body
    look = {}
    if settings.get('font_family'):
        look['font'] = settings['font_family']
    if settings.get('font_size'):
        look['size'] = round(float(settings['font_size']) / 10.8, 3)
    if settings.get('box_enabled'):
        look['background'] = settings.get('box_color') or '#cc000000'
        padding = round(float(settings.get('box_padding') or 0) / 10.8, 3)
        look['padding'] = [padding] * 4
    elif settings.get('shadow_enabled'):
        look['shadow'] = [{'x': 0, 'y': 0.2, 'blur': 1.1, 'color': settings.get('shadow_color') or '#aa000000'}]
    subtitld_look.store(copy, look)
    return copy
