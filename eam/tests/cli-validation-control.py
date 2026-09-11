#!/usr/bin/env python3
"""Drive the batch-only Ghostel validation harness, never a user's GUI instance."""
import argparse,json,subprocess,time,math
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('directory',type=Path)
p.add_argument('action',choices=['paste','key','snapshot','editor-read','editor-write','wait-exit','exit'])
p.add_argument('value',nargs='?')
p.add_argument('--mods')
p.add_argument('--timeout',type=float,default=10)
a=p.parse_args()
if not math.isfinite(a.timeout) or a.timeout<=0:p.error('--timeout must be finite and positive')
if a.action in ('paste','key','editor-read','editor-write') and a.value is None:p.error('this action requires a value')
deadline=time.monotonic()+a.timeout
while True:
 try:state=json.loads((a.directory/'state.json').read_text())
 except (FileNotFoundError,json.JSONDecodeError):state=None
 if state is not None and (a.action!='wait-exit' or state.get('status') in ('exit','signal')):break
 if time.monotonic()>=deadline:p.error('timed out waiting for CLI state'+(' exit' if a.action=='wait-exit' else ''))
 time.sleep(.05)
if a.action=='wait-exit':
 print(json.dumps({'cli_pid':state['cli_pid'],'status':state['status']}))
 raise SystemExit(0)
q=lambda x:json.dumps(x,ensure_ascii=False)
if a.action=='paste':form='(ai-validation-paste-file '+q(str(Path(a.value).resolve()))+')'
elif a.action=='key':form='(ai-validation-key '+q(a.value)+' '+(q(a.mods) if a.mods else 'nil')+')'
elif a.action=='snapshot':form='(ai-validation-snapshot)'
elif a.action in ('editor-read','editor-write'):form='(ai-validation-'+a.action+' '+q(str(Path(a.value).resolve()))+')'
else:form='(kill-emacs)'
subprocess.run(['/Applications/Emacs.app/Contents/MacOS/bin/emacsclient','--socket-name',state['socket'],'-a','false','--eval',form],check=a.action!='exit',timeout=a.timeout)
