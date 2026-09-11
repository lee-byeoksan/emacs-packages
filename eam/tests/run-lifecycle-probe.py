#!/usr/bin/env python3
"""Run four bounded local probes; clean only child PIDs verified as our fixture."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(__file__).resolve().parent.parent
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=False)
results = []
for action in ("close", "cli-kill", "emacs-exit", "emacs-kill"):
    directory = output / action
    directory.mkdir(exist_ok=True)
    env = dict(os.environ, EMACS_AI_LIFECYCLE_DIR=str(directory), EMACS_AI_LIFECYCLE_ACTION=action)
    job = subprocess.Popen(["/Applications/Emacs.app/Contents/MacOS/Emacs", "--batch", "-Q", "-L", "lisp",
                            "-l", "tests/terminal-lifecycle-probe.el"], cwd=root, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        stdout, _ = job.communicate(timeout=15)
        (directory / "emacs-output.txt").write_bytes(stdout)
        time.sleep(.5)
        row = {"action": action, "emacs_exit_code": job.returncode}
        for mode in ("foreground", "detached"):
            p = directory / (mode + ".heartbeat")
            before = p.read_text() if p.exists() else None
            time.sleep(.3)
            row[mode + "_still_working"] = p.exists() and p.read_text() != before
        results.append(row)
    finally:
        if job.poll() is None:
            job.kill()
            job.wait()
        p = directory / "pids.json"
        if p.exists():
            for pid in json.loads(p.read_text()).values():
                command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
                if "lifecycle-fixture.py" in command and str(directory) in command:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
(output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results, indent=2))
