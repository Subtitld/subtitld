"""IMSC's Image Profiles written: each subtitle drawn as the preview draws
it, a PNG in a folder beside the file, laid where it was drawn, its words
as alt text; the file declared as IMSC 1.0.1's or 1.1's Image Profile and
read back by ttml.check and by Subtitld itself, pictures and words.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. The renderer's helper draws in software.
"""
import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, file_io, picture_ocr, session, ttml
from subtitld.interface import subtitle_pictures

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())
SEGMENTS = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <i>there</i>'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}A sign', 'forced': True},
            {'start': 5.0, 'end': 6.0, 'text': ''},
            {'start': 7.0, 'end': 8.0, 'text': '<ruby>東京<rt>とうきょう</rt></ruby>'}]

print('drawn')
drawn = subtitle_pictures.draw(SEGMENTS, (1280, 720), str(folder / 'drawn'), stem='cue')
check('a picture for each subtitle with something to show', [(p['start'], p['alt']) for p in drawn],
      [(1.0, 'Hello there'), (3.0, 'A sign'), (7.0, '東京')])
images = [QImage(p['path']) for p in drawn]
check('PNGs as big as the subtitle they show', [(i.width(), i.height()) == (p['width'], p['height'])
                                               for i, p in zip(images, drawn)], [True, True, True])
check('with nothing around the subtitle drawn', all(i.hasAlphaChannel() and i.pixelColor(0, 0).alpha() == 0
                                                    for i in images), True)
check('where the preview puts them: the sign at the top, the line at the bottom',
      (drawn[1]['y'] < 720 / 3, drawn[0]['y'] + drawn[0]['height'] > 720 * 2 / 3, drawn[1].get('forced')),
      (True, True, True))

print('written')
session.SUBTITLE = {'segments': SEGMENTS, 'language': 'en', 'metadata': {'title': 'Pictures'}}
session.VIDEO = {'width': 1280, 'height': 720}
for profile in ttml.IMAGE_PROFILES:
    session.FORMAT = {'format': 'TTML', 'options': {'profile': profile}}
    target = folder / f'{profile}.ttml'
    file_io.save_file(str(target), 'TTML')
    written = target.read_text(encoding='utf-8')
    check(f'{profile}: nothing ttml.check finds against it', ttml.check(written, profile), [])
    check(f'{profile}: the pictures beside it, named from it',
          sorted(os.listdir(folder / f'{profile}_pictures')), ['00001.png', '00002.png', '00003.png'])
    if profile == 'imsc1-image':
        (folder / f'{profile}_pictures' / '00009.png').write_bytes(b'left from a longer export')
        (folder / f'{profile}_pictures' / 'notes.txt').write_text('mine')
        file_io.save_file(str(target), 'TTML')
        check('written again: the pictures of the export before go, what is not one of them stays',
              sorted(os.listdir(folder / f'{profile}_pictures')), ['00001.png', '00002.png', '00003.png', 'notes.txt'])
    check(f'{profile}: the picture\'s size, and a region for each picture as big as it',
          ('tts:extent="1280px 720px"' in written, written.count('<region'), 'smpte:image' in written),
          (True, 3, False))
check('a text file is no Image Profile file, and says why',
      [problem.split(':')[0] for problem in ttml.check(ttml.write(SEGMENTS[:1], 'en', profile='imsc1.1'), 'imsc1.1-image')],
      ["ttp", "<tt> has no tts", "2 <p> or <span> element(s)"])

print('read back')
pictures, language = picture_ocr.imsc_pictures(str(folder / 'imsc1.1-image.ttml'))
check('the pictures where they were drawn, with their words',
      [(p.x, p.y, p.width, p.height, p.text, p.forced) for p in pictures],
      [(p['x'], p['y'], p['width'], p['height'], p['alt'], p.get('forced', False)) for p in drawn])
check('in the language it was written in', language, 'en')
opened, fmt = file_io.process_subtitles_file(str(folder / 'imsc1.1-image.ttml'))
check('opened as the words it gives, every picture having some, the sign at the top and forced',
      ([(s['start'], s['text'], s.get('forced', False)) for s in opened], fmt),
      ([(1.0, 'Hello there', False), (3.0, '{\\an8}A sign', True), (7.0, '東京', False)], 'TTML'))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
