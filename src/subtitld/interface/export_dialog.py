"""Unified export dialog with four category tabs (Subtitles / Audio /
Video / Documents). Each tab exposes a format selector plus the
options that apply to that format. The dialog returns a single config
dict; the caller is responsible for the final file picker and dispatch."""

import copy
import os
import shutil
import tempfile

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
                               QPushButton, QButtonGroup, QRadioButton, QCheckBox,
                               QLabel, QSizePolicy, QDialog, QGridLayout,
                               QSpinBox, QDoubleSpinBox, QComboBox, QProgressBar, QPlainTextEdit)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QTextCharFormat, QPixmap, QPainter, QPainterPath, QColor, QImage

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import audio_report
from subtitld.modules import bdn
from subtitld.modules import burned_video
from subtitld.modules import csv_export
from subtitld.modules import dcp
from subtitld.modules import documents
from subtitld.modules import dvd_scripts
from subtitld.modules import edl
from subtitld.modules import export_options
from subtitld.modules import file_io
from subtitld.modules import hls
from subtitld.modules import isobmff_text
from subtitld.modules import markup
from subtitld.modules import json_dialects
from subtitld.modules import lrc
from subtitld.modules import mcc
from subtitld.modules import pac
from subtitld.modules import project_export
from subtitld.modules import session
from subtitld.modules import soft_subtitles
from subtitld.modules import subtitle_styles
from subtitld.modules import ttml
from subtitld.modules import vobsub
from subtitld.modules import xml_dialects
from subtitld.modules.segment import text_of, timed_pieces


SUBTITLE_FORMATS = ['SRT', 'VTT', 'HLS', 'CMFT', 'DASH', 'ISMT', 'ASS', 'DFXP', 'TTML', 'SAMI', 'SCC', 'MCC', 'STL', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890',
                    'EZTXML', 'AYATO', 'SOFTNI', 'SOFTNICOLON', 'SWIFTTEXT', 'SWIFTNUMBERS', 'SWIFTNUMBERSDURATION', 'XIF', 'SIF', 'CHEETAHASC', 'CAPMAKER',
                    'AVID', 'FCPXML', 'DCP', 'VOBSUB', 'PGS', 'DVDSUP', 'CDG', 'BDN', 'SCENARISTBMP', 'SON', 'DOST', 'SPUMUX',
                    'TIMEDIMAGES', 'FCPIMAGE', 'RHOZET', 'DVDSTUDIOPRO', 'ENCORE', 'DVDARCHITECT', 'SCENARIST', 'DVDPRODUCER', 'AVIDDVD', 'SBV', 'SUB',
                    'XML', 'XMLDIALECT', 'LRC', 'TTXT', 'KATE', 'DCPMXF', 'IMSCMXF', 'STLMXF', 'EDL', 'EDIUS', 'PREMIERE', 'DETX', 'USF', 'JSON', 'JSONDIALECT', 'BCC',
                    'TEXTGRID', 'EAF', 'TRS', 'EXB', 'CHAT', 'CTM', 'AUDACITY', 'AENEAS', 'TMX']
# The dialects of these two, one written at a time: which, an option.
DIALECTS = {'XMLDIALECT': (xml_dialects, 'xml_dialect'), 'JSONDIALECT': (json_dialects, 'json_dialect')}
# The formats an editor's timeline reads, frames counted at a rate.
EDITOR_LISTS = ('EDL', 'EDIUS', 'PREMIERE', 'DETX')
# The DVD authoring tools' scripts: frames, an encoding and line ends each;
# drop-frame for the two whose tools write it.
DVD_SCRIPTS = tuple(dvd_scripts.SESSION_FORMATS)
DVD_DROP_FRAME = ('DVDSTUDIOPRO', 'ENCORE')
# The subtitling houses' formats: frames each, at the rate chosen.
VENDOR_FORMATS = ('EZTXML', 'AYATO', 'SOFTNI', 'SOFTNICOLON', 'SWIFTTEXT', 'SWIFTNUMBERS', 'SWIFTNUMBERSDURATION', 'XIF', 'SIF', 'CHEETAHASC', 'CAPMAKER')
# A DVD's pictures, placed on DVD's PAL or NTSC frame or the video's.
DVD_PICTURES = ('VOBSUB', 'DVDSUP', 'SCENARISTBMP', 'SON', 'DOST', 'SPUMUX')
# Pictures and an index of them (modules.picture_indexes).
PICTURE_INDEXES = ('SCENARISTBMP', 'SON', 'DOST', 'SPUMUX', 'TIMEDIMAGES', 'FCPIMAGE', 'RHOZET')
AUDIO_FORMATS = ['WAV', 'FLAC', 'MP3']
VIDEO_FORMATS = ['MP4']


