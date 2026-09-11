"""Real PTY attachment races/hangup; only an owned finite fake CLI is started."""
import json
import os
from pathlib import Path
import pty
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from manager import start,status,stop
from probe import wait

MANAGER=Path(__file__).resolve().parents[2]/'bridge/persistence/manager.py'
EDITOR=MANAGER.with_name('editor.py')

def run(root):
    root.mkdir(parents=True,exist_ok=False);root=root.resolve()
    session=root/'session';children={};fds={};meta=None
    def attach(number):
        pid,fd=pty.fork()
        if pid==0:
            os.environ['TERM']='xterm-256color'
            os.execv(sys.executable,[sys.executable,str(MANAGER),'attach',str(session),
                                    '/Applications/Emacs.app/Contents/MacOS/bin/emacsclient',f'/tmp/eai-endpoint-{number}'])
        children[pid]=None;fds[pid]=fd
        return pid
    def exited(pid):
        if children[pid] is None:
            found,code=os.waitpid(pid,os.WNOHANG)
            if found:children[pid]=os.waitstatus_to_exitcode(code)
        return children[pid] is not None
    try:
        meta=start(session,'fake',sys.executable,['-c','import time; print("READY",flush=True); time.sleep(40)'],root)
        wait(lambda:(session/'events.jsonl').exists())
        recorder=status(session)['recorder_pid']
        one,two=attach(1),attach(2)
        wait(lambda:exited(one) or exited(two))
        losers=[p for p in (one,two) if exited(p)]
        assert len(losers)==1,children
        assert children[losers[0]]==1,children
        winner=two if losers[0]==one else one
        wait(lambda:status(session)['attached_clients']==1)
        route=json.loads((session/'editor.json').read_text())
        expected='/tmp/eai-endpoint-2' if winner==two else '/tmp/eai-endpoint-1'
        assert route['socket']==expected
        # Losing the outer PTY is what a crashed owning Emacs causes.
        os.close(fds.pop(winner))
        wait(lambda:exited(winner))
        wait(lambda:status(session)['attached_clients']==0)
        assert status(session)['state']=='running'
        stale_exists=(session/'editor.json').exists()
        if stale_exists:
            p=subprocess.run([sys.executable,str(EDITOR),str(session/'editor.json'),'unused'],capture_output=True,timeout=5)
            assert p.returncode==1 and b'No active Emacs attachment' in p.stderr,p.stderr
        third=attach(3)
        wait(lambda:status(session)['attached_clients']==1)
        assert not exited(third)
        assert json.loads((session/'editor.json').read_text())['socket']=='/tmp/eai-endpoint-3'
        assert status(session)['recorder_pid']==recorder
        result=dict(exclusive_winner=True,loser_exit=1,outer_pty_hangup_releases_lease=True,
                    cli_survives=True,reattach_same_recorder=True,new_editor_endpoint=True,
                    stale_route_present=stale_exists,stale_route_rejected=stale_exists,
                    ai_calls=0,gui_test=False)
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:
        for fd in fds.values():os.close(fd)
        for pid in children:
            if not exited(pid):
                os.kill(pid,signal.SIGTERM)
                wait(lambda pid=pid:exited(pid))
        if meta:
            stop(session)
            shutil.rmtree(meta['runtime'])

if __name__=='__main__':run(Path(sys.argv[1]))
