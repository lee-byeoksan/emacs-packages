use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Write},
    os::unix::{fs::PermissionsExt, net::UnixStream},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
const BIN: &str = env!("CARGO_BIN_EXE_eam-runtime");
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = PathBuf::from(format!(
            "/tmp/eam-rtest-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&p).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o700)).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn run(args: &[&str], input: &[u8]) -> std::process::Output {
    let mut p = Command::new(BIN)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    p.stdin.take().unwrap().write_all(input).unwrap();
    p.wait_with_output().unwrap()
}
fn request(action: &str, v: Value) -> Value {
    let o = run(&["manager", action], &serde_json::to_vec(&v).unwrap());
    assert!(
        o.status.success(),
        "{} {}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    );
    serde_json::from_slice(&o.stdout).unwrap()
}
fn history(v: Value) -> Value {
    let o = run(&["history"], &serde_json::to_vec(&v).unwrap());
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stdout));
    serde_json::from_slice(&o.stdout).unwrap()
}
fn fixture(name: &str) -> PathBuf {
    Path::new(BIN).parent().unwrap().join("examples").join(name)
}
fn wait(test: impl FnMut() -> bool) {
    wait_for(Duration::from_secs(5), test);
}
fn wait_for(timeout: Duration, mut test: impl FnMut() -> bool) {
    let end = Instant::now() + timeout;
    while Instant::now() < end {
        if test() {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(test(), "Timed out");
}
struct Session {
    path: PathBuf,
    runtime: PathBuf,
    _temp: Temp,
}
impl Session {
    fn new(backend: &str, args: Value, record: bool) -> Self {
        Self::options(backend, args, record, false)
    }
    fn options(backend: &str, args: Value, record: bool, temporary: bool) -> Self {
        let tmp = Temp::new();
        let path = tmp.0.join("session");
        let v = request(
            "start",
            json!({"session":path,"provider":"Fake","backend":backend,"executable":fixture("fixture"),"args":args,"directory":tmp.0,"raw_recording":record,"temporary":temporary}),
        );
        Self {
            path,
            runtime: v["runtime"].as_str().unwrap().into(),
            _temp: tmp,
        }
    }
    fn status(&self) -> Value {
        request("inspect", json!({"session":self.path}))["status"].clone()
    }
    fn connect(&self) -> UnixStream {
        let c = UnixStream::connect(self.runtime.join("socket")).unwrap();
        c.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        c
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        let _ = run(
            &["manager", "stop"],
            &serde_json::to_vec(&json!({"session":self.path})).unwrap(),
        );
        let _ = fs::remove_dir_all(&self.runtime);
    }
}
fn packet(k: u8, b: &[u8]) -> Vec<u8> {
    let mut p = vec![k];
    p.extend(&(b.len() as u32).to_be_bytes());
    p.extend(b);
    p
}
fn next(c: &mut UnixStream) -> (u8, Vec<u8>) {
    let mut h = [0; 5];
    c.read_exact(&mut h).unwrap();
    let n = u32::from_be_bytes(h[1..5].try_into().unwrap()) as usize;
    assert!(n <= 256 * 1024);
    let mut b = vec![0; n];
    c.read_exact(&mut b).unwrap();
    (h[0], b)
}
fn until(c: &mut UnixStream, end: &[u8]) -> Vec<u8> {
    let mut output = Vec::new();
    while !output.windows(end.len()).any(|x| x == end) {
        let (k, b) = next(c);
        if k == b'O' {
            output.extend(b)
        }
    }
    output
}
#[test]
fn pty_lifetime_bytes_resize_and_exit() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let pid = s.status()["recorder_pid"].clone();
    let input = "한글🙂\x1b[200~code\nlog\x1b[201~".as_bytes();
    for byte in packet(b'I', input) {
        c.write_all(&[byte]).unwrap();
    }
    assert_eq!(until(&mut c, input), input);
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    assert_eq!(s.status()["recorder_pid"], pid);
    let mut c = s.connect();
    until(&mut c, input);
    let mut size = Vec::new();
    for n in [31u16, 97, 0, 0] {
        size.extend(n.to_ne_bytes());
    }
    c.write_all(&packet(b'R', &size)).unwrap();
    c.write_all(&packet(b'I', b"q")).unwrap();
    wait(|| s.status()["state"] == "stopped");
    assert_eq!(s.status()["exit_code"], 0);
    assert!(!s.path.join("output.ansi").exists());
}
#[test]
fn five_mib_replay_boundary() {
    let s = Session::new(
        "pty",
        json!(["replay", (5 * 1024 * 1024).to_string()]),
        false,
    );
    wait(|| s._temp.0.join("ready").exists());
    wait(|| s.status()["replay_bytes"] == 5 * 1024 * 1024);
    let mut c = s.connect();
    let b = until(&mut c, b"END");
    assert_eq!(b.len(), 5 * 1024 * 1024);
    assert_eq!(&b[..b.len() - 3], vec![b'a'; 5 * 1024 * 1024 - 3]);
    c.write_all(&packet(b'I', b"x")).unwrap();
    until(&mut c, b"x");
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    let mut c = s.connect();
    let (k, b) = next(&mut c);
    assert_eq!(k, b'H');
    assert_eq!(
        serde_json::from_slice::<Value>(&b).unwrap()["replay_complete"],
        false
    );
}
#[test]
fn detached_natural_exit() {
    for backend in ["pty"] {
        let s = Session::new(backend, json!(["exit"]), true);
        wait(|| s.status()["state"] == "stopped");
        assert!(
            String::from_utf8(fs::read(s.path.join("output.ansi")).unwrap())
                .unwrap()
                .contains("한글 종료")
        );
    }
}
#[test]
fn notify_and_cross_chunk_search() {
    let o = run(&["notify-claude"], br#"{"hook_event_name":"Stop"}"#);
    assert!(o.status.success());
    let v: Value = serde_json::from_slice(&o.stdout).unwrap();
    assert!(v["terminalSequence"]
        .as_str()
        .unwrap()
        .contains("Claude Code;Stop"));
    let o = run(&["notify-claude"], b"bad");
    assert!(o.status.success() && o.stdout.is_empty());
    let t = Temp::new();
    let file = t.0.join("raw");
    let mut b = vec![b'a'; 65535];
    b.extend("한글needle".as_bytes());
    fs::write(&file, &b).unwrap();
    let o = run(
        &["search-archive", file.to_str().unwrap(), "0"],
        "한글".as_bytes(),
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&o.stdout).unwrap()["offset"],
        65535
    );
    assert_eq!(fs::read(file).unwrap(), b);
}
#[test]
fn native_history_filter_pages_and_readonly_sqlite() {
    let t = Temp::new();
    let projects = t.0.join("projects/project");
    fs::create_dir_all(&projects).unwrap();
    let path = projects.join("conversation.jsonl");
    let text = "한글 코드\n".repeat(2000);
    let records = [
        json!({"type":"user","sessionId":"conversation","cwd":t.0,"message":{"role":"user","content":text}}),
        json!({"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"hidden"},{"type":"text","text":"끝"}]}}),
        json!({"type":"user","isMeta":true,"message":{"role":"user","content":"hidden"}}),
    ];
    let raw = records.iter().map(|v| format!("{v}\n")).collect::<String>() + "{\"partial\":";
    fs::write(&path, &raw).unwrap();
    let entry = json!({"provider":"Claude","id":"conversation","path":path});
    let expected = format!("\n## 사용자\n\n{text}\n\n## 응답\n\n끝\n");
    let mut rebuilt = String::new();
    for offset in (0..expected.chars().count()).step_by(1024) {
        let p = history(json!({"action":"page","entry":entry,"offset":offset,"limit":1024}));
        assert!(p["text"].as_str().unwrap().chars().count() <= 1024);
        rebuilt.push_str(p["text"].as_str().unwrap());
    }
    assert_eq!(rebuilt, expected);
    assert_eq!(fs::read_to_string(&path).unwrap(), raw);
    let list = history(json!({"action":"list","provider":"Claude","root":t.0,"directory":t.0}));
    assert_eq!(list["entries"][0]["id"], "conversation");
    let db = t.0.join("thread_history_1.sqlite");
    let c = rusqlite::Connection::open(&db).unwrap();
    c.execute_batch("CREATE TABLE thread_items(thread_id,rollout_ordinal,item_type,item_json)")
        .unwrap();
    c.execute(
        "INSERT INTO thread_items VALUES('one',1,'agentMessage',?)",
        [json!({"text":"SQLite 응답"}).to_string()],
    )
    .unwrap();
    drop(c);
    let before = fs::read(&db).unwrap();
    let entry = json!({"provider":"Codex","id":"one","mode":"paginated","history_db":db});
    let p = history(json!({"action":"page","entry":entry}));
    assert!(p["text"].as_str().unwrap().contains("SQLite 응답"));
    assert_eq!(fs::read(db).unwrap(), before);
}
#[test]
fn both_readonly_provider_adapters_use_native_fixture() {
    let t = Temp::new();
    for provider in ["claude", "codex"] {
        let state = t.0.join(format!("{provider}.json"));
        for prompt in ["입력 그대로\n$(echo unsafe) `literal`", "두 번째"] {
            let mut p = Command::new(BIN)
                .args([
                    "provider",
                    "--provider",
                    provider,
                    "--executable",
                    fixture("provider_fixture").to_str().unwrap(),
                    "--cwd",
                    t.0.to_str().unwrap(),
                    "--state",
                    state.to_str().unwrap(),
                    "--prefix",
                    t.0.join(provider).to_str().unwrap(),
                ])
                .env("CODEX_HOME", &t.0)
                .env("EMACS_AI_FIXTURE_REQUESTS", t.0.join("requests"))
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .unwrap();
            p.stdin
                .take()
                .unwrap()
                .write_all(prompt.as_bytes())
                .unwrap();
            let o = p.wait_with_output().unwrap();
            assert!(
                o.status.success(),
                "{} {}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
            let text = String::from_utf8(o.stdout).unwrap();
            assert_eq!(text.matches("한글🙂").count(), 1);
            let v: Value = serde_json::from_slice(&fs::read(&state).unwrap()).unwrap();
            assert_eq!(v["backend_id"], format!("fixture-{provider}"));
            assert_eq!(v["status"], "completed");
        }
    }
    let requests = fs::read_to_string(t.0.join("requests")).unwrap();
    assert!(
        requests.contains("--resume")
            && requests.contains("thread/resume")
            && requests.contains("$(echo unsafe)")
    );
}

fn adapter(temp: &Temp, provider: &str, mode: &str) -> std::process::Child {
    let mut c = Command::new(BIN)
        .args([
            "provider",
            "--provider",
            provider,
            "--executable",
            fixture("provider_fixture").to_str().unwrap(),
            "--cwd",
            temp.0.to_str().unwrap(),
            "--state",
            temp.0.join("state").to_str().unwrap(),
            "--prefix",
            temp.0.join("output").to_str().unwrap(),
        ])
        .env("CODEX_HOME", &temp.0)
        .env("EMACS_AI_FIXTURE_MODE", mode)
        .env("EMACS_AI_FIXTURE_AUTH", "apiKey")
        .env("EMACS_AI_FIXTURE_CHILD", temp.0.join("child"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    c.stdin
        .take()
        .unwrap()
        .write_all("실패 시험".as_bytes())
        .unwrap();
    c
}
#[test]
fn adapter_failure_and_cancellation_do_not_reuse_partial_session() {
    for (provider, mode) in [("claude", "eof"), ("claude", "oversize"), ("codex", "auth")] {
        let t = Temp::new();
        let mut c = adapter(&t, provider, mode);
        // This checks the frame bound, not throughput on a debug build.
        let timeout = if mode == "oversize" { 30 } else { 5 };
        wait_for(Duration::from_secs(timeout), || {
            c.try_wait().unwrap().is_some()
        });
        let o = c.wait_with_output().unwrap();
        assert!(!o.status.success());
        let v: Value = serde_json::from_slice(&fs::read(t.0.join("state")).unwrap()).unwrap();
        assert_eq!(v["status"], "failed");
        assert!(v["backend_id"].is_null());
    }
    let t = Temp::new();
    let mut c = adapter(&t, "claude", "wait");
    wait(|| t.0.join("child").exists());
    unsafe {
        libc::kill(c.id() as i32, libc::SIGTERM);
    }
    wait(|| c.try_wait().unwrap().is_some());
    assert!(!c.wait_with_output().unwrap().status.success());
    let v: Value = serde_json::from_slice(&fs::read(t.0.join("state")).unwrap()).unwrap();
    assert_eq!(v["status"], "cancelled");
    assert!(v["backend_id"].is_null());
    let pid: i32 = fs::read_to_string(t.0.join("child"))
        .unwrap()
        .parse()
        .unwrap();
    wait(|| unsafe { libc::kill(pid, 0) } < 0);
}
#[test]
fn slow_consumer_backpressure_preserves_connection_and_every_byte() {
    let s = Session::new("pty", json!(["burst"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY\r\n");
    c.write_all(&packet(b'I', b"b")).unwrap();
    wait(|| s.status()["output_backpressured"] == true);
    let v = s.status();
    assert_eq!(v["attached_clients"], 1);
    assert_eq!(v["disconnect_count"], 0);
    assert!(v["output_queue_peak_bytes"].as_u64().unwrap() <= 256 * 1024);
    assert!(!s._temp.0.join("ready").exists());
    let mut received = 0;
    while received < 32 * 1024 * 1024 {
        let (k, b) = next(&mut c);
        assert_eq!(k, b'O');
        assert!(b.iter().all(|x| *x == b'x'));
        received += b.len();
    }
    assert_eq!(received, 32 * 1024 * 1024);
    wait(|| s._temp.0.join("ready").exists());
    c.write_all(&packet(b'I', "한글".as_bytes())).unwrap();
    until(&mut c, "한글".as_bytes());
    let v = s.status();
    assert_eq!(v["attached_clients"], 1);
    assert_eq!(v["disconnect_count"], 0);
    assert!(v["backpressure_seconds"].as_f64().unwrap() > 0.0);
}

#[test]
fn detach_and_stop_remain_responsive_under_backpressure() {
    for stop in [false, true] {
        let s = Session::new("pty", json!(["burst"]), false);
        let mut c = s.connect();
        let initial = until(&mut c, b"READY\r\n").len();
        c.write_all(&packet(b'I', b"b")).unwrap();
        wait(|| s.status()["output_backpressured"] == true);
        let began = Instant::now();
        if stop {
            request("stop", json!({"session":s.path}));
            wait(|| s.status()["state"] == "stopped");
            assert!(began.elapsed() < Duration::from_secs(2));
        } else {
            drop(c);
            wait(|| s._temp.0.join("ready").exists());
            wait(|| s.status()["output_bytes"] == initial + 32 * 1024 * 1024);
            let v = s.status();
            assert_eq!(v["attached_clients"], 0);
            assert_eq!(v["replay_bytes"], 0);
            assert_eq!(v["disconnect_count"], 1);
            assert!(v["last_disconnect_reason"].as_str().is_some());
            let mut c = s.connect();
            c.write_all(&packet(b'I', "한글".as_bytes())).unwrap();
            until(&mut c, "한글".as_bytes());
        }
    }
}

#[test]
fn cli_exit_with_slave_holder_finishes_attached_and_detached() {
    for attached in [true, false] {
        let s = Session::new("pty", json!(["inherited-pty"]), false);
        let mut c = s.connect();
        until(&mut c, b"READY");
        let began = Instant::now();
        c.write_all(&packet(b'I', b"q")).unwrap();
        wait(|| s._temp.0.join("child").exists()); // CLI consumed quit before detach.
        let mut attached_client = if attached {
            Some(c)
        } else {
            drop(c);
            None
        };
        // The fixture helper ignores HUP and holds the slave for three seconds.
        // The CLI session must close sooner without signalling that helper.
        wait(|| s.status()["state"] == "stopped");
        assert!(began.elapsed() < Duration::from_secs(2));
        assert_eq!(s.status()["exit_code"], 0);
        if let Some(ref mut c) = attached_client {
            let mut b = Vec::new();
            c.read_to_end(&mut b).unwrap();
        }
        let pid: i32 = fs::read_to_string(s._temp.0.join("child"))
            .unwrap()
            .parse()
            .unwrap();
        wait(|| unsafe { libc::kill(pid, 0) } < 0);
        assert!(!s.runtime.join("socket").exists());
    }
}

#[test]
fn unsupported_backend_and_retired_operations_are_rejected() {
    let t = Temp::new();
    let path = t.0.join("session");
    let o = run(
        &["manager", "start"],
        &serde_json::to_vec(&json!({
            "session":path,"provider":"Fake","backend":"retired",
            "executable":fixture("fixture"),"args":["echo"],"directory":t.0
        }))
        .unwrap(),
    );
    assert!(!o.status.success());
    assert!(!path.exists());
    for command in ["record", "reap", "reap-worker"] {
        let o = run(&[command], b"");
        assert!(!o.status.success());
        assert!(String::from_utf8_lossy(&o.stdout).contains("Unknown native operation"));
    }
}

#[test]
fn session_names_and_activity_survive_detach_and_exit() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY\r\n");
    let initial = s.status()["last_output_at"].as_f64().unwrap();
    assert!(initial > 0.0);
    let original = fs::read(s.path.join("session.json")).unwrap();
    let renamed = request("rename", json!({"session":s.path,"name":"  한글 작업  "}));
    assert_eq!(renamed["name"], "한글 작업");
    assert!(renamed["created_at"].as_f64().unwrap() > 0.0);
    assert_eq!(original, fs::read(s.path.join("session.json")).unwrap());
    assert_eq!(
        fs::metadata(s.path.join("display.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    for name in ["x".repeat(129), "invalid\nname".into()] {
        let o = run(
            &["manager", "rename"],
            &serde_json::to_vec(&json!({"session":s.path,"name":name})).unwrap(),
        );
        assert!(!o.status.success());
    }
    std::thread::sleep(Duration::from_millis(15));
    c.write_all(&packet(b'I', "한글".as_bytes())).unwrap();
    until(&mut c, "한글".as_bytes());
    wait(|| s.status()["last_input_at"].as_f64().unwrap() > initial);
    let output = s.status()["last_output_at"].as_f64().unwrap();
    assert!(output > initial);
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    let live = request("list-live", json!({"root":s._temp.0}));
    assert_eq!(live["sessions"][0]["name"], "한글 작업");
    assert_eq!(live["sessions"][0]["last_output_at"], output);
    let mut c = s.connect();
    until(&mut c, "한글".as_bytes());
    // Replaying output does not manufacture a new activity timestamp.
    assert_eq!(s.status()["last_output_at"], output);
    c.write_all(&packet(b'I', b"q")).unwrap();
    wait(|| s.status()["state"] == "stopped");
    let info = request("inspect", json!({"session":s.path}));
    assert_eq!(info["metadata"]["name"], "한글 작업");
    assert!(info["metadata"]["last_input_at"].as_f64().unwrap() > initial);
    assert_eq!(
        request("rename", json!({"session":s.path,"name":""}))["name"],
        ""
    );
}

#[test]
fn detached_notification_receipts_are_persistent_and_snapshot_bounded() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY\r\n");
    c.write_all(&packet(b'I', b"\x1b]9;notice-one\x07"))
        .unwrap();
    until(&mut c, b"notice-one\x07");
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    wait(|| request("inspect", json!({"session":s.path}))["metadata"]["notifications"]["seq"] == 1);
    let live = request("list-live", json!({"root":s._temp.0}));
    assert_eq!(live["sessions"][0]["notifications"]["unread"], true);
    let mut c = s.connect();
    until(&mut c, b"notice-one\x07");
    c.write_all(&packet(b'I', b"\x1b]9;notice-two\x07"))
        .unwrap();
    until(&mut c, b"notice-two\x07");
    wait(|| request("inspect", json!({"session":s.path}))["metadata"]["notifications"]["seq"] == 2);
    let ack = request("acknowledge", json!({"session":s.path,"seq":1}));
    assert_eq!(ack["unread"], true);
    assert_eq!(ack["read_seq"], 1);
    let ack = request("acknowledge", json!({"session":s.path,"seq":2}));
    assert_eq!(ack["unread"], false);
    // An older display cannot move the read cursor backwards by acknowledging.
    assert_eq!(
        request("acknowledge", json!({"session":s.path,"seq":1}))["read_seq"],
        2
    );
    assert_eq!(
        request(
            "acknowledge",
            json!({"session":s.path,"seq":2,"unread":true})
        )["unread"],
        true
    );
    request("acknowledge", json!({"session":s.path,"seq":2}));
    let o = run(
        &["manager", "acknowledge"],
        &serde_json::to_vec(&json!({"session":s.path,"seq":3})).unwrap(),
    );
    assert!(!o.status.success());
    c.write_all(&packet(b'I', b"q")).unwrap();
    wait(|| s.status()["state"] == "stopped");
    assert_eq!(
        request("inspect", json!({"session":s.path}))["metadata"]["notifications"]["unread"],
        false
    );
}

fn usage(mut q: Value) -> Value {
    q["account"] = json!(false);
    let o = run(&["usage"], &serde_json::to_vec(&q).unwrap());
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stdout));
    serde_json::from_slice(&o.stdout).unwrap()
}
#[test]
fn usage_snapshots_keep_provider_semantics_and_identity() {
    let temp = Temp::new();
    let path = temp.0.join("usage.jsonl");
    let codex = json!({"provider":"Codex","id":"thread-one","path":path,"directory":temp.0});
    let records = [
        json!({"type":"session_meta","payload":{"id":"thread-one","cwd":temp.0}}),
        json!({"type":"turn_context","payload":{"model":"test-model"}}),
        json!({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1000,"output_tokens":100,"cached_input_tokens":800,"total_tokens":1100},"last_token_usage":{"total_tokens":500},"model_context_window":2000}}}),
        json!({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1500,"output_tokens":200,"cached_input_tokens":900,"total_tokens":1700},"last_token_usage":{"total_tokens":700},"model_context_window":2000}}}),
    ];
    fs::write(
        &path,
        records.iter().map(|v| format!("{v}\n")).collect::<String>() + "{\"partial\":",
    )
    .unwrap();
    let result = usage(json!({"action":"snapshot","entry":codex,"root":temp.0}));
    assert_eq!(result["scope"], "total");
    assert_eq!(result["total"], 1700); // latest cumulative record, never summed
    assert_eq!(result["cached"], 900);
    assert_eq!(result["context_used"], 700);
    assert_eq!(result["model"], "test-model");
    let bad = run(&["usage"], &serde_json::to_vec(&json!({"action":"snapshot","root":temp.0,"entry":{"provider":"Codex","id":"another-thread","path":path}})).unwrap());
    assert!(!bad.status.success());
    let claude = json!({"provider":"Claude","id":"claude-one","path":path,"directory":temp.0});
    let message = |id: &str, input, side| json!({"type":"assistant","sessionId":id,"isSidechain":side,"message":{"model":"claude-test","usage":{"input_tokens":input,"output_tokens":30,"cache_read_input_tokens":900,"cache_creation_input_tokens":50}}});
    fs::write(
        &path,
        format!(
            "{}\n{}\n{}\n{}\n",
            message("claude-one", 100, false),
            message("claude-one", 200, false),
            message("claude-one", 999, true),
            message("other", 999, false)
        ),
    )
    .unwrap();
    let result = usage(json!({"action":"snapshot","entry":claude,"root":temp.0}));
    assert_eq!(result["scope"], "last");
    assert_eq!(result["input"], 200);
    assert_eq!(result["output"], 30);
    assert_eq!(result["cache_write"], 50);
    assert!(result["total"].is_null());
    // No record inside the bounded tail means unknown, never fabricated zero.
    let mut large = format!("{}\n", message("claude-one", 200, false));
    large.push_str(&" ".repeat(2 * 1024 * 1024));
    fs::write(&path, large).unwrap();
    assert_eq!(
        usage(json!({"action":"snapshot","entry":claude,"root":temp.0}))["state"],
        "unavailable"
    );
}
#[test]
fn usage_paginated_saved_counter_is_readonly() {
    let temp = Temp::new();
    let path = temp.0.join("state_5.sqlite");
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE threads(id TEXT,cwd TEXT,tokens_used INTEGER);")
        .unwrap();
    db.execute(
        "INSERT INTO threads VALUES ('exact', ?, 4567)",
        [temp.0.to_str().unwrap()],
    )
    .unwrap();
    drop(db);
    let before = fs::read(&path).unwrap();
    let result = usage(
        json!({"action":"snapshot","root":temp.0,"entry":{"provider":"Codex","id":"exact","directory":temp.0}}),
    );
    assert_eq!(result["saved"], 4567);
    assert_eq!(result["scope"], "saved");
    assert!(result["input"].is_null());
    assert_eq!(before, fs::read(&path).unwrap());
}

#[test]
fn usage_discovers_live_writer_without_manual_selection() {
    let root = Temp::new();
    let transcript = root.0.join("conversation.jsonl");
    fs::write(&transcript, "").unwrap();
    let s = Session::new("pty", json!(["usage-file", transcript]), false);
    let mut c = s.connect();
    until(&mut c, b"READY\r\n");
    let mut metadata: Value =
        serde_json::from_slice(&fs::read(s.path.join("session.json")).unwrap()).unwrap();
    metadata["provider"] = json!("Claude");
    metadata["auth_root"] = json!(root.0);
    fs::write(
        s.path.join("session.json"),
        serde_json::to_vec(&metadata).unwrap(),
    )
    .unwrap();
    fs::write(&transcript,format!("{}\n",json!({"sessionId":"exact-id","cwd":s._temp.0,"type":"assistant","message":{"usage":{"input_tokens":123,"output_tokens":45}}}))).unwrap();
    let result = usage(json!({"action":"poll","session":s.path,"root":root.0}));
    assert_eq!(result["source"], "automatic");
    assert_eq!(result["input"], 123);
    c.write_all(&packet(b'I', b"q")).unwrap();
    wait(|| s.status()["state"] == "stopped");
    let result = usage(json!({"action":"poll","session":s.path,"root":root.0}));
    assert_eq!(result["state"], "waiting");
    assert!(!s.path.join("usage-entry.json").exists());
}

#[test]
fn account_uses_launch_root_without_usage_or_conversation() {
    let temp = Temp::new();
    let session = temp.0.join("session");
    let auth = temp.0.join("auth");
    fs::create_dir(&session).unwrap();
    fs::set_permissions(&session, fs::Permissions::from_mode(0o700)).unwrap();
    fs::create_dir(&auth).unwrap();
    fs::write(session.join("session.json"),serde_json::to_vec(&json!({"version":1,"id":"a".repeat(32),"stopped":true,"provider":"Claude","directory":temp.0,"auth_root":auth})).unwrap()).unwrap();
    fs::write(
        auth.join(".claude.json"),
        serde_json::to_vec(&json!({"oauthAccount":{"emailAddress":"launch@example.test"}}))
            .unwrap(),
    )
    .unwrap();
    let mut q = json!({"session":session,"root":"/missing/history","usage":false,"account":true});
    let o = run(&["usage"], &serde_json::to_vec(&q).unwrap());
    assert!(o.status.success());
    let v: Value = serde_json::from_slice(&o.stdout).unwrap();
    assert_eq!(v["state"], "disabled");
    assert_eq!(v["account"]["email"], "launch@example.test");
    assert_eq!(v["account"]["root_source"], "launch");
    q["account"] = json!(false);
    let o = run(&["usage"], &serde_json::to_vec(&q).unwrap());
    assert!(o.status.success());
    assert!(!String::from_utf8(o.stdout)
        .unwrap()
        .contains("launch@example.test"));
    assert!(!session.join("usage-entry.json").exists());
}

#[test]
fn telemetry_is_automatic_bounded_and_tracks_conversation_changes() {
    let temp = Temp::new();
    fs::write(temp.0.join("session.json"),serde_json::to_vec(&json!({"version":1,"id":"a".repeat(32),"stopped":true,"provider":"Claude","directory":temp.0})).unwrap()).unwrap();
    for id in ["first", "after-clear"] {
        let payload = json!({"session_id":id,"transcript_path":"/not/read","secret":"SECRET",
            "model":{"display_name":"Opus"},"context_window":{"total_input_tokens":1200,"total_output_tokens":300,"used_percentage":42},
            "rate_limits":{"seven_day":{"used_percentage":60,"resets_at":1800000000u64}}});
        let out = run(
            &["telemetry", "claude", temp.0.to_str().unwrap()],
            &serde_json::to_vec(&payload).unwrap(),
        );
        assert!(out.status.success(), "{:?}", out);
        assert!(out.stdout.is_empty());
        let value = usage(json!({"action":"poll","session":temp.0,"root":temp.0}));
        assert_eq!(value["id"], id);
        assert_eq!(value["input"], 1200);
        assert_eq!(value["context_percent"], 42);
        assert_eq!(value["provider_usage"]["windows"][0]["minutes"], 10080);
        assert!(
            !String::from_utf8(fs::read(temp.0.join("telemetry.json")).unwrap())
                .unwrap()
                .contains("SECRET")
        );
        assert!(!temp.0.join("usage-entry.json").exists());
    }
}

#[test]
fn provider_usage_without_conversation_and_codex_notification_binding() {
    let temp = Temp::new();
    let root = temp.0.join("codex");
    fs::create_dir(&root).unwrap();
    let db = rusqlite::Connection::open(root.join("state_5.sqlite")).unwrap();
    db.execute_batch(
        "CREATE TABLE threads(id TEXT,cwd TEXT,rollout_path TEXT,tokens_used INTEGER)",
    )
    .unwrap();
    db.execute(
        "INSERT INTO threads VALUES('notified',?,NULL,12345)",
        [temp.0.to_str().unwrap()],
    )
    .unwrap();
    fs::write(temp.0.join("session.json"),serde_json::to_vec(&json!({"version":1,"id":"a".repeat(32),"stopped":true,"provider":"Codex","directory":temp.0,"auth_root":root,"executable":fixture("fixture"),"telemetry":true})).unwrap()).unwrap();
    let q = json!({"action":"poll","session":temp.0,"root":root,"account":true});
    let o = run(&["usage"], &serde_json::to_vec(&q).unwrap());
    assert!(o.status.success());
    let v: Value = serde_json::from_slice(&o.stdout).unwrap();
    assert_eq!(v["state"], "waiting");
    assert_eq!(v["provider_usage"]["windows"][1]["used"], 60);
    assert_eq!(v["account"]["plan"], "prolite");
    assert_eq!(v["provider_usage"]["lifetime_tokens"], 99999);
    let payload =
        json!({"type":"agent-turn-complete","thread-id":"notified","input-messages":["SECRET"]})
            .to_string();
    assert!(run(
        &["telemetry", "codex", temp.0.to_str().unwrap(), &payload],
        b""
    )
    .status
    .success());
    let v = usage(q);
    assert_eq!(v["saved"], 12345);
    assert!(v["account"].is_null());
    assert!(!v.to_string().contains("fixture@example.test"));
    assert!(!fs::read_to_string(temp.0.join("telemetry.json"))
        .unwrap()
        .contains("SECRET"));
}

#[test]
fn pending_notes_survive_detach_and_cli_exit() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    request(
        "note-save",
        json!({"session":s.path,"text":"다음에 할 일\n테스트"}),
    );
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    assert_eq!(
        request("note-read", json!({"session":s.path}))["text"],
        "다음에 할 일\n테스트"
    );
    let mut c = s.connect();
    until(&mut c, b"READY");
    c.write_all(&packet(b'I', b"q")).unwrap();
    wait(|| s.status()["state"] == "stopped");
    assert!(s.path.join("pending-note.json").exists());
    assert_eq!(
        request("note-read", json!({"session":s.path}))["text"],
        "다음에 할 일\n테스트"
    );
    request("note-save", json!({"session":s.path,"text":"late save"}));
    assert_eq!(
        request("note-read", json!({"session":s.path}))["text"],
        "late save"
    );
}
#[test]
fn quick_session_stops_on_disconnect_ordinary_session_does_not() {
    for temporary in [false, true] {
        let s = Session::options("pty", json!(["echo"]), false, temporary);
        let mut c = s.connect();
        until(&mut c, b"READY");
        request("note-save", json!({"session":s.path,"text":"memo"}));
        drop(c);
        if temporary {
            wait(|| s.status()["state"] == "stopped");
            assert_eq!(
                request("note-read", json!({"session":s.path}))["text"],
                "memo"
            );
        } else {
            wait(|| s.status()["attached_clients"] == 0);
            assert_eq!(s.status()["state"], "running");
            request("stop", json!({"session":s.path}));
            assert_eq!(
                request("note-read", json!({"session":s.path}))["text"],
                "memo"
            );
        }
    }
}
#[test]
fn failed_quota_refresh_retains_last_good_timestamp() {
    let t = Temp::new();
    let old = 1000000;
    fs::write(t.0.join("session.json"),serde_json::to_vec(&json!({"version":1,"id":"a".repeat(32),"stopped":true,"provider":"Codex","telemetry":true,"directory":t.0,"auth_root":t.0,"executable":"/usr/bin/false"})).unwrap()).unwrap();
    fs::write(
        t.0.join("provider-usage-private.json"),
        serde_json::to_vec(
            &json!({"state":"available","timestamp":old,"windows":[{"minutes":300,"used":25}]}),
        )
        .unwrap(),
    )
    .unwrap();
    let result = usage(json!({"session":t.0,"root":t.0,"account":false}));
    assert_eq!(result["provider_usage"]["timestamp"], old);
    assert_eq!(result["provider_usage"]["windows"][0]["used"], 25);
    assert_eq!(result["provider_usage"]["refresh_error"], true);
}

