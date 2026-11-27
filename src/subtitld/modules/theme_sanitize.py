"""The safety check of a subtitle or title theme file (modules.themes): what
in its HTML and CSS a theme may not have, listed so the author can read why.

A theme is one HTML file that must be able to do nothing but draw: no
script of its own, nothing loaded from anywhere but the file itself. So:

- Elements come from an allowlist: text and layout HTML, inline SVG and its
  filters, <style>, and the one <script type="application/json"
  id="subtitld-theme"> that holds the manifest (inert: browsers do not run
  that type). SMIL (<animate>, <set>…) is out too: Subtitld seeks a theme's
  animations to the moment it draws, and only CSS animations can be seeked.
- No `on…` attribute; a URL in an attribute (src, href…) is a `data:` one of
  an image type, or `#fragment`.
- In CSS (the <style>s and style attributes), `url()` is a `data:` image or
  font, or `#fragment`; no @import, no expression(), no bindings.

The file is read as a browser's tokenizer reads it (html.parser follows the
HTML5 tokenizer), and is never written back: Subtitld uses the file as it
came, so nothing in it changes by being parsed and serialized again. What
this check misses, the renderer's Content Security Policy still stops (no
scripts, no network); the check is there so a theme is refused with reasons,
on subtitld.cc and in Subtitld alike.
"""

import re
from html.parser import HTMLParser

MANIFEST_ID = 'subtitld-theme'
MANIFEST_TYPE = 'application/json'

HTML_ELEMENTS = {
    'html', 'head', 'body', 'title', 'meta', 'style', 'script',
    'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr', 'wbr',
    'strong', 'em', 'b', 'i', 'u', 's', 'small', 'sub', 'sup', 'mark', 'q', 'cite', 'abbr', 'time', 'bdi', 'bdo',
    'ruby', 'rt', 'rp', 'ul', 'ol', 'li', 'figure', 'figcaption', 'blockquote',
    'section', 'header', 'footer', 'article', 'aside', 'main', 'nav', 'img',
}
SVG_ELEMENTS = {
    'svg', 'g', 'defs', 'symbol', 'use', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
    'text', 'tspan', 'textpath', 'lineargradient', 'radialgradient', 'stop', 'clippath', 'mask', 'pattern',
    'marker', 'image', 'desc', 'foreignobject', 'filter',
    'feblend', 'fecolormatrix', 'fecomponenttransfer', 'fecomposite', 'feconvolvematrix', 'fediffuselighting',
    'fedisplacementmap', 'fedistantlight', 'fedropshadow', 'feflood', 'fefunca', 'fefuncb', 'fefuncg', 'fefuncr',
    'fegaussianblur', 'feimage', 'femerge', 'femergenode', 'femorphology', 'feoffset', 'fepointlight',
    'fespecularlighting', 'fespotlight', 'fetile', 'feturbulence',
}
ELEMENTS = HTML_ELEMENTS | SVG_ELEMENTS
# Why some elements are not there, for the message.
REASONS = {
    'animate': 'SMIL animation cannot be seeked: use CSS animations',
    'animatemotion': 'SMIL animation cannot be seeked: use CSS animations',
    'animatetransform': 'SMIL animation cannot be seeked: use CSS animations',
    'set': 'SMIL animation cannot be seeked: use CSS animations',
    'iframe': 'loads another page', 'frame': 'loads another page', 'frameset': 'loads another page',
    'object': 'loads another document', 'embed': 'loads another document', 'portal': 'loads another page',
    'link': 'loads a file from outside the theme: put CSS in <style>',
    'base': 'changes where links point',
    'video': 'not drawn by themes', 'audio': 'not drawn by themes', 'source': 'not drawn by themes',
    'canvas': 'needs a script to draw', 'noscript': 'themes have no scripts',
    'form': 'not part of a picture', 'input': 'not part of a picture', 'button': 'not part of a picture',
    'textarea': 'not part of a picture', 'select': 'not part of a picture',
    'math': 'MathML is not supported',
}
# Attributes whose value is a URL.
URL_ATTRIBUTES = {'src', 'href', 'xlink:href', 'poster', 'background', 'action', 'formaction', 'data',
                  'ping', 'cite', 'longdesc', 'manifest', 'codebase', 'archive', 'lowsrc', 'dynsrc'}
REFUSED_ATTRIBUTES = {'srcset': 'loads images by URL: use src with a data: URL',
                      'http-equiv': 'changes how the page behaves'}
