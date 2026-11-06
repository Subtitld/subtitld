"""Picture subtitle files for the tests: VobSub (.idx/.sub) and PGS (.sup) written from scratch.

Each subtitle is drawn with Qt (white letters, black outline), cut to four
colours (transparent, fill, antialiasing, outline) and encoded the way a
DVD or Blu-ray authoring tool does: a VobSub SPU per subtitle in 2048-byte
MPEG-2 packs with its .idx; a PGS display set per subtitle and one that
clears it. FFmpeg reads both (test_picture_subtitles checks that), so they
test the readers against files the readers did not make.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

PALETTE16 = ['000000', 'f0f0f0', '808080', '101010'] + ['808080'] * 12


def draw(text, size=24, bold=True, italic=False):
    """(width, height, indices): the text drawn, 0 transparent, 1 fill, 2 edge, 3 outline."""
    font = QFont('DejaVu Sans', size)
    font.setPixelSize(size)
    font.setBold(bold)
    font.setItalic(italic)
    path = QPainterPath()
    lines = text.split('\n')
    step = size * 1.25
    for number, line in enumerate(lines):
        line_path = QPainterPath()
        line_path.addText(0, 0, font, line)
        box = line_path.boundingRect()
        path.addPath(line_path.translated(-box.width() / 2 - box.left(), number * step))
    box = path.boundingRect().adjusted(-6, -6, 6, 6)
    width, height = int(box.width()) + 2, int(box.height()) + 2
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(-box.left(), -box.top())
    # The outline first, twice as wide, then the letters over it: the outline
    # stays outside them, as authoring tools draw it, and thin strokes
    # (accents, cedillas) keep their fill.
    painter.setPen(QPen(QColor(16, 16, 16), 2 * max(2, size // 10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(QColor(16, 16, 16))
    painter.drawPath(path)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(240, 240, 240))
    painter.drawPath(path)
    painter.end()
    indices = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 110:
                continue
            luma = pixel.red() * 0.299 + pixel.green() * 0.587 + pixel.blue() * 0.114
            indices[y * width + x] = 1 if luma > 170 else 2 if luma > 80 else 3
    return width, height, bytes(indices)


# --- VobSub ---------------------------------------------------------------

def _nibbles_for_run(count, colour):
    value = count << 2 | colour
    digits = 1 if count < 4 else 2 if count < 16 else 3 if count < 64 else 4
    return [(value >> (4 * k)) & 15 for k in reversed(range(digits))]


def _rle_rows(rows, width):
    nibbles = []
    for row in rows:
        x = 0
        while x < width:
            colour = row[x]
            run = 1
            while x + run < width and row[x + run] == colour and run < 255:
                run += 1
            if x + run == width and run > 0:
                nibbles += [0, 0, 0, colour]           # to the end of the line
            else:
                nibbles += _nibbles_for_run(run, colour)
            x += run
        if len(nibbles) % 2:
            nibbles.append(0)
    return bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def spu(width, height, indices, x, y, duration, forced=False):
    rows = [indices[r * width:(r + 1) * width] for r in range(height)]
    top = _rle_rows(rows[0::2], width)
    bottom = _rle_rows(rows[1::2], width)
    top_offset = 4
    bottom_offset = top_offset + len(top)
    control = bottom_offset + len(bottom)
    x2, y2 = x + width - 1, y + height - 1
    first = bytes([0x03, 0x32, 0x10,                   # colours of values 3, 2, 1, 0: palette 3, 2, 1, 0
                   0x04, 0xFF, 0xF0,                   # alpha: 3, 2, 1 opaque, 0 clear
                   0x05, x >> 4, (x & 15) << 4 | x2 >> 8, x2 & 255, y >> 4, (y & 15) << 4 | y2 >> 8, y2 & 255,
                   0x06, top_offset >> 8, top_offset & 255, bottom_offset >> 8, bottom_offset & 255,
                   0x00 if forced else 0x01, 0xFF])
    second_offset = control + 4 + len(first)
    stop = round(duration / (1024 / 90000))
    sequences = bytes([0, 0, second_offset >> 8, second_offset & 255]) + first
    sequences += bytes([stop >> 8, stop & 255, second_offset >> 8, second_offset & 255, 0x02, 0xFF])
    body = top + bottom + sequences
    size = 4 + len(body)
    return bytes([size >> 8, size & 255, control >> 8, control & 255]) + body


def _pack(scr_seconds, pes):
    scr = int(scr_seconds * 90000)
    header = bytes([0, 0, 1, 0xBA,
                    0x44 | (scr >> 27 & 0x38) | (scr >> 28 & 3), scr >> 20 & 255,
                    (scr >> 12 & 0xF8) | 4 | (scr >> 13 & 3), scr >> 5 & 255, (scr << 3 & 0xF8) | 4, 1,
                    0x01, 0x89, 0xC3, 0xF8])
    pack = header + pes
    pad = 2048 - len(pack)
    if pad >= 6:
        pack += bytes([0, 0, 1, 0xBE, (pad - 6) >> 8, (pad - 6) & 255]) + b'\xff' * (pad - 6)
    elif pad > 0:
        pack += b'\xff' * pad
    return pack


def _pes(payload, pts=None, track=0):
    if pts is None:
        header = bytes([0x81, 0x00, 0x00])
    else:
        p = int(pts * 90000)
        header = bytes([0x81, 0x80, 0x05, 0x21 | (p >> 29 & 0x0E), p >> 22 & 255, (p >> 14 & 0xFE) | 1,
                        p >> 7 & 255, (p << 1 & 0xFE) | 1])
    body = header + bytes([0x20 + track]) + payload
    return bytes([0, 0, 1, 0xBD, len(body) >> 8, len(body) & 255]) + body


def write_vobsub(stem, cues, language='en', size=(720, 480)):
    """cues: [(start, end, text, at_top)]. Writes stem.idx and stem.sub."""
    data = bytearray()
    entries = []
    for start, end, text, at_top in cues:
        width, height, indices = draw(text)
        x = (size[0] - width) // 2
        y = 30 if at_top else size[1] - height - 30
        unit = spu(width, height, indices, x, y, end - start)
        entries.append((start, len(data)))
        first = True
        while unit:
            room = 2048 - 14 - 6 - (8 if first else 3) - 1
            chunk, unit = unit[:room], unit[room:]
            data += _pack(start, _pes(chunk, start if first else None))
            first = False
    with open(stem + '.sub', 'wb') as handle:
        handle.write(bytes(data))
    lines = ['# VobSub index file, v7 (do not modify this line!)', f'size: {size[0]}x{size[1]}',
             'org: 0, 0', 'scale: 100%, 100%', 'alpha: 100%', 'smooth: OFF', 'fadein/out: 50, 50',
             'align: OFF at LEFT TOP', 'time offset: 0', 'forced subs: OFF',
             'palette: ' + ', '.join(PALETTE16), 'custom colors: OFF, tridx: 0000, colors: 000000, 000000, 000000, 000000',
             'langidx: 0', f'id: {language}, index: 0']
    for start, position in entries:
        ms = round(start * 1000)
        lines.append(f'timestamp: {ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}:{ms % 1000:03d}, '
                     f'filepos: {position:09x}')
    with open(stem + '.idx', 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')


# --- PGS ------------------------------------------------------------------

def _pgs_rle(width, height, indices):
    out = bytearray()
    for y in range(height):
        row = indices[y * width:(y + 1) * width]
        x = 0
        while x < width:
            colour = row[x]
            run = 1
            while x + run < width and row[x + run] == colour and run < 16383:
                run += 1
            if colour and run <= 2:
                out += bytes([colour]) * run
            elif colour == 0:
                out += bytes([0, run]) if run < 64 else bytes([0, 0x40 | run >> 8, run & 255])
            else:
                out += bytes([0, 0x80 | run, colour]) if run < 64 else bytes([0, 0xC0 | run >> 8, run & 255, colour])
            x += run
        out += b'\x00\x00'
    return bytes(out)


def _segment(pts, kind, payload):
    p = int(pts * 90000)
    return b'PG' + p.to_bytes(4, 'big') + b'\x00\x00\x00\x00' + bytes([kind]) + len(payload).to_bytes(2, 'big') + payload


def write_pgs(path, cues, size=(1920, 1080)):
    """cues: [(start, end, text, at_top)]. Writes a .sup."""
    data = bytearray()
    # Palette: 1 fill, 2 edge, 3 outline, as BT.709 YCbCr.
    palette = bytes([0, 2]) + bytes([1, 219, 128, 128, 255, 2, 126, 128, 128, 255, 3, 30, 128, 128, 255])
    for number, (start, end, text, at_top) in enumerate(cues):
        width, height, indices = draw(text, size=54)
        x = (size[0] - width) // 2
        y = 60 if at_top else size[1] - height - 60
        rle = _pgs_rle(width, height, indices)
        pcs = size[0].to_bytes(2, 'big') + size[1].to_bytes(2, 'big') + bytes([0x10]) + \
            (number * 2).to_bytes(2, 'big') + bytes([0x80, 0, 0, 1]) + \
            bytes([0, 0, 0, 0]) + x.to_bytes(2, 'big') + y.to_bytes(2, 'big')
        wds = bytes([1, 0]) + x.to_bytes(2, 'big') + y.to_bytes(2, 'big') + width.to_bytes(2, 'big') + \
            height.to_bytes(2, 'big')
        ods = bytes([0, 0, 0, 0xC0]) + (len(rle) + 4).to_bytes(3, 'big') + width.to_bytes(2, 'big') + \
            height.to_bytes(2, 'big') + rle
        data += _segment(start, 0x16, pcs) + _segment(start, 0x17, wds) + _segment(start, 0x14, palette)
        data += _segment(start, 0x15, ods) + _segment(start, 0x80, b'')
        clear = size[0].to_bytes(2, 'big') + size[1].to_bytes(2, 'big') + bytes([0x10]) + \
            (number * 2 + 1).to_bytes(2, 'big') + bytes([0, 0, 0, 0])
        data += _segment(end, 0x16, clear) + _segment(end, 0x17, wds) + _segment(end, 0x80, b'')
    with open(path, 'wb') as handle:
        handle.write(bytes(data))
