"""Box frames: an SVG drawn as a cue's box at any width and height, its parts
pinned to the box's sides.

A frame is an SVG (checked and kept as modules.timeline_svg keeps them)
drawn at 1 unit a pixel. Each element at its top level is a part, laid
out on its own:

    <svg viewBox="0 0 100 40">
      <g data-x="left">…</g>       keeps its width, stays at the left
      <g data-x="right">…</g>      keeps its width, stays at the right
      <g data-x="center">…</g>     keeps its width, stays in the middle
      <g data-x="stretch">…</g>    keeps its distances to both sides: grows
      <g data-x="scale">…</g>      grows and moves in proportion
      data-y: top, bottom, center, stretch, scale, the same up and down
      <g data-hit="start">…</g>    where the mouse grabs the start (or
                                   "end", or "body": moves the cue)
      <g data-slice="8">…</g>      grows without stretching its 8 units at
                                   each side: corners stay round
    </svg>

A part that stretches is stretched whole, the curves of its corners with
it. `data-slice` keeps them: the part is drawn at its own size and laid
out in nine pieces (corners as they are, edges stretched along, the
middle both ways), its insets one number for all four sides, two (top
and bottom, left and right) or four (top, right, bottom, left).

A part says nothing: stretch both ways, the box it is drawn as. A click
region is the part's rectangle (its stroke too), wherever the part is
laid out; a frame without one leaves the grab zone to the look's setting.

`currentColor` is the colour the frame is drawn in (the box's fill for
its state), `speakerColor` the colour of the cue's speaker (the fill when
speaker colours are off): a frame with it draws the speaker's colour
where it likes. When any of a kind's frames does, the theme places the
speaker's colour: the timeline's strip along the top is left off all of
that kind's boxes, whatever their state (places_speaker). A part pinned both ways keeps its size in every box: it is
drawn into an image once (for each colour and screen scale) and copied
after. A part that stretches or scales changes with every box, and is
drawn from the SVG each time.
"""

from xml.etree import ElementTree

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from subtitld.modules import timeline_svg

SVG_NS = 'http://www.w3.org/2000/svg'
ANCHORS = ('left', 'right', 'center', 'stretch', 'scale')
VERTICAL_ANCHORS = {'top': 'left', 'bottom': 'right', 'center': 'center', 'stretch': 'stretch', 'scale': 'scale'}
HITS = ('start', 'end', 'body')
_NOT_PARTS = {'defs', 'style', 'title', 'desc', 'metadata'}
_IMAGES_KEPT = 256

ElementTree.register_namespace('', SVG_NS)
ElementTree.register_namespace('xlink', 'http://www.w3.org/1999/xlink')


class Part:
    __slots__ = ('id', 'x', 'y', 'hit', 'bounds', 'slice', 'paint')

    def __init__(self, part_id, x, y, hit, bounds, slice_=None, paint=frozenset()):
        self.id, self.x, self.y, self.hit, self.bounds, self.slice = part_id, x, y, hit, bounds, slice_
        # The colours it names, of 'current' and 'speaker': it is drawn as transparent as they are.
        self.paint = paint

    @property
    def fixed(self):
        """Whether it is drawn its own size whatever the box's: an image made
        once does for every box."""
        return self.x in ('left', 'right', 'center') and self.y in ('left', 'right', 'center')


class Frame:
    """A frame's parts, and the SVG they are drawn from."""

    def __init__(self, data, width, height, parts):
        self.data, self.width, self.height, self.parts = data, width, height, parts
        self.uses_speaker = b'speakerColor' in data
        self._renderers = {}

    def renderer(self, color, speaker=None):
        speaker = (speaker or color) if self.uses_speaker else None
        key = (color, speaker)
        if key not in self._renderers:
            data = self.data.replace(b'currentColor', color.encode('ascii')) if color else self.data
            if speaker:
                data = data.replace(b'speakerColor', speaker.encode('ascii'))
            self._renderers[key] = QSvgRenderer(QByteArray(data))
        return self._renderers[key]

    @property
    def has_regions(self):
        return any(part.hit for part in self.parts)


def _local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _insets(value):
    """data-slice as (top, right, bottom, left), as CSS writes them; None
    for none or one that is not numbers."""
    if not value:
        return None
    try:
        numbers = [max(0.0, float(n)) for n in value.replace(',', ' ').split()]
    except ValueError:
        return None
    if len(numbers) == 1:
        numbers *= 4
    elif len(numbers) == 2:
        numbers *= 2
    return tuple(numbers) if len(numbers) == 4 else None