class _SubtitlesPanel(QWidget):
    """The subtitles tab: the format, and the options it takes. The formats
    of timed text share what text is written and how (export_options); SRT,
    SUB and USF have options of their own. The choices are remembered
    (CONFIG['export_subtitles']), except the shift, which is for one export."""

    # The format or one of its options changed: what the export writes did.
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        remembered = session.CONFIG.get('export_subtitles') or {}

        self.format_combo = utils.HeaderComboBox()
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
        for language in export_options.translation_languages(session.SUBTITLE.segments):
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

        # Subtitles timed word by word, in a format with no timing inside a
        # subtitle: whole, or their words revealed one after another.
        self.timed_combo = utils.LabeledComboBox()
        self.timed_combo.setLabel(_('export_dialog.timed'))
        self.timed_combo.combobox.addItem(_('export_dialog.timed_words'), 'words')
        self.timed_combo.combobox.addItem(_('export_dialog.timed_reveal'), 'reveal')
        grid.addWidget(self.timed_combo, 2, 1)
        self._has_timed = any(timed_pieces(segment) for segment in session.SUBTITLE.segments)

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
        # EDL: the subtitles as events, Avid's locators or Resolve's markers.
        self.edl_style_combo = utils.LabeledComboBox()
        self.edl_style_combo.setLabel(_('export_dialog.edl_style'))
        for style in edl.STYLES:
            self.edl_style_combo.combobox.addItem(_(f'export_dialog.edl_style_{style}'), style)
        row.addWidget(self.edl_style_combo, 1)
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
        for language in export_options.translation_languages(session.SUBTITLE.segments):
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

        # LRC: its moments in hundredths, thousandths, or with no ending ones.
        self.lrc_options = QWidget()
        row = QHBoxLayout(self.lrc_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.lrc_times_combo = utils.LabeledComboBox()
        self.lrc_times_combo.setLabel(_('export_dialog.lrc_times'))
        for dialect in lrc.DIALECTS:
            self.lrc_times_combo.combobox.addItem(_(f'export_dialog.lrc_times_{dialect}'), dialect)
        row.addWidget(self.lrc_times_combo, 1)
        row.addStretch()
        self.layout().addWidget(self.lrc_options)

        # CD+G: the song beside it as an MP3 by the same name (MP3+G).
        self.cdg_mp3_check = QCheckBox(_('export_dialog.cdg_mp3'))
        self.layout().addWidget(self.cdg_mp3_check)

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
                              ('smpte-tt', 'smptett'), ('ebu-tt', 'ebutt'), ('arib-ttml', 'aribttml'), ('dapt', 'dapt'),
                              ('imsc1-image', 'imsc1_image'), ('imsc1.1-image', 'imsc11_image')):
            self.profile_combo.combobox.addItem(_(f'export_dialog.ttml_profile_{name}'), profile)
        row.addWidget(self.profile_combo, 1)
        # TTML and DFXP: a service's published delivery rules, checked too.
        self.delivery_combo = utils.LabeledComboBox()
        self.delivery_combo.setLabel(_('export_dialog.ttml_delivery'))
        self.delivery_combo.combobox.addItem(_('export_dialog.ttml_delivery_none'), '')
        for delivery, name in ttml.DELIVERIES.items():
            self.delivery_combo.combobox.addItem(name, delivery)
        row.addWidget(self.delivery_combo, 1)
        self.layout().addWidget(self.ass_options)
        # XMLDIALECT and JSONDIALECT: which dialect to write.
        self.dialect_options = QWidget()
        row = QHBoxLayout(self.dialect_options)
        row.setContentsMargins(0, 0, 0, 0)
        self.dialect_combo = utils.LabeledComboBox()
        self.dialect_combo.setLabel(_('export_dialog.dialect'))
        row.addWidget(self.dialect_combo, 1)
        row.addStretch()
        self.layout().addWidget(self.dialect_options)
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
        self.delivery_combo.combobox.currentIndexChanged.connect(self._run_check)

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
        _select(self.timed_combo, remembered.get('timed', 'words'))
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
        _select(self.edl_style_combo, remembered.get('edl_style', 'events'))
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
        # The way the LRC opened writes its moments; else the last used.
        opened_options = session.FORMAT.get('options') if isinstance(session.FORMAT, dict) else None
        if not _select(self.lrc_times_combo, (opened_options or {}).get('lrc_times')):
            _select(self.lrc_times_combo, remembered.get('lrc_times', 'hundredths'))
        self.cdg_mp3_check.setChecked(bool(remembered.get('cdg_mp3', True)))
        _select(self.segment_combo, remembered.get('segment_duration', hls.SEGMENT_DURATION))
        _select(self.mpegts_combo, remembered.get('mpegts', hls.MPEGTS))
        _select(self.carriage_combo, remembered.get('mp4_text', 'wvtt'))
        _select(self.delivery_combo, remembered.get('delivery', ''))
        self._dialects_wanted = {key: remembered.get(key) for _module, key in DIALECTS.values()}
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
        # Any choice made changes the file: the combos (the format's among
        # them), the shift, the boxes.
        for combo in self.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self.changed)
        for spin in self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox):
            spin.valueChanged.connect(self.changed)
        for box in self.findChildren(QCheckBox):
            box.toggled.connect(self.changed)

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
        elif fmt in ('DFXP', 'TTML', 'CMFT', 'DASH', 'IMSCMXF'):
            combo.addItem(_('export_dialog.speaker_names_agent'), 'voice')
        elif fmt == 'SAMI':
            combo.addItem(_('export_dialog.speaker_names_source'), 'voice')
        elif fmt in ('SCC', 'MCC'):
            combo.addItem(_('export_dialog.speaker_names_chevrons'), 'voice')
        elif fmt == 'SBV':
            combo.addItem(_('export_dialog.speaker_names_youtube'), 'voice')
        elif fmt in ('TEXTGRID', 'EAF', 'EXB'):
            combo.addItem(_('export_dialog.speaker_names_tiers'), 'voice')
        elif fmt in ('TRS', 'CHAT'):
            combo.addItem(_('export_dialog.speaker_names_participants'), 'voice')
        elif fmt == 'LRC':
            combo.addItem(_('export_dialog.speaker_names_walaoke'), 'voice')
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
        self.sub_options.setVisible(fmt in ('SUB', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890', 'AVID', 'FCPXML') + DVD_SCRIPTS
                                    + VENDOR_FORMATS + tuple(DIALECTS) + EDITOR_LISTS)
        self._show_dialects(fmt)
        # SCC is always 29.97; the editors' formats count at the rate chosen.
        rate = float(self.fps_combo.combobox.currentData() or 0)
        self.scc_options.setVisible(fmt == 'SCC' or (fmt in ('AVID', 'FCPXML', 'LAMBDACAP', 'EDL', 'PREMIERE') + DVD_DROP_FRAME
                                                     and _has_drop_frame(rate)))
        self.timeline_options.setVisible(fmt in ('AVID', 'FCPXML', 'EDL'))
        self.edl_style_combo.setVisible(fmt == 'EDL')
        self.cavena_options.setVisible(fmt == 'CAVENA890')
        self.lrc_options.setVisible(fmt == 'LRC')
        self.cdg_mp3_check.setVisible(fmt == 'CDG' and bool(session.VIDEO.get('filepath')))
        self.vobsub_options.setVisible(fmt in DVD_PICTURES)
        self.pac_options.setVisible(fmt == 'PAC')
        self.stl_options.setVisible(fmt in ('STL', 'STLMXF'))
        self.stream_options.setVisible(fmt in ('HLS', 'CMFT', 'DASH', 'ISMT'))
        self.mpegts_combo.setVisible(fmt == 'HLS')
        self.carriage_combo.setVisible(fmt in ('CMFT', 'DASH'))
        stpp = fmt in ('CMFT', 'DASH') and self.carriage_combo.combobox.currentData() != 'wvtt'
        self.dcp_options.setVisible(fmt in ('DCP', 'DCPMXF'))
        # A track file is SMPTE's alone (ST 429-5).
        self.dcp_standard_combo.setVisible(fmt == 'DCP')
        self.dcp_rate_combo.setVisible(fmt == 'DCPMXF' or self.dcp_standard_combo.combobox.currentData() == dcp.SMPTE)
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
                                                     'DVDPRODUCER', 'TEXTGRID', 'EAF', 'TRS', 'EXB', 'CHAT', 'CTM',
                                                     'AUDACITY', 'AENEAS', 'TMX'))
        # Karaoke formats and WebVTT keep the timing as it is.
        self.timed_combo.setVisible(self._has_timed and fmt not in export_options.KARAOKE_FORMATS
                                    + export_options.INLINE_TIMED_FORMATS)
        self.ass_options.setVisible(fmt in ('ASS', 'DFXP', 'TTML', 'IMSCMXF') or stpp)
        # TTML has no shadow to give.
        self.look_combo.combobox.setItemText(1, _('export_dialog.ass_look_preview') if fmt == 'ASS'
                                             else _('export_dialog.ttml_look_preview'))
        # Only TTML and DFXP have a profile to declare.
        self.profile_combo.setVisible(fmt in ('DFXP', 'TTML'))
        self.delivery_combo.setVisible(fmt in ('DFXP', 'TTML'))
        self._show_tunnel()
        self.usf_options.setVisible(fmt == 'USF')
        self._run_check()

    def _show_dialects(self, fmt):
        """The dialects `fmt` writes: the one the file opened was in, else
        the last chosen, else the first."""
        self.dialect_options.setVisible(fmt in DIALECTS)
        if fmt not in DIALECTS:
            return
        module, key = DIALECTS[fmt]
        combo = self.dialect_combo.combobox
        if [combo.itemData(index) for index in range(combo.count())] != list(module.WRITABLE):
            combo.clear()
            for name in module.WRITABLE:
                combo.addItem(module.DIALECTS[name], name)
        opened = (session.FORMAT.get('options') or {}).get(key) if isinstance(session.FORMAT, dict) else None
        for wanted in (opened, getattr(self, '_dialects_wanted', {}).get(key)):
            if _select(self.dialect_combo, wanted):
                break

    def _show_tunnel(self, *_args):
        """The CEA-608 tunnel is SMPTE-TT's alone."""
        self.tunnel_608_check.setVisible(self.format_combo.currentText() in ('DFXP', 'TTML')
                                         and self.profile_combo.combobox.currentData() == 'smpte-tt')

    def _run_check(self, *_args):
        """What the TTML this export writes would break in its profile:
        the file written as it will be, then read back by ttml.check."""
        fmt = self.format_combo.currentText()
        segments = session.SUBTITLE.segments
        if fmt == 'CDG':
            # Drawn with QPainter, which every build has.
            self.check_label.setText(_('export_dialog.cdg_note'))
            self.check_label.setVisible(True)
            return
        if fmt in ('BDN', 'PGS') + DVD_PICTURES + PICTURE_INDEXES or (fmt in ('DCP', 'DCPMXF') and self.dcp_content_combo.combobox.currentData() == 'pictures'):
            # Pictures: the preview shows one, nothing to say here.
            self.check_label.setVisible(False)
            return
        if fmt not in ('DFXP', 'TTML', 'IMSCMXF') or not segments:
            self.check_label.setVisible(False)
            return
        # IMF's track file holds IMSC 1.1, whatever the TTML profile chosen.
        profile = 'imsc1.1' if fmt == 'IMSCMXF' else self.profile_combo.combobox.currentData() or 'ttml1'
        if profile in ttml.IMAGE_PROFILES:
            # Pictures are drawn when the file is written, not to check it
            # here; the preview shows one.
            self.check_label.setVisible(False)
            return
        written = ttml.write(segments, session.SUBTITLE.language or 'en', profile=profile,
                             metadata=session.SUBTITLE.metadata)
        delivery = self.delivery_combo.combobox.currentData() or None
        problems = ttml.check(written, profile, delivery=delivery)
        name = _('export_dialog.ttml_profile_imsc11') if fmt == 'IMSCMXF' else self.profile_combo.combobox.currentText()
        if delivery:
            name = f'{name}, {ttml.DELIVERIES[delivery]}'
        if problems:
            self.check_label.setText('\n'.join([_('export_dialog.ttml_check_found').format(profile=name)]
                                               + [f'\u2022 {problem}' for problem in problems]))
        else:
            self.check_label.setText(_('export_dialog.ttml_check_clean').format(profile=name))
        self.check_label.setVisible(True)

    def get_config(self):
        fmt, choices, options = self.export_choices()
        # The speaker choice as last made: voice tags stay wanted for WebVTT
        # even when another format wrote names before the text.
        if fmt in DIALECTS:
            self._dialects_wanted[DIALECTS[fmt][1]] = self.dialect_combo.combobox.currentData()
        session.CONFIG['export_subtitles'] = dict(choices, format=fmt, speakers=self._speakers_wanted,
                                                  **{key: value for key, value in self._dialects_wanted.items() if value})
        return {'category': 'subtitles', 'format': fmt, 'options': options}

    def export_choices(self):
        """The format, every choice made and the options the export takes
        of them, remembering nothing: the preview asks for them too."""
        fmt = self.format_combo.currentText()
        text, language = self.text_combo.combobox.currentData() or ('original', '')
        choices = {
            'text': text,
            'language': language,
            'speakers': self.speakers_combo.combobox.currentData(),
            'formatting': self.formatting_combo.combobox.currentData(),
            'timed': self.timed_combo.combobox.currentData(),
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
            'edl_style': self.edl_style_combo.combobox.currentData(),
            'dcp_standard': self.dcp_standard_combo.combobox.currentData(),
            'dcp_fps': self.dcp_rate_combo.combobox.currentData(),
            'dcp_content': self.dcp_content_combo.combobox.currentData(),
            'bdn_fps': self.bdn_rate_combo.combobox.currentData(),
            'cavena_start': self.cavena_start_combo.combobox.currentData(),
            'lrc_times': self.lrc_times_combo.combobox.currentData(),
            'cdg_mp3': self.cdg_mp3_check.isChecked(),
            'segment_duration': self.segment_combo.combobox.currentData(),
            'mpegts': self.mpegts_combo.combobox.currentData(),
            'mp4_text': self.carriage_combo.combobox.currentData(),
            'delivery': self.delivery_combo.combobox.currentData() or '',
            'vobsub_size': self.vobsub_size_combo.combobox.currentData(),
            'pac_code_page': self.pac_code_page_combo.combobox.currentData(),
            'embed_speaker_images': self.usf_embed_images.isChecked(),
            'embed_audio_clips': self.usf_embed_dubs.isChecked(),
        }

        # Every export sets its options, so none is left over from the last.
        options = {}
        if fmt in export_options.TEXT_FORMATS:
            options.update({key: choices[key] for key in ('text', 'language', 'speakers', 'formatting', 'timed')})
            options['offset'] = self.offset_spin.value()
        if fmt in ('SRT', 'SAMI', 'SUB', 'LRC', 'AVID') + DVD_SCRIPTS:
            options.update(encoding=choices['encoding'], line_ends=choices['line_ends'])
        if fmt in ('SUB', 'PAC', 'FPC', 'CAP', 'LAMBDACAP', 'CAVENA890', 'AVID', 'FCPXML') + DVD_SCRIPTS + VENDOR_FORMATS + tuple(DIALECTS) + EDITOR_LISTS:
            options['fps'] = choices['fps']
        if fmt in DIALECTS:
            options[DIALECTS[fmt][1]] = self.dialect_combo.combobox.currentData()
        if fmt == 'ISMT':
            options['segment_duration'] = choices['segment_duration']
        if fmt == 'EDL':
            options.update(timecode=choices['timecode'], timeline_start=choices['timeline_start'],
                           edl_style=choices['edl_style'])
        if fmt == 'PREMIERE':
            options['timecode'] = choices['timecode']
        if fmt in DVD_DROP_FRAME:
            options['timecode'] = choices['timecode']
        if fmt in ('AVID', 'FCPXML'):
            options.update(timecode=choices['timecode'], timeline_start=choices['timeline_start'])
        if fmt == 'CAVENA890':
            options['start'] = choices['cavena_start']
        if fmt == 'LRC':
            options['lrc_times'] = choices['lrc_times']
        if fmt == 'CDG':
            options['cdg_mp3'] = choices['cdg_mp3'] and bool(session.VIDEO.get('filepath'))
        if fmt in DVD_PICTURES:
            options['vobsub_size'] = choices['vobsub_size']
        if fmt == 'PAC':
            options['pac_code_page'] = choices['pac_code_page']
        if fmt in ('ASS', 'DFXP', 'TTML', 'IMSCMXF'):
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
        if fmt in ('STL', 'STLMXF'):
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
        if fmt == 'DCPMXF':
            options.update(dcp_fps=choices['dcp_fps'], dcp_content=choices['dcp_content'])
        if fmt == 'BDN':
            options['bdn_fps'] = choices['bdn_fps']
        if fmt == 'MCC':
            options.update(mcc_rate=choices['mcc_rate'], mcc_captions=choices['mcc_captions'],
                           caption_service_language=choices['caption_service_language']
                           if choices['mcc_captions'] != '608' else '')
        if fmt == 'USF':
            options.update(embed_speaker_images=choices['embed_speaker_images'],
                           embed_audio_clips=choices['embed_audio_clips'])
        return fmt, choices, options


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
    changed = Signal()                                         # a choice the export is made with

    def __init__(self, parent=None, has_background=False, has_vocals=False, formats=None):
        super().__init__(parent)
        self._has_background = has_background
        self._has_vocals = has_vocals
        self._formats = formats or AUDIO_FORMATS
        self._allow_clips = 'MP4' not in self._formats

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.format_combo = utils.HeaderComboBox()
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
            btn.toggled.connect(self.changed)
        self.format_combo.combobox.currentIndexChanged.connect(self.changed)
        self.include_background_checkbox.toggled.connect(self.changed)

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

    def export_choices(self):
        """(format, mode, whether the background is mixed in), as chosen."""
        config = self.get_config()
        return config['format'], config['audio_config']['mode'], config['audio_config']['include_background']

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
    """The documents tab: the format, then what the document holds, of what
    that format can (modules.documents), then the options that takes."""
    changed = Signal()                                         # a choice the document is written with

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        remembered = session.CONFIG.get('export_documents') or {}

        self.format_combo = utils.HeaderComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        for fmt in documents.FORMATS:
            self.format_combo.combobox.addItem(_(f'export_dialog.document_{fmt.lower()}'), fmt)
        self.format_combo.activated.connect(self._refresh_format)
        self.layout().addWidget(self.format_combo)

        self.content_combo = utils.LabeledComboBox()
        self.content_combo.setLabel(_('export_dialog.document_content'))
        self.content_combo.activated.connect(self._refresh_content)
        self.layout().addWidget(self.content_combo)
        # What each format was last made to hold.
        self._contents = dict(remembered.get('contents') or {})

        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        self.times_combo = utils.LabeledComboBox()
        self.times_combo.setLabel(_('export_dialog.document_times'))
        for times in documents.TIMES:
            self.times_combo.combobox.addItem(_(f'export_dialog.document_times_{times}'), times)
        self.times_combo.activated.connect(self._refresh_content)
        line.addWidget(self.times_combo, 1)
        self.time_format_combo = utils.LabeledComboBox()
        self.time_format_combo.setLabel(_('export_dialog.document_time_format'))
        for time_format in documents.TIME_FORMATS:
            self.time_format_combo.combobox.addItem(_(f'export_dialog.document_time_{time_format}'), time_format)
        line.addWidget(self.time_format_combo, 1)
        self.layout().addWidget(row)

        # The translations a sheet has columns for: all, or one.
        self.languages_combo = utils.LabeledComboBox()
        self.languages_combo.setLabel(_('export_dialog.document_languages'))
        self.languages_combo.combobox.addItem(_('export_dialog.document_languages_all'), '')
        names = {code: name for name, code in session.LANGUAGE_DICT_LIST.items()}
        for language in documents.languages_of(session.SUBTITLE.segments):
            self.languages_combo.combobox.addItem(names.get(language, language), language)
        self.layout().addWidget(self.languages_combo)

        self.speakers_check = QCheckBox(_('export_dialog.document_speakers'))
        self.layout().addWidget(self.speakers_check)
        self.by_speaker_check = QCheckBox(_('export_dialog.document_by_speaker'))
        self.layout().addWidget(self.by_speaker_check)
        self.pauses_check = QCheckBox(_('export_dialog.document_pauses'))
        self.layout().addWidget(self.pauses_check)
        self.notes_check = QCheckBox(_('export_dialog.document_notes'))
        self.layout().addWidget(self.notes_check)
        self.title_check = QCheckBox(_('export_dialog.document_title'))
        self.layout().addWidget(self.title_check)

        self.layout().addStretch()

        _select(self.format_combo, remembered.get('format'))
        _select(self.times_combo, remembered.get('times', documents.DEFAULTS['times']))
        _select(self.time_format_combo, remembered.get('time_format', documents.DEFAULTS['time_format']))
        _select(self.languages_combo, remembered.get('language', ''))
        self.speakers_check.setChecked(bool(remembered.get('speakers', documents.DEFAULTS['speakers'])))
        self.by_speaker_check.setChecked(bool(remembered.get('by_speaker', documents.DEFAULTS['by_speaker'])))
        self.pauses_check.setChecked(bool(remembered.get('pauses', documents.DEFAULTS['pauses'])))
        self.notes_check.setChecked(bool(remembered.get('notes', documents.DEFAULTS['notes'])))
        self.title_check.setChecked(bool(remembered.get('title', documents.DEFAULTS['title'])))
        for combo in (self.format_combo, self.content_combo, self.times_combo, self.time_format_combo,
                      self.languages_combo):
            combo.combobox.currentIndexChanged.connect(self.changed)
        for check in (self.speakers_check, self.by_speaker_check, self.pauses_check, self.notes_check,
                      self.title_check):
            check.toggled.connect(self.changed)
        self._refresh_format()

    def choose(self, fmt, content=None):
        """Select `fmt` and, if it can hold it, `content`."""
        _select(self.format_combo, fmt)
        if content:
            self._contents[fmt] = content
        self._refresh_format()

    def _format(self):
        return self.format_combo.combobox.currentData() or next(iter(documents.FORMATS))

    def _content(self):
        return documents.content_of(self._format(), self.content_combo.combobox.currentData())

    def _refresh_format(self, *_args):
        """The contents the format can hold, the one it held last chosen."""
        fmt = self._format()
        combo = self.content_combo.combobox
        combo.blockSignals(True)
        combo.clear()
        for content in documents.contents_of(fmt):
            combo.addItem(_(f'export_dialog.document_content_{content}'), content)
        _select(self.content_combo, documents.content_of(fmt, self._contents.get(fmt)))
        combo.blockSignals(False)
        self._refresh_content()
        self.changed.emit()

    def _refresh_content(self, *_args):
        """The options the chosen content takes in the chosen format."""
        fmt, content = self._format(), self._content()
        self._contents[fmt] = content
        timed = content in (documents.TRANSCRIPT, documents.NUMBERED, documents.SCRIPT, documents.TRANSLATION)
        self.times_combo.setVisible(timed)
        self.time_format_combo.setVisible(content in (documents.SPOTTING, documents.DUBBING, documents.SPEAKERS) or (
            timed and self.times_combo.combobox.currentData() != 'none'))
        self.languages_combo.setVisible(content == documents.TRANSLATION and self.languages_combo.combobox.count() > 2)
        self.speakers_check.setVisible(content in (documents.TRANSCRIPT, documents.NUMBERED, documents.SCRIPT))
        self.by_speaker_check.setVisible(content == documents.DUBBING)
        self.pauses_check.setVisible(content == documents.NUMBERED)
        # A column for notes: on paper, to be written in, or in a sheet.
        self.notes_check.setVisible(content in (documents.SCRIPT, documents.TRANSLATION, documents.DUBBING)
                                    and fmt != 'TXT')
        self.title_check.setVisible(fmt != 'CSV' and content != documents.MARKERS)

    def export_choices(self):
        """(format, what the document holds and how), as chosen."""
        language = self.languages_combo.combobox.currentData()
        return self._format(), {
            'content': self._content(),
            'times': self.times_combo.combobox.currentData(),
            'time_format': self.time_format_combo.combobox.currentData(),
            'languages': [language] if language else None,
            'speakers': self.speakers_check.isChecked(),
            'by_speaker': self.by_speaker_check.isChecked(),
            'pauses': self.pauses_check.isChecked(),
            'notes': self.notes_check.isChecked(),
            'title': self.title_check.isChecked(),
        }

    def get_config(self):
        fmt, chosen = self.export_choices()
        remembered = {key: value for key, value in chosen.items() if key not in ('content', 'languages')}
        remembered['language'] = self.languages_combo.combobox.currentData() or ''
        session.CONFIG['export_documents'] = dict(remembered, format=fmt, contents=dict(self._contents))
        return {'category': 'documents', 'format': fmt, 'options': {'document': chosen}}


