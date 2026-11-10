"""CEA-708: decoding caption channel packets built here byte by byte (pop-on
windows shown and toggled, live roll-up lines, places, pen styles and
colours, G0, G1, G2, G3 and 16-bit characters, the extended codes it skips,
Delay, a packet across frames, a broken one, other services), and writing
service 1 pop-on captions (round trips, the frames they show on, how the
packets go out).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import cea708, markup, position

fails = []
RATE = 30000 / 1001


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def packet(*blocks, sequence=0, service=1):
    """A caption channel packet with a service block for each of `blocks`."""
    data = []
    for block in blocks:
        assert len(block) <= 31, block
        if service < 7:
            data += [service << 5 | len(block)]
        else:
            data += [7 << 5 | len(block), service]
        data += list(block)
    if len(data) % 2 == 0:
        data.append(0)
    return [sequence << 6 | (len(data) + 1) // 2 % 64] + data


def entries(data):
    return [(3 if i == 0 else 2, data[i], data[i + 1]) for i in range(0, len(data), 2)]


def stream(*timed):
    return [(frame, entries(data)) for frame, data in timed]


def text(string):
    return list(string.encode('latin-1'))


def define(window, visible=False, relative=False, v=0, h=0, anchor=0, rows=1, columns=32, style=1, pen=1):
    return [0x98 + window, 0x18 | (0x20 if visible else 0), (0x80 if relative else 0) | v, h, anchor << 4 | (rows - 1),
            columns - 1, style << 3 | pen]


def centred(window, width, rows=1, visible=False):
    """A window as wide as its text, low and centred, as CEA-608 converted
    to CEA-708 has them."""
    return define(window, visible=visible, v=60, h=(160 - width * 5) // 2, rows=rows, columns=width)


def cues(batches, service=1):
    return [(s['start'], s['end'], s['text']) for s in cea708.decode(batches, RATE, service)]


ITALIC, PLAIN = [0x90, 0x05, 0x80], [0x90, 0x05, 0x00]
CR, DSW, HDW, TGW, CLW, DLW = [0x0D], 0x89, 0x8A, 0x8B, 0x88, 0x8C

print('pop-on windows')
check('loaded hidden, shown, cleared; italics by the pen; a CR a new row; low and centred, as upconverted CEA-608 places it',
      cues(stream(
          (20, packet(centred(0, 14, rows=2) + text("You're a "), ITALIC + text('jerk,') + PLAIN + CR + text('Celia.'))),
          (30, packet([DSW, 0x01])),
          (75, packet([CLW, 0x01])))),
      [(1.001, 2.502, "You're a <i>jerk,</i>\nCelia.")])
TOGGLED = stream(
    (10, packet(centred(0, 5) + text('First'))),
    (30, packet([DSW, 0x01])),
    (40, packet(define(1, relative=True, v=10, h=10), text('Second, top left'))),
    (60, packet([TGW, 0x03])),
    (90, packet([HDW, 0x02])))
check('ToggleWindows swaps two: the second shows as the first goes; top left by percentages', cues(TOGGLED),
      [(1.001, 2.002, 'First'), (2.002, 3.003, '{\\an7}Second, top left')])
check('the last one, never hidden: four seconds', cues(stream(
    (0, packet(centred(0, 3) + text('End'))), (30, packet([DSW, 0x01]))))[0][:2], (1.001, 5.001))
check('defined visible: shown at once; DeleteWindows ends it', cues(stream(
    (0, packet(centred(3, 12) + text('Hidden first'))),
    (30, packet(centred(3, 12, visible=True))),
    (60, packet([DLW, 0x08])))), [(1.001, 2.002, 'Hidden first')])
check('the same definition again changes nothing', cues(stream(
    (0, packet(centred(0, 4) + text('Kept'))), (30, packet([DSW, 0x01])),
    (40, packet(centred(0, 4))), (60, packet([CLW, 0x01]))))[0][:2], (1.001, 2.002))

print('places')
for name, window, want in (
        ('right-justified at the right, anchored there', define(0, relative=True, v=90, h=90, anchor=8) + [0x97, 0, 0, 0x0D, 0],
         '{\\an3}'),
        ('centred style (3), in the middle', define(0, relative=True, v=50, h=50, anchor=4, style=3), '{\\an5}'),
        ('absolute, top centre of a 16:9 grid', define(0, v=4, h=105, anchor=1, style=3), '{\\an8}'),
        ('absolute, low and left', define(0, v=70, h=10, anchor=6), '{\\an1}')):
    got = cues(stream((0, packet(window + text('Here'))), (30, packet([DSW, 0x01]))))[0][2]
    check(name, got, want + 'Here')

print('live lines')
check('written on screen: a subtitle a line, from its first character to the next line\'s; low centre',
      cues(stream(
          (0, packet(define(2, visible=True, v=70, h=10, anchor=6, rows=2, style=4))),
          (10, packet(text('HELLO,'))), (20, packet(CR)), (25, packet(text('THIS IS'))), (40, packet(CR)),
          (45, packet(text('LIVE.'))), (90, packet([HDW, 0x04])))),
      [(0.334, 0.834, 'HELLO,'), (0.834, 1.502, 'THIS IS'), (1.502, 3.003, 'LIVE.')])
check('a pop-on window written while shown grows as one subtitle', cues(stream(
    (0, packet(centred(0, 8, rows=2) + text('Paint'))), (30, packet([DSW, 0x01])),
    (40, packet(text(' on'))), (60, packet([CLW, 0x01])))), [(1.001, 2.002, 'Paint on')])

print('pen and characters')
COLOURS = ([0x91, 0x30, 0, 0] + text('red ') + [0x91, 0x3C, 0, 0] + text('yellow '), [0x91, 0x3F, 0, 0] + text('white'))
check('SetPenColor: two bits a component; white none', cues(stream(
    (0, packet(centred(0, 16), *COLOURS)), (30, packet([DSW, 0x01]))))[0][2],
      '<font color="#ff0000">red</font> <font color="#ffff00">yellow</font> white')
CHARACTERS = ([0x7F, 0xE9, 0x10, 0x25, 0x10, 0x33] + text('q') + [0x10, 0x34, 0x10, 0x39, 0x20, 0x18, 0xD5, 0x5C],
              [0x10, 0xA0, 0x10, 0x08, 0x41, 0x10, 0x80, 1, 2, 3, 4, 0x10, 0x22] + [0x11, 0x41] + text('end'))
check('♪, é, …, “ ”, ™, 16-bit 한; the CC logo nothing; C2 and C3 codes skipped whole; an unknown G2 an underscore; '
      'C0 0x11 two bytes', cues(stream((0, packet(centred(0, 14), *CHARACTERS)), (30, packet([DSW, 0x01]))))[0][2],
      '♪é…“q”™ 한_end')
check('BS, HCR, FF', cues(stream(
    (0, packet(centred(0, 5, rows=2) + text('Typoo'), [0x08] + text('!') + CR + text('gone') + [0x0E] + text('kept'))),
    (30, packet([DSW, 0x01])), (40, packet([0x0C])))), [(1.001, 1.335, 'Typo!\nkept')])

print('commands in time')
check('Delay holds what follows: shown a second later', cues(stream(
    (0, packet(centred(0, 4) + text('Late'))), (30, packet([0x8D, 10, DSW, 0x01])),
    (90, packet([CLW, 0x01]))))[0][:2], (2.001, 3.003))
check('Reset: what shows goes', cues(stream(
    (0, packet(centred(0, 5) + text('Reset'))), (30, packet([DSW, 0x01])), (45, packet([0x8F])),
    (60, packet([CLW, 0x01]))))[0][:2], (1.001, 1.502))
SPLIT = entries(packet(centred(0, 5) + text('Split') + [DSW, 0x01]))
check('a packet across frames counts at its last', cues([(29, SPLIT[:3]), (30, SPLIT[3:])])[0][:2], (1.001, 5.001))
check('a packet cut short by the next is dropped', cues([(0, entries(packet(centred(0, 4) + text('Lost')))[:2]),
                                                       (10, entries(packet(centred(0, 4) + text('Kept')))),
                                                       (30, entries(packet([DSW, 0x01])))])[0][2], 'Kept')
SERVICES = stream(
    (0, packet(centred(0, 3) + text('One'), [DSW, 0x01])),
    (2, packet(centred(0, 3) + text('Dos'), [DSW, 0x01], service=2)),
    (4, packet(centred(0, 3) + text('Ten'), [DSW, 0x01], service=10)))
check('services: which hold data; the second; an extended one (header 7)', (
    cea708.services(SERVICES), cues(SERVICES, 2)[0][2], cues(SERVICES, 10)[0][2], cues(SERVICES)[0][2]),
      ([1, 2, 10], 'Dos', 'Ten', 'One'))

print('writing')
segments = [
    (1.0, 2.5, "You're a <i>jerk</i>,\nCelia."),
    (2.5, 4.0, '{\\an8}<font color="#ffd700">Gold at the top</font>'),
    (5.0, 7.0, '{\\an3}Ação, café — “quotes” ♪ ½ Łódź ő 한'),
    (8.0, 9.0, '{\\an7}<u>Under</u> and <font color="#ff0000">red</font>'),
    (10.0, 11.0, '{\\an4}<i>Left</i>'), (11.0, 12.0, '{\\an5}Middle'), (12.0, 13.0, '{\\an6}Right'),
    (13.0, 14.0, '{\\an1}Low left'), (14.0, 15.0, '{\\an9}Top right'),
    (16.0, 17.0, 'A long line that goes on for more than thirty-two columns'),
]
shows = []
for index, (start, end, words) in enumerate(segments):
    following = segments[index + 1][0] if index + 1 < len(segments) else None
    shows.append((words, round(start * RATE), None if following == end else round(end * RATE)))
data = cea708.frame_data(shows, 18)
batches = sorted(data.items())
back = cues(batches)
check('read back: text, italics, colours to two bits, underline, the nine places, accents CEA-708 lacks as their '
      'letter, 16-bit characters; long lines wrapped at 32', [words for _start, _end, words in back], [
          "You're a <i>jerk</i>,\nCelia.", '{\\an8}<font color="#ffff00">Gold at the top</font>',
          '{\\an3}Ação, café — “quotes” ♪ ½ Łódz o\n한', '{\\an7}<u>Under</u> and <font color="#ff0000">red</font>',
          '{\\an4}<i>Left</i>', '{\\an5}Middle', '{\\an6}Right', '{\\an1}Low left', '{\\an9}Top right',
          'A long line that goes on for\nmore than thirty-two columns'])
check('shown on their frames, cleared on theirs, or by the next', [(start, end) for start, end, _words in back][:3],
      [(1.001, 2.502), (2.502, 4.004), (5.005, 7.007)])
assembled = cea708.packets(batches)
check('the show: the last one deleted, then this one shown, in one packet on its frame', [
    list(packet_data[2:]) for frame, packet_data in assembled if frame == round(2.5 * RATE)], [[0x8C, 0x01, 0x89, 0x02]])
sequences = [packet_data[0] >> 6 for _frame, packet_data in assembled]
check('packets: whole, one after another; numbered 0 to 3 in turn; even, at most 128 bytes; blocks of at most 31',
      (sum(len(packet_data) for _frame, packet_data in assembled) == 2 * sum(len(t) for _f, t in batches),
       sequences == [n % 4 for n in range(len(sequences))],
       all(len(p) % 2 == 0 and len(p) <= 128 for _f, p in assembled),
       all(len(block) <= 31 for _f, p in assembled for _s, block in cea708.blocks(p))), (True, True, True, True))
check('at most the room a frame has', max(len(triplets) for triplets in data.values()) <= 18, True)
tight = cea708.frame_data([('One', 10, None), ('Two two two two two two two', 11, 40)], 2)
check('too little room: the show waits for its load', [round(s['start'] * RATE) for s in cea708.decode(
    sorted(tight.items()), RATE)][1] > 11, True)

print('writing: roll-up, paint-on and more than one service')
SHOWS = [('First line', 30, None), ('Second line', 90, 150)]
for mode, want in (('roll2', [(1.034, 3.003, 'First line'), (3.003, 5.005, 'Second line')]),
                   ('roll4', [(1.034, 3.003, 'First line'), (3.003, 5.005, 'Second line')]),
                   ('paint', [(1.034, 3.036, 'First line'), (3.036, 5.005, 'Second line')])):
    typed = cea708.frame_data(SHOWS, 18, mode=mode)
    check(f'{mode}: read back as it was typed',
          [(round(s['start'], 3), round(s['end'], 3), s['text']) for s in cea708.decode(sorted(typed.items()), RATE)],
          want)
rolled = cea708.frame_data(SHOWS, 18, mode='roll3')
first = cea708.blocks(cea708.packets(sorted(rolled.items()))[0][1])[0][1]
check('roll-up: the windows cleared, then one defined visible, three rows, the pen on its base row',
      (list(first[:2]), first[3] & 0x20, (first[6] & 0x0F) + 1, list(first[14:19])),
      ([0x8C, 0xFF], 0x20, 3, [0x88, 0x01, 0x92, 0x02, 0x00]))
both = cea708.frames_for({1: SHOWS, 3: [('Primera linea', 30, None)]}, 18)
batches = sorted(both.items())
check('two services in one channel, each read on its own',
      (cea708.services(batches), [s['text'] for s in cea708.decode(batches, RATE, 1)],
       [s['text'] for s in cea708.decode(batches, RATE, 3)]),
      ([1, 3], ['First line', 'Second line'], ['Primera linea']))


print('placed by hand')


def _window_of(text, place, mode='pop'):
    """The DefineWindow a caption of `mode` writes, as its bytes."""
    shows = [(text, 30, None, place)]
    frames = cea708.frames_for({1: shows}, 18, mode)
    data = [byte for _frame, triplets in sorted(frames.items())
            for _type, first, second in triplets for byte in (first, second)]
    start = next(index for index, byte in enumerate(data) if 0x98 <= byte <= 0x9F)
    return data[start:start + 7]


_plain = _window_of('Where it always went', position.NOWHERE)
check('no place of its own: anchored at the bottom middle, as it always was',
      (_plain[2], _plain[3]), (0x80 | 90, 50))
_corner_plain = _window_of('{\\an1}Hard against the corner', position.NOWHERE)
check('and at a corner, the title-safe tenth in from both edges',
      (_corner_plain[2], _corner_plain[3]), (0x80 | 90, 10))
_lifted = _window_of('Clear of a lower third', position.Place(v='30%'))
check('lifted 30%: the anchor says exactly that, with nothing rounded',
      (_lifted[2], _lifted[3]), (0x80 | 70, 50))
_corner = _window_of('{\\an7}In from the corner', position.Place(h='20%', v='15%'))
check('in from a corner: both margins straight into the anchor, and the corner it hangs from',
      (_corner[2], _corner[3], _corner[4] >> 4), (0x80 | 15, 20, 0))
_pixels = _window_of('Said in pixels', position.Place(v='108'))
check('a margin in pixels of a 1080-tall picture is a tenth of it',
      _pixels[2], 0x80 | 90)
_turned = _window_of('Turned about', position.Place(v='30%', rotate=(0.0, 0.0, 45.0)))
check('a rotation is nothing CEA-708 can draw: the place it leaves is still kept',
      _turned[2], 0x80 | 70)

for _mode in ('pop', 'roll2', 'paint'):
    _shows = [('{\\an1}Hard against the corner', 30, None, position.Place(h='40%'))]
    _read = cea708.decode(sorted(cea708.frames_for({1: _shows}, 18, _mode).items()), RATE, 1)
    check(f'{_mode}: read back where it was placed, off the left edge',
          (_read[0]['text'], _read[0].get('position')),
          ('{\\an1}Hard against the corner', {'h': '40%'}))
_read = cea708.decode(sorted(cea708.frames_for({1: SHOWS}, 18).items()), RATE, 1)
check('captions where the nine places alone put them say nothing more',
      [subtitle.get('position') for subtitle in _read], [None, None])


print('a background behind the pen')


def _pen_bytes(text):
    """The SetPenColor a caption writes for its first run."""
    units = cea708._load(text, 0)
    return next(unit for unit in units if unit[0] == 0x91)


check('solid black is what a pen sits on, as a CEA-608 one does: nothing says otherwise',
      _pen_bytes('Plain')[2], 0x00)
check('a colour to two bits a component, solid',
      _pen_bytes('<font background="#0000ff">On blue</font>')[2], 0b00_00_00_11)
check('half seen through, and asked to be seen through altogether',
      (_pen_bytes('<font background="#00ff0080">x</font>')[2] >> 6,
       _pen_bytes('<font background="#00000000">x</font>')[2] >> 6), (2, 3))
for _mode in ('pop', 'roll2', 'paint'):
    _asked = 'Plain <font color="#ffff00" background="#0000ff">yellow on blue</font> plain'
    _read = cea708.decode(sorted(cea708.frames_for(
        {1: [(_asked, 30, None)]}, 18, _mode).items()), RATE, 1)
    check(f'{_mode}: written into the pen and read back, colour and all',
          markup.spans(_read[0]['text']), markup.spans(_asked))
_read = cea708.decode(sorted(cea708.frames_for({1: SHOWS}, 18).items()), RATE, 1)
check('captions that ask for no background say nothing about one',
      [subtitle['text'] for subtitle in _read], ['First line', 'Second line'])


print('the size, place and edge of a pen')


def _attrs(text):
    """The SetPenAttributes a caption writes for its first run."""
    return next(unit for unit in cea708._load(text, 0) if unit[0] == 0x90)


check('standard size, on the line, no edge: what it always wrote',
      _attrs('Plain')[1:], [0x05, 0x00])
check('the three sizes, by their two bits',
      [_attrs(f'<font size="{size}">x</font>')[1] & 0x03 for size in ('80%', '100%', '120%')], [0, 1, 2])
check('lowered, on the line and raised',
      [(_attrs(written)[1] >> 2) & 0x03 for written in ('<sub>x</sub>', 'x', '<sup>x</sup>')], [0, 1, 2])
check('each edge CEA-708 names, by its three bits',
      [(_attrs(f'<font edge="{edge}">x</font>')[2] >> 3) & 0x07
       for edge in ('raised', 'depressed', 'uniform', 'left-shadow', 'right-shadow')], [1, 2, 3, 4, 5])
check('the colour the edge is drawn in goes in the pen colour, black by default',
      [next(unit for unit in cea708._load(written, 0) if unit[0] == 0x91)[3]
       for written in ('<font edge="uniform">x</font>',
                       '<font edge="uniform" edge-color="#ff0000">x</font>')],
      [0x00, 0b11_00_00])
for _mode in ('pop', 'roll2', 'paint'):
    _asked = ('Plain <font size="80%" edge="raised" edge-color="#ff0000">small and raised</font> '
              'then <sup>up</sup>')
    _read = cea708.decode(sorted(cea708.frames_for({1: [(_asked, 30, None)]}, 18, _mode).items()), RATE, 1)
    check(f'{_mode}: written into the pen and read back, every part of it',
          markup.spans(_read[0]['text']), markup.spans(_asked))
check('an edge colour with no edge to draw says nothing',
      [subtitle.get('text') for subtitle in cea708.decode(
          sorted(cea708.frames_for({1: SHOWS}, 18).items()), RATE, 1)],
      ['First line', 'Second line'])


print('the services above the sixth')
check('named by an extended service number, written and read back',
      [(cea708.services(sorted(cea708.frames_for({number: [(f'In {number}', 30, None)]}, 18).items())),
        [subtitle['text'] for subtitle in cea708.decode(
            sorted(cea708.frames_for({number: [(f'In {number}', 30, None)]}, 18).items()), RATE, number)])
       for number in (6, 7, 8, 63)],
      [([6], ['In 6']), ([7], ['In 7']), ([8], ['In 8']), ([63], ['In 63'])])
_mixed = sorted(cea708.frames_for({1: [('Main', 30, None)], 20: [('Twentieth', 30, None)]}, 18).items())
check('one of each in a channel, each read on its own',
      (cea708.services(_mixed), [s['text'] for s in cea708.decode(_mixed, RATE, 1)],
       [s['text'] for s in cea708.decode(_mixed, RATE, 20)]),
      ([1, 20], ['Main'], ['Twentieth']))
check('the header says 7 and the byte after it says which',
      [(packet[1] >> 5, packet[2] & 0x3F) for _frame, packet in
       cea708.packets(sorted(cea708.frames_for({20: [('x', 30, None)]}, 18).items()))][:1],
      [(7, 20)])


print('the typeface a pen asks for')
check('each of the eight CEA-708 names, written and read back',
      [cea708.decode(sorted(cea708.frames_for(
          {1: [(f'<font face="{face}">x</font>', 30, None)]}, 18).items()), RATE, 1)[0]['text']
       for face in markup.FACES],
      [f'<font face="{face}">x</font>' for face in markup.FACES])
check('one it does not name is whatever the decoder draws anyway',
      cea708.decode(sorted(cea708.frames_for(
          {1: [('<font face="Arial">x</font>', 30, None)]}, 18).items()), RATE, 1)[0]['text'], 'x')
check('the default takes the tag that says nothing',
      next(unit for unit in cea708._load('Plain', 0) if unit[0] == 0x90)[2] & 0x07, 0)


print('text that flashes')
check('a pen says it in how opaque its own colour is',
      [next(unit for unit in cea708._load(written, 0) if unit[0] == 0x91)[1] >> 6
       for written in ('x', '<blink>x</blink>')], [0, 1])
for _mode in ('pop', 'roll2', 'paint'):
    _asked = 'Plain <blink>flash</blink> then plain'
    _read = cea708.decode(sorted(cea708.frames_for({1: [(_asked, 30, None)]}, 18, _mode).items()), RATE, 1)
    check(f'{_mode}: written into the pen and read back', markup.spans(_read[0]['text']), markup.spans(_asked))
check('a caption that asks for none says nothing about it',
      [subtitle['text'] for subtitle in cea708.decode(
          sorted(cea708.frames_for({1: SHOWS}, 18).items()), RATE, 1)],
      ['First line', 'Second line'])


print('what kind of text a pen says it is')
check('the text tag, in the four bits above the size and the offset',
      [next(unit for unit in cea708._load(f'<font kind="{kind}">x</font>', 0)
            if unit[0] == 0x90)[1] >> 4
       for kind in ('source-id', 'lyrics', 'expletive', 'not-displayed')],
      [1, 8, 11, 15])
check('dialogue is the tag that says nothing, and what a run says by default',
      next(unit for unit in cea708._load('Plain', 0) if unit[0] == 0x90)[1] >> 4, 0)
check('every kind written and read back',
      [cea708.decode(sorted(cea708.frames_for(
          {1: [(f'<font kind="{kind}">x</font>', 30, None)]}, 18).items()), RATE, 1)[0]['text']
       for kind in markup.KINDS if kind],
      [f'<font kind="{kind}">x</font>' for kind in markup.KINDS if kind])
check('beside dialogue in one caption',
      cea708.decode(sorted(cea708.frames_for(
          {1: [('Dialog <font kind="sound-effect">[a door]</font> more', 30, None)]}, 18).items()),
          RATE, 1)[0]['text'],
      'Dialog <font kind="sound-effect">[a door]</font> more')


print("what a window says about itself")


def _window_kept(held, mode='pop'):
    shows = [('Caption text', 30, None, position.NOWHERE, held)]
    read = cea708.decode(sorted(cea708.frames_for({1: shows}, 18, mode).items()), RATE, 1)
    return read[0].get('caption_window') if read else '(nothing read)'


check('a window Subtitld writes itself asks for nothing out of the ordinary',
      _window_kept(None), None)
check('the bytes it writes for one are the ones it always wrote',
      [unit for unit in cea708._load('Plain', 0) if unit[0] == 0x97][0],
      [0x97, 0xC0, 0x00, 0x0C | cea708._CENTER, 0x00])
for _mode in ('pop', 'roll2', 'paint'):
    _asked = {'fill': '#0055aa', 'border': 'raised', 'border_color': '#ffffff',
              'print_direction': 'rtl', 'scroll_direction': 'ltr', 'word_wrap': True,
              'effect': 'wipe', 'effect_direction': 'btt', 'effect_speed': 9, 'priority': 2}
    check(f'{_mode}: all of it written and read back', _window_kept(_asked, _mode), _asked)
check('a box solid black, which a pen on solid black is not',
      (_window_kept({'fill': '#000000'}), _window_kept({'fill': '#0000ff80'})),
      ({'fill': '#000000'}, {'fill': '#0000ff80'}))
check('a border with nothing to draw has no colour of its own',
      _window_kept({'border_color': '#ff0000'}), None)
check('a colour CEA-708 cannot say to two bits a component: the nearest it can',
      _window_kept({'fill': '#003366'}), {'fill': '#005555'})
check('the ordinary directions say nothing, and a named one says itself',
      (_window_kept({'print_direction': 'ltr', 'scroll_direction': 'btt'}),
       _window_kept({'print_direction': 'ttb'})),
      (None, {'print_direction': 'ttb'}))
check('what a window holds, read off a subtitle and put back on one',
      (cea708.window_of({'caption_window': {'fill': '#000000', 'effect': 'fade'}}),
       cea708.window_of({}), cea708.store_window({'text': 'x'}, cea708.NOWINDOW)),
      (cea708.Window(fill='#000000', effect='fade'), cea708.NOWINDOW, {'text': 'x'}))
check('a kind of thing it does not know is nothing',
      cea708.window_of({'caption_window': {'effect': 'explode', 'priority': 99, 'fill': 'nonsense'}}),
      cea708.Window(priority=7))


print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
