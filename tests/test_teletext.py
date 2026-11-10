"""Teletext subtitles (EN 300 706 pages in a DVB transport stream, EN 300
472): the error protection (Hamming 8/4 and 24/18, odd parity), with one
error put right and two found; a page's rows read as a subtitle (boxing,
colours, backgrounds, flash, double height, national option sub-sets
chosen by the header and by an M/29/0 packet, accented letters placed by
X/26, where it sits); the timing of a page's transmissions (shown from the
one that brings it until the one that erases or replaces it, a page sent
in another magazine or serially ending one's transmission, PTS wrapping);
a transport stream built here (PAT, PMT with the teletext descriptor, PES
packets of data units) read back, as 188- and 192-byte packets, and
checked against FFmpeg's own teletext decoder (libzvbi) where FFmpeg has
it; a video written out as a transport stream with teletext pages, listed
as tracks when it is opened and read by the "Subtitles in the video"
engine; and the export path and its place in the dialog.

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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import session
session.set_unsaved = lambda *a, **k: None
from subtitld.modules import teletext as tt

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('error protection')
check('Hamming 8/4: the codes of EN 300 706 table 8.2', bytes(tt.ham84_encode(n) for n in range(16)).hex(),
      '1502495e6473382fd0c78c9ba1b6fdea')
check('every code read back', [tt.ham84(tt.ham84_encode(n)) for n in range(16)], list(range(16)))
check('one bit wrong: put right', {tt.ham84(tt.ham84_encode(n) ^ (1 << bit)) == n for n in range(16) for bit in range(8)},
      {True})
check('two bits wrong: refused', {tt.ham84(tt.ham84_encode(n) ^ 0x03) for n in range(16)}, {None})
values = (0, 1, 0x2AAAA, 0x15555, 0x3FFFF, 0x12345)
check('Hamming 24/18: read back', [tt.ham2418(tt.ham2418_encode(v)) for v in values], list(values))
check('any one of its 24 bits wrong: put right',
      {tt.ham2418(bytes(b ^ ((1 << (bit % 8)) if i == bit // 8 else 0) for i, b in enumerate(tt.ham2418_encode(v)))) == v
       for v in values for bit in range(24)}, {True})
check('two wrong: refused', {tt.ham2418(bytes((tt.ham2418_encode(v)[0] ^ 0x06,)) + tt.ham2418_encode(v)[1:])
                             for v in values}, {None})
check('odd parity', (tt.parity_encode(0x41), tt.parity(0xC1), tt.parity(0x41), tt.parity_encode(0x43)),
      (0xC1, 0x41, None, 0x43))

print('characters')
check('Latin G0, English', ''.join(tt.g0_char(code) for code in (0x23, 0x41, 0x5C, 0x7E)), '£A½÷')
check('the national option sub-sets', [tt.subset_of(0, option) for option in range(8)],
      ['english', 'german', 'swedish', 'italian', 'french', 'spanish', 'czech', 'english'])
check('German, French, Spanish in their places',
      [tt.g0_char(0x5B, 'german'), tt.g0_char(0x7E, 'german'), tt.g0_char(0x23, 'french'), tt.g0_char(0x7C, 'spanish')],
      ['Ä', 'ß', 'é', 'ñ'])
check('the other groups: Polish, Turkish, Romanian, Estonian',
      [tt.subset_of(1, 0), tt.subset_of(2, 6), tt.subset_of(3, 7), tt.subset_of(4, 2)],
      ['polish', 'turkish', 'romanian', 'estonian'])
check('a page\'s language chooses its group', [tt.language_code(code) for code in ('pol', 'fre', 'pt-br', 'xx-yy')],
      ['pl', 'fr', 'pt', 'xx'])


def row(*parts):
    """A row's 40 bytes: ints are codes, strings G0 text; padded with spaces."""
    codes = []
    for part in parts:
        codes += [part] if isinstance(part, int) else [ord(c) for c in part]
    codes += [0x20] * (40 - len(codes))
    return bytes(tt.parity_encode(code) for code in codes[:40])


