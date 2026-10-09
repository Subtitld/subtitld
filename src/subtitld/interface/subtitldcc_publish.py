"""Publishing the subtitles in the editor to subtitld.cc, inside the subtitld.cc panel. Never a dialog: the
upload and every other call run in the background, so Subtitld stays usable the whole time.

The same three steps as on the website:

1. **Check**: the subtitles go up as USF (one language, no dubbing, speaker images or file paths) and
   subtitld.cc reports problems (some block publishing, some can be fixed with a click) and similar
   subtitles already there. What goes up is the subtitles as they are at that moment; if they change
   afterwards, Publish offers to check them again.
2. **Describe**: title, language, visibility… For the next version of a subtitle opened from
   subtitld.cc: what changed.
3. **Done**: the link.

If someone published a version after the one that was opened, subtitld.cc says so (409) and the
person chooses, right there, whether to publish anyway.
"""

import pathlib
import re
import uuid

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

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


def _spin(maximum, minimum=0):
    """A number field that can be left empty (shown blank at its minimum)."""
    spin = QSpinBox()
    spin.setRange(minimum - 1, maximum)
    spin.setSpecialValueText(' ')
    spin.setValue(minimum - 1)
    return spin


def _value(spin):
    return spin.value() if spin.value() >= spin.minimum() + 1 else None


def _text(object_name='subtitldcc_text'):
    label = QLabel()
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


def _button(text='', primary=False, small=True):
    button = QPushButton(text)
    if primary:
        button.setProperty('class', 'primary')
    if small:
        button.setObjectName('subtitldcc_small_button')
    button.setCursor(Qt.PointingHandCursor)
    return button


def _row(*widgets, stretch_at=None):
    row = QWidget()
    row.setProperty('class', 'transparent_panel')
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for index, widget in enumerate(widgets):
        if index == stretch_at:
            layout.addStretch()
        layout.addWidget(widget)
    if stretch_at is None or stretch_at >= len(widgets):
        layout.addStretch()
    return row


def _page():
    page = QWidget()
    page.setProperty('class', 'transparent_panel')
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    return page, layout


