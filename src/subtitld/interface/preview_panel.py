import os

from PySide6.QtWidgets import QVBoxLayout, QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView, QFrame, QGraphicsOpacityEffect, QSizePolicy, QRubberBand
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QRect, QRectF, QPoint, QSize, QMargins, QMarginsF, Signal, QTimer, QEvent
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QMediaMetaData
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from subtitld.interface import utils
from subtitld.interface.translation import _

from subtitld.modules import session
from subtitld.modules import subtitles
from subtitld.modules import audioengine #AudaspaceAudioDevice
    

class PlayerWidget(QWidget):
    position_changed_signal = Signal()
    def __init__(widget, parent=None):
        super().__init__(parent)
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(0, 0, 0, 0)

        widget._media_player = QMediaPlayer()
        
        widget._audio_output = QAudioOutput()
        widget._media_player.setAudioOutput(widget._audio_output)
        # widget._media_player.setAudioOutput(QAudioOutput())
        
        widget._audio_device = audioengine.SoundDeviceAudioEngine()

        # Bridge USFX Phase 2 per-member completion to the engine: when a
        # deferred dub WAV lands on disk, ask the engine to preload it
        # right away instead of waiting for an audio-callback miss.
        # The signal is emitted from the extractor worker thread, but
        # `subtitle_clips` is main-thread-only — so we explicitly request
        # a QueuedConnection. Auto-promote relies on the receiver being
        # a QObject; a plain closure has no affinity, so we say it out
        # loud. Because `connect()` runs here on the main thread,
        # QueuedConnection dispatches the slot through the main event
        # loop, which is what we want.
        try:
            from subtitld.modules.signals import SIGNALS as _SESSION_SIGNALS

            def _on_member_ready(arcname, target_path):
                if hasattr(widget, '_audio_device'):
                    widget._audio_device.on_usfx_member_ready(arcname, target_path)

            widget._usfx_member_ready_slot = _on_member_ready  # keep ref so Qt doesn't drop the connection
            _SESSION_SIGNALS.usfx_member_ready.connect(_on_member_ready, Qt.QueuedConnection)
        except Exception:
            pass

        widget._graphics_scene = QGraphicsScene()
        widget._graphics_view = QGraphicsView(widget._graphics_scene)
        widget._graphics_view.setFrameShadow(QFrame.Plain)
        widget._graphics_view.setFrameShape(QFrame.NoFrame)
        widget._graphics_view.setRenderHints(QPainter.Antialiasing)
        widget._graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget._graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
       
        outer = widget

        class graphicsvideoitem(QGraphicsVideoItem):
            def __init__(self):
                super().__init__()

            def paint(self, painter, option, widget):
                super().paint(painter, option, widget)

                # If the QMediaPlayer has nothing in its sink right now (which
                # happens when playback hits EndOfMedia and Qt clears the last
                # frame to black), overpaint the cached `_last_video_frame`
                # so the picture stays visible.
                sink = self.videoSink()
                current = sink.videoFrame() if sink is not None else None
                cached = outer._last_video_frame
                if (current is None or not current.isValid()) and cached is not None and cached.isValid():
                    try:
                        image = cached.toImage()
                    except Exception:
                        image = None
                    if image is not None and not image.isNull():
                        item_rect = self.boundingRect()
                        scale = min(item_rect.width() / image.width(), item_rect.height() / image.height())
                        vw = image.width() * scale
                        vh = image.height() * scale
                        vx = item_rect.x() + (item_rect.width() - vw) / 2.0
                        vy = item_rect.y() + (item_rect.height() - vh) / 2.0
                        painter.drawImage(QRectF(vx, vy, vw, vh), image)

                painter.setRenderHint(QPainter.Antialiasing)

                # Actual video content rect inside the item (letterboxed).
                item_rect = self.boundingRect()
                native = self.nativeSize()
                if native is not None and native.width() > 0 and native.height() > 0:
                    scale = min(item_rect.width() / native.width(), item_rect.height() / native.height())
                    vw = native.width() * scale
                    vh = native.height() * scale
                    vx = item_rect.x() + (item_rect.width() - vw) / 2.0
                    vy = item_rect.y() + (item_rect.height() - vh) / 2.0
                    video_rect = QRectF(vx, vy, vw, vh)
                else:
                    video_rect = QRectF(item_rect)

                painter.setFont(QFont(session.CONFIG.get('videoplayer', {}).get('font_family', 'Montserrat'), session.CONFIG.get('videoplayer', {}).get('font_size', 40)))

                title_safe_margin_qrect = video_rect - QMarginsF(
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * video_rect.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * video_rect.height(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_x', 10) / 100) * video_rect.width(),
                    (session.CONFIG.get('videoplayer', {}).get('safe_margin_title_y', 10) / 100) * video_rect.height()
                )

                if session.SUBTITLE.get('current', False):

                    if session.CONFIG.get('videoplayer', {}).get('backgroundbox_enabled', True):
                        text_rect = painter.boundingRect(title_safe_margin_qrect, Qt.AlignBottom | Qt.AlignHCenter | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(QColor(session.CONFIG.get('videoplayer', {}).get('backgroundbox_color', '#55000000'))))

                        if session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5):
                            painter.drawRoundedRect(text_rect.marginsAdded(QMargins(session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10))), session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5), session.CONFIG.get('videoplayer', {}).get('backgroundbox_border_radius', 5))
                        else:
                            painter.drawRect(text_rect.marginsAdded(QMargins(session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10), session.CONFIG.get('videoplayer', {}).get('backgroundbox_padding', 10))))

                        painter.setBrush(Qt.NoBrush)

                    if session.CONFIG.get('videoplayer', {}).get('shadow_enabled', True):
                        painter.setPen(QPen(session.CONFIG.get('videoplayer', {}).get('shadow_color', '#ff000000')))
                        painter.drawText(title_safe_margin_qrect - QMarginsF(session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), session.CONFIG.get('videoplayer', {}).get('shadow_y', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_x', 2), -session.CONFIG.get('videoplayer', {}).get('shadow_y', 2)), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                    painter.setPen(QPen(session.CONFIG.get('videoplayer', {}).get('color', '#ffffffff')))
                    painter.drawText(title_safe_margin_qrect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, session.SUBTITLE['current']['text'])

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_action_enabled', False):
                    action_safe_margin_qrect = video_rect - QMarginsF(
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * video_rect.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * video_rect.height(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_x', 5) / 100) * video_rect.width(),
                        (session.CONFIG.get('videoplayer', {}).get('safe_margin_action_y', 5) / 100) * video_rect.height()
                    )

                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_action_color', '#dd67FF4D')), 1, Qt.SolidLine))
                    painter.drawRect(action_safe_margin_qrect)

                    center_x = video_rect.center().x()
                    center_y = video_rect.center().y()

                    painter.drawLine(
                        center_x,
                        action_safe_margin_qrect.y(),
                        center_x,
                        action_safe_margin_qrect.y() + (video_rect.height() * .025)
                    )
                    painter.drawLine(
                        center_x,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height(),
                        center_x,
                        action_safe_margin_qrect.y() + action_safe_margin_qrect.height() - (video_rect.height() * .025)
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x(),
                        center_y,
                        action_safe_margin_qrect.x() + (video_rect.width() * .025),
                        center_y
                    )
                    painter.drawLine(
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width(),
                        center_y,
                        action_safe_margin_qrect.x() + action_safe_margin_qrect.width() - (video_rect.width() * .025),
                        center_y
                    )

                if session.CONFIG.get('videoplayer', {}).get('safe_margin_title_enabled', False):
                    painter.setPen(QPen(QColor(session.CONFIG.get('videoplayer', {}).get('safe_margin_title_color', '#ddff0000')), 1, Qt.SolidLine))
                    painter.drawRect(title_safe_margin_qrect)

        widget._graphics_video_item = graphicsvideoitem()
        widget._graphics_scene.addItem(widget._graphics_video_item)
        widget._media_player.setVideoOutput(widget._graphics_video_item)
        widget._media_player.positionChanged.connect(lambda position: widget.position_changed(position))
        widget._media_player.mediaStatusChanged.connect(widget._on_media_status_changed)

        widget._last_video_frame = None
        widget._graphics_video_item.videoSink().videoFrameChanged.connect(widget._on_video_frame)

        widget.layout().addWidget(widget._graphics_view, 1)

        # Video-manipulation plugins (lip-sync mouth crop, ...): a controller
        # taps the frame stream and shows plugin output in a floating panel.
        from subtitld.interface.video_manipulation import VideoManipulationController
        widget.video_manipulation = VideoManipulationController(widget)

        QTimer.singleShot(0, widget._force_resize_update)
    
    def position_changed(widget, position):
        session.SUBTITLE['position'] = position / 1000.0
        widget.update_subtitle_layer()
        widget.position_changed_signal.emit()

        # Stop ~1 frame before the end so QMediaPlayer never reaches EndOfMedia
        # — once it does, the QGraphicsVideoItem clears the last frame to black
        # and the picture vanishes. Pausing pre-emptively keeps the final
        # rendered frame on screen.
        if widget._media_player.playbackState() == QMediaPlayer.PlayingState:
            duration_ms = widget._media_player.duration()
            if duration_ms > 0:
                fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate) or 25
                try:
                    safety_ms = max(40, int(round(2000 / float(fps))))
                except (TypeError, ValueError):
                    safety_ms = 80
                if position >= duration_ms - safety_ms:
                    widget._media_player.pause()
                    widget._audio_device.pause()
                    window = widget.window()
                    if hasattr(window, 'playercontrols_playpause_button'):
                        from subtitld.interface import playercontrols
                        playercontrols.update_playercontrols_playpause_button(window)

    def _on_media_status_changed(widget, status):
        """Two responsibilities:

        1) Apply any `seek()` that landed before the backing media was
           actually loaded. `QMediaPlayer.setSource()` is async, so a
           seek() that runs in the same tick as `loadfile()` silently
           setPosition's into an empty pipeline. We stash that seek
           and replay it the moment the player reaches LoadedMedia
           (or any of the ready states that follow).
        2) Last-resort safety net if the proactive pause in
           `position_changed` missed (e.g. the player jumped past the
           end without firing position updates). Pause both engines
           and step back so the player isn't stuck at EndOfMedia.
        """
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            pending = getattr(widget, '_pending_seek_seconds', None)
            if pending is not None:
                widget._pending_seek_seconds = None
                widget._media_player.setPosition(int(pending * 1000))
                widget._audio_device.seek(pending)
                # The player will fire `positionChanged` asynchronously
                # but the timeline doesn't repaint when paused (the
                # 4Hz follow-timer only runs during playback), so the
                # cursor would sit at 0 until the user interacts. Nudge
                # the timeline + scroll once now so the restored cursor
                # appears in view immediately on project open.
                window = widget.window()
                tl = getattr(window, 'timeline_widget', None)
                if tl is not None:
                    tl.update()
                    try:
                        from subtitld.interface import timeline as _timeline
                        _timeline.update_scrollbar(window, position='middle')
                    except Exception:
                        pass
            return
        if status != QMediaPlayer.EndOfMedia:
            return
        widget._media_player.pause()
        widget._audio_device.pause()
        duration_ms = widget._media_player.duration()
        if duration_ms > 0:
            fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate) or 25
            try:
                step = max(1, int(round(1000 / float(fps))))
            except (TypeError, ValueError):
                step = 40
            widget._media_player.setPosition(max(0, duration_ms - step))
        window = widget.window()
        if hasattr(window, 'playercontrols_playpause_button'):
            from subtitld.interface import playercontrols
            playercontrols.update_playercontrols_playpause_button(window)

    def update_subtitle_layer(widget):
        session.SUBTITLE['current'] = subtitles.subtitle_under_current_position()
        widget.update()

    def resizeEvent(widget, event):
        widget._graphics_view.setSceneRect(QRect(0, 0, event.size().width(), event.size().height()))
        widget._graphics_video_item.setSize(event.size())
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        vm = getattr(widget, 'video_manipulation', None)
        if vm is not None:
            vm.reposition()
        event.accept()

    def showEvent(widget, event):
        # When the player transitions hidden → visible (e.g. on a
        # project load where the preview panel was kept hidden until
        # the slide-in animation positioned it), Qt schedules a layout
        # pass that resizes the player to its real laid-out size. The
        # `resizeEvent` above then re-fits the scene. BUT — that
        # scheduling sequence is async, and the player's size in
        # *this* showEvent is still whatever it was before being
        # hidden (often tiny if the parent was hidden through
        # multiple resize cycles). Defer `_force_resize_update` to
        # the next event-loop tick so it reads the post-layout size
        # rather than the stale showEvent-time size.
        super().showEvent(event)
        QTimer.singleShot(0, widget._force_resize_update)

    def loadfile(widget, filepath):
        if os.path.isfile(filepath):
            from PySide6.QtCore import QUrl
            widget._media_player.setSource(QUrl.fromLocalFile(str(filepath)))
            # widget._audio_device.load(filepath)
            widget.play()
            widget.pause()
            
    def frameStep(widget):
        fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate)
        widget._media_player.setPosition(widget._media_player.position() + int(1000/fps))

    def frameBackStep(widget):
        fps = widget._media_player.metaData().value(QMediaMetaData.VideoFrameRate)
        widget._media_player.setPosition(widget._media_player.position() - int(1000/fps))

    def seek(widget, pos=0.0, method='absolute+exact'):
        """Function to seek at some position. If the backing media
        isn't ready yet (setSource is async), the seek is queued and
        replayed by `_on_media_status_changed` once the player reaches
        LoadedMedia. Without this, a seek issued right after
        `loadfile()` — most importantly the last-position restore at
        project open — silently no-ops because setPosition runs into
        an empty pipeline.

        We also update `session.SUBTITLE['position']` here regardless
        of media readiness. The timeline cursor draws from that field;
        without an immediate write, the queued-seek case would leave
        the cursor at 0 until the player's positionChanged signal
        finally lands, well after the user can see the screen."""
        pos_f = float(pos)
        session.SUBTITLE['position'] = pos_f
        status = widget._media_player.mediaStatus()
        if status not in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                          QMediaPlayer.BufferingMedia, QMediaPlayer.EndOfMedia):
            widget._pending_seek_seconds = pos_f
            return
        widget._media_player.setPosition(int(pos_f*1000))
        widget._audio_device.seek(pos_f)
        
    def stop(widget):
        """Function to stop playback (fake stop, it is pause + position 0)"""
        widget._media_player.pause()
        widget._audio_device.stop()
        widget.seek(0)

    def pause(widget):
        """Function to pause playback (fake pause, it just changes actual playback status)"""
        widget._media_player.pause()
        widget._audio_device.pause()

    def play(widget):
        """Function to play (fake play, it just changes actual playback status)"""
        widget._media_player.play()
        widget._audio_device.play(session.SUBTITLE.get('position', 0))

    def mute(widget):
        """Function to mute"""
        None

    def is_paused(widget):
        return widget._media_player.playbackState() == QMediaPlayer.PausedState

    def volume(widget, vol: int):
        """Function to change volume"""
        None
    
    def set_position(widget, pos):
        widget.seek(pos)

    def get_position(widget):
        return widget._media_player.position()

    def update_speed(self):
        """Function to change playback speed"""
        self._media_player.setPlaybackRate(session.CONFIG['playback_speed'])
        self._audio_device.set_speed(session.CONFIG['playback_speed'])
        # self._audio_device.change_speed(session.CONFIG['playback_speed'])

    def _force_resize_update(widget):
        size = widget.size()
        widget._graphics_view.setSceneRect(QRect(0, 0, size.width(), size.height()))
        widget._graphics_video_item.setSize(size)
        widget._graphics_view.fitInView(widget._graphics_view.sceneRect(), Qt.KeepAspectRatio)
        # NOTE: do NOT call widget.updateGeometry() here. This used to be
        # connected to the panel's slide-in animation on every
        # valueChanged tick — each updateGeometry() invalidates the
        # parent layout, which re-runs the layout pass and resets the
        # animated panel's `pos` back to its laid-out final position.
        # The result was the panel appearing instantly at its final
        # spot with no visible slide.

    def start_face_selection(widget, speaker_name):
        """Enter face-selection mode. User drags a 1:1 square on the video;
        on mouse release, the selected region becomes the speaker image.
        Right-click or Esc cancels."""
        widget._face_selection_speaker = speaker_name
        widget._face_selection_origin = None
        if getattr(widget, '_face_rubber_band', None) is None:
            widget._face_rubber_band = QRubberBand(QRubberBand.Rectangle, widget._graphics_view.viewport())
        viewport = widget._graphics_view.viewport()
        widget._face_selection_prev_focus_policy = viewport.focusPolicy()
        viewport.setFocusPolicy(Qt.StrongFocus)
        viewport.setFocus(Qt.OtherFocusReason)
        viewport.setCursor(Qt.CrossCursor)
        viewport.installEventFilter(widget)

    def _end_face_selection(widget):
        viewport = widget._graphics_view.viewport()
        viewport.removeEventFilter(widget)
        viewport.unsetCursor()
        prev_policy = getattr(widget, '_face_selection_prev_focus_policy', None)
        if prev_policy is not None:
            viewport.setFocusPolicy(prev_policy)
            widget._face_selection_prev_focus_policy = None
        if getattr(widget, '_face_rubber_band', None) is not None:
            widget._face_rubber_band.hide()
        widget._face_selection_speaker = None
        widget._face_selection_origin = None

    def _on_video_frame(widget, frame):
        if frame.isValid():
            widget._last_video_frame = frame
            vm = getattr(widget, 'video_manipulation', None)
            if vm is not None and vm.is_enabled():
                vm.on_frame(frame)

    def set_mouth_view_enabled(widget, enabled):
        """Toggle the lip-sync mouth-crop panel (used by the toolbar button)."""
        vm = getattr(widget, 'video_manipulation', None)
        if vm is not None:
            vm.set_enabled(enabled)

    def _video_rect_in_viewport(widget):
        frame = widget._last_video_frame
        if frame is None or not frame.isValid():
            return None
        size = frame.size()
        fw, fh = size.width(), size.height()
        if fw <= 0 or fh <= 0:
            return None
        vp = widget._graphics_view.viewport()
        vw, vh = vp.width(), vp.height()
        scale = min(vw / fw, vh / fh)
        video_w = fw * scale
        video_h = fh * scale
        video_x = (vw - video_w) / 2.0
        video_y = (vh - video_h) / 2.0
        return QRectF(video_x, video_y, video_w, video_h)

    @staticmethod
    def _clamp_point(pt, rect):
        return QPoint(
            int(max(rect.left(), min(rect.right(), pt.x()))),
            int(max(rect.top(), min(rect.bottom(), pt.y()))),
        )

    def _square_rect(widget, origin, current):
        video_rect = widget._video_rect_in_viewport()
        if video_rect is not None:
            origin = widget._clamp_point(origin, video_rect)
            current = widget._clamp_point(current, video_rect)
        dx = current.x() - origin.x()
        dy = current.y() - origin.y()
        sx = -1 if dx < 0 else 1
        sy = -1 if dy < 0 else 1
        size = max(abs(dx), abs(dy))
        if video_rect is not None:
            max_x = video_rect.right() - origin.x() if sx > 0 else origin.x() - video_rect.left()
            max_y = video_rect.bottom() - origin.y() if sy > 0 else origin.y() - video_rect.top()
            size = int(min(size, max_x, max_y))
        size = max(0, size)
        end = QPoint(origin.x() + sx * size, origin.y() + sy * size)
        top_left = QPoint(min(origin.x(), end.x()), min(origin.y(), end.y()))
        return QRect(top_left, QSize(size, size))

    def eventFilter(widget, obj, event):
        if not getattr(widget, '_face_selection_speaker', None):
            return super().eventFilter(obj, event)
        if obj is not widget._graphics_view.viewport():
            return super().eventFilter(obj, event)

        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            widget._end_face_selection()
            return True
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            widget._face_selection_origin = event.pos()
            widget._face_rubber_band.setGeometry(QRect(event.pos(), QSize()))
            widget._face_rubber_band.show()
            return True
        if etype == QEvent.MouseMove and widget._face_selection_origin is not None:
            widget._face_rubber_band.setGeometry(widget._square_rect(widget._face_selection_origin, event.pos()))
            return True
        if etype == QEvent.MouseButtonRelease and widget._face_selection_origin is not None:
            rect = widget._square_rect(widget._face_selection_origin, event.pos())
            widget._apply_face_selection(rect)
            widget._end_face_selection()
            return True
        if etype == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            widget._end_face_selection()
            return True
        return super().eventFilter(obj, event)

    def _apply_face_selection(widget, rect):
        frame = widget._last_video_frame
        if frame is None or not frame.isValid():
            return
        video_rect = widget._video_rect_in_viewport()
        if video_rect is None:
            return

        clipped = rect.intersected(video_rect.toRect())
        if clipped.width() < 4 or clipped.height() < 4:
            return

        frame_size = frame.size()
        fw, fh = frame_size.width(), frame_size.height()
        scale_x = fw / video_rect.width()
        scale_y = fh / video_rect.height()
        fx = int(round((clipped.x() - video_rect.x()) * scale_x))
        fy = int(round((clipped.y() - video_rect.y()) * scale_y))
        fw_sel = int(round(clipped.width() * scale_x))
        fh_sel = int(round(clipped.height() * scale_y))
        fx = max(0, min(fx, fw - 1))
        fy = max(0, min(fy, fh - 1))
        fw_sel = max(1, min(fw_sel, fw - fx))
        fh_sel = max(1, min(fh_sel, fh - fy))

        frame_image = frame.toImage()
        cropped = frame_image.copy(QRect(fx, fy, fw_sel, fh_sel))
        if cropped.isNull():
            return

        # Downscale to a sane upper bound. Display code always shows the
        # speaker thumbnail at 36×36 (subtitle-list rows, speakers panel,
        # selector). Keeping the full-resolution crop in memory wastes
        # RAM — a 1080×1080 RGBA crop is ~4.7 MB; 256×256 is ~260 KB.
        # 256 px leaves headroom for high-DPI scaling without ballooning
        # the project's footprint. We keep aspect ratio in case the user
        # cropped a non-square region.
        SPEAKER_IMAGE_MAX_DIM = 256
        if max(cropped.width(), cropped.height()) > SPEAKER_IMAGE_MAX_DIM:
            cropped = cropped.scaled(
                SPEAKER_IMAGE_MAX_DIM, SPEAKER_IMAGE_MAX_DIM,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )

        speaker_name = widget._face_selection_speaker
        from subtitld.modules import session as _session
        _session.SPEAKERS.setdefault(speaker_name, {})['image'] = cropped
        _session.set_unsaved()
        window = widget.window()
        from subtitld.interface import left_panel_speakers
        left_panel_speakers.update_speakers_list(window)
        widget.update()


