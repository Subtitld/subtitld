"""A theme file checked as Subtitld checks it, for its author (and for an AI
writing one): every problem found, or what Subtitld reads in it; with
--preview, a picture of it drawn with sample text at moments through its
intro, middle and outro, by the same renderer the preview and burn-in use.

    subtitld --check-theme THEME.html [--preview OUT.png] [--text TEXT]
             [--slot KEY=TEXT]... [--set FIELD=VALUE]...

--text is the cue's text (\n for a new line), --slot a title's other text
slot (a list slot's items split by |), --set one of the theme's fields
(JSON where it parses: true, 72, "#ff00ff00"; text otherwise).

The theme is not installed: it is drawn as a project's carried theme is
(modules.themes.carry). A frozen Subtitld has no Python to run a script
with, hence a command of its own.
"""

import sys
import tempfile
from pathlib import Path

SIZE = (1920, 1080)
TILE = (640, 360)
FPS = 25
SAMPLE_SUBTITLE = 'A sample subtitle,\nin two lines of text'
SAMPLE_TITLE = 'Sample title'
SAMPLE_LIST = ['First item', 'Second item', 'Third item']


def summary(theme):
    """What Subtitld reads in a theme, as lines."""
    lines = [f'OK: {theme.id} ({theme.kind}) "{theme.name}" {theme.version or "(no version)"}']
    if theme.timing:
        lines.append(f'  animated: intro {theme.timing["intro"]} ms, outro {theme.timing["outro"]} ms')
    else:
        lines.append('  not animated (no "timing"): drawn once, its animations at their first frame')
    lines.append(f'  slots: {", ".join(f"{s["key"]} ({s["type"]})" for s in theme.slots)}'
                 + ('' if theme.slotted else '  [no data-slot in the markup: the text goes on Subtitld\'s stage, '
                                             'unsliced]'))
    if theme.slotted:
        lines.append(f'  slice: {theme.slice}')
    lines.append(f'  fields: {", ".join(f["key"] for f in theme.fields) or "none"}')
    lines.append(f'  places itself: {"yes" if theme.ignores_position else "no"}')
    lines.append(f'  fallback for ASS/TTML: {"yes" if theme.fallback else "no (burn-in and preview only)"}')
    return lines


def moments(theme, length):
    """(seconds into the cue, label) to draw a cue of `length` seconds at."""
    if not theme.timing:
        return [(length / 2, 'still')]
    intro, outro = theme.timing['intro'] / 1000, theme.timing['outro'] / 1000
    found = [(intro * part, f'intro {int(part * 100)}%') for part in (0, 0.25, 0.5, 0.75)]
    found.append((intro + (length - intro - outro) / 2, 'middle'))
    out = length - outro
    found += [(out + outro * part, f'outro {int(part * 100)}%') for part in (0, 0.5)]
    found.append((length - 1 / FPS, 'last frame'))
    return found


