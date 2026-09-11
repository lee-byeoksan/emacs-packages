#!/usr/bin/env python3
"""Real batch Emacs/Ghostel lifecycle experiment; no AI, no GUI launch."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watchdog", root / "experiments/lifecycle/watchdog.py")
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)
processes = w.Processes()
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=False)
jobs, known, logs = [], {}, []


def wait_for(check, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(.025)
    raise TimeoutError("Fixture/watchdog handshake timed out")


def remember(pid):
    p = processes.get(pid)
    if p:
        known[p.identity] = p
    return p


def launch(directory, action):
    directory.mkdir()
    log = (directory / "emacs-output.txt").open("wb")
    logs.append(log)
    env = dict(os.environ, EMACS_AI_LIFECYCLE_DIR=str(directory),
               EMACS_AI_LIFECYCLE_ACTION=action, EMACS_AI_LIFECYCLE_GATE="1",
               EMACS_AI_LIFECYCLE_FIXTURE="tests/supervisor-fixture.py")
    job = subprocess.Popen(["/Applications/Emacs.app/Contents/MacOS/Emacs", "--batch", "-Q",
                            "-L", "lisp", "-l", "tests/terminal-lifecycle-probe.el"],
                           cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    jobs.append(job)
    remember(job.pid)
    wait_for(lambda: (directory / "emacs.pid").exists())
    pids = json.loads((directory / "pids.json").read_text())
    for pid in pids.values():
        assert remember(pid), "Fixture died before attachment"
    log = (directory / "watchdog-output.txt").open("wb")
    logs.append(log)
    watcher = subprocess.Popen([sys.executable, str(root / "experiments/lifecycle/watchdog.py"),
                                "--owner", str(job.pid), "--root", str(pids["cli"]),
                                "--output", str(directory), "--timeout", "55"],
                               start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    jobs.append(watcher)
    remember(watcher.pid)
    def attached():
        path = directory / "observed.json"
        return path.exists() and set(pids.values()).issubset(
            {p[0] for p in json.loads(path.read_text())["owned"]})
    wait_for(attached)
    for mode in ("foreground", "detached"):
        wait_for(lambda: (directory / (mode + ".heartbeat")).exists())
    return job, watcher, pids


def changing(path):
    before = path.read_text()
    time.sleep(.2)
    return path.read_text() != before


rows = []
try:
    control_dir = output / "other-session"
    control, control_watch, control_pids = launch(control_dir, "close")
    # Intentionally persistent/shared stand-in: launched outside the session tree.
    kept = subprocess.Popen([sys.executable, str(root / "tests/supervisor-fixture.py"),
                             str(output), "kept"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    jobs.append(kept)
    remember(kept.pid)
    wait_for(lambda: (output / "kept.heartbeat").exists())
    for action in ("close", "cli-kill", "emacs-exit", "emacs-kill", "fast-orphan"):
        directory = output / action
        job, watcher, pids = launch(directory, "cli-kill" if action == "fast-orphan" else action)
        orphan = None
        if action == "fast-orphan":
            # Force a scheduling gap, not a statistically lucky race.
            os.kill(watcher.pid, signal.SIGSTOP)
            (directory / "race").touch()
            wait_for(lambda: (directory / "race-complete").exists())
            orphan = remember(int((directory / "orphan.pid").read_text()))
            wait_for(lambda: processes.get(orphan.pid).ppid == 1)
            wait_for(lambda: (directory / "orphan.heartbeat").exists())
            os.kill(watcher.pid, signal.SIGCONT)
            time.sleep(.2)
            observed = json.loads((directory / "observed.json").read_text())
            assert orphan.pid not in {p[0] for p in observed["owned"]}
        assert changing(directory / "detached.heartbeat")
        (directory / "go").touch()
        job.wait(timeout=10)
        watcher.wait(timeout=10)
        assert watcher.returncode == 0, (directory / "watchdog-output.txt").read_text()
        result = json.loads((directory / "result.json").read_text())
        for pid in pids.values():
            assert processes.get(pid) is None, "Observed fixture remains executing"
        assert any(e["identity"][0] == pids["detached"] and e["signal"] == "KILL"
                   for e in result["signals"]), "Stubborn child did not exercise escalation"
        other_survives = all(processes.get(pid) for pid in control_pids.values())
        other_moves = changing(control_dir / "detached.heartbeat")
        kept_moves = changing(output / "kept.heartbeat")
        assert other_survives and other_moves and kept_moves
        row = {"action": action, "emacs_exit_code": job.returncode,
               "observed_tasks_stopped": True, "other_session_working": bool(other_survives and other_moves),
               "kept_external_task_working": kept_moves, "watchdog": result}
        if orphan:
            row["unobserved_orphan_still_working"] = changing(directory / "orphan.heartbeat")
            assert row["unobserved_orphan_still_working"], "Expected counterexample not reproduced"
            w.signal_verified(processes, orphan, signal.SIGKILL)
            wait_for(lambda: processes.get(orphan.pid) is None)
            row["orphan_cleaned_by_harness"] = True
        rows.append(row)
        print(json.dumps(row), flush=True)
    (control_dir / "go").touch()
    control.wait(timeout=10)
    control_watch.wait(timeout=10)
    assert control_watch.returncode == 0
    assert all(processes.get(pid) is None for pid in control_pids.values())
finally:
    # Only exact identities from this harness; never names or global process groups.
    for p in known.values():
        w.signal_verified(processes, p, signal.SIGKILL)
    for job in jobs:
        job.wait(timeout=5)
    for log in logs:
        log.close()
    time.sleep(.2)
    survivors = [p.identity for p in known.values()
                 if (current := processes.get(p.pid)) and current.identity == p.identity]
    w.write_json(output / "results.json", {"cases": rows, "harness_survivors": survivors,
                 "ai_requests": 0, "gui_test": False})
    assert not survivors, survivors