print('a page as a subtitle')
SB, EB, DH = 0x0B, 0x0A, 0x0D
check('boxed text, in its colours', tt.render({22: row(' ' * 10, DH, SB, SB, 'Hello ', 0x03, 'world', EB, EB)}),
      'Hello <font color="#ffff00">world</font>')
check('outside the box nothing shows on a subtitle page',
      tt.render({22: row('Not shown here', SB, SB, 'Shown', EB, EB, 'nor this, at all')}), 'Shown')
check('a page not for subtitles shows all of it', tt.render({5: row(' Index 100')}, subtitle=False), '{\\an7}Index 100')
check('two rows, centred near the bottom',
      tt.render({20: row(' ' * 14, DH, SB, SB, 'First', EB), 22: row(' ' * 14, DH, SB, SB, 'Second', EB)}),
      'First\nSecond')
check('the row under a double height one is not read',
      tt.render({20: row(' ' * 14, DH, SB, SB, 'Upper', EB), 21: row(SB, SB, 'lower half')}), 'Upper')
check('at the top', tt.render({2: row(' ' * 15, SB, SB, 'Top', EB)}), '{\\an8}Top')
check('on the left', tt.render({22: row(SB, SB, 'Left side', EB)}), '{\\an1}Left side')
check('a background (New Background takes the colour the text is in), and flash',
      tt.render({22: row(' ' * 10, SB, SB, 0x04, 0x1D, 0x07, 'Boxed', 0x1C, ' ', 0x08, 'flash', EB)}),
      '<font background="#0000ff">Boxed</font> <blink>flash</blink>')
check('mosaics and concealed text show nothing',
      tt.render({22: row(SB, SB, 'Word ', 0x11, '#$%', 0x07, 'and ', 0x18, 'secret', EB)}), '{\\an1}Word and')
check('a character failing its parity is a space',
      tt.render({22: bytes([tt.parity_encode(SB)] * 2) + bytes(tt.parity_encode(ord(c)) for c in 'ab') + b'\x63' +
                 bytes(tt.parity_encode(ord(c)) for c in 'cd') + bytes([tt.parity_encode(0x20)] * 33)}), '{\\an1}ab cd')
check('national option characters', tt.render({22: row(' ' * 14, SB, SB, 'Gr', 0x7D, 0x7E, 'e')}, 'german'), 'Grüße')
check('X/26: an accented letter and a G2 character placed over the page',
      tt.render({22: row(' ' * 14, SB, SB, 'Cafe ', 0x20)}, placed={(22, 19): 'é', (22, 21): '♪'}), 'Café ♪')
check('an empty page: no subtitle', tt.render({}), '')

print('transmissions')


def header(page='888', erase=True, option=0, subtitle=True, serial=False):
    magazine, number = tt.page_parts(page)
    controls = [0, erase << 3, 0, subtitle << 3, 0x07,
                serial | ((option >> 2) & 1) << 1 | ((option >> 1) & 1) << 2 | (option & 1) << 3]
    data = bytes([tt.ham84_encode(number & 0x0F), tt.ham84_encode(number >> 4)] + [tt.ham84_encode(c) for c in controls])
    return tt._field(magazine, 0, data + row()[:32])


def at(seconds, *fields):
    return [(round(seconds * 90000), field) for field in fields]


def line(page, number, *parts):
    return tt._field(tt.page_parts(page)[0], number, row(*parts))


stream = (at(0.0, header()) + at(1.0, header(), line('888', 22, ' ' * 14, SB, SB, 'One', EB))
          + at(1.5, header(erase=False))
          + at(2.0, header('100', subtitle=False), line('100', 22, ' ' * 14, SB, SB, 'Elsewhere'))
          + at(3.0, header()) + at(4.0, header(), line('888', 22, ' ' * 14, SB, SB, 'Two', EB))
          + at(5.0, header(), line('888', 22, ' ' * 14, SB, SB, 'Three', EB)) + at(6.0, header()))
