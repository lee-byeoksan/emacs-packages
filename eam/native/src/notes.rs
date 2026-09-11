//! Private notes follow provider conversation identity, never a working directory.
use crate::common::*;
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

fn identity(metadata: &Value, id: &str) -> Result<Value> {
    if id.is_empty() || id.len() > 128 || id.chars().any(char::is_control) {
        return Err(error("Invalid note conversation ID"));
    }
    Ok(json!([
        string(metadata, "provider")?,
        string(metadata, "auth_root")?,
        id
    ]))
}
fn database(path: &Path) -> Result<Connection> {
    let parent = path
        .parent()
        .ok_or_else(|| error("Missing session parent"))?;
    let root = if parent.file_name().is_some_and(|s| s == "persistent") {
        parent.parent().unwrap_or(parent)
    } else {
        parent
    };
    let dir = root.join("notes");
    mkdir(&dir)?;
    let db = Connection::open(dir.join("notes.sqlite3"))?;
    db.busy_timeout(Duration::from_millis(1000))?;
    db.execute_batch("CREATE TABLE IF NOT EXISTS notes (identity TEXT PRIMARY KEY, text TEXT NOT NULL, revision TEXT NOT NULL)")?;
    Ok(db)
}
fn pending(path: &Path) -> Result<PathBuf> {
    let file = path.join("pending-note.json");
    let old = path.join("note.json");
    if !file.exists() && old.exists() {
        fs::rename(old, &file)?;
    }
    Ok(file)
}
fn read_pending(file: &Path) -> Result<Value> {
    if !file.exists() {
        return Ok(json!({"text":"", "revision":null}));
    }
    let mut v = read_json(file, 400000)?;
    if v["revision"].is_null() {
        v["revision"] = json!(unique());
        save(file, &v)?;
    }
    Ok(v)
}
fn read(db: &Connection, key: &str) -> Result<Value> {
    Ok(db
        .query_row(
            "SELECT text,revision FROM notes WHERE identity=?",
            [key],
            |r| Ok(json!({"text":r.get::<_,String>(0)?,"revision":r.get::<_,String>(1)?})),
        )
        .optional()?
        .unwrap_or(json!({"text":"","revision":null})))
}
fn bind(path: &Path, key: &Value) -> Result<()> {
    let file = pending(path)?;
    if file.exists() {
        let v = read_pending(&file)?;
        let mut db = database(path)?;
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let key_string = key.to_string();
        let existing = read(&tx, &key_string)?;
        if !existing["revision"].is_null() && existing["text"] != v["text"] {
            return Err(error(format!("Conversation already has a note; pending note retained at {}. Resolve the two notes before retrying", file.display())));
        }
        if existing["revision"].is_null() {
            tx.execute(
                "INSERT INTO notes VALUES (?,?,?)",
                rusqlite::params![key_string, string(&v, "text")?, string(&v, "revision")?],
            )?;
        }
        tx.commit()?;
        fs::remove_file(file)?;
    }
    let binding = path.join("note-binding.json");
    if read_json(&binding, 16384).ok().as_ref() != Some(key) {
        save(&binding, key)?;
    }
    Ok(())
}
/// Called only with an identified conversation from existing passive observation.
pub fn remember(path: &Path, id: &str) -> Result<()> {
    let metadata = session(path)?;
    let key = identity(&metadata, id)?;
    let _lock = lock(&path.join("note.lock"), false)?;
    bind(path, &key)
}
pub fn dispatch(q: &Value, write: bool) -> Result<Value> {
    let path = Path::new(string(q, "session")?);
    let metadata = session(path)?;
    // An open editor keeps its original conversation even if the CLI changes it.
    let pinned = q.get("identity").filter(|v| v.is_array());
    let key = if let Some(key) = pinned {
        let id = key[2]
            .as_str()
            .ok_or_else(|| error("Invalid note identity"))?;
        let expected = identity(&metadata, id)?;
        if *key != expected {
            return Err(error("Note provider/root mismatch"));
        }
        Some(expected)
    } else {
        let telemetry = read_json(&path.join("telemetry.json"), 16384).unwrap_or(Value::Null);
        let entry = if telemetry["id"].as_str().is_some_and(|s| !s.is_empty()) {
            telemetry
        } else {
            crate::usage::history_entry(path).unwrap_or(Value::Null)
        };
        if let Some(id) = entry["id"].as_str() {
            Some(identity(&metadata, id)?)
        } else {
            read_json(&path.join("note-binding.json"), 16384).ok()
        }
    };
    let _lock = lock(&path.join("note.lock"), false)?;
    let file = pending(path)?;
    if pinned.is_none() {
        if let Some(ref key) = key {
            bind(path, key)?;
        }
    }
    let mut db = if key.is_some() {
        Some(database(path)?)
    } else {
        None
    };
    let mut result = if let (Some(db), Some(key)) = (&db, &key) {
        read(db, &key.to_string())?
    } else {
        read_pending(&file)?
    };
    if write {
        let text = string(q, "text")?;
        if text.len() > 65536 {
            return Err(error("Note exceeds 64 KiB"));
        }
        if let (Some(db), Some(key)) = (&mut db, &key) {
            let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let current = read(&tx, &key.to_string())?;
            if q.get("revision")
                .is_some_and(|rev| *rev != current["revision"])
            {
                return Err(error(
                    "Note changed in another editor; reopen before saving. Your edits are retained",
                ));
            }
            result = json!({"text":text,"revision":unique()});
            tx.execute("INSERT INTO notes VALUES (?,?,?) ON CONFLICT(identity) DO UPDATE SET text=excluded.text, revision=excluded.revision",
                rusqlite::params![key.to_string(),text,string(&result,"revision")?])?;
            tx.commit()?;
        } else {
            if q.get("revision")
                .is_some_and(|rev| *rev != result["revision"])
            {
                return Err(error("Pending note changed; reopen before saving"));
            }
            result = json!({"text":text,"revision":unique()});
            save(&file, &result)?;
        }
    }
    result["identity"] = key.unwrap_or(Value::Null);
    result["pending"] = json!(result["identity"].is_null());
    Ok(result)
}
