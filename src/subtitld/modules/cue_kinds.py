"""Kinds of cue: subtitles, marks and titles, all in SUBTITLE.segments.

A cue's 'kind' says which it is: none (or 'subtitle') for a subtitle,
'mark' for a mark, 'title' for a title. Each kind has its own lane in the
timeline (modules.timeline_style draws them by kind) and a flag in the
subtitles list; what is only for subtitles — the quality check, speech
recognition, dubbing, the formats of subtitles — takes `subtitles()`.

A mark is a place in the media worth finding again: a chapter, a note, a
to-do, a comment, or a category the user made. Its text is plain, no
formatting. Its 'category' is the id of one of the categories (below). A
mark the user gave no end to is 'open_end', its 'end' its start: the
timeline shows it as its label alone. Where a format needs it to end
(chapters, modules.chapters), it lasts until the next mark of its category
starts, the last one until the media ends (`ends()`); that end is worked
out for the file, never kept.

    {'kind': 'mark', 'start': 62.0, 'end': 62.0, 'text': 'Act two',
     'category': 'chapter', 'open_end': True}

A title is text drawn by a title theme over the picture: a heading, a
lower third, on-screen text. Its 'title_theme' is the theme's id and its
'title_settings' only what this title changed of the theme's defaults
(modules.title_themes); its 'title_transform', when it has one, how it is
moved, turned and sized on top of what the theme draws.

    {'kind': 'title', 'start': 3.0, 'end': 8.0, 'text': 'Lisbon, 1755',
     'title_theme': 'subtitld.plain', 'title_settings': {'font_size': 72}}

Mark categories are the app's (CONFIG['marks']['categories']): four built
in (BUILTIN_CATEGORIES), and those the user adds. A project carries the
ones its marks use, with their names and colours, as its
`mark_categories` ({id: {'name', 'color'}}), so it looks the same on a
computer that does not know them; opening it adds them to that
computer's (`adopt()`). A category's look is the project's where the
project has it, the app's otherwise.
"""

import re
import secrets

from subtitld.modules import session

SUBTITLE = 'subtitle'
MARK = 'mark'
TITLE = 'title'
KINDS = (SUBTITLE, MARK, TITLE)

# The built-in categories: their names are translated (marks.category.<id>)
# unless the user renamed them.
BUILTIN_CATEGORIES = {
    'chapter': {'name': 'Chapter', 'color': '#ffe5a823'},
    'note': {'name': 'Note', 'color': '#ff4fa3e0'},
    'todo': {'name': 'To-do', 'color': '#ffe0574f'},
    'comment': {'name': 'Comment', 'color': '#ff7fc77a'},
}
DEFAULT_CATEGORY = 'chapter'
# What a mark of a category no one here knows is drawn in.
UNKNOWN_COLOR = '#ff8c8c8c'
_COLOR = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})')


def kind_of(segment):
    """The kind of cue `segment` is: SUBTITLE, MARK or TITLE."""
    kind = segment.get('kind') if hasattr(segment, 'get') else None
    return kind if kind in (MARK, TITLE) else SUBTITLE


def is_subtitle(segment):
    return kind_of(segment) == SUBTITLE


def subtitles(segments):
    """The subtitles of `segments`, without its marks and titles."""
    return [segment for segment in segments or [] if kind_of(segment) == SUBTITLE]


def of_kind(segments, kind):
    """The cues of `segments` of `kind`, in their order."""
    return [segment for segment in segments or [] if kind_of(segment) == kind]


def new_mark(start, text='', category=None, end=None):
    """A mark at `start`, open-ended unless it is given an `end`."""
    mark = {'kind': MARK, 'start': round(float(start), 3), 'text': str(text or ''),
            'category': category or DEFAULT_CATEGORY}
    if end is None or float(end) <= float(start):
        # No end: a point, until a format needs it to end (ends()).
        mark['end'] = mark['start']
        mark['open_end'] = True
    else:
        mark['end'] = round(float(end), 3)
    return mark


