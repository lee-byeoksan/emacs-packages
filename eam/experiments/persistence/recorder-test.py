"""Real PTY tests for the experimental recorder; finite fake child only."""
import fcntl
import pty
import signal
import struct
import termios
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import unittest

RECORDER = Path(__file__).with_name('recorder.py')
BLOCK = b'x'*4094+b'\r\n'
SIZE = len(BLOCK)*8192


def wait(predicate):
    deadline = time.monotonic()+8
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.02)
    raise TimeoutError('condition not met')


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eai-recorder-')
        self.root = Path(self.temp.name)
        self.jobs = []
    def tearDown(self):
        for process in self.jobs:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream: stream.close()
        self.temp.cleanup()
    def start(self, code, limit=False):
        archive = self.root/'output.ansi'
        def restrict(): resource.setrlimit(resource.RLIMIT_FSIZE, (4096,4096))
        p = subprocess.Popen([sys.executable,str(RECORDER),str(archive),sys.executable,'-c',code],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             preexec_fn=restrict if limit else None)
        self.jobs.append(p)
        return p,archive
    def test_slow_display_backpressures_without_losing_bytes(self):
        code = "import os,tty; tty.setraw(0); b=b'x'*4094+b'\\r\\n'; [os.write(1,b) for _ in range(8192)]"
        p,archive = self.start(code)
        wait(lambda: archive.exists() and archive.stat().st_size > 16384)
        def rss(): return int(subprocess.check_output(['ps','-o','rss=','-p',str(p.pid)]).strip())
        before = rss()
        time.sleep(.2)
        first = archive.stat().st_size
        time.sleep(.3)
        after = rss()
        self.assertEqual(first,archive.stat().st_size)
        self.assertLess(first,1024*1024)
        self.assertLess(after-before,16*1024)
        digest=hashlib.sha256()
        count=0
        while chunk := p.stdout.read(65536):
            digest.update(chunk); count+=len(chunk)
        self.assertEqual(p.wait(timeout=5),0,p.stderr.read())
        self.assertEqual(count,SIZE)
        self.assertEqual(digest.hexdigest(),hashlib.sha256(archive.read_bytes()).hexdigest())
        print(json.dumps(dict(test='slow-display', emitted=SIZE, stalled_archive_bytes=first,
                              rss_before_kib=before,rss_after_kib=after,exact_output=True)))
    def test_disk_limit_stops_child(self):
        pidfile=self.root/'pid'
        code=f"import os,tty,time; from pathlib import Path; tty.setraw(0); Path({str(pidfile)!r}).write_text(str(os.getpid())); [os.write(1,b'x'*4096) for _ in range(8192)]; time.sleep(30)"
        p,archive=self.start(code,limit=True)
        p.wait(timeout=8)
        self.assertEqual(p.returncode,74)
        self.assertIn(b'RECORDING ERROR',p.stderr.read())
        self.assertLessEqual(archive.stat().st_size,4096)
        with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()),0)
    def test_existing_archive_never_starts_child(self):
        (self.root/'output.ansi').write_bytes(b'keep')
        marker=self.root/'started'
        p,archive=self.start(f'from pathlib import Path; Path({str(marker)!r}).touch()')
        self.assertEqual(p.wait(timeout=5),74)
        self.assertFalse(marker.exists())
        self.assertEqual(archive.read_bytes(),b'keep')
    def test_korean_input_and_exit_code(self):
        marker=self.root/'ready'
        payload='한글 전달'.encode()
        code=f"import os,tty; from pathlib import Path; tty.setraw(0); Path({str(marker)!r}).touch(); data=os.read(0,1024); os.write(1,data); raise SystemExit(7)"
        p,archive=self.start(code)
        wait(marker.exists)
        p.stdin.write(payload); p.stdin.flush()
        p.wait(timeout=5)
        self.assertEqual(p.returncode,7)
        self.assertEqual(p.stdout.read(),payload)
        self.assertEqual(archive.read_bytes(),payload)

    def test_child_signal_exit_code(self):
        p,_=self.start('import os,signal; os.kill(os.getpid(),signal.SIGTERM)')
        self.assertEqual(p.wait(timeout=5),143,p.stderr.read())
    def test_explicit_terminate_stops_child(self):
        marker=self.root/'pid'
        p,_=self.start(f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)")
        wait(marker.exists)
        p.terminate()
        self.assertEqual(p.wait(timeout=5),143,p.stderr.read())
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()),0)
    def test_ctrl_c_reaches_child_foreground_group(self):
        marker=self.root/'ready'
        code=f"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGINT,lambda *_: (os.write(1,b'INTERRUPTED'),exit(23))); Path({str(marker)!r}).touch(); time.sleep(30)"
        p,archive=self.start(code)
        wait(marker.exists)
        p.stdin.write(b'\x03'); p.stdin.flush()
        self.assertEqual(p.wait(timeout=5),23)
        self.assertIn(b'INTERRUPTED',archive.read_bytes())
    def test_outer_pty_resize_and_control_bytes(self):
        master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',28,93,0,0))
        marker=self.root/'ready'
        sizefile=self.root/'size'
        code=f"""import os,signal,tty,struct,fcntl,termios,time
from pathlib import Path
tty.setraw(0)
def size(*_):
 Path({str(sizefile)!r}).write_text(str(struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8))[:2]))
signal.signal(signal.SIGWINCH,size)
size()
os.write(1,b'\\x1b[?2004h\\x1b]9;fixture\\x07')
Path({str(marker)!r}).touch()
data=os.read(0,1024)
os.write(1,data)
"""
        archive=self.root/'output.ansi'
        p=subprocess.Popen([sys.executable,str(RECORDER),str(archive),sys.executable,'-c',code],
                           stdin=slave,stdout=slave,stderr=subprocess.PIPE)
        self.jobs.append(p)
        try:
            wait(marker.exists)
            self.assertEqual(sizefile.read_text(),'(28, 93)')
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',37,111,0,0))
            p.send_signal(signal.SIGWINCH)
            wait(lambda: sizefile.read_text()=='(37, 111)')
            payload=b'\x1b[200~'+ '한글\n붙여넣기'.encode()+b'\x1b[201~'
            os.write(master,payload)
            self.assertEqual(p.wait(timeout=5),0,p.stderr.read())
            self.assertEqual(archive.read_bytes(),b'\x1b[?2004h\x1b]9;fixture\x07'+payload)
        finally:
            os.close(master); os.close(slave)

if __name__=='__main__': unittest.main()
