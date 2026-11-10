"""Picture subtitles read as text by an OCR add-on.

Opening a DVD VobSub (.idx/.sub), a Blu-ray PGS (.sup) or a TTML file in
IMSC's Image Profile, or reading a picture track of a video, Subtitld
decodes each subtitle into a picture (vobsub, pgs, `imsc_pictures`), and an
add-on serving `ocr.recognize` (Tesseract, PP-OCRv5) reads the text in it.
A picture whose words the file gives (IMSC's alt text) is not read: the
file's words are better than any reading of them. `OCRJob` runs the whole
of it off the main thread:

1. decode the pictures, in a thread (0-10 %);
2. crop each to what shows and write it as a grayscale PGM, dark text on
   light (picture_subtitles.pgm), in a folder of its own under the session's
   temporary folder;
3. ask the add-on for the text in all of them (10-95 %);
4. read again, laid out the other way (dark letters outlined in light), the
   pictures read with little confidence or none, and keep the better (95-100 %);
5. make subtitles of the texts: `finished(segments, info)`, or `failed`.

`info` holds the language the pictures said they are in ('' when they say
nothing), Subtitld's code for it, and how many pictures gave no text.
"""

import os
import shutil
import tempfile
import urllib.parse

from PySide6.QtCore import QObject, QThread, Signal, Slot

from subtitld.modules import picture_subtitles, session
from subtitld.modules.addons.provider import TASK_OCR_RECOGNIZE

# Pictures read with less confidence than this are read again the other way.
RETRY_BELOW = 0.6
PICTURE_EXTENSIONS = ('.sup', '.idx')


def ocr_provider():
    """The OCR engine to use, or None when no add-on serves `ocr.recognize`."""
    from subtitld.modules.addons.manager import get_manager
    manager = get_manager()
    return manager.default_for_task(TASK_OCR_RECOGNIZE)


def is_picture_file(path):
    """Whether the file at `path` holds picture subtitles: a .sup, an .idx,
    or a .sub that is a VobSub (not MicroDVD's text)."""
    lowered = str(path).lower()
    if lowered.endswith(PICTURE_EXTENSIONS):
        return True
    if lowered.endswith('.sub'):
        try:
            with open(path, 'rb') as handle:
                return handle.read(4) == b'\x00\x00\x01\xba'
        except OSError:
            return False
    return False


# What a TTML file is called; any of them may be in IMSC's Image Profile.
TTML_EXTENSIONS = ('.ttml', '.dfxp', '.xml', '.itt')
# The picture a TTML file that gives no size of its own is laid on.
_IMSC_SCREEN = (1920, 1080)


def read_file(path):
    """(pictures, language tag the file gives or '') of a picture subtitle file."""
    from subtitld.modules import pgs, vobsub
    if str(path).lower().endswith('.sup'):
        return pgs.read(path), ''
    if str(path).lower().endswith(TTML_EXTENSIONS):
        return imsc_pictures(path)
    found, track = vobsub.read(path)
    return found, track.get('language') or ''


def imsc_pictures(path):
    """(pictures, language tag) of a TTML file in IMSC's Image Profile: each
    PNG where its region puts it, on the picture the file gives the size
    of, else a 1920x1080 one, with the words its alt text gives."""
    from subtitld.modules import ttml
    images, language, size = ttml.read_images(path)
    screen = tuple(int(round(side)) for side in size) if size else _IMSC_SCREEN
    folder = os.path.dirname(os.path.abspath(path))
    pictures = []
    for image in images:
        data = image['data']
        if data is None and image['href']:
            href = urllib.parse.unquote(urllib.parse.urlparse(image['href']).path or image['href'])
            try:
                with open(os.path.join(folder, href), 'rb') as handle:
                    data = handle.read()
            except OSError:
                data = None
        decoded = png_picture(data) if data else None
        if decoded is None:
            if not image['alt']:
                continue
            # No picture to be had, but words: one transparent pixel says them.
            decoded = (1, 1, b'\x00', [(0, 0, 0, 0)])
        width, height, indices, colours = decoded
        origin = image['origin'] or (0.0, 0.0)
        pictures.append(picture_subtitles.Picture(
            image['start'], image['end'], int(round(origin[0] * screen[0] / 100)),
            int(round(origin[1] * screen[1] / 100)), width, height, indices, colours, image['forced'],
            screen[0], screen[1], image['alt']))
    return pictures, language