class _ProjectPanel(QWidget):
    """The project tab: the program, then what goes on its timeline
    (modules.project_export): the video, its sound or the background
    separated from the voices, the dubbed speeches, the subtitles and
    their translations; each offered when there is one."""
    changed = Signal()                                         # a choice the project is written with

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        remembered = session.CONFIG.get('export_project') or {}
        segments = session.SUBTITLE.segments
        has_video = bool((session.VIDEO or {}).get('filepath'))

        self.format_combo = utils.HeaderComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        for fmt in project_export.FORMATS:
            self.format_combo.combobox.addItem(project_logo(fmt), _(f'export_dialog.project_{fmt.lower()}'), fmt)
        self.layout().addWidget(self.format_combo)

        self.audio_combo = utils.LabeledComboBox()
        self.audio_combo.setLabel(_('export_dialog.project_audio'))
        self.audio_combo.combobox.addItem(_('export_dialog.project_audio_original'), 'original')
        if project_export.background_path(session.VIDEO):
            self.audio_combo.combobox.addItem(_('export_dialog.project_audio_background'), 'background')
        self.audio_combo.combobox.addItem(_('export_dialog.project_audio_none'), 'none')
        self.audio_combo.setVisible(has_video)
        self.layout().addWidget(self.audio_combo)

        self.video_check = QCheckBox(_('export_dialog.project_video'))
        self.video_check.setVisible(has_video)
        self.layout().addWidget(self.video_check)
        self.speeches_check = QCheckBox(_('export_dialog.project_speeches'))
        self.speeches_check.setVisible(any(s.get('dubbing') for s in segments))
        self.layout().addWidget(self.speeches_check)
        self.subtitles_check = QCheckBox(_('export_dialog.project_subtitles'))
        self.layout().addWidget(self.subtitles_check)
        self.translations_check = QCheckBox(_('export_dialog.project_translations'))
        self._has_translations = bool(documents.languages_of(segments))
        self.layout().addWidget(self.translations_check)
        self.layout().addStretch()

        _select(self.format_combo, remembered.get('format'))
        _select(self.audio_combo, remembered.get('audio', project_export.DEFAULTS['audio']))
        for key, check in (('video', self.video_check), ('speeches', self.speeches_check),
                           ('subtitles', self.subtitles_check), ('translations', self.translations_check)):
            check.setChecked(bool(remembered.get(key, project_export.DEFAULTS[key])))
        self.subtitles_check.toggled.connect(self._refresh)
        self.format_combo.combobox.currentIndexChanged.connect(self._refresh)
        self._has_video = has_video
        for combo in (self.format_combo, self.audio_combo):
            combo.combobox.currentIndexChanged.connect(self.changed)
        for check in (self.video_check, self.speeches_check, self.subtitles_check, self.translations_check):
            check.toggled.connect(self.changed)
        self._refresh()

    def _refresh(self, *_args):
        # An audio editor takes the sound alone; a program with room for one
        # subtitle track, the subtitles without their translations.
        fmt = self.format_combo.combobox.currentData()
        self.translations_check.setVisible(self._has_translations and self.subtitles_check.isChecked() and
                                           not project_export.FORMATS.get(fmt, {}).get('one_subtitle_track'))
        self.video_check.setVisible(self._has_video and not project_export.FORMATS.get(fmt, {}).get('audio_only'))

    def choose(self, fmt):
        _select(self.format_combo, fmt)

    def export_choices(self):
        """(program, what goes in), as chosen; what is not offered left out."""
        choices = {'audio': self.audio_combo.combobox.currentData() if not self.audio_combo.isHidden() else 'none',
                   'video': self.video_check.isChecked() and not self.video_check.isHidden(),
                   'speeches': self.speeches_check.isChecked() and not self.speeches_check.isHidden(),
                   'subtitles': self.subtitles_check.isChecked(),
                   'translations': self.translations_check.isChecked() and not self.translations_check.isHidden()}
        return self.format_combo.combobox.currentData(), choices

    def get_config(self):
        fmt, choices = self.export_choices()
        remembered = {'format': fmt, 'audio': self.audio_combo.combobox.currentData()}
        for key, check in (('video', self.video_check), ('speeches', self.speeches_check),
                           ('subtitles', self.subtitles_check), ('translations', self.translations_check)):
            remembered[key] = check.isChecked()
        session.CONFIG['export_project'] = remembered
        return {'category': 'project', 'format': fmt, 'options': {'project': choices}}