def _parse(data):
    """The frame in a kept shape's SVG: its parts given ids to be drawn by,
    their anchors, and where each is in the frame. None if it has none."""
    root = ElementTree.fromstring(data)
    found = []
    for index, element in enumerate(list(root)):
        if not isinstance(element.tag, str) or _local(element.tag) in _NOT_PARTS:
            continue
        part_id = f'subtitld-part-{index}'
        element.set('id', part_id)
        x = element.get('data-x', 'stretch')
        y = VERTICAL_ANCHORS.get(element.get('data-y', 'stretch'), 'stretch')
        hit = element.get('data-hit')
        source = ElementTree.tostring(element)
        paint = frozenset(name for name, word in (('current', b'currentColor'), ('speaker', b'speakerColor'))
                          if word in source)
        found.append((part_id, x if x in ANCHORS else 'stretch', y, hit if hit in HITS else None,
                      _insets(element.get('data-slice')), paint))
    if not found:
        return None
    data = ElementTree.tostring(root)
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        return None
    view = renderer.viewBoxF()
    if view.width() <= 0 or view.height() <= 0:
        return None
    parts = []
    for part_id, x, y, hit, insets, paint in found:
        # Where it is, its own transform and stroke in; from the frame's top left.
        bounds = renderer.boundsOnElement(part_id).translated(-view.x(), -view.y())
        if bounds.width() > 0 and bounds.height() > 0:
            parts.append(Part(part_id, x, y, hit, bounds, insets, paint))
    return Frame(data, view.width(), view.height(), parts) if parts else None


_frames = {}            # kept shape name -> Frame, or None for one that is not a frame
_images = {}            # (name, part, width, height, colour, scale) -> QPixmap


def get(name):
    """The frame in the kept shape `name`, or None."""
    if not name:
        return None
    if name not in _frames:
        data = timeline_svg.read(name)
        try:
            _frames[name] = _parse(data) if data is not None else None
        except ElementTree.ParseError:
            _frames[name] = None
    return _frames[name]


def places_speaker(box):
    """Whether a kind's box look (timeline_style.BoxLook) has a frame, in
    any state, that draws speakerColor."""
    names = {box.frame_name(state) for state in ('normal', 'selected', 'hover', 'locked', 'failed')}
    return any(frame is not None and frame.uses_speaker for frame in map(get, names))


def _place(start, length, frame_length, box_start, box_length, anchor):
    """Where a part `length` long at `start` in a frame `frame_length` long
    goes in a box at `box_start`, `box_length` long."""
    if anchor == 'left':
        return box_start + start, length
    if anchor == 'right':
        return box_start + box_length - (frame_length - start), length
    if anchor == 'center':
        return box_start + box_length / 2.0 - (frame_length / 2.0 - start), length
    if anchor == 'scale':
        ratio = box_length / frame_length
        return box_start + start * ratio, length * ratio
    return box_start + start, length + (box_length - frame_length)


def layout(frame, rect):
    """Each part and the rectangle it is drawn in, for a box in `rect`."""
    placed = []
    for part in frame.parts:
        b = part.bounds
        x, width = _place(b.x(), b.width(), frame.width, rect.x(), rect.width(), part.x)
        y, height = _place(b.y(), b.height(), frame.height, rect.y(), rect.height(), part.y)
        if width > 0 and height > 0:
            placed.append((part, QRectF(x, y, width, height)))
    return placed


def regions(frame, rect):
    """{'start' | 'end' | 'body': [rectangles]} for a box in `rect`."""
    found = {}
    for part, target in layout(frame, rect):
        if part.hit:
            found.setdefault(part.hit, []).append(target)
    return found


def hit(frame, rect, point):
    """'start', 'end' or 'body' at `point` of a box in `rect`, by the
    frame's click regions (start and end first); None outside them."""
    found = regions(frame, rect)
    for what in ('start', 'end', 'body'):
        if any(region.contains(point) for region in found.get(what, ())):
            return what
    return None


def paint(painter, frame, rect, color=None, scale=1.0, name='', speaker=None, opacity=1.0):
    """The frame drawn as a box in `rect`, its currentColor `color`, its
    speakerColor `speaker` ("#rrggbb" both; SVG takes no alpha in them).
    `opacity` is currentColor's: a part naming it is drawn that transparent,
    and so is one naming speakerColor when there is no speaker's colour
    (it is currentColor then)."""
    renderer = frame.renderer(color, speaker)
    colors = (color, speaker if frame.uses_speaker else None)
    base = painter.opacity()
    for part, target in layout(frame, rect):
        faded = 'current' in part.paint or ('speaker' in part.paint and not speaker)
        painter.setOpacity(base * opacity if faded else base)
        if part.slice and not part.fixed:
            # Its top and bottom kept once for the box's height (a lane's,
            # the same for every box), then its sides: three pieces a box.
            image = _stretched(frame, renderer, part, target.height(), colors, scale, name)
            _three(painter, image, part.bounds.width(), part.slice[3], part.slice[1], target)
        elif part.fixed:
            image = _image(frame, renderer, part, target.width(), target.height(), colors, scale, name)
            painter.drawPixmap(target.topLeft(), image)
        else:
            renderer.render(painter, part.id, target)
    painter.setOpacity(base)


