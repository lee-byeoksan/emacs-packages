"""Fake PTY tests; never starts an AI or opens a GUI."""
import fcntl
import pty
import select
import termios
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('relay', HERE.parents[1] / 'bridge/persistence/pty_relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eam-pty-', dir='/tmp')
        self.path = Path(self.temp.name)
        self.proc = None
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
            self.proc.wait(timeout=5)
            if self.proc.returncode not in (0,):
                self.fail(self.proc.stderr.read().decode())
            self.proc.stderr.close()
        self.temp.cleanup()

    def start(self, code):
        self.proc = subprocess.Popen([sys.executable, str(HERE.parents[1] / 'bridge/persistence/pty_relay.py'), 'serve', str(self.path),
                                      '--', sys.executable, '-u', '-c', code],
                                     stderr=subprocess.PIPE, start_new_session=True)
        self.wait(lambda: (self.path / 'state.json').exists())

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out')

    def state(self):
        return json.loads((self.path / 'state.json').read_text())

    def connect(self):
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(5)
        client.connect(str(self.path / 'socket'))
        self.clients.append(client)
        return client

    def read_until(self, client, marker):
        buffer, output = bytearray(), bytearray()
        while marker not in output:
            data = client.recv(65536)
            self.assertTrue(data, 'Unexpected disconnect')
            buffer.extend(data)
            for kind, payload in relay.packets(buffer):
                if kind == b'O':
                    output.extend(payload)
        return output

    def test_detach_unicode_resize_exit_latency(self):
        self.start("import os,tty,signal; tty.setraw(0); "
                   "signal.signal(signal.SIGWINCH,lambda *a: os.write(1, ('SIZE:%s\\n'%str(os.get_terminal_size(0))).encode())); "
                   "os.write(1,b'READY');\nwhile True:\n d=os.read(0,4096)\n if d==b'quit': break\n os.write(1,d)\n")
        client = self.connect()
        self.read_until(client, b'READY')
        child = self.state()['child_pid']
        for message in ['한글 받침 수정 ㄱ', '\x1b[200~여러 줄\n코드🙂\x1b[201~']:
            raw = message.encode()
            # Deliberately fragment a packet, including multibyte input.
            packet = relay.frame(b'I', raw)
            for byte in packet:
                client.sendall(bytes([byte]))
            self.assertIn(raw, self.read_until(client, raw))
        client.sendall(relay.frame(b'R', struct.pack('HHHH', 31, 91, 0, 0)))
        self.read_until(client, b'columns=91, lines=31')
        samples = []
        for i in range(100):
            raw = ('PING-%03d' % i).encode()
            started = time.perf_counter()
            client.sendall(relay.frame(b'I', raw))
            self.read_until(client, raw)
            samples.append((time.perf_counter() - started) * 1000)
        client.close()
        self.wait(lambda: self.state()['state'] == 'detached')
        os.kill(child, 0)
        client = self.connect()
        self.read_until(client, b'PING-099')
        self.assertEqual(child, self.state()['child_pid'])
        other = self.connect()
        self.assertEqual(other.recv(100), b'')
        client.sendall(relay.frame(b'I', b'quit'))
        self.proc.wait(timeout=5)
        self.assertEqual(self.state()['state'], 'exited')
        self.assertEqual(self.state()['exit_code'], 0)
        self.assertFalse((self.path / 'socket').exists())
        print(json.dumps(dict(test='socket-PTY-echo-100', median_ms=statistics.median(samples),
                              p95_ms=sorted(samples)[94], max_ms=max(samples))))

    def test_detached_flood_bounded_replay(self):
        self.start("import os,tty; tty.setraw(0); os.write(1,b'READY'); os.read(0,1); os.write(1,b'GO'); "
                   "[(os.write(1,b'x'*16384)) for _ in range(2048)]; "
                   "open('" + str(self.path / 'done') + "','w').write('done'); os.read(0,1)")
        client = self.connect()
        self.read_until(client, b'READY')
        client.sendall(relay.frame(b'I', b'g'))
        self.read_until(client, b'GO')
        client.close()
        self.wait(lambda: (self.path / 'done').exists())
        # Capture counters at a state transition after 32 MiB of detached output.
        client = self.connect()
        buffer = bytearray()
        handshake = None
        while handshake is None:
            buffer.extend(client.recv(65536))
            for kind, payload in relay.packets(buffer):
                if kind == b'H':
                    handshake = json.loads(payload)
        self.assertFalse(handshake['replay_complete'])
        self.wait(lambda: self.state()['state'] == 'attached')
        state = self.state()
        self.assertGreaterEqual(state['output_bytes'], 32 * 1024 * 1024)
        self.assertEqual(state['replay_bytes'], 0)
        self.assertLessEqual(state['peak_output_queue'], relay.LIMIT)
        print(json.dumps(dict(test='detached-32MiB', **state)))
        client.sendall(relay.frame(b'I', b'q'))
        self.proc.wait(timeout=5)

    def test_one_mib_replay_and_boundary(self):
        size = relay.REPLAY_LIMIT
        self.assertEqual(size, 1024 * 1024)
        self.start("import os,tty; tty.setraw(0); data=b'a'*(" + str(size) + "-3)+b'END'; "
                   "\nwhile data:\n n=os.write(1,data); data=data[n:]\n"
                   "open(" + repr(str(self.path / 'ready')) + ",'w').write('done')\n"
                   "while True:\n d=os.read(0,1)\n if d==b'q': break\n os.write(1,d)\n")
        self.wait(lambda: (self.path / 'ready').exists())
        client = self.connect()
        self.assertEqual(self.read_until(client, b'END'), b'a'*(size-3)+b'END')
        client.close()
        self.wait(lambda: self.state()['state'] == 'detached')
        self.assertEqual(self.state()['replay_bytes'], size)
        client = self.connect()
        self.assertEqual(self.read_until(client, b'END'), b'a'*(size-3)+b'END')
        client.sendall(relay.frame(b'I', b'x'))
        self.read_until(client, b'x')
        client.close()
        self.wait(lambda: self.state()['state'] == 'detached')
        self.assertTrue(self.state()['replay_overflow'])
        client = self.connect()
        buffer = bytearray()
        while True:
            buffer.extend(client.recv(4096))
            headers = [json.loads(payload) for kind,payload in relay.packets(buffer) if kind == b'H']
            if headers:
                self.assertFalse(headers[0]['replay_complete'])
                break
        client.sendall(relay.frame(b'I', b'q'))
        self.proc.wait(timeout=5)

    def test_attach_client_drains_one_mib_replay(self):
        size = relay.REPLAY_LIMIT
        self.start("import os,tty; tty.setraw(0); data=b'a'*(" + str(size) + "-3)+b'END'; "
                   "\nwhile data:\n n=os.write(1,data); data=data[n:]\n"
                   "open(" + repr(str(self.path / 'ready')) + ",'w').write('done')\nos.read(0,1)\n")
        self.wait(lambda: (self.path / 'ready').exists())
        client = self.connect()
        self.read_until(client, b'END')
        client.close()
        self.wait(lambda: self.state()['state'] == 'detached')
        proc = subprocess.Popen([sys.executable, str(relay.__file__), 'attach', str(self.path)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            output = bytearray()
            end = time.monotonic() + 5
            while b'END' not in output and time.monotonic() < end:
                if select.select([proc.stdout], [], [], .1)[0]:
                    data = os.read(proc.stdout.fileno(), 65536)
                    self.assertTrue(data, 'Attach exited before replay completion')
                    output.extend(data)
            self.assertEqual(output, b'a'*(size-3)+b'END')
            proc.stdin.write(b'q')
            proc.stdin.flush()
            proc.wait(timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr.read().decode())
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()

    def test_idle_attach_forwards_resize_without_input(self):
        self.start("import os,tty,signal,time; tty.setraw(0); "
                   "signal.signal(signal.SIGWINCH,lambda *a: os.write(1,('SIZE:%d\\n'%os.get_terminal_size(0).columns).encode())); "
                   "os.write(1,b'READY'); time.sleep(30)")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 80, 0, 0))
        client = subprocess.Popen([sys.executable, str(relay.__file__), 'attach', str(self.path)],
                                  stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        def receive(marker):
            output = bytearray()
            deadline = time.monotonic() + 2
            while marker not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    output.extend(os.read(master, 65536))
            self.assertIn(marker, output)
        try:
            receive(b'READY')
            time.sleep(.1)
            for columns in (97, 143, 61):
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 30, columns, 0, 0))
                os.kill(client.pid, signal.SIGWINCH)
                receive(('SIZE:%d'%columns).encode())
        finally:
            client.terminate()
            client.wait(timeout=5)
            os.close(master)

    def test_malformed_client_is_disconnected_without_killing_cli(self):
        self.start("import os,tty; tty.setraw(0); os.write(1,b'READY'); os.read(0,1)")
        client = self.connect()
        self.read_until(client, b'READY')
        # A claimed huge packet must not pin the parser waiting for its body.
        client.sendall(relay.HEADER.pack(b'I', relay.LIMIT))
        self.assertEqual(client.recv(100), b'')
        self.wait(lambda: self.state()['state'] == 'detached')
        replacement = self.connect()
        self.read_until(replacement, b'READY')
        replacement.sendall(relay.frame(b'I', b'q'))
        self.proc.wait(timeout=5)
        self.assertEqual(self.state()['exit_code'], 0)

    def test_slow_client_does_not_block_child(self):
        self.start("import os,tty; tty.setraw(0); os.write(1,b'READY'); os.read(0,1); os.write(1,b'GO'); "
                   "[(os.write(1,b'x'*16384)) for _ in range(2048)]; "
                   "open('" + str(self.path / 'done') + "','w').write('done'); os.read(0,1)")
        client = self.connect()
        self.read_until(client, b'READY')
        client.sendall(relay.frame(b'I', b'g'))
        self.wait(lambda: (self.path / 'done').exists())
        self.wait(lambda: self.state()['state'] == 'detached')
        self.assertLessEqual(self.state()['peak_output_queue'], relay.LIMIT)
        # A new reader can attach after the stalled client was evicted.
        replacement = self.connect()
        replacement.recv(65536)
        replacement.sendall(relay.frame(b'I', b'q'))
        self.proc.wait(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
