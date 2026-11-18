"""All path definitions for Subtitld"""

import pathlib
import sys
import tempfile
import subprocess
import datetime
import subtitld
import platformdirs
from subtitld.modules import collab_state, cue_ids, journal

PATH_SUBTITLD = pathlib.Path(subtitld.__file__).parent
PATH_HOME = pathlib.Path.home()
PATH_LOCALE = PATH_SUBTITLD / 'locale'
PATH_SUBTITLD_GRAPHICS = PATH_SUBTITLD / 'graphics'
PATH_SUBTITLD_USER_CONFIG = pathlib.Path(platformdirs.user_config_dir('subtitld'))
PATH_SUBTITLD_USER_CACHE = pathlib.Path(platformdirs.user_cache_dir('subtitld'))
# User-data dir is *persistent* (unlike cache, which some systems wipe). Used
# for installed AI add-ons that the user explicitly downloaded — losing them
# on a cache cleanup would force a multi-hundred-MB redownload.
PATH_SUBTITLD_USER_DATA = pathlib.Path(platformdirs.user_data_dir('subtitld'))
FFMPEG_EXECUTABLE = 'ffmpeg'
FFPROBE_EXECUTABLE = 'ffprobe'
STARTUPINFO = None

ACTUAL_OS = 'linux'

tempdir = tempfile.TemporaryDirectory()
PATH_TEMP = tempdir.name

if sys.platform == 'darwin':
    ACTUAL_OS = 'macos'
    if getattr(sys, "frozen", False):
        FFMPEG_EXECUTABLE = str(pathlib.Path(PATH_SUBTITLD).parent / 'ffmpeg')
        FFPROBE_EXECUTABLE = str(pathlib.Path(PATH_SUBTITLD).parent / 'ffprobe')
elif sys.platform == 'win32':
    ACTUAL_OS = 'windows'

    if getattr(sys, "frozen", False):
        FFMPEG_EXECUTABLE = pathlib.Path(PATH_SUBTITLD).parent / 'ffmpeg.exe'
        FFPROBE_EXECUTABLE = pathlib.Path(PATH_SUBTITLD).parent / 'ffprobe.exe'
    else:
        script_dir = pathlib.Path(sys.argv[0]).parent
        FFMPEG_EXECUTABLE = script_dir / 'ffmpeg.exe'
        FFPROBE_EXECUTABLE = script_dir / 'ffprobe.exe'
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    STARTUPINFO.wShowWindow = subprocess.SW_HIDE
    import multiprocessing
    multiprocessing.freeze_support()
else:
    # Linux (AppImage, Snap, Flatpak, pip install). When frozen by
    # PyInstaller we ship ffmpeg/ffprobe next to the main executable;
    # for regular installs we fall back to whatever the distro provides.
    if getattr(sys, "frozen", False):
        FFMPEG_EXECUTABLE = str(pathlib.Path(PATH_SUBTITLD).parent / 'ffmpeg')
        FFPROBE_EXECUTABLE = str(pathlib.Path(PATH_SUBTITLD).parent / 'ffprobe')

if not PATH_SUBTITLD_USER_CONFIG.exists():
    PATH_SUBTITLD_USER_CONFIG.mkdir(parents=True)

if not PATH_SUBTITLD_USER_CACHE.exists():
    PATH_SUBTITLD_USER_CACHE.mkdir(parents=True)

if not PATH_SUBTITLD_USER_DATA.exists():
    PATH_SUBTITLD_USER_DATA.mkdir(parents=True)

PATH_SUBTITLD_ADDONS = PATH_SUBTITLD_USER_DATA / 'addons'

if not PATH_SUBTITLD_ADDONS.exists():
    PATH_SUBTITLD_ADDONS.mkdir(parents=True)

PATH_SUBTITLD_DATA_BACKUP = PATH_SUBTITLD_USER_CACHE / 'backup'

if not PATH_SUBTITLD_DATA_BACKUP.exists():
    PATH_SUBTITLD_DATA_BACKUP.mkdir(parents=True)

PATH_SUBTITLD_DATA_MODELS = PATH_SUBTITLD_USER_CACHE / 'models'

if not PATH_SUBTITLD_DATA_MODELS.exists():
    PATH_SUBTITLD_DATA_MODELS.mkdir(parents=True)

PATH_SUBTITLD_DATA_AUDIOSEPARATION = PATH_SUBTITLD_USER_CACHE / 'audioseparation'

if not PATH_SUBTITLD_DATA_AUDIOSEPARATION.exists():
    PATH_SUBTITLD_DATA_AUDIOSEPARATION.mkdir(parents=True)

# Downscaled, video-only proxy files for large (4K/8K) sources. Regenerable
# derived artifacts keyed to the source video (path/size/mtime) → USER_CACHE.
PATH_SUBTITLD_DATA_PROXY = PATH_SUBTITLD_USER_CACHE / 'proxy'