#[test]
fn observation_tail_is_bounded_and_does_not_invent_missing_prompt() {
    let t = Temp::new();
    let path = t.0.join("rollout.jsonl");
    let mut f = fs::File::create(&path).unwrap();
    writeln!(f, "{}", json!({"type":"session_meta","payload":{"id":"x"}})).unwrap();
    writeln!(f,"{}",json!({"type":"event_msg","payload":{"type":"user_message","message":"old request outside tail"}})).unwrap();
    writeln!(
        f,
        "{}",
        json!({"type":"response_item","payload":{"type":"message","text":"x".repeat(2*1024*1024)}})
    )
    .unwrap();
    writeln!(
        f,
        "{}",
        json!({"type":"event_msg","payload":{"type":"task_complete"}})
    )
    .unwrap();
    // A partially written record is ignored until a newline arrives.
    write!(
        f,
        "{{\"type\":\"event_msg\",\"payload\":{{\"type\":\"task_started\"}}"
    )
    .unwrap();
    drop(f);
    let v = usage(
        json!({"action":"snapshot","root":t.0,"entry":{"provider":"Codex","id":"x","path":path}}),
    );
    assert_eq!(v["activity"]["state"], "waiting");
    assert_eq!(v["activity"]["prompt"], "");
    assert_eq!(v["activity"]["partial"], true);
    assert!(serde_json::to_vec(&v).unwrap().len() < 16384);
}

