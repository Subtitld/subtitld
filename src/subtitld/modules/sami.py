"""SAMI (.smi, .sami): read the files players and subtitlers have, write
what Windows Media Player and the rest show.

SAMI, Microsoft's Synchronized Accessible Media Interchange, is HTML with a
<SYNC Start=...> before each caption: a caption shows from its start until
the next one, and "&nbsp;" blanks it. Its CSS classes are languages, so
one file can hold several; a paragraph with ID=Source names who speaks.

Reading forgives, as HTML does:

- the bytes are decoded as SRT's are (Korean files are mostly CP949);
- tags in capitals or not, attributes quoted or not, paragraphs and syncs
  closed or not, syncs out of order;
- each class is a language (its "lang:" in the style sheet, else guessed
  from its name, KRCC Korean); the first one defined, as in Windows Media
  Player, is the subtitles' language, and the others are their
  translations, each a caption matched to the subtitle it overlaps most;
- a caption lasts until the next sync with a caption in its class, or an
  empty sync; the last one until the duration SAMIParam's Metrics give,
  else four seconds;
- <I>, <EM>, <B>, <STRONG>, <U>, <S>, <STRIKE>, <DEL>, <FONT color>, and
  the color, font-style, font-weight and text-decoration the style sheet
  (P, the class) or a style="..." gives, become formatting tags; <BR>
  breaks a line; other whitespace, &nbsp; too, is one space; a ruby's
  reading (<RT>) is left out;
- a Source paragraph (<P Class=ENCC ID=Source>) is the speaker of the
  captions after it in its class, until another one;
- the subtitles come sorted by start.

Writing gives SAMI 1.0 as Microsoft's own sample has it, its tags in
capitals (Windows Media Player may want them so): a class for each
language (two when the export asks for the original and a translation),
a sync where each subtitle starts and an &nbsp; one where it ends, unless
the next starts there (an empty paragraph where another language shows a
caption in the same sync); formatting as <I>, <B>, <U>, <S> and <FONT color>,
line breaks as <BR>; and, when asked, each speaker as a Source line,
an empty one when a subtitle has none.
SAMI has no place on the picture: positions are not written.
"""

import bisect
import re
from html.parser import HTMLParser

from subtitld.modules import export_options, markup
from subtitld.modules.srt import decode

LAST_DURATION = 4.0
_SPACES = ' \t\r\f\u00a0'                # in a caption, <BR> its only line break
_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_NUMBER = re.compile(r'\s*(\d+(?:\.\d+)?)')
_FLAG_TAGS = {'i': 'italic', 'em': 'italic', 'b': 'bold', 'strong': 'bold', 'u': 'underline',
              's': 'strike', 'strike': 'strike', 'del': 'strike'}
_STYLED_TAGS = ('font', 'span')
_SKIPPED = ('rt', 'rp', 'title', 'script', 'samiparam')
# Class names that say a country, not a language.
_COUNTRIES = {'kr': 'ko', 'jp': 'ja', 'cn': 'zh', 'tw': 'zh-tw', 'br': 'pt-br', 'us': 'en-us', 'gb': 'en-gb',
              'dk': 'da', 'se': 'sv', 'gr': 'el', 'il': 'he', 'cz': 'cs', 'ua': 'uk'}


def _declarations(text):
    """{property: value} for CSS declarations, the properties in lower case."""
    found = {}
    for declaration in text.split(';'):
        name, _colon, value = declaration.partition(':')
        if _colon and name.strip():
            found[name.strip().lower()] = value.strip().strip('"\'').strip()
    return found


def _style_sheet(text):
    """[(selector, {property: value})] in the order the style sheet has
    them; selectors in lower case."""
    text = re.sub(r'/\*.*?\*/', '', text.replace('<!--', '').replace('-->', ''), flags=re.S)
    rules = []
    for selectors, body in _RULE.findall(text):
        declarations = _declarations(body)
        for selector in selectors.split(','):
            if selector.strip():
                rules.append((selector.strip().lower(), declarations))
    return rules


def _styled(style, props):
    """`style` changed by CSS `props`."""
    if 'color' in props:
        color = _color(props['color'])
        style = style._replace(color='' if color in ('', '#ffffff') else color)
    if 'font-style' in props:
        style = style._replace(italic=props['font-style'].lower() in ('italic', 'oblique'))
    if 'font-weight' in props:
        weight = props['font-weight'].lower()
        style = style._replace(bold=weight in ('bold', 'bolder') or (weight.isdigit() and int(weight) >= 600))
    if 'text-decoration' in props:
        decoration = props['text-decoration'].lower()
        style = style._replace(underline='underline' in decoration, strike='line-through' in decoration)
    return style