check('shown from the transmission that brings it until the one that erases or replaces it; '
      'one sent again unchanged, and another page\'s rows, change nothing',
      [(s['start'], s['end'], s['text']) for s in tt.decode(stream, '888', origin=0)],
      [(1.0, 3.0, 'One'), (4.0, 5.0, 'Two'), (5.0, 6.0, 'Three')])
check('another page of the stream', [(s['start'], s['text']) for s in tt.decode(stream, '100', origin=0)],
      [(2.0, 'Elsewhere')])
parallel = at(1.0, header(), header('700'), line('888', 22, ' ' * 14, SB, SB, 'Mine', EB))
check('a header of another magazine leaves a page in parallel mode open',
      [s['text'] for s in tt.decode(parallel + at(2.0, header()), '888', origin=0)], ['Mine'])
serial = at(1.0, header(serial=True), header('700', serial=True), line('888', 22, ' ' * 14, SB, SB, 'Not mine', EB))
check('but closes it in serial mode', tt.decode(serial + at(2.0, header(serial=True)), '888', origin=0), [])
check('the header\'s national option: French',
      [s['text'] for s in tt.decode(at(1.0, header(option=4), line('888', 22, ' ' * 14, SB, SB, 'D', 0x23, 'j', 0x40, EB))
                                     + at(2.0, header()), '888', origin=0)], ['Déjà'])
designation = tt._field(8, 29, bytes([tt.ham84_encode(0)]) + tt.ham2418_encode(1 << 10) + bytes(36))
check('M/29/0 designating the group: Polish where English would be',
      [s['text'] for s in tt.decode(at(0.5, designation) + at(1.0, header(), line('888', 22, ' ' * 14, SB, SB, 'Gd', 0x7B, 'nia', EB))
                                     + at(2.0, header()), '888', origin=0)], ['Gdżnia'])
check('the page\'s language choosing the group, without one',
      [s['text'] for s in tt.decode(at(1.0, header(), line('888', 22, ' ' * 14, SB, SB, 'Gd', 0x7B, 'nia', EB))
                                     + at(2.0, header()), '888', language='pol', origin=0)], ['Gdżnia'])
wrapped = [((pts + (1 << 33) - 45000) % (1 << 33), field) for pts, field in stream]
check('PTS wrapping round', [(s['start'], s['end']) for s in tt.decode(wrapped, '888')],
      [(1.0, 3.0), (4.0, 5.0), (5.0, 6.0)])

print('a transport stream')
folder = Path(tempfile.mkdtemp())
segments = [{'start': 1.0, 'end': 2.5, 'text': 'Hello <font color="#ffff00">yellow</font> world\nSecond line'},
            {'start': 3.0, 'end': 4.0, 'text': '{\\an8}Café über naïve — ♪'},
            {'start': 4.0, 'end': 5.0, 'text': 'Straight after'}]
pages = [{'page': '888', 'language': 'fre', 'subtitld_language': 'fr-fr', 'type': 2, 'segments': segments},
         {'page': '889', 'language': 'ger', 'type': 5, 'segments': [{'start': 0.5, 'end': 1.5, 'text': 'Grüße'}]}]
data = tt.transport_stream(pages, start=10.0)
(folder / 'pages.ts').write_bytes(data)
check('a transport stream of whole packets', (len(data) % 188, tt.is_transport_stream(folder / 'pages.ts')), (0, True))
check('its pages, from the PMT\'s descriptor', tt.streams(folder / 'pages.ts'),
      {tt.TELETEXT_PID: [{'language': 'fre', 'type': 2, 'magazine': 8, 'page': '888'},
                         {'language': 'ger', 'type': 5, 'magazine': 8, 'page': '889'}]})
read, info = tt.read(folder / 'pages.ts')
check('read back: the first subtitle page, its colours, its place, the accented letters its sub-set lacks',
      ([(s['start'], s['end'], s['text']) for s in read], info['page'], info['language']),
      ([(1.0, 2.5, 'Hello <font color="#ffff00">yellow</font> world\nSecond line'),
        (3.0, 4.0, '{\\an8}Café über naïve — ♪'), (4.0, 5.0, 'Straight after')], '888', 'fre'))
