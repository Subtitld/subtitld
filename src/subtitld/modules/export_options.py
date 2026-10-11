"""What an export of subtitles writes, by the options the export dialog sets.

The options apply to the formats that hold only timed text (SRT, VTT, ASS,
DFXP, TTML, SAMI, SCC, MCC, STL, SBV, SUB, BCC); USF, USFX and JSON keep
the project as it is.

    text         'original', 'translation' (in `language`, falling back to
                 the original where a subtitle has none) or 'both' (the
                 original, then the translation below it; SAMI: each as a
                 language of its own)
    language     the translation's language code
    speakers     'none'; 'prefix': the speaker's name before the text; or
                 'voice', the format's own way: WebVTT's voice tags
                 (<v Name>), TTML's agents, SAMI's Source line, SCC's
                 and MCC's ">>" at each change of speaker, SBV's ">> NAME: "
    formatting   'keep' the formatting tags (modules.markup), or 'remove'
                 them (SBV has none to keep)
    offset       seconds to shift every subtitle by; one that ends up
                 before 0 is left out, one that starts before 0 starts at 0

And for a format of its own:

    encoding     SRT, SAMI and SUB: 'utf-8', 'utf-8-sig' (with a byte
                 order mark), 'utf-16', 'cp1252' (Western European),
                 'cp1250' (Central European), 'cp1251' (Cyrillic) or
                 'cp949' (Korean)
    line_ends    SRT, SAMI and SUB: 'lf' or 'crlf'
    fps          SUB: the frame rate its frame numbers count in
    timecode     SCC: 'drop' (drop-frame, 00:00:01;00) or 'non-drop'
    mcc_rate     MCC: its frame rate, a key of modules.mcc.RATES ('29.97DF' ...)
    mcc_captions MCC: 'both' (CEA-608 and CEA-708) or '608' (CEA-608 alone)
    stl_fps      STL: 25 (STL25.01) or 30 (STL30.01, for 29.97 video)
    display      STL: 'teletext' (level 1) or 'open' subtitles
    start        STL: the programme's start, '00:00:00:00' or '10:00:00:00'
    look         ASS and TTML: their style 'clean' (white, a black
                 outline) or as Subtitld's 'preview' draws (modules.ass,
                 modules.ttml)
"""

from subtitld.modules import markup

TEXT_FORMATS = ('SRT', 'VTT', 'ASS', 'DFXP', 'TTML', 'SAMI', 'SCC', 'MCC', 'STL', 'SBV', 'SUB', 'BCC')
ENCODINGS = ('utf-8', 'utf-8-sig', 'utf-16', 'cp1252', 'cp1250', 'cp1251', 'cp949')
LINE_ENDS = {'lf': '\n', 'crlf': '\r\n'}
FRAME_RATES = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)
TIMECODES = ('drop', 'non-drop')

DEFAULTS = {
    'text': 'original',
    'language': '',
    'speakers': 'none',
    'formatting': 'keep',
    'offset': 0.0,
    'encoding': 'utf-8',
    'line_ends': 'lf',
}


def language_tag(language):
    """BCP 47's way of writing a language, as files declare it: 'pt-BR' for
    Subtitld's 'pt-br' (or 'pt_BR')."""
    parts = str(language or 'en').replace('_', '-').split('-')
    return '-'.join([parts[0].lower()] + [part.upper() if len(part) == 2 else part for part in parts[1:]])


def translation_languages(segments):
    """The languages the subtitles have translations in, sorted."""
    found = set()
    for segment in segments or []:
        translations = segment.get('translations')
        if isinstance(translations, dict):
            found.update(language for language, text in translations.items() if str(text or '').strip())
    return sorted(found)


def _with_speaker(text, name):
    """`text` with "Name: " before it, after any override codes it starts
    with (so a {\\an8} still leads)."""
    lead = ''
    rest = text
    while rest.startswith('{\\') and '}' in rest:
        end = rest.index('}') + 1
        lead, rest = lead + rest[:end], rest[end:]
    return f'{lead}{name}: {rest}'


def prepare(segments, options=None):
    """Copies of `segments` with the text and times the options ask for,
    in order of start."""
    settings = dict(DEFAULTS)
    settings.update({key: value for key, value in (options or {}).items() if value is not None})
    language = settings['language']
    offset = float(settings['offset'] or 0)
    prepared = []
    for segment in sorted(segments or [], key=lambda s: float(s.get('start', 0))):
        original = str(segment.get('text') or '')
        translations = segment.get('translations') if isinstance(segment.get('translations'), dict) else {}
        translated = str(translations.get(language) or '').strip() if language else ''
        if settings['text'] == 'translation' and translated:
            text = translated
        elif settings['text'] == 'both' and translated:
            text = original.rstrip() + '\n' + translated
        else:
            text = original
        speaker = str(segment.get('speaker') or '').strip()
        if settings['speakers'] == 'prefix' and speaker and markup.plain(text).strip():
            text = _with_speaker(text, speaker)
        if settings['formatting'] == 'remove':
            text = markup.plain(text)
        start = float(segment.get('start', 0)) + offset
        end = float(segment.get('end', 0)) + offset
        if end <= 0:
            continue
        copy = dict(segment)
        copy.update(start=max(0.0, round(start, 3)), end=round(end, 3), text=text)
        prepared.append(copy)
    return prepared
