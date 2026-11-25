"""Subtitle and title themes: how a subtitle or a title is drawn over the
picture, each one self-contained HTML file (SUBTITLE_THEMES_PLAN.md).

The file is the theme: opened in a browser it shows itself with sample
text, and Subtitld renders cues in it. Its manifest is inert JSON in the
page:

    <script type="application/json" id="subtitld-theme">
    {
      "format": 1,
      "id": "jonata.newsroom",
      "kind": "title",                       subtitle | title
      "name": "Newsroom",
      "version": "26.12.1",
      "min_subtitld": "26.12",
      "author": "…", "licence": "CC BY 4.0", "description": "…",
      "timing": {"intro": 400, "outro": 300},    ms; present = animated
      "ignores_position": false,
      "slice": "words",                       none | lines | words | chars
      "slots": [
        {"key": "main", "type": "text", "label": "Name"},
        {"key": "role", "type": "text", "label": "Role", "max_length": 60},
        {"key": "logo", "type": "image", "visible_if": "show_logo"},
        {"key": "people", "type": "list", "of": "text"}
      ],
      "fields": [
        {"key": "accent", "type": "color", "default": "#ffe5a823", "per_speaker": true},
        {"key": "show_logo", "type": "bool", "default": true}
      ],
      "fallback": {"placement": "lower_third", "color": "#ffffffff"}
    }
    </script>

Slots are where a cue's texts and pictures go: elements with
`data-slot="<key>"`, their content the sample a browser shows. "main" is
the cue's own text; a subtitle theme has that one alone (declared or not),
a title theme as many as it likes. A theme with no data-slot in its markup
has its text put on Subtitld's own stage, in a `.cue` (as Classic); one
with any has "main" among them. `slice` is how far its text slots are cut
into elements of their own (`.line`, `.word`, `.char`) for it to style. Fields are the options, in the subtitle
styles' field format (modules.subtitle_styles), plus `per_speaker`. The
fallback is the look formats that cannot carry the theme get (ASS, TTML).

Everything in the file goes through modules.theme_sanitize first; a file
with anything it refuses is not a theme.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field as dataclass_field

from subtitld.modules import session, theme_sanitize
from subtitld.modules.subtitle_styles import FIELD_TYPES, StyleError, _COLOR, _check_field

FORMAT = 1
KINDS = ('subtitle', 'title')
SLICES = ('none', 'lines', 'words', 'chars')
SLOT_TYPES = ('text', 'image', 'list')
LIST_OF = ('text',)
MAX_BYTES = 5 * 1024 * 1024
MAX_SLOTS = 24
MAX_FIELDS = 48
MAX_TIMING = 60000                  # ms, an intro or outro
EXTENSION = '.html'
_ID = re.compile(r'[a-z0-9][a-z0-9._-]*')
_KEY = re.compile(r'[a-z][a-z0-9_]*')
_VERSION = re.compile(r'\d+(\.\d+)*')
# The file starts so (a byte-order mark and blanks before it aside): no
# quirks mode, and the renderer's policy goes in right after it.
DOCTYPE = re.compile(r'\A\ufeff?\s*<!doctype\s+html\s*>', re.IGNORECASE)
# What a fallback may say, and how each is checked.
PLACEMENTS = ('top', 'upper_third', 'center', 'lower_third', 'bottom')
FALLBACK_KEYS = {
    'placement': lambda v: v in PLACEMENTS,
    'font_family': lambda v: isinstance(v, str) and bool(v.strip()),
    'font_size': lambda v: isinstance(v, int) and not isinstance(v, bool) and 4 <= v <= 400,
    'bold': lambda v: isinstance(v, bool),
    'italic': lambda v: isinstance(v, bool),
    'color': lambda v: isinstance(v, str) and bool(_COLOR.fullmatch(v)),
    'outline_color': lambda v: isinstance(v, str) and bool(_COLOR.fullmatch(v)),
    'shadow_color': lambda v: isinstance(v, str) and bool(_COLOR.fullmatch(v)),
    'box_color': lambda v: isinstance(v, str) and bool(_COLOR.fullmatch(v)),
    'box_padding': lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 200,
}


# What a theme's page may load and run in the renderer, whatever the check
# above missed: nothing but its own inline CSS, data: pictures and fonts, and
# Subtitld's fonts (file:); no script of its own (host.js is Subtitld's,
# put in by QtWebEngine, which the policy does not stop).
POLICY = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; "
          "font-src data: file:; base-uri 'none'; form-action 'none'")


class ThemeError(ValueError):
    """A file Subtitld cannot use as a theme. `problems` lists every reason
    found, `str()` the first ones."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        shown = '; '.join(self.problems[:3])
        more = len(self.problems) - 3
        super().__init__(shown + (f' (and {more} more)' if more > 0 else ''))


