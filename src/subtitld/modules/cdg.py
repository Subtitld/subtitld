"""CD+G (.cdg): the karaoke graphics a CD's subcode carries beside the
song, and MP3+G, the same graphics in a .cdg beside an .mp3 of the song
by the same name.

A CD's subcode gives graphics 4 packs a sector, 75 sectors a second: 300
packs a second. A .cdg is those packs, 24 bytes each: a command (9,
"TV graphics", in its low six bits; anything else, or 0, is no graphics),
an instruction, two bytes of parity, 16 of data (six bits of each used)
and four more of parity. The screen is 300x216 pixels in 50 columns and
18 rows of 6x12 tiles, the outer tiles a border (a player shows 294x204);
each pixel one of 16 colours, a palette of 4 bits a channel. The
instructions read and written here are FFmpeg's (libavcodec/cdgraphics.c)
and Jim Bumgardner's "CD+G Revealed":

    1   memory preset: the screen one colour (data 0), done once of the 16
        times it may be sent (data 1, the repeat, 0)
    2   border preset: the border one colour
    6   tile block: a tile at row (data 2) and column (data 3), its 12
        rows of 6 bits (data 4-15, the leftmost pixel the top bit) in the
        colour of data 1 where a bit is set, of data 0 where not
    38  tile block XOR: the same, the colours XORed into the pixels there
    30, 31  the palette's colours 0-7 and 8-15, each two bytes,
        00RRRRGG 00GGBBBB
    20, 24  scroll preset and scroll copy; 28 a transparent colour (read
        and ignored: a page shown at the moment is what is read)

Writing draws each subtitle as a page of the screen, with QPainter (not
the preview's renderer: the wipe needs where each letter of a line is,
which QFontMetrics gives, and 16 colours leave little of a style to keep):
its lines centred, a line two tile rows high, wrapped to the 288 pixels
inside the border, eight at most; in white over dark blue, with three
levels of edge (the drawing quantised to 0-3) so the letters are not
jagged. The palette is laid out so that the wipe is one XOR: colours 1-3
are the levels unsung, 5-7 the same levels sung (yellow), 0 and 4 the
background, so XORing 4 into a pixel of a letter turns it sung. A tile is
drawn with a tile block (its levels' low bit) and, where a level has its
high bit, an XOR block; the wipe then sends, pack after pack, an XOR block
for the tile whose next columns are due: a column is due when the
syllable over it is sung that far, from the karaoke codes (modules.karaoke;
a subtitle without them is wiped evenly over its time). A page is drawn
in the packs before its subtitle starts, as far as the screen is free; a
page that follows straight on clears what of the one before it does not
cover, and shows as fast as the packs draw it (a line of words in about a
fifth of a second); after a gap the screen is cleared. The wipe falls
behind where more columns are due at once than 300 packs a second send.
For MP3+G, file_io writes the video's sound beside the .cdg with FFmpeg. Packs with nothing to do
are zeros, the file is whole sectors, and with `duration` it runs that
long (the song's).

Reading decodes the packs into the screens they show, and gives a
picture_subtitles.Picture for every screen whose letters (every pixel
not the background, so a wipe changes nothing) stay for `MIN_SHOW` or
more, for an OCR add-on to read (modules.picture_ocr).

What a round trip loses: the word timing (the pictures are read as text,
not as a wipe), formatting and places; text that does not fit eight lines.
"""

import heapq

from subtitld.modules import karaoke, markup
from subtitld.modules.picture_subtitles import Picture

WIDTH, HEIGHT = 300, 216
TILE_WIDTH, TILE_HEIGHT = 6, 12
COLUMNS, ROWS = WIDTH // TILE_WIDTH, HEIGHT // TILE_HEIGHT
PACK = 24
PACKS_PER_SECTOR = 4
SECTORS_PER_SECOND = 75
PACKS_PER_SECOND = PACKS_PER_SECTOR * SECTORS_PER_SECOND
COMMAND = 0x09
MEMORY_PRESET, BORDER_PRESET, TILE, SCROLL_PRESET, SCROLL_COPY = 1, 2, 6, 20, 24
TRANSPARENT, PALETTE_LOW, PALETTE_HIGH, TILE_XOR = 28, 30, 31, 38