#[test]
fn claude_account_probe_preserves_launch_config_override() {
    let t = Temp::new();
    for explicit in [false, true] {
        fs::write(
            t.0.join("session.json"),
            serde_json::to_vec(&json!({
                "version":1,"id":"a".repeat(32),"stopped":true,"provider":"Claude",
                "telemetry":true,"directory":t.0,"auth_root":t.0,
                "auth_root_override":explicit,"executable":fixture("fixture")
            }))
            .unwrap(),
        )
        .unwrap();
        let output = run(
            &["usage"],
            &serde_json::to_vec(&json!({"session":t.0,"root":t.0,"usage":false,"account":true}))
                .unwrap(),
        );
        assert!(output.status.success());
        let v: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            v["account"]["email"],
            if explicit {
                "custom@example.test"
            } else {
                "default@example.test"
            }
        );
        assert_eq!(v["account"]["plan"], "max");
        assert_eq!(v["account"]["source"], "auth status");
    }
}

#[test]
fn quick_can_be_kept_without_restart_and_survives_reattach() {
    let s = Session::options("pty", json!(["echo"]), false, true);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let pid = s.status()["recorder_pid"].clone();
    request("note-save", json!({"session":s.path,"text":"keep memo"}));
    request("rename", json!({"session":s.path,"name":"my session"}));
    for _ in 0..2 {
        let metadata = request("persist", json!({"session":s.path}));
        assert_eq!(metadata["temporary"], false);
        assert_eq!(metadata["name"], "my session");
    }
    drop(c);
    wait(|| s.status()["attached_clients"] == 0);
    assert_eq!(s.status()["state"], "running");
    assert_eq!(s.status()["recorder_pid"], pid);
    assert_eq!(
        request("note-read", json!({"session":s.path}))["text"],
        "keep memo"
    );
    let mut c = s.connect();
    until(&mut c, b"READY");
    c.write_all(&packet(b'I', b"still-here\n")).unwrap();
    until(&mut c, b"still-here");
    assert_eq!(s.status()["recorder_pid"], pid);
    request("stop", json!({"session":s.path}));
    assert!(!run(
        &["manager", "persist"],
        &serde_json::to_vec(&json!({"session":s.path})).unwrap()
    )
    .status
    .success());
    assert_eq!(
        request("note-read", json!({"session":s.path}))["text"],
        "keep memo"
    );
}