@dataclass
class Theme:
    id: str
    kind: str
    name: str
    html: str                       # the file, as it came: what the renderer loads
    version: str = ''
    min_subtitld: str = ''
    author: str = ''
    licence: str = ''
    description: str = ''
    timing: dict = None             # {'intro': ms, 'outro': ms}, or None: not animated
    ignores_position: bool = False
    slice: str = 'words'            # how far text slots are cut: none, lines, words, chars
    slots: list = dataclass_field(default_factory=list)
    fields: list = dataclass_field(default_factory=list)
    fallback: dict = None
    marked_slots: list = dataclass_field(default_factory=list)    # data-slot keys in the markup
    path: object = None             # where it was read from (pathlib.Path), if a file
    builtin: bool = False
    carried: bool = False           # from the open project's file, not installed (carry())

    @property
    def animated(self):
        return self.timing is not None

    @property
    def slotted(self):
        """Its markup has the slots, "main" among them; otherwise the text
        goes on Subtitld's own stage."""
        return 'main' in self.marked_slots

    def field(self, key):
        return next((f for f in self.fields if f['key'] == key), None)

    def slot(self, key):
        return next((s for s in self.slots if s['key'] == key), None)


def _check_slot(raw, keys, bools):
    """One of a manifest's slots, checked: the problems, and the slot."""
    if not isinstance(raw, dict) or not isinstance(raw.get('key'), str) or not _KEY.fullmatch(raw['key']):
        return [f'a slot whose key is not lowercase letters, digits and "_": {raw!r}'[:120]], None
    key = raw['key']
    where = f'slot "{key}"'
    problems = []
    if key in keys:
        problems.append(f'{where} twice')
    kind = raw.get('type', 'text')
    if kind not in SLOT_TYPES:
        problems.append(f'{where}: unknown type {kind!r} ({", ".join(SLOT_TYPES)})')
    slot = {'key': key, 'type': kind, 'label': str(raw.get('label') or key)}
    if kind == 'list':
        of = raw.get('of', 'text')
        if of not in LIST_OF:
            problems.append(f'{where}: a list of {of!r} (lists hold {", ".join(LIST_OF)})')
        slot['of'] = of
    for hint, check, says in (('multiline', lambda v: isinstance(v, bool), 'true or false'),
                              ('max_length', lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
                               'a number above 0'),
                              ('placeholder', lambda v: isinstance(v, str), 'text')):
        if hint in raw:
            if kind == 'image':
                problems.append(f'{where}: {hint} is for text')
            elif not check(raw[hint]):
                problems.append(f'{where}: {hint} is not {says}')
            else:
                slot[hint] = raw[hint]
    if 'default' in raw:
        default = raw['default']
        if kind == 'text' and isinstance(default, str):
            slot['default'] = default
        elif kind == 'list' and isinstance(default, list) and all(isinstance(item, str) for item in default):
            slot['default'] = list(default)
        else:
            problems.append(f'{where}: default {default!r} does not fit a {kind} slot'[:160])
    condition = raw.get('visible_if')
    if condition is not None:
        if key == 'main':
            problems.append(f'{where}: the main slot is always shown')
        elif condition not in bools:
            problems.append(f'{where}: visible_if names no bool field')
        else:
            slot['visible_if'] = condition
    unknown = set(raw) - {'key', 'type', 'label', 'of', 'multiline', 'max_length', 'placeholder', 'default',
                          'visible_if'}
    if unknown:
        problems.append(f'{where}: unknown keys {", ".join(sorted(unknown))}')
    return problems, slot


def _check_fields(raw):
    problems, fields, keys = [], [], set()
    if raw is None:
        return problems, fields
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        return [f'fields: a list of at most {MAX_FIELDS}'], fields
    for entry in raw:
        if isinstance(entry, dict) and entry.get('type') not in FIELD_TYPES:
            problems.append(f'field "{entry.get("key")}": unknown type {entry.get("type")!r} '
                            f'({", ".join(FIELD_TYPES)})')
            continue
        try:
            field_ = _check_field(entry, keys)
        except StyleError as error:
            problems.append(str(error))
            continue
        if 'per_speaker' in field_ and not isinstance(field_['per_speaker'], bool):
            problems.append(f'field "{field_["key"]}": per_speaker is not true or false')
        elif not _KEY.fullmatch(field_['key']):
            problems.append(f'field "{field_["key"]}": keys are lowercase letters, digits and "_"')
        keys.add(field_['key'])
        fields.append(field_)
    for field_ in fields:
        condition = field_.get('visible_if')
        if condition is not None and not any(f['key'] == condition and f['type'] == 'bool' for f in fields):
            problems.append(f'field "{field_["key"]}": visible_if names no bool field')
    return problems, fields


