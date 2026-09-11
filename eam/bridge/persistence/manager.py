"""Owned session lifecycle; no command runs on import.

Historical Python PTY prototype. The product uses the Rust runtime.
JSON is metadata, never executable code.
"""
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

# Keep the ownership protocol key stable across the public EAM rename.
HERE=Path(__file__).resolve().parent


def save(path,data):
    fd,name=tempfile.mkstemp(prefix='.state-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,ensure_ascii=False)
            stream.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def read(session):
    session=Path(session)
    info=session.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise ValueError('Session directory must be private and owned by this user')
    path=session/'session.json'
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_size>16384:raise ValueError('Oversized session metadata')
        data=os.read(fd,16385)
        if len(data)>16384:raise ValueError('Oversized session metadata')
        value=json.loads(data)
    finally:os.close(fd)
    if value.get('version')!=1 or not isinstance(value.get('id'),str) or len(value['id'])!=32:
        raise ValueError('Invalid session metadata')
    int(value['id'],16)
    if value.get('stopped') is True:
        return value
    runtime=Path(value['runtime'])
    if not runtime.is_absolute():raise ValueError('Runtime must be absolute')
    info=runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise ValueError('Runtime must be private and owned by this user')
    marker=runtime/'owner'
    if marker.is_symlink() or marker.stat().st_size!=32 or marker.read_text()!=value['id']:
        raise ValueError('Runtime owner mismatch')
    return value


def start(session,provider,executable,args,directory,raw_recording=False,backend="pty"):
    """Explicitly start one CLI, preserving native arguments and no user prompt."""
    if backend != "pty":raise ValueError("Unknown terminal backend")
    session=Path(session).resolve()
    directory=Path(directory).resolve(strict=True)
    if not directory.is_dir():raise ValueError('Project must be a directory')
    if not Path(executable).is_absolute() or not os.access(executable,os.X_OK):
        raise ValueError('CLI executable must be an executable absolute path')
    session.mkdir(mode=0o700,parents=True,exist_ok=False)
    identity=uuid.uuid4().hex
    runtime=Path(tempfile.mkdtemp(prefix='eai-persist-',dir='/tmp'))
    (runtime/'owner').write_text(identity)
    metadata=dict(version=1,id=identity,provider=provider,backend=backend,directory=str(directory),runtime=str(runtime),
                  archive=str(session/'output.ansi') if raw_recording else None,events=str(session/'events.jsonl'))
    save(session/'session.json',metadata)
    env=dict(os.environ)
    for key in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY','CODEX_API_KEY'):
        env.pop(key,None)
    env['EDITOR']=env['VISUAL']=shlex.join([sys.executable,str(HERE/'editor.py'),str(session/'editor.json')])
    if backend == 'pty':
        from pty_backend import start as pty_start
        env['TERM']='xterm-256color'
        return pty_start(session,metadata,executable,args,env)

def status(session):
    value=read(session)
    if value.get('stopped') is True:return dict(state='stopped',exit_code=value.get('exit_code',''),attached_clients=0)
    if value.get('backend') == 'pty':
        from pty_backend import status as pty_status
        return pty_status(session,value)
    raise ValueError('Unsupported stored session backend')


def stop(session, only_exited=False):
    """Serialize automatic and explicit cleanup for this owned session."""
    read(session)
    with open(Path(session)/'cleanup.lock', 'a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _stop(session, only_exited)


def _stop(session, only_exited=False):
    """Explicitly stop only a server whose owner token matches this session."""
    value=read(session)
    current=status(session)
    if current['state'] in ('unavailable','stopped'):return current
    if only_exited and current['state']!='exited':return current
    if value.get('backend') == 'pty':
        from pty_backend import stop as pty_stop
        return pty_stop(session,value)
    raise ValueError('Unsupported stored session backend')


def attach(session,client,socket,token=None):
    """Hold an OS-released exclusive attachment lease for the display process."""
    import fcntl
    value=read(session)
    session=Path(session)
    fd=os.open(session/'attach.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Session is already attached')
        current=status(session)
        if current['state']!='running' or current['attached_clients']:
            raise ValueError('Session is not available for an exclusive attachment')
        for path in (client,socket):
            if not isinstance(path,str) or not os.path.isabs(path):raise ValueError('Invalid editor endpoint')
        if token is None:token=uuid.uuid4().hex
        if not isinstance(token,str) or not 1<=len(token)<=128:raise ValueError('Invalid attachment token')
        endpoint=dict(version=1,client=client,socket=socket,lease=str(session/'attach.lock'),attachment_token=token,
                      attachment_pid=os.getpid(),connected=False)
        save(session/'editor.json',endpoint)
        child=None
        try:
            if value.get('backend') == 'pty':
                from pty_relay import attach as pty_attach
                def ready(info):
                    endpoint['connected']=True
                    endpoint['replay_complete']=info['replay_complete']
                    save(session/'editor.json',endpoint)
                pty_attach(value['runtime'],on_ready=ready)
                return 0
            raise ValueError('Unsupported stored session backend')
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait()
            (session/'editor.json').unlink(missing_ok=True)
    finally:os.close(fd)


def list_live(root, extra=()):
    """Read owned sessions, including detached ones; never start/stop a CLI."""
    from concurrent.futures import ThreadPoolExecutor
    root=Path(root)
    paths=set(str(Path(p).absolute()) for p in extra)
    if root.exists():
        for path in root.iterdir():
            if path.is_dir():paths.add(str(path.absolute()))
            if len(paths)>256:raise ValueError('Too many sessions to query (limit 256)')
    if len(paths)>256:raise ValueError('Too many sessions to query (limit 256)')
    def inspect_one(path):
        try:
            metadata=read(path)
            state=status(path)
            if state['state']=='running':
                return dict(session=path,provider=metadata['provider'],directory=metadata['directory'],
                            attached_clients=state['attached_clients'])
            if state['state']=='unavailable':return dict(unverified=path)
        except (OSError,ValueError,RuntimeError,KeyError,subprocess.TimeoutExpired):
            return dict(unverified=path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries=list(pool.map(inspect_one, sorted(paths)))
    return dict(sessions=[e for e in entries if e and 'session' in e],
                unverified=[e['unverified'] for e in entries if e and 'unverified' in e])


def guard_worktree(root, directory):
    """Reject known non-stopped sessions, including detached/unknown sessions."""
    root=Path(root)
    directory=Path(directory).resolve()
    if not root.exists():return dict(allowed=True)
    for session in root.iterdir():
        if not session.is_dir():continue
        value=read(session)
        project=Path(value['directory']).resolve()
        if project==directory or directory in project.parents:
            if value.get('stopped') is not True:
                raise ValueError(f'Persistent session must be explicitly stopped before worktree removal: {session}')
    return dict(allowed=True)


if __name__=='__main__':
    try:
        action=sys.argv[1]
        if action=='attach':
            raise SystemExit(attach(*sys.argv[2:]))
        payload=sys.stdin.buffer.read(16385)
        if len(payload)>16384:raise ValueError('Request exceeds 16 KiB')
        request=json.loads(payload)
        if action=='start':
            result=start(request['session'],request['provider'],request['executable'],request['args'],request['directory'],request.get('raw_recording',False),request.get('backend','pty'))
        elif action=='inspect':
            result=dict(metadata=read(request['session']),status=status(request['session']))
        elif action=='list-live':result=list_live(request['root'],request.get('extra',[]))
        elif action=='stop':result=stop(request['session'])
        elif action=='guard-worktree':result=guard_worktree(request['root'],request['directory'])
        else:raise ValueError('Unknown session operation')
        print(json.dumps(result,ensure_ascii=False))
    except (OSError,ValueError,RuntimeError,KeyError,IndexError,subprocess.TimeoutExpired) as exc:
        print(f'PERSISTENT SESSION ERROR: {exc}',file=sys.stderr)
        raise SystemExit(1)
