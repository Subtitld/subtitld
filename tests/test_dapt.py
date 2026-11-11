"""DAPT (W3C Dubbing and Audio description Profiles of TTML2, Candidate
Recommendation Draft of 26 June 2026): scripts for dubbing and audio
description read into a dubbing session — characters as speakers, Script
Events as subtitles, original text as the subtitle's and translations as its
translations, recordings as its dub — and written from one, checked by
ttml.check against DAPT's rules, and opened and saved through file_io.

The first two documents are Examples 10 and 4 of the DAPT specification
(https://www.w3.org/TR/dapt/), Copyright © 2026 World Wide Web Consortium,
used under the W3C Document License.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os
import re
import sys
import tempfile
import wave
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, file_io, session, ttml
from subtitld.interface.export_dialog import _SubtitlesPanel

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
folder = Path(tempfile.mkdtemp())

# DAPT, Example 10: a translated transcript, the original kept.
EXAMPLE_10 = '''<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
    xmlns:daptm="http://www.w3.org/ns/ttml/profile/dapt#metadata"
    ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/dapt1.0/content"
    xml:lang="en"
    daptm:langSrc="fr"
    daptm:scriptRepresents="audio.dialogue"
    daptm:scriptType="translatedTranscript">
  <head>
    <metadata>
      <ttm:agent type="character" xml:id="character_1">
        <ttm:name type="alias">ASSANE</ttm:name>
      </ttm:agent>
    </metadata>
  </head>
  <body>
    <div begin="10s" end="13s" xml:id="d1" ttm:agent="character_1" daptm:represents="audio.dialogue">
      <p xml:lang="fr" daptm:langSrc="fr"> <!-- original -->
        <span>Et c'est grâce à ça qu'on va devenir riches.</span>
      </p>
      <p xml:lang="en" daptm:langSrc="fr"> <!-- translated -->
        <span>And thanks to that, we're gonna get rich.</span>
      </p>
    </div>
  </body>
</tt>'''

# DAPT, Example 4: an audio description pre-recording script.
EXAMPLE_4 = '''<tt xmlns="http://www.w3.org/ns/ttml"
  xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
  xmlns:daptm="http://www.w3.org/ns/ttml/profile/dapt#metadata"
  xmlns:xml="http://www.w3.org/XML/1998/namespace"
  ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/dapt1.0/content"
  xml:lang="en"
  daptm:langSrc="zxx"
  daptm:scriptRepresents="visual.nonText visual.text"
  daptm:scriptType="preRecording">
  <body>
    <div begin="7s" end="8.5s" xml:id="at1"
         daptm:represents="visual.text.location" daptm:langSrc="en">
      <p>
        The Lake District, England
      </p>
    </div>
    <div begin="10s" end="13s" xml:id="a1"
         daptm:represents="visual.nonText">
      <p>
        A woman climbs into a small sailing boat.
      </p>
    </div>
    <div begin="18s" end="20s" xml:id="a2"
         daptm:represents="visual.nonText">
      <p>
        The woman pulls the tiller and the boat turns.
      </p>
    </div>
  </body>
</tt>'''

print('read: a translated dubbing transcript (DAPT Example 10)')
segments, language, metadata = ttml.read_text(EXAMPLE_10)
check('the event a subtitle: its character the speaker, the original text its own, the translation beside it',
      [(s['start'], s['end'], s['text'], s.get('speaker'), s.get('translations')) for s in segments],
      [(10.0, 13.0, "Et c'est grâce à ça qu'on va devenir riches.", 'ASSANE',
        {'en': "And thanks to that, we're gonna get rich."})])
check('the subtitle\'s language the original\'s, not the document\'s default', language, 'fr')
check('what the script says of itself kept', metadata['dapt'],
      {'script_type': 'translatedTranscript', 'script_represents': 'audio.dialogue', 'lang_src': 'fr'})
check('the event\'s identifier and what it represents kept, the source language its own',
      segments[0]['dapt'], {'id': 'd1', 'represents': 'audio.dialogue'})
check('the example is DAPT by ttml.check', ttml.check(EXAMPLE_10, 'dapt'), [])
check('and is told from other TTML', (ttml.is_dapt(EXAMPLE_10), ttml.is_dapt(ttml.write([], 'en'))), (True, False))

print('read: an audio description script (DAPT Example 4)')
segments, language, metadata = ttml.read_text(EXAMPLE_4)
check('each description a subtitle, in the document\'s language, there being no original language to go by',
      (language, [(s['start'], s['text']) for s in segments]),
      ('en', [(7.0, 'The Lake District, England'), (10.0, 'A woman climbs into a small sailing boat.'),
              (18.0, 'The woman pulls the tiller and the boat turns.')]))
check('what each represents, and the in-image text\'s own source language, kept',
      [s['dapt'] for s in segments],
      [{'id': 'at1', 'represents': 'visual.text.location', 'lang_src': 'en'},
       {'id': 'a1', 'represents': 'visual.nonText'}, {'id': 'a2', 'represents': 'visual.nonText'}])
check('the example is DAPT by ttml.check', ttml.check(EXAMPLE_4, 'dapt'), [])
again = ttml.write(segments, language, profile='dapt', metadata=metadata)
check('written again: the script type and what it represents as they were, the descriptions with no language',
      (re.search(r'daptm:scriptRepresents="visual.nonText visual.text" daptm:scriptType="preRecording"', again)
       is not None, 'daptm:langSrc="zxx"' in again,
       '<div xml:id="at1" begin="00:00:07.000" end="00:00:08.500" daptm:represents="visual.text.location">' in again,
       '<p xml:lang="en" daptm:langSrc="en">The Lake District, England</p>' in again), (True, True, True, True))
check('and read back the same', [(s['start'], s['text'], s['dapt']) for s in ttml.parse(again)],
      [(s['start'], s['text'], s['dapt']) for s in segments])
check('still DAPT', ttml.check(again, 'dapt'), [])

print('written from a dubbing session')
dubs = folder / 'dubs'
dubs.mkdir()
for name in ('take1.wav', 'take2 long.wav'):
    with wave.open(str(dubs / name), 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(b'\x00\x00' * 16000 * 4)
SESSION = [
    {'start': 1.0, 'end': 3.0, 'text': 'Where <i>were</i> you?', 'speaker': 'MARIA',
     'translations': {'pt-br': 'Onde você estava?', 'fr': 'Où étais-tu ?'},
     'dubbing': [{'path': str(dubs / 'take1.wav'), 'start': 1.2}]},
    {'start': 3.5, 'end': 6.0, 'text': 'In the woods.', 'speaker': 'JOÃO',
     'translations': {'pt-br': 'Na floresta.'},
     'dubbing': [{'path': str(dubs / 'old.wav'), 'muted': True},
                 {'path': str(dubs / 'take2 long.wav'), 'start': 3.5, 'muted': False,
                  'segments': [{'type': 'audio', 'path': str(dubs / 'take2 long.wav'), 'start': 0.5, 'end': 1.5,
                                'offset': 0.0},
                               {'type': 'audio', 'path': str(dubs / 'take2 long.wav'), 'start': 2.0, 'end': 3.0,
                                'offset': 1.2}]}]},
    {'start': 7.0, 'end': 8.0, 'text': '{\\an8}[door slams]'},
]
path = folder / 'script.ttml'
ttml.save(str(path), SESSION, 'en', profile='dapt', metadata={'title': 'The Woods', 'dapt': {'talents': {'MARIA': 'Ana Atriz'}}})
written = path.read_text(encoding='utf-8')
check('a dubbing script with recordings: as recorded, of dialogue, in the content profile DAPT names',
      (re.search(r'ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/dapt1.0/content" xml:lang="en" '
                 r'daptm:langSrc="en" daptm:scriptRepresents="audio.dialogue" daptm:scriptType="asRecorded"',
                 written) is not None, 'ttp:profile' in written), (True, False))
check('the characters, the talent who voices one before it, as DAPT recommends',
      re.findall(r'<ttm:agent [^>]*>.*?</ttm:agent>', written),
      ['<ttm:agent type="person" xml:id="actor_1"><ttm:name type="full">Ana Atriz</ttm:name></ttm:agent>',
       '<ttm:agent type="character" xml:id="character_1"><ttm:name type="alias">MARIA</ttm:name>'
       '<ttm:actor agent="actor_1"/></ttm:agent>',
       '<ttm:agent type="character" xml:id="character_2"><ttm:name type="alias">JOÃO</ttm:name></ttm:agent>'])
check('the original text and each translation a <p>, translated from it; the recording heard, linked from the script',
      re.findall(r'<p [^>]*>.*?</p>', written)[:3],
      ['<p xml:lang="en" daptm:langSrc="en"><audio src="dubs/take1.wav" type="audio/wave" begin="00:00:00.200"/>'
       'Where <span tts:fontStyle="italic">were</span> you?</p>',
       '<p xml:lang="fr" daptm:langSrc="en">Où étais-tu ?</p>',
       '<p xml:lang="pt-BR" daptm:langSrc="en">Onde você estava?</p>'])
check('a take of clips: each its part of the file, where it plays; the muted take left out',
      re.findall(r'<audio [^>]*/>', written)[1:],
      ['<audio src="dubs/take2%20long.wav" type="audio/wave" clipBegin="00:00:00.500" clipEnd="00:00:01.500"/>',
       '<audio src="dubs/take2%20long.wav" type="audio/wave" begin="00:00:01.200" clipBegin="00:00:02.000" '
       'clipEnd="00:00:03.000"/>'])
check('the place a subtitle sits in is not a script\'s', '[door slams]</p>' in written and 'an8' not in written, True)
check('ttml.check finds nothing wrong with it', ttml.check(written, 'dapt'), [])
segments, language, metadata = ttml.read(str(path))
check('read back: text, speakers, translations as they were',
      [(s['start'], s['end'], s['text'], s.get('speaker'), s.get('translations')) for s in segments],
      [(1.0, 3.0, 'Where <i>were</i> you?', 'MARIA', {'fr': 'Où étais-tu ?', 'pt-BR': 'Onde você estava?'}),
       (3.5, 6.0, 'In the woods.', 'JOÃO', {'pt-BR': 'Na floresta.'}), (7.0, 8.0, '[door slams]', None, None)])
check('the recordings found beside the script, as the dubs they were',
      [s.get('dubbing') for s in segments],
      [[{'path': str(dubs / 'take1.wav'), 'start': 1.2}],
       [{'path': str(dubs / 'take2 long.wav'), 'start': 3.5,
         'segments': [{'type': 'audio', 'path': str(dubs / 'take2 long.wav'), 'start': 0.5, 'end': 1.5, 'offset': 0.0},
                      {'type': 'audio', 'path': str(dubs / 'take2 long.wav'), 'start': 2.0, 'end': 3.0,
                       'offset': 1.2}]}], None])
check('the talent and the title', (metadata['dapt'].get('talents'), metadata['title'], metadata['dapt']['script_type']),
      ({'MARIA': 'Ana Atriz'}, 'The Woods', 'asRecorded'))
translated = ttml.write([{k: v for k, v in s.items() if k != 'dubbing'} for s in SESSION], 'en', profile='dapt')
check('with no recordings, a translated transcript; with no translations either, an original one',
      ('daptm:scriptType="translatedTranscript"' in translated,
       'daptm:scriptType="originalTranscript"' in ttml.write([{'start': 1, 'end': 2, 'text': 'Hi'}], 'en', profile='dapt'),
       'xmlns:tta' in translated), (True, True, False))

print('what has no home in a subtitle, kept')
KEPT = '''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xmlns:tta="http://www.w3.org/ns/ttml#audio"
    xmlns:daptm="http://www.w3.org/ns/ttml/profile/dapt#metadata"
    ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/dapt1.0/content" xml:lang="en" daptm:langSrc="zxx"
    daptm:scriptRepresents="visual.nonText" daptm:scriptType="asRecorded">
  <head><metadata>
    <ttm:agent type="character" xml:id="narrator"><ttm:name type="alias">NARRATOR</ttm:name></ttm:agent>
    <ttm:agent type="character" xml:id="crowd"><ttm:name type="alias">CROWD</ttm:name></ttm:agent>
  </metadata></head>
  <body daptm:represents="visual.nonText">
    <div xml:id="scene1">
      <div begin="25s" end="28s" xml:id="a3" ttm:agent="narrator crowd" daptm:onScreen="OFF_ON">
        <ttm:desc daptm:descType="scene">Scene 1</ttm:desc>
        <p>
          <animate begin="0.0s" end="0.3s" tta:gain="1;0.39" fill="freeze"/>
          <span begin="0.3s" end="2.7s"><audio src="https://example.org/clip3.wav" type="audio/wave"/>The sails billow in the wind.</span>
        </p>
      </div>
      <div begin="30s" end="32s" xml:id="a4">
        <p><span tta:speak="normal">The boat turns.</span></p>
      </div>
    </div>
  </body>
</tt>'''
segments, language, metadata = ttml.read_text(KEPT)
check('the events inside a <div> that groups them, the first character the speaker',
      [(s['start'], s['text'], s.get('speaker')) for s in segments],
      [(25.0, 'The sails billow in the wind.', 'NARRATOR'), (30.0, 'The boat turns.', None)])
check('the second character, the subject coming on screen, the description, the mixing, a recording that is not '
      'here, the speech asked of a synthesizer',
      [s['dapt'] for s in segments],
      [{'id': 'a3', 'on_screen': 'OFF_ON', 'characters': ['NARRATOR', 'CROWD'],
        'descriptions': [{'text': 'Scene 1', 'type': 'scene'}],
        'mixing': [{'begin': '0.0s', 'end': '0.3s', 'gain': '1;0.39', 'fill': 'freeze'}],
        'audio': [{'sources': ['https://example.org/clip3.wav'], 'start': 25.3, 'end': 27.7, 'type': 'audio/wave'}]},
       {'id': 'a4', 'speak': 'normal'}])
check('and DAPT', ttml.check(KEPT, 'dapt'), [])
again = ttml.write(segments, language, profile='dapt', metadata=metadata)
check('written again: both characters, on screen, described, mixed, spoken',
      ('ttm:agent="character_1 character_2" daptm:onScreen="OFF_ON"' in again,
       '<ttm:desc daptm:descType="scene">Scene 1</ttm:desc>' in again,
       '<animate begin="0.0s" end="0.3s" tta:gain="1;0.39" fill="freeze"/>' in again,
       'tta:speak="normal"' in again, ttml.check(again, 'dapt')), (True, True, True, True, []))
check('the absent recording is the one thing not written back', 'clip3.wav' in again, False)

print('what check finds')
broken = (EXAMPLE_10.replace('ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/dapt1.0/content"',
                             'ttp:profile="http://www.w3.org/ns/ttml/profile/imsc1/text"')
          .replace('translatedTranscript', 'translation').replace('daptm:represents="audio.dialogue"',
                                                                   'daptm:represents="visual.text" daptm:onScreen="MAYBE"')
          .replace('ttm:agent="character_1"', 'ttm:agent="character_9"')
          .replace('<p xml:lang="en" daptm:langSrc="fr">', '<p xml:lang="en" daptm:langSrc="fr_FR">')
          .replace('</div>', '</div><div begin="14s" end="15s"><p>Stray</p></div>'))
check('the content profile, ttp:profile, the script type, what an event represents, the subject on screen, the '
      'character, the source language, text in no event', ttml.check(broken, 'dapt'),
      ['ttp:contentProfiles does not name DAPT\'s content profile (http://www.w3.org/ns/ttml/profile/dapt1.0/content)',
       'ttp:profile is present: DAPT, built on TTML2, prohibits it',
       'daptm:scriptType="translation": it is originalTranscript, translatedTranscript, preRecording, asRecorded',
       'Events name characters the script does not have: character_9',
       '1 <p> element(s) in no <div> with an xml:id: text belongs to a Script Event',
       'daptm:represents that is not one of the script\'s daptm:scriptRepresents, or below one (visual.text)',
       'daptm:langSrc that is not a language tag (fr_FR)',
       'daptm:onScreen="MAYBE": it is ON, OFF, ON_OFF or OFF_ON'])
check('no script represents or type, frames with no frame rate, a recording with no type, an id used twice',
      ttml.check(EXAMPLE_4.replace(' daptm:scriptRepresents="visual.nonText visual.text"', '')
                 .replace(' daptm:scriptType="preRecording"', '').replace('begin="10s"', 'begin="250f"')
                 .replace('xml:id="a2"', 'xml:id="a1"')
                 .replace('A woman climbs', '<audio src="climb.wav"/>A woman climbs'), 'dapt'),
      ['<tt> has no daptm:scriptRepresents: a script says what it is an alternative for',
       '<tt> has no daptm:scriptType', 'Times in frames, but <tt> has no ttp:frameRate',
       'xml:ids used twice (a1): each identifier is the script\'s once',
       'daptm:represents that is not one of the script\'s daptm:scriptRepresents, or below one '
       '(visual.nonText, visual.text.location)',
       '1 recording(s) linked with no type: DAPT asks for the audio\'s MIME type'])
check('a value of one\'s own begins x-, or extends the registry\'s with an x- part',
      ttml.check(EXAMPLE_4.replace('visual.nonText visual.text"', 'visual.nonText visual.text x-music"')
                 .replace('visual.text.location', 'visual.text.x-sign'), 'dapt'), [])

print('through file_io')
(folder / 'example10.xml').write_text(EXAMPLE_10, encoding='utf-8')
session.SPEAKERS.clear()
session.FORMAT = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'example10.xml'))
english = file_io._language_like_project('en')
check('a DAPT .xml opens as TTML; its language the original\'s, the translation by Subtitld\'s code, the character '
      'a speaker, kept to be saved as DAPT', (fmt, session.SUBTITLE['language'], list(opened[0]['translations']),
                                              'ASSANE' in session.SPEAKERS, session.FORMAT['options'].get('profile')),
      ('TTML', file_io._known_language('fr'), [english], True, 'dapt'))
session.SUBTITLE['segments'] = opened
session.FORMAT = {'format': 'TTML', 'options': {'profile': 'dapt', 'text': 'translation', 'language': english}}
file_io.save_file(str(folder / 'saved.ttml'), 'TTML')
saved = (folder / 'saved.ttml').read_text(encoding='utf-8')
check('saved as DAPT: the original and its translation both, whatever the export asks of the text',
      (ttml.check(saved, 'dapt'), len(re.findall(r'<p xml:lang=', saved)),
       "And thanks to that, we're gonna get rich." in saved, 'daptm:scriptType="translatedTranscript"' in saved),
      ([], 2, True, True))

print('the export dialog')
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE = {'segments': [dict(s) for s in SESSION], 'language': 'en'}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('TTML')
panel._refresh_options()
panel.profile_combo.combobox.setCurrentIndex(panel.profile_combo.combobox.findData('dapt'))
check('DAPT offered, and the script it would write checked',
      (panel.profile_combo.combobox.currentText(), panel.check_label.text()),
      ('DAPT (dubbing and audio description script)',
       'Nothing Subtitld checks for breaks DAPT (dubbing and audio description script) in this file.'))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