if not PATH_SUBTITLD_DATA_PROXY.exists():
    PATH_SUBTITLD_DATA_PROXY.mkdir(parents=True)

PATH_SUBTITLD_USER_CONFIG_FILE = PATH_SUBTITLD_USER_CONFIG / 'subtitld.config'

# Reference handle for the background extractor that streams heavy USFX
# assets (audio stems, waveform cache) after a project open. Parked here
# so the QThread object stays alive for its lifetime; replaced on each
# project open. None when no extraction is in flight.
USFX_BACKGROUND_LOAD = None

# Reference handle for the off-main-thread speaker thumbnail decoder.
# Same lifetime story as `USFX_BACKGROUND_LOAD`: parked here so the
# QThread isn't GC'd mid-decode; replaced on each project open.
SPEAKER_IMAGE_LOAD = None

# VERSION_NUMBER = '20.07.0.0'
# if os.path.isfile(os.path.join(PATH_SUBTITLD, 'current_version')):
#     VERSION_NUMBER = open(os.path.join(PATH_SUBTITLD, 'current_version')).read().strip()

LIST_OF_SUPPORTED_VIDEO_EXTENSIONS = {
    'MP4': {'description': 'MPEG-4 Video format', 'extensions': ['mp4']},
    'MKV': {'description': 'Matroska Video format', 'extensions': ['mkv']},
    'MOV': {'description': 'Quicktime Video format', 'extensions': ['mov']},
    'MPG': {'description': 'MPEG Video format', 'extensions': ['mpg', 'mpeg']},
    'TS': {'description': 'MPEG Transport Stream video format', 'extensions': ['ts', 'm2ts', 'mts']},
    'WEBM': {'description': 'WebM Video format', 'extensions': ['webm']},
    'OGV': {'description': 'Ogg Video format', 'extensions': ['ogv']},
    'M4V': {'description': 'MPEG-4 Video format', 'extensions': ['m4v']},
}

