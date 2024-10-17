"""Timeline module

"""

import time
from bisect import bisect

from PySide6.QtWidgets import QWidget, QScrollArea, QFrame
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF, QFont, QPixmap, QPainterPath, QLinearGradient, QFontMetrics
from PySide6.QtCore import Qt, QRectF, QPointF, QThread, Signal, QMarginsF, QTimer

from subtitld import timecode

from subtitld.modules import waveform, history, subtitles, quality_check, utils, session
from subtitld.interface import subtitles_panel, player


class TimelineScroll(QScrollArea):
    """Class for timeline scroll area"""
    def __init__(widget, parent=None):
        super().__init__(parent)
        # widget.setWidgetResizable(True)
        widget.setObjectName('timeline_scroll')
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        widget.setMinimumHeight(70)

    def enterEvent(widget, event):
        event.accept()

    def leaveEvent(widget, event):
        event.accept()

    def wheelEvent(widget, event):
        widget.horizontalScrollBar().setValue(widget.horizontalScrollBar().value() + event.angleDelta().y())
        event.accept()
    
    def resizeEvent(widget, event):
        # widget.window().timeline_widget.setFixedHeight(widget.height())
        widget.window().timeline_widget.update_size()
        event.accept()


class ThreadGetWaveform(QThread):
    """Class of qtread to get waveform data"""
    command = Signal(list)
    zoom = False
    audio = False
    duration = False
    width = False
    height = False
    filepath = ''

    def run(self):
        # positive_values, negative_values, _ = waveform.generate_waveform_zoom(zoom=self.zoom, duration=self.duration, waveform=self.audio)
        positive_values, negative_values, _ = waveform.generate_waveform_zoom2(zoom=self.zoom, duration=self.duration, filepath=self.filepath)
        self.command.emit([self.zoom, [positive_values, negative_values]])


class ThreadGetQImages(QThread):
    """Class to generate QThread for QImages"""
    command = Signal(list)
    endcommand = Signal(str)
    values_list = []
    zoom = 100.0
    width = 32767
    border_color = '#ff153450'
    fill_color = '#cc153450'

    def run(self):
        """Function to run QThread"""

        class DrawPixmap(QPixmap):
            """Class to generate pixmaps for timeline"""
            waveform_up = []
            waveform_down = []
            x_offset = 0
            waveformsize = .7
            border_color = '#ff153450'
            fill_color = '#cc153450'

            def paintEvent(self):
                """Function of paintEvent for DrawPixmap"""
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)

                painter.setPen(QPen(QColor(self.border_color), 1, Qt.SolidLine))
                painter.setBrush(QColor(self.fill_color))

                x_position = 0
                polygon = QPolygonF()
                for point in self.waveform_up:
                    polygon.append(QPointF(x_position, (self.height() * .5) + (point * (self.waveformsize * 100))))
                    x_position += 1
                for point in reversed(self.waveform_down):
                    polygon.append(QPointF(x_position, (self.height() * .5) + (point * (self.waveformsize * 100))))
                    x_position -= 1
                painter.drawPolygon(polygon)

                painter.end()

        parser = 0
        while True:
            qpixmap = DrawPixmap(self.width, 124)
            qpixmap.fill(QColor(0, 0, 0, 0))
            qpixmap.waveform_up = self.values_list[0][parser:parser + self.width]
            qpixmap.waveform_down = self.values_list[1][parser:parser + self.width]
            qpixmap.border_color = self.border_color
            qpixmap.fill_color = self.fill_color
            qpixmap.paintEvent()
            self.command.emit([self.zoom, qpixmap.toImage()])                # qpixmap.save('/tmp/teste.png')
            time.sleep(.2)

            parser += self.width
            if parser > len(self.values_list[0]):
                break
        self.endcommand.emit('qthread finished')