def _check_manifest(manifest, marked_slots):
    """The manifest's problems, and the Theme's own values from it."""
    problems = []
    if manifest.get('format') != FORMAT:
        problems.append(f'format {manifest.get("format")!r}: this Subtitld reads format {FORMAT}')
    theme_id = manifest.get('id')
    if not isinstance(theme_id, str) or not _ID.fullmatch(theme_id):
        problems.append(f'id {theme_id!r}: lowercase letters, digits, ".", "_" and "-" only')
    kind = manifest.get('kind')
    if kind not in KINDS:
        problems.append(f'kind {kind!r}: subtitle or title')
    name = manifest.get('name')
    if not isinstance(name, str) or not name.strip():
        problems.append('no name')
    for key in ('version', 'min_subtitld'):
        value = manifest.get(key)
        if value is not None and not (isinstance(value, str) and _VERSION.fullmatch(value)):
            problems.append(f'{key} {value!r}: numbers and dots, as 26.12.1')
    for key in ('author', 'licence', 'description'):
        if key in manifest and not isinstance(manifest[key], str):
            problems.append(f'{key} is not text')
    timing = manifest.get('timing')
    if timing is not None:
        if not isinstance(timing, dict) or set(timing) - {'intro', 'outro'}:
            problems.append('timing: {"intro": ms, "outro": ms}')
            timing = None
        else:
            for part in ('intro', 'outro'):
                value = timing.get(part, 0)
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TIMING:
                    problems.append(f'timing {part}: whole milliseconds from 0 to {MAX_TIMING}')
            timing = {'intro': timing.get('intro', 0), 'outro': timing.get('outro', 0)}
    ignores_position = manifest.get('ignores_position', False)
    if not isinstance(ignores_position, bool):
        problems.append('ignores_position is not true or false')
    slice_ = manifest.get('slice', 'words')
    if slice_ not in SLICES:
        problems.append(f'slice {slice_!r}: {", ".join(SLICES)}')
    field_problems, fields = _check_fields(manifest.get('fields'))
    problems += field_problems
    bools = {f['key'] for f in fields if f['type'] == 'bool'}
    raw_slots = manifest.get('slots')
    slots, keys = [], set()
    if raw_slots is not None and (not isinstance(raw_slots, list) or len(raw_slots) > MAX_SLOTS):
        problems.append(f'slots: a list of at most {MAX_SLOTS}')
        raw_slots = None
    for raw in raw_slots or []:
        slot_problems, slot = _check_slot(raw, keys, bools)
        problems += slot_problems
        if slot is not None:
            keys.add(slot['key'])
            slots.append(slot)
    main = next((slot for slot in slots if slot['key'] == 'main'), None)
    if main is None:
        if kind == 'title' and raw_slots:
            problems.append('no "main" slot: the title\'s own text goes there')
        main = {'key': 'main', 'type': 'text', 'label': 'Text'}
        slots.insert(0, main)
    elif main['type'] != 'text':
        problems.append('slot "main" is a text slot')
    if kind == 'subtitle' and len(slots) > 1:
        problems.append('a subtitle theme has one slot, "main": its text')
    # The markup and the manifest name the same slots ("main" may be left
    # out of the markup: then Subtitld's own stage places the text).
    for key in dict.fromkeys(marked_slots):
        if key not in keys and key != 'main':
            problems.append(f'data-slot="{key}" in the markup is not a slot of the manifest')
    if marked_slots and 'main' not in marked_slots:
        problems.append('no element with data-slot="main": where the other slots are, the main text is too')
    for slot in slots:
        if slot['key'] != 'main' and slot['key'] not in marked_slots:
            problems.append(f'slot "{slot["key"]}" has no element with data-slot="{slot["key"]}"')
    fallback = manifest.get('fallback')
    if fallback is not None:
        if not isinstance(fallback, dict):
            problems.append('fallback is not an object')
            fallback = None
        else:
            for key, value in fallback.items():
                check = FALLBACK_KEYS.get(key)
                if check is None:
                    problems.append(f'fallback: unknown key {key!r} ({", ".join(FALLBACK_KEYS)})')
                elif not check(value):
                    problems.append(f'fallback {key}: {value!r} does not fit'[:120])
            if isinstance(fallback.get('color'), str) and len(fallback['color']) == 7:
                fallback = dict(fallback, color='#ff' + fallback['color'][1:])
    unknown = set(manifest) - {'format', 'id', 'kind', 'name', 'version', 'min_subtitld', 'author', 'licence',
                               'description', 'timing', 'ignores_position', 'slice', 'slots', 'fields', 'fallback'}
    if unknown:
        problems.append(f'unknown keys {", ".join(sorted(unknown))}')
    values = dict(id=theme_id, kind=kind, name=name.strip() if isinstance(name, str) else name,
                  version=manifest.get('version') or '', min_subtitld=manifest.get('min_subtitld') or '',
                  author=manifest.get('author', ''), licence=manifest.get('licence', ''),
                  description=manifest.get('description', ''), timing=timing,
                  ignores_position=ignores_position, slice=slice_, slots=slots, fields=fields, fallback=fallback)
    return problems, values


