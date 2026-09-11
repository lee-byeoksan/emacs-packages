use crate::common::*;
use rusqlite::{params, Connection, OpenFlags};
use serde_json::{json, Value};
use std::{
    fs::{self, File},
    io::{BufRead, BufReader, Read},
    path::Path,
    time::{Duration, UNIX_EPOCH},
};
const ROW: usize = 8 * 1024 * 1024;
const LIST: usize = 300;
fn db(p: &Path) -> Result<Connection> {
    let c = Connection::open_with_flags(p, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    c.busy_timeout(Duration::from_secs(2))?;
    c.execute_batch("PRAGMA query_only=ON")?;
    Ok(c)
}
fn lines(p: &Path, mut visit: impl FnMut(Value) -> Result<bool>) -> Result<()> {
    let mut f = BufReader::new(File::open(p)?);
    loop {
        let mut b = Vec::new();
        let n = f
            .by_ref()
            .take((ROW + 1) as u64)
            .read_until(b'\n', &mut b)?;
        if n == 0 {
            break;
        }
        if n > ROW {
            return Err(error("Native history item exceeds 8 MiB"));
        }
        match serde_json::from_slice::<Value>(&b) {
            Ok(v) => {
                if v.is_object() && !visit(v)? {
                    break;
                }
            }
            Err(_) if b.last() != Some(&b'\n') => break,
            Err(_) => return Err(error("Unrecognized native JSONL record")),
        }
    }
    Ok(())
}
fn parts(v: &Value) -> String {
    if let Some(s) = v.as_str() {
        return s.to_owned();
    }
    v.as_array()
        .map(|a| {
            a.iter()
                .filter(|p| {
                    ["text", "input_text", "output_text"]
                        .contains(&p["type"].as_str().unwrap_or(""))
                })
                .filter_map(|p| p["text"].as_str())
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}
fn text100(s: &str) -> String {
    s.chars().take(100).collect()
}
fn messages(entry: &Value, mut emit: impl FnMut(&str, String) -> Result<()>) -> Result<()> {
    let codex = entry["provider"] == "Codex";
    if codex && entry["mode"] == "paginated" {
        let c = db(Path::new(string(entry, "history_db")?))?;
        let mut st=c.prepare("SELECT length(item_json),substr(item_json,1,?),item_type FROM thread_items WHERE thread_id=? AND item_type IN (?,?) ORDER BY rollout_ordinal")?;
        let mut rows = st.query(params![
            (ROW + 1) as i64,
            string(entry, "id")?,
            "userMessage",
            "agentMessage"
        ])?;
        while let Some(row) = rows.next()? {
            let n: i64 = row.get(0)?;
            let raw: String = row.get(1)?;
            let kind: String = row.get(2)?;
            if n > ROW as i64 || raw.len() > ROW {
                return Err(error("Native history item exceeds 8 MiB"));
            }
            let v: Value = serde_json::from_str(&raw)?;
            let agent = kind == "agentMessage";
            let text = if agent {
                v["text"].as_str().unwrap_or("").to_owned()
            } else {
                parts(&v["content"])
            };
            if !text.is_empty() {
                emit(if agent { "assistant" } else { "user" }, text)?;
            }
        }
        return Ok(());
    }
    lines(Path::new(string(entry, "path")?), |v| {
        let message = if codex {
            if v["type"] != "response_item" || v["payload"]["type"] != "message" {
                return Ok(true);
            }
            &v["payload"]
        } else {
            if !v["sessionId"].is_null() && v["sessionId"] != entry["id"]
                || v["isSidechain"] == true
                || v["isMeta"] == true
                || !["user", "assistant"].contains(&v["type"].as_str().unwrap_or(""))
            {
                return Ok(true);
            }
            &v["message"]
        };
        let role = message["role"].as_str().unwrap_or("");
        if ["user", "assistant"].contains(&role) {
            let text = parts(&message["content"]);
            if !text.is_empty() {
                emit(role, text)?
            }
        }
        Ok(true)
    })
}
fn list(q: &Value) -> Result<Value> {
    let root = Path::new(string(q, "root")?);
    let provider = string(q, "provider")?;
    let dir = q["directory"].as_str();
    let mut entries = Vec::new();
    if provider == "Codex" {
        let c = db(&root.join("state_5.sqlite"))?;
        let mut st=c.prepare("SELECT id,cwd,title,rollout_path,history_mode,updated_at FROM threads WHERE (? IS NULL OR rtrim(cwd,'/')=rtrim(?,'/')) ORDER BY updated_at DESC LIMIT ?")?;
        let mut rows = st.query(params![dir, dir, (LIST + 1) as i64])?;
        while let Some(row) = rows.next()? {
            let mode: String = row.get(4)?;
            if !["legacy", "paginated"].contains(&mode.as_str()) {
                return Err(error("Unsupported Codex history mode"));
            }
            entries.push(json!({"provider":provider,"id":row.get::<_,String>(0)?,"directory":row.get::<_,String>(1)?,"title":text100(&row.get::<_,Option<String>>(2)?.unwrap_or_default()),"path":row.get::<_,Option<String>>(3)?,"mode":mode,"updated":row.get::<_,i64>(5)?,"history_db":root.join("thread_history_1.sqlite")}));
        }
    } else if provider == "Claude" {
        let mut count = 0;
        for project in fs::read_dir(root.join("projects"))? {
            let project = project?.path();
            if !project.is_dir() {
                continue;
            }
            for entry in fs::read_dir(project)? {
                let path = entry?.path();
                if path.extension().is_none_or(|x| x != "jsonl") {
                    continue;
                }
                count += 1;
                if count > 10000 {
                    return Err(error("More than 10000 Claude histories"));
                }
                let (mut cwd, mut title) = (String::new(), String::new());
                let mut index = 0;
                lines(&path, |v| {
                    if cwd.is_empty() {
                        cwd = v["cwd"].as_str().unwrap_or("").to_owned()
                    }
                    if v["type"] == "user" && v["isMeta"] != true {
                        title = text100(&parts(&v["message"]["content"]))
                    }
                    index += 1;
                    Ok((cwd.is_empty() || title.is_empty()) && index <= 100)
                })?;
                if let Some(dir) = dir {
                    if cwd.is_empty() || fs::canonicalize(&cwd).ok() != fs::canonicalize(dir).ok() {
                        continue;
                    }
                }
                let id = path.file_stem().unwrap().to_string_lossy().into_owned();
                let updated = path
                    .metadata()?
                    .modified()?
                    .duration_since(UNIX_EPOCH)?
                    .as_secs_f64();
                entries.push(json!({"provider":provider,"id":id,"directory":cwd,"title":if title.is_empty(){id}else{title},"path":path,"updated":updated}));
                entries.sort_by(|a, b| {
                    b["updated"]
                        .as_f64()
                        .partial_cmp(&a["updated"].as_f64())
                        .unwrap_or(std::cmp::Ordering::Equal)
                });
                entries.truncate(LIST + 1);
            }
        }
    } else {
        return Err(error("Unsupported provider"));
    }
    let truncated = entries.len() > LIST;
    entries.truncate(LIST);
    Ok(json!({"entries":entries,"truncated":truncated}))
}
pub fn dispatch(q: &Value) -> Result<Value> {
    if q["action"] == "current" {
        return Ok(json!({"entry":crate::usage::history_entry(Path::new(string(q, "session")?))?}));
    }
    if q["action"] == "list" {
        return list(q);
    }
    let entry = &q["entry"];
    let limit = q["limit"].as_u64().unwrap_or(65536).clamp(128, 65536) as usize;
    let offset = q["offset"].as_u64().map(|x| x as usize);
    let prompt = q["prompt"] == true;
    let mut total = 0;
    let mut shown = String::new();
    messages(entry, |role, text| {
        if prompt {
            if role == "user" {
                total = text.chars().count();
                shown = text
                    .chars()
                    .skip(offset.unwrap_or(0).min(total))
                    .take(limit)
                    .collect();
            }
            return Ok(());
        }
        let block = format!(
            "\n## {}\n\n{}\n",
            if role == "user" {
                "사용자"
            } else {
                "응답"
            },
            text
        );
        let n = block.chars().count();
        if let Some(o) = offset {
            let a = o.saturating_sub(total);
            let b = n.min(o.saturating_add(limit).saturating_sub(total));
            if b > a {
                shown.extend(block.chars().skip(a).take(b - a));
            }
        } else {
            let tail: String = block.chars().skip(n.saturating_sub(limit)).collect();
            shown.push_str(&tail);
            let n = shown.chars().count();
            shown = shown.chars().skip(n.saturating_sub(limit)).collect();
        }
        total += n;
        Ok(())
    })?;
    let count = shown.chars().count();
    let start = if prompt {
        offset.unwrap_or(0).min(total)
    } else {
        offset.map_or(total.saturating_sub(count), |x| x.min(total))
    };
    Ok(
        json!({"text":shown,"start":start,"end":start+count,"total":total,"provider":entry["provider"],"id":entry["id"],"prompt":prompt}),
    )
}
