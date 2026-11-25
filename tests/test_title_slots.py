"""A title's other parts (modules.title_themes): the slots of its theme
besides its text, what it puts in them and their translations, kept in its
USFX, swapped in for a translation's export, drawn by the renderer in the
theme's markup (Lower third, a built-in theme with a role and a logo), and
edited in the editor's slots panel.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself.
"""
import base64, os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit
app = QApplication([])
from subtitld.modules import config, export_options, session, title_themes, usf
from subtitld.modules.segment import Segment, Subtitle

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def raises(name, error, call):
    try:
        call()
    except error:
        check(name, True, True)
        return
    check(name, False, True)


session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'c.config')
session.SUBTITLE = Subtitle(segments=[])
session.VIDEO = {'framerate': 25}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')

print('the built-in Lower third')
check('there, beside Plain', sorted(title_themes.available(reload=True)), ['subtitld.lower_third', 'subtitld.plain'])
title = Segment({'kind': 'title', 'start': 1, 'end': 5, 'text': 'Maria Souza', 'title_theme': 'subtitld.lower_third'})
check('its other slots', [s['key'] for s in title_themes.extra_slots(title)], ['role', 'logo'])
check('shown', [s['key'] for s in title_themes.shown_slots(title)], ['role', 'logo'])
title_themes.set_value(title, 'show_logo', False)
check('a setting hides the logo', [s['key'] for s in title_themes.shown_slots(title)], ['role'])
title_themes.set_value(title, 'show_logo', True)
check('Plain has none', title_themes.extra_slots(Segment({'kind': 'title', 'text': 'x'})), [])

print('what a title puts in them')
title_themes.set_slot(title, 'role', 'Field reporter\nin Lisbon')
check('a line, not two', title['title_slots']['role'], 'Field reporter in Lisbon')
title_themes.set_slot(title, 'role', 'x' * 200)
check('cut to max_length', len(title['title_slots']['role']), 80)
title_themes.set_slot(title, 'role', 'Field reporter')
raises('a picture must be a data: URL', ValueError, lambda: title_themes.set_slot(title, 'logo', 'https://example.com/a.png'))
raises('no such slot', KeyError, lambda: title_themes.set_slot(title, 'date', 'today'))
path = Path(tempfile.mkdtemp()) / 'logo.png'
path.write_bytes(PNG)
logo = title_themes.image_data_url(path)
check('a picture as a data: URL', logo.startswith('data:image/png;base64,'), True)
title_themes.set_slot(title, 'logo', logo)
check('kept', title_themes.slot_value(title, 'logo') == logo, True)
raises('not a picture', ValueError, lambda: title_themes.image_data_url(path.with_suffix('.txt')))
big = path.with_name('big.png')
big.write_bytes(b'\0' * (title_themes.MAX_IMAGE_BYTES + 1))
raises('too big', ValueError, lambda: title_themes.image_data_url(big))

print('translations')
title_themes.set_slot(title, 'role', 'Repórter de campo', 'pt-br')
check('kept by language', title['title_slot_translations'], {'pt-br': {'role': 'Repórter de campo'}})
check('read in the language', title_themes.slot_value(title, 'role', 'pt-br'), 'Repórter de campo')
check('the original elsewhere', title_themes.slot_value(title, 'role', 'es'), 'Field reporter')
raises('a picture is the same in every language', ValueError,
       lambda: title_themes.set_slot(title, 'logo', logo, 'pt-br'))
title_themes.set_slot(title, 'role', '', 'es')
check('emptying one that is not there leaves the rest', title['title_slot_translations'], {'pt-br': {'role': 'Repórter de campo'}})

print('a change of theme keeps them')
title['title_theme'] = 'subtitld.plain'
check('not shown by Plain', title_themes.extra_slots(title), [])
check('still there', title['title_slots']['role'], 'Field reporter')
title['title_theme'] = 'subtitld.lower_third'
check('back with Lower third', title_themes.slot_value(title, 'role'), 'Field reporter')

print('in the file')
content = usf.USFWriter().write([title], language='en-us')
read = usf.USFReader().read(content)
check('slots read back', read[0].get('title_slots'), title['title_slots'])
check('their translations too', read[0].get('title_slot_translations'), title['title_slot_translations'])

print('a translation\'s export')
title['translations'] = {'pt-br': 'Maria Souza'}
prepared = export_options.prepare([title], {'text': 'translation', 'language': 'pt-br'})
check('the translated role', prepared[0]['title_slots']['role'], 'Repórter de campo')
check('the logo kept', prepared[0]['title_slots']['logo'] == logo, True)
check('the original untouched', title['title_slots']['role'], 'Field reporter')
prepared = export_options.prepare([title], {'text': 'original'})
check('the original export: the original role', prepared[0]['title_slots']['role'], 'Field reporter')

print('rendered')
from subtitld.interface import subtitle_renderer


