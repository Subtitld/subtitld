"""Smooth Streaming text tracks (.ismt): reading a track laid out as FFmpeg's
ismv muxer writes one (a 'dfxp' description of nothing, PIFF fragments
timed by their tfxd and no tfdt, TTML documents timed from their sample's
start), one whose documents count from the track's start, writing one and
reading it back, telling it from a CMAF 'stpp' track, and the file and the
export dialog.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, struct, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, isobmff_text, session
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# Boxes, written here by hand rather than with the module's own helpers.
def box(kind, *parts):
    payload = b''.join(parts)
    return struct.pack('>I4s', 8 + len(payload), kind) + payload


def full(kind, flags, *parts):
    return box(kind, struct.pack('>I', flags), *parts)


TFXD = bytes.fromhex('6d1d9b0542d544e680e2141daff757b2')
SCALE = 10000000


def document(paragraphs):
    body = ''.join(f'<p begin="{begin}" end="{end}">{text}</p>' for begin, end, text in paragraphs)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:tts="http://www.w3.org/ns/ttml#styling" xml:lang="de"><head/><body><div>'
            f'{body}</div></body></tt>').encode()


def ismt(samples):
    """A track as FFmpeg's ismv muxer lays it out: ftyp isml/piff, a 'text'
    handler, a 'dfxp' entry of six reserved bytes and a data reference, and
    a moof (tfhd without a base offset, trun, tfxd) and mdat a sample."""
    ftyp = box(b'ftyp', b'isml', struct.pack('>I', 1), b'isml', b'piff')
    mvhd = full(b'mvhd', 0, struct.pack('>IIII', 0, 0, SCALE, 0), b'\0' * 80)
    tkhd = full(b'tkhd', 3, struct.pack('>IIIII', 0, 0, 7, 0, 0), b'\0' * 60)
    language = ((ord('d') - 0x60) << 10) | ((ord('e') - 0x60) << 5) | (ord('u') - 0x60)
    mdhd = full(b'mdhd', 0, struct.pack('>IIIIHH', 0, 0, SCALE, 0, language, 0))
    hdlr = full(b'hdlr', 0, b'\0' * 4, b'text', b'\0' * 12, b'SubtitleHandler\0')
    stsd = full(b'stsd', 0, struct.pack('>I', 1), box(b'dfxp', b'\0' * 6, struct.pack('>H', 1)))
    stbl = box(b'stbl', stsd, full(b'stts', 0, b'\0' * 4), full(b'stsc', 0, b'\0' * 4),
               full(b'stsz', 0, b'\0' * 8), full(b'stco', 0, b'\0' * 4))
    moov = box(b'moov', mvhd, box(b'trak', tkhd, box(b'mdia', mdhd, hdlr, box(b'minf', full(b'nmhd', 0), stbl))),
               box(b'mvex', full(b'trex', 0, struct.pack('>IIIII', 7, 1, 0, 0, 0))))
    out = ftyp + moov
    for number, (start, duration, data) in enumerate(samples, 1):
        def moof(offset):
            trun = full(b'trun', 0x301, struct.pack('>IiII', 1, offset, duration, len(data)))
            tfxd = struct.pack('>I4s', 44, b'uuid') + TFXD + struct.pack('>IQQ', 1 << 24, start, duration)
            return box(b'moof', full(b'mfhd', 0, struct.pack('>I', number)),
                       box(b'traf', full(b'tfhd', 0, struct.pack('>I', 7)), trun, tfxd))
        head = moof(len(moof(0)) + 8)
        out += head + box(b'mdat', data)
    return out


# Two fragments of 5 s, at 0 and 5 s, their documents timed from their
# own start: the second's "00:00:01" is 6 s into the programme.
RELATIVE = ismt([(0, 5 * SCALE, document([('00:00:01.000', '00:00:03.000', 'Erste<br/>Zeile')])),
                 (5 * SCALE, 5 * SCALE, document([('00:00:01.000', '00:00:02.500', 'Zweite'),
                                                  ('00:00:04.000', '00:00:05.000', 'Weiter')])),
                 (10 * SCALE, 5 * SCALE, document([('00:00:00.000', '00:00:01.000', 'Weiter')]))])
folder = Path(tempfile.mkdtemp())

print('reading')
(folder / 'relative.ismt').write_bytes(RELATIVE)
segments, settings = isobmff_text.read(str(folder / 'relative.ismt'))
check("FFmpeg's layout: times from each tfxd, documents from their sample's start, a cue going on joined",
      [(s['start'], s['end'], s['text']) for s in segments],
      [(1.0, 3.0, 'Erste\nZeile'), (6.0, 7.5, 'Zweite'), (9.0, 11.0, 'Weiter')])
check("its settings: 'dfxp', the document's language, the fragments' length",
      (settings['codec'], settings['language'], settings['segment_duration']), ('dfxp', 'de', 5.0))
ABSOLUTE = ismt([(0, 5 * SCALE, document([('00:00:01.000', '00:00:03.000', 'Eins')])),
                 (5 * SCALE, 5 * SCALE, document([('00:00:06.000', '00:00:08.000', 'Zwei')]))])
(folder / 'absolute.ismt').write_bytes(ABSOLUTE)
check('documents that count from the programme start read where they say',
      [(s['start'], s['end'], s['text']) for s in isobmff_text.read(str(folder / 'absolute.ismt'))[0]],
      [(1.0, 3.0, 'Eins'), (6.0, 8.0, 'Zwei')])
tracks, _defaults = isobmff_text.movie(RELATIVE)
check("the 'dfxp' description is a text track", [t['codec'] for t in isobmff_text.text_tracks(tracks)], ['dfxp'])

print('writing')
trip = [{'start': 0.5, 'end': 2.0, 'text': 'Hello <i>world</i>'},
        {'start': 4.0, 'end': 8.0, 'text': 'Across a fragment'},
        {'start': 13.0, 'end': 14.25, 'text': 'Late\nline'}]
data = isobmff_text.write_ismt(trip, duration=6, language='en')
check('ftyp isml, compatible with piff', (data[8:12], data[16:24]), (b'isml', b'ismlpiff'))
tracks, _defaults = isobmff_text.movie(data)
track = tracks[1]
check("a 'dfxp' track on Smooth Streaming's 10 MHz clock, handler 'text'",
      (track['codec'], track['timescale'], track['handler']), ('dfxp', 10000000, 'text'))
samples, _clock = isobmff_text.fragment_samples(data, 1)
check('a fragment each 6 s, timed by its tfxd', [(start / SCALE, duration / SCALE) for start, duration, _b in samples],
      [(0.0, 6.0), (6.0, 6.0), (12.0, 2.25)])
check("no tfdt (PIFF's fragments do not have one)", b'tfdt' in data, False)
check("the second fragment's document counts from its own start",
      b'begin="00:00:00.000" end="00:00:02.000"' in samples[1][2], True)
(folder / 'out.ismt').write_bytes(data)
check('read back as written', [(s['start'], s['end'], s['text']) for s in isobmff_text.read(str(folder / 'out.ismt'))[0]],
      [(s['start'], s['end'], s['text']) for s in trip])

print('told apart')
cmft_segments, cmft_settings = isobmff_text.read_data(b''.join([isobmff_text.write(trip, codec='stpp')[0]]
                                                               + isobmff_text.write(trip, codec='stpp')[1]))
check("a CMAF 'stpp' track still reads as stpp, its times as they are",
      (cmft_settings['codec'], [s['start'] for s in cmft_segments]), ('stpp', [0.5, 4.0, 13.0]))

print('through file_io')
session.SUBTITLE = Subtitle({'language': 'en'})
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'relative.ismt'))
check('opens as ISMT, its language and fragment length kept',
      (fmt, len(opened), session.SUBTITLE.get('language').split('-')[0], session.FORMAT['options'].get('segment_duration')),
      ('ISMT', 3, 'de', 5.0))
session.SUBTITLE = Subtitle({'segments': trip, 'language': 'en'})
session.VIDEO = {}
session.FORMAT = {'options': {'segment_duration': 4}}
file_io.save_file(str(folder / 'saved.ismt'), 'ISMT')
saved, saved_settings = isobmff_text.read(str(folder / 'saved.ismt'))
check('saved and read back, in fragments of the length kept',
      ([(s['start'], s['end'], s['text']) for s in saved], saved_settings['segment_duration']),
      ([(s['start'], s['end'], s['text']) for s in trip], 4.0))
check('the extension', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['ISMT']['extensions'], ['ismt'])

print('the dialog')
from subtitld.interface.export_dialog import SUBTITLE_FORMATS, _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('ISMT')
panel._refresh_options()
config = panel.get_config()
check('ISMT in the export menu, with the text options and the segment length, no track choice',
      ('ISMT' in SUBTITLE_FORMATS, panel.text_options.isHidden(), panel.segment_combo.isHidden(),
       panel.carriage_combo.isHidden(), 'segment_duration' in config['options']),
      (True, False, False, True, True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
