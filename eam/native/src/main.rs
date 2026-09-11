#[cfg(not(unix))]
compile_error!(
    "EAM native PTY currently requires macOS or Unix; Windows ConPTY is not implemented"
);
mod account;
mod activity;
mod common;
mod editor;
mod events;
mod history;
mod notes;
mod providers;
mod quota;
mod serve;
mod session;
mod telemetry;
mod transport;
mod usage;
use common::*;
use serde_json::json;
use std::{
    io::{Read, Seek, SeekFrom},
    path::Path,
};
fn run() -> Result<i32> {
    unsafe {
        libc::umask(0o077);
    }
    let a: Vec<String> = std::env::args().skip(1).collect();
    let cmd = a.first().map(String::as_str).unwrap_or("--version");
    match cmd {
        "--version" => {
            println!("eam-runtime {} protocol=1", env!("CARGO_PKG_VERSION"));
        }
        "manager" => {
            emit(&session::dispatch(
                a.get(1).ok_or_else(|| error("Missing action"))?,
                &request()?,
            )?)?;
        }
        "attach" => session::attach(&a[1..])?,
        "serve" => serve::run(&a[1..])?,
        "daemon" => {
            return transport::run(Path::new(a.get(1).ok_or_else(|| error("Missing session"))?))
        }
        "editor" => return editor::run(&a[1..]),
        "provider" => providers::run(&a[1..])?,
        "usage" => emit(&usage::dispatch(&request()?)?)?,
        "telemetry" => telemetry::run(&a[1..])?,
        "history" => emit(&history::dispatch(&request()?)?)?,
        "notify-claude" => {
            if let Ok(raw) = stdin_limit(1024 * 1024) {
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&raw) {
                    if let Some(k) = v["hook_event_name"].as_str() {
                        if ["Stop", "Notification", "PermissionRequest"].contains(&k) {
                            emit(
                                &json!({"terminalSequence":format!("\x1b]777;notify;Claude Code;{k}\x07")}),
                            )?;
                        }
                    }
                }
            }
        }
        "search-archive" => {
            if a.len() != 3 {
                return Err(error("search-archive FILE OFFSET"));
            }
            let needle = stdin_limit(4096)?;
            if needle.is_empty() {
                return Err(error("Empty query"));
            }
            let mut f = std::fs::File::open(&a[1])?;
            let size = f.metadata()?.len();
            let mut pos = a[2].parse::<u64>()?.min(size);
            f.seek(SeekFrom::Start(pos))?;
            let mut tail: Vec<u8> = Vec::new();
            let mut found = None;
            while pos < size {
                let mut b = vec![0; 65536usize.min((size - pos) as usize)];
                let n = f.read(&mut b)?;
                if n == 0 {
                    return Err(error("Archive shortened during search"));
                }
                b.truncate(n);
                let mut window = tail.clone();
                window.extend(&b);
                if let Some(at) = window.windows(needle.len()).position(|x| x == needle) {
                    found = Some(pos - tail.len() as u64 + at as u64);
                    break;
                }
                let keep = (needle.len() - 1).min(window.len());
                tail = window[window.len() - keep..].to_vec();
                pos += n as u64;
            }
            emit(&json!({"offset":found,"size":size}))?;
        }
        _ => return Err(error("Unknown native operation")),
    }
    Ok(0)
}
fn main() {
    match run() {
        Ok(code) => std::process::exit(code),
        Err(e) => {
            let _ = emit(&json!({"error":e.to_string()}));
            std::process::exit(1)
        }
    }
}