def _sample_image():
    """A small picture for image slots, as a data: URL."""
    import base64
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    image = QImage(160, 160, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#e5a823'))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(8, 8, 144, 144)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(bytes(data)).decode('ascii')


def preview(theme, out, text=None, slot_texts=None, settings=None):
    """Draw `theme` with sample text (or `text`, `slot_texts` {key: text},
    `settings` {field: value}) at moments() into a contact sheet `out` (PNG)."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from subtitld.interface import subtitle_pictures
    from subtitld.modules import config, session, themes
    from subtitld.modules.segment import Segment, Subtitle

    session.CONFIG = config.Config(Path(tempfile.mkdtemp()) / 'check.config')
    session.SUBTITLE = Subtitle(segments=[])
    session.VIDEO = {'framerate': FPS, 'width': SIZE[0], 'height': SIZE[1]}
    themes.carry({theme.path.name: theme.html.encode('utf-8')})
    length = 4.0
    if theme.timing:
        length = max(length, (theme.timing['intro'] + theme.timing['outro']) / 1000 + 1.5)
    if theme.kind == 'title':
        slots = {}
        for slot in theme.slots:
            if slot['key'] == 'main':
                continue
            if slot['type'] == 'image':
                slots[slot['key']] = _sample_image()
            elif slot['type'] == 'list':
                slots[slot['key']] = slot.get('default') or SAMPLE_LIST
            else:
                slots[slot['key']] = slot.get('default') or slot.get('placeholder') or f'Sample {slot["label"].lower()}'
        for key, value in (slot_texts or {}).items():
            slot = theme.slot(key)
            if slot is not None and slot['type'] != 'image':
                slots[key] = value.split('|') if slot['type'] == 'list' else value
        segment = Segment({'kind': 'title', 'start': 0, 'end': length, 'text': text or SAMPLE_TITLE,
                           'title_theme': theme.id, 'title_slots': slots, 'title_settings': dict(settings or {})})
        style_id = None
    else:
        from subtitld.modules import subtitle_styles
        for key, value in (settings or {}).items():
            subtitle_styles.set_value(key, value, theme.id)
        segment = Segment({'start': 0, 'end': length, 'text': text or SAMPLE_SUBTITLE})
        style_id = theme.id
    folder = tempfile.mkdtemp()
    pictures = subtitle_pictures.draw([segment], SIZE, folder, style_id=style_id, fps=FPS)
    shots = moments(theme, length)
    columns = min(4, len(shots))
    rows = (len(shots) + columns - 1) // columns
    sheet = QImage(TILE[0] * columns, TILE[1] * rows, QImage.Format_ARGB32)
    sheet.fill(QColor('#1b1f24'))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    scale = TILE[0] / SIZE[0]
    for index, (at, label) in enumerate(shots):
        x0, y0 = (index % columns) * TILE[0], (index // columns) * TILE[1]
        # A checked backdrop, as a video would be under it.
        for cy in range(0, TILE[1], 40):
            for cx in range(0, TILE[0], 40):
                painter.fillRect(x0 + cx, y0 + cy, 40, 40, QColor('#59606a' if (cx + cy) // 40 % 2 else '#4a5058'))
        picture = next((p for p in pictures if p['start'] - 1e-6 <= at < p['end'] - 1e-6), None)
        if picture is not None:
            image = QImage(picture['path'])
            painter.drawImage(QRect(x0 + round(picture['x'] * scale), y0 + round(picture['y'] * scale),
                                    round(picture['width'] * scale), round(picture['height'] * scale)), image)
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Sans', 11))
        painter.fillRect(x0, y0, 220, 24, QColor(0, 0, 0, 160))
        painter.drawText(QRect(x0 + 8, y0, 212, 24), Qt.AlignVCenter, f'{label}  ({at:.2f} s)')
    painter.end()
    sheet.save(str(out), 'PNG')
    return len(shots)


USAGE = 'subtitld --check-theme THEME.html [--preview OUT.png] [--text TEXT] [--slot KEY=TEXT]... [--set FIELD=VALUE]...'


def _options(args):
    """(the file, out, text, {slot: text}, {field: value}) from the command line; None when it makes no sense."""
    import json
    files, out, text, slot_texts, settings = [], None, None, {}, {}
    args = list(args)
    while args:
        arg = args.pop(0)
        if arg in ('--preview', '--text', '--slot', '--set'):
            if not args:
                return None
            value = args.pop(0)
            if arg == '--preview':
                out = Path(value)
            elif arg == '--text':
                text = value.replace('\\n', '\n')
            else:
                key, equals, rest = value.partition('=')
                if not equals:
                    return None
                if arg == '--slot':
                    slot_texts[key] = rest.replace('\\n', '\n')
                else:
                    try:
                        settings[key] = json.loads(rest)
                    except ValueError:
                        settings[key] = rest
        else:
            files.append(arg)
    return (Path(files[0]), out, text, slot_texts, settings) if len(files) == 1 else None


def main(args):
    from PySide6.QtWidgets import QApplication
    options = _options(args)
    if options is None:
        print(USAGE, file=sys.stderr)
        return 2
    path, out, text, slot_texts, settings = options
    app = QApplication(sys.argv[:1])   # noqa: F841 (the renderer needs one)
    from subtitld.modules import themes
    try:
        theme = themes.load(path)
    except themes.ThemeError as error:
        print(f'REFUSED: {path}')
        for problem in error.problems:
            print(f'  - {problem}')
        return 1
    for line in summary(theme):
        print(line)
    if not themes.is_new_enough(theme):
        print(f'  needs Subtitld {theme.min_subtitld}: this one would not load it')
    if out is not None:
        unknown = [key for key in settings if theme.field(key) is None] + \
            [key for key in slot_texts if theme.slot(key) is None or key == 'main']
        if unknown:
            print(f'  no such field or slot: {", ".join(unknown)}')
        count = preview(theme, out, text, slot_texts, {k: v for k, v in settings.items() if theme.field(k)})
        print(f'preview: {out} ({count} moments)')
    return 0
