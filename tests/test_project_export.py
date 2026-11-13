"""Projects of other programs (modules.project_export): the timeline each
gets (the video, its sound or the background, each speaker's dubbed
speeches as they play, the subtitles and their translations), the sound
files copied beside the project, Kdenlive's project (kdenlive_project)
read back and played by melt, through file_io, and the Project tab.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, kdenlive_project, project_export, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {str(got)[:90]!r}' + ('' if ok else f'   (want {str(want)[:90]!r})'))
    if not ok:
        fails.append(name)


ffmpeg = shutil.which('ffmpeg')
if not ffmpeg:
    print('  [SKIP] no FFmpeg to make the media with')
    sys.exit(0)
folder = Path(tempfile.mkdtemp())


def make(*arguments):
    subprocess.run([ffmpeg, '-v', 'error', '-y'] + [str(a) for a in arguments], check=True)


make('-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=25:d=8', '-f', 'lavfi', '-i', 'sine=f=220:d=8', '-c:v', 'libx264',
     '-pix_fmt', 'yuv420p', '-c:a', 'aac', folder / 'film.mp4')
for name, frequency, seconds in (('a1', 660, 1.5), ('a2', 880, 2.5), ('b', 440, 2.0), ('alt', 1200, 1.0)):
    make('-f', 'lavfi', '-i', f'sine=f={frequency}:d={seconds}', folder / f'{name}.wav')
make('-f', 'lavfi', '-i', 'sine=f=330:d=8', folder / 'background.flac')

video = {'filepath': str(folder / 'film.mp4'), 'framerate': 25.0, 'width': 640, 'height': 360, 'duration': 8.0}
subtitle = {'language': 'en', 'segments': [
    {'start': 1.0, 'end': 2.5, 'text': 'Hello there.', 'speaker': 'Ana', 'translations': {'pt-br': 'Olá.'},
     'dubbing': [{'start': 1.0, 'path': str(folder / 'a1.wav')}, {'start': 1.0, 'path': str(folder / 'alt.wav')}]},
    {'start': 3.0, 'end': 5.0, 'text': 'How are you?', 'speaker': 'Bo', 'translations': {'pt-br': 'Como vai?'},
     'dubbing': [{'start': 3.0, 'path': str(folder / 'alt.wav'), 'muted': True},
                 {'start': 3.0, 'path': str(folder / 'b.wav'), 'muted': False}]},
    {'start': 4.0, 'end': 6.5, 'text': 'Fine, thanks.', 'speaker': 'Ana', 'dubbing': [{'start': 4.0, 'segments': [
        {'type': 'audio', 'path': str(folder / 'a2.wav'), 'start': 0.0, 'end': 1.0, 'offset': 0.0},
        {'type': 'audio', 'path': str(folder / 'a2.wav'), 'start': 1.5, 'end': 2.5, 'offset': 1.2}]}]},
    {'start': 4.5, 'end': 5.5, 'text': 'Wait!', 'speaker': 'Ana', 'dubbing': [{'start': 4.5, 'path': str(folder / 'b.wav')}]},
]}

print('the timeline')
check('rates as fractions', [str(project_export.rate_of(fps)) for fps in (29.97, 25, 23.976, None)],
      ['30000/1001', '25', '24000/1001', '25'])
timeline = project_export.collect(subtitle, video)
check('the video, its sound, a track a speaker; the overlap on a second one',
      [(t.name, t.kind, len(t.clips)) for t in timeline.tracks],
      [('Video', 'video', 1), ('Audio', 'audio', 1), ('Ana 1', 'audio', 3), ('Ana 2', 'audio', 1), ('Bo', 'audio', 1)])
check('each speech as it plays: the first unmuted dub, cut as it is cut',
      [(c.path.rsplit('/', 1)[1], round(c.at, 2), c.source_in, c.source_out) for c in timeline.tracks[2].clips],
      [('a1.wav', 1.0, 0.0, 1.5), ('a2.wav', 4.0, 0.0, 1.0), ('a2.wav', 5.2, 1.5, 2.5)])
check('the unmuted alternate plays', timeline.tracks[4].clips[0].path.rsplit('/', 1)[1], 'b.wav')
check('the subtitles and each translation', [(n, len(c)) for n, _l, c in timeline.subtitles],
      [('Subtitles', 4), ('pt-br', 2)])
alone = project_export.collect(subtitle, video, {'audio': 'background', 'speeches': False, 'translations': False},
                               background=str(folder / 'background.flac'), names={'background': 'Fundo'})
check('the background in place of the sound; no speeches, no translations',
      ([(t.name, t.kind) for t in alone.tracks], len(alone.subtitles)), ([('Video', 'video'), ('Fundo', 'audio')], 1))

print('the files beside it')
project = folder / 'out' / 'film.kdenlive'
copies = project_export.gather(timeline, project_export.media_folder(str(project)), keep={video['filepath']})
check('each speech copied once, into "film media"; the video and its sound left where they are',
      (sorted(os.listdir(folder / 'out' / 'film media')), timeline.tracks[0].clips[0].path),
      (['0001 Ana 1.wav', '0002 Ana 1.wav', '0003 Ana 2.wav'], video['filepath']))
check('the clips point at the copies', timeline.tracks[2].clips[2].path, str(folder / 'out' / 'film media' / '0002 Ana 1.wav'))

print('Kdenlive')
timeline = project_export.collect(subtitle, video)
project_export.gather(timeline, project_export.media_folder(str(project)), keep={video['filepath'], str(folder / 'film.mp4')})
kdenlive_project.save(str(project), timeline)
from lxml import etree
root = etree.parse(str(project)).getroot()
prop = lambda node, name: node.findtext(f"property[@name='{name}']")
by_id = {node.get('id'): node for node in root if node.get('id')}
check('MLT, the bin first named, a 25 fps profile of the video\'s size',
      (root.get('producer'), root.find('profile').get('width'), root.find('profile').get('frame_rate_num')),
      ('main_bin', '640', '25'))
main_bin = by_id['main_bin']
sequence_id = prop(main_bin, 'kdenlive:docproperties.uuid')
sequence = by_id[sequence_id]
names = [prop(by_id[track.get('producer')], 'kdenlive:track_name') for track in sequence.findall('track')[1:]]
check('the sequence: the sound from A1 up, the video on top', names, ['Bo', 'Ana 2', 'Ana 1', 'Audio', 'Video'])
check('its subtitle tracks, beside the project', (
    [node.findtext("property[@name='mlt_service']") for node in sequence.findall('filter')],
    prop(sequence, 'kdenlive:sequenceproperties.subtitlesList').count('"file"'),
    sorted(p.name for p in (folder / 'out').glob('*.ass'))),
    (['avfilter.subtitles'], 2, ['film.kdenlive-1.ass', 'film.kdenlive.ass']))
ana = by_id[[t.get('producer') for t in by_id[sequence.findall('track')[3].get('producer')].findall('track')][0]]
check('Ana\'s speeches at their frames, the cut piece from its own in point', [
    (entry.get('in'), entry.get('out')) for entry in ana.findall('entry')] + [b.get('length') for b in ana.findall('blank')],
    [('00:00:00.000', '00:00:01.480'), ('00:00:00.000', '00:00:00.960'), ('00:00:01.520', '00:00:02.480'),
     '00:00:01.000', '00:00:01.480', '00:00:00.200'])
chain = next(node for node in root.iter('chain') if prop(node, 'resource') == video['filepath'])
check('a clip carries Kdenlive\'s hash of its file', prop(chain, 'kdenlive:file_hash'),
      kdenlive_project.file_hash(video['filepath']))
check('opens zoomed to fit', prop(sequence, 'kdenlive:sequenceproperties.zoom'), str(kdenlive_project.zoom_for(200)))

if shutil.which('melt'):
    render = folder / 'render.mp4'
    subprocess.run(['melt', str(project), '-consumer', f'avformat:{render}', 'vcodec=libx264', 'acodec=aac'],
                   capture_output=True, timeout=120, cwd=folder / 'out')
    import numpy as np

    def loudest(start, end):
        raw = subprocess.run([ffmpeg, '-v', 'error', '-ss', str(start), '-t', str(end - start), '-i', str(render),
                              '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], capture_output=True).stdout
        samples = np.frombuffer(raw, np.float32)
        spectrum = np.abs(np.fft.rfft(samples))
        frequencies = np.fft.rfftfreq(len(samples), 1 / 8000)
        return {int(round(frequencies[i] / 10) * 10) for i in np.argsort(spectrum)[-8:]}

    check('melt plays it: the sound, and each speech at its time', (
        220 in loudest(0.2, 0.8), 660 in loudest(1.2, 2.2), 440 in loudest(3.2, 3.8), 880 in loudest(5.4, 6.0)),
        (True, True, True, True))
else:
    print('  [SKIP] no melt to play it')

print('through file_io')
session.SUBTITLE = subtitle
session.VIDEO = video
session.FORMAT = {'options': {'project': {'translations': False}}}
saved = folder / 'saved' / 'film.kdenlive'
saved.parent.mkdir()
file_io.save_file(str(saved), 'KDENLIVE')
check('written, one subtitle track, the speeches beside it', (saved.is_file(), sorted(p.name for p in saved.parent.iterdir())),
      (True, ['film media', 'film.kdenlive', 'film.kdenlive.ass']))
check('the file picker knows it', session.LIST_OF_SUPPORTED_PROJECT_EXTENSIONS['KDENLIVE']['extensions'], ['kdenlive'])

print('the dialog')
from subtitld.interface import export_dialog
session.CONFIG.pop('export_project', None)
session.CONFIG.setdefault('selected_language', 'en')
panel = export_dialog._ProjectPanel()
check('everything offered, the video\'s sound under it', panel.export_choices(),
      ('KDENLIVE', {'audio': 'original', 'video': True, 'speeches': True, 'subtitles': True, 'translations': True}))
panel.subtitles_check.setChecked(False)
check('no subtitles: no translations', (panel.translations_check.isHidden(), panel.export_choices()[1]['translations']),
      (True, False))
config = panel.get_config()
again = export_dialog._ProjectPanel()
check('the export\'s options, remembered', (config['options']['project']['subtitles'], again.subtitles_check.isChecked()),
      (False, False))
session.VIDEO = {}
bare = export_dialog._ProjectPanel()
check('no video: neither the video nor its sound offered', bare.export_choices()[1],
      {'audio': 'none', 'video': False, 'speeches': True, 'subtitles': False, 'translations': False})
session.VIDEO = video
session.CONFIG.pop('export_project', None)
dialog = export_dialog.ExportDialog(initial_category='project')
dialog.show()
dialog.refresh_preview()
check('its preview: the tracks, numbered as the editors number them',
      dialog.preview_panel.file.toPlainText().splitlines(),
      ['V1  Video  (1)', 'A1  Audio  (1)', 'A2  Ana 1  (3)', 'A3  Ana 2  (1)', 'A4  Bo  (1)', 'S1  Subtitles  (4)',
       'S2  pt-br  (2)'])
dialog.done(0)

shutil.rmtree(folder, ignore_errors=True)
print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