# The page: inside the border, a line two tile rows high.
LINE_ROWS = 2
FIRST_ROW, LAST_ROW = 1, ROWS - 2
FIRST_COLUMN, LAST_COLUMN = 1, COLUMNS - 2
MAX_LINES = (LAST_ROW - FIRST_ROW + 1) // LINE_ROWS
MARGIN = 6                                  # pixels kept free at each side of a line
FONT_PIXELS = 18
LEVELS = 3                                  # edge levels of a letter, 1-3
SUNG = 4                                    # XORed into a letter's pixel when it is sung
# 4 bits a channel: the background, the letters, the letters sung.
BACKGROUND = (0x0, 0x0, 0x5)
LETTERS = (0xF, 0xF, 0xF)
LETTERS_SUNG = (0xF, 0xD, 0x0)
# A screen that shows for less than this is one being drawn, not read.
MIN_SHOW = 0.25
# A gap this long between subtitles clears the screen.
CLEAR_GAP = 0.1


def _blend(level, colour):
    return tuple(int(round(back + (front - back) * level / LEVELS)) for back, front in zip(BACKGROUND, colour))


def palette():
    """The 16 colours written, (r, g, b) of 4 bits each."""
    colours = [BACKGROUND] + [_blend(level, LETTERS) for level in range(1, LEVELS + 1)]
    colours += [BACKGROUND] + [_blend(level, LETTERS_SUNG) for level in range(1, LEVELS + 1)]
    return colours + [BACKGROUND] * (16 - len(colours))


def _pack(instruction, data):
    data = bytes(value & 0x3F for value in data) + bytes(16 - len(data))
    return bytes([COMMAND, instruction, 0, 0]) + data + bytes(4)


def _palette_packs(colours):
    packs = []
    for instruction, part in ((PALETTE_LOW, colours[:8]), (PALETTE_HIGH, colours[8:16])):
        data = []
        for r, g, b in part:
            data += [(r << 2) | (g >> 2), ((g & 3) << 4) | b]
        packs.append(_pack(instruction, data))
    return packs


def _tile(instruction, row, column, colour0, colour1, bits):
    return _pack(instruction, [colour0, colour1, row, column] + list(bits))


