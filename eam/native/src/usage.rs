//! Passive, bounded usage snapshots. Never infer conversation identity from recency.
use crate::{common::*, session};
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    os::unix::io::AsRawFd,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};
const LIMIT: u64 = 1024 * 1024;
fn records(path: &Path, tail: bool) -> Result<Vec<Value>> {
    let mut f = File::open(path)?;
    let meta = f.metadata()?;
    if !meta.is_file() {
        return Err(error("Regular history file required"));
    }
    let offset = if tail {
        meta.len().saturating_sub(LIMIT)
    } else {
        0
    };
    f.seek(SeekFrom::Start(offset))?;
    let mut b = Vec::new();
    f.take(LIMIT).read_to_end(&mut b)?;
    let mut lines = b.split_inclusive(|c| *c == b'\n');
    if offset > 0 {
        lines.next();
    }
    let mut result = Vec::new();
    for line in lines {
        if line.last() != Some(&b'\n') {
            continue;
        }
        if let Ok(v) = serde_json::from_slice::<Value>(line) {
            result.push(v);
        }
    }
    Ok(result)
}
fn number(v: &Value) -> Value {
    v.as_u64().map_or(Value::Null, |n| json!(n))
}
fn model(v: &Value) -> Value {
    v.as_str().map_or(Value::Null, |s| {
        json!(s
            .chars()
            .filter(|c| !c.is_control())
            .take(80)
            .collect::<String>())
    })
}
fn snapshot(entry: &Value, root: &Path) -> Result<Value> {
    if !["Claude", "Codex"].contains(&string(entry, "provider")?)
        || string(entry, "id")?.is_empty()
        || string(entry, "id")?.len() > 128
    {
        return Err(error("Invalid usage conversation"));
    }
    let codex = entry["provider"] == "Codex";
    let mut result =
        json!({"state":"unavailable","id":entry["id"],"scope":if codex {"total"} else {"last"}});
    if let Some(path) = entry["path"].as_str().filter(|s| !s.is_empty()) {
        let path = Path::new(path);
        if path.is_file() {
            let head = records(path, false)?;
            let matches = head.iter().any(|v| {
                if codex {
                    v["type"] == "session_meta" && v["payload"]["id"] == entry["id"]
                } else {
                    v["sessionId"] == entry["id"] && v["isSidechain"] != true
                }
            });
            if !matches {
                return Err(error("Usage conversation identity mismatch"));
            }
            let tail = records(path, true)?;
            result["activity"] = crate::activity::snapshot(
                &tail,
                codex,
                &entry["id"],
                fs::metadata(path)?.len() > LIMIT,
            );
            result["checked_at"] = json!(now());
            for v in tail {
                if codex {
                    if v["type"] == "turn_context" {
                        result["model"] = model(&v["payload"]["model"]);
                    }
                    if v["type"] != "event_msg" || v["payload"]["type"] != "token_count" {
                        continue;
                    }
                    let info = &v["payload"]["info"];
                    let usage = &info["total_token_usage"];
                    if !usage.is_object() {
                        continue;
                    }
                    result["input"] = number(&usage["input_tokens"]);
                    result["output"] = number(&usage["output_tokens"]);
                    result["cached"] = number(&usage["cached_input_tokens"]);
                    result["total"] = number(&usage["total_tokens"]);
                    result["context_used"] = number(&info["last_token_usage"]["total_tokens"]);
                    result["context_limit"] = number(&info["model_context_window"]);
                } else {
                    if v["sessionId"] != entry["id"]
                        || v["isSidechain"] == true
                        || v["type"] != "assistant"
                    {
                        continue;
                    }
                    let usage = &v["message"]["usage"];
                    if !usage.is_object() {
                        continue;
                    }
                    result["input"] = number(&usage["input_tokens"]);
                    result["output"] = number(&usage["output_tokens"]);
                    result["cached"] = number(&usage["cache_read_input_tokens"]);
                    result["cache_write"] = number(&usage["cache_creation_input_tokens"]);
                    result["model"] = model(&v["message"]["model"]);
                }
                result["state"] = json!("available");
                result["timestamp"] = model(&v["timestamp"]);
            }
        }
    }
    // Paginated Codex histories may not retain token_count events in JSONL.
    if codex && result["state"] != "available" && root.join("state_5.sqlite").is_file() {
        let db = Connection::open_with_flags(
            root.join("state_5.sqlite"),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        db.busy_timeout(Duration::from_millis(300))?;
        let mut st = db.prepare(
            "SELECT tokens_used FROM threads WHERE id=? AND rtrim(cwd,'/')=rtrim(?,'/')",
        )?;
        let mut rows = st.query([string(entry, "id")?, string(entry, "directory")?])?;
        if let Some(row) = rows.next()? {
            let tokens: i64 = row.get(0)?;
            if tokens >= 0 {
                result["saved"] = json!(tokens);
                result["scope"] = json!("saved");
                result["state"] = json!("available");
            }
        }
    }
    Ok(result)
}
// Bounded subprocess capture, including cleanup on timeout. No shell or CLI calls.
fn capture(command: &str, args: &[&str]) -> Result<Vec<u8>> {
    let mut c = Command::new(command);
    c.args(args);
    capture_command(&mut c)
}
pub fn capture_command(c: &mut Command) -> Result<Vec<u8>> {
    let mut child = c
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut out = child.stdout.take().unwrap();
    unsafe {
        libc::fcntl(out.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK);
    }
    let deadline = Instant::now() + Duration::from_millis(700);
    let result = (|| {
        let mut bytes = Vec::new();
        loop {
            let mut b = [0; 8192];
            match out.read(&mut b) {
                Ok(0) => break,
                Ok(n) => {
                    bytes.extend_from_slice(&b[..n]);
                    if bytes.len() > LIMIT as usize {
                        return Err(error("Process inventory too large"));
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => (),
                Err(e) => return Err(e.into()),
            }
            if Instant::now() > deadline {
                return Err(error("Process inventory timeout"));
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        Ok(bytes)
    })();
    let _ = child.kill();
    let _ = child.wait();
    result
}
fn discover(q: &Value, metadata: &Value) -> Result<Option<Value>> {
    let state = session::status(Path::new(string(q, "session")?))?;
    if state["state"] != "running" {
        return Ok(None);
    }
    let Some(pid) = state["recorder_pid"].as_u64() else {
        return Ok(None);
    };
    // The npm Codex launcher has one native codex child. Do not attribute
    // files held by arbitrary tool descendants or subagents to the main CLI.
    let tree = capture("/bin/ps", &["-axo", "pid=,ppid=,comm="])?;
    let mut pids = vec![pid];
    if metadata["provider"] == "Codex" {
        for line in String::from_utf8_lossy(&tree).lines() {
            let mut words = line.split_whitespace();
            let p = words.next().and_then(|s| s.parse::<u64>().ok());
            let parent = words.next().and_then(|s| s.parse::<u64>().ok());
            let command = words.next().unwrap_or("");
            if parent == Some(pid) && Path::new(command).file_name().is_some_and(|x| x == "codex") {
                if let Some(p) = p {
                    pids.push(p);
                }
            }
        }
    }
    let filter = pids
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let raw = capture("lsof", &["-b", "-nP", "-a", "-p", &filter, "-Ffan0"])?;
    let root = fs::canonicalize(string(q, "root")?)?;
    let directory = fs::canonicalize(string(metadata, "directory")?)?;
    let codex = metadata["provider"] == "Codex";
    let mut entries = Vec::new();
    let mut paths = std::collections::BTreeSet::new();
    let mut writable = false;
    for field in raw.split(|b| *b == 0) {
        let field = field.strip_prefix(b"\n").unwrap_or(field);
        if field.starts_with(b"f") {
            writable = false;
        }
        if let Some(access) = field.strip_prefix(b"a") {
            writable = access == b"w" || access == b"u";
        }
        if let Some(name) = field.strip_prefix(b"n").filter(|_| writable) {
            let Ok(name) = std::str::from_utf8(name) else {
                continue;
            };
            let path = PathBuf::from(name);
            if path.extension().is_some_and(|x| x == "jsonl") && path.starts_with(&root) {
                paths.insert(path);
            }
        }
    }
    if paths.len() > 16 {
        return Ok(None);
    }
    for path in paths {
        let head = records(&path, false)?;
        for v in head {
            let (id, cwd) = if codex && v["type"] == "session_meta" {
                (v["payload"]["id"].as_str(), v["payload"]["cwd"].as_str())
            } else if !codex && v["isSidechain"] != true {
                (v["sessionId"].as_str(), v["cwd"].as_str())
            } else {
                (None, None)
            };
            if let (Some(id), Some(cwd)) = (id, cwd) {
                if fs::canonicalize(cwd).ok().as_ref() == Some(&directory) {
                    entries.push(json!({"provider":metadata["provider"],"id":id,"directory":cwd,"path":path}));
                }
                break;
            }
        }
    }
    Ok(if entries.len() == 1 {
        entries.pop()
    } else {
        None
    })
}
// Identity comes from a session callback/resume argument or an open writable
// transcript. Never select another session merely because its file is newer.
fn bound_entry(
    q: &Value,
    metadata: &Value,
    telemetry: &Value,
    root: &Path,
    discover_live: bool,
) -> Option<Value> {
    if metadata["provider"] == "Claude" {
        if telemetry["id"].is_string() && telemetry["path"].is_string() {
            return Some(
                json!({"provider":"Claude","id":telemetry["id"],"path":telemetry["path"]}),
            );
        }
    } else if let Some(id) = telemetry["id"].as_str() {
        if let Ok(db) = Connection::open_with_flags(
            root.join("state_5.sqlite"),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        ) {
            let _ = db.busy_timeout(Duration::from_millis(300));
            if let Ok(entry) = db.query_row(
                "SELECT cwd, rollout_path FROM threads WHERE id=?",
                [id],
                |row| {
                    let dir: String = row.get(0)?;
                    let file: Option<String> = row.get(1)?;
                    Ok(json!({"provider":"Codex","id":id,"directory":dir,"path":file}))
                },
            ) {
                return Some(entry);
            }
        }
    }
    if discover_live {
        discover(q, metadata).ok().flatten()
    } else {
        None
    }
}
pub fn observe(path: &Path, metadata: &Value) -> Value {
    observe_inner(path, metadata, true)
}
pub fn history_entry(path: &Path) -> Result<Value> {
    let metadata = session(path)?;
    let root = Path::new(string(&metadata, "auth_root")?);
    let telemetry = read_json(&path.join("telemetry.json"), 16384).unwrap_or(Value::Null);
    let q = json!({"session":path,"root":root});
    let entry = bound_entry(&q, &metadata, &telemetry, root, true)
        .ok_or_else(|| error("Current conversation is not identified yet; retry after the CLI saves a turn. Use C-c a o to choose another saved conversation."))?;
    if !entry["path"]
        .as_str()
        .is_some_and(|p| Path::new(p).is_file())
    {
        return Err(error(
            "Current conversation has no readable transcript yet; retry after the CLI saves it",
        ));
    }
    // History identity must not depend on token counters or quota schemas.
    let head = records(Path::new(string(&entry, "path")?), false)?;
    let matches = head.iter().any(|v| {
        if metadata["provider"] == "Codex" {
            v["type"] == "session_meta" && v["payload"]["id"] == entry["id"]
        } else {
            v["sessionId"] == entry["id"] && v["isSidechain"] != true
        }
    });
    if !matches {
        return Err(error(
            "Current conversation transcript identity could not be verified",
        ));
    }
    Ok(entry)
}
pub fn summary(path: &Path, metadata: &Value) -> Value {
    let v = observe_inner(path, metadata, false);
    json!({"activity":{"state":v["activity"]["state"],"estimated":v["activity"]["estimated"]},
        "context_percent":v["context_percent"],"context_used":v["context_used"],
        "context_limit":v["context_limit"],"timestamp":v["timestamp"],"context_timestamp":v["context_timestamp"]})
}
fn observe_inner(path: &Path, metadata: &Value, discover_live: bool) -> Value {
    let Some(root) = metadata["auth_root"].as_str() else {
        return json!({"state":"waiting"});
    };
    let telemetry = read_json(&path.join("telemetry.json"), 16384).unwrap_or(Value::Null);
    let q = json!({"session":path,"root":root});
    let mut v = bound_entry(&q, metadata, &telemetry, Path::new(root), discover_live)
        .and_then(|entry| {
            if let Some(id) = entry["id"].as_str() {
                let _ = crate::notes::remember(path, id);
            }
            snapshot(&entry, Path::new(root)).ok()
        })
        .unwrap_or(json!({"state":"waiting"}));
    v["source"] = json!("automatic");
    if metadata["provider"] == "Claude" {
        for key in ["context_percent", "context_limit"] {
            if telemetry[key].is_number() {
                v[key] = telemetry[key].clone();
            }
        }
        v["context_timestamp"] = telemetry["timestamp"].clone();
    }
    v
}
pub fn dispatch(q: &Value) -> Result<Value> {
    if q["action"] == "account" {
        return Ok(crate::account::snapshot(
            string(q, "provider")?,
            Path::new(string(q, "root")?),
        ));
    }
    if q["action"] == "snapshot" {
        return snapshot(&q["entry"], Path::new(string(q, "root")?));
    }
    if q["action"] != "poll" && q["action"] != "observe" && !q["action"].is_null() {
        return Err(error("Unsupported usage action"));
    }
    let path = Path::new(string(q, "session")?);
    let metadata = session(path)?;
    if q["action"] == "observe" {
        return Ok(observe(path, &metadata));
    }
    let account = if q["account"] == false {
        Value::Null
    } else {
        let root = metadata["auth_root"]
            .as_str()
            .map(PathBuf::from)
            .or_else(|| {
                crate::account::launch_root(
                    metadata["provider"].as_str().unwrap_or(""),
                    Path::new(metadata["directory"].as_str().unwrap_or("/")),
                )
            });
        let mut result = root.map_or(json!({"state":"unknown"}), |root| {
            crate::account::snapshot_with_override(
                metadata["provider"].as_str().unwrap_or(""),
                &root,
                crate::account::config_override(&root, &metadata["auth_root_override"]),
            )
        });
        if metadata["provider"] == "Claude" && metadata["telemetry"] == true {
            if let (Some(exe), Some(root)) = (
                metadata["executable"].as_str(),
                metadata["auth_root"].as_str(),
            ) {
                if let Some(current) = crate::account::claude_cli(
                    exe,
                    Path::new(root),
                    crate::account::config_override(
                        Path::new(root),
                        &metadata["auth_root_override"],
                    ),
                ) {
                    result = current;
                }
            }
        }
        result["root_source"] = json!(if metadata["auth_root"].is_string() {
            "launch"
        } else {
            "current environment"
        });
        result
    };
    if q["usage"] == false {
        return Ok(json!({"state":"disabled","account":account}));
    }
    // Session callbacks identify the conversation without asking the user to guess.
    let telemetry = read_json(&path.join("telemetry.json"), 16384).unwrap_or(Value::Null);
    let mut provider_usage = if metadata["provider"] == "Codex" && metadata["telemetry"] == true {
        crate::quota::snapshot(path, &metadata, q["account"] != false)
    } else {
        telemetry["provider_usage"].clone()
    };
    let account = if q["account"] != false && provider_usage["account"].is_object() {
        provider_usage["account"].clone()
    } else {
        account
    };
    if let Some(obj) = provider_usage.as_object_mut() {
        obj.remove("account");
    }
    let observed = observe(path, &metadata);
    let mut usage = if metadata["provider"] == "Claude" && telemetry["id"].is_string() {
        telemetry.clone()
    } else {
        observed.clone()
    };
    usage["account"] = account;
    usage["provider_usage"] = provider_usage;
    usage["activity"] = observed["activity"].clone();
    usage["checked_at"] = json!(now());
    Ok(usage)
}
