"""The XML of three tools that transcribe and annotate speech: ELAN's EAF,
Transcriber's TRS and EXMARaLDA's basic transcription (EXB).

ELAN (.eaf) follows the EAF schema, version 3.0 (EAFv3.0.xsd, MPI for
Psycholinguistics): a TIME_ORDER of TIME_SLOTs (milliseconds; a slot with
no TIME_VALUE is unaligned, and takes its place between the aligned ones
around it), TIERs of ANNOTATIONs, each ALIGNABLE (two slots) or a
REF_ANNOTATION (on an annotation of the parent tier), a tier's
LINGUISTIC_TYPE saying whether it is time-aligned and how it hangs from its
parent (the CONSTRAINT stereotypes), and LANGUAGEs a tier refers to.

    <TIME_ORDER><TIME_SLOT TIME_SLOT_ID="ts1" TIME_VALUE="1000"/> ...</TIME_ORDER>
    <TIER TIER_ID="Mary" PARTICIPANT="Mary" LINGUISTIC_TYPE_REF="utterance" LANG_REF="eng">
      <ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a1" TIME_SLOT_REF1="ts1" TIME_SLOT_REF2="ts2">
        <ANNOTATION_VALUE>Hello</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION>
    </TIER>
    <TIER TIER_ID="Mary - por" PARENT_REF="Mary" LINGUISTIC_TYPE_REF="translation" LANG_REF="por">
      <ANNOTATION><REF_ANNOTATION ANNOTATION_ID="a2" ANNOTATION_REF="a1">
        <ANNOTATION_VALUE>Olá</ANNOTATION_VALUE></REF_ANNOTATION></ANNOTATION>
    </TIER>

Reading gives a subtitle an annotation of a top tier (one with no parent)
that is time-aligned: the tiers with a PARTICIPANT when any has one, else
all of them; a tier whose LINGUISTIC_TYPE takes its values from a
CONTROLLED_VOCABULARY (gesture phases, coded events) is left out when
there is a tier of free text. The speaker is the PARTICIPANT, else the tier's name when
there is more than one tier. A child tier of reference annotations in a
language other than its parent's (its LANG_REF) is a translation: its
annotations are the subtitles' in that language. Time subdivisions and
other child tiers (words, glosses) are not read. Writing gives a valid EAF
3.0: a tier a speaker, its PARTICIPANT the speaker, when the export asks
for speakers ('voice'; else one tier, "default"), a further tier of the
same speaker for a subtitle that overlaps another (ELAN's tiers hold no
overlaps), a translation tier under each for each language the subtitles
are translated into (Symbolic_Association), the LANGUAGEs they refer to,
ELAN's four constraints and the video as the MEDIA_DESCRIPTOR.

Transcriber (.trs) follows its DTD, trans-14.dtd: Speakers (id, name),
an Episode of Sections of Turns, a Turn the speech of its speakers (an id
or several) from its startTime to its endTime, in seconds, cut by Sync
points; Who nb="n" says which of a Turn's speakers is speaking. Reading
gives a subtitle the text between a Sync and the next (or the Turn's
end), each of the speakers in it a subtitle of their own, the speaker the
Speaker's name; Event, Comment, Background and Vocal marks are left out,
the words around them kept, and a Trans's xml:lang is the language.
Writing gives a Speakers table (when the export asks for speakers), one
Section, report, a Turn for the subtitles one speaker says one after the
other, a Sync a subtitle and one where a pause starts; subtitles that
overlap end where the next begins, as a Transcriber timeline has one
Turn at a time.

EXMARaLDA (.exb) follows the basic transcription DTD, version 1.4
(basic-transcription.dtd, Schmidt): a head with meta-information and a
speakertable, a common-timeline of tli points (seconds), and tiers of
events from one tli to another. Reading takes the transcription tiers
(type "t"), the speaker the speakertable's abbreviation; a tier whose
events are mostly single words (EXMARaLDA's way, one word an event, each
with its space after it) becomes subtitles of the length asked for, as
modules.json_subtitles makes them of words, and any other a subtitle an
event. Annotation and description tiers are not read. Writing gives the
head (the video as the referenced file), a speaker for each speaker when
the export asks for them, the timeline of the subtitles' times, and a
transcription tier a speaker (category "v"), an event a subtitle.

All three write the plain text (none has formatting); a subtitle's lines
are kept in EAF and become one line in TRS and EXB, which keep running
text.
"""