def png_picture(data):
    """(width, height, indices, colours) for PNG bytes, as a Picture holds
    them, or None when they are not a picture.

    A PNG is in true colour, and a Picture in at most 256 of them. What an
    OCR reading needs of a pixel is how light it is and how much of it
    shows (picture_subtitles.ink), so the palette is 16 of each: grays
    from black to white at 16 opacities, which is exactly what is drawn.
    """
    import numpy
    from PySide6.QtGui import QImage
    image = QImage.fromData(data, 'PNG')
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    rows = numpy.frombuffer(image.constBits(), numpy.uint8, count=image.sizeInBytes())
    rgba = rows.reshape(height, image.bytesPerLine())[:, :width * 4].reshape(height, width, 4).astype(numpy.float32)
    luma = 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
    indices = (numpy.rint(luma / 17) * 16 + numpy.rint(rgba[..., 3] / 17)).astype(numpy.uint8)
    colours = [(level * 17, level * 17, level * 17, alpha * 17) for level in range(16) for alpha in range(16)]
    return width, height, indices.tobytes(), colours


def project_language(tag):
    """Subtitld's code for a language tag a disc gives ('en', 'fre', 'pt-BR'), or ''."""
    from subtitld.modules import file_io
    return file_io._known_language(tag) if tag else ''


class _Decoder(QThread):
    """Decodes the pictures and writes them out, off the main thread."""
    decoded = Signal(list, str, list)              # pictures, language tag, PGM paths
    failed = Signal(str)

    def __init__(self, read, folder):
        super().__init__()
        self.read, self.folder = read, folder
        self.stopped = False
        self.process = None

    def started_process(self, process):
        self.process = process
        if self.stopped:
            process.kill()

    def run(self):
        try:
            found, language = self.read(self)
            pictures, paths = [], []
            for number, picture in enumerate(found):
                if self.stopped:
                    return
                cropped = picture_subtitles.crop(picture)
                if cropped is None:
                    # Nothing shows, but the file gives its words: kept for them.
                    if not picture.text:
                        continue
                    cropped = picture
                target = os.path.join(self.folder, f'{number:05d}.pgm')
                with open(target, 'wb') as handle:
                    handle.write(picture_subtitles.pgm(cropped))
                pictures.append(cropped)
                paths.append(target)
        except Exception as error:                   # an unreadable file or track, FFmpeg stopped
            if not self.stopped:
                self.failed.emit(str(error) or type(error).__name__)
            return
        if not self.stopped:
            self.decoded.emit(pictures, language, paths)

    def stop(self):
        self.stopped = True
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


