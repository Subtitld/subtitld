"""What an export of subtitles writes, by the options the export dialog sets.

The options apply to the formats that hold only timed text (SRT, VTT, HLS,
CMFT, DASH, ASS, DFXP, TTML, SAMI, SCC, MCC, STL, PAC, FPC, CAP, LAMBDACAP,
CAVENA890, the subtitling houses' formats (EZTXML, AYATO, SOFTNI,
SOFTNICOLON, the three Swift text layouts, XIF, SIF, CHEETAHASC, CAPMAKER),
AVID, FCPXML, DCP, BDN, the DVD authoring scripts, SBV, SUB, BCC, XML, LRC,
UltraStar, KAR, MIDI lyrics, KBP, TTXT, KATE, ISMT, XMLDIALECT, JSONDIALECT,
the MXF track files (DCPMXF, IMSCMXF, STLMXF), EDL, EDIUS, PREMIERE, DETX,
the player-era formats and the transcripts: TextGrid, EAF, TRS, EXB, CHAT,
CTM, AUDACITY, AENEAS and TMX) and to VOBSUB, PGS, DVDSUP, CDG and the
indexes of pictures (SCENARISTBMP, SON, DOST, SPUMUX, TIMEDIMAGES, FCPIMAGE,
RHOZET), whose pictures are drawn of the text they write; USF, USFX and JSON
keep the project as it is. TMX writes the original and every translation
side by side, whatever `text` asks. Karaoke timing is formatting
(modules.karaoke): removing the formatting takes the word timing of ASS, LRC,
UltraStar, KAR, MIDI, KBP, TTXT, WebVTT's timestamps and the wipe of CDG
with it, and CTM's words share out their subtitle's time instead.

    text         'original', 'translation' (in `language`, falling back to
                 the original where a subtitle has none) or 'both' (the
                 original, then the translation below it; SAMI and FCPXML:
                 each as a language of its own)
    language     the translation's language code
    speakers     'none'; 'prefix': the speaker's name before the text; or
                 'voice', the format's own way: WebVTT's (and HLS's) voice tags
                 (<v Name>), TTML's agents, SAMI's Source line, SCC's
                 and MCC's ">>" at each change of speaker, SBV's ">> NAME: ",
                 a tier a speaker in TextGrid, EAF and EXB, the speakers
                 (participants) of TRS and CHAT,
                 LRC's Walaoke parts ("M:", "F:", "D:")
    formatting   'keep' the formatting tags (modules.markup), or 'remove'
                 them (SBV has none to keep)
    offset       seconds to shift every subtitle by; one that ends up
                 before 0 is left out, one that starts before 0 starts at 0
    timed        a subtitle timed word by word (modules.segment), in a format
                 with no timing inside a subtitle: 'words', the subtitle
                 whole, or 'reveal', its words shown one after another, a
                 subtitle a word (DFXP and TTML: a span a word, in one
                 paragraph). Karaoke formats, and WebVTT's timestamps, keep
                 the timing either way.

And for a format of its own:

    encoding     SRT, SAMI, SUB, LRC, AVID and the DVD authoring scripts
                 (DVDSTUDIOPRO, ENCORE, DVDARCHITECT, SCENARIST, DVDPRODUCER,
                 AVIDDVD): 'utf-8', 'utf-8-sig' (with a byte order mark),
                 'utf-16', 'cp1252' (Western European), 'cp1250' (Central
                 European), 'cp1251' (Cyrillic) or 'cp949' (Korean)
    line_ends    SRT, SAMI, SUB, LRC, AVID and the DVD authoring scripts:
                 'lf' or 'crlf'
    fps          the subtitling houses' formats (EZTXML: the time code
                 standard of this rate, 30drop at 29.97, 60drop at 59.94),
                 SUB, PAC, FPC, CAP, LAMBDACAP, CAVENA890, AVID, EDL, EDIUS,
                 PREMIERE, DETX and the DVD authoring scripts: the frame
                 rate its frame numbers count in; FCPXML: its sequence's,
                 whose frames the captions are on; XMLDIALECT and
                 JSONDIALECT: the rate of the dialects that count in frames
                 (Rhozet Harmonic, JSON Type 16)
    timecode     SCC: 'drop' (drop-frame, 00:00:01;00) or 'non-drop'; AVID,
                 FCPXML, LAMBDACAP, EDL and PREMIERE the same, at 29.97 and 59.94, the
                 rates that have it; DVDSTUDIOPRO and ENCORE too, where it
                 is 'non-drop' unless asked
    script_layout
                 the DVD authoring scripts: which of the format's layouts
                 to write, one of modules.dvd_scripts.LAYOUTS; a file
                 opened keeps its own
    script_header
                 DVDSTUDIOPRO and SCENARIST: the header lines of the file
                 opened, written back above the subtitles
    timeline_start
                 AVID, FCPXML and EDL: the timecode the editor's timeline
                 starts at, '00:00:00:00' or '01:00:00:00'
    lrc_times    LRC: 'hundredths', 'milliseconds' (three digits) or
                 'no_end' (hundredths, no moment where a line ends; each
                 line until the next), modules.lrc.DIALECTS; a file opened
                 keeps its own
    cdg_mp3      CDG: whether the video's sound is written beside the .cdg as
                 an MP3 by the same name, which makes them MP3+G
    edl_style    EDL: how the subtitles go in it, one of modules.edl.STYLES:
                 'events' (an event each, its text the clip name and
                 comments), 'locators' (Avid's * LOC: lines) or 'markers'
                 (DaVinci Resolve's marker EDL)
    marker_version
                 EDIUS: the CSV's format version, 2 or 3, kept from the
                 file opened (a .xml is version 4's XML)
    marker_units EDIUS: 'frames', or 'ms' as a file opened had its times
    mcc_rate     MCC: its frame rate, a key of modules.mcc.RATES ('29.97DF' ...)
    mcc_captions MCC: 'both' (CEA-608 and CEA-708), '608' (CEA-608 alone) or
                 'op47' (both, and the subtitles as OP-47 teletext beside
                 them, page 888)
    caption_mode SCC and MCC: how the captions are written — 'pop' (pop-on,
                 as prepared captions are), 'roll2', 'roll3', 'roll4'
                 (roll-up of that many rows, as live captions are) or
                 'paint' (paint-on). A video written with captions in it
                 takes the same choice (modules.embedded_captions)
    caption_channel
                 SCC: 1 or 2 (CC1 or CC2, the channels one field holds);
                 MCC: 1 to 4, CC3 and CC4 being the second field's
    caption_service_language
                 MCC: a translation's language code to write beside the
                 captions as CEA-708's service 2, where a second language
                 goes; '' for none
    stl_fps      STL and STLMXF: 25 (STL25.01) or 30 (STL30.01, for 29.97 video)
    display      STL and STLMXF: 'teletext' (level 1) or 'open' subtitles
    start        STL, STLMXF and CAVENA890: the programme's start,
                 '00:00:00:00' or '10:00:00:00'
    dcp_standard DCP: 'interop' (CineCanvas's <DCSubtitle>) or 'smpte'
                 (SMPTE ST 428-7's <SubtitleReel>; modules.dcp)
    dcp_fps      DCP and DCPMXF: the frame rate a SMPTE reel counts its
                 times in, one of modules.dcp.RATES (24, 25, 30, 48, 50, 60)
    dcp_content  DCP and DCPMXF: 'text', or 'pictures': each subtitle drawn
                 as a PNG, beside the file (named in Interop, by UUID in
                 SMPTE) or, in DCPMXF, in the track file
                 (modules.dcp.CONTENTS)
    bdn_fps      BDN: the frame rate its events count in, one of
                 modules.bdn.RATES (23.976, 24, 25, 29.97, 50, 59.94); each
                 subtitle is drawn as a PNG beside the file
    pac_code_page
                 PAC: 'auto' (the language's, else the first that holds
                 the text) or one of modules.pac.CODE_PAGES ('latin',
                 'greek', 'cyrillic', 'korean' ...); a page that does not
                 hold the text gives way to one that does
    look         ASS, TTML, IMSCMXF, and CMFT and DASH's TTML: their style 'clean' (white, a black
                 outline) or as Subtitld's 'preview' draws (modules.ass,
                 modules.ttml)
    profile      TTML and DFXP: the profile the file declares itself to be,
                 a value of modules.ttml.PROFILES ('ttml1', 'imsc1',
                 'imsc1.1', 'ebu-tt-d', 'smpte-tt', 'ebu-tt', 'arib-ttml', 'dapt'), or
                 of its IMAGE_PROFILES ('imsc1-image', 'imsc1.1-image'),
                 which draw each subtitle as a PNG beside the file
    tunnel_608   TTML and DFXP in SMPTE-TT: whether the file carries the
                 subtitles as CEA-608 captions too (RP 2052-10)
    xml_dialect  XMLDIALECT: which of modules.xml_dialects.WRITABLE to write
                 ('flash', 'ttaf2006', 'rhozet', 'timexml', 'timexml2'); a
                 file opened in one keeps it
    json_dialect JSONDIALECT: which of modules.json_dialects.WRITABLE to
                 write ('wistia', 'ted', 'podcastindex', ...); a file opened
                 in one keeps it
    segment_duration
                 HLS, CMFT, DASH and ISMT: the seconds each segment (a fragment
                 of a CMAF track) lasts (6, as Apple recommends;
                 modules.hls.SEGMENT_DURATIONS)
    mp4_text     CMFT and DASH: what the track carries, 'wvtt' (WebVTT) or
                 TTML in a profile, 'imsc1.1', 'imsc1' or 'ebu-tt-d'
                 (modules.isobmff_text.CARRIAGES)
    mpegts       HLS: the MPEG-2 timestamp each segment maps its cue time
                 0 to (900000, 10 s at 90 kHz, where most encoders start
                 a stream's clock; or 0)
    vobsub_size  VOBSUB, DVDSUP, SCENARISTBMP, SON, DOST, SPUMUX (a DVD's
                 pictures): the frame its pictures are placed on, 'video' (the
                 video's), 'pal' (720x576) or 'ntsc' (720x480), a key of
                 modules.vobsub.SIZES (modules.vobsub.frame_size)
"""