def parse(data, path=None, builtin=False):
    """The theme in `data` (bytes or str): ThemeError, with every problem
    found, unless it is one Subtitld can use."""
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise ThemeError(f'larger than {MAX_BYTES // (1024 * 1024)} MB')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ThemeError(f'not UTF-8 ({error.reason} at byte {error.start})') from error
    else:
        text = data
        if len(text.encode('utf-8')) > MAX_BYTES:
            raise ThemeError(f'larger than {MAX_BYTES // (1024 * 1024)} MB')
    report = theme_sanitize.check(text)
    problems = list(report.problems)
    if not DOCTYPE.match(text):
        problems.insert(0, 'the file starts with <!doctype html>')
    manifest = None
    if report.manifest is not None:
        try:
            manifest = json.loads(report.manifest)
        except ValueError as error:
            problems.append(f'manifest: {error}')
        else:
            if not isinstance(manifest, dict):
                problems.append('manifest: not an object')
                manifest = None
    if manifest is None:
        raise ThemeError(problems)
    manifest_problems, values = _check_manifest(manifest, report.slots)
    problems += manifest_problems
    if problems:
        raise ThemeError(problems)
    return Theme(html=text, marked_slots=list(dict.fromkeys(report.slots)), path=path, builtin=builtin, **values)


def load(path, builtin=False):
    """The theme in file `path` (pathlib.Path): ThemeError unless usable."""
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise ThemeError(f'larger than {MAX_BYTES // (1024 * 1024)} MB')
        data = path.read_bytes()
    except OSError as error:
        raise ThemeError(f'cannot be read: {error.strerror or error}') from error
    return parse(data, path, builtin)


def served(theme):
    """The file the renderer loads for `theme`: its own, with POLICY in a
    <meta> right after its doctype, kept in Subtitld's cache (written once
    for each version of the file)."""
    folder = session.PATH_SUBTITLD_USER_CACHE / 'themes'
    digest = hashlib.sha1(theme.html.encode('utf-8')).hexdigest()[:16]
    path = folder / f'{theme.id}-{digest}.html'
    if not path.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        end = DOCTYPE.match(theme.html).end()
        meta = f'\n<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
        path.write_text(theme.html[:end] + meta + theme.html[end:], encoding='utf-8')
        # The copies of the theme's earlier versions go.
        for old in folder.glob(f'{theme.id}-*.html'):
            if old != path and re.fullmatch(re.escape(theme.id) + r'-[0-9a-f]{16}\.html', old.name):
                old.unlink(missing_ok=True)
    return path


def is_new_enough(theme, running=None):
    """Whether this Subtitld is at least the theme's `min_subtitld`."""
    from subtitld.modules.addons.installer import subtitld_is_new_enough
    return not theme.min_subtitld or subtitld_is_new_enough(theme.min_subtitld, running)


def animation(segment, style_id=None):
    """(intro, outro) in seconds of the theme a cue is drawn with, when it
    is an animated theme file (its manifest's timing); None otherwise, and
    for a mark. `style_id`: the subtitle style every subtitle is drawn in
    (an export's choice), whatever theme it has of its own."""
    from subtitld.modules import cue_kinds, subtitle_styles, title_themes
    kind = cue_kinds.kind_of(segment)
    if kind == cue_kinds.MARK:
        return None
    if kind == cue_kinds.TITLE:
        look = title_themes.of(segment)
    else:
        look = subtitle_styles.get(style_id) if style_id else subtitle_styles.of(segment)
    timing = getattr(getattr(look, 'file', None), 'timing', None)
    if not timing:
        return None
    return timing.get('intro', 0) / 1000, timing.get('outro', 0) / 1000


