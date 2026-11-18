"""DVD SUP (.sup): a DVD's subpicture stream as the authoring tools take it,
read and written.

IfoEdit, SubRip, SRT2SUP, BDSup2Sub, Subtitle Edit, MuxMan and Scenarist
pass DVD subtitles to each other as a .sup of bare subpicture units (SPUs),
without the program stream packs a VobSub wraps them in (vobsub). Each is
a 10-byte header and the SPU (DVD-Video's, which vobsub decodes and
encodes):

    "SP"          2 bytes, ASCII
    PTS           8 bytes, little-endian, 90 kHz; only the first 4 are used
    SPU           its first 2 bytes its size, big-endian, as on a disc

So the PTS is at bytes 2-5 and the SPU's size at bytes 10-11, as
BDSup2Sub's SupDvd and Subtitle Edit's SpHeader read them and BDSup2Sub's
SupDvdWriter and Subtitle Edit's DvdSupWriter write them. A Blu-ray's .sup
(pgs) begins "PG" instead, which is how the two are told apart: by their
bytes, never by the extension they share.

An SPU picks its four colours by number from the 16 of the title's palette,
which is in the DVD's IFO, not in the .sup. Beside a .sup BDSup2Sub writes
and reads an .ifo of the same name, a VTS IFO cut down to what a subtitle
needs: "DVDVIDEO-VTS", the video attributes at 0x200 (PAL or NTSC, and
the size), the first subtitle stream's language at 0x254-0x259, and the
palette of the first program chain, 16 entries of (0, Y, Cr, Cb) at 0xA4
into it, found through the sector at 0xCC and the offset at 12 bytes into
that table. The entries are read as FFmpeg's dvdsub decoder reads an IFO
(its ifo_palette option) and libdvdread lays them out: Y, then Cr, then
Cb, BT.601 in video range. BDSup2Sub takes the second as Cb and the third
as Cr, on reading and writing alike, so a palette it wrote comes in with
red and blue chroma swapped; light and dark, which is what an OCR reading
needs, are the same either way.

Reading gives the pictures for an OCR add-on (picture_ocr), on the frame
the .ifo gives, else DVD PAL's (720x576), with the .ifo's palette, else
BDSup2Sub's default one, which is what a tool shows a .sup without one in.

Writing draws the subtitles as VobSub's export does (vobsub.spus: one on
screen at a time, four colours each, out of a palette of sixteen for the
file) and writes the .sup and its .ifo, with that palette, the frame's
standard and the language.
"""

import os
import struct

from subtitld.modules import vobsub

HEADER = 10                     # "SP" and the PTS
MAGIC = b'SP'
# BDSup2Sub's default DVD palette (core/Constants.java), for a .sup with no .ifo.
DEFAULT_PALETTE = [(0x00, 0x00, 0x00), (0xf0, 0xf0, 0xf0), (0xcc, 0xcc, 0xcc), (0x99, 0x99, 0x99),
                   (0x33, 0x33, 0xfa), (0x11, 0x11, 0xbb), (0xfa, 0x33, 0x33), (0xbb, 0x11, 0x11),
                   (0x33, 0xfa, 0x33), (0x11, 0xbb, 0x11), (0xfa, 0xfa, 0x33), (0xbb, 0xbb, 0x11),
                   (0xfa, 0x33, 0xfa), (0xbb, 0x11, 0xbb), (0x33, 0xfa, 0xfa), (0x11, 0xbb, 0xbb)]
DEFAULT_SIZE = (720, 576)
IFO_MAGIC = b'DVDVIDEO-VTS'
# The frame for the video attributes' resolution field, PAL's and NTSC's (BDSup2Sub's IfoParser).
_IFO_SIZES = {True: ((720, 576), (704, 576), (352, 576), (352, 288)),
              False: ((720, 480), (704, 480), (352, 480), (352, 240))}


class DvdSupError(ValueError):
    """Not a DVD SUP Subtitld can read."""


def is_dvdsup(head):
    """Whether `head` (the first bytes of a .sup) is a DVD's subpicture
    stream: "SP", then an SPU whose size and control offset make sense.
    A Blu-ray's PGS begins "PG"."""
    head = bytes(head or b'')
    if len(head) < HEADER + 4 or head[:2] != MAGIC:
        return False
    size, control = struct.unpack('>HH', head[HEADER:HEADER + 4])
    return 4 <= control < size


looks_like = is_dvdsup


def units(data):
    """[(offset, PTS, SPU)] of the "SP" packets in `data`. After a packet
    that does not end where the next begins, the next "SP" is looked for,
    as Subtitle Edit does."""
    found = []
    position = 0
    while position + HEADER + 4 <= len(data):
        if data[position:position + 2] != MAGIC:
            following = data.find(MAGIC, position + 1)
            if following < 0:
                break
            position = following
            continue
        pts = struct.unpack('<I', data[position + 2:position + 6])[0]
        size = struct.unpack('>H', data[position + HEADER:position + HEADER + 2])[0]
        spu = data[position + HEADER:position + HEADER + size]
        if size < 4 or len(spu) < size:
            position += 2
            continue
        found.append((position, pts, spu))
        position += HEADER + size
    return found


def _ifo_path(path):
    stem = os.path.splitext(str(path))[0]
    return next((stem + ext for ext in ('.ifo', '.IFO', '.Ifo') if os.path.isfile(stem + ext)), None)


