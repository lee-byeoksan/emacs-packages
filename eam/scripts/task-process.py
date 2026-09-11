#!/usr/bin/env python3
"""Inspect a known task PID, or SIGTERM that exact inspected identity.

No process discovery by name, no descendant sweep, no automatic cleanup.
An identity receipt does not prove that the task belongs to an AI session.
"""
import argparse
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parent.parent

def processes():
    # Reuse the tested, read-only libproc identity implementation. Importing the
    # experiment does not run its observer or send any signals.
    spec = importlib.util.spec_from_file_location('emacs_ai_process_identity',
                    ROOT / 'experiments/lifecycle/watchdog.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Processes()

def inspect(pid):
    if pid <= 1 or pid in (os.getpid(), os.getppid()):
        raise ValueError('Choose a separate task process owned by this user')
    process = processes().get(pid)
    if process is None:
        raise ValueError('PID is no longer running')
    if process.uid != os.getuid():
        raise ValueError('Choose a separate task process owned by this user')
    return asdict(process)

def stop(receipt):
    if not all(type(receipt.get(key)) is int for key in ('pid', 'uid', 'sec', 'usec')):
        raise ValueError('Invalid process identity receipt')
    current = inspect(receipt['pid'])
    if any(current[key] != receipt[key] for key in ('pid', 'uid', 'sec', 'usec')):
        raise ValueError('Process identity changed; refusing to signal')
    os.kill(current['pid'], signal.SIGTERM)
    return {'pid': current['pid'], 'signal_sent': 'SIGTERM',
            'exit_confirmed': False, 'complete_tree_guarantee': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect').add_argument('pid', type=int)
    commands.add_parser('stop').add_argument('receipt', type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.pid) if args.command == 'inspect' else stop(json.loads(args.receipt.read_text()))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