check('the page for the hard of hearing, in German',
      [(s['start'], s['end'], s['text']) for s in tt.read(folder / 'pages.ts', page='889')[0]], [(0.5, 1.5, 'Grüße')])
m2ts = b''.join(b'\x00\x00\x00\x00' + data[i:i + 188] for i in range(0, len(data), 188))
(folder / 'pages.m2ts').write_bytes(m2ts)
check('Blu-ray\'s 192-byte packets', [s['text'] for s in tt.read(folder / 'pages.m2ts', page='889')[0]], ['Grüße'])
(folder / 'none.txt').write_bytes(b'not a transport stream' * 20)
check('a file that is no transport stream', tt.is_transport_stream(folder / 'none.txt'), False)
try:
    tt.read(folder / 'none.txt')
    check('no teletext: an error', None, 'ValueError')
except ValueError:
    check('no teletext: an error', True, True)

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
decoders = subprocess.run([ffmpeg, '-hide_banner', '-decoders'], capture_output=True, text=True).stdout if ffmpeg else ''
if 'libzvbi_teletextdec' not in decoders:
    print('  [SKIP] FFmpeg with libzvbi is not installed: the stream is not checked against it')
else:
    def zvbi(path, page):
        out = subprocess.run([ffmpeg, '-v', 'error', '-txt_format', 'text', '-txt_page', page, '-i', str(path),
                              '-map', '0:s:0', '-f', 'srt', '-'], capture_output=True, text=True).stdout
        return [' '.join(block.split('\n')[2:]).split() for block in out.strip().split('\n\n') if block.strip()]
    check('libzvbi reads the page the same: addresses, Hamming, parity, sub-set, X/26',
          zvbi(folder / 'pages.ts', '888'),
          [['Hello', 'yellow', 'world', 'Second', 'line'], ['Café', 'über', 'naïve', '—', '♪'], ['Straight', 'after']])
    check('and the German one', zvbi(folder / 'pages.ts', '889'), [['Grüße']])

