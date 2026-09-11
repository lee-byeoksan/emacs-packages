//! Preserved explicit read-only buffer adapters; native terminal sessions do not use this.
use crate::{common::*, transport};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::{io::AsRawFd, process::CommandExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
const FRAME: usize = 4 * 1024 * 1024;
fn display(s: &str) -> Result<()> {
    std::io::stdout().write_all(s.as_bytes())?;
    std::io::stdout().flush()?;
    Ok(())
}
fn environment(c: &mut Command, p: &str) {
    let keys: &[&str] = if p == "codex" {
        &["OPENAI_API_KEY", "CODEX_API_KEY"]
    } else {
        &[
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_PROFILE",
            "ANTHROPIC_FEDERATION_RULE_ID",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        ]
    };
    for k in keys {
        c.env_remove(k);
    }
    if p == "claude" {
        c.env("CLAUDE_CODE_SAFE_MODE", "1");
    }
}
struct Wire {
    child: Child,
    pending: Vec<u8>,
    raw: File,
}
impl Drop for Wire {
    fn drop(&mut self) {
        let _ = transport::terminate(&mut self.child);
    }
}
impl Wire {
    fn new(exe: &str, args: &[String], cwd: &str, provider: &str, prefix: &str) -> Result<Self> {
        let err = OpenOptions::new()
            .append(true)
            .create(true)
            .open(format!("{prefix}.stderr.log"))?;
        let raw = OpenOptions::new()
            .append(true)
            .create(true)
            .open(format!("{prefix}.protocol.jsonl"))?;
        let mut c = Command::new(exe);
        c.args(args)
            .current_dir(cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(err);
        environment(&mut c, provider);
        unsafe {
            c.pre_exec(|| {
                if libc::setsid() < 0 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                }
            });
        }
        let child = c.spawn()?;
        nonblock(child.stdout.as_ref().unwrap().as_raw_fd())?;
        Ok(Self {
            child,
            pending: Vec::new(),
            raw,
        })
    }
    fn send(&mut self, v: &Value) -> Result<()> {
        let input = self
            .child
            .stdin
            .as_mut()
            .ok_or_else(|| error("Provider input closed"))?;
        serde_json::to_writer(&mut *input, v)?;
        input.write_all(b"\n")?;
        input.flush()?;
        Ok(())
    }
    fn chunk(&mut self, end: Instant) -> Result<bool> {
        loop {
            if transport::interrupted() {
                return Err(error("Cancelled; no automatic retry"));
            }
            let remain = end.saturating_duration_since(Instant::now());
            if remain.is_zero() {
                return Err(error("Provider idle timeout; no automatic retry"));
            }
            let stdout = self.child.stdout.as_mut().unwrap();
            let mut p = libc::pollfd {
                fd: stdout.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            let n =
                unsafe { libc::poll(&mut p, 1, remain.as_millis().min(i32::MAX as u128) as i32) };
            if n < 0 {
                if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(std::io::Error::last_os_error().into());
            }
            if n == 0 {
                continue;
            }
            let mut b = [0; 8192];
            let n = stdout.read(&mut b)?;
            if n == 0 {
                return Ok(false);
            }
            self.raw.write_all(&b[..n])?;
            self.pending.extend(&b[..n]);
            if self.pending.len() > FRAME + 8192 {
                return Err(error("Provider frame exceeds 4 MiB"));
            }
            return Ok(true);
        }
    }
    fn next(&mut self, seconds: u64) -> Result<Value> {
        let end = Instant::now() + Duration::from_secs(seconds);
        loop {
            if let Some(pos) = self.pending.iter().position(|&b| b == b'\n') {
                if pos > FRAME {
                    return Err(error("Provider frame exceeds 4 MiB"));
                }
                let line: Vec<u8> = self.pending.drain(..=pos).collect();
                if line.iter().all(u8::is_ascii_whitespace) {
                    continue;
                }
                return Ok(serde_json::from_slice(&line)?);
            }
            if !self.chunk(end)? {
                return Err(error("Provider exited before completion"));
            }
        }
    }
}
struct Runner {
    wire: Option<Wire>,
    state: Value,
    path: PathBuf,
    serial: u64,
    item: Value,
    had_delta: bool,
    completed: Option<Value>,
}
impl Runner {
    fn save(&self) -> Result<()> {
        save(&self.path, &self.state)
    }
    fn wire(&mut self) -> &mut Wire {
        self.wire.as_mut().unwrap()
    }
    fn event(&mut self, m: Value) -> Result<()> {
        let method = m["method"].as_str().unwrap_or("");
        let p = &m["params"];
        if !m["id"].is_null() && !method.is_empty() {
            let result = match method {
                "item/commandExecution/requestApproval" | "item/fileChange/requestApproval" => {
                    display("\n[Permission request declined: read-only connection]\n")?;
                    json!({"id":m["id"],"result":{"decision":"decline"}})
                }
                "item/tool/requestUserInput" => {
                    display("\n[Interactive input unsupported in this connection]\n")?;
                    json!({"id":m["id"],"result":{"answers":{}}})
                }
                _ => {
                    json!({"id":m["id"],"error":{"code":-32601,"message":"Unsupported client request"}})
                }
            };
            return self.wire().send(&result);
        }
        match method {
            "item/agentMessage/delta" => {
                if self.item != p["itemId"] {
                    self.item = p["itemId"].clone();
                }
                self.had_delta = true;
                display(p["delta"].as_str().unwrap_or(""))?
            }
            "item/completed" => {
                let item = &p["item"];
                if item["type"] == "agentMessage" {
                    if item["id"] != self.item || !self.had_delta {
                        display(item["text"].as_str().unwrap_or(""))?
                    }
                    display("\n")?;
                    self.item = Value::Null;
                    self.had_delta = false;
                } else if item["type"] == "commandExecution" {
                    display(&format!(
                        "\n[command]\n{}\n",
                        item["aggregatedOutput"].as_str().unwrap_or("")
                    ))?
                }
            }
            "turn/completed" => self.completed = Some(p["turn"].clone()),
            "error" => display(&format!(
                "\n[provider error] {}\n",
                p["error"]["message"].as_str().unwrap_or("unknown")
            ))?,
            _ => (),
        }
        Ok(())
    }
    fn rpc(&mut self, method: &str, params: Value) -> Result<Value> {
        self.serial += 1;
        let id = self.serial;
        self.wire()
            .send(&json!({"id":id,"method":method,"params":params}))?;
        loop {
            let m = self.wire().next(60)?;
            if m["id"] == id && m["method"].is_null() {
                if !m["error"].is_null() {
                    return Err(error(m["error"].to_string()));
                }
                return Ok(m["result"].clone());
            }
            self.event(m)?
        }
    }
    fn codex(&mut self, exe: &str, cwd: &str, prefix: &str, prompt: &str) -> Result<()> {
        let mut settings = vec![
            ("features.hooks".to_owned(), json!(false)),
            ("features.plugins".into(), json!(false)),
            ("features.apps".into(), json!(false)),
            ("features.multi_agent".into(), json!(false)),
            ("features.memories".into(), json!(false)),
            ("features.skill_search".into(), json!(false)),
            ("features.skip_host_skill_discovery".into(), json!(true)),
            ("project_doc_max_bytes".into(), json!(0)),
            ("developer_instructions".into(), json!("")),
            ("web_search".into(), json!("disabled")),
            ("analytics.enabled".into(), json!(false)),
        ];
        let home = std::env::var_os("CODEX_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".codex")
            });
        let mut configs = vec![home.join("config.toml")];
        configs.extend(
            Path::new(cwd)
                .ancestors()
                .map(|p| p.join(".codex/config.toml")),
        );
        for p in configs {
            if p.is_file() {
                let raw = bounded(&p, 1024 * 1024)?;
                let v: toml::Value = toml::from_str(std::str::from_utf8(&raw)?)?;
                if let Some(m) = v.get("mcp_servers").and_then(toml::Value::as_table) {
                    for name in m.keys() {
                        if name.is_empty()
                            || !name
                                .chars()
                                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
                        {
                            return Err(error("Unsupported MCP server name"));
                        }
                        settings.push((format!("mcp_servers.{name}.enabled"), json!(false)));
                    }
                }
            }
        }
        let mut args = vec!["app-server".to_owned()];
        for (k, v) in settings {
            args.extend(["-c".to_owned(), format!("{k}={v}")]);
        }
        self.wire = Some(Wire::new(exe, &args, cwd, "codex", prefix)?);
        self.rpc(
            "initialize",
            json!({"clientInfo":{"name":"emacs_ai","version":"0.2.0"}}),
        )?;
        self.wire()
            .send(&json!({"method":"initialized","params":{}}))?;
        let account = self.rpc("account/read", json!({"refreshToken":false}))?["account"].clone();
        if account["type"] != "chatgpt" {
            return Err(error("ChatGPT login required"));
        }
        self.state["auth"] = json!({"type":account["type"],"plan":account["planType"]});
        let mut params = json!({"cwd":cwd,"modelProvider":"openai","approvalPolicy":"never","sandbox":"read-only"});
        let method = if self.state["backend_id"].is_string() {
            params["threadId"] = self.state["backend_id"].clone();
            params["excludeTurns"] = json!(true);
            "thread/resume"
        } else {
            "thread/start"
        };
        let thread = self.rpc(method, params)?;
        self.state["backend_id"] = thread["thread"]["id"].clone();
        self.state["instruction_sources"] = thread["instructionSources"].clone();
        self.state["status"] = json!("running");
        self.save()?;
        self.rpc(
            "turn/start",
            json!({"threadId":self.state["backend_id"],"input":[{"type":"text","text":prompt}]}),
        )?;
        while self.completed.is_none() {
            let m = self.wire().next(300)?;
            self.event(m)?;
        }
        if self.completed.as_ref().unwrap()["status"] != "completed" {
            return Err(error(self.completed.as_ref().unwrap().to_string()));
        }
        Ok(())
    }
    fn claude(&mut self, exe: &str, cwd: &str, prefix: &str, prompt: &str) -> Result<()> {
        let authargs = ["--safe-mode", "auth", "status", "--json"].map(str::to_owned);
        let mut auth = Wire::new(exe, &authargs, cwd, "claude", &format!("{prefix}.auth"))?;
        auth.child.stdin.take();
        let end = Instant::now() + Duration::from_secs(20);
        while auth.chunk(end)? {}
        let account: Value = serde_json::from_slice(&auth.pending)?;
        if account["loggedIn"] != true || account["authMethod"] != "claude.ai" {
            return Err(error("Claude.ai login required"));
        }
        self.state["auth"] = json!({"type":"claude.ai","plan":account["subscriptionType"]});
        drop(auth);
        let mut args: Vec<String> = [
            "--safe-mode",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--tools",
            "Read,Grep,Glob",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--prompt-suggestions",
            "false",
        ]
        .map(str::to_owned)
        .to_vec();
        if let Some(id) = self.state["backend_id"].as_str() {
            args.extend(["--resume".into(), id.into()]);
        }
        self.wire = Some(Wire::new(exe, &args, cwd, "claude", prefix)?);
        let mut input = self.wire().child.stdin.take().unwrap();
        input.write_all(prompt.as_bytes())?;
        drop(input);
        let mut delta = false;
        loop {
            let v = self.wire().next(300)?;
            match v["type"].as_str().unwrap_or("") {
                "system" if v["subtype"] == "init" => {
                    self.state["backend_id"] = v["session_id"].clone();
                    self.state["status"] = json!("running");
                    self.save()?
                }
                "stream_event" => {
                    let e = &v["event"];
                    if e["type"] == "message_start" {
                        delta = false
                    }
                    if e["delta"]["type"] == "text_delta" {
                        delta = true;
                        display(e["delta"]["text"].as_str().unwrap_or(""))?
                    }
                }
                "assistant" => {
                    if !delta {
                        if let Some(blocks) = v["message"]["content"].as_array() {
                            for b in blocks {
                                if b["type"] == "text" {
                                    display(b["text"].as_str().unwrap_or(""))?
                                }
                            }
                        }
                    }
                    display("\n")?
                }
                "result" => {
                    if v["is_error"] == true {
                        return Err(error(v.to_string()));
                    }
                    if v["permission_denials"]
                        .as_array()
                        .is_some_and(|x| !x.is_empty())
                    {
                        display(
                            "\n[Some tool operations were denied by the read-only connection]\n",
                        )?
                    }
                    break;
                }
                "system"
                    if ["api_retry", "permission_denied"]
                        .contains(&v["subtype"].as_str().unwrap_or("")) =>
                {
                    display(&format!("\n[Claude {}]\n", v["subtype"]))?
                }
                _ => (),
            }
        }
        Ok(())
    }
}
pub fn run(args: &[String]) -> Result<()> {
    transport::signals();
    let mut opts = HashMap::new();
    for pair in args.chunks(2) {
        if pair.len() != 2 {
            return Err(error("Invalid provider arguments"));
        }
        opts.insert(pair[0].as_str(), pair[1].as_str());
    }
    let get = |k| {
        opts.get(k)
            .copied()
            .ok_or_else(|| error(format!("Missing {k}")))
    };
    let (provider, exe, cwd, path, prefix) = (
        get("--provider")?,
        get("--executable")?,
        get("--cwd")?,
        get("--state")?,
        get("--prefix")?,
    );
    if !["claude", "codex"].contains(&provider) {
        return Err(error("Unsupported provider"));
    }
    let prompt = String::from_utf8(stdin_limit(1024 * 1024)?)?;
    if prompt.is_empty() {
        return Err(error("Empty prompt"));
    }
    let cwd = std::fs::canonicalize(cwd)?.to_string_lossy().into_owned();
    let state = if Path::new(path).exists() {
        read_json(Path::new(path), 4 * 1024 * 1024)?
    } else {
        json!({"provider":provider,"cwd":cwd,"backend_id":null})
    };
    if state["provider"] != provider || state["cwd"] != cwd {
        return Err(error("Saved provider/cwd mismatch"));
    }
    let mut r = Runner {
        wire: None,
        state,
        path: path.into(),
        serial: 0,
        item: Value::Null,
        had_delta: false,
        completed: None,
    };
    r.state["status"] = json!("starting");
    r.save()?;
    let result = if provider == "codex" {
        r.codex(exe, &cwd, prefix, &prompt)
    } else {
        r.claude(exe, &cwd, prefix, &prompt)
    };
    if result.is_ok() {
        r.state["status"] = json!("completed")
    } else {
        let interrupted = transport::interrupted();
        let key = if interrupted {
            "interrupted_backend_id"
        } else {
            "failed_backend_id"
        };
        r.state[key] = r.state["backend_id"].clone();
        r.state["backend_id"] = Value::Null;
        r.state["status"] = json!(if interrupted { "cancelled" } else { "failed" });
    }
    r.save()?;
    result
}