import datetime
import mimetypes
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitld.modules import json_subtitles
from subtitld.modules.transcripts import iso639_3, layers, plain_text


def _root(data):
    """The root of XML bytes or text; ValueError when it is not XML."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f'Not well-formed XML: {error}') from error


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def kind(data):
    """'eaf', 'trs' or 'exb' for the first bytes (or text) of a file of
    one of these tools, by its root element; None otherwise."""
    if isinstance(data, bytes):
        data = data[:4096].decode('utf-8', 'replace') if not data.startswith((b'\xff\xfe', b'\xfe\xff')) \
            else data[:4096].decode('utf-16', 'replace')
    match = re.search(r'<(?![?!])([\w:.-]+)', re.sub(r'<!--.*?-->|<!DOCTYPE[^>]*>', '', str(data or ''), flags=re.S))
    return {'ANNOTATION_DOCUMENT': 'eaf', 'Trans': 'trs', 'basic-transcription': 'exb'}.get(
        _local(match.group(1).split(':')[-1]) if match else '')


def _filled(times):
    """Times (in order, None where unknown) with each unknown one placed
    evenly between the known ones around it."""
    out = list(times)
    known = [index for index, value in enumerate(out) if value is not None]
    if not known:
        return [0.0] * len(out)
    for index in range(len(out)):
        if out[index] is not None:
            continue
        before = max((k for k in known if k < index), default=None)
        after = min((k for k in known if k > index), default=None)
        if before is None:
            out[index] = out[after]
        elif after is None:
            out[index] = out[before]
        else:
            out[index] = out[before] + (out[after] - out[before]) * (index - before) / (after - before)
    return out


def _float(value):
    try:
        return float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return None


# --- ELAN EAF -------------------------------------------------------------

FRAME_UNITS = {'PAL-frames': 40.0, 'NTSC-frames': 1001 / 30}


def parse_eaf(data):
    """(subtitles, the language of their tiers or None) of an EAF document
    (bytes or text); ValueError when it is not one."""
    root = _root(data)
    if _local(root.tag) != 'ANNOTATION_DOCUMENT':
        raise ValueError(f'Not an ELAN document: its root is <{_local(root.tag)}>')
    header = root.find('HEADER')
    unit = FRAME_UNITS.get(header.get('TIME_UNITS') if header is not None else '', 1.0)
    order = root.find('TIME_ORDER')
    slots = list(order.iter('TIME_SLOT')) if order is not None else []
    values = _filled([_float(slot.get('TIME_VALUE')) for slot in slots])
    times = {slot.get('TIME_SLOT_ID'): round(value * unit / 1000, 3) for slot, value in zip(slots, values)}
    types = {entry.get('LINGUISTIC_TYPE_ID'): entry for entry in root.iter('LINGUISTIC_TYPE')}
    tiers = root.findall('TIER')

    def aligned(tier):
        entry = types.get(tier.get('LINGUISTIC_TYPE_REF'))
        return tier.find('ANNOTATION/ALIGNABLE_ANNOTATION') is not None and (
            entry is None or entry.get('TIME_ALIGNABLE', 'true') != 'false')

    top = [tier for tier in tiers if not tier.get('PARENT_REF') and aligned(tier)]
    # A tier whose type takes its values from a controlled vocabulary holds
    # codes from a fixed list (gesture phases, events), not what is said:
    # left out where a tier of free text is there to read instead.
    said = [tier for tier in top if not (types.get(tier.get('LINGUISTIC_TYPE_REF')) is not None and
                                         types[tier.get('LINGUISTIC_TYPE_REF')].get('CONTROLLED_VOCABULARY_REF'))]
    top = said or top
    # A tier's name is its speaker's only in a file that names none: there
    # a tier is a person's (or ELAN's own "default", nobody's).
    named = any(tier.get('PARTICIPANT', '').strip() for tier in top)
    by_name = {re.sub(r' \(\d+\)$', '', tier.get('TIER_ID') or '') for tier in top} - {'default', ''}
    speakers = json_subtitles.Speakers()
    subtitles, by_id, languages = [], {}, set()
    for tier in top:
        speaker = tier.get('PARTICIPANT', '').strip()
        if not named and by_name and len(top) > 1:
            speaker = re.sub(r' \(\d+\)$', '', tier.get('TIER_ID') or '')
            speaker = '' if speaker == 'default' else speaker
        languages.add(tier.get('LANG_REF'))
        for annotation in tier.iter('ALIGNABLE_ANNOTATION'):
            value = (annotation.findtext('ANNOTATION_VALUE') or '').strip()
            start, end = times.get(annotation.get('TIME_SLOT_REF1')), times.get(annotation.get('TIME_SLOT_REF2'))
            if not value or start is None or end is None:
                continue
            subtitle = {'start': start, 'end': max(start, end), 'text': value}
            if speaker:
                subtitle['speaker'] = speakers(speaker)
            subtitles.append(subtitle)
            by_id[annotation.get('ANNOTATION_ID')] = subtitle
    names = {tier.get('TIER_ID'): tier for tier in top}
    for tier in tiers:
        parent = names.get(tier.get('PARENT_REF'))
        language = tier.get('LANG_REF')
        if parent is None or not language or language == parent.get('LANG_REF'):
            continue
        for annotation in tier.iter('REF_ANNOTATION'):
            subtitle = by_id.get(annotation.get('ANNOTATION_REF'))
            value = (annotation.findtext('ANNOTATION_VALUE') or '').strip()
            if subtitle is not None and value:
                subtitle.setdefault('translations', {})[language] = value
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    languages.discard(None)
    return subtitles, (languages.pop() if len(languages) == 1 else None)


def read_eaf(path):
    with open(path, 'rb') as eaf_file:
        return parse_eaf(eaf_file.read())


# ELAN's four constraint stereotypes, as it writes them into every file.
CONSTRAINTS = (
    ('Time_Subdivision', "Time subdivision of parent annotation's time interval, no time gaps allowed within this interval"),
    ('Symbolic_Subdivision', 'Symbolic subdivision of a parent annotation. Annotations refering to the same parent are ordered'),
    ('Symbolic_Association', '1-1 association with a parent annotation'),
    ('Included_In', "Time alignable annotations within the parent annotation's time interval, gaps are allowed"),
)


def write_eaf(segments, language='en', speakers=False, media=None, date=None):
    """An EAF 3.0 document of `segments`: tiers by speaker (when
    `speakers`), translation tiers under them, `media` the video's path."""
    shown = [segment for segment in segments if plain_text(segment)]
    lang = iso639_3(language or 'en')
    translated = sorted({code for segment in shown for code, text in (segment.get('translations') or {}).items()
                         if str(text or '').strip()})
    # A LANGUAGE a language, by its ISO 639-3 code; one that code is
    # already taken by (en-gb beside en-us) by Subtitld's own.
    languages, by_lang = [lang], {}
    for code in translated:
        candidate = iso639_3(code)
        if candidate in languages:
            candidate = re.sub(r'[^\w.-]', '_', code)
        languages.append(candidate)
        by_lang[code] = candidate
    stamps = sorted({round(float(value) * 1000) for segment in shown for value in (segment['start'], segment['end'])})
    slot_of = {stamp: f'ts{index}' for index, stamp in enumerate(stamps, 1)}
    when = (date or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec='seconds')
    count = [0]

    def annotation_id():
        count[0] += 1
        return f'a{count[0]}'

    tiers = []
    for name, held in layers(shown, speakers, 'default').items():
        speaker = str(held[0].get('speaker') or '').strip() if speakers else ''
        participant = f' PARTICIPANT={quoteattr(speaker)}' if speaker else ''
        lines = [f'    <TIER LINGUISTIC_TYPE_REF="default-lt" TIER_ID={quoteattr(name)}{participant} LANG_REF="{lang}">']
        ids = []
        for segment in held:
            ids.append(annotation_id())
            start, end = round(float(segment['start']) * 1000), round(float(segment['end']) * 1000)
            lines += [f'        <ANNOTATION>',
                      f'            <ALIGNABLE_ANNOTATION ANNOTATION_ID="{ids[-1]}" TIME_SLOT_REF1="{slot_of[start]}" '
                      f'TIME_SLOT_REF2="{slot_of[end]}">',
                      f'                <ANNOTATION_VALUE>{escape(plain_text(segment))}</ANNOTATION_VALUE>',
                      '            </ALIGNABLE_ANNOTATION>', '        </ANNOTATION>']
        tiers += lines + ['    </TIER>']
        for code in translated:
            texts = [(ref, str((segment.get('translations') or {}).get(code) or '').strip()) for ref, segment in zip(ids, held)]
            if not any(text for _ref, text in texts):
                continue
            tiers.append(f'    <TIER LINGUISTIC_TYPE_REF="translation-lt" PARENT_REF={quoteattr(name)} '
                         f'TIER_ID={quoteattr(name + " - " + code)}{participant} LANG_REF="{by_lang[code]}">')
            for ref, text in texts:
                if text:
                    tiers += ['        <ANNOTATION>',
                              f'            <REF_ANNOTATION ANNOTATION_ID="{annotation_id()}" ANNOTATION_REF="{ref}">',
                              f'                <ANNOTATION_VALUE>{escape(text)}</ANNOTATION_VALUE>',
                              '            </REF_ANNOTATION>', '        </ANNOTATION>']
            tiers.append('    </TIER>')
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<ANNOTATION_DOCUMENT AUTHOR="" DATE="{when}" FORMAT="3.0" VERSION="3.0" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xsi:noNamespaceSchemaLocation="http://www.mpi.nl/tools/elan/EAFv3.0.xsd">',
           '    <HEADER MEDIA_FILE="" TIME_UNITS="milliseconds">']
    if media:
        mime = mimetypes.guess_type(media)[0] or 'video/*'
        url = 'file://' + ('' if media.startswith('/') else '/') + media.replace('\\', '/').replace(' ', '%20')
        out.append(f'        <MEDIA_DESCRIPTOR MEDIA_URL={quoteattr(url)} MIME_TYPE={quoteattr(mime)} '
                   f'RELATIVE_MEDIA_URL={quoteattr("./" + os.path.basename(media).replace(" ", "%20"))}/>')
    out += [f'        <PROPERTY NAME="lastUsedAnnotationId">{count[0]}</PROPERTY>', '    </HEADER>', '    <TIME_ORDER>']
    out += [f'        <TIME_SLOT TIME_SLOT_ID="{slot_of[stamp]}" TIME_VALUE="{max(0, stamp)}"/>' for stamp in stamps]
    out += ['    </TIME_ORDER>'] + tiers
    out.append('    <LINGUISTIC_TYPE GRAPHIC_REFERENCES="false" LINGUISTIC_TYPE_ID="default-lt" TIME_ALIGNABLE="true"/>')
    if translated:
        out.append('    <LINGUISTIC_TYPE CONSTRAINTS="Symbolic_Association" GRAPHIC_REFERENCES="false" '
                   'LINGUISTIC_TYPE_ID="translation-lt" TIME_ALIGNABLE="false"/>')
    out += [f'    <LANGUAGE LANG_ID="{key}"/>' for key in languages]
    out += [f'    <CONSTRAINT DESCRIPTION={quoteattr(description)} STEREOTYPE="{name}"/>' for name, description in CONSTRAINTS]
    out.append('</ANNOTATION_DOCUMENT>')
    return '\n'.join(out) + '\n'