if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed: no video, no engine')
else:
    print('in a video')
    from subtitld.modules import file_io, soft_subtitles, video_subtitles, waveform
    session.FFMPEG_EXECUTABLE, session.FFPROBE_EXECUTABLE = ffmpeg, ffprobe
    waveform.FFPROBE_EXECUTABLE = ffprobe
    source = folder / 'source.mkv'
    # PCM sound: AAC's priming would start the file 21 ms before the picture.
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=6',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '6', '-c:v', 'mpeg2video',
                    '-c:a', 'mp2', str(source)], check=True)
    tracks = [{'segments': segments, 'language': 'fr-fr'},
              {'segments': [{'start': 2.0, 'end': 3.0, 'text': 'Olá'}], 'language': 'pt-br'}]
    made = soft_subtitles.write_video(source, folder / 'out.ts', tracks, container='TS', ffmpeg=ffmpeg)
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_name', '-of',
                            'default=noprint_wrappers=1:nokey=1', str(made)], capture_output=True, text=True).stdout.split()
    probe = list(dict.fromkeys(probe))             # each stream is listed under its programme too
    check('the picture and sound copied, beside a teletext stream', probe, ['mpeg2video', 'mp2', 'dvb_teletext'])
    opened = file_io.process_video_file(str(made))
    check('opened: a track for each page, its language and page',
          [(t['codec'], t['kind'], t['page'], t['language_tag'], t['language'], t['hearing_impaired'])
           for t in opened['subtitle_tracks']],
          [('dvb_teletext', 'teletext', '888', 'fre', 'fr-fr', False), ('dvb_teletext', 'teletext', '889', 'por', 'pt-pt', False)])
    check('each told from the other', [video_subtitles.key(t) for t in opened['subtitle_tracks']], ['2/888', '2/889'])

    def near(found, want):
        return all(abs(a - b) <= 0.05 for a, b in zip(found, want)) and len(found) == len(want)

    first = opened['subtitle_tracks'][0]
    read = video_subtitles.read_teletext(made, first['index'], first['page'], first['pid'], ffprobe=ffprobe)
    check('read: the subtitles, at the times of the picture they were written for',
          ([s['text'] for s in read], near([s['start'] for s in read], [1.0, 3.0, 4.0])),
          ([s['text'] for s in segments], True))
    second = video_subtitles.read_teletext(made, first['index'], '889', None, ffprobe=ffprobe)
    check('the other page, with no PID given', [s['text'] for s in second], ['Olá'])
    # Another container's teletext stream is read packet by packet through
    # ffprobe: the same file, taken for one.
    real = tt.is_transport_stream
    tt.is_transport_stream = lambda path: False
    try:
        probed = video_subtitles.read_teletext(made, first['index'], '888', ffprobe=ffprobe)
    finally:
        tt.is_transport_stream = real
    check('a stream read through ffprobe: the same subtitles, at the same times',
          ([s['text'] for s in probed], near([s['start'] for s in probed], [1.0, 3.0, 4.0])),
          ([s['text'] for s in segments], True))

    print('the engine')
    from subtitld.modules.addons.builtin import subtitles_provider
    provider = subtitles_provider.get_provider()

    def wait(signals, seconds=20):
        loop, heard = QEventLoop(), {}
        slots = []
        for name, signal in signals.items():
            slot = (lambda name: lambda *args: (heard.setdefault(name, args), loop.quit()))(name)
            signal.connect(slot)
            slots.append((signal, slot))
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()
        for signal, slot in slots:
            signal.disconnect(slot)
        return heard

    second_track = opened['subtitle_tracks'][1]
    provider.transcribe(str(made), 'en-us', {'track': second_track['index'], 'codec': 'dvb_teletext',
                                             'page': second_track['page'], 'pid': second_track['pid'], 'duration': 6})
    outcome = wait({'finished': provider.transcript_finished, 'error': provider.error})
    check('"Subtitles in the video" reads the page chosen, in a thread',
          [s['text'] for s in outcome.get('finished', [[]])[0]], ['Olá'])
    provider.transcribe(str(folder / 'missing.ts'), 'en-us', {'track': 2, 'codec': 'dvb_teletext', 'page': '888'})
    check('a video it cannot read: an error', sorted(wait({'finished': provider.transcript_finished,
                                                            'error': provider.error})), ['error'])

    print('the export path')
    from subtitld.modules import bounce
    session.VIDEO = {'filepath': str(source), 'framerate': 25.0, 'duration': 6.0, 'width': 320, 'height': 240}
    session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Um', 'translations': {'en-us': 'One'}}],
                        'language': 'pt-br'}
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.ts'), 'TS', {'translations': True, 'forced': True})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    check('what the export runs writes the file, a page for each language',
          (said, tt.streams(folder / 'wired.ts').get(next(iter(tt.streams(folder / 'wired.ts'))))),
          ([(True, '')], [{'language': 'por', 'type': 2, 'magazine': 8, 'page': '888'},
                          {'language': 'eng', 'type': 2, 'magazine': 8, 'page': '889'}]))
    check('and the translation on its own page', [s['text'] for s in tt.read(folder / 'wired.ts', page='889')[0]],
          ['One'])

print('the dialog')
from subtitld.interface.export_dialog import _VideoPanel
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.mp4'}
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'}
panel = _VideoPanel()
panel.video_tabwidget.setCurrentWidget(panel.tracks_panel)
panel.tracks_format_combo.combobox.setCurrentIndex(panel.tracks_format_combo.combobox.findData('TS'))
panel._refresh_tracks()
check('a transport stream with teletext: no codec, nor forced, to choose',
      (panel.get_config()['format'], panel.tracks_codec_combo.isHidden(), panel.tracks_forced.isHidden()),
      ('TS', True, True))
check('its file name', session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS['TS']['extensions'], ['ts'])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