def parse_ifo(data):
    """{'size', 'palette', 'language'} a VTS IFO gives a .sup, or None for
    anything else: the frame from the video attributes, the palette as
    (r, g, b) from the first program chain's, the first subtitle stream's
    language ('' for none)."""
    if len(data) < 0x260 or data[:12] != IFO_MAGIC:
        return None
    from subtitld.modules import pgs
    attributes = struct.unpack('>H', data[0x200:0x202])[0]
    size = _IFO_SIZES[bool(attributes & 0x3000)][(attributes >> 3) & 3]
    language = ''
    if struct.unpack('>H', data[0x254:0x256])[0] > 0 and data[0x256] == 1:
        code = data[0x258:0x25A].decode('ascii', 'replace')
        language = code.lower() if code.isalpha() else ''
    palette = None
    try:
        table = struct.unpack('>I', data[0xCC:0xD0])[0] * 2048
        chain = table + struct.unpack('>I', data[table + 0x0C:table + 0x10])[0]
        entries = data[chain + 0xA4:chain + 0xA4 + 64]
        if len(entries) == 64:
            palette = [pgs._rgba(entries[4 * n + 1], entries[4 * n + 2], entries[4 * n + 3], 255, False)[:3]
                       for n in range(16)]
    except struct.error:
        palette = None
    return {'size': size, 'palette': palette or list(DEFAULT_PALETTE), 'language': language}


def read(path):
    """(pictures, language) of the DVD SUP at `path`: picture_subtitles
    Pictures, timed by each packet's PTS, on the frame and in the palette
    its .ifo gives (else PAL's frame and BDSup2Sub's palette); the
    language the .ifo names, else ''. Raises DvdSupError for anything else."""
    with open(path, 'rb') as handle:
        data = handle.read()
    if not is_dvdsup(data[:HEADER + 4]):
        raise DvdSupError('Not a DVD subtitle stream: it does not begin with an "SP" packet')
    info = None
    ifo = _ifo_path(path)
    if ifo:
        with open(ifo, 'rb') as handle:
            info = parse_ifo(handle.read())
    info = info or {'size': DEFAULT_SIZE, 'palette': list(DEFAULT_PALETTE), 'language': ''}
    found = vobsub.pictures(units(data), {'size': info['size'], 'palette': info['palette'], 'origin': (0, 0)})
    return found, info['language']


# Writing ------------------------------------------------------------------

def write_ifo(size, palette, language='en'):
    """The .ifo BDSup2Sub writes beside a .sup (its IfoWriter), its palette
    entries (0, Y, Cr, Cb) as FFmpeg reads them: one title set of one
    program chain, with the video attributes of `size`'s standard (NTSC
    for 480 lines, PAL otherwise), one subtitle stream in `language` (as
    Subtitld names it) and the 16 colours of `palette`."""
    from subtitld.modules import pgs
    data = bytearray(0x1800)

    def word(at, value):
        data[at:at + 2] = struct.pack('>H', value)

    def dword(at, value):
        data[at:at + 4] = struct.pack('>I', value)

    attributes = 0x4f01 if int(size[1]) == 480 else 0x5f01
    data[0:12] = IFO_MAGIC
    dword(0x12, 4)                       # the title set's last sector
    dword(0x1C, 4)                       # the IFO's last sector
    dword(0x80, 0x7ff)                   # the end of VTSI_MAT
    dword(0xC8, 1)                       # VTS_PTT_SRPT's sector
    dword(0xCC, 2)                       # VTS_PGCI's sector
    word(0x100, attributes)
    word(0x200, attributes)
    code = vobsub._language_id(language)[:2].ljust(2, 'n')
    word(0x254, 1)                       # one subtitle stream
    data[0x256] = 1                      # with a language
    data[0x258:0x25A] = code.encode('ascii')
    word(0x800, 1)                       # VTS_PTT_SRPT: one title,
    dword(0x804, 0x0C)                   # its parts from byte 12,
    word(0x80C, 1)                       # the first program chain,
    word(0x80E, 1)                       # its first program
    word(0x1000, 1)                      # VTS_PGCI: one program chain,
    dword(0x1004, 0x119)
    dword(0x1008, 0x81000000)            # the title's entry,
    dword(0x100C, 0x10)                  # 16 bytes in
    data[0x1012] = 1                     # one program,
    data[0x1013] = 1                     # one cell
    for number, colour in enumerate((list(palette) + list(DEFAULT_PALETTE))[:16]):
        y, cr, cb, _alpha = pgs._ycbcr(*colour[:3], 255, False)
        data[0x1010 + 0xA4 + 4 * number:0x1010 + 0xA4 + 4 * number + 4] = bytes([0, y, cr, cb])
    return bytes(data)


def save(path, pictures, size, language='en'):
    """Write the DVD SUP of `pictures` (disc_pictures' dicts, in order of
    start) on a frame of `size` to `path`, and its .ifo beside it, in
    `language`. Returns (the .sup, the .ifo)."""
    palette, found = vobsub.spus(pictures, size)
    data = bytearray()
    for start, spu in found:
        pts = max(0, int(round(start * 90000))) & 0xFFFFFFFF
        data += MAGIC + struct.pack('<II', pts, 0) + spu
    with open(path, 'wb') as handle:
        handle.write(bytes(data))
    ifo = os.path.splitext(str(path))[0] + '.ifo'
    with open(ifo, 'wb') as handle:
        handle.write(write_ifo(size, palette, language))
    return path, ifo