def save_eaf(path, segments, language='en', speakers=False, media=None):
    with open(path, 'w', encoding='utf-8', newline='\n') as eaf_file:
        eaf_file.write(write_eaf(segments, language, speakers, media))


# --- Transcriber TRS ------------------------------------------------------

def parse_trs(data):
    """(subtitles, the Trans's xml:lang or None) of a Transcriber file
    (bytes or text); ValueError when it is not one."""
    root = _root(data)
    if _local(root.tag) != 'Trans':
        raise ValueError(f'Not a Transcriber file: its root is <{_local(root.tag)}>')
    names = {speaker.get('id'): (speaker.get('name') or speaker.get('id') or '').strip() for speaker in root.iter('Speaker')}
    speakers = json_subtitles.Speakers()
    subtitles = []
    for turn in root.iter('Turn'):
        who = (turn.get('speaker') or '').split()
        start, end = _float(turn.get('startTime')) or 0.0, _float(turn.get('endTime'))
        span = {'start': start, 'texts': {}, 'who': 0}

        def flush(until):
            for index, pieces in sorted(span['texts'].items()):
                text = ' '.join(' '.join(pieces).split())
                if not text:
                    continue
                subtitle = {'start': round(span['start'], 3), 'end': round(max(until, span['start']), 3), 'text': text}
                if index < len(who) and names.get(who[index], who[index]):
                    subtitle['speaker'] = speakers(names.get(who[index], who[index]))
                subtitles.append(subtitle)
            span['texts'] = {}

        span['texts'].setdefault(0, []).append(turn.text or '')
        for child in turn:
            tag = _local(child.tag)
            if tag == 'Sync':
                time = _float(child.get('time'))
                if time is not None:
                    flush(time)
                    span['start'] = time
            elif tag == 'Who':
                number = int(_float(child.get('nb')) or 1)
                span['who'] = max(0, number - 1)
            span['texts'].setdefault(span['who'], []).append(child.tail or '')
        flush(end if end is not None else span['start'] + 3.0)
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    language = root.get('{http://www.w3.org/XML/1998/namespace}lang') or root.get('lang')
    return subtitles, language