def too_short(segment):
    """How long a cue's animation takes (intro and outro), when the cue is
    shorter than that; None when it is not (or has none)."""
    timing = animation(segment)
    if not timing:
        return None
    needed = timing[0] + timing[1]
    length = float(segment.get('end', 0)) - float(segment.get('start', 0))
    return needed if length < needed - 1e-6 else None


def folders():
    """Where theme files are looked for: Subtitld's own, then the user's."""
    return [(session.PATH_SUBTITLD / 'themes', True),
            (session.PATH_SUBTITLD_USER_DATA / 'themes', False)]


_themes = None
problems = {}       # file → why it was not loaded
# The themes the open project's file carries (a USFX's assets/themes/):
# usable while it is open, never installed (carry()).
_carried = {}
carried_problems = {}   # member → why it was not used


def available(kind=None, reload=False):
    """Every usable theme by id, built-in ones first; of `kind` only, if given."""
    global _themes
    if _themes is None or reload:
        _themes = {}
        problems.clear()
        for root, builtin in folders():
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.iterdir() if p.suffix == EXTENSION and p.is_file()):
                try:
                    theme = load(path, builtin)
                except ThemeError as error:
                    problems[path] = str(error)
                    continue
                if theme.id in _themes:
                    problems[path] = f'id {theme.id} is taken'
                    continue
                if not is_new_enough(theme):
                    problems[path] = f'needs Subtitld {theme.min_subtitld}'
                    continue
                _themes[theme.id] = theme
    found = dict(_themes)
    # One the project carries stands in for a theme not installed, or for an
    # older version of it; never for one of Subtitld's own.
    for theme_id, theme in _carried.items():
        mine = found.get(theme_id)
        if mine is None or (not mine.builtin and version_key(theme.version) > version_key(mine.version)):
            found[theme_id] = theme
    return {key: theme for key, theme in found.items() if kind is None or theme.kind == kind}


def version_key(version):
    """A theme's version, to compare: (26, 12, 1); () for none."""
    return tuple(int(part) for part in version.split('.')) if version and _VERSION.fullmatch(version) else ()


def reload_looks():
    """The subtitle styles' and title themes' lists, built from these."""
    from subtitld.modules import subtitle_styles, title_themes
    subtitle_styles.available(reload=True)
    title_themes.available(reload=True)


def carry(files):
    """The themes the project just opened carries ({member: bytes}), in
    place of the last project's: checked as any theme file is, and kept in
    Subtitld's cache while the project is open."""
    if not files and not _carried and not carried_problems:
        return
    _carried.clear()
    carried_problems.clear()
    folder = session.PATH_SUBTITLD_USER_CACHE / 'themes' / 'project'
    for old in folder.glob(f'*{EXTENSION}') if folder.is_dir() else ():
        old.unlink(missing_ok=True)
    for member, data in sorted(files.items()):
        try:
            theme = parse(data)
        except ThemeError as error:
            carried_problems[member] = str(error)
            continue
        if not is_new_enough(theme):
            carried_problems[member] = f'needs Subtitld {theme.min_subtitld}'
            continue
        folder.mkdir(parents=True, exist_ok=True)
        theme.path = folder / f'{theme.id}{EXTENSION}'
        theme.path.write_text(theme.html, encoding='utf-8')
        theme.carried = True
        _carried[theme.id] = theme
    reload_looks()


def used(segments, table=None):
    """{theme id: version} of every theme a project draws with: its
    subtitles' (`table`, the project's theme table, and the cues' own) and
    its titles'; the version where the project says it ('' otherwise)."""
    from subtitld.modules import cue_kinds
    found = {}
    if isinstance(table, dict) and isinstance(table.get('id'), str) and table['id']:
        found[table['id']] = str(table.get('version') or '')
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        kind = cue_kinds.kind_of(segment)
        own = segment.get('title_theme') if kind == cue_kinds.TITLE else \
            segment.get('theme') if kind != cue_kinds.MARK else None
        if isinstance(own, str) and own:
            found.setdefault(own, '')
    return found


def missing(segments, table=None):
    """{theme id: version} of the themes a project draws with that are not
    here (installed, Subtitld's or carried by its file)."""
    here = available()
    return {theme_id: version for theme_id, version in used(segments, table).items() if theme_id not in here}


def to_embed(segments, table=None, installed=False):
    """The theme files a project's file carries: the ones it draws with that
    only it carries here, always (it would lose them otherwise), and with
    `installed` the user's own it draws with too; never Subtitld's."""
    here = available()
    themes = [here[theme_id] for theme_id in used(segments, table) if theme_id in here]
    return [theme for theme in themes if not theme.builtin and (theme.carried or installed)]
