use crate::{common::*, events::Events};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::unix::{
        fs::OpenOptionsExt,
        io::{AsRawFd, FromRawFd, RawFd},
        net::{UnixDatagram, UnixListener, UnixStream},
        process::CommandExt,
    },
    path::Path,
    process::{Child, Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
const LIMIT: usize = 256 * 1024;
const REPLAY: usize = 5 * 1024 * 1024;
static STOP: AtomicBool = AtomicBool::new(false);
static RESIZE: AtomicBool = AtomicBool::new(true);
extern "C" fn handler(sig: i32) {
    if sig == libc::SIGWINCH {
        RESIZE.store(true, Ordering::Relaxed)
    } else {
        STOP.store(true, Ordering::Relaxed)
    }
}
pub fn signals() {
    unsafe {
        let mut a: libc::sigaction = std::mem::zeroed();
        a.sa_sigaction = handler as *const () as usize;
        libc::sigemptyset(&mut a.sa_mask);
        for sig in [libc::SIGWINCH, libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
            libc::sigaction(sig, &a, std::ptr::null_mut());
        }
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
}
fn size(fd: RawFd) -> libc::winsize {
    unsafe {
        let mut s: libc::winsize = std::mem::zeroed();
        libc::ioctl(fd, libc::TIOCGWINSZ, &mut s);
        if s.ws_row == 0 || s.ws_col == 0 {
            s.ws_row = 40;
            s.ws_col = 120;
        }
        s
    }
}
fn resize(fd: RawFd, s: &libc::winsize) -> Result<()> {
    if unsafe { libc::ioctl(fd, libc::TIOCSWINSZ, s) } < 0 {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}
fn size_bytes(s: &libc::winsize) -> Vec<u8> {
    [s.ws_row, s.ws_col, s.ws_xpixel, s.ws_ypixel]
        .into_iter()
        .flat_map(u16::to_ne_bytes)
        .collect()
}
fn packet(k: u8, p: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(p.len() + 5);
    v.push(k);
    v.extend_from_slice(&(p.len() as u32).to_be_bytes());
    v.extend_from_slice(p);
    v
}
fn decode(b: &mut Vec<u8>) -> Result<Vec<(u8, Vec<u8>)>> {
    let mut out = Vec::new();
    let mut offset = 0;
    while b.len() - offset >= 5 {
        let n = u32::from_be_bytes(b[offset + 1..offset + 5].try_into()?) as usize;
        if n > LIMIT {
            return Err(error("Oversized packet"));
        }
        if b.len() - offset < 5 + n {
            break;
        }
        out.push((b[offset], b[offset + 5..offset + 5 + n].to_vec()));
        offset += 5 + n;
    }
    b.drain(..offset);
    Ok(out)
}
fn push(q: &mut VecDeque<u8>, data: &[u8]) -> Result<()> {
    if q.len() + data.len() > LIMIT {
        return Err(error("Queue full"));
    }
    q.extend(data);
    Ok(())
}
fn flush(fd: RawFd, q: &mut VecDeque<u8>) -> Result<()> {
    if q.is_empty() {
        return Ok(());
    }
    let b = q.as_slices().0;
    let n = unsafe { libc::write(fd, b.as_ptr().cast(), b.len()) };
    if n > 0 {
        q.drain(..n as usize);
    } else if n < 0 {
        let e = io::Error::last_os_error();
        if e.kind() != io::ErrorKind::WouldBlock && e.kind() != io::ErrorKind::Interrupted {
            return Err(e.into());
        }
    }
    Ok(())
}
fn poll(fds: &mut [libc::pollfd], ms: i32) -> Result<()> {
    let n = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, ms) };
    if n < 0 && io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
        return Err(io::Error::last_os_error().into());
    }
    Ok(())
}
fn pfd(fd: RawFd, read: bool, write: bool) -> libc::pollfd {
    libc::pollfd {
        fd,
        events: (if read { libc::POLLIN } else { 0 }) | (if write { libc::POLLOUT } else { 0 }),
        revents: 0,
    }
}
fn readable(p: &libc::pollfd) -> bool {
    p.revents & (libc::POLLIN | libc::POLLHUP | libc::POLLERR) != 0
}
fn read_fd(fd: RawFd) -> Result<Vec<u8>> {
    let mut b = vec![0; 16384];
    let n = unsafe { libc::read(fd, b.as_mut_ptr().cast(), b.len()) };
    if n < 0 {
        let e = io::Error::last_os_error();
        if e.raw_os_error() == Some(libc::EIO) {
            return Ok(Vec::new());
        }
        return Err(e.into());
    }
    b.truncate(n as usize);
    Ok(b)
}
struct Raw {
    fd: RawFd,
    term: Option<libc::termios>,
    flags: i32,
}
impl Raw {
    fn new(fd: RawFd) -> Result<Self> {
        unsafe {
            let flags = libc::fcntl(fd, libc::F_GETFL);
            let mut t = std::mem::zeroed();
            let term = if fd == 0 && libc::tcgetattr(fd, &mut t) == 0 {
                let mut raw = t;
                libc::cfmakeraw(&mut raw);
                libc::tcsetattr(fd, libc::TCSANOW, &raw);
                Some(t)
            } else {
                None
            };
            if fd == 1 {
                nonblock(fd)?;
            }
            Ok(Self { fd, term, flags })
        }
    }
}
impl Drop for Raw {
    fn drop(&mut self) {
        unsafe {
            libc::fcntl(self.fd, libc::F_SETFL, self.flags);
            if let Some(t) = self.term {
                libc::tcsetattr(self.fd, libc::TCSANOW, &t);
            }
        }
    }
}
fn spawn(v: &Value, mut s: libc::winsize) -> Result<(Child, File)> {
    let launch = read_json(&Path::new(string(v, "runtime")?).join("launch.json"), 16384)?;
    let args = launch["args"]
        .as_array()
        .ok_or_else(|| error("Invalid CLI argv"))?;
    let mut master = 0;
    let mut slave = 0;
    unsafe {
        if libc::openpty(
            &mut master,
            &mut slave,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut s,
        ) < 0
        {
            return Err(io::Error::last_os_error().into());
        }
    }
    let master = unsafe { File::from_raw_fd(master) };
    let slave = unsafe { File::from_raw_fd(slave) };
    for fd in [master.as_raw_fd(), slave.as_raw_fd()] {
        if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
            return Err(io::Error::last_os_error().into());
        }
    }
    let mut c = Command::new(string(&launch, "executable")?);
    for arg in args {
        c.arg(arg.as_str().ok_or_else(|| error("Invalid argument"))?);
    }
    c.current_dir(string(v, "directory")?).env(
        "TERM",
        std::env::var("TERM")
            .ok()
            .filter(|v| !v.is_empty() && v != "dumb")
            .unwrap_or_else(|| "xterm-256color".into()),
    );
    for k in [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    ] {
        c.env_remove(k);
    }
    let editor = crate::editor::launcher(
        Path::new(string(v, "runtime")?),
        &std::env::current_exe()?,
        Path::new(string(&launch, "editor")?),
    )?;
    c.env("EDITOR", &editor).env("VISUAL", &editor);
    c.stdin(Stdio::from(slave.try_clone()?))
        .stdout(Stdio::from(slave.try_clone()?))
        .stderr(Stdio::from(slave));
    unsafe {
        c.pre_exec(|| {
            if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let child = c.spawn()?;
    nonblock(master.as_raw_fd())?;
    Ok((child, master))
}
pub fn terminate(child: &mut Child) -> Result<i32> {
    use std::os::unix::process::ExitStatusExt;
    if let Some(s) = child.try_wait()? {
        return Ok(s.code().unwrap_or(128 + s.signal().unwrap_or(0)));
    }
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    let end = Instant::now() + Duration::from_secs(1);
    while Instant::now() < end {
        if let Some(s) = child.try_wait()? {
            return Ok(s.code().unwrap_or(128 + s.signal().unwrap_or(0)));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let s = child.wait()?;
    Ok(s.code().unwrap_or(128 + s.signal().unwrap_or(0)))
}
pub fn run(path: &Path) -> Result<i32> {
    signals();
    let v = session(path)?;
    let runtime = Path::new(string(&v, "runtime")?);
    let _lock = lock(&runtime.join("lock"), true)?;
    let listener = UnixListener::bind(runtime.join("socket"))?;
    listener.set_nonblocking(true)?;
    let control = UnixDatagram::bind(runtime.join("control"))?;
    control.set_nonblocking(true)?;
    let mut archive = if let Some(p) = v["archive"].as_str() {
        Some(
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(p)?,
        )
    } else {
        None
    };
    let mut events = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(string(&v, "events")?)?;
    let mut extractor = Events::default();
    let (mut child, mut master) = spawn(&v, size(-1))?;
    let mut client: Option<UnixStream> = None;
    let (mut input, mut output, mut replay_send) =
        (VecDeque::new(), VecDeque::new(), VecDeque::new());
    let mut incoming = Vec::new();
    let mut replay = Vec::new();
    let mut overflow = false;
    let mut eof = false;
    let mut end = None;
    let mut total = 0u64;
    let mut last_input = 0.0;
    let mut last_output = 0.0;
    let mut attached_once = false;
    let mut queue_peak = 0usize;
    let mut blocked_since: Option<Instant> = None;
    let mut blocked_time = Duration::ZERO;
    let mut blocked_count = 0u64;
    let mut disconnect_count = 0u64;
    let mut disconnect_reason: Option<&str> = None;
    let mut explicit_stop = false;
    let mut temporary_detach = false;
    let mut temporary = v["temporary"] == true;
    let attach_deadline = Instant::now() + Duration::from_secs(30);
    let result = (|| -> Result<()> {
        while !STOP.load(Ordering::Relaxed) {
            if temporary && client.is_none() && (attached_once || Instant::now() > attach_deadline)
            {
                temporary_detach = true;
                break;
            }
            // A CLI can leave a helper holding the slave PTY open.  Process
            // exit is authoritative; EOF alone would keep such sessions alive.
            if end.is_none() && child.try_wait()?.is_some() {
                end = Some(Instant::now() + Duration::from_secs(1));
            }
            if (eof && output.is_empty() && replay_send.is_empty())
                || end.is_some_and(|t| Instant::now() > t)
            {
                break;
            }
            let dest = client.as_ref().map_or(-1, AsRawFd::as_raw_fd);
            let source = dest;
            // Reserve an entire read plus framing before touching the PTY.
            // Detached sessions keep draining; control/input remain independent.
            let blocked = client.is_some() && output.len() > LIMIT - 16384 - 5;
            if blocked && blocked_since.is_none() {
                blocked_since = Some(Instant::now());
                blocked_count += 1;
            } else if !blocked {
                if let Some(start) = blocked_since.take() {
                    blocked_time += start.elapsed();
                }
            }
            let read_master = !eof && !blocked;
            let write_master = !input.is_empty() && !eof;
            let mut fds = [
                pfd(
                    if read_master || write_master {
                        master.as_raw_fd()
                    } else {
                        -1
                    },
                    read_master,
                    write_master,
                ),
                pfd(
                    source,
                    source >= 0 && !eof && input.len() + incoming.len() < LIMIT / 2,
                    false,
                ),
                pfd(
                    dest,
                    false,
                    dest >= 0 && (!output.is_empty() || !replay_send.is_empty()),
                ),
                pfd(listener.as_raw_fd(), !eof, false),
                pfd(control.as_raw_fd(), !eof, false),
            ];
            poll(&mut fds, 50)?;
            if readable(&fds[4]) {
                let sock = &control;
                let mut b = [0; 2048];
                if let Ok((n, addr)) = sock.recv_from(&mut b) {
                    if let Ok(q) = serde_json::from_slice::<Value>(&b[..n]) {
                        if q["id"] == v["id"]
                            && ["status", "stop", "persist"]
                                .contains(&q["action"].as_str().unwrap_or(""))
                        {
                            let mut failure = None;
                            if q["action"] == "persist" {
                                let result = (|| -> Result<()> {
                                    if eof || end.is_some() || child.try_wait()?.is_some() {
                                        return Err(error("CLI has already ended"));
                                    }
                                    let mut latest = session(path)?;
                                    latest["temporary"] = json!(false);
                                    save(&path.join("session.json"), &latest)?;
                                    temporary = false;
                                    Ok(())
                                })();
                                if let Err(e) = result {
                                    failure = Some(e.to_string());
                                }
                            }
                            let reply = json!({"id":v["id"],"state":"running","temporary":temporary,"error":failure,"recorder_pid":child.id(),"daemon_pid":std::process::id(),"attached_clients":usize::from(client.is_some()),"output_bytes":total,"replay_bytes":replay.len(),"last_input_at":last_input,"last_output_at":last_output,
                                "output_queue_bytes":output.len(), "output_queue_peak_bytes":queue_peak,
                                "replay_pending_bytes":replay_send.len(), "output_backpressured":blocked,
                                "backpressure_count":blocked_count,
                                "backpressure_seconds":(blocked_time + blocked_since.map_or(Duration::ZERO, |t| t.elapsed())).as_secs_f64(),
                                "disconnect_count":disconnect_count, "last_disconnect_reason":disconnect_reason});
                            if let Some(p) = addr.as_pathname() {
                                let _ = sock.send_to(&serde_json::to_vec(&reply)?, p);
                            }
                            if q["action"] == "stop" {
                                explicit_stop = true;
                                STOP.store(true, Ordering::Relaxed);
                            }
                        }
                    }
                }
            }
            if readable(&fds[3]) {
                if let Ok((c, _)) = listener.accept() {
                    if client.is_none() {
                        c.set_nonblocking(true)?;
                        replay_send.extend(packet(
                            b'H',
                            &serde_json::to_vec(&json!({"replay_complete":!overflow}))?,
                        ));
                        if !overflow {
                            for chunk in replay.chunks(16384) {
                                replay_send.extend(packet(b'O', chunk));
                            }
                        }
                        client = Some(c);
                        attached_once = true;
                    }
                }
            }
            if read_master && readable(&fds[0]) {
                let mut b = [0; 16384];
                let n = match master.read(&mut b) {
                    Ok(n) => n,
                    Err(e) if e.raw_os_error() == Some(libc::EIO) => 0,
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => continue,
                    Err(e) => return Err(e.into()),
                };
                if n == 0 {
                    eof = true;
                    end.get_or_insert(Instant::now() + Duration::from_secs(1));
                } else {
                    let b = &b[..n];
                    total += n as u64;
                    last_output = now();
                    if let Some(f) = archive.as_mut() {
                        if let Err(e) = f.write_all(b) {
                            eprintln!("Raw recording disabled after error: {e}");
                            archive = None;
                        }
                    }
                    for event in extractor.feed(b) {
                        serde_json::to_writer(&mut events, &event)?;
                        events.write_all(b"\n")?;
                    }
                    if !overflow {
                        if replay.len() + n <= REPLAY {
                            replay.extend_from_slice(b)
                        } else {
                            overflow = true;
                            replay.clear()
                        }
                    }
                    if client.is_some() {
                        push(&mut output, &packet(b'O', b))?;
                        queue_peak = queue_peak.max(output.len());
                    }
                }
            }
            if readable(&fds[1])
                && source >= 0
                && client.as_ref().map(AsRawFd::as_raw_fd) == Some(source)
            {
                match read_fd(source) {
                    Ok(b) if b.is_empty() => {
                        disconnect_count += 1;
                        disconnect_reason = Some("client_eof");
                        client = None;
                        output.clear();
                        replay_send.clear();
                        input.clear();
                        incoming.clear();
                    }
                    Ok(b) => {
                        incoming.extend(b);
                        let header_valid = incoming.len() < 5 || {
                            let n = u32::from_be_bytes(incoming[1..5].try_into().unwrap()) as usize;
                            (incoming[0] == b'I' && n <= 16384) || (incoming[0] == b'R' && n == 8)
                        };
                        let decoded = if header_valid {
                            decode(&mut incoming)
                        } else {
                            Err(error("Invalid input packet header"))
                        };
                        let mut valid = true;
                        match decoded {
                            Ok(parts) => {
                                for (k, b) in parts {
                                    match k {
                                        b'I' if b.len() <= 16384 => {
                                            if push(&mut input, &b).is_err() {
                                                valid = false
                                            }
                                        }
                                        b'R' if b.len() == 8 => {
                                            let nums: Vec<u16> = b
                                                .chunks_exact(2)
                                                .map(|x| u16::from_ne_bytes([x[0], x[1]]))
                                                .collect();
                                            if (1..=1000).contains(&nums[0])
                                                && (1..=1000).contains(&nums[1])
                                            {
                                                resize(
                                                    master.as_raw_fd(),
                                                    &libc::winsize {
                                                        ws_row: nums[0],
                                                        ws_col: nums[1],
                                                        ws_xpixel: nums[2],
                                                        ws_ypixel: nums[3],
                                                    },
                                                )?
                                            } else {
                                                valid = false
                                            }
                                        }
                                        _ => valid = false,
                                    }
                                }
                            }
                            Err(_) => valid = false,
                        }
                        if !valid {
                            disconnect_count += 1;
                            disconnect_reason = Some("invalid_input");
                            client = None;
                            incoming.clear();
                            input.clear();
                            output.clear();
                            replay_send.clear();
                        }
                    }
                    Err(e)
                        if e.downcast_ref::<io::Error>().is_some_and(|e| {
                            matches!(
                                e.kind(),
                                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                            )
                        }) => {}
                    Err(_) => {
                        disconnect_count += 1;
                        disconnect_reason = Some("client_read_error");
                        client = None;
                        incoming.clear();
                        input.clear();
                        output.clear();
                        replay_send.clear();
                    }
                }
            }
            if fds[0].revents & libc::POLLOUT != 0 {
                let queued = input.len();
                let _ = flush(master.as_raw_fd(), &mut input);
                if input.len() < queued {
                    last_input = now();
                }
            }
            if fds[2].revents & libc::POLLOUT != 0
                && dest >= 0
                && client.as_ref().map(AsRawFd::as_raw_fd) == Some(dest)
            {
                let q = if replay_send.is_empty() {
                    &mut output
                } else {
                    &mut replay_send
                };
                if flush(dest, q).is_err() {
                    disconnect_count += 1;
                    disconnect_reason = Some("client_write_error");
                    client = None;
                    output.clear();
                    replay_send.clear();
                    input.clear();
                    incoming.clear();
                }
            }
        }
        Ok(())
    })();
    let code = terminate(&mut child)?;
    drop(master); // Hang up the CLI terminal even if a descendant retained it.
    drop(client);
    drop(listener);
    drop(control);
    {
        // Serialize exit metadata with note binding and saves.
        let _note_lock = lock(&path.join("note.lock"), false)?;
        let _ = fs::remove_file(runtime.join("socket"));
        let _ = fs::remove_file(runtime.join("control"));
        let mut latest = session(path)?;
        latest["stopped"] = json!(true);
        latest["exit_code"] = json!(code);
        latest["interrupted"] = json!(
            !explicit_stop
                && !temporary_detach
                && (STOP.load(Ordering::Relaxed) || result.is_err() || code != 0)
        );
        latest["transport_diagnostics"] = json!({
            "output_queue_peak_bytes":queue_peak, "backpressure_count":blocked_count,
            "backpressure_seconds":(blocked_time + blocked_since.map_or(Duration::ZERO, |t| t.elapsed())).as_secs_f64(),
            "disconnect_count":disconnect_count, "last_disconnect_reason":disconnect_reason,
            "pending_output_bytes":output.len() + replay_send.len(),
            "end_reason":if result.is_err() { "transport_error" } else if STOP.load(Ordering::Relaxed) { "stop" } else if temporary_detach { "temporary_detach" } else { "cli_exit" }
        });
        latest["last_input_at"] = json!(last_input);
        latest["last_output_at"] = json!(last_output);
        save(&path.join("session.json"), &latest)?;
    }
    result?;
    Ok(code)
}
pub fn attach(runtime: &Path, mut ready: impl FnMut(bool) -> Result<()>) -> Result<()> {
    signals();
    private_dir(runtime)?;
    let mut c = UnixStream::connect(runtime.join("socket"))?;
    let _awake = KeepAwake::start();
    c.set_nonblocking(true)?;
    let _raw = (Raw::new(0)?, Raw::new(1)?);
    let (mut incoming, mut output, mut sending) = (Vec::new(), VecDeque::new(), VecDeque::new());
    let mut open = true;
    while !STOP.load(Ordering::Relaxed) && (open || !output.is_empty()) {
        if open && RESIZE.swap(false, Ordering::Relaxed) {
            push(&mut sending, &packet(b'R', &size_bytes(&size(0))))?
        }
        let mut fds = [
            pfd(
                c.as_raw_fd(),
                open && incoming.len() + output.len() < LIMIT - 16384,
                open && !sending.is_empty(),
            ),
            pfd(0, open && sending.len() < LIMIT / 2, false),
            pfd(1, false, !output.is_empty()),
        ];
        poll(&mut fds, -1)?;
        if readable(&fds[0]) {
            let mut b = [0; 16384];
            match c.read(&mut b) {
                Ok(0) => open = false,
                Ok(n) => {
                    incoming.extend(&b[..n]);
                    for (k, b) in decode(&mut incoming)? {
                        match k {
                            b'O' => push(&mut output, &b)?,
                            b'H' => {
                                let v: Value = serde_json::from_slice(&b)?;
                                let complete = v["replay_complete"] == true;
                                ready(complete)?;
                                if !complete {
                                    push(&mut output,b"\r\n[EAM PTY: 5MiB replay limit exceeded; C-c a h opens history]\r\n")?
                                }
                            }
                            _ => return Err(error("Unknown server packet")),
                        }
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => (),
                Err(e) => return Err(e.into()),
            }
        }
        if readable(&fds[1]) {
            let b = read_fd(0)?;
            if b.is_empty() {
                break;
            }
            push(&mut sending, &packet(b'I', &b))?
        }
        if fds[0].revents & libc::POLLOUT != 0 {
            flush(c.as_raw_fd(), &mut sending)?
        }
        if fds[2].revents & libc::POLLOUT != 0 {
            flush(1, &mut output)?
        }
    }
    Ok(())
}

pub fn interrupted() -> bool {
    STOP.load(Ordering::Relaxed)
}
