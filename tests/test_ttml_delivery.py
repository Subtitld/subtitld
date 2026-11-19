"""Netflix's delivery rules in ttml.check: a file Subtitld writes that keeps
them is clean; each rule the Partner Help Center publishes (Timed Text
Style Guide: General Requirements; Additional TTML Schema Inspections;
the Netflix IMSC 1.1 Text Profile) is found where a file breaks it; and
the export dialog lists them under the profile.

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
from subtitld.modules import session, ttml
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def netflix(text, profile='ttml1'):
    return [problem for problem in ttml.check(text, profile, delivery='netflix') if problem.startswith('Netflix')]


GOOD = [{'start': 1.0, 'end': 3.0, 'text': 'Hello'}, {'start': 4.0, 'end': 6.5, 'text': '{\\an8}Top\nsecond line'}]
# Netflix's own sample from "What does a properly formatted TTML file look like?".
SAMPLE = ('<?xml version="1.0" encoding="utf-8"?>\n<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
          'xmlns:tts="http://www.w3.org/ns/ttml#styling" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" ttp:timeBase="media" '
          'ttp:frameRate="24" xml:lang="en"><head><metadata><ttm:title>Sample TTML</ttm:title></metadata><styling>'
          '<style xml:id="s1" tts:textAlign="center" tts:fontFamily="Arial" tts:fontSize="100%"/></styling><layout>'
          '<region xml:id="bottom" tts:displayAlign="after" tts:extent="80% 40%" tts:origin="10% 50%"/>'
          '<region xml:id="top" tts:displayAlign="before" tts:extent="80% 40%" tts:origin="10% 10%"/></layout></head>'
          '<body><div><p xml:id="subtitle1" begin="00:00:01.375" end="00:00:05.750" style="s1" region="top">TOP SAMPLE TEXT</p>'
          '<p xml:id="subtitle6" begin="00:00:25.875" end="00:00:30.166" style="s1" region="bottom">- Did he tell you?<br/>- He did</p>'
          '</div></body></tt>')

print('clean')
check("Netflix's own sample", netflix(SAMPLE), [])
for profile in ('ttml1', 'imsc1'):
    check(f'what Subtitld writes in {profile}, kept to the rules', netflix(ttml.write(GOOD, 'en', profile=profile)), [])
check('IMSC 1.1 in Japanese', netflix(ttml.write(GOOD, 'ja', profile='imsc1.1'), 'imsc1.1'), [])
check('no delivery asked: nothing of it', [p for p in ttml.check(SAMPLE.replace('100%', '12px'), 'ttml1') if 'Netflix' in p], [])

print('General Requirements')
check('5/6 of a second to 7 seconds; two lines', netflix(ttml.write(
    [{'start': 0, 'end': 0.8, 'text': 'short'}, {'start': 1, 'end': 9, 'text': 'long'}, {'start': 10, 'end': 12, 'text': 'a\nb\nc'}], 'en')),
    ['Netflix: 1 subtitle(s) last less than 5/6 of a second', 'Netflix: 1 subtitle(s) last more than 7 seconds',
     'Netflix: 1 subtitle(s) of more than two lines'])
check('exactly 5/6 of a second is enough', netflix(ttml.write([{'start': 0, 'end': 0.834, 'text': 'x'}], 'en')), [])
check('centred', netflix(ttml.write([{'start': 0, 'end': 2, 'text': '{\\an7}left'}], 'en')),
      ['Netflix: tts:textAlign left: subtitles are to be centred'])
check('percentages, not pixels; a font size of 100%',
      netflix(SAMPLE.replace('tts:fontSize="100%"', 'tts:fontSize="80%"').replace('80% 40%" tts:origin="10% 50%"', '80% 40%" tts:origin="10px 50px"')),
      ['Netflix: lengths in pixels (origin="10px 50px"): only percentages are taken',
       'Netflix: tts:fontSize 80%: it is to be 100%'])

print('Additional TTML Schema Inspections')
check('xml:lang with a hyphen', netflix(SAMPLE.replace('xml:lang="en"', 'xml:lang="pt_BR"')),
      ['Netflix: xml:lang="pt_BR" is not a language tag: it is written with a hyphen (pt-BR)'])
check('id, not xml:id', netflix(SAMPLE.replace('xml:id="subtitle1"', 'id="subtitle1"')),
      ['Netflix: id="..." where TTML has xml:id (subtitle1)'])
check('an xml:id twice, one starting with a digit', netflix(SAMPLE.replace('xml:id="subtitle6"', 'xml:id="subtitle1"')
                                                         .replace('xml:id="s1"', 'xml:id="1s"').replace('style="s1"', 'style="1s"')),
      ['Netflix: xml:id used more than once: subtitle1', 'Netflix: xml:id that does not start with a letter: 1s'])

print('Netflix IMSC 1.1 Text Profile')
check('Japanese only', netflix(ttml.write(GOOD, 'en', profile='imsc1.1'), 'imsc1.1'), ['Netflix: IMSC 1.1 is taken for Japanese only'])
check("font sizes it takes (150%) and one it does not (120%)",
      netflix(ttml.write(GOOD, 'ja', profile='imsc1.1').replace('tts:fontSize="100%"', 'tts:fontSize="150%"'), 'imsc1.1')
      + netflix(ttml.write(GOOD, 'ja', profile='imsc1.1').replace('tts:fontSize="100%"', 'tts:fontSize="120%"'), 'imsc1.1'),
      ['Netflix: tts:fontSize 120%: its IMSC 1.1 profile takes 50%, 100%, 150% and 200%'])
prohibited = ttml.write(GOOD, 'ja', profile='imsc1.1').replace('<p ', '<p tts:zIndex="1" ', 1).replace(
    '<div>', '<div timeContainer="seq">', 1)
check('what it prohibits', netflix(prohibited, 'imsc1.1'), ['Netflix: its IMSC 1.1 profile prohibits timeContainer, zIndex'])
english = ttml.write([{'start': 0, 'end': 2, 'text': '<font emphasis="filled dot">x</font>'},
                      {'start': 3, 'end': 5, 'text': 'down', 'writing': 'vertical-rl'}], 'en', profile='imsc1.1')
check("Japanese's features outside Japanese", netflix(english, 'imsc1.1')[-1],
      'Netflix: its IMSC 1.1 profile prohibits tts:textEmphasis outside Japanese, vertical writing outside Japanese')

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = Subtitle({'segments': [{'start': 0, 'end': 0.5, 'text': 'quick'}], 'language': 'en'})
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTML')
panel._refresh_options()
panel.profile_combo.combobox.setCurrentIndex(panel.profile_combo.combobox.findData('imsc1'))
check('a delivery check beside the profile, none by default', (panel.delivery_combo.isHidden(), panel.delivery_combo.combobox.currentData()),
      (False, ''))
check('without it, no Netflix rules', 'Netflix' in panel.check_label.text(), False)
panel.delivery_combo.combobox.setCurrentIndex(panel.delivery_combo.combobox.findData('netflix'))
check("with Netflix, its rules listed under the profile", ('Netflix' in panel.check_label.text().splitlines()[0],
                                                            'less than 5/6 of a second' in panel.check_label.text()), (True, True))
check('remembered', panel.get_config() and session.CONFIG['export_subtitles'].get('delivery'), 'netflix')
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('hidden for formats that are not TTML', panel.delivery_combo.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
