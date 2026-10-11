"""The current subtitle, drawn over the video in the preview.

Centred at the bottom of the title-safe area and wrapped to it, in the
font, colour, shadow and background box the preview settings give
(session.CONFIG['videoplayer']). Plain text is drawn as one string; text
with formatting tags (modules.markup) goes through Qt's rich text, so its
italic, bold, underline, strikeout and colours show as they will in a
player.
"""

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QBrush, QColor, QPalette, QPen, QTextDocument, QTextOption

from subtitld.modules import markup


def _draw_box(painter, rect, config):
    padding = config.get('backgroundbox_padding', 10)
    radius = config.get('backgroundbox_border_radius', 5)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(config.get('backgroundbox_color', '#55000000'))))
    box = QRectF(rect).marginsAdded(QMarginsF(padding, padding, padding, padding))
    if radius:
        painter.drawRoundedRect(box, radius, radius)
    else:
        painter.drawRect(box)
    painter.setBrush(Qt.NoBrush)


def _shadow_offset(config):
    return config.get('shadow_x', 2), config.get('shadow_y', 2)


def _paint_plain(painter, area, text, config):
    flags = Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap
    if config.get('backgroundbox_enabled', True):
        _draw_box(painter, painter.boundingRect(area, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, text), config)
    if config.get('shadow_enabled', True):
        shadow_x, shadow_y = _shadow_offset(config)
        painter.setPen(QPen(config.get('shadow_color', '#ff000000')))
        painter.drawText(area - QMarginsF(shadow_x, shadow_y, -shadow_x, -shadow_y), flags, text)
    painter.setPen(QPen(config.get('color', '#ffffffff')))
    painter.drawText(area, flags, text)


_HORIZONTAL = {1: Qt.AlignLeft, 2: Qt.AlignHCenter, 0: Qt.AlignRight}


def _document(painter, area, text, colors):
    document = QTextDocument()
    document.documentLayout().setPaintDevice(painter.device())
    document.setDocumentMargin(0)
    document.setDefaultFont(painter.font())
    option = QTextOption(_HORIZONTAL[markup.alignment(text) % 3])
    option.setWrapMode(QTextOption.WordWrap)
    document.setDefaultTextOption(option)
    document.setTextWidth(area.width())
    # pre-wrap: spaces count as they do in the plain drawing.
    document.setHtml('<div style="white-space:pre-wrap">' + markup.to_html(text, colors=colors) + '</div>')
    return document


def _text_rect(document, area, keypad=2):
    """Where the document's lines are, hugging them: at the bottom of
    `area`, or the top or middle when the text asks ({\\an8})."""
    height = document.size().height()
    if keypad >= 7:
        top = area.top()
    elif keypad >= 4:
        top = area.center().y() - height / 2
    else:
        top = area.bottom() - height
    left = right = None
    block = document.begin()
    while block.isValid():
        layout = block.layout()
        for index in range(layout.lineCount()):
            line = layout.lineAt(index).naturalTextRect()
            left = line.left() if left is None else min(left, line.left())
            right = line.right() if right is None else max(right, line.right())
        block = block.next()
    if left is None:
        return QRectF(area.left(), top, 0, height)
    return QRectF(area.left() + left, top, right - left, height)


def _draw_document(painter, document, origin, color):
    context = QAbstractTextDocumentLayout.PaintContext()
    context.palette.setColor(QPalette.Text, QColor(color))
    painter.save()
    painter.translate(origin)
    document.documentLayout().draw(painter, context)
    painter.restore()


def _paint_rich(painter, area, text, config):
    document = _document(painter, area, text, colors=True)
    rect = _text_rect(document, area, markup.alignment(text))
    if config.get('backgroundbox_enabled', True):
        _draw_box(painter, rect, config)
    origin = QPointF(area.left(), rect.top())
    if config.get('shadow_enabled', True):
        shadow_x, shadow_y = _shadow_offset(config)
        _draw_document(painter, _document(painter, area, text, colors=False), origin + QPointF(shadow_x, shadow_y),
                       config.get('shadow_color', '#ff000000'))
    _draw_document(painter, document, origin, config.get('color', '#ffffffff'))


def paint(painter, area, text, config):
    """Draw `text` at the bottom of `area` (a QRectF) with `painter`, whose
    font is already set, as the preview `config` says."""
    if markup.has_markup(text):
        _paint_rich(painter, area, text, config)
    else:
        _paint_plain(painter, area, text, config)
