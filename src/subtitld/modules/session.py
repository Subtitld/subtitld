"""All path definitions for Subtitld"""

import pathlib
import sys
import tempfile
import subprocess
import datetime
import subtitld
import platformdirs

PATH_SUBTITLD = pathlib.Path(subtitld.__file__).parent
PATH_HOME = pathlib.Path.home()
PATH_LOCALE = PATH_SUBTITLD / 'locale'
PATH_SUBTITLD_GRAPHICS = PATH_SUBTITLD / 'graphics'
PATH_SUBTITLD_USER_CONFIG = pathlib.Path(platformdirs.user_config_dir('subtitld'))
PATH_SUBTITLD_USER_CACHE = pathlib.Path(platformdirs.user_cache_dir('subtitld'))
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

PATH_SUBTITLD_DATA_BACKUP = PATH_SUBTITLD_USER_CACHE / 'backup'

if not PATH_SUBTITLD_DATA_BACKUP.exists():
    PATH_SUBTITLD_DATA_BACKUP.mkdir(parents=True)

PATH_SUBTITLD_DATA_MODELS = PATH_SUBTITLD_USER_CACHE / 'models'

if not PATH_SUBTITLD_DATA_MODELS.exists():
    PATH_SUBTITLD_DATA_MODELS.mkdir(parents=True)

PATH_SUBTITLD_DATA_AUDIOSEPARATION = PATH_SUBTITLD_USER_CACHE / 'audioseparation'

if not PATH_SUBTITLD_DATA_AUDIOSEPARATION.exists():
    PATH_SUBTITLD_DATA_AUDIOSEPARATION.mkdir(parents=True)

PATH_SUBTITLD_USER_CONFIG_FILE = PATH_SUBTITLD_USER_CONFIG / 'subtitld.config'

# VERSION_NUMBER = '20.07.0.0'
# if os.path.isfile(os.path.join(PATH_SUBTITLD, 'current_version')):
#     VERSION_NUMBER = open(os.path.join(PATH_SUBTITLD, 'current_version')).read().strip()

LIST_OF_SUPPORTED_VIDEO_EXTENSIONS = {
    'MP4': {'description': 'MPEG-4 Video format', 'extensions': ['mp4']},
    'MKV': {'description': 'Matroska Video format', 'extensions': ['mkv']},
    'MOV': {'description': 'Quicktime Video format', 'extensions': ['mov']},
    'MPG': {'description': 'MPEG Video format', 'extensions': ['mpg']},
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
    'VTT': {'description': 'VTT Subtitle format', 'extensions': ['webvtt', 'vtt']},
    'ASS': {'description': 'SubStation Alpha Subtitle format', 'extensions': ['ass', 'ssa']},
    'SBV': {'description': 'SBV Subtitle format', 'extensions': ['sbv']},
    'SUB': {'description': 'MicroDVD Subtitle format', 'extensions': ['sub']},
    'XML': {'description': 'XML Subtitle format', 'extensions': ['xml']},
    'USF': {'description': 'Universal Subtitle Format', 'extensions': ['usf']},
    'USFX': {'description': 'Universal Subtitle Format bundled with assets', 'extensions': ['usfx']},
    'JSON': {'description': 'JSON format', 'extensions': ['json']},
}

LIST_OF_SUPPORTED_IMPORT_EXTENSIONS = {
    'TXT': {'description': 'Simple TXT file', 'extensions': ['txt']},
    'DOCX': {'description': 'Microsoft Word document (docx)', 'extensions': ['docx']},
    'SRT': {'description': 'SubRip Subtitle format', 'extensions': ['srt']}
}

LIST_OF_SUPPORTED_EXPORT_EXTENSIONS = {
    'TXT': {'description': 'Simple TXT file', 'extensions': ['txt']},
    'KDENLIVE': {'description': 'Kdenlive format', 'extensions': ['kdenlive']}
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


UNSAVED = False
AUTOSAVE_BACKUP_DIRTY = False
AUTOSAVE_LAST_BACKUP = None
AUTOSAVE_LAST_ORIGINAL = None
_unsaved_change_callbacks = []
_autosave_status_callbacks = []
_save_success_callbacks = []
def set_unsaved(value=True):
    global UNSAVED, AUTOSAVE_BACKUP_DIRTY
    old_value = UNSAVED
    UNSAVED = value
    if value:
        AUTOSAVE_BACKUP_DIRTY = True
    if old_value != value:
        for callback in _unsaved_change_callbacks:
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

