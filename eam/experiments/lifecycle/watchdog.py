#!/usr/bin/env python3
"""macOS observation-only ownership experiment. NOT a complete tree boundary.

No command/environment scanning; PID + start time is rechecked before signals.
Fast orphaning between snapshots and check-to-kill races remain limitations.
"""
import argparse
import ctypes as C
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import time


class BSDInfo(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in
                "flags status xstatus pid ppid uid gid ruid rgid svuid svgid reserved".split()] + [
        ("comm", C.c_char * 16), ("name", C.c_char * 32)] + [
        (n, C.c_uint32) for n in "nfiles pgid jobc tdev tpgid".split()] + [
        ("nice", C.c_int32), ("sec", C.c_uint64), ("usec", C.c_uint64)]


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    sec: int
    usec: int

    @property
    def identity(self):
        return self.pid, self.sec, self.usec


class Processes:
    def __init__(self):
        self.lib = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.lib.proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
        self.lib.proc_pidinfo.restype = C.c_int
        self.lib.proc_listallpids.argtypes = [C.c_void_p, C.c_int]
        self.lib.proc_listallpids.restype = C.c_int
        assert C.sizeof(BSDInfo) == 136

    def get(self, pid):
        info = BSDInfo()
        C.set_errno(0)
        n = self.lib.proc_pidinfo(pid, 3, 0, C.byref(info), C.sizeof(info))
        if n == 0:
            error = C.get_errno()
            if error in (0, 3):  # vanished / ESRCH
                return None
            raise OSError(error, "proc_pidinfo", pid)
        if n != C.sizeof(info):
            raise RuntimeError("Incomplete proc_pidinfo result")
        if info.status == 5:  # SZOMB; no longer executing
            return None
        return Process(info.pid, info.ppid, info.uid, info.sec, info.usec)

    def snapshot(self):
        # Retry a full buffer rather than silently treating truncation as complete.
        size = 4096
        while size <= 262144:
            buf = (C.c_int * size)()
            n = self.lib.proc_listallpids(buf, C.sizeof(buf))
            if n <= 0:
                raise RuntimeError("Process enumeration failed")
            if n < size:
                break
            size *= 2
        else:
            raise RuntimeError("Process enumeration limit exceeded")
        result = {}
        for pid in buf[:n]:
            if pid <= 0:
                continue
            try:
                p = self.get(pid)
            except PermissionError:
                continue  # unrelated protected processes; owned checks are strict
            if p and p.uid == os.getuid():
                result[p.pid] = p
        return result


def discover(owned, snapshot):
    """Retain only exact identities; extend through currently observed parents."""
    alive = {pid: p for pid, p in owned.items()
             if pid in snapshot and snapshot[pid].identity == p.identity}
    while True:
        added = {pid: p for pid, p in snapshot.items()
                 if pid not in alive and p.ppid in alive
                 and (p.sec, p.usec) >= alive[p.ppid].identity[1:]}
        if not added:
            return alive
        alive.update(added)


def signal_verified(processes, p, sig):
    current = processes.get(p.pid)
    if not current or current.identity != p.identity or current.uid != os.getuid():
        return False
    try:
        os.kill(p.pid, sig)
        return True
    except ProcessLookupError:
        return False


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def run(args):
    os.umask(0o077)
    output = Path(args.output)
    processes = Processes()
    owner, root = processes.get(args.owner), processes.get(args.root)
    if not owner or not root or root.uid != os.getuid() or owner.uid != os.getuid():
        raise RuntimeError("Live same-user owner and root required")
    if args.root == os.getpid() or args.owner == os.getpid():
        raise RuntimeError("Watchdog cannot supervise itself")
    owned = {root.pid: root}
    events = []
    peak = 1
    reason = None
    start = time.monotonic()
    # Test watchdog is launched by the independent harness, outside the owned tree.
    while time.monotonic() - start < args.timeout:
        snapshot = processes.snapshot()
        # Strictly query all known identities: permission failures must not look like exits.
        for p in owned.values():
            processes.get(p.pid)
        owned = discover(owned, snapshot)
        peak = max(peak, len(owned))
        write_json(output / "observed.json", {"watchdog": os.getpid(),
                   "owned": [p.identity for p in owned.values()]})
        current_owner, current_root = processes.get(owner.pid), processes.get(root.pid)
        if not current_owner or current_owner.identity != owner.identity:
            reason = "owner-exit"
        elif not current_root or current_root.identity != root.identity:
            reason = "root-exit"
        if reason:
            break
        time.sleep(args.interval)
    if reason is None:
        raise RuntimeError("Observation timeout; harness must clean up")
    # Bounded repeated discovery during grace; never infer ancestry from names.
    deadline = time.monotonic() + 2
    termed = set()
    while owned and time.monotonic() < deadline:
        owned = discover(owned, processes.snapshot())
        for p in owned.values():
            if p.identity not in termed:
                if signal_verified(processes, p, signal.SIGTERM):
                    events.append({"identity": p.identity, "signal": "TERM"})
                termed.add(p.identity)
        time.sleep(args.interval)
    for p in owned.values():
        if signal_verified(processes, p, signal.SIGKILL):
            events.append({"identity": p.identity, "signal": "KILL"})
    time.sleep(.2)
    remaining = []
    for p in owned.values():
        current = processes.get(p.pid)
        if current and current.identity == p.identity:
            remaining.append(p.identity)
    write_json(output / "result.json", {"reason": reason, "peak_owned": peak,
               "signals": events, "remaining_observed": remaining,
               "elapsed_seconds": time.monotonic() - start,
               "complete_tree_guarantee": False})
    if remaining:
        raise RuntimeError("Observed processes remain alive")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=int, required=True)
    parser.add_argument("--root", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=float, default=.05)
    parser.add_argument("--timeout", type=float, default=30)
    options = parser.parse_args()
    if options.interval < .01 or options.timeout <= 0:
        parser.error("interval >= .01 and timeout > 0 required")
    run(options)
