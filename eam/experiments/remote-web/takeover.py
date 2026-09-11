"""Release only the Emacs attach process identified by its session and lease token."""
import asyncio
import json
import os
from pathlib import Path
import stat


def expression(session, endpoint):
    quote = lambda s: json.dumps(str(s), ensure_ascii=False)
    # Do not close buffers: preserve unsent drafts and the last PC display.
    return f'''(let ((target {quote(session)}) (token {quote(endpoint['attachment_token'])})
                    (pid {int(endpoint['attachment_pid'])}) (released nil))
      (dolist (buffer (buffer-list))
        (with-current-buffer buffer
          (when (and (boundp 'eam-terminal-persistent-directory)
                     (stringp eam-terminal-persistent-directory)
                     (equal (directory-file-name eam-terminal-persistent-directory) target))
            (let ((process (get-buffer-process buffer)))
              (when (and (process-live-p process)
                         (equal (process-id process) pid)
                         (let ((command (process-command process)))
                           (or (member token command)
                               (and (equal (car command) "/bin/sh")
                                    (equal (cadr command) "-c")
                                    (= (length command) 3)
                                    (string-suffix-p (concat " " token) (nth 2 command))))))
                (delete-process process)
                (setq header-line-format "EAM | Remote control active — M-x eam-attach to return")
                (setq released t))))))
      (if released "eam-remote-released" (error "Matching EAM attachment not found")))'''


async def release_emacs(session):
    path = Path(session) / 'editor.json'
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(path, flags)
    with os.fdopen(fd) as file:
        attrs = os.fstat(file.fileno())
        if not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid() or attrs.st_mode & 0o077 or attrs.st_size > 4096:
            raise ValueError('Invalid editor endpoint permissions')
        endpoint = json.load(file)
    if endpoint.get('connected') is not True or not endpoint.get('attachment_token'):
        raise ValueError('활성 Emacs 연결을 확인할 수 없습니다.')
    client, socket = endpoint['client'], endpoint['socket']
    if not Path(client).is_absolute() or not Path(socket).is_absolute():
        raise ValueError('Invalid editor endpoint')
    proc = await asyncio.create_subprocess_exec(client, '--socket-name', socket, '-a', 'false',
        '--eval', expression(str(session), endpoint),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError('Emacs 응답 시간 초과. 잠시 후 다시 시도하세요.')
    if proc.returncode or b'eam-remote-released' not in out:
        raise ValueError('Emacs 연결을 전환하지 못했습니다: ' + err.decode(errors='replace')[:300])

    return endpoint


async def restore_emacs(session, endpoint):
    target = json.dumps(str(session), ensure_ascii=False)
    expr = f'''(progn (eam-attach {target}) "eam-remote-restored")'''
    proc = await asyncio.create_subprocess_exec(endpoint['client'], '--socket-name', endpoint['socket'],
        '-a', 'false', '--eval', expr, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 8)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError('웹 연결은 해제됐지만 Emacs 복귀가 지연됩니다. PC에서 eam-attach로 재연결하세요.')
    if proc.returncode or b'eam-remote-restored' not in out:
        raise ValueError('웹 연결은 해제됐지만 Emacs 복귀에 실패했습니다: ' + err.decode(errors='replace')[:300])

    for _ in range(80):
        try:
            current = json.loads((Path(session) / 'editor.json').read_text())
            if current.get('connected') and current.get('attachment_token') != endpoint.get('attachment_token'):
                return
        except (OSError, ValueError):
            pass
        await asyncio.sleep(.05)
    raise ValueError('Emacs에서 새 연결을 시작했지만 완료를 확인하지 못했습니다.')