def _three(painter, image, width, left, right, target):
    """`image` (a part `width` wide, already as tall as `target`) laid into
    `target` in three pieces: its left and right insets kept, its middle
    stretched. The nine pieces' one row, without their loop: a box drawn
    with them is three calls."""
    left, right = min(left, width / 2.0), min(right, width / 2.0)
    fit = min(1.0, target.width() / (left + right)) if left + right else 1.0
    t_left, t_right = left * fit, right * fit
    ratio = image.devicePixelRatio()
    pixels_high = image.height()
    x, y, w, h = target.x(), target.y(), target.width(), target.height()
    if t_left > 0:
        painter.drawPixmap(QRectF(x, y, t_left, h), image, QRectF(0, 0, left * ratio, pixels_high))
    middle = w - t_left - t_right
    if middle > 0:
        painter.drawPixmap(QRectF(x + t_left, y, middle, h), image,
                           QRectF(left * ratio, 0, (width - left - right) * ratio, pixels_high))
    if t_right > 0:
        painter.drawPixmap(QRectF(x + w - t_right, y, t_right, h), image,
                           QRectF((width - right) * ratio, 0, right * ratio, pixels_high))


def _nine(painter, image, width, height, insets, target):
    """`image` (a part drawn `width` × `height`) laid into `target` in nine
    pieces: its insets kept, the rest stretched. Insets wider than the
    target are shrunk to fit it."""
    top, right, bottom, left = insets
    left, right = min(left, width / 2.0), min(right, width / 2.0)
    top, bottom = min(top, height / 2.0), min(bottom, height / 2.0)
    fit_x = min(1.0, target.width() / (left + right)) if left + right else 1.0
    fit_y = min(1.0, target.height() / (top + bottom)) if top + bottom else 1.0
    t_left, t_right, t_top, t_bottom = left * fit_x, right * fit_x, top * fit_y, bottom * fit_y
    ratio = image.devicePixelRatio()
    source_x = (0.0, left, width - right, width)
    source_y = (0.0, top, height - bottom, height)
    target_x = (target.left(), target.left() + t_left, target.right() - t_right, target.right())
    target_y = (target.top(), target.top() + t_top, target.bottom() - t_bottom, target.bottom())
    for row in range(3):
        for column in range(3):
            into = QRectF(target_x[column], target_y[row], target_x[column + 1] - target_x[column],
                          target_y[row + 1] - target_y[row])
            source = QRectF(source_x[column] * ratio, source_y[row] * ratio,
                            (source_x[column + 1] - source_x[column]) * ratio,
                            (source_y[row + 1] - source_y[row]) * ratio)
            if into.width() > 0 and into.height() > 0 and source.width() > 0 and source.height() > 0:
                painter.drawPixmap(into, image, source)


def _stretched(frame, renderer, part, height, colors, scale, name):
    """A sliced part drawn at its own width and `height` tall: its top and
    bottom insets kept, its middle stretched. Made once a height."""
    key = (name, part.id, 'tall', round(height, 2), colors, scale)
    image = _images.get(key)
    if image is None:
        width = part.bounds.width()
        natural = _image(frame, renderer, part, width, part.bounds.height(), colors, scale, name)
        image = QPixmap(max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        image.setDevicePixelRatio(scale)
        image.fill(Qt.transparent)
        p = QPainter(image)
        top, _right, bottom, _left = part.slice
        _nine(p, natural, width, part.bounds.height(), (top, 0.0, bottom, 0.0), QRectF(0, 0, width, height))
        p.end()
        if len(_images) >= _IMAGES_KEPT:
            _images.pop(next(iter(_images)))
        _images[key] = image
    return image


def _image(frame, renderer, part, width, height, color, scale, name):
    key = (name, part.id, round(width, 2), round(height, 2), color, scale)
    image = _images.get(key)
    if image is None:
        image = QPixmap(max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        image.setDevicePixelRatio(scale)
        image.fill(Qt.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, part.id, QRectF(0, 0, width, height))
        p.end()
        if len(_images) >= _IMAGES_KEPT:
            _images.pop(next(iter(_images)))
        _images[key] = image
    return image


def forget():
    _frames.clear()
    _images.clear()

