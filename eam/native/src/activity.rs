//! Bounded, passive transcript observation. No prompts are copied to disk.
use serde_json::{json, Value};
use std::collections::BTreeMap;

fn text(v: &Value, limit: usize) -> String {
    v.as_str()
        .unwrap_or("")
        .chars()
        .filter(|c| {
            (!c.is_control() || *c == '\n' || *c == '\t')
                && !matches!(*c, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')
        })
        .take(limit)
        .collect()
}

pub fn snapshot(records: &[Value], codex: bool, id: &Value, partial: bool) -> Value {
    let mut state = "unknown";
    let mut estimated = true;
    let mut prompt = String::new();
    let mut timestamp = Value::Null;
    let mut tools = BTreeMap::new();
    for v in records {
        if !codex && (v["sessionId"] != *id || v["isSidechain"] == true) {
            continue;
        }
        let before = state;
        if codex {
            let p = &v["payload"];
            if v["type"] == "event_msg" {
                match p["type"].as_str().unwrap_or("") {
                    "task_started" | "user_message" => {
                        tools.clear();
                        state = "thinking";
                        estimated = false;
                        if p["type"] == "user_message" {
                            prompt = text(&p["message"], 1024);
                        }
                    }
                    "task_complete" | "turn_aborted" => {
                        tools.clear();
                        state = "waiting";
                        estimated = false;
                    }
                    _ => (),
                }
            } else if v["type"] == "response_item" {
                match p["type"].as_str().unwrap_or("") {
                    "function_call" | "custom_tool_call" => {
                        let key = text(&p["call_id"], 128);
                        if tools.len() < 8 {
                            tools.insert(key, text(&p["name"], 80));
                        }
                        state = "executing";
                        estimated = true;
                    }
                    "function_call_output" | "custom_tool_call_output" => {
                        tools.remove(&text(&p["call_id"], 128));
                        state = if tools.is_empty() {
                            "thinking"
                        } else {
                            "executing"
                        };
                        estimated = true;
                    }
                    "message" if p["role"] == "assistant" && p["phase"] == "final_answer" => {
                        tools.clear();
                        state = "waiting";
                        estimated = true;
                    }
                    _ => (),
                }
            }
        } else {
            let m = &v["message"];
            // Claude may omit stop_reason in JSONL. An assistant text block
            // with no outstanding tool is only an inferred input wait.
            if v["type"] == "assistant"
                && tools.is_empty()
                && m["content"]
                    .as_array()
                    .is_some_and(|blocks| blocks.iter().any(|b| b["type"] == "text"))
            {
                state = "waiting";
            }
            let mut user_text = String::new();
            if v["type"] == "user" && m["content"].is_string() {
                user_text = text(&m["content"], 1024);
            }
            if let Some(blocks) = m["content"].as_array() {
                for b in blocks {
                    match b["type"].as_str().unwrap_or("") {
                        "text" if v["type"] == "user" && user_text.is_empty() => {
                            user_text = text(&b["text"], 1024);
                        }
                        "tool_use" if v["type"] == "assistant" => {
                            if tools.len() < 8 {
                                tools.insert(text(&b["id"], 128), text(&b["name"], 80));
                            }
                            state = "executing";
                        }
                        "tool_result" if v["type"] == "user" => {
                            tools.remove(&text(&b["tool_use_id"], 128));
                            state = if tools.is_empty() {
                                "thinking"
                            } else {
                                "executing"
                            };
                        }
                        _ => (),
                    }
                }
            }
            if !user_text.is_empty() && v["isMeta"] != true {
                prompt = user_text;
                tools.clear();
                state = "thinking";
            }
            if v["type"] == "assistant" && m["stop_reason"] == "end_turn" {
                tools.clear();
                state = "waiting";
            }
            // Claude JSONL is an observation, not a live execution-status API.
            estimated = true;
        }
        if state != before {
            timestamp = json!(text(&v["timestamp"], 80));
        }
    }
    json!({"state":state,"estimated":estimated,"prompt":prompt,
        "tools":tools.values().collect::<Vec<_>>(),"since":timestamp,
        "partial":partial,"source":"CLI transcript (bounded tail)"})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parallel_tools_and_completion() {
        let mut rows = vec![
            json!({"type":"event_msg","payload":{"type":"user_message","message":"한글 요청"}}),
            json!({"type":"response_item","payload":{"type":"function_call","call_id":"a","name":"exec_command"}}),
            json!({"type":"response_item","payload":{"type":"function_call","call_id":"b","name":"read"}}),
            json!({"type":"response_item","payload":{"type":"function_call_output","call_id":"a"}}),
        ];
        let v = snapshot(&rows, true, &Value::Null, false);
        assert_eq!(v["state"], "executing");
        assert_eq!(v["tools"], json!(["read"]));
        assert_eq!(v["prompt"], "한글 요청");
        rows.push(json!({"type":"event_msg","payload":{"type":"task_complete"}}));
        assert_eq!(
            snapshot(&rows, true, &Value::Null, false)["state"],
            "waiting"
        );
    }
    #[test]
    fn claude_tool_result_is_not_a_user_request_and_sidechains_are_ignored() {
        let rows = vec![
            json!({"sessionId":"x","type":"user","message":{"content":"요청"}}),
            json!({"sessionId":"x","type":"assistant","message":{"content":[{"type":"tool_use","id":"a","name":"Read"}]}}),
            json!({"sessionId":"x","type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":"not a prompt"}]}}),
            json!({"sessionId":"x","isSidechain":true,"type":"user","message":{"content":"wrong"}}),
        ];
        let v = snapshot(&rows, false, &json!("x"), true);
        assert_eq!(v["state"], "thinking");
        assert_eq!(v["prompt"], "요청");
        assert_eq!(v["estimated"], true);
        assert_eq!(v["partial"], true);
        assert_eq!(snapshot(&[], true, &Value::Null, true)["state"], "unknown");
    }
    #[test]
    fn claude_text_without_stop_reason_is_only_inferred_waiting() {
        let rows = vec![
            json!({"sessionId":"x","type":"user","message":{"content":"request"}}),
            json!({"sessionId":"x","type":"assistant","message":{"content":[{"type":"text","text":"response"}]}}),
        ];
        let v = snapshot(&rows, false, &json!("x"), false);
        assert_eq!(v["state"], "waiting");
        assert_eq!(v["estimated"], true);
    }
}
