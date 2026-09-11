"""PTY session backend behind the common persistent-session manager."""
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def control(value, action):
    runtime = Path(value['runtime'])
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    # A private short-lived reply socket; no process IDs are trusted for signals.
    with tempfile.TemporaryDirectory(prefix='eam-ctl-', dir='/tmp') as temp:
        try:
            sock.bind(str(Path(temp) / 's'))
            sock.settimeout(1)
            sock.connect(str(runtime / 'control'))
            sock.send(json.dumps(dict(id=value['id'], action=action)).encode())
            reply = json.loads(sock.recv(2048))
            if reply.get('id') != value['id']:
                raise ValueError('PTY daemon ownership mismatch')
            return reply
        finally:
            sock.close()


def start(session, metadata, executable, args, env):
    from manager import save
    # The session's private metadata retains launch argv only until startup.
    launch = Path(metadata['runtime']) / 'launch.json'
    save(launch, dict(executable=executable, args=args))
    with open(Path(metadata['runtime']) / 'daemon.log', 'xb') as log:
        proc = subprocess.Popen([sys.executable, str(HERE / 'pty_backend.py'), str(session)],
                                cwd=metadata['directory'], env=env, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        from manager import read
        current = read(session)
        if current.get('stopped'):
            return current
        try:
            control(metadata, 'status')
            return metadata
        except OSError:
            pass
        if proc.poll() is not None:
            raise RuntimeError('PTY daemon failed; inspect ' + str(Path(metadata['runtime']) / 'daemon.log'))
        time.sleep(.02)
    proc.terminate()
    proc.wait(timeout=3)
    raise RuntimeError('PTY daemon startup timed out')


def status(session, value):
    from manager import read
    try:
        return control(value, 'status')
    except OSError as exc:
        latest = read(session)
        if latest.get('stopped'):
            return dict(state='stopped', exit_code=latest.get('exit_code', ''), attached_clients=0)
        return dict(state='unavailable', detail=str(exc))


def stop(session, value):
    from manager import read
    control(value, 'stop')
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        latest = read(session)
        if latest.get('stopped'):
            return dict(state='stopped', exit_code=latest.get('exit_code', ''))
        time.sleep(.02)
    raise RuntimeError('PTY stop not yet verified')


def serve(session):
    from manager import read, save
    from pty_relay import serve as relay
    value = read(session)
    launch = Path(value['runtime']) / 'launch.json'
    command = json.loads(launch.read_text())
    launch.unlink()
    args = [sys.executable, str(HERE / 'recorder.py'), '--events', value['events']]
    if not value['archive']:
        args.append('--no-record')
    args += [value['archive'] or '-', command['executable'], *command['args']]

    def changed(state):
        if state['state'] == 'exited':
            latest = read(session)
            latest.update(stopped=True, exit_code=state['exit_code'], stop_reason='pty-ended')
            save(Path(session) / 'session.json', latest)

    relay(value['runtime'], args, identity=value['id'], on_state=changed)


if __name__ == '__main__':
    serve(sys.argv[1])