def new_title(start, end, text='', theme=None):
    """A title from `start` to `end`, drawn by `theme` (the current title
    theme when None)."""
    if theme is None:
        from subtitld.modules import title_themes
        theme = title_themes.current_id()
    return {'kind': TITLE, 'start': round(float(start), 3), 'end': round(float(end), 3), 'text': str(text or ''),
            'title_theme': theme, 'title_settings': {}}


# --------------------------------------------------------------------------- #
# Open ends                                                                    #
# --------------------------------------------------------------------------- #
def has_end(mark):
    """Whether a mark was given an end: one that lasts, not a point."""
    return not mark.get('open_end') and float(mark.get('end', 0)) > float(mark.get('start', 0))


def ends(segments, duration=None):
    """{id(mark): end} for the marks of `segments`, where a format needs every
    mark to end: its own, else the start of the next mark of its category,
    the last one's the end of the media (`duration`, the video's when
    None; a mark past it, or with no media, ends where it starts). Nothing
    is changed."""
    if duration is None:
        duration = float((session.VIDEO or {}).get('duration') or 0)
    by_category = {}
    for segment in segments or []:
        if kind_of(segment) == MARK:
            by_category.setdefault(segment.get('category') or DEFAULT_CATEGORY, []).append(segment)
    out = {}
    for marks in by_category.values():
        marks.sort(key=lambda mark: float(mark.get('start', 0)))
        for number, mark in enumerate(marks):
            start = float(mark.get('start', 0))
            if has_end(mark):
                out[id(mark)] = float(mark['end'])
                continue
            following = next((float(later['start']) for later in marks[number + 1:]
                              if float(later['start']) > start), None)
            out[id(mark)] = round(following if following is not None else max(start, float(duration or 0)), 3)
    return out


# --------------------------------------------------------------------------- #
# Mark categories                                                              #
# --------------------------------------------------------------------------- #
def _color(value, fallback=UNKNOWN_COLOR):
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        return fallback
    return ('#ff' + value[1:] if len(value) == 7 else value).lower()


def _config():
    config = session.CONFIG.get('marks') if isinstance(session.CONFIG, dict) else None
    if not isinstance(config, dict):
        config = {}
        if isinstance(session.CONFIG, dict):
            session.CONFIG['marks'] = config
    return config


def _saved():
    saved = _config().get('categories')
    return saved if isinstance(saved, dict) else {}


def categories():
    """The app's categories, {id: {'name', 'color', 'builtin'}}, the built-in
    ones first, then the user's in the order they were added."""
    saved = _saved()
    out = {}
    for category_id, builtin in BUILTIN_CATEGORIES.items():
        mine = saved.get(category_id) if isinstance(saved.get(category_id), dict) else {}
        out[category_id] = {'name': str(mine.get('name') or builtin['name']),
                            'color': _color(mine.get('color'), builtin['color']), 'builtin': True}
    for category_id, mine in saved.items():
        if category_id in out or not isinstance(mine, dict):
            continue
        out[category_id] = {'name': str(mine.get('name') or category_id), 'color': _color(mine.get('color')),
                            'builtin': False}
    return out


def _new_category_id(name, taken):
    base = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')[:24] or 'category'
    category_id = base
    while category_id in taken:
        category_id = f'{base}-{secrets.token_hex(2)}'
    return category_id


def add_category(name, color, category_id=None):
    """A category of the user's; its id."""
    taken = set(categories())
    if category_id is None or category_id in taken:
        category_id = _new_category_id(name, taken)
    _config().setdefault('categories', {})[category_id] = {'name': str(name).strip() or category_id,
                                                          'color': _color(color)}
    return category_id