LIST_OF_SUPPORTED_SUBTITLE_EXTENSIONS = {
    'SRT': {'description': 'SubRip Subtitle format', 'extensions': ['srt']},
    'DFXP': {'description': 'DFXP ITT Subtitle format', 'extensions': ['dfxp', 'itt']},
    'TTML': {'description': 'TTML Subtitle format', 'extensions': ['ttml']},
    'SAMI': {'description': 'SAMI Subtitle format', 'extensions': ['smi', 'sami']},
    'SCC': {'description': 'SCC Subtitle format', 'extensions': ['scc']},
    'STL': {'description': 'EBU STL Subtitle format', 'extensions': ['stl']},
    'MCC': {'description': 'MacCaption Subtitle format', 'extensions': ['mcc']},
    'DCP': {'description': 'DCP Subtitle format (Interop or SMPTE)', 'extensions': ['xml']},
    # A Blu-ray's authoring files: the XML and a PNG a picture. .xml is a
    # DCP's above, which keeps it; file_io tells them apart by the root.
    'BDN': {'description': 'Blu-ray BDN XML with PNG pictures', 'extensions': ['xml']},
    'PAC': {'description': 'Screen PAC Subtitle format', 'extensions': ['pac']},
    'FPC': {'description': 'Screen UniPac Subtitle format', 'extensions': ['fpc']},
    'VTT': {'description': 'VTT Subtitle format', 'extensions': ['webvtt', 'vtt']},
    'HLS': {'description': 'HLS playlist of WebVTT segments', 'extensions': ['m3u8']},
    # WebVTT or TTML in fragmented MP4 (modules.isobmff_text). Not .mp4,
    # which is a video's; the bytes of a .cmft say which it carries.
    'CMFT': {'description': 'CMAF text track (WebVTT or TTML in MP4)', 'extensions': ['cmft']},
    'DASH': {'description': 'DASH manifest of a WebVTT or TTML track', 'extensions': ['mpd']},
    # Smooth Streaming's text track: TTML in PIFF fragments (modules.isobmff_text).
    'ISMT': {'description': 'Smooth Streaming text track (TTML in PIFF)', 'extensions': ['ismt']},
    'ASS': {'description': 'SubStation Alpha Subtitle format', 'extensions': ['ass', 'ssa']},
    'SBV': {'description': 'SBV Subtitle format', 'extensions': ['sbv']},
    'SUB': {'description': 'MicroDVD Subtitle format', 'extensions': ['sub']},
    'XML': {'description': 'YouTube Timed Text format', 'extensions': ['srv3', 'ytt', 'srv1']},
    'USF': {'description': 'Universal Subtitle Format', 'extensions': ['usf']},
    'USFX': {'description': 'Universal Subtitle Format bundled with assets', 'extensions': ['usfx']},
    'JSON': {'description': 'JSON format', 'extensions': ['json', 'json3']},
    'BCC': {'description': 'Bilibili BCC Subtitle format', 'extensions': ['bcc']},
    'LRC': {'description': 'LRC Lyrics format', 'extensions': ['lrc']},
    # UltraStar's songs are .txt, as VPlayer's and its kin's are below; the
    # bytes tell them apart (file_io).
    'ULTRASTAR': {'description': 'UltraStar Karaoke format', 'extensions': ['txt']},
    # Karaoke MIDI: the words of a .kar, or the lyrics of any MIDI file.
    'KAR': {'description': 'KAR MIDI Karaoke format', 'extensions': ['kar', 'mid', 'midi']},
    # The same words as a MIDI file's lyric events (RP-026), which XF's
    # and most players read; KAR above keeps the extensions for opening.
    'MIDI': {'description': 'MIDI file with lyrics', 'extensions': ['mid', 'midi']},
    'KBP': {'description': 'Karaoke Builder Studio project', 'extensions': ['kbp']},
    # Video editors' own: Avid SubCap's .txt is told from the player-era
    # .txt formats below by its bytes, in file_io.
    'AVID': {'description': 'Avid SubCap Subtitle format', 'extensions': ['txt']},
    'FCPXML': {'description': 'Final Cut Pro XML captions', 'extensions': ['fcpxml', 'fcpxmld']},
    'CAP': {'description': 'Cheetah CAP Caption format', 'extensions': ['cap']},
    # Lambda's .cap is text and Cheetah's binary: the bytes tell them apart
    # (file_io), and Cheetah's, first here, keeps the extension.
    'LAMBDACAP': {'description': 'Lambda CAP Japanese Subtitle format', 'extensions': ['cap']},
    'CAVENA890': {'description': 'Cavena 890 Subtitle format', 'extensions': ['890']},
    # Subtitling houses' formats (modules eztitles, ayato, softni, swift,
    # cheetahcap's ASCII, caption_vendors). Those sharing
    # .sub, .txt and .cap come after the formats that already claim them;
    # file_io tells them apart by their bytes.
    'EZTXML': {'description': 'EZTitles XML project', 'extensions': ['eztxml']},
    'AYATO': {'description': 'Ayato Subtitle format', 'extensions': ['aya']},
    'SOFTNI': {'description': 'SoftNi Subtitle format', 'extensions': ['sub']},
    'SOFTNICOLON': {'description': 'SoftNi Subtitle format (colon timecodes)', 'extensions': ['sub']},
    'SWIFTTEXT': {'description': 'Softel Swift text (TIMEIN, DURATION)', 'extensions': ['txt']},
    'SWIFTNUMBERS': {'description': 'Softel Swift text (SUBTITLE, TIMEIN)', 'extensions': ['txt']},
    'SWIFTNUMBERSDURATION': {'description': 'Softel Swift text (SUBTITLE, TIMEIN, DURATION)', 'extensions': ['txt']},
    'XIF': {'description': 'Softel Swift XIF', 'extensions': ['xif']},
    'SIF': {'description': 'Softel Swift Interchange File', 'extensions': ['sif']},
    'CHEETAHASC': {'description': 'Cheetah ASCII Caption format', 'extensions': ['asc']},
    'CAPMAKER': {'description': 'CapMaker Plus Caption format', 'extensions': ['cap']},
    # The DVD authoring tools' scripts (modules.dvd_scripts). DVD Studio
    # Pro's .stl is EBU STL's extension and Spruce's; the rest share .txt,
    # .sub and .sst with formats above and below. The bytes decide, in
    # file_io.
    'DVDSTUDIOPRO': {'description': 'DVD Studio Pro STL Subtitle script', 'extensions': ['stl']},
    'ENCORE': {'description': 'Adobe Encore Subtitle script', 'extensions': ['txt']},
    'DVDARCHITECT': {'description': 'Sony DVD Architect Subtitle script', 'extensions': ['sub', 'txt']},
    'SCENARIST': {'description': 'Sonic Scenarist Subtitle script', 'extensions': ['sst', 'txt']},
    'DVDPRODUCER': {'description': 'Sonic DVD Producer Subtitle script', 'extensions': ['txt']},
    'AVIDDVD': {'description': 'Avid DVD Subtitle script', 'extensions': ['txt']},
    # MP4Box's text tracks as XML; CCExtractor's transcripts share .ttxt and
    # are told apart by their bytes (file_io).
    'TTXT': {'description': 'GPAC TTXT (MP4 Timed Text) format', 'extensions': ['ttxt']},
    # Transcripts of speech research and recognition (modules.transcripts,
    # modules.annotation, modules.audacity, modules.aeneas, modules.tmx).
    # Audacity's .txt and aeneas's .json are told from the formats above
    # that share them by their bytes (file_io).
    'TEXTGRID': {'description': 'Praat TextGrid', 'extensions': ['TextGrid', 'textgrid']},
    'EAF': {'description': 'ELAN Annotation Format', 'extensions': ['eaf']},
    'TRS': {'description': 'Transcriber transcription', 'extensions': ['trs']},
    'EXB': {'description': 'EXMARaLDA basic transcription', 'extensions': ['exb']},
    'CHAT': {'description': 'CHAT transcript (CLAN, TalkBank)', 'extensions': ['cha']},
    'CTM': {'description': 'CTM word timings (NIST, Kaldi)', 'extensions': ['ctm']},
    'AUDACITY': {'description': 'Audacity labels', 'extensions': ['txt']},
    'AENEAS': {'description': 'Aeneas sync map (JSON)', 'extensions': ['json']},
    'TMX': {'description': 'TMX translation memory with times', 'extensions': ['tmx']},
    # Dialects Subtitle Edit reads (modules.xml_dialects, json_dialects):
    # .xml is DCP's and .json Subtitld's own, which keep them; the bytes
    # tell a dialect apart (file_io), and which one is an export option.
    'XMLDIALECT': {'description': 'XML subtitle dialects (Flash, Timed Text 2006, Rhozet, ...)',
                   'extensions': ['xml']},
    'JSONDIALECT': {'description': 'Platform JSON subtitles (Wistia, TED, TwentyThree, ...)',
                    'extensions': ['json', 'atpj', 'clqtt', 'gst']},
    # Xiph's Kate in an Ogg file of its own; an .ogg with no Kate stream in
    # it is no subtitle file (file_io looks at its bytes).
    'KATE': {'description': 'Ogg Kate Subtitle format', 'extensions': ['ogg']},
    # The player era (modules.legacy). These come after the formats above
    # on purpose: three of them share .sub, .stl and .txt with a format
    # already here, and the first entry to claim an extension keeps it.
    # What a file actually holds is decided by its bytes, in file_io.
    'MPL2': {'description': 'MPL2 Subtitle format', 'extensions': ['mpl', 'mpl2']},
    'PJS': {'description': 'Phoenix Japanimation Society format', 'extensions': ['pjs']},
    'MPSUB': {'description': 'MPlayer MPsub format', 'extensions': ['sub']},
    'VPLAYER': {'description': 'VPlayer and TMPlayer format', 'extensions': ['txt']},
    'AQTITLE': {'description': 'AQTitle Subtitle format', 'extensions': ['aqt']},
    'SUBVIEWER1': {'description': 'SubViewer 1.0 Subtitle format', 'extensions': ['sub']},
    'SPRUCE': {'description': 'Spruce Subtitle File format', 'extensions': ['stl']},
    'REALTEXT': {'description': 'RealText Subtitle format', 'extensions': ['rt']},
    'TURBOTITLE': {'description': 'TurboTitle Subtitle format', 'extensions': ['tts']},
    'ZEROG': {'description': 'ZeroG Subtitle format', 'extensions': ['zeg']},
    'VIPLAY': {'description': 'ViPlay Subtitle File format', 'extensions': ['vsf']},
    'SBT': {'description': 'SBT Subtitle format', 'extensions': ['sbt']},
    'CAPTIONS32': {'description': 'Captions 32 Subtitle format', 'extensions': ['txt']},
    'SUBSONIC': {'description': 'SubSonic Subtitle format', 'extensions': ['sub']},
    'ORESME': {'description': 'Oresme Subtitle format', 'extensions': ['txt']},
    'JACOSUB': {'description': 'JACOSub Subtitle format', 'extensions': ['jss', 'js']},
    'SASAMI': {'description': 'Sasami2k Script format', 'extensions': ['s2k']},
    'XOMBIE': {'description': 'XombieSub Subtitle format', 'extensions': ['xss']},
    'MACSUB': {'description': 'MacSubtitler Subtitle format', 'extensions': ['scr', 'mac']},
    'SST': {'description': 'Stream SubText Player format', 'extensions': ['sst']},
    'SSTS': {'description': 'Stream SubText Script format', 'extensions': ['ssts']},
    'DKS': {'description': 'DKS Subtitle format', 'extensions': ['dks']},
    'POWERDIVX': {'description': 'Power DivX Subtitle format', 'extensions': ['psb']},
    'DVDSS': {'description': 'DVD Subtitle System format', 'extensions': ['txt']},
    'QTTEXT': {'description': 'QuickTime QTtext format', 'extensions': ['txt']},
    'CAPTIONATE': {'description': 'Captionate XML format', 'extensions': ['xml']},
    'CMML': {'description': 'Continuous Media Markup Language', 'extensions': ['cmml']},
    'SMILTEXT': {'description': 'SMIL smilText format', 'extensions': ['smil']},
    'SSF': {'description': 'Structured Subtitle Format', 'extensions': ['ssf']},
    'TEXML': {'description': 'QuickTime TeXML format', 'extensions': ['txml']},
}

