#!/usr/bin/env python3
"""Read macOS rusage for one PID; never signal or inspect process contents."""
import argparse
import ctypes as C
import json
from pathlib import Path
import time

class Usage(C.Structure):
    _fields_ = [("uuid", C.c_uint8 * 16)] + [(name, C.c_uint64) for name in (
        "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size "
        "resident_size phys_footprint proc_start_abstime proc_exit_abstime "
        "child_user_time child_system_time child_pkg_idle_wkups child_interrupt_wkups "
        "child_pageins child_elapsed_abstime diskio_bytesread diskio_byteswritten").split()]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("pid", type=int)
parser.add_argument("output", type=Path)
parser.add_argument("--duration", type=float, default=2400)
parser.add_argument("--interval", type=float, default=30)
args = parser.parse_args()
if args.pid <= 0 or not 0 < args.duration <= 7200 or not .1 <= args.interval <= 60:
    parser.error("positive PID, duration <= 7200 and interval .1..60 required")
lib = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
lib.proc_pid_rusage.argtypes = [C.c_int, C.c_int, C.c_void_p]
lib.proc_pid_rusage.restype = C.c_int
assert C.sizeof(Usage) == 160
start = time.monotonic()
identity = None
# Refuse to replace an earlier measurement.
with args.output.open("x") as output:
    while time.monotonic() - start < args.duration:
        info = Usage()
        if lib.proc_pid_rusage(args.pid, 2, C.byref(info)) != 0:
            error = C.get_errno()
            if error == 3:  # ESRCH, target exited
                break
            raise OSError(error, "proc_pid_rusage")
        if identity is None:
            identity = info.proc_start_abstime
        if info.proc_start_abstime != identity or info.proc_exit_abstime:
            break
        row = {"pid": args.pid, "sampled_at": time.time(),
               "elapsed_seconds": time.monotonic() - start,
               "resident_bytes": info.resident_size,
               "physical_footprint_bytes": info.phys_footprint,
               "wired_bytes": info.wired_size}
        output.write(json.dumps(row) + "\n")
        output.flush()
        time.sleep(args.interval)
