#!/usr/bin/env python3
"""Kill only an identified validation CLI while its known probe is working."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

directory, workspace = map(Path, sys.argv[1:])
state = json.loads((directory / "state.json").read_text())
pid = int((workspace / "exit-probe.pid").read_text())
heartbeat = workspace / "exit-probe.heartbeat"


def command(process_id):
    return subprocess.run(["ps", "-p", str(process_id), "-o", "command="],
                          capture_output=True, text=True).stdout.strip()


def working():
    before = heartbeat.read_text()
    time.sleep(.3)
    return heartbeat.read_text() != before


assert "exit_probe.py" in command(pid), "Probe identity mismatch"
assert directory.name in ("claude", "codex")
assert directory.name in command(state["cli_pid"]), "CLI identity mismatch"
assert working(), "Probe not actively running; do not count a natural exit"
result = {"probe_running_before_kill": True, "cli_pid": state["cli_pid"], "probe_pid": pid}
try:
    os.kill(state["cli_pid"], signal.SIGKILL)
    time.sleep(2)
    result["probe_still_working_after_cli_kill"] = working()
    result["probe_process_remained"] = bool(command(pid))
finally:
    # This file is our fixture, not an arbitrary descendant or shared daemon.
    if "exit_probe.py" in command(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            result["test_controller_cleaned_probe"] = True
        except ProcessLookupError:
            pass
    time.sleep(.3)
    result["probe_command_after_cleanup"] = command(pid)
    (directory / "kill-result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
