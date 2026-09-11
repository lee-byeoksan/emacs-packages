//! Loopback HTTP/WebSocket gateway. PTYs remain owned by session daemons.
mod emacs;
mod terminal;
use crate::common;
use axum::{
    extract::{DefaultBodyLimit, Query, State, WebSocketUpgrade},
    http::{header, HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{sync::Mutex, time::timeout};
use tokio_util::sync::CancellationToken;

type WebResult<T> = std::result::Result<T, WebError>;
#[derive(Debug)]
struct WebError(StatusCode, String);
impl IntoResponse for WebError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error": self.1}))).into_response()
    }
}
fn fail(message: impl Into<String>) -> WebError {
    WebError(StatusCode::BAD_REQUEST, message.into())
}
fn internal(e: impl std::fmt::Display) -> WebError {
    fail(e.to_string())
}
fn field<'a>(v: &'a Value, key: &str) -> WebResult<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| fail(format!("Missing string: {key}")))
}

struct App {
    root: PathBuf,
    browse: PathBuf,
    origin: String,
    host: String,
    token: String,
    tailscale_user: Option<String>,
    control: Mutex<Control>,
    stopping: CancellationToken,
    // Explicit opt-in fake CLI for integration tests; never exposed as an API argv.
    demo: Option<PathBuf>,
}
#[derive(Default)]
struct Control {
    active: HashMap<PathBuf, terminal::Attachment>,
    returns: HashMap<PathBuf, Value>,
}
type Shared = Arc<App>;

async fn manager(action: &'static str, query: Value) -> WebResult<Value> {
    tokio::task::spawn_blocking(move || {
        crate::session::dispatch(action, &query).map_err(|e| e.to_string())
    })
    .await
    .map_err(internal)?
    .map_err(fail)
}
impl App {
    fn session(&self, value: &str) -> WebResult<PathBuf> {
        let p = std::fs::canonicalize(value).map_err(internal)?;
        if p.parent() != Some(self.root.as_path()) {
            return Err(fail("Unknown session"));
        }
        common::private_dir(&p).map_err(internal)?;
        Ok(p)
    }
    fn directory(&self, value: &str) -> WebResult<PathBuf> {
        let p = std::fs::canonicalize(value).map_err(internal)?;
        if !p.starts_with(&self.browse) || !p.is_dir() {
            return Err(fail("탐색 범위 밖의 디렉터리입니다."));
        }
        Ok(p)
    }
}

