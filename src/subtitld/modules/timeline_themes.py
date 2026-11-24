"""Timeline themes: a look (modules.timeline_style) with a name, in one JSON
file that carries its SVG shapes in it.

    {
      "format": 1,
      "id": "subtitld.classic",
      "name": "Classic",
      "author": "Subtitld",
      "version": "26.11.1",
      "description": "…",
      "timeline": {"cursor_color": "#ccff0000", "grid_line_svg": "grid_line", …},
      "boxes": {"subtitle": {"frame_svg": "box", …}},
      "shapes": {"grid_line": "<svg …>…</svg>", "box": "<svg …>…</svg>"},
      "options": [
        {"key": "box_opacity", "type": "int", "label": "Subtitle opacity", "unit": "%",
         "min": 30, "max": 100, "default": 90,
         "sets": [{"box": "subtitle", "key": "fill_color", "part": "alpha"}]},
        …
      ]
    }

A setting that names a file (an SVG) names one of the theme's shapes.
Each shape is checked as a picked SVG is (modules.timeline_svg) and kept
by its content when the theme is used; a theme with a shape refused, or
naming one it does not carry, is refused whole. Settings it does not set
are the defaults; ones this Subtitld does not know are left out, a kind
of cue it does not know is kept (timeline_style.clean).

Themes come from Subtitld itself (subtitld/timeline_themes) and from the
user's data folder (timeline_themes), where an imported one is copied; a
user's theme cannot take a built-in one's id. CONFIG['timeline']['theme']
is the theme the look was last set from: changing a setting after keeps
it, as "changed".

DEFAULT is the look of everyone who has neither picked a theme nor
changed a setting (use_default, as Subtitld starts); one who changed
some keeps their own look, a theme of no name.

A theme drawn for one of the interface's looks says so ("look": "dark"
or "light") and may name its "counterpart", the same theme for the other
look: as the interface's look changes, a theme set and not changed since
gives way to its counterpart (follow_look), so Classic becomes Classic
Light in the light look and back.

Options are what a theme lets its user change, and all: a few fields in
the subtitle styles' shape (modules.subtitle_styles: color, int, number,
bool, select, font), each setting the theme's settings it names ("sets"),
a box's with "box" (its kind of cue), the timeline's without. A colour
option sets a colour whole, or with "part": "color" its red, green and
blue alone (the theme's transparency kept); a number from 0 to 100 sets
a colour's "part": "alpha", its opacity in percent; any other option sets
the setting as it is, if the setting can hold it. A number option without
"min" or "max" goes as far as what it sets can (0 to 100 for an opacity). A theme whose options
name a setting there is not, or one they cannot set, is refused whole.

What the user chose is kept per theme in CONFIG['timeline']['theme_options']
({theme id: {option: value}}, defaults left out): picking another theme
and back finds it, and so does a later version of the theme that still
has the option. The look is the theme's settings with its options' values
on them.
"""

import json
import re
from dataclasses import dataclass, field as dataclass_field

from subtitld.modules import session, timeline_style, timeline_svg
from subtitld.modules.subtitle_styles import StyleError, _check_field, coerce

FORMAT = 1
# The theme a look no one has set or changed is set from, and Reset to defaults sets.
DEFAULT = 'subtitld.classic'
MAX_BYTES = 4 * 1024 * 1024
MAX_SHAPES = 64
MAX_OPTIONS = 32
_OPTION_KEYS = ('key', 'type', 'default', 'label', 'min', 'max', 'step', 'unit', 'options')
_PARTS = (None, 'color', 'alpha')
_ID = re.compile(r'[a-z0-9][a-z0-9._-]*')
_ABOUT = ('name', 'author', 'version', 'description')
LOOKS = ('dark', 'light')


class ThemeError(ValueError):
    """A theme file Subtitld cannot use, and why."""


@dataclass
class Theme:
    id: str
    name: str
    author: str = ''
    version: str = ''
    description: str = ''
    look: dict = dataclass_field(default_factory=dict)     # as the file has it: shapes by their keys
    shapes: dict = dataclass_field(default_factory=dict)   # key -> SVG (bytes)
    options: list = dataclass_field(default_factory=list)  # fields, each with its "sets"
    path: object = None
    builtin: bool = False
    look_for: str = ''          # the interface's look it is drawn for ('' for any)
    counterpart: str = ''       # the id of the same theme for the other look


def _file_fields():
    """The keys of the timeline's settings, and of a box's, that name a file."""
    return ([f['key'] for f in timeline_style.TIMELINE_FIELDS if f.get('file')],
            [f['key'] for f in timeline_style.BOX_FIELDS if f.get('file')])