#[test]
fn current_history_uses_session_identity_for_both_providers() {
    for provider in ["Claude", "Codex"] {
        let t = Temp::new();
        let transcript = t.0.join("conversation.jsonl");
        fs::write(
            t.0.join("session.json"),
            serde_json::to_vec(&json!({
                "version":1,"id":"a".repeat(32),"provider":provider,"stopped":true,"auth_root":t.0
            }))
            .unwrap(),
        )
        .unwrap();
        fs::write(&transcript, if provider == "Claude" {
            "{\"sessionId\":\"current\",\"type\":\"user\",\"message\":{\"content\":\"hello\"}}\n"
        } else {
            "{\"type\":\"session_meta\",\"payload\":{\"id\":\"current\"}}\n"
        }).unwrap();
        if provider == "Codex" {
            let db = rusqlite::Connection::open(t.0.join("state_5.sqlite")).unwrap();
            db.execute_batch("CREATE TABLE threads (id TEXT, cwd TEXT, rollout_path TEXT);")
                .unwrap();
            db.execute(
                "INSERT INTO threads VALUES ('current', ?1, ?2)",
                rusqlite::params![t.0.to_str().unwrap(), transcript.to_str().unwrap()],
            )
            .unwrap();
        }
        let query = json!({"action":"current","session":t.0});
        // Even with a local transcript, an unbound session must not guess.
        assert!(!run(&["history"], &serde_json::to_vec(&query).unwrap())
            .status
            .success());
        fs::write(
            t.0.join("telemetry.json"),
            serde_json::to_vec(&json!({"id":"current","path":transcript})).unwrap(),
        )
        .unwrap();
        let value = history(query.clone());
        assert_eq!(value["entry"]["id"], "current");
        assert_eq!(value["entry"]["provider"], provider);
        assert_eq!(value["entry"]["path"], transcript.to_str().unwrap());
        fs::write(
            &transcript,
            "{\"sessionId\":\"other\",\"type\":\"session_meta\",\"payload\":{\"id\":\"other\"}}\n",
        )
        .unwrap();
        assert!(!run(&["history"], &serde_json::to_vec(&query).unwrap())
            .status
            .success());
    }
}