fn secret_equal(a: &str, b: &str) -> bool {
    let mut diff = a.len() ^ b.len();
    for (i, expected) in b.bytes().enumerate() {
        diff |= usize::from(a.as_bytes().get(i).copied().unwrap_or(0) ^ expected);
    }
    diff == 0
}
fn text_header<'a>(h: &'a HeaderMap, key: &str) -> Option<&'a str> {
    h.get(key)?.to_str().ok()
}
fn authorized(app: &App, headers: &HeaderMap) -> bool {
    if let Some(user) = &app.tailscale_user {
        // Only the loopback listener is exposed. Serve strips client-supplied identity
        // headers and inserts its authenticated identity. Local processes are trusted.
        let mut values = headers.get_all("tailscale-user-login").iter();
        return values.next().and_then(|v| v.to_str().ok()) == Some(user.as_str())
            && values.next().is_none();
    }
    text_header(headers, "cookie")
        .unwrap_or("")
        .split(';')
        .any(|c| {
            c.trim()
                .strip_prefix("eam_token=")
                .is_some_and(|t| secret_equal(t, &app.token))
        })
}
async fn auth_info(State(app): State<Shared>) -> Json<Value> {
    Json(json!({"mode": if app.tailscale_user.is_some() { "tailscale" } else { "token" }}))
}
async fn guard(State(app): State<Shared>, request: axum::extract::Request, next: Next) -> Response {
    let headers = request.headers();
    let path = request.uri().path();
    let origin = text_header(headers, "origin");
    let protected = path.starts_with("/api/")
        || path == "/ws"
        || (path == "/login" && app.tailscale_user.is_some());
    let denied = text_header(headers, "host") != Some(app.host.as_str())
        || origin.is_some_and(|o| o != app.origin)
        || ((request.method() != axum::http::Method::GET || path == "/ws")
            && origin != Some(app.origin.as_str()));
    let mut response = if denied {
        WebError(StatusCode::FORBIDDEN, "Request origin rejected".into()).into_response()
    } else if app.stopping.is_cancelled() {
        WebError(StatusCode::SERVICE_UNAVAILABLE, "Server stopping".into()).into_response()
    } else if protected && !authorized(&app, headers) {
        if app.tailscale_user.is_some() {
            WebError(
                StatusCode::FORBIDDEN,
                "허용된 본인 계정으로 Tailscale에 연결해 주세요.".into(),
            )
            .into_response()
        } else {
            WebError(StatusCode::UNAUTHORIZED, "접속 토큰을 입력하세요.".into()).into_response()
        }
    } else {
        next.run(request).await
    };
    let h = response.headers_mut();
    h.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    h.insert("x-content-type-options", "nosniff".parse().unwrap());
    h.insert("referrer-policy", "no-referrer".parse().unwrap());
    h.insert("content-security-policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'".parse().unwrap());
    response
}
async fn login(State(app): State<Shared>, Json(q): Json<Value>) -> WebResult<Response> {
    if app.tailscale_user.is_some() {
        return Err(WebError(
            StatusCode::FORBIDDEN,
            "토큰 로그인을 사용하지 않습니다.".into(),
        ));
    }
    if !secret_equal(q["token"].as_str().unwrap_or(""), &app.token) {
        return Err(WebError(
            StatusCode::UNAUTHORIZED,
            "접속 토큰이 다릅니다.".into(),
        ));
    }
    let mut response = Json(json!({"ok":true})).into_response();
    let cookie = format!(
        "eam_token={}; Path=/; HttpOnly; SameSite=Strict{}",
        app.token,
        if app.origin.starts_with("https:") {
            "; Secure"
        } else {
            ""
        }
    );
    response
        .headers_mut()
        .insert(header::SET_COOKIE, cookie.parse().map_err(internal)?);
    Ok(response)
}
async fn config(State(app): State<Shared>) -> Json<Value> {
    Json(
        json!({"demo":app.demo.is_some(),"runtime":"rust", "sessions":app.root,
        "browse_root":app.browse, "origin":app.origin, "pid":std::process::id(),
        "auth": if app.tailscale_user.is_some() { "tailscale" } else { "token" }, "tailscale_user":app.tailscale_user}),
    )
}
async fn shutdown(State(app): State<Shared>) -> Json<Value> {
    tokio::spawn(async move {
        // Let the HTTP acknowledgement reach the caller before draining connections.
        tokio::time::sleep(Duration::from_millis(100)).await;
        app.stopping.cancel();
    });
    Json(json!({"ok":true}))
}
async fn sessions(State(app): State<Shared>) -> WebResult<Json<Value>> {
    Ok(Json(manager("list-live", json!({"root":app.root})).await?))
}
async fn folders(
    State(app): State<Shared>,
    Query(q): Query<HashMap<String, String>>,
) -> WebResult<Json<Value>> {
    let path = app.directory(
        q.get("path")
            .map(String::as_str)
            .unwrap_or(app.browse.to_str().unwrap_or("")),
    )?;
    let mut children = Vec::new();
    for entry in std::fs::read_dir(&path).map_err(internal)? {
        let entry = entry.map_err(internal)?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with('.') && entry.file_type().map_err(internal)?.is_dir() {
            children.push(json!({"name":name,"path":entry.path()}));
        }
    }
    children.sort_by_key(|v| v["name"].as_str().unwrap_or("").to_lowercase());
    Ok(Json(
        json!({"path":path,"parent":if path==app.browse {None} else {path.parent()},"children":children}),
    ))
}
async fn mkdir(State(app): State<Shared>, Json(q): Json<Value>) -> WebResult<Json<Value>> {
    use std::os::unix::fs::DirBuilderExt;
    let parent = app.directory(field(&q, "parent")?)?;
    let name = field(&q, "name")?;
    if name.trim().is_empty() || [".", ".."].contains(&name) || name.contains(['/', '\0']) {
        return Err(fail("폴더 이름을 확인하세요."));
    }
    let path = parent.join(name);
    std::fs::DirBuilder::new()
        .mode(0o700)
        .create(&path)
        .map_err(internal)?;
    Ok(Json(json!({"path":path})))
}
fn executable(name: &str) -> WebResult<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .map(|p| p.join(name))
        .find(|p| {
            p.is_file()
                && p.metadata()
                    .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
        })
        .and_then(|p| std::fs::canonicalize(p).ok())
        .ok_or_else(|| fail("PC에서 CLI 실행 파일을 찾지 못했습니다."))
}
async fn start(State(app): State<Shared>, Json(q): Json<Value>) -> WebResult<Json<Value>> {
    let provider = field(&q, "provider")?;
    let exe = match provider {
        "Claude" => executable("claude")?,
        "Codex" => executable("codex")?,
        "Demo" if app.demo.is_some() => app.demo.clone().unwrap(),
        _ => return Err(fail("Unknown provider")),
    };
    let directory = app.directory(field(&q, "directory")?)?;
    let session = app.root.join(format!("web-{}", common::unique()));
    manager(
        "start",
        json!({"session":session,"directory":directory,"provider":provider,
        "executable":exe,"args":[],"temporary":false}),
    )
    .await?;
    Ok(Json(json!({"session":session})))
}

