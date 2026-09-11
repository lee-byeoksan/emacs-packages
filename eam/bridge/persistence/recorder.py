"""Bounded PTY relay with optional diagnostic recording.

When enabled, the archive is opened exclusively before the child starts.
Notification events remain available when raw recording is disabled.
"""
import argparse
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import stat
import sys
import termios
import time
import tty
from events import Notifications, encode

LIMIT = 65536


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Archive write made no progress')
        view = view[count:]


def relay(archive, command, events=None):
    # O_EXCL also rejects existing symlinks; never truncate a previous archive.
    record = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600) if archive is not None else None
    if record is not None and not stat.S_ISREG(os.fstat(record).st_mode):
        if record is not None: os.close(record)
        raise ValueError('Archive must be a regular file')
    event_file = None
    pid = master = None
    original_tty = termios.tcgetattr(0) if os.isatty(0) else None
    output_flags = fcntl.fcntl(1, fcntl.F_GETFL)
    size = (fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0'*8) if os.isatty(0)
            else b'\x18\x00\x50\x00\0\0\0\0')
    stop = False
    resize = False
    old_handlers = {}
    def handle(sig, _frame):
        nonlocal stop, resize
        if sig == signal.SIGWINCH:
            resize = True
        else:
            stop = True
    try:
        if events is not None:
            event_file = os.open(events, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        extractor = Notifications(lambda event: write_all(event_file, encode(event))) if event_file is not None else None
        pid, master = pty.fork()
        if pid == 0:
            try:
                if record is not None: os.close(record)
                if event_file is not None:
                    os.close(event_file)
                fcntl.ioctl(0, termios.TIOCSWINSZ, size)
                os.execvpe(command[0], command, os.environ)
            except BaseException:
                os._exit(127)
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGWINCH):
            old_handlers[sig] = signal.signal(sig, handle)
        # A file size limit should become EFBIG, allowing normal failure cleanup.
        old_handlers[signal.SIGXFSZ] = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        if original_tty is not None:
            tty.setraw(0)
        os.set_blocking(master, False)
        os.set_blocking(1, False)
        pending_output = bytearray()
        pending_input = bytearray()
        eof = False
        input_open = True
        while not stop and (not eof or pending_output):
            if resize:
                if os.isatty(0):
                    fcntl.ioctl(master, termios.TIOCSWINSZ,
                                fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0'*8))
                resize = False
            readers = []
            if not eof and len(pending_output) < LIMIT:
                readers.append(master)
            if input_open and not eof and len(pending_input) < LIMIT:
                readers.append(0)
            writers = ([1] if pending_output else []) + ([master] if pending_input and not eof else [])
            ready, writable, _ = select.select(readers, writers, [], .1)
            if master in ready:
                try:
                    data = os.read(master, min(16384, LIMIT-len(pending_output)))
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    data = b''
                if data:
                    if record is not None: write_all(record, data)
                    if extractor is not None:
                        extractor.feed(data)
                    pending_output.extend(data)
                else:
                    eof = True
            if 0 in ready:
                data = os.read(0, min(16384, LIMIT-len(pending_input)))
                if data:
                    pending_input.extend(data)
                else:
                    input_open = False
                    stop = True
            for fd, pending in ((1, pending_output), (master, pending_input)):
                if fd in writable and pending:
                    try:
                        written = os.write(fd, pending)
                        del pending[:written]
                    except BlockingIOError:
                        pass
            assert len(pending_output) <= LIMIT and len(pending_input) <= LIMIT
        if stop:
            return 143
        # Child may close its PTY without exiting. Wait briefly, then clean up.
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                pid = None
                code = os.waitstatus_to_exitcode(status)
                return code if code >= 0 else 128-code
            time.sleep(.01)
        return 143
    finally:
        # The direct child remains unreaped until cleanup, reserving its PID.
        # This does not promise to terminate arbitrary setsid/double-fork jobs.
        if pid:
            reaped = False
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # macOS may return EPERM for a group containing only the
                    # unreaped dead child. Verify exit; never suppress EPERM
                    # while the directly owned child is still running.
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                    if not waited:
                        raise
                    reaped = True
                    break
                if sig == signal.SIGTERM:
                    time.sleep(.1)
            if not reaped:
                os.waitpid(pid, 0)
        if master is not None:
            os.close(master)
        if record is not None: os.close(record)
        if event_file is not None:
            os.close(event_file)
        fcntl.fcntl(1, fcntl.F_SETFL, output_flags)
        if original_tty is not None:
            termios.tcsetattr(0, termios.TCSANOW, original_tty)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path)
    parser.add_argument('--no-record', action='store_true')
    parser.add_argument('archive', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error('Missing child command')
    try:
        return relay(None if args.no_record else args.archive, args.command, args.events)
    except (OSError, ValueError) as exc:
        print(f'RECORDING ERROR: {exc}', file=sys.stderr)
        return 74

if __name__ == '__main__':
    raise SystemExit(main())