import re

from subtitld.modules import karaoke, markup
from subtitld.modules.segment import text_of, timed_pieces

TEXT_FORMATS = ('SRT', 'VTT', 'HLS', 'CMFT', 'DASH', 'ASS', 'DFXP', 'TTML', 'SAMI', 'SCC', 'MCC', 'STL', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890',
                'EZTXML', 'AYATO', 'SOFTNI', 'SOFTNICOLON', 'SWIFTTEXT', 'SWIFTNUMBERS', 'SWIFTNUMBERSDURATION', 'XIF', 'SIF', 'CHEETAHASC', 'CAPMAKER',
                'AVID', 'FCPXML', 'DCP', 'BDN', 'DVDSTUDIOPRO', 'ENCORE', 'DVDARCHITECT', 'SCENARIST', 'DVDPRODUCER', 'AVIDDVD', 'SBV', 'SUB', 'BCC', 'XML', 'LRC', 'ULTRASTAR', 'KAR', 'MIDI', 'KBP', 'TTXT', 'DCPMXF', 'IMSCMXF', 'STLMXF', 'EDL', 'EDIUS', 'PREMIERE', 'DETX', 'KATE',
                'TEXTGRID', 'EAF', 'TRS', 'EXB', 'CHAT', 'CTM', 'AUDACITY', 'AENEAS', 'TMX',
                'ISMT', 'XMLDIALECT', 'JSONDIALECT',
                'MPL2', 'PJS', 'MPSUB', 'VPLAYER', 'AQTITLE', 'SUBVIEWER1', 'SPRUCE', 'REALTEXT',
                'TURBOTITLE', 'ZEROG', 'VIPLAY', 'SBT', 'CAPTIONS32', 'SUBSONIC', 'ORESME',
                'JACOSUB', 'SASAMI', 'XOMBIE', 'MACSUB', 'SST', 'SSTS', 'DKS', 'POWERDIVX', 'DVDSS', 'QTTEXT', 'CAPTIONATE', 'CMML', 'SMILTEXT', 'SSF', 'TEXML',
                'VOBSUB', 'PGS', 'DVDSUP', 'CDG',
                'SCENARISTBMP', 'SON', 'DOST', 'SPUMUX', 'TIMEDIMAGES', 'FCPIMAGE', 'RHOZET')
