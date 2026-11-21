"""Shapes for the timeline from SVG files: the playhead's head and line, as
a look sets them (modules.timeline_style).

An SVG is checked once, when it comes in, and refused unless it is one
Subtitld can draw without reaching anything outside it: no DOCTYPE or
entities, no scripts, foreign objects or images, no reference or url()
but to its own elements, not too big. It is then kept in the user's data
folder under a name made of its content, and the look keeps that name:
moving or deleting the file picked changes nothing, and a theme carries
its shapes by these names.

A shape is drawn into an image once for each size, colour and screen
scale it is drawn at, and that image is what the timeline paints: no
more work while the video plays than a built-in shape. `currentColor` in
an SVG is the colour of what it draws (the playhead's for the playhead).
"""

import hashlib
import re
from xml.etree import ElementTree

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from subtitld.modules import session

MAX_BYTES = 256 * 1024
MAX_ELEMENTS = 5000
_NAME = re.compile(r'[0-9a-f]{12}\.svg')
_FORBIDDEN = {'script', 'foreignobject', 'image', 'iframe', 'audio', 'video', 'handler', 'listener'}
_OUTSIDE_URL = re.compile(r'url\(\s*["\']?(?!#)', re.IGNORECASE)
_IMAGES_KEPT = 64


class ShapeError(ValueError):
    """An SVG the timeline will not draw, and why."""


def folder():
    return session.PATH_SUBTITLD_USER_DATA / 'timeline_shapes'


def _local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def check(data):
    """Raise ShapeError unless `data` (bytes) is an SVG the timeline may draw."""
    if len(data) > MAX_BYTES:
        raise ShapeError(f'larger than {MAX_BYTES // 1024} KB')
    head = data[:4096].lower()
    if b'<!doctype' in head or b'<!entity' in data.lower():
        raise ShapeError('a DOCTYPE or entities')
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ShapeError(f'not XML: {error}') from error
    if _local(root.tag) != 'svg':
        raise ShapeError('not an SVG')
    count = 0
    for element in root.iter():
        count += 1
        if count > MAX_ELEMENTS:
            raise ShapeError(f'more than {MAX_ELEMENTS} elements')
        tag = _local(element.tag)
        if tag in _FORBIDDEN:
            raise ShapeError(f'a <{tag}> element')
        for name, value in element.attrib.items():
            attribute = _local(name)
            if attribute == 'href' and not value.strip().startswith('#'):
                raise ShapeError(f'a link outside it: {value[:60]}')
            if attribute.startswith('on'):
                raise ShapeError(f'an event handler: {attribute}')
            if _OUTSIDE_URL.search(value):
                raise ShapeError('a url() outside it')
        if tag == 'style' and element.text and ('@import' in element.text or _OUTSIDE_URL.search(element.text)):
            raise ShapeError('a style reaching outside it')
    if not QSvgRenderer(QByteArray(data)).isValid():
        raise ShapeError('an SVG Qt cannot draw')


def import_file(path):
    """Check the SVG at `path` and keep a copy: the name the look keeps it
    by. ShapeError if it is refused, OSError if it cannot be read."""
    with open(path, 'rb') as source:
        data = source.read(MAX_BYTES + 1)
    check(data)
    name = hashlib.sha1(data).hexdigest()[:12] + '.svg'
    folder().mkdir(parents=True, exist_ok=True)
    target = folder() / name
    if not target.exists():
        target.write_bytes(data)
    return name


def read(name):
    """A kept shape's SVG, checked again (it is a file anyone could change),
    or None."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    try:
        data = (folder() / name).read_bytes()
        check(data)
    except (OSError, ShapeError):
        return None
    return data


_renderers = {}         # (name, colour) -> QSvgRenderer, or None for a shape that cannot be drawn
_images = {}            # (name, width, height, colour, scale) -> QPixmap
_takes_color = {}       # name -> whether it has currentColor


def _renderer(name, color):
    key = (name, color)
    if key not in _renderers:
        data = read(name)
        if data is not None and color and b'currentColor' in data:
            data = data.replace(b'currentColor', color.encode('ascii'))
        renderer = QSvgRenderer(QByteArray(data)) if data is not None else None
        _renderers[key] = renderer if renderer is not None and renderer.isValid() else None
    return _renderers[key]


def available(name):
    """Whether there is a kept shape by this name the timeline can draw."""
    return _renderer(name, None) is not None


def takes_color(name):
    """Whether the shape is drawn in the colour it is given (currentColor)."""
    if name not in _takes_color:
        data = read(name)
        _takes_color[name] = data is not None and b'currentColor' in data
    return _takes_color[name]


def aspect(name):
    """Its width over its height, 1 when it says neither."""
    renderer = _renderer(name, None)
    if renderer is None:
        return 1.0
    box = renderer.viewBoxF()
    if box.width() > 0 and box.height() > 0:
        return box.width() / box.height()
    size = renderer.defaultSize()
    return size.width() / size.height() if size.width() > 0 and size.height() > 0 else 1.0


def image(name, width, height, color=None, scale=1.0):
    """The shape drawn `width` × `height` px (stretched to them), its
    currentColor `color` ("#rrggbb"), for a screen `scale`; None if it
    cannot be drawn. Made once, then kept."""
    width, height = max(1, int(round(width))), max(1, int(round(height)))
    key = (name, width, height, color, scale)
    if key in _images:
        return _images[key]
    renderer = _renderer(name, color)
    if renderer is None:
        return None
    renderer.setAspectRatioMode(Qt.IgnoreAspectRatio)
    pixmap = QPixmap(int(round(width * scale)), int(round(height * scale)))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    if len(_images) >= _IMAGES_KEPT:
        _images.pop(next(iter(_images)))
    _images[key] = pixmap
    return pixmap


def forget():
    """Draw every shape anew: after a shape's file changed."""
    _renderers.clear()
    _images.clear()
    _takes_color.clear()
