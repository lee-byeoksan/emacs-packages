import asyncio
import os
import json
from pathlib import Path
import tempfile
import shlex
import unittest
from aiohttp import ClientSession, CookieJar, web
import sys
HERE = Path(__file__).resolve().parents[1] / 'experiments/remote-web'


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix='eam-web-') as tmp:
            root = Path(tmp)
            binary = Path(os.environ.get('EAM_WEB_BINARY', str(HERE.parents[1] / 'native/target/debug/eam-runtime')))
            base='http://127.0.0.1:18765'
            runner=None
            server_process=None
            if os.environ.get('EAM_WEB_RUNTIME', 'rust') == 'rust':
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
                sys.path.insert(0, str(HERE))
                from server import create_app
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
                    async def remote_state():
                        async with c.get(base+'/api/sessions') as response:
                            entries=(await response.json())['sessions']
                        return next(entry for entry in entries if entry['session']==session_path).get('remote',False)

                    original_pid=await cli_pid()
                    if os.environ.get('EAM_WEB_BROWSER_TEST'):
                        async def browser(*args):
                            proc=await asyncio.create_subprocess_exec('npx','--yes','--cache','/private/tmp/eam-npm-cache',
                                'agent-browser','--session','eam-return-test',*args,
                                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                            out,err=await asyncio.wait_for(proc.communicate(),30)
                            self.assertEqual(proc.returncode,0,err.decode())
                            return out.decode()
                        async def click_current_input():
                            await browser('eval', """(() => {
                              term.scrollToBottom();
                              const r=document.querySelector('.xterm-screen').getBoundingClientRect();
                              const y=r.top+(term.buffer.active.cursorY+.5)*r.height/term.rows;
                              const screen=document.querySelector('.xterm-screen');
                              screen.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:r.left+20,clientY:y,pointerType:'mouse'}));
                              screen.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:r.left+20,clientY:y}));
                            })()""")
                        try:
                            await browser('open',base)
                            await browser('fill','#token','test-token')
                            await browser('click','#loginButton')
                            await browser('click','#sessions button')
                            await browser('wait','--fn','document.querySelector("#notice").textContent.includes("연결됨") && document.getElementById("connectionState").dataset.connected==="true"')
                            if os.environ.get('EAM_WEB_LAYOUT_TEST'):
                                await browser('set','viewport','390','844')
                                await browser('eval',"setTheme('dark');setEditMode('buffer')")
                                await browser('eval', """(() => {
                                  const saved=term, r=document.querySelector('.xterm-screen').getBoundingClientRect();
                                  const lines=['> submitted','old output','────────','❯ first line','second line','third line','────────','status','',''];
                                  const buffer={baseY:0,viewportY:0,cursorY:4,length:10,getLine:i=>i<0||i>=10?undefined:{isWrapped:false,translateToString:()=>lines[i]}};
                                  try {
                                    term={rows:10,buffer:{active:buffer}};
                                    const hit=row=>isCurrentInput({clientX:r.left+20,clientY:r.top+(row+.5)*r.height/10});
                                    if(!hit(3)||!hit(4)||!hit(5)||hit(0)||hit(1)||hit(7))throw Error('multiline editor bounds');
                                    lines[3]='ordinary output';
                                    if(hit(3)||hit(5)||!hit(4))throw Error('unrecognized box must use cursor line');
                                    buffer.viewportY=10;
                                    if(hit(4))throw Error('scrolled history accepted');
                                  } finally {term=saved;}
                                })()""")
                                await click_current_input()
                                await browser('wait','--fn','document.getElementById("composePanel").open')
                                await browser('click','#composeClose')
                                await browser('eval', """(() => {
                                  const el=document.querySelector('.xterm-screen'), r=el.getBoundingClientRect();
                                  for(const mode of ['buffer','live']) {
                                    setEditMode(mode);
                                    for(const type of ['mousedown','click'])el.dispatchEvent(new MouseEvent(type,{bubbles:true,clientX:r.left+20,clientY:r.top+2}));
                                    if(document.getElementById('composePanel').open || document.activeElement.matches('textarea'))throw Error('old output started input');
                                  }
                                  setEditMode('buffer');
                                })()""")
                                await click_current_input()
                                await browser('fill','#composer','burst')
                                await browser('click','#paste')
                                await browser('eval',"window.frameSamples=[];window.measureFrames=true;window.frameAt=performance.now();requestAnimationFrame(function sample(now){frameSamples.push(now-frameAt);frameAt=now;if(measureFrames)requestAnimationFrame(sample)})")
                                await browser('click','[data-key="enter"]')
                                await browser('wait','--fn','term.buffer.active.baseY > 1000')
                                await browser('wait','--fn',"Array.from({length:term.buffer.active.length},(_,i)=>term.buffer.active.getLine(i).translateToString()).join('\\n').includes('BURST-DONE')")
                                print('PERF burst:',await browser('eval',"measureFrames=false;JSON.stringify({...terminalMetrics,frameP95:frameSamples.sort((a,b)=>a-b)[Math.floor(frameSamples.length*.95)],maxFrame:Math.max(...frameSamples)})"))
                                await browser('click','#selectOutput')
                                await browser('eval', """(() => {
                                  const field=document.getElementById('outputText');
                                  if(!field.readOnly || !field.value.includes('BURST-DONE'))throw Error('output snapshot missing');
                                  const start=field.value.indexOf('한글');
                                  if(start<0)throw Error('unicode output missing');
                                  field.setSelectionRange(start,start+2);
                                  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedOutput=text}}});
                                })()""")
                                await browser('click','#copySelection')
                                await browser('wait','--fn',"window.copiedOutput==='한글'")
                                await browser('click','#outputClose')
                                await browser('wait','--fn','!document.getElementById("composePanel").open')
                                await browser('click','#pageUp')
                                await browser('wait','--fn','term.buffer.active.viewportY < term.buffer.active.baseY')
                                await browser('click','#latest')
                                await browser('wait','--fn','term.buffer.active.viewportY === term.buffer.active.baseY')
                                await browser('eval', """(() => {
                                  const el=document.getElementById('terminal');
                                  const touch=y=>new Touch({identifier:1,target:el,clientX:100,clientY:y});
                                  el.dispatchEvent(new TouchEvent('touchstart',{touches:[touch(200)],bubbles:true}));
                                  el.dispatchEvent(new TouchEvent('touchmove',{touches:[touch(400)],bubbles:true,cancelable:true}));
                                  el.dispatchEvent(new TouchEvent('touchend',{touches:[],bubbles:true}));
                                })()""")
                                await browser('wait','--fn','term.buffer.active.viewportY < term.buffer.active.baseY')
                                for width,height in [(390,844),(360,640),(844,390),(1280,800),(390,400)]:
                                    await browser('set','viewport',str(width),str(height))
                                    await browser('wait','--fn',f'Math.abs(document.body.clientHeight - {height}) < 2')
                                    await browser('eval', """(() => {
                                      const ids=['terminal','keys','detach','editMode','fullscreen'];
                                      for(const id of ids){
                                        const r=document.getElementById(id).getBoundingClientRect();
                                        if(r.top<0 || r.bottom>innerHeight+1 || r.left<0 || r.right>innerWidth+1 || r.height<1) throw Error(id+' outside viewport');
                                      }
                                      const keys=[...document.querySelectorAll('#keys button')];
                                      if(keys.map(el=>el.dataset.key).join(',')!=='escape,interrupt,eof,backtab,left,up,down,right,enter') throw Error('key order');
                                      for(let i=1;i<keys.length;i++) if(keys[i].getBoundingClientRect().top===keys[i-1].getBoundingClientRect().top && keys[i].getBoundingClientRect().left-keys[i-1].getBoundingClientRect().right<7) throw Error('key spacing');
                                      const headerStyle=getComputedStyle(document.querySelector('header'));
                                      if(parseFloat(headerStyle.paddingTop)<6 || parseFloat(headerStyle.paddingLeft)<8) throw Error('header padding');
                                      if(document.documentElement.scrollHeight>innerHeight+1) throw Error('page scrolls');
                                      const terminal=document.getElementById('terminal').getBoundingClientRect();
                                      const screen=document.querySelector('#terminal .xterm-screen').getBoundingClientRect();
                                      if(screen.bottom>terminal.bottom+.5) throw Error('last terminal row clipped');
                                      if(terminal.width-screen.width >= screen.width/term.cols+1) throw Error('scrollbar reserves text columns');
                                      if(screen.right>terminal.right+1) throw Error('terminal columns overflow');
                                      const scrollbar=document.querySelector('.xterm-scrollable-element > .scrollbar.vertical').getBoundingClientRect();
                                      if(scrollbar.width>8 || scrollbar.right>terminal.right+1 || scrollbar.left>=screen.right) throw Error('scrollbar is not overlay');
                                      if(document.querySelector('header').getBoundingClientRect().height>48) throw Error('header too tall');
                                    })()""")
                                    if (width,height) in [(390,844),(844,390),(1280,800)]:
                                        await browser('screenshot',str(HERE.parents[1]/f'var/validation/remote-ui-{width}x{height}.png'))
                                await browser('set','viewport','390','844')
                                await browser('click','#themeToggle')
                                await browser('wait','--fn','document.documentElement.dataset.theme==="light" && term.options.theme.background==="#f4f2e9"')
                                await browser('eval',"send({type:'input',data:'colors\\r'});term.scrollToBottom()")
                                await browser('wait','--fn',"Array.from({length:term.buffer.active.length},(_,i)=>term.buffer.active.getLine(i).translateToString()).join('\\n').includes('COLORS-DONE')")
                                await browser('eval', """(() => {
                                  for(const id of ['sessionName','editMode','composer','composeTitle','paste'])
                                    if(getComputedStyle(document.getElementById(id)).fontSize!=='13px') throw Error('inconsistent font '+id);
                                  if(term.options.fontSize!==13 || term.options.minimumContrastRatio!==7) throw Error('terminal typography/contrast');
                                })()""")
                                await browser('eval','term.scrollToBottom()')
                                await browser('wait','--fn',"document.querySelector('.xterm-rows').textContent.includes('COLORS-DONE')")
                                await browser('screenshot',str(HERE.parents[1]/'var/validation/remote-ui-light.png'))
                                await browser('eval',"if(localStorage.getItem('eam-theme')!=='light') throw Error('theme not saved')")
                                await browser('click','#themeToggle')
                                await browser('wait','--fn','document.documentElement.dataset.theme==="dark" && term.options.theme.background==="#111111"')
                                await browser('click','#fullscreen')
                                await browser('wait','--fn','!!document.fullscreenElement')
                                await browser('click','#fullscreen')
                                await browser('wait','--fn','!document.fullscreenElement')
                                await click_current_input()
                                await browser('fill','#composer','모드 전환 뒤에도 남는 초안')
                                await browser('click','#composeClose')
                                await browser('click','#editMode')
                                await browser('eval',"if(document.activeElement.matches('textarea,input') || document.getElementById('composePanel').open) throw Error('mode toggle started input')")
                                await browser('wait','--fn','document.getElementById("editMode").getAttribute("aria-checked")==="true"')
                                await browser('eval', "term.write('\\x1b[?25l',()=>updateLiveCursor())")
                                await browser('wait','--fn','!document.getElementById("liveCursor").hidden && document.getElementById("liveCursor").getBoundingClientRect().height>0')
                                await click_current_input()
                                await browser('eval',"if(document.getElementById('composePanel').open) throw Error('live mode opened modal')")
                                await browser('set','viewport','390','400')
                                await browser('wait','--fn','document.body.dataset.keyboard==="true"')
                                await browser('eval', """(() => {
                                  const cursor=document.getElementById('liveCursor').getBoundingClientRect();
                                  const terminal=document.getElementById('terminal').getBoundingClientRect();
                                  const keys=document.getElementById('keys').getBoundingClientRect();
                                  if(cursor.top<terminal.top || cursor.bottom>terminal.bottom+1 || keys.bottom>400)throw Error('keyboard obscures input');
                                  if(getComputedStyle(document.getElementById('fullscreen')).display!=='none')throw Error('keyboard header not compact');
                                })()""")
                                await browser('set','viewport','390','844')
                                await browser('wait','--fn','document.body.dataset.keyboard==="false"')
                                await browser('type','.xterm-helper-textarea','live-input-check')
                                await browser('press','Enter')
                                await browser('wait','--fn',"Array.from({length:term.buffer.active.length},(_,i)=>term.buffer.active.getLine(i).translateToString()).join('\\n').includes('REPLY: live-input-check')")
                                await browser('click','#editMode')
                                await browser('eval',"if(document.activeElement.matches('textarea,input') || document.getElementById('composePanel').open) throw Error('mode toggle started input')")
                                await click_current_input()
                                await browser('wait','--fn','document.getElementById("composePanel").open')
                                await browser('eval',"if(document.getElementById('composer').value!=='모드 전환 뒤에도 남는 초안') throw Error('mode change lost draft')")
                                await browser('fill','#composer','재연결 후에도 남는 초안')
                                await browser('set','viewport','390','400')
                                await browser('wait','--fn','Math.abs(document.body.clientHeight-400)<2')
                                await browser('eval', """(() => {
                                  const r=document.getElementById('composePanel').getBoundingClientRect();
                                  if(r.top<0 || r.bottom>innerHeight+1) throw Error('modal outside keyboard viewport');
                                })()""")
                                await browser('screenshot',str(HERE.parents[1]/'var/validation/remote-input-modal.png'))
                                await browser('click','#composeClose')
                                await browser('set','viewport','390','844')
                                await browser('eval','checkResumeConnection()')
                                await browser('wait','--fn','resumeProbe===null && document.getElementById("connectionState").dataset.connected==="true"')
                                await browser('eval',"window.savedSend=send;send=q=>{if(q.type!=='ping')window.savedSend(q)};checkResumeConnection()")
                                await browser('wait','--fn','!document.getElementById("reconnect").hidden && document.getElementById("connectionState").dataset.connected==="false"')
                                await browser('eval','send=window.savedSend')
                                await browser('wait','--fn','terminalReady && !reconnectTask')
                                await browser('wait','--fn','document.querySelector("#notice").textContent.includes("연결됨") && document.getElementById("connectionState").dataset.connected==="true"')
                                await browser('eval', """(() => {
                                  if(document.getElementById('composer').value!=='재연결 후에도 남는 초안') throw Error('draft lost');
                                  if(document.querySelectorAll('#terminal .xterm').length!==1) throw Error('duplicate terminal');
                                  if(!document.getElementById('reconnect').hidden) throw Error('reconnect did not reset');
                                })()""")
                                print('PERF reconnect:',await browser('eval','JSON.stringify(terminalMetrics)'))
                                await browser('eval',"Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));socket.close()")
                                await browser('wait','--fn','!terminalReady')
                                await browser('eval',"delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
                                await browser('wait','--fn','terminalReady && document.getElementById("connectionState").dataset.connected==="true"')
                                self.assertEqual(await cli_pid(),original_pid)
                                self.assertTrue(await remote_state())
                                print('PASS responsive viewport, touch scroll, tap input modal, fullscreen, reconnect preserves draft/PID')
                            await browser('click','#detach')
                            await browser('wait','--fn','document.querySelector("#status").textContent.includes("Emacs에 다시 연결했습니다")')
                            self.assertEqual(await cli_pid(),original_pid)
                            await browser('wait','--fn','document.getElementById("connectionState").dataset.connected==="false"')
                            await browser('eval',"checkResumeConnection();if(reconnectAllowed || retryTimer || reconnectTask)throw Error('explicit detach must not reconnect')")
                            self.assertFalse(await remote_state())
                            await browser('click','#sessionList')
                            await browser('wait','--fn','!document.getElementById("home").hidden')
                            print('PASS: browser button PC → web → PC, same CLI PID; connection light and list')
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
                    if os.environ.get('EAM_WEB_BROWSER_TEST') and os.environ.get('EAM_WEB_LAYOUT_TEST'):
                        try:
                            await browser('open',base)
                            await browser('fill','#token','test-token')
                            await browser('click','#loginButton')
                            await browser('click','#sessions button')
                            await browser('wait','--fn','terminalReady')
                            await browser('eval',"send({type:'input',data:'overflow-screen\\r'})")
                            await browser('wait','--fn',"document.querySelector('.xterm-rows').textContent.includes('FULL-SCREEN-RESTORED')")
                            # Forget the browser screen and reattach after the 5MiB replay cap.
                            await browser('eval','socket.close()')
                            await browser('wait','--fn',"terminalReady && document.getElementById('notice').textContent.includes('재생 한도') && document.querySelector('.xterm-rows').textContent.includes('FULL-SCREEN-RESTORED')")
                            self.assertEqual(await cli_pid(),original_pid)
                            print('PASS: replay overflow reconnect repaints the full screen without keyboard resize')
                        finally:
                            await browser('close')
                        await wait_return()
                    if server_process:
                        last_emacs_token=endpoint['attachment_token']
                        await take_again()
                        during_shutdown=await c.ws_connect(base+'/ws',params={'session':session_path})
                        await during_shutdown.receive_json(timeout=5)
                        if os.environ.get('EAM_WEB_SHUTDOWN') == 'http':
                            self.assertEqual((await c.post(base+'/api/shutdown')).status,200)
                        else:
                            server_process.terminate()
                        await asyncio.wait_for(server_process.wait(),15)
                        await wait_return()
                        await during_shutdown.close()
                        print('PASS: Rust server shutdown restores Emacs and preserves CLI')
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
