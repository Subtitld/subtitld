"""What Subtitld writes to the terminal it is started from, and how it leaves
that terminal (modules/console).

Quiet by default: warnings and errors only, Qt's critical messages, nothing
from the add-ons; `--debug` (SUBTITLD_DEBUG=1 for the processes it starts)
shows everything. Ctrl+C closes the window. And the terminal is left as it
was found: ffmpeg, given a terminal on its stdin, turns its echo off while it
runs, and killed mid-job (Subtitld closed during a waveform or a proxy) leaves
it off, so no process Subtitld starts gets the terminal; and its settings are
put back at the end even if something turned the echo off through /dev/tty.

The terminal checks run in a pseudo-terminal, with the ffmpeg on the PATH.

Standalone script, like the other suites here. It puts this checkout's src/
on the path itself: the venv holds a NON-editable install, and importing that
instead silently tests stale code.
"""
import io
import logging
import os
import select
import shutil
import subprocess
import sys
import time
import types
import warnings
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / 'src')
sys.path.insert(0, SRC)
os.environ.pop('SUBTITLD_DEBUG', None)
os.environ.pop('QTWEBENGINE_CHROMIUM_FLAGS', None)
from subtitld.modules import console

fails = []


def check(name, got, want):
    ok = got == want
    print(f'  [{"OK " if ok else "FAIL"}] {name}: {got!r}' + ('' if ok else f'   (want {want!r})'))
    if not ok:
        fails.append(name)


print('quiet by default')
console.configure(False)
check('not debugging, nor told so to the processes it starts', (console.enabled(), os.environ.get('SUBTITLD_DEBUG')),
      (False, None))
check("Subtitld's info hidden, its warnings shown",
      (logging.getLogger('subtitld.x').isEnabledFor(logging.INFO),
       logging.getLogger('subtitld.x').isEnabledFor(logging.WARNING)), (False, True))
check("other libraries' info hidden", logging.getLogger('matplotlib').isEnabledFor(logging.INFO), False)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('ignore')     # what configure set, as catch_warnings restores it after
    console.configure(False)
    warnings.warn('a dependency complains')
check("Python's warnings hidden", len(caught), 0)
check("Chromium's messages: fatal only", os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS'), '--log-level=3')
console.configure(False)
check('(said once, however often configured)', os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS'), '--log-level=3')
check('one handler of its own on the root logger',
      sum(1 for h in logging.getLogger().handlers if getattr(h, '_subtitld', False)), 1)

from PySide6.QtCore import QtMsgType
shown = {mode.name: console.qt_message_shown(mode) for mode in
         (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg, QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg)}
check("Qt's messages: critical ones only", shown,
      {'QtDebugMsg': False, 'QtInfoMsg': False, 'QtWarningMsg': False, 'QtCriticalMsg': True})


def addon_log(lines):
    """What an add-on's stderr puts on Subtitld's stderr (process.AddonProcess)."""
    from subtitld.modules.addons.process import AddonProcess
    stand_in = types.SimpleNamespace(addon_id='mock', _proc=types.SimpleNamespace(stderr=io.BytesIO(lines)))
    captured = io.BytesIO()
    wrapper = io.TextIOWrapper(captured, encoding='utf-8')
    stderr, sys.stderr = sys.stderr, wrapper
    try:
        AddonProcess._read_stderr(stand_in)
    finally:
        sys.stderr = stderr
        wrapper.flush()
        wrapper.detach()        # dropping the wrapper would close `captured`
    return captured.getvalue().decode()


check("an add-on's own log: not shown", addon_log(b'INFO loading the model\nINFO ready\n'), '')

print('--debug')
console.configure(True)
check('debugging, and the processes it starts are told so', (console.enabled(), os.environ.get('SUBTITLD_DEBUG')),
      (True, '1'))
check("Subtitld's debug lines shown", logging.getLogger('subtitld.x').isEnabledFor(logging.DEBUG), True)
check("other libraries' info shown, not their debug",
      (logging.getLogger('matplotlib').isEnabledFor(logging.INFO),
       logging.getLogger('matplotlib').isEnabledFor(logging.DEBUG)), (True, False))
check("Qt's messages: all", console.qt_message_shown(QtMsgType.QtDebugMsg), True)
check("an add-on's own log: shown, named", addon_log(b'INFO ready\n'), '[addon:mock] INFO ready\n')
os.environ.pop('SUBTITLD_DEBUG')

print('the command line')
help_text = subprocess.run([sys.executable, '-m', 'subtitld', '--help'], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=SRC), timeout=120).stdout
check('--debug is there, said what it does', ('--debug' in help_text, 'diagnostic' in help_text), (True, True))

print('the terminal it is started from')
if sys.platform.startswith('win') or not shutil.which('ffmpeg'):
    print('  SKIP: needs a pseudo-terminal and ffmpeg')
else:
    import pty
    import termios

    def echo_after(child):
        """Run `child` (Python source) in a pseudo-terminal; its echo then."""
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, '-c', child], dict(os.environ, PYTHONPATH=SRC))
        end = time.time() + 20
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    os.read(fd, 4096)
                except OSError:
                    break
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
        echo = bool(termios.tcgetattr(fd)[3] & termios.ECHO)
        os.close(fd)
        return echo

    # A job long enough to be killed mid-way, as when Subtitld closes during it.
    KILLED_FFMPEG = '''
import subprocess, time
p = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-re',
                      '-i', 'sine=duration=60', '-y', '-f', 'wav', '/dev/null'])
time.sleep(2)
p.kill()
p.wait()
'''
    check('(an ffmpeg killed mid-job does leave a terminal without echo)', echo_after(KILLED_FFMPEG), False)
    check('not the one Subtitld was started from',
          echo_after('from subtitld.modules import console\nconsole.detach_from_terminal()\n' + KILLED_FFMPEG), True)
    check("and what turns the echo off through /dev/tty is undone at the end", echo_after('''
import subprocess
from subtitld.modules import console
console.detach_from_terminal()
subprocess.run('stty -echo < /dev/tty', shell=True)
'''), True)

print()
print('FAILED:' if fails else 'ALL PASS', fails if fails else '')
sys.exit(1 if fails else 0)