class PublishFlow(QWidget):
    """The publish steps, shown in the panel's Publish card. Emits ``published`` (share_id, version) as soon
    as subtitld.cc has it, and ``finished`` once when the steps close: with (share_id, version) after
    publishing, or None when canceled."""

    published_now = Signal(object)
    finished = Signal(object)

    def __init__(self, target=None, video=None):
        """``target``: GET /assets/{id} of the subtitle opened from subtitld.cc (plus ``opened_version``)
        to publish the next version of it; None for a new subtitle. ``video``: the open video's
        fingerprint, if it's known already."""
        super().__init__()
        self.setProperty('class', 'transparent_panel')
        self.target = target
        self.video = video
        self.upload = None
        self.published = None  # (share_id, version) once published
        self.translation = None
        self._checked = None  # the USF that was checked
        self._key = str(uuid.uuid4())
        self._call = None
        self._ended = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.heading = _text('subtitldcc_kicker')
        layout.addWidget(self.heading)

        self.pages = []
        self._build_text_page(layout)
        self._build_check_page(layout)
        self._build_describe_page(layout)
        self._build_done_page(layout)

        # Questions asked in place (the subtitles changed since the check; a newer version exists).
        self.question = QWidget()
        self.question.setObjectName('subtitldcc_problem')
        self.question.setProperty('severity', 'warn')
        self.question.setAttribute(Qt.WA_StyledBackground, True)
        question = QVBoxLayout(self.question)
        question.setContentsMargins(10, 8, 10, 8)
        self.question_text = _text()
        question.addWidget(self.question_text)
        self.question_yes = _button(primary=True)
        self.question_no = _button()
        question.addWidget(_row(self.question_no, self.question_yes))
        self.question.hide()
        layout.addWidget(self.question)

        self.error = _text('subtitldcc_error')
        self.error.hide()
        layout.addWidget(self.error)

        self.cancel_button = _button(_('subtitldcc.cancel'))
        self.cancel_button.clicked.connect(self.cancel)
        self.next_button = _button(primary=True)
        self.next_button.clicked.connect(self._next)
        layout.addWidget(_row(self.cancel_button, self.next_button, stretch_at=1))

        self.heading.setText(
            _('subtitldcc.publishing_version').format(version=(target.get('version') or 0) + 1) if target
            else _('subtitldcc.publishing_new'))

    def start(self):
        if len(self._texts) > 1:
            self._show(self.text_page, _('subtitldcc.continue'))
        else:
            self.check()

    # --- Plumbing ----------------------------------------------------------------------------------------

    def _show(self, page, next_label=None):
        for other in self.pages:
            other.setVisible(other is page)
        self.next_button.setVisible(next_label is not None)
        self.next_button.setEnabled(True)
        if next_label:
            self.next_button.setText(next_label)
        self.error.hide()
        self.question.hide()
        QTimer.singleShot(0, self._reveal)

    def _reveal(self):
        """Scroll the panel so the step and its buttons are in view."""
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is not None and not self._ended:
            parent.ensureWidgetVisible(self.next_button if self.next_button.isVisible() else self.heading)

    def _busy(self, busy, label=None):
        self.next_button.setEnabled(not busy)
        if label:
            self.next_button.setText(label)

    def _fail(self, error):
        self._busy(False, _("subtitldcc.publish") if self.describe_page.isVisibleTo(self) else None)
        self.error.setText(service.error_message(error, _))
        self.error.show()

    def _ask(self, text, yes, on_yes, no=None, on_no=None):
        self.question_text.setText(text)
        self.question_yes.setText(yes)
        self.question_no.setText(no or _('subtitldcc.cancel'))
        for button in (self.question_yes, self.question_no):
            try:
                button.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.question_yes.clicked.connect(lambda: (self.question.hide(), on_yes()))
        self.question_no.clicked.connect(lambda: (self.question.hide(), on_no() if on_no else None))
        self.question.show()

    def _next(self):
        if self.text_page.isVisibleTo(self):
            self.translation = next(
                (language for (language, is_translation), radio in zip(self._texts, self._text_radios)
                 if radio.isChecked() and is_translation),
                None,
            )
            self.check()
        elif self.check_page.isVisibleTo(self):
            self._fill_describe()
            self._show(self.describe_page, _('subtitldcc.publish'))
        elif self.describe_page.isVisibleTo(self):
            self.publish()
        else:
            self._end()

    def cancel(self):
        # A checked file nobody published is dropped right away (it would expire in 7 days anyway).
        if self.upload and not self.published:
            upload_id = self.upload['id']
            service.call(lambda: service.client().discard_upload(upload_id))
        self._end()

    def _end(self):
        if not self._ended:
            self._ended = True
            self.finished.emit(self.published)

    # --- 0. Which text -----------------------------------------------------------------------------------

    def _build_text_page(self, layout):
        self.text_page, page = _page()
        question = _text()
        question.setText(_('subtitldcc.which_text'))
        page.addWidget(question)
        self._texts = service.text_languages()
        self._text_radios = []
        names = (service.cached_meta() or {}).get('language_names') or {}
        for language, is_translation in self._texts:
            name = names.get(service.bcp47(language, names), language or _('subtitldcc.unknown_language'))
            key = 'subtitldcc.text_translation' if is_translation else 'subtitldcc.text_original'
            radio = QRadioButton(_(key).format(language=name))
            radio.setChecked(not self._text_radios)
            self._text_radios.append(radio)
            page.addWidget(radio)
        self.pages.append(self.text_page)
        layout.addWidget(self.text_page)

    # --- 1. Check ----------------------------------------------------------------------------------------

    def _build_check_page(self, layout):
        self.check_page, page = _page()
        self.check_status = _text()
        page.addWidget(self.check_status)
        self.problems = QVBoxLayout()
        self.problems.setSpacing(6)
        page.addLayout(self.problems)
        self.check_again_button = _button(_('subtitldcc.check_again'))
        self.check_again_button.clicked.connect(self.check)
        page.addWidget(_row(self.check_again_button))
        self.pages.append(self.check_page)
        layout.addWidget(self.check_page)

    def _file_name(self):
        """Named after the project or the video, so subtitld.cc can guess the title. Only the person
        publishing sees this name on subtitld.cc."""
        source = session.SUBTITLE.get('filepath') or session.VIDEO.get('filepath') or 'subtitles'
        return pathlib.Path(source).stem + '.usf'

    def check(self):
        if self.upload and not self.published:  # checking again: the earlier file isn't needed
            upload_id = self.upload['id']
            service.call(lambda: service.client().discard_upload(upload_id))
            self.upload = None
        self._show(self.check_page)
        self.check_again_button.hide()
        self.check_status.setText(_('subtitldcc.checking'))
        self._clear_problems()
        data = service.usf_for_publishing(self.translation)
        self._checked = data
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

        def failed(error):
            self.check_status.setText('')
            self.check_again_button.show()
            self._fail(error)

        self._call = service.call(work, done, failed)

    def _clear_problems(self):
        while self.problems.count():
            item = self.problems.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _render_check(self):
        if self._ended:
            return
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
        self.check_again_button.show()

    def _box(self, severity):
        box = QWidget()
        box.setObjectName('subtitldcc_problem')
        box.setProperty('severity', severity)
        box.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        return box, layout

    def _problem_box(self, problem):
        box, layout = self._box(problem['severity'])
        title = _text('subtitldcc_result_title')
        title.setText(problem['title'])
        layout.addWidget(title)
        text = _text()
        text.setText(problem['text'])
        layout.addWidget(text)
        if problem.get('action') in ('remove_links', 'repair_text'):
            button = _button(_(f"subtitldcc.fix_{problem['action']}"))
            button.clicked.connect(lambda: self._apply_fix(problem['action'], button))
            layout.addWidget(_row(button))
        return box

    def _suggestion_box(self, suggestion):
        box, layout = self._box('info')
        text = _text()
        text.setText(_(f"subtitldcc.suggestion_{suggestion['kind']}").format(
            title=suggestion['title'], owner=suggestion.get('owner') or ''))
        layout.addWidget(text)
        button = _button(_('subtitldcc.page'))
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(service.site_url(suggestion['share_id']))))
        layout.addWidget(_row(button))
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

    def _field(self, page, key, widget):
        """A label above its field: the panel is narrow."""
        box = QWidget()
        box.setProperty('class', 'transparent_panel')
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = _text('subtitldcc_wlabel')
        label.setText(_(f'subtitldcc.field_{key}'))
        layout.addWidget(label)
        layout.addWidget(widget)
        page.addWidget(box)
        self._fields[key] = (box, label)
        return box

    def _build_describe_page(self, layout):
        self.describe_page, page = _page()
        self._fields = {}
        self.version_intro = _text()
        page.addWidget(self.version_intro)

        self.owner = QComboBox()
        self._field(page, 'owner', self.owner)
        self.kind = QComboBox()
        for kind in KINDS:
            self.kind.addItem(_(f'subtitldcc.kind_{kind}'), kind)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._field(page, 'kind', self.kind)
        self.series = QLineEdit()
        self._field(page, 'series', self.series)
        self.season = _spin(999)
        self.episode = _spin(9999)
        numbers = QWidget()
        numbers.setProperty('class', 'transparent_panel')
        numbers_layout = QHBoxLayout(numbers)
        numbers_layout.setContentsMargins(0, 0, 0, 0)
        numbers_layout.setSpacing(10)
        for key, spin in (('season', self.season), ('episode', self.episode)):
            column = QWidget()
            column.setProperty('class', 'transparent_panel')
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(3)
            label = _text('subtitldcc_wlabel')
            label.setText(_(f'subtitldcc.field_{key}'))
            column_layout.addWidget(label)
            column_layout.addWidget(spin)
            numbers_layout.addWidget(column)
        page.addWidget(numbers)
        self._fields['numbers'] = (numbers, None)
        self.work_title = QLineEdit()
        self._field(page, 'work_title', self.work_title)
        self.year = _spin(2100, minimum=1870)
        self._field(page, 'year', self.year)
        self.language = QComboBox()
        self._field(page, 'language', self.language)
        self.hearing_impaired = QCheckBox(_('subtitldcc.hearing_impaired'))
        self.forced = QCheckBox(_('subtitldcc.forced'))
        self.machine_translated = QCheckBox(_('subtitldcc.machine_translated'))
        for box in (self.hearing_impaired, self.forced, self.machine_translated):
            page.addWidget(box)
        self.visibility = QComboBox()
        for visibility in VISIBILITIES:
            self.visibility.addItem(_(f'subtitldcc.visibility_{visibility}'), visibility)
        self.visibility.currentIndexChanged.connect(self._visibility_changed)
        self._field(page, 'visibility', self.visibility)
        self.license = QComboBox()
        self._field(page, 'license', self.license)
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(64)
        self._field(page, 'description', self.description)
        self.version_kind = QComboBox()
        for kind in VERSION_KINDS:
            self.version_kind.addItem(_(f'subtitldcc.version_kind_{kind}'), kind)
        self._field(page, 'version_kind', self.version_kind)
        self.changelog = QLineEdit()
        self.changelog.setMaxLength(500)
        self._field(page, 'changelog', self.changelog)
        self.with_video = QCheckBox(_('subtitldcc.with_video'))
        self.with_video.setToolTip(_('subtitldcc.with_video_tip'))
        self.show_name = QCheckBox(_('subtitldcc.show_video_name'))
        self.with_video.toggled.connect(self.show_name.setEnabled)
        page.addWidget(self.with_video)
        page.addWidget(self.show_name)
        self._new_only = [self.hearing_impaired, self.forced, self.machine_translated]
        self.pages.append(self.describe_page)
        layout.addWidget(self.describe_page)

    def _set_field(self, key, visible):
        self._fields[key][0].setVisible(visible)

    def _fill_describe(self):
        meta = service.cached_meta() or {}
        names = meta.get('language_names') or {}
        is_version = self.target is not None
        for key in ('owner', 'kind', 'series', 'numbers', 'work_title', 'year', 'language', 'visibility',
                    'license', 'description'):
            self._set_field(key, not is_version)
        for widget in self._new_only:
            widget.setVisible(not is_version)
        for key in ('version_kind', 'changelog'):
            self._set_field(key, is_version)
        has_video = self.video is not None
        self.with_video.setVisible(has_video)
        self.show_name.setVisible(has_video)
        self.with_video.setChecked(has_video)
        self.version_intro.setVisible(is_version)
        if is_version:
            self.version_intro.setText(_('subtitldcc.version_intro').format(
                title=self.target.get('title', ''), version=(self.target.get('version') or 0) + 1))
            return

        handles = service.account().get('publish_as') or [service.account().get('handle')]
        self.owner.clear()
        for handle in handles:
            self.owner.addItem(f'@{handle}', handle)
        self._set_field('owner', len(handles) > 1)

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
        for key in ('series', 'numbers'):
            self._set_field(key, kind == 'episode')
        self._set_field('year', kind == 'movie')
        self._fields['work_title'][1].setText(
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

    def publish(self, parent=None, as_checked=False):
        # What was checked is what gets published: say so if the subtitles changed since.
        if not as_checked and service.usf_for_publishing(self.translation) != self._checked:
            self._ask(_('subtitldcc.changed_since_check'), _('subtitldcc.check_again'), self.check,
                      _('subtitldcc.publish_as_checked'), lambda: self.publish(parent, as_checked=True))
            return
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

        self._busy(True, _('subtitldcc.publishing'))
        self.error.hide()
        self._call = service.call(work, self._published, self._publish_failed)

    def _publish_failed(self, error):
        if service.error_code(error) == 'version_conflict':
            self._busy(False, _('subtitldcc.publish'))
            latest = error.body.get('latest')

            def anyway():
                self._key = str(uuid.uuid4())  # a different request now
                self.publish(parent=latest, as_checked=True)

            text = _('subtitldcc.conflict_text').format(opened=self.target.get('opened_version') or 0, latest=latest)
            self._ask(text, _('subtitldcc.publish_anyway'), anyway)
            return
        self._fail(error)

    def _published(self, answer):
        asset = answer['asset']
        self.published = (asset['share_id'], answer['version'])
        self.done_text.setText(_('subtitldcc.published_text').format(
            title=asset.get('title', ''), version=answer['version']))
        self.done_link.setText(asset['url'])
        self.cancel_button.hide()
        self._show(self.done_page, _('subtitldcc.done'))
        self.published_now.emit(self.published)

    # --- 4. Done -----------------------------------------------------------------------------------------

    def _build_done_page(self, layout):
        self.done_page, page = _page()
        heading = _text('subtitldcc_result_title')
        heading.setText(_('subtitldcc.published'))
        page.addWidget(heading)
        self.done_text = _text()
        page.addWidget(self.done_text)
        self.done_link = QLineEdit()
        self.done_link.setReadOnly(True)
        page.addWidget(self.done_link)
        copy = _button(_('subtitldcc.copy_link'))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.done_link.text()))
        open_page = _button(_('subtitldcc.page'))
        open_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.done_link.text())))
        page.addWidget(_row(copy, open_page))
        next_time = _text()
        next_time.setText(_('subtitldcc.published_next'))
        page.addWidget(next_time)
        self.pages.append(self.done_page)
        layout.addWidget(self.done_page)
