"""Corpus: subtitles and captions inside real videos and streams, read the
way the app reads them when a video is opened (file_io.process_video_file
lists its tracks; the subtitles engine reads a text, teletext, ARIB or
picture track, the captions engine a video's closed captions), checked
against what Subtitle Edit's own tests (tests/libse at 80b7c034) and
FFmpeg's FATE references (tests/ref/fate/sub-*) say of the same files.

Picture tracks are read by OCR in the app: here the pictures themselves are
checked (count, times), and one track goes through the OCR job with a fake
engine. Where Subtitld knowingly differs from a reference, `known` prints
it without failing; those are listed in the final line.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code. It skips (passing) when the corpus has not
been fetched (python tests/corpus/fetch.py) or FFmpeg is missing.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import (closed_captions, embedded_captions, file_io, markup, picture_ocr, session, teletext,
                              video_subtitles, waveform)
from subtitld.modules.addons.builtin.subtitles_provider import read as read_track_file
from subtitld.modules.addons.provider import OCRProvider
from corpus_common import need

fails, differences = [], []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def known(name, got, want):
    """A difference from a reference that is known and recorded, not fixed."""
    same = got == want
    print(f'  [{"OK " if same else "KNOWN"}] {name}: {got!r}' + ('' if same else f'   (reference {want!r})'))
    if not same:
        differences.append(name)


def near(value, want, tolerance):
    return value is not None and abs(value - want) <= tolerance


SE = 'subtitleedit/'
(MKV_SRT, MKS_USF, MKV_VOBSUB_PGS, MKV_VOBSUB_NO_DURATION, MKV_CHAPTERS, MKV_DELAYED, MOV_C608, MOV_C608_CHANGE,
 MOV_C708, MP4, MP4_SRT, MP4_CHAPTER_TRACK, MP4_CHAPTERS, MP4_STZ2, TS_608_H264, TS_608_MPEG2, TS_SCTE20_STRIPPED,
 TS_DVB_AT_START, TS_DVB_RS204, TS_GRAPHICS, TS_TELETEXT, MP4_FRAGMENTED, MKV_608_H264, MKV_608_MPEG2,
 MKV_NO_CAPTIONS, MP4_H264_NO_CAPTIONS, MP4_HEVC_NO_CAPTIONS, MPG_DVD, MPG_NO_CAPTIONS, MXF_436M, VOB_DVD,
 VOB_DVD_ONE_PTS, ARIB_CLEAR, ARIB_RUBY, DVBSUB_FILTER, SCTE20, ROLLUP_M2V, MOVTEXT, REF_DVB) = need(
    SE + 'sample_MKV_SRT.mkv', SE + 'sample_MKV_USF.mks', SE + 'sample_MKV_VobSub_PGS.mkv',
    SE + 'sample_MKV_ffmpeg_VobSub_no_duration.mkv', SE + 'sample_MKV_chapters.mkv', SE + 'sample_MKV_delayed.mkv',
    SE + 'sample_MOV_C608.mov', SE + 'sample_MOV_C608_caption_change_in_sample.mov', SE + 'sample_MOV_C708.mov',
    SE + 'sample_MP4.mp4', SE + 'sample_MP4_SRT.mp4', SE + 'sample_MP4_chapter_track.mp4',
    SE + 'sample_MP4_chapters.mp4', SE + 'sample_MP4_stz2_compact.mp4', SE + 'sample_TS_cea608_h264.ts',
    SE + 'sample_TS_cea608_mpeg2.ts', SE + 'sample_TS_cea608_scte20.ts', SE + 'sample_TS_dvbsub_at_video_start.ts',
    SE + 'sample_TS_dvbsub_rs204.ts', SE + 'sample_TS_with_graphics.ts', SE + 'sample_TS_with_teletext.ts',
    SE + 'sample_fragmented_mp4_cea608_708.mp4', SE + 'sample_mkv_cea608_h264.mkv', SE + 'sample_mkv_cea608_mpeg2.mkv',
    SE + 'sample_mkv_no_captions_4min.mkv', SE + 'sample_mp4_h264_no_captions.mp4',
    SE + 'sample_mp4_hevc_no_captions.mp4', SE + 'sample_mpg_dvd_captions.mpg', SE + 'sample_mpg_mpeg2_no_captions.mpg',
    SE + 'sample_mxf_436m_cea608_708.mxf', SE + 'sample_vob_dvd_captions.vob', SE + 'sample_vob_dvd_captions_one_pts.vob',
    'fate/sub/aribcaption_clear.ts', 'fate/sub/aribcaption_ruby.ts', 'fate/sub/dvbsubtest_filter.ts',
    'fate/sub/scte20.ts', 'fate/sub/Closedcaption_rollup.m2v', 'fate/sub/MovText_capability_tester.mp4',
    'fate/ref/sub-dvb')

FFMPEG, FFPROBE = shutil.which('ffmpeg'), shutil.which('ffprobe')
if not (FFMPEG and FFPROBE):
    print('SKIP: FFmpeg is not installed')
    sys.exit(0)
session.FFMPEG_EXECUTABLE, session.FFPROBE_EXECUTABLE = FFMPEG, FFPROBE
waveform.FFPROBE_EXECUTABLE = FFPROBE


def probe(path, *entries):
    """ffprobe's JSON of `path` (-show_format -show_streams, and `entries`)."""
    out = subprocess.run([FFPROBE, '-v', 'error', '-of', 'json', '-show_format', '-show_streams', *entries, str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out or b'{}')


def packets(path, index):
    """[(pts, duration or None, size)] of stream `index`, as the container
    times them (ffprobe): the reference for a track's own cue times."""
    found = []
    for packet in probe(path, '-select_streams', str(index), '-show_packets')['packets']:
        duration = float(packet['duration_time']) if packet.get('duration_time') not in (None, 'N/A') else None
        found.append((float(packet['pts_time']), duration, int(packet['size'])))
    return found


def start_of(path):
    return float(probe(path)['format'].get('start_time') or 0)


def tracks(path):
    """The subtitle tracks opening the video lists, as the Import panel offers them."""
    return file_io.process_video_file(str(path))['subtitle_tracks']


def text_track(path, track):
    """A text track's subtitles, as the subtitles engine reads one: written
    out by FFmpeg, then read as Subtitld opens such a file."""
    target = video_subtitles.extract(str(path), track['index'], track['codec'], ffmpeg=FFMPEG, ffprobe=FFPROBE)
    try:
        return read_track_file(target)
    finally:
        os.remove(target)


def captions(path, track='auto'):
    return closed_captions.from_video(str(path), track, ffprobe=FFPROBE)


def cues(segments):
    return [(round(s['start'], 3), round(s['end'], 3), markup.split_lead(s['text'])[1]) for s in segments]


def plain(text):
    return markup.plain(markup.split_lead(text)[1])


def pictures(path, track):
    return video_subtitles.extract_pictures(str(path), track['index'], track['codec'], ffmpeg=FFMPEG,
                                            ffprobe=FFPROBE, pid=track.get('pid'), page=track.get('page'))


def times(found):
    return [(round(p.start, 3), round(p.end, 3)) for p in found]


def brief(found):
    return [(t['index'], t['codec'], t['kind']) for t in found]


print('text tracks')
found = tracks(MKV_SRT)
check('MKV with SRT: one text track', brief(found), [(1, 'subrip', 'text')])
# Expected: the track's blocks as the container times them (ffprobe), and its
# two obvious lines; Subtitle Edit only asks for blocks in playback order.
blocks = packets(MKV_SRT, 1)
check('MKV with SRT: the blocks, their times and lines', cues(text_track(MKV_SRT, found[0])),
      [(round(p, 3), round(p + d, 3), f'Line {n}') for n, (p, d, _size) in enumerate(blocks, 1)])

found = tracks(MP4_SRT)
check('MP4 with mov_text: one text track', brief(found), [(2, 'mov_text', 'text')])
# FFmpeg's own ASS of this track has the sample description's font (Serif
# 18) in its Default style, the cues plain: not a <font> around each cue.
check('MP4 mov_text: the samples, as plain lines', cues(text_track(MP4_SRT, found[0])),
      [(round(p, 3), round(p + d, 3), f'Line {n}') for n, (p, d, _size) in
       enumerate([b for b in packets(MP4_SRT, 2) if b[2] > 2], 1)])

found = tracks(MP4_STZ2)
read = cues(text_track(MP4_STZ2, found[0]))
# Subtitle Edit, Mp4CompactSampleSizeTest: stz2 sample sizes read like stsz.
check('stz2 (compact sample sizes): 5 cues', len(read), 5)
check('stz2: the first, 1.0-2.5 "Line one plain"', read[0], (1.0, 2.5, 'Line one plain'))
check('stz2: the last, Japanese', read[4][2], '日本語のテスト')
# The same cues as the USF test file (MatroskaUsfTrackTest): italic, bold, underline.
check('stz2: its styles', [read[1][2], read[3][2]], ['<i>Italic line</i>', '<b>Bold</b> and <u>underline</u>'])

found = tracks(MP4)
read = text_track(MP4, found[0])
samples = [b for b in packets(MP4, found[0]['index']) if b[2] > 2]   # 2 bytes: an empty sample, a gap
check('a real MP4 (808 timed text samples): a cue for each sample with text, at its time',
      (len(read), round(read[0]['start'], 3), round(read[-1]['start'], 3)),
      (len(samples), round(samples[0][0], 3), round(samples[-1][0], 3)))
check('its language, from the track', (found[0]['language_tag'], found[0]['language']), ('eng', 'en-us'))

found = tracks(MOVTEXT)
# FFmpeg FATE sub-movtext: the ASS FFmpeg makes of this file.
check('FATE MovText_capability_tester: as FFmpeg reads it', cues(text_track(MOVTEXT, found[0])), [
    (0.97, 2.54, '- Test 1.\n- Test 2.'), (3.05, 4.74, 'Test 3.'), (5.85, 8.14, '- Test 4.\n- Test 5.')])

# Subtitle Edit, MatroskaUsfTrackTest: one S_TEXT/USF track, 5 cues. FFmpeg
# does not know the codec, and Subtitld lists only what FFmpeg names.
known('MKS with a USF track: listed', brief(tracks(MKS_USF)), [(0, 'usf', 'text')])

print('no subtitle tracks')
# Subtitle Edit, VideoChapterReaderTest: a chapter track is not a subtitle
# track; the delayed MKV has audio and video only (ffprobe).
for path in (MKV_CHAPTERS, MP4_CHAPTERS, MP4_CHAPTER_TRACK, MKV_DELAYED, MKV_NO_CAPTIONS, MP4_H264_NO_CAPTIONS,
             MP4_HEVC_NO_CAPTIONS, MPG_NO_CAPTIONS):
    check(f'{path.name}: no subtitle tracks', tracks(path), [])
for path in (MKV_NO_CAPTIONS, MP4_H264_NO_CAPTIONS, MP4_HEVC_NO_CAPTIONS, MPG_NO_CAPTIONS):
    # Subtitle Edit, MatroskaClosedCaptionTest and the *_no_captions samples.
    check(f'{path.name}: no closed captions', captions(path), ([], None))

print('picture tracks')
found = tracks(MKV_VOBSUB_PGS)
check('MKV with VobSub and PGS: two picture tracks', brief(found),
      [(1, 'dvd_subtitle', 'pictures'), (2, 'hdmv_pgs_subtitle', 'pictures')])
# VobSub: each block's time and duration (ffprobe); PGS: each display
# set's time, cleared by the next (ffprobe).
check('VobSub: a picture for each block, at its time, for its duration', times(pictures(MKV_VOBSUB_PGS, found[0])),
      [(p, round(p + d, 3)) for p, d, _s in packets(MKV_VOBSUB_PGS, 1)])
sets = packets(MKV_VOBSUB_PGS, 2)
check('PGS: a picture for each display set, until the set that clears it',
      times(pictures(MKV_VOBSUB_PGS, found[1])), [(sets[0][0], sets[1][0]), (sets[2][0], sets[3][0])])

found = tracks(MKV_VOBSUB_NO_DURATION)
shown = pictures(MKV_VOBSUB_NO_DURATION, found[0])
starts = [p for p, _d, _s in packets(MKV_VOBSUB_NO_DURATION, 0)]
# Subtitle Edit, MatroskaFileTest.UnknownBlockDurationIsNotNegative: FFmpeg
# writes "unknown" durations here; none may end before it starts. FFmpeg
# dates each SPU's stop at its largest (745.6 s): no stop, so each shows
# until the next.
check('VobSub with no durations: a picture for each block, at its time', [round(p.start, 3) for p in shown], starts)
check('none ends before it starts, nor 745 s later; each until the next',
      [round(p.end, 3) for p in shown[:-1]], starts[1:])

print('the OCR job on a picture track (a fake engine)')


class FakeOCR(OCRProvider):
    """Reads every picture as its number."""

    @property
    def id(self):
        return 'fake-ocr'

    def recognize(self, image_paths, language, options=None):
        texts = [f'picture {n}' for n, _path in enumerate(image_paths, 1)]
        QTimer.singleShot(0, lambda: self.finished.emit(texts, [0.99] * len(texts)))


def run(job):
    loop, out = QEventLoop(), {}
    job.finished.connect(lambda segments, info: (out.update(segments=segments), loop.quit()))
    job.failed.connect(lambda message: (out.update(failed=message), loop.quit()))
    job.start()
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    return out


pgs_track = tracks(MKV_VOBSUB_PGS)[1]
out = run(picture_ocr.OCRJob.for_track(str(MKV_VOBSUB_PGS), pgs_track['index'], pgs_track['codec'], 'en', FakeOCR()))
check('PGS in an MKV, through the OCR job: a subtitle a picture, at its times',
      [(round(s['start'], 3), round(s['end'], 3), s['text']) for s in out.get('segments', [])],
      [(sets[0][0], sets[1][0], 'picture 1'), (sets[2][0], sets[3][0], 'picture 2')])

print('DVB subtitles')
found = tracks(DVBSUB_FILTER)
check('FATE dvbsubtest_filter: one DVB track, English', [(t['codec'], t['language_tag']) for t in found],
      [('dvb_subtitle', 'eng')])
# FFmpeg FATE sub-dvb: FFmpeg re-encodes the track; each display set is a
# packet at its time, lasting its duration, and a 14-byte one clears it.
reference = []
for line in REF_DVB.read_text().splitlines():
    if line.startswith('#'):
        continue
    _stream, _dts, pts, duration, size, _crc = [part.strip() for part in line.split(',')]
    if int(size) != 14:
        reference.append((int(pts) / 1e6, (int(pts) + int(duration)) / 1e6))
shown = pictures(DVBSUB_FILTER, found[0])
check('FATE dvbsubtest_filter: 36 pictures, as FFmpeg shows them', len(shown), len(reference))
check('each at its time and for as long, to the millisecond',
      [(p.start, p.end) for p, (start, end) in zip(shown, reference)
       if not (near(p.start, start, 0.0015) and near(p.end, end, 0.0015))], [])

found = tracks(TS_DVB_AT_START)
at_start = pictures(TS_DVB_AT_START, found[0])
# Subtitle Edit, TransportStreamDvbTimingTest: the subtitle sent a
# millisecond before the first video frame stays at the start (not 7 s
# later), and ends within the first 3 s.
check('DVB a millisecond before the video: at the start, ending within 3 s',
      [(p.start, p.end < 3.0) for p in at_start], [(0.0, True)])
# Subtitle Edit counts 2; Subtitld and FFmpeg (ffmpeg -c:s dvbsub writes
# one display set with something in it) count 1. The stream sends a display
# set for PTS 144000, then a page with nothing on it for 143910 and a new
# one for 143911: EN 300 743's decoder takes display sets in the order they
# come, each page instance taking the place of the one before it, and a
# decoder cannot show the page for 144000 once a later page instance came
# for a time already before it, so the first is never shown (at most for
# the millisecond between, not a frame).
check('DVB at the start: one subtitle, the first display set replaced before its time', len(at_start), 1)
rs204 = tracks(TS_DVB_RS204)
check('204-byte packets (Reed-Solomon): read as the 188-byte form', times(pictures(TS_DVB_RS204, rs204[0])),
      times(at_start))

found = tracks(TS_GRAPHICS)
graphics = pictures(TS_GRAPHICS, found[0])
# Subtitle Edit, TransportStreamReadingTest: 10 subtitles, the first 1.05 s
# after a video PES put 1 s before the first of them. This stream is the
# subtitles alone, no PAT or PMT: time zero is its first PES.
check('DVB in a stream with no PMT (only its PID): 10 pictures', len(graphics), 10)
check('the first 0.05 s after the stream starts', near(graphics[0].start, 0.05, 0.001), True)

print('teletext')
found = tracks(TS_TELETEXT)
check('teletext with no PMT: one track, its page unknown', [(t['codec'], t.get('page')) for t in found],
      [('dvb_teletext', None)])
default = video_subtitles.read_teletext(str(TS_TELETEXT), found[0]['index'], page=None, pid=found[0]['pid'])
pages = {page: video_subtitles.read_teletext(str(TS_TELETEXT), found[0]['index'], page=page, pid=found[0]['pid'])
         for page in ('150', '799')}
# Subtitle Edit, TransportStreamReadingTest (teletext with and without PTS):
# 3 subtitles on its pages, each starting after 1 s and ending before 10 s.
check('its two subtitle pages hold 3 subtitles', sum(len(segments) for segments in pages.values()), 3)
check('each after 1 s and before 10 s',
      all(s['start'] > 1 and s['end'] < 10 for segments in pages.values() for s in segments), True)
check('the page read when none is chosen is one of them', default in pages.values(), True)

print('ARIB captions')
found = tracks(ARIB_CLEAR)
check('FATE aribcaption_clear: one ARIB track', brief(found), [(0, 'arib_caption', 'text')])
# FFmpeg FATE sub-aribcaption-clear(-ass): a caption at 1 s, cleared at 3 s
# (from the file's start, 1 s); its one character is a DRCS pattern
# libaribcaption does not know, the geta mark.
clear = video_subtitles.read_arib(str(ARIB_CLEAR), 0, pid=found[0]['pid'])
check('a caption from 1 s to 3 s: the geta mark', [(s['start'], s['end'], plain(s['text'])) for s in clear],
      [(1.0, 3.0, '〓')])
found = tracks(ARIB_RUBY)
ruby = video_subtitles.read_arib(str(ARIB_RUBY), 0, pid=found[0]['pid'])
# FFmpeg FATE sub-aribcaption-ruby-ass (rubies ignored): the geta mark at 1 s
# and at 3 s, and at 2 s nothing but a ruby.
base = {s['start']: markup.plain(markup.split_lead(s['text'])[1].split('<rt>')[0]) for s in ruby}
check('aribcaption_ruby: the geta mark at 1 s and 3 s', (base.get(1.0), base.get(3.0)), ('〓', '〓'))
# FFmpeg's probe of the same file (sub-aribcaption-ruby-text) lists three
# captions; the statement at 5 s (from the start, 4 s) is not among them.
known('aribcaption_ruby: when captions start', sorted(base), [1.0, 2.0, 3.0])
# Real broadcasts' data groups: each one's CRC-16, as Subtitld computes it,
# is the one the stream carries (the check reading does would drop it).
from subtitld.modules import aribb24
for label, path in (('aribcaption_clear', ARIB_CLEAR), ('aribcaption_ruby', ARIB_RUBY)):
    groups = [payload for items in aribb24.scan(str(path))[1].values() for _pts, payload in items]
    carried = []
    for payload in groups:
        begin = 3 + (payload[2] & 0x0F)
        end = begin + 5 + (payload[begin + 3] << 8 | payload[begin + 4])
        carried.append(aribb24.crc16(payload[begin:end]) == int.from_bytes(payload[end:end + 2], 'big'))
    check(f'{label}: every data group\'s CRC-16 the one the stream carries, none dropped',
          (len(groups) > 3, all(carried), all(aribb24.data_group(payload) for payload in groups)), (True, True, True))

print('closed captions in a caption track of their own')
found = tracks(MOV_C608)
check('QuickTime c608: a captions track', brief(found), [(1, 'eia_608', 'captions')])
segments, track = captions(MOV_C608)
# Subtitle Edit, MovC608TrackTest: "Hello world" 1-3 s, "Second line" from
# 4 s (its edit list honoured). Within half a frame: CEA-608 pairs go out
# a frame each.
check('c608: two captions, the edit list kept', (track, [plain(s['text']) for s in segments]),
      ('cc1', ['Hello world', 'Second line']))
check('c608: at 1, 3 and 4 s, within half a frame',
      [near(segments[0]['start'], 1.0, 0.017), near(segments[0]['end'], 3.0, 0.017),
       near(segments[1]['start'], 4.0, 0.017)], [True, True, True])
# Subtitle Edit ends the last at the end of the track's last sample (6 s);
# Subtitld gives a caption that is never erased its default length.
known('c608: the last caption ends with the track', near(segments[1]['end'], 6.0, 0.017), True)

segments, track = captions(MOV_C608_CHANGE)
# Subtitle Edit, MovC608TrackTest: "AB" from pair 5 to pair 12 of a sample
# at 1 s, then "CD". This file holds nothing before 1 s (ffprobe's start),
# which Subtitld counts its times from, as for every video.
offset = start_of(MOV_C608_CHANGE)
check('c608, a caption replaced within one sample: "AB" then "CD"', [plain(s['text']) for s in segments], ['AB', 'CD'])
check('"AB" for pairs 5 to 12, "CD" from then',
      [near(segments[0]['start'], 1 + 5 * 1.001 / 30 - offset, 0.002),
       near(segments[0]['end'], 1 + 12 * 1.001 / 30 - offset, 0.002), segments[1]['start'] == segments[0]['end']],
      [True, True, True])

found = tracks(MOV_C708)
check('QuickTime c708 (which FFmpeg does not know): a captions track', brief(found), [(0, 'eia_708', 'captions')])
service, track = captions(MOV_C708)
cc1, _track = captions(MOV_C708, 'cc1')
# Subtitle Edit, MovC708TrackTest: CEA-708 service 1 has 9 captions, CC1 8,
# the first from 0.6 s, "N/A, N/A, N/A".
check('c708: CEA-708 service 1, 9 captions', (track, len(service)), ('708-1', 9))
check('c708: CC1, the first at 0.6 s, "N/A, N/A, N/A"',
      (near(cc1[0]['start'], 0.6, 0.01), plain(cc1[0]['text']).startswith('N/A, N/A, N/A')), (True, True))
# The stream ends while CC1 writes a ninth line ("N/A,"): Subtitld keeps it.
known('c708: CC1, 8 captions', len(cc1), 8)

print('closed captions in the video frames')
ROLL_FIRST, ROLL_SECOND = '(<i>inaudible radio chatter</i>)', '>> Safety remains our number one'
for path in (TS_608_H264, TS_608_MPEG2, MKV_608_H264, MKV_608_MPEG2, MP4_FRAGMENTED, MXF_436M):
    cc1, _track = captions(path, 'cc1')
    service, track = captions(path)
    # Subtitle Edit, TransportStreamClosedCaptionTest, MatroskaClosedCaptionTest,
    # Mp4FragmentedCeaTest, MxfClosedCaptionTest: the roll-up's first line, then
    # the second under it; CEA-708 has the second line too. Subtitld makes a
    # roll-up caption of each line, so the second cue is that line alone.
    check(f'{path.name}: CC1, its two lines', [markup.split_lead(s['text'])[1] for s in cc1[:2]],
          [ROLL_FIRST, ROLL_SECOND])
    check(f'{path.name}: CEA-708 read when none is chosen, its two lines',
          (track, [plain(s['text']) for s in service[:2]]),
          ('708-1', ['( inaudible radio chatter )', ROLL_SECOND]))
    first = {MXF_436M: (0.933, 0.935), MP4_FRAGMENTED: (1.033, 1.035)}.get(path, (0.966, 0.970))
    if path == MP4_FRAGMENTED:
        # Subtitle Edit counts from the file's zero (1.034 s); Subtitld from
        # the first frame, 2 frames (ffprobe's start) later.
        first = tuple(round(value - start_of(path), 3) for value in first)
    check(f'{path.name}: the first from {first[0]}-{first[1]} s', first[0] <= cc1[0]['start'] <= first[1], True)
    if path == TS_608_H264:
        # Subtitle Edit, TransportStreamClosedCaptionTest: the first line until 3.103 s.
        check('the first line until the second comes', round(cc1[0]['end'], 3), 3.103)

for path in (VOB_DVD, MPG_DVD, VOB_DVD_ONE_PTS):
    cc1, track = captions(path)
    # Subtitle Edit, ProgramStreamClosedCaptionTest: DVD captions (GOP user data).
    check(f'{path.name}: DVD captions, CC1, its two lines', (track, [markup.split_lead(s['text'])[1] for s in cc1[:2]]),
          ('cc1', [ROLL_FIRST, ROLL_SECOND]))
    if path != VOB_DVD_ONE_PTS:
        check(f'{path.name}: the second line from 3.101-3.105 s', 3.101 <= cc1[1]['start'] <= 3.105, True)
        # FFmpeg hands a whole GOP's captions to one frame, and Subtitld
        # spreads them a frame each from it: the first line comes 3 frames
        # late. Subtitle Edit times each by its own picture.
        known(f'{path.name}: the first line from 0.966-0.970 s', 0.966 <= cc1[0]['start'] <= 0.970, True)

segments, track = captions(SCTE20)
# FFmpeg FATE sub-cc-scte20: SCTE 20 in MPEG-2 user data. The recording
# starts in the middle of a roll-up line, before any code: "BESIDES THE"
# is kept (Subtitle Edit too). FFmpeg counts from the first frame, Subtitld
# from the file's start: 0.1 s apart at most.
check('FATE scte20.ts: the lines FFmpeg reads, the first one begun before any code',
      [plain(s['text']) for s in segments[:3]],
      ['BESIDES THE', 'SPENDING AND THIS, IS THAT CAR', 'MANUFACTURERS ARE ABOUT AS'])
check('at 0, 1.43 and 3.94 s (FFmpeg), within 0.1 s',
      [near(s['start'], want, 0.1) for s, want in zip(segments, (0.0, 1.43, 3.94))], [True, True, True])
# Subtitle Edit, TransportStreamClosedCaptionTest: its third caption at 3.9-4.1 s.
check('the third at 3.9-4.1 s', 3.9 <= segments[2]['start'] <= 4.1, True)
# The same captions with the pictures cut out: FFmpeg hands on caption data
# only for the frames it decodes, so there are none.
known('Subtitle Edit\'s SCTE 20 sample (no picture slices): its captions', len(captions(TS_SCTE20_STRIPPED)[0]), 3)

segments, track = captions(ROLLUP_M2V, 'cc1')
# FFmpeg FATE sub-cc-realtime: "(" at 0.97 s and ">>" at 3.10 s from its
# first frame, which the raw .m2v puts at 0.1001 s (it has no start time
# for Subtitld to count from). FFmpeg guesses 25 frames a second for it.
check('FATE Closedcaption_rollup.m2v: its two lines', [markup.split_lead(s['text'])[1] for s in segments],
      [ROLL_FIRST, ROLL_SECOND])
check('as FFmpeg times them, within a frame', [near(segments[0]['start'], 0.97 + 0.1001, 0.04),
                                             near(segments[1]['start'], 3.10 + 0.1001, 0.04)], [True, True])

print('written back')
folder = Path(tempfile.mkdtemp())
cc1, _track = captions(MKV_608_H264, 'cc1')
rate = file_io.process_video_file(str(MKV_608_H264))['framerate']
made = embedded_captions.write_video(str(MKV_608_H264), str(folder / 'again.mov'), cc1, fps=rate, duration=5.0,
                                     container='MOV', ffmpeg=FFMPEG, ffprobe=FFPROBE)
again, track = captions(made)
check('CC1 written into a QuickTime caption track and read back: the same lines',
      (track, [plain(s['text']) for s in again]), ('cc1', [plain(s['text']) for s in cc1]))
check('at the same times, within a frame',
      all(near(a['start'], b['start'], 0.034) for a, b in zip(again, cc1)), True)

pages_written = [{'page': page, 'language': 'ger' if page == '150' else 'dan', 'type': 2, 'segments': segments}
                 for page, segments in pages.items()]
(folder / 'again.ts').write_bytes(teletext.transport_stream(pages_written))
for page, segments in pages.items():
    again = teletext.read(str(folder / 'again.ts'), page=page)[0]
    check(f'teletext page {page} written into a transport stream and read back',
          [(s['start'], s['end'], s['text']) for s in again], [(s['start'], s['end'], s['text']) for s in segments])

print()
if differences:
    print('KNOWN DIFFERENCES:', differences)
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
