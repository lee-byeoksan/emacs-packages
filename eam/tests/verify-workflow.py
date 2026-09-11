#!/usr/bin/env python3
"""Independently check the real-CLI workflow artifacts; makes no AI requests."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
parser.add_argument('evidence', type=Path)
args = parser.parse_args()
args.evidence.mkdir(parents=True, exist_ok=True)
results = []
for provider in ('claude', 'codex'):
    project = args.directory / provider / 'workspace'
    result = subprocess.run(['python3', '-m', 'unittest', '-v'], cwd=project,
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    if result.returncode or 'Ran 6 tests' not in output:
        raise RuntimeError(f'{provider}: expected six passing tests\n{output}')
    spec = importlib.util.spec_from_file_location(f'{provider}_label', project / 'label.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value, expected in [(' \t한글\n 프로젝트🙂 ', '한글 프로젝트🙂'),
                            ('alpha\u2003beta', 'alpha beta')]:
        if module.label(value) != expected:
            raise AssertionError(f'{provider}: normalization mismatch')
    for value, exception in [('', ValueError), (' \t\n', ValueError),
                             (None, TypeError), (42, TypeError), (b'bytes', TypeError),
                             ([], TypeError), ({}, TypeError), (1.5, TypeError)]:
        try:
            module.label(value)
        except exception:
            pass
        else:
            raise AssertionError(f'{provider}: missing {exception.__name__}')
    for path in project.glob('*.py'):
        if '은하-924' in path.read_text():
            raise AssertionError(f'Conversation marker found in {path}')
    (args.evidence / f'{provider}-resumed-tests.txt').write_text(output)
    for name in ('label.py', 'test_label.py'):
        (args.evidence / f'{provider}-final-{name}').write_bytes((project / name).read_bytes())
    results.append({'provider': provider, 'unittest_count': 6,
                    'independent_cases': 10, 'exit_code': result.returncode,
                    'marker_absent_from_python_files': True})
(args.evidence / 'resumed-checks.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
