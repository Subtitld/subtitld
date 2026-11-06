"""Where a subtitle sits, beyond the nine places.

USF says it with a <position>: an alignment, a horizontal and a vertical
margin, what those are measured against, and rotations about the three
axes. Subtitld already keeps the alignment — the nine places, 7 8 9 across
the top to 1 2 3 across the bottom — as an {\\anN} code at the start of the
text (modules.markup), where every format reads it from. The rest is a
subtitle's 'position':

    {'h': '10%', 'v': '20%', 'relative_to': 'video',
     'rotate': {'x': 0, 'y': 0, 'z': 45}}

Every key is optional and a subtitle with nothing to say has no 'position'
at all, so a file, a project and a wire message that never place a subtitle
precisely look exactly as they did before this existed.

A margin is the distance from the edge the text is aligned to: a subtitle
at the bottom with a vertical margin of 20% sits a fifth of the picture up
from the bottom. Against a middle alignment it moves the text from the
middle. It is written as USF writes it, a number for pixels of a 1080-tall
picture or a number with "%" for part of it, so a file read and written
again says what it said.

`relative_to` is 'video' or 'window', USF's two. Subtitld draws subtitles
on the picture, where the two are the same thing; the value is kept so a
file that says 'window' still says it after a round trip.

Rotations are degrees, counter-clockwise about each axis, as USF and ASS
both count them.
"""

import re
from typing import NamedTuple

AXES = ('x', 'y', 'z')
RELATIVE_TO = ('video', 'window')

_LENGTH = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*(%?)\s*$')

# The picture a pixel margin is counted in: the stage the renderer draws on
# and the lengths a style's settings are written in.
PICTURE_HEIGHT = 1080.0


class Place(NamedTuple):
    """A subtitle's position, with '' and 0 for what it does not say."""

    h: str = ''
    v: str = ''
    relative_to: str = ''
    rotate: tuple = (0.0, 0.0, 0.0)          # degrees about x, y and z

    def any(self):
        return self != NOWHERE

    def turned(self):
        return any(self.rotate)


NOWHERE = Place()


def length(value):
    """(number, unit) for a margin as USF writes it, unit '%' or 'px';
    None when it is not a length."""
    found = _LENGTH.match(str(value or ''))
    if not found:
        return None
    return float(found.group(1)), ('%' if found.group(2) else 'px')


def as_percent(value, picture=None):
    """A margin as a percentage of the picture, or None.

    `picture` is how many pixels the picture measures along that axis, for
    a margin written in pixels; without it a pixel margin is taken against
    a 1080-tall picture, which is what a style's lengths are written in.
    """
    found = length(value)
    if found is None:
        return None
    amount, unit = found
    if unit == '%':
        return amount
    return amount * 100.0 / float(picture or PICTURE_HEIGHT)


def _rotation(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def of(segment):
    """The Place a subtitle holds; NOWHERE when it holds none."""
    held = segment.get('position') if isinstance(segment, dict) else None
    if not isinstance(held, dict):
        return NOWHERE
    turns = held.get('rotate') if isinstance(held.get('rotate'), dict) else {}
    relative = str(held.get('relative_to') or '').strip().lower()
    return Place(
        h=str(held.get('h') or '').strip() if length(held.get('h')) else '',
        v=str(held.get('v') or '').strip() if length(held.get('v')) else '',
        relative_to=relative if relative in RELATIVE_TO else '',
        rotate=tuple(_rotation(turns.get(axis)) for axis in AXES))


def store(segment, place):
    """Put `place` on a subtitle, or take its 'position' away when the
    place says nothing. Returns the subtitle."""
    held = {}
    if place.h:
        held['h'] = place.h
    if place.v:
        held['v'] = place.v
    if place.relative_to in RELATIVE_TO:
        held['relative_to'] = place.relative_to
    if place.turned():
        held['rotate'] = {axis: turn for axis, turn in zip(AXES, place.rotate) if turn}
    if held:
        segment['position'] = held
    else:
        segment.pop('position', None)
    return segment


def from_usf(attributes):
    """The Place a USF <position> or <text> gives, by its attribute names."""
    def attribute(name):
        value = attributes.get(name)
        return '' if value is None else str(value)

    margins = {}
    for key, name in (('h', 'horizontal-margin'), ('v', 'vertical-margin')):
        margins[key] = attribute(name).strip() if length(attribute(name)) else ''
    relative = attribute('relative-to').strip().lower()
    return Place(
        h=margins['h'], v=margins['v'],
        relative_to=relative if relative in RELATIVE_TO else '',
        rotate=tuple(_rotation(attributes.get(f'rotate-{axis}')) for axis in AXES))


def to_usf(place):
    """{attribute: value} for a USF <position>, without the alignment,
    which the text's own code says."""
    attributes = {}
    if place.h:
        attributes['horizontal-margin'] = place.h
    if place.v:
        attributes['vertical-margin'] = place.v
    if place.relative_to in RELATIVE_TO:
        # USF writes them capitalised, as its examples do.
        attributes['relative-to'] = place.relative_to.capitalize()
    for axis, turn in zip(AXES, place.rotate):
        if turn:
            attributes[f'rotate-{axis}'] = _number(turn)
    return attributes


def _number(value):
    """A number as a file writes it: 45, not 45.0."""
    return str(int(value)) if float(value).is_integer() else str(float(value))