def load(self):
    self.preview_panel_container = QWidget()
    self.preview_panel_container.setLayout(QVBoxLayout())
    self.preview_panel_container.layout().setContentsMargins(0, 0, 0, 0)
    self.preview_panel = QWidget(self.preview_panel_container)
    self.preview_panel.setLayout(QVBoxLayout())
    self.preview_panel.setObjectName('preview_panel')
    self.preview_panel.layout().setContentsMargins(0, self.titleBar.height() - 1, 0, 0)
    self.preview_panel.opacity = QGraphicsOpacityEffect()
    self.preview_panel.opacity.setOpacity(0)
    self.preview_panel.setGraphicsEffect(self.preview_panel.opacity)
    self.preview_panel.animation = QPropertyAnimation(self.preview_panel, b'pos')
    self.preview_panel.animation.setEasingCurve(QEasingCurve.OutCubic)

    self.preview_panel_player = PlayerWidget()
    self.preview_panel_player.setObjectName('preview_panel_player')
    self.preview_panel.layout().addWidget(self.preview_panel_player)

    self.preview_panel_container.layout().addWidget(self.preview_panel)

    self.main_horizontal_splitter.addWidget(self.preview_panel_container)
    
        
def show(self):
    # Opacity = 1 immediately. See bottom_panel.show().
    self.preview_panel.opacity.setOpacity(1.0)
    utils.animate_element(self.preview_panel.animation, duration=1000, effect='slide_from_right')
    # Re-fit the QGraphicsVideoItem AFTER the slide-in finishes —
    # NOT on every tick. The panel's size doesn't change during the
    # slide (only `pos` is animated), so re-fitting per tick is
    # wasteful, AND `_force_resize_update`'s `updateGeometry()`
    # invalidates the parent layout, which re-positions the panel
    # back to its laid-out final spot and overrides the animation.
    # One call at the end is enough — by then the panel is at its
    # final size and the layout is stable.
    anim = self.preview_panel.animation
    def _on_finish():
        try:
            anim.finished.disconnect(_on_finish)
        except (TypeError, RuntimeError):
            pass
        self.preview_panel_player._force_resize_update()
    anim.finished.connect(_on_finish)


def hide(self):
    utils.animate_element(self.preview_panel.animation, duration=200, effect='slide_to_right')


def translate(self):
    pass