async fn wait_detached(path: &Path) -> WebResult<bool> {
    for _ in 0..60 {
        let info = manager("inspect", json!({"session":path})).await?;
        if info["status"]["state"] != "running" {
            return Ok(false);
        }
        if info["status"]["attached_clients"] == 0 {
            return Ok(true);
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    Err(fail("연결 해제 확인이 지연됩니다. 다시 시도하세요."))
}
async fn return_to_emacs(control: &mut Control, path: &Path) -> WebResult<bool> {
    if !wait_detached(path).await? {
        control.returns.remove(path);
        return Ok(false);
    }
    if let Some(endpoint) = control.returns.get(path) {
        emacs::restore(path, endpoint).await?;
        control.returns.remove(path);
        Ok(true)
    } else {
        Ok(false)
    }
}
async fn takeover(State(app): State<Shared>, Json(q): Json<Value>) -> WebResult<Json<Value>> {
    let path = app.session(field(&q, "session")?)?;
    let mut control = app.control.lock().await;
    let info = manager("inspect", json!({"session":path})).await?;
    if info["metadata"]["temporary"] == true {
        return Err(fail("Quick 세션은 먼저 eam-persist로 전환하세요."));
    }
    if let Some(old) = control.active.remove(&path) {
        old.stop.cancel();
        timeout(Duration::from_secs(3), old.done.cancelled())
            .await
            .map_err(|_| fail("연결 해제 시간 초과"))?;
    } else if info["status"]["attached_clients"].as_u64().unwrap_or(0) > 0 {
        let endpoint = emacs::release(&path).await?;
        control.returns.insert(path.clone(), endpoint);
    }
    if !wait_detached(&path).await? {
        return Err(fail("CLI가 종료됐습니다."));
    }
    Ok(Json(json!({"session":path})))
}
async fn detach(State(app): State<Shared>, Json(q): Json<Value>) -> WebResult<Json<Value>> {
    let path = app.session(field(&q, "session")?)?;
    let mut control = app.control.lock().await;
    if let Some(old) = control.active.get(&path) {
        if q["connection"].as_str() != Some(old.id.as_str()) {
            return Err(fail("다른 화면으로 조작권이 이동했습니다."));
        }
        let old = control.active.remove(&path).unwrap();
        old.stop.cancel();
        timeout(Duration::from_secs(3), old.done.cancelled())
            .await
            .map_err(|_| fail("연결 해제 시간 초과"))?;
    }
    let restored = return_to_emacs(&mut control, &path).await?;
    Ok(Json(json!({"detached":true,"restored":restored})))
}

async fn asset(uri: axum::http::Uri) -> Response {
    let (mime, content): (&str, &[u8]) = match uri.path() {
        "/" => (
            "text/html; charset=utf-8",
            include_bytes!("../web/index.html"),
        ),
        "/app.js" => (
            "text/javascript; charset=utf-8",
            include_bytes!("../web/app.js"),
        ),
        "/style.css" => ("text/css", include_bytes!("../web/style.css")),
        "/xterm.js" => ("text/javascript", include_bytes!("../web/vendor/xterm.js")),
        "/fit.js" => ("text/javascript", include_bytes!("../web/vendor/fit.js")),
        "/xterm.css" => ("text/css", include_bytes!("../web/vendor/xterm.css")),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    ([(header::CONTENT_TYPE, mime)], content).into_response()
}

pub fn run(args: &[String]) -> common::Result<()> {
    if args.iter().any(|s| s == "--help" || s == "-h") {
        println!("eam-runtime serve [--port 8765] [--origin https://HOST] [--sessions DIR] [--browse-root DIR] [--token-file FILE] [--tailscale-user LOGIN]\nTailscale: only the exact allowed login; token authentication is disabled.\nToken: EAM_WEB_TOKEN environment variable (random if unset); token-file persists it.\nLoopback only; use Tailscale Serve for HTTPS.\nTest-only: --demo-executable PATH");
        return Ok(());
    }
    let home =
        PathBuf::from(std::env::var_os("HOME").ok_or_else(|| common::error("HOME missing"))?);
    let (mut port, mut root, mut browse, mut origin, mut demo) =
        (8765u16, home.join(".eam/persistent"), home, None, None);
    let mut it = args.iter();
    let mut token_file = None;
    let mut tailscale_user: Option<String> = None;
    while let Some(key) = it.next() {
        let value = it
            .next()
            .ok_or_else(|| common::error("Missing serve option value"))?;
        match key.as_str() {
            "--port" => port = value.parse()?,
            "--origin" => origin = Some(value.clone()),
            "--sessions" => root = PathBuf::from(value),
            "--browse-root" => browse = PathBuf::from(value),
            "--demo-executable" => demo = Some(std::fs::canonicalize(value)?),
            "--tailscale-user" => tailscale_user = Some(value.clone()),
            "--token-file" => token_file = Some(PathBuf::from(value)),
            _ => return Err(common::error(format!("Unknown serve option: {key}"))),
        }
    }
    let origin = origin.unwrap_or_else(|| format!("http://127.0.0.1:{port}"));
    let uri: axum::http::Uri = origin.parse()?;
    if !matches!(uri.scheme_str(), Some("http" | "https"))
        || uri.authority().is_none()
        || uri
            .path_and_query()
            .is_some_and(|p| p.as_str() != "/" && !p.as_str().is_empty())
        || origin.ends_with('/')
    {
        return Err(common::error(
            "--origin must be an http(s) origin without a trailing slash",
        ));
    }
    let host = uri.authority().unwrap().as_str().to_owned();
    if tailscale_user.as_ref().is_some_and(|u| {
        u.is_empty()
            || !u.is_ascii()
            || u.bytes()
                .any(|b| b.is_ascii_whitespace() || b.is_ascii_control())
    }) {
        return Err(common::error("Invalid Tailscale login"));
    }
    if tailscale_user.is_some() && (!origin.starts_with("https://") || token_file.is_some()) {
        return Err(common::error(
            "Tailscale authentication requires an HTTPS origin and no --token-file",
        ));
    }
    let token = if tailscale_user.is_some() {
        String::new()
    } else {
        access_token(token_file.as_deref())?
    };
    if tailscale_user.is_none()
        && (token.is_empty()
            || !token
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c)))
    {
        return Err(common::error(
            "Token must contain only letters, digits, _ or -",
        ));
    }
    common::mkdir(&root)?;
    let app = Arc::new(App {
        root: std::fs::canonicalize(root)?,
        browse: std::fs::canonicalize(browse)?,
        origin,
        host,
        token,
        tailscale_user,
        control: Mutex::new(Control::default()),
        stopping: CancellationToken::new(),
        demo,
    });
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(async move {
            let router = Router::new()
                .route("/auth", get(auth_info))
                .route("/login", post(login))
                .route("/api/config", get(config))
                .route("/api/shutdown", post(shutdown))
                .route("/api/sessions", get(sessions).post(start))
                .route("/api/folders", get(folders).post(mkdir))
                .route("/api/takeover", post(takeover))
                .route("/api/detach", post(detach))
                .route("/ws", get(terminal::upgrade))
                .fallback(asset)
                .layer(DefaultBodyLimit::max(32768))
                .layer(middleware::from_fn_with_state(app.clone(), guard))
                .with_state(app.clone());
            let listener =
                tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).await?;
            println!("Open {}", app.origin);
            if let Some(user) = &app.tailscale_user {
                println!("Tailscale account: {user}");
            } else {
                println!("Access token: {}", app.token);
            }
            let shutdown_app = app.clone();
            axum::serve(listener, router)
                .with_graceful_shutdown(async move {
                    let mut term =
                        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                            .expect("SIGTERM handler");
                    tokio::select! { _=tokio::signal::ctrl_c()=>{}, _=term.recv()=>{}, _=shutdown_app.stopping.cancelled()=>{} }
                    shutdown_app.stopping.cancel();
                    let mut control = shutdown_app.control.lock().await;
                    let attachments: Vec<_> = control.active.drain().map(|(_, a)| a).collect();
                    for a in &attachments {
                        a.stop.cancel();
                    }
                    for a in attachments {
                        let _ = timeout(Duration::from_secs(3), a.done.cancelled()).await;
                    }
                    let paths: Vec<_> = control.returns.keys().cloned().collect();
                    for p in paths {
                        if let Err(e) = return_to_emacs(&mut control, &p).await {
                            eprintln!("Emacs return failed: {}", e.1);
                        }
                    }
                })
                .await?;
            Ok::<(), Box<dyn std::error::Error>>(())
        })
}

