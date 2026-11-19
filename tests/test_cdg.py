"""CD+G: writing subtitles as karaoke graphics (the packs, sectors and
palette; the pages drawn; the wipe following the karaoke timing),
decoding them back with the module's reader and with FFmpeg's, the
pictures handed to the OCR path, and the export through file_io with the
MP3 beside it (MP3+G).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import cdg, export_options, file_io, picture_ocr, picture_subtitles, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


SONG = [{'start': 1.0, 'end': 4.0, 'text': '{\\k50}Hel{\\k50}lo {\\k100}world'},
        {'start': 6.0, 'end': 9.0, 'text': 'No karaoke codes'},
        {'start': 9.0, 'end': 10.0, 'text': 'Straight after'}]
data = cdg.write(SONG, duration=12.0)
packs = [data[at:at + cdg.PACK] for at in range(0, len(data), cdg.PACK)]

print('packs and sectors')
check('24 bytes a pack, 4 packs a sector, 300 a second: 12 seconds are 3600 packs, 900 sectors',
      (len(data) % cdg.PACK, len(packs), len(packs) % cdg.PACKS_PER_SECTOR, len(packs) // cdg.PACKS_PER_SECTOR,
       cdg.PACKS_PER_SECOND), (0, 3600, 0, 900, 300))
check('a song longer than the duration asked for still ends on a sector', len(cdg.write(SONG, duration=0)) % 96, 0)
check('every pack graphics (command 9) or nothing, six bits of data a byte, no parity written', (
    {pack[0] for pack in packs}, all(byte < 64 for pack in packs for byte in pack[4:20]),
    {pack[2:4] + pack[20:] for pack in packs}), ({0, 9}, True, {bytes(6)}))
check('the file begins with a memory preset, a border preset and both halves of the palette',
      [pack[1] for pack in packs[:4]], [cdg.MEMORY_PRESET, cdg.BORDER_PRESET, cdg.PALETTE_LOW, cdg.PALETTE_HIGH])
check('it says it is CD+G as FFmpeg\'s probe would', (cdg.is_cdg(data[:7200]), cdg.is_cdg(b'\x01' * 48), cdg.is_cdg(bytes(48))),
      (True, False, False))

print('the palette')
colours = cdg.palette()
check('16 colours of 4 bits a channel', (len(colours), all(0 <= value <= 15 for colour in colours for value in colour)), (16, True))
check('0 and 4 the background, 1-3 the letters\' levels, 5-7 the same levels sung',
      (colours[0] == colours[4] == cdg.BACKGROUND, colours[3], colours[7]), (True, cdg.LETTERS, cdg.LETTERS_SUNG))
check('the wipe XORs 4 and nothing else', {pack[5] for pack in packs if pack[1] == cdg.TILE_XOR and pack[4] == 0} - {2},
      {cdg.SUNG})

print('the screen, and the wipe')
screen = cdg.Screen()
states = {}
want_times = {0.9, 1.25, 1.55, 2.05, 2.5, 3.5, 4.5, 6.05, 7.5, 9.2}
for number, pack in enumerate(packs):
    screen.apply(pack)
    seconds = round((number + 1) / cdg.PACKS_PER_SECOND, 3)
    if seconds in want_times:
        states[seconds] = bytes(screen.pixels)
check('the palette read back as written, at 8 bits', screen.colours[:8], [tuple(v * 17 for v in c) for c in colours[:8]])
check('only colours 0-7 on the screen at any time', max(max(state) for state in states.values()), 7)
page = cdg._Page(SONG[0])
_row, x0, advances, _times = page.wipes[0]
top, bottom = page.wipes[0][0] * cdg.TILE_HEIGHT, (page.wipes[0][0] + cdg.LINE_ROWS) * cdg.TILE_HEIGHT


def sung(state, first, last):
    """(letters' pixels sung, letters' pixels) in the line between the
    advances of letters `first` and `last`, a pixel in from each side."""
    left, right = int(x0 + advances[first]) + 1, int(x0 + advances[last]) - 1
    ink = [state[y * cdg.WIDTH + x] for y in range(top, bottom) for x in range(left, right) if state[y * cdg.WIDTH + x]]
    return sum(1 for index in ink if index > cdg.SUNG), len(ink)


check('drawn before it starts, nothing sung yet', (sung(states[0.9], 0, 11)[0], sung(states[0.9], 0, 11)[1] > 100), (0, True))
hel, world = sung(states[1.55], 0, 3), sung(states[1.55], 6, 11)
check('a moment after "Hel" (1.0-1.5): all of it sung, none of "world"', (hel[0] == hel[1] > 0, world[0]), (True, 0))
half = sung(states[1.25], 0, 3)
check('halfway through "Hel": about half of it', 0.25 < half[0] / half[1] < 0.75, True)
lo, world = sung(states[2.05], 3, 5), sung(states[2.05], 6, 11)
check('just after "lo " (1.5-2.0): "lo" sung, "world" begun', (lo[0] == lo[1] > 0, world[0] / world[1] < 0.1), (True, True))
part = sung(states[2.5], 6, 11)
check('halfway into "world" (2.0-3.0): about half', 0.35 < part[0] / part[1] < 0.65, True)
check('after it, all of the line', sung(states[3.5], 0, 11)[0] == sung(states[3.5], 0, 11)[1], True)
check('cleared in the gap after it', max(states[4.5][cdg.WIDTH * cdg.TILE_HEIGHT:-cdg.WIDTH * cdg.TILE_HEIGHT]), 0)
plain = cdg._Page(SONG[1])
_row, x0, advances, _times = plain.wipes[0]
top, bottom = plain.wipes[0][0] * cdg.TILE_HEIGHT, (plain.wipes[0][0] + cdg.LINE_ROWS) * cdg.TILE_HEIGHT
done = sung(states[7.5], 0, len(advances) - 1)
check('no karaoke codes: wiped evenly, half of it halfway', 0.35 < done[0] / done[1] < 0.65, True)

# The lag of the wipe: when each column of the first page turns, against
# when it is due.
due = page.due()
turned = {}
screen = cdg.Screen()
for number, pack in enumerate(packs[:int(4.0 * cdg.PACKS_PER_SECOND)]):
    screen.apply(pack)
    for (row, column), columns in due.items():
        for when, x in columns:
            key = (row, column, x)
            if key not in turned and any(screen.pixels[(row * cdg.TILE_HEIGHT + y) * cdg.WIDTH + column * cdg.TILE_WIDTH + x] > cdg.SUNG
                                         for y in range(cdg.TILE_HEIGHT)):
                turned[key] = (number + 1) / cdg.PACKS_PER_SECOND - when
check('every column of a letter turns, none early, none more than a twentieth of a second late',
      (len(turned), sum(len(columns) for columns in due.values()), min(turned.values()) >= -1 / 300,
       max(turned.values()) <= 0.05), (sum(len(columns) for columns in due.values()),) * 2 + (True, True))

ffmpeg = shutil.which('ffmpeg')
folder = Path(tempfile.mkdtemp())
(folder / 'song.cdg').write_bytes(data)
if ffmpeg:
    # FFmpeg's decoder gives a frame a graphics pack, its pts the pack's
    # number: the screen after that pack.
    chosen = [max(number for number in range(target + 1) if packs[number][0] == 9) for target in (450, 1100, 1850)]
    screen = cdg.Screen()
    mine = {}
    for number, pack in enumerate(packs):
        screen.apply(pack)
        if number in chosen:
            mine[number] = b''.join(bytes(screen.colours[index]) for index in screen.pixels)
    theirs = {}
    for number in mine:
        run = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(folder / 'song.cdg'),
                              '-vf', f'select=eq(pts\\,{number})', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                             capture_output=True, timeout=60)
        theirs[number] = run.stdout
    check("FFmpeg's decoder draws the same screens, while a page is wiped", [mine[number] == theirs[number] for number in chosen],
          [True, True, True])
else:
    print('  [skip] ffmpeg is not installed')

print('reading back as pictures')
pictures = cdg.parse(data)
check('a picture a page, from its start to its end when it was drawn before it',
      [(p.start, p.end) for p in pictures][:2], [(1.0, 4.0), (6.0, 9.0)])
check('the one straight after another shows once drawn (in under half a second), until it ends',
      (len(pictures), 9.0 < pictures[2].start < 9.5, pictures[2].end), (3, True, 10.0))
picture = picture_subtitles.crop(pictures[0])
check('cropped to the letters, inside the border', (picture.y >= cdg.TILE_HEIGHT, picture.x >= cdg.TILE_WIDTH,
                                                     0 < picture.width < 288, 0 < picture.height <= 24), (True, True, True, True))
check('a PGM for the OCR add-on', picture_subtitles.pgm(picture)[:2], b'P5')
check('through picture_ocr: the same pictures, no language', (len(picture_ocr.read_file(str(folder / 'song.cdg'))[0]),
                                                              picture_ocr.read_file(str(folder / 'song.cdg'))[1],
                                                              picture_ocr.is_picture_file('x.cdg')), (3, '', True))
check('a transparent background, the letters opaque', (pictures[0].colours[0][3], pictures[0].colours[3][3]), (0, 255))

print('through file_io')
session.SUBTITLE = {'segments': []}
session.FORMAT = {}
session.VIDEO = {}
try:
    file_io.process_subtitles_file(str(folder / 'song.cdg'))
    check('opening a .cdg: pictures, for the OCR add-on', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError as error:
    check('opening a .cdg: pictures, for the OCR add-on', (error.kind, error.path), ('cdg', str(folder / 'song.cdg')))
(folder / 'not.cdg').write_bytes(b'hello world' * 10)
try:
    file_io.process_subtitles_file(str(folder / 'not.cdg'))
    check('a .cdg that is not one says so', None, 'CorruptedProjectFileError')
except file_io.PictureSubtitlesError:
    check('a .cdg that is not one says so', 'PictureSubtitlesError', 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('a .cdg that is not one says so', str(error).startswith('Not a CD+G'), True)

session.SUBTITLE = {'segments': [dict(segment) for segment in SONG]}
if ffmpeg:
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=11',
                    '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=11', '-shortest', '-c:v', 'libx264', '-c:a', 'aac',
                    str(folder / 'video.mp4')], check=True, timeout=120)
    session.VIDEO = {'filepath': str(folder / 'video.mp4'), 'duration': 11.0}
session.FORMAT = {'format': 'CDG', 'options': {'cdg_mp3': True}}
file_io.save_file(str(folder / 'export.cdg'), 'CDG')
written = (folder / 'export.cdg').read_bytes()
check('the export: CD+G as long as the video', (len(written) // cdg.PACK, cdg.is_cdg(written[:2400])),
      (3300 if ffmpeg else 3012, True))
if ffmpeg:
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name:format=duration', '-of', 'csv=p=0',
                            str(folder / 'export.mp3')], capture_output=True, text=True).stdout.split()
    check('MP3+G: the video\'s sound beside it, by the same name, as long', (probe[0], round(float(probe[1]))), ('mp3', 11))
check('registered: a picture format to export, with the text options', (
    session.LIST_OF_PICTURE_SUBTITLE_EXTENSIONS['CDG']['extensions'], 'CDG' in export_options.TEXT_FORMATS), (['cdg'], True))
from subtitld.interface import export_dialog
dialog = export_dialog.ExportDialog()
panel = dialog.findChild(export_dialog._SubtitlesPanel)
panel.format_combo.setCurrentText('CDG')
panel._refresh_options()
check('the dialog offers it, with the MP3 beside it when there is a video, and says what it draws', (
    'CDG' in export_dialog.SUBTITLE_FORMATS, panel.cdg_mp3_check.isHidden(), panel.check_label.isHidden(),
    panel.get_config()['options'].get('cdg_mp3')), (True, not ffmpeg, False, bool(ffmpeg)))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
