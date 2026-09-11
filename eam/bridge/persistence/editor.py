"""Resolve the current explicit Emacs attachment for each editor invocation.

The CLI keeps this helper command, not an Emacs PID/socket fixed at startup.
Never creates a frame or starts Emacs. Route fields are data, never Lisp or shell code.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def invoke(route, files):
    fd = os.open(route, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise ValueError('Editor route must be a private regular file, at most 4 KiB')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError('Editor route exceeds 4 KiB')
        route = json.loads(data)
    finally:
        os.close(fd)
    if not isinstance(route, dict) or route.get('version') != 1:
        raise ValueError('Unsupported editor route')
    lease = route.get('lease')
    if lease is not None:
        if not isinstance(lease, str) or not os.path.isabs(lease):
            raise ValueError('Invalid attachment lease')
        lock = os.open(lease, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError('No active Emacs attachment')
        finally:
            os.close(lock)
    client, socket = route.get('client'), route.get('socket')
    if not all(isinstance(value, str) and os.path.isabs(value) and '\0' not in value
               for value in (client, socket)):
        raise ValueError('Editor client and socket must be absolute paths')
    if not files:
        raise ValueError('No edit file supplied')
    return edit_files(client, socket, files)


def edit_files(client, socket, files):
    """Register a display-only guide, then wait for normal server-edit completion."""
    files = [os.path.abspath(file) for file in files]
    expression = "(progn (eam-terminal--register-editor-files '(" + " ".join(
        json.dumps(file, ensure_ascii=False) for file in files) + ")) nil)"
    command = [client, '--socket-name', socket, '-a', 'false']
    result = subprocess.run([*command, '--eval', expression],
                            stdout=subprocess.DEVNULL, check=False)
    if result.returncode:
        return result.returncode
    return subprocess.run([*command, '--', *files], check=False).returncode

if __name__ == '__main__':
    try:
        if len(sys.argv) >= 5 and sys.argv[1] == '--direct':
            raise SystemExit(edit_files(sys.argv[2], sys.argv[3], sys.argv[4:]))
        if len(sys.argv) < 3:
            raise ValueError('Usage: editor.py ROUTE FILE...')
        raise SystemExit(invoke(Path(sys.argv[1]), sys.argv[2:]))
    except (OSError, ValueError) as exc:
        print(f'EDITOR UNAVAILABLE: {exc}', file=sys.stderr)
        raise SystemExit(1)