# Subtitles drawn as pictures, which open through an OCR add-on
# (modules.picture_ocr). They are exported, each subtitle drawn as the
# preview draws it (modules.vobsub, modules.pgs), but are not a format to
# save a project in, as its text would not come back. A VobSub's .sub is
# in the list above, as MicroDVD's, whose extension it shares; its .idx
# names both.
LIST_OF_PICTURE_SUBTITLE_EXTENSIONS = {
    'VOBSUB': {'description': 'DVD VobSub subtitles (pictures)', 'extensions': ['idx']},
    'PGS': {'description': 'Blu-ray PGS subtitles (pictures)', 'extensions': ['sup']},
    # Karaoke graphics, a .cdg alone or beside an .mp3 (MP3+G); drawn with
    # QPainter and wiped as the karaoke codes say (modules.cdg).
    'CDG': {'description': 'CD+G karaoke graphics (MP3+G)', 'extensions': ['cdg']},
    # A DVD's bare subpicture units, which share .sup with PGS; the bytes
    # tell them apart (modules.dvdsup).
    'DVDSUP': {'description': 'DVD SUP subtitles (pictures)', 'extensions': ['sup']},
    # Image files and an index timing them (modules.picture_indexes). .sst
    # is Scenarist's text script's too, and .xml many formats'; the bytes
    # tell them apart, in file_io.
    'SCENARISTBMP': {'description': 'Sonic Scenarist bitmap script (pictures)', 'extensions': ['sst']},
    'SON': {'description': 'DVD Maestro SON script (pictures)', 'extensions': ['son']},
    'DOST': {'description': 'DOST script with PNG pictures', 'extensions': ['dost']},
    'SPUMUX': {'description': 'dvdauthor spumux XML with PNG pictures', 'extensions': ['xml']},
    'TIMEDIMAGES': {'description': 'Timed Images XML with PNG pictures', 'extensions': ['xml']},
    'FCPIMAGE': {'description': 'Final Cut Pro XML with PNG pictures', 'extensions': ['xml']},
    'RHOZET': {'description': 'Rhozet Harmonic XML with PNG pictures', 'extensions': ['xml']},
}

