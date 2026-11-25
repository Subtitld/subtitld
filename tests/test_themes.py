"""Subtitle and title themes as self-contained HTML files (modules.themes,
modules.theme_sanitize): a good file reads as a theme with its slots,
fields, timing and fallback; a file with a script, a remote URL, SMIL, a
bad manifest or slots the markup and the manifest disagree on is refused,
with the reason.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, sys, tempfile
from pathlib import Path
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import session, theme_sanitize, themes

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


TITLE_MANIFEST = {
    'format': 1, 'id': 'test.newsroom', 'kind': 'title', 'name': 'Newsroom', 'version': '26.12.1',
    'min_subtitld': '26.12', 'author': 'Someone', 'licence': 'CC BY 4.0',
    'timing': {'intro': 400, 'outro': 300},
    'slots': [{'key': 'main', 'type': 'text', 'label': 'Name'},
              {'key': 'role', 'type': 'text', 'label': 'Role', 'max_length': 60, 'placeholder': 'Their role'},
              {'key': 'logo', 'type': 'image', 'label': 'Logo', 'visible_if': 'show_logo'},
              {'key': 'people', 'type': 'list', 'of': 'text', 'default': ['A', 'B']}],
    'fields': [{'key': 'accent', 'type': 'color', 'default': '#ffe5a823', 'per_speaker': True},
               {'key': 'show_logo', 'type': 'bool', 'default': True}],
    'fallback': {'placement': 'lower_third', 'color': '#ffffff', 'font_size': 54},
}
PNG = 'data:image/png;base64,iVBORw0KGgo='
FONT = 'data:font/woff2;base64,d09GMgAB'


def page(manifest=None, head='', body=None, manifest_tag=None):
    manifest = TITLE_MANIFEST if manifest is None else manifest
    if body is None:
        body = (f'<div class="card"><img data-slot="logo" src="{PNG}"><h1 data-slot="main">Maria</h1>'
                f'<p data-slot="role">Reporter</p><ul data-slot="people"><li>Someone</li></ul>'
                '<svg viewBox="0 0 10 10"><linearGradient id="g"/><rect width="10" height="10" fill="url(#g)"/>'
                '</svg></div>')
    tag = manifest_tag or '<script type="application/json" id="subtitld-theme">'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
            f'{tag}{json.dumps(manifest)}</script>'
            f'<style>@font-face {{ font-family: Inter; src: url({FONT}); }}'
            ' :root { --accent: #e5a823 } @media (aspect-ratio < 1) { .card { left: 0 } }'
            f' .card {{ background: url("{PNG}"); animation: in .4s both }} @keyframes in {{ from {{ opacity: 0 }} }}'
            f'</style>{head}</head><body>{body}</body></html>')


def refused(text):
    """The problems a file is refused for, or [] when it reads as a theme."""
    try:
        themes.parse(text)
    except themes.ThemeError as error:
        return error.problems
    return []


def refused_for(name, text, *words):
    problems = refused(text)
    found = bool(problems) and all(any(word in problem for problem in problems) for word in words)
    check(name, found, True)
    if not found:
        print('       problems:', problems)


print('a good title theme')
theme = themes.parse(page().encode())
check('id', theme.id, 'test.newsroom')
check('kind', theme.kind, 'title')
check('animated', theme.animated, True)
check('timing', theme.timing, {'intro': 400, 'outro': 300})
check('slots', [(s['key'], s['type']) for s in theme.slots],
      [('main', 'text'), ('role', 'text'), ('logo', 'image'), ('people', 'list')])
check('slot hints kept', (theme.slot('role')['max_length'], theme.slot('role')['placeholder']), (60, 'Their role'))
check('slot visible_if', theme.slot('logo')['visible_if'], 'show_logo')
check('list default', theme.slot('people')['default'], ['A', 'B'])
check('per_speaker field', theme.field('accent')['per_speaker'], True)
check('fallback colour gets its alpha', theme.fallback['color'], '#ffffffff')
check('marked slots', theme.marked_slots, ['logo', 'main', 'role', 'people'])
check('html kept as it came', theme.html, page())
check('BOM is fine', themes.parse(b'\xef\xbb\xbf' + page().encode()).id, 'test.newsroom')

print('a subtitle theme')
subtitle = {'format': 1, 'id': 'test.plain', 'kind': 'subtitle', 'name': 'Plain'}
theme = themes.parse(page(subtitle, body='<p>No slot: Subtitld places the text</p>'))
check('main slot implied', [(s['key'], s['type']) for s in theme.slots], [('main', 'text')])
check('not animated without timing', theme.animated, False)
theme = themes.parse(page(subtitle, body='<div class="frame"><p data-slot="main">Sample</p></div>'))
check('main slot in the markup', theme.marked_slots, ['main'])
refused_for('subtitle theme with a second slot',
            page(dict(subtitle, slots=[{'key': 'main'}, {'key': 'extra'}]),
                 body='<p data-slot="main">a</p><p data-slot="extra">b</p>'), 'one slot')

print('scripts')
refused_for('<script>', page(head='<script>alert(1)</script>'), 'no scripts')
refused_for('<script src>', page(head='<script src="x.js"></script>'), 'no scripts')
refused_for('JSON script with another id', page(head='<script type="application/json" id="x">{}</script>'),
            'no scripts')
refused_for('SVG <script>', page(body='<svg><script>alert(1)</script></svg>'), 'no scripts')
refused_for('onerror', page(body=f'<img src="{PNG}" onerror="alert(1)">'), 'onerror')
refused_for('onload on svg', page(body='<svg onload="alert(1)"></svg>'), 'onload')
refused_for('javascript: href', page(body='<svg><a href="javascript:alert(1)"><text>x</text></a></svg>'),
            'not an element')
refused_for('javascript: in an entity-encoded href',
            page(body='<svg><use href="java&#115;cript:alert(1)"/></svg>'), 'not in the theme file')
refused_for('second manifest', page(head='<script type="application/json" id="subtitld-theme">{}</script>'),
            'more than one manifest')
refused_for('no manifest', '<!doctype html><html><body><p>x</p></body></html>', 'no manifest')
refused_for('manifest inside svg',
            page(body='<svg><script type="application/json" id="subtitld-theme">{}</script></svg>'), 'inside <svg>')

refused_for('script after <!-->', page(body='<!--><script>alert(1)</script>-->'), 'comment written')
refused_for('onerror after <!--->', page(body='<!---><img src=x onerror=alert(1)>-->'), 'comment written')
refused_for('script after CDATA', page(body='<![CDATA[><script>alert(1)</script>]]>'), 'CDATA')
check('ordinary comments are fine', refused(page(body=page().split('<body>')[1].split('</body>')[0] + '<!-- a note -->')), [])

print('outside the file')
refused_for('remote img', page(body='<img src="https://example.com/a.png" data-slot="main">'),
            'not in the theme file')
refused_for('relative img', page(body='<img src="logo.png">'), 'not in the theme file')
refused_for('data: html in an img', page(body='<img src="data:text/html;base64,PHA+">'), 'text/html')
refused_for('srcset', page(body=f'<img src="{PNG}" srcset="https://example.com/a.png 2x">'), 'srcset')
refused_for('<link>', page(head='<link rel="stylesheet" href="https://example.com/a.css">'), 'link')
refused_for('<iframe>', page(body='<iframe src="data:text/html,x"></iframe>'), 'another page')
refused_for('<base>', page(head='<base href="https://example.com/">'), 'base')
refused_for('meta refresh', page(head='<meta http-equiv="refresh" content="0;url=https://example.com">'),
            'meta')
refused_for('svg image from the web', page(body='<svg><image href="https://example.com/a.png"/></svg>'),
            'not in the theme file')
refused_for('svg xlink:href', page(body='<svg><use xlink:href="other.svg#a"/></svg>'), 'not in the theme file')
refused_for('svg fill url() from the web', page(body='<svg><rect fill="url(https://example.com/a.svg#p)"/></svg>'),
            'not in the theme file')

print('CSS')
refused_for('@import', page(head='<style>@import url("https://example.com/a.css");</style>'), '@import')
refused_for('remote font', page(head='<style>@font-face { src: url(https://example.com/a.woff2) }</style>'),
            'not in the theme file')
refused_for('relative background', page(head='<style>.a { background: url(bg.png) }</style>'),
            'not in the theme file')
refused_for('escaped url()', page(head='<style>.a { background: \\75 rl(https://example.com/a.png) }</style>'),
            'not in the theme file')
refused_for('a comment inside url()',
            page(head='<style>.a { background: url(/**/https://example.com/a.png) }</style>'), 'not in the theme file')
check('url() in a comment is fine', refused(page(head='<style>/* url(https://example.com/a.png) */</style>')), [])
refused_for('image-set', page(head='<style>.a { background: image-set("https://example.com/a.png" 1x) }</style>'),
            'not in the theme file')
refused_for('style attribute', page(body='<p style="background:url(https://example.com/a.png)">x</p>'),
            'not in the theme file')
refused_for('expression()', page(head='<style>.a { width: expression(alert(1)) }</style>'), 'expression')
refused_for('markup in an svg style',
            page(body='<svg><style><img src=x onerror=alert(1)></style></svg>'), 'markup inside')
check('media range syntax is fine', refused(page(head='<style>@media (width < 600px) { .a { color: red } }</style>')),
      [])

print('animation')
refused_for('SMIL <animate>', page(body='<svg><rect><animate attributeName="x" to="5"/></rect></svg>'),
            'CSS animations')
refused_for('SMIL <set>', page(body='<svg><set attributeName="href" to="javascript:alert(1)"/></svg>'),
            'CSS animations')

print('manifest')
refused_for('format 2', page(dict(TITLE_MANIFEST, format=2)), 'format')
refused_for('bad id', page(dict(TITLE_MANIFEST, id='Bad ID')), 'id')
refused_for('bad kind', page(dict(TITLE_MANIFEST, kind='banner')), 'kind')
refused_for('no name', page(dict(TITLE_MANIFEST, name=' ')), 'no name')
refused_for('bad version', page(dict(TITLE_MANIFEST, version='v1')), 'version')
refused_for('bad timing', page(dict(TITLE_MANIFEST, timing={'intro': -1})), 'timing intro')
refused_for('unknown timing key', page(dict(TITLE_MANIFEST, timing={'middle': 3})), 'timing')
refused_for('unknown manifest key', page(dict(TITLE_MANIFEST, colour='red')), 'unknown keys colour')
refused_for('JSON error', page(manifest_tag='<script type="application/json" id="subtitld-theme">{,'), 'manifest')
refused_for('no main slot', page(dict(TITLE_MANIFEST, slots=TITLE_MANIFEST['slots'][1:])), 'no "main"')
refused_for('main slot is an image', page(dict(TITLE_MANIFEST, slots=[{'key': 'main', 'type': 'image'}]),
                                          body='<img data-slot="main">'), 'text slot')
refused_for('slot twice', page(dict(TITLE_MANIFEST, slots=TITLE_MANIFEST['slots'] + [{'key': 'role'}])), 'twice')
refused_for('unknown slot type', page(dict(TITLE_MANIFEST, slots=TITLE_MANIFEST['slots'] + [{'key': 'clip', 'type': 'video'}]),
                                      body=page().split('<body>')[1].split('</body>')[0] + '<div data-slot="clip"></div>'),
            'unknown type')
refused_for('slot without an element', page(dict(TITLE_MANIFEST, slots=TITLE_MANIFEST['slots'] + [{'key': 'date'}])),
            'no element with data-slot="date"')
refused_for('element without a slot', page(body=page().split('<body>')[1].split('</body>')[0] + '<p data-slot="extra"></p>'),
            'not a slot of the manifest')
refused_for('visible_if on no bool', page(dict(TITLE_MANIFEST, slots=[{'key': 'main'}, {'key': 'role', 'visible_if': 'accent'}]),
                                          body='<p data-slot="main"></p><p data-slot="role"></p>'), 'visible_if')
refused_for('hint on an image slot', page(dict(TITLE_MANIFEST, slots=[{'key': 'main'}, {'key': 'logo', 'type': 'image', 'multiline': True}]),
                                          body='<p data-slot="main"></p><img data-slot="logo">'), 'is for text')
refused_for('bad field', page(dict(TITLE_MANIFEST, fields=[{'key': 'size', 'type': 'int'}])), 'no default')
refused_for('unknown field type', page(dict(TITLE_MANIFEST, fields=[{'key': 'x', 'type': 'gradient', 'default': 1}])),
            'unknown type')
refused_for('per_speaker not a bool', page(dict(TITLE_MANIFEST, fields=[{'key': 'accent', 'type': 'color',
                                                                         'default': '#ff000000', 'per_speaker': 'yes'},
                                                                        {'key': 'show_logo', 'type': 'bool', 'default': True}])),
            'per_speaker')
refused_for('unknown fallback key', page(dict(TITLE_MANIFEST, fallback={'glow': True})), 'fallback: unknown key')
refused_for('bad fallback value', page(dict(TITLE_MANIFEST, fallback={'placement': 'left'})), 'fallback placement')
refused_for('no doctype', page().replace('<!doctype html>', ''), 'doctype')
refused_for('bad slice', page(dict(TITLE_MANIFEST, slice='syllables')), 'slice')
check('slice kept', themes.parse(page(dict(TITLE_MANIFEST, slice='chars'))).slice, 'chars')
refused_for('other slots marked but not main',
            page(dict(TITLE_MANIFEST, slots=[{'key': 'main'}, {'key': 'role'}]), body='<p data-slot="role"></p>'),
            'data-slot="main"')
check('built-in Classic reads', themes.load(Path(__file__).resolve().parents[1] / 'src/subtitld/themes/classic.html').slotted, False)
check('built-in Plain reads', themes.load(Path(__file__).resolve().parents[1] / 'src/subtitld/themes/plain.html').kind, 'title')
refused_for('every problem listed', page(dict(TITLE_MANIFEST, kind='x', version='y'), head='<script>1</script>'),
            'kind', 'version', 'no scripts')

print('size and encoding')
refused_for('too large', b'x' * (themes.MAX_BYTES + 1), 'larger than')
refused_for('not UTF-8', b'\xff\xfe<html>', 'not UTF-8')

print('files and folders')
session.PATH_SUBTITLD = Path(tempfile.mkdtemp())
session.PATH_SUBTITLD_USER_DATA = Path(tempfile.mkdtemp())
(session.PATH_SUBTITLD / 'themes').mkdir()
(session.PATH_SUBTITLD_USER_DATA / 'themes').mkdir()
(session.PATH_SUBTITLD / 'themes' / 'newsroom.html').write_text(page())
(session.PATH_SUBTITLD / 'themes' / 'plain.html').write_text(page(subtitle, body='<p>x</p>'))
(session.PATH_SUBTITLD_USER_DATA / 'themes' / 'copy.html').write_text(page())
(session.PATH_SUBTITLD_USER_DATA / 'themes' / 'bad.html').write_text(page(head='<script>1</script>'))
(session.PATH_SUBTITLD_USER_DATA / 'themes' / 'future.html').write_text(
    page(dict(subtitle, id='test.future', min_subtitld='99.1'), body='<p>x</p>'))
(session.PATH_SUBTITLD_USER_DATA / 'themes' / 'notes.txt').write_text('not a theme')
from subtitld.modules.addons import installer
installer.__version__ = '26.12.1'           # a release, not a development build (those run every theme)
found = themes.available(reload=True)
check('available', sorted(found), ['test.newsroom', 'test.plain'])
check('by kind', sorted(themes.available('subtitle')), ['test.plain'])
check('built-in flag', found['test.newsroom'].builtin, True)
check('path kept', found['test.plain'].path.name, 'plain.html')
check('problems', sorted((path.name, reason.split(' (')[0].split(':')[-1].strip()) for path, reason in themes.problems.items()),
      [('bad.html', 'themes have no scripts'), ('copy.html', 'id test.newsroom is taken'),
       ('future.html', 'needs Subtitld 99.1')])
check('development build is new enough', themes.is_new_enough(found['test.plain'], '0.0.0.dev0'), True)
check('older Subtitld is not', themes.is_new_enough(themes.parse(page(dict(subtitle, min_subtitld='26.12'), body='<p>x</p>')), '26.11'),
      False)

print('the check on its own')
report = theme_sanitize.check(page())
check('report ok', report.ok, True)
check('report manifest', json.loads(report.manifest)['id'], 'test.newsroom')

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