def _check_options(raw):
    """A theme's options, checked: ThemeError for one Subtitld cannot use."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_OPTIONS:
        raise ThemeError(f'options: a list of at most {MAX_OPTIONS}')
    options, keys = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ThemeError('an option that is not an object')
        try:
            option = _check_field({k: entry[k] for k in _OPTION_KEYS if k in entry}, keys)
        except StyleError as error:
            raise ThemeError(f'option: {error}') from error
        key = option['key']
        targets = entry.get('sets')
        if not isinstance(targets, list) or not targets:
            raise ThemeError(f'option "{key}": sets no setting')
        checked = []
        for target in targets:
            if not isinstance(target, dict) or not isinstance(target.get('key'), str):
                raise ThemeError(f'option "{key}": a setting without a key')
            box = target.get('box')
            if box is not None and not isinstance(box, str):
                raise ThemeError(f'option "{key}": a box that is not a kind of cue')
            fields = timeline_style.BOX_FIELDS_BY_KEY if box is not None else timeline_style.TIMELINE_FIELDS_BY_KEY
            field = fields.get(target['key'])
            if field is None or field.get('file'):
                raise ThemeError(f'option "{key}": no setting {target["key"]!r} it can set')
            part = target.get('part')
            if part not in _PARTS:
                raise ThemeError(f'option "{key}": part {part!r} is not color or alpha')
            if part == 'color' and not (option['type'] == 'color' and field['type'] == 'color'):
                raise ThemeError(f'option "{key}": a colour\'s colour is set by a colour option')
            if option['type'] in ('int', 'number'):
                # Limits it leaves out are those of what it sets.
                low, high = (0, 100) if part == 'alpha' else (field.get('min'), field.get('max'))
                if low is not None and 'min' not in entry:
                    option['min'] = max(option.get('min', low), low)
                if high is not None and 'max' not in entry:
                    option['max'] = min(option.get('max', high), high)
            if part == 'alpha' and not (option['type'] in ('int', 'number') and field['type'] == 'color'
                                        and option.get('min', -1) >= 0 and option.get('max', 101) <= 100):
                raise ThemeError(f'option "{key}": a colour\'s opacity is set by a number from 0 to 100')
            if part is None and coerce(field, option['default']) is None:
                raise ThemeError(f'option "{key}": {target["key"]} cannot hold {option["default"]!r}')
            checked.append({'key': target['key'], 'box': box, 'part': part})
        option['sets'] = checked
        keys.add(key)
        options.append(option)
    return options


def parse(data, path=None, builtin=False):
    """The theme in `data` (bytes): ThemeError unless it is one Subtitld can use."""
    if len(data) > MAX_BYTES:
        raise ThemeError(f'larger than {MAX_BYTES // (1024 * 1024)} MB')
    try:
        raw = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ThemeError(f'not JSON: {error}') from error
    if not isinstance(raw, dict):
        raise ThemeError('not an object')
    if raw.get('format') != FORMAT:
        raise ThemeError(f'format {raw.get("format")!r}: this Subtitld reads format {FORMAT}')
    theme_id = raw.get('id')
    if not isinstance(theme_id, str) or not _ID.fullmatch(theme_id):
        raise ThemeError(f'id {theme_id!r}: lowercase letters, digits, ".", "_" and "-" only')
    about = {key: raw.get(key, '') for key in _ABOUT}
    if not all(isinstance(value, str) for value in about.values()) or not about['name'].strip():
        raise ThemeError('no name, or a name, author, version or description that is not text')
    shapes = raw.get('shapes') or {}
    if not isinstance(shapes, dict) or len(shapes) > MAX_SHAPES:
        raise ThemeError(f'shapes: an object of at most {MAX_SHAPES}')
    kept = {}
    for key, svg in shapes.items():
        if not isinstance(key, str) or not isinstance(svg, str):
            raise ThemeError(f'shape {key!r} is not an SVG text')
        try:
            timeline_svg.check(svg.encode('utf-8'))
        except timeline_svg.ShapeError as error:
            raise ThemeError(f'shape "{key}": {error}') from error
        kept[key] = svg.encode('utf-8')
    look = timeline_style.clean({'timeline': raw.get('timeline'), 'boxes': raw.get('boxes')})
    timeline_files, box_files = _file_fields()
    named = [look['timeline'].get(key) for key in timeline_files]
    named += [box.get(key) for box in look['boxes'].values() for key in box_files]
    missing = sorted({name for name in named if name and name not in kept})
    if missing:
        raise ThemeError(f'names shapes it does not carry: {", ".join(missing)}')
    options = _check_options(raw.get('options'))
    look_for, counterpart = raw.get('look', ''), raw.get('counterpart', '')
    if look_for not in ('',) + LOOKS:
        raise ThemeError(f'look {look_for!r}: dark or light')
    if not isinstance(counterpart, str) or (counterpart and (not _ID.fullmatch(counterpart) or not look_for)):
        raise ThemeError(f'counterpart {counterpart!r}: a theme\'s id, from a theme that says its look')
    return Theme(theme_id, about['name'].strip(), about['author'], about['version'], about['description'],
                 look, kept, options, path, builtin, look_for, counterpart)


def load(path, builtin=False):
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ThemeError(str(error)) from error
    return parse(data, path, builtin)


def folders():
    """Where themes are looked for: Subtitld's own, then the user's."""
    return [(session.PATH_SUBTITLD / 'timeline_themes', True),
            (session.PATH_SUBTITLD_USER_DATA / 'timeline_themes', False)]


_themes = None
problems = {}       # file -> why it was not loaded


def available(reload=False):
    """Every usable theme by id, built-in ones first."""
    global _themes
    if _themes is None or reload:
        _themes = {}
        problems.clear()
        for root, builtin in folders():
            if not root.is_dir():
                continue
            for path in sorted(root.glob('*.json')):
                try:
                    theme = load(path, builtin)
                except ThemeError as error:
                    problems[path] = str(error)
                    continue
                if theme.id in _themes:
                    problems[path] = f'id {theme.id} is taken'
                    continue
                _themes[theme.id] = theme
    return _themes


def resolved(theme):
    """The theme's look with its shapes kept and named as the look keeps
    them (timeline_svg)."""
    names = {key: timeline_svg.keep(svg) for key, svg in theme.shapes.items()}
    timeline_files, box_files = _file_fields()
    look = {'timeline': dict(theme.look['timeline']),
            'boxes': {kind: dict(box) for kind, box in theme.look['boxes'].items()}}
    for key in timeline_files:
        if look['timeline'].get(key):
            look['timeline'][key] = names[look['timeline'][key]]
    for box in look['boxes'].values():
        for key in box_files:
            if box.get(key):
                box[key] = names[box[key]]
    return look


def _config():
    timeline = session.CONFIG.setdefault('timeline', {}) if isinstance(session.CONFIG, dict) else {}
    return timeline


def _chosen(theme_id):
    """{option: value} the user chose for a theme; kept in the config."""
    config = _config()
    every = config.get('theme_options')
    if not isinstance(every, dict):
        every = config['theme_options'] = {}
    chosen = every.get(theme_id)
    if not isinstance(chosen, dict):
        chosen = every[theme_id] = {}
    return chosen


def option_values(theme):
    """Each of the theme's options' value: what the user chose, where the
    option can hold it, or its default."""
    chosen = _chosen(theme.id)
    values = {}
    for option in theme.options:
        value = coerce(option, chosen[option['key']]) if option['key'] in chosen else None
        values[option['key']] = option['default'] if value is None else value
    return values


def _set_by(target, current, value):
    """A setting as an option's `value` sets it, from its `current` value."""
    part = target['part']
    if part == 'color':
        return current[:3] + value[3:]                          # its alpha, the option's colour
    if part == 'alpha':
        # Percent to 0-255, half up: 90 % is 230 (0xe6), as a colour written by hand has it.
        return f'#{int(max(0.0, min(100.0, value)) * 255 / 100 + 0.5):02x}' + current[3:]
    return value


