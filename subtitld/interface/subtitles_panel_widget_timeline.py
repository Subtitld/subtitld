from bisect import bisect
# from multiprocessing.spawn import old_main_modules
from PySide6.QtWidgets import QWidget, QPushButton, QScrollArea, QLineEdit, QTextEdit
from PySide6.QtGui import QColor, QPen, QPainter, QPolygonF, QTransform, QPainterPath, QFont, QFontMetrics
from PySide6.QtCore import QMarginsF, QRectF, Qt, QPointF, Signal

from subtitld.modules import quality_check
from subtitld.modules import history
from subtitld.modules import subtitles
from subtitld.modules import utils
from subtitld.modules import session
from subtitld.interface import subtitles_panel


class subtitles_panel_timeline_widget_timeline(QWidget):
    """Class for timeline QWidget"""
    seek = Signal(float)

    def __init__(widget, self):
        super().__init__()
        widget.w_waveform = 125
        widget.x_waveform = widget.width() - widget.w_waveform
        widget.subtitle_width = widget.width()
        widget.subtitle_x = 0
        widget.height_proportion = 1
        widget.show_limiters = False
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.show_tug_of_war = False
        widget.tug_of_war_pressed = False
        widget.is_cursor_pressing = False
        widget.waveformsize = .7
        widget.offset = 0.0
        session.CONFIG['timeline_zoom'] = 100.0

        widget.show_editing_widgets = False

        widget.starting_time_qlineedit = QLineEdit(widget)
        widget.starting_time_qlineedit.editingFinished.connect(lambda: widget.qlineedit_editing_finished())
        widget.starting_time_qlineedit.setProperty('class', 'subtitles_panel_timeline_widget_qlineedit')

        widget.text_qtextedit = QTextEdit(widget)
        widget.text_qtextedit.setProperty('class', 'subtitles_panel_timeline_widget_qlineedit')
        widget.text_qtextedit.textChanged.connect(lambda: widget.qlineedit_editing_finished())
        widget.text_qtextedit.setAlignment(Qt.AlignCenter)

        widget.ending_time_qlineedit = QLineEdit(widget)
        widget.ending_time_qlineedit.editingFinished.connect(lambda: widget.qlineedit_editing_finished())
        widget.ending_time_qlineedit.setProperty('class', 'subtitles_panel_timeline_widget_qlineedit')

        widget.update_editing_widgets()

    def paintEvent(widget, event):
        """Function for paintEvent of Timeline"""
        painter = QPainter(widget)
        scroll_position = widget.parent().parent().verticalScrollBar().value()
        scroll_height = widget.parent().parent().height()

        painter.setRenderHint(QPainter.Antialiasing)

        waveform_background_qrectF = QRectF(
            widget.width() - widget.w_waveform,
            0,
            widget.w_waveform,
            widget.height()
        )

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(session.CONFIG['timeline'].get('waveform_background_color', '#55153450')))

        painter.drawRect(waveform_background_qrectF)

        transform_rotate_90 = QTransform()
        transform_rotate_90.rotate(90)

        if session.CONFIG['timeline'].get('view_mode', 'verticalform') and session.VIDEO.get('waveform', {}):
            if session.CONFIG['timeline'].get('view_mode', 'verticalform') in ['waveform', 'verticalform']:
                if session.VIDEO.get('waveform', {}):
                    zoom_factor = 1
                    available_zoom = session.CONFIG['timeline_zoom']
                    if available_zoom not in session.VIDEO['waveform'].keys():
                        available_zoom = sorted(session.VIDEO['waveform'].keys())[0]
                        zoom_factor = session.CONFIG['timeline_zoom'] / available_zoom

                    h_factor = (session.VIDEO.get('duration', 0.01) * available_zoom) / len(session.VIDEO['waveform'][available_zoom]['points'][0])

                    if session.VIDEO['waveform'][available_zoom].get('qimages', []):
                        ypos = 0
                        for qimage in session.VIDEO['waveform'][available_zoom]['qimages']:
                            wid = qimage.width() * h_factor * zoom_factor
                            if not ypos > scroll_position + scroll_height and not ypos + wid < scroll_position:
                                painter.drawImage(QRectF(widget.x_waveform, ypos, widget.w_waveform, wid), qimage.transformed(transform_rotate_90))
                            ypos += wid

                    elif session.VIDEO['waveform'][available_zoom].get('points', []):
                        painter.setPen(QPen(QColor(session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')), 1, Qt.SolidLine))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')))

                        x_position = 0
                        polygon = QPolygonF()

                        for point in session.VIDEO['waveform'][available_zoom]['points'][0][int(scroll_position / (zoom_factor * h_factor)):int((scroll_position + scroll_height) / (zoom_factor * h_factor))]:
                            polygon.append(QPointF((x_position + scroll_position), widget.x_waveform + (widget.w_waveform * .5) + (point * (widget.waveformsize * 100))))
                            x_position += (zoom_factor * h_factor)

                        for point in reversed(session.VIDEO['waveform'][available_zoom]['points'][1][int(scroll_position / (zoom_factor * h_factor)):int((scroll_position + scroll_height) / (zoom_factor * h_factor))]):
                            polygon.append(QPointF((x_position + scroll_position), widget.x_waveform + (widget.w_waveform * .5) + (point * (widget.waveformsize * 100))))
                            x_position -= (zoom_factor * h_factor)

                        painter.drawPolygon(polygon)

        if session.SUBTITLE['segments']:
            painter.setOpacity(1)

            for subtitle in session.SUBTITLE['segments']:
                if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_height) / widget.height()):
                    break
                elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.height()):
                    continue
                else:
                    painter.setPen(Qt.NoPen)
                    if session.SUBTITLE['selected'] == subtitle:
                        # painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_border_color', '#ff304251')))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
                    else:
                        # painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_border_color', '#ff6a7483')))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))

                    subtitle_rect = QRectF(
                        widget.subtitle_x,
                        subtitle['start'] * widget.height_proportion,
                        widget.subtitle_width,
                        (subtitle['end'] - subtitle['start']) * widget.height_proportion,
                    )

                    painter.drawRect(subtitle_rect)

                    if not session.SUBTITLE['selected'] == subtitle or (session.SUBTITLE['selected'] == subtitle and not widget.show_editing_widgets):
                        start_time = utils.get_timeline_time_str(subtitle['start'], ms=True)
                        start_time_width = QFontMetrics(QFont('Ubuntu', 8)).horizontalAdvance(start_time)
                        start_time_rect = QRectF(
                            subtitle_rect.left() + 20,
                            subtitle_rect.top(),
                            start_time_width + 20,
                            20
                        )

                        path = QPainterPath()
                        path.moveTo(start_time_rect.right() + 5, subtitle_rect.top())
                        path.lineTo(0, subtitle_rect.top())
                        path.lineTo(0, subtitle_rect.top() + 20)
                        path.lineTo(start_time_rect.right(), subtitle_rect.top() + 20)
                        path.lineTo(start_time_rect.right() + 5, subtitle_rect.top())
                        painter.drawPath(path)

                        painter.setFont(QFont('Ubuntu Mono', 8))
                        painter.setPen(QColor(session.CONFIG['timeline'].get('time_text_color', '#806a7483')))
                        painter.drawText(start_time_rect, Qt.AlignLeft | Qt.AlignVCenter, start_time)

                        approved, _, _ = quality_check.check_subtitle(subtitle, session.CONFIG['quality_check'])
                        if session.CONFIG['quality_check'].get('enabled', False) and not approved:
                            painter.setPen(QColor('#9e1a1a'))
                        elif session.SUBTITLE['selected'] == subtitle:
                            painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_text_color', '#ffffffff')))
                        else:
                            painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_text_color', '#ff304251')))

                        text_rect = subtitle_rect - QMarginsF(22, 2, widget.w_waveform + 2, 2)
                        painter.setFont(QFont('Ubuntu', 10))
                        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap, subtitle['text'])

                        if widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.height_proportion) > 40:
                            painter.setPen(Qt.NoPen)

                            path = QPainterPath()
                            path.moveTo(start_time_rect.right() + 5, subtitle_rect.top())
                            path.lineTo((widget.width() - widget.w_waveform) + 5, subtitle_rect.top())
                            path.lineTo((widget.width() - widget.w_waveform), subtitle_rect.top() + 20)
                            path.lineTo(start_time_rect.right(), subtitle_rect.top() + 20)
                            path.lineTo(start_time_rect.right() + 5, subtitle_rect.top())
                            painter.drawPath(path)

                            ly = 1
                            for _ in range(2):
                                if session.SUBTITLE['selected'] == subtitle:
                                    lpen = QPen(QColor('#07000000') if ly % 2 else QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 2)
                                else:
                                    lpen = QPen(QColor('#07000000') if ly % 2 else QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')), 2)

                                painter.setPen(lpen)
                                painter.setBrush(Qt.NoBrush)
                                path = QPainterPath()
                                path.moveTo(start_time_rect.right() + ((widget.width() - widget.w_waveform - start_time_rect.right()) * .5) - 10, (subtitle_rect.top() + 10) + ly + 1)
                                path.lineTo(start_time_rect.right() + ((widget.width() - widget.w_waveform - start_time_rect.right()) * .5), (subtitle_rect.top() + 10) - (3) + ly + 1)
                                path.lineTo(start_time_rect.right() + ((widget.width() - widget.w_waveform - start_time_rect.right()) * .5) + 10, (subtitle_rect.top() + 10) + ly + 1)
                                painter.drawPath(path)
                                ly -= 1

                        painter.setPen(Qt.NoPen)

                        if session.SUBTITLE['selected'] == subtitle:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))

                        end_time = utils.get_timeline_time_str(subtitle['end'], ms=True)
                        end_time_width = QFontMetrics(QFont('Ubuntu', 8)).horizontalAdvance(end_time)
                        end_time_rect = QRectF(
                            subtitle_rect.left() + 20,
                            subtitle_rect.bottom() - 20,
                            end_time_width + 20,
                            20
                        )

                        path = QPainterPath()
                        path.moveTo(end_time_rect.right(), subtitle_rect.bottom() - 20)
                        path.lineTo(0, subtitle_rect.bottom() - 20)
                        path.lineTo(0, subtitle_rect.bottom())
                        path.lineTo(end_time_rect.right() + 5, subtitle_rect.bottom())
                        path.lineTo(end_time_rect.right(), subtitle_rect.bottom() - 20)
                        painter.drawPath(path)

                        painter.setFont(QFont('Ubuntu Mono', 8))
                        painter.setPen(QColor(session.CONFIG['timeline'].get('time_text_color', '#806a7483')))
                        painter.drawText(end_time_rect, Qt.AlignLeft | Qt.AlignVCenter, end_time)

                        approved, _, _ = quality_check.check_subtitle(subtitle, session.CONFIG['quality_check'])
                        if session.CONFIG['quality_check'].get('enabled', False) and not approved:
                            painter.setPen(QColor('#9e1a1a'))
                        elif session.SUBTITLE['selected'] == subtitle:
                            painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_text_color', '#ffffffff')))
                        else:
                            painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_text_color', '#ff304251')))

                        text_rect = subtitle_rect - QMarginsF(22, 2, widget.w_waveform + 2, 2)
                        painter.setFont(QFont('Ubuntu', 10))
                        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap, subtitle['text'])

                        if widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.height_proportion) > 40:
                            painter.setPen(Qt.NoPen)

                            path = QPainterPath()
                            path.moveTo(end_time_rect.right(), subtitle_rect.bottom() - 20)
                            path.lineTo((widget.width() - widget.w_waveform), subtitle_rect.bottom() - 20)
                            path.lineTo((widget.width() - widget.w_waveform) + 5, subtitle_rect.bottom())
                            path.lineTo(end_time_rect.right() + 5, subtitle_rect.bottom())
                            path.lineTo(end_time_rect.right(), subtitle_rect.bottom() - 20)
                            painter.drawPath(path)

                            ly = 0
                            for _ in range(2):
                                if session.SUBTITLE['selected'] == subtitle:
                                    lpen = QPen(QColor('#07000000') if ly % 2 else QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 2)
                                else:
                                    lpen = QPen(QColor('#07000000') if ly % 2 else QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')), 2)

                                painter.setPen(lpen)
                                painter.setBrush(Qt.NoBrush)
                                path = QPainterPath()
                                path.moveTo(end_time_rect.right() + ((widget.width() - widget.w_waveform - end_time_rect.right()) * .5) - 10, (subtitle_rect.bottom() - 20 + 10) + ly - 2)
                                path.lineTo(end_time_rect.right() + ((widget.width() - widget.w_waveform - end_time_rect.right()) * .5), (subtitle_rect.bottom() - 20 + 10) + (3) + ly - 2)
                                path.lineTo(end_time_rect.right() + ((widget.width() - widget.w_waveform - end_time_rect.right()) * .5) + 10, (subtitle_rect.bottom() - 20 + 10) + ly - 2)
                                painter.drawPath(path)
                                ly += 1

                    #     if session.SUBTITLE['selected'] == subtitle:
                    #         painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')))
                    #     else:
                    #         painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')))

                    #     painter.drawText(lim_rect, Qt.AlignCenter, '︿')

                    #     painter.setPen(Qt.NoPen)
                    #     lim_rect = QRectF(
                    #         widget.subtitle_x + 2,
                    #         (subtitle[0] * widget.height_proportion) + (subtitle[1] * widget.height_proportion) - 20,
                    #         widget.subtitle_width - 4,
                    #         18
                    #     )

                    #     painter.drawRect(lim_rect)

                    #     if session.SUBTITLE['selected'] == subtitle:
                    #         painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')))
                    #     else:
                    #         painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')))

                    #     painter.drawText(lim_rect, Qt.AlignCenter, '﹀')

                    if session.SUBTITLE['selected'] == subtitle:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_border_color', '#ff304251')))
                    else:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_border_color', '#ff6a7483')))

                    painter.drawLine(
                        int(widget.subtitle_x),
                        int(subtitle_rect.top()),
                        int(widget.width()),
                        int(subtitle_rect.top()),
                    )

                    painter.drawLine(
                        int(widget.subtitle_x),
                        int(subtitle_rect.bottom()),
                        int(widget.width()),
                        int(subtitle_rect.bottom()),
                    )

            painter.setOpacity(1)

        if bool(widget.show_tug_of_war):
            tug_of_war_pen = QPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 4, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(tug_of_war_pen)
            ypos = int(widget.show_tug_of_war * widget.height_proportion) - 4
            x_tug_pos = 0
            for _ in range(6):
                painter.drawLine(
                    int(widget.subtitle_x + 8 + x_tug_pos),
                    ypos,
                    int(widget.subtitle_x + 8 + x_tug_pos),
                    ypos + 8
                )
                # painter.drawLine(ypos, widget.subtitle_y + 8 + x_tug_pos, ypos + 8, widget.subtitle_y + widget.subtitle_height - 8)
                x_tug_pos += (widget.subtitle_width - widget.w_waveform - 16) / 5

        if session.SUBTITLE.get('position', 0) is not None:
            painter.setPen(QPen(QColor(session.CONFIG['timeline'].get('cursor_color', '#ccff0000')), 2, Qt.SolidLine))
            cursor_pos = int(session.SUBTITLE.get('position', 0) * widget.height_proportion)
            painter.drawLine(0, cursor_pos, widget.width(), cursor_pos)

        painter.end()
        event.accept()

    def mousePressEvent(widget, event):
        """Function to call when mouse is pressed"""
        scroll_position = widget.parent().parent().verticalScrollBar().value()
        scroll_height = widget.parent().parent().height()

        cursor_is_out_of_view = bool(session.SUBTITLE.get('position', 0) * widget.height_proportion < widget.parent().parent().verticalScrollBar().value() or session.SUBTITLE.get('position', 0) * widget.height_proportion > widget.parent().parent().width() + widget.parent().parent().verticalScrollBar().value())

        widget.is_cursor_pressing = True
        session.SUBTITLE['selected'] = False

        for subtitle in session.SUBTITLE['segments']:
            if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_height) / widget.height()):
                break
            elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.height()):
                continue
            else:
                if (widget.subtitle_x < event.pos().x() < widget.width() - widget.w_waveform) and (((event.pos().y()) / widget.height_proportion) > subtitle['start'] and ((event.pos().y()) / widget.height_proportion) < (subtitle['end'])):
                    session.SUBTITLE['selected'] = subtitle
                    if event.pos().y() / widget.height_proportion > (subtitle['end']) - (20 / widget.height_proportion):
                        widget.subtitle_end_is_clicked = True
                        widget.offset = ((session.SUBTITLE['selected']['end']) * widget.height_proportion) - event.pos().y()
                        widget.tug_of_war_pressed = widget.show_tug_of_war
                    else:
                        widget.offset = event.pos().y() - session.SUBTITLE['selected']['start'] * widget.height_proportion
                        if event.pos().y() / widget.height_proportion < subtitle['start'] + (20 / widget.height_proportion):
                            widget.subtitle_start_is_clicked = True
                            widget.tug_of_war_pressed = widget.show_tug_of_war
                        else:
                            widget.subtitle_is_clicked = True
                    break

        if not (widget.subtitle_end_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_is_clicked) or cursor_is_out_of_view:
            session.SUBTITLE['position'] = int((event.pos().y() / widget.height()) * session.VIDEO.get('duration', 0.01))
            widget.seek.emit(session.SUBTITLE.get('position', 0))

        if (widget.subtitle_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked):
            history.history_append(session.SUBTITLE['segments'])

        widget.update()

    def mouseReleaseEvent(widget, event):
        """Function to call when mouse press is released"""
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.is_cursor_pressing = False
        widget.tug_of_war_pressed = False
        widget.update()
        event.accept()

    def mouseMoveEvent(widget, event):
        widget.show_limiters = bool(event.pos().x() > widget.subtitle_x and event.pos().x() < (widget.subtitle_width - widget.w_waveform))

        for subtitle in session.SUBTITLE['segments']:
            last = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) - 1] if session.SUBTITLE['segments'].index(subtitle) > 0 else [0, 0, '']
            nextsub = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) + 1] if session.SUBTITLE['segments'].index(subtitle) < len(session.SUBTITLE['segments']) - 1 else [session.VIDEO['duration'], 0, '']

            if subtitle['end'] > event.pos().y() / widget.height_proportion > (subtitle['end']) - (4 / widget.height_proportion) and nextsub['start'] < (subtitle['end'] + .02) and widget.show_limiters:
                widget.show_tug_of_war = subtitle['end'] + .0005
                break
            elif subtitle['start'] < event.pos().y() / widget.height_proportion < subtitle['start'] + (4 / widget.height_proportion) and last['end'] > subtitle['start'] - .02 and widget.show_limiters:
                widget.show_tug_of_war = subtitle['start'] - .0005
                break
            elif not widget.tug_of_war_pressed:
                widget.show_tug_of_war = False

        if session.SUBTITLE['selected']:
            i = session.SUBTITLE['segments'].index(session.SUBTITLE['selected'])
            last = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) - 1] if session.SUBTITLE['segments'].index(subtitle) > 0 else {'start':0, 'end':0, 'text':''}
            nextsub = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) + 1] if session.SUBTITLE['segments'].index(subtitle) < len(session.SUBTITLE['segments']) - 1 else {'start':session.VIDEO['duration'], 'end':0, 'text':''}
            scenes_list = session.VIDEO['scenes'] if len(session.VIDEO['scenes']) > 1 else [0.0]
            scenes_list.append(session.VIDEO['duration'])
            start_position = (event.pos().y() - widget.offset) / widget.height_proportion
            last_scene = scenes_list[bisect(scenes_list, start_position) - 1]
            next_scene = scenes_list[bisect(scenes_list, start_position)]

            if widget.subtitle_start_is_clicked:
                end = session.SUBTITLE['segments'][i]['end']
                if not start_position > (end - session.CONFIG['default_values'].get('minimum_subtitle_width', 1)):
                    if not (bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed) and round(last['end'] + .001, 3) == round(session.SUBTITLE['selected']['start'], 3)) and session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_limits', True) and (last['end'] + session.CONFIG['timeline'].get('snap_value', .1)) > start_position:
                        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last['end'] + 0.001, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    elif session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_grid', False):
                        if session.CONFIG['timeline'].get('grid_type', False) == 'frames':
                            difference = start_position % (1.0 / session.VIDEO['framerate'])
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) > float(float(int(start_position) + 1) - float(session.CONFIG['timeline'].get('snap_value', .1))):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position) + 1), move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) < float(float(int(start_position)) + float(session.CONFIG['timeline'].get('snap_value', .1))):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position)), move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position > next_scene - session.CONFIG['timeline'].get('snap_value', .1):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position < last_scene + session.CONFIG['timeline'].get('snap_value', .1):
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        else:
                            subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    else:
                        subtitles.move_start_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                if widget.tug_of_war_pressed:
                    widget.show_tug_of_war = session.SUBTITLE['selected']['start']
            elif widget.subtitle_end_is_clicked:
                end_position = (event.pos().y() + widget.offset) / widget.height_proportion
                if not end_position < (session.SUBTITLE['segments'][i]['start'] + session.CONFIG['default_values'].get('minimum_subtitle_width', 1)):
                    if not (bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed) and round(end_position, 3) >= round(nextsub['start'] - 0.001, 3)) and session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_limits', True) and (nextsub['start'] - session.CONFIG['timeline'].get('snap_value', .1)) < end_position:
                        # session.SUBTITLE['segments'][i][1] = (nextsub[0] - 0.001) - session.SUBTITLE['segments'][i][0]
                        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=(nextsub['start'] - 0.001), move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    elif session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_grid', False):
                        if session.CONFIG['timeline'].get('grid_type', False) == 'frames':
                            difference = end_position % (1.0 / session.VIDEO['framerate'])
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(end_position) > float(float(int(end_position) + 1) - float(session.CONFIG['timeline'].get('snap_value', .1))):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(end_position) + 1), move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(end_position) < float(float(int(end_position)) + float(session.CONFIG['timeline'].get('snap_value', .1))):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(end_position)), move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and end_position > next_scene - session.CONFIG['timeline'].get('snap_value', .1):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and end_position < last_scene + session.CONFIG['timeline'].get('snap_value', .1):
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                        else:
                            subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=end_position, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                    else:
                        subtitles.move_end_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=end_position, move_nereast=bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed))
                if widget.tug_of_war_pressed:
                    widget.show_tug_of_war = session.SUBTITLE['selected']['end']
            elif widget.subtitle_is_clicked:
                if session.CONFIG['timeline'].get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_moving', True) and ((nextsub[0] - session.CONFIG['timeline'].get('snap_value', .1)) < (start_position + session.SUBTITLE['segments'][i][1]) or (last['end'] + session.CONFIG['timeline'].get('snap_value', .1)) > start_position):
                    if (nextsub['start'] - session.CONFIG['timeline'].get('snap_value', .1)) < (start_position + (session.SUBTITLE['segments'][i]['end'] - session.SUBTITLE['segments'][i]['start'])):
                        session.SUBTITLE['segments'][i]['start'] = nextsub['start'] - (session.SUBTITLE['segments'][i]['end'] - session.SUBTITLE['segments'][i]['start']) - 0.001
                    else:
                        session.SUBTITLE['segments'][i]['start'] = last['end'] + 0.001
                elif session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_grid', False):
                    if session.CONFIG['timeline'].get('grid_type', False) == 'frames':
                        difference = start_position % (1.0 / session.VIDEO['framerate'])
                        session.SUBTITLE['segments'][i]['start'] = start_position - difference
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) > float(float(int(start_position) + 1) - float(session.CONFIG['timeline'].get('snap_value', .1))):
                        session.SUBTITLE['segments'][i]['start'] = float(int(start_position) + 1)
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) < float(float(int(start_position)) + float(session.CONFIG['timeline'].get('snap_value', .1))):
                        session.SUBTITLE['segments'][i]['start'] = float(int(start_position))
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position > next_scene - session.CONFIG['timeline'].get('snap_value', .1):
                        session.SUBTITLE['segments'][i]['start'] = next_scene
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position < last_scene + session.CONFIG['timeline'].get('snap_value', .1):
                        session.SUBTITLE['segments'][i]['start'] = last_scene
                    else:
                        session.SUBTITLE['segments'][i]['start'] = start_position
                else:
                    session.SUBTITLE['segments'][i]['start'] = start_position

        if widget.is_cursor_pressing and not (widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked or widget.subtitle_is_clicked):
            widget.seek.emit((event.pos().y() / widget.height()) * session.VIDEO.get('duration', 0.01))

        widget.update()

    def mouseDoubleClickEvent(widget, event):
        widget.show_editing_widgets = True
        widget.update_editing_widgets()
        event.accept()

    def resizeEvent(widget, event):
        """Function to call when timeline is resized"""
        # widget.height_proportion = widget.width()/session.VIDEO.get('duration', 0.01)
        widget.x_waveform = widget.width() - widget.w_waveform
        widget.subtitle_width = widget.width()
        widget.height_proportion = widget.height() / session.VIDEO.get('duration', 0.01)
        event.accept()

    def leaveEvent(widget, event):
        widget.qlineedit_editing_finished()
        widget.show_editing_widgets = False
        widget.update_editing_widgets()
        event.accept()

    def qlineedit_editing_finished(widget):
        if widget.show_editing_widgets and session.SUBTITLE['selected'] and widget.starting_time_qlineedit.text() and widget.ending_time_qlineedit.text():
            session.SUBTITLE['selected']['start'] = float(widget.starting_time_qlineedit.text())
            session.SUBTITLE['selected']['end'] = float(widget.ending_time_qlineedit.text())
            session.SUBTITLE['selected']['text'] = widget.text_qtextedit.toPlainText()
        widget.update()

    def update_editing_widgets(widget):
        if session.SUBTITLE['selected']:
            start_time = utils.get_timeline_time_str(session.SUBTITLE['selected']['start'], ms=True)
            start_time_width = QFontMetrics(QFont('Ubuntu', 8)).horizontalAdvance(start_time)

            widget.starting_time_qlineedit.setGeometry(
                int(widget.subtitle_x) + 10,
                int(session.SUBTITLE['selected']['start'] * widget.height_proportion) + 3,
                start_time_width + 20,
                15,
            )
            widget.starting_time_qlineedit.setText(str(session.SUBTITLE['selected']['start']))
            widget.starting_time_qlineedit.setVisible(True)

            widget.text_qtextedit.setGeometry(
                int(widget.subtitle_x) + 10,
                int((session.SUBTITLE['selected']['start']) * widget.height_proportion) + 24,
                widget.x_waveform - 20,
                int((session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) * widget.height_proportion) - 46
            )
            widget.text_qtextedit.blockSignals(True)
            widget.text_qtextedit.setText(str(session.SUBTITLE['selected']['text']))
            widget.text_qtextedit.blockSignals(False)

            widget.text_qtextedit.setVisible(True)

            end_time = utils.get_timeline_time_str(session.SUBTITLE['selected']['start'], ms=True)
            end_time_width = QFontMetrics(QFont('Ubuntu', 8)).horizontalAdvance(end_time)

            widget.ending_time_qlineedit.setGeometry(
                int(widget.subtitle_x) + 10,
                int((session.SUBTITLE['selected']['end']) * widget.height_proportion) - 17,
                end_time_width + 20,
                15,
            )
            widget.ending_time_qlineedit.setText(str(session.SUBTITLE['selected']['end']))
            widget.ending_time_qlineedit.setVisible(True)

        widget.starting_time_qlineedit.setVisible(widget.show_editing_widgets)
        widget.text_qtextedit.setVisible(widget.show_editing_widgets)
        widget.ending_time_qlineedit.setVisible(widget.show_editing_widgets)


