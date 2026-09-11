import asyncio
import os
import json
from pathlib import Path
import tempfile
import shlex
import unittest
from aiohttp import ClientSession, CookieJar, web
from server import create_app, HERE


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix='eam-web-') as tmp:
            root = Path(tmp)
            binary = Path(os.environ.get('EAM_WEB_BINARY', str(HERE.parents[1] / 'native/target/debug/eam-runtime')))
            base='http://127.0.0.1:18765'
            runner=None
            server_process=None
            if os.environ.get('EAM_WEB_RUNTIME') == 'rust':
                server_process=await asyncio.create_subprocess_exec(str(binary),'serve','--port','18765',
                    '--sessions',str(root/'sessions'),'--browse-root',str(root),
                    '--demo-executable',str(binary.parent/'examples/remote_fixture'),
                    env={**os.environ,'EAM_WEB_TOKEN':'test-token'},
                    stdout=asyncio.subprocess.DEVNULL)
                async with ClientSession() as ready:
                    for _ in range(100):
                        try:
                            r=await ready.get(base+'/api/config')
                            if r.status==401: break
                        except OSError: pass
                        await asyncio.sleep(.05)
                    else: self.fail('Rust server did not start')
            else:
                app = create_app(binary, root / 'sessions', root, 'test-token', base, True)
                runner = web.AppRunner(app)
                await runner.setup()
                await web.TCPSite(runner, '127.0.0.1', 18765).start()
            session_path=None
            emacs=None
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True), headers={'Origin':base}) as c:
                    self.assertEqual((await c.get(base+'/api/sessions')).status,401)
                    self.assertEqual((await c.post(base+'/login',json={'token':'test-token'})).status,200)
                    self.assertEqual((await c.get(base+'/api/folders?path=/')).status,400)
                    self.assertEqual((await c.post(base+'/api/folders',json={'parent':tmp,'name':'../escape'})).status,400)
                    self.assertEqual((await c.get(base+'/api/sessions',headers={'Origin':'http://evil.test'})).status,403)
                    r=await c.post(base+'/api/folders',json={'parent':tmp,'name':'새 프로젝트'})
                    project=(await r.json())['path']
                    r=await c.post(base+'/api/sessions',json={'directory':project,'provider':'Demo'})
                    session_path=(await r.json())['session']
                    # Real isolated Emacs holds the native attach lease; remote takeover
                    # must release only that display and retain the same CLI PID.
                    socket_dir=root/'emacs'
                    socket_dir.mkdir(mode=0o700)
                    quote=lambda x: json.dumps(str(x),ensure_ascii=False)
                    attach_command='exec '+shlex.join([str(binary),'attach',session_path,
                        '/Applications/Emacs.app/Contents/MacOS/bin/emacsclient',
                        str(socket_dir/'test'),'takeover-test-token'])
                    expr=f"""(progn (require 'server)
                      (setq server-socket-dir {quote(socket_dir)} server-name "test")
                      (server-start)
                      (defun eam-attach (_directory)
                       (let ((buffer (get-buffer-create "eam-test-display")))
                        (with-current-buffer buffer
                          (setq-local eam-terminal-persistent-directory {quote(session_path)}))
                        (make-process :name "eam-test-attach" :buffer buffer :connection-type 'pipe
                          :command (list "/bin/sh" "-c"
                            (replace-regexp-in-string "takeover-test-token" (format "takeover-%s" (float-time)) {quote(attach_command)})))))
                      (eam-attach {quote(session_path)})
                      (while t (accept-process-output nil .05)))"""
                    emacs=await asyncio.create_subprocess_exec('/Applications/Emacs.app/Contents/MacOS/Emacs',
                        '--batch','-Q','--eval',expr,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    for _ in range(100):
                        endpoint=Path(session_path)/'editor.json'
                        if endpoint.exists() and json.loads(endpoint.read_text()).get('connected'):
                            break
                        await asyncio.sleep(.05)
                    else:
                        self.fail('Test Emacs did not attach')
                    async def cli_pid():
                        proc=await asyncio.create_subprocess_exec(str(binary),'manager','inspect',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
                        out,_=await proc.communicate(json.dumps({'session':session_path}).encode())
                        return json.loads(out)['status']['recorder_pid']
                    original_pid=await cli_pid()
                    if os.environ.get('EAM_WEB_BROWSER_TEST'):
                        async def browser(*args):
                            proc=await asyncio.create_subprocess_exec('npx','--yes','--cache','/private/tmp/eam-npm-cache',
                                'agent-browser','--session','eam-return-test',*args,
                                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                            out,err=await asyncio.wait_for(proc.communicate(),30)
                            self.assertEqual(proc.returncode,0,err.decode())
                            return out.decode()
                        try:
                            await browser('open',base)
                            await browser('fill','#token','test-token')
                            await browser('click','#loginButton')
                            await browser('click','#sessions button')
                            await browser('wait','--fn','document.querySelector("#notice").textContent.includes("연결됨")')
                            await browser('click','#detach')
                            await browser('wait','--fn','document.querySelector("#status").textContent.includes("Emacs에 다시 연결했습니다")')
                            self.assertEqual(await cli_pid(),original_pid)
                            print('PASS: browser button PC → web → PC, same CLI PID')
                        finally:
                            await browser('close')
                    last_emacs_token=json.loads((Path(session_path)/'editor.json').read_text())['attachment_token']
                    r=await c.post(base+'/api/takeover',json={'session':session_path})
                    self.assertEqual(r.status,200,await r.text())
                    self.assertIsNone(emacs.returncode)
                    self.assertEqual(await cli_pid(),original_pid)
                    self.assertFalse(json.loads((Path(session_path)/'session.json').read_text()).get('stopped',False))
                    async def wait_return():
                        nonlocal last_emacs_token
                        for _ in range(150):
                            try:
                                endpoint=json.loads((Path(session_path)/'editor.json').read_text())
                                if endpoint.get('connected') and endpoint['attachment_token'] != last_emacs_token:
                                    last_emacs_token=endpoint['attachment_token']
                                    self.assertEqual(await cli_pid(),original_pid)
                                    return
                            except (OSError, ValueError):
                                pass
                            await asyncio.sleep(.05)
                        self.fail('Automatic Emacs return did not complete')

                    async def take_again():
                        r=await c.post(base+'/api/takeover',json={'session':session_path})
                        self.assertEqual(r.status,200,await r.text())

                    connection_id=None
                    async def collect(ws, needle):
                        nonlocal connection_id
                        data=b''
                        while needle not in data:
                            msg=await ws.receive(timeout=10)
                            if isinstance(msg.data,bytes):
                                data+=msg.data
                                await ws.send_json({'type':'ack','bytes':len(msg.data)})
                            elif msg.type.name == 'TEXT':
                                control=json.loads(msg.data)
                                if control.get('type') == 'connection':
                                    connection_id=control['id']
                            elif msg.type.name in ('CLOSE','CLOSED','ERROR'):
                                self.fail(f'Unexpected close {msg}')
                        return data
                    async with c.ws_connect(base+'/ws',params={'session':session_path}) as ws:
                        await collect(ws,b'EAM remote test')
                        with self.assertRaises(Exception):
                            await c.ws_connect(base+'/ws',params={'session':session_path})
                        await ws.send_json({'type':'input','data':'안녕하세요\r'})
                        await collect(ws,'REPLY: 안녕하세요'.encode())
                        await ws.send_json({'type':'resize','rows':31,'cols':67})
                        await ws.send_json({'type':'input','data':'size\r'})
                        await collect(ws,b'columns=67, lines=31')
                        await ws.send_json({'type':'input','data':'approval\r'})
                        await collect(ws,b'Approve file edit?')
                        await ws.send_json({'type':'input','data':'y\r'})
                        await collect(ws,b'REPLY: y')
                        await ws.send_json({'type':'input','data':'burst\r'})
                        output=await collect(ws,b'BURST-DONE')
                        self.assertGreater(len(output),1000000)
                    await wait_return()
                    await take_again()
                    async with c.ws_connect(base+'/ws',params={'session':session_path}) as ws:
                        await collect(ws,b'BURST-DONE')
                        await ws.send_json({'type':'input','data':'after reconnect\r'})
                        await collect(ws,b'REPLY: after reconnect')
                    await wait_return()
                    await take_again()
                    # Abrupt TCP loss without a WebSocket close handshake.
                    lost=await c.ws_connect(base+'/ws',params={'session':session_path})
                    await collect(lost,b'BURST-DONE')
                    lost._response.connection.transport.abort()
                    await wait_return()
                    await lost.close()
                    await take_again()
                    # A deliberate browser-to-browser transfer must not restore Emacs
                    # between the old close and the new WebSocket connection.
                    async with c.ws_connect(base+'/ws',params={'session':session_path}) as old_browser:
                        await collect(old_browser,b'BURST-DONE')
                        await take_again()
                        async with c.ws_connect(base+'/ws',params={'session':session_path}) as new_browser:
                            await collect(new_browser,b'BURST-DONE')
                            current=json.loads((Path(session_path)/'editor.json').read_text())
                            self.assertEqual(current['attachment_token'],last_emacs_token)
                    await wait_return()
                    await take_again()
                    # Stop consuming/ACKing output. HTTP release must still free
                    # the Unix socket/lease and reconnect Emacs, without waiting
                    # for this browser to complete its WebSocket close handshake.
                    async with c.ws_connect(base+'/ws',params={'session':session_path}) as stalled:
                        control=await stalled.receive_json(timeout=5)
                        stalled_id=control['id']
                        await stalled.send_json({'type':'input','data':'burst\r'})
                        await asyncio.sleep(.2)
                        r=await asyncio.wait_for(c.post(base+'/api/detach',json={'session':session_path,'connection':stalled_id}),10)
                        result=await r.json()
                        self.assertEqual(r.status,200,result)
                        self.assertTrue(result['restored'])
                        self.assertEqual(await cli_pid(),original_pid)
                        endpoint=json.loads((Path(session_path)/'editor.json').read_text())
                        self.assertTrue(endpoint['connected'])
                    if server_process:
                        last_emacs_token=endpoint['attachment_token']
                        await take_again()
                        during_shutdown=await c.ws_connect(base+'/ws',params={'session':session_path})
                        await during_shutdown.receive_json(timeout=5)
                        server_process.terminate()
                        await asyncio.wait_for(server_process.wait(),15)
                        await wait_return()
                        await during_shutdown.close()
                        print('PASS: Rust server SIGTERM restores Emacs and preserves CLI')
                    print('PASS: live Emacs takeover without CLI restart, auth, origin, folder bounds/create, session start, exclusive lease, UTF-8, resize, approval, >1MB output, detach/reconnect, stalled browser release, automatic return on close and TCP loss')
            finally:
                if session_path:
                    p=await asyncio.create_subprocess_exec(str(binary),'manager','stop',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL)
                    await p.communicate(json.dumps({'session':session_path}).encode())
                if emacs:
                    emacs.terminate()
                    await emacs.wait()
                if runner:
                    await runner.cleanup()
                if server_process and server_process.returncode is None:
                    server_process.terminate()
                    await asyncio.wait_for(server_process.wait(),15)

if __name__=='__main__':
    unittest.main()