# Drawing.
def _characters(segment):
    """[(character, start, end)] for a subtitle's plain text, "\\n" between
    its lines: each syllable's letters sung in turn over its time, or the
    whole text so over the subtitle's."""
    start = float(segment.get('start', 0))
    end = max(start, float(segment.get('end', start)))
    text = str(segment.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
    pieces = karaoke.timed(text, start)
    if not pieces:
        pieces = [(start, end, markup.plain(markup.split_lead(text)[1]))]
    out = []
    for begin, finish, piece in pieces:
        letters = [char for char in piece if char != '\n']
        step = (finish - begin) / len(letters) if letters else 0
        at = begin
        for char in piece:
            if char == '\n':
                out.append(('\n', at, at))
                continue
            out.append((char, at, at + step))
            at += step
    return out


def _wrap(characters, metrics, width):
    """Lines of [(character, start, end)], no wider than `width` pixels."""
    lines, line = [], []
    for item in characters + [('\n', 0, 0)]:
        if item[0] == '\n':
            while line and line[-1][0] == ' ':
                line.pop()
            if line:
                lines.append(line)
            line = []
            continue
        if not line and item[0] == ' ':
            continue
        line.append(item)
        if metrics.horizontalAdvance(''.join(char for char, _s, _e in line)) > width and len(line) > 1:
            # Back to the last space, else the letter that does not fit.
            text = ''.join(char for char, _s, _e in line)
            cut = text.rfind(' ')
            if cut > 0:
                rest = line[cut + 1:]
                line = line[:cut]
            else:
                rest = line[-1:]
                line = line[:-1]
            lines.append(line)
            line = rest
    return lines


class _Page:
    """A subtitle drawn: levels[y][x] 0-3 on the screen, the wipe's times
    by tile, the tiles with letters."""

    def __init__(self, segment):
        from PySide6.QtCore import QPointF, QRect
        from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

        font = QFont()
        font.setPixelSize(FONT_PIXELS)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        inner = (LAST_COLUMN - FIRST_COLUMN + 1) * TILE_WIDTH - 2 * MARGIN
        lines = _wrap(_characters(segment), metrics, inner)[:MAX_LINES]
        image = QImage(WIDTH, HEIGHT, QImage.Format_Grayscale8)
        image.fill(0)
        top_row = FIRST_ROW + (MAX_LINES - len(lines)) * LINE_ROWS // 2
        line_height = LINE_ROWS * TILE_HEIGHT
        self.wipes = []                     # [(top row, x0, [advance], [(start, end)])] a line
        painter = QPainter(image)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        for number, line in enumerate(lines):
            text = ''.join(char for char, _s, _e in line)
            advances = [metrics.horizontalAdvance(text[:index]) for index in range(len(text) + 1)]
            x0 = FIRST_COLUMN * TILE_WIDTH + (inner + 2 * MARGIN - advances[-1]) / 2
            row = top_row + number * LINE_ROWS
            box = QRect(FIRST_COLUMN * TILE_WIDTH, row * TILE_HEIGHT, inner + 2 * MARGIN, line_height)
            painter.setClipRect(box)
            baseline = row * TILE_HEIGHT + (line_height - metrics.height()) / 2 + metrics.ascent()
            painter.drawText(QPointF(x0, baseline), text)
            self.wipes.append((row, x0, advances, [(start, end) for _char, start, end in line]))
        painter.end()
        bits = image.constBits()
        stride = image.bytesPerLine()
        raw = bytes(bits)[:stride * HEIGHT]
        step = 256 / (LEVELS + 1)
        table = bytes(min(LEVELS, int(value / step + 0.5) if value else 0) for value in range(256))
        self.levels = [raw[y * stride:y * stride + WIDTH].translate(table) for y in range(HEIGHT)]
        self.tiles = {}
        for row in range(FIRST_ROW, LAST_ROW + 1):
            for column in range(FIRST_COLUMN, LAST_COLUMN + 1):
                block = [self.levels[row * TILE_HEIGHT + y][column * TILE_WIDTH:(column + 1) * TILE_WIDTH]
                         for y in range(TILE_HEIGHT)]
                if any(any(part) for part in block):
                    self.tiles[(row, column)] = block

    def draw_packs(self):
        """{tile: [packs]} drawing each tile with letters."""
        out = {}
        for (row, column), block in self.tiles.items():
            low = [sum(1 << (5 - x) for x in range(TILE_WIDTH) if part[x] & 1) for part in block]
            high = [sum(1 << (5 - x) for x in range(TILE_WIDTH) if part[x] & 2) for part in block]
            packs = [_tile(TILE, row, column, 0, 1, low)]
            if any(high):
                packs.append(_tile(TILE_XOR, row, column, 0, 2, high))
            out[(row, column)] = packs
        return out

    def due(self):
        """{tile: [(seconds, column in the tile)]} when each column of a
        tile with letters in it is to be sung, by time."""
        out = {}
        for row, x0, advances, times in self.wipes:
            for line_row in range(row, row + LINE_ROWS):
                for column in range(FIRST_COLUMN, LAST_COLUMN + 1):
                    block = self.tiles.get((line_row, column))
                    if block is None:
                        continue
                    found = []
                    for x in range(TILE_WIDTH):
                        if not any(part[x] for part in block):
                            continue
                        found.append((_time_at(column * TILE_WIDTH + x + 0.5 - x0, advances, times), x))
                    if found:
                        out[(line_row, column)] = sorted(found)
        return out


def _time_at(x, advances, times):
    """When the wipe reaches `x` pixels into a line of letters that start
    at `advances` and are sung at `times`."""
    if not times:
        return 0.0
    if x <= 0:
        return times[0][0]
    for index, (start, end) in enumerate(times):
        left, right = advances[index], advances[index + 1]
        if x < right or index == len(times) - 1:
            share = min(1.0, max(0.0, (x - left) / (right - left))) if right > left else 1.0
            return start + (end - start) * share
    return times[-1][1]


def write(segments, duration=0.0):
    """CD+G bytes for `segments`, whole sectors, at least `duration`
    seconds of them."""
    ordered = sorted((segment for segment in segments if markup.plain(str(segment.get('text') or '')).strip()),
                     key=lambda segment: (float(segment.get('start', 0)), float(segment.get('end', 0))))
    slots = {}
    colours = palette()
    header = [_pack(MEMORY_PRESET, [0, 0]), _pack(BORDER_PRESET, [0])] + _palette_packs(colours)
    for number, packet in enumerate(header):
        slots[number] = packet
    cursor = len(header)
    shown = set()                           # tiles with letters on screen
    for index, segment in enumerate(ordered):
        start = float(segment.get('start', 0))
        end = max(start, float(segment.get('end', start)))
        following = float(ordered[index + 1].get('start', 0)) if index + 1 < len(ordered) else None
        if following is not None:
            end = min(end, max(start, following))
        page = _Page(segment)
        drawing = page.draw_packs()
        packs = [_tile(TILE, row, column, 0, 0, [0] * TILE_HEIGHT) for row, column in sorted(shown - set(drawing))]
        for tile in sorted(drawing):
            packs += drawing[tile]
        start_slot, end_slot = int(round(start * PACKS_PER_SECOND)), int(round(end * PACKS_PER_SECOND))
        # Drawn just before it starts, if the screen is free that long.
        cursor = max(cursor, start_slot - len(packs))
        for packet in packs:
            slots[cursor] = packet
            cursor += 1
        shown = set(drawing)
        # The wipe: a pack a slot, for the tile whose next columns are due.
        due = page.due()
        heap = [(columns[0][0], tile) for tile, columns in due.items()]
        heapq.heapify(heap)
        done = {tile: 0 for tile in due}
        while heap and cursor < end_slot:
            now = cursor / PACKS_PER_SECOND
            if heap[0][0] > now:
                cursor = max(cursor + 1, int(heap[0][0] * PACKS_PER_SECOND))
                continue
            _when, tile = heapq.heappop(heap)
            columns = due[tile]
            mask = 0
            while done[tile] < len(columns) and columns[done[tile]][0] <= now:
                mask |= 1 << (5 - columns[done[tile]][1])
                done[tile] += 1
            block = page.tiles[tile]
            bits = [sum(1 << (5 - x) for x in range(TILE_WIDTH) if part[x] and mask & (1 << (5 - x))) for part in block]
            slots[cursor] = _tile(TILE_XOR, tile[0], tile[1], 0, SUNG, bits)
            cursor += 1
            if done[tile] < len(columns):
                heapq.heappush(heap, (columns[done[tile]][0], tile))
        cursor = max(cursor, end_slot)
        if following is None or following - end >= CLEAR_GAP:
            slots[cursor] = _pack(MEMORY_PRESET, [0, 0])
            cursor += 1
            shown = set()
    total = max(cursor, int(round(float(duration or 0) * PACKS_PER_SECOND)))
    total = -(-total // PACKS_PER_SECTOR) * PACKS_PER_SECTOR
    empty = bytes(PACK)
    return b''.join(slots.get(number, empty) for number in range(total))


def save(path, segments, duration=0.0):
    """Write `segments` to `path` as CD+G."""
    data = write(segments, duration)
    with open(path, 'wb') as cdg_file:
        cdg_file.write(data)


# Reading.
class Screen:
    """The screen the packs draw: `pixels` (300x216 colour indices, row
    after row) and `colours` [(r, g, b)] of 8 bits."""

    def __init__(self):
        self.pixels = bytearray(WIDTH * HEIGHT)
        self.colours = [(0, 0, 0)] * 16
        self.background = 0

    def apply(self, packet):
        """Do one pack; whether it changed the screen or the palette."""
        if len(packet) < PACK or packet[0] & 0x3F != COMMAND:
            return False
        instruction, data = packet[1] & 0x3F, packet[4:20]
        if instruction == MEMORY_PRESET:
            if data[1] & 0x0F:
                return False
            self.background = data[0] & 0x0F
            self.pixels[:] = bytes([self.background]) * (WIDTH * HEIGHT)
            return True
        if instruction == BORDER_PRESET:
            colour = data[0] & 0x0F
            for y in range(HEIGHT):
                row = y * WIDTH
                if y < TILE_HEIGHT or y >= HEIGHT - TILE_HEIGHT:
                    self.pixels[row:row + WIDTH] = bytes([colour]) * WIDTH
                else:
                    self.pixels[row:row + TILE_WIDTH] = bytes([colour]) * TILE_WIDTH
                    self.pixels[row + WIDTH - TILE_WIDTH:row + WIDTH] = bytes([colour]) * TILE_WIDTH
            return True
        if instruction in (TILE, TILE_XOR):
            row, column = (data[2] & 0x1F) * TILE_HEIGHT, (data[3] & 0x3F) * TILE_WIDTH
            if row > HEIGHT - TILE_HEIGHT or column > WIDTH - TILE_WIDTH:
                return False
            colour0, colour1 = data[0] & 0x0F, data[1] & 0x0F
            for y in range(TILE_HEIGHT):
                bits = data[4 + y]
                at = (row + y) * WIDTH + column
                for x in range(TILE_WIDTH):
                    colour = colour1 if bits >> (5 - x) & 1 else colour0
                    self.pixels[at + x] = self.pixels[at + x] ^ colour if instruction == TILE_XOR else colour
            return True
        if instruction in (PALETTE_LOW, PALETTE_HIGH):
            first = 0 if instruction == PALETTE_LOW else 8
            for index in range(8):
                value = ((data[2 * index] & 0x3F) << 6) | (data[2 * index + 1] & 0x3F)
                self.colours[first + index] = (((value >> 8) & 15) * 17, ((value >> 4) & 15) * 17, (value & 15) * 17)
            return True
        if instruction in (SCROLL_PRESET, SCROLL_COPY):
            return self._scroll(data, instruction == SCROLL_COPY)
        return False

    def _scroll(self, data, copy):
        """A whole tile's scroll; the offsets of a smooth one are left out."""
        colour = data[0] & 0x0F
        across, down = {2: -TILE_WIDTH, 1: TILE_WIDTH}.get((data[1] & 0x30) >> 4, 0), \
            {2: -TILE_HEIGHT, 1: TILE_HEIGHT}.get((data[2] & 0x30) >> 4, 0)
        if not across and not down:
            return False
        old = bytes(self.pixels)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                source_x, source_y = x - across, y - down
                if 0 <= source_x < WIDTH and 0 <= source_y < HEIGHT:
                    self.pixels[y * WIDTH + x] = old[source_y * WIDTH + source_x]
                elif copy:
                    self.pixels[y * WIDTH + x] = old[(source_y % HEIGHT) * WIDTH + source_x % WIDTH]
                else:
                    self.pixels[y * WIDTH + x] = colour
        return True


def is_cdg(data):
    """Whether `data` (the first packs of a file) is CD+G, as FFmpeg's
    probe has it: every pack's command 9 or 0, and some 9."""
    data = bytes(data)
    count = len(data) // PACK
    commands = [data[number * PACK] & 0x3F for number in range(count)]
    return bool(count) and all(command in (0, COMMAND) for command in commands) and COMMAND in commands


def parse(data, min_show=MIN_SHOW):
    """[Picture] for the screens a CD+G's bytes show: each the display
    (inside the border) as it was when its letters last changed, from
    then until they change again, when it shows for `min_show` or more."""
    screen = Screen()
    found = []
    last_mask, since, last_pixels = None, 0.0, None
    screen_colours, background = list(screen.colours), 0
    count = len(data) // PACK
    # A screen is looked at a tenth of a second at most, once it has changed.
    every = PACKS_PER_SECOND // 10
    changed = False
    for number in range(count + 1):
        if number < count:
            changed = screen.apply(data[number * PACK:(number + 1) * PACK]) or changed
        if not (number == count or (changed and number % every == 0)):
            continue
        changed = False
        table = bytes(0 if index == screen.background else 1 for index in range(256))
        mask = bytes(screen.pixels).translate(table)
        now = number / PACKS_PER_SECOND
        if mask == last_mask and number < count:
            continue
        if last_mask is not None and last_mask.strip(b'\x00') and now - since >= min_show:
            found.append(_picture(last_pixels, since, now, screen_colours, background))
        last_mask, since, last_pixels = mask, now, bytes(screen.pixels)
        screen_colours, background = list(screen.colours), screen.background
    return found


def _picture(pixels, start, end, colours, background):
    """The display area of `pixels` as a Picture, the background clear."""
    left, top = TILE_WIDTH, TILE_HEIGHT
    width, height = WIDTH - 2 * TILE_WIDTH, HEIGHT - 2 * TILE_HEIGHT
    indices = b''.join(pixels[(top + y) * WIDTH + left:(top + y) * WIDTH + left + width] for y in range(height))
    rgba = [(r, g, b, 0 if index == background else 255) for index, (r, g, b) in enumerate(colours)]
    return Picture(round(start, 3), round(end, 3), left, top, width, height, indices, rgba, False, WIDTH, HEIGHT, '')


def read(path):
    """[Picture] for the CD+G at `path`."""
    with open(path, 'rb') as cdg_file:
        return parse(cdg_file.read())
