"""Subtitle styles: manifests read and refused, settings kept per style and
held to their fields, the preview settings moved over from videoplayer, and
the settings as the template reads them.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from subtitld.modules import config, session, subtitle_styles as styles

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def write_style(root, folder, manifest, template='style.html'):
    path = root / folder
    path.mkdir(parents=True)
    (path / 'style.json').write_text(json.dumps(manifest), encoding='utf-8')
    if template:
        (path / template).write_text('<!DOCTYPE html>', encoding='utf-8')
    return path


def refusal(manifest, **kwargs):
    root = Path(tempfile.mkdtemp())
    try:
        styles.load(write_style(root, 'style', manifest, **kwargs))
    except styles.StyleError as error:
        return str(error)
    return None


GOOD = {'id': 'test.box', 'name': 'Box', 'fields': [
    {'key': 'size', 'type': 'number', 'default': 2.5, 'min': 1, 'max': 4, 'unit': 'em'},
    {'key': 'boxed', 'type': 'bool', 'default': True},
    {'key': 'edge', 'type': 'color', 'default': '#80FF0000', 'visible_if': 'boxed'},
    {'key': 'motion', 'type': 'select', 'default': 'fade', 'options': ['none', 'fade',
                                                                         {'value': 'pop', 'label': 'Pop in'}]},
    {'key': 'family', 'type': 'font', 'default': 'Inter'},
    {'key': 'title', 'type': 'text', 'default': 'Hi "you"'},
]}

print('the built-in style')
session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'subtitld.config')
classic = styles.get()
check('the default is Classic', (classic.id, classic.name, classic.builtin), ('subtitld.classic', 'Classic', True))
check('its fields: the preview settings before styles', [f['key'] for f in classic.fields],
      ['font_family', 'font_size', 'color', 'shadow_enabled', 'shadow_x', 'shadow_y', 'shadow_color',
       'backgroundbox_enabled', 'backgroundbox_padding', 'backgroundbox_border_radius', 'backgroundbox_color'])
check('its defaults: the old ones', styles.values(),
      {'font_family': 'Montserrat', 'font_size': 40, 'color': '#ffffffff', 'shadow_enabled': True,
       'shadow_x': 2, 'shadow_y': 2, 'shadow_color': '#ff000000', 'backgroundbox_enabled': True,
       'backgroundbox_padding': 10, 'backgroundbox_border_radius': 5, 'backgroundbox_color': '#55000000'})
check('nothing refused', styles.problems, {})

print('manifests')
user_root = session.PATH_SUBTITLD_USER_DATA / 'subtitle_styles'
write_style(user_root, 'box', GOOD)
write_style(user_root, 'impostor', dict(GOOD, id='subtitld.classic', name='Not Classic'))
write_style(user_root, 'broken', {'id': 'test.broken', 'name': 'Broken', 'fields': [{'key': 'x', 'type': 'shape', 'default': 1}]})
found = styles.available(reload=True)
check('a user\'s style is found, after the built-in ones', list(found), ['subtitld.classic', 'test.box'])
check('a built-in id cannot be taken', styles.problems.get(user_root / 'impostor'), 'id subtitld.classic is taken')
check('a broken one is left out, and why', styles.problems.get(user_root / 'broken'), 'field "x": unknown type \'shape\'')
box = found['test.box']
check('select options in one shape', box.field('motion')['options'],
      [{'value': 'none', 'label': 'none'}, {'value': 'fade', 'label': 'fade'}, {'value': 'pop', 'label': 'Pop in'}])
check('a colour default as Qt writes it', box.field('edge')['default'], '#80ff0000')
check('no id', refusal({'name': 'X'}), "id None: lowercase letters, digits, \".\", \"_\" and \"-\" only")
check('an id with capitals', bool(refusal(dict(GOOD, id='Test.Box'))), True)
check('no name', refusal({'id': 'x'}), 'no name')
check('no template', refusal(GOOD, template=None), "template 'style.html' is not a file in the style's folder")
check('a template outside the folder', bool(refusal(dict(GOOD, template='../style.html'))), True)
check('a field twice', refusal(dict(GOOD, fields=GOOD['fields'] + [GOOD['fields'][0]])), 'field "size" twice')
check('no default', refusal(dict(GOOD, fields=[{'key': 'a', 'type': 'bool'}])), 'field "a": no default')
check('a default the field cannot hold', refusal(dict(GOOD, fields=[{'key': 'a', 'type': 'color', 'default': 'red'}])),
      'field "a": default \'red\' is not a color')
check('a select with no options', refusal(dict(GOOD, fields=[{'key': 'a', 'type': 'select', 'default': 1}])),
      'field "a": a select needs options')
check('visible_if naming no bool', refusal(dict(GOOD, fields=[
    {'key': 'a', 'type': 'int', 'default': 1, 'visible_if': 'b'}])), 'field "a": visible_if names no bool field')

print('settings')
check('a number past its range is clamped', styles.set_value('size', 9, 'test.box'), 4.0)
check('an int is rounded', styles.set_value('font_size', 41.6), 42)
check('a colour, lower case', styles.set_value('color', '#FF00FF00'), '#ff00ff00')
check('six digits: opaque', styles.set_value('edge', '#00ff00', 'test.box'), '#ff00ff00')
for key, value, style_id in (('boxed', 'yes', 'test.box'), ('motion', 'spin', 'test.box'), ('edge', 'green', 'test.box'),
                             ('size', 'big', 'test.box'), ('size', True, 'test.box')):
    try:
        styles.set_value(key, value, style_id)
        check(f'{key} = {value!r} refused', False, True)
    except ValueError:
        check(f'{key} = {value!r} refused', True, True)
try:
    styles.set_value('nothing', 1)
    check('no such field: KeyError', False, True)
except KeyError:
    check('no such field: KeyError', True, True)
check('kept per style', styles.values('test.box')['size'], 4.0)
check('and Classic\'s apart', (styles.values()['font_size'], styles.values()['color']), (42, '#ff00ff00'))
styles.set_value('font_size', 40)
check('a default is not saved', session.CONFIG['subtitle_style']['settings']['subtitld.classic'], {'color': '#ff00ff00'})
session.CONFIG['subtitle_style']['settings']['test.box']['motion'] = 'spin'
check('a saved value the field cannot hold: the default', styles.values('test.box')['motion'], 'fade')
styles.set_current('test.box')
check('the current style', styles.get().id, 'test.box')
check('switching back keeps what was set', (styles.values('subtitld.classic')['color'], styles.values()['size']),
      ('#ff00ff00', 4.0))
session.CONFIG['subtitle_style']['current'] = 'gone.style'
check('a current style no longer there: Classic', styles.get().id, 'subtitld.classic')
try:
    styles.set_current('gone.style')
    check('picking one that is not there: KeyError', False, True)
except KeyError:
    check('picking one that is not there: KeyError', True, True)

print('as the template reads them')
check('Classic', styles.css_variables('subtitld.classic'),
      {'--font-family': '"Montserrat"', '--font-size': '40px', '--color': '#00ff00', '--shadow-enabled': '1',
       '--shadow-x': '2px', '--shadow-y': '2px', '--shadow-color': '#000000', '--backgroundbox-enabled': '1',
       '--backgroundbox-padding': '10px', '--backgroundbox-border-radius': '5px',
       '--backgroundbox-color': 'rgba(0, 0, 0, 0.333)'})
check('every type', styles.css_variables('test.box'),
      {'--size': '4em', '--boxed': '1', '--edge': '#00ff00', '--motion': 'fade', '--family': '"Inter"',
       '--title': '"Hi \\"you\\""'})

print('from videoplayer, where the settings were')
path = Path(tempfile.mkdtemp()) / 'subtitld.config'
path.write_text(json.dumps({'videoplayer': {'font_size': 55, 'shadow_enabled': False, 'color': '#ffffff00',
                                             'safe_margin_title_x': 12, 'music_voice_separation_volume': 0.3}}))
session.CONFIG = config.Config(path)
check('they leave videoplayer; the guides and the rest stay', session.CONFIG['videoplayer'],
      {'safe_margin_title_x': 12, 'music_voice_separation_volume': 0.3})
check('they are Classic\'s', session.CONFIG['subtitle_style'],
      {'settings': {'subtitld.classic': {'font_size': 55, 'shadow_enabled': False, 'color': '#ffffff00'}}})
check('and read as Classic\'s', {k: styles.values()[k] for k in ('font_size', 'shadow_enabled', 'color', 'shadow_x')},
      {'font_size': 55, 'shadow_enabled': False, 'color': '#ffffff00', 'shadow_x': 2})
check('the ASS and TTML "preview" look reads them too', styles.preview_look()['font_size'], 55)
session.CONFIG.save()
session.CONFIG = config.Config(path)
check('saved and read again: moved once', (session.CONFIG['videoplayer'], styles.values()['font_size']),
      ({'safe_margin_title_x': 12, 'music_voice_separation_volume': 0.3}, 55))
check('a config with both keeps what the style has', (
    styles.migrate(cfg := {'videoplayer': {'font_size': 70}, 'subtitle_style': {'settings': {'subtitld.classic': {'font_size': 30}}}}),
    cfg['subtitle_style']['settings']['subtitld.classic']['font_size'], cfg['videoplayer'])[1:], (30, {}))

print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
