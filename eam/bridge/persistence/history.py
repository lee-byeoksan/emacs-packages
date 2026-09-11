"""Read native CLI history without copying it to EAM or issuing AI requests.

Bounded JSON lines, SQLite rows and display pages. Schema mismatches fail visibly.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys

ROW_LIMIT = 8 * 1024 * 1024
LIST_LIMIT = 300


@contextmanager
def database(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        con.execute('PRAGMA query_only=ON')
        yield con
    finally:
        con.close()


def json_lines(path):
    with open(path, 'rb') as source:
        while True:
            line = source.readline(ROW_LIMIT + 1)
            if not line:
                break
            if len(line) > ROW_LIMIT:
                raise ValueError('Native history item exceeds 8 MiB; refusing an incomplete view')
            try:
                value = json.loads(line)
            except ValueError:
                # A writer may not have finished the last JSON line yet.
                if not line.endswith(b'\n'):
                    break
                raise ValueError('Unrecognized native JSONL record')
            if isinstance(value, dict):
                yield value


def text_parts(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(item['text'] for item in content
                     if isinstance(item, dict) and item.get('type') in ('text', 'input_text', 'output_text')
                     and isinstance(item.get('text'), str))


def messages(entry):
    if entry['provider'] == 'Codex' and entry.get('mode') == 'paginated':
        with database(entry['history_db']) as con:
            rows = con.execute('SELECT length(item_json), substr(item_json,1,?), item_type FROM thread_items '
                               'WHERE thread_id=? AND item_type IN (?,?) ORDER BY rollout_ordinal',
                               (ROW_LIMIT + 1, entry['id'], 'userMessage', 'agentMessage'))
            for length, raw, kind in rows:
                if length > ROW_LIMIT or len(raw.encode('utf-8')) > ROW_LIMIT:
                    raise ValueError('Native history item exceeds 8 MiB')
                item = json.loads(raw)
                text = item.get('text', '') if kind == 'agentMessage' else text_parts(item.get('content'))
                if text:
                    yield ('assistant' if kind == 'agentMessage' else 'user'), text
        return
    for item in json_lines(entry['path']):
        if entry['provider'] == 'Claude':
            if item.get('sessionId') not in (None, entry['id']):
                continue
            if item.get('isSidechain') or item.get('isMeta') or item.get('type') not in ('user', 'assistant'):
                continue
            message = item.get('message', {})
        else:
            if item.get('type') != 'response_item':
                continue
            message = item.get('payload', {})
            if message.get('type') != 'message':
                continue
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
            continue
        text = text_parts(message.get('content'))
        if text:
            yield message['role'], text


def catalog(provider, root, directory=None):
    root = Path(root).expanduser()
    entries = []
    if provider == 'Codex':
        # Require an identified schema version rather than silently reading an
        # unrelated DB after a CLI migration.
        with database(root / 'state_5.sqlite') as con:
            rows = con.execute('SELECT id,cwd,title,rollout_path,history_mode,updated_at FROM threads '
                               'WHERE (? IS NULL OR rtrim(cwd,\'/\')=rtrim(?,\'/\')) '
                               'ORDER BY updated_at DESC LIMIT ?', (directory, directory, LIST_LIMIT + 1))
            for identity, cwd, title, path, mode, updated in rows:
                if mode not in ('legacy', 'paginated'):
                    raise ValueError('Unsupported Codex history mode: ' + str(mode))
                entries.append(dict(provider=provider, id=identity, directory=cwd,
                                    title=(title or '')[:100], path=path, mode=mode, updated=updated,
                                    history_db=str(root / 'thread_history_1.sqlite')))
    elif provider == 'Claude':
        projects = root / 'projects'
        if not projects.is_dir():
            raise ValueError('Claude projects directory not found')
        # Iterate instead of materializing every path. Bound discovery explicitly.
        count = 0
        for project in projects.iterdir():
            if not project.is_dir():
                continue
            for path in project.glob('*.jsonl'):
                count += 1
                if count > 10000:
                    raise ValueError('More than 10000 Claude histories; narrow the configured root')
                cwd = title = None
                for index, item in enumerate(json_lines(path)):
                    cwd = cwd or item.get('cwd')
                    if item.get('type') == 'user' and not item.get('isMeta'):
                        title = text_parts(item.get('message', {}).get('content'))[:100]
                    if (cwd and title) or index >= 100:
                        break
                if directory and (not cwd or Path(cwd).resolve() != Path(directory).resolve()):
                    continue
                entries.append(dict(provider=provider, id=path.stem, directory=cwd or '', title=title or path.stem,
                                    path=str(path), updated=path.stat().st_mtime))
                entries.sort(key=lambda e: e['updated'], reverse=True)
                del entries[LIST_LIMIT+1:]
    else:
        raise ValueError('Unsupported provider')
    return dict(entries=entries[:LIST_LIMIT], truncated=len(entries) > LIST_LIMIT)


def page(entry, offset, limit, prompt=False):
    limit = max(128, min(int(limit), 65536))
    offset = None if offset is None else max(0, int(offset))
    total = 0
    shown = ''
    for role, text in messages(entry):
        if prompt:
            if role == 'user':
                start = 0 if offset is None else min(offset, len(text))
                shown = text[start:start+limit]
                total = len(text)
            continue
        block = ('\n## 사용자\n\n' if role == 'user' else '\n## 응답\n\n') + text + '\n'
        if offset is None:
            shown = (shown + block[-limit:])[-limit:]
        else:
            a = max(0, offset - total)
            b = min(len(block), offset + limit - total)
            if b > a:
                shown += block[a:b]
        total += len(block)
    start = (0 if offset is None else min(offset, total)) if prompt else max(0, total-len(shown)) if offset is None else min(offset, total)
    return dict(text=shown, start=start, end=start+len(shown), total=total,
                provider=entry['provider'], id=entry['id'], prompt=prompt)


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.buffer.read(65537))
        if request['action'] == 'list':
            result = catalog(request['provider'], request['root'], request.get('directory'))
        else:
            result = page(request['entry'], request.get('offset'), request.get('limit',65536), request.get('prompt',False))
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False))
        sys.exit(1)
