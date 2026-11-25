"""A project's subtitle theme (modules.subtitle_styles): the project's
choice and values, the app's as where a project that says nothing starts,
a subtitle's own theme, a speaker's own values of per-speaker fields, the
USFX that carries them, and the renderer drawing each subtitle with its
own theme and its speaker's values.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import json, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import config, session, subtitle_styles as styles, themes, usf
from subtitld.modules.segment import Segment, Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


# A second subtitle theme, with a per-speaker colour.
manifest = {'format': 1, 'id': 'test.boxed', 'kind': 'subtitle', 'name': 'Boxed', 'version': '26.12.3',
            'fields': [{'key': 'color', 'type': 'color', 'default': '#ffffffff', 'per_speaker': True},
                       {'key': 'size', 'type': 'int', 'default': 50, 'unit': 'px'}]}
(session.PATH_SUBTITLD_USER_DATA / 'themes').mkdir(parents=True, exist_ok=True)
(session.PATH_SUBTITLD_USER_DATA / 'themes' / 'boxed.html').write_text(
    '<!doctype html><html><head><meta charset="utf-8">'
    f'<script type="application/json" id="subtitld-theme">{json.dumps(manifest)}</script>'
    '<style>.cue { font: var(--size) sans-serif; color: var(--color); background: #000; padding: 10px }</style>'
    '</head><body></body></html>')
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.SUBTITLE = Subtitle(segments=[])
check('both themes there', sorted(styles.available(reload=True)), ['subtitld.classic', 'test.boxed'])

print('a project that says nothing: the app\'s')
session.CONFIG['subtitle_style'] = {'current': 'subtitld.classic', 'settings': {'subtitld.classic': {'font_size': 60}}}
check('the app\'s style', styles.get().id, 'subtitld.classic')
check('the app\'s values', styles.values()['font_size'], 60)
check('no table in the project yet', session.SUBTITLE.theme, None)

print('set in the project')
styles.set_value('color', '#ffff0000')
check('the project takes over what it showed', session.SUBTITLE.theme['values']['subtitld.classic'],
      {'font_size': 60, 'color': '#ffff0000'})
check('and the app keeps it, for new projects', session.CONFIG['subtitle_style']['settings']['subtitld.classic']['color'],
      '#ffff0000')
session.CONFIG['subtitle_style']['settings']['subtitld.classic']['font_size'] = 20
check('the project\'s own, whatever the app has now', styles.values()['font_size'], 60)
styles.set_current('test.boxed')
check('the project\'s choice', (session.SUBTITLE.theme['id'], session.SUBTITLE.theme['version']),
      ('test.boxed', '26.12.3'))
check('the app\'s choice follows', session.CONFIG['subtitle_style']['current'], 'test.boxed')
session.CONFIG['subtitle_style']['current'] = 'subtitld.classic'
check('the project\'s choice wins', styles.get().id, 'test.boxed')

print('a subtitle\'s own')
check('of() the project\'s', styles.of(Segment({'text': 'a'})).id, 'test.boxed')
check('of() its own', styles.of(Segment({'text': 'a', 'theme': 'subtitld.classic'})).id, 'subtitld.classic')
check('of() one not here: the project\'s', styles.of(Segment({'text': 'a', 'theme': 'gone.theme'})).id, 'test.boxed')

print('a speaker\'s own')
styles.set_value('color', '#ff00ff00', speaker='Ana')
check('their value', styles.values('test.boxed', 'Ana')['color'], '#ff00ff00')
check('the others keep the project\'s', styles.values('test.boxed', 'Bo')['color'], '#ffffffff')
try:
    styles.set_value('size', 10, speaker='Ana')
    check('size is not per speaker', False, True)
except KeyError:
    check('size is not per speaker', True, True)
check('speaker_values', styles.speaker_values('test.boxed', 'Ana'), {'color': '#ff00ff00'})
styles.rename_speaker('Ana', 'Ana Lima')
check('a rename takes them along', styles.speaker_values('test.boxed', 'Ana Lima'), {'color': '#ff00ff00'})
styles.set_value('color', None, speaker='Ana Lima')
check('taken away', styles.speaker_values('test.boxed', 'Ana Lima'), {})
styles.set_value('color', '#ff00ff00', speaker='Ana')

print('in the file')
segments = [Segment({'start': 1, 'end': 2, 'text': 'one', 'speaker': 'Ana'}),
            Segment({'start': 2, 'end': 3, 'text': 'two', 'theme': 'subtitld.classic'})]
table = styles.project_table(segments)
check('table: the project\'s style', (table['id'], table['version']), ('test.boxed', '26.12.3'))
check('table: values of the styles in use', sorted(table['values']), ['subtitld.classic', 'test.boxed'])
check('table: the speakers', table['speakers'], {'Ana': {'test.boxed': {'color': '#ff00ff00'}}})
content = usf.USFWriter().write(segments, language='en-us', theme=table)
check('written in the subtitldcc namespace', 'subtitldcc:theme' in content, True)
reader = usf.USFReader()
read = reader.read(content)
check('read back', styles.adopt(reader.theme), table)
check('a subtitle\'s own theme read back', [s.get('theme') for s in read], [None, 'subtitld.classic'])
check('adopt drops nonsense', styles.adopt({'id': 5, 'values': [1], 'speakers': {'x': 3}}), {'speakers': {}})
check('adopt of nothing', styles.adopt(None), None)

print('rendered')
from subtitld.interface import subtitle_renderer


def wait_for(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


def layer(renderer, segment):
    def ready():
        renderer.layers([segment], segment['start'])
        return renderer._job(segment, segment['start'], *renderer._style())[0] in renderer.cache
    wait_for(ready)
    out = renderer.layers([segment], segment['start'])
    return out[0] if out else None


session.VIDEO = {'framerate': 25}
renderer = subtitle_renderer.SubtitleRenderer(disk_cache=None)
renderer.set_size(960, 540)
plain = Segment({'start': 1, 'end': 2, 'text': 'Hello', 'speaker': 'Bo'})
green = Segment({'start': 1, 'end': 2, 'text': 'Hello', 'speaker': 'Ana'})
classic = Segment({'start': 1, 'end': 2, 'text': 'Hello', 'theme': 'subtitld.classic', 'speaker': 'Bo'})
style, variables, attributes = renderer._style()
key_plain, job_plain = renderer._job(plain, 1, style, variables, attributes)
key_green, job_green = renderer._job(green, 1, style, variables, attributes)
key_classic, job_classic = renderer._job(classic, 1, style, variables, attributes)
check('a speaker\'s values: a layer of their own', key_plain != key_green, True)
check('their colour in the job', job_green['page']['vars']['--color'], '#00ff00')
check('the same template', 'template' in job_green, False)
check('a subtitle\'s own theme: its template', Path(job_classic.get('template', '')).name.startswith('subtitld.classic-'), True)
check('its own settings', job_classic['page']['vars'].get('--font-size'), '60px')

layers = [layer(renderer, segment) for segment in (plain, green, classic)]
check('all three drawn', all(found is not None for found in layers), True)
if all(found is not None for found in layers):
    def has(found, test):
        image = found[1]
        return any(test(image.pixelColor(x, y)) for y in range(0, image.height(), 2) for x in range(0, image.width(), 2))
    check('Bo\'s in white', has(layers[0], lambda c: c.red() > 200 and c.green() > 200 and c.blue() > 200), True)
    check('Ana\'s in green, none white', (has(layers[1], lambda c: c.green() > 200 and c.red() < 80),
                                          has(layers[1], lambda c: c.red() > 200 and c.green() > 200 and c.blue() > 200)),
          (True, False))
    check('the Classic one larger (60px)', layers[2][1].height() > layers[0][1].height(), True)
print('an export\'s one style for all')
renderer.style_id, renderer.one_style = 'test.boxed', True
key_one, job_one = renderer._job(classic, 1, *renderer._style())
check('a subtitle\'s own theme set aside', 'template' in job_one, False)
renderer.close()

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