def wait_for(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


def layer(renderer, segment, at):
    def ready():
        renderer.layers([segment], at)
        return renderer._job(segment, at, *renderer._style())[0] in renderer.cache
    wait_for(ready)
    out = renderer.layers([segment], at)
    return out[0] if out else None


renderer = subtitle_renderer.SubtitleRenderer(disk_cache=None)
renderer.set_size(960, 540)
key, job = renderer._job(title, 3.0, *renderer._style())
check('the role in the job', job['page']['slots']['role'], 'Field reporter')
check('the logo in the job', job['page']['slots']['logo'] == logo, True)
check('its outro', job['page']['outro'], 400)
check('the served file', Path(job['template']).name.startswith('subtitld.lower_third-'), True)
settled = layer(renderer, title, 3.0)
check('drawn', settled is not None and settled[1].width() > 100, True)
check('on the left, low (lower third)', settled is not None and settled[0].left() < 0.2 and settled[0].top() > 0.5, True)
without_role = Segment(dict(title, title_slots={'logo': logo}, title_slot_translations={}))
plain_layer = layer(renderer, without_role, 3.0)
check('without a role: less tall', plain_layer is not None and settled is not None and
      plain_layer[0].height() < settled[0].height(), True)
early = renderer._job(title, 1.08, *renderer._style())[0]
check('animated: another layer early on', early != key, True)
def drawn(found):
    """How much of a layer is there: its alpha, summed (every other pixel)."""
    if found is None:
        return 0
    image = found[1]
    return sum(image.pixelColor(x, y).alpha() for y in range(0, image.height(), 2) for x in range(0, image.width(), 2))
intro = layer(renderer, title, 1.04)
check('early: little of it drawn yet', drawn(intro) < drawn(settled) // 2, True)
outro = layer(renderer, title, 4.9)
check('near the end: sliding away', drawn(outro) < drawn(settled), True)
renderer.close()

print('the editor\'s slots panel')
from subtitld.interface.title_slots_panel import TitleSlotsPanel
session.SUBTITLE = Subtitle(segments=[title])
session.SUBTITLE.selected = session.SUBTITLE.segments[0]
selected = session.SUBTITLE.selected
panel = TitleSlotsPanel(lambda: session.SUBTITLE.selected)
changes = []
panel.changed.connect(lambda: changes.append(1))
panel.refresh()
check('a row a slot shown', [row.slot['key'] for row in panel.rows], ['role', 'logo'])
check('the role in its field', panel.rows[0].field.text(), 'Field reporter')
panel.rows[0].field.textEdited.emit('Anchor')
check('typing sets the slot', selected['title_slots']['role'], 'Anchor')
check('and says so', len(changes), 1)
check('the remove button on, with a logo', panel.rows[1].remove.isEnabled(), True)
panel.rows[1].remove.click()
check('removed', 'logo' in selected.get('title_slots', {}), False)
check('no translation fields', panel.rows[0].translated, None)
session.CONFIG.setdefault('translation', {}).setdefault('engine_options', {}).update(show_translations=True,
                                                                                       target_language='pt-br')
panel.refresh()
check('with translations shown: a field for it', isinstance(panel.rows[0].translated, QLineEdit), True)
check('holding the translation', panel.rows[0].translated.text(), 'Repórter de campo')
panel.rows[0].translated.textEdited.emit('Âncora')
check('typing there sets the translation', selected['title_slot_translations']['pt-br']['role'], 'Âncora')
title_themes.set_value(selected, 'show_logo', False)
panel.refresh()
check('a hidden slot has no row', [row.slot['key'] for row in panel.rows], ['role'])

print('back to a title the panel showed before')
panel.setVisible(False)                 # the editor hides it for a subtitle or a mark
panel.refresh()
check('shown again, with its rows', (not panel.isHidden(), [row.slot['key'] for row in panel.rows]), (True, ['role']))

print('the settings dialog fits its fields')
from PySide6.QtCore import QDir
from subtitld.interface import theme as interface_theme
from subtitld.interface.title_panel import TitleSettingsDialog
QDir.addSearchPath('graphics', str(session.PATH_SUBTITLD_GRAPHICS))
app.setStyleSheet(interface_theme.stylesheet())
dialog = TitleSettingsDialog(selected)
dialog.show()
loop = QEventLoop()
QTimer.singleShot(200, loop.quit)
loop.exec()
viewport = dialog.scroll.viewport().width()
check('the fields as wide as they ask, inside the scroll area',
      viewport >= dialog.fields_widget.sizeHint().width(), True)
check('no sideways scrolling needed', dialog.fields_widget.width() <= viewport, True)
dialog.reset()
loop = QEventLoop()
QTimer.singleShot(100, loop.quit)
loop.exec()
check('after Reset too', dialog.scroll.viewport().width() >= dialog.fields_widget.sizeHint().width(), True)
dialog.close()

print()
print('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