class OCRJob(QObject):
    """Picture subtitles to text subtitles. `read(decoder)` returns
    (pictures, language tag); `decoder.started_process(process)` lets it
    hand over an FFmpeg process to stop. `language` is the subtitles'
    language when the caller knows it better than the pictures do."""

    progress = Signal(float, str)
    finished = Signal(list, dict)
    failed = Signal(str)

    def __init__(self, read, provider, language='', parent=None):
        super().__init__(parent)
        self._read = read
        self._provider = provider
        self._language = language
        self._folder = None
        self._decoder = None
        self._pictures = []
        self._paths = []
        self._texts = []
        self._confidences = []
        self._retry = []
        self._unread = []
        self._stage = 'idle'
        self._connected = False

    @classmethod
    def for_file(cls, path, provider, language='', parent=None):
        return cls(lambda _decoder: read_file(path), provider, language, parent)

    @classmethod
    def for_track(cls, video, index, codec, language, provider, parent=None):
        from subtitld.modules import video_subtitles

        def read(decoder):
            return video_subtitles.extract_pictures(
                video, index, codec, ffmpeg=session.FFMPEG_EXECUTABLE, ffprobe=session.FFPROBE_EXECUTABLE,
                folder=session.PATH_TEMP, startupinfo=session.STARTUPINFO, started=decoder.started_process), ''
        return cls(read, provider, language, parent)

    def start(self):
        self._folder = tempfile.mkdtemp(prefix='subtitld_ocr_', dir=session.PATH_TEMP)
        self._stage = 'decoding'
        self.progress.emit(0.0, '')
        decoder = _Decoder(self._read, self._folder)
        decoder.decoded.connect(self._decoded)
        decoder.failed.connect(self._fail)
        self._decoder = decoder
        decoder.start()

    @Slot(list, str, list)
    def _decoded(self, pictures, language, paths):
        if self._stage != 'decoding':
            return
        self._pictures, self._paths = pictures, paths
        self._language = self._language or language
        # The pictures whose words the file gives are not read again.
        self._texts = [picture.text for picture in pictures]
        self._confidences = [1.0 if picture.text else 0.0 for picture in pictures]
        self._unread = [number for number, picture in enumerate(pictures) if not picture.text]
        if not self._unread:
            self._finish()
            return
        self._stage = 'reading'
        self.progress.emit(0.1, '')
        self._connect()
        self._provider.recognize([paths[number] for number in self._unread], self._language)

    def _connect(self):
        self._connected = True
        provider = self._provider
        provider.progress.connect(self._on_progress)
        provider.finished.connect(self._on_finished)
        provider.error.connect(self._on_error)

    def _disconnect(self):
        if not self._connected:
            return
        self._connected = False
        provider = self._provider
        for signal, slot in ((provider.progress, self._on_progress), (provider.finished, self._on_finished),
                             (provider.error, self._on_error)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    @Slot(float, str)
    def _on_progress(self, value, message):
        if self._stage == 'reading':
            self.progress.emit(0.1 + 0.85 * value, message)
        elif self._stage == 'retrying':
            self.progress.emit(0.95 + 0.05 * value, message)

    @Slot(list, list)
    def _on_finished(self, texts, confidences):
        if self._stage == 'reading':
            for number, text, confidence in zip(self._unread, texts, confidences):
                self._texts[number], self._confidences[number] = text, confidence
            self._retry = [number for number in self._unread
                           if not self._texts[number].strip() or self._confidences[number] < RETRY_BELOW]
            if not self._retry:
                self._finish()
                return
            self._stage = 'retrying'
            paths = []
            for number in self._retry:
                target = os.path.join(self._folder, f'dark-{number:05d}.pgm')
                with open(target, 'wb') as handle:
                    handle.write(picture_subtitles.pgm(self._pictures[number], polarity='dark'))
                paths.append(target)
            self._provider.recognize(paths, self._language)
        elif self._stage == 'retrying':
            for number, text, confidence in zip(self._retry, texts, confidences):
                if text.strip() and confidence > self._confidences[number]:
                    self._texts[number], self._confidences[number] = text, confidence
            self._finish()

    @Slot(str)
    def _on_error(self, message):
        if self._stage in ('reading', 'retrying'):
            self._fail(message)

    def _finish(self):
        self._stage = 'done'
        self._disconnect()
        segments = picture_subtitles.segments(self._pictures, self._texts or [''] * len(self._pictures))
        info = {'language_tag': self._language, 'language': project_language(self._language),
                'pictures': len(self._pictures), 'unread': sum(1 for t in self._texts if not t.strip())}
        self._cleanup()
        self.progress.emit(1.0, '')
        self.finished.emit(segments, info)

    @Slot(str)
    def _fail(self, message):
        if self._stage in ('done', 'failed', 'cancelled'):
            return
        self._stage = 'failed'
        self._disconnect()
        self._cleanup()
        self.failed.emit(message)

    def cancel(self):
        """Stop, and say nothing more."""
        if self._stage in ('done', 'failed', 'cancelled'):
            return
        stage, self._stage = self._stage, 'cancelled'
        self._disconnect()
        if stage in ('reading', 'retrying'):
            self._provider.cancel()
        self._cleanup()

    def _cleanup(self):
        if self._decoder is not None:
            self._decoder.stop()
            self._decoder.wait(5000)
            self._decoder = None
        if self._folder:
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder = None
