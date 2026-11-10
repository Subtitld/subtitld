"""Subtitles written into a video as a track of their own: the FFmpeg
command each kind of file takes, and a round trip — an MP4 and a QuickTime
file with 3GPP timed text (mov_text) in them, a Matroska one with SRT and
ASS, a track for each translation — read back out with ffprobe and the
reader the Import panel uses (video_subtitles).

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import soft_subtitles, srt, video_subtitles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def option(command, flag):
    return [command[index + 1] for index, part in enumerate(command) if part == flag]


print('the command')
tracks = [{'path': 'a.ass', 'language': 'pt-br', 'title': 'Português (Brasil)'},
          {'path': 'b.ass', 'language': 'en-us', 'title': ''}]
mp4 = soft_subtitles.command('in.mkv', 'out.mp4', tracks, 'MP4', ffmpeg='ffmpeg')
check('MP4: the picture and sound copied, the subtitles timed text',
      (option(mp4, '-c:v'), option(mp4, '-c:a'), option(mp4, '-c:s')), (['copy'], ['copy'], ['mov_text']))
check('the video and each subtitle file mapped; no attachments in an MP4', option(mp4, '-map'),
      ['0:v?', '0:a?', '1:0', '2:0'])
check('each track names its language as ISO 639-2, and the first its title, as its handler\'s name',
      [part for part in mp4 if part.startswith(('language=', 'title=', 'handler_name='))],
      ['language=por', 'handler_name=Português (Brasil)', 'language=eng'])
check('the first the default track, the others not', option(mp4, '-disposition:s:0') + option(mp4, '-disposition:s:1'),
      ['default', '0'])
check('forced when asked', option(soft_subtitles.command('i', 'o', tracks[:1], 'MP4', forced=True), '-disposition:s:0'),
      ['default+forced'])
check('a track of no language is "und"',
      [part for part in soft_subtitles.command('i', 'o', [{'path': 'a', 'language': ''}], 'MP4') if 'language=' in part],
      ['language=und'])
mkv = soft_subtitles.command('in.mp4', 'out.mkv', tracks, 'MKV', 'ass')
check('Matroska: the subtitles copied from their file, its fonts kept, a title of its own',
      (option(mkv, '-c:s'), '0:t?' in option(mkv, '-map'), '-movflags' in mkv, 'title=Português (Brasil)' in mkv),
      (['copy'], True, False, True))
check('codecs each container takes',
      (soft_subtitles.codec_for('MP4', 'ass'), soft_subtitles.codec_for('MKV', 'ass'), soft_subtitles.codec_for('MKV'),
       soft_subtitles.codec_for('mov text')),
      ('mov_text', 'ass', 'srt', 'mov_text'))
check('which containers are this module\'s', (soft_subtitles.is_soft('mp4'), soft_subtitles.is_soft('MOV TEXT'),
                                             soft_subtitles.is_soft('MOV'), soft_subtitles.is_soft('MXF')),
      (True, True, False, False))
check('colours as the mov_text encoder reads them',
      soft_subtitles.for_mov_text(r'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a {\b1\c&H0000FF&}red{\c\b0} b'),
      r'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a {\b1\1c&H0000FF&}red{\1c&HFFFFFF&\b0} b')

print('in a video')
ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
encoders = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout if ffmpeg else ''
if not (ffmpeg and ffprobe) or 'mov_text' not in encoders:
    print('  [SKIP] FFmpeg (with mov_text) is not installed')
else:
    folder = Path(tempfile.mkdtemp())
    source = folder / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=4',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '4', '-c:v', 'mpeg4',
                    '-c:a', 'aac', str(source)], check=True)
    segments = [{'start': 0.5, 'end': 1.5, 'text': '<b>Hello</b> <font color="#ff0000">red</font>',
                 'translations': {'pt-br': 'Olá vermelho'}},
                {'start': 2.0, 'end': 3.25, 'text': '<i>Two</i>\n<u>lines</u>', 'translations': {'pt-br': 'Duas'}}]

    def probe(path):
        out = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def read_back(path, track):
        written = video_subtitles.extract(path, track['index'], track['codec'], ffmpeg=ffmpeg, folder=folder)
        try:
            return [(s['start'], s['end'], s['text']) for s in srt.read(written)] if written.endswith('.srt') \
                else open(written, encoding='utf-8').read()
        finally:
            os.remove(written)

    made = soft_subtitles.write_video(source, folder / 'out.mp4',
                                      [{'segments': segments, 'language': 'en-us', 'title': 'English'}],
                                      container='MP4', forced=True, width=320, height=240, ffmpeg=ffmpeg)
    metadata = probe(made)
    listed = video_subtitles.tracks(metadata)
    check('MP4: the picture and sound copied, beside a timed text track',
          [stream['codec_name'] for stream in metadata['streams']], ['mpeg4', 'aac', 'mov_text'])
    check('the track as the Import panel lists it: its language, default and forced',
          [(t['codec'], t['kind'], t['language_tag'], t['default'], t['forced']) for t in listed],
          [('mov_text', 'text', 'eng', True, True)])
    check('read back: the times, and the bold, italic, underline and colour the style records keep',
          read_back(made, listed[0]),
          [(0.5, 1.5, '<b>Hello</b> <font color="#ff0000">red</font>'), (2.0, 3.25, '<i>Two</i>\n<u>lines</u>')])

    made = soft_subtitles.write_video(source, folder / 'out.mov',
                                      [{'segments': segments, 'language': 'en-us', 'title': 'English'}],
                                      container='MOV TEXT', ffmpeg=ffmpeg)
    listed = video_subtitles.tracks(probe(made))
    check('QuickTime: a timed text track, not forced', [(t['codec'], t['default'], t['forced']) for t in listed],
          [('mov_text', True, False)])
    check('read back', [text for _start, _end, text in read_back(made, listed[0])],
          ['<b>Hello</b> <font color="#ff0000">red</font>', '<i>Two</i>\n<u>lines</u>'])

    from subtitld.modules import export_options
    two = [{'segments': export_options.prepare(segments, {'text': 'original'}), 'language': 'en-us', 'title': 'English'},
           {'segments': export_options.prepare(segments, {'text': 'translation', 'language': 'pt-br'}),
            'language': 'pt-br', 'title': 'Português (Brasil)'}]
    made = soft_subtitles.write_video(source, folder / 'out.mkv', two, container='MKV', codec='srt', ffmpeg=ffmpeg)
    listed = video_subtitles.tracks(probe(made))
    check('Matroska, SRT: a track for each language, titled, the first the default',
          [(t['codec'], t['language_tag'], t['title'], t['default']) for t in listed],
          [('subrip', 'eng', 'English', True), ('subrip', 'por', 'Português (Brasil)', False)])
    check('the translation read back', [text for _start, _end, text in read_back(made, listed[1])],
          ['Olá vermelho', 'Duas'])

    placed = [{'start': 0.5, 'end': 1.5, 'text': '{\\an8}On top'}]
    made = soft_subtitles.write_video(source, folder / 'ass.mkv', [{'segments': placed, 'language': 'en-us'}],
                                      container='MKV', codec='ass', width=320, height=240, ffmpeg=ffmpeg)
    listed = video_subtitles.tracks(probe(made))
    written = read_back(made, listed[0])
    check('Matroska, ASS: the track is ASS and keeps where the subtitle sits',
          (listed[0]['codec'], '{\\an8}On top' in written, 'PlayResY: 240' in written), ('ass', True, True))

    print('the export path')
    from subtitld.modules import bounce, session
    session.VIDEO = {'filepath': str(source), 'framerate': 25.0, 'duration': 4.0, 'width': 320, 'height': 240}
    session.SUBTITLE = {'segments': segments, 'language': 'pt-br'}
    session.FFMPEG_EXECUTABLE = ffmpeg
    said = []
    worker = bounce.CaptionsThread(str(folder / 'wired.mp4'), 'MP4', {'translations': False, 'speakers': 'none'})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()                                   # the thread's work, here on this one
    check('what the export runs writes the file and says it did', said, [(True, '')])
    streams = [stream for stream in probe(folder / 'wired.mp4')['streams'] if stream['codec_type'] == 'subtitle']
    check('one track, in the project\'s language, named after it',
          [(s['tags']['language'], s['tags']['handler_name']) for s in streams], [('por', 'Portuguese (Brazil)')])
    said = []
    worker = bounce.CaptionsThread(str(folder / 'both.mkv'), 'MKV', {'translations': True, 'subtitle_codec': 'ass'})
    worker.captions_finished.connect(lambda path, ok, error: said.append((ok, error)))
    worker.run()
    listed = video_subtitles.tracks(probe(folder / 'both.mkv'))
    check('a track for each translation as well, when asked', (said, [(t['codec'], t['language_tag']) for t in listed]),
          ([(True, '')], [('ass', 'por'), ('ass', 'por')]))

    try:
        soft_subtitles.write_video(folder / 'missing.mp4', folder / 'no.mp4', two, ffmpeg=ffmpeg)
        check('a video that is not there: what FFmpeg said', None, 'RuntimeError')
    except RuntimeError:
        check('a video that is not there: what FFmpeg said', True, True)
    try:
        soft_subtitles.write_video(source, folder / 'no.mp4', [], ffmpeg=ffmpeg)
        check('no tracks: an error', None, 'ValueError')
    except ValueError:
        check('no tracks: an error', True, True)
    check('the subtitle files it wrote are gone',
          [name for name in os.listdir(tempfile.gettempdir()) if name.startswith('subtitld_track_')], [])

print('the dialog')
from subtitld.modules import session
session.CONFIG.pop('export_video', None)
session.VIDEO = {'framerate': 25.0, 'duration': 10.0, 'filepath': 'video.mp4'}
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a', 'translations': {'fr-fr': 'b'}}], 'language': 'en-us'}
from subtitld.interface.export_dialog import _VideoPanel
panel = _VideoPanel()
panel.video_tabwidget.setCurrentWidget(panel.tracks_panel)
config = panel.get_config()
check('MP4 first, with no codec to choose',
      (config['format'], panel.tracks_codec_combo.isHidden(), config['captions']),
      ('MP4', True, {'subtitle_codec': 'srt', 'translations': False, 'forced': False}))
panel.tracks_format_combo.combobox.setCurrentIndex(2)
panel.tracks_codec_combo.combobox.setCurrentIndex(1)
panel._refresh_tracks()
panel.tracks_translations.setChecked(True)
config = panel.get_config()
check('Matroska: SRT or ASS, and a track for each translation',
      (config['format'], panel.tracks_codec_combo.isHidden(), config['captions']),
      ('MKV', False, {'subtitle_codec': 'ass', 'translations': True, 'forced': False}))
again = _VideoPanel()
check('remembered, on its own tab', (again.video_tabwidget.currentWidget() is again.tracks_panel,
                                     again.get_config()['format'], again.get_config()['captions']['subtitle_codec']),
      (True, 'MKV', 'ass'))
check('a file name for each', [session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS[key]['extensions'][0]
                               for key in ('MP4', 'MOV TEXT', 'MKV')], ['mp4', 'mov', 'mkv'])
session.SUBTITLE = {'segments': [{'start': 0, 'end': 1, 'text': 'a'}], 'language': 'en-us'}
plain = _VideoPanel()
check('no translations: nothing to ask', plain.tracks_translations.isHidden(), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