def set_category(category_id, name=None, color=None):
    """Rename or recolour a category, here and in the open project."""
    current = categories().get(category_id)
    if current is None:
        raise KeyError(category_id)
    saved = _config().setdefault('categories', {}).setdefault(category_id, {})
    if name is not None:
        saved['name'] = str(name).strip() or current['name']
    if color is not None:
        saved['color'] = _color(color, current['color'])
    builtin = BUILTIN_CATEGORIES.get(category_id)
    if builtin:
        # A built-in one keeps only what differs from it.
        if saved.get('name') == builtin['name']:
            saved.pop('name', None)
        if saved.get('color') == builtin['color']:
            saved.pop('color', None)
        if not saved:
            _config()['categories'].pop(category_id, None)
    table = _project_table()
    if category_id in table:
        table[category_id] = {'name': categories()[category_id]['name'], 'color': categories()[category_id]['color']}


def remove_category(category_id):
    """Forget one of the user's categories (the built-in ones stay). Marks of
    it keep it, drawn as the project has it."""
    if category_id in BUILTIN_CATEGORIES:
        raise ValueError('a built-in category stays')
    _config().get('categories', {}).pop(category_id, None)


def _project_table():
    table = getattr(session.SUBTITLE, 'mark_categories', None)
    return table if isinstance(table, dict) else {}


def category(category_id):
    """{'id', 'name', 'color', 'builtin', 'known'} of a category: as the open
    project has it, else as the app does; a category neither knows has its
    id for a name and a neutral colour."""
    category_id = category_id or DEFAULT_CATEGORY
    app = categories().get(category_id)
    project = _project_table().get(category_id)
    if isinstance(project, dict):
        name = str(project.get('name') or (app or {}).get('name') or category_id)
        color = _color(project.get('color'), (app or {}).get('color', UNKNOWN_COLOR))
    elif app:
        name, color = app['name'], app['color']
    else:
        name, color = category_id, UNKNOWN_COLOR
    builtin = BUILTIN_CATEGORIES.get(category_id)
    return {'id': category_id, 'name': name, 'color': color, 'known': app is not None,
            'builtin': bool(builtin and name == builtin['name'])}


def display_name(category_id, translate=None):
    """A category's name as shown: a built-in one's not renamed translated by
    `translate` (marks.category.<id>)."""
    found = category(category_id)
    if found['builtin'] and translate is not None:
        translated = translate(f'marks.category.{category_id}')
        if translated and translated != f'marks.category.{category_id}':
            return translated
    return found['name']


def project_table(segments):
    """{id: {'name', 'color'}} of the categories the marks of `segments` use,
    as the project will carry them."""
    table = {}
    for segment in segments or []:
        if kind_of(segment) != MARK:
            continue
        category_id = segment.get('category') or DEFAULT_CATEGORY
        if category_id not in table:
            found = category(category_id)
            table[category_id] = {'name': found['name'], 'color': found['color']}
    return table


def adopt(table):
    """A project's categories, as it was opened: the project draws its marks
    with them, and those this app does not know are added to its own."""
    clean = {}
    for category_id, values in (table or {}).items():
        if not isinstance(category_id, str) or not category_id or not isinstance(values, dict):
            continue
        clean[category_id] = {'name': str(values.get('name') or category_id), 'color': _color(values.get('color'))}
    known = categories()
    for category_id, values in clean.items():
        if category_id not in known:
            add_category(values['name'], values['color'], category_id=category_id)
    return clean


# --------------------------------------------------------------------------- #
# The timeline's lanes                                                         #
# --------------------------------------------------------------------------- #
def _timeline_config():
    timeline = session.CONFIG.get('timeline') if isinstance(session.CONFIG, dict) else None
    return timeline if isinstance(timeline, dict) else {}


def shown(kind):
    """Whether the timeline shows (and lets the mouse take) cues of `kind`."""
    if kind == SUBTITLE:
        return True
    return bool(_timeline_config().get(f'show_{kind}s', True))


def set_shown(kind, value):
    if kind == SUBTITLE:
        return
    if isinstance(session.CONFIG, dict):
        session.CONFIG.setdefault('timeline', {})[f'show_{kind}s'] = bool(value)
