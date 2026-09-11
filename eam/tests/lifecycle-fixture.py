#!/usr/bin/env python3
"""Owned lifecycle probe: foreground child plus deliberately detached child."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

directory = Path(sys.argv[1])
if len(sys.argv) > 2:
    # Each child exits on its own as a final test-only backstop.
    end = time.monotonic() + 45
    while time.monotonic() < end:
        (directory / (sys.argv[2] + ".heartbeat")).write_text(str(time.time()))
        time.sleep(.1)
else:
    children = []
    for mode in ("foreground", "detached"):
        children.append(subprocess.Popen(
            [sys.executable, __file__, str(directory), mode],
            start_new_session=mode == "detached",
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    (directory / "pids.json").write_text(json.dumps({
        "cli": os.getpid(), "foreground": children[0].pid, "detached": children[1].pid}))
    print("READY", flush=True)
    for child in children:
        child.wait()
