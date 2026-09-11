#!/usr/bin/env python3
"""Bounded fixture: stubborn detached task plus optional fast orphan."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

directory = Path(sys.argv[1])
if len(sys.argv) > 2:
    mode = sys.argv[2]
    if mode == "detached":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        (directory / (mode + ".heartbeat")).write_text(str(time.time()))
        time.sleep(.05)
else:
    children = [subprocess.Popen([sys.executable, __file__, str(directory), mode],
                start_new_session=mode == "detached", stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for mode in ("foreground", "detached")]
    (directory / "pids.json").write_text(json.dumps({"cli": os.getpid(),
        "foreground": children[0].pid, "detached": children[1].pid}))
    end = time.monotonic() + 60
    raced = False
    print("READY", flush=True)
    while time.monotonic() < end:
        if (directory / "race").exists() and not raced:
            raced = True
            intermediary = os.fork()
            if intermediary == 0:
                os.setsid()
                grandchild = os.fork()
                if grandchild == 0:
                    devnull = os.open(os.devnull, os.O_RDWR)
                    for fd in (0, 1, 2):
                        os.dup2(devnull, fd)
                    os.close(devnull)
                    os.execv(sys.executable, [sys.executable, __file__, str(directory), "orphan"])
                (directory / "orphan.pid").write_text(str(grandchild))
                os._exit(0)
            os.waitpid(intermediary, 0)
            (directory / "race-complete").touch()
        time.sleep(.02)
