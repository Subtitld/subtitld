"""TSB4 (SubtitleNext): chunks laid out as Subtitle Edit's reader takes
them (CPAG's code page, TITL with TIME and LINE, sizes padded with zeros
or with spaces, 7F italics, 00 a space), and its .sub told from MicroDVD's,
SubViewer's, VobSub's and SoftNi's through file_io, read only.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, tsb4

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def title(first, last, text, pad=0):
    # TITL, its size, TIME, its size, the two frame numbers, LINE, its
    # length and the text; `pad` is what fills a zero high byte.
    line = b'LINE' + bytes([len(text) & 0xFF, pad, 0, 0]) + text
    time = b'TIME' + bytes([8, pad, 0, 0]) + first.to_bytes(2, 'little') + bytes([pad, 0]) + last.to_bytes(2, 'little') + bytes([pad, 0])
    body = time + line
    return b'TITL' + bytes([len(body), pad, 0, 0]) + body


def build(titles, code_page=1252):
    return (b'TSB4' + bytes(40) + b'CPAG\x04\x00\x00\x00' + code_page.to_bytes(2, 'little') + b'\x00\x00'
            + b''.join(titles)).ljust(120, b'\x00')


SAMPLE = build([title(25, 75, b'Ol\xe1 mundo'), title(100, 150, b'\x7fItalic\x7f and not\r\nSecond\x00line'),
                title(200, 250, b'')])
SPACED = build([title(25, 75, 'Привет'.encode('cp1251'), 0x20), title(100, 150, b'Two', 0x20)], 1251)

print('reading')
segments, settings = tsb4.parse(SAMPLE)
check('frames at 25, Windows-1252, 7F italics, 00 a space', ([(s['start'], s['end'], s['text']) for s in segments], settings), (
    [(1.0, 3.0, 'Olá mundo'), (4.0, 6.0, '<i>Italic</i> and not\nSecond line')], {'fps': 25.0, 'code_page': 1252}))
check('sizes padded with spaces, Windows-1251', [(s['start'], s['text']) for s in tsb4.parse(SPACED)[0]],
      [(1.0, 'Привет'), (4.0, 'Two')])
check('looks_like', (tsb4.looks_like(SAMPLE[:16]), tsb4.looks_like(b'{1}{2}Hello')), (True, False))

print('through file_io: .sub by its bytes')
folder = Path(tempfile.mkdtemp())
files = {
    'tsb4.sub': SAMPLE,
    'microdvd.sub': b'{25}{75}Hello|world\n{100}{150}Again\n',
    'subviewer.sub': b'[INFORMATION]\n[TITLE]x\n[END INFORMATION]\n[SUBTITLE]\n00:00:01.00,00:00:03.00\nHello[br]world\n\n',
    'softni.sub': b'*PART 1*\r\n00:00:00.00\\00:00:00.00\r\nHello\r\n00:00:01.00\\00:00:03.00\r\n*END*\r\n',
}
want = {'tsb4.sub': 'TSB4', 'microdvd.sub': 'SUB', 'subviewer.sub': 'SUBVIEWER2', 'softni.sub': 'SOFTNI'}
for name, data in files.items():
    (folder / name).write_bytes(data)
    session.VIDEO = {}
    session.FORMAT = {}
    check(name, file_io.process_subtitles_file(str(folder / name))[1], want[name])
(folder / 'vobsub.sub').write_bytes(b'\x00\x00\x01\xba' + bytes(2044))
try:
    file_io.process_subtitles_file(str(folder / 'vobsub.sub'))
    check('VobSub still pictures', None, 'PictureSubtitlesError')
except file_io.PictureSubtitlesError:
    check('VobSub still pictures', True, True)
check('read only: offered to open, not to save', ('TSB4' in session.LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS,
                                                  'TSB4' in session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS), (True, False))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