# Formats Subtitld opens but does not write, as no source says how (or the
# one there is disagrees with its own reader). They are offered when
# opening a file and nowhere else; file_io reads them by their bytes.
LIST_OF_READ_ONLY_SUBTITLE_EXTENSIONS = {
    'EZT': {'description': 'EZTitles binary project', 'extensions': ['ezt']},
    'TSB4': {'description': 'TSB4 SubtitleNext Subtitle format', 'extensions': ['sub']},
    'SPOT': {'description': 'Spot Subtitle format', 'extensions': ['spt', 'sptx']},
    'NCI': {'description': 'NCI Caption format', 'extensions': ['cap']},
    'CAPTIONSINC': {'description': 'Captions Inc. Caption format', 'extensions': ['cin']},
    'ULTECH': {'description': 'Ultech 1.30 Caption format', 'extensions': ['ult', 'uld']},
    'RAC': {'description': 'Screen PAC recovery file', 'extensions': ['rac']},
}

LIST_OF_SUPPORTED_IMPORT_EXTENSIONS = {
    'TXT': {'description': 'Simple TXT file', 'extensions': ['txt']},
    'DOCX': {'description': 'Microsoft Word document (docx)', 'extensions': ['docx']},
    'SRT': {'description': 'SubRip Subtitle format', 'extensions': ['srt']}
}

LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS = {
    'MOV': {'description': 'QuickTime video with closed captions', 'extensions': ['mov']},
    'MXF': {'description': 'MXF video with closed captions', 'extensions': ['mxf']},
    # Captions in the video's own frames, as ATSC has them (modules.a53).
    'MP4 A53': {'description': 'MP4 video with closed captions in its frames', 'extensions': ['mp4']},
    'TS A53': {'description': 'MPEG transport stream with closed captions in its frames', 'extensions': ['ts']},
    # The subtitles as a track of their own (modules.soft_subtitles).
    'MP4': {'description': 'MP4 video with a subtitle track', 'extensions': ['mp4', 'm4v']},
    'MOV TEXT': {'description': 'QuickTime video with a subtitle track', 'extensions': ['mov']},
    'MKV': {'description': 'Matroska video with a subtitle track', 'extensions': ['mkv']},
    'TS': {'description': 'MPEG transport stream with teletext subtitles', 'extensions': ['ts']},
    'TS DVB': {'description': 'MPEG transport stream with DVB subtitles', 'extensions': ['ts']},
    # The subtitles in the picture, drawn in a style (modules.burned_video).
    'BURN MP4': {'description': 'MP4 video with the subtitles burned in', 'extensions': ['mp4']},
    'BURN MOV': {'description': 'QuickTime video with the subtitles burned in', 'extensions': ['mov']},
    'BURN MKV': {'description': 'Matroska video with the subtitles burned in', 'extensions': ['mkv']},
    'BURN TS': {'description': 'MPEG transport stream with the subtitles burned in', 'extensions': ['ts']},
    'BURN WEBM': {'description': 'WebM video with the subtitles burned in', 'extensions': ['webm']},
    'ALPHA MOV': {'description': 'QuickTime video of the subtitles alone, transparent', 'extensions': ['mov']},
    'ALPHA MKV': {'description': 'Matroska video of the subtitles alone, transparent', 'extensions': ['mkv']},
    'ALPHA WEBM': {'description': 'WebM video of the subtitles alone, transparent', 'extensions': ['webm']},
    'AVI XSUB': {'description': 'AVI video with DivX (XSUB) subtitles', 'extensions': ['avi']},
    'TS ARIB': {'description': 'MPEG transport stream with ARIB captions', 'extensions': ['ts']},
    'TS SCTE27': {'description': 'MPEG transport stream with SCTE 27 subtitles', 'extensions': ['ts']},
}

# Documents to read (modules.documents).
LIST_OF_SUPPORTED_EXPORT_EXTENSIONS = {
    'CSV': {'description': 'CSV table', 'extensions': ['csv']},
    'TXT': {'description': 'Plain text', 'extensions': ['txt']},
    'DOCX': {'description': 'Word document', 'extensions': ['docx']},
    'ODT': {'description': 'OpenDocument text', 'extensions': ['odt']},
    'PDF': {'description': 'PDF document', 'extensions': ['pdf']},
}

# Projects of video and audio editors (modules.project_export).
LIST_OF_SUPPORTED_PROJECT_EXTENSIONS = {
    'KDENLIVE': {'description': 'Kdenlive project', 'extensions': ['kdenlive']},
    'SHOTCUT': {'description': 'Shotcut project', 'extensions': ['mlt']},
    'RESOLVE': {'description': 'DaVinci Resolve timeline (FCPXML)', 'extensions': ['fcpxml']},
    'FINALCUT': {'description': 'Final Cut Pro project (FCPXML)', 'extensions': ['fcpxml']},
    'OPENSHOT': {'description': 'OpenShot project', 'extensions': ['osp']},
    'ARDOUR': {'description': 'Ardour session', 'extensions': ['ardour']},
    'AUDACITY': {'description': 'Audacity project', 'extensions': ['aup']},
}

LIST_OF_SUPPORTED_AUDIO_EXPORT_EXTENSIONS = {
    'WAV': {'description': 'WAV audio', 'extensions': ['wav']},
    'FLAC': {'description': 'FLAC lossless audio', 'extensions': ['flac']},
    'MP3': {'description': 'MP3 audio', 'extensions': ['mp3']},
    'MP4': {'description': 'MP4 video with dubbed audio', 'extensions': ['mp4']},
}

