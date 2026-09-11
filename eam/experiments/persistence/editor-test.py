"""Two real batch Emacs servers, one persistent fake CLI and unchanged EDITOR."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from probe import wait

ROOT = Path(__file__).resolve().parents[2]
EMACS = '/Applications/Emacs.app/Contents/MacOS/Emacs'
CLIENT = '/Applications/Emacs.app/Contents/MacOS/bin/emacsclient'


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    root=root.resolve()
    jobs=[]
    with tempfile.TemporaryDirectory(prefix='eai-edit-',dir='/tmp') as private:
        route=Path(private)/'route.json'
        editor=Path(__file__).with_name('editor.py')
        env=dict(os.environ, EDITOR=shlex.join([sys.executable,str(editor),str(route)]))
        def launch_server(number):
            name=f's{number}'
            script=root/f'server-{number}.el'
            q=lambda v: json.dumps(str(v),ensure_ascii=False)
            script.write_text(f''';;; -*- lexical-binding: t; -*-
(require 'server)
(setq server-name {q(name)} server-socket-dir {q(private)})
(server-start)
(with-temp-file {q(root/f'server-{number}.ready')} (insert "ready"))
(while t
  (accept-process-output nil .05)
  (dolist (buffer (buffer-list))
    (with-current-buffer buffer
      (when (and server-buffer-clients buffer-file-name
                 (string-prefix-p {q(str(root)+'/')} buffer-file-name))
        (goto-char (point-max))
        (insert {q('편집-'+str(number))})
        (save-buffer)
        (server-edit)))))
''')
            log=open(root/f'server-{number}.log','wb')
            p=subprocess.Popen([EMACS,'--batch','-Q','-l',str(script)],stdout=log,stderr=log)
            log.close();jobs.append(p)
            wait(lambda: (root/f'server-{number}.ready').exists() or p.poll() is not None)
            assert p.poll() is None, (root/f'server-{number}.log').read_text()
            target=route.with_suffix('.new')
            target.write_text(json.dumps(dict(version=1,client=CLIENT,socket=str(Path(private)/name))))
            target.chmod(0o600); target.replace(route)
            return p
        fixture=root/'fake-cli.py'
        fixture.write_text('''import os,shlex,subprocess,sys,time
from pathlib import Path
p=Path(sys.argv[1]); (p/'cli.pid').write_text(str(os.getpid()))
for n in (1,2):
 deadline=time.monotonic()+25
 while not (p/f'go-{n}').exists():
  if time.monotonic()>deadline: raise SystemExit(1)
  time.sleep(.02)
 file=p/f'한글 입력 {n}.txt'; file.write_text('초안:')
 code=subprocess.run(shlex.split(os.environ['EDITOR'])+[str(file)]).returncode
 (p/f'done-{n}').write_text(str(code))
''')
        try:
            first=launch_server(1)
            log=open(root/'cli.log','wb')
            cli=subprocess.Popen([sys.executable,str(fixture),str(root)],env=env,stdout=log,stderr=log)
            log.close();jobs.append(cli)
            wait(lambda: (root/'cli.pid').exists())
            pid=int((root/'cli.pid').read_text())
            (root/'go-1').touch()
            wait(lambda: (root/'done-1').exists())
            assert (root/'done-1').read_text()=='0'
            assert (root/'한글 입력 1.txt').read_text()=='초안:편집-1\n'
            first.terminate(); first.wait(timeout=5)
            stale=subprocess.run([sys.executable,str(editor),str(route),str(root/'한글 입력 1.txt')],
                                 capture_output=True,timeout=5)
            assert stale.returncode!=0
            launch_server(2)
            assert cli.poll() is None and int((root/'cli.pid').read_text())==pid
            (root/'go-2').touch()
            wait(lambda: (root/'done-2').exists())
            assert (root/'done-2').read_text()=='0'
            assert (root/'한글 입력 2.txt').read_text()=='초안:편집-2\n'
            assert cli.wait(timeout=5)==0
            # Bad route must fail before invoking any alternate editor.
            route.chmod(0o644)
            bad=subprocess.run([sys.executable,str(editor),str(route),'unused'],capture_output=True,timeout=5)
            assert bad.returncode==1 and b'private regular file' in bad.stderr
            result=dict(emacs_servers=2,same_cli_pid=True,unchanged_editor_command=True,
                        korean_paths=True,stale_socket_fails=True,insecure_route_rejected=True,
                        gui_launched=False,ai_calls=0)
            (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result))
        finally:
            for p in reversed(jobs):
                if p.poll() is None: p.terminate()
                p.wait(timeout=5)

if __name__=='__main__': run(Path(sys.argv[1]))
