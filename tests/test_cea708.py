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
from subtitld.modules import cea708

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

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
