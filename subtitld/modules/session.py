"""All path definitions for Subtitld"""

import pathlib
import sys
import tempfile
import subprocess
import subtitld
import os
import platformdirs

PATH_SUBTITLD = pathlib.Path(subtitld.__file__).parent

print(PATH_SUBTITLD)

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
    
    # FFMPEG_EXECUTABLE = PATH_SUBTITLD_USER_CONFIG / 'ffmpeg'
    # FFPROBE_EXECUTABLE = PATH_SUBTITLD_USER_CONFIG / 'ffprobe'
    # try:
    #     from Foundation import NSURL
    # except ImportError:
    #     sys.path.append('/System/Library/Frameworks/Python.framework/Versions/2.7/Extras/lib/python/PyObjC')
    #     from Foundation import NSURL
elif sys.platform == 'win32':
    ACTUAL_OS = 'windows'
    
    if getattr(sys, "frozen", False):
        # PATH_SUBTITLD = pathlib.Path(PATH_SUBTITLD).parent
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


    # FFMPEG_EXECUTABLE = pathlib.Path('ffmpeg').resolve()
    # FFPROBE_EXECUTABLE = pathlib.Path('ffprobe').resolve()

print(FFMPEG_EXECUTABLE)


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

CONFIG = {}

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

SUBTITLE = {
    'segments': [],
}

SPEAKERS = {}

VIDEO = {}

CONFIG = {}

REPEAT_DURATION_BUFFER = []

UNSAVED = False
_unsaved_change_callbacks = []
def set_unsaved(value=True):
    global UNSAVED
    old_value = UNSAVED
    UNSAVED = value
    if old_value != value:
        for callback in _unsaved_change_callbacks:
            callback()