# The video page's formats, as its header lists them: the ways the subtitles
# can go in each, and the export each way makes (a transport stream's track
# is teletext, 'TS', DVB subtitles, 'TS DVB', ARIB captions, 'TS ARIB', or
# SCTE 27 subtitles, 'TS SCTE27', as its track format says).
TS_TRACKS = ('TS', 'TS DVB', 'TS ARIB', 'TS SCTE27')
VIDEO_FILES = {
    'MP4': {'label': 'video_mp4_track', 'ways': {'cc': 'MP4 A53', 'track': 'MP4', 'burn': 'BURN MP4'}},
    'MOV': {'label': 'video_mov', 'ways': {'cc': 'MOV', 'track': 'MOV TEXT', 'burn': 'BURN MOV', 'alpha': 'ALPHA MOV'}},
    'MKV': {'label': 'video_mkv_track', 'ways': {'track': 'MKV', 'burn': 'BURN MKV', 'alpha': 'ALPHA MKV'}},
    'MXF': {'label': 'video_mxf', 'ways': {'cc': 'MXF'}},
    'TS': {'label': 'video_ts_track', 'ways': {'cc': 'TS A53', 'track': 'TS', 'burn': 'BURN TS'}},
    'WEBM': {'label': 'video_webm', 'ways': {'burn': 'BURN WEBM', 'alpha': 'ALPHA WEBM'}},
    'AVI': {'label': 'video_avi_xsub', 'ways': {'track': 'AVI XSUB'}},
}


def _video_file_of(export):
    """(format, way) of the video page that make `export`; None for none."""
    export = 'TS' if export in TS_TRACKS else export
    for video_file, spec in VIDEO_FILES.items():
        for way, key in spec['ways'].items():
            if key == export:
                return video_file, way
    return None


def _codec_label(codec):
    """A picture codec's name as the list shows it."""
    names = {'h264': 'H.264', 'hevc': 'HEVC', 'vp9': 'VP9', 'prores': 'ProRes 422 HQ', 'prores4444': 'ProRes 4444',
             'png': 'PNG', 'qtrle': 'QuickTime Animation'}
    return names.get(codec) or _(f'export_dialog.video_codec_{codec}')


class _VideoPanel(QWidget):
    """The video tab: the kind of file, and the options it takes. The
    subtitles go in as closed captions, as a track of their own, burned into
    the picture, or drawn alone on a clear video."""
    changed = Signal()                                         # a choice the export is made with

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)

        # The file's format, the page's header; then how the subtitles go in
        # it, of the ways that format has; then the options that way takes.
        remembered = session.CONFIG.get('export_video') or {}
        self.format_combo = utils.HeaderComboBox()
        self.format_combo.setLabel(_('export_dialog.format'))
        for key, spec in VIDEO_FILES.items():
            self.format_combo.combobox.addItem(_(f'export_dialog.{spec["label"]}'), key)
        self.format_combo.activated.connect(self._refresh_format)
        self.layout().addWidget(self.format_combo)

        self.way_combo = utils.LabeledComboBox()
        self.way_combo.setLabel(_('export_dialog.video_subtitles'))
        self.way_combo.activated.connect(self._refresh_way)
        self.layout().addWidget(self.way_combo)
        # The way chosen for each format, and the codec for each export, so
        # going to another and back keeps them.
        self._ways = dict(remembered.get('ways') or {})
        self._codecs = dict(remembered.get('codecs') or {})

        self.video_pages = QStackedWidget()


        # Closed captions sub-tab ------------------------------------------
        # The captions go inside the file, as a broadcast master carries
        # them, rather than beside it: a QuickTime caption track, or the
        # ancillary data of an MXF (embedded_captions).
        self.captions_panel = QWidget()
        self.captions_panel.setLayout(QVBoxLayout())
        self.captions_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.captions_panel.layout().setSpacing(10)

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
        self.captions_codec_combo.setLabel(_('export_dialog.codec'))
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_dnxhr'), 'dnxhr')
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_mpeg2'), 'mpeg2')
        self.captions_codec_combo.combobox.addItem(_('export_dialog.video_codec_prores'), 'prores')
        line.addWidget(self.captions_codec_combo, 1)
        self.captions_panel.layout().addWidget(row)
        # An MXF's ancillary data carries teletext as well (OP-47).
        self.captions_teletext = QCheckBox(_('export_dialog.video_captions_teletext'))
        self.captions_panel.layout().addWidget(self.captions_teletext)

        self.captions_panel.layout().addStretch()

        _select(self.captions_mode_combo, remembered.get('caption_mode', 'pop'))
        _select(self.captions_channel_combo, remembered.get('caption_channel', 1))
        _select(self.captions_codec_combo, remembered.get('codec', 'dnxhr'))
        self.captions_teletext.setChecked(bool(remembered.get('teletext')))
        self.video_pages.addWidget(self.captions_panel)

        # Subtitle track sub-tab -------------------------------------------
        # The subtitles as a track of their own, which a player lists and
        # turns on and off: MP4's timed text, Matroska's SRT or ASS, or a
        # transport stream's teletext or DVB subtitles, or an AVI's XSUB
        # (soft_subtitles, teletext, dvbsub, xsub).
        self.tracks_panel = QWidget()
        self.tracks_panel.setLayout(QVBoxLayout())
        self.tracks_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.tracks_panel.layout().setSpacing(10)

        self.tracks_codec_combo = utils.LabeledComboBox()
        self.tracks_codec_combo.setLabel(_('export_dialog.subtitle_codec'))
        self.tracks_codec_combo.combobox.addItem(_('export_dialog.subtitle_codec_srt'), 'srt')
        self.tracks_codec_combo.combobox.addItem(_('export_dialog.subtitle_codec_ass'), 'ass')
        self.tracks_codec_combo.activated.connect(self._refresh_tracks)
        self.tracks_panel.layout().addWidget(self.tracks_codec_combo)
        # A transport stream's subtitles: teletext pages, or DVB's pictures.
        self.tracks_ts_combo = utils.LabeledComboBox()
        self.tracks_ts_combo.setLabel(_('export_dialog.subtitle_codec'))
        self.tracks_ts_combo.combobox.addItem(_('export_dialog.subtitle_codec_teletext'), 'TS')
        self.tracks_ts_combo.combobox.addItem(_('export_dialog.subtitle_codec_dvb'), 'TS DVB')
        self.tracks_ts_combo.combobox.addItem(_('export_dialog.subtitle_codec_arib'), 'TS ARIB')
        self.tracks_ts_combo.combobox.addItem(_('export_dialog.subtitle_codec_scte27'), 'TS SCTE27')
        self.tracks_ts_combo.activated.connect(self._refresh_tracks)
        self.tracks_panel.layout().addWidget(self.tracks_ts_combo)

        self.tracks_translations = QCheckBox(_('export_dialog.subtitle_tracks_translations'))
        self._has_translations = bool(export_options.translation_languages(session.SUBTITLE.segments))
        self.tracks_translations.setVisible(self._has_translations)
        self.tracks_panel.layout().addWidget(self.tracks_translations)
        self.tracks_forced = QCheckBox(_('export_dialog.subtitle_track_forced'))
        self.tracks_panel.layout().addWidget(self.tracks_forced)

        self.tracks_panel.layout().addStretch()

        _select(self.tracks_codec_combo, remembered.get('subtitle_codec', 'srt'))
        self.tracks_translations.setChecked(bool(remembered.get('translations')))
        self.tracks_forced.setChecked(bool(remembered.get('forced')))
        self.video_pages.addWidget(self.tracks_panel)

        # Burned in, or alone on a clear video: drawn in a subtitle style, as
        # the preview draws them (burned_video).
        self.burn_panel = QWidget()
        self.burn_panel.setLayout(QVBoxLayout())
        self.burn_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.burn_panel.layout().setSpacing(10)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        self.codec_combo = utils.LabeledComboBox()
        self.codec_combo.setLabel(_('export_dialog.codec'))
        self.codec_combo.activated.connect(self._codec_chosen)
        line.addWidget(self.codec_combo, 1)
        self.style_combo = utils.LabeledComboBox()
        self.style_combo.setLabel(_('export_dialog.subtitle_style'))
        for style in sorted(subtitle_styles.available().values(), key=lambda style: style.name):
            self.style_combo.combobox.addItem(style.name, style.id)
        line.addWidget(self.style_combo, 1)
        self.burn_panel.layout().addWidget(row)
        self.quality_row = QWidget()
        line = QHBoxLayout(self.quality_row)
        line.setContentsMargins(0, 0, 0, 0)
        self.quality_combo = utils.LabeledComboBox()
        self.quality_combo.setLabel(_('export_dialog.video_quality'))
        for quality in burned_video.QUALITIES:
            self.quality_combo.combobox.addItem(_(f'export_dialog.video_quality_{quality}'), quality)
        line.addWidget(self.quality_combo, 1)
        line.addStretch(1)
        self.burn_panel.layout().addWidget(self.quality_row)
        # Alone, the picture is the video's, or any other when there is none.
        self.alpha_options = QWidget()
        line = QHBoxLayout(self.alpha_options)
        line.setContentsMargins(0, 0, 0, 0)
        self.alpha_size_combo = utils.LabeledComboBox()
        self.alpha_size_combo.setLabel(_('export_dialog.picture_size'))
        video_size = (int(session.VIDEO.get('width') or 0), int(session.VIDEO.get('height') or 0))
        if all(video_size):
            self.alpha_size_combo.combobox.addItem(
                _('export_dialog.picture_size_video').format(size=f'{video_size[0]}x{video_size[1]}'),
                f'{video_size[0]}x{video_size[1]}')
        for size in ('3840x2160', '1920x1080', '1280x720', '1080x1920'):
            if size != f'{video_size[0]}x{video_size[1]}':
                self.alpha_size_combo.combobox.addItem(size, size)
        line.addWidget(self.alpha_size_combo, 1)
        self.alpha_fps_combo = utils.LabeledComboBox()
        self.alpha_fps_combo.setLabel(_('export_dialog.frame_rate'))
        video_fps = float(session.VIDEO.get('framerate') or 0)
        rates = list(export_options.FRAME_RATES)
        if video_fps and not any(abs(video_fps - rate) < 0.01 for rate in rates):
            rates = sorted(rates + [round(video_fps, 3)])
        for rate in rates:
            label = f'{rate:g} fps'
            if video_fps and abs(video_fps - rate) < 0.01:
                label = _('export_dialog.frame_rate_video').format(fps=label)
                rate = video_fps
            self.alpha_fps_combo.combobox.addItem(label, rate)
        line.addWidget(self.alpha_fps_combo, 1)
        self.burn_panel.layout().addWidget(self.alpha_options)
        self.burn_panel.layout().addStretch()
        _select(self.style_combo, remembered.get('style') or subtitle_styles.get().id)
        _select(self.quality_combo, remembered.get('quality', burned_video.DEFAULT_QUALITY))
        if not all(video_size):
            _select(self.alpha_size_combo, remembered.get('size') or '{}x{}'.format(*burned_video.DEFAULT_SIZE))
        if not (video_fps and _select(self.alpha_fps_combo, video_fps)):
            fps = remembered.get('fps') or burned_video.DEFAULT_FPS
            if not _select(self.alpha_fps_combo, fps):
                _select(self.alpha_fps_combo, burned_video.DEFAULT_FPS)
        self.video_pages.addWidget(self.burn_panel)

        self.choose(remembered.get('format') if _video_file_of(remembered.get('format')) else 'MOV')
        for combo in (self.format_combo, self.way_combo, self.captions_mode_combo, self.captions_channel_combo,
                      self.captions_codec_combo, self.tracks_codec_combo, self.tracks_ts_combo, self.codec_combo,
                      self.style_combo, self.quality_combo, self.alpha_size_combo, self.alpha_fps_combo):
            combo.combobox.currentIndexChanged.connect(self.changed)
        for check in (self.captions_teletext, self.tracks_translations, self.tracks_forced):
            check.toggled.connect(self.changed)
        self.layout().addWidget(self.video_pages, 0)


    def choose(self, export):
        """Show the choices that make `export` (a key of
        session.LIST_OF_SUPPORTED_VIDEO_EXPORT_EXTENSIONS): its format, its
        way, and a transport stream's kind of track."""
        found = _video_file_of(export)
        if found is None:
            return False
        video_file, way = found
        if export in TS_TRACKS:
            _select(self.tracks_ts_combo, export)
        _select(self.format_combo, video_file)
        self._ways[video_file] = way
        self._refresh_format()
        return True

    def _video_file(self):
        return self.format_combo.combobox.currentData() or 'MOV'

    def _container(self):
        """The export the choices make."""
        ways = VIDEO_FILES[self._video_file()]['ways']
        export = ways.get(self.way_combo.combobox.currentData()) or next(iter(ways.values()))
        if export == 'TS':
            return self.tracks_ts_combo.combobox.currentData() or 'TS'
        return export

    def _refresh_format(self, *_args):
        """The ways the chosen format has, the one last chosen with it
        first; a choice of one is not asked."""
        video_file = self._video_file()
        ways = VIDEO_FILES[video_file]['ways']
        combobox = self.way_combo.combobox
        combobox.blockSignals(True)
        combobox.clear()
        for way in ways:
            combobox.addItem(_(f'export_dialog.video_subtitles_{way}'), way)
        _select(self.way_combo, self._ways.get(video_file))
        combobox.blockSignals(False)
        self.way_combo.setVisible(len(ways) > 1)
        self._refresh_way()

    def _refresh_way(self, *_args):
        """The options of the way chosen: the captions', a subtitle track's,
        or those of the subtitles drawn in the picture."""
        self._ways[self._video_file()] = self.way_combo.combobox.currentData()
        container = self._container()
        if burned_video.is_burned(container):
            self.video_pages.setCurrentWidget(self.burn_panel)
            combobox = self.codec_combo.combobox
            combobox.blockSignals(True)
            combobox.clear()
            for codec in burned_video.CONTAINERS[container]['codecs']:
                combobox.addItem(_codec_label(codec), codec)
            _select(self.codec_combo, self._codecs.get(container))
            combobox.blockSignals(False)
            self.codec_combo.setVisible(combobox.count() > 1)
            self.alpha_options.setVisible(burned_video.is_alpha(container))
            self._codec_chosen()
            return
        soft = container in soft_subtitles.CONTAINERS
        self.video_pages.setCurrentWidget(self.tracks_panel if soft else self.captions_panel)
        if soft:
            self._refresh_tracks()
        else:
            self._refresh_captions()

    def _codec_chosen(self, *_args):
        """A quality for the codecs that take one; lossless and fixed-rate
        ones do not."""
        codec = self.codec_combo.combobox.currentData()
        self._codecs[self._container()] = codec
        self.quality_row.setVisible(burned_video.has_quality(codec))

    def _refresh_captions(self, *_args):
        """What the chosen container can hold: a QuickTime caption track
        carries CEA-608 and keeps the picture as it is; an MXF's ancillary
        data carries CEA-708 beside it, but takes a picture of its own; the
        video's own frames carry both, the picture copied."""
        container = self._container()
        self.captions_codec_combo.setVisible(container == 'MXF')
        self.captions_teletext.setVisible(container == 'MXF')

    def _refresh_tracks(self, *_args):
        """What the chosen kind of file does with the subtitles: an MP4's
        timed text keeps a run's formatting alone; Matroska takes SRT,
        which every player reads, or ASS, which keeps the look; a transport
        stream, teletext pages or DVB subtitles, pictures."""
        container = self._container()
        self.tracks_codec_combo.setVisible(container == 'MKV')
        self.tracks_ts_combo.setVisible(container in TS_TRACKS)
        # Teletext, DVB subtitles, ARIB captions, SCTE 27 and XSUB have no
        # forced streams: a viewer chooses one or none.
        self.tracks_forced.setVisible(container not in soft_subtitles.UNFORCED)

    def get_config(self):
        container, captions = self.export_choices()
        remembered = dict(captions)
        if 'width' in remembered:
            remembered['size'] = f"{remembered.pop('width')}x{remembered.pop('height')}"
        remembered.pop('video_codec', None)
        session.CONFIG['export_video'] = dict(session.CONFIG.get('export_video') or {}, **remembered, format=container,
                                              ways=dict(self._ways), codecs=dict(self._codecs))
        return {'category': 'video', 'format': container, 'captions': captions}

    def export_choices(self):
        """(format, the options it is written with), as chosen: nothing
        remembered."""
        container = self._container()
        if burned_video.is_burned(container):
            choices = {'video_codec': burned_video.codec_of(container, self.codec_combo.combobox.currentData()),
                       'style': self.style_combo.combobox.currentData() or subtitle_styles.DEFAULT_STYLE,
                       'quality': self.quality_combo.combobox.currentData() or burned_video.DEFAULT_QUALITY}
            if burned_video.is_alpha(container):
                size = str(self.alpha_size_combo.combobox.currentData() or '{}x{}'.format(*burned_video.DEFAULT_SIZE))
                width, height = (int(value) for value in size.split('x'))
                choices.update(width=width, height=height,
                               fps=float(self.alpha_fps_combo.combobox.currentData() or burned_video.DEFAULT_FPS))
            return container, choices
        if container in soft_subtitles.CONTAINERS:
            return container, {
                'subtitle_codec': self.tracks_codec_combo.combobox.currentData() or 'srt',
                'translations': self._has_translations and self.tracks_translations.isChecked(),
                'forced': self.tracks_forced.isChecked(),
            }
        return container, {
            'caption_mode': self.captions_mode_combo.combobox.currentData() or 'pop',
            'caption_channel': self.captions_channel_combo.combobox.currentData() or 1,
            'codec': self.captions_codec_combo.combobox.currentData() or 'dnxhr',
            'teletext': container == 'MXF' and self.captions_teletext.isChecked(),
        }


