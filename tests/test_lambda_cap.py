"""Lambda CAP: reading screens laid out as Subtitle Edit writes them and as
cap2tt's test files have them (Shift-JIS, UTF-8 with and without a byte
order mark, UTF-16; codes after tabs or spaces; ruby, emphasis marks,
tate-chū-yoko, italics, nested; placements, alignments and the codes that
hold; a screen without times; the space escapes; the wide dash), writing
what both readers take, round trips, telling it from Cheetah's .cap, and
the file and the export dialog.

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
from subtitld.modules import cheetahcap, file_io, lambdacap, markup, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


HEADER_NDF = 'Lambda字幕V4\tDF0+1\tSCENE"和文標準"'
HEADER_DF = 'Lambda字幕V4\tDF1+1\tSCENE"和文標準"'


def texts(text, fps=None):
    return [(s['start'], s['end'], s['text'], s.get('writing', '')) for s in lambdacap.parse(text, fps)[0]]


# Screens as Subtitle Edit's tests write them (codes in fields of their own)
# and as cap2tt's test files do (codes after a space, a screen without
# times, the codes that hold).
SAMPLE = '\r\n'.join([
    HEADER_NDF, '',
    '1\t00000100/00000215\t中国 ＠ルビ上［成都｜せいと］＠\t＠横下\t＠中頭',
    '2\t00000300/00000400\tでも＠ルビ上［誰｜↓］＠が恋しいのよ\t＠横下\t＠中頭',
    '3\t00000500/00000600\t＠組［12］＠時間\t＠縦右',
    '4\t00000700/00000800\t中国\t＠横下\t＠中頭\t＠斜３',
    '\t\t\t\t24時間 耐久洗礼式',
    '5\t00000900/00001000\t＠斜３［ABC］＠ですね\t＠横上',
    '6\t00001100/00001200\tテスト\t＠縦左 ＠行頭',
    '7\t00001300/00001400\tテスト ＠縦右 ＠行頭',
    '8\t00001500/00001600\t兵士は＠幅広［―］＠\t＠横下\t＠謎コード',
    '9\t00001700/00001800\t左\t＠横下 ＠行頭',
    '10\t00001900/00002000\t画面上\t＠横中',
    '\t\t\t\t右\t＠行末',
    '',
])

print('reading')
check('ruby, a mark-only reading as emphasis, tate-chu-yoko, italics, the places', texts(SAMPLE), [
    (1.0, 2.5, '中国 <ruby position="before">成都<rt>せいと</rt></ruby>', ''),
    (3.0, 4.0, 'でも<font emphasis="filled dot before">誰</font>が恋しいのよ', ''),
    (5.0, 6.0, '{\\an6}<font combine="all">12</font>時間', 'vertical-rl'),
    (7.0, 8.0, '<i>中国\n24時間 耐久洗礼式</i>', ''),
    (9.0, 10.0, '{\\an8}<i>ABC</i>ですね', ''),
    (11.0, 12.0, '{\\an7}テスト', 'vertical-rl'),
    (13.0, 14.0, '{\\an9}テスト', 'vertical-rl'),
    (15.0, 16.0, '兵士は⸺', ''),
    (17.0, 18.0, '{\\an1}左', ''),
    (19.0, 20.0, '{\\an6}画面上\n右', '')])
check('its settings: 30 without a video, not drop-frame', lambdacap.parse(SAMPLE)[1], {'fps': 30.0, 'drop': False})
check("at the video's rate", lambdacap.parse(SAMPLE, 25)[0][0]['end'], 2.6)
check('DF1: drop-frame at 29.97 whatever the video',
      (lambdacap.parse(HEADER_DF + '\n\n1\t00100000/00100010\tx', 25)[0][0]['start'], lambdacap.parse(HEADER_DF + '\n', 25)[1]),
      (round(17982 * 1001 / 30000, 3), {'fps': 29.97, 'drop': True}))

# cap2tt's test files: below, left, right and the plain ＠ルビ; emphasis by
# its side; nesting; the codes that hold; a screen shown with the one before.
CAP2TT = '\n'.join([
    HEADER_NDF, '',
    '1\t00000100/00000200\t＠ルビ下［Ruby｜below］＠\t＠横下',
    '2\t00000200/00000300\t＠ルビ左［Ruby｜left］＠\t＠縦右',
    '3\t00000300/00000400\t＠ルビ［Ruby｜auto］＠\t＠横下',
    '4\t00000400/00000500\t＠ルビ右［Emphasis｜・・・］＠\t＠縦右',
    '5\t00000500/00000600\t＠ルビ右［right｜↓↓↑↑↑］＠',
    '6\t00000600/00000700\ttext ＠斜３［＠ルビ上［nest｜ruby］＠］＠ text',
    '7\t00000700/00000800\tvertical\t＠縦右！ ＠中頭！',
    '8\t00000800/00000900\tstill vertical',
    '9\t00000900/00001000\tacross again\t＠横下！',
    '10\t00001000/00001100\t▽一緒に',
    '11\t\t\t\tもう一つ',
    '12\t00001200/00001300\t_h_a_l_f_＿f＿ ＠_＠＠＿＠',
    '13\t00001300/00001400\t斜体\t＠斜',
    '14\t00001400/00001500\t正体\t＠斜０',
])
check("cap2tt's: positions, emphasis by side, nesting, held codes, a screen without times, escapes", texts(CAP2TT), [
    (1.0, 2.0, '<ruby position="after">Ruby<rt>below</rt></ruby>', ''),
    (2.0, 3.0, '{\\an6}<ruby position="after">Ruby<rt>left</rt></ruby>', 'vertical-rl'),
    (3.0, 4.0, '<ruby>Ruby<rt>auto</rt></ruby>', ''),
    (4.0, 5.0, '{\\an6}<font emphasis="filled dot before">Emphasis</font>', 'vertical-rl'),
    (5.0, 6.0, '<font emphasis="filled dot">right</font>', ''),
    (6.0, 7.0, 'text <ruby position="before"><i>nest</i><rt><i>ruby</i></rt></ruby> text', ''),
    (7.0, 8.0, '{\\an6}vertical', 'vertical-rl'),
    (8.0, 9.0, '{\\an6}still vertical', 'vertical-rl'),
    (9.0, 10.0, 'across again', ''),
    (10.0, 11.0, '▽一緒に', ''),
    (10.0, 11.0, 'もう一つ', ''),
    (12.0, 13.0, ' h a l f \u3000f\u3000 _＿', ''),
    (13.0, 14.0, '<i>斜体</i>', ''),
    (14.0, 15.0, '正体', '')])
check('two rubies in a row stay two', texts(HEADER_NDF + '\n1\t00000100/00000200\t＠ルビ左［久住｜くすみ］＠＠ルビ左［昌之｜まさゆき］＠が\t＠縦左')[0][2],
      '{\\an4}<ruby position="after">久住<rt>くすみ</rt></ruby><ruby position="after">昌之<rt>まさゆき</rt></ruby>が')
check('a half-width @ is text (cap2tt)', texts(HEADER_NDF + '\n1\t00000100/00000200\thalf-width\t@斜５')[0][2], 'half-width @斜５')
try:
    lambdacap.parse('1\t00000100/00000200\tno header')
    check('not Lambda CAP: refused', None, 'ValueError')
except ValueError as error:
    check('not Lambda CAP: refused', str(error).startswith('Not a Lambda CAP file'), True)

print('encodings')
for encoding, data in (('cp932', SAMPLE.encode('cp932')), ('utf-8', SAMPLE.encode('utf-8')),
                       ('utf-8-sig', SAMPLE.encode('utf-8-sig')), ('utf-16', SAMPLE.encode('utf-16')),
                       ('utf-16-le, no mark', SAMPLE.encode('utf-16-le'))):
    text, found = lambdacap.decode(data)
    check(f'{encoding}: told, read the same', (lambdacap.is_lambda(data), found.split('-le')[0], texts(text) == texts(SAMPLE)),
          (True, {'utf-16-le, no mark': 'utf-16'}.get(encoding, encoding), True))
check("the first bytes cut inside a character: still told", lambdacap.is_lambda(SAMPLE.encode('utf-8')[:13]), True)
cheetah = cheetahcap.write([{'start': 1.0, 'end': 2.0, 'text': 'Hello'}])
check("Cheetah's .cap is not Lambda's, nor Lambda's Cheetah's",
      (lambdacap.is_lambda(cheetah), cheetahcap.is_cap(SAMPLE.encode('utf-8').ljust(256, b' '))), (False, False))
check('nor is any other text', lambdacap.is_lambda(b'1\t00000100/00000200\tLambda'), False)

print('writing')
written = lambdacap.write([{'start': 0, 'end': 1, 'text': '中国 <ruby position="before">成都<rt>せいと</rt></ruby>'}])
check("a header and a screen as Subtitle Edit writes them: codes in fields, CRLF", written,
      HEADER_NDF + '\r\n\r\n1\t00000000/00000100\t中国 ＠ルビ上［成都｜せいと］＠\t＠横下\t＠中頭\r\n')
check('italic all through: ＠斜３ for the screen, the next line after tabs',
      lambdacap.write([{'start': 0, 'end': 1, 'text': '<i>中国\n24時間</i>'}]).split('\r\n')[2:4],
      ['1\t00000000/00000100\t中国\t＠横下\t＠中頭\t＠斜３', '\t\t\t\t24時間'])
check('drop-frame at 29.97: DF1 and the timecode it counts',
      lambdacap.write([{'start': 600.0, 'end': 601.0, 'text': 'x'}], fps=29.97).split('\r\n')[::2][:2],
      [HEADER_DF, '1\t00100000/00100100\tx\t＠横下\t＠中頭'])
check('non-drop asked at 29.97: DF0', lambdacap.write([{'start': 0, 'end': 1, 'text': 'x'}], fps=29.97, drop=False)[:22],
      HEADER_NDF[:22])
places = {keypad: lambdacap.write([{'start': 0, 'end': 1, 'text': f'{{\\an{keypad}}}x'}]).split('\r\n')[2].split('\tx\t')[1]
          for keypad in range(1, 10)}
check('across: the nine places', places, {
    1: '＠横下\t＠行頭', 2: '＠横下\t＠中頭', 3: '＠横下\t＠行末', 4: '＠横中\t＠行頭', 5: '＠横中\t＠中頭',
    6: '＠横中\t＠行末', 7: '＠横上\t＠行頭', 8: '＠横上\t＠中頭', 9: '＠横上\t＠行末'})
columns = {keypad: lambdacap.write([{'start': 0, 'end': 1, 'text': f'{{\\an{keypad}}}x', 'writing': 'vertical-rl'}])
           .split('\r\n')[2].split('\tx\t')[1] for keypad in range(1, 10)}
check('down: the column and where it starts', columns, {
    1: '＠縦左\t＠行末', 2: '＠縦中\t＠行末', 3: '＠縦右\t＠行末', 4: '＠縦左', 5: '＠縦中', 6: '＠縦右',
    7: '＠縦左\t＠行頭', 8: '＠縦中\t＠行頭', 9: '＠縦右\t＠行頭'})
check('emphasis a mark a character, on the side the writing has; tate-chu-yoko; inline italics',
      [lambdacap.write([{'start': 0, 'end': 1, 'text': text, 'writing': writing}]).split('\r\n')[2].split('\t')[2]
       for text, writing in (('<font emphasis="filled sesame">誰 か</font>', ''),
                             ('<font emphasis="filled dot after">誰</font>', 'vertical-rl'),
                             ('<font combine="all">12</font>時', 'vertical-rl'),
                             ('<i>ABC</i>です<i>ね</i>', ''),
                             ('<i>小野<ruby position="after">大輔<rt>だいすけ</rt></ruby></i>が', 'vertical-rl'))],
      ['＠ルビ上［誰｜・］＠ ＠ルビ上［か｜・］＠', '＠ルビ左［誰｜・］＠', '＠組［12］＠時', '＠斜３［ABC］＠です＠斜３［ね］＠',
       '＠斜３［小野＠ルビ左［大輔｜だいすけ］＠］＠が'])
check('characters that are codes escaped; the two-em dash the wide one; what Lambda has not left out',
      lambdacap.write([{'start': 0, 'end': 1, 'text': 'a_b＿c＠d⸺ <b>bold</b> <font color="#ff0000">red</font>'}]).split('\r\n')[2].split('\t')[2],
      'a＠_＠b＠＿＠c＠＠＠d＠幅広［―］＠ bold red')

print('round trips')
trip = [
    {'start': 1.0, 'end': 2.5, 'text': '中国 <ruby position="before">成都<rt>せいと</rt></ruby>'},
    {'start': 3.0, 'end': 4.0, 'text': 'でも<font emphasis="filled dot before">誰</font>が恋しい'},
    {'start': 5.0, 'end': 6.0, 'text': '{\\an9}<font combine="all">12</font>月<ruby position="after">5日<rt>いつか</rt></ruby>',
     'writing': 'vertical-rl'},
    {'start': 7.0, 'end': 8.0, 'text': '<i>全部 斜体\n二行目</i>'},
    {'start': 9.0, 'end': 10.0, 'text': '{\\an7}一部<i>斜体</i><ruby position="before"><i>漢字</i><rt><i>かんじ</i></rt></ruby>'},
    {'start': 11.0, 'end': 12.0, 'text': '{\\an4}左の列', 'writing': 'vertical-rl'},
    {'start': 13.0, 'end': 14.0, 'text': '{\\an3}a_b＠c ⸺ 二つ　空白'},
]
check('at 30', lambdacap.parse(lambdacap.write(trip))[0], trip)
check('at 29.97, drop-frame', lambdacap.parse(lambdacap.write(trip, fps=29.97), 29.97)[0],
      [dict(s, start=round(round(s['start'] * 30000 / 1001) * 1001 / 30000, 3),
            end=round(round(s['end'] * 30000 / 1001) * 1001 / 30000, 3)) for s in trip])
check('written again as it was read', lambdacap.write(lambdacap.parse(lambdacap.write(trip))[0]), lambdacap.write(trip))
check('what it loses: an emphasis shape and the auto position, a column from the left', texts(lambdacap.write([
    {'start': 0, 'end': 1, 'text': '<font emphasis="open circle">誰</font><ruby>字<rt>じ</rt></ruby>'},
    {'start': 1, 'end': 2, 'text': '{\\an4}x', 'writing': 'vertical-lr'}])),
    [(0.0, 1.0, '<font emphasis="filled dot before">誰</font><ruby position="before">字<rt>じ</rt></ruby>', ''),
     (1.0, 2.0, '{\\an4}x', 'vertical-rl')])
check('the plain text sees no codes', markup.plain(lambdacap.parse(SAMPLE)[0][0]['text']), '中国 成都')

print('through file_io')
folder = Path(tempfile.mkdtemp())
(folder / 'in.cap').write_bytes(SAMPLE.encode('cp932'))
session.FORMAT = {}
session.VIDEO = {}
opened, fmt = file_io.process_subtitles_file(str(folder / 'in.cap'))
check('opens as Lambda CAP, Shift-JIS, at 30; the rate, timecode and encoding kept',
      (fmt, len(opened), opened[2].get('writing'), session.FORMAT['options']),
      ('LAMBDACAP', 10, 'vertical-rl', {'fps': 30.0, 'timecode': 'non-drop', 'lambda_encoding': 'cp932'}))
session.SUBTITLE['segments'] = opened
file_io.save_file(str(folder / 'again.cap'), 'LAMBDACAP')
check('saved again in its encoding, and read back the same',
      ((folder / 'again.cap').read_bytes().startswith('Lambda字幕V4'.encode('cp932')),
       lambdacap.read(str(folder / 'again.cap'))[0] == [{key: value for key, value in s.items() if key != 'id'} for s in opened]),
      (True, True))
(folder / 'cheetah.cap').write_bytes(cheetah)
opened, fmt = file_io.process_subtitles_file(str(folder / 'cheetah.cap'))
check("a Cheetah .cap still opens as Cheetah's", (fmt, opened[0]['text']), ('CAP', 'Hello'))
session.SUBTITLE['segments'] = trip
session.VIDEO = {'framerate': 29.97, 'filepath': '/videos/eiga.mov'}
session.FORMAT = {'options': {}}
file_io.save_file(str(folder / 'out.cap'), 'LAMBDACAP')
saved = (folder / 'out.cap').read_bytes()
check("saved: UTF-8 with a byte order mark, drop-frame at the video's 29.97, read back",
      (saved[:3], saved[3:].split(b'\t')[1], lambdacap.read(str(folder / 'out.cap'))[0][0]['text']),
      (b'\xef\xbb\xbf', b'DF1+1', trip[0]['text']))
check('the extension is .cap, after Cheetah\'s', (session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['LAMBDACAP']['extensions'],
                                                  list(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS).index('CAP')
                                                  < list(session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS).index('LAMBDACAP')),
      (['cap'], True))

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
session.VIDEO = {'framerate': 29.97}
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('LAMBDACAP')
panel._refresh_options()
config = panel.get_config()['options']
check("LAMBDACAP: the frame rate (the video's) and the timecode", (panel.sub_options.isHidden(), panel.scc_options.isHidden(),
      config.get('fps'), config.get('timecode')), (False, False, 29.97, 'drop'))

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
