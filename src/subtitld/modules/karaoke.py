"""Karaoke timing inside a subtitle's text: when each word or syllable of it
is sung.

Subtitld has one place for it, and it is the one ASS gives: the override
codes {\\k}, {\\K}, {\\kf} and {\\ko}, each the length of the syllable after
it in hundredths of a second, the syllables following one another from the
subtitle's start. modules.markup keeps override codes it does not draw as
they are written, so the timing goes through the editor, the plain-text
panel and an ASS export untouched, and the preview shows the words without
them. A code with no text after it is a pause.

    {\\k50}Hel{\\k30}lo {\\k20}{\\k60}world

is "Hel" for half a second from the start, "lo " for 0.3, a pause of 0.2,
then "world" for 0.6.

`timed` reads that back as times, for the formats that write each word's
moment (LRC's word stamps, UltraStar's notes); `text_from` writes it from
times. Positions are rounded from the subtitle's start, not syllable by
syllable, so the hundredths do not drift over a long line.
"""

import re

from subtitld.modules import markup

_BLOCK = re.compile(r'\{([^{}]*)\}')
_CODE = re.compile(r'\\(?:kf|ko|k|K)(\d+)')


def syllables(text):
    """[(hundredths, text)] for the karaoke codes in `text`, the text plain;
    a first piece before any code has None. [] when it has no codes."""
    text = str(text or '')
    if not any(_CODE.search(block.group(1)) for block in _BLOCK.finditer(text)):
        return []
    pieces = [[None, '']]
    position = 0
    for block in _BLOCK.finditer(text):
        pieces[-1][1] += text[position:block.start()]
        position = block.end()
        codes = _CODE.findall(block.group(1))
        if not codes:
            # Another override ({\\i1}, {\\an8}): it stays with the text,
            # which `plain` then takes it out of.
            pieces[-1][1] += block.group(0)
            continue
        for code in codes:
            pieces.append([int(code), ''])
    pieces[-1][1] += text[position:]
    if not markup.plain(pieces[0][1]):
        pieces.pop(0)
    return [(duration, markup.plain(piece)) for duration, piece in pieces]


def timed(text, start):
    """[(start, end, text)] in seconds for the syllables of `text`, a
    subtitle starting at `start`; None when it has no karaoke codes. Text
    before the first code takes no time; a pause is a piece with no text."""
    pieces = syllables(text)
    if not pieces:
        return None
    out = []
    at = 0
    for duration, piece in pieces:
        after = at + (duration or 0)
        out.append((round(start + at / 100, 3), round(start + after / 100, 3), piece))
        at = after
    return out


def text_from(start, pieces):
    """A subtitle's text from [(start, end, text)] syllables, by time, for a
    subtitle that starts at `start`: a pause where one ends before the next
    begins. Overlapping syllables are cut where the next one starts."""
    out = []
    at = 0
    for begin, end, piece in sorted(pieces, key=lambda piece: piece[0]):
        here = max(at, round((begin - start) * 100))
        if here > at:
            out.append(f'{{\\k{here - at}}}')
        there = max(here, round((end - start) * 100))
        out.append(f'{{\\k{there - here}}}{piece}')
        at = there
    return ''.join(out)
