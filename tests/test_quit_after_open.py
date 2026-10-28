"""Closing Subtitld right after opening a .usfx: a clean exit, even with the
project's loaders still running.

Opening a .usfx starts two threads nothing used to wait for: the extractor
streaming its heavy assets (dubs, waveform cache, stems) out of the zip, and
the decoder for its speaker thumbnails. Quitting while either ran destroyed
the QThread with its thread still running, and Qt aborted the process
("QThread: Destroyed while thread '' is still running", exit 134). Now
aboutToQuit asks both to stop and waits for them.

Each run is a child process starting Subtitld as a file association does,
with the .usfx on its command line, and quitting as soon as the event loop
starts. Both loaders are slowed down so they are surely still running then.
The child's exit code is what is checked, and that no thread was left for
main()'s last resort (leaving without the teardown) to cover.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, subprocess, sys, tempfile
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / 'src')

CHILD = r'''
import os, sys, time
sys.path.insert(0, SRC)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from subtitld.modules import file_io
from subtitld.modules.signals import SIGNALS

# Slow enough that neither is anywhere near done when the quit comes:
# Window() takes a few seconds after the open, before the loop runs.
file_io._USFXBackgroundExtractor._CHUNK = 256
_ready = file_io._USFXBackgroundExtractor._emit_member_ready
file_io._USFXBackgroundExtractor._emit_member_ready = lambda self, *a: (time.sleep(1), _ready(self, *a))
_downscale = file_io._downscale_speaker_image
file_io._downscale_speaker_image = lambda image: (time.sleep(1), _downscale(image))[1]

# Edge TTS fetches its voice list at launch, on a thread quitting must
# wait for too; a stand-in keeps the network out of it.
import asyncio, edge_tts


async def voices():
    await asyncio.sleep(0.5)
    return []


edge_tts.list_voices = voices

# And subtitld.cc, which the open asks about the video: refused at once.
import urllib.error
from subtitld.modules import subtitldcc_service


def offline(request, timeout):
    raise urllib.error.URLError('offline in this test')


subtitldcc_service._open = offline

seen = {}


def quit_now():
    from subtitld.modules import session
    for name in ('USFX_BACKGROUND_LOAD', 'SPEAKER_IMAGE_LOAD'):
        thread = getattr(session, name)
        seen[name] = bool(thread is not None and thread.isRunning())
    print('running at quit:', seen, flush=True)
    QApplication.quit()


# The extractor is the last thing an open starts; the loop runs this
# as soon as it begins.
SIGNALS.usfx_background_load_started.connect(lambda: QTimer.singleShot(0, quit_now), Qt.DirectConnection)

sys.argv = ['subtitld', USFX]
from subtitld import __main__ as subtitld_main
# main() leaves without the teardown if a thread outlived the waits on quit,
# which would hide a loader nothing waited for: with the network out of it,
# none may be left.
_still_running = subtitld_main._threads_still_running
subtitld_main._threads_still_running = lambda: (lambda left: (print('left running:', left, flush=True), left)[1])(_still_running())
try:
    subtitld_main.main()
except SystemExit:
    pass
if not all(seen.values()) or len(seen) != 2:
    print('the loaders were not running at quit: nothing tested', flush=True)
    sys.exit(3)
print('left main()', flush=True)
'''

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


def environment(folder):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    for key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
        env[key] = str(folder / key.lower())
    return env


def make_project(folder):
    """A .usfx with dubs and speaker images, saved by Subtitld itself (in a
    child too, so this process stays free of Qt)."""
    script = r'''
import os, sys
sys.path.insert(0, SRC)
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
app = QApplication([])
from subtitld.modules import file_io, session
folder = os.path.dirname(USFX)
video = os.path.join(folder, 'clip.mp4')
open(video, 'wb').close()
segments, speakers = [], {}
for index in range(8):
    dub = os.path.join(folder, f'dub{index}.wav')
    with open(dub, 'wb') as handle:
        handle.write(b'RIFF' + os.urandom(256 * 1024))
    name = f'Speaker {index}'
    image = QImage(640, 640, QImage.Format_RGB32)
    image.fill(QColor(40 * index % 255, 90, 160))
    speakers[name] = {'color': '#3366cc', 'image': image}
    segments.append({'start': float(index), 'end': index + 0.5, 'text': f'Line {index}', 'speaker': name,
                     'dubbing': [{'uid': f'd{index}', 'path': dub, 'start': float(index), 'end': index + 0.5}]})
session.SUBTITLE = {'language': 'en-us', 'segments': segments}
session.SPEAKERS, session.FORMAT, session.VIDEO = speakers, {}, {'filepath': video}
file_io.save_file(USFX, 'USFX')
'''
    usfx = folder / 'project.usfx'
    done = subprocess.run([sys.executable, '-c', f'SRC = {SRC!r}\nUSFX = {str(usfx)!r}\n' + script],
                          env=environment(folder), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        print(done.stdout + done.stderr)
        raise SystemExit('could not make the project')
    return usfx


folder = Path(tempfile.mkdtemp())
usfx = make_project(folder)

print('quitting right after opening a .usfx with assets')
runs = int(os.environ.get('QUIT_AFTER_OPEN_RUNS', '3'))
for run in range(runs):
    done = subprocess.run([sys.executable, '-c', f'SRC = {SRC!r}\nUSFX = {str(usfx)!r}\n' + CHILD],
                          env=environment(folder), capture_output=True, text=True, timeout=120)
    output = done.stdout + done.stderr
    check(f'run {run + 1}: exit code', done.returncode, 0)
    check(f'run {run + 1}: both loaders were still running when it quit',
          "running at quit: {'USFX_BACKGROUND_LOAD': True, 'SPEAKER_IMAGE_LOAD': True}" in output, True)
    check(f'run {run + 1}: no "Destroyed while thread" abort', 'Destroyed while thread' in output, False)
    check(f'run {run + 1}: every thread stopped by the waits on quit', 'left running: False' in output, True)
    if done.returncode != 0:
        print(output[-3000:])

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
