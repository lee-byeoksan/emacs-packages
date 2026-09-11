#!/usr/bin/env python3
"""Bounded-rate PTY output and bracketed-paste receipts; no AI/network."""
import hashlib
import json
import os
from pathlib import Path
import select
import sys
import time
import tty

directory = Path(sys.argv[1])
duration = float(sys.argv[2])
tty.setraw(0)
digest = hashlib.sha256()
written = 0


def emit(data):
    global written
    while data:
        n = os.write(1, data)
        digest.update(data[:n])
        written += n
        data = data[n:]


pending = bytearray()
emit(b"\x1b[?2004h")
end = time.monotonic() + duration
sequence = 0
with (directory / "receipts.jsonl").open("w") as receipts:
    while time.monotonic() < end:
        text = (f"\x1b[32mLOG {sequence:08d}\x1b[0m " + "한글🙂 code(x); " * 100 + "\r\n")
        if sequence % 100 == 0:
            text += "LONG " + "가나다abc" * 1500 + "\r\n"
        emit(text.encode())
        if select.select([0], [], [], .025)[0]:
            data = os.read(0, 4096)
            if not data:
                break
            pending.extend(data)
            while b"\x1b[201~" in pending:
                value, _, rest = pending.partition(b"\x1b[201~")
                pending = bytearray(rest)
                value = value.partition(b"\x1b[200~")[2]
                receipts.write(json.dumps({"text": value.decode(), "received_at": time.time()}, ensure_ascii=False) + "\n")
                receipts.flush()
            if len(pending) > 65536:
                raise RuntimeError("Unexpected unbounded input")
        sequence += 1
(directory / "expected.json").write_text(json.dumps({"bytes": written, "sha256": digest.hexdigest(), "chunks": sequence}))
