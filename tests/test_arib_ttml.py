"""ARIB-TTML (ARIB STD-B62 Volume 1 Part 3, chapter 3): reading the
standard's own examples and a file with ARIB's rubies (the reading after
its base and before it), writing the profile, round trips, ttml.check's
rules for it, and the export dialog and file_io.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session, ttml

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:tts="http://www.w3.org/ns/ttml#styling" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
        'xmlns:arib-tt="http://www.arib.or.jp/ns/arib-tt" '
        'xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt" '
        'ttp:profile="http://www.arib.or.jp/ns/profiles/arib-ttml-full/v1_0" xml:lang="ja">\n')

# Table 3-14 of the standard: a character of a font sent with the document.
GAIJI = (HEAD + '<head><styling><arib-tt:font-face font-family="extChar1" unicode-range="U+E000">'
         '<arib-tt:src url="subt://1" format="svg"/></arib-tt:font-face></styling></head>\n'
         '<body><div><p begin="00:00:03" end="00:00:06">display supplemental characters &#xe000;</p></div></body>\n</tt>')
# Rubies as 3.3.3.5 has them: a span naming the xml:id of the span it reads,
# after it and before it; borders, shadows, spacing and a marquee on the way.
RUBIES = (HEAD + '<body><div>\n'
          '<p begin="00:00:01.000" end="00:00:02.500" arib-tt:text-shadow="2px 2px 1px black">'
          '<span xml:id="r1">漢字</span><span arib-tt:ruby="r1">かんじ</span>を<span tts:color="yellow">読む</span></p>\n'
          '<p begin="00:00:03" end="00:00:04" arib-tt:border="solid 3px white" arib-tt:letter-spacing="2px">'
          '<span arib-tt:ruby="r2">とうきょう</span> <span xml:id="r2">東京</span>へ</p>\n'
          '<p begin="00:00:05" end="00:00:06" arib-tt:marquee="scroll forward normal 1">'
          '<span arib-tt:ruby="nowhere">よみ</span>だけ</p>\n'
          '</div></body>\n</tt>')

print('reading')
check("Table 3-14's document: its text, the character of the sent font as it is",
      ttml.read_text(GAIJI)[0], [{'start': 3.0, 'end': 6.0, 'text': 'display supplemental characters '}])
check("rubies by xml:id, the reading after its base and before it; one naming nothing stays text",
      [(s['start'], s['end'], s['text']) for s in ttml.read_text(RUBIES)[0]],
      [(1.0, 2.5, '<ruby>漢字<rt>かんじ</rt></ruby>を<font color="#ffff00">読む</font>'),
       (3.0, 4.0, '<ruby>東京<rt>とうきょう</rt></ruby>へ'),
       (5.0, 6.0, 'よみだけ')])
check('both documents pass the profile\'s checks but for the ruby naming nothing',
      (ttml.check(GAIJI, 'arib-ttml'), ttml.check(RUBIES, 'arib-ttml')),
      ([], ['arib-tt:ruby="nowhere" names no element of the file']))

print('writing')
SEGMENTS = [
    {'start': 1.0, 'end': 2.5, 'text': '<ruby>漢字<rt>かんじ</rt></ruby>を<b>読む</b>'},
    {'start': 3.0, 'end': 4.0, 'text': '{\\an8}<ruby>東京<rt>とうきょう</rt></ruby>と<ruby>大阪<rt>おおさか</rt></ruby>'},
    {'start': 5.0, 'end': 6.0, 'text': '<font color="#00ffff">字幕</font>\n二行目'},
]
written = ttml.write(SEGMENTS, language='ja', profile='arib-ttml')
root = ET.fromstring(written.encode('utf-8'))
check("declared by ARIB's designator, with its namespace and SMPTE-TT's 2013 one, in Japanese",
      (root.get('{http://www.w3.org/ns/ttml#parameter}profile'), 'xmlns:arib-tt="http://www.arib.or.jp/ns/arib-tt"' in written,
       'xmlns:smpte="http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt"' in written,
       root.get('{http://www.w3.org/XML/1998/namespace}lang')),
      ('http://www.arib.or.jp/ns/profiles/arib-ttml-full/v1_0', True, True, 'ja'))
check('rubies written as ARIB does: the base with an xml:id, the reading naming it, numbered through the file',
      [(element.get('{http://www.w3.org/XML/1998/namespace}id'), element.get('{http://www.arib.or.jp/ns/arib-tt}ruby'),
        element.text) for element in root.iter('{http://www.w3.org/ns/ttml}span')
       if element.get('{http://www.arib.or.jp/ns/arib-tt}ruby') or element.get('{http://www.w3.org/XML/1998/namespace}id')],
      [('ruby1', None, '漢字'), (None, 'ruby1', 'かんじ'), ('ruby2', None, '東京'), (None, 'ruby2', 'とうきょう'),
       ('ruby3', None, '大阪'), (None, 'ruby3', 'おおさか')])
check('no TTML2 ruby in it, and nothing ttml.check finds', ('tts:ruby' in written, ttml.check(written, 'arib-ttml')),
      (False, []))
check('read back as written', ttml.read_text(written)[0], SEGMENTS)

print('the checks')
broken = (HEAD.replace('arib-ttml-full/v1_0', 'arib-ttml-full/v2')
          .replace('2052-1/2013/smpte-tt', '2052-1/2010/smpte-tt')
          + '<head><arib-tt:font-face font-family="x"/><styling><arib-tt:keyframes xml:id="k"/></styling>'
          '<metadata><smpte:image xml:id="Img1" imageType="PNG" encoding="Base64">AA==</smpte:image></metadata></head>\n'
          '<body><div><p begin="0s" end="1s" arib-tt:border="thick 3 white" arib-tt:letter-spacing="2em" '
          'arib-tt:text-shadow="5px 5px blue" arib-tt:marquee="scroll up normal 1" arib-tt:blink="yes">'
          'a\x85b</p></div></body>\n</tt>')
check('the profile, control codes, the 2013 namespace, where elements go, the words attributes take', ttml.check(broken, 'arib-ttml'), [
    "ttp:profile does not name ARIB-TTML's profile (http://www.arib.or.jp/ns/profiles/arib-ttml-full/v1_0)",
    'Control codes U+0085: an ARIB-TTML document holds TAB, LF and CR only',
    "SMPTE vocabulary in http://www.smpte-ra.org/schemas/2052-1/2010/smpte-tt: ARIB-TTML names SMPTE-TT's 2013 "
    'namespace (http://www.smpte-ra.org/schemas/2052-1/2013/smpte-tt)',
    'arib-tt:font-face outside <styling>, where ARIB-TTML puts it',
    'arib-tt:border="thick 3 white": a border is a style, a width in px and a colour',
    'arib-tt:letter-spacing="2em": a length in px',
    'arib-tt:text-shadow="5px 5px blue": two offsets and a blur in px, then a colour',
    'arib-tt:marquee="scroll up normal 1": a style, a direction, a speed and a count',
    'arib-tt:blink: not an attribute ARIB-TTML defines'])
check('another profile does not ask for ARIB\'s rules', ttml.check(written, 'ttml1'), [])

print('the export dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': [dict(s) for s in SEGMENTS], 'language': 'ja-jp'}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTML')
panel._refresh_options()
check('ARIB-TTML among the text profiles, in the order PROFILES has them',
      [panel.profile_combo.combobox.itemData(i) for i in range(panel.profile_combo.combobox.count())],
      list(ttml.PROFILES + ttml.IMAGE_PROFILES))
panel.profile_combo.combobox.setCurrentIndex(panel.profile_combo.combobox.findData('arib-ttml'))
check('its name, and the file it would write checked clean',
      (panel.profile_combo.combobox.currentText(), panel.check_label.text(), panel.get_config()['options']['profile']),
      ('ARIB-TTML (Japan, 4K and 8K broadcast)', 'Nothing Subtitld checks for breaks ARIB-TTML (Japan, 4K and 8K broadcast) '
       'in this file.', 'arib-ttml'))

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'arib.ttml').write_text(RUBIES, encoding='utf-8')
opened, fmt = file_io.process_subtitles_file(str(folder / 'arib.ttml'))
check('opens as TTML with its rubies', (fmt, opened[1]['text']), ('TTML', '<ruby>東京<rt>とうきょう</rt></ruby>へ'))
session.SUBTITLE = {'segments': [dict(s) for s in SEGMENTS], 'language': 'ja-jp'}
session.VIDEO = {}
session.FORMAT = {'format': 'TTML', 'options': {'profile': 'arib-ttml'}}
file_io.save_file(str(folder / 'out.ttml'), 'TTML')
out = (folder / 'out.ttml').read_text(encoding='utf-8')
check('saved through file_io in the profile, clean, and read back',
      ('arib-ttml-full' in out, ttml.check(out, 'arib-ttml'), ttml.read(str(folder / 'out.ttml'))[0] == SEGMENTS),
      (True, [], True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