def read_trs(path):
    with open(path, 'rb') as trs_file:
        return parse_trs(trs_file.read())


def _seconds(value):
    return ('%.3f' % value).rstrip('0').rstrip('.') or '0'


def write_trs(segments, language='en', speakers=False, media=None, date=None):
    """A Transcriber file of `segments`: one report Section, a Turn a run
    of one speaker's subtitles, a Sync a subtitle or a pause."""
    shown = sorted((dict(segment, text=' '.join(plain_text(segment).split())) for segment in segments
                    if plain_text(segment)), key=lambda segment: (float(segment['start']), float(segment['end'])))
    for current, following in zip(shown, shown[1:]):
        if float(following['start']) < float(current['end']):
            current['end'] = following['start']
    names = []
    for segment in shown:
        name = str(segment.get('speaker') or '').strip() if speakers else ''
        if name and name not in names:
            names.append(name)
    ids = {name: f'spk{index}' for index, name in enumerate(names, 1)}
    # The timeline: the subtitles, and a pause wherever one ends before
    # the next begins; runs of one speaker's are a Turn.
    pieces, at = [], 0.0
    for segment in shown:
        start, end = float(segment['start']), float(segment['end'])
        if start > at + 1e-9:
            pieces.append((at, start, '', None))
        if end > start:
            speaker = ids.get(str(segment.get('speaker') or '').strip()) if speakers else None
            pieces.append((start, end, segment['text'], speaker))
            at = end
    turns = []
    for piece in pieces:
        if turns and turns[-1][0][3] == piece[3]:
            turns[-1].append(piece)
        else:
            turns.append([piece])
    end = pieces[-1][1] if pieces else 0.0
    stamp = (date or datetime.date.today()).strftime('%y%m%d')
    lang = str(language or 'en').replace('_', '-').split('-')[0].lower()
    audio = os.path.splitext(os.path.basename(media))[0] if media else ''
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE Trans SYSTEM "trans-14.dtd">',
           f'<Trans scribe="" audio_filename={quoteattr(audio)} version="1" version_date="{stamp}" xml:lang="{lang}">']
    if names:
        out.append('<Speakers>')
        out += [f'<Speaker id="{ids[name]}" name={quoteattr(name)} check="no" dialect="native" accent="" scope="local"/>'
                for name in names]
        out.append('</Speakers>')
    out += ['<Episode>', f'<Section type="report" startTime="0" endTime="{_seconds(end)}">']
    for turn in turns:
        speaker = f' speaker="{turn[0][3]}"' if turn[0][3] else ''
        out.append(f'<Turn{speaker} startTime="{_seconds(turn[0][0])}" endTime="{_seconds(turn[-1][1])}">')
        for start, _end, text, _speaker in turn:
            out.append(f'<Sync time="{_seconds(start)}"/>')
            if text:
                out.append(escape(text))
        out.append('</Turn>')
    out += ['</Section>', '</Episode>', '</Trans>']
    return '\n'.join(out) + '\n'


