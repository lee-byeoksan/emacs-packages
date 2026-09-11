use serde_json::{json, Value};
#[derive(Default, Clone, Copy)]
enum State {
    #[default]
    Text,
    Escape,
    Osc,
    OscEscape,
    Discard,
    DiscardEscape,
    String,
    StringEscape,
}
#[derive(Default)]
pub struct Events {
    pending: Vec<u8>,
    state: State,
    offset: u64,
    sequence: u64,
}
impl Events {
    fn finish(&mut self) -> Option<Value> {
        let text = std::str::from_utf8(&self.pending).ok()?;
        if text.starts_with("9;4;") || text.starts_with("9;9;") {
            return None;
        }
        let (title, body) = if let Some(s) = text.strip_prefix("9;") {
            ("", s)
        } else if let Some(s) = text.strip_prefix("777;notify;") {
            s.split_once(';')?
        } else {
            return None;
        };
        if title.is_empty() && body.is_empty()
            || title
                .chars()
                .chain(body.chars())
                .any(|c| c < ' ' || ('\u{7f}'..='\u{9f}').contains(&c))
        {
            return None;
        }
        self.sequence += 1;
        Some(
            json!({"version":1,"seq":self.sequence,"end_offset":self.offset,"title":title,"body":body}),
        )
    }
    pub fn feed(&mut self, data: &[u8]) -> Vec<Value> {
        use State::*;
        let mut out = Vec::new();
        for &b in data {
            self.offset += 1;
            match self.state {
                Text => {
                    if b == 27 {
                        self.state = Escape
                    }
                }
                Escape => {
                    self.pending.clear();
                    self.state = match b {
                        b']' => Osc,
                        b'P' | b'X' | b'^' | b'_' => String,
                        27 => Escape,
                        _ => Text,
                    }
                }
                Osc | Discard => match b {
                    24 | 26 => {
                        self.pending.clear();
                        self.state = Text
                    }
                    7 => {
                        if matches!(self.state, Osc) {
                            if let Some(v) = self.finish() {
                                out.push(v)
                            }
                        }
                        self.pending.clear();
                        self.state = Text
                    }
                    27 => {
                        self.state = if matches!(self.state, Osc) {
                            OscEscape
                        } else {
                            DiscardEscape
                        }
                    }
                    _ => {
                        if matches!(self.state, Osc) {
                            if self.pending.len() >= 8192 {
                                self.pending.clear();
                                self.state = Discard
                            } else {
                                self.pending.push(b)
                            }
                        }
                    }
                },
                OscEscape | DiscardEscape => {
                    if b == b'\\' {
                        if matches!(self.state, OscEscape) {
                            if let Some(v) = self.finish() {
                                out.push(v)
                            }
                        }
                        self.state = Text
                    } else {
                        self.state = if b == 27 { DiscardEscape } else { Discard }
                    }
                    self.pending.clear()
                }
                String => match b {
                    24 | 26 => self.state = Text,
                    27 => self.state = StringEscape,
                    _ => (),
                },
                StringEscape => {
                    self.state = if b == b'\\' {
                        Text
                    } else if b == 27 {
                        StringEscape
                    } else {
                        String
                    }
                }
            }
        }
        out
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fragmented_osc_and_ignored_control_strings() {
        let mut e = Events::default();
        let mut out = Vec::new();
        for b in b"\x1b]777;notify;Claude;Stop\x07" {
            out.extend(e.feed(&[*b]));
        }
        assert_eq!(out[0]["body"], "Stop");
        assert!(e.feed(b"\x1bPignored;\x1b]9;hidden\x07\x1b\\").is_empty());
        assert!(e
            .feed(b"\x1b]9;4;progress\x07\x1b]9;\x01bad\x07")
            .is_empty());
    }
}
