use super::*;
use std::os::unix::fs::MetadataExt;

fn quoted(s: &str) -> String {
    serde_json::to_string(s).unwrap()
}

async fn eval(endpoint: &Value, expression: String, expected: &str) -> WebResult<()> {
    let client = field(endpoint, "client")?;
    let socket = field(endpoint, "socket")?;
    if !Path::new(client).is_absolute() || !Path::new(socket).is_absolute() {
        return Err(fail("Invalid editor endpoint"));
    }
    let output = timeout(
        Duration::from_secs(8),
        tokio::process::Command::new(client)
            .args([
                "--socket-name",
                socket,
                "-a",
                "false",
                "--eval",
                &expression,
            ])
            .kill_on_drop(true)
            .output(),
    )
    .await
    .map_err(|_| fail("Emacs 응답 시간 초과"))?
    .map_err(internal)?;
    if !output.status.success() || !String::from_utf8_lossy(&output.stdout).contains(expected) {
        return Err(fail(format!(
            "Emacs 연결 전환 실패: {}",
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        )));
    }
    Ok(())
}

pub async fn release(path: &Path) -> WebResult<Value> {
    let file = path.join("editor.json");
    let meta = std::fs::symlink_metadata(&file).map_err(internal)?;
    if !meta.is_file() || meta.uid() != unsafe { libc::getuid() } || meta.mode() & 0o077 != 0 {
        return Err(fail("Invalid editor endpoint permissions"));
    }
    let endpoint = crate::common::read_json(&file, 4096).map_err(internal)?;
    let token = field(&endpoint, "attachment_token")?;
    if endpoint["connected"] != true || token.is_empty() {
        return Err(fail("활성 Emacs 연결을 확인할 수 없습니다."));
    }
    let pid = endpoint["attachment_pid"]
        .as_u64()
        .ok_or_else(|| fail("Invalid attach PID"))?;
    let expr = format!(
        r#"(let ((target {}) (token {}) (pid {}) (released nil))
      (dolist (buffer (buffer-list))
        (with-current-buffer buffer
          (when (and (boundp 'eam-terminal-persistent-directory)
                     (stringp eam-terminal-persistent-directory)
                     (equal (directory-file-name eam-terminal-persistent-directory) target))
            (let ((process (get-buffer-process buffer)))
              (when (and (process-live-p process) (equal (process-id process) pid)
                         (let ((command (process-command process)))
                           (or (member token command)
                               (and (equal (car command) "/bin/sh")
                                    (equal (cadr command) "-c") (= (length command) 3)
                                    (string-suffix-p (concat " " token) (nth 2 command))))))
                (delete-process process)
                (setq header-line-format "EAM | Remote control active")
                (setq released t))))))
      (if released "eam-remote-released" (error "Matching EAM attachment not found")))"#,
        quoted(&path.to_string_lossy()),
        quoted(token),
        pid
    );
    eval(&endpoint, expr, "eam-remote-released").await?;
    Ok(endpoint)
}

pub async fn restore(path: &Path, endpoint: &Value) -> WebResult<()> {
    eval(
        endpoint,
        format!(
            "(progn (eam-attach {}) \"eam-remote-restored\")",
            quoted(&path.to_string_lossy())
        ),
        "eam-remote-restored",
    )
    .await?;
    for _ in 0..80 {
        if let Ok(current) = crate::common::read_json(&path.join("editor.json"), 4096) {
            if current["connected"] == true
                && current["attachment_token"] != endpoint["attachment_token"]
            {
                return Ok(());
            }
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    Err(fail("Emacs 새 연결 완료를 확인하지 못했습니다."))
}
