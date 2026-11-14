"""What Subtitld writes to the terminal it is started from, and how it
leaves that terminal.

Quiet by default: Subtitld's warnings and errors, and Qt's critical messages.
`subtitld --debug` (or SUBTITLD_DEBUG=1 in the environment) shows the rest:
Subtitld's informational lines, Qt's messages, what the subtitle renderer's
Chromium says, the add-ons' own logs, Python's warnings. The processes
Subtitld starts (the subtitle renderer, the add-ons) learn which it is from
the environment.

The terminal is left as it was found. No process started from here reads it:
ffmpeg, given a terminal on its stdin, turns its echo off to listen for keys,
and killed mid-job (Subtitld closed while a waveform or a proxy was being
made) it leaves the echo off. And the terminal's settings are put back when
Subtitld ends.
"""
import atexit
import logging
import os
import signal
import sys
import warnings

ENV = 'SUBTITLD_DEBUG'

_terminal = {'fd': None, 'settings': None}


def enabled() -> bool:
    """Whether diagnostic output is wanted (--debug, or SUBTITLD_DEBUG)."""
    return os.environ.get(ENV, '').strip() not in ('', '0')


def configure(debug: bool = False) -> bool:
    """Set this process's terminal output up, for it and the processes it
    starts. `debug` turns diagnostics on; without it, SUBTITLD_DEBUG decides.
    Call it before Qt (and Chromium) are loaded: they read their settings
    from the environment once."""
    if debug:
        os.environ[ENV] = '1'
    debug = enabled()
    root = logging.getLogger()
    if not any(getattr(handler, '_subtitld', False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler._subtitld = True
        handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
        root.addHandler(handler)
    # Other libraries' INFO is enough even when debugging; Subtitld's own
    # DEBUG lines are wanted then.
    root.setLevel(logging.INFO if debug else logging.WARNING)
    logging.getLogger('subtitld').setLevel(logging.DEBUG if debug else logging.WARNING)
    if not debug:
        warnings.simplefilter('ignore')
        # Chromium (the subtitle renderer's page): fatal messages only.
        flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
        if '--log-level' not in flags:
            os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (flags + ' --log-level=3').strip()
    return debug


def qt_message_shown(mode) -> bool:
    """Whether a Qt message of this type reaches the terminal: all of them
    when debugging, otherwise only critical and fatal ones."""
    if enabled():
        return True
    from PySide6.QtCore import QtMsgType
    return mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)


def qt_message(mode, context, message):
    """A Qt message handler (qInstallMessageHandler) that follows the above."""
    if qt_message_shown(mode):
        try:
            sys.stderr.write(message + '\n')
        except Exception:
            pass


def detach_from_terminal():
    """When stdin is a terminal: keep its settings to put back at the end,
    and give this process (and so every process it starts) /dev/null for
    stdin instead. Subtitld reads nothing from it."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return
    if not os.isatty(fd):
        return
    try:
        import termios
        _terminal['settings'] = termios.tcgetattr(fd)
        _terminal['fd'] = os.dup(fd)
    except Exception:       # no termios (Windows), or not a real terminal
        _terminal['settings'] = None
    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, fd)
        os.close(devnull)
    except OSError:
        return
    atexit.register(restore_terminal)


def restore_terminal():
    """Put the terminal's settings back as they were when Subtitld started.
    Safe to call more than once; called at exit, and before an exit that
    skips atexit (os._exit)."""
    if _terminal['fd'] is None or _terminal['settings'] is None:
        return
    try:
        import termios
        termios.tcsetattr(_terminal['fd'], termios.TCSADRAIN, _terminal['settings'])
    except Exception:
        pass


def on_interrupt(callback):
    """Call `callback` (on Qt's thread) when the terminal's Ctrl+C arrives.
    Python runs a signal handler only between bytecodes, and Qt's event loop
    waits in C: a wake-up socket that Qt watches gets it run at once.
    Returns what has to stay alive for as long as it should work."""
    try:
        import socket
        from PySide6.QtCore import QSocketNotifier, QTimer
        receiver, sender = socket.socketpair()
        receiver.setblocking(False)
        sender.setblocking(False)
        signal.set_wakeup_fd(sender.fileno())
        notifier = QSocketNotifier(receiver.fileno(), QSocketNotifier.Type.Read)

        def drain():
            try:
                receiver.recv(64)
            except OSError:
                pass

        notifier.activated.connect(drain)
        signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, callback))
        return receiver, sender, notifier
    except (ValueError, OSError, ImportError, AttributeError):
        return None
