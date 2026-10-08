"""Publishing the subtitles in the editor to subtitld.cc, the same three steps as on the website:

1. **Check**: the subtitles go up as USF (one language, no dubbing, speaker images or file paths) and
   subtitld.cc reports problems (some block publishing, some can be fixed with a click) and similar
   subtitles already there.
2. **Describe**: title, language, visibility… For the next version of a subtitle opened from
   subtitld.cc: what changed.
3. **Done**: the link.

If someone published a version after the one that was opened, subtitld.cc says so (409) and the
person chooses whether to publish anyway.
"""

import pathlib
import re
import uuid

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import session
from subtitld.modules import subtitldcc_service as service

KINDS = ('movie', 'episode', 'other')
VERSION_KINDS = ('fix', 'resync', 'translation')
VISIBILITIES = ('public', 'unlisted', 'restricted')
PUBLIC_LICENSE = 'CC-BY-4.0'
EPISODE = re.compile(r'(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?![0-9])')


def guess_episode(name):
    """("Example Series", 1, 4) from "Example.Series.S01E04.1080p.mkv", or None."""
    match = EPISODE.search(name or '')
    if not match:
        return None
    series = re.sub(r'[._]+', ' ', name[:match.start()]).strip(' -')
    return series, int(match.group(1)), int(match.group(2))


class _Pages(QStackedWidget):
    """Sized to the page on show, not the largest one, so the dialog fits each step."""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


def ask(parent, title, text, accept):
    """A yes/no question in Subtitld's dialog style. True when accepted."""
    dialog = utils.SimpleDialog(parent, title)
    message = QLabel(text)
    message.setWordWrap(True)
    dialog.content.layout().addWidget(message)
    dialog.accept_button.setText(accept)
    dialog.reject_button.setText(_('subtitldcc.cancel'))
    return bool(dialog.exec())


def _spin(maximum, minimum=0):
    """A number field that can be left empty (shown blank at its minimum)."""
    spin = QSpinBox()
    spin.setRange(minimum - 1, maximum)
    spin.setSpecialValueText(' ')
    spin.setValue(minimum - 1)
    return spin


def _value(spin):
    return spin.value() if spin.value() >= spin.minimum() + 1 else None