def _color(value):
    rgb = re.match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', value or '')
    if rgb:
        return '#' + ''.join(f'{min(255, int(part)):02x}' for part in rgb.groups())
    return markup.color_hex(value)


def _guess_language(class_name):
    """A language for a class with none declared, from its name: ENUSCC
    'en-us', KRCC 'ko'; '' when the name says nothing."""
    letters = re.sub(r'cc$', '', re.sub(r'[^a-z]', '', class_name.lower()))
    if len(letters) == 4:
        return f'{letters[:2]}-{letters[2:]}'
    if len(letters) == 2:
        return _COUNTRIES.get(letters, letters)
    return ''


class _Reader(HTMLParser):
    """The syncs of a SAMI document: [{'start', 'paragraphs': [{'class',
    'source', 'runs'}]}], and its style sheet."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.css_text = []
        self.params = []
        self.syncs = []
        self.rules = []
        self.sync = None
        self.paragraph = None
        self.stack = []                    # [(tag, Style)] of the formatting tags open
        self.skipping = []                 # tags whose text is no caption's
        self.in_style = False

    def _rule(self, selector):
        props = {}
        for name, declarations in self.rules:
            if name == selector:
                props.update(declarations)
        return props

    def _close_paragraph(self):
        self.paragraph = None
        self.stack = []

    def _open_paragraph(self, attrs):
        self._close_paragraph()
        css_class = (attrs.get('class') or '').strip().lower()
        style = _styled(markup.PLAIN, self._rule('p'))
        if css_class:
            style = _styled(style, self._rule('.' + css_class))
        style = _styled(style, _declarations(attrs.get('style') or ''))
        self.paragraph = {'class': css_class, 'source': (attrs.get('id') or '').strip().lower() == 'source',
                          'runs': [], 'style': style}
        self.sync['paragraphs'].append(self.paragraph)

    def _style(self):
        return self.stack[-1][1] if self.stack else self.paragraph['style']

    def handle_starttag(self, tag, attrs):
        attrs = {name: value or '' for name, value in attrs}
        if tag == 'style':
            self.in_style = True
        elif tag in _SKIPPED:
            self.skipping.append(tag)
        elif tag == 'sync':
            self._close_paragraph()
            start = _NUMBER.match(attrs.get('start') or '')
            self.sync = {'start': float(start.group(1)) / 1000, 'paragraphs': []} if start else None
            if self.sync:
                self.syncs.append(self.sync)
        elif self.sync is None:
            return
        elif tag == 'p':
            self._open_paragraph(attrs)
        elif self.paragraph is None and tag != 'br':
            # Formatting before any paragraph opens a paragraph of no class.
            self._open_paragraph({})
            self.handle_starttag(tag, list(attrs.items()))
        elif tag == 'br':
            if self.paragraph is not None:
                self.paragraph['runs'].append(('\n', self._style()))
        elif tag in _FLAG_TAGS:
            self.stack.append((tag, self._style()._replace(**{_FLAG_TAGS[tag]: True})))
        elif tag in _STYLED_TAGS:
            props = _declarations(attrs.get('style') or '')
            if attrs.get('color'):
                props['color'] = attrs['color']
            self.stack.append((tag, _styled(self._style(), props)))

    def handle_startendtag(self, tag, attrs):
        if tag in ('br', 'sync', 'p'):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False
            self.rules = _style_sheet(''.join(self.css_text))
        elif tag in _SKIPPED:
            if tag in self.skipping:
                del self.skipping[len(self.skipping) - 1 - self.skipping[::-1].index(tag):]
        elif tag == 'sync':
            self._close_paragraph()
            self.sync = None
        elif tag == 'p':
            self._close_paragraph()
        else:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    def handle_data(self, data):
        if self.in_style:
            self.css_text.append(data)
        elif self.skipping:
            if self.skipping[-1] == 'samiparam':
                self.params.append(data)
        elif self.sync is not None:
            if self.paragraph is None:
                if not data.strip(_SPACES + '\n'):
                    return
                self._open_paragraph({})
            self.paragraph['runs'].append((re.sub('[ \t\r\n\f]+', ' ', data), self._style()))


def _cues(syncs, duration):
    """{class: [{'start', 'end', 'text', 'speaker'}]} for the syncs: a
    caption lasts until the next one in its class (or an empty sync), and
    a Source paragraph names the speaker of the captions after it."""
    cues = {}
    open_cues = {}
    sources = {}

    def close(css_class, at):
        cue = open_cues.pop(css_class, None)
        if cue:
            cue['end'] = round(max(cue['start'], at), 3)

    for sync in sorted(syncs, key=lambda sync: sync['start']):
        start = sync['start']
        captions = {}
        for paragraph in sync['paragraphs']:
            text = markup.tidy(paragraph['runs'], _SPACES)
            if paragraph['source']:
                sources[paragraph['class']] = markup.plain(text).strip()
            else:
                captions[paragraph['class']] = (captions[paragraph['class']] + '\n' + text).strip('\n') \
                    if paragraph['class'] in captions else text
        if not sync['paragraphs']:
            for css_class in list(open_cues):
                close(css_class, start)
        for css_class, text in captions.items():
            close(css_class, start)
            if markup.plain(text).strip():
                cue = {'start': round(start, 3), 'end': None, 'text': text}
                speaker = sources.get(css_class) or sources.get('')
                if speaker:
                    cue['speaker'] = speaker
                open_cues[css_class] = cue
                cues.setdefault(css_class, []).append(cue)
    for css_class, cue in list(open_cues.items()):
        close(css_class, duration if duration and duration > cue['start'] else cue['start'] + LAST_DURATION)
    return cues


def _overlap(a, b):
    return min(a['end'], b['end']) - max(a['start'], b['start'])


def parse(text):
    """(subtitles, languages) in SAMI text. The subtitles are the first
    language's, [{'start', 'end', 'text'[, 'speaker'][, 'translations']}];
    translations are keyed by the other languages, as `languages` names
    them after the first: tags as the file writes them (en-US), or class
    names where it gives none."""
    reader = _Reader()
    reader.feed(text)
    reader.close()
    if reader.in_style:
        reader.rules = _style_sheet(''.join(reader.css_text))
    # The classes in the order the style sheet defines them, then the ones
    # only used. The first with a caption is the subtitles' language, and
    # a paragraph of no class, which players show whatever the language,
    # is its.
    paragraphs = [paragraph for sync in reader.syncs for paragraph in sync['paragraphs']]
    defined = [selector[1:] for selector, _props in reader.rules if selector.startswith('.')]
    order = list(dict.fromkeys(defined + [paragraph['class'] for paragraph in paragraphs]))
    captioned = {paragraph['class'] for paragraph in paragraphs
                 if not paragraph['source'] and markup.plain(markup.tidy(paragraph['runs'], _SPACES)).strip()}
    first = next((css_class for css_class in order if css_class and css_class in captioned), '')
    for paragraph in paragraphs:
        if not paragraph['class']:
            paragraph['class'] = first
    metrics = re.search(r'duration\s*:\s*(\d+)', ''.join(reader.params), re.I)
    cues = _cues(reader.syncs, int(metrics.group(1)) / 1000 if metrics else None)
    classes = [css_class for css_class in order if cues.get(css_class)]
    if not classes:
        return [], []

    def language(css_class):
        props = {}
        for selector, declarations in reader.rules:
            if selector == '.' + css_class:
                props.update(declarations)
        tag = re.sub(r'-cc$', '', (props.get('lang') or '').strip(), flags=re.I)
        if not css_class and not tag and len(defined) == 1:
            return language(defined[0])
        return tag or _guess_language(css_class) or css_class

    languages = [language(css_class) for css_class in classes]
    segments = [dict(cue) for cue in cues[classes[0]]]       # in order, none overlapping
    starts = [segment['start'] for segment in segments]
    for css_class, tag in zip(classes[1:], languages[1:]):
        for cue in cues[css_class]:
            # The subtitles it can overlap: the one showing when it starts,
            # and those starting before it ends.
            nearby = segments[max(0, bisect.bisect_right(starts, cue['start']) - 1):bisect.bisect_left(starts, cue['end'])]
            best = max(nearby, key=lambda segment: _overlap(segment, cue), default=None)
            if best is None or _overlap(best, cue) <= 0:
                continue
            translations = best.setdefault('translations', {})
            translations[tag] = (translations[tag] + '\n' + cue['text']) if tag in translations else cue['text']
            if 'speaker' not in best and cue.get('speaker'):
                best['speaker'] = cue['speaker']
    segments.sort(key=lambda segment: (segment['start'], segment['end']))
    return segments, languages


def read(path):
    """(subtitles, languages) in the SAMI file at `path`, as parse() gives them."""
    with open(path, 'rb') as sami_file:
        return parse(decode(sami_file.read()))


def class_name(language, taken=()):
    """The class for a language, as Microsoft names them: ENUSCC for
    en-US, PTBRCC for pt-BR."""
    name = re.sub(r'[^A-Z0-9]', '', str(language or 'en').upper()) + 'CC'
    candidate, number = name, 2
    while candidate in taken:
        candidate, number = f'{name}{number}', number + 1
    return candidate


def _html(text):
    """Text as SAMI's HTML: "<" as "&lt;", and "&" as "&amp;" only where it
    would read as an entity (FFmpeg shows "&amp;" as it is written)."""
    text = re.sub(r'&(?=#?\w+;)', '&amp;', text).replace('<', '&lt;')
    return text.replace('\u00a0', '&nbsp;')


def _caption(text):
    """A subtitle's text as SAMI: its formatting as tags in capitals, line
    breaks as <BR>, without the place on the picture SAMI has none for."""
    _lead, rest = markup.split_lead(text)
    rest = '\n'.join(line.strip() for line in rest.split('\n') if line.strip())
    runs = [(_html(run).replace('\n', '<BR>'), style) for run, style in markup.spans(rest)]
    return re.sub(r'<(/?)(font|[ibus])\b', lambda m: f'<{m.group(1)}{m.group(2).upper()}', markup.from_spans(runs))


def write(tracks, sources=False):
    """SAMI text for `tracks`, [(language, name, segments)]: a class for
    each, the first the one players show unless told otherwise. With
    `sources`, each subtitle's speaker as a Source line."""
    names = []
    syncs = {}                                  # milliseconds: [(class, source, content)]

    def add(at, css_class, content, source=False):
        syncs.setdefault(int(round(at * 1000)), []).append((css_class, source, content))

    for language, _name, segments in tracks:
        css_class = class_name(language, names)
        names.append(css_class)
        cues = []
        for segment in sorted(segments, key=lambda s: (float(s.get('start', 0)), float(s.get('end', 0)))):
            text = _caption(str(segment.get('text') or ''))
            if markup.plain(text).strip():
                start = float(segment.get('start', 0))
                cues.append((start, max(start, float(segment.get('end', start))), text,
                             str(segment.get('speaker') or '').strip() if sources else ''))
        shown_source = ''
        for index, (start, end, text, speaker) in enumerate(cues):
            if speaker or shown_source:
                add(start, css_class, _html(speaker), source=True)
                shown_source = speaker
            add(start, css_class, text)
            following = cues[index + 1][0] if index + 1 < len(cues) else None
            if following is None or following > end:
                if shown_source:
                    add(end, css_class, '', source=True)
                    shown_source = ''
                add(end, css_class, '')

    rules = ['P { margin-left: 8pt; margin-right: 8pt; margin-bottom: 2pt; margin-top: 2pt;',
             '    text-align: center; font-size: 20pt; font-family: Arial, Helvetica, sans-serif;',
             '    font-weight: normal; color: white; }']
    for css_class, (language, name, _segments) in zip(names, tracks):
        rules.append(f'.{css_class} {{ Name: {_css_text(name or language)}; '
                     f'lang: {export_options.language_tag(language)}; SAMIType: CC; }}')
    if sources:
        rules.append('#Source { color: silver; font-size: 16pt; font-weight: bold; }')
    lines = ['<SAMI>', '<HEAD>', '<SAMIParam>', '  Metrics {time:ms;}', '  Spec {MSFT:1.0;}', '</SAMIParam>',
             '<STYLE TYPE="text/css">', '<!--', *rules, '-->', '</STYLE>', '</HEAD>', '<BODY>']
    for at in sorted(syncs):
        lines.append(f'<SYNC Start={at}>')
        # A caption is blanked with &nbsp;, as SAMI says, but by an empty
        # paragraph in a sync where another language shows one: FFmpeg
        # (and the players built on it) drops a sync with &nbsp; in it.
        # An empty Source line takes the last one away.
        # Empty Source lines first: FFmpeg keeps one speaker for every
        # language, the last it reads.
        showing = any(content and not source for _css_class, source, content in syncs[at])
        for css_class, source, content in sorted(syncs[at], key=lambda entry: not (entry[1] and not entry[2])):
            if source:
                lines.append(f'<P Class={css_class} ID=Source>{content}')
            else:
                lines.append(f'<P Class={css_class}>{content or ("" if showing else "&nbsp;")}')
    lines += ['</BODY>', '</SAMI>', '']
    return '\n'.join(lines)


def _css_text(value):
    """A value for the style sheet: quoted when it has more than letters."""
    value = re.sub(r'[{};"<>]', '', str(value)).strip()
    return value if re.fullmatch(r'[\w-]+', value) else f'"{value}"'


def save(path, tracks, sources=False, encoding='utf-8', line_end='\n'):
    """Write `tracks` to `path` as SAMI: in UTF-8 unless an older player
    needs otherwise (a character the encoding lacks becomes "?"), with
    "\\n" or "\\r\\n" line ends."""
    with open(path, 'w', encoding=encoding, errors='replace', newline=line_end) as sami_file:
        sami_file.write(write(tracks, sources))
