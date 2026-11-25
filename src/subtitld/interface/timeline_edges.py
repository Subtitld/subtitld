"""A cue box's start and end edges, the ones dragged to retime it, as its
kind's box look has them (modules.timeline_style): a glow, the box's
outline at the edge fading inwards; a bar along the edge; or nothing.

Drawn for a box laid along x, start on the left. The vertical timeline's
boxes run along y, start at the top: `vertical` swaps the axes, the same
shape mirrored onto them.
"""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainterPath, QPen, QTransform

# x and y swapped: a box along x drawn as one along y.
_SWAP = QTransform(0, 1, 1, 0, 0, 0)


def shown(box, hovered):
    """Whether a box's edges are drawn: `hovered` is whether the mouse is on
    (or drags) that edge."""
    if box['edge_style'] == 'none':
        return False
    return hovered or box['edge_show'] == 'always'


def paint(painter, box, rect, side, radius, vertical=False):
    """The `side` ('start' or 'end') edge of the box in `rect`, with corners
    of `radius`."""
    style = box['edge_style']
    if style == 'none' or side not in ('start', 'end'):
        return
    if vertical:
        painter.save()
        painter.setTransform(_SWAP, True)
        rect = QRectF(rect.top(), rect.left(), rect.height(), rect.width())
    thickness = box['edge_handle_thickness']
    color = box.color('edge_start_color' if side == 'start' else 'edge_end_color')
    left, right, top, bottom = rect.left(), rect.right(), rect.top(), rect.bottom()
    r = max(0.0, min(radius, rect.height() / 2.0, rect.width() / 2.0))
    if style == 'bar' and side == 'end' and box['end_slant'] < 90:
        # An end that slants (its frame draws it so): the bar along it, its
        # top at the box's right, leaning back to the bottom.
        width = min(thickness, rect.width() / 2.0)
        run = rect.height() / math.tan(math.radians(box['end_slant']))
        # Inside the frame's slanted side, which starts its curve a little
        # short of the box's right.
        edge = right - 2.5
        bar = QPainterPath()
        bar.moveTo(edge - width, top)
        bar.lineTo(edge, top)
        bar.lineTo(edge - run, bottom)
        bar.lineTo(edge - run - width, bottom)
        bar.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(bar)
    elif style == 'bar':
        shape = QPainterPath()
        shape.addRoundedRect(rect, r, r)
        width = min(thickness, rect.width() / 2.0)
        bar = QPainterPath()
        bar.addRect(QRectF(left if side == 'start' else right - width, top, width, rect.height()))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(shape.intersected(bar))
    else:
        reach = min(float(box['edge_handle_width']), rect.width() / 2.0)
        path = QPainterPath()
        if side == 'end':
            inner = right - reach
            path.moveTo(inner, top)
            path.lineTo(right - r, top)
            path.arcTo(right - 2 * r, top, 2 * r, 2 * r, 90, -90)
            path.lineTo(right, bottom - r)
            path.arcTo(right - 2 * r, bottom - 2 * r, 2 * r, 2 * r, 0, -90)
            path.lineTo(inner, bottom)
            gradient = QLinearGradient(right, 0, inner, 0)
        else:
            inner = left + reach
            path.moveTo(inner, top)
            path.lineTo(left + r, top)
            path.arcTo(left, top, 2 * r, 2 * r, 90, 90)
            path.lineTo(left, bottom - r)
            path.arcTo(left, bottom - 2 * r, 2 * r, 2 * r, 180, 90)
            path.lineTo(inner, bottom)
            gradient = QLinearGradient(left, 0, inner, 0)
        gradient.setColorAt(0, color)
        faded = QColor(color)
        faded.setAlpha(0)
        gradient.setColorAt(1, faded)
        painter.setPen(QPen(QBrush(gradient), thickness))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
    if vertical:
        painter.restore()
