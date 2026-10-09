"""Add-ons panel worker threads: closing Subtitld while the catalog fetch or an
install is still running drains them first, and a finished worker is let go
only once its thread has really returned.

The panel starts a catalog fetch as soon as it is built, and that fetch can
wait up to 15 s on the network. Nothing stopped it on a normal quit, so
closing the window while it ran destroyed the QThread with its thread still
running and Qt aborted the process ("QThread: Destroyed while thread '' is
still running"). The same could happen a moment after a fetch or an install
ended, when the slot for QThread.finished dropped the last reference while
the thread was still unwinding.

The catalog fetch and the install are stand-ins, so nothing goes to the
network, and so is PortAudio: opening a stream raises what sounddevice
raises with no default output device.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import os, sys, tempfile, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_xdg = tempfile.mkdtemp()
for _key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
    os.environ[_key] = os.path.join(_xdg, _key.lower())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.argv = sys.argv[:1]  # subtitld.__main__ parses the command line on import
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
app = QApplication([])
from subtitld.modules import audioengine
from subtitld.modules.addons import installer
from subtitld.interface import addons_dialog
from subtitld import __main__ as subtitld_main

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got}' + ('' if ok else f'   (want {want})'))
    if not ok:
        fails.append(name)


def pump_until(done, seconds=10):
    # A tight loop keeps the main thread idle and ready, so each slot runs
    # the moment it's posted: the timing that used to abort.
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
    return done()


def running(thread):
    try:
        return thread.isRunning()
    except RuntimeError:  # already deleted, which happens only once it has finished
        return False


counts = {'created': 0, 'destroyed': 0}


def counted(cls):
    original = cls.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        counts['created'] += 1
        self.destroyed.connect(lambda *_: counts.__setitem__('destroyed', counts['destroyed'] + 1))

    cls.__init__ = init


counted(addons_dialog._CatalogFetchThread)
counted(addons_dialog._InstallThread)


def quick_catalog(force_refresh=False, **_kwargs):
    return {'addons': []}


def quick_install(entry, on_progress=None, release_version=None):
    if on_progress:
        on_progress(0.5, 'Downloading…')
    return entry['id']


def no_device(**_kwargs):
    raise audioengine.sd.PortAudioError('Error querying device -1')


def mock_row():
    return addons_dialog._Row(addon_id='mock', display_name='Mock', summary='', license='MIT',
                              catalog_entry={'id': 'mock', 'latest_version': '0.0.1', 'releases': []})


installer.fetch_catalog = quick_catalog
installer.download_and_install = quick_install
audioengine.sd.OutputStream = no_device

print('refreshing the catalog over and over')
panel = addons_dialog.AddonsPanel()
pump_until(lambda: panel._fetch_thread is None)  # the fetch the panel starts on its own
rounds, completed = 200, 0
for _ in range(rounds):
    panel._refresh_catalog(force=True)
    if pump_until(lambda: panel._fetch_thread is None):
        completed += 1
check('every fetch ended and was let go', completed, rounds)
check('the catalog arrived', panel._fetch_state, 'ok')

print('installing over and over')
rounds, completed = 100, 0
for _ in range(rounds):
    panel._install_row(mock_row())
    if pump_until(lambda: panel._install_thread is None and panel._install_target_id is None):
        completed += 1
check('every install ended and was let go', completed, rounds)
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
check('every worker thread object deleted', counts['destroyed'], counts['created'])

print('closing the window while the catalog fetch waits on the network and an install runs')
install_ran = []


def hanging_catalog(force_refresh=False, **_kwargs):
    time.sleep(60)  # a server that never answers
    return {}


def slow_install(entry, on_progress=None, release_version=None):
    time.sleep(1.0)
    install_ran.append(entry['id'])
    return entry['id']


installer.fetch_catalog = hanging_catalog
installer.download_and_install = slow_install
window = subtitld_main.Window()
addons_panel = window.left_panel_global_addons_panel
fetch = addons_panel._fetch_thread
addons_panel._install_row(mock_row())
install = addons_panel._install_thread
check('both are running', (running(fetch), running(install)), (True, True))
QTimer.singleShot(0, window.close)
QTimer.singleShot(20000, app.quit)  # in case closing the window does not quit
started = time.monotonic()
app.exec()
took = time.monotonic() - started
check('quitting does not wait out the network', took < 6, True)
check('the catalog fetch is no longer running', running(fetch), False)
check('the install is no longer running', running(install), False)
check('and it got to finish', install_ran, ['mock'])
window.preview_panel_player._audio_device.shutdown()

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