_KARAOKE_CODE = re.compile(r'\{[^{}]*\\(?:kf|ko|k|K)\d')

# The formats that read a subtitle's timing word by word as karaoke codes in
# its text (modules.karaoke): their copies of a timed subtitle carry them.
KARAOKE_FORMATS = ('ASS', 'LRC', 'ULTRASTAR', 'KAR', 'MIDI', 'KBP', 'CDG', 'TTXT', 'CTM')
# WebVTT writes the codes as its timestamps, which leave the words shown and
# only say when each is sung; TTML shows a timed span only while it is
# active, so its words are timed only to be revealed one after another.
INLINE_TIMED_FORMATS = ('VTT',)
REVEALED_FORMATS = ('DFXP', 'TTML')


def karaoke_codes_for(fmt, options=None):
    """Whether an export to `fmt` with `options` writes a timed subtitle's
    timing as karaoke codes, which its writer turns into its own."""
    options = options or {}
    if options.get('formatting') == 'remove':
        return False
    if fmt in KARAOKE_FORMATS or fmt in INLINE_TIMED_FORMATS:
        return True
    return fmt in REVEALED_FORMATS and options.get('timed') == 'reveal'
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
    'timed': 'words',
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
    with (so a {\\an8} still leads), and before karaoke codes (the name
    takes no time)."""
    lead = ''
    rest = text
    while rest.startswith('{\\') and '}' in rest and not _KARAOKE_CODE.match(rest):
        end = rest.index('}') + 1
        lead, rest = lead + rest[:end], rest[end:]
    return f'{lead}{name}: {rest}'


def prepare(segments, options=None, karaoke_codes=False):
    """Copies of `segments` with the text and times the options ask for,
    in order of start. A subtitle timed word by word (modules.segment) gives
    its words; with `karaoke_codes` (a format that reads them: KARAOKE_FORMATS,
    INLINE_TIMED_FORMATS) its words with their timing as karaoke codes; with
    `timed` 'reveal', for a format that does not, one copy a piece, each
    showing the words up to it, from its start to the next one's. Removing
    the formatting takes the timing with it."""
    settings = dict(DEFAULTS)
    settings.update({key: value for key, value in (options or {}).items() if value is not None})
    language = settings['language']
    offset = float(settings['offset'] or 0)
    remove = settings['formatting'] == 'remove'
    prepared = []
    for segment in sorted(segments or [], key=lambda s: float(s.get('start', 0))):
        translations = segment.get('translations') if isinstance(segment.get('translations'), dict) else {}
        translated = str(translations.get(language) or '').strip() if language else ''
        pieces = [] if remove or (settings['text'] == 'translation' and translated) else timed_pieces(segment)
        start = float(segment.get('start', 0))
        if pieces and karaoke_codes:
            shown = [(start, float(segment.get('end', 0)), karaoke.codes_of(pieces))]
        elif pieces and settings['timed'] == 'reveal':
            shown = _revealed(segment, pieces)
        else:
            shown = [(start, float(segment.get('end', 0)), text_of(segment))]
        for number, (begin, end, original) in enumerate(shown):
            if settings['text'] == 'translation' and translated:
                text = translated
            elif settings['text'] == 'both' and translated:
                text = original.rstrip() + '\n' + translated
            else:
                text = original
            speaker = str(segment.get('speaker') or '').strip()
            if settings['speakers'] == 'prefix' and speaker and markup.plain(text).strip():
                text = _with_speaker(text, speaker)
            if remove:
                text = markup.plain(text)
            begin, end = begin + offset, end + offset
            if end <= 0:
                continue
            copy = dict(segment)
            copy.update(start=max(0.0, round(begin, 3)), end=round(end, 3), text=text)
            if number and copy.get('id'):
                copy['id'] = f'{copy["id"]}-{number}'
            prepared.append(copy)
    return prepared


def _revealed(segment, pieces):
    """[(start, end, text)] for a subtitle revealed word by word: one a
    piece, from its start to the next one's (the last to the subtitle's
    end), with the words up to it, their formatting closed. A piece taking
    no time shows with the next."""
    start, end = float(segment.get('start', 0)), float(segment.get('end', 0))
    out = []
    for number, piece in enumerate(pieces):
        begin = start + float(piece['start'])
        until = start + float(pieces[number + 1]['start']) if number + 1 < len(pieces) else end
        if until <= begin:
            continue
        words = ''.join(earlier['text'] for earlier in pieces[:number + 1]).rstrip()
        out.append((begin, until, markup.rebalance(words, '')[0]))
    return out or [(start, end, text_of(segment))]
