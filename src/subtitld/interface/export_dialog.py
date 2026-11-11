"""Unified export dialog with four category tabs (Subtitles / Audio /
Video / Documents). Each tab exposes a format selector plus the
options that apply to that format. The dialog returns a single config
dict; the caller is responsible for the final file picker and dispatch."""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
                               QPushButton, QButtonGroup, QRadioButton, QCheckBox,
                               QLabel, QSizePolicy, QDialog, QTabWidget, QGridLayout,
                               QGroupBox, QSpinBox, QComboBox, QLineEdit, QProgressBar)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import bdn
from subtitld.modules import csv_export
from subtitld.modules import dcp
from subtitld.modules import dvd_scripts
from subtitld.modules import export_options
from subtitld.modules import hls
from subtitld.modules import isobmff_text
from subtitld.modules import mcc
from subtitld.modules import pac
from subtitld.modules import session
from subtitld.modules import soft_subtitles
from subtitld.modules import ttml
from subtitld.modules import vobsub


SUBTITLE_FORMATS = ['SRT', 'VTT', 'HLS', 'CMFT', 'DASH', 'ASS', 'DFXP', 'TTML', 'SAMI', 'SCC', 'MCC', 'STL', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890',
                    'AVID', 'FCPXML', 'DCP', 'VOBSUB', 'PGS', 'BDN', 'DVDSTUDIOPRO', 'ENCORE', 'DVDARCHITECT', 'SCENARIST', 'DVDPRODUCER', 'AVIDDVD', 'SBV', 'SUB',
                    'XML', 'LRC', 'TTXT', 'USF', 'JSON', 'BCC']
# The DVD authoring tools' scripts: frames, an encoding and line ends each;
# drop-frame for the two whose tools write it.
DVD_SCRIPTS = tuple(dvd_scripts.SESSION_FORMATS)
DVD_DROP_FRAME = ('DVDSTUDIOPRO', 'ENCORE')
AUDIO_FORMATS = ['WAV', 'FLAC', 'MP3']
VIDEO_FORMATS = ['MP4']
DOCUMENT_FORMATS = ['TXT', 'KDENLIVE', 'CSV']


