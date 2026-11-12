"""HLS segmented WebVTT: the playlist and segments written (target duration,
timestamp maps, a cue in every segment it spans, empty segments, the
programme's length), read back as one file, the timestamp maps of other
segmenters (per-segment maps, none, a LOCAL time, a wrapped clock), cues
cut at a segment's edge, master playlists, byte ranges, http(s), file_io,
the export dialog, and FFmpeg's view of both directions when it is here.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, hls, session

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def cues(segments):
    return [(s['start'], s['end'], s['text']) + ((s['speaker'],) if 'speaker' in s else ()) for s in segments]


def write_files(folder, files):
    """Write {name: text} into `folder`."""
    os.makedirs(folder, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(folder, name), 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)


folder = Path(tempfile.mkdtemp())
SUBTITLES = [
    {'start': 1.0, 'end': 3.0, 'text': 'Hello <i>there</i>'},
    {'start': 5.0, 'end': 8.5, 'text': 'Across\nthe edge', 'speaker': 'Ann'},
    {'start': 13.0, 'end': 14.0, 'text': '{\\an8}<font color="#ffd700">Gold</font> on top'},
]

print('writing')
text, files = hls.write(SUBTITLES, voices=True, total=20, folder='out_segments')
lines = text.strip().split('\n')
check('the playlist begins as RFC 8216 asks', lines[:5],
      ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:6', '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:VOD'])
check('and ends', lines[-1], '#EXT-X-ENDLIST')
check('6 s segments for the 20 s of the programme, the last shorter',
      [line for line in lines if line.startswith('#EXTINF')], ['#EXTINF:6.000,'] * 3 + ['#EXTINF:2.000,'])
check('their addresses in the folder', [line for line in lines if not line.startswith('#')],
      [f'out_segments/segment{index:05d}.vtt' for index in range(4)])
check('each segment maps cue time 0 to MPEGTS 900000',
      {body.split('\n')[1] for _name, body in files}, {'X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000'})
check('a cue spanning two segments is in both, with its whole time',
      ['00:00:05.000 --> 00:00:08.500' in body for _name, body in files], [True, True, False, False])
check('a segment with nothing to show is its header alone', files[3][1],
      'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\n')
check('a STYLE block for a colour comes after the header, map included',
      files[2][1].split('\n')[:4], ['WEBVTT', 'X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000', '', 'STYLE'])
text, files = hls.write(SUBTITLES, duration=4, mpegts=0)
check('4 s segments, as long as the subtitles when there is no video, MPEGTS 0',
      ([line for line in text.split('\n') if line.startswith('#EXTINF')], files[0][1].split('\n')[1]),
      (['#EXTINF:4.000,'] * 3 + ['#EXTINF:2.000,'], 'X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0'))
text, _files = hls.write([{'start': 0, 'end': 6.6, 'text': 'x'}], duration=6.6)
check('a target duration is the longest segment rounded, halves up', '#EXT-X-TARGETDURATION:7' in text, True)
text, files = hls.write([])
check('nothing to write: one empty segment', (len(files), files[0][1].count('-->')), (1, 0))

print('read back')
hls.save(str(folder / 'out.m3u8'), SUBTITLES, voices=True, total=20)
back, settings = hls.read(str(folder / 'out.m3u8'))
check('the subtitles, each once', cues(back), cues(SUBTITLES))
check('the settings', settings, {'language': '', 'segment_duration': 6.0, 'mpegts': 900000})
hls.save(str(folder / 'out.m3u8'), SUBTITLES[:1], duration=10)
check('a second save takes the first one\'s segments away', sorted(os.listdir(folder / 'out_segments')),
      ['segment00000.vtt'])
check('a stem with a space is an address with %20',
      hls.write(SUBTITLES[:1], folder='my show_segments')[0].split('\n')[6], 'my%20show_segments/segment00000.vtt')

print("other segmenters' timestamp maps")
HEAD = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-VERSION:3\n'


def playlist_of(names, durations=None):
    return HEAD + ''.join(f'#EXTINF:{(durations or [6] * len(names))[index]},\n{name}\n'
                          for index, name in enumerate(names)) + '#EXT-X-ENDLIST\n'


case = folder / 'local_times'
write_files(case, {
    'subs.m3u8': playlist_of(['a.vtt', 'b.vtt', 'c.vtt']),
    # Each segment's cues from 0, its map where the segment starts.
    'a.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000\n\n00:00:01.000 --> 00:00:02.000\nOne\n',
    'b.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:1440000,LOCAL:00:00:00.000\n\n00:00:01.500 --> 00:00:03.000\nTwo\n',
    'c.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:1980000,LOCAL:00:00:00.000\n\n',
})
back, settings = hls.read(str(case / 'subs.m3u8'))
check('a map a segment, its cues from 0: moved to where it starts', (cues(back), settings['mpegts']),
      ([(1.0, 2.0, 'One'), (7.5, 9.0, 'Two')], 900000))
write_files(case, {
    'subs.m3u8': playlist_of(['a.vtt', 'b.vtt']),
    'a.vtt': 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nOne\n',
    'b.vtt': 'WEBVTT\n\n00:00:07.000 --> 00:00:08.000\nTwo\n',
})
check('no map at all: cue times as they are', cues(hls.read(str(case / 'subs.m3u8'))[0]),
      [(1.0, 2.0, 'One'), (7.0, 8.0, 'Two')])
write_files(case, {
    'a.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:10.000,MPEGTS:900000\n\n00:00:11.000 --> 00:00:12.000\nOne\n',
    'b.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:10.000,MPEGTS:900000\n\n00:00:17.000 --> 00:00:18.000\nTwo\n',
})
check('a LOCAL time of 10 s for MPEGTS 900000, everywhere: as they are', cues(hls.read(str(case / 'subs.m3u8'))[0]),
      [(11.0, 12.0, 'One'), (17.0, 18.0, 'Two')])
wrap = (1 << 33) - 90000
write_files(case, {
    'a.vtt': f'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:{wrap},LOCAL:00:00:00.000\n\n00:00:00.500 --> 00:00:00.900\nOne\n',
    'b.vtt': 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:450000,LOCAL:00:00:00.000\n\n00:00:00.500 --> 00:00:01.000\nTwo\n',
})
check("the 33-bit clock wrapping round between segments", cues(hls.read(str(case / 'subs.m3u8'))[0]),
      [(0.5, 0.9, 'One'), (6.5, 7.0, 'Two')])

print('cues cut at an edge')
write_files(case, {
    'a.vtt': 'WEBVTT\n\n00:00:05.000 --> 00:00:06.000\nCut in two\n',
    'b.vtt': 'WEBVTT\n\n00:00:06.000 --> 00:00:08.000\nCut in two\n\n00:00:09.000 --> 00:00:10.000\nAgain\n\n'
             '00:00:10.000 --> 00:00:11.000\nAgain\n',
})
check("one again where the segment ends; twice said in the middle of one, twice",
      cues(hls.read(str(case / 'subs.m3u8'))[0]),
      [(5.0, 8.0, 'Cut in two'), (9.0, 10.0, 'Again'), (10.0, 11.0, 'Again')])

print('master playlists')
write_files(case, {
    'master.m3u8': '#EXTM3U\n'
                   '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",URI="audio.m3u8"\n'
                   '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=NO,URI="en/subs.m3u8"\n'
                   '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Português",LANGUAGE="pt-BR",DEFAULT=YES,'
                   'AUTOSELECT=YES,URI="pt/subs.m3u8"\n'
                   '#EXT-X-STREAM-INF:BANDWIDTH=1000000,CODECS="avc1.64001f,mp4a.40.2,wvtt",SUBTITLES="subs"\nvideo.m3u8\n',
})
write_files(case / 'en', {'subs.m3u8': playlist_of(['1.vtt']), '1.vtt': 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n'})
write_files(case / 'pt', {'subs.m3u8': playlist_of(['1.vtt']), '1.vtt': 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nOlá\n'})
back, settings = hls.read(str(case / 'master.m3u8'))
check('the default rendition, beside the master', (cues(back), settings['language']), ([(1.0, 2.0, 'Olá')], 'pt-BR'))
back, settings = hls.read(str(case / 'master.m3u8'), language='en-us')
check("the one in the project's language", (cues(back), settings['language']), ([(1.0, 2.0, 'Hello')], 'en'))
write_files(case, {'video.m3u8': '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n'})
try:
    hls.read(str(case / 'video.m3u8'))
    check('a master with no subtitles says so', 'read', 'ValueError')
except ValueError as error:
    check('a master with no subtitles says so', 'no subtitles' in str(error), True)
write_files(case, {'notes.m3u8': 'a playlist of songs\n'})
try:
    hls.read(str(case / 'notes.m3u8'))
    check('not a playlist says so', 'read', 'ValueError')
except ValueError as error:
    check('not a playlist says so', 'EXTM3U' in str(error), True)

print('byte ranges')
one = 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFirst\n'
two = 'WEBVTT\n\n00:00:07.000 --> 00:00:08.000\nSecond\n'
write_files(case, {
    'all.vtt': one + two,
    'ranges.m3u8': HEAD + f'#EXTINF:6,\n#EXT-X-BYTERANGE:{len(one)}@0\nall.vtt\n'
                          f'#EXTINF:6,\n#EXT-X-BYTERANGE:{len(two)}\nall.vtt\n#EXT-X-ENDLIST\n',
})
check('a segment a part of one file, the second where the first ends', cues(hls.read(str(case / 'ranges.m3u8'))[0]),
      [(1.0, 2.0, 'First'), (7.0, 8.0, 'Second')])

print('over http')


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(_Quiet, directory=str(folder)))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    back, settings = hls.read(f'http://127.0.0.1:{server.server_address[1]}/local_times/master.m3u8', language='en')
    check('a master and its rendition fetched, addresses against the URL', cues(back), [(1.0, 2.0, 'Hello')])
finally:
    server.shutdown()

print('through file_io')
session.FORMAT = {}
session.SUBTITLE['language'] = 'en-us'
opened, fmt = file_io.process_subtitles_file(str(case / 'master.m3u8'))
check("opened: HLS, the rendition in the project's language, its settings kept",
      (fmt, cues(opened), session.SUBTITLE['language'],
       {key: session.FORMAT['options'].get(key) for key in ('segment_duration', 'mpegts')}),
      ('HLS', [(1.0, 2.0, 'Hello')], 'en-us', {'segment_duration': 6.0, 'mpegts': 0}))
session.SUBTITLE['segments'] = [dict(segment) for segment in SUBTITLES]
session.VIDEO = {'filepath': '/videos/show.mp4', 'duration': 30.0}
session.FORMAT = {'options': {'segment_duration': 10.0, 'mpegts': 900000, 'speakers': 'voice'}}
file_io.save_file(str(folder / 'saved.m3u8'), 'HLS')
saved = (folder / 'saved.m3u8').read_text()
check('saved: 10 s segments for the 30 s video, voices', (saved.count('#EXTINF:10.000,'),
      '<v Ann>' in (folder / 'saved_segments' / 'segment00000.vtt').read_text()), (3, True))
check('and read again', cues(hls.read(str(folder / 'saved.m3u8'))[0]), cues(SUBTITLES))
write_files(folder, {'broken.m3u8': 'nothing'})
try:
    file_io.process_subtitles_file(str(folder / 'broken.m3u8'))
    check('a file that is not a playlist', 'opened', 'CorruptedProjectFileError')
except file_io.CorruptedProjectFileError:
    check('a file that is not a playlist', 'CorruptedProjectFileError', 'CorruptedProjectFileError')
check('the extension is HLS\'s', session.LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS['HLS']['extensions'], ['m3u8'])

print('the dialog')
from subtitld.interface.export_dialog import _SubtitlesPanel
session.CONFIG.pop('export_subtitles', None)
panel = _SubtitlesPanel()
panel.format_combo.setCurrentText('HLS')
panel._refresh_options()
options = panel.get_config()['options']
check('HLS: its options, 6 s and MPEGTS 900000 by default, voice tags offered',
      (panel.stream_options.isHidden(), panel.text_options.isHidden(), options.get('segment_duration'),
       options.get('mpegts'), panel.speakers_combo.combobox.findData('voice') >= 0),
      (False, False, 6.0, 900000, True))
panel.segment_combo.combobox.setCurrentIndex(panel.segment_combo.combobox.findData(2.0))
panel.mpegts_combo.combobox.setCurrentIndex(1)
check('chosen and remembered', ({key: panel.get_config()['options'].get(key) for key in ('segment_duration', 'mpegts')},
                                 _SubtitlesPanel().segment_combo.combobox.currentData()),
      ({'segment_duration': 2.0, 'mpegts': 0}, 2.0))
panel.format_combo.setCurrentText('SRT')
panel._refresh_options()
check('not for SRT', panel.stream_options.isHidden(), True)

print('FFmpeg')
if shutil.which('ffmpeg'):
    out = folder / 'ffmpeg_read.vtt'
    hls.save(str(folder / 'for_ffmpeg.m3u8'), SUBTITLES, total=20)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(folder / 'for_ffmpeg.m3u8'), str(out)], check=True)
    read_by_ffmpeg = out.read_text()
    check('FFmpeg reads the playlist written, at the times written',
          all(stamp in read_by_ffmpeg for stamp in ('00:01.000 --> 00:03.000', '00:13.000 --> 00:14.000')), True)
    made = folder / 'by_ffmpeg'
    made.mkdir()
    (made / 'in.vtt').write_text('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nOne\n\n00:00:05.000 --> 00:00:08.000\nTwo\n')
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=64x48:d=12:r=25',
                             '-i', str(made / 'in.vtt'), '-c:v', 'mpeg2video', '-g', '25', '-c:s', 'webvtt', '-f', 'hls',
                             '-hls_time', '2', '-hls_playlist_type', 'vod', '-master_pl_name', 'master.m3u8',
                             '-var_stream_map', 'v:0,s:0,sgroup:subs,language:en', str(made / 'out_%v.m3u8')],
                            capture_output=True)
    if result.returncode == 0:
        back, settings = hls.read(str(made / 'master.m3u8'))
        check("FFmpeg's own HLS read, through its master playlist",
              ([text for _start, _end, text in cues(back)], settings['language']), (['One', 'Two'], 'en'))
    else:
        print('  [skip] FFmpeg could not write HLS here:', result.stderr.decode(errors='replace').strip()[:200])
else:
    print('  [skip] no FFmpeg')

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