def _duration_text(seconds):
    """`seconds` as "M:SS", or "H:MM:SS" from an hour on."""
    total = int(round(max(0.0, float(seconds))))
    hours, rest = divmod(total, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


def _size_text(size):
    """`size` bytes as "≈ 54 MB", in the unit that reads best."""
    for unit, scale in (('GB', 1e9), ('MB', 1e6), ('kB', 1e3)):
        if size >= scale:
            value = size / scale
            return f'≈ {value:.1f} {unit}' if value < 10 else f'≈ {value:.0f} {unit}'
    return f'{int(size)} B'


class _LoudnessScale(QWidget):
    """The loudness programs aim at, marked on a short scale, and where the
    export's falls among them."""

    LOW, HIGH = -36.0, -6.0                                    # the scale's ends, in LUFS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = None
        self.setFixedHeight(34)

    def set_value(self, value):
        self.value = value
        self.update()

    def _x(self, lufs):
        span = self.width() - 12
        return 6 + span * (min(self.HIGH, max(self.LOW, lufs)) - self.LOW) / (self.HIGH - self.LOW)

    def paintEvent(self, _event):
        from subtitld.modules import audio_report
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        line = QColor(184, 206, 224, 70)
        painter.fillRect(QRectF(6, 15, self.width() - 12, 2), line)
        font = painter.font()
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        for number, (key, lufs) in enumerate(audio_report.TARGETS):
            x = self._x(lufs)
            painter.fillRect(QRectF(x - 0.5, 11, 1, 10), QColor(184, 206, 224, 140))
            painter.setPen(QColor(184, 206, 224, 120))
            # the labels alternate above and under the line, as ATSC and EBU sit 1 LU apart
            top = 0 if number % 2 == 0 else 23
            text = _(f'export_dialog.audio_target_{key}')
            width = painter.fontMetrics().horizontalAdvance(text)
            left = min(max(0, x - width / 2), self.width() - width)
            painter.drawText(QRectF(left, top, width, 10), Qt.AlignCenter, text)
        if self.value is not None:
            x = self._x(self.value)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#e9eef2'))
            painter.drawEllipse(QRectF(x - 4, 12, 8, 8))
        painter.end()


class _AudioDashboard(QWidget):
    """An audio export's facts, its loudness and its problems, in cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(6)

        row = QWidget()
        row.setLayout(QHBoxLayout())
        row.layout().setContentsMargins(0, 0, 0, 0)
        row.layout().setSpacing(6)
        self.length = self._card('audio_length', row.layout())
        self.size = self._card('audio_size', row.layout())
        self.layout().addWidget(row)
        self.format = self._card('audio_format', self.layout())

        loudness = self._frame(self.layout())
        loudness.layout().addWidget(self._label('audio_loudness'))
        self.loudness = QLabel()
        self.loudness.setObjectName('export_dialog_card_big')
        loudness.layout().addWidget(self.loudness)
        self.scale = _LoudnessScale()
        loudness.layout().addWidget(self.scale)
        under = QWidget()
        under.setLayout(QHBoxLayout())
        under.layout().setContentsMargins(0, 0, 0, 0)
        self.peak = QLabel()
        self.peak.setObjectName('export_dialog_card_value')
        self.range = QLabel()
        self.range.setObjectName('export_dialog_card_value')
        under.layout().addWidget(self.peak)
        under.layout().addStretch()
        under.layout().addWidget(self.range)
        loudness.layout().addWidget(under)

        self.files_card = self._frame(self.layout())
        self.files_title = self._label('audio_files')
        self.files_card.layout().addWidget(self.files_title)
        self.files = QLabel()
        self.files.setObjectName('export_dialog_card_list')
        self.files.setWordWrap(True)
        self.files_card.layout().addWidget(self.files)

        self.problems = QLabel()
        self.problems.setObjectName('export_dialog_problems')
        self.problems.setWordWrap(True)
        self.layout().addWidget(self.problems)
        self.layout().addStretch()

    @staticmethod
    def _frame(layout):
        frame = QWidget()
        frame.setObjectName('export_dialog_card')
        frame.setAttribute(Qt.WA_StyledBackground)
        frame.setLayout(QVBoxLayout())
        frame.layout().setContentsMargins(8, 6, 8, 7)
        frame.layout().setSpacing(2)
        layout.addWidget(frame)
        return frame

    @staticmethod
    def _label(key):
        label = QLabel(_(f'export_dialog.{key}'))
        label.setObjectName('export_dialog_card_label')
        return label

    def _card(self, key, layout):
        frame = self._frame(layout)
        frame.layout().addWidget(self._label(key))
        value = QLabel()
        value.setObjectName('export_dialog_card_value')
        frame.layout().addWidget(value)
        return value

    # The most file names listed; the rest counted.
    LISTED = 6

    def show_report(self, facts, problems, loudness):
        """Fill the cards: `facts` and `problems` (audio_report's), and
        `loudness`, its measure, or 'measuring', or None when there is none."""
        from subtitld.modules import audio_report
        self.length.setText(_duration_text(facts['length']))
        self.size.setText(_size_text(facts['size']))
        channels = _('export_dialog.audio_stereo') if facts['channels'] == 2 else str(facts['channels'])
        self.format.setText(f"{facts['format']} · {facts['rate'] / 1000:g} kHz · {channels}")
        files = facts['files']
        self.files_card.setVisible(len(files) > 1)
        self.files_title.setText(f"{_('export_dialog.audio_files')} · {len(files)}")
        shown = files[:self.LISTED]
        more = len(files) - len(shown)
        self.files.setText('\n'.join(shown) + ('\n' + _('export_dialog.audio_more').format(count=more)
                                               if more else ''))
        lines = []
        if loudness == 'measuring':
            self.loudness.setText(_('export_dialog.audio_measuring'))
            self.scale.set_value(None)
            self.peak.setText('')
            self.range.setText('')
        elif loudness and loudness.get('integrated') is not None:
            self.loudness.setText(f"{loudness['integrated']:.1f} LUFS")
            self.scale.set_value(loudness['integrated'])
            peak = loudness.get('peak')
            self.peak.setText(f"{_('export_dialog.audio_true_peak')} {peak:.1f} dBTP".replace('-0.0', '0.0')
                              if peak is not None else '')
            spread = loudness.get('range')
            self.range.setText(f"{_('export_dialog.audio_range')} {spread:.1f} LU" if spread is not None else '')
            if peak is not None and peak > audio_report.PEAK_LIMIT:
                lines.append(_('export_dialog.audio_problem_clipping').format(limit=f'{audio_report.PEAK_LIMIT:g}'))
        else:
            self.loudness.setText('—')
            self.scale.set_value(None)
            self.peak.setText('')
            self.range.setText('')
        if problems.get('no_speech'):
            lines.append(_('export_dialog.audio_problem_no_speech').format(count=problems['no_speech'],
                                                                           total=problems['subtitles']))
        if problems.get('files_gone'):
            lines.append(_('export_dialog.audio_problem_files_gone').format(count=problems['files_gone']))
        self.problems.setText('\n'.join(f'⚠ {line}' for line in lines))
        self.problems.setVisible(bool(lines))


def project_logo(fmt):
    """The program's logo, one colour, for the Project tab: a QIcon."""
    from PySide6.QtGui import QIcon
    logo = project_export.FORMATS.get(fmt, {}).get('logo')
    return QIcon(os.path.join(session.PATH_SUBTITLD_GRAPHICS, f'project_logo_{logo}')) if logo else QIcon()


class _TimelineMiniature(QWidget):
    """A project's timeline, small: a row a track, numbered as the editors
    number them (V1, A1..., S1... for subtitles), its clips as blocks at
    their times, in the speaker's colour on a speaker's track."""

    ROW, GAP, LABEL = 8, 3, 74                                 # a row's height, between rows, the names' column
    MOST = 14                                                  # rows drawn; the rest counted

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []                                         # (number, kind, [(start, end)], colour)
        self.duration = 1.0
        self.more = 0

    def set_timeline(self, timeline):
        rows, counts = [], {}
        for track in timeline.tracks:
            letter = 'V' if track.kind == 'video' else 'A'
            counts[letter] = counts.get(letter, 0) + 1
            colour = None
            if track.speaker:
                colour = (session.SPEAKERS.get(track.speaker) or {}).get('color')
            rows.append((f'{letter}{counts[letter]}  {track.name}', track.kind, [(c.at, c.end) for c in track.clips],
                         colour))
        for number, (name, _language, cues) in enumerate(timeline.subtitles, start=1):
            rows.append((f'S{number}  {name}', 'subtitles', [(float(c['start']), float(c['end'])) for c in cues],
                         None))
        self.duration = max(1.0, timeline.duration)
        self.more = max(0, len(rows) - self.MOST)
        self.rows = rows[:self.MOST]
        self.setFixedHeight(len(self.rows) * (self.ROW + self.GAP) + (12 if self.more else 0) + 2)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        font = painter.font()
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        width = self.width() - self.LABEL - 2
        colours = {'video': QColor('#7f9bb0'), 'audio': QColor('#5f7b90'), 'subtitles': QColor('#b8cee0')}
        for index, (number, kind, spans, colour) in enumerate(self.rows):
            y = index * (self.ROW + self.GAP)
            painter.setPen(QColor(184, 206, 224, 120))
            label = painter.fontMetrics().elidedText(number, Qt.ElideRight, self.LABEL - 6)
            painter.drawText(QRectF(0, y - 2, self.LABEL, self.ROW + 4), Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.fillRect(QRectF(self.LABEL, y, width, self.ROW), QColor(0, 0, 0, 40))
            fill = QColor(colour) if colour and QColor(colour).isValid() else colours[kind]
            for start, end in spans:
                left = self.LABEL + width * start / self.duration
                size = max(1.0, width * (end - start) / self.duration)
                if kind == 'subtitles':
                    painter.fillRect(QRectF(left, y + 1, size, self.ROW - 2), fill)
                else:
                    painter.fillRect(QRectF(left, y, size, self.ROW), fill)
        if self.more:
            painter.setPen(QColor(184, 206, 224, 120))
            y = len(self.rows) * (self.ROW + self.GAP)
            painter.drawText(QRectF(self.LABEL, y, width, 10), Qt.AlignLeft | Qt.AlignVCenter,
                             _('export_dialog.audio_more').format(count=self.more))
        painter.end()


class _ProjectDashboard(QWidget):
    """A project export's timeline, small, the program it opens in, the
    files it writes, and what will not carry over."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(6)
        card = _AudioDashboard._frame(self.layout())
        top = QWidget()
        top.setLayout(QHBoxLayout())
        top.layout().setContentsMargins(0, 0, 0, 0)
        top.layout().setSpacing(8)
        self.logo = QLabel()
        self.logo.setFixedSize(28, 28)
        top.layout().addWidget(self.logo)
        names = QWidget()
        names.setLayout(QVBoxLayout())
        names.layout().setContentsMargins(0, 0, 0, 0)
        names.layout().setSpacing(0)
        names.layout().addWidget(_AudioDashboard._label('project_opens_in'))
        self.opens = QLabel()
        self.opens.setObjectName('export_dialog_card_value')
        names.layout().addWidget(self.opens)
        top.layout().addWidget(names, 1)
        card.layout().addWidget(top)

        timeline = _AudioDashboard._frame(self.layout())
        timeline.layout().addWidget(_AudioDashboard._label('project_timeline'))
        self.miniature = _TimelineMiniature()
        timeline.layout().addWidget(self.miniature)
        self.picture = QLabel()
        self.picture.setObjectName('export_dialog_card_value')
        timeline.layout().addWidget(self.picture)

        files = _AudioDashboard._frame(self.layout())
        files.layout().addWidget(_AudioDashboard._label('project_files'))
        self.files = QLabel()
        self.files.setObjectName('export_dialog_card_list')
        self.files.setWordWrap(True)
        files.layout().addWidget(self.files)

        self.problems = QLabel()
        self.problems.setObjectName('export_dialog_problems')
        self.problems.setWordWrap(True)
        self.layout().addWidget(self.problems)
        self.losses = QLabel()
        self.losses.setObjectName('export_dialog_losses')
        self.losses.setWordWrap(True)
        self.layout().addWidget(self.losses)
        self.layout().addStretch()

    def show_project(self, fmt, timeline, files, problems, losses):
        """Fill the cards for the `fmt` export of `timeline`: `files`, the
        lines of what it writes; `problems` and `losses`, lines of each."""
        spec = project_export.FORMATS[fmt]
        self.logo.setPixmap(project_logo(fmt).pixmap(28, 28))
        program, since = spec['opens']
        self.opens.setText(_('export_dialog.project_or_later').format(program=program, version=since))
        self.miniature.set_timeline(timeline)
        if spec.get('audio_only'):
            self.picture.setText(f'{_duration_text(timeline.duration)} · 48 kHz')
        else:
            self.picture.setText(f'{_duration_text(timeline.duration)} · {timeline.width}×{timeline.height} · '
                                 f'{timeline.fps:.3g} fps')
        self.files.setText('\n'.join(files))
        self.problems.setText('\n'.join(f'⚠ {line}' for line in problems))
        self.problems.setVisible(bool(problems))
        self.losses.setText('\n'.join(f'· {line}' for line in losses))
        self.losses.setVisible(bool(losses))


class _PreviewPanel(QWidget):
    """The export's preview, beside every page: the start of the file as it
    will be written, or a faded note where there is no text to show. For
    an export of pictures, one of them on its frame, and as itself."""

    WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('export_dialog_preview')
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(self.WIDTH)
        self.setLayout(QGridLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        file_page = QWidget()
        file_page.setLayout(QVBoxLayout())
        file_page.layout().setContentsMargins(12, 8, 12, 12)
        file_page.layout().setSpacing(4)
        label = QLabel(_('export_dialog.preview'))
        label.setObjectName('export_dialog_preview_label')
        file_page.layout().addWidget(label)
        self.picture = QLabel()
        self.picture.setVisible(False)
        file_page.layout().addWidget(self.picture)
        # and the picture itself, as large as it is or as the panel allows, its clear parts checkered
        self.crop = QLabel()
        self.crop.setVisible(False)
        file_page.layout().addWidget(self.crop)
        self.file = QPlainTextEdit()
        self.file.setObjectName('export_dialog_preview_file')
        self.file.setReadOnly(True)
        file_page.layout().addWidget(self.file, 1)
        # an audio export's facts, loudness and problems, in cards
        self.dashboard = _AudioDashboard()
        self.dashboard.setVisible(False)
        file_page.layout().addWidget(self.dashboard, 1)
        # a project export's timeline, small, and its facts
        self.project = _ProjectDashboard()
        self.project.setVisible(False)
        file_page.layout().addWidget(self.project, 1)
        file_page.layout().addStretch()                        # where the well was, under a picture
        self.layout().addWidget(file_page, 0, 0)

        # over it, the sidebar's shadow mirrored: falling into the panel from its left edge
        shadow = QWidget()
        shadow.setObjectName('export_dialog_preview_shadow')
        shadow.setAttribute(Qt.WA_StyledBackground)
        shadow.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.layout().addWidget(shadow, 0, 0)

    def show_text(self, text):
        self.dashboard.setVisible(False)
        self.project.setVisible(False)
        self.picture.setVisible(False)
        self.crop.setVisible(False)
        self.file.setVisible(True)
        self._plain(text)

    def show_note(self, note):
        """`note` in the well, faded: no file to show."""
        self.dashboard.setVisible(False)
        self.project.setVisible(False)
        self.picture.setVisible(False)
        self.crop.setVisible(False)
        self.file.setVisible(True)
        self.file.clear()
        self._faded(note)

    def _faded(self, note):
        faded = QTextCharFormat()
        color = self.file.palette().text().color()
        color.setAlpha(110)
        faded.setForeground(color)
        faded.setFontItalic(True)
        self.file.textCursor().insertText(note, faded)

    def show_nothing(self):
        self.dashboard.setVisible(False)
        self.project.setVisible(False)
        self.picture.setVisible(False)
        self.crop.setVisible(False)
        self.file.setVisible(True)
        self.file.clear()

    def show_project(self, *report):
        """A project export's dashboard in place of the well (see
        _ProjectDashboard.show_project)."""
        self.picture.setVisible(False)
        self.crop.setVisible(False)
        self.file.setVisible(False)
        self.dashboard.setVisible(False)
        self.project.setVisible(True)
        self.project.show_project(*report)

    def show_audio(self, facts, problems, loudness):
        """An audio export's dashboard in place of the well."""
        self.picture.setVisible(False)
        self.crop.setVisible(False)
        self.file.setVisible(False)
        self.project.setVisible(False)
        self.dashboard.setVisible(True)
        self.dashboard.show_report(facts, problems, loudness)

    RADIUS = 3                                                 # the well's corners

    def show_picture(self, rect, image, frame, text=None):
        """`image`, drawn at `rect` (of the frame, 0..1) on a frame `frame`
        (width, height) pixels, scaled to the panel's width; under it, the
        picture itself; and under that, in the well, `text`: the file the
        pictures go with, when there is one."""
        self.dashboard.setVisible(False)
        self.project.setVisible(False)
        margins = self.layout().itemAtPosition(0, 0).widget().layout().contentsMargins()
        width = self.WIDTH - margins.left() - margins.right()
        height = max(1, round(width * frame[1] / frame[0]))
        painter, pixmap = self._rounded(width, height)
        painter.fillRect(QRectF(0, 0, width, height), QColor(0, 0, 0, 110))
        if rect is not None and image is not None:
            painter.drawImage(QRectF(rect.x() * width, rect.y() * height, rect.width() * width, rect.height() * height), image)
        painter.end()
        self.picture.setPixmap(pixmap)
        self.picture.setFixedSize(width, height)
        self.picture.setVisible(True)
        self.crop.setVisible(image is not None)
        if image is not None:
            scale = min(1.0, width / image.width())
            size = (max(1, round(image.width() * scale)), max(1, round(image.height() * scale)))
            painter, pixmap = self._rounded(*size)
            self._checkers(painter, *size)
            painter.drawImage(QRectF(0, 0, *size), image)
            painter.end()
            self.crop.setPixmap(pixmap)
            self.crop.setFixedSize(*size)
        self._plain(text or '')
        self.file.setVisible(text is not None)

    def show_still(self, image):
        """`image`, a frame of the video as the export writes it, scaled to
        the panel's width; its clear parts, if it has them, checkered."""
        self.dashboard.setVisible(False)
        self.project.setVisible(False)
        margins = self.layout().itemAtPosition(0, 0).widget().layout().contentsMargins()
        width = self.WIDTH - margins.left() - margins.right()
        height = max(1, round(width * image.height() / max(1, image.width())))
        painter, pixmap = self._rounded(width, height)
        if image.hasAlphaChannel():
            self._checkers(painter, width, height)
        painter.drawImage(QRectF(0, 0, width, height), image)
        painter.end()
        self.picture.setPixmap(pixmap)
        self.picture.setFixedSize(width, height)
        self.picture.setVisible(True)
        self.crop.setVisible(False)
        self.file.setVisible(False)

    @staticmethod
    def _checkers(painter, width, height, square=6):
        """A checkerboard under a picture's clear parts."""
        for row in range(0, height, square):
            for column in range(0, width, square):
                light = (row // square + column // square) % 2
                painter.fillRect(column, row, square, square, QColor(90, 90, 90) if light else QColor(60, 60, 60))

    def _plain(self, text):
        """`text` in the well as it is: not in the faded look a note left."""
        self.file.clear()
        self.file.setCurrentCharFormat(QTextCharFormat())
        self.file.setPlainText(text)

    def _rounded(self, width, height):
        """A clear pixmap `width` x `height` at the screen's density, and a
        painter on it that paints only inside its rounded corners."""
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(width * ratio), round(height * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, width, height), self.RADIUS, self.RADIUS)
        painter.setClipPath(path)
        return painter, pixmap


class ExportDialog(utils.SimpleDialog):
    """Universal export dialog. Returns a config dict via
    `exec_and_get_values()` or None when cancelled."""

    CATEGORY_KEYS = ['subtitles', 'audio', 'video', 'documents', 'project']

    def __init__(self, parent=None, has_background=False, has_vocals=False, initial_category=None,
                 audio_engine=None):
        super().__init__(parent, _('export_dialog.title'))
        # The player's audio engine, which renders the mix an audio export
        # writes: its loudness is measured from it.
        self._audio_engine = audio_engine
        self._loudness = {}                                    # measured, by the choices it was measured for
        self._measuring = None
        self.setMinimumSize(900, 420)
        self.make_resizable()

        self.content.layout().setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setLayout(QHBoxLayout())
        body.layout().setContentsMargins(0, 0, 0, 0)
        body.layout().setSpacing(0)
        self.content.layout().addWidget(body)

        # the categories, and over them the sidebar's shadow (the chosen one lies beneath it)
        side = QWidget()
        side.setLayout(QGridLayout())
        side.layout().setContentsMargins(0, 0, 0, 0)
        side.setFixedWidth(140)
        body.layout().addWidget(side)

        sidebar = QWidget()
        sidebar.setObjectName('export_dialog_sidebar')
        sidebar.setLayout(QVBoxLayout())
        sidebar.layout().setContentsMargins(0, 10, 1, 10)      # the chosen category stops short of the shadow's light line
        sidebar.layout().setSpacing(0)
        side.layout().addWidget(sidebar, 0, 0)

        self._category_buttons = {}
        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        sidebar.layout().addStretch()                          # the categories sit at the sidebar's middle
        for key in self.CATEGORY_KEYS:
            btn = QPushButton(_(f'export_dialog.category_{key}'))
            btn.setObjectName('export_dialog_category_button')
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            sidebar.layout().addWidget(btn)
            self._category_group.addButton(btn)
            self._category_buttons[key] = btn
        sidebar.layout().addStretch()
        shadow = QWidget()
        shadow.setObjectName('export_dialog_sidebar_shadow')
        shadow.setAttribute(Qt.WA_StyledBackground)
        shadow.setAttribute(Qt.WA_TransparentForMouseEvents)
        side.layout().addWidget(shadow, 0, 0)

        right = QWidget()
        right.setObjectName('export_dialog_pages')
        right.setLayout(QVBoxLayout())
        right.layout().setContentsMargins(0, 0, 0, 0)          # each page's header runs edge to edge
        body.layout().addWidget(right, 1)

        self._pages = QStackedWidget()
        right.layout().addWidget(self._pages)

        # the preview, one for every page
        self.preview_panel = _PreviewPanel()
        body.layout().addWidget(self.preview_panel)

        self.subtitles_panel = _SubtitlesPanel()
        self.audio_panel = _AudioPanel(has_background=has_background, has_vocals=has_vocals)
        self.video_panel = _VideoPanel()
        self.documents_panel = _DocumentsPanel()
        self.project_panel = _ProjectPanel()
        for panel in (self.subtitles_panel, self.audio_panel, self.video_panel, self.documents_panel,
                      self.project_panel):
            self._pages.addWidget(self._page(panel))

        for index, key in enumerate(self.CATEGORY_KEYS):
            self._category_buttons[key].clicked.connect(lambda _checked=False, i=index, k=key: self._select_category(i, k))

        # The preview follows the subtitles' choices a moment after the last.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self.refresh_preview)
        self.subtitles_panel.changed.connect(self._preview_timer.start)
        self.video_panel.changed.connect(self._preview_timer.start)
        self.documents_panel.changed.connect(self._preview_timer.start)
        self.project_panel.changed.connect(self._preview_timer.start)
        self.audio_panel.changed.connect(self._preview_timer.start)
        # A video export's still: a few seconds written in the background, one at a time.
        self._still = None                                     # the request being drawn: (number, path)
        self._still_count = 0
        self._still_again = False                              # asked for while one was being drawn
        self._still_folder = None
        # An export of pictures shows one, drawn by a renderer of the dialog's
        # own, started the first time one is asked for.
        self._renderer = None
        self._picture = None                                   # (key, frame) of the picture awaited
        self._picture_timeout = QTimer(self)
        self._picture_timeout.setSingleShot(True)
        self._picture_timeout.setInterval(30_000)
        self._picture_timeout.timeout.connect(self._picture_late)
        self.finished.connect(self._close_renderer)
        self.finished.connect(self._drop_stills)

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

    @staticmethod
    def _page(panel):
        """A page: the panel's format as its header, flush with the top and edge to edge, then the rest of the
        panel inside the page's margins."""
        page = QWidget()
        page.setLayout(QVBoxLayout())
        page.layout().setContentsMargins(0, 0, 0, 0)
        page.layout().setSpacing(0)
        header = getattr(panel, 'format_combo', None)
        if isinstance(header, utils.HeaderComboBox):
            page.layout().addWidget(header)
        form = QWidget()
        form.setLayout(QVBoxLayout())
        form.layout().setContentsMargins(20, 16, 20, 16)
        form.layout().addWidget(panel)
        page.layout().addWidget(form, 1)
        return page

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
        self.refresh_preview()

    def refresh_preview(self):
        """The preview for the page shown: the start of the subtitles file
        the choices write, or a frame of the video export; nothing yet for
        audio and documents."""
        self._preview_timer.stop()
        self._picture = None
        self._picture_timeout.stop()
        self._still_again = False
        if self._current_category() == 'video' and session.SUBTITLE.segments:
            self._preview_still()
            return
        if self._current_category() == 'audio':
            self._preview_audio()
            return
        if self._current_category() == 'project':
            self._preview_project()
            return
        if self._current_category() == 'documents' and session.SUBTITLE.segments:
            self._preview_document()
            return
        if self._current_category() != 'subtitles' or not session.SUBTITLE.segments:
            self.preview_panel.show_nothing()
            return
        try:
            fmt, _choices, options = self.subtitles_panel.export_choices()
            frame = file_io.picture_frame(fmt, options)
            if frame is not None:
                # Drawing every picture is for the export: the preview draws one.
                self._preview_picture(fmt, options, frame)
                return
            text = file_io.preview_file(fmt, options, session.CONFIG.get('selected_language') or 'en')
        except file_io.PreviewBusy:
            self._preview_timer.start()                        # a save holds the files: again in a moment
            return
        except file_io.PreviewBinary:
            self.preview_panel.show_note(_('export_dialog.preview_binary'))
            return
        except Exception:
            self.preview_panel.show_note(_('export_dialog.preview_failed'))
            return
        self.preview_panel.show_text(text)

    def _audio_key(self, mode, include_background):
        """What the loudness of an audio export depends on: the choices, and
        each speech that plays and where."""
        playing = []
        for segment in session.SUBTITLE.segments:
            dub = audio_report._playing(segment.get('dubbing'))
            if dub:
                playing.append((float(dub.get('start', 0.0)), str(dub.get('path')), repr(dub.get('segments'))))
        # The stems and the clips are measured played together, as the mixdown
        # is; the clips never carry the background.
        if mode in ('stems', 'clips'):
            mode, include_background = 'mixdown', include_background and mode == 'stems'
        return mode, bool(include_background), tuple(playing)

    def _preview_audio(self):
        """An audio export's dashboard: its facts and problems at once, its
        loudness once measured in the background."""
        from subtitld.modules import bounce
        fmt, mode, include_background = self.audio_panel.export_choices()
        segments = session.SUBTITLE.segments
        base = os.path.splitext(os.path.basename(session.SUBTITLE.filepath or session.VIDEO.get('filepath')
                                                 or 'subtitle'))[0]
        engine = self._audio_engine
        facts = audio_report.facts(mode, fmt, include_background, segments, session.VIDEO,
                                   background=bounce._background_audio_path(), vocals=bounce._vocals_audio_path(),
                                   stem=base)
        if engine is not None:
            facts['rate'] = int(engine.samplerate)
        problems = audio_report.problems(mode, segments)
        key = self._audio_key(mode, include_background)
        loudness = self._loudness.get(key)
        if key not in self._loudness:
            loudness = None
            can_render = engine is not None or mode in ('background_only', 'vocals_only')
            if can_render:
                loudness = 'measuring'
                if self._measuring is None:
                    self._measuring = key
                    audio_report.measure_async(key[0], key[1], engine,
                                               lambda result, key=key: self._measured(key, result))
        self.preview_panel.show_audio(facts, problems, loudness)

    def _measured(self, key, result):
        self._measuring = None
        self._loudness[key] = result
        if self._current_category() == 'audio':
            self.refresh_preview()

    def _preview_project(self):
        """A project export's dashboard: its timeline, small, the program it
        opens in, the files it writes, and what will not carry over."""
        fmt, choices = self.project_panel.export_choices()
        spec = project_export.FORMATS[fmt]
        try:
            timeline = project_export.collect(
                session.SUBTITLE, session.VIDEO, choices, project_export.background_path(session.VIDEO),
                names={key: _(f'export_dialog.project_track_{key}') for key in ('video', 'audio', 'background',
                                                                                 'subtitles')})
        except Exception:
            self.preview_panel.show_note(_('export_dialog.preview_failed'))
            return
        stem = os.path.splitext(os.path.basename(session.SUBTITLE.filepath or session.VIDEO.get('filepath')
                                                 or 'subtitle'))[0]
        video = session.VIDEO.get('filepath')
        sounds = [path for path in timeline.media() if path != video]
        size = sum(os.path.getsize(path) for path in sounds if os.path.isfile(path))
        if spec.get('session_folder'):
            files = [f'{stem}/', f'  {stem}.ardour']
        else:
            files = [f'{stem}.{spec["extension"]}']
        if sounds:
            files.append(_('export_dialog.project_sounds').format(count=len(sounds), size=_size_text(size)))
        segments = session.SUBTITLE.segments
        problems = []
        gone = audio_report.problems('mixdown', segments).get('files_gone') if choices.get('speeches') else 0
        if gone:
            problems.append(_('export_dialog.audio_problem_files_gone').format(count=gone))
        losses = []
        if spec.get('audio_only') and video:
            losses.append(_('export_dialog.project_no_video'))
        if spec.get('one_subtitle_track') and choices.get('subtitles') and documents.languages_of(segments):
            losses.append(_('export_dialog.project_no_translations'))
        if timeline.subtitles and not spec.get('styled_captions'):
            losses.append(_('export_dialog.project_plain_subtitles'))
        moved = sum(len(track.clips) for track in timeline.tracks if track.speaker and track.lane > 1)
        if moved:
            losses.append(_('export_dialog.project_moved').format(count=moved))
        self.preview_panel.show_project(fmt, timeline, files, problems, losses)

    def _preview_document(self):
        """The document as chosen: the start of its text, or its first
        page as the PDF prints it."""
        fmt, chosen = self.documents_panel.export_choices()
        chosen = dict(chosen)
        content = chosen.pop('content')
        segments = session.SUBTITLE.segments
        try:
            if content == documents.MARKERS:
                self.preview_panel.show_text(csv_export.write(segments))
                return
            if fmt == 'CSV':
                chosen['title'] = False
            document = documents.build(
                content, segments, chosen, fps=(session.VIDEO or {}).get('framerate'),
                title=documents.title_for(session.SUBTITLE, session.VIDEO),
                texts=None if fmt == 'CSV' else {key: _(f'export_dialog.document_{key}') for key in documents.TEXTS},
                speakers=session.SPEAKERS)
            if fmt == 'TXT':
                self.preview_panel.show_text(documents.txt(document))
            elif fmt == 'CSV':
                self.preview_panel.show_text(documents.csv_text(document))
            else:
                self.preview_panel.show_still(documents.page_image(document, 2 * _PreviewPanel.WIDTH))
        except Exception:
            self.preview_panel.show_note(_('export_dialog.preview_failed'))

    def _preview_still(self):
        """The video export's frame at the subtitle under the cursor, else
        the next: a few seconds of the video written as the export writes
        it, in the background; one at a time, the last asked for drawn
        next."""
        from subtitld.modules import video_preview
        container, captions = self.video_panel.export_choices()
        if not (session.VIDEO or {}).get('filepath') and not burned_video.is_alpha(container):
            self.preview_panel.show_nothing()
            return
        if self._still is not None:
            self._still_again = True
            return
        cue = self._picture_cue('', {})
        if cue is None:
            self.preview_panel.show_nothing()
            return
        captions.setdefault('speakers', (session.CONFIG.get('export_subtitles') or {}).get('speakers'))
        if self._still_folder is None:
            self._still_folder = tempfile.mkdtemp(prefix='subtitld-stills-')
        self._still_count += 1
        path = os.path.join(self._still_folder, f'{self._still_count}.png')
        self._still = (self._still_count, path)
        self.preview_panel.show_note(_('export_dialog.preview_drawing'))
        video_preview.still_async(container, captions, copy.deepcopy(session.SUBTITLE.segments), cue,
                                  float(session.SUBTITLE.position or 0), path, self._still_done,
                                  language=session.SUBTITLE.language or '')

    def _still_done(self, path, error):
        if self._still is None or self._still[1] != path:
            return                                             # the dialog closed, or let it go
        self._still = None
        if self._still_again:
            self.refresh_preview()
            return
        if self._current_category() != 'video':
            return
        image = QImage(path) if not error else QImage()
        if image.isNull():
            self.preview_panel.show_note(_('export_dialog.preview_failed'))
        else:
            self.preview_panel.show_still(image)
        try:
            os.unlink(path)
        except OSError:
            pass

    def _drop_stills(self, *_args):
        """A still still being drawn is let go; its folder goes once it is."""
        self._still = None
        if self._still_folder is not None:
            shutil.rmtree(self._still_folder, ignore_errors=True)
            self._still_folder = None

    def _picture_cue(self, fmt, options):
        """The subtitle under the timeline's cursor, else the next after it,
        as the export writes it; None when there is none to draw."""
        position = float(session.SUBTITLE.position or 0)
        segments = sorted(session.SUBTITLE.segments, key=lambda segment: float(segment.get('start', 0)))
        under = [segment for segment in segments if float(segment['start']) <= position < float(segment['end'])]
        after = [segment for segment in segments if float(segment['start']) > position]
        for segment in under + after:
            prepared = (export_options.prepare([segment], options) if fmt in export_options.TEXT_FORMATS
                        else [dict(segment)])
            if prepared and markup.plain(str(prepared[0].get('text') or '')).strip():
                return prepared[0]
        return None

    # The BDN preview's XML holds the pictures of its first events: so many are drawn for it.
    BDN_EVENTS = 6

    def _preview_picture(self, fmt, options, frame):
        from subtitld.interface import subtitle_renderer
        if not subtitle_renderer.available():
            self.preview_panel.show_note(_('export_dialog.preview_pictures'))
            return
        cue = self._picture_cue(fmt, options)
        if cue is None:
            self.preview_panel.show_nothing()
            return
        if self._renderer is None:
            self._renderer = subtitle_renderer.SubtitleRenderer(self, disk_cache=None)
            self._renderer.layer_ready.connect(self._picture_ready)
        # Drawn on the export's own frame, as the export draws it.
        self._renderer.size = (int(frame[0]), int(frame[1]))
        style = self._renderer._style()
        wanted = [(self._renderer._job(cue, float(cue['start']), *style)[0], cue)]
        drawable = None
        if fmt == 'BDN':
            # Its XML too, which says where each picture is and how large:
            # the first ones drawn for it, numbered as the export numbers them.
            drawable = [segment for segment in export_options.prepare(session.SUBTITLE.segments, options)
                        if markup.plain(text_of(segment)).strip()]
            wanted += [(self._renderer._job(segment, float(segment['start']), *style)[0], segment)
                       for segment in drawable[:self.BDN_EVENTS]]
        self._picture = {'keys': [key for key, _segment in wanted], 'frame': frame, 'fmt': fmt,
                         'options': options, 'drawable': drawable}
        if not self._picture_ready():
            for key, segment in wanted:
                if key not in self._renderer.cache:
                    self._renderer.layers([segment], float(segment['start']))
            self.preview_panel.show_note(_('export_dialog.preview_drawing'))
            self._picture_timeout.start()

    def _picture_ready(self):
        """Shows the picture awaited once the renderer has all it waits
        for; whether it had."""
        if self._picture is None or self._renderer is None:
            return False
        layers = [self._renderer.cache.get(key) for key in self._picture['keys']]
        if any(layer is None for layer in layers):
            return False
        picture, self._picture = self._picture, None
        self._picture_timeout.stop()
        text = None
        if picture['fmt'] == 'BDN':
            text = self._bdn_start(picture['drawable'], layers[1:], picture['frame'], picture['options'])
        self.preview_panel.show_picture(layers[0][0], layers[0][1], picture['frame'], text)
        return True

    @staticmethod
    def _bdn_start(drawable, layers, frame, options):
        """The start of the BDN XML the export writes: its description, for
        every subtitle, and the events of the first, whose `layers` are drawn."""
        source = session.SUBTITLE.filepath or session.VIDEO.get('filepath') or ''
        stem = os.path.splitext(os.path.basename(source))[0] or 'subtitle'
        unknown = '\0'                                        # a picture not drawn: where the start stops
        pictures = []
        for number, segment in enumerate(drawable, start=1):
            layer = layers[number - 1] if number <= len(layers) else None
            if layer is not None and (layer[0] is None or layer[1] is None):
                continue                                       # nothing drawn: the export leaves it out too
            rect, image = layer if layer is not None else (None, None)
            pictures.append({'start': float(segment['start']), 'end': float(segment['end']),
                             'href': f'{stem}_{number:05d}.png' if image is not None else unknown,
                             'x': round(rect.x() * frame[0]) if rect is not None else 0,
                             'y': round(rect.y() * frame[1]) if rect is not None else 0,
                             'width': image.width() if image is not None else 0,
                             'height': image.height() if image is not None else 0,
                             'forced': bool(segment.get('forced'))})
        language = (session.SUBTITLE.language or '').strip() or session.CONFIG.get('selected_language') or 'en'
        lines = bdn.write(pictures, frame, **file_io.bdn_options(options, language, stem + '.xml')).split('\n')
        cut = next((index for index, line in enumerate(lines) if unknown in line), None)
        if cut is not None:
            while cut > 0 and not lines[cut].lstrip().startswith('<Event '):
                cut -= 1
            lines = lines[:cut]
        return '\n'.join(lines[:200])

    def _picture_late(self):
        if self._picture is not None:
            self._picture = None
            self.preview_panel.show_note(_('export_dialog.preview_failed'))

    def _close_renderer(self, *_args):
        self._picture = None
        self._picture_timeout.stop()
        if self._renderer is not None:
            renderer, self._renderer = self._renderer, None
            renderer.close()
            renderer.deleteLater()

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
        if category == 'project':
            return self.project_panel.get_config()
        return self.documents_panel.get_config()