class _SubtitlesPanel(QWidget):
    """The subtitles tab: the format, and the options it takes. The formats
    of timed text share what text is written and how (export_options); SRT,
    SUB and USF have options of their own. The choices are remembered
    (CONFIG['export_subtitles']), except the shift, which is for one export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        remembered = session.CONFIG.get('export_subtitles') or {}

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(SUBTITLE_FORMATS)
        self.format_combo.activated.connect(self._refresh_options)
        self.layout().addWidget(self.format_combo)

        # What the timed-text formats write.
        self.text_options = QWidget()
        grid = QGridLayout(self.text_options)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        self.text_combo = utils.LabeledComboBox()
        self.text_combo.setLabel(_('export_dialog.text'))
        self.text_combo.combobox.addItem(_('export_dialog.text_original'), ('original', ''))
        names = {code: name for name, code in session.LANGUAGE_DICT_LIST.items()}
        for language in export_options.translation_languages(session.SUBTITLE.get('segments')):
            name = names.get(language, language)
            self.text_combo.combobox.addItem(_('export_dialog.text_translation').format(language=name), ('translation', language))
            self.text_combo.combobox.addItem(_('export_dialog.text_both').format(language=name), ('both', language))
        # Only offered when there is a translation to choose.
        self.text_combo.setVisible(self.text_combo.combobox.count() > 1)
        grid.addWidget(self.text_combo, 0, 0, 1, 2)

        self.speakers_combo = utils.LabeledComboBox()
        self.speakers_combo.setLabel(_('export_dialog.speaker_names'))
        # A choice made (not the list rebuilt for another format, which
        # blocks the signal) is the one wanted from then on.
        self.speakers_combo.combobox.currentIndexChanged.connect(
            lambda _index: setattr(self, '_speakers_wanted', self.speakers_combo.combobox.currentData()))
        grid.addWidget(self.speakers_combo, 1, 0)

        self.formatting_combo = utils.LabeledComboBox()
        self.formatting_combo.setLabel(_('export_dialog.formatting'))
        self.formatting_combo.combobox.addItem(_('export_dialog.formatting_keep'), 'keep')
        self.formatting_combo.combobox.addItem(_('export_dialog.formatting_remove'), 'remove')
        grid.addWidget(self.formatting_combo, 1, 1)

        self.offset_spin = utils.LabeledDoubleSpinBox()
        self.offset_spin.setLabel(_('export_dialog.shift_times'))
        self.offset_spin.setRange(-3600, 3600)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(0.1)
        grid.addWidget(self.offset_spin, 2, 0)
        self.layout().addWidget(self.text_options)

        # SRT, SAMI, SUB, LRC, Avid SubCap and the DVD scripts: for players
        # (and editors, and authoring tools) that need an older encoding or
        # Windows line ends.
        self.srt_options = QWidget()
        row = QHBoxLayout(self.srt_options)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.encoding_combo = utils.LabeledComboBox()
        self.encoding_combo.setLabel(_('export_dialog.encoding'))
        for encoding, key in zip(export_options.ENCODINGS, ('utf8', 'utf8_bom', 'utf16', 'cp1252', 'cp1250', 'cp1251', 'cp949')):
            self.encoding_combo.combobox.addItem(_(f'export_dialog.encoding_{key}'), encoding)
        row.addWidget(self.encoding_combo, 1)
        self.line_ends_combo = utils.LabeledComboBox()
        self.line_ends_combo.setLabel(_('export_dialog.line_ends'))
        self.line_ends_combo.combobox.addItem(_('export_dialog.line_ends_lf'), 'lf')
        self.line_ends_combo.combobox.addItem(_('export_dialog.line_ends_crlf'), 'crlf')
        row.addWidget(self.line_ends_combo, 1)
        self.layout().addWidget(self.srt_options)

        # SUB (MicroDVD), PAC, UniPac, Cheetah CAP, Lambda CAP, Cavena 890,
        # Avid SubCap and Final Cut Pro count in frames; the first six do not
        # say at what rate.
        self.sub_options = QWidget()
        row = QHBoxLayout(self.sub_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.fps_combo = utils.LabeledComboBox()
        self.fps_combo.setLabel(_('export_dialog.frame_rate'))
        video_fps = float(session.VIDEO.get('framerate') or 0)
        rates = list(export_options.FRAME_RATES)
        if video_fps and not any(abs(video_fps - rate) < 0.01 for rate in rates):
            rates = sorted(rates + [round(video_fps, 3)])
        for rate in rates:
            label = f'{rate:g} fps'
            if video_fps and abs(video_fps - rate) < 0.01:
                label = _('export_dialog.frame_rate_video').format(fps=label)
            self.fps_combo.combobox.addItem(label, rate)
        row.addWidget(self.fps_combo)
        row.addStretch()
        self.layout().addWidget(self.sub_options)

        # PAC: the code page its text is written in, as the file does not
        # say it; found from the language and the text unless one is asked.
        self.pac_options = QWidget()
        row = QHBoxLayout(self.pac_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.pac_code_page_combo = utils.LabeledComboBox()
        self.pac_code_page_combo.setLabel(_('export_dialog.pac_code_page'))
        self.pac_code_page_combo.combobox.addItem(_('export_dialog.pac_code_page_auto'), 'auto')
        for page in pac.CODE_PAGES:
            self.pac_code_page_combo.combobox.addItem(_('export_dialog.pac_code_page_' + page.replace('-', '_')), page)
        row.addWidget(self.pac_code_page_combo, 1)
        self.layout().addWidget(self.pac_options)

        # SCC: drop-frame timecode, as broadcast uses, or not.
        self.scc_options = QWidget()
        row = QHBoxLayout(self.scc_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.timecode_combo = utils.LabeledComboBox()
        self.timecode_combo.setLabel(_('export_dialog.timecode'))
        self.timecode_combo.combobox.addItem(_('export_dialog.timecode_drop'), 'drop')
        self.timecode_combo.combobox.addItem(_('export_dialog.timecode_non_drop'), 'non-drop')
        row.addWidget(self.timecode_combo, 1)
        self.layout().addWidget(self.scc_options)

        # Avid SubCap and Final Cut Pro: the timecode the editor's timeline
        # starts at.
        self.timeline_options = QWidget()
        row = QHBoxLayout(self.timeline_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.timeline_start_combo = utils.LabeledComboBox()
        self.timeline_start_combo.setLabel(_('export_dialog.timeline_start'))
        self.timeline_start_combo.combobox.addItem('00:00:00:00', '00:00:00:00')
        self.timeline_start_combo.combobox.addItem(_('export_dialog.timeline_start_hour'), '01:00:00:00')
        row.addWidget(self.timeline_start_combo, 1)
        self.layout().addWidget(self.timeline_options)

        # MacCaption: its frame rate, the video's; CEA-708 beside CEA-608, or not.
        self.mcc_options = QWidget()
        row = QHBoxLayout(self.mcc_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.mcc_rate_combo = utils.LabeledComboBox()
        self.mcc_rate_combo.setLabel(_('export_dialog.frame_rate'))
        for key in mcc.RATES:
            if key == '29.97DF':
                label = _('export_dialog.mcc_rate_drop').format(fps='29.97')
            elif key == '29.97':
                label = _('export_dialog.mcc_rate_non_drop').format(fps='29.97')
            else:
                label = f'{key} fps'
            if key == mcc.rate_for(video_fps):
                label = _('export_dialog.frame_rate_video').format(fps=label)
            self.mcc_rate_combo.combobox.addItem(label, key)
        row.addWidget(self.mcc_rate_combo, 1)
        self.mcc_captions_combo = utils.LabeledComboBox()
        self.mcc_captions_combo.setLabel(_('export_dialog.mcc_captions'))
        self.mcc_captions_combo.combobox.addItem(_('export_dialog.mcc_captions_both'), 'both')
        self.mcc_captions_combo.combobox.addItem(_('export_dialog.mcc_captions_608'), '608')
        self.mcc_captions_combo.combobox.addItem(_('export_dialog.mcc_captions_op47'), 'op47')
        row.addWidget(self.mcc_captions_combo, 1)
        self.layout().addWidget(self.mcc_options)

        # SCC and MCC: how the captions are written, in which channel, and
        # (MacCaption, which has room for them) another language beside them.
        self.caption_options = QWidget()
        row = QHBoxLayout(self.caption_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.caption_mode_combo = utils.LabeledComboBox()
        self.caption_mode_combo.setLabel(_('export_dialog.caption_mode'))
        for mode in ('pop', 'roll2', 'roll3', 'roll4', 'paint'):
            self.caption_mode_combo.combobox.addItem(_(f'export_dialog.caption_mode_{mode}'), mode)
        row.addWidget(self.caption_mode_combo, 1)
        self.caption_channel_combo = utils.LabeledComboBox()
        self.caption_channel_combo.setLabel(_('export_dialog.caption_channel'))
        row.addWidget(self.caption_channel_combo, 1)
        self.caption_service_combo = utils.LabeledComboBox()
        self.caption_service_combo.setLabel(_('export_dialog.caption_service'))
        self.caption_service_combo.combobox.addItem(_('export_dialog.caption_service_none'), '')
        for language in export_options.translation_languages(session.SUBTITLE.get('segments')):
            self.caption_service_combo.combobox.addItem(
                _('export_dialog.caption_service_language').format(language=names.get(language, language)), language)
        row.addWidget(self.caption_service_combo, 1)
        self.layout().addWidget(self.caption_options)

        # EBU STL: its frame rate, its display and the programme's start.
        self.stl_options = QWidget()
        stl_grid = QGridLayout(self.stl_options)
        stl_grid.setContentsMargins(0, 0, 0, 0)
        stl_grid.setSpacing(10)
        self.stl_rate_combo = utils.LabeledComboBox()
        self.stl_rate_combo.setLabel(_('export_dialog.frame_rate'))
        self.stl_rate_combo.combobox.addItem(_('export_dialog.stl_fps_25'), 25)
        self.stl_rate_combo.combobox.addItem(_('export_dialog.stl_fps_30'), 30)
        stl_grid.addWidget(self.stl_rate_combo, 0, 0)
        self.stl_display_combo = utils.LabeledComboBox()
        self.stl_display_combo.setLabel(_('export_dialog.stl_display'))
        self.stl_display_combo.combobox.addItem(_('export_dialog.stl_display_teletext'), 'teletext')
        self.stl_display_combo.combobox.addItem(_('export_dialog.stl_display_open'), 'open')
        stl_grid.addWidget(self.stl_display_combo, 0, 1)
        self.stl_start_combo = utils.LabeledComboBox()
        self.stl_start_combo.setLabel(_('export_dialog.stl_start'))
        self.stl_start_combo.combobox.addItem('00:00:00:00', '00:00:00:00')
        self.stl_start_combo.combobox.addItem(_('export_dialog.stl_start_ten'), '10:00:00:00')
        stl_grid.addWidget(self.stl_start_combo, 1, 0)
        self.layout().addWidget(self.stl_options)

        # DCP: Interop or SMPTE, and the frame rate a SMPTE reel counts its
        # times in (Interop counts ticks of a second, whatever the film's).
        self.dcp_options = QWidget()
        row = QHBoxLayout(self.dcp_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.dcp_standard_combo = utils.LabeledComboBox()
        self.dcp_standard_combo.setLabel(_('export_dialog.dcp_standard'))
        self.dcp_standard_combo.combobox.addItem(_('export_dialog.dcp_standard_smpte'), dcp.SMPTE)
        self.dcp_standard_combo.combobox.addItem(_('export_dialog.dcp_standard_interop'), dcp.INTEROP)
        row.addWidget(self.dcp_standard_combo, 1)
        # Text the projector draws, or pictures drawn here.
        self.dcp_content_combo = utils.LabeledComboBox()
        self.dcp_content_combo.setLabel(_('export_dialog.dcp_content'))
        self.dcp_content_combo.combobox.addItem(_('export_dialog.dcp_content_text'), 'text')
        self.dcp_content_combo.combobox.addItem(_('export_dialog.dcp_content_pictures'), 'pictures')
        row.addWidget(self.dcp_content_combo, 1)
        self.dcp_rate_combo = utils.LabeledComboBox()
        self.dcp_rate_combo.setLabel(_('export_dialog.frame_rate'))
        for rate in dcp.RATES:
            label = f'{rate} fps'
            if video_fps and rate == dcp.rate_for(video_fps):
                label = _('export_dialog.frame_rate_video').format(fps=label)
            self.dcp_rate_combo.combobox.addItem(label, rate)
        row.addWidget(self.dcp_rate_combo, 1)
        self.dcp_standard_combo.combobox.currentIndexChanged.connect(
            lambda _index: self.dcp_rate_combo.setVisible(self.dcp_standard_combo.combobox.currentData() == dcp.SMPTE))
        self.dcp_standard_combo.combobox.currentIndexChanged.connect(self._run_check)
        self.dcp_content_combo.combobox.currentIndexChanged.connect(self._run_check)
        self.layout().addWidget(self.dcp_options)

        # BDN: the frame rate its events count in, a Blu-ray's.
        self.bdn_options = QWidget()
        row = QHBoxLayout(self.bdn_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.bdn_rate_combo = utils.LabeledComboBox()
        self.bdn_rate_combo.setLabel(_('export_dialog.frame_rate'))
        for rate in bdn.RATES:
            label = f'{rate:g} fps'
            if video_fps and rate == bdn.rate_for(video_fps):
                label = _('export_dialog.frame_rate_video').format(fps=label)
            self.bdn_rate_combo.combobox.addItem(label, rate)
        row.addWidget(self.bdn_rate_combo, 1)
        row.addStretch()
        self.layout().addWidget(self.bdn_options)

        # Cavena 890: the programme's start its frames count from.
        self.cavena_options = QWidget()
        row = QHBoxLayout(self.cavena_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.cavena_start_combo = utils.LabeledComboBox()
        self.cavena_start_combo.setLabel(_('export_dialog.stl_start'))
        self.cavena_start_combo.combobox.addItem('00:00:00:00', '00:00:00:00')
        self.cavena_start_combo.combobox.addItem(_('export_dialog.stl_start_ten'), '10:00:00:00')
        row.addWidget(self.cavena_start_combo, 1)
        row.addStretch()
        self.layout().addWidget(self.cavena_options)

        # HLS, CMAF and DASH: how long each segment is; HLS's timestamp its
        # cue times map to; what a CMAF track carries, WebVTT or TTML.
        self.stream_options = QWidget()
        row = QHBoxLayout(self.stream_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.carriage_combo = utils.LabeledComboBox()
        self.carriage_combo.setLabel(_('export_dialog.mp4_text'))
        for carriage in isobmff_text.CARRIAGES:
            self.carriage_combo.combobox.addItem(_(f'export_dialog.mp4_text_{carriage.replace(".", "").replace("-", "")}'),
                                                 carriage)
        self.carriage_combo.combobox.currentIndexChanged.connect(lambda _index: self._refresh_options())
        row.addWidget(self.carriage_combo, 1)
        self.segment_combo = utils.LabeledComboBox()
        self.segment_combo.setLabel(_('export_dialog.segment_duration'))
        for seconds in hls.SEGMENT_DURATIONS:
            label = _('export_dialog.segment_seconds').format(seconds=f'{seconds:g}')
            if seconds == hls.SEGMENT_DURATION:
                label = _('export_dialog.segment_seconds_apple').format(seconds=f'{seconds:g}')
            self.segment_combo.combobox.addItem(label, seconds)
        row.addWidget(self.segment_combo, 1)
        self.mpegts_combo = utils.LabeledComboBox()
        self.mpegts_combo.setLabel(_('export_dialog.mpegts'))
        self.mpegts_combo.combobox.addItem(_('export_dialog.mpegts_ten'), 900000)
        self.mpegts_combo.combobox.addItem(_('export_dialog.mpegts_zero'), 0)
        row.addWidget(self.mpegts_combo, 1)
        self.layout().addWidget(self.stream_options)
        # VobSub: the frame its pictures are on, a DVD's (PAL or NTSC) or,
        # for a VobSub kept beside a video file, the video's.
        self.vobsub_options = QWidget()
        row = QHBoxLayout(self.vobsub_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.vobsub_size_combo = utils.LabeledComboBox()
        self.vobsub_size_combo.setLabel(_('export_dialog.picture_size'))
        video_size = (int(session.VIDEO.get('width') or 0), int(session.VIDEO.get('height') or 0))
        if all(video_size):
            self.vobsub_size_combo.combobox.addItem(
                _('export_dialog.picture_size_video').format(size=f'{video_size[0]}x{video_size[1]}'), 'video')
        self.vobsub_size_combo.combobox.addItem(_('export_dialog.picture_size_pal'), 'pal')
        self.vobsub_size_combo.combobox.addItem(_('export_dialog.picture_size_ntsc'), 'ntsc')
        row.addWidget(self.vobsub_size_combo, 1)
        row.addStretch()
        self.layout().addWidget(self.vobsub_options)

        # ASS and TTML: the look of their one style.
        self.ass_options = QWidget()
        row = QHBoxLayout(self.ass_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.look_combo = utils.LabeledComboBox()
        self.look_combo.setLabel(_('export_dialog.ass_look'))
        self.look_combo.combobox.addItem(_('export_dialog.ass_look_clean'), 'clean')
        self.look_combo.combobox.addItem(_('export_dialog.ass_look_preview'), 'preview')
        row.addWidget(self.look_combo, 1)
        # TTML and DFXP: the profile the file says it follows.
        self.profile_combo = utils.LabeledComboBox()
        self.profile_combo.setLabel(_('export_dialog.ttml_profile'))
        for profile, name in (('ttml1', 'ttml1'), ('imsc1', 'imsc1'), ('imsc1.1', 'imsc11'), ('ebu-tt-d', 'ebuttd'),
                              ('smpte-tt', 'smptett'),
                              ('imsc1-image', 'imsc1_image'), ('imsc1.1-image', 'imsc11_image')):
            self.profile_combo.combobox.addItem(_(f'export_dialog.ttml_profile_{name}'), profile)
        row.addWidget(self.profile_combo, 1)
        self.layout().addWidget(self.ass_options)
        # SMPTE-TT: the subtitles as CEA-608 captions too, tunnelled in the
        # file for what takes the captions and not the text (RP 2052-10).
        self.tunnel_608_check = QCheckBox(_('export_dialog.ttml_tunnel_608'))
        self.layout().addWidget(self.tunnel_608_check)
        self.profile_combo.combobox.currentIndexChanged.connect(self._show_tunnel)
        # What the file about to be written would break in the profile it
        # declares, as ttml.check finds it, while there is still time to
        # change something.
        self.check_label = QLabel()
        self.check_label.setObjectName('export_dialog_ttml_check')
        self.check_label.setWordWrap(True)
        self.check_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # In from the edge as far as the fields' own text is.
        self.check_label.setContentsMargins(6, 4, 6, 4)
        self.layout().addWidget(self.check_label)
        self.profile_combo.combobox.currentIndexChanged.connect(self._run_check)

        self.usf_options = QWidget()
        self.usf_options.setLayout(QVBoxLayout())
        self.usf_options.layout().setContentsMargins(0, 0, 0, 0)
        self.usf_embed_images = QCheckBox(_('export_usf_dialog.embed_speaker_images'))
        self.usf_options.layout().addWidget(self.usf_embed_images)
        self.usf_embed_dubs = QCheckBox(_('export_usf_dialog.embed_audio_clips'))
        self.usf_options.layout().addWidget(self.usf_embed_dubs)
        self.layout().addWidget(self.usf_options)

        self.layout().addStretch()

        self.format_combo.setCurrentText(remembered.get('format') if remembered.get('format') in SUBTITLE_FORMATS else 'SRT')
        _select(self.text_combo, (remembered.get('text', 'original'), remembered.get('language', '')))
        self._speakers_wanted = remembered.get('speakers', 'none')
        # The channel last chosen, kept while a format that lacks it is picked.
        self._caption_channel_wanted = remembered.get('caption_channel', 1)
        self.caption_channel_combo.combobox.activated.connect(
            lambda _index: setattr(self, '_caption_channel_wanted', self.caption_channel_combo.combobox.currentData()))
        _select(self.formatting_combo, remembered.get('formatting', 'keep'))
        _select(self.encoding_combo, remembered.get('encoding', 'utf-8'))
        _select(self.line_ends_combo, remembered.get('line_ends', 'lf'))
        # The video's frame rate when there is a video; else the last one used.
        video_rate = next((rate for rate in rates if video_fps and abs(video_fps - rate) < 0.01), None)
        if not _select(self.fps_combo, video_rate) and not _select(self.fps_combo, remembered.get('fps')):
            _select(self.fps_combo, 25.0)
        _select(self.look_combo, remembered.get('look', 'clean'))
        _select(self.profile_combo, remembered.get('profile', 'ttml1'))
        self.tunnel_608_check.setChecked(bool(remembered.get('tunnel_608', False)))
        _select(self.timecode_combo, remembered.get('timecode', 'drop'))
        # 30 (29.97) for a video at that rate or twice it; else the last used.
        if video_fps and (abs(video_fps - 30) < 0.1 or abs(video_fps - 60) < 0.1):
            _select(self.stl_rate_combo, 30)
        elif video_fps:
            _select(self.stl_rate_combo, 25)
        else:
            _select(self.stl_rate_combo, remembered.get('stl_fps', 25))
        _select(self.stl_display_combo, remembered.get('display', 'teletext'))
        # The video's rate when MCC has it (drop-frame at 29.97); else the last used.
        if not _select(self.mcc_rate_combo, mcc.rate_for(video_fps)):
            _select(self.mcc_rate_combo, remembered.get('mcc_rate', '29.97DF'))
        _select(self.mcc_captions_combo, remembered.get('mcc_captions', 'both'))
        _select(self.caption_mode_combo, remembered.get('caption_mode', 'pop'))
        _select(self.stl_start_combo, remembered.get('start', '00:00:00:00'))
        _select(self.timeline_start_combo, remembered.get('timeline_start', '00:00:00:00'))
        _select(self.dcp_standard_combo, remembered.get('dcp_standard', dcp.SMPTE))
        _select(self.dcp_content_combo, remembered.get('dcp_content', 'text'))
        # The video's rate, as near as a DCP plays; else the last used, else 24.
        _select(self.dcp_rate_combo, dcp.rate_for(video_fps) if video_fps
                else remembered.get('dcp_fps', dcp.DEFAULT_RATE))
        self.dcp_rate_combo.setVisible(self.dcp_standard_combo.combobox.currentData() == dcp.SMPTE)
        # The video's rate, as near as a Blu-ray plays; else the last used.
        _select(self.bdn_rate_combo, bdn.rate_for(video_fps) if video_fps
                else remembered.get('bdn_fps', bdn.DEFAULT_RATE))
        _select(self.cavena_start_combo, remembered.get('cavena_start', '00:00:00:00'))
        _select(self.segment_combo, remembered.get('segment_duration', hls.SEGMENT_DURATION))
        _select(self.mpegts_combo, remembered.get('mpegts', hls.MPEGTS))
        _select(self.carriage_combo, remembered.get('mp4_text', 'wvtt'))
        # The video's frame when there is a video; else the last used, else
        # the DVD frame of the video's rate.
        if not _select(self.vobsub_size_combo, 'video') and not _select(self.vobsub_size_combo, remembered.get('vobsub_size')):
            _select(self.vobsub_size_combo, 'ntsc' if vobsub.frame_size(fps=video_fps) == vobsub.SIZES['ntsc'] else 'pal')
        _select(self.pac_code_page_combo, remembered.get('pac_code_page', 'auto'))
        self.usf_embed_images.setChecked(bool(remembered.get('embed_speaker_images', False)))
        self.usf_embed_dubs.setChecked(bool(remembered.get('embed_audio_clips', False)))
        # Drop-frame or not is only a choice at the rates that have it.
        self.fps_combo.combobox.currentIndexChanged.connect(self._refresh_options)
        self._refresh_options()

    def _speaker_choices(self, fmt):
        """The speaker choices for `fmt`: leave out, before the text and the
        format's own way, if it has one: WebVTT's voice tags, TTML's agents,
        SAMI's Source line, SCC's ">>", SBV's ">> NAME:".
        The one last chosen stays chosen when it is offered; the format's own
        way falls back to names before the text."""
        combo = self.speakers_combo.combobox
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_('export_dialog.speaker_names_none'), 'none')
        combo.addItem(_('export_dialog.speaker_names_prefix'), 'prefix')
        wvtt = fmt in ('CMFT', 'DASH') and self.carriage_combo.combobox.currentData() == 'wvtt'
        if fmt in ('VTT', 'HLS') or wvtt:
            combo.addItem(_('export_dialog.speaker_names_voice'), 'voice')
        elif fmt in ('DFXP', 'TTML', 'CMFT', 'DASH'):
            combo.addItem(_('export_dialog.speaker_names_agent'), 'voice')
        elif fmt == 'SAMI':
            combo.addItem(_('export_dialog.speaker_names_source'), 'voice')
        elif fmt in ('SCC', 'MCC'):
            combo.addItem(_('export_dialog.speaker_names_chevrons'), 'voice')
        elif fmt == 'SBV':
            combo.addItem(_('export_dialog.speaker_names_youtube'), 'voice')
        if not _select(self.speakers_combo, self._speakers_wanted):
            _select(self.speakers_combo, 'prefix' if self._speakers_wanted == 'voice' else 'none')
        combo.blockSignals(False)

    def _refresh_options(self):
        fmt = self.format_combo.currentText()
        self._speaker_choices(fmt)
        self.text_options.setVisible(fmt in export_options.TEXT_FORMATS)
        self.srt_options.setVisible(fmt in ('SRT', 'SAMI', 'SUB', 'LRC', 'AVID') + DVD_SCRIPTS)
        # SAMI and FCPXML write the original and the translation as two languages.
        names = {code: name for name, code in session.LANGUAGE_DICT_LIST.items()}
        combo = self.text_combo.combobox
        for index in range(combo.count()):
            text, language = combo.itemData(index)
            if text == 'both':
                combo.setItemText(index, _('export_dialog.text_both_sami' if fmt in ('SAMI', 'FCPXML') else 'export_dialog.text_both')
                                  .format(language=names.get(language, language)))
        self.sub_options.setVisible(fmt in ('SUB', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890', 'AVID', 'FCPXML') + DVD_SCRIPTS)
        # SCC is always 29.97; the editors' formats count at the rate chosen.
        rate = float(self.fps_combo.combobox.currentData() or 0)
        self.scc_options.setVisible(fmt == 'SCC' or (fmt in ('AVID', 'FCPXML', 'LAMBDACAP') + DVD_DROP_FRAME and _has_drop_frame(rate)))
        self.timeline_options.setVisible(fmt in ('AVID', 'FCPXML'))
        self.cavena_options.setVisible(fmt == 'CAVENA890')
        self.vobsub_options.setVisible(fmt == 'VOBSUB')
        self.pac_options.setVisible(fmt == 'PAC')
        self.stl_options.setVisible(fmt == 'STL')
        self.stream_options.setVisible(fmt in ('HLS', 'CMFT', 'DASH'))
        self.mpegts_combo.setVisible(fmt == 'HLS')
        self.carriage_combo.setVisible(fmt in ('CMFT', 'DASH'))
        stpp = fmt in ('CMFT', 'DASH') and self.carriage_combo.combobox.currentData() != 'wvtt'
        self.dcp_options.setVisible(fmt == 'DCP')
        self.bdn_options.setVisible(fmt == 'BDN')
        self.mcc_options.setVisible(fmt == 'MCC')
        # A file holds one field, so SCC has CC1 and CC2; MacCaption carries
        # both fields, so it has all four.
        self.caption_options.setVisible(fmt in ('SCC', 'MCC'))
        channels = self.caption_channel_combo.combobox
        wanted = [1, 2] if fmt == 'SCC' else [1, 2, 3, 4]
        if [channels.itemData(index) for index in range(channels.count())] != wanted:
            chosen = channels.currentData()
            channels.clear()
            for number in wanted:
                channels.addItem(f'CC{number}', number)
            if not _select(self.caption_channel_combo, chosen):
                _select(self.caption_channel_combo, self._caption_channel_wanted)
        # Another language rides in a CEA-708 service, which only MCC carries.
        self.caption_service_combo.setVisible(fmt == 'MCC' and self.caption_service_combo.combobox.count() > 1)
        # SBV, BCC, Avid SubCap and the DVD scripts but DVD Studio Pro's and
        # Avid DVD's (italics, and DVD Studio Pro's places) have no
        # formatting to keep.
        self.formatting_combo.setVisible(fmt not in ('SBV', 'BCC', 'AVID', 'ENCORE', 'DVDARCHITECT', 'SCENARIST',
                                                     'DVDPRODUCER'))
        self.ass_options.setVisible(fmt in ('ASS', 'DFXP', 'TTML') or stpp)
        # TTML has no shadow to give.
        self.look_combo.combobox.setItemText(1, _('export_dialog.ass_look_preview') if fmt == 'ASS'
                                             else _('export_dialog.ttml_look_preview'))
        # Only TTML and DFXP have a profile to declare.
        self.profile_combo.setVisible(fmt in ('DFXP', 'TTML'))
        self._show_tunnel()
        self.usf_options.setVisible(fmt == 'USF')
        self._run_check()

    def _show_tunnel(self, *_args):
        """The CEA-608 tunnel is SMPTE-TT's alone."""
        self.tunnel_608_check.setVisible(self.format_combo.currentText() in ('DFXP', 'TTML')
                                         and self.profile_combo.combobox.currentData() == 'smpte-tt')

    def _run_check(self, *_args):
        """What the TTML this export writes would break in its profile:
        the file written as it will be, then read back by ttml.check. For
        VobSub and PGS, what their pictures will be."""
        fmt = self.format_combo.currentText()
        segments = session.SUBTITLE.get('segments') or []
        if fmt == 'BDN':
            # Drawn when the file is written: what is said is where they go.
            self.check_label.setText(_('export_dialog.bdn_note'))
            self.check_label.setVisible(True)
            return
        if fmt == 'DCP' and self.dcp_content_combo.combobox.currentData() == 'pictures':
            smpte = self.dcp_standard_combo.combobox.currentData() == dcp.SMPTE
            self.check_label.setText(_('export_dialog.dcp_pictures_note_smpte' if smpte
                                       else 'export_dialog.dcp_pictures_note_interop'))
            self.check_label.setVisible(True)
            return
        if fmt in ('VOBSUB', 'PGS'):
            # A disc's pictures: what they will be, or that this build
            # cannot draw them.
            from subtitld.interface import subtitle_renderer
            self.check_label.setText(_('export_dialog.picture_note_' + fmt.lower()) if subtitle_renderer.available()
                                     else _('export_dialog.picture_no_renderer'))
            self.check_label.setVisible(True)
            return
        if fmt not in ('DFXP', 'TTML') or not segments:
            self.check_label.setVisible(False)
            return
        profile = self.profile_combo.combobox.currentData() or 'ttml1'
        if profile in ttml.IMAGE_PROFILES:
            # Pictures are drawn when the file is written, not to check it
            # here: what is said instead is where they will go.
            self.check_label.setText(_('export_dialog.ttml_image_note'))
            self.check_label.setVisible(True)
            return
        written = ttml.write(segments, session.SUBTITLE.get('language') or 'en', profile=profile,
                             metadata=session.SUBTITLE.get('metadata'))
        problems = ttml.check(written, profile)
        name = self.profile_combo.combobox.currentText()
        if problems:
            self.check_label.setText('\n'.join([_('export_dialog.ttml_check_found').format(profile=name)]
                                               + [f'\u2022 {problem}' for problem in problems]))
        else:
            self.check_label.setText(_('export_dialog.ttml_check_clean').format(profile=name))
        self.check_label.setVisible(True)

    def get_config(self):
        fmt = self.format_combo.currentText()
        text, language = self.text_combo.combobox.currentData() or ('original', '')
        choices = {
            'text': text,
            'language': language,
            'speakers': self.speakers_combo.combobox.currentData(),
            'formatting': self.formatting_combo.combobox.currentData(),
            'encoding': self.encoding_combo.combobox.currentData(),
            'line_ends': self.line_ends_combo.combobox.currentData(),
            'fps': self.fps_combo.combobox.currentData(),
            'look': self.look_combo.combobox.currentData(),
            'profile': self.profile_combo.combobox.currentData(),
            'tunnel_608': self.tunnel_608_check.isChecked(),
            'timecode': self.timecode_combo.combobox.currentData(),
            'stl_fps': self.stl_rate_combo.combobox.currentData(),
            'mcc_rate': self.mcc_rate_combo.combobox.currentData(),
            'mcc_captions': self.mcc_captions_combo.combobox.currentData(),
            'caption_mode': self.caption_mode_combo.combobox.currentData(),
            'caption_channel': self._caption_channel_wanted,
            'caption_service_language': self.caption_service_combo.combobox.currentData(),
            'display': self.stl_display_combo.combobox.currentData(),
            'start': self.stl_start_combo.combobox.currentData(),
            'timeline_start': self.timeline_start_combo.combobox.currentData(),
            'dcp_standard': self.dcp_standard_combo.combobox.currentData(),
            'dcp_fps': self.dcp_rate_combo.combobox.currentData(),
            'dcp_content': self.dcp_content_combo.combobox.currentData(),
            'bdn_fps': self.bdn_rate_combo.combobox.currentData(),
            'cavena_start': self.cavena_start_combo.combobox.currentData(),
            'segment_duration': self.segment_combo.combobox.currentData(),
            'mpegts': self.mpegts_combo.combobox.currentData(),
            'mp4_text': self.carriage_combo.combobox.currentData(),
            'vobsub_size': self.vobsub_size_combo.combobox.currentData(),
            'pac_code_page': self.pac_code_page_combo.combobox.currentData(),
            'embed_speaker_images': self.usf_embed_images.isChecked(),
            'embed_audio_clips': self.usf_embed_dubs.isChecked(),
        }
        # The speaker choice as last made: voice tags stay wanted for WebVTT
        # even when another format wrote names before the text.
        session.CONFIG['export_subtitles'] = dict(choices, format=fmt, speakers=self._speakers_wanted)

        # Every export sets its options, so none is left over from the last.
        options = {}
        if fmt in export_options.TEXT_FORMATS:
            options.update({key: choices[key] for key in ('text', 'language', 'speakers', 'formatting')})
            options['offset'] = self.offset_spin.value()
        if fmt in ('SRT', 'SAMI', 'SUB', 'LRC', 'AVID') + DVD_SCRIPTS:
            options.update(encoding=choices['encoding'], line_ends=choices['line_ends'])
        if fmt in ('SUB', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890', 'AVID', 'FCPXML') + DVD_SCRIPTS:
            options['fps'] = choices['fps']
        if fmt in DVD_DROP_FRAME:
            options['timecode'] = choices['timecode']
        if fmt in ('AVID', 'FCPXML'):
            options.update(timecode=choices['timecode'], timeline_start=choices['timeline_start'])
        if fmt == 'CAVENA890':
            options['start'] = choices['cavena_start']
        if fmt == 'VOBSUB':
            options['vobsub_size'] = choices['vobsub_size']
        if fmt == 'PAC':
            options['pac_code_page'] = choices['pac_code_page']
        if fmt in ('ASS', 'DFXP', 'TTML'):
            options['look'] = choices['look']
        if fmt in ('DFXP', 'TTML'):
            options['profile'] = choices['profile']
            if choices['profile'] == 'smpte-tt':
                options['tunnel_608'] = choices['tunnel_608']
        if fmt in ('SCC', 'LAMBDACAP'):
            options['timecode'] = choices['timecode']
        if fmt in ('SCC', 'MCC'):
            options.update(caption_mode=choices['caption_mode'],
                           caption_channel=self.caption_channel_combo.combobox.currentData() or 1)
        if fmt == 'STL':
            options.update(stl_fps=choices['stl_fps'], display=choices['display'], start=choices['start'])
        if fmt == 'HLS':
            options.update(segment_duration=choices['segment_duration'], mpegts=choices['mpegts'])
        if fmt in ('CMFT', 'DASH'):
            options.update(segment_duration=choices['segment_duration'], mp4_text=choices['mp4_text'])
            if choices['mp4_text'] != 'wvtt':
                options['look'] = choices['look']
        if fmt == 'DCP':
            options.update(dcp_standard=choices['dcp_standard'], dcp_fps=choices['dcp_fps'],
                           dcp_content=choices['dcp_content'])
        if fmt == 'BDN':
            options['bdn_fps'] = choices['bdn_fps']
        if fmt == 'MCC':
            options.update(mcc_rate=choices['mcc_rate'], mcc_captions=choices['mcc_captions'],
                           caption_service_language=choices['caption_service_language']
                           if choices['mcc_captions'] != '608' else '')
        if fmt == 'USF':
            options.update(embed_speaker_images=choices['embed_speaker_images'],
                           embed_audio_clips=choices['embed_audio_clips'])
        return {'category': 'subtitles', 'format': fmt, 'options': options}


def _has_drop_frame(rate):
    """Whether timecode at `rate` can be drop-frame: 29.97 and 59.94."""
    return any(abs(rate - known) < 0.01 for known in (29.97, 59.94))


def _select(labeled_combo, data):
    """Select the item of `labeled_combo` holding `data`; whether there was one."""
    if data is None:
        return False
    for index in range(labeled_combo.combobox.count()):
        if labeled_combo.combobox.itemData(index) == data:
            labeled_combo.combobox.setCurrentIndex(index)
            return True
    return False


class _AudioPanel(QWidget):
    def __init__(self, parent=None, has_background=False, has_vocals=False, formats=None):
        super().__init__(parent)
        self._has_background = has_background
        self._has_vocals = has_vocals
        self._formats = formats or AUDIO_FORMATS
        self._allow_clips = 'MP4' not in self._formats

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(self._formats)
        self.layout().addWidget(self.format_combo)

        mode_label = QLabel(_('export_dialog.output_mode'))
        mode_label.setProperty('class', 'widget_label')
        self.layout().addWidget(mode_label)

        self.mode_group = QButtonGroup(self)
        self.mode_mixdown = QRadioButton(_('export_audio_dialog.mode_mixdown'))
        self.mode_stems = QRadioButton(_('export_audio_dialog.mode_stems'))
        self.mode_clips = QRadioButton(_('export_audio_dialog.mode_clips'))
        self.mode_background_only = QRadioButton(_('export_audio_dialog.mode_background_only'))
        self.mode_vocals_only = QRadioButton(_('export_audio_dialog.mode_vocals_only'))
        self.mode_mixdown.setChecked(True)
        for btn in (self.mode_mixdown, self.mode_stems, self.mode_clips,
                    self.mode_background_only, self.mode_vocals_only):
            self.mode_group.addButton(btn)
            self.layout().addWidget(btn)

        self.mode_clips.setEnabled(self._allow_clips)
        self.mode_background_only.setEnabled(has_background)
        if not has_background:
            self.mode_background_only.setToolTip(_('export_audio_dialog.background_unavailable'))
        self.mode_vocals_only.setEnabled(has_vocals)
        if not has_vocals:
            self.mode_vocals_only.setToolTip(_('export_audio_dialog.vocals_unavailable'))

        self.include_background_checkbox = QCheckBox(_('export_audio_dialog.include_background'))
        self.include_background_checkbox.setEnabled(has_background)
        if not has_background:
            self.include_background_checkbox.setToolTip(_('export_audio_dialog.background_unavailable'))
        self.layout().addWidget(self.include_background_checkbox)

        for btn in (self.mode_mixdown, self.mode_stems, self.mode_clips,
                    self.mode_background_only, self.mode_vocals_only):
            btn.toggled.connect(self._refresh_checkbox_state)

        # A shared project's dub clips still on their way from collaborators (modules/collab_service.py).
        from subtitld.modules import collab_service
        missing = collab_service.dubs_status()['missing']
        self.dubs_missing = QLabel(_('export_audio_dialog.dubs_missing').format(count=missing))
        self.dubs_missing.setWordWrap(True)
        self.dubs_missing.setProperty('class', 'widget_label')
        self.dubs_missing.setVisible(bool(missing))
        self.layout().addWidget(self.dubs_missing)

        self.layout().addStretch()

    def _refresh_checkbox_state(self):
        is_passthrough = self.mode_background_only.isChecked() or self.mode_vocals_only.isChecked()
        self.include_background_checkbox.setDisabled(is_passthrough or not self._has_background)

    def get_config(self):
        mode = 'mixdown'
        if self.mode_stems.isChecked():
            mode = 'stems'
        elif self.mode_clips.isChecked():
            mode = 'clips'
        elif self.mode_background_only.isChecked():
            mode = 'background_only'
        elif self.mode_vocals_only.isChecked():
            mode = 'vocals_only'
        return {
            'format': self.format_combo.currentText(),
            'audio_config': {
                'mode': mode,
                'include_background': (
                    self.include_background_checkbox.isChecked()
                    and self.include_background_checkbox.isEnabled()
                ),
            },
        }


class _DocumentsPanel(QWidget):
    """The documents tab: the format and, for CSV, which table to write
    (modules.csv_export)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)

        self.format_combo = utils.LabeledComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        self.format_combo.addItems(DOCUMENT_FORMATS)
        self.format_combo.combobox.currentIndexChanged.connect(self._refresh_options)
        self.layout().addWidget(self.format_combo)

        self.csv_type_combo = utils.LabeledComboBox()
        self.csv_type_combo.setLabel(_('export_dialog.csv_type'))
        for csv_type in csv_export.TYPES:
            self.csv_type_combo.combobox.addItem(_(f'export_dialog.csv_{csv_type}'), csv_type)
        self.layout().addWidget(self.csv_type_combo)

        self.note = QLabel(_('export_dialog.documents_placeholder'))
        self.note.setWordWrap(True)
        self.layout().addWidget(self.note)

        self.layout().addStretch()
        self._refresh_options()

    def _refresh_options(self, *_args):
        is_csv = self.format_combo.currentText() == 'CSV'
        self.csv_type_combo.setVisible(is_csv)
        self.note.setVisible(not is_csv)

    def get_config(self):
        fmt = self.format_combo.currentText()
        config = {
            'category': 'documents',
            'format': fmt,
        }
        if fmt == 'CSV':
            config['options'] = {'csv_type': self.csv_type_combo.combobox.currentData()}
        return config


class _VideoPanel(QWidget):
    """Video export panel.

    Lifted verbatim (in structure) from ``left_panel_export.load`` —
    the FFmpeg sub-tab and the standalone "generate transparent video"
    button. Signals are wired to local placeholder slots; the user will
    re-implement actual behaviour later.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(20)

        self.video_tabwidget = QTabWidget()

        # FFmpeg sub-tab ---------------------------------------------------
        self.ffmpeg_panel = QWidget()
        self.ffmpeg_panel.setLayout(QGridLayout())
        self.ffmpeg_panel.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_panel.layout().setSpacing(20)

        self.ffmpeg_left_panel = QWidget()
        self.ffmpeg_left_panel.setLayout(QVBoxLayout())
        self.ffmpeg_left_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_left_panel.layout().setSpacing(20)

        # Font group ------------------------------------------------------
        self.ffmpeg_font_group = QGroupBox(_('units.font'))
        self.ffmpeg_font_group.setLayout(QHBoxLayout())
        self.ffmpeg_font_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_font_group.layout().setSpacing(20)

        self.ffmpeg_fontsize_line = QVBoxLayout()
        self.ffmpeg_fontsize_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontsize_line.setSpacing(2)

        self.ffmpeg_fontsize_label = QLabel()
        self.ffmpeg_fontsize_label.setProperty('class', 'widget_label')
        self.ffmpeg_fontsize_line.addWidget(self.ffmpeg_fontsize_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_line_2 = QHBoxLayout()
        self.ffmpeg_fontsize_line_2.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontsize_line_2.setSpacing(5)

        self.ffmpeg_fontsize_spinbox = QSpinBox()
        self.ffmpeg_fontsize_spinbox.setMinimum(1)
        self.ffmpeg_fontsize_spinbox.setMaximum(999)
        self.ffmpeg_fontsize_spinbox.valueChanged.connect(self._fontsize_changed)
        self.ffmpeg_fontsize_line_2.addWidget(self.ffmpeg_fontsize_spinbox, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_seconds_label = QLabel()
        self.ffmpeg_fontsize_seconds_label.setProperty('class', 'units_label')
        self.ffmpeg_fontsize_line_2.addWidget(self.ffmpeg_fontsize_seconds_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontsize_line.addLayout(self.ffmpeg_fontsize_line_2)
        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_fontsize_line)

        self.ffmpeg_fontfamily_line = QVBoxLayout()
        self.ffmpeg_fontfamily_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontfamily_line.setSpacing(2)

        self.ffmpeg_fontfamily_label = QLabel()
        self.ffmpeg_fontfamily_label.setProperty('class', 'widget_label')
        self.ffmpeg_fontfamily_line.addWidget(self.ffmpeg_fontfamily_label, 0, Qt.AlignLeft)

        self.ffmpeg_fontfamily_line_2 = QHBoxLayout()
        self.ffmpeg_fontfamily_line_2.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_fontfamily_line_2.setSpacing(5)

        fonts = QFontDatabase().families()
        self.ffmpeg_fontfamily_combobox = QComboBox()
        self.ffmpeg_fontfamily_combobox.addItems(fonts)
        self.ffmpeg_fontfamily_combobox.activated.connect(self._fontfamily_changed)
        self.ffmpeg_fontfamily_line_2.addWidget(self.ffmpeg_fontfamily_combobox, 0, Qt.AlignLeft)

        self.ffmpeg_fontfamily_line.addLayout(self.ffmpeg_fontfamily_line_2)
        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_fontfamily_line)

        self.ffmpeg_color_vbox = QVBoxLayout()
        self.ffmpeg_color_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_color_vbox.setSpacing(2)

        self.ffmpeg_color_label = QLabel()
        self.ffmpeg_color_label.setProperty('class', 'widget_label')
        self.ffmpeg_color_vbox.addWidget(self.ffmpeg_color_label, 0, Qt.AlignLeft)

        self.ffmpeg_color_button = QPushButton()
        self.ffmpeg_color_button.setProperty('class', 'color_pick_button')
        self.ffmpeg_color_button.setFixedWidth(80)
        self.ffmpeg_color_button.clicked.connect(self._color_button_clicked)
        self.ffmpeg_color_vbox.addWidget(self.ffmpeg_color_button, 0, Qt.AlignLeft)

        self.ffmpeg_font_group.layout().addLayout(self.ffmpeg_color_vbox)
        self.ffmpeg_font_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_font_group)

        # Outline ---------------------------------------------------------
        self.ffmpeg_outline_group = QGroupBox()
        self.ffmpeg_outline_group.setCheckable(True)
        self.ffmpeg_outline_group.setLayout(QHBoxLayout())
        self.ffmpeg_outline_group.toggled.connect(self._outline_group_toggled)
        self.ffmpeg_outline_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_outline_group.layout().setSpacing(20)

        self.ffmpeg_outline_vbox = QVBoxLayout()
        self.ffmpeg_outline_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_outline_vbox.setSpacing(2)

        self.ffmpeg_outline_label = QLabel()
        self.ffmpeg_outline_label.setProperty('class', 'widget_label')
        self.ffmpeg_outline_vbox.addWidget(self.ffmpeg_outline_label, 0, Qt.AlignLeft)

        self.ffmpeg_outline_line = QHBoxLayout()
        self.ffmpeg_outline_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_outline_line.setSpacing(5)

        self.ffmpeg_outline_value = QSpinBox()
        self.ffmpeg_outline_value.setMinimum(0)
        self.ffmpeg_outline_value.setMaximum(99999)
        self.ffmpeg_outline_value.valueChanged.connect(self._outline_value_changed)
        self.ffmpeg_outline_line.addWidget(self.ffmpeg_outline_value, 0, Qt.AlignLeft)

        self.ffmpeg_outline_value_pixels_label = QLabel()
        self.ffmpeg_outline_value_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_outline_line.addWidget(self.ffmpeg_outline_value_pixels_label, 0, Qt.AlignLeft)

        self.ffmpeg_outline_vbox.addLayout(self.ffmpeg_outline_line)
        self.ffmpeg_outline_group.layout().addLayout(self.ffmpeg_outline_vbox)
        self.ffmpeg_outline_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_outline_group)

        # Shadow ----------------------------------------------------------
        self.ffmpeg_shadow_group = QGroupBox()
        self.ffmpeg_shadow_group.setCheckable(True)
        self.ffmpeg_shadow_group.setLayout(QHBoxLayout())
        self.ffmpeg_shadow_group.toggled.connect(self._shadow_group_toggled)
        self.ffmpeg_shadow_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_shadow_group.layout().setSpacing(20)

        self.ffmpeg_shadow_vbox = QVBoxLayout()
        self.ffmpeg_shadow_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_shadow_vbox.setSpacing(2)

        self.ffmpeg_shadow_label = QLabel()
        self.ffmpeg_shadow_label.setProperty('class', 'widget_label')
        self.ffmpeg_shadow_vbox.addWidget(self.ffmpeg_shadow_label, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_line = QHBoxLayout()
        self.ffmpeg_shadow_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_shadow_line.setSpacing(5)

        self.ffmpeg_shadow_distance = QSpinBox()
        self.ffmpeg_shadow_distance.setMinimum(-99999)
        self.ffmpeg_shadow_distance.setMaximum(99999)
        self.ffmpeg_shadow_distance.valueChanged.connect(self._shadow_distance_changed)
        self.ffmpeg_shadow_line.addWidget(self.ffmpeg_shadow_distance, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_distance_pixels_label = QLabel()
        self.ffmpeg_shadow_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_shadow_line.addWidget(self.ffmpeg_shadow_distance_pixels_label, 0, Qt.AlignLeft)

        self.ffmpeg_shadow_vbox.addLayout(self.ffmpeg_shadow_line)
        self.ffmpeg_shadow_group.layout().addLayout(self.ffmpeg_shadow_vbox)
        self.ffmpeg_shadow_group.layout().addStretch()
        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_shadow_group)

        # Margins ---------------------------------------------------------
        self.ffmpeg_margins_group = QGroupBox()
        self.ffmpeg_margins_group.setLayout(QGridLayout())
        self.ffmpeg_margins_group.layout().setContentsMargins(10, 10, 10, 10)
        self.ffmpeg_margins_group.layout().setSpacing(20)

        # Left
        self.ffmpeg_margins_left_vbox = QVBoxLayout()
        self.ffmpeg_margins_left_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_left_vbox.setSpacing(2)

        self.ffmpeg_margins_left_label = QLabel()
        self.ffmpeg_margins_left_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_left_vbox.addWidget(self.ffmpeg_margins_left_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_left_line = QHBoxLayout()
        self.ffmpeg_margins_left_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_left_line.setSpacing(5)

        self.ffmpeg_margins_left_distance = QSpinBox()
        self.ffmpeg_margins_left_distance.setMinimum(-99999)
        self.ffmpeg_margins_left_distance.setMaximum(99999)
        self.ffmpeg_margins_left_distance.valueChanged.connect(self._margins_left_changed)
        self.ffmpeg_margins_left_line.addWidget(self.ffmpeg_margins_left_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_left_distance_pixels_label = QLabel()
        self.ffmpeg_margins_left_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_left_line.addWidget(self.ffmpeg_margins_left_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_left_line.addStretch()

        self.ffmpeg_margins_left_vbox.addLayout(self.ffmpeg_margins_left_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_left_vbox, 1, 1, 1, 1)

        # Bottom
        self.ffmpeg_margins_bottom_vbox = QVBoxLayout()
        self.ffmpeg_margins_bottom_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_bottom_vbox.setSpacing(2)

        self.ffmpeg_margins_bottom_label = QLabel()
        self.ffmpeg_margins_bottom_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_bottom_vbox.addWidget(self.ffmpeg_margins_bottom_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_bottom_line = QHBoxLayout()
        self.ffmpeg_margins_bottom_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_bottom_line.setSpacing(5)

        self.ffmpeg_margins_bottom_distance = QSpinBox()
        self.ffmpeg_margins_bottom_distance.setMinimum(-99999)
        self.ffmpeg_margins_bottom_distance.setMaximum(99999)
        self.ffmpeg_margins_bottom_distance.valueChanged.connect(self._margins_bottom_changed)
        self.ffmpeg_margins_bottom_line.addWidget(self.ffmpeg_margins_bottom_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_bottom_distance_pixels_label = QLabel()
        self.ffmpeg_margins_bottom_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_bottom_line.addWidget(self.ffmpeg_margins_bottom_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_bottom_line.addStretch()

        self.ffmpeg_margins_bottom_vbox.addLayout(self.ffmpeg_margins_bottom_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_bottom_vbox, 2, 2, 1, 1)

        # Right
        self.ffmpeg_margins_right_vbox = QVBoxLayout()
        self.ffmpeg_margins_right_vbox.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_right_vbox.setSpacing(2)

        self.ffmpeg_margins_right_label = QLabel()
        self.ffmpeg_margins_right_label.setProperty('class', 'widget_label')
        self.ffmpeg_margins_right_vbox.addWidget(self.ffmpeg_margins_right_label, 0, Qt.AlignLeft)

        self.ffmpeg_margins_right_line = QHBoxLayout()
        self.ffmpeg_margins_right_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_margins_right_line.setSpacing(5)

        self.ffmpeg_margins_right_distance = QSpinBox()
        self.ffmpeg_margins_right_distance.setMinimum(-99999)
        self.ffmpeg_margins_right_distance.setMaximum(99999)
        self.ffmpeg_margins_right_distance.valueChanged.connect(self._margins_right_changed)
        self.ffmpeg_margins_right_line.addWidget(self.ffmpeg_margins_right_distance, 0, Qt.AlignLeft)

        self.ffmpeg_margins_right_distance_pixels_label = QLabel()
        self.ffmpeg_margins_right_distance_pixels_label.setProperty('class', 'units_label')
        self.ffmpeg_margins_right_line.addWidget(self.ffmpeg_margins_right_distance_pixels_label, 0, Qt.AlignLeft)
        self.ffmpeg_margins_right_line.addStretch()

        self.ffmpeg_margins_right_vbox.addLayout(self.ffmpeg_margins_right_line)
        self.ffmpeg_margins_group.layout().addLayout(self.ffmpeg_margins_right_vbox, 1, 3, 1, 1)

        self.ffmpeg_left_panel.layout().addWidget(self.ffmpeg_margins_group)

        # Final command + export button ----------------------------------
        self.ffmpeg_final_line = QHBoxLayout()
        self.ffmpeg_final_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_final_line.setSpacing(20)

        self.ffmpeg_command_line = QVBoxLayout()
        self.ffmpeg_command_line.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_command_line.setSpacing(5)

        self.ffmpeg_command_label = QLabel()
        self.ffmpeg_command_label.setProperty('class', 'widget_label')
        self.ffmpeg_command_line.addWidget(self.ffmpeg_command_label, 0, Qt.AlignLeft)

        self.ffmpeg_command_qlineedit = QLineEdit()
        self.ffmpeg_command_qlineedit.textEdited.connect(self._command_text_edited)
        self.ffmpeg_command_line.addWidget(self.ffmpeg_command_qlineedit)

        self.ffmpeg_final_line.addLayout(self.ffmpeg_command_line)

        self.ffmpeg_export_button = QPushButton()
        self.ffmpeg_export_button.setProperty('class', 'button_dark')
        self.ffmpeg_export_button.clicked.connect(self._export_button_clicked)
        self.ffmpeg_export_button.setSizePolicy(QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum))
        self.ffmpeg_final_line.addWidget(self.ffmpeg_export_button)

        self.ffmpeg_left_panel.layout().addLayout(self.ffmpeg_final_line)
        self.ffmpeg_left_panel.layout().addStretch()

        self.ffmpeg_panel.layout().addWidget(self.ffmpeg_left_panel, 1, 1, 1, 2)

        # Preview ---------------------------------------------------------
        self.ffmpeg_preview_panel = QWidget()
        self.ffmpeg_preview_panel.setLayout(QVBoxLayout())
        self.ffmpeg_preview_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_preview_panel.layout().setSpacing(20)

        self.ffmpeg_preview_label = QLabel()
        self.ffmpeg_preview_label.setProperty('class', 'widget_label')
        self.ffmpeg_preview_panel.layout().addWidget(self.ffmpeg_preview_label, 0)

        self.ffmpeg_preview_image = QLabel()
        self.ffmpeg_preview_image.setProperty('class', 'widget_label')
        self.ffmpeg_preview_image.setAlignment(Qt.AlignTop)
        self.ffmpeg_preview_panel.layout().addWidget(self.ffmpeg_preview_image, 1)

        self.ffmpeg_preview_panel.layout().addStretch()

        self.ffmpeg_panel.layout().addWidget(self.ffmpeg_preview_panel, 1, 3, 1, 1)

        self.video_tabwidget.addTab(self.ffmpeg_panel, 'FFMPEG')

        # Closed captions sub-tab ------------------------------------------
        # The captions go inside the file, as a broadcast master carries
        # them, rather than beside it: a QuickTime caption track, or the
        # ancillary data of an MXF (embedded_captions).
        self.captions_panel = QWidget()
        self.captions_panel.setLayout(QVBoxLayout())
        self.captions_panel.layout().setContentsMargins(10, 10, 10, 10)
        self.captions_panel.layout().setSpacing(10)
        remembered = session.CONFIG.get('export_video') or {}

        self.captions_format_combo = utils.LabeledComboBox()
        self.captions_format_combo.setLabel(_('export_dialog.format'))
        self.captions_format_combo.combobox.addItem(_('export_dialog.video_mov'), 'MOV')
        self.captions_format_combo.combobox.addItem(_('export_dialog.video_mxf'), 'MXF')
        self.captions_format_combo.combobox.addItem(_('export_dialog.video_mp4_a53'), 'MP4 A53')
        self.captions_format_combo.combobox.addItem(_('export_dialog.video_ts_a53'), 'TS A53')
        self.captions_format_combo.activated.connect(self._refresh_captions)
        self.captions_panel.layout().addWidget(self.captions_format_combo)

        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        self.captions_mode_combo = utils.LabeledComboBox()
        self.captions_mode_combo.setLabel(_('export_dialog.caption_mode'))
        for mode in ('pop', 'roll2', 'roll3', 'roll4', 'paint'):
            self.captions_mode_combo.combobox.addItem(_(f'export_dialog.caption_mode_{mode}'), mode)
        line.addWidget(self.captions_mode_combo, 1)
        self.captions_channel_combo = utils.LabeledComboBox()
        self.captions_channel_combo.setLabel(_('export_dialog.caption_channel'))
        for number in (1, 2, 3, 4):
            self.captions_channel_combo.combobox.addItem(f'CC{number}', number)
        line.addWidget(self.captions_channel_combo, 1)
        self.captions_codec_combo = utils.LabeledComboBox()
        self.captions_codec_combo.setLabel(_('export_dialog.video_codec'))
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_dnxhr'), 'dnxhr')
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_mpeg2'), 'mpeg2')
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_prores'), 'prores')
        line.addWidget(self.captions_codec_combo, 1)
        self.captions_panel.layout().addWidget(row)
        # An MXF's ancillary data carries teletext as well (OP-47).
        self.captions_teletext = QCheckBox(_('export_dialog.video_captions_teletext'))
        self.captions_panel.layout().addWidget(self.captions_teletext)

        self.captions_note = QLabel()
        self.captions_note.setWordWrap(True)
        self.captions_note.setProperty('class', 'widget_label')
        self.captions_panel.layout().addWidget(self.captions_note)
        self.captions_panel.layout().addStretch()

        _select(self.captions_format_combo, remembered.get('format', 'MOV'))
        _select(self.captions_mode_combo, remembered.get('caption_mode', 'pop'))
        _select(self.captions_channel_combo, remembered.get('caption_channel', 1))
        _select(self.captions_codec_combo, remembered.get('codec', 'dnxhr'))
        self.captions_teletext.setChecked(bool(remembered.get('teletext')))
        self._refresh_captions()
        self.video_tabwidget.addTab(self.captions_panel, _('export_dialog.category_captions'))

        # Subtitle track sub-tab -------------------------------------------
        # The subtitles as a track of their own, which a player lists and
        # turns on and off: MP4's timed text, Matroska's SRT or ASS, or a
        # transport stream's teletext or DVB subtitles (soft_subtitles,
        # teletext, dvbsub).
        self.tracks_panel = QWidget()
        self.tracks_panel.setLayout(QVBoxLayout())
        self.tracks_panel.layout().setContentsMargins(10, 10, 10, 10)
        self.tracks_panel.layout().setSpacing(10)

        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        self.tracks_format_combo = utils.LabeledComboBox()
        self.tracks_format_combo.setLabel(_('export_dialog.format'))
        self.tracks_format_combo.combobox.addItem(_('export_dialog.video_mp4_track'), 'MP4')
        self.tracks_format_combo.combobox.addItem(_('export_dialog.video_mov_track'), 'MOV TEXT')
        self.tracks_format_combo.combobox.addItem(_('export_dialog.video_mkv_track'), 'MKV')
        self.tracks_format_combo.combobox.addItem(_('export_dialog.video_ts_teletext'), 'TS')
        self.tracks_format_combo.combobox.addItem(_('export_dialog.video_ts_dvb'), 'TS DVB')
        self.tracks_format_combo.activated.connect(self._refresh_tracks)
        line.addWidget(self.tracks_format_combo, 2)
        self.tracks_codec_combo = utils.LabeledComboBox()
        self.tracks_codec_combo.setLabel(_('export_dialog.subtitle_codec'))
        self.tracks_codec_combo.combobox.addItem(_('export_dialog.subtitle_codec_srt'), 'srt')
        self.tracks_codec_combo.combobox.addItem(_('export_dialog.subtitle_codec_ass'), 'ass')
        self.tracks_codec_combo.activated.connect(self._refresh_tracks)
        line.addWidget(self.tracks_codec_combo, 1)
        self.tracks_panel.layout().addWidget(row)

        self.tracks_translations = QCheckBox(_('export_dialog.subtitle_tracks_translations'))
        self._has_translations = bool(export_options.translation_languages(session.SUBTITLE.get('segments')))
        self.tracks_translations.setVisible(self._has_translations)
        self.tracks_panel.layout().addWidget(self.tracks_translations)
        self.tracks_forced = QCheckBox(_('export_dialog.subtitle_track_forced'))
        self.tracks_panel.layout().addWidget(self.tracks_forced)

        self.tracks_note = QLabel()
        self.tracks_note.setWordWrap(True)
        self.tracks_note.setProperty('class', 'widget_label')
        self.tracks_panel.layout().addWidget(self.tracks_note)
        self.tracks_panel.layout().addStretch()

        soft = remembered.get('format') in soft_subtitles.CONTAINERS
        _select(self.tracks_format_combo, remembered.get('format') if soft else 'MP4')
        _select(self.tracks_codec_combo, remembered.get('subtitle_codec', 'srt'))
        self.tracks_translations.setChecked(bool(remembered.get('translations')))
        self.tracks_forced.setChecked(bool(remembered.get('forced')))
        self._refresh_tracks()
        self.video_tabwidget.addTab(self.tracks_panel, _('export_dialog.category_subtitle_track'))
        if soft:
            self.video_tabwidget.setCurrentWidget(self.tracks_panel)

        self.layout().addWidget(self.video_tabwidget, 0)

        # Standalone "generate transparent video" button -----------------
        self.generate_transparent_video_button = QPushButton()
        self.generate_transparent_video_button.setProperty('class', 'button_dark')
        self.generate_transparent_video_button.clicked.connect(self._generate_transparent_video_clicked)
        self.generate_transparent_video_button.setVisible(False)
        self.layout().addWidget(self.generate_transparent_video_button)

    # ------------------------------------------------------------------
    # Placeholder slots — user will rewire actual behaviour later.
    # ------------------------------------------------------------------
    def _fontsize_changed(self):
        pass

    def _fontfamily_changed(self):
        pass

    def _color_button_clicked(self):
        pass

    def _outline_group_toggled(self):
        pass

    def _outline_value_changed(self):
        pass

    def _shadow_group_toggled(self):
        pass

    def _shadow_distance_changed(self):
        pass

    def _margins_left_changed(self):
        pass

    def _margins_bottom_changed(self):
        pass

    def _margins_right_changed(self):
        pass

    def _command_text_edited(self):
        pass

    def _export_button_clicked(self):
        pass

    def _generate_transparent_video_clicked(self):
        pass

    def _update_preview(self):
        # Placeholder; user will reconnect to a worker thread later.
        pass

    def showEvent(self, event):
        self._update_preview()
        return super().showEvent(event)

    def _refresh_captions(self):
        """What the chosen container can hold: a QuickTime caption track
        carries CEA-608 and keeps the picture as it is; an MXF's ancillary
        data carries CEA-708 beside it, but takes a picture of its own; the
        video's own frames carry both, the picture copied."""
        container = self.captions_format_combo.combobox.currentData() or 'MOV'
        self.captions_codec_combo.setVisible(container == 'MXF')
        self.captions_teletext.setVisible(container == 'MXF')
        note = 'mxf' if container == 'MXF' else 'a53' if container.endswith('A53') else 'mov'
        self.captions_note.setText(_(f'export_dialog.video_captions_note_{note}'))

    def _refresh_tracks(self):
        """What the chosen kind of file does with the subtitles: an MP4's
        timed text keeps a run's formatting alone; Matroska takes SRT,
        which every player reads, or ASS, which keeps the look; a transport
        stream, teletext pages or DVB subtitles, pictures."""
        container = self.tracks_format_combo.combobox.currentData() or 'MP4'
        self.tracks_codec_combo.setVisible(container == 'MKV')
        # Teletext and DVB subtitles have no forced pages: a viewer chooses
        # one or none.
        self.tracks_forced.setVisible(container not in soft_subtitles.UNFORCED)
        if container == 'MKV':
            key = 'export_dialog.subtitle_track_note_' + (self.tracks_codec_combo.combobox.currentData() or 'srt')
        elif container == 'TS':
            key = 'export_dialog.subtitle_track_note_teletext'
        elif container == 'TS DVB':
            key = 'export_dialog.subtitle_track_note_dvb'
        else:
            key = 'export_dialog.subtitle_track_note_mov_text'
        self.tracks_note.setText(_(key))

    def get_config(self):
        if self.video_tabwidget.currentWidget() is self.tracks_panel:
            container = self.tracks_format_combo.combobox.currentData() or 'MP4'
            tracks = {
                'subtitle_codec': self.tracks_codec_combo.combobox.currentData() or 'srt',
                'translations': self._has_translations and self.tracks_translations.isChecked(),
                'forced': self.tracks_forced.isChecked(),
            }
            session.CONFIG['export_video'] = dict(session.CONFIG.get('export_video') or {}, **tracks, format=container)
            return {'category': 'video', 'format': container, 'captions': tracks}
        container = self.captions_format_combo.combobox.currentData() or 'MOV'
        captions = {
            'caption_mode': self.captions_mode_combo.combobox.currentData() or 'pop',
            'caption_channel': self.captions_channel_combo.combobox.currentData() or 1,
            'codec': self.captions_codec_combo.combobox.currentData() or 'dnxhr',
            'teletext': container == 'MXF' and self.captions_teletext.isChecked(),
        }
        session.CONFIG['export_video'] = dict(session.CONFIG.get('export_video') or {}, **captions, format=container)
        return {'category': 'video', 'format': container, 'captions': captions}


class ExportDialog(utils.SimpleDialog):
    """Universal export dialog. Returns a config dict via
    `exec_and_get_values()` or None when cancelled."""

    CATEGORY_KEYS = ['subtitles', 'audio', 'video', 'documents']

    def __init__(self, parent=None, has_background=False, has_vocals=False, initial_category=None):
        super().__init__(parent, _('export_dialog.title'))
        self.setMinimumSize(640, 380)

        self.content.layout().setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setLayout(QHBoxLayout())
        body.layout().setContentsMargins(0, 0, 0, 0)
        body.layout().setSpacing(0)
        self.content.layout().addWidget(body)

        sidebar = QWidget()
        sidebar.setObjectName('export_dialog_sidebar')
        sidebar.setLayout(QVBoxLayout())
        sidebar.layout().setContentsMargins(0, 10, 0, 10)
        sidebar.layout().setSpacing(0)
        sidebar.setFixedWidth(140)
        body.layout().addWidget(sidebar)

        self._category_buttons = {}
        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        for key in self.CATEGORY_KEYS:
            btn = QPushButton(_(f'export_dialog.category_{key}'))
            btn.setObjectName('export_dialog_category_button')
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            sidebar.layout().addWidget(btn)
            self._category_group.addButton(btn)
            self._category_buttons[key] = btn
        sidebar.layout().addStretch()

        right = QWidget()
        right.setObjectName('export_dialog_pages')
        right.setLayout(QVBoxLayout())
        right.layout().setContentsMargins(20, 16, 20, 16)
        body.layout().addWidget(right, 1)

        self._pages = QStackedWidget()
        right.layout().addWidget(self._pages)

        self.subtitles_panel = _SubtitlesPanel()
        self._pages.addWidget(self.subtitles_panel)

        self.audio_panel = _AudioPanel(has_background=has_background, has_vocals=has_vocals)
        self._pages.addWidget(self.audio_panel)

        self.video_panel = _VideoPanel()
        self._pages.addWidget(self.video_panel)

        self.documents_panel = _DocumentsPanel()
        self._pages.addWidget(self.documents_panel)

        for index, key in enumerate(self.CATEGORY_KEYS):
            self._category_buttons[key].clicked.connect(lambda _checked=False, i=index, k=key: self._select_category(i, k))

        start = initial_category if initial_category in self.CATEGORY_KEYS else 'subtitles'
        self._select_category(self.CATEGORY_KEYS.index(start), start)

        self.accept_button.setText(_('export_dialog.export_button'))

        # Remember the form-side widgets so `enter_processing_state` can
        # hide them in one shot, and re-show them if the caller ever
        # wants to revert (currently unused but cheap to support).
        self._form_body = body
        # `self.bottom_line`, not `accept_button.parent()` — the default
        # button now lives inside the footer's right-hand tab, so the parent
        # is the tab rather than the footer we mean to hide.
        self._form_buttons_parent = self.bottom_line
        self._processing_widget = None
        self._processing_label = None

    # ------------------------------------------------------------------
    # Processing-state UI
    #
    # The host (top_bar.toppanel_export_button_clicked) kicks the actual
    # export off on a background thread. While that thread runs we swap
    # the form for a small indeterminate-progress widget, leave the
    # dialog visible and application-modal, and wait for the host to
    # call `finish_processing()` from the thread's done-callback.
    #
    # This keeps the user oriented (the same dialog they clicked Export
    # in is now showing "Exporting...") instead of a frozen window. The
    # placeholder spinner is a vanilla `QProgressBar` in indeterminate
    # mode; when the user ships their custom animation later, drop it
    # in by replacing `_build_processing_widget` — the public API
    # (`enter_processing_state` / `finish_processing`) doesn't change.
    # ------------------------------------------------------------------
    def _build_processing_widget(self) -> QWidget:
        widget = QWidget()
        widget.setLayout(QVBoxLayout())
        widget.layout().setContentsMargins(40, 40, 40, 40)
        widget.layout().setSpacing(16)

        widget.layout().addStretch()

        label = QLabel('')
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setProperty('class', 'widget_label')
        widget.layout().addWidget(label, 0, Qt.AlignCenter)
        self._processing_label = label

        # min=0, max=0 puts the bar into Qt's indeterminate "barber pole"
        # mode — animated by Qt itself on the GUI thread. The export work
        # runs on a worker QThread so the GUI event loop stays free to
        # tick the animation.
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        progress.setMinimumWidth(280)
        widget.layout().addWidget(progress, 0, Qt.AlignCenter)

        widget.layout().addStretch()
        return widget

    def enter_processing_state(self, message: str) -> None:
        """Switch the dialog into a busy / processing display: hide the
        category form + the OK/Cancel buttons, show a centered status
        label + indeterminate progress bar. Idempotent — calling twice
        just updates the message. Application-modal so the user can't
        start a second export from the main window while this one runs.
        """
        if self._processing_widget is None:
            self._processing_widget = self._build_processing_widget()
            # Append into the same content layout as the body so the
            # processing widget inherits the dialog's content margins.
            self.content.layout().addWidget(self._processing_widget)

        self._processing_label.setText(message or '')
        self._processing_widget.setVisible(True)

        self._form_body.setVisible(False)
        if self._form_buttons_parent is not None:
            self._form_buttons_parent.setVisible(False)

        # The dialog was hidden by `accept()` when the user confirmed the
        # form. Re-show it (non-blocking) and mark it application-modal
        # so the rest of the app stops responding to clicks while the
        # export runs. We don't call `exec()` here because the caller
        # has already spawned the background thread and needs to keep
        # executing past this point.
        self.setWindowModality(Qt.ApplicationModal)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def finish_processing(self) -> None:
        """Tear down the processing state and close the dialog. Safe to
        call from any thread — Qt routes the close through the event
        loop via `QDialog.accept`."""
        # Use done() with a sentinel return code so any future caller
        # that wants to know whether the dialog closed via the
        # processing path can distinguish it from form Accept. For now
        # the return code isn't read.
        self.accept()

    def _select_category(self, index, key):
        self._pages.setCurrentIndex(index)
        self._category_buttons[key].setChecked(True)
        self.title_line.label.setText(f'<b>{_("export_dialog.title").upper()}</b>  <span style="opacity:0.6">{_(f"export_dialog.category_{key}")}</span>')

    def _current_category(self):
        for index, key in enumerate(self.CATEGORY_KEYS):
            if self._pages.currentIndex() == index:
                return key
        return 'subtitles'

    def exec_and_get_values(self):
        if self.exec() != QDialog.Accepted:
            return None
        category = self._current_category()
        if category == 'subtitles':
            return self.subtitles_panel.get_config()
        if category == 'audio':
            cfg = self.audio_panel.get_config()
            cfg['category'] = 'audio'
            return cfg
        if category == 'video':
            cfg = self.video_panel.get_config()
            cfg['category'] = 'video'
            return cfg
        return self.documents_panel.get_config()
