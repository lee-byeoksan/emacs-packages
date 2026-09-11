//! Display-only local identity metadata. Never returns credentials or verifies login.
use crate::common::*;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
fn text(v: &Value, max: usize) -> Value {
    match v.as_str().map(str::trim) {
        Some(s)
            if !s.is_empty() && s.chars().count() <= max && !s.chars().any(char::is_control) =>
        {
            json!(s)
        }
        _ => Value::Null,
    }
}
// Decode the bounded ID-token payload for display, not authentication.
fn claims(token: &str) -> Option<Value> {
    if token.len() > 65536 {
        return None;
    }
    let pieces: Vec<_> = token.split('.').collect();
    if pieces.len() != 3 {
        return None;
    }
    let mut out = Vec::new();
    let (mut bits, mut n) = (0u32, 0u32);
    for c in pieces[1].bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'-' => 62,
            b'_' => 63,
            _ => return None,
        };
        bits = (bits << 6) | v as u32;
        n += 6;
        if n >= 8 {
            n -= 8;
            out.push((bits >> n) as u8);
            bits &= (1 << n) - 1;
        }
    }
    if n == 6 || bits != 0 {
        return None;
    }
    serde_json::from_slice(&out).ok()
}
pub fn launch_root(provider: &str, cwd: &Path) -> Option<PathBuf> {
    let (key, suffix) = match provider {
        "Codex" => ("CODEX_HOME", ".codex"),
        "Claude" => ("CLAUDE_CONFIG_DIR", ".claude"),
        _ => return None,
    };
    let root = std::env::var_os(key)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|p| Path::new(&p).join(suffix)))?;
    if root.is_absolute() {
        Some(root)
    } else {
        Some(cwd.join(root))
    }
}
pub fn config_override(root: &Path, saved: &Value) -> bool {
    saved.as_bool().unwrap_or_else(|| {
        // Older sessions did not record whether the variable was set. Infer
        // the ordinary default only for the host's default Claude directory.
        std::env::var_os("HOME").is_none_or(|home| root != Path::new(&home).join(".claude"))
    })
}
fn read(provider: &str, root: &Path, explicit_config: bool) -> Result<Value> {
    let mut result = json!({"state":"unknown","source":"local profile"});
    if provider == "Codex" {
        // An old auth.json can coexist with a newer keyring login.
        if root.join("config.toml").is_file() {
            let raw = String::from_utf8(bounded(&root.join("config.toml"), 1024 * 1024)?)?;
            let config: toml::Value = match toml::from_str(&raw) {
                Ok(config) => config,
                Err(_) => {
                    result["reason"] = json!("unrecognized config");
                    return Ok(result);
                }
            };
            if config
                .get("cli_auth_credentials_store")
                .and_then(toml::Value::as_str)
                .is_some_and(|v| v != "file")
            {
                return Ok(result);
            }
        }
        let auth = read_json(&root.join("auth.json"), 256 * 1024)?;
        if matches!(auth["auth_mode"].as_str(), Some("apikey" | "apiKey"))
            || (auth["auth_mode"].is_null()
                && auth["OPENAI_API_KEY"]
                    .as_str()
                    .is_some_and(|s| !s.is_empty()))
        {
            return Ok(json!({"state":"local","source":"local profile","method":"API key"}));
        }
        if auth["auth_mode"].as_str().is_some_and(|s| s != "chatgpt") {
            return Ok(result);
        }
        let token = string(&auth["tokens"], "id_token")?;
        let Some(c) = claims(token) else {
            result["reason"] = json!("unrecognized identity metadata");
            return Ok(result);
        };
        result["email"] = text(&c["email"], 254);
        result["name"] = text(&c["name"], 80);
        let info = &c["https://api.openai.com/auth"];
        if auth["tokens"]["account_id"] == info["chatgpt_account_id"] {
            result["plan"] = text(&info["chatgpt_plan_type"], 40);
        }
        result["method"] = json!("ChatGPT");
    } else if provider == "Claude" {
        let config = if explicit_config {
            root.join(".claude.json")
        } else {
            root.with_extension("json")
        };
        let profile = read_json(&config, 2 * 1024 * 1024)?;
        let a = &profile["oauthAccount"];
        result["email"] = text(&a["emailAddress"], 254);
        result["name"] = text(&a["displayName"], 80);
        result["organization"] = text(&a["organizationName"], 80);
        // A seat tier is not necessarily the user's billing plan.
        result["tier"] = text(&a["seatTier"], 40);
        result["method"] = json!("Claude profile");
    }
    if result["email"].as_str().is_some_and(|s| s.contains('@')) {
        result["state"] = json!("local");
    } else {
        result = json!({"state":"unknown","source":"local profile"});
    }
    Ok(result)
}
pub fn snapshot(provider: &str, root: &Path) -> Value {
    snapshot_with_override(provider, root, config_override(root, &Value::Null))
}
pub fn snapshot_with_override(provider: &str, root: &Path, explicit_config: bool) -> Value {
    read(provider, root, explicit_config)
        .unwrap_or_else(|_| json!({"state":"unknown","source":"local profile"}))
}

