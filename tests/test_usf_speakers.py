"""USF: per-speaker dubbing settings survive a save and a reload.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules.usf import USFReader, USFWriter

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def round_trip(speakers):
    segments = [{'start': 1.0, 'end': 2.0, 'text': 'hello', 'speaker': name} for name in speakers]
    content = USFWriter().write(segments, speakers=speakers, language='en-us')
    reader = USFReader()
    reader.read(content)
    return content, reader.speakers


print('"Fit dub to subtitle duration" is kept per speaker')
content, read = round_trip({
    'Alice': {'color': '#ff0000', 'dubbing': {'engine': 'edge-tts', 'voice': 'en-US-AvaNeural',
                                              'rate': 10, 'fit_to_subtitle': True}},
    'Bob': {'color': '#00ff00', 'dubbing': {'engine': 'edge-tts', 'voice': 'en-US-GuyNeural',
                                            'fit_to_subtitle': False}},
    'Carol': {'color': '#0000ff', 'dubbing': {'fit_to_subtitle': True}},
})
check('on is read back as on', read['Alice']['dubbing'].get('fit_to_subtitle'), True)
check("the speaker's other settings come back too",
      {k: read['Alice']['dubbing'].get(k) for k in ('engine', 'voice', 'rate')},
      {'engine': 'edge-tts', 'voice': 'en-US-AvaNeural', 'rate': 10})
check('off reads back as off', bool(read['Bob']['dubbing'].get('fit_to_subtitle', False)), False)
check('off is not written at all', content.count('fit_to_subtitle'), 2)
check('it is kept even with no other dubbing setting', read['Carol'].get('dubbing'), {'fit_to_subtitle': True})

print('a project saved before the setting existed opens with it off')
_content, read = round_trip({'Dave': {'color': '#ffffff', 'dubbing': {'engine': 'edge-tts'}}})
check('no flag, no key', 'fit_to_subtitle' in read['Dave']['dubbing'], False)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