class PublishDialog(utils.SimpleDialog):
    def __init__(self, parent, target=None, video=None):
        """``target``: GET /assets/{id} of the subtitle opened from subtitld.cc (plus ``opened_version``)
        to publish the next version of it; None for a new subtitle. ``video``: the open video's
        fingerprint, if it's known already."""
        super().__init__(parent, _('subtitldcc.publish_dialog_title'))
        self.setMinimumWidth(520)
        self.target = target
        self.video = video
        self.upload = None
        self.published = None  # (share_id, version) once published
        self.translation = None
        self._key = str(uuid.uuid4())
        self._call = None

        self.pages = _Pages()
        self.content.layout().addWidget(self.pages)
        self.error = QLabel()
        self.error.setObjectName('subtitldcc_error')
        self.error.setWordWrap(True)
        self.error.hide()
        self.content.layout().addWidget(self.error)

        self.accept_button.clicked.disconnect()
        self.accept_button.clicked.connect(self._next)
        self.reject_button.setText(_('subtitldcc.cancel'))

        self._build_text_page()
        self._build_check_page()
        self._build_describe_page()
        self._build_done_page()

        if len(self._texts) > 1:
            self._show(self.text_page, _('subtitldcc.continue'))
        else:
            self.check()

    # --- Page plumbing -----------------------------------------------------------------------------------

    def _show(self, page, accept_label=None):
        self.pages.setCurrentWidget(page)
        self.accept_button.setVisible(accept_label is not None)
        if accept_label:
            self.accept_button.setText(accept_label)
        self.error.hide()
        self.pages.updateGeometry()
        self.adjustSize()

    def _fail(self, error):
        self._busy(False)
        self.error.setText(service.error_message(error, _))
        self.error.show()

    def _busy(self, busy):
        self.accept_button.setEnabled(not busy)

    def _next(self):
        page = self.pages.currentWidget()
        if page is self.text_page:
            self.translation = next(
                (language for (language, is_translation), radio in zip(self._texts, self._text_radios)
                 if radio.isChecked() and is_translation),
                None,
            )
            self.check()
        elif page is self.check_page:
            self._fill_describe()
            self._show(self.describe_page, _('subtitldcc.publish'))
        elif page is self.describe_page:
            self.publish()
        else:
            self.accept()

    def reject(self):
        # A checked file nobody published is dropped right away (it would expire in 7 days anyway).
        if self.upload and not self.published:
            upload_id = self.upload['id']
            service.call(lambda: service.client().discard_upload(upload_id))
        super().reject()

    # --- 0. Which text -----------------------------------------------------------------------------------

    def _build_text_page(self):
        self.text_page = QWidget()
        layout = QVBoxLayout(self.text_page)
        layout.setContentsMargins(0, 0, 0, 0)
        question = QLabel(_('subtitldcc.which_text'))
        question.setWordWrap(True)
        layout.addWidget(question)
        self._texts = service.text_languages()
        self._text_radios = []
        names = (service.cached_meta() or {}).get('language_names') or {}
        for language, is_translation in self._texts:
            name = names.get(service.bcp47(language, names), language or _('subtitldcc.unknown_language'))
            key = 'subtitldcc.text_translation' if is_translation else 'subtitldcc.text_original'
            radio = QRadioButton(_(key).format(language=name))
            radio.setChecked(not self._text_radios)
            self._text_radios.append(radio)
            layout.addWidget(radio)
        layout.addStretch()
        self.pages.addWidget(self.text_page)

    # --- 1. Check ----------------------------------------------------------------------------------------

    def _build_check_page(self):
        self.check_page = QWidget()
        layout = QVBoxLayout(self.check_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.check_status = QLabel()
        self.check_status.setWordWrap(True)
        layout.addWidget(self.check_status)
        self.problems = QVBoxLayout()
        self.problems.setSpacing(6)
        layout.addLayout(self.problems)
        layout.addStretch()
        self.pages.addWidget(self.check_page)

    def _file_name(self):
        """Named after the project or the video, so subtitld.cc can guess the title. Only the person
        publishing sees this name on subtitld.cc."""
        source = session.SUBTITLE.get('filepath') or session.VIDEO.get('filepath') or 'subtitles'
        return pathlib.Path(source).stem + '.usf'

    def check(self):
        self._show(self.check_page)
        self.check_status.setText(_('subtitldcc.checking'))
        self._clear_problems()
        data = service.usf_for_publishing(self.translation)
        name = self._file_name()
        target = self.target['share_id'] if self.target else None
        path, duration, known = session.VIDEO.get('filepath'), session.VIDEO.get('duration'), self.video

        def work():
            video = known
            if video is None and path:
                try:
                    video = service.video_fingerprint(path, duration)
                except OSError:
                    video = None
            service.meta()  # languages and licenses for the next step (fetched once)
            return service.client().check(name, data, target=target), video

        def done(answer):
            self.upload, self.video = answer
            self._render_check()

        self._call = service.call(work, done, self._check_failed)

    def _check_failed(self, error):
        self.check_status.setText('')
        self._fail(error)

    def _clear_problems(self):
        while self.problems.count():
            item = self.problems.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _render_check(self):
        self._clear_problems()
        upload = self.upload
        blocking = [p for p in upload['problems'] if p['severity'] == 'block']
        warnings = [p for p in upload['problems'] if p['severity'] != 'block']
        if blocking:
            self.check_status.setText(_('subtitldcc.check_blocked'))
        elif warnings:
            self.check_status.setText(_('subtitldcc.check_warnings').format(count=upload['cue_count']))
        else:
            self.check_status.setText(_('subtitldcc.check_ok').format(count=upload['cue_count']))
        for problem in blocking + warnings:
            self.problems.addWidget(self._problem_box(problem))
        for suggestion in upload.get('suggestions') or []:
            self.problems.addWidget(self._suggestion_box(suggestion))
        self._show(self.check_page, None if blocking else _('subtitldcc.continue'))

    def _problem_box(self, problem):
        box = QWidget()
        box.setObjectName('subtitldcc_problem')
        box.setProperty('severity', problem['severity'])
        box.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        title = QLabel(f"<b>{problem['title']}</b>")
        title.setWordWrap(True)
        layout.addWidget(title)
        text = QLabel(problem['text'])
        text.setWordWrap(True)
        layout.addWidget(text)
        if problem.get('action') in ('remove_links', 'repair_text'):
            button = QPushButton(_(f"subtitldcc.fix_{problem['action']}"))
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda: self._apply_fix(problem['action'], button))
            layout.addWidget(button, 0, Qt.AlignLeft)
        return box

    def _suggestion_box(self, suggestion):
        box = QWidget()
        box.setObjectName('subtitldcc_problem')
        box.setProperty('severity', 'info')
        box.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        text = QLabel(_(f"subtitldcc.suggestion_{suggestion['kind']}").format(
            title=suggestion['title'], owner=suggestion.get('owner') or ''))
        text.setWordWrap(True)
        layout.addWidget(text)
        button = QPushButton(_('subtitldcc.page'))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(service.site_url(suggestion['share_id']))))
        layout.addWidget(button, 0, Qt.AlignLeft)
        return box

    def _apply_fix(self, fix, button):
        button.setEnabled(False)
        upload_id = self.upload['id']
        fixes = list(dict.fromkeys([*self.upload.get('fixes', []), fix]))

        def done(upload):
            self.upload = upload
            self._render_check()

        self._call = service.call(lambda: service.client().update_upload(upload_id, fixes=fixes), done, self._fail)

    # --- 2. Describe -------------------------------------------------------------------------------------

    def _build_describe_page(self):
        self.describe_page = QWidget()
        grid = QGridLayout(self.describe_page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._grid = grid
        self._rows = {}

        self.version_intro = QLabel()
        self.version_intro.setWordWrap(True)
        grid.addWidget(self.version_intro, 0, 0, 1, 2)

        self.owner = QComboBox()
        self.kind = QComboBox()
        for kind in KINDS:
            self.kind.addItem(_(f'subtitldcc.kind_{kind}'), kind)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.series = QLineEdit()
        self.season = _spin(999)
        self.episode = _spin(9999)
        self.work_title = QLineEdit()
        self.year = _spin(2100, minimum=1870)
        self.language = QComboBox()
        self.flags = QWidget()
        flags = QVBoxLayout(self.flags)
        flags.setContentsMargins(0, 0, 0, 0)
        self.hearing_impaired = QCheckBox(_('subtitldcc.hearing_impaired'))
        self.forced = QCheckBox(_('subtitldcc.forced'))
        self.machine_translated = QCheckBox(_('subtitldcc.machine_translated'))
        for box in (self.hearing_impaired, self.forced, self.machine_translated):
            flags.addWidget(box)
        self.visibility = QComboBox()
        for visibility in VISIBILITIES:
            self.visibility.addItem(_(f'subtitldcc.visibility_{visibility}'), visibility)
        self.visibility.currentIndexChanged.connect(self._visibility_changed)
        self.license = QComboBox()
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(64)
        self.version_kind = QComboBox()
        for kind in VERSION_KINDS:
            self.version_kind.addItem(_(f'subtitldcc.version_kind_{kind}'), kind)
        self.changelog = QLineEdit()
        self.changelog.setMaxLength(500)
        self.with_video = QCheckBox(_('subtitldcc.with_video'))
        self.with_video.setToolTip(_('subtitldcc.with_video_tip'))
        self.show_name = QCheckBox(_('subtitldcc.show_video_name'))
        self.with_video.toggled.connect(self.show_name.setEnabled)

        fields = [
            ('owner', self.owner), ('kind', self.kind), ('series', self.series), ('season', self.season),
            ('episode', self.episode), ('work_title', self.work_title), ('year', self.year),
            ('language', self.language), ('flags', self.flags), ('visibility', self.visibility),
            ('license', self.license), ('description', self.description), ('version_kind', self.version_kind),
            ('changelog', self.changelog), ('video', self.with_video), ('video_name', self.show_name),
        ]
        for row, (key, widget) in enumerate(fields, start=1):
            label = QLabel(_(f'subtitldcc.field_{key}') if key not in ('video', 'video_name', 'flags') else '')
            label.setProperty('class', 'widget_label')
            grid.addWidget(label, row, 0, Qt.AlignTop | Qt.AlignRight)
            grid.addWidget(widget, row, 1)
            self._rows[key] = (label, widget)
        grid.setRowStretch(len(fields) + 1, 1)  # spare height goes below the fields, not between them
        self.pages.addWidget(self.describe_page)

    def _set_row(self, key, visible):
        for widget in self._rows[key]:
            widget.setVisible(visible)

    def _fill_describe(self):
        meta = service.cached_meta() or {}
        names = meta.get('language_names') or {}
        is_version = self.target is not None
        for key in ('owner', 'kind', 'series', 'season', 'episode', 'work_title', 'year', 'language', 'flags',
                    'visibility', 'license', 'description'):
            self._set_row(key, not is_version)
        for key in ('version_kind', 'changelog'):
            self._set_row(key, is_version)
        has_video = self.video is not None
        self._set_row('video', has_video)
        self._set_row('video_name', has_video)
        self.with_video.setChecked(has_video)
        self.version_intro.setVisible(is_version)
        if is_version:
            self.version_intro.setText(_('subtitldcc.version_intro').format(
                title=self.target.get('title', ''), version=(self.target.get('version') or 0) + 1))
            self.changelog.setFocus()
            return

        handles = service.account().get('publish_as') or [service.account().get('handle')]
        self.owner.clear()
        for handle in handles:
            self.owner.addItem(f'@{handle}', handle)
        self._set_row('owner', len(handles) > 1)

        self.language.clear()
        for code in meta.get('languages') or []:
            self.language.addItem(names.get(code, code), code)
        detected = self.upload.get('language') or ''
        wanted = service.bcp47(self.translation or session.SUBTITLE.get('language'), meta.get('languages') or ())
        index = self.language.findData(wanted or detected)
        self.language.setCurrentIndex(max(index, 0))

        self.license.clear()
        for item in meta.get('licenses') or [{'key': PUBLIC_LICENSE, 'name': 'CC BY 4.0'}]:
            self.license.addItem(item['name'], item['key'])

        video_name = pathlib.Path(session.VIDEO.get('filepath') or '').name
        episode = guess_episode(video_name)
        if episode:
            self.kind.setCurrentIndex(KINDS.index('episode'))
            self.series.setText(episode[0])
            self.season.setValue(episode[1])
            self.episode.setValue(episode[2])
        else:
            self.work_title.setText(self.upload.get('title_hint') or '')
            if self.upload.get('year_hint'):
                self.year.setValue(self.upload['year_hint'])
        self._kind_changed()
        self._visibility_changed()

    def _kind_changed(self):
        if self.target is not None:
            return
        kind = self.kind.currentData()
        for key in ('series', 'season', 'episode'):
            self._set_row(key, kind == 'episode')
        self._set_row('year', kind == 'movie')
        self._rows['work_title'][0].setText(
            _('subtitldcc.field_episode_title') if kind == 'episode' else _('subtitldcc.field_work_title'))

    def _visibility_changed(self):
        public = self.visibility.currentData() == 'public'
        if public:
            self.license.setCurrentIndex(max(self.license.findData(PUBLIC_LICENSE), 0))
        self.license.setEnabled(not public)
        self.license.setToolTip(_('subtitldcc.public_license') if public else '')

    # --- 3. Publish --------------------------------------------------------------------------------------

    def _video_payload(self):
        if not (self.video and self.with_video.isChecked()):
            return None
        return service.video_for_publishing(self.video, show_name=self.show_name.isChecked())

    def publish(self, parent=None):
        upload_id = self.upload['id']
        video = self._video_payload()
        if self.target is not None:
            changelog = self.changelog.text().strip()
            if not changelog:
                self.error.setText(_('subtitldcc.changelog_required'))
                self.error.show()
                return
            share_id = self.target['share_id']
            parent = parent if parent is not None else (self.target.get('opened_version') or 0)
            kind, key = self.version_kind.currentData(), self._key

            def work():
                return service.client().new_version(share_id, upload_id, parent=parent, changelog=changelog,
                                                    kind=kind, video=video, idempotency_key=key)
        else:
            fields = {
                'owner': self.owner.currentData() or '',
                'visibility': self.visibility.currentData(),
                'license': self.license.currentData() or '',
                'description': self.description.toPlainText().strip(),
                'kind': self.kind.currentData(),
                'work_title': self.work_title.text().strip(),
                'language': self.language.currentData() or '',
                'hearing_impaired': self.hearing_impaired.isChecked(),
                'forced': self.forced.isChecked(),
                'machine_translated': self.machine_translated.isChecked(),
            }
            if fields['kind'] == 'movie' and _value(self.year):
                fields['year'] = _value(self.year)
            if fields['kind'] == 'episode':
                fields['series_title'] = self.series.text().strip()
                fields['season'] = _value(self.season)
                fields['episode'] = _value(self.episode)
            if video:
                fields['video'] = video
            key = self._key

            def work():
                return service.client().publish(upload_id, idempotency_key=key, **fields)

        self._busy(True)
        self.error.hide()
        self._call = service.call(work, self._published, self._publish_failed)

    def _publish_failed(self, error):
        self._busy(False)
        if service.error_code(error) == 'version_conflict':
            latest = error.body.get('latest')
            if ask(self, _('subtitldcc.conflict_title'),
                         _('subtitldcc.conflict_text').format(opened=self.target.get('opened_version') or 0,
                                                              latest=latest),
                         _('subtitldcc.publish_anyway')):
                self._key = str(uuid.uuid4())  # a different request now
                self.publish(parent=latest)
            return
        self._fail(error)

    def _published(self, answer):
        self._busy(False)
        asset = answer['asset']
        self.published = (asset['share_id'], answer['version'])
        self.done_text.setText(_('subtitldcc.published_text').format(
            title=asset.get('title', ''), version=answer['version']))
        self.done_link.setText(asset['url'])
        self.reject_button.hide()
        self._show(self.done_page, _('subtitldcc.close'))

    # --- 4. Done -----------------------------------------------------------------------------------------

    def _build_done_page(self):
        self.done_page = QWidget()
        layout = QVBoxLayout(self.done_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QLabel(f"<b>{_('subtitldcc.published')}</b>")
        layout.addWidget(heading)
        self.done_text = QLabel()
        self.done_text.setWordWrap(True)
        layout.addWidget(self.done_text)
        self.done_link = QLineEdit()
        self.done_link.setReadOnly(True)
        layout.addWidget(self.done_link)
        copy = QPushButton(_('subtitldcc.copy_link'))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.done_link.text()))
        open_page = QPushButton(_('subtitldcc.page'))
        open_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.done_link.text())))
        for button in (copy, open_page):
            button.setCursor(Qt.PointingHandCursor)
            layout.addWidget(button, 0, Qt.AlignLeft)
        layout.addStretch()
        self.pages.addWidget(self.done_page)