pub fn claude_cli(exe: &str, root: &Path, explicit_config: bool) -> Option<Value> {
    let mut command = std::process::Command::new(exe);
    command.args(["auth", "status", "--json"]);
    if explicit_config {
        command.env("CLAUDE_CONFIG_DIR", root);
    } else {
        command.env_remove("CLAUDE_CONFIG_DIR");
    }
    // Match the subscription-only CLI launch environment.
    for key in [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    ] {
        command.env_remove(key);
    }
    let raw = crate::usage::capture_command(&mut command).ok()?;
    let v: Value = serde_json::from_slice(&raw).ok()?;
    if v["loggedIn"] != true {
        return Some(json!({"state":"unknown","source":"auth status"}));
    }
    Some(
        json!({"state":"local","source":"auth status","email":text(&v["email"],254),
        "plan":text(&v["subscriptionType"],80),"organization":text(&v["orgName"],80)}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn encoded(value: &Value) -> String {
        let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        let mut out = String::new();
        let (mut bits, mut n) = (0u32, 0u32);
        for byte in serde_json::to_vec(value).unwrap() {
            bits = (bits << 8) | byte as u32;
            n += 8;
            while n >= 6 {
                n -= 6;
                out.push(alphabet[((bits >> n) & 63) as usize] as char);
            }
            bits &= (1 << n) - 1;
        }
        if n > 0 {
            out.push(alphabet[(bits << (6 - n)) as usize] as char);
        }
        format!("header.{out}.signature")
    }
    #[test]
    fn accounts_allowlist_identity_without_secrets_or_writes() {
        let root = std::env::temp_dir().join(format!("eam-account-{}", unique()));
        mkdir(&root).unwrap();
        let token = encoded(
            &json!({"email":"user@example.test","https://api.openai.com/auth":{"chatgpt_account_id":"account","chatgpt_plan_type":"plus"},"sensitive":"must-not-return"}),
        );
        let auth = json!({"auth_mode":"chatgpt","tokens":{"id_token":token,"access_token":"SECRET-ACCESS","refresh_token":"SECRET-REFRESH","account_id":"account"}});
        save(&root.join("auth.json"), &auth).unwrap();
        let before = std::fs::read(root.join("auth.json")).unwrap();
        let result = snapshot("Codex", &root);
        assert_eq!(result["email"], "user@example.test");
        assert_eq!(result["plan"], "plus");
        assert!(!result.to_string().contains("SECRET"));
        assert!(!result.to_string().contains("token"));
        assert!(!result.to_string().contains("must-not-return"));
        assert_eq!(before, std::fs::read(root.join("auth.json")).unwrap());
        std::fs::write(
            root.join("config.toml"),
            "cli_auth_credentials_store = 'keyring'\n",
        )
        .unwrap();
        assert_eq!(snapshot("Codex", &root)["state"], "unknown");
        std::fs::write(
            root.join("config.toml"),
            "cli_auth_credentials_store = 'file'\nmodel = 'test'\n",
        )
        .unwrap();
        assert_eq!(snapshot("Codex", &root)["email"], "user@example.test");
        std::fs::write(root.join("config.toml"), "broken = [").unwrap();
        assert_eq!(snapshot("Codex", &root)["state"], "unknown");
        std::fs::remove_file(root.join("config.toml")).unwrap();
        let mut other = auth.clone();
        other["tokens"]["account_id"] = json!("different-account");
        save(&root.join("auth.json"), &other).unwrap();
        assert!(snapshot("Codex", &root)["plan"].is_null());
        save(
            &root.join("auth.json"),
            &json!({"auth_mode":"apikey","OPENAI_API_KEY":"SECRET-KEY","tokens":auth["tokens"]}),
        )
        .unwrap();
        assert_eq!(snapshot("Codex", &root)["method"], "API key");
        assert!(snapshot("Codex", &root)["email"].is_null());
        save(&root.join(".claude.json"),&json!({"oauthAccount":{"emailAddress":"other@example.test","organizationName":"Test Org","seatTier":"max","secret":"SECRET"},"apiKey":"SECRET"})).unwrap();
        let c = snapshot("Claude", &root);
        assert_eq!(c["email"], "other@example.test");
        assert_eq!(c["tier"], "max");
        assert!(c["plan"].is_null());
        assert!(!c.to_string().contains("SECRET"));
        std::fs::write(root.join("auth.json"), "malformed").unwrap();
        assert_eq!(snapshot("Codex", &root)["state"], "unknown");
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn malformed_claims_are_not_identity() {
        for s in ["", "a.!.b", "a.A.b", "a..b", "a.e30.b.extra"] {
            assert!(claims(s).is_none());
        }
        assert!(claims(&"x".repeat(65537)).is_none());
    }
    #[test]
    fn claude_default_profile_is_not_shadowed_by_explicit_config_profile() {
        let temp = std::env::temp_dir().join(format!("eam-claude-account-{}", unique()));
        let root = temp.join(".claude");
        mkdir(&root).unwrap();
        save(
            &temp.join(".claude.json"),
            &json!({"oauthAccount":{"emailAddress":"default@example.test"}}),
        )
        .unwrap();
        save(&root.join(".claude.json"), &json!({"someSetting":true})).unwrap();
        assert_eq!(
            snapshot_with_override("Claude", &root, false)["email"],
            "default@example.test"
        );
        // Missing identity in an explicit config must not borrow the host login.
        assert_eq!(
            snapshot_with_override("Claude", &root, true)["state"],
            "unknown"
        );
        save(
            &root.join(".claude.json"),
            &json!({"oauthAccount":{"emailAddress":"custom@example.test"}}),
        )
        .unwrap();
        assert_eq!(
            snapshot_with_override("Claude", &root, true)["email"],
            "custom@example.test"
        );
        assert!(!config_override(&root, &json!(false)));
        assert!(config_override(&root, &json!(true)));
        std::fs::remove_dir_all(temp).unwrap();
    }
}
