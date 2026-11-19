"""Ogg Kate streams made by libkate itself, and read back by it, through
ctypes: the library the format comes from (libkate.so.1, as Debian and
Ubuntu package it in libkate1), for tests/test_kate.py to check Subtitld's
reader against. Its headers are not needed: the structures here are
include/kate/kate.h's (libkate 0.4), the calls its encoder's and its
high-level decoder's. `available()` is False where the library is not
installed, and the tests that need it are left out.

The packets libkate makes are put in Ogg pages with kate.page, a page
each, as libkate's own oggkate wrapper does.
"""
import ctypes
import ctypes.util

from subtitld.modules import kate

_lib = None


def available():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library('kate') or 'libkate.so.1'
        try:
            _lib = ctypes.CDLL(name)
        except OSError:
            _lib = False
            return False
        _lib.kate_encode_text.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_char_p,
                                          ctypes.c_size_t, ctypes.POINTER(Packet)]
        _lib.kate_encode_finish.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.POINTER(Packet)]
        _lib.kate_info_set_original_canvas_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        _lib.kate_encode_set_palette_index.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _lib.kate_encode_set_bitmap_index.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _lib.kate_encode_get_granule.argtypes = [ctypes.c_void_p]
        _lib.kate_encode_get_granule.restype = ctypes.c_int64
    return bool(_lib)


class Color(ctypes.Structure):
    _fields_ = [('r', ctypes.c_ubyte), ('g', ctypes.c_ubyte), ('b', ctypes.c_ubyte), ('a', ctypes.c_ubyte)]


class Palette(ctypes.Structure):
    _fields_ = [('ncolors', ctypes.c_size_t), ('colors', ctypes.POINTER(Color)), ('meta', ctypes.c_void_p),
                ('pad', ctypes.c_size_t * 1)]


class Bitmap(ctypes.Structure):
    _fields_ = [('width', ctypes.c_size_t), ('height', ctypes.c_size_t), ('bpp', ctypes.c_ubyte), ('type', ctypes.c_int),
                ('pad0', ctypes.c_ubyte * 1), ('internal', ctypes.c_ubyte), ('palette', ctypes.c_int),
                ('pixels', ctypes.POINTER(ctypes.c_ubyte)), ('size', ctypes.c_size_t), ('x_offset', ctypes.c_int),
                ('y_offset', ctypes.c_int), ('meta', ctypes.c_void_p), ('pad1', ctypes.c_size_t * 14)]


class Region(ctypes.Structure):
    _fields_ = [('metric', ctypes.c_int), ('x', ctypes.c_int), ('y', ctypes.c_int), ('w', ctypes.c_int),
                ('h', ctypes.c_int), ('style', ctypes.c_int), ('clip', ctypes.c_uint), ('meta', ctypes.c_void_p),
                ('pad1', ctypes.c_size_t * 5)]


class Packet(ctypes.Structure):
    _fields_ = [('nbytes', ctypes.c_size_t), ('data', ctypes.c_void_p)]


class Event(ctypes.Structure):
    """kate_event, as far as its bitmap."""
    _fields_ = [('start', ctypes.c_int64), ('duration', ctypes.c_int64), ('backlink', ctypes.c_int64),
                ('start_time', ctypes.c_float), ('end_time', ctypes.c_float), ('id', ctypes.c_int32),
                ('text_encoding', ctypes.c_int), ('text_directionality', ctypes.c_int),
                ('language', ctypes.c_char_p), ('text', ctypes.c_void_p), ('len', ctypes.c_size_t),
                ('len0', ctypes.c_size_t), ('nmotions', ctypes.c_size_t), ('motions', ctypes.c_void_p),
                ('region', ctypes.POINTER(Region)), ('style', ctypes.c_void_p), ('secondary_style', ctypes.c_void_p),
                ('font_mapping', ctypes.c_void_p), ('palette', ctypes.POINTER(Palette)),
                ('bitmap', ctypes.POINTER(Bitmap))]


# libkate keeps pointers to what it is given: kept alive here.
_kept = []


def _ok(value):
    if value < 0:
        raise RuntimeError(f'libkate error {value}')
    return value


def palette(colours):
    """A kate_palette of `colours` [(r, g, b, a)]."""
    made = Palette()
    _ok(_lib.kate_palette_init(ctypes.byref(made)))
    array = (Color * len(colours))(*[Color(*colour) for colour in colours])
    made.ncolors, made.colors = len(colours), ctypes.cast(array, ctypes.POINTER(Color))
    _kept.extend((array, made))
    return made


def bitmap(width, height, bpp, pixels=b'', palette_index=-1, png=None):
    """A kate_bitmap: paletted (`pixels`, one byte each, `bpp` bits
    meaning) or the PNG `png`."""
    made = Bitmap()
    _ok(_lib.kate_bitmap_init(ctypes.byref(made)))
    data = bytes(png) if png is not None else bytes(pixels)
    array = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    made.width, made.height = width, height
    made.bpp, made.type = (0, 1) if png is not None else (bpp, 0)
    made.palette = palette_index
    made.pixels = ctypes.cast(array, ctypes.POINTER(ctypes.c_ubyte))
    made.size = len(data) if png is not None else 0
    _kept.extend((array, made))
    return made


