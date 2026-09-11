"""Experimental loopback-only EAM gateway. Expose privately using Tailscale Serve."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import struct
import uuid
from aiohttp import web, WSMsgType
from takeover import release_emacs, restore_emacs

HERE = Path(__file__).resolve().parent


def create_app(runtime, root, browse_root, token, origin, demo=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    browse_root = Path(browse_root).resolve()

    async def manager(action, **query):
        proc = await asyncio.create_subprocess_exec(str(runtime), 'manager', action,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(query).encode()), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ValueError('Runtime timeout')
        value = json.loads(out)
        if proc.returncode:
            raise ValueError(value.get('error', 'Runtime failed'))
        return value

    def directory(value):
        p = Path(value).resolve(strict=True)
        if not p.is_relative_to(browse_root) or not p.is_dir():
            raise ValueError('탐색 범위 밖의 디렉터리입니다.')
        return p

    def session(value):
        p = Path(value).resolve(strict=True)
        if p.parent != root:
            raise ValueError('Unknown session')
        return p

    @web.middleware
    async def guard(request, handler):
        try:
            # Fixed public origin also prevents DNS rebinding / cross-site socket access.
            if request.host != origin.split('://', 1)[1]:
                raise web.HTTPForbidden()
            if request.path.startswith('/api/') or request.path == '/ws':
                if request.headers.get('Origin') not in (None, origin):
                    raise web.HTTPForbidden()
                if request.method != 'GET' and request.headers.get('Origin') != origin:
                    raise web.HTTPForbidden()
                if request.cookies.get('eam_token') != token:
                    raise web.HTTPUnauthorized()
            response = await handler(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
            return response
        except (ValueError, OSError, json.JSONDecodeError) as e:
            return web.json_response({'error': str(e)}, status=400)

    app = web.Application(middlewares=[guard], client_max_size=32768)

    async def login(request):
        if request.headers.get('Origin') != origin:
            raise web.HTTPForbidden()
        data = await request.json()
        if not secrets.compare_digest(str(data.get('token', '')), token):
            raise web.HTTPUnauthorized()
        response = web.json_response({'ok': True})
        response.set_cookie('eam_token', token, httponly=True, secure=origin.startswith('https:'), samesite='Strict')
        return response

    async def sessions(request):
        return web.json_response(await manager('list-live', root=str(root)))

    async def folders(request):
        p = directory(request.query.get('path', str(browse_root)))
        children = []
        for entry in sorted(p.iterdir(), key=lambda x: x.name.casefold()):
            if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith('.'):
                children.append({'name': entry.name, 'path': str(entry)})
        return web.json_response({'path': str(p), 'parent': str(p.parent) if p != browse_root else None, 'children': children})

    async def mkdir(request):
        q = await request.json()
        p = directory(q['parent'])
        name = q['name']
        if not isinstance(name, str) or not name.strip() or name in ('.', '..') or '/' in name or '\0' in name:
            raise ValueError('폴더 이름을 확인하세요.')
        (p / name).mkdir(mode=0o700)
        return web.json_response({'path': str(p / name)})

    async def start(request):
        q = await request.json()
        provider = q['provider']
        commands = {'Claude': 'claude', 'Codex': 'codex'}
        if demo:
            commands['Demo'] = str(HERE / 'fixture.py')
        if provider not in commands:
            raise ValueError('Unknown provider')
        executable = shutil.which(commands[provider])
        if not executable:
            raise ValueError('PC에서 CLI 실행 파일을 찾지 못했습니다.')
        p = root / ('web-' + uuid.uuid4().hex)
        await manager('start', session=str(p), directory=str(directory(q['directory'])),
                      provider=provider, executable=executable, args=[], temporary=False)
        return web.json_response({'session': str(p)})

    active = {}
    return_routes = {}
    takeover_lock = asyncio.Lock()
    return_tasks = set()

    async def return_to_emacs(p):
        # Caller holds takeover_lock. A stopped CLI must not be restarted.
        for _ in range(60):
            info = await manager('inspect', session=str(p))
            if info['status'].get('state') != 'running':
                return_routes.pop(p, None)
                return False
            if info['status'].get('attached_clients') == 0:
                break
            await asyncio.sleep(.05)
        else:
            raise ValueError('연결 해제 확인이 지연됩니다. 다시 시도하세요.')
        if p not in return_routes:
            return False
        await restore_emacs(p, return_routes[p])
        return_routes.pop(p, None)
        return True

    async def auto_return(p, route):
        async with takeover_lock:
            if p in active or return_routes.get(p) is not route:
                return
            try:
                await return_to_emacs(p)
            except (ValueError, OSError):
                logging.exception('Automatic Emacs return failed for %s', p)

    def schedule_return(p):
        if p in return_routes:
            task = asyncio.create_task(auto_return(p, return_routes[p]))
            return_tasks.add(task)
            task.add_done_callback(return_tasks.discard)

    async def shutdown(app):
        for bridge in list(active.values()):
            bridge['return_on_close'] = False
            bridge['release']()
        active.clear()
        for p in list(return_routes):
            schedule_return(p)
        if return_tasks:
            await asyncio.gather(*list(return_tasks), return_exceptions=True)

    app.on_shutdown.append(shutdown)

    async def takeover(request):
        q = await request.json()
        p = session(q['session'])
        async with takeover_lock:
            info = await manager('inspect', session=str(p))
            if info['metadata'].get('temporary'):
                raise ValueError('Quick 세션은 전환 시 종료될 수 있어 지원하지 않습니다.')
            if p in active:
                active[p]['return_on_close'] = False
                active[p]['release']()
                await close_websocket(active[p]['ws'])
            elif info['status'].get('attached_clients'):
                return_routes[p] = await release_emacs(p)
            for _ in range(50):
                info = await manager('inspect', session=str(p))
                if info['status'].get('attached_clients') == 0:
                    return web.json_response({'session': str(p)})
                await asyncio.sleep(.05)
            raise ValueError('연결 해제를 기다리는 중입니다. 다시 시도하세요.')

    async def detach(request):
        q = await request.json()
        p = session(q['session'])
        async with takeover_lock:
            bridge = active.get(p)
            if bridge:
                if q.get('connection') != bridge['id']:
                    raise ValueError('다른 화면으로 조작권이 이동했습니다. 목록을 새로고침하세요.')
                # Release Unix socket and flock before waiting on the browser.
                bridge['return_on_close'] = False
                bridge['release']()
            restored = await return_to_emacs(p)
            return web.json_response({'detached': True, 'restored': restored})

    async def close_websocket(ws):
        with contextlib.suppress(TimeoutError, OSError):
            await asyncio.wait_for(ws.close(), 1)

    async def ws_handler(request):
        p = session(request.query['session'])
        # Same flock as Emacs attach: no stealing a live attachment.
        lease = open(p / 'attach.lock', 'a')
        writer = None
        ws = None
        task = None
        released = False
        bridge = None
        setup_locked = False
        def release():
            nonlocal released
            if released:
                return
            released = True
            if writer:
                writer.transport.abort()
            lease.close()
            if task:
                task.cancel()
            if ws:
                asyncio.create_task(close_websocket(ws))

        try:
            await takeover_lock.acquire()
            setup_locked = True
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('다른 화면이 조작 중입니다. 목록에서 조작권 가져오기를 선택하세요.')
            info = await manager('inspect', session=str(p))
            if info['status']['state'] != 'running' or info['status']['attached_clients']:
                raise ValueError('연결할 수 없는 세션입니다.')
            if info['metadata'].get('temporary'):
                raise ValueError('PC에서 eam-persist로 지속 세션으로 전환하세요.')
            reader, writer = await asyncio.open_unix_connection(str(Path(info['metadata']['runtime']) / 'socket'))
            ws = web.WebSocketResponse(heartbeat=15, max_msg_size=32768)
            await ws.prepare(request)
            connection_id = secrets.token_urlsafe(16)
            bridge = {'ws': ws, 'release': release, 'id': connection_id, 'return_on_close': True}
            active[p] = bridge
            takeover_lock.release()
            setup_locked = False
            await ws.send_json({'type': 'connection', 'id': connection_id})
            pending = 0
            acknowledged = asyncio.Event()
            acknowledged.set()

            async def output():
                nonlocal pending
                try:
                    while True:
                        while pending >= 131072:
                            acknowledged.clear()
                            await asyncio.wait_for(acknowledged.wait(), 30)
                        header = await reader.readexactly(5)
                        kind, length = header[0], int.from_bytes(header[1:], 'big')
                        if length > 262144:
                            raise ValueError('Invalid daemon packet')
                        payload = await reader.readexactly(length)
                        if kind == ord('O'):
                            pending += length
                            await ws.send_bytes(payload)
                        elif kind == ord('H'):
                            await ws.send_json({'type': 'ready', **json.loads(payload)})
                except (asyncio.IncompleteReadError, TimeoutError, OSError):
                    await ws.close()

            task = asyncio.create_task(output())
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    break
                q = json.loads(msg.data)
                kind = q.get('type')
                if kind == 'ack':
                    n = q['bytes']
                    if not isinstance(n, int) or not 0 < n <= pending:
                        break
                    pending -= n
                    acknowledged.set()
                    continue
                if kind == 'ping':
                    await ws.send_json({'type': 'pong', 'at': q['at']})
                    continue
                if kind == 'input':
                    payload = q['data'].encode('utf-8')
                    if len(payload) > 16384:
                        break
                    tag = b'I'
                elif kind == 'resize':
                    rows, cols = q['rows'], q['cols']
                    if not all(isinstance(n, int) and 1 <= n <= 1000 for n in (rows, cols)):
                        break
                    payload, tag = struct.pack('=HHHH', rows, cols, 0, 0), b'R'
                else:
                    break
                writer.write(tag + len(payload).to_bytes(4, 'big') + payload)
                await writer.drain()
            return ws
        finally:
            # Never wait on a WebSocket close handshake while holding the PTY lease.
            release()
            if setup_locked:
                takeover_lock.release()
            if active.get(p, {}).get('ws') is ws:
                active.pop(p, None)
                if bridge and bridge['return_on_close']:
                    schedule_return(p)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError, OSError):
                    await asyncio.wait_for(task, 1)
            if ws:
                with contextlib.suppress(TimeoutError, OSError):
                    await asyncio.wait_for(ws.close(), 1)

    async def config(request):
        return web.json_response({'demo': demo})

    app.router.add_post('/login', login)
    app.router.add_get('/api/config', config)
    app.router.add_get('/api/sessions', sessions)
    app.router.add_post('/api/sessions', start)
    app.router.add_get('/api/folders', folders)
    app.router.add_post('/api/folders', mkdir)
    app.router.add_post('/api/takeover', takeover)
    app.router.add_post('/api/detach', detach)
    app.router.add_get('/ws', ws_handler)
    assets = {'/': HERE / 'index.html', '/app.js': HERE / 'app.js', '/style.css': HERE / 'style.css',
              '/xterm.js': HERE / 'node_modules/@xterm/xterm/lib/xterm.js',
              '/xterm.css': HERE / 'node_modules/@xterm/xterm/css/xterm.css',
              '/fit.js': HERE / 'node_modules/@xterm/addon-fit/lib/addon-fit.js'}
    for url, path in assets.items():
        async def asset(request, path=path):
            return web.FileResponse(path)
        app.router.add_get(url, asset)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default=str(HERE.parents[1] / 'native/target/debug/eam-runtime'))
    parser.add_argument('--sessions', default=str(Path.home() / '.eam/persistent'))
    parser.add_argument('--browse-root', default=str(Path.home()))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--origin', help='Exact browser origin, e.g. https://mac.example.ts.net')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('EAM_WEB_TOKEN') or secrets.token_urlsafe(32)
    origin = args.origin or f'http://127.0.0.1:{args.port}'
    print(f'Open {origin}\nAccess token: {token}', flush=True)
    web.run_app(create_app(args.runtime, args.sessions, args.browse_root, token, origin, args.demo),
                host='127.0.0.1', port=args.port, access_log=None)
