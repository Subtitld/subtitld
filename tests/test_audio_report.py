"""An audio export's report (modules.audio_report): its loudness measured
as EBU R128 measures it (against FFmpeg's ebur128 meter when it is
there), its facts and problems for each mode, and the dashboard the export
dialog shows for it, measured in the background.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
import soundfile
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import audio_report, session
from subtitld.modules.segment import Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:90]!r}' + ('' if ok else f'   (want {str(want)[:90]!r})'))
    if not ok:
        fails.append(name)


print('loudness')
rate = 48000
t = np.arange(rate * 20) / rate
sine = np.stack([0.1 * np.sin(2 * np.pi * 997 * t)] * 2, 1).astype(np.float32)
measured = audio_report.loudness(sine, rate)
check('a 997 Hz sine at -20 dBFS in both channels: -20 LUFS, a -20 dBTP peak, no range',
      (round(measured['integrated'], 1), round(measured['peak'], 1), round(measured['range'], 1)), (-20.0, -20.0, 0.0))
check('the same at 44.1 kHz', round(audio_report.loudness(sine[:, :1].repeat(2, 1)[:44100 * 10], 44100)['integrated']), -20)
check('silence: nothing to measure', audio_report.loudness(np.zeros((rate * 5, 2), np.float32), rate),
      {'integrated': None, 'peak': None, 'range': None})
rng = np.random.default_rng(1)
level = np.repeat(rng.uniform(0.05, 0.6, 30), rate)
noise = ((0.3 * np.sin(2 * np.pi * 440 * np.arange(rate * 30) / rate) + 0.2 * rng.standard_normal(rate * 30)) * level)
noise[rate * 10:rate * 12] = 0
mix = np.stack([noise, 0.7 * noise], 1).astype(np.float32)
mine = audio_report.loudness(mix, rate)
if shutil.which('ffmpeg'):
    folder = tempfile.mkdtemp()
    soundfile.write(os.path.join(folder, 'mix.wav'), mix, rate, subtype='FLOAT')
    said = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', os.path.join(folder, 'mix.wav'), '-af',
                           'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    summary = said[said.rfind('Summary'):]
    theirs = [float(re.search(pattern, summary).group(1)) for pattern in
              (r'I:\s+(-?[\d.]+)', r'Peak:\s+(-?[\d.]+)', r'LRA:\s+(-?[\d.]+)')]
    check('speech-like sound: as FFmpeg\'s ebur128 measures it, within 0.3', all(
        abs(a - b) <= 0.3 for a, b in zip((mine['integrated'], mine['peak'], mine['range']), theirs)), True)
    shutil.rmtree(folder, ignore_errors=True)

print('facts and problems')
folder = Path(tempfile.mkdtemp())
for name in ('a', 'b'):
    soundfile.write(str(folder / f'{name}.wav'), np.zeros((rate * 2, 1), np.float32), rate)
segments = [
    {'start': 1.0, 'end': 3.0, 'text': 'Hi.', 'speaker': 'Ana', 'dubbing': [{'start': 1.0, 'path': str(folder / 'a.wav')}]},
    {'start': 4.0, 'end': 6.0, 'text': 'Yes.', 'speaker': 'Bo', 'dubbing': [{'start': 4.0, 'path': str(folder / 'b.wav')}]},
    {'start': 7.0, 'end': 9.0, 'text': 'Gone.', 'speaker': 'Ana', 'dubbing': [{'start': 7.0, 'path': str(folder / 'x.wav')}]},
    {'start': 10.0, 'end': 12.0, 'text': 'Silent.', 'speaker': 'Bo'},
    {'start': 13.0, 'end': 14.0, 'text': ' '},
]
video = {'duration': 60.0}
mixdown = audio_report.facts('mixdown', 'WAV', False, segments, video, stem='film')
check('mixdown: the video\'s length, one file, 16-bit stereo at 48 kHz',
      (mixdown['length'], mixdown['files'], mixdown['size']), (60.0, ['film.wav'], 60 * 48000 * 4))
check('stems: a file a speaker', audio_report.facts('stems', 'FLAC', False, segments, video, stem='film')['files'],
      ['film-Ana.flac', 'film-Bo.flac'])
clips = audio_report.facts('clips', 'WAV', False, segments, video)
check('clips: a WAV a speech whose file is there, as large as the files',
      (clips['files'], clips['size'], clips['format']),
      (['1.000_Ana.wav', '4.000_Bo.wav'], 2 * os.path.getsize(folder / 'a.wav'), 'WAV'))
check('MP3 smaller than WAV', audio_report.facts('mixdown', 'MP3', False, segments, video)['size'] < mixdown['size'], True)
check('no video: as long as the speech', audio_report.facts('mixdown', 'WAV', False, segments, {})['length'], 9.0)
check('problems: the subtitles with words but no speech, the speeches whose files are gone',
      audio_report.problems('mixdown', segments), {'no_speech': 1, 'subtitles': 4, 'files_gone': 1})
check('none for the background alone', audio_report.problems('background_only', segments), {})

print('the dashboard')
from subtitld.interface import export_dialog


class Engine:
    samplerate = 48000
    speaker_tracks = {'Ana': 1, 'Bo': 2}
    renders = 0

    def render_buffer(self, start=0.0, end=None, samplerate=48000, tracks=None):
        Engine.renders += 1
        loud = mix.copy()
        loud[rate] = 0.999                                     # a peak: clipping
        return loud


session.VIDEO = video
session.SUBTITLE = Subtitle({'segments': segments, 'filepath': '/films/film.usfx'})
session.CONFIG.setdefault('selected_language', 'en')
dialog = export_dialog.ExportDialog(initial_category='audio', audio_engine=Engine())
dialog.show()
dialog.refresh_preview()
dash = dialog.preview_panel.dashboard
check('at once: the facts, the loudness being measured', (dash.isVisible(), dialog.preview_panel.file.isVisible(),
                                                         dash.length.text(), dash.loudness.text()),
      (True, False, '1:00', export_dialog._('export_dialog.audio_measuring')))


def settle():
    started = time.time()
    while (dialog._measuring is not None or dialog._preview_timer.isActive()) and time.time() - started < 60:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


settle()
check('then the loudness, its true peak and range', (dash.loudness.text().endswith('LUFS'), 'dBTP' in dash.peak.text(),
                                                    dash.range.text().endswith('LU')), (True, True, True))
problems = dash.problems.text().splitlines()
check('the problems: clipping, a subtitle without speech, a file gone', len(problems), 3)
dialog.audio_panel.mode_stems.setChecked(True)
settle()
check('stems: the files listed, the mixdown\'s loudness kept, not measured again',
      (dash.files_card.isVisible(), dash.files.text().splitlines(), Engine.renders), (True, ['film-Ana.wav', 'film-Bo.wav'], 1))
dialog._select_category(0, 'subtitles')
check('another page: no dashboard', dash.isVisible(), False)
dialog.done(0)
nothing = export_dialog.ExportDialog(initial_category='audio')
nothing.show()
nothing.refresh_preview()
check('no engine to render with: no loudness, the rest shown', (nothing.preview_panel.dashboard.loudness.text(),
                                                                nothing._measuring), ('—', None))
nothing.done(0)

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
