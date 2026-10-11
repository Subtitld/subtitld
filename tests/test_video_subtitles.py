"""Subtitles in the video: the subtitle tracks opening a video lists from its
probe (text, pictures, closed captions; language, title, default, forced),
with nothing run to list them; a track written out by FFmpeg (copied when
it is in its file's format already, converted otherwise; never a question
on a terminal, a file of its own each time) and read as Subtitld opens
such a file, the project's format and origin kept. Then the "Subtitles in
the video" engine: in a thread with progress, cancelled with FFmpeg
stopped, a run replaced by the next; and its panel: the tracks of the
video open, the one offered first, refreshed for another video, those
that cannot be read, nothing cleared when there is nothing to read, the
range chosen, the track's language the project's when it replaces every
subtitle; its place among the engines and its Start button.

The videos are made in a temporary folder with FFmpeg (lavfi, and SRT,
ASS and WebVTT written here): an MKV with English, Portuguese and Spanish
tracks, an MP4 with mov_text, one with no subtitles. The checks that need
them are left out (said so) where FFmpeg is not installed.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile, time, types
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QDir, QEventLoop, QThread, QTimer
from PySide6.QtWidgets import (QApplication, QLabel, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)
app = QApplication([])
from subtitld.modules import session
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
session.set_unsaved = lambda *a, **k: None
from subtitld.modules import file_io, video_subtitles
from subtitld.modules.addons.builtin import subtitles_provider

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def stream(index, codec, language=None, title=None, default=0, forced=0, kind='subtitle'):
    tags = {key: value for key, value in (('language', language), ('title', title)) if value is not None}
    return {'index': index, 'codec_type': kind, 'codec_name': codec, 'tags': tags,
            'disposition': {'default': default, 'forced': forced}}


def brief(tracks):
    return [(t['index'], t['codec'], t['kind'], t['language'], t['language_tag'], t['title'], t['default'], t['forced'])
            for t in tracks]


def leftovers(folder):
    return sorted(name for name in os.listdir(folder) if name.startswith('subtitld_track_'))


print('the tracks a probe lists')
probe = {'streams': [stream(0, 'h264', kind='video'), stream(1, 'aac', 'eng', kind='audio'),
                     stream(2, 'hdmv_pgs_subtitle', 'eng', default=1), stream(3, 'subrip', 'und', 'Signs'),
                     stream(4, 'dvd_subtitle', 'fre'), stream(5, 'eia_608'), stream(6, 'ttml', 'eng'),
                     stream(7, 'dvb_teletext', 'ger'), stream(8, 'mov_text', 'pt-BR', forced=1)]}
probe['streams'][3]['tags'] = {'LANGUAGE': 'und', 'TITLE': 'Signs'}
check('subtitle streams only, those it can read and those it cannot say why; TTML and teletext left out',
      brief(video_subtitles.tracks(probe, language_of=file_io._known_language)),
      [(2, 'hdmv_pgs_subtitle', 'pictures', 'en-us', 'eng', '', True, False),
       (3, 'subrip', 'text', '', '', 'Signs', False, False),
       (4, 'dvd_subtitle', 'pictures', 'fr-fr', 'fre', '', False, False),
       (5, 'eia_608', 'captions', '', '', '', False, False),
       (8, 'mov_text', 'text', 'pt-br', 'pt-BR', '', False, True)])
check('a video with no streams: none', video_subtitles.tracks({}), [])
check('the file each is written as, copied when it is that already', [video_subtitles.output_of(codec) for codec in (
    'subrip', 'ass', 'ssa', 'webvtt', 'mov_text', 'text', 'microdvd')],
      [('.srt', 'copy'), ('.ass', 'copy'), ('.ass', 'ass'), ('.vtt', 'copy'), ('.srt', 'srt'), ('.srt', 'srt'),
       ('.srt', 'srt')])

ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (ffmpeg and ffprobe):
    print('  [SKIP] FFmpeg is not installed: no videos, no engine, no panel runs')
    print()
    print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
    sys.exit(1 if fails else 0)

session.FFMPEG_EXECUTABLE, session.FFPROBE_EXECUTABLE = ffmpeg, ffprobe
from subtitld.modules import waveform
waveform.FFPROBE_EXECUTABLE = ffprobe
folder = Path(tempfile.mkdtemp())
(folder / 'en.srt').write_text('1\n00:00:01,000 --> 00:00:02,500\nHello <i>there</i>\n\n'
                               '2\n00:00:20,000 --> 00:00:22,000\n{\\an8}At the top\n\n'
                               '3\n00:00:50,000 --> 00:00:52,000\nLast line\n', encoding='utf-8')
(folder / 'pt.ass').write_text(
    '[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\n\n[V4+ Styles]\n'
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
    'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, '
    'MarginV, Encoding\n'
    'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n'
    'Style: Sign,Arial,20,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,1,0,0,100,100,0,0,1,2,0,8,10,10,10,1\n\n'
    '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    'Dialogue: 0,0:00:03.00,0:00:04.50,Default,Ana,0,0,0,,Olá {\\i1}mundo{\\i0}\n'
    'Dialogue: 0,0:00:21.00,0:00:23.00,Sign,,0,0,0,,Placa amarela\n', encoding='utf-8')
(folder / 'es.vtt').write_text('WEBVTT\n\n00:00:05.000 --> 00:00:06.000 line:0\nHola arriba\n\n'
                               '00:00:30.000 --> 00:00:31.500\nAdiós\n', encoding='utf-8')
(folder / 'mov.srt').write_text('1\n00:00:02,000 --> 00:00:03,000\nUna <i>pista</i> MP4\n\n'
                                '2\n00:00:07,000 --> 00:00:08,500\nY otra\n', encoding='utf-8')
video = ['-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=25:d=60']
run = lambda *args: subprocess.run([ffmpeg, '-nostdin', '-y', '-v', 'error', *args], check=True)
# PCM audio: AAC's priming would start the file 23 ms early, and every time with it.
run(*video, '-f', 'lavfi', '-i', 'sine=d=60', '-i', str(folder / 'en.srt'), '-i', str(folder / 'pt.ass'),
    '-i', str(folder / 'es.vtt'), '-map', '0:v', '-map', '1:a', '-map', '2:s', '-map', '3:s', '-map', '4:s',
    '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', '-c:s:0', 'srt', '-c:s:1', 'ass', '-c:s:2', 'copy',
    '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Full', '-metadata:s:s:1', 'language=por',
    '-metadata:s:s:1', 'title=Brasil', '-metadata:s:s:2', 'language=spa', '-disposition:s:0', 'default',
    '-disposition:s:2', 'forced', str(folder / 'tracks.mkv'))
run(*video, '-i', str(folder / 'mov.srt'), '-map', '0:v', '-map', '1:s', '-c:v', 'mpeg4', '-c:s', 'mov_text',
    '-metadata:s:s:0', 'language=spa', str(folder / 'mov.mp4'))
run(*video[:-1], 'color=c=black:s=160x90:r=25:d=2', '-c:v', 'mpeg4', str(folder / 'plain.mp4'))

real_popen, launched = subprocess.Popen, []


def recording_popen(*args, **kwargs):
    launched.append(list(args[0] if args else kwargs['args']))
    return real_popen(*args, **kwargs)


subprocess.Popen = recording_popen
mkv = file_io.process_video_file(str(folder / 'tracks.mkv'))
check('opening a video lists its tracks from the probe it runs anyway, and runs nothing else',
      [Path(command[0]).name for command in launched], ['ffprobe'])
subprocess.Popen = real_popen
check('an MKV: English SRT (default), Portuguese ASS, Spanish WebVTT (forced)', brief(mkv['subtitle_tracks']), [
    (2, 'subrip', 'text', 'en-us', 'eng', 'Full', True, False), (3, 'ass', 'text', 'pt-pt', 'por', 'Brasil', False, False),
    (4, 'webvtt', 'text', 'es-es', 'spa', '', False, True)])
mp4 = file_io.process_video_file(str(folder / 'mov.mp4'))
check('an MP4: its mov_text track', brief(mp4['subtitle_tracks']),
      [(1, 'mov_text', 'text', 'es-es', 'spa', '', True, False)])
plain = file_io.process_video_file(str(folder / 'plain.mp4'))
check('a video with no subtitles: no tracks', plain['subtitle_tracks'], [])

print('a track written out and read')
read = subtitles_provider.read
scratch = tempfile.mkdtemp()
launched.clear()
subprocess.Popen = recording_popen
seen = []
first = video_subtitles.extract(folder / 'tracks.mkv', 2, 'subrip', ffmpeg=ffmpeg, folder=scratch, duration=60,
                                progress=seen.append)
second = video_subtitles.extract(folder / 'tracks.mkv', 2, 'subrip', ffmpeg=ffmpeg, folder=scratch, duration=60)
subprocess.Popen = real_popen
check('FFmpeg never asks (-nostdin, -y), and the track goes to a file of its own each time',
      (all('-nostdin' in command and '-y' in command for command in launched), first != second,
       Path(first).read_text() == Path(second).read_text() != ''), (True, True, True))
check('how far it got, from 0 to 1', (bool(seen), all(0 <= share <= 1 for share in seen), max(seen) > 0.8),
      (True, True, True))
check('SRT, copied: its times, tags and position', [(s['start'], s['end'], s['text']) for s in read(first)],
      [(1.0, 2.5, 'Hello <i>there</i>'), (20.0, 22.0, '{\\an8}At the top'), (50.0, 52.0, 'Last line')])
ass_file = video_subtitles.extract(folder / 'tracks.mkv', 3, 'ass', ffmpeg=ffmpeg, folder=scratch)
check('ASS, copied: its styles kept (italic, colour, at the top), the actor its speaker',
      [(s['start'], s['text'], s.get('speaker')) for s in read(ass_file)],
      [(3.0, 'Olá <i>mundo</i>', 'Ana'), (21.0, '{\\an8}<font color="#ffff00"><i>Placa amarela</i></font>', None)])
vtt_file = video_subtitles.extract(folder / 'tracks.mkv', 4, 'webvtt', ffmpeg=ffmpeg, folder=scratch)
check('WebVTT, copied: its cue settings kept', (Path(vtt_file).suffix, [(s['start'], s['text']) for s in read(vtt_file)]),
      ('.vtt', [(5.0, '{\\an8}Hola arriba'), (30.0, 'Adiós')]))
mov_file = video_subtitles.extract(folder / 'mov.mp4', 1, 'mov_text', ffmpeg=ffmpeg, folder=scratch)
check('mov_text, converted to SRT', (Path(mov_file).suffix, [(s['start'], s['end'], s['text']) for s in read(mov_file)]),
      ('.srt', [(2.0, 3.0, 'Una <i>pista</i> MP4'), (7.0, 8.5, 'Y otra')]))
for path in (first, second, ass_file, vtt_file, mov_file):
    os.remove(path)
try:
    video_subtitles.extract(folder / 'missing.mkv', 2, 'subrip', ffmpeg=ffmpeg, folder=scratch)
    check('a video that is not there: an error', None, 'RuntimeError')
except RuntimeError:
    check('a video that is not there: an error, and no file left behind', leftovers(scratch), [])
session.FORMAT = {'format': 'USFX', 'options': {'embed_speaker_images': True}}
session.SUBTITLE = {'segments': [], 'origin': {'site': 'subtitld.cc', 'id': '42'}}
kept = video_subtitles.extract(folder / 'tracks.mkv', 3, 'ass', ffmpeg=ffmpeg, folder=scratch)
read(kept)
os.remove(kept)
check("reading a track leaves the project's format and subtitld.cc origin as they were",
      (session.FORMAT, session.SUBTITLE.get('origin')),
      ({'format': 'USFX', 'options': {'embed_speaker_images': True}}, {'site': 'subtitld.cc', 'id': '42'}))

print('the engine')
provider = subtitles_provider.get_provider()
on_main = []


def reading_where(path):
    on_main.append(QThread.currentThread() is app.thread())
    return read(path)


subtitles_provider.read = reading_where


def wait(signals, seconds=20):
    """Run the event loop until one of `signals` (name: signal) is heard;
    {name: its arguments} of those heard."""
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


def ends():
    return {'finished': provider.transcript_finished, 'error': provider.error}


shares = []
provider.progress.connect(lambda share, _message: shares.append(share))
provider.transcribe(str(folder / 'tracks.mkv'), 'en-us', {'track': 3, 'codec': 'ass', 'duration': 60})
outcome = wait(ends())
check('extracts in a thread, reads on the main thread, then hands the subtitles on',
      ([s['text'] for s in outcome.get('finished', [[]])[0]], on_main),
      (['Olá <i>mundo</i>', '{\\an8}<font color="#ffff00"><i>Placa amarela</i></font>'], [True]))
check('progress as it goes, and the file it wrote gone', (bool(shares), leftovers(session.PATH_TEMP)), (True, []))
provider.transcribe(str(folder / 'missing.mkv'), 'en-us', {'track': 2, 'codec': 'subrip', 'duration': 60})
check('a video it cannot read: an error', sorted(wait(ends())), ['error'])

print('cancelled')
slow = folder / 'slow-ffmpeg'
pid_file = folder / 'slow.pid'
slow.write_text(f'#!/bin/sh\necho $$ > "{pid_file}"\necho out_time_us=6000000\necho progress=continue\nexec sleep 30\n')
slow.chmod(0o755)
session.FFMPEG_EXECUTABLE = str(slow)
provider.transcribe(str(folder / 'tracks.mkv'), 'en-us', {'track': 2, 'codec': 'subrip', 'duration': 60})
check('a slow FFmpeg: its progress heard', wait({'progress': provider.progress}).get('progress'), (0.1, ''))
began = time.monotonic()
provider.cancel()
took = time.monotonic() - began
pid = int(pid_file.read_text())
try:
    os.kill(pid, 0)
    gone = False
except ProcessLookupError:
    gone = True
check('cancel stops FFmpeg at once, and leaves no file', (took < 2, gone, leftovers(session.PATH_TEMP)), (True, True, []))
check('and nothing is heard of that run again', wait(ends(), seconds=0.5), {})
provider.transcribe(str(folder / 'tracks.mkv'), 'en-us', {'track': 2, 'codec': 'subrip', 'duration': 60})
provider.cancel()
check('cancelled before FFmpeg even starts: nothing heard, nothing left',
      (wait(ends(), seconds=0.5), leftovers(session.PATH_TEMP)), ({}, []))
provider.transcribe(str(folder / 'tracks.mkv'), 'en-us', {'track': 2, 'codec': 'subrip', 'duration': 60})
wait({'progress': provider.progress})
session.FFMPEG_EXECUTABLE = ffmpeg
provider.transcribe(str(folder / 'tracks.mkv'), 'en-us', {'track': 4, 'codec': 'webvtt', 'duration': 60})
outcome = wait(ends())
check('a run started while one is going replaces it: only its subtitles come',
      [s['text'] for s in outcome.get('finished', [[]])[0]], ['{\\an8}Hola arriba', 'Adiós'])
check('and the one replaced is heard of no more', wait(ends(), seconds=0.5), {})

print('the panel')
from subtitld.interface import left_panel_import as lpi
from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import addons
from subtitld.modules.addons.builtin import captions_provider, import_provider
from subtitld.modules.addons.provider import SOURCE_ENGINES, TASK_ASR_TRANSCRIBE
shown = []


class Dialog:
    """utils.SimpleDialog, answered at once: what it says is kept in `shown`."""

    def __init__(self, parent=None, title=''):
        self.title = title
        self.content = QWidget()
        self.content.setLayout(QVBoxLayout())
        self.reject_button = QPushButton()

    def exec(self):
        shown.append((self.title, [label.text() for label in self.content.findChildren(QLabel)]))
        return 0

    def result(self):
        return 0


utils.SimpleDialog = Dialog
manager = addons.get_manager()
for builtin in (import_provider, captions_provider, subtitles_provider):
    manager.register_builtin(builtin.get_provider())
scope = {'range': None}
window = QWidget()
window.global_panel_import_engine_combobox = utils.LabeledComboBox()
window.global_panel_import_tabwidget = QStackedWidget(window)  # the panels' window(): where the run's range is
window.global_panel_import_start_transcription_button = QPushButton()
window.global_panel_import_start_transcription_progress = QProgressBar()
window.timeline_widget = types.SimpleNamespace(update=lambda: None, update_size=lambda: None)
window.transcription_scope = types.SimpleNamespace(current_range=lambda: scope['range'], scope='all',
                                                   selected_segment=lambda: None)
window.global_panel_import_language_combobox = utils.LabeledComboBox()
window.global_panel_import_language_combobox.addItems(list(lpi.LANGUAGE_DESCRIPTIONS))
session.CONFIG = {'transcription': {}}
session.VIDEO = mkv
session.SUBTITLE = {'segments': [], 'language': 'en-us'}
session.SPEAKERS = {}
lpi._populate_asr_addons(window)
combobox = window.global_panel_import_engine_combobox.combobox
check('right after the closed captions among the engines', [combobox.itemText(i) for i in range(combobox.count())],
      ['import', 'captions', 'subtitles'])
combobox.setCurrentText('subtitles')
lpi.global_panel_import_tabwidget_update(window)
panel = lpi._current_engine_panel(window)
check('its own panel, and a Start button that says what it does',
      (type(panel).__name__, window.global_panel_import_start_transcription_button.text()),
      ('_SubtitleTracksPanel', _('import_panel.read_subtitles')))
check('which reads "Read subtitles", not "Read captions"', (_('import_panel.read_subtitles'), _('import_panel.read_captions')),
      ('Read subtitles', 'Read captions'))
track_combo = panel.track_combo.combobox


def listed():
    return [(track_combo.itemText(i), track_combo.model().item(i).isEnabled()) for i in range(track_combo.count())]


check('the tracks of the video open: language, title, codec, flags', listed(), [
    ('1. English “Full” · SRT · default', True), ('2. Portuguese “Brasil” · ASS', True), ('3. Spanish · WebVTT · forced', True)])
check('the default track offered first', track_combo.currentData(), 2)
track_combo.setCurrentIndex(1)
track_combo.activated.emit(1)
check('the one chosen remembered by language and title', session.CONFIG['transcription'].get('subtitles_track'),
      {'language': 'por', 'title': 'Brasil'})
session.VIDEO = mp4
panel.update_callback()
check('another video opened: its tracks', (listed(), track_combo.currentData(), track_combo.isEnabled()),
      ([('1. Spanish · MP4 Timed Text · default', True)], 1, True))
session.VIDEO = file_io.process_video_file(str(folder / 'tracks.mkv'))
panel.update_callback()
check('back to one with Portuguese: the language chosen last time offered first', track_combo.currentData(), 3)
session.VIDEO = {'filepath': str(folder / 'tracks.mkv'),
                 'subtitle_tracks': video_subtitles.tracks(probe, language_of=file_io._known_language)}
panel.update_callback()
check('pictures and closed captions listed, saying why, but not to be chosen', listed(), [
    ('1. English · PGS · default · ' + _('import_panel.subtitles_pictures'), False),
    ('2. Signs · SRT', True),
    ('3. French · VobSub · ' + _('import_panel.subtitles_pictures'), False),
    ('4. ' + _('import_panel.subtitles_unknown_language') + ' · CEA-608 · ' + _('import_panel.subtitles_captions'), False),
    ('5. Portuguese (Brazil) · MP4 Timed Text · forced', True)])
check('the text track not forced offered first', track_combo.currentData(), 3)
session.VIDEO = {'filepath': str(folder / 'tracks.mkv'),
                 'subtitle_tracks': [t for t in video_subtitles.tracks(probe) if t['kind'] != 'text']}
panel.update_callback()
shown.clear()
check('pictures only: none chosen, the list open to say why, nothing to start',
      (track_combo.currentIndex(), track_combo.placeholderText(), track_combo.isEnabled(), panel.can_start(), shown),
      (-1, _('import_panel.no_subtitles_title'), True, False,
       [(_('import_panel.no_subtitles_title'), [_('import_panel.no_subtitles')])]))

session.VIDEO = plain
panel.update_callback()
session.SUBTITLE = {'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Mine'}], 'language': 'en-us'}
shown.clear()
lpi.global_panel_import_start_transcription_button_clicked(window)
check('no subtitles in the video: Start says so, and clears nothing nor runs',
      (track_combo.isEnabled(), [title for title, _texts in shown], [s['text'] for s in session.SUBTITLE['segments']],
       getattr(window, '_transcription_running', False)),
      (False, [_('import_panel.no_subtitles_title')], ['Mine'], False))


def start(range_=None):
    """Start through the button, as the user does; the run to its end."""
    scope['range'] = range_
    done = []
    heard = lambda: done.append(True)
    panel.transcript_finished.connect(heard)
    lpi.global_panel_import_start_transcription_button_clicked(window)
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if done else None)
    timer.start(20)
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    timer.stop()
    panel.transcript_finished.disconnect(heard)
    return bool(done)


session.VIDEO = mkv
session.CONFIG['transcription'].pop('subtitles_track')
panel.update_callback()
session.SUBTITLE = {'segments': [], 'language': 'en-us'}
check('Start reads the track chosen into the timeline, and the run ends',
      (start(), [(s['start'], s['text']) for s in session.SUBTITLE['segments']],
       window._transcription_running),
      (True, [(1.0, 'Hello <i>there</i>'), (20.0, '{\\an8}At the top'), (50.0, 'Last line')], False))
session.SUBTITLE = {'segments': [{'start': 5.0, 'end': 6.0, 'text': 'Before'}, {'start': 21.0, 'end': 22.0, 'text': 'Old'},
                                 {'start': 55.0, 'end': 56.0, 'text': 'After'}], 'language': 'en-us'}
check('a range: only the subtitles in it replaced',
      (start((10.0, 40.0)), [s['text'] for s in session.SUBTITLE['segments']]),
      (True, ['Before', '{\\an8}At the top', 'After']))
shown.clear()
check('a range the track has nothing in: says so, and changes nothing',
      (start((30.0, 45.0)), [s['text'] for s in session.SUBTITLE['segments']], shown),
      (True, ['Before', '{\\an8}At the top', 'After'],
       [(_('import_panel.no_subtitles_title'), [_('import_panel.no_subtitles_in_track')])]))

print('the language it gives the project')
language_combo = window.global_panel_import_language_combobox.combobox


def read_track(index, language, range_=None, source=None, segments=()):
    """Read track `index` into a project in `language` (what is spoken:
    `source`): the project's language then, what is spoken, and the
    language the panel shows."""
    track_combo.setCurrentIndex(track_combo.findData(index))
    session.SUBTITLE = {'segments': list(segments), 'language': language}
    if source:
        session.SUBTITLE['source_language'] = source
    lpi._show_language(window)
    start(range_)
    return session.SUBTITLE.get('language'), session.SUBTITLE.get('source_language'), language_combo.currentText()


check('the whole video from the Portuguese track: the subtitles are Portuguese, the audio still English, shown so',
      read_track(3, 'en-us', source='en-us'), ('pt-pt', 'en-us', 'Portuguese (Portugal)'))
check('what is spoken, never declared yet, kept as it was first', read_track(3, 'en-us'),
      ('pt-pt', 'en-us', 'Portuguese (Portugal)'))
check('"por" in a Brazilian project: still Brazilian', read_track(3, 'pt-br', source='en-us'),
      ('pt-br', 'en-us', 'Portuguese (Brazil)'))
session.CONFIG['transcription']['language'] = 'pt-br'
check("nor Portugal's when the transcription settings say Brazil", read_track(3, 'en-us', source='en-us'),
      ('pt-br', 'en-us', 'Portuguese (Brazil)'))
session.CONFIG['transcription'].pop('language')
check('a range: the other subtitles are still English, and so is the project',
      read_track(4, 'en-us', (0.0, 10.0), source='en-us', segments=[{'start': 40.0, 'end': 41.0, 'text': 'English'}]),
      ('en-us', 'en-us', 'English (United States)'))
check('(the Spanish one did come in)', [s['text'] for s in session.SUBTITLE['segments']], ['{\\an8}Hola arriba', 'English'])
session.VIDEO = dict(mkv, subtitle_tracks=[dict(t, language='', language_tag='') for t in mkv['subtitle_tracks']])
panel.update_callback()
check('a track that names no language: the project keeps its own', read_track(4, 'en-us', source='en-us'),
      ('en-us', 'en-us', 'English (United States)'))
session.VIDEO = mkv
panel.update_callback()

check('an engine that brings subtitles in, never the default transcription engine',
      ('subtitles' in SOURCE_ENGINES, manager.default_for_task(TASK_ASR_TRANSCRIBE)), (True, None))
check('nor does a run leave files behind', leftovers(session.PATH_TEMP), [])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
