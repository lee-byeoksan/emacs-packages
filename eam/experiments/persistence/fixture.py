"""Finite local fake CLI for the detached terminal experiment; no AI calls."""
import os
from pathlib import Path
import sys
import time
import tty

root = Path(sys.argv[1])
tty.setraw(0)
(root / 'pid').write_text(str(os.getpid()))
gate_deadline = time.monotonic() + 45
while not (root / 'go').exists():
    if time.monotonic() >= gate_deadline:
        raise SystemExit('Fixture gate timed out')
    time.sleep(.01)
os.write(1, b'\x1b[?2004h')
for n in range(6000):
    os.write(1, f'ROW:{n:05d}:한글 코드 log {"x" * 180}\r\n'.encode())
os.write(1, b'\x1b]9;detached-event\x07READY\r\n')
received = bytearray()
deadline = time.monotonic() + 45
import select
while time.monotonic() < deadline:
    if not select.select([0], [], [], .1)[0]:
        continue
    data = os.read(0, 4096)
    if not data:
        break
    received.extend(data)
    (root / 'input.bin').write_bytes(received)
    os.write(1, b'INPUT:' + data + b'\r\n')
    os.write(1, b'\x1b]9;fixture-nine\x07\x1b]777;notify;fixture;fixture-seven\x07')
    if b'EXIT' in received:
        break
