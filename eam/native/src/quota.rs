//! Read-only account RPC. No threads or turns are created.
use crate::common::*;
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    os::unix::{io::AsRawFd, process::CommandExt},
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};
fn call(exe: &str, root: &Path, show_account: bool) -> Result<Value> {
    let mut command = Command::new(exe);
    command
        .args(["app-server", "--listen", "stdio://"])
        .env("CODEX_HOME", root)
        .env_remove("OPENAI_API_KEY")
        .env_remove("CODEX_API_KEY")
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() < 0 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(())
            }
        });
    }
    let mut child = command.spawn()?;
    let result = (|| {
        let input = child.stdin.as_mut().unwrap();
        writeln!(
            input,
            "{}",
            json!({"id":0,"method":"initialize","params":{"clientInfo":{"name":"eam_usage","version":"0.1.0"}}})
        )?;
        let mut output = child.stdout.take().unwrap();
        unsafe {
            libc::fcntl(output.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK);
        }
        let end = Instant::now() + Duration::from_millis(2500);
        let mut pending = Vec::new();
        let mut total = 0;
        let mut done = 0;
        let mut result = json!({"windows":[],"timestamp":now(),"state":"unavailable"});
        while Instant::now() < end {
            let mut b = [0u8; 8192];
            match output.read(&mut b) {
                Ok(0) => break,
                Ok(n) => {
                    total += n;
                    if total > 1024 * 1024 {
                        return Err(error("RPC output too large"));
                    }
                    pending.extend_from_slice(&b[..n]);
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10));
                    continue;
                }
                Err(e) => return Err(e.into()),
            }
            while let Some(pos) = pending.iter().position(|b| *b == b'\n') {
                let line: Vec<_> = pending.drain(..=pos).collect();
                let Ok(v) = serde_json::from_slice::<Value>(&line) else {
                    continue;
                };
                match v["id"].as_u64() {
                    Some(0) => {
                        if !v["error"].is_null() {
                            return Err(error("Initialization rejected"));
                        }
                        writeln!(input, "{}", json!({"method":"initialized"}))?;
                        for (id, method) in
                            [(1, "account/rateLimits/read"), (2, "account/usage/read")]
                        {
                            writeln!(input, "{}", json!({"id":id,"method":method}))?;
                        }
                        if show_account {
                            writeln!(
                                input,
                                "{}",
                                json!({"id":3,"method":"account/read","params":{"refreshToken":false}})
                            )?;
                        }
                    }
                    Some(1) => {
                        done += 1;
                        result["windows"] =
                            crate::telemetry::limits(&v["result"]["rateLimits"], true);
                        if v["result"].is_object() {
                            result["state"] = json!("available");
                        }
                    }
                    Some(2) => {
                        done += 1;
                        result["lifetime_tokens"] = v["result"]["summary"]["lifetimeTokens"]
                            .as_u64()
                            .map_or(Value::Null, |n| json!(n));
                        // Dates stay explicit; do not label an incomplete set as a full week.
                        if let Some(rows) = v["result"]["dailyUsageBuckets"].as_array() {
                            let mut daily = rows
                                .iter()
                                .take(366)
                                .filter_map(|row| {
                                    let date = row["startDate"].as_str()?;
                                    if date.len() != 10
                                        || !date.bytes().all(|b| b.is_ascii_digit() || b == b'-')
                                    {
                                        return None;
                                    }
                                    Some(json!({"date":date,"tokens":row["tokens"].as_u64()?}))
                                })
                                .collect::<Vec<_>>();
                            daily.sort_by(|a, b| b["date"].as_str().cmp(&a["date"].as_str()));
                            daily.truncate(14);
                            result["daily"] = json!(daily);
                        }
                    }
                    Some(3) => {
                        done += 1;
                        let a = &v["result"]["account"];
                        if a["type"] == "chatgpt" {
                            let safe = |key: &str| {
                                a[key]
                                    .as_str()
                                    .filter(|s| s.len() <= 254 && !s.chars().any(char::is_control))
                                    .map_or(Value::Null, |s| json!(s))
                            };
                            result["account"] = json!({"state":"local","source":"account/read","email":safe("email"),"plan":safe("planType")});
                        }
                    }
                    _ => (),
                }
                if done >= if show_account { 3 } else { 2 } {
                    return Ok(result);
                }
            }
        }
        Ok(result)
    })();
    let _ = crate::transport::terminate(&mut child);
    result
}
pub fn snapshot(path: &Path, metadata: &Value, show_account: bool) -> Value {
    let cache = path.join(if show_account {
        "provider-usage.json"
    } else {
        "provider-usage-private.json"
    });
    let previous = read_json(&cache, 65536).ok();
    if let Some(v) = &previous {
        if v["checked_at"]
            .as_f64()
            .or_else(|| v["timestamp"].as_f64())
            .is_some_and(|t| now() - t >= 0.0 && now() - t < 60.0)
        {
            return v.clone();
        }
    }
    let mut v = match (
        metadata["executable"].as_str(),
        metadata["auth_root"].as_str(),
    ) {
        (Some(exe), Some(root)) => call(exe, Path::new(root), show_account)
            .unwrap_or_else(|_| json!({"state":"unavailable","timestamp":now()})),
        _ => json!({"state":"unavailable","timestamp":now()}),
    };
    if v["state"] != "available" {
        if let Some(mut old) =
            previous.filter(|p| p["windows"].as_array().is_some_and(|a| !a.is_empty()))
        {
            old["refresh_error"] = json!(true);
            v = old;
        }
    }
    v["checked_at"] = json!(now());
    let _ = save(&cache, &v);
    v
}