def add_button(self):
    self.subtitles_panel_widget_button_timeline = QPushButton()
    self.subtitles_panel_widget_button_timeline.setObjectName('subtitles_panel_widget_button_timeline')
    self.subtitles_panel_widget_button_timeline.setProperty('class', 'subtitles_panel_left_button')
    self.subtitles_panel_widget_button_timeline.setCheckable(True)
    self.subtitles_panel_widget_button_timeline.setFixedWidth(23)
    # self.subtitles_panel_widget_button_timeline.icon().addPixmap(self.subtitles_panel_widget_button_timeline.icon(), QIcon.Disable)
    self.subtitles_panel_widget_button_timeline.clicked.connect(lambda vision: subtitles_panel.update_subtitles_panel_widget_vision(self, 'timeline'))
    self.subtitles_panel_widget_buttons_vbox.addWidget(self.subtitles_panel_widget_button_timeline)


def add_widgets(self):
    self.subtitles_panel_timeline_widget = QScrollArea()
    self.subtitles_panel_timeline_widget.setObjectName('subtitles_panel_timeline_widget')
    self.subtitles_panel_timeline_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    self.subtitles_panel_timeline_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    self.subtitles_panel_timeline_widget_timeline = subtitles_panel_timeline_widget_timeline(self)
    self.subtitles_panel_timeline_widget_timeline.setMouseTracking(True)
    self.subtitles_panel_timeline_widget_timeline.seek.connect(lambda position: subtitles_panel_timeline_widget_timeline_seek(self, position))
    # self.subtitles_panel_timeline_widget_timeline.focusout.connect(lambda: subtitles_panel_timeline_widget_timeline_loses_focus(self))
    # self.subtitles_panel_timeline_widget_timeline.doubleclicked.connect(lambda: subtitles_panel_timeline_widget_timeline_doubleclicked(self))
    self.subtitles_panel_timeline_widget.setWidget(self.subtitles_panel_timeline_widget_timeline)

    self.subtitles_panel_stackedwidgets.addWidget(self.subtitles_panel_timeline_widget)


