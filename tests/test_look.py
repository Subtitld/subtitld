"""A subtitle's own look: the dict a file's styling becomes, and the inline
CSS the renderer draws it with.

Lengths are percentages of the picture's height, so they mean the same at
any size; the stage host.js lays a subtitle on is 1080 tall, which is what
turns one back into pixels.

Pure module — no Qt.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import look

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('on the subtitle')
check('a subtitle with no look of its own has none', look.of({'start': 0, 'end': 1, 'text': 'x'}), {})
segment = {'start': 0, 'end': 1, 'text': 'x'}
check('what is stored is what comes back', (look.store(segment, {'size': 8.0}), look.of(segment)),
      ({'size': 8.0}, {'size': 8.0}))
check('nothing to say takes the key away again, not an empty dict left behind',
      (look.store(segment, {'font': '', 'size': None}), 'look' in segment), ({}, False))
check('a key a look has no room for is not kept', look.store({}, {'size': 8.0, 'elbow': 'wide'}), {'size': 8.0})

print('as CSS the renderer draws')
check('a length is pixels of the 1080 the stage is tall', look.css({'size': 10.0}), {'font-size': '108px'})
check('normal is a line height too, and not a length',
      (look.css({'line_height': 'normal'}), look.css({'line_height': 5.0})),
      ({'line-height': 'normal'}, {'line-height': '54px'}))
check('a colour keeps its alpha, and drops it when there is none to keep',
      (look.css({'background': '#80ff0000'})['background'], look.css({'background': '#ffff0000'})['background']),
      ('rgba(255, 0, 0, 0.502)', '#ff0000'))
check('an outline is twice the stroke, with the letters painted over it',
      look.css({'outline': {'width': 1.0, 'color': '#ff000000'}}),
      {'-webkit-text-stroke': '21.6px #000000', 'paint-order': 'stroke fill'})
check('every shadow the file gave, in order',
      look.css({'shadow': [{'x': 0.5, 'y': 0.5, 'blur': 1.0, 'color': '#80000000'},
                            {'x': -0.5, 'y': -0.5}]})['text-shadow'],
      '5.4px 5.4px 10.8px rgba(0, 0, 0, 0.502), -5.4px -5.4px 0px #000000')
check('line padding comes after the padding it narrows, so CSS keeps it',
      list(look.css({'padding': [1.0, 1.0, 1.0, 1.0], 'line_padding': 2.0}).items()),
      [('padding', '10.8px 10.8px 10.8px 10.8px'), ('padding-left', '21.6px'), ('padding-right', '21.6px')])
check('where the shorter lines of a block sit', look.css({'row_align': 'end'}), {'text-align': 'right'})
check('a subtitle a file only part shows', look.css({'opacity': 0.6}), {'opacity': '0.6'})
check('no wrapping is pre, wrapping is pre-wrap',
      (look.css({'wrap': 'nowrap'}), look.css({'wrap': 'wrap'})),
      ({'white-space': 'pre'}, {'white-space': 'pre-wrap'}))
check('a look that says nothing draws nothing', (look.css({}), look.css(None)), ({}, {}))
check('and as one attribute', look.style_attribute({'size': 5.0, 'font': 'Arial'}),
      'font-family: Arial; font-size: 54px')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
