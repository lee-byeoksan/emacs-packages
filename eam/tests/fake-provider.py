#!/usr/bin/env python3
"""Local protocol fixture. Never connects to a model or the network."""
import json
import os
import subprocess
import sys
import time

def emit(message):
    data = (json.dumps(message, ensure_ascii=False) + '\n').encode()
    # Deliberately split UTF-8 and JSON tokens over pipe writes.
    for i in range(0, len(data), 2):
        os.write(sys.stdout.fileno(), data[i:i + 2])

def record(message):
    with open(os.environ['EMACS_AI_FIXTURE_REQUESTS'], 'a') as out:
        out.write(json.dumps(message) + '\n')

def mode():
    m = os.environ.get('EMACS_AI_FIXTURE_MODE', 'normal')
    if m == 'eof':
        sys.exit(0)
    if m == 'oversize':
        os.write(sys.stdout.fileno(), b'x' * (4 * 1024 * 1024 + 8192))
        sys.exit(0)
    if m == 'wait':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        with open(os.environ['EMACS_AI_FIXTURE_CHILD'], 'w') as out:
            out.write(str(child.pid))
        time.sleep(60)

if 'auth' in sys.argv:
    emit({'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'fixture'})
elif 'app-server' in sys.argv:
    for line in sys.stdin:
        req = json.loads(line)
        record(req)
        name = req.get('method')
        if name == 'initialized':
            continue
        result = {}
        if name == 'account/read':
            result = {'account': {'type': os.environ.get('EMACS_AI_FIXTURE_AUTH', 'chatgpt'), 'planType': 'fixture'}}
        if name in ('thread/start', 'thread/resume'):
            result = {'thread': {'id': 'fixture-codex'}, 'instructionSources': []}
        emit({'id': req['id'], 'result': result})
        if name == 'turn/start':
            mode()
            for text in ('한', '글🙂'):
                emit({'method': 'item/agentMessage/delta', 'params': {'itemId': 'a', 'delta': text}})
            emit({'method': 'item/completed', 'params': {'item': {'id': 'a', 'type': 'agentMessage', 'text': '한글🙂'}}})
            emit({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})
else:
    record({'args': sys.argv[1:], 'prompt': sys.stdin.read()})
    emit({'type': 'system', 'subtype': 'init', 'session_id': 'fixture-claude'})
    mode()
    emit({'type': 'stream_event', 'event': {'type': 'message_start'}})
    for text in ('한', '글🙂'):
        emit({'type': 'stream_event', 'event': {'delta': {'type': 'text_delta', 'text': text}}})
    emit({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '한글🙂'}]}})
    emit({'type': 'result', 'is_error': False, 'result': '한글🙂'})