#[test]
fn conversation_notes_resume_isolate_and_reject_stale_saves() {
    let t = Temp::new();
    for provider in ["Claude", "Codex"] {
        let a = t.0.join(format!("{provider}-a"));
        let b = t.0.join(format!("{provider}-b"));
        for path in [&a, &b] {
            fs::create_dir(path).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
            fs::write(
                path.join("session.json"),
                json!({"version":1,"id":"00000000000000000000000000000000","provider":provider,"auth_root":t.0,"stopped":true}).to_string(),
            )
            .unwrap();
        }
        // A note can precede the first provider conversation ID.
        let pending = request("note-save", json!({"session":a,"text":"한글 TODO"}));
        assert_eq!(pending["pending"], true);
        let callback = if provider == "Claude" {
            run(
                &["telemetry", "claude", a.to_str().unwrap()],
                br#"{"session_id":"conversation-one"}"#,
            )
        } else {
            run(
                &[
                    "telemetry",
                    "codex",
                    a.to_str().unwrap(),
                    r#"{"thread-id":"conversation-one"}"#,
                ],
                b"",
            )
        };
        assert!(callback.status.success());
        // Callback alone must move the pending note before any UI read.
        assert!(!a.join("pending-note.json").exists());
        let original = request("note-read", json!({"session":a}));
        assert_eq!(original["pending"], false);
        assert!(!a.join("pending-note.json").exists());
        fs::write(
            b.join("telemetry.json"),
            json!({"id":"conversation-one"}).to_string(),
        )
        .unwrap();
        let resumed = request("note-read", json!({"session":b}));
        assert_eq!(resumed["text"], "한글 TODO");
        assert_eq!(resumed["identity"], original["identity"]);
        request(
            "note-save",
            json!({"session":b,"identity":resumed["identity"],"revision":resumed["revision"],"text":"updated"}),
        );
        let stale = run(&["manager","note-save"], &serde_json::to_vec(&json!({"session":a,"identity":original["identity"],"revision":original["revision"],"text":"stale"})).unwrap());
        assert!(!stale.status.success());
        fs::write(
            b.join("telemetry.json"),
            json!({"id":"conversation-two"}).to_string(),
        )
        .unwrap();
        assert_eq!(request("note-read", json!({"session":b}))["text"], "");
        // An open editor is pinned to the original conversation.
        assert_eq!(
            request(
                "note-read",
                json!({"session":b,"identity":original["identity"]})
            )["text"],
            "updated"
        );
        assert_eq!(request("note-read", json!({"session":b}))["text"], "");
        fs::write(
            b.join("session.json"),
            json!({"version":1,"id":"00000000000000000000000000000000","provider":provider,"auth_root":t.0.join("other-profile"),"stopped":true})
                .to_string(),
        )
        .unwrap();
        fs::write(
            b.join("telemetry.json"),
            json!({"id":"conversation-one"}).to_string(),
        )
        .unwrap();
        assert_eq!(request("note-read", json!({"session":b}))["text"], "");
    }
}