def look_of(theme):
    """The theme's look with its options' values on it."""
    look = resolved(theme)
    values = option_values(theme)
    for option in theme.options:
        value = values[option['key']]
        for target in option['sets']:
            if target['box'] is None:
                settings, field = look['timeline'], timeline_style.TIMELINE_FIELDS_BY_KEY[target['key']]
                current = settings.get(target['key'], timeline_style.TIMELINE_DEFAULTS[target['key']])
            else:
                settings = look['boxes'].setdefault(target['box'], {})
                field = timeline_style.BOX_FIELDS_BY_KEY[target['key']]
                inherited = look['boxes'].get(timeline_style.SUBTITLE, {}).get(
                    target['key'], timeline_style.BOX_DEFAULTS[target['key']])
                current = settings.get(target['key'], inherited)
            new = coerce(field, _set_by(target, current, value))
            if new is not None:
                settings[target['key']] = new
    return look


def apply(theme_id):
    """Set the look to the theme's, with the options the user chose for it;
    KeyError for no such theme."""
    theme = available()[theme_id]
    timeline_style.set_values(look_of(theme))
    _config()['theme'] = theme_id


def set_option(key, value, theme_id=None):
    """Set an option of the current theme (or of `theme_id`'s), and the look
    with it while that theme is the look's: the value as kept. KeyError for
    no such option, ValueError for a value it cannot hold."""
    theme = available().get(theme_id) if theme_id else current()
    option = next((o for o in theme.options if o['key'] == key), None) if theme else None
    if option is None:
        raise KeyError(key)
    coerced = coerce(option, value)
    if coerced is None:
        raise ValueError(f'{key}: {value!r}')
    chosen = _chosen(theme.id)
    if coerced == option['default']:
        chosen.pop(key, None)
    else:
        chosen[key] = coerced
    if current() is not None and current().id == theme.id:
        apply(theme.id)
    return coerced


