"""The DVD authoring scripts: DVD Studio Pro's .stl, Adobe Encore, DVD
Architect, Scenarist, DVD Producer and Avid DVD, each layout read from a
sample shaped as the tool (and Subtitle Edit, which files are exchanged
with) writes it, written back in the same layout, drop-frame through an
hour, and told apart from each other and from every other format sharing
.txt, .sub, .stl and .sst.

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
from subtitld.modules import dvd_scripts, ebustl, file_io, legacy, markup, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(text, fmt, fps=None):
    segments, settings = dvd_scripts.parse(text, fmt, fps)
    return [(s['start'], s['end'], s['text']) for s in segments], settings


SEGMENTS = [{'start': 1.0, 'end': 3.48, 'text': 'First line\nsecond line'},
            {'start': 4.0, 'end': 6.0, 'text': 'Second cue'}]
STYLED = [{'start': 1.0, 'end': 3.0, 'text': '<i>Hello</i>\nworld'},
          {'start': 4.0, 'end': 6.0, 'text': '{\\an8}Up <b>here</b>'},
          {'start': 7.0, 'end': 8.0, 'text': '<b><i>Hello\nWorld</i></b>'}]

# --------------------------------------------------------------------------
print('DVD Studio Pro: its header, its toggles, its places')
# As DVD Studio Pro 4 exports a script: tabs around the commas.
DSP = ('$FontName       = Arial\r\n$FontSize       = 30\r\n$HorzAlign      = Center\r\n$VertAlign      = Bottom\r\n'
       '$TapeOffset     = FALSE\r\n//$SetFilePathToken  = <<:>>\r\n$ColorIndex1    = 0\r\n$ColorIndex2    = 1\r\n'
       '$ColorIndex3    = 2\r\n$ColorIndex4    = 3\r\n//Subtitles\r\n'
       '00:00:01:00\t,\t00:00:03:12\t,\t^IHello^I|world\r\n'
       '$VertAlign = Top\r\n'
       '00:00:04:00\t,\t00:00:06:00\t,\tUp ^Bhere^B\r\n'
       '$VertAlign = Bottom\r\n$HorzAlign = Left\r\n$Italic = TRUE\r\n'
       '00:00:07:00\t,\t00:00:08:00\t,\tAll slanted | two lines\r\n'
       '$Italic = FALSE\r\n$HorzAlign = Center\r\n'
       '00:00:09:00\t,\t00:00:10:00\t,\t<<Graphic>>logo.tif\r\n'
       '00:00:11:00\t,\t00:00:12:00\t,\t^UUnder^U and ^Iopen to the end\r\n')
found, settings = cues(DSP, 'dvdstudiopro', 25)
check('subtitles, their styles and places; the picture left out', found, [
    (1.0, 3.48, '<i>Hello</i>\nworld'), (4.0, 6.0, '{\\an8}Up <b>here</b>'),
    (7.0, 8.0, '{\\an1}<i>All slanted\ntwo lines</i>'), (11.0, 12.0, '<u>Under</u> and <i>open to the end</i>')])
check('the layout and the header kept', (settings['layout'], settings['header'][0], settings['header'][-1], len(settings['header'])),
      ('tab', '$FontName       = Arial', '//Subtitles', 11))
written = dvd_scripts.write([{'start': s, 'end': e, 'text': t} for s, e, t in found], 'dvdstudiopro', 25,
                            layout=settings['layout'], header=settings['header'])
check('written back under its header, the places as it says them', written.split('\n')[:3] + written.split('\n')[-9:], [
    '$FontName       = Arial', '$FontSize       = 30', '$TapeOffset     = FALSE',
    '00:00:01:00\t,\t00:00:03:12\t,\t^IHello^I|world', '$VertAlign = Top',
    '00:00:04:00\t,\t00:00:06:00\t,\tUp ^Bhere^B', '$VertAlign = Bottom', '$HorzAlign = Left',
    '00:00:07:00\t,\t00:00:08:00\t,\t^IAll slanted^I|^Itwo lines^I', '$HorzAlign = Center',
    '00:00:11:00\t,\t00:00:12:00\t,\t^UUnder^U and ^Iopen to the end^I', ''])
check('and read again the same', cues(written, 'dvdstudiopro', 25)[0], found)
# Subtitle Edit's own test: bold italic on two lines, each line toggled.
both = dvd_scripts.write([STYLED[2]], 'dvdstudiopro', 25).split('\n')[-2]
check('bold italic on two lines, no stray underline', both.split('\t')[-1], '^B^IHello^B^I|^B^IWorld^B^I')
check('and it reads back bold and italic', markup.spans(cues(both, 'dvdstudiopro')[0][0][2]),
      markup.spans(STYLED[2]['text']))
check('a new file gets the header Subtitle Edit writes', dvd_scripts.write(SEGMENTS, 'dvdstudiopro', 25).split('\n')[0],
      '$VertAlign          =   Bottom')

print('DVD Studio Pro: the five layouts, each read and written')
LINES = {
    'tab': '00:00:01:00\t,\t00:00:03:12\t,\tFirst line|second line',
    'space': '00:00:01:00 , 00:00:03:12 , First line|second line',
    'one': '00:00:01:00,00:00:03:12, First line|second line',
    'spruce': '00:00:01:00,00:00:03:12,First line|second line',
    'spruce-space': '00:00:01:00, 00:00:03:12, First line|second line',
}
for layout, line in LINES.items():
    found, settings = cues(line + '\n', 'dvdstudiopro', 25)
    check(f'{layout}: read', (found[0], settings['layout']), ((1.0, 3.48, 'First line\nsecond line'), layout))
    again = dvd_scripts.write(SEGMENTS, 'dvdstudiopro', 25, layout=layout, header=False)
    check(f'{layout}: written', again.split('\n')[0], line)
check('a comma inside the text stays', cues('00:00:01:00,00:00:02:00, Yes, sir , no\n', 'dvdstudiopro')[0][0][2], 'Yes, sir , no')

print('DVD Studio Pro: drop-frame and the rate it names')
NTSC = ('// Format: NTSC - 29.97 Drop Frame\n'
        '00:00:01;00,00:00:03;00, One\n00:01:00;02,00:01:02;00, A minute in\n01:00:00;00,01:00:02;00, An hour in\n')
found, settings = cues(NTSC, 'dvdstudiopro', None)
check('";" is drop-frame at the 29.97 the comment names', (found, settings['fps'], settings['drop']),
      ([(1.001, 3.003, 'One'), (60.06, 61.995, 'A minute in'), (3599.996, 3601.998, 'An hour in')], 29.97, True))
again = dvd_scripts.write([{'start': s, 'end': e, 'text': t} for s, e, t in found], 'dvdstudiopro', 29.97, drop=True,
                          layout='one', header=False)
check('written drop-frame again, frame for frame', again.split('\n'), NTSC.split('\n'))
check('drop-frame asked at 25 is not given', dvd_scripts.write(SEGMENTS, 'dvdstudiopro', 25, drop=True, header=False,
                                                              layout='one').split('\n')[0], LINES['one'])
check('23.976 is said, and read again', cues(dvd_scripts.write(SEGMENTS, 'dvdstudiopro', 23.976, header=False),
                                             'dvdstudiopro', 25)[1]['fps'], 23.976)
check("non-drop at 29.97 counts frames, as the tools do", cues('00:01:00:00 , 00:01:01:00 , x\n', 'dvdstudiopro', 29.97)[0][0][:2],
      (60.06, 61.061))

print('Spruce is the same script, written as Spruce wrote it')
check('legacy reads it with the same reader', [(s['start'], s['text']) for s in legacy.parse(DSP, 'spruce', 25)][:2],
      [(1.0, '<i>Hello</i>\nworld'), (4.0, '{\\an8}Up <b>here</b>')])
check('and writes " , " with no header', legacy.write(STYLED[:2], 'spruce', 25).split('\n'),
      ['00:00:01:00 , 00:00:03:00 , ^IHello^I|world', '$VertAlign = Top', '00:00:04:00 , 00:00:06:00 , Up ^Bhere^B', ''])
check('a bare Spruce file is Spruce', dvd_scripts.stl_flavour('00:00:01:00 , 00:00:03:00 , Hello\n'), 'spruce')
check('with a header it is DVD Studio Pro', dvd_scripts.stl_flavour(DSP), 'dvdstudiopro')
check('tabs, or drop-frame, too', (dvd_scripts.stl_flavour(LINES['tab']), dvd_scripts.stl_flavour(NTSC)),
      ('dvdstudiopro', 'dvdstudiopro'))
check('legacy still knows a drop-frame script for a text .stl', legacy.kind(NTSC), 'spruce')

# --------------------------------------------------------------------------
print('Adobe Encore: the layouts')
ENCORE = {
    'numbered': '1 00:00:01:00 00:00:03:12 First line\nsecond line\n2 00:00:04:00 00:00:06:00 Second cue\n',
    'plain': '00:00:01:00 00:00:03:12 First line\nsecond line\n00:00:04:00 00:00:06:00 Second cue\n',
    'tabs': '00:00:01:00\t00:00:03:12\tFirst line\nsecond line\n00:00:04:00\t00:00:06:00\tSecond cue\n',
    'linetabs': '0001\t00:00:01:00\t00:00:03:12\tFirst line//second line\n0002\t00:00:04:00\t00:00:06:00\tSecond cue\n',
    'linetabnewline': '0001 00:00:01:00 00:00:03:12\tFirst line\n\t\t\t\tsecond line\n0002 00:00:04:00 00:00:06:00\tSecond cue\n',
}
for layout, text in ENCORE.items():
    found, settings = cues(text, 'encore', 25)
    check(f'{layout}: read, sniffed', (found, settings['layout'], dvd_scripts.kind(text, 'txt')),
          ([(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')], layout, 'encore'))
    check(f'{layout}: written as it was', dvd_scripts.write(SEGMENTS, 'encore', 25, layout=layout), text)
check('"@Italic@" turns italic on and off', cues('0001\t00:00:01:00\t00:00:03:00\t@Italic@Hello@Italic@ world//Next\n',
                                                 'encore')[0][0][2], '<i>Hello</i> world\nNext')
check('and is written so', dvd_scripts.write(STYLED[:1], 'encore', 25, layout='linetabs'),
      '0001\t00:00:01:00\t00:00:03:00\t@Italic@Hello@Italic@//world\n')
ENCORE_NTSC = '1 00;00;01;00 00;00;03;00 First\n2 00;01;00;02 00;01;02;00 A minute in\n'
found, settings = cues(ENCORE_NTSC, 'encore', None)
check('NTSC: semicolons throughout are drop-frame', (found, settings['fps'], settings['drop']),
      ([(1.001, 3.003, 'First'), (60.06, 61.995, 'A minute in')], 29.97, True))
check('and are written so', dvd_scripts.write([{'start': s, 'end': e, 'text': t} for s, e, t in found], 'encore', 29.97,
                                              drop=True), ENCORE_NTSC)
check("one subtitle line each, no '//': DVDSS's, not Encore's", dvd_scripts.kind(
    '00:00:01:00 00:00:03:00 First\n00:00:04:00 00:00:06:00 Second\n', 'txt'), '')
check("'//' between lines is DVDSS's", dvd_scripts.kind('00:00:01:00 00:00:03:00 First//second\nmore\n', 'txt'), '')
check("Pinnacle's list is not Encore", dvd_scripts.kind('#INPOINT OUTPOINT PATH\n00:00:01:00 00:00:03:00 a.tga\nb\n'), '')

# --------------------------------------------------------------------------
print('DVD Architect: the layouts')
SONY = {
    'dash': '00:00:01:00 - 00:00:03:48  \tFirst line\rsecond line\n00:00:04:00 - 00:00:06:00  \tSecond cue\n',
    'tabs': '00:00:01:00\t00:00:03:48\tFirst line\rsecond line\n00:00:04:00\t00:00:06:00\tSecond cue\n',
    'numbered': '0001  00:00:01:00  00:00:03:48    \tFirst line\rsecond line\n0002  00:00:04:00  00:00:06:00    \tSecond cue\n',
    'explicit': '00:00:01.000\t00:00:03.480\t00:00:02.480\tFirst line\rsecond line\n'
                '00:00:04.000\t00:00:06.000\t00:00:02.000\tSecond cue\n',
}
for layout, text in SONY.items():
    found, settings = cues(text, 'dvdarchitect', 25)
    check(f'{layout}: read (hundredths, not frames), sniffed as .sub', (found, settings['layout'], dvd_scripts.kind(text, 'sub')),
          ([(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')], layout, 'dvdarchitect'))
    check(f'{layout}: written as it was', dvd_scripts.write(SEGMENTS, 'dvdarchitect', 25, layout=layout), text)
# The two with a header, as DVD Architect exports them (frames, at 25).
DURATION = ('Title: Tears of Steel\r\nTranslator: No Author\r\nDate: 25.08.2011\r\nDuration: 00:00:06:00\r\n'
            'Program start: 00:00:00:00\r\nTitle count: 2\r\n\r\n#\tIn\tOut\tDuration\r\n\r\n'
            '1\t00:00:01:00\t00:00:03:12\t00:00:02:12\r\nFirst line\r\nsecond line\r\n\r\n'
            '2\t00:00:04:00\t00:00:06:00\t00:00:02:00\r\nSecond cue\r\n\r\n')
for layout, text in (('duration', DURATION),
                     ('length', DURATION.replace('Duration\r\n\r\n', 'Duration\tLength\r\n\r\n')
                      .replace('00:00:02:12\r\n', '00:00:02:12\t22\r\n').replace('00:00:02:00\r\n', '00:00:02:00\t10\r\n'))):
    found, settings = cues(text, 'dvdarchitect', 25)
    check(f'{layout}: read (frames), sniffed as .txt', (found, settings['layout'], dvd_scripts.kind(text, 'txt')),
          ([(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')], layout, 'dvdarchitect'))
    again = dvd_scripts.write(SEGMENTS, 'dvdarchitect', 25, layout=layout, line_end='\r\n', title='Tears of Steel')
    check(f'{layout}: written as it was, but for the date', [line for line in again.split('\r\n') if not line.startswith('Date')],
          [line for line in text.split('\r\n') if not line.startswith('Date')])
check('Subtitle Edit\'s sample', cues('00:04:10:92 - 00:04:13:32    Raise Yourself To Help Mankind\n', 'dvdarchitect')[0],
      [(250.92, 253.32, 'Raise Yourself To Help Mankind')])
check('tabs in a .txt are Encore\'s, unless a field is past the frames', (
    dvd_scripts.kind(SONY['tabs'], 'txt'), dvd_scripts.kind(SONY['tabs'].replace('03:48', '03:12'), 'txt'),
    dvd_scripts.kind(SONY['tabs'].replace('03:48', '03:12'), 'sub')), ('dvdarchitect', 'encore', 'dvdarchitect'))

# --------------------------------------------------------------------------
print('Scenarist: the script, with its header and without')
SCENARIST = ('st_format 2\r\nDisplay_Start non_forced\r\nTV_Type PAL\r\nTape_Type NON_DROP\r\nPixel_Area (2 575)\r\n'
             'Directory C:\\subs\r\nSubtitle tears\r\nDisplay_Area (0 2 719 575)\r\nContrast (15 15 15 0)\r\n'
             '#\r\n# Palette entries:\r\n#\r\nPA (0 0 255 ===)\r\nE1 (255 0 0 ===)\r\nE2 (0 0 0 ===)\r\nBG (255 255 255 ===)\r\n'
             '#\r\nSP_NUMBER\tSTART\tEND\tFILE_NAME\r\n'
             '0001\t00:00:01:00\t00:00:03:12\tFirst line\tsecond line\r\n0002\t00:00:04:00\t00:00:06:00\tSecond cue\r\n')
found, settings = cues(SCENARIST, 'scenarist', 29.97)
check('read at the PAL its header names, a tab between lines', (found, settings['fps'], settings['drop'], len(settings['header'])),
      ([(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')], 25.0, False, 18))
check('sniffed, as .sst and .txt', (dvd_scripts.kind(SCENARIST, 'sst'), dvd_scripts.kind(SCENARIST, 'txt')),
      ('scenarist', 'scenarist'))
check('written back under its header', dvd_scripts.write(SEGMENTS, 'scenarist', 25, header=settings['header'], line_end='\r\n'),
      SCENARIST)
BARE = '0001\t00:00:28:16\t00:00:31:04\tJeg vil lære jer\tfrygten for HERREN.\n0002\t00:00:32:00\t00:00:34:00\tNext\n'
check('Subtitle Edit\'s layout, no header', (dvd_scripts.kind(BARE, 'txt'), cues(BARE, 'scenarist', 25)[0][0]),
      ('scenarist', (28.64, 31.16, 'Jeg vil lære jer\nfrygten for HERREN.')))
check('NTSC and DROP in the header', cues(SCENARIST.replace('PAL', 'NTSC').replace('NON_DROP', 'DROP'), 'scenarist')[1]['drop'], True)
PICTURES = SCENARIST.replace('First line\tsecond line', 'tears_0001.tif').replace('Second cue', 'tears_0002.tif')
check('a script of pictures is said to be one', (dvd_scripts.is_picture_script(PICTURES), dvd_scripts.is_picture_script(SCENARIST)),
      (True, False))

# --------------------------------------------------------------------------
print('DVD Producer: columns')
PRODUCER = ('1 00:01:00:00        00:01:19:00 Subtitle line 1\r\n'
            '                                 Subtitle line 2\r\n'
            '2 00:01:20:00        00:01:29:00 Another subtitle\r\n'
            '3 00:01:30:00        00:01:39:00 Third subtitle\r\n')
check('the user guide\'s sample (Subtitle Edit\'s test)', (dvd_scripts.kind(PRODUCER, 'txt'), cues(PRODUCER, 'dvdproducer')[0]),
      ('dvdproducer', [(60.0, 79.0, 'Subtitle line 1\nSubtitle line 2'), (80.0, 89.0, 'Another subtitle'),
                       (90.0, 99.0, 'Third subtitle')]))
written = dvd_scripts.write(SEGMENTS, 'dvdproducer', 25)
check('written in columns, the second line under the first', written.split('\n')[:2],
      ['1    00:00:01:00  00:00:03:12  First line', ' ' * 31 + 'second line'])
check('and read again as itself', (dvd_scripts.kind(written, 'txt'), cues(written, 'dvdproducer', 25)[0][0]),
      ('dvdproducer', (1.0, 3.48, 'First line\nsecond line')))
check('single spaces are Encore\'s', dvd_scripts.kind('1 00:01:00:00 00:01:19:00 Subtitle line 1\nSubtitle line 2\n'
                                                     '2 00:01:20:00 00:01:29:00 Another subtitle\n', 'txt'), 'encore')

# --------------------------------------------------------------------------
print('Avid DVD')
AVID = ('25\t10:03:20:23\t10:03:23:05\tsome text\r\nI see, on my way.|New line also.\r\n\r\n'
        '$Italic = TRUE\r\n26\t10:03:31:18\t10:03:34:00\teven more text\r\nPanessa, why didn\'t they give them\r\n'
        'an escape route ?\r\n\r\n$Italic = FALSE\r\n27\t10:03:35:00\t10:03:36:00\tLast\r\n')
check('Subtitle Edit\'s sample, more lines under the times, italics around', (dvd_scripts.kind(AVID, 'txt'), cues(AVID, 'aviddvd', 25)[0]),
      ('aviddvd', [(36200.92, 36203.2, 'some text\nI see, on my way.\nNew line also.'),
                   (36211.72, 36214.0, "<i>even more text\nPanessa, why didn't they give them\nan escape route ?</i>"),
                   (36215.0, 36216.0, 'Last')]))
written = dvd_scripts.write(STYLED, 'aviddvd', 25)
check('written with "|" and $Italic', written.split('\n'), [
    '1\t00:00:01:00\t00:00:03:00\tHello|world', '', '2\t00:00:04:00\t00:00:06:00\tUp here', '',
    '$Italic = TRUE', '3\t00:00:07:00\t00:00:08:00\tHello|World', '', ''])
check('read again as itself', (dvd_scripts.kind(written, 'txt'), cues(written, 'aviddvd', 25)[0][2]),
      ('aviddvd', (7.0, 8.0, '<i>Hello\nWorld</i>')))

# --------------------------------------------------------------------------
print('every layout written is read again as its format and layout')
for fmt, layouts in dvd_scripts.LAYOUTS.items():
    for layout in layouts:
        text = dvd_scripts.write(SEGMENTS, fmt, 25, layout=layout)
        extension = {'dvdarchitect': 'sub', 'scenarist': 'sst'}.get(fmt, 'txt')
        if layout in ('duration', 'length'):
            extension = 'txt'
        sniffed = dvd_scripts.stl_flavour(text) and 'dvdstudiopro' if fmt == 'dvdstudiopro' else dvd_scripts.kind(text, extension)
        found, settings = cues(text, fmt, 25)
        check(f'{fmt} {layout}', (sniffed, settings['layout'], [(s, e, markup.plain(t)) for s, e, t in found]),
              (fmt, layout, [(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')]))

# --------------------------------------------------------------------------
print('through file_io')
folder = Path(tempfile.mkdtemp())


def opened(name, data, video=None):
    path = folder / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    session.FORMAT = {}
    session.VIDEO = dict(video or {})
    return file_io.process_subtitles_file(str(path))


segments, fmt = opened('dsp.stl', DSP.encode('utf-16'))
check('a UTF-16 .stl with a header opens as DVD Studio Pro, its layout, header and encoding kept',
      (fmt, len(segments), session.FORMAT['options']['script_layout'], session.FORMAT['options']['encoding'],
       session.FORMAT['options']['script_header'][0]), ('DVDSTUDIOPRO', 4, 'tab', 'utf-16', '$FontName       = Arial'))
session.SUBTITLE['segments'] = segments
file_io.save_file(str(folder / 'dsp-again.stl'), 'DVDSTUDIOPRO')
again = (folder / 'dsp-again.stl').read_bytes()
check('saved again in UTF-16, under its header, in tabs', (again[:2], again.decode('utf-16').split('\n')[0],
                                                           again.decode('utf-16').count('\t,\t')),
      (b'\xff\xfe', '$FontName       = Arial', 8))
check('and opens as it did', [s['text'] for s in opened('dsp-again.stl', again)[0]], [s['text'] for s in segments])
check('a bare .stl stays Spruce', opened('spruce.stl', '00:00:01:00 , 00:00:03:00 , Spruce here\n')[1], 'SPRUCE')
check('drop-frame .stl at a 29.97 video', (opened('ntsc.stl', NTSC, {'framerate': 29.97})[1],
                                           session.FORMAT['options']['timecode'], session.FORMAT['options']['fps']),
      ('DVDSTUDIOPRO', 'drop', 29.97))
session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 3.0, 'text': 'EBU'}]
session.FORMAT = {}
file_io.save_file(str(folder / 'ebu.stl'), 'STL')
check('EBU STL is still EBU STL', (ebustl.is_stl((folder / 'ebu.stl').read_bytes()[:1024]),
                                   file_io.process_subtitles_file(str(folder / 'ebu.stl'))[1]), (True, 'STL'))

for name, text, want in (('encore.txt', ENCORE['numbered'], 'ENCORE'), ('encore-ntsc.txt', ENCORE_NTSC, 'ENCORE'),
                         ('sony.sub', SONY['dash'], 'DVDARCHITECT'), ('sony-tabs.sub', SONY['tabs'], 'DVDARCHITECT'),
                         ('sony-explicit.sub', SONY['explicit'], 'DVDARCHITECT'),
                         ('sony-numbered.sub', SONY['numbered'], 'DVDARCHITECT'),
                         ('sony-duration.txt', DURATION, 'DVDARCHITECT'), ('scenarist.sst', SCENARIST, 'SCENARIST'),
                         ('scenarist.txt', BARE, 'SCENARIST'), ('producer.txt', PRODUCER, 'DVDPRODUCER'),
                         ('avid.txt', AVID, 'AVIDDVD')):
    check(f'{name} opens as {want}', opened(name, text)[1], want)
try:
    opened('pictures.sst', PICTURES)
    check('a Scenarist script of pictures says so', None, 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError as error:
    check('a Scenarist script of pictures says so', 'pictures' in str(error), True)

print('saved and opened again, through file_io, in every format')
for name in dvd_scripts.SESSION_FORMATS:
    extension = session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS[name]['extensions'][0]
    path = folder / f'out-{name}.{extension}'
    session.SUBTITLE['segments'] = [dict(segment) for segment in SEGMENTS]
    session.VIDEO = {'framerate': 25.0, 'filepath': '/videos/tears.mov'}
    session.FORMAT = {}
    file_io.save_file(str(path), name)
    back, found = file_io.process_subtitles_file(str(path))
    check(f'{name}', (found, [(s['start'], s['end'], s['text']) for s in back]),
          (name, [(1.0, 3.48, 'First line\nsecond line'), (4.0, 6.0, 'Second cue')]))
# A file opened keeps its layout for the next save.
opened('encore-tabs.txt', ENCORE['linetabnewline'])
session.SUBTITLE['segments'] = [dict(segment) for segment in SEGMENTS]
file_io.save_file(str(folder / 'encore-again.txt'), 'ENCORE')
check('Encore saved again in the layout it came in', (folder / 'encore-again.txt').read_text(encoding='utf-8'),
      ENCORE['linetabnewline'])
opened('ntsc.txt', ENCORE_NTSC, {'framerate': 29.97})
session.SUBTITLE['segments'] = [{'start': 1.001, 'end': 3.003, 'text': 'First'}]
file_io.save_file(str(folder / 'ntsc-again.txt'), 'ENCORE')
check('and drop-frame, as it came', (folder / 'ntsc-again.txt').read_text(encoding='utf-8'), ENCORE_NTSC.split('\n')[0] + '\n')

print('every other format sharing these extensions still opens as itself')
for name, fmt in sorted(legacy.SESSION_FORMATS.items()):
    extension = legacy.EXTENSIONS[fmt]
    if extension not in ('txt', 'sub', 'stl', 'sst'):
        continue
    path = folder / f'legacy-{fmt}.{extension}'
    session.SUBTITLE['segments'] = [{'start': 1.0, 'end': 3.5, 'text': 'First line\nsecond line'},
                                    {'start': 4.0, 'end': 6.25, 'text': 'Second cue'}]
    session.VIDEO = {'framerate': 25.0}
    session.FORMAT = {}
    file_io.save_file(str(path), name)
    check(f'.{extension}: {name}', file_io.process_subtitles_file(str(path))[1], name)
for name, text, want in (('micro.sub', '{25}{75}MicroDVD here\n', 'SUB'),
                         ('avid-subcap.txt', '@ This file written with the Avid Caption plugin, version 1\n\n<begin subtitles>\n'
                          '00:00:01:00 00:00:03:00\nSubCap here\nand here\n\n<end subtitles>\n', 'AVID'),
                         ('dvdss.txt', '00:00:01:00 00:00:03:00 DVD Subtitle System//two\n00:00:04:00 00:00:06:00 Second\n', 'DVDSS'),
                         ('ultrastar.txt', '#TITLE:Song\n#ARTIST:Someone\n#BPM:300\n#GAP:0\n: 0 4 5 Hel\n: 4 4 5 lo\nE\n',
                          'ULTRASTAR')):
    check(f'{name} is {want}', opened(name, text)[1], want)
check('a transcript is no script', dvd_scripts.kind('Just some words.\nAnd more, 00:00:01:00 too.\n', 'txt'), '')

# --------------------------------------------------------------------------
print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.SUBTITLE['segments'] = [dict(segment) for segment in SEGMENTS]
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('DVDSTUDIOPRO')
panel._refresh_options()
options = panel.get_config()['options']
check("DVD Studio Pro: the rate (the video's), drop-frame, encoding, formatting", (
    panel.sub_options.isHidden(), panel.scc_options.isHidden(), panel.srt_options.isHidden(), panel.formatting_combo.isHidden(),
    {key: options.get(key) for key in ('fps', 'timecode', 'encoding')}),
      (False, False, False, False, {'fps': 29.97, 'timecode': 'drop', 'encoding': 'utf-8'}))
for name in ('ENCORE', 'DVDARCHITECT', 'SCENARIST', 'DVDPRODUCER', 'AVIDDVD'):
    panel.format_combo.setCurrentText(name)
    panel._refresh_options()
    check(f'{name}: the rate and the encoding; drop-frame for Encore alone', (
        panel.sub_options.isHidden(), panel.srt_options.isHidden(), panel.scc_options.isHidden(),
        'fps' in panel.get_config()['options']), (False, False, name != 'ENCORE', True))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