def save_trs(path, segments, language='en', speakers=False, media=None):
    with open(path, 'w', encoding='utf-8', newline='\n') as trs_file:
        trs_file.write(write_trs(segments, language, speakers, media))


# --- EXMARaLDA basic transcription ----------------------------------------

def parse_exb(data, limits=None):
    """The subtitles of an EXMARaLDA basic transcription (bytes or text);
    ValueError when it is not one."""
    root = _root(data)
    if _local(root.tag) != 'basic-transcription':
        raise ValueError(f'Not an EXMARaLDA basic transcription: its root is <{_local(root.tag)}>')
    points = list(root.iter('tli'))
    values = _filled([_float(point.get('time')) for point in points])
    times = {point.get('id'): value for point, value in zip(points, values)}
    abbreviations = {speaker.get('id'): (speaker.findtext('abbreviation') or '').strip() or speaker.get('id')
                     for speaker in root.iter('speaker')}
    speakers = json_subtitles.Speakers()
    subtitles = []
    for tier in root.iter('tier'):
        if tier.get('type') != 't':
            continue
        speaker = speakers(abbreviations.get(tier.get('speaker'), tier.get('speaker'))) if tier.get('speaker') else None
        events = []
        for event in tier.iter('event'):
            start, end = times.get(event.get('start')), times.get(event.get('end'))
            raw = ''.join(event.itertext()) if len(event) == 0 else (event.text or '')
            if start is None or end is None or not raw.strip():
                continue
            events.append((start, end, raw))
        events.sort(key=lambda event: event[0])
        single = sum(1 for _start, _end, raw in events if len(raw.split()) == 1)
        if events and single >= 0.6 * len(events):
            words = []
            for index, (start, end, raw) in enumerate(events):
                previous = events[index - 1] if index else None
                attached = bool(previous) and not previous[2][-1:].isspace() and abs(previous[1] - start) < 1e-6
                words.append({'start': start, 'end': end, 'text': raw.strip(), 'speaker': speaker, 'attached': attached})
            found = json_subtitles.from_words(words, limits)
        else:
            found = [{'start': round(start, 3), 'end': round(end, 3), 'text': ' '.join(raw.split()), 'speaker': speaker}
                     for start, end, raw in events]
        subtitles += [{key: value for key, value in subtitle.items() if value is not None} for subtitle in found]
    subtitles.sort(key=lambda subtitle: (subtitle['start'], subtitle['end']))
    return subtitles


