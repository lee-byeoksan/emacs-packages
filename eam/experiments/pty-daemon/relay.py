"""Experimental single-client PTY owner. No terminal emulation or raw disk log.

Not a production backend: bounded startup replay is not a terminal snapshot.
A private runtime directory is the local user's authorization boundary.
"""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import time
import tty

LIMIT = 256 * 1024
REPLAY_LIMIT = 1024 * 1024
HEADER = struct.Struct('!cI')


def frame(kind, data=b''):
    return HEADER.pack(kind, len(data)) + data


def packets(buffer):
    while len(buffer) >= HEADER.size:
        kind, size = HEADER.unpack_from(buffer)
        if size > LIMIT:
            raise ValueError('Oversized packet')
        end = HEADER.size + size
        if len(buffer) < end:
            break
        data = bytes(buffer[HEADER.size:end])
        del buffer[:end]
        yield kind, data


def private_runtime(path):
    path = Path(path).absolute()
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Runtime must be a private directory owned by this user')
    if len(os.fsencode(path / 'socket')) >= 100:
        raise ValueError('Use a short runtime path, such as /tmp/eam-pty-XXXX')
    return path


def save(runtime, data):
    temp = runtime / 'state.tmp'
    with open(temp, 'w') as out:
        json.dump(data, out)
    os.replace(temp, runtime / 'state.json')


