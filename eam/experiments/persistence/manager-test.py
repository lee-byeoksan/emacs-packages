import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from manager import start,status,stop,read,save
from probe import wait

class ManagerTests(unittest.TestCase):
    def test_isolation_status_no_start_stop_and_owner_mismatch(self):
        with tempfile.TemporaryDirectory(prefix='eai-manager-') as temp:
            root=Path(temp);sessions=[root/'세션 1',root/'세션 2'];metadata=[]
            try:
                for session in sessions:
                    metadata.append(start(session,'fake',sys.executable,['-c','import time; print("ready",flush=True); time.sleep(30)'],root))
                wait(lambda:all((s/'output.ansi').exists() for s in sessions))
                states=[status(s) for s in sessions]
                self.assertTrue(all(s['state']=='running' for s in states))
                self.assertNotEqual(states[0]['recorder_pid'],states[1]['recorder_pid'])
                self.assertEqual(stop(sessions[0])['state'],'stopped')
                self.assertEqual(status(sessions[1])['state'],'running')
                self.assertEqual(status(sessions[0])['state'],'stopped')
                original=read(sessions[1]);corrupt=dict(original,id='0'*32)
                save(sessions[1]/'session.json',corrupt)
                with self.assertRaises(ValueError):stop(sessions[1])
                save(sessions[1]/'session.json',original)
                self.assertEqual(status(sessions[1])['state'],'running')
                self.assertEqual(stop(sessions[1])['state'],'stopped')
                self.assertTrue(all((s/'output.ansi').exists() for s in sessions))
            finally:
                for session,meta in zip(sessions,metadata):
                    save(session/'session.json',meta)
                    stop(session)
                    shutil.rmtree(meta['runtime'])
    def test_duplicate_and_invalid_executable_do_not_replace(self):
        with tempfile.TemporaryDirectory(prefix='eai-manager-') as temp:
            session=Path(temp)/'s';session.mkdir(mode=0o700)
            (session/'keep').write_text('keep')
            with self.assertRaises(FileExistsError):start(session,'fake',sys.executable,[],temp)
            self.assertEqual((session/'keep').read_text(),'keep')
            with self.assertRaises(ValueError):start(Path(temp)/'new','fake','relative',[],temp)
            self.assertFalse((Path(temp)/'new').exists())

if __name__=='__main__':unittest.main()