def update_subtitles_panel_timeline(self):
    # print(session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom'])
    self.subtitles_panel_timeline_widget_timeline.update()

    if session.CONFIG['timeline'].get('scrolling', 'page') == 'follow':
        if (session.SUBTITLE.get('position', 0) * (self.subtitles_panel_timeline_widget_timeline.height() / session.VIDEO.get('duration', 0.01))) > self.subtitles_panel_timeline_widget.width() * .5 and (session.SUBTITLE.get('position', 0) * (self.subtitles_panel_timeline_widget_timeline.height() / session.VIDEO.get('duration', 0.01))) < (self.subtitles_panel_timeline_widget_timeline.height() - (self.subtitles_panel_timeline_widget.height() * .5)):
            update_scrollbar(self, position='middle')
    elif session.CONFIG['timeline'].get('scrolling', 'page') == 'page' and (session.SUBTITLE.get('position', 0) * (self.subtitles_panel_timeline_widget_timeline.height() / session.VIDEO.get('duration', 0.01))) > self.subtitles_panel_timeline_widget.height() + self.subtitles_panel_timeline_widget.verticalScrollBar().value():
        update_scrollbar(self)

    # self.subtitles_panel_timeline_widget_timeline.setGeometry(0, 0, self.subtitles_panel_subtitles_panel_timeline_widget_timeline.height(), session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom'])
    # print(self.subtitles_panel_timeline_widget_timeline.height())


def update_scrollbar(self, position=0):
    """Function to update scrollbar of timeline"""
    if position == 'middle':
        offset = self.subtitles_panel_timeline_widget.height() * .5
    elif isinstance(position, float):
        offset = self.subtitles_panel_timeline_widget.height() * position
    elif isinstance(position, int):
        offset = position
    self.subtitles_panel_timeline_widget.verticalScrollBar().setValue(int(session.SUBTITLE.get('position', 0) * (self.subtitles_panel_timeline_widget_timeline.height() / session.VIDEO.get('duration', 0.01)) - offset))


def timeline_resized(self):
    self.subtitles_panel_timeline_widget_timeline.setGeometry(0, 0, self.subtitles_panel_timeline_widget.width(), int(session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom']))


def subtitles_panel_timeline_widget_timeline_seek(self, position):
    self.player_widget.seek(position)