def serve(runtime, command):
    runtime = private_runtime(runtime)
    # One daemon per runtime. Never replace an existing socket.
    lock = os.open(runtime / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(runtime / 'socket'))
    listener.listen(4)
    listener.setblocking(False)
    child, master = pty.fork()
    if child == 0:
        listener.close()
        os.close(lock)
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
        try:
            os.execvpe(command[0], command, os.environ)
        except OSError:
            os._exit(127)
    os.set_blocking(master, False)
    stop = False

    def stopping(_sig, _frame):
        nonlocal stop
        stop = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stopping)
    client = None
    incoming, outgoing, pending, replay = (bytearray() for _ in range(4))
    replay_pending = bytearray()
    overflow = False
    total = 0
    peak = 0
    reaped = False
    exit_code = None
    eof = False
    finish_by = None

    def state():
        save(runtime, dict(daemon_pid=os.getpid(), child_pid=child,
                           state='exited' if eof else ('attached' if client else 'detached'),
                           output_bytes=total, replay_bytes=len(replay), replay_overflow=overflow,
                           peak_output_queue=peak, exit_code=exit_code))

    def disconnect():
        nonlocal client
        if client:
            client.close()
        client = None
        incoming.clear()
        outgoing.clear()
        replay_pending.clear()
        # Never pass a previous attachment's queued input to a new attachment.
        pending.clear()
        state()

    state()
    try:
        while not stop:
            if not reaped:
                waited, status = os.waitpid(child, os.WNOHANG)
                if waited:
                    reaped = True
                    exit_code = os.waitstatus_to_exitcode(status)
            if eof:
                if not (outgoing or replay_pending) or time.monotonic() >= finish_by:
                    break
            readers = ([] if eof else [listener, master])
            if client and len(pending) + len(incoming) < LIMIT // 2 and not eof:
                readers.append(client)
            writers = ([master] if pending and not eof else []) + ([client] if client and (outgoing or replay_pending) else [])
            ready, writable, _ = select.select(readers, writers, [], .05)
            if listener in ready:
                new, _ = listener.accept()
                new.setblocking(False)
                if client:
                    new.close()  # An attachment cannot steal the live terminal.
                else:
                    client = new
                    # Replay only from byte zero, never from the middle of ANSI/UTF-8.
                    info = json.dumps(dict(replay_complete=not overflow)).encode()
                    replay_pending.extend(frame(b'H', info))
                    if not overflow and replay:
                        # A bounded replay snapshot, separate from the live queue.
                        # Small packets let the client's bounded decoder drain it.
                        for offset in range(0, len(replay), 16384):
                            replay_pending.extend(frame(b'O', replay[offset:offset + 16384]))
                    state()
            if master in ready:
                try:
                    data = os.read(master, 16384)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    data = b''
                if not data:
                    eof = True
                    finish_by = time.monotonic() + 1
                else:
                    total += len(data)
                    if not overflow:
                        if len(replay) + len(data) <= REPLAY_LIMIT:
                            replay.extend(data)
                        else:
                            overflow = True
                            replay.clear()
                    if client:
                        packet = frame(b'O', data)
                        if len(outgoing) + len(packet) > LIMIT:
                            # A stalled UI must not stop the CLI or grow memory.
                            disconnect()
                        else:
                            outgoing.extend(packet)
                            peak = max(peak, len(outgoing))
            if client and client in ready:
                try:
                    data = client.recv(16384)
                    if not data:
                        disconnect()
                    else:
                        incoming.extend(data)
                        if len(incoming) >= HEADER.size:
                            kind, size = HEADER.unpack_from(incoming)
                            if kind not in (b'I', b'R') or (kind == b'I' and size > 16384) or (kind == b'R' and size != 8):
                                raise ValueError('Invalid input packet size')
                        for kind, payload in packets(incoming):
                            if kind == b'I':
                                if len(pending) + len(payload) > LIMIT:
                                    raise ValueError('Input queue full')
                                pending.extend(payload)
                            elif kind == b'R' and len(payload) == 8:
                                rows, cols, _, _ = struct.unpack('HHHH', payload)
                                if not (1 <= rows <= 1000 and 1 <= cols <= 1000):
                                    raise ValueError('Invalid terminal size')
                                fcntl.ioctl(master, termios.TIOCSWINSZ, payload)
                            else:
                                raise ValueError('Invalid client packet')
                except (OSError, ValueError):
                    disconnect()
            if master in writable and pending:
                try:
                    count = os.write(master, pending)
                    del pending[:count]
                except BlockingIOError:
                    pass
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    pending.clear()
            if client and client in writable and (outgoing or replay_pending):
                try:
                    sending_output = replay_pending if replay_pending else outgoing
                    count = client.send(sending_output)
                    del sending_output[:count]
                except BlockingIOError:
                    pass
                except OSError:
                    disconnect()
    finally:
        # Only signal the group whose ID is our unreaped child PID.
        # A closed PTY can report a stale/reassigned foreground group on macOS.
        if not reaped:
            waited, status = os.waitpid(child, os.WNOHANG)
            if waited:
                reaped = True
                exit_code = os.waitstatus_to_exitcode(status)
        if not reaped:
            groups = {child}
            for group in groups:
                if 0 < group < 2**31 and group != os.getpgrp():
                    try:
                        os.killpg(group, signal.SIGTERM)
                    except PermissionError:
                        # macOS may reject killpg when the leader is exiting.
                        # The unreaped PID is still ours; signal it directly.
                        try:
                            os.kill(child, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                waited, status = os.waitpid(child, os.WNOHANG)
                if waited:
                    reaped = True
                    exit_code = os.waitstatus_to_exitcode(status)
                    break
                time.sleep(.01)
            if not reaped:
                for group in groups:
                    if 0 < group < 2**31 and group != os.getpgrp():
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except PermissionError:
                            try:
                                os.kill(child, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        except ProcessLookupError:
                            pass
                _, status = os.waitpid(child, 0)
                exit_code = os.waitstatus_to_exitcode(status)
        eof = True
        if client:
            client.close()
        os.close(master)
        listener.close()
        (runtime / 'socket').unlink()
        state()
        os.close(lock)


def attach(runtime):
    runtime = private_runtime(runtime)
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(str(runtime / 'socket'))
    connection.setblocking(False)
    original = termios.tcgetattr(0) if os.isatty(0) else None
    flags = fcntl.fcntl(1, fcntl.F_GETFL)
    incoming, output, sending = (bytearray() for _ in range(3))
    resize = True
    connected = True

    def resized(_sig, _frame):
        nonlocal resize
        resize = True

    # Python restarts select after a flag-only signal handler.  Wake the
    # descriptor wait explicitly so idle terminals forward resize immediately.
    wake_read, wake_write = os.pipe()
    os.set_blocking(wake_read, False)
    os.set_blocking(wake_write, False)
    old_wakeup = signal.set_wakeup_fd(wake_write)
    old_resize = signal.signal(signal.SIGWINCH, resized)
    try:
        if original:
            tty.setraw(0)
        os.set_blocking(1, False)
        while connected or output:
            if resize and connected:
                size = (fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0' * 8) if original
                        else struct.pack('HHHH', 40, 120, 0, 0))
                rows, cols, _, _ = struct.unpack('HHHH', size)
                if not rows or not cols:
                    size = struct.pack('HHHH', 40, 120, 0, 0)
                sending.extend(frame(b'R', size))
                resize = False
            readers = [wake_read] + ([connection] if connected and len(output) + len(incoming) < LIMIT else [])
            if connected and len(sending) < LIMIT // 2:
                readers.append(0)
            writers = ([connection] if connected and sending else []) + ([1] if output else [])
            ready, writable, _ = select.select(readers, writers, [])
            if wake_read in ready:
                os.read(wake_read, 4096)
            if connection in ready:
                data = connection.recv(min(16384, LIMIT - len(output) - len(incoming)))
                if not data:
                    connected = False
                else:
                    incoming.extend(data)
                    for kind, payload in packets(incoming):
                        if kind == b'O':
                            output.extend(payload)
                        elif kind == b'H':
                            if not json.loads(payload)['replay_complete']:
                                output.extend(b'\r\n[EAM experiment: replay limit exceeded; screen restoration unavailable]\r\n')
            if 0 in ready:
                data = os.read(0, 16384)
                if not data:
                    break
                sending.extend(frame(b'I', data))
            if connection in writable and sending:
                try:
                    count = connection.send(sending)
                    del sending[:count]
                except BlockingIOError:
                    pass
            if 1 in writable and output:
                try:
                    count = os.write(1, output)
                    del output[:count]
                except BlockingIOError:
                    pass
    finally:
        signal.set_wakeup_fd(old_wakeup)
        signal.signal(signal.SIGWINCH, old_resize)
        os.close(wake_read)
        os.close(wake_write)
        connection.close()
        fcntl.fcntl(1, fcntl.F_SETFL, flags)
        if original:
            termios.tcsetattr(0, termios.TCSANOW, original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'serve', 'attach'])
    parser.add_argument('runtime')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'attach':
        attach(args.runtime)
    else:
        command = args.command
        if command[:1] == ['--']:
            command = command[1:]
        if not command:
            parser.error('A command is required')
        if args.action == 'serve':
            serve(args.runtime, command)
        else:
            runtime = private_runtime(args.runtime)
            if (runtime / 'lock').exists():
                parser.error('Use a fresh private runtime directory')
            with open(runtime / 'daemon.log', 'xb') as log:
                proc = subprocess.Popen([sys.executable, __file__, 'serve', str(runtime), '--', *command],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        start_new_session=True)
            for _ in range(100):
                if (runtime / 'state.json').exists():
                    print(runtime)
                    return
                if proc.poll() is not None:
                    parser.error('Daemon failed; inspect daemon.log')
                time.sleep(.02)
            proc.terminate()
            parser.error('Daemon startup timed out')


if __name__ == '__main__':
    main()
