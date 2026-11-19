"""Karaoke timing inside a subtitle's text: when each word or syllable of it
is sung.

A subtitle holds it as timed pieces (modules.segment). The formats that
carry it inside the text write it as ASS does: the override codes {\\k},
{\\K}, {\\kf} and {\\ko}, each the length of the syllable after it in
hundredths of a second, the syllables following one another from the
subtitle's start. A code with no text after it is a pause.

    {\\k50}Hel{\\k30}lo {\\k20}{\\k60}world

is "Hel" for half a second from the start, "lo " for 0.3, a pause of 0.2,
then "world" for 0.6.

`pieces_of` reads the codes into a subtitle's pieces when a file is opened,
`codes_of` writes them back for the formats that read them (an export's
copies, export_options.prepare). `timed` reads them as times, for the
formats that write each word's moment (LRC's word stamps, UltraStar's
notes); `text_from` writes them from times. Positions are rounded from
the subtitle's start, not syllable by syllable, so the hundredths do not
drift over a long line.
"""

import re

from subtitld.modules import markup

_BLOCK = re.compile(r'\{([^{}]*)\}')
_CODE = re.compile(r'\\(?:kf|ko|k|K)(\d+)')
_KIND = re.compile(r'\\(kf|ko|k|K)(\d+)')

# How a syllable lights up (modules.segment.EFFECTS): \\k all at once, the
# style's way, so it is no effect of its own; \\kf and \\K a wipe across it;
# \\ko its outline.
_EFFECTS = {'k': None, 'kf': 'wipe', 'K': 'wipe', 'ko': 'outline'}
_CODES = {'wipe': 'kf', 'outline': 'ko'}

# Where a karaoke code stood, in a text a reader builds as it reads its
# formatting (modules.ass, modules.usf): marked_pieces turns it into pieces.
MARK = '\ue000'


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


def pieces_of(text):
    """The timed pieces of a text with karaoke codes, as a subtitle holds
    them (modules.segment): times in seconds from its start, the effect
    \\kf, \\K and \\ko say. Text before the first code goes into the first
    piece (its own, taking no time, if it has words), a pause is a gap
    between two. None for a text with no codes, and for one with karaoke
    and other override codes in one block, which stays as it is written."""
    text = str(text or '')
    items, position = [], 0
    for block in _BLOCK.finditer(text):
        codes = _KIND.findall(block.group(1))
        if not codes:
            continue  # another override ({\\i1}): it stays in the text
        if _KIND.sub('', block.group(1)).strip():
            return None
        items.append(text[position:block.start()])
        items += [(kind, int(length)) for kind, length in codes]
        position = block.end()
    if not items:
        return None
    items.append(text[position:])
    pieces, lead, at, current = [], '', 0, None

    def close():
        nonlocal lead
        if current is None:
            return
        if current['text'].strip():
            pieces.append(current)
        elif pieces:
            pieces[-1]['text'] += current['text']
        else:
            lead += current['text']

    for item in items:
        if isinstance(item, str):
            if current is None:
                lead += item
            else:
                current['text'] += item
            continue
        close()
        kind, length = item
        current = {'text': '', 'start': at / 100, 'end': (at + length) / 100}
        if _EFFECTS[kind]:
            current['effect'] = _EFFECTS[kind]
        at += length
    close()
    if not pieces:
        return None
    if markup.plain(lead).strip():
        pieces.insert(0, {'text': lead, 'start': 0.0, 'end': 0.0})
    else:
        pieces[0]['text'] = lead + pieces[0]['text']
    return pieces


def codes_of(pieces):
    """A subtitle's timed pieces as a text with karaoke codes: what the
    first starts with that places the subtitle ({\\an8}) before them, a
    pause where one ends before the next begins."""
    if not pieces:
        return ''
    lead, first = markup.split_lead(pieces[0]['text'])
    out, at = [lead], 0
    for number, piece in enumerate(pieces):
        here = max(at, round(float(piece['start']) * 100))
        if here > at:
            out.append(f'{{\\k{here - at}}}')
        there = max(here, round(float(piece['end']) * 100))
        code = _CODES.get(piece.get('effect'), 'k')
        out.append(f'{{\\{code}{there - here}}}' + (first if number == 0 else piece['text']))
        at = there
    return ''.join(out)


def timed_texts(segments):
    """Each of `segments` whose text has karaoke codes timed by them, its
    text its pieces (pieces_of), as a file opened gives them; returns
    `segments`."""
    for segment in segments or []:
        text = segment.get('text')
        if isinstance(text, str) and '\\k' in text.lower():
            pieces = pieces_of(text)
            if pieces:
                segment['text'] = pieces
    return segments


def effect_of(code):
    """The effect of a karaoke code's name ('k', 'kf', 'K', 'ko'), None for
    the style's."""
    return _EFFECTS.get(code)


def marked_pieces(text, marks):
    """A subtitle's timed pieces from a text with MARK where each karaoke
    code stood and `marks`, in order, each a code's length in seconds and
    what else its piece says ({'effect': ...}): each piece the text after
    its mark, from the end of the one before. Text with no words after a
    mark is a pause (its spaces stay with the piece before); text before
    the first, a piece of its own taking no time if it has words. None for
    no words after any mark."""
    parts = text.split(MARK)
    lead, pieces, at = parts[0], [], 0.0
    for part, (length, extras) in zip(parts[1:], marks):
        if part.strip():
            pieces.append(dict(extras, text=part, start=round(at, 3), end=round(at + length, 3)))
        elif pieces:
            pieces[-1]['text'] += part
        else:
            lead += part
        at += length
    if not pieces:
        return None
    if markup.plain(lead).strip():
        pieces.insert(0, {'text': lead, 'start': 0.0, 'end': 0.0})
    else:
        pieces[0]['text'] = lead + pieces[0]['text']
    return pieces