def read_exb(path, limits=None):
    with open(path, 'rb') as exb_file:
        return parse_exb(exb_file.read(), limits)


def write_exb(segments, speakers=False, media=None, name=''):
    """An EXMARaLDA basic transcription of `segments`: a transcription tier
    a speaker (when `speakers`), an event a subtitle."""
    shown = [dict(segment, text=' '.join(plain_text(segment).split())) for segment in segments if plain_text(segment)]
    tiers = layers(shown, speakers, '')
    stamps = sorted({round(float(value), 3) for segment in shown for value in (segment['start'], segment['end'])})
    point = {stamp: f'T{index}' for index, stamp in enumerate(stamps)}
    names = [speaker for speaker in dict.fromkeys(re.sub(r' \(\d+\)$', '', key) for key in tiers) if speaker]
    ids = {speaker: f'SPK{index}' for index, speaker in enumerate(names)}
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<basic-transcription>', '<head>', '<meta-information>',
           '<project-name></project-name>', f'<transcription-name>{escape(name)}</transcription-name>',
           f'<referenced-file url={quoteattr(media or "")}/>', '<ud-meta-information></ud-meta-information>',
           '<comment></comment>', '<transcription-convention></transcription-convention>', '</meta-information>',
           '<speakertable>']
    for speaker in names:
        out += [f'<speaker id="{ids[speaker]}">', f'<abbreviation>{escape(speaker)}</abbreviation>', '<sex value="u"/>',
                '<languages-used></languages-used>', '<l1></l1>', '<l2></l2>',
                '<ud-speaker-information></ud-speaker-information>', '<comment></comment>', '</speaker>']
    out += ['</speakertable>', '</head>', '<basic-body>', '<common-timeline>']
    out += [f'<tli id="{point[stamp]}" time="{_seconds(stamp)}"/>' for stamp in stamps]
    out.append('</common-timeline>')
    for number, (key, held) in enumerate(tiers.items()):
        speaker = re.sub(r' \(\d+\)$', '', key)
        owner = f' speaker="{ids[speaker]}"' if speaker else ''
        display = f'{key} [v]' if key else '[v]'
        out.append(f'<tier id="TIE{number}"{owner} category="v" type="t" display-name={quoteattr(display)}>')
        for segment in held:
            start, end = round(float(segment['start']), 3), round(float(segment['end']), 3)
            out.append(f'<event start="{point[start]}" end="{point[end]}">{escape(segment["text"])} </event>')
        out.append('</tier>')
    out += ['</basic-body>', '</basic-transcription>']
    return '\n'.join(out) + '\n'


def save_exb(path, segments, speakers=False, media=None, name=''):
    with open(path, 'w', encoding='utf-8', newline='\n') as exb_file:
        exb_file.write(write_exb(segments, speakers, media, name))
