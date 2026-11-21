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
      "shapes": {"grid_line": "<svg …>…</svg>", "box": "<svg …>…</svg>"}
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
"""

import json
import re
from dataclasses import dataclass, field as dataclass_field

from subtitld.modules import session, timeline_style, timeline_svg

FORMAT = 1
# The theme a look no one has set or changed is set from, and Reset to defaults sets.
DEFAULT = 'subtitld.classic'
MAX_BYTES = 4 * 1024 * 1024
MAX_SHAPES = 64
_ID = re.compile(r'[a-z0-9][a-z0-9._-]*')
_ABOUT = ('name', 'author', 'version', 'description')


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
    path: object = None
    builtin: bool = False


def _file_fields():
    """The keys of the timeline's settings, and of a box's, that name a file."""
    return ([f['key'] for f in timeline_style.TIMELINE_FIELDS if f.get('file')],
            [f['key'] for f in timeline_style.BOX_FIELDS if f.get('file')])


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
    return Theme(theme_id, about['name'].strip(), about['author'], about['version'], about['description'],
                 look, kept, path, builtin)


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


def apply(theme_id):
    """Set the look to the theme's; KeyError for no such theme."""
    theme = available()[theme_id]
    timeline_style.set_values(resolved(theme))
    _config()['theme'] = theme_id


def forget_current():
    """The look is no theme's any more."""
    _config().pop('theme', None)


def use_default():
    """As Subtitld starts: set a look no theme set and no one changed from
    the default theme. Whether it did."""
    changes = timeline_style.changes()
    if _config().get('theme') or changes['timeline'] or changes['boxes'] or DEFAULT not in available():
        return False
    apply(DEFAULT)
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
    reference = timeline_style.clean(resolved(theme))
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
    path.write_text(json.dumps(theme, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return theme