def region(metric, x, y, w, h):
    made = Region()
    _ok(_lib.kate_region_init(ctypes.byref(made)))
    made.metric, made.x, made.y, made.w, made.h = metric, x, y, w, h
    _kept.append(made)
    return made


def encode(events, palettes=(), bitmaps=(), canvas=(720, 576), language='en', category='SUB', serial=4321):
    """The bytes of an Ogg file of a Kate stream libkate encodes: the
    headers with `palettes` and `bitmaps`, then `events` [(start, end,
    text, {'region', 'palette', 'bitmap'})], a palette or bitmap given as
    one or as the index of one in the headers."""
    info = ctypes.create_string_buffer(1 << 16)          # kate_info, kate_state, kate_comment: room enough
    state = ctypes.create_string_buffer(1 << 16)
    comment = ctypes.create_string_buffer(1 << 12)
    _kept.extend((info, state, comment))
    _ok(_lib.kate_info_init(info))
    _ok(_lib.kate_info_set_language(info, language.encode()))
    _ok(_lib.kate_info_set_category(info, category.encode()))
    if canvas:
        _ok(_lib.kate_info_set_original_canvas_size(info, canvas[0], canvas[1]))
    for made in palettes:
        _ok(_lib.kate_info_add_palette(info, ctypes.byref(made)))
    for made in bitmaps:
        _ok(_lib.kate_info_add_bitmap(info, ctypes.byref(made)))
    _ok(_lib.kate_comment_init(comment))
    _ok(_lib.kate_encode_init(state, info))
    pages, sequence = [], 0
    while True:
        packet = Packet()
        if _ok(_lib.kate_encode_headers(state, comment, ctypes.byref(packet))) > 0:
            break
        pages.append(kate.page(serial, sequence, 0, [ctypes.string_at(packet.data, packet.nbytes)],
                               0x02 if sequence == 0 else 0))
        sequence += 1
    for start, end, text, extra in events:
        if 'region' in extra:
            _ok(_lib.kate_encode_set_region(state, ctypes.byref(extra['region'])))
        if isinstance(extra.get('palette'), int):
            _ok(_lib.kate_encode_set_palette_index(state, extra['palette']))
        elif 'palette' in extra:
            _ok(_lib.kate_encode_set_palette(state, ctypes.byref(extra['palette'])))
        if isinstance(extra.get('bitmap'), int):
            _ok(_lib.kate_encode_set_bitmap_index(state, extra['bitmap']))
        elif 'bitmap' in extra:
            _ok(_lib.kate_encode_set_bitmap(state, ctypes.byref(extra['bitmap'])))
        raw = text.encode('utf-8')
        packet = Packet()
        _ok(_lib.kate_encode_text(state, start, end, raw, len(raw), ctypes.byref(packet)))
        pages.append(kate.page(serial, sequence, _lib.kate_encode_get_granule(state),
                               [ctypes.string_at(packet.data, packet.nbytes)]))
        sequence += 1
    packet = Packet()
    _ok(_lib.kate_encode_finish(state, -1.0, ctypes.byref(packet)))
    pages.append(kate.page(serial, sequence, _lib.kate_encode_get_granule(state),
                           [ctypes.string_at(packet.data, packet.nbytes)], 0x04))
    return b''.join(pages)


def decode(data):
    """What libkate's decoder makes of the Kate stream in an Ogg file's
    bytes: per event, {'start', 'end', 'region' (metric, x, y, w, h) or
    None, 'colours' [(r, g, b, a)] of its palette or None, 'bitmap':
    (width, height, bpp, pixels or PNG bytes) or None}."""
    state = ctypes.create_string_buffer(1 << 16)
    _kept.append(state)
    _ok(_lib.kate_high_decode_init(state))
    found = []
    for serial, stream in kate.packets(bytes(data)).items():
        for raw, _granule in stream:
            buffer = ctypes.create_string_buffer(bytes(raw), len(raw))
            packet = Packet(len(raw), ctypes.cast(buffer, ctypes.c_void_p))
            event = ctypes.POINTER(Event)()
            ended = _ok(_lib.kate_high_decode_packetin(state, ctypes.byref(packet), ctypes.byref(event)))
            if event:
                item = event.contents
                region = item.region.contents if item.region else None
                colours = None
                if item.palette:
                    shown = item.palette.contents
                    colours = [(shown.colors[n].r, shown.colors[n].g, shown.colors[n].b, shown.colors[n].a)
                               for n in range(shown.ncolors)]
                picture = None
                if item.bitmap:
                    shown = item.bitmap.contents
                    size = shown.size if shown.type == 1 else shown.width * shown.height
                    picture = (shown.width, shown.height, shown.bpp, ctypes.string_at(shown.pixels, size))
                found.append({'start': round(item.start_time, 3), 'end': round(item.end_time, 3),
                              'region': (region.metric, region.x, region.y, region.w, region.h) if region else None,
                              'colours': colours, 'bitmap': picture})
            if ended:
                break
        break
    return found
