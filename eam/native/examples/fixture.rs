//! Test-only fake CLI; omitted from the installed package.
use std::{
    io::{Read, Write},
    path::Path,
    process::Command,
};
fn write(b: &[u8]) {
    std::io::stdout().write_all(b).unwrap();
    std::io::stdout().flush().unwrap();
}
extern "C" fn resized(_: i32) {
    unsafe {
        let mut s: libc::winsize = std::mem::zeroed();
        libc::ioctl(0, libc::TIOCGWINSZ, &mut s);
        let text = s.ws_col.to_string();
        std::fs::write("width", text).unwrap();
    }
}
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    unsafe {
        let mut t = std::mem::zeroed();
        if libc::tcgetattr(0, &mut t) == 0 {
            libc::cfmakeraw(&mut t);
            libc::tcsetattr(0, libc::TCSANOW, &t);
        }
    }
    let mode = args.first().map(String::as_str).unwrap_or("echo");
    if mode == "terminal-env" {
        let values: std::collections::BTreeMap<_, _> =
            ["TERM", "TERMINFO", "TERM_PROGRAM", "COLORTERM"]
                .into_iter()
                .map(|key| (key, std::env::var(key).ok()))
                .collect();
        std::fs::write(&args[1], serde_json::to_vec(&values).unwrap()).unwrap();
        let _ = std::io::stdin().read(&mut [0u8; 1]);
        return;
    }
    if mode == "auth" {
        assert_eq!(args, ["auth", "status", "--json"]);
        for key in [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
        ] {
            assert!(std::env::var_os(key).is_none());
        }
        write(format!("{}\n", serde_json::json!({"loggedIn":true,
            "email": if std::env::var_os("CLAUDE_CONFIG_DIR").is_some() {"custom@example.test"} else {"default@example.test"},
            "subscriptionType":"max"})).as_bytes());
        return;
    }
    if mode == "app-server" {
        use serde_json::{json, Value};
        use std::io::BufRead;
        for line in std::io::stdin().lock().lines() {
            let v: Value = serde_json::from_str(&line.unwrap()).unwrap();
            let result = match v["method"].as_str().unwrap() {
                "initialize" => json!({}),
                "initialized" => continue,
                "account/read" => {
                    json!({"account":{"type":"chatgpt","email":"fixture@example.test","planType":"prolite"}})
                }
                "account/rateLimits/read" => {
                    json!({"rateLimits":{"primary":{"usedPercent":25,"windowDurationMins":300,"resetsAt":1800000000u64},"secondary":{"usedPercent":60,"windowDurationMins":10080}}})
                }
                "account/usage/read" => {
                    json!({"summary":{"lifetimeTokens":99999},"dailyUsageBuckets":[{"startDate":"2026-09-16","tokens":1234}]})
                }
                other => panic!("Unexpected non-read-only RPC: {other}"),
            };
            write(format!("{}\n", json!({"id":v["id"],"result":result})).as_bytes());
        }
        return;
    }
    let _usage_file = if mode == "usage-file" {
        Some(
            std::fs::OpenOptions::new()
                .append(true)
                .open(&args[1])
                .unwrap(),
        )
    } else {
        None
    };
    if mode == "flood" {
        write(&vec![b'x'; 100000]);
        return;
    }
    if mode == "notification" {
        std::thread::sleep(std::time::Duration::from_millis(200));
        for packet in [
            format!("\x1b]9;{} 한글 완료\x07", args[1]),
            format!("\x1b]777;notify;{};확인 요청\x1b\\", args[1]),
        ] {
            for chunk in packet.as_bytes().chunks(3) {
                write(chunk);
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(300));
        return;
    }
    if mode == "size" {
        unsafe {
            libc::signal(libc::SIGWINCH, resized as *const () as usize);
        }
        resized(0);
        write(b"READY");
        loop {
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
    }
    if mode == "exit" {
        write("한글 종료\r\n".as_bytes());
        std::thread::sleep(std::time::Duration::from_millis(300));
        return;
    }
    if mode == "replay" {
        let n = args[1].parse::<usize>().unwrap();
        write(&vec![b'a'; n - 3]);
        write(b"END");
        std::fs::write("ready", "done").unwrap();
    } else {
        write(b"\x1b[?2004hREADY\r\n");
    }
    let log = args.get(1);
    let mut pending = Vec::new();
    let mut pasted = false;
    let mut text = String::new();
    let mut b = [0];
    while std::io::stdin().read_exact(&mut b).is_ok() {
        if mode != "terminal" {
            if mode == "burst" && b[0] == b'b' {
                for _ in 0..512 {
                    write(&[b'x'; 65536]);
                }
                std::fs::write("ready", "done").unwrap();
                continue;
            }
            if b[0] == b'q' {
                if mode == "inherited-pty" {
                    use std::os::unix::process::CommandExt;
                    let mut command = Command::new("/bin/sleep");
                    command.arg("3");
                    unsafe {
                        command.pre_exec(|| {
                            libc::signal(libc::SIGHUP, libc::SIG_IGN);
                            Ok(())
                        });
                    }
                    let child = command.spawn().unwrap();
                    std::fs::write("child", child.id().to_string()).unwrap();
                }
                break;
            }
            write(&b);
            continue;
        }
        pending.push(b[0]);
        if pending.ends_with(b"\x1b[200~") {
            pending.clear();
            pasted = true;
        } else if pasted && pending.ends_with(b"\x1b[201~") {
            text = String::from_utf8(pending[..pending.len() - 6].to_vec()).unwrap();
            pending.clear();
            pasted = false;
            if let Some(log) = log {
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(log)
                    .unwrap();
                writeln!(f, "{}", serde_json::json!({"paste":text})).unwrap();
            }
            write(format!("PASTED {}\r\n", text.replace('\n', " | ")).as_bytes());
        } else if !pasted && b[0] == 7 {
            pending.clear();
            let file = Path::new(log.unwrap()).with_extension("editor.txt");
            std::fs::write(&file, &text).unwrap();
            // Exercise a CLI that spawns the editor without shell unquoting.
            let editor = std::env::var("VISUAL").unwrap();
            let mut words = editor.split_whitespace();
            assert!(Command::new(words.next().unwrap())
                .args(words)
                .arg(&file)
                .status()
                .unwrap()
                .success());
            text = std::fs::read_to_string(file).unwrap();
            write(format!("EDITED {}\r\n", text.replace('\n', " | ")).as_bytes());
        } else if !pasted && b[0] == 3 {
            break;
        } else if !pasted && b[0] == b'\r' {
            pending.clear();
            write(b"ENTER\r\n")
        }
    }
}
