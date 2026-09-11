#!/usr/bin/env python3
"""Personal Emacs UI adapters for unmodified, locally installed official CLIs.

No API client, token extraction, prompt wrapper, extra model call, or SDK.
One invocation = one user turn. Stdout is display text, protocol is on disk.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import tomllib

MAX_FRAME = 4 * 1024 * 1024
MAX_PROMPT = 1024 * 1024


class ProviderError(Exception):
    pass


def save_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    with open(temp, 'w', encoding='utf-8') as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    temp.replace(path)


def subscription_env(provider):
    """Exclude alternate billing overrides only in the child environment."""
    env = os.environ.copy()
    names = ['OPENAI_API_KEY', 'CODEX_API_KEY'] if provider == 'codex' else [
        'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
        'ANTHROPIC_PROFILE', 'ANTHROPIC_FEDERATION_RULE_ID',
        'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY']
    for name in names:
        env.pop(name, None)
    if provider == 'claude':
        # Set before CLI module initialization, not only during flag parsing.
        env['CLAUDE_CODE_SAFE_MODE'] = '1'
    return env


def codex_command(executable, cwd):
    # Overrides are per process. No writes to the user's Codex configuration.
    config = {
        'features.hooks': False, 'features.plugins': False,
        'features.apps': False, 'features.multi_agent': False,
        'features.memories': False, 'features.skill_search': False,
        'features.skip_host_skill_discovery': True,
        'project_doc_max_bytes': 0, 'developer_instructions': '',
        'web_search': 'disabled', 'analytics.enabled': False,
    }
    # Disable configured MCP servers without echoing their settings/secrets.
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    paths = [home / 'config.toml']
    paths += [p / '.codex/config.toml' for p in [Path(cwd), *Path(cwd).parents]]
    for path in paths:
        if path.is_file():
            with open(path, 'rb') as stream:
                data = tomllib.load(stream)
            for name in data.get('mcp_servers', {}):
                if not name.replace('_', '').replace('-', '').isalnum():
                    raise ProviderError('Unsupported MCP server name in configuration')
                config[f'mcp_servers.{name}.enabled'] = False
    args = [executable, 'app-server']
    for key, value in config.items():
        args += ['-c', f'{key}={json.dumps(value)}']
    return args


def claude_command(executable, backend_id):
    args = [executable, '--safe-mode', '--disable-slash-commands',
            '--setting-sources', '', '--strict-mcp-config', '-p', '--output-format', 'stream-json',
            '--verbose', '--include-partial-messages', '--tools', 'Read,Grep,Glob',
            '--permission-mode', 'dontAsk', '--permission-prompts', 'none',
            '--prompt-suggestions', 'false']
    if backend_id:
        args += ['--resume', backend_id]
    return args


class Wire:
    """Read byte chunks, archive first, then decode bounded JSONL frames."""
    def __init__(self, process, log):
        self.process = process
        self.log = log
        self.pending = bytearray()
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def next(self, timeout=300):
        deadline = time.monotonic() + timeout
        while True:
            end = self.pending.find(b'\n')
            if end >= 0:
                if end > MAX_FRAME:
                    raise ProviderError('Protocol frame exceeds 4 MiB; received bytes are archived')
                line = bytes(self.pending[:end])
                del self.pending[:end + 1]
                if not line.strip():
                    continue
                return json.loads(line)
            if len(self.pending) > MAX_FRAME:
                raise ProviderError('Protocol frame exceeds 4 MiB; received bytes are archived')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise ProviderError('Provider idle timeout; no automatic retry')
            chunk = os.read(self.process.stdout.fileno(), 8192)
            if not chunk:
                if self.pending.strip():
                    raise ProviderError('Incomplete JSON frame at provider EOF')
                raise EOFError('Provider exited before a completion event')
            self.log.write(chunk)
            self.log.flush()
            self.pending.extend(chunk)

    def close(self):
        self.selector.close()


def display(text):
    sys.stdout.write(text)
    sys.stdout.flush()


class Runner:
    def __init__(self, provider, executable, cwd, state_path, prefix):
        self.provider, self.executable = provider, executable
        self.cwd = str(Path(cwd).resolve())
        self.state_path, self.prefix = Path(state_path), str(prefix)
        self.state = {'provider': provider, 'cwd': self.cwd, 'backend_id': None}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state['provider'] != provider or self.state['cwd'] != self.cwd:
                raise ProviderError('Saved provider/cwd mismatch; create a new session')
        self.process = None
        self.wire = None
        self.stopped = False
        self.serial = 0
        self.seen_delta = False
        self.item_id = None
        self.item_had_delta = False
        self.completed_turn = None

    def save(self):
        save_json(self.state_path, self.state)

    def cancel(self, *_):
        self.stopped = True
        self.state['interrupted_backend_id'] = self.state.get('backend_id')
        self.state['backend_id'] = None
        self.state['status'] = 'cancelled'
        self.save()
        self.shutdown()
        raise ProviderError('Cancelled; next send starts a new remote conversation')

    def shutdown(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
            except ProcessLookupError:
                pass

    def start(self, command, stderr, raw):
        self.process = subprocess.Popen(command, cwd=self.cwd,
                                        env=subscription_env(self.provider),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=stderr, start_new_session=True)
        self.wire = Wire(self.process, raw)

    def send(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            message = self.wire.next(timeout=60)
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise ProviderError(str(message['error']))
                return message['result']
            self.codex_event(message)

    def codex_event(self, message):
        method, params = message.get('method', ''), message.get('params', {})
        if 'id' in message and method:
            # This first adapter never elevates permissions or calls client tools.
            if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                self.send({'id': message['id'], 'result': {'decision': 'decline'}})
                display('\n[Permission request declined: read-only connection]\n')
            elif method == 'item/tool/requestUserInput':
                self.send({'id': message['id'], 'result': {'answers': {}}})
                display('\n[Provider requested interactive input; unsupported in this connection]\n')
            else:
                self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported client request'}})
            return
        if method == 'item/agentMessage/delta':
            if self.item_id != params.get('itemId'):
                self.item_id = params.get('itemId')
                self.item_had_delta = False
            self.item_had_delta = True
            display(params.get('delta', ''))
        elif method == 'item/completed':
            item = params.get('item', {})
            if item.get('type') == 'agentMessage':
                if item.get('id') != self.item_id or not self.item_had_delta:
                    display(item.get('text', ''))
                display('\n')
                self.item_id, self.item_had_delta = None, False
            elif item.get('type') == 'commandExecution':
                display('\n[command]\n' + item.get('aggregatedOutput', '') + '\n')
        elif method == 'error':
            display('\n[provider error] ' + str(params.get('error', {}).get('message', 'unknown')) + '\n')
        elif method == 'turn/completed':
            self.completed_turn = params['turn']

    def codex(self, prompt, stderr, raw):
        self.start(codex_command(self.executable, self.cwd), stderr, raw)
        self.rpc('initialize', {'clientInfo': {'name': 'emacs_ai', 'version': '0.2.0'}})
        self.send({'method': 'initialized', 'params': {}})
        account = self.rpc('account/read', {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt':
            raise ProviderError('ChatGPT login required; run codex login in your terminal')
        self.state['auth'] = {'type': account['type'], 'plan': account.get('planType')}
        params = {'cwd': self.cwd, 'modelProvider': 'openai',
                  'approvalPolicy': 'never', 'sandbox': 'read-only'}
        method = 'thread/start'
        if self.state.get('backend_id'):
            method = 'thread/resume'
            params.update(threadId=self.state['backend_id'], excludeTurns=True)
        thread = self.rpc(method, params)
        self.state.update(backend_id=thread['thread']['id'],
                          instruction_sources=thread.get('instructionSources', []), status='running')
        self.save()
        self.rpc('turn/start', {'threadId': self.state['backend_id'],
                                'input': [{'type': 'text', 'text': prompt}]})
        while self.completed_turn is None:
            self.codex_event(self.wire.next())
        if self.completed_turn['status'] != 'completed':
            raise ProviderError(str(self.completed_turn.get('error') or self.completed_turn['status']))

    def claude(self, prompt, stderr, raw):
        auth = subprocess.run([self.executable, '--safe-mode', 'auth', 'status', '--json'],
                              cwd=self.cwd, env=subscription_env('claude'),
                              capture_output=True, timeout=20, text=True)
        account = json.loads(auth.stdout)
        if not account.get('loggedIn') or account.get('authMethod') != 'claude.ai':
            raise ProviderError('Claude.ai login required; sign in with the official claude CLI')
        self.state['auth'] = {'type': 'claude.ai', 'plan': account.get('subscriptionType')}
        self.start(claude_command(self.executable, self.state.get('backend_id')), stderr, raw)
        self.process.stdin.write(prompt.encode('utf-8'))
        self.process.stdin.close()
        while True:
            event = self.wire.next()
            typ = event.get('type')
            if typ == 'system' and event.get('subtype') == 'init':
                self.state.update(backend_id=event.get('session_id'), status='running')
                self.save()
            elif typ == 'stream_event':
                inner = event.get('event', {})
                if inner.get('type') == 'message_start':
                    self.seen_delta = False
                delta = inner.get('delta', {})
                if delta.get('type') == 'text_delta':
                    self.seen_delta = True
                    display(delta.get('text', ''))
            elif typ == 'assistant':
                if not self.seen_delta:
                    for block in event.get('message', {}).get('content', []):
                        if block.get('type') == 'text':
                            display(block.get('text', ''))
                display('\n')
            elif typ == 'result':
                if event.get('is_error'):
                    raise ProviderError(str(event.get('errors') or event.get('result') or event.get('subtype')))
                if event.get('permission_denials'):
                    display('\n[Some tool operations were denied by the read-only connection]\n')
                break
            elif typ == 'system' and event.get('subtype') in ('api_retry', 'permission_denied'):
                display('\n[Claude ' + event['subtype'] + ']\n')

    def run(self, prompt):
        self.state['status'] = 'starting'
        self.save()
        try:
            with open(self.prefix + '.protocol.jsonl', 'ab', buffering=0) as raw, \
                    open(self.prefix + '.stderr.log', 'ab', buffering=0) as stderr:
                getattr(self, self.provider)(prompt, stderr, raw)
            self.state['status'] = 'completed'
            self.save()
        except Exception:
            if not self.stopped:
                # Never silently resume an incomplete turn after failure.
                self.state['failed_backend_id'] = self.state.get('backend_id')
                self.state.update(status='failed', backend_id=None)
                self.save()
            raise
        finally:
            self.shutdown()
            if self.wire:
                self.wire.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['claude', 'codex'], required=True)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--prefix', required=True)
    args = parser.parse_args()
    try:
        prompt = sys.stdin.buffer.read(MAX_PROMPT + 1)
        if not prompt or len(prompt) > MAX_PROMPT:
            raise ProviderError('Prompt must contain 1 byte to 1 MiB of UTF-8 text')
        runner = Runner(args.provider, args.executable, args.cwd, args.state, args.prefix)
        signal.signal(signal.SIGTERM, runner.cancel)
        signal.signal(signal.SIGINT, runner.cancel)
        signal.signal(signal.SIGHUP, runner.cancel)
        runner.run(prompt.decode('utf-8'))
        return 0
    except Exception as error:
        display('\n[connection stopped] ' + str(error) + '\n')
        display('[diagnostics] ' + args.prefix + '.stderr.log\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
