#!/usr/bin/env python3
"""Run the explicit PTY harness with a process-only refused local proxy.

Does not send a prompt. Drive/close it with cli-validation-control.py.
The bound, non-listening socket reserves the port without accepting credentials.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('provider', choices=['claude', 'codex'])
parser.add_argument('directory', type=Path)
args = parser.parse_args()
directory = args.directory.resolve()
directory.mkdir(parents=True, exist_ok=False)
(directory / 'workspace').mkdir()
with socket.socket() as refused:
    refused.bind(('127.0.0.1', 0))
    proxy = f'http://127.0.0.1:{refused.getsockname()[1]}'
    env = os.environ.copy()
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env[name] = proxy
    for name in ('NO_PROXY', 'no_proxy'):
        env[name] = ''
    env.update(EMACS_AI_VALIDATION_DIR=str(directory),
               EMACS_AI_VALIDATION_PROVIDER=args.provider)
    (directory / 'probe.json').write_text(json.dumps({
        'provider': args.provider, 'proxy': proxy,
        'listening': False, 'scope': 'child process environment only',
        'prompts_sent_by_launcher': 0}, indent=2) + '\n')
    result = subprocess.run([
        '/Applications/Emacs.app/Contents/MacOS/Emacs', '--batch', '-Q',
        '-L', 'lisp', '-l', 'tests/cli-validation-driver.el'], cwd=ROOT, env=env)
    raise SystemExit(result.returncode)