fn access_token(path: Option<&Path>) -> common::Result<String> {
    use std::io::Write;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    let from_env = std::env::var("EAM_WEB_TOKEN").ok();
    let Some(path) = path else {
        return Ok(from_env.unwrap_or_else(|| format!("{}{}", common::unique(), common::unique())));
    };
    if !path.exists() {
        let token = from_env
            .clone()
            .unwrap_or_else(|| format!("{}{}", common::unique(), common::unique()));
        if token.is_empty()
            || !token
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
        {
            return Err(common::error("Invalid EAM_WEB_TOKEN"));
        }
        // Publish a complete file atomically, without replacing a racing starter's token.
        let temp = path.with_file_name(format!(".token-{}", common::unique()));
        let result = (|| -> common::Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temp)?;
            writeln!(file, "{token}")?;
            match std::fs::hard_link(&temp, path) {
                Ok(()) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
                Err(e) => Err(e.into()),
            }
        })();
        let _ = std::fs::remove_file(temp);
        result?;
    }
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file()
        || metadata.uid() != unsafe { libc::getuid() }
        || metadata.mode() & 0o077 != 0
    {
        return Err(common::error(
            "Token file must be private and owned by this user",
        ));
    }
    let token = String::from_utf8(common::bounded(path, 4096)?)?
        .trim()
        .to_owned();
    if from_env.is_some_and(|value| value != token) {
        return Err(common::error(
            "EAM_WEB_TOKEN differs from the saved token file",
        ));
    }
    Ok(token)
}