LANGUAGE_DICT_LIST = {
    'Afrikaans (South Africa)': 'af-za',
    'Amharic (Ethiopia)': 'am-et',
    'Arabic (United Arab Emirates)': 'ar-ae',
    'Arabic (Bahrain)': 'ar-bh',
    'Arabic (Algeria)': 'ar-dz',
    'Arabic (Egypt)': 'ar-eg',
    'Arabic (Israel)': 'ar-il',
    'Arabic (Iraq)': 'ar-iq',
    'Arabic (Jordan)': 'ar-jo',
    'Arabic (Kuwait)': 'ar-kw',
    'Arabic (Lebanon)': 'ar-lb',
    'Arabic (Morocco)': 'ar-ma',
    'Arabic (Oman)': 'ar-om',
    'Arabic (State of Palestine)': 'ar-ps',
    'Arabic (Qatar)': 'ar-qa',
    'Arabic (Saudi Arabia)': 'ar-sa',
    'Arabic (Tunisia)': 'ar-tn',
    'Azerbaijani (Azerbaijan)': 'az-az',
    'Bulgarian (Bulgaria)': 'bg-bg',
    'Bengali (Bangladesh)': 'bn-bd',
    'Bengali (India)': 'bn-in',
    'Catalan (Spain)': 'ca-es',
    'Chinese, Mandarin (Simplified, China)': 'cmn-hans-cn',
    'Chinese, Mandarin (Simplified, Hong Kong)': 'cmn-hans-hk',
    'Chinese, Mandarin (Traditional, Taiwan)': 'cmn-hant-tw',
    'Czech (Czech Republic)': 'cs-cz',
    'Danish (Denmark)': 'da-dk',
    'German (Germany)': 'de-de',
    'Greek (Greece)': 'el-gr',
    'English (Australia)': 'en-au',
    'English (Canada)': 'en-ca',
    'English (United Kingdom)': 'en-gb',
    'English (Ghana)': 'en-gh',
    'English (Ireland)': 'en-ie',
    'English (India)': 'en-in',
    'English (Kenya)': 'en-ke',
    'English (Nigeria)': 'en-ng',
    'English (New Zealand)': 'en-nz',
    'English (Philippines)': 'en-ph',
    'English (Singapore)': 'en-sg',
    'English (Tanzania)': 'en-tz',
    'English (United States)': 'en-us',
    'English (South Africa)': 'en-za',
    'Spanish (Argentina)': 'es-ar',
    'Spanish (Bolivia)': 'es-bo',
    'Spanish (Chile)': 'es-cl',
    'Spanish (Colombia)': 'es-co',
    'Spanish (Costa Rica)': 'es-cr',
    'Spanish (Dominican Republic)': 'es-do',
    'Spanish (Ecuador)': 'es-ec',
    'Spanish (Spain)': 'es-es',
    'Spanish (Guatemala)': 'es-gt',
    'Spanish (Honduras)': 'es-hn',
    'Spanish (Mexico)': 'es-mx',
    'Spanish (Nicaragua)': 'es-ni',
    'Spanish (Panama)': 'es-pa',
    'Spanish (Peru)': 'es-pe',
    'Spanish (Puerto Rico)': 'es-pr',
    'Spanish (Paraguay)': 'es-py',
    'Spanish (El Salvador)': 'es-sv',
    'Spanish (United States)': 'es-us',
    'Spanish (Uruguay)': 'es-uy',
    'Spanish (Venezuela)': 'es-ve',
    'Basque (Spain)': 'eu-es',
    'Persian (Iran)': 'fa-ir',
    'Finnish (Finland)': 'fi-fi',
    'Filipino (Philippines)': 'fil-ph',
    'French (Canada)': 'fr-ca',
    'French (France)': 'fr-fr',
    'Galician (Spain)': 'gl-es',
    'Gujarati (India)': 'gu-in',
    'Hebrew (Israel)': 'he-il',
    'Hindi (India)': 'hi-in',
    'Croatian (Croatia)': 'hr-hr',
    'Hungarian (Hungary)': 'hu-hu',
    'Armenian (Armenia)': 'hy-am',
    'Indonesian (Indonesia)': 'id-id',
    'Icelandic (Iceland)': 'is-is',
    'Italian (Italy)': 'it-it',
    'Japanese (Japan)': 'ja-jp',
    'Javanese (Indonesia)': 'jv-id',
    'Georgian (Georgia)': 'ka-ge',
    'Khmer (Cambodia)': 'km-kh',
    'Kannada (India)': 'kn-in',
    'Korean (South Korea)': 'ko-kr',
    'Lao (Laos)': 'lo-la',
    'Lithuanian (Lithuania)': 'lt-lt',
    'Latvian (Latvia)': 'lv-lv',
    'Malayalam (India)': 'ml-in',
    'Marathi (India)': 'mr-in',
    'Malay (Malaysia)': 'ms-my',
    'Norwegian Bokmal (Norway)': 'nb-no',
    'Nepali (Nepal)': 'ne-np',
    'Dutch (Netherlands)': 'nl-nl',
    'Polish (Poland)': 'pl-pl',
    'Portuguese (Brazil)': 'pt-br',
    'Portuguese (Portugal)': 'pt-pt',
    'Romanian (Romania)': 'ro-ro',
    'Russian (Russia)': 'ru-ru',
    'Sinhala (Sri Lanka)': 'si-lk',
    'Slovak (Slovakia)': 'sk-sk',
    'Slovenian (Slovenia)': 'sl-si',
    'Serbian (Serbia)': 'sr-rs',
    'Sundanese (Indonesia)': 'su-id',
    'Swedish (Sweden)': 'sv-se',
    'Swahili (Kenya)': 'sw-ke',
    'Swahili (Tanzania)': 'sw-tz',
    'Tamil (India)': 'ta-in',
    'Tamil (Sri Lanka)': 'ta-lk',
    'Tamil (Malaysia)': 'ta-my',
    'Tamil (Singapore)': 'ta-sg',
    'Telugu (India)': 'te-in',
    'Thai (Thailand)': 'th-th',
    'Turkish (Turkey)': 'tr-tr',
    'Ukrainian (Ukraine)': 'uk-ua',
    'Urdu (India)': 'ur-in',
    'Urdu (Pakistan)': 'ur-pk',
    'Vietnamese (Vietnam)': 'vi-vn',
    'Chinese, Cantonese (Traditional, Hong Kong)': 'yue-hant-hk',
    'Zulu (South Africa)': 'zu-za'
}