# What a data: URL may hold where: images in markup, images and fonts in CSS.
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif', 'image/svg+xml'}
FONT_TYPES = {'font/woff2', 'font/woff', 'font/ttf', 'font/otf', 'font/sfnt', 'application/font-woff',
              'application/font-woff2', 'application/x-font-ttf', 'application/x-font-otf',
              'application/x-font-truetype', 'application/vnd.ms-opentype', 'application/font-sfnt'}

_DATA_URL = re.compile(r'data:([a-z0-9.+-]+/[a-z0-9.+-]+)', re.IGNORECASE)
_CSS_COMMENT = re.compile(r'/\*.*?(\*/|$)', re.DOTALL)
_CSS_ESCAPE = re.compile(r'\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?|\\(.)', re.DOTALL)
_CSS_URL = re.compile(r'url\(\s*(?:"([^"]*)"|\'([^\']*)\'|([^)]*?))\s*\)', re.IGNORECASE)
_CSS_FUNCTION_URL = re.compile(r'(?:image-set|image|src)\(\s*(["\'])(.*?)\1', re.IGNORECASE)
_ABRUPT_COMMENT = re.compile(r'<!---?>')
_CDATA = re.compile(r'<!\[CDATA\[', re.IGNORECASE)
_SCRIPT_TAG = re.compile(r'<script[\s/>]', re.IGNORECASE)
_MARKUP = re.compile(r'<[a-zA-Z/!?]')          # a tag, not `<` in `@media (width < 600px)`
_ATTRIBUTE_URL = re.compile(r'url\s*\(', re.IGNORECASE)
_CSS_REFUSED = [
    (re.compile(r'@import\b', re.IGNORECASE), '@import loads CSS from outside the theme'),
    (re.compile(r'\bexpression\s*\(', re.IGNORECASE), 'expression() runs a script'),
    (re.compile(r'-moz-binding\b|\bbehavior\s*:', re.IGNORECASE), 'bindings run scripts'),
    (re.compile(r'javascript:', re.IGNORECASE), 'javascript: URLs run scripts'),
]