class Timeline(QWidget):
    """Class for timeline QWidget"""
    seek = Signal(float)
    

    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.subtitle_height = 50
        widget.subtitle_y = 55
        widget.offset = 0.0
        widget.y_waveform = 0
        widget.h_waveform = 124
        widget.show_limiters = False
        widget.show_tug_of_war = False
        widget.tug_of_war_pressed = False
        widget.is_cursor_pressing = False
        widget.waveformsize = .7
        widget.width_proportion = widget.width() / session.VIDEO.get('duration', 0.01)

    def paintEvent(widget, event):
        """Function for paintEvent of Timeline"""
        painter = QPainter(widget)
        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        painter.setRenderHint(QPainter.Antialiasing)

        # painter.setOpacity(session.CONFIG['mediaplayer_opacity'])

        if session.REPEAT_DURATION_BUFFER:
            rep_rect = QRectF(
                session.REPEAT_DURATION_BUFFER[0][0] * widget.width_proportion,
                0,
                (session.REPEAT_DURATION_BUFFER[0][1] - session.REPEAT_DURATION_BUFFER[0][0]) * widget.width_proportion,
                widget.height()
            )

            grad = QLinearGradient(0, 0, 0, 100)
            c1 = QColor(session.CONFIG['timeline'].get('cursor_color', '#ccff0000'))
            c1.setAlpha(80)
            grad.setColorAt(0, c1)
            c2 = QColor(session.CONFIG['timeline'].get('cursor_color', '#ccff0000'))
            c2.setAlpha(0)
            grad.setColorAt(1, c2)

            painter.fillRect(rep_rect, grad)

        if session.CONFIG['timeline'].get('view_mode', 'verticalform') and session.VIDEO.get('waveform', {}):
            if session.CONFIG['timeline'].get('view_mode', 'verticalform') in ['waveform', 'verticalform']:
                if session.VIDEO.get('waveform', {}):
                    x_factor = 1
                    available_zoom = session.CONFIG['timeline_zoom']
                    if available_zoom not in session.VIDEO['waveform'].keys():
                        available_zoom = sorted(session.VIDEO['waveform'].keys())[0]
                        x_factor = session.CONFIG['timeline_zoom'] / available_zoom

                    w_factor = (session.VIDEO.get('duration', 0.01) * available_zoom) / len(session.VIDEO['waveform'][available_zoom]['points'][0])

                    if session.VIDEO['waveform'][available_zoom].get('qimages', []):
                        xpos = 0
                        for qimage in session.VIDEO['waveform'][available_zoom]['qimages']:
                            wid = qimage.width() * w_factor * x_factor
                            if not xpos > scroll_position + scroll_width and not xpos + wid < scroll_position:
                                painter.drawImage(QRectF(xpos, widget.y_waveform, wid, widget.h_waveform), qimage)
                            xpos += wid

                    elif session.VIDEO['waveform'][available_zoom].get('points', []):
                        painter.setPen(QPen(QColor(session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')), 1, Qt.SolidLine))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')))

                        x_position = 0
                        polygon = QPolygonF()

                        for point in session.VIDEO['waveform'][available_zoom]['points'][0][int(scroll_position / (x_factor * w_factor)):int((scroll_position + scroll_width) / (x_factor * w_factor))]:
                            polygon.append(QPointF((x_position + scroll_position), widget.y_waveform + (widget.h_waveform * .5) + (point * (widget.waveformsize * 100))))
                            x_position += (x_factor * w_factor)

                        for point in reversed(session.VIDEO['waveform'][available_zoom]['points'][1][int(scroll_position / (x_factor * w_factor)):int((scroll_position + scroll_width) / (x_factor * w_factor))]):
                            polygon.append(QPointF((x_position + scroll_position), widget.y_waveform + (widget.h_waveform * .5) + (point * (widget.waveformsize * 100))))
                            x_position -= (x_factor * w_factor)

                        painter.drawPolygon(polygon)

        if session.SUBTITLE['segments']:
            painter.setOpacity(1)
            # painter.setPen(QPen(QColor.fromRgb(240, 240, 240, 200), 1, Qt.SolidLine))

            for subtitle in session.SUBTITLE['segments']:
                if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_width) / widget.width()):
                    break
                elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.width()):
                    continue
                else:
                    if session.SUBTITLE['selected'] == subtitle:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_border_color', '#ff304251')))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
                    else:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_border_color', '#ff6a7483')))
                        painter.setBrush(QColor(session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))

                    subtitle_rect = QRectF(
                        subtitle['start'] * widget.width_proportion,
                        widget.subtitle_y,
                        (subtitle['end'] - subtitle['start']) * widget.width_proportion,
                        widget.subtitle_height
                    )

                    painter.drawRoundedRect(subtitle_rect, 2.0, 2.0, Qt.AbsoluteSize)

                    approved, _, _ = quality_check.check_subtitle(subtitle, session.CONFIG['quality_check'])
                    if session.CONFIG['quality_check'].get('enabled', False) and not approved:
                        painter.setPen(QColor('#9e1a1a'))
                    elif session.SUBTITLE['selected'] == subtitle:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_text_color', '#ffffffff')))
                    else:
                        painter.setPen(QColor(session.CONFIG['timeline'].get('subtitle_text_color', '#ff304251')))

                    subtitle_rect -= QMarginsF(22, 2, 22, 2)
                    painter.drawText(subtitle_rect, Qt.AlignCenter | Qt.TextWordWrap, subtitle['text'])

                    if widget.show_limiters and ((subtitle['end'] - subtitle['start']) * widget.width_proportion) > 40:
                        if session.SUBTITLE['selected'] == subtitle:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))

                        painter.setPen(Qt.NoPen)
                        lim_rect = QRectF(
                            (subtitle['start'] * widget.width_proportion) + 2,
                            widget.subtitle_y + 2,
                            18,
                            widget.subtitle_height - 4
                        )

                        painter.drawRoundedRect(lim_rect, 1.0, 1.0, Qt.AbsoluteSize)

                        lx = 1
                        for _ in range(2):
                            if session.SUBTITLE['selected'] == subtitle:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 2)
                            else:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')), 2)

                            painter.setPen(lpen)
                            painter.setBrush(Qt.NoBrush)
                            path = QPainterPath()
                            path.moveTo(lim_rect.center().x() + 2 + lx, lim_rect.center().y() - 10)
                            path.lineTo(lim_rect.center().x() - 1 + lx, lim_rect.center().y())
                            path.lineTo(lim_rect.center().x() + 2 + lx, lim_rect.center().y() + 10)
                            painter.drawPath(path)
                            lx -= 1

                        if session.SUBTITLE['selected'] == subtitle:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('selected_subtitle_fill_color', '#cc3e5363')))
                        else:
                            painter.setBrush(QColor(session.CONFIG['timeline'].get('subtitle_fill_color', '#ccb8cee0')))

                        painter.setPen(Qt.NoPen)
                        lim_rect = QRectF(
                            (subtitle['start'] * widget.width_proportion) + ((subtitle['end'] - subtitle['start']) * widget.width_proportion) - 20,
                            widget.subtitle_y + 2,
                            18,
                            widget.subtitle_height - 4
                        )

                        painter.drawRoundedRect(lim_rect, 1.0, 1.0, Qt.AbsoluteSize)

                        lx = 1
                        for _ in range(2):
                            if session.SUBTITLE['selected'] == subtitle:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 2)
                            else:
                                lpen = QPen(QColor('#07000000') if lx % 2 else QColor(session.CONFIG['timeline'].get('subtitle_arrow_color', '#ff969696')), 2)

                            painter.setPen(lpen)
                            painter.setBrush(Qt.NoBrush)
                            path = QPainterPath()
                            path.moveTo(lim_rect.center().x() + lx, lim_rect.center().y() - 10)
                            path.lineTo(lim_rect.center().x() + 3 + lx, lim_rect.center().y())
                            path.lineTo(lim_rect.center().x() + lx, lim_rect.center().y() + 10)
                            painter.drawPath(path)
                            lx -= 1

            painter.setOpacity(1)

        grid_pen = QPen(QColor(session.CONFIG['timeline'].get('grid_color', '#336a7483')), 1, Qt.SolidLine)
        painter.setFont(QFont('Ubuntu Mono', 8))
        xpos = 0
        for sec in range(int(session.VIDEO.get('duration', 60))):
            if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                if (session.CONFIG['timeline_zoom'] > 75) or (session.CONFIG['timeline_zoom'] > 50 and session.CONFIG['timeline_zoom'] <= 75 and not int((sec % 2))) or (session.CONFIG['timeline_zoom'] > 25 and session.CONFIG['timeline_zoom'] <= 50 and not int((sec % 4))) or (session.CONFIG['timeline_zoom'] <= 25 and not int((sec % 8))):
                    lim_rect = QRectF(
                        xpos + 3,
                        27,
                        50,
                        20
                    )
                    painter.setPen(QColor(session.CONFIG['timeline'].get('time_text_color', '#806a7483')))
                    painter.drawText(lim_rect, Qt.AlignLeft, utils.get_timeline_time_str(sec))
                if session.CONFIG['timeline'].get('show_grid', False) and session.CONFIG['timeline'].get('grid_type', False) == 'seconds':
                    painter.setPen(grid_pen)
                    painter.drawLine(xpos, 0, xpos, widget.height())
            xpos += widget.width_proportion
        if session.CONFIG['timeline'].get('show_grid', False) and session.CONFIG['timeline'].get('grid_type', False) == 'frames':
            painter.setPen(grid_pen)
            xpos = 0.0
            for _ in range(int(session.VIDEO.get('duration', 60) * session.VIDEO['framerate'])):
                if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                    painter.drawLine(xpos, 0, xpos, widget.height())
                xpos += widget.width_proportion / session.VIDEO['framerate']
        elif session.CONFIG['timeline'].get('show_grid', False) and session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and session.VIDEO['scenes']:
            painter.setPen(grid_pen)
            for scene in session.VIDEO['scenes']:
                xpos = (scene * widget.width_proportion)
                if xpos >= scroll_position and xpos <= (scroll_position + widget.parent().parent().width()):
                    painter.drawLine(xpos, 0, xpos, widget.height())

        if bool(widget.show_tug_of_war):
            tug_of_war_pen = QPen(QColor(session.CONFIG['timeline'].get('selected_subtitle_arrow_color', '#ff969696')), 4, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(tug_of_war_pen)
            xpos = int(widget.show_tug_of_war * widget.width_proportion) - 4
            y_tug_pos = 0
            for _ in range(6):
                # print(xpos)
                # print(widget.subtitle_y)
                # print(y_tug_pos)
                # print(int(widget.subtitle_y + 8 + y_tug_pos, xpos + 8))
                # print(int(widget.subtitle_y + 8 + y_tug_pos))
                painter.drawLine(xpos, int(widget.subtitle_y + 8 + y_tug_pos), int(xpos + 8), int(widget.subtitle_y + 8 + y_tug_pos))
                # painter.drawLine(xpos, widget.subtitle_y + 8 + y_tug_pos, xpos + 8, widget.subtitle_y + widget.subtitle_height - 8)
                y_tug_pos += (widget.subtitle_height - 8) / 6

        if session.SUBTITLE.get('position', 0) is not None:
            painter.setPen(QPen(QColor(session.CONFIG['timeline'].get('cursor_color', '#ccff0000')), 2, Qt.SolidLine))
            cursor_pos = int(session.SUBTITLE.get('position', 0) * widget.width_proportion)
            painter.drawLine(cursor_pos, 0, cursor_pos, widget.height())

            if (session.REPEAT_DURATION_BUFFER and session.SUBTITLE.get('position', 0) > session.REPEAT_DURATION_BUFFER[0][0]) or (not session.CONFIG['playback_speed'] == 1.0):
                cfont = QFont('Ubuntu Mono', 10)
                cfont.setBold(True)

                text = ''
                if session.CONFIG['repeat_activated']:
                    text += '⤺{}'.format(len(session.REPEAT_DURATION_BUFFER))

                if not session.CONFIG['playback_speed'] == 1.0:
                    text += (' ' if text else '') + 'x{}'.format(session.CONFIG['playback_speed'])

                cfont_metr = QFontMetrics(cfont).horizontalAdvance(text)

                c_ind_color = QColor(session.CONFIG['timeline'].get('cursor_color', '#ccff0000'))
                c_ind_color.setAlpha(150)
                painter.setBrush(c_ind_color)
                painter.setPen(Qt.NoPen)

                path = QPainterPath()
                path.moveTo(cursor_pos, 25)
                path.lineTo(cursor_pos - cfont_metr - 12, 25)
                path.lineTo(cursor_pos - cfont_metr - 7, 47)
                path.lineTo(cursor_pos, 47)
                path.lineTo(cursor_pos, 25)
                painter.drawPath(path)

                painter.setFont(cfont)
                painter.setPen(QPen(QColor(session.CONFIG['timeline'].get('cursor_text_indicator', '#ffffffff'))))
                text_rect = path.boundingRect() + QMarginsF(5, 0, 5, 0)
                painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)

        painter.end()
        event.accept()

    def mousePressEvent(widget, event):
        """Function to call when mouse is pressed"""
        scroll_position = widget.parent().parent().horizontalScrollBar().value()
        scroll_width = widget.parent().parent().width()

        cursor_is_out_of_view = bool(session.SUBTITLE.get('position', 0) * widget.width_proportion < widget.parent().parent().horizontalScrollBar().value() or session.SUBTITLE.get('position', 0) * widget.width_proportion > widget.parent().parent().width() + widget.parent().parent().horizontalScrollBar().value())

        widget.is_cursor_pressing = True
        session.SUBTITLE['selected'] = False

        for subtitle in session.SUBTITLE['segments']:
            if (subtitle['start'] / session.VIDEO.get('duration', 0.01)) > ((scroll_position + scroll_width) / widget.width()):
                break
            elif (subtitle['end']) / session.VIDEO.get('duration', 0.01) < (scroll_position / widget.width()):
                continue
            else:
                if event.pos().y() > widget.subtitle_y and event.pos().y() < (widget.subtitle_height + widget.subtitle_y) and (((event.pos().x()) / widget.width_proportion) > subtitle['start'] and ((event.pos().x()) / widget.width_proportion) < (subtitle['end'])):
                    session.SUBTITLE['selected'] = subtitle
                    if event.pos().x() / widget.width_proportion > (subtitle['end']) - (20 / widget.width_proportion):
                        widget.subtitle_end_is_clicked = True
                        widget.offset = ((session.SUBTITLE['selected']['end']) * widget.width_proportion) - event.pos().x()
                        widget.tug_of_war_pressed = widget.show_tug_of_war
                    else:
                        widget.offset = event.pos().x() - session.SUBTITLE['selected']['start'] * widget.width_proportion
                        if event.pos().x() / widget.width_proportion < subtitle['start'] + (20 / widget.width_proportion):
                            widget.subtitle_start_is_clicked = True
                            widget.tug_of_war_pressed = widget.show_tug_of_war
                        else:
                            widget.subtitle_is_clicked = True
                    break

        if not (widget.subtitle_end_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_is_clicked):# or cursor_is_out_of_view:
            # session.SUBTITLE.get('position', 0) = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            if session.CONFIG['repeat_activated']:
                session.REPEAT_DURATION_BUFFER = []
            widget.seek.emit(session.SUBTITLE.get('position', 0))
            # update_timecode_label(widget.parent)

        if (widget.subtitle_is_clicked or widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked):
            history.history_append(session.SUBTITLE['segments'])
            session.CONFIG['unsaved'] = True

        widget.update()

    def mouseReleaseEvent(widget, event):
        """Function to call when mouse press is released"""
        widget.subtitle_is_clicked = False
        widget.subtitle_start_is_clicked = False
        widget.subtitle_end_is_clicked = False
        widget.is_cursor_pressing = False
        widget.tug_of_war_pressed = False
        widget.update()
        subtitles_panel.update_subtitles_panel_widget_vision_content(widget.window())
        event.accept()

    def mouseMoveEvent(widget, event):
        """Function to call when mouse moves"""
        # scroll_position = widget.parent().parent().horizontalScrollBar().value()
        # scroll_width = widget.parent().parent().width()

        widget.show_limiters = bool(event.pos().y() > widget.subtitle_y and event.pos().y() < (widget.subtitle_height + widget.subtitle_y))

        for subtitle in session.SUBTITLE['segments']:
            last = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) - 1] if session.SUBTITLE['segments'].index(subtitle) > 0 else {'start':0, 'end':0, 'text':''}
            nextsub = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(subtitle) + 1] if session.SUBTITLE['segments'].index(subtitle) < len(session.SUBTITLE['segments']) - 1 else {'start':session.VIDEO.get('duration', 60), 'end':0, 'text':''}

            if subtitle['end'] > event.pos().x() / widget.width_proportion > (subtitle['end']) - (4 / widget.width_proportion) and nextsub['start'] < (subtitle['end'] + .02):
                widget.show_tug_of_war = subtitle['end'] + .0005
                break
            elif subtitle['start'] < event.pos().x() / widget.width_proportion < subtitle['start'] + (4 / widget.width_proportion) and last['end'] > subtitle['start'] - .02:
                widget.show_tug_of_war = subtitle['start'] - .0005
                break
            elif not widget.tug_of_war_pressed:
                widget.show_tug_of_war = False

        if session.SUBTITLE['selected']:
            i = session.SUBTITLE['segments'].index(session.SUBTITLE['selected'])
            last = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) - 1] if session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) > 0 else {'start': 0, 'end': 0, 'text': ''}
            nextsub = session.SUBTITLE['segments'][session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) + 1] if session.SUBTITLE['segments'].index(session.SUBTITLE['selected']) < len(session.SUBTITLE['segments']) - 1 else {'start': session.VIDEO.get('duration', 60), 'end': 0, 'text': ''}
            scenes_list = session.VIDEO['scenes'] if len(session.VIDEO['scenes']) > 1 else [0.0]
            scenes_list.append(session.VIDEO.get('duration', 60))
            start_position = (event.pos().x() - widget.offset) / widget.width_proportion
            last_scene = scenes_list[bisect(scenes_list, start_position) - 1]
            next_scene = scenes_list[bisect(scenes_list, start_position)]

            if widget.subtitle_start_is_clicked:
                end = session.SUBTITLE['selected']['end']
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
                end_position = (event.pos().x() + widget.offset) / widget.width_proportion
                if not end_position < (session.SUBTITLE['selected']['start'] + session.CONFIG['default_values'].get('minimum_subtitle_width', 1)):
                    if not (bool(session.CONFIG['timeline'].get('snap_move_nereast', False) or widget.tug_of_war_pressed) and round(end_position, 3) >= round(nextsub['start'] - 0.001, 3)) and session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_limits', True) and (nextsub['start'] - session.CONFIG['timeline'].get('snap_value', .1)) < end_position:
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
                if session.CONFIG['timeline'].get('snap', True) and session.CONFIG.get('timeline', {}).get('snap_moving', True) and ((nextsub['start'] - session.CONFIG['timeline'].get('snap_value', .1)) < (start_position + (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])) or (last['end'] + session.CONFIG['timeline'].get('snap_value', .1)) > start_position):
                    if (nextsub['start'] - session.CONFIG['timeline'].get('snap_value', .1)) < (start_position + (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start'])):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=nextsub['start'] - (session.SUBTITLE['selected']['end'] - session.SUBTITLE['selected']['start']) - 0.001)
                    else:
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last['end'] + 0.001)
                elif session.CONFIG['timeline'].get('snap', True) and session.CONFIG['timeline'].get('snap_grid', False):
                    if session.CONFIG['timeline'].get('grid_type', False) == 'frames':
                        difference = start_position % (1.0 / session.VIDEO['framerate'])
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], amount=difference)
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) > float(float(int(start_position) + 1) - float(session.CONFIG['timeline'].get('snap_value', .1))):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position) + 1))
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'seconds' and float(start_position) < float(float(int(start_position)) + float(session.CONFIG['timeline'].get('snap_value', .1))):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=float(int(start_position)))
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position > next_scene - session.CONFIG['timeline'].get('snap_value', .1):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=next_scene)
                    elif session.CONFIG['timeline'].get('grid_type', False) == 'scenes' and start_position < last_scene + session.CONFIG['timeline'].get('snap_value', .1):
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=last_scene)
                    else:
                        subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position)
                else:
                    subtitles.move_subtitle(selected_subtitle=session.SUBTITLE['selected'], absolute_time=start_position)


        if widget.is_cursor_pressing and not (widget.subtitle_start_is_clicked or widget.subtitle_end_is_clicked or widget.subtitle_is_clicked):
            # widget.seek.emit((event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60))
            session.SUBTITLE['position'] = (event.pos().x() / widget.width()) * session.VIDEO.get('duration', 60)
            # if session.CONFIG['repeat_activated']:
            #     session.REPEAT_DURATION_BUFFER = []
            widget.seek.emit(session.SUBTITLE.get('position', 0))
            # update_timecode_label(widget.parent)
        widget.update()

    def mouseDoubleClickEvent(widget, event):
        """Function to call when mouse double clicks"""
        widget.update()
        event.accept()

    def resizeEvent(widget, event):
        """Function to call when timeline is resized"""
        widget.width_proportion = widget.width() / session.VIDEO.get('duration', 0.01)
        widget.subtitle_height = widget.height() - 80
        event.accept()

    def update_size(widget):
        widget.setGeometry(0, 0, int(session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom']), widget.parent().parent().height())

def load(self):
    self.timeline_scroll = TimelineScroll()

    self.timeline_widget = Timeline()
    self.timeline_widget.setObjectName('timeline_widget')
    self.timeline_widget.setMouseTracking(True)
    self.timeline_widget.seek.connect(lambda pos: self.player_widget.seek(pos))
    self.player_widget.position_changed_signal.connect(lambda: self.timeline_widget.update())

    self.timeline_scroll.setWidget(self.timeline_widget)

    def thread_get_waveform_ended(command):
        session.VIDEO['waveform'][command[0]] = {'points': command[1], 'qimages': []}
        # self.videoinfo_label.setText('Waveform updated')
        self.timeline_widget.update()
        self.thread_get_qimages.values_list = command[1]
        self.thread_get_qimages.zoom = command[0]
        self.thread_get_qimages.width = self.timeline_scroll.width()
        self.thread_get_qimages.border_color = session.CONFIG['timeline'].get('waveform_border_color', '#ff153450')
        self.thread_get_qimages.fill_color = session.CONFIG['timeline'].get('waveform_fill_color', '#cc153450')
        self.thread_get_qimages.start()
        # self.thread_get_qimages.start(QThread.IdlePriority)

    self.thread_get_waveform = ThreadGetWaveform(self)
    self.thread_get_waveform.command.connect(thread_get_waveform_ended)

    def thread_get_qimages_ended(command):
        session.VIDEO['waveform'][command[0]]['qimages'].append(command[1])
        self.timeline_widget.update()

    # def thread_qimages_endcommand(command):
    #     self.videoinfo_label.setText('Waveform optimized')

    self.thread_get_qimages = ThreadGetQImages(self)
    self.thread_get_qimages.command.connect(thread_get_qimages_ended)
    # self.thread_get_qimages.endcommand.connect(thread_qimages_endcommand)


# def resized(self):
#     """Function to call when timeline is resized"""
#     self.timeline_scroll.setGeometry(0, self.playercontrols_widget_frame.y() + self.playercontrols_widget_frame.height() - 26, self.playercontrols_widget.width(), self.playercontrols_widget.height() - self.playercontrols_widget_frame.y() - self.playercontrols_widget_frame.height() + 26)
#     update_timeline(self)


# def update_timeline(self):
#     """Function to update timeline"""
#     self.timeline_widget.setGeometry(0, -40, int(session.VIDEO.get('duration', 0.01) * session.CONFIG['timeline_zoom']), self.timeline_scroll.height() - 15)


def update_scrollbar(self, position=0):
    """Function to update scrollbar of timeline"""
    current_position_in_timeline_widget = (session.SUBTITLE.get('position', 0) / session.VIDEO.get('duration', 0.01)) * self.timeline_widget.width()
    offset = 0

    if position == 'middle':
        if (self.timeline_widget.width() - (self.timeline_scroll.width() * .5)) > current_position_in_timeline_widget > self.timeline_scroll.width() * .5:
            offset = self.timeline_scroll.width() * .5
    elif isinstance(position, float):
        offset = self.timeline_scroll.width() * position
    elif isinstance(position, int):
        offset = position

    self.timeline_scroll.horizontalScrollBar().setValue(int(current_position_in_timeline_widget - offset))



def update(self):
    """Function to update timeline"""
    if session.CONFIG['repeat_activated']:
        if not session.REPEAT_DURATION_BUFFER:
            session.REPEAT_DURATION_BUFFER = [[session.SUBTITLE.get('position', 0), session.SUBTITLE.get('position', 0) + session.CONFIG['repeat_duration']] for i in range(session.CONFIG['repeat_times'])]
        else:
            last_pos = session.REPEAT_DURATION_BUFFER[0][1]
            if session.SUBTITLE.get('position', 0) > last_pos:
                self.player_widget.set_position(session.REPEAT_DURATION_BUFFER[0][0])
                self.seek.emit(session.SUBTITLE.get('position', 0))
                del session.REPEAT_DURATION_BUFFER[0]
                if not len(session.REPEAT_DURATION_BUFFER):
                    for i in range(session.CONFIG['repeat_times']):
                        session.REPEAT_DURATION_BUFFER.append([last_pos, last_pos + session.CONFIG['repeat_duration']])
    if not self.player_widget.is_paused():
        current_position_in_timeline_widget = (session.SUBTITLE.get('position', 0) * (self.timeline_widget.width() / session.VIDEO.get('duration', 0.01)))
        if session.CONFIG['timeline'].get('scrolling', 'page') == 'follow':
            update_scrollbar(self, position='middle')
        elif session.CONFIG['timeline'].get('scrolling', 'page') == 'page' and current_position_in_timeline_widget > self.timeline_scroll.width() + self.timeline_scroll.horizontalScrollBar().value():
            update_scrollbar(self)
    self.timeline_widget.update()


def zoom_update_waveform(self):
    """Function to update timeline zoom"""
    if not isinstance(session.VIDEO['audio'], bool) and session.CONFIG['timeline_zoom'] not in session.VIDEO['waveform'].keys():
        # self.videoinfo_label.setText('Generating waveform...')
        # self.thread_get_waveform.audio = session.VIDEO['audio']
        self.thread_get_waveform.filepath = session.VIDEO['filepath']
        self.thread_get_waveform.zoom = session.CONFIG['timeline_zoom']
        self.thread_get_waveform.duration = session.VIDEO.get('duration', 0.01)
        self.thread_get_waveform.start(QThread.IdlePriority)
