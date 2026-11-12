"""The dialog that reads picture subtitles (VobSub, PGS, a BDN, IMSC's Image Profile, a DCP's) as text with an OCR add-on.

Opening such a file, Subtitld asks for the subtitles' language, which an
OCR engine needs to know (a VobSub's .idx, a BDN, a TTML file and a DCP's
XML name it; a .sup never does),
then reads them with a progress bar the user can stop
(modules.picture_ocr.OCRJob). Without an OCR add-on installed, the dialog
says which one to install instead.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar

from subtitld.interface import utils
from subtitld.interface.translation import _
from subtitld.modules import picture_ocr, session


def _language_guess(path):
    """Subtitld's code for the language the file names, else the project's,
    else the transcription settings', else English."""
    if str(path).lower().endswith('.xml') and picture_ocr._is_dcp(path):
        from subtitld.modules import dcp, file_io
        try:
            code = file_io._language_like_project(dcp.read_images(path)[2]['language'])
        except (OSError, ValueError):
            code = ''
        if code:
            return code
    elif str(path).lower().endswith('.xml') and picture_ocr._is_bdn(path):
        from subtitld.modules import bdn, file_io
        try:
            code = file_io._language_like_project(bdn.read(path)[1]['language'])
        except (OSError, ValueError):
            code = ''
        if code:
            return code
    elif str(path).lower().endswith(picture_ocr.TTML_EXTENSIONS):
        from subtitld.modules import ttml
        try:
            code = picture_ocr.project_language(ttml.read_images(path)[1])
        except (OSError, ValueError, SyntaxError):
            code = ''
        if code:
            return code
    elif not str(path).lower().endswith('.sup'):
        from subtitld.modules import vobsub
        idx, _sub = vobsub.paths(path)
        try:
            with open(idx, 'rb') as handle:
                info = vobsub.parse_idx(handle.read().decode('utf-8', 'replace'))
            track = next((t for t in info['tracks'] if t['index'] == info['langidx']),
                         info['tracks'][0] if info['tracks'] else None)
            code = picture_ocr.project_language(track['language']) if track else ''
            if code:
                return code
        except OSError:
            pass
    return (session.SUBTITLE.get('language') or (session.CONFIG.get('transcription') or {}).get('language')
            or 'en-us')


def read_file(window, path):
    """Read the picture subtitles in the file at `path`: (segments, info),
    or None when the user stops, or when no OCR add-on is installed (the
    dialog then says so)."""
    provider = picture_ocr.ocr_provider()
    if provider is None:
        dialog = utils.SimpleDialog(window, title=_('startscreen.picture_subtitles_title'))
        message = QLabel(_('picture_ocr.no_addon').format(filepath=os.path.basename(path)))
        message.setWordWrap(True)
        dialog.content.layout().addWidget(message)
        dialog.reject_button.hide()
        dialog.exec()
        return None
    return run(window, lambda language: picture_ocr.OCRJob.for_file(path, provider, language),
               os.path.basename(path), _language_guess(path), provider.display_name)


def run(window, make_job, name, language, engine):
    """The dialog: the language, then the reading. `make_job(language)` gives
    the OCRJob. (segments, info), or None when the user stops."""
    dialog = utils.SimpleDialog(window, title=_('picture_ocr.title'))
    message = QLabel(_('picture_ocr.message').format(filepath=name, engine=engine))
    message.setWordWrap(True)
    dialog.content.layout().addWidget(message)

    languages = utils.LabeledComboBox()
    languages.setLabel(_('picture_ocr.language'))
    for title, code in session.LANGUAGE_DICT_LIST.items():
        languages.combobox.addItem(title, code)
    found = languages.combobox.findData(language)
    if found < 0:
        found = next((i for i in range(languages.combobox.count())
                      if str(languages.combobox.itemData(i)).split('-')[0] == str(language).split('-')[0]), 0)
    languages.combobox.setCurrentIndex(found)
    dialog.content.layout().addWidget(languages)

    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setTextVisible(False)
    bar.hide()
    dialog.content.layout().addWidget(bar)
    status = QLabel('')
    status.setWordWrap(True)
    status.setProperty('class', 'dialog_detail')
    status.hide()
    dialog.content.layout().addWidget(status)

    dialog.accept_button.setText(_('picture_ocr.read'))
    state = {'job': None, 'result': None}

    def moved(value, text):
        bar.setValue(int(value * 1000))
        if text:
            status.setText(text)
            status.show()

    def finished(segments, info):
        state['job'] = None
        state['result'] = (segments, info)
        dialog.done(1)

    def failed(error):
        state['job'] = None
        bar.hide()
        status.setText(_('picture_ocr.failed').format(error=error))
        status.show()
        languages.setEnabled(True)
        dialog.accept_button.setEnabled(True)

    def start():
        languages.setEnabled(False)
        dialog.accept_button.setEnabled(False)
        status.setText(_('picture_ocr.reading'))
        status.show()
        bar.setValue(0)
        bar.show()
        job = make_job(languages.combobox.currentData() or '')
        job.progress.connect(moved)
        job.finished.connect(finished)
        job.failed.connect(failed)
        state['job'] = job
        job.start()

    # The OK button starts the reading; the dialog closes when it is done.
    dialog.accept_button.clicked.disconnect()
    dialog.accept_button.clicked.connect(start)
    code = dialog.exec()
    if state['job'] is not None:
        state['job'].cancel()
        state['job'] = None
    if code and state['result'] is not None:
        return state['result']
    return None