#[test]
fn legacy_notes_migrate_without_overwriting_existing_conversation_notes() {
    let t = Temp::new();
    for name in ["a", "b"] {
        let path = t.0.join(name);
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(
            path.join("session.json"),
            json!({"version":1,"id":"00000000000000000000000000000000","provider":"Claude","auth_root":t.0,"stopped":true}).to_string(),
        )
        .unwrap();
        fs::write(
            path.join("telemetry.json"),
            json!({"id":"same"}).to_string(),
        )
        .unwrap();
        fs::write(path.join("note.json"), json!({"text":name}).to_string()).unwrap();
    }
    assert_eq!(
        request("note-read", json!({"session":t.0.join("a")}))["text"],
        "a"
    );
    let conflict = run(
        &["manager", "note-read"],
        &serde_json::to_vec(&json!({"session":t.0.join("b")})).unwrap(),
    );
    assert!(!conflict.status.success());
    assert!(t.0.join("b/pending-note.json").exists());
    assert_eq!(
        request("note-read", json!({"session":t.0.join("a")}))["text"],
        "a"
    );
}

#[test]
fn interrupted_sessions_survive_missing_runtime_and_can_be_dismissed() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let daemon = s.status()["daemon_pid"].as_i64().unwrap() as i32;
    let id = "11111111-2222-3333-4444-555555555555";
    fs::write(
        s.path.join("note-binding.json"),
        json!(["Fake", s._temp.0, id]).to_string(),
    )
    .unwrap();
    // Only the daemon created by this test is signalled.
    assert_eq!(unsafe { libc::kill(daemon, libc::SIGKILL) }, 0);
    wait(|| s.status()["state"] == "interrupted");
    drop(c);
    fs::remove_dir_all(&s.runtime).unwrap(); // Model /tmp cleanup during reboot.
    assert_eq!(s.status()["state"], "interrupted");
    let list = request(
        "list-live",
        json!({"root":s._temp.0,"include_interrupted":true}),
    );
    let entries = list["sessions"].as_array().unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0]["state"], "interrupted");
    assert_eq!(entries[0]["resume_id"], id);
    assert!(request("list-live", json!({"root":s._temp.0}))["sessions"]
        .as_array()
        .unwrap()
        .is_empty());
    assert_eq!(
        request("stop", json!({"session":s.path}))["state"],
        "stopped"
    );
    assert!(request(
        "list-live",
        json!({"root":s._temp.0,"include_interrupted":true})
    )["sessions"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn interrupted_duplicates_retire_only_with_matching_live_conversation() {
    for provider in ["Codex", "Claude"] {
        let old = Session::new("pty", json!(["echo"]), false);
        let live = Session::new("pty", json!(["echo"]), false);
        let pid = old.status()["daemon_pid"].as_i64().unwrap() as i32;
        assert_eq!(unsafe { libc::kill(pid, libc::SIGKILL) }, 0);
        wait(|| old.status()["state"] == "interrupted");
        let id = "aaaaaaaa-2222-3333-4444-555555555555";
        let update = |s: &Session, provider: &str, auth: &str, id: Value| {
            let file = s.path.join("session.json");
            let mut v: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
            v["provider"] = json!(provider);
            v["auth_root"] = json!(auth);
            v["resume_id"] = id;
            fs::write(file, v.to_string()).unwrap();
        };
        update(&old, provider, "/tmp/profile-a", json!(id));
        fs::write(old.path.join("pending-note.json"), "retained note").unwrap();
        let list = || {
            request(
                "list-live",
                json!({"root":old._temp.0,
            "extra":[live.path], "include_interrupted":true}),
            )
        };
        // Unknown UUID, another profile/provider, or another conversation
        // must not retire the interrupted entry.
        for (p, auth, conversation) in [
            (provider, "/tmp/profile-a", Value::Null),
            (provider, "/tmp/profile-b", json!(id)),
            ("Other", "/tmp/profile-a", json!(id)),
            (
                provider,
                "/tmp/profile-a",
                json!("bbbbbbbb-2222-3333-4444-555555555555"),
            ),
        ] {
            update(&live, p, auth, conversation);
            assert_eq!(list()["sessions"].as_array().unwrap().len(), 2);
            assert!(!old.path.join("closure.json").exists());
        }
        update(&live, provider, "/tmp/profile-a", json!(id.to_uppercase()));
        // Don't race an explicit recovery already in progress.
        let guard = fs::File::create(old.path.join("recovery.lock")).unwrap();
        use std::os::fd::AsRawFd;
        assert_eq!(
            unsafe { libc::flock(guard.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        assert_eq!(list()["sessions"].as_array().unwrap().len(), 2);
        drop(guard);
        let entries = list();
        assert_eq!(entries["sessions"].as_array().unwrap().len(), 1);
        assert_eq!(entries["sessions"][0]["session"], json!(live.path));
        assert_eq!(live.status()["state"], "running");
        assert_eq!(old.status()["state"], "stopped");
        let closure: Value =
            serde_json::from_slice(&fs::read(old.path.join("closure.json")).unwrap()).unwrap();
        assert_eq!(closure["reason"], "duplicate_conversation");
        assert_eq!(closure["replacement"], json!(live.path));
        assert_eq!(
            fs::read_to_string(old.path.join("pending-note.json")).unwrap(),
            "retained note"
        );
        assert_eq!(list()["sessions"].as_array().unwrap().len(), 1);
    }
}

#[test]
fn interrupted_recovery_retires_source_only_after_successful_start() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let pid = s.status()["recorder_pid"].as_i64().unwrap() as i32;
    assert_eq!(unsafe { libc::kill(pid, libc::SIGKILL) }, 0);
    wait(|| s.status()["state"] == "interrupted");
    let replacement = s._temp.0.join("replacement");
    let mut q = json!({"session":replacement,"recover_from":s.path,"provider":"Fake","backend":"pty","executable":"/no/such/fixture","args":["echo"],"directory":s._temp.0});
    assert!(
        !run(&["manager", "start"], &serde_json::to_vec(&q).unwrap())
            .status
            .success()
    );
    assert_eq!(s.status()["state"], "interrupted");
    q["executable"] = json!(fixture("fixture"));
    let started = request("start", q.clone());
    assert_eq!(s.status()["state"], "stopped");
    assert_eq!(
        request("inspect", json!({"session":replacement}))["status"]["state"],
        "running"
    );
    q["session"] = json!(s._temp.0.join("duplicate"));
    assert!(
        !run(&["manager", "start"], &serde_json::to_vec(&q).unwrap())
            .status
            .success()
    );
    request("stop", json!({"session":replacement}));
    let _ = fs::remove_dir_all(started["runtime"].as_str().unwrap());
}

#[test]
fn daemon_shutdown_signal_is_interruption_not_intentional_quit() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let pid = s.status()["daemon_pid"].as_i64().unwrap() as i32;
    assert_eq!(unsafe { libc::kill(pid, libc::SIGTERM) }, 0);
    wait(|| s.status()["state"] == "interrupted");
    assert!(!s.path.join("closure.json").exists());
    request("stop", json!({"session":s.path}));
    assert_eq!(s.status()["state"], "stopped");
}

#[test]
fn control_timeout_with_live_daemon_lock_is_not_recoverable() {
    let s = Session::new("pty", json!(["echo"]), false);
    let mut c = s.connect();
    until(&mut c, b"READY");
    let pid = s.status()["daemon_pid"].as_i64().unwrap() as i32;
    assert_eq!(unsafe { libc::kill(pid, libc::SIGSTOP) }, 0);
    let output = run(
        &["manager", "inspect"],
        &serde_json::to_vec(&json!({"session":s.path})).unwrap(),
    );
    // Resume before assertions so a failure cannot leave the fixture suspended.
    assert_eq!(unsafe { libc::kill(pid, libc::SIGCONT) }, 0);
    assert!(output.status.success());
    let v: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(v["status"]["state"], "unavailable");
    wait(|| s.status()["state"] == "running");
}
