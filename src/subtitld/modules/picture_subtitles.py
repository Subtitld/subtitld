"""Picture subtitles (DVD VobSub, Blu-ray PGS) made ready for OCR, and its text made subtitles.

The readers (vobsub, pgs) give a `Picture` per subtitle: its time, its place
on the frame and its pixels, as palette indices with the palette's colours.
An OCR add-on reads text best as dark ink on light paper, so `ink` turns
a picture into 8-bit grayscale that way, in one table lookup:

- 'light' (the default): the picture laid on black and inverted, so a light
  fill (white, yellow: almost every disc) becomes dark ink and its dark
  outline light paper;
- 'dark': laid on white as it is, for the rare disc with dark letters
  outlined in a light colour.

`segments` makes the add-on's texts subtitles: empty ones dropped, those in
the upper part of the frame starting {\\an8}, and a subtitle a disc repeats
(PGS redraws one to fade it) joined into one.
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
                   indices, picture.colours, picture.forced, picture.screen_width, picture.screen_height)


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


def segments(pictures, texts, join_gap=0.1):
    """Subtitles from the pictures and the text read in each."""
    found = []
    for picture, text in zip(pictures, texts):
        text = (text or '').strip()
        if not text:
            continue
        if picture.screen_height and picture.y + picture.height / 2 < picture.screen_height * 0.4:
            text = '{\\an8}' + text
        previous = found[-1] if found else None
        if previous and previous['text'] == text and picture.start - previous['end'] <= join_gap:
            previous['end'] = max(previous['end'], picture.end)
            continue
        found.append({'start': round(picture.start, 3), 'end': round(picture.end, 3), 'text': text})
    return found