CONFIG = {}

SUBTITLE = {
    'segments': [],
}

SPEAKERS = {}

VIDEO = {}

FORMAT = {}

LAST_EXPORT = None  # {'filepath', 'format', 'audio_format', 'audio_config', 'extension'}
_last_export_callbacks = []
def set_last_export(info):
    global LAST_EXPORT
    LAST_EXPORT = info
    for callback in _last_export_callbacks:
        callback()

REPEAT_DURATION_BUFFER = []

def add_to_recent_files(subtitle_filepath, video_filepath=None):
    """Insert (or refresh) a recent-files entry for the given subtitle file.
    Refreshes `last_opened`, preserves `last_position`, and updates the linked
    video path when one is provided. Safe to call repeatedly."""
    if not subtitle_filepath:
        return
    if not isinstance(CONFIG, dict):
        return
    key = str(subtitle_filepath)
    recent = CONFIG.setdefault('recent_files', {})
    entry = recent.setdefault(key, {'last_position': 0})
    if video_filepath:
        entry['video_filepath'] = str(video_filepath)
    elif 'video_filepath' not in entry:
        entry['video_filepath'] = ''
    entry['last_opened'] = str(datetime.datetime.now().timestamp())


def persist_current_playback_position():
    """Write the current playback position into the open subtitle's
    recent-files entry so it can be restored on the next open. No-op if
    no project is loaded or its recent-files entry is missing — callers
    don't need to gate on those."""
    if not isinstance(CONFIG, dict):
        return
    filepath = SUBTITLE.get('filepath')
    if not filepath:
        return
    recent = CONFIG.get('recent_files')
    if not isinstance(recent, dict):
        return
    entry = recent.get(str(filepath))
    if not isinstance(entry, dict):
        return
    try:
        entry['last_position'] = float(SUBTITLE.get('position', 0) or 0)
    except (TypeError, ValueError):
        entry['last_position'] = 0


UNSAVED = False
AUTOSAVE_BACKUP_DIRTY = False
AUTOSAVE_LAST_BACKUP = None
AUTOSAVE_LAST_ORIGINAL = None
_unsaved_change_callbacks = []
_autosave_status_callbacks = []
_save_success_callbacks = []
# Called on every set_unsaved() — after any edit, not just when the
# unsaved state flips — for views that mirror the whole document.
_document_change_callbacks = []
# Called once a project is opened or started (document_loaded): a shared one connects (modules.collab_service).
_document_loaded_callbacks = []
# Called before each change is recorded, and when a project opens: what the document holds is brought up to
# date, such as each dub file's hash (modules.collab_media).
_before_capture_callbacks = []
def capture_changes():
    """Record what the document's last edit changed (modules.journal)."""
    # Whatever the edit made, every subtitle leaves it with its own ID.
    cue_ids.ensure_ids(SUBTITLE.get('segments'))
    _before_capture()
    return journal.capture(SUBTITLE.get('segments'), SPEAKERS, SUBTITLE)


def _before_capture():
    for callback in list(_before_capture_callbacks):
        try:
            callback()
        except Exception:
            import logging
            logging.getLogger(__name__).exception('session: a before-capture step failed')


def document_loaded():
    """A project was opened or started: the journal starts from it, so that
    opening it is not recorded as an edit, and it is shared for co-editing
    if its file says so (modules.collab_state)."""
    cue_ids.ensure_ids(SUBTITLE.get('segments'))
    _before_capture()
    journal.reset(SUBTITLE.get('segments'), SPEAKERS, SUBTITLE)
    collab_state.opened()
    for callback in list(_document_loaded_callbacks):
        callback()


def set_unsaved(value=True):
    global UNSAVED, AUTOSAVE_BACKUP_DIRTY
    old_value = UNSAVED
    UNSAVED = value
    if value:
        AUTOSAVE_BACKUP_DIRTY = True
    capture_changes()
    if old_value != value:
        for callback in _unsaved_change_callbacks:
            callback()
    for callback in list(_document_change_callbacks):
        callback()


def notify_save_success():
    """Fire after the document is successfully saved (manual save or autosave
    of the original file). UI hooks listen to play a 'save success' visual
    cue. Backup snapshots intentionally don't fire this — they're too frequent
    to tie to a noticeable animation."""
    for callback in _save_success_callbacks:
        try:
            callback()
        except Exception:
            pass