def _unescape_css(text):
    """CSS with its comments gone and its escapes undone (`\\75 rl(` is
    url(): what is looked for cannot hide behind them)."""
    text = _CSS_COMMENT.sub(' ', text)

    def plain(match):
        if match.group(1):
            code = int(match.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF else '�'
        return match.group(2) if match.group(2) not in '\n\r\f' else ''
    return _CSS_ESCAPE.sub(plain, text)


def _url_problem(url, allowed_types, where):
    """Why `url` may not be in a theme, or None when it may: a `data:` URL
    of one of `allowed_types`, or a `#fragment`."""
    url = re.sub(r'[\x00-\x20]', '', url or '')
    if not url:
        return None
    if url.startswith('#'):
        return None
    match = _DATA_URL.match(url)
    if match:
        if match.group(1).lower() in allowed_types:
            return None
        kinds = 'images and fonts' if allowed_types & FONT_TYPES else 'images'
        return f'{where}: a data: URL of type {match.group(1)} ({kinds} only)'
    shown = url if len(url) <= 60 else url[:57] + '…'
    return f'{where}: {shown!r} is not in the theme file (use a data: URL or #id)'


def check_css(text, where='CSS'):
    """What in stylesheet `text` a theme may not have, as messages."""
    problems = []
    plain = _unescape_css(text)
    for pattern, reason in _CSS_REFUSED:
        if pattern.search(plain):
            problems.append(f'{where}: {reason}')
    for match in _CSS_URL.finditer(plain):
        url = next(group for group in match.groups() if group is not None)
        problem = _url_problem(url, IMAGE_TYPES | FONT_TYPES, f'{where} url()')
        if problem:
            problems.append(problem)
    for match in _CSS_FUNCTION_URL.finditer(plain):
        problem = _url_problem(match.group(2), IMAGE_TYPES | FONT_TYPES, f'{where} {match.group(0).split("(")[0]}()')
        if problem:
            problems.append(problem)
    return problems


class _Reader(HTMLParser):
    """Walks the file as a browser's tokenizer does, noting what it must."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems = []
        self.manifests = []         # the manifest scripts' text
        self.slots = []             # data-slot values, in the order met
        self.in_manifest = False
        self.in_style = False
        self.manifest_text = []
        self.style_text = []
        self.svg_depth = 0
        self.scripts = 0            # <script> start tags met

    def _line(self):
        return f'line {self.getpos()[0]}'

    def handle_starttag(self, tag, attrs):
        self._element(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._element(tag, attrs, closed=True)

    def _element(self, tag, attrs, closed=False):
        tag = tag.lower()
        where = f'{self._line()} <{tag}>'
        if tag == 'script':
            self.scripts += 1
            values = {name.lower(): (value or '') for name, value in attrs}
            if values.get('type', '').strip().lower() != MANIFEST_TYPE or values.get('id') != MANIFEST_ID:
                self.problems.append(f'{where}: themes have no scripts (only the manifest, '
                                     f'<script type="{MANIFEST_TYPE}" id="{MANIFEST_ID}">)')
            elif values.get('src'):
                self.problems.append(f'{where}: the manifest is written in the file, not loaded')
            elif self.svg_depth:
                self.problems.append(f'{where}: the manifest goes in <head>, not inside <svg>')
            elif not closed:
                self.in_manifest, self.manifest_text = True, []
        elif tag not in ELEMENTS:
            reason = REASONS.get(tag, 'not an element themes may use')
            self.problems.append(f'{where}: {reason}')
        elif tag == 'style' and not closed:
            self.in_style, self.style_text = True, []
        if tag == 'svg' and not closed:
            self.svg_depth += 1
        if tag == 'meta':
            values = {name.lower() for name, _value in attrs}
            if values - {'charset', 'name', 'content'}:
                self.problems.append(f'{where}: only <meta charset> and <meta name=… content=…>')
        for name, value in attrs:
            self._attribute(tag, name.lower(), value or '', where)

    def _attribute(self, tag, name, value, where):
        if name.startswith('on'):
            self.problems.append(f'{where} {name}: themes have no scripts')
        elif name in REFUSED_ATTRIBUTES:
            self.problems.append(f'{where} {name}: {REFUSED_ATTRIBUTES[name]}')
        elif name in URL_ATTRIBUTES or name.endswith(':href'):
            problem = _url_problem(value, IMAGE_TYPES, f'{where} {name}')
            if problem:
                self.problems.append(problem)
        elif name == 'style':
            self.problems.extend(check_css(value, f'{where} style'))
        elif name == 'data-slot':
            self.slots.append(value)
        elif _ATTRIBUTE_URL.search(value):
            # SVG's fill, filter, mask, clip-path, marker…: url() as in CSS.
            self.problems.extend(check_css(value, f'{where} {name}'))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'svg' and self.svg_depth:
            self.svg_depth -= 1
        if tag == 'script' and self.in_manifest:
            self.in_manifest = False
            self.manifests.append(''.join(self.manifest_text))
        elif tag == 'style' and self.in_style:
            self.in_style = False
            self._style_done(f'{self._line()} <style>')

    def _style_done(self, where):
        css = ''.join(self.style_text)
        # Inside <svg> a browser reads tags in a <style> as tags; here they
        # would pass for CSS. Stylesheets have no use for them.
        if _MARKUP.search(css):
            self.problems.append(f'{where}: markup inside a stylesheet')
        self.problems.extend(check_css(css, where))

    def handle_data(self, data):
        if self.in_manifest:
            self.manifest_text.append(data)
        elif self.in_style:
            self.style_text.append(data)

    def handle_pi(self, data):
        self.problems.append(f'{self._line()}: a processing instruction')


class Report:
    """What check() found: `problems` (empty when the file may be used), the
    manifest's text (None when there is not exactly one) and the slots the
    markup names."""

    def __init__(self, problems, manifest, slots):
        self.problems = problems
        self.manifest = manifest
        self.slots = slots

    @property
    def ok(self):
        return not self.problems


def check(text):
    """Read theme file `text` (str): a Report."""
    reader = _Reader()
    reader.feed(text)
    reader.close()
    # A <style> or manifest left open runs to the end of the file, as in a browser.
    if reader.in_style:
        reader._style_done('<style>')
    if reader.in_manifest:
        reader.manifests.append(''.join(reader.manifest_text))
    problems = list(reader.problems)
    # Where html.parser and a browser part ways, refused outright: a comment
    # a browser ends at once (`<!-->`) or CDATA in HTML (a browser ends it
    # at the first `>`) could hide from this check what a browser runs.
    if _ABRUPT_COMMENT.search(text):
        problems.append('a comment written <!--> or <!--->: write <!-- … -->')
    if _CDATA.search(text):
        problems.append('<![CDATA[ … ]]>: not needed in a theme')
    if len(_SCRIPT_TAG.findall(text)) > reader.scripts:
        problems.append('a <script> this check could not place (inside a comment?)')
    manifest = None
    if not reader.manifests:
        problems.append(f'no manifest: <script type="{MANIFEST_TYPE}" id="{MANIFEST_ID}">')
    elif len(reader.manifests) > 1:
        problems.append('more than one manifest')
    else:
        manifest = reader.manifests[0]
    return Report(problems, manifest, reader.slots)
