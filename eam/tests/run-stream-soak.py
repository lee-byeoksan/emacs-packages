#!/usr/bin/env python3
"""Reproduce a bounded stream soak with source hashes and memory sampling."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
parser.add_argument("--seconds", type=int, default=600)
args = parser.parse_args()
if not 5 <= args.seconds <= 7200:
    parser.error("--seconds must be between 5 and 7200")
root = Path(__file__).resolve().parents[1]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
sources = ["lisp/eam.el", "lisp/eam-terminal.el",
           "tests/terminal-stream-soak.el", "tests/stream-fixture.py",
           "tests/analyze-stream-soak.py", "tests/process-footprint.py",
           "tests/run-stream-soak.py",
           "var/deps/ghostel/lisp/ghostel.el", "var/deps/ghostel/ghostel-module.dylib"]
# Record optional compiled artifacts too, since Emacs may choose them when newer.
sources += [str(path.relative_to(root)) for path in (root / "lisp").glob("*.elc")]
sources += [str(path.relative_to(root)) for path in (root / "lisp").glob("*.el")
            if str(path.relative_to(root)) not in sources]
hashes = {}
for name in sources:
    with (root / name).open("rb") as source:
        hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()
manifest = {"started_at": time.time(), "seconds": args.seconds, "sessions": 2,
            "source_sha256": hashes, "system": platform.platform(), "python": sys.version,
            "gui": False, "real_providers_started": False}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
env = dict(os.environ, EMACS_AI_STREAM_DIR=str(output), EMACS_AI_STREAM_SECONDS=str(args.seconds))
job = footprint = None
with (output / "emacs.log").open("wb") as log, (output / "footprint.log").open("wb") as footprint_log:
    try:
        job = subprocess.Popen(["/Applications/Emacs.app/Contents/MacOS/Emacs", "--batch", "-Q",
                                "-L", "lisp", "--eval", "(setq load-prefer-newer t)",
                                "-l", "tests/terminal-stream-soak.el"], cwd=root, env=env,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        (output / "emacs.pid").write_text(str(job.pid))
        footprint = subprocess.Popen([sys.executable, str(root / "tests/process-footprint.py"),
                                       str(job.pid), str(output / "footprint.jsonl"),
                                       "--duration", str(min(7200, args.seconds + 30)),
                                       "--interval", str(min(30, args.seconds / 2))],
                                      stdin=subprocess.DEVNULL, stdout=footprint_log, stderr=subprocess.STDOUT)
        if job.wait(timeout=args.seconds + 45) != 0:
            raise RuntimeError("Emacs soak failed; see emacs.log")
        if footprint.wait(timeout=35) != 0:
            raise RuntimeError("Memory sampler failed; see footprint.log")
        subprocess.run([sys.executable, "tests/analyze-stream-soak.py", str(output)], cwd=root, check=True)
        if not (output / "footprint.jsonl").stat().st_size:
            raise RuntimeError("No physical footprint samples")
        cleanup = [json.loads(line) for line in (output / "cleanup.jsonl").read_text().splitlines()]
        if [row['phase'] for row in cleanup] != ['before-close', 'closed', 'closed-gc']:
            raise RuntimeError('Missing cleanup memory boundaries')
        if any(row['sessions'] != 0 for row in cleanup[1:]):
            raise RuntimeError('Terminal sessions survived cleanup')
    finally:
        for process in (job, footprint):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
