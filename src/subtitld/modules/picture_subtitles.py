"""Picture subtitles (DVD VobSub, Blu-ray PGS, IMSC's Image Profile) made ready for OCR, and its text made subtitles.

The readers (vobsub, pgs, picture_ocr for IMSC's PNGs) give a `Picture` per
subtitle: its time, its place on the frame and its pixels, as palette
indices with the palette's colours, and the words it is, where the file
gives them (IMSC's alt text), so those need no reading.
An OCR add-on reads text best as dark ink on light paper, so `ink` turns
a picture into 8-bit grayscale that way, in one table lookup:

- 'light' (the default): the picture laid on black and inverted, so a light
  fill (white, yellow: almost every disc) becomes dark ink and its dark
  outline light paper;
- 'dark': laid on white as it is, for the rare disc with dark letters
  outlined in a light colour.

`segments` makes the add-on's texts subtitles: empty ones dropped, those in
the upper part of the frame starting {\\an8}, the pictures shown together
in one part of the frame (a PGS composition's windows) one subtitle, and a
subtitle a disc repeats (PGS redraws one to fade it) joined into one.
"""

from dataclasses import dataclass, field


@dataclass
class Picture:
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int
    indices: bytes                  # width * height palette indices, row after row
    colours: list = field(default_factory=list)   # [(r, g, b, a)] per index
    forced: bool = False
    screen_width: int = 0
    screen_height: int = 0
    text: str = ''                  # its words where the file gives them, '' to read them


def _table(colours, polarity):
    table = bytearray(256)
    for index in range(256):
        r, g, b, a = colours[index] if index < len(colours) else (0, 0, 0, 0)
        luma = (0.299 * r + 0.587 * g + 0.114 * b) * a / 255
        if polarity == 'dark':
            value = luma + 255 * (1 - a / 255)
        else:
            value = 255 - luma
        table[index] = max(0, min(255, int(round(value))))
    return bytes(table)


def crop(picture):
    """The picture without its transparent margins (a new Picture), or None
    when nothing in it shows."""
    opaque = bytes(1 if (picture.colours[i][3] if i < len(picture.colours) else 0) > 0 else 0
                   for i in range(256))
    mask = picture.indices.translate(opaque)
    width = picture.width
    rows = [y for y in range(picture.height) if mask[y * width:(y + 1) * width].strip(b'\x00')]
    if not rows:
        return None
    top, bottom = rows[0], rows[-1] + 1
    left, right = width, 0
    for y in range(top, bottom):
        row = mask[y * width:(y + 1) * width]
        stripped = row.lstrip(b'\x00')
        if stripped:
            left = min(left, width - len(stripped))
            right = max(right, len(row.rstrip(b'\x00')))
    indices = b''.join(picture.indices[y * width + left:y * width + right] for y in range(top, bottom))
    return Picture(picture.start, picture.end, picture.x + left, picture.y + top, right - left, bottom - top,
                   indices, picture.colours, picture.forced, picture.screen_width, picture.screen_height,
                   picture.text)


def ink(picture, polarity='light'):
    """The picture as 8-bit grayscale, dark text on light: width * height bytes."""
    return picture.indices.translate(_table(picture.colours, polarity))


def pgm(picture, polarity='light', margin=8):
    """The picture as a binary PGM, with a margin of paper around it."""
    gray = ink(picture, polarity)
    width = picture.width + 2 * margin
    paper = b'\xff' * width
    side = b'\xff' * margin
    rows = [paper] * margin
    rows += [side + gray[y * picture.width:(y + 1) * picture.width] + side for y in range(picture.height)]
    rows += [paper] * margin
    return f'P5\n{width} {picture.height + 2 * margin}\n255\n'.encode('ascii') + b''.join(rows)


def reading_order(pictures):
    """`pictures` shown together, in the order they read: in rows (those
    whose heights overlap are side by side), top to bottom, each row left
    to right. A list of rows, each a list of pictures."""
    rows = []
    for picture in sorted(pictures, key=lambda p: (p.y, p.x)):
        row = next((row for row in rows if any(
            picture.y < other.y + other.height and other.y < picture.y + picture.height for other in row)), None)
        if row is None:
            rows.append([picture])
        else:
            row.append(picture)
    return [sorted(row, key=lambda p: p.x) for row in rows]


def _at_top(picture):
    return bool(picture.screen_height and picture.y + picture.height / 2 < picture.screen_height * 0.4)


def segments(pictures, texts, join_gap=0.1):
    """Subtitles from the pictures and the text read in each. The pictures
    of one moment (the windows of a PGS composition: the same start and
    end) in the same part of the frame are one subtitle, read in rows, a
    row's words side by side and the rows as its lines; a sign at the top
    and the line at the bottom stay two."""
    groups = []
    for picture, text in zip(pictures, texts):
        text = (text or '').strip()
        if not text:
            continue
        top = _at_top(picture)
        group = None
        for other in reversed(groups):
            if other['start'] != picture.start:
                break
            if other['end'] == picture.end and other['top'] == top:
                group = other
                break
        if group is None:
            group = {'start': picture.start, 'end': picture.end, 'top': top, 'parts': []}
            groups.append(group)
        group['parts'].append((picture, text))
    found = []
    for group in groups:
        texts = {id(picture): text for picture, text in group['parts']}
        text = '\n'.join(' '.join(texts[id(picture)] for picture in row)
                         for row in reading_order([picture for picture, _text in group['parts']]))
        if group['top']:
            text = '{\\an8}' + text
        start, end = group['start'], group['end']
        forced = any(picture.forced for picture, _text in group['parts'])
        # A subtitle a disc repeats (PGS redraws one to fade it): the last
        # one in the same part of the frame, joined when it says the same.
        previous = next((s for s in reversed(found) if s['text'].startswith('{\\an8}') == group['top']), None)
        if previous and previous['text'] == text and start - previous['end'] <= join_gap:
            previous['end'] = max(previous['end'], round(end, 3))
            if forced:
                previous['forced'] = True
            continue
        subtitle = {'start': round(start, 3), 'end': round(end, 3), 'text': text}
        # A DVD or a Blu-ray marks the subtitle a viewer sees even with
        # subtitles off: the sign or the foreign line a film translates.
        if forced:
            subtitle['forced'] = True
        found.append(subtitle)
    return found
