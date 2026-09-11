//! Session-local CLI callbacks. Persist only identity and numeric usage, never prompts.
use crate::common::*;
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    os::unix::{io::AsRawFd, process::CommandExt},
    path::Path,
    process::{Command, Stdio},
};

fn clean(v: &Value) -> Value {
    v.as_str()
        .filter(|s| s.len() <= 512 && !s.chars().any(char::is_control))
        .map_or(Value::Null, |s| json!(s))
}
fn num(v: &Value) -> Value {
    if v.as_u64().is_some() {
        return v.clone();
    }
    v.as_f64()
        .filter(|n| n.is_finite() && *n >= 0.0)
        .map_or(Value::Null, |n| json!(n))
}
pub fn limits(v: &Value, codex: bool) -> Value {
    let keys = if codex {
        ["primary", "secondary"]
    } else {
        ["five_hour", "seven_day"]
    };
    let mut windows = Vec::new();
    for (i, key) in keys.iter().enumerate() {
        let w = &v[*key];
        let used = num(&w[if codex {
            "usedPercent"
        } else {
            "used_percentage"
        }]);
        let mins = if codex {
            num(&w["windowDurationMins"])
        } else {
            json!(if i == 0 { 300 } else { 10080 })
        };
        if used.is_number() && mins.is_number() {
            windows.push(json!({"used":used,"minutes":mins,"resets":num(&w[if codex {"resetsAt"} else {"resets_at"}])}));
        }
    }
    json!(windows)
}
pub fn claude(v: &Value) -> Value {
    let ctx = &v["context_window"];
    json!({"state":if ctx["total_input_tokens"].is_number() {"available"} else {"waiting"},
        "scope":"total", "id":clean(&v["session_id"]),"path":clean(&v["transcript_path"]),
        "model":clean(&v["model"]["display_name"]),"input":num(&ctx["total_input_tokens"]),
        "output":num(&ctx["total_output_tokens"]),"context_percent":num(&ctx["used_percentage"]),
        "context_limit":num(&ctx["context_window_size"]),"provider_usage":{"windows":limits(&v["rate_limits"],false),"timestamp":now()},
        "timestamp":now(),"source":"CLI statusline"})
}
pub fn prepare(path: &Path, q: &Value, root: &Path) -> Result<Value> {
    let mut args = q["args"].as_array().cloned().unwrap_or_default();
    let binary = std::env::current_exe()?;
    if q["provider"] == "Claude" {
        let mut original = Value::Null;
        for file in [
            root.join("settings.json"),
            Path::new(string(q, "directory")?).join(".claude/settings.json"),
            Path::new(string(q, "directory")?).join(".claude/settings.local.json"),
        ] {
            if let Ok(v) = read_json(&file, 1024 * 1024) {
                if v["statusLine"].is_object() {
                    original = v["statusLine"].clone();
                }
            }
        }
        let mut settings = json!({});
        let mut kept = Vec::new();
        let mut i = 0;
        while i < args.len() {
            if args[i] == "--settings" && i + 1 < args.len() {
                let s = args[i + 1].as_str().unwrap_or("");
                let v: Value = match serde_json::from_str(s) {
                    Ok(v) => v,
                    Err(_) => read_json(Path::new(s), 1024 * 1024)?,
                };
                if v["statusLine"].is_object() {
                    original = v["statusLine"].clone();
                }
                if let Some(obj) = v.as_object() {
                    for (k, v) in obj {
                        settings[k] = v.clone();
                    }
                }
                i += 2;
            } else {
                kept.push(args[i].clone());
                i += 1;
            }
        }
        save(
            &path.join("telemetry-config.json"),
            &json!({"statusLine":original}),
        )?;
        let command = format!(
            "{} telemetry claude {}",
            shquote(&binary.to_string_lossy()),
            shquote(&path.to_string_lossy())
        );
        settings["statusLine"] = json!({"type":"command","command":command});
        kept.extend([json!("--settings"), json!(settings.to_string())]);
        args = kept;
    } else if q["provider"] == "Codex" {
        let original = fs::read_to_string(root.join("config.toml"))
            .ok()
            .and_then(|s| toml::from_str::<toml::Value>(&s).ok())
            .and_then(|v| v.get("notify").and_then(|x| serde_json::to_value(x).ok()))
            .unwrap_or(Value::Null);
        save(
            &path.join("telemetry-config.json"),
            &json!({"notify":original}),
        )?;
        args.extend([
            json!("-c"),
            json!(format!(
                "notify={}",
                json!([binary, "telemetry", "codex", path])
            )),
        ]);
    }
    // Explicit native resume IDs are authoritative even before the first callback.
    if let Some(pair) = args
        .windows(2)
        .find(|w| w[0] == "resume" || w[0] == "--resume")
    {
        if let Some(id) = pair[1]
            .as_str()
            .filter(|s| s.len() == 36 && s.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-'))
        {
            save(
                &path.join("telemetry.json"),
                &json!({"id":id,"source":"resume argument","timestamp":now()}),
            )?;
        }
    }
    Ok(json!(args))
}
pub fn run(args: &[String]) -> Result<()> {
    let kind = args
        .first()
        .ok_or_else(|| error("Missing telemetry kind"))?;
    let path = Path::new(args.get(1).ok_or_else(|| error("Missing session"))?);
    let metadata = session(path)?;
    let raw = if kind == "codex" {
        args.get(2)
            .ok_or_else(|| error("Missing payload"))?
            .as_bytes()
            .to_vec()
    } else {
        stdin_limit(1024 * 1024)?
    };
    if raw.len() > 1024 * 1024 {
        return Err(error("Telemetry payload too large"));
    }
    let v: Value = serde_json::from_slice(&raw)?;
    let result = if kind == "claude" && metadata["provider"] == "Claude" {
        claude(&v)
    } else if kind == "codex" && metadata["provider"] == "Codex" {
        json!({"id":clean(&v["thread-id"]),"timestamp":now(),"source":"CLI notification"})
    } else {
        return Err(error("Provider mismatch"));
    };
    if result["id"].as_str().is_some_and(|s| !s.is_empty()) {
        save(&path.join("telemetry.json"), &result)?;
        let _ = crate::notes::remember(path, string(&result, "id")?);
    }
    // Preserve an existing user statusline/notify command and its output. Nothing is injected into model context.
    if let Ok(config) = read_json(&path.join("telemetry-config.json"), 65536) {
        let mut command = if kind == "claude" {
            config["statusLine"]["command"].as_str().map(|s| {
                let mut c = Command::new("/bin/sh");
                c.args(["-c", s]);
                c
            })
        } else {
            config["notify"]
                .as_array()
                .filter(|a| !a.is_empty())
                .and_then(|a| {
                    let mut c = Command::new(a[0].as_str()?);
                    for arg in &a[1..] {
                        c.arg(arg.as_str()?);
                    }
                    c.arg(String::from_utf8_lossy(&raw).as_ref());
                    Some(c)
                })
        };
        if let Some(ref mut c) = command {
            c.stdin(Stdio::piped()).stderr(Stdio::null());
            unsafe {
                c.pre_exec(|| {
                    if libc::setsid() < 0 {
                        Err(std::io::Error::last_os_error())
                    } else {
                        Ok(())
                    }
                });
            }
            let mut child = c.spawn()?;
            let end = std::time::Instant::now() + std::time::Duration::from_secs(2);
            if let Some(mut input) = child.stdin.take() {
                if kind == "claude" {
                    unsafe {
                        libc::fcntl(input.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK);
                    }
                    let mut offset = 0;
                    while offset < raw.len() && std::time::Instant::now() < end {
                        match input.write(&raw[offset..]) {
                            Ok(0) => break,
                            Ok(n) => offset += n,
                            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                                std::thread::sleep(std::time::Duration::from_millis(10))
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
            while child.try_wait()?.is_none() {
                if std::time::Instant::now() > end {
                    let _ = crate::transport::terminate(&mut child);
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn launch_settings_preserve_hooks_and_user_callback() {
        let dir = std::env::temp_dir().join(format!("eam-telemetry-{}", unique()));
        mkdir(&dir).unwrap();
        save(
            &dir.join("settings.json"),
            &json!({"statusLine":{"type":"command","command":"printf existing"}}),
        )
        .unwrap();
        let q = json!({"provider":"Claude","directory":dir,"args":["--settings",json!({"hooks":{"Stop":[]}}).to_string()]});
        let args = prepare(&dir, &q, &dir).unwrap();
        let settings: Value = serde_json::from_str(args[1].as_str().unwrap()).unwrap();
        assert_eq!(settings["hooks"]["Stop"], json!([]));
        assert!(settings["statusLine"]["command"]
            .as_str()
            .unwrap()
            .contains("telemetry claude"));
        assert_eq!(
            read_json(&dir.join("telemetry-config.json"), 65536).unwrap()["statusLine"]["command"],
            "printf existing"
        );
        let id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
        let args = prepare(
            &dir,
            &json!({"provider":"Codex","args":["resume",id]}),
            &dir,
        )
        .unwrap();
        assert_eq!(args[0], "resume");
        assert_eq!(
            read_json(&dir.join("telemetry.json"), 16384).unwrap()["id"],
            id
        );
        // Personal settings are read, never overwritten.
        assert_eq!(
            read_json(&dir.join("settings.json"), 65536).unwrap()["statusLine"]["command"],
            "printf existing"
        );
        fs::remove_dir_all(dir).unwrap();
    }
}
