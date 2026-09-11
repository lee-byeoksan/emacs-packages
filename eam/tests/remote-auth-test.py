"""Identity guard checks; real Tailscale proxy is verified separately at deployment."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from aiohttp import ClientSession, WSServerHandshakeError

BINARY = Path(__file__).resolve().parents[1] / 'native/target/debug/eam-runtime'
class IdentityTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity(self):
        with tempfile.TemporaryDirectory(prefix='eam-identity-') as temp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port=sock.getsockname()[1]
            origin='https://eam.test'
            base=f'http://127.0.0.1:{port}'
            headers={'Host':'eam.test','Origin':origin}
            owner={**headers,'Tailscale-User-Login':'owner@example.test'}
            proc=await asyncio.create_subprocess_exec(str(BINARY),'serve','--port',str(port),
                '--origin',origin,'--tailscale-user','owner@example.test',
                '--sessions',temp+'/sessions','--browse-root',temp,
                '--demo-executable',str(BINARY.parent/'examples/remote_fixture'),
                env={**os.environ,'EAM_WEB_TOKEN':'old-token'},stdout=asyncio.subprocess.DEVNULL)
            try:
                async with ClientSession() as c:
                    for _ in range(100):
                        try:
                            r=await c.get(base+'/auth',headers=headers)
                            self.assertEqual(await r.json(),{'mode':'tailscale'})
                            break
                        except OSError: await asyncio.sleep(.03)
                    else: self.fail('Server failed to start')
                    for identity in ({}, {'Cookie':'eam_token=old-token'},
                                     {'Tailscale-User-Login':'other@example.test'},
                                     {'Tailscale-User-Login':'owner@example.test.evil'}):
                        denied={**headers,**identity}
                        self.assertEqual((await c.get(base+'/api/config',headers=denied)).status,403)
                        self.assertEqual((await c.post(base+'/api/sessions',headers=denied,json={})).status,403)
                        with self.assertRaises(WSServerHandshakeError) as caught:
                            await c.ws_connect(base+'/ws?session=no',headers=denied)
                        self.assertEqual(caught.exception.status,403)
                    duplicate=list(owner.items())+[('Tailscale-User-Login','other@example.test')]
                    self.assertEqual((await c.get(base+'/api/config',headers=duplicate)).status,403)
                    self.assertEqual((await c.post(base+'/login',headers=owner,json={'token':'old-token'})).status,403)
                    self.assertEqual((await c.get(base+'/api/config',headers={**owner,'Origin':'https://evil.test'})).status,403)
                    self.assertEqual((await c.post(base+'/api/sessions',headers={'Host':'eam.test','Tailscale-User-Login':'owner@example.test'},json={})).status,403)
                    config=await (await c.get(base+'/api/config',headers=owner)).json()
                    self.assertEqual(config['auth'],'tailscale')
                    self.assertEqual(config['tailscale_user'],'owner@example.test')
                    r=await c.post(base+'/api/sessions',headers=owner,json={'directory':temp,'provider':'Demo'})
                    self.assertEqual(r.status,200,await r.text())
                    session=(await r.json())['session']
                    try:
                        async with c.ws_connect(base+'/ws',params={'session':session},headers=owner) as ws:
                            for _ in range(30):
                                msg=await ws.receive(timeout=3)
                                if msg.type.name=='TEXT' and json.loads(msg.data).get('type')=='ready': break
                            else: self.fail('No ready event')
                            await ws.send_json({'type':'ping','at':123})
                            for _ in range(30):
                                msg=await ws.receive(timeout=3)
                                if msg.type.name=='TEXT' and json.loads(msg.data).get('type')=='pong': break
                            else: self.fail('No pong')
                    finally:
                        stop=await asyncio.create_subprocess_exec(str(BINARY),'manager','stop',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL)
                        await stop.communicate(json.dumps({'session':session}).encode())
                print('PASS exact identity, missing/wrong/duplicate identity, old-token rejection, Origin, authenticated WebSocket')
            finally:
                proc.terminate()
                await asyncio.wait_for(proc.wait(),10)
if __name__=='__main__': unittest.main()
