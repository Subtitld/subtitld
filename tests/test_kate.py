"""Ogg Kate: Ogg pages and their checksums, the bit packing, reading a
stream built packet by packet as libkate lays it out (regions, styles, a
motion, simple markup), writing, round trips, telling a Kate .ogg from
another, and the file and the export dialog. Where the system has libkate,
what Subtitld writes is decoded by it, and what it encodes is read here.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import ctypes, ctypes.util, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, kate, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('ogg and bits')
check("the checksum of RFC 3533's polynomial, from 0", (kate.ogg_crc(b''), kate.ogg_crc(b'OggS')), (0, 0x5FB0A94F))
one = kate.page(7, 0, 5, [b'a' * 300, b'b'])
check('a page: lacing of 255s, its checksum over the page with the field zeroed',
      (one[26], list(one[27:30]), kate.ogg_crc(one[:22] + b'\0\0\0\0' + one[26:]) == int.from_bytes(one[22:26], 'little')),
      (3, [255, 45, 1], True))
import struct
open_page = struct.pack('<4sBBqIIIB', b'OggS', 0, 0, -1, 7, 0, 0, 1) + b'\xff' + b'x' * 255
check('packets back from pages, one continued on the next',
      kate.packets(open_page + kate.page(7, 1, 9, [b'yz'], 0x01)), {7: [(b'x' * 255 + b'yz', 9)]})
writer = kate._Writer()
for value in (0, 7, 17, -3, 1000, 842389532):
    writer.write32v(value)
writer.floats([0.5, -1.0, 0.0, 123.25])
reader = kate._Reader(writer.bytes())
check("variable length numbers (the format's own examples) and 16.16 floats, least significant bit first",
      ([reader.read32v() for _ in range(6)], reader.floats(4)), ([0, 7, 17, -3, 1000, 842389532], [0.5, -1.0, 0.0, 123.25]))
writer = kate._Writer()
writer.write(0b101, 3)
writer.write(1, 1)
check('bits fill a byte from its least significant end', writer.bytes(), bytes([0b1101]))


def header(kind, body):
    """A header packet: its type, the magic, the reserved byte, then `body`."""
    out = kate._Writer()
    out.buffer(bytes((kind,)) + b'kate\0\0\0\0')
    body(out)
    return out.bytes()


def regions(out):
    out.write32v(1)
    out.write(kate.PIXEL, 8)                         # a region at the top of a 640x480 canvas
    for value in (40, 10, 560, 60, 0):
        out.write32v(value)
    clip = kate._Writer(); clip.write(0, 1); out.warp(clip)
    meta = kate._Writer(); meta.write(0, 1); out.warp(meta)
    out.warp()
    out.warp()


def styles(out):
    out.write32v(1)
    kate._style_bits(out, 7)                         # left, top
    out.warp()


def text_event(start, duration, text, region=None, motion=None, markup=kate.MARKUP_SIMPLE):
    out = kate._Writer()
    out.write(0, 8)
    for value in (start, duration, 0):
        out.write(value, 64)
    out.write(len(text), 32)
    out.buffer(text)
    out.write(1, 1); out.write32v(42)                # an id
    if motion:
        out.write(1, 1); out.write32v(1); out.write(0, 1)
        out.write32v(1)                              # one curve, of its own
        out.write(1, 8)                              # static
        out.write32v(1); out.warp()
        out.floats([0.1, 0.7])                       # x, y, merged in one stream
        out.floats([1.0])
        out.write(1, 8); out.write(1, 8); out.write(kate.REGION_POSITION, 8); out.write(0, 1)
        meta = kate._Writer(); meta.write(0, 1); out.warp(meta)
        out.warp()
    else:
        out.write(0, 1)
    if region is not None:
        out.write(1, 1)
        for flag in (0, 0, 0, 1):
            out.write(flag, 1)
        out.write32v(region)
        for _ in range(6):
            out.write(0, 1)
    else:
        out.write(0, 1)
    extra = kate._Writer(); extra.write(1, 1)
    for _ in range(4):
        extra.write(0, 1)
    extra.write(1, 1); extra.write(markup, 8)
    out.warp(extra)
    none = kate._Writer(); none.write32v(0); out.warp(none)
    meta = kate._Writer(); meta.write(0, 1); out.warp(meta)
    out.warp()
    return out.bytes()


def stream(serial=3, language='en'):
    id_header = kate._id_header(language, 'SUB', 640, 480)
    others = [kate._comment_header('test'), header(0x82, regions), header(0x83, styles)] + \
        [kate._list_header(kind) for kind in (0x84, 0x85, 0x86, 0x87, 0x88)]
    return (kate.page(serial, 0, 0, [id_header], 0x02) + kate.page(serial, 1, 0, others)
            + kate.page(serial, 2, 1000 << 32, [text_event(1000, 1500, b'<i>Hello</i> &amp; <span foreground="#00ff00">'
                                                           b'green</span> <big>big</big>', region=0)])
            + kate.page(serial, 3, 3000 << 32, [text_event(3000, 1000, b'a < b', markup=kate.MARKUP_NONE)])
            + kate.page(serial, 4, 5000 << 32, [text_event(5000, 500, 'Moved, lá'.encode(), motion=True)])
            + kate.page(serial, 5, 6000 << 32, [b'\x7f'], 0x04))


print('reading')
segments, info = kate.parse(stream())
check('events from their start for their duration; simple markup, the region and its style, a motion; plain text kept',
      segments, [{'start': 1.0, 'end': 2.5, 'text': '{\\an7}<i>Hello</i> & <font color="#00ff00">green</font> big'},
                 {'start': 3.0, 'end': 4.0, 'text': 'a < b'},
                 {'start': 5.0, 'end': 5.5, 'text': 'Moved, lá'}])
check("the ID header's language and category", info, {'language': 'en', 'category': 'SUB'})
two = stream(3, 'en') + stream(9, 'pt_BR')
check('two streams listed; the second read by its serial',
      ([s['language'] for s in kate.streams(two)], kate.parse(two, 9)[1]['language']), (['en', 'pt_BR'], 'pt_BR'))
check('a Kate .ogg told from a Vorbis one', (kate.is_kate(stream()), kate.is_kate(kate.page(1, 0, 0, [b'\x01vorbis'], 2))),
      (True, False))
try:
    kate.parse(kate.page(1, 0, 0, [b'\x01vorbis'], 2))
    check('an Ogg file without Kate: refused', None, 'ValueError')
except ValueError as error:
    check('an Ogg file without Kate: refused', 'no Kate stream' in str(error), True)

print('writing')
written = kate.write([{'start': 0.5, 'end': 2, 'text': '<b>Bold</b> <a>'}, {'start': 1, 'end': 3, 'text': '{\\an8}Top'}],
                     'pt-br', width=1920, height=1080, serial=5)
found = kate.packets(written)[5]
check('nine headers, one text packet a page, the end packet',
      ([p[0] for p, _g in found], [p[0] for p, _g in found][:1] == [0x80]), ([0x80 + n for n in range(9)] + [0, 0, 0x7f], True))
granules = [g for _p, g in found]
check('granules: the earliest event still on, shifted, plus the offset; the end at the last end',
      granules[-3:], [500 << 32, (500 << 32) | 500, 3000 << 32])
check("the language as kateenc writes it, the canvas the video's", (kate._headers([p for p, _g in found])['language'],
                                                                     kate._headers([p for p, _g in found])['canvas']),
      ('pt_BR', (1920, 1080)))
check('the text in simple markup, escaped', found[9][0][29:29 + 21], b'<b>Bold</b> &lt;a&gt;')

print('round trips')
trip = [
    {'start': 0.5, 'end': 2.0, 'text': '<b>Bold</b>, <i>italic</i>, <u>under</u>, <s>struck</s>\nand <font color="#ffff00">yellow</font>'},
    {'start': 1.0, 'end': 3.25, 'text': '{\\an7}Top left & <less> than'},
    {'start': 4.0, 'end': 6.0, 'text': '{\\an6}Right 日本語'},
    {'start': 6.0, 'end': 8.0, 'text': '{\\an5}Middle'},
    {'start': 8.0, 'end': 9.0, 'text': '{\\an3}Bottom right 😀'},
]
check('read back as written', kate.parse(kate.write(trip))[0], trip)
check('what it loses: a reading', kate.parse(kate.write([{'start': 0, 'end': 1, 'text': '<ruby>漢字<rt>かんじ</rt></ruby>'}]))[0][0]['text'],
      '漢字')

print('libkate')
library = ctypes.util.find_library('kate')
if not library:
    print('  [SKIP] libkate is not installed')
else:
    lib = ctypes.CDLL(library)

    class Packet(ctypes.Structure):
        _fields_ = [('nbytes', ctypes.c_size_t), ('data', ctypes.c_void_p)]

    def as_packet(data):
        buffer = ctypes.create_string_buffer(data, len(data))
        return Packet(len(data), ctypes.cast(buffer, ctypes.c_void_p)), buffer

    # libkate decodes what Subtitld writes.
    info_buffer, comment_buffer, state_buffer = (ctypes.create_string_buffer(8192) for _ in range(3))
    lib.kate_info_init(info_buffer)
    lib.kate_comment_init(comment_buffer)
    decoded, results = kate.packets(kate.write(trip, 'en', serial=1))[1], []
    for packet, _granule in decoded[:9]:
        kept = as_packet(packet)
        results.append(lib.kate_decode_headerin(info_buffer, comment_buffer, ctypes.byref(kept[0])))
    check('libkate takes the nine headers, the last saying they are all there', results, [0] * 8 + [1])
    lib.kate_decode_init(state_buffer, info_buffer)
    events = []
    for packet, _granule in decoded[9:]:
        kept = as_packet(packet)
        check_in = lib.kate_decode_packetin(state_buffer, ctypes.byref(kept[0]))
        event = ctypes.c_void_p()
        if check_in >= 0 and lib.kate_decode_eventout(state_buffer, ctypes.byref(event)) == 0 and event.value:
            # kate_event: three int64, two floats, id, two enums, then the
            # language and text pointers.
            start, end = (ctypes.c_float.from_address(event.value + 24 + 4 * n).value for n in range(2))
            text = ctypes.c_char_p.from_address(event.value + 56).value
            region = ctypes.c_void_p.from_address(event.value + 96).value
            region_y = ctypes.c_int.from_address(region + 8).value if region else None
            events.append((round(start, 3), round(end, 3), text.decode('utf-8'), region_y))
    check("libkate decodes each event: times, the text in simple markup, the region of its place",
          events, [(0.5, 2.0, '<b>Bold</b>, <i>italic</i>, <u>under</u>, <s>struck</s>\nand '
                    '<span foreground="#ffff00">yellow</span>', 75),
                   (1.0, 3.25, 'Top left &amp; &lt;less&gt; than', 5), (4.0, 6.0, 'Right 日本語', 40),
                   (6.0, 8.0, 'Middle', 40), (8.0, 9.0, 'Bottom right 😀', 75)])

    # What libkate encodes, read here.
    class Region(ctypes.Structure):
        _fields_ = [('metric', ctypes.c_int), ('x', ctypes.c_int), ('y', ctypes.c_int), ('w', ctypes.c_int),
                    ('h', ctypes.c_int), ('style', ctypes.c_int), ('clip', ctypes.c_uint), ('meta', ctypes.c_void_p),
                    ('pad', ctypes.c_size_t * 5)]

    info_buffer, comment_buffer, state_buffer = (ctypes.create_string_buffer(8192) for _ in range(3))
    lib.kate_info_init(info_buffer)
    lib.kate_info_set_language(info_buffer, b'de')
    lib.kate_info_set_category(info_buffer, b'SUB')
    lib.kate_info_set_granule_encoding.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.kate_info_set_granule_encoding(info_buffer, 0.001, 1000.0, 1000.0)
    lib.kate_comment_init(comment_buffer)
    lib.kate_encode_init(state_buffer, info_buffer)
    made = []
    while True:
        packet = Packet()
        status = lib.kate_encode_headers(state_buffer, comment_buffer, ctypes.byref(packet))
        if status != 0:
            break
        made.append(ctypes.string_at(packet.data, packet.nbytes))
    lib.kate_encode_text.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_char_p, ctypes.c_size_t,
                                     ctypes.c_void_p]
    for start, end, text, region in ((1.0, 2.0, '<i>Hallo</i> Welt', (1, 10, 5, 80, 10)),
                                     (2.5, 4.0, 'unten ü', None)):
        if region:
            kept_region = Region(*region, -1)        # libkate keeps the pointer until the text is encoded
            lib.kate_encode_set_region(state_buffer, ctypes.byref(kept_region))
        lib.kate_encode_set_markup_type(state_buffer, kate.MARKUP_SIMPLE)
        data = text.encode('utf-8')
        packet = Packet()
        lib.kate_encode_text(state_buffer, start, end, data, len(data), ctypes.byref(packet))
        made.append(ctypes.string_at(packet.data, packet.nbytes))
    packet = Packet()
    lib.kate_encode_finish.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p]
    lib.kate_encode_finish(state_buffer, -1.0, ctypes.byref(packet))
    made.append(ctypes.string_at(packet.data, packet.nbytes))
    ogg = kate.page(11, 0, 0, made[:1], 0x02) + kate.page(11, 1, 0, made[1:9]) + b''.join(
        kate.page(11, 2 + n, 0, [p], 0x04 if n == len(made) - 10 else 0) for n, p in enumerate(made[9:]))
    check("libkate's own stream read: times, simple markup, its region (top, by percent), its language",
          kate.parse(ogg), ([{'start': 1.0, 'end': 2.0, 'text': '{\\an8}<i>Hallo</i> Welt'},
                             {'start': 2.5, 'end': 4.0, 'text': 'unten ü'}], {'language': 'de', 'category': 'SUB'}))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.ogg').write_bytes(stream(3, 'pt_BR'))
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.ogg'))
check('opens as KATE, the stream\'s language the project\'s', (fmt, len(opened), session.SUBTITLE.get('language')),
      ('KATE', 3, 'pt-br'))
(folder / 'vorbis.ogg').write_bytes(kate.page(1, 0, 0, [b'\x01vorbis' + b'\0' * 23], 2))
check('an .ogg with no Kate in it is not opened as Kate', file_io._is_kate(str(folder / 'vorbis.ogg')), False)
session.SUBTITLE['segments'] = trip
session.VIDEO = {'width': 1280, 'height': 720, 'filepath': '/videos/clip.ogv'}
session.FORMAT = {'options': {'kate_category': 'CC'}}
file_io.save_file(str(folder / 'out.ogg'), 'KATE')
back, header_info = kate.read(str(folder / 'out.ogg'))
check("saved with the category it came with and the video's canvas, and read back",
      (back == trip, header_info['category'], kate._headers([p for p, _g in kate.packets(
          (folder / 'out.ogg').read_bytes()).popitem()[1]])['canvas']), (True, 'CC', (1280, 720)))
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['KATE']['extensions'], ['ogg'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('KATE')
panel._refresh_options()
check('KATE in the export menu, with the text options, no frame rate',
      ('KATE' in SUBTITLE_FORMATS, panel.format_combo.currentText(), panel.text_options.isHidden(), panel.sub_options.isHidden()),
      (True, 'KATE', False, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
