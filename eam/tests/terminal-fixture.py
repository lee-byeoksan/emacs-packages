#!/usr/bin/env python3
import os, sys, tty, json, subprocess, shlex
from pathlib import Path
tty.setraw(sys.stdin.fileno())
log=Path(sys.argv[1])
os.write(1,b'\x1b[?2004hREADY\r\n')
pending=bytearray(); pasted=False; text=''
while True:
 b=os.read(0,1)
 if not b: break
 pending.extend(b)
 if pending.endswith(b'\x1b[200~'):
  pending.clear();pasted=True
 elif pasted and pending.endswith(b'\x1b[201~'):
  text=bytes(pending[:-6]).decode('utf-8');pending.clear();pasted=False
  with log.open('a') as f:f.write(json.dumps({'paste':text},ensure_ascii=False)+'\n')
  os.write(1,('PASTED '+text.replace('\n',' | ')+'\r\n').encode())
 elif not pasted and b==b'\r':
  pending.clear()
  with log.open('a') as f:f.write('{"enter":true}\n')
  os.write(1,b'ENTER\r\n')
 elif not pasted and b==b'\x07':
  pending.clear()
  editor_file=log.with_suffix('.editor.txt')
  editor_file.write_text(text)
  subprocess.run(shlex.split(os.environ['VISUAL'])+[str(editor_file)],check=True)
  text=editor_file.read_text()
  with log.open('a') as f:f.write(json.dumps({'edited':text},ensure_ascii=False)+'\n')
  os.write(1,('EDITED '+text.replace('\n',' | ')+'\r\n').encode())
 elif not pasted and b==b'\x03':break