def reset_options(theme_id=None):
    """The current theme's (or `theme_id`'s) options back to its own."""
    theme = available().get(theme_id) if theme_id else current()
    if theme is not None:
        _chosen(theme.id).clear()
        if current() is not None and current().id == theme.id:
            apply(theme.id)


def preview_path(theme):
    """The picture of the theme beside its file (<file>.png), drawn by Subtitld itself: shipped with
    Subtitld's own themes, kept from subtitld.cc for those installed from there. None when there is none."""
    path = theme.path.with_suffix('.png') if theme.path else None
    return path if path is not None and path.is_file() else None


def forget_current():
    """The look is no theme's any more."""
    _config().pop('theme', None)


def use_default(interface_look=None):
    """As Subtitld starts: set a look no theme set and no one changed from
    the default theme (its counterpart for `interface_look`, if it has
    one). Whether it did."""
    changes = timeline_style.changes()
    if _config().get('theme') or changes['timeline'] or changes['boxes'] or DEFAULT not in available():
        return False
    apply(DEFAULT)
    if interface_look:
        follow_look(interface_look)
    return True


def counterpart_for(theme, interface_look):
    """The theme to draw in `interface_look` in place of `theme`: its
    counterpart if it is drawn for the other look and the counterpart for
    this one, else None."""
    if theme is None or not theme.look_for or theme.look_for == interface_look:
        return None
    other = available().get(theme.counterpart)
    return other if other is not None and other.look_for == interface_look else None


def follow_look(interface_look):
    """The interface's look is now `interface_look`: a theme set and not
    changed since gives way to its counterpart for it. Whether it did."""
    theme = current()
    other = counterpart_for(theme, interface_look)
    if other is None or changed():
        return False
    apply(other.id)
    return True


def reset():
    """Back to the defaults: the default theme's look (the settings' own
    defaults, should it be missing)."""
    timeline_style.reset()
    forget_current()
    if DEFAULT in available():
        apply(DEFAULT)


def current():
    """The theme the look was last set from, or None."""
    return available().get(_config().get('theme'))


def changed():
    """Whether the look has been changed since it was set from its theme
    (True without one)."""
    theme = current()
    if theme is None:
        return True
    reference = timeline_style.clean(look_of(theme))
    expected = {'timeline': {k: v for k, v in reference['timeline'].items() if v != timeline_style.TIMELINE_DEFAULTS[k]},
                'boxes': {kind: box for kind, box in reference['boxes'].items() if box}}
    return expected != timeline_style.changes()


def import_file(path):
    """Check the theme file at `path` and keep a copy among the user's: the
    theme. ThemeError if it is refused or takes a built-in theme's id."""
    theme = load(path)
    builtin = {t.id for t in available().values() if t.builtin}
    if theme.id in builtin:
        raise ThemeError(f'id {theme.id} is a theme of Subtitld\'s')
    folder = folders()[1][0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{theme.id}.json').write_bytes(path.read_bytes())
    available(reload=True)
    return available()[theme.id]


def _slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'theme'


def export(path, name, author='', description='', version=''):
    """Write the look as it is now to `path` as a theme called `name`, its
    shapes in it."""
    look = timeline_style.changes()
    timeline_files, box_files = _file_fields()
    shapes, keys = {}, {}

    def carried(stored, key):
        if stored not in keys:
            data = timeline_svg.read(stored)
            if data is None:
                return None
            key = key if key not in shapes else f'{key}_{len(shapes)}'
            keys[stored], shapes[key] = key, data.decode('utf-8')
        return keys[stored]

    for key in timeline_files:
        if look['timeline'].get(key):
            look['timeline'][key] = carried(look['timeline'][key], key.removesuffix('_svg'))
            if look['timeline'][key] is None:
                del look['timeline'][key]
    for kind, box in look['boxes'].items():
        for key in box_files:
            if box.get(key):
                box[key] = carried(box[key], f'{kind}_{key.removesuffix("_svg")}')
                if box[key] is None:
                    del box[key]
    theme = {'format': FORMAT, 'id': f'user.{_slug(name)}', 'name': name, 'author': author, 'version': version,
             'description': description, **look, 'shapes': shapes}
    source = current()
    if source is not None and source.options:
        # Its theme's options, as they are now: the look exported is what they set.
        values = option_values(source)
        theme['options'] = [{**{k: v for k, v in option.items() if k != 'sets'}, 'default': values[option['key']],
                             'sets': [{k: v for k, v in target.items() if v is not None} for target in option['sets']]}
                            for option in source.options]
    path.write_text(json.dumps(theme, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return theme
