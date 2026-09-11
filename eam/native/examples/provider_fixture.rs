//! Fake official-CLI protocols for native adapter tests only.
use serde_json::{json, Value};
use std::io::{BufRead, Read, Write};
fn emit(v: Value) {
    println!("{v}");
    std::io::stdout().flush().unwrap();
}
fn record(v: Value) {
    if let Ok(path) = std::env::var("EMACS_AI_FIXTURE_REQUESTS") {
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap();
        writeln!(f, "{v}").unwrap();
    }
}
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|x| x == "auth") {
        emit(json!({"loggedIn":true,"authMethod":"claude.ai","subscriptionType":"fixture"}));
        return;
    }
    if args.first().is_some_and(|x| x == "app-server") {
        for line in std::io::stdin().lock().lines() {
            let m: Value = serde_json::from_str(&line.unwrap()).unwrap();
            record(m.clone());
            let method = m["method"].as_str().unwrap_or("");
            if method.is_empty() || method == "initialized" {
                continue;
            }
            let result = match method {
                "initialize" => json!({}),
                "account/read" => {
                    json!({"account":{"type":std::env::var("EMACS_AI_FIXTURE_AUTH").unwrap_or("chatgpt".into()),"planType":"fixture"}})
                }
                "thread/start" | "thread/resume" => json!({"thread":{"id":"fixture-codex"}}),
                _ => json!({}),
            };
            emit(json!({"id":m["id"],"result":result}));
            if method == "turn/start" {
                emit(json!({"id":90,"method":"item/commandExecution/requestApproval","params":{}}));
                emit(
                    json!({"method":"item/agentMessage/delta","params":{"itemId":"a","delta":"한글🙂"}}),
                );
                emit(
                    json!({"method":"item/completed","params":{"item":{"type":"agentMessage","id":"a","text":"한글🙂"}}}),
                );
                emit(json!({"method":"turn/completed","params":{"turn":{"status":"completed"}}}));
            }
        }
    } else {
        let mut prompt = String::new();
        std::io::stdin().read_to_string(&mut prompt).unwrap();
        record(json!({"prompt":prompt,"args":args}));
        emit(json!({"type":"system","subtype":"init","session_id":"fixture-claude"}));
        match std::env::var("EMACS_AI_FIXTURE_MODE")
            .unwrap_or_default()
            .as_str()
        {
            "eof" => return,
            "oversize" => {
                std::io::stdout()
                    .write_all(&vec![b'x'; 4 * 1024 * 1024 + 16384])
                    .unwrap();
                return;
            }
            "wait" => {
                let child = std::process::Command::new("/bin/sleep")
                    .arg("60")
                    .spawn()
                    .unwrap();
                if let Ok(p) = std::env::var("EMACS_AI_FIXTURE_CHILD") {
                    std::fs::write(p, child.id().to_string()).unwrap();
                }
                loop {
                    std::thread::sleep(std::time::Duration::from_secs(1));
                }
            }
            _ => (),
        }
        emit(json!({"type":"stream_event","event":{"type":"message_start"}}));
        emit(
            json!({"type":"stream_event","event":{"delta":{"type":"text_delta","text":"한글🙂"}}}),
        );
        emit(json!({"type":"assistant","message":{"content":[{"type":"text","text":"한글🙂"}]}}));
        emit(json!({"type":"result","is_error":false}));
    }
}
