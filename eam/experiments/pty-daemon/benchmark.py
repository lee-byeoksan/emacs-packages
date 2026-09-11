"""Compare direct PTY with full relay-client path; measure five detached owners."""
import json
import os
from pathlib import Path
import pty
import select
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from test_relay import relay, HERE

ECHO = "import os,tty; tty.setraw(0); os.write(1,b'READY');\nwhile True:\n d=os.read(0,4096)\n if d==b'q': break\n os.write(1,d)\n"


def wait_for(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise RuntimeError('Timeout')


def read_until(fd, marker):
    result = bytearray()
    end = time.monotonic() + 8
    while marker not in result:
        if time.monotonic() >= end:
            raise RuntimeError('PTY timeout')
        if select.select([fd], [], [], .1)[0]:
            data = os.read(fd, 65536)
            if not data:
                raise RuntimeError('PTY closed')
            result.extend(data)


def timed_echo(command):
    master, slave = pty.openpty()
    proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    try:
        read_until(master, b'READY')
        samples = []
        for i in range(200):
            raw = ('PING%04d:' % i).encode() + '한글'.encode() * 100 + b'END'
            started = time.perf_counter()
            os.write(master, raw)
            read_until(master, raw)
            samples.append((time.perf_counter() - started) * 1000)
        os.write(master, b'q')
        proc.wait(timeout=5)
        assert proc.returncode == 0
        return dict(median_ms=statistics.median(samples), p95_ms=sorted(samples)[189],
                    max_ms=max(samples), samples=len(samples), bytes_per_echo=len(raw))
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=5)


def owner(path, code):
    proc = subprocess.Popen([sys.executable, str(HERE / 'relay.py'), 'serve', str(path),
                             '--', sys.executable, '-u', '-c', code], start_new_session=True)
    wait_for(lambda: (path / 'state.json').exists())
    return proc


def rss(pid):
    return int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(pid)]).strip())


def main():
    result = {'direct_pty': timed_echo([sys.executable, '-u', '-c', ECHO])}
    with tempfile.TemporaryDirectory(prefix='eam-bench-', dir='/tmp') as root:
        path = Path(root)
        proc = owner(path, ECHO)
        try:
            result['daemon_with_attach_client'] = timed_echo(
                [sys.executable, str(HERE / 'relay.py'), 'attach', str(path)])
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            assert proc.returncode == 0, "Echo owner cleanup failed"
    owners, temps, before, after = [], [], [], []
    try:
        for _ in range(5):
            temp = tempfile.TemporaryDirectory(prefix='eam-memory-', dir='/tmp')
            temps.append(temp)
            path = Path(temp.name)
            code = ("import os,tty; tty.setraw(0); os.write(1,b'READY'); os.read(0,1); "
                    "[os.write(1,b'x'*16384) for _ in range(2048)]; "
                    "open(" + repr(str(path / 'done')) + ",'w').write('done'); os.read(0,1)")
            proc = owner(path, code)
            owners.append(proc)
        for temp, proc in zip(temps, owners):
            path = Path(temp.name)
            client = socket.socket(socket.AF_UNIX)
            client.settimeout(5)
            client.connect(str(path / 'socket'))
            received = bytearray()
            while b'READY' not in received:
                received.extend(client.recv(65536))
            before.append(rss(proc.pid))
            client.sendall(relay.frame(b'I', b'g'))
            # Stop reading; daemon must evict the slow attachment and drain output.
            wait_for(lambda: (path / 'done').exists())
            client.close()
            after.append(rss(proc.pid))
        result['five_owners'] = dict(output_bytes_each=32 * 1024 * 1024,
                                     rss_before_kib=before, rss_after_kib=after,
                                     rss_delta_kib=[b-a for a,b in zip(before, after)])
    finally:
        for proc in owners:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            assert proc.returncode == 0, "Owner cleanup failed"
        for temp in temps:
            temp.cleanup()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
