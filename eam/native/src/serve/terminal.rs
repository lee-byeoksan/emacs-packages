use super::*;
use axum::extract::ws::{Message, WebSocket};
use bytes::{Buf, BufMut, BytesMut};
use futures_util::{SinkExt, StreamExt};
use std::{fs::File, io};
use tokio::{net::UnixStream, time::Instant};
use tokio_util::codec::{Decoder, Encoder, Framed};

#[derive(Clone)]
pub(super) struct Attachment {
    pub id: String,
    pub stop: CancellationToken,
    pub done: CancellationToken,
}
// This guard also releases a prepared attachment if the HTTP upgrade fails.
struct Connection {
    framed: Option<Framed<UnixStream, PacketCodec>>,
    lease: Option<File>,
    remote_lease: Option<File>,
    awake: Option<common::KeepAwake>,
    done: CancellationToken,
}
impl Drop for Connection {
    fn drop(&mut self) {
        self.framed.take();
        self.awake.take();
        self.remote_lease.take();
        self.lease.take();
        self.done.cancel();
    }
}
struct PacketCodec;
impl Decoder for PacketCodec {
    type Item = (u8, Vec<u8>);
    type Error = io::Error;
    fn decode(&mut self, src: &mut BytesMut) -> io::Result<Option<Self::Item>> {
        if src.len() < 5 {
            return Ok(None);
        }
        let length = u32::from_be_bytes(src[1..5].try_into().unwrap()) as usize;
        if length > 262144 {
            return Err(io::Error::other("Oversized daemon packet"));
        }
        if src.len() < length + 5 {
            return Ok(None);
        }
        let kind = src[0];
        src.advance(5);
        Ok(Some((kind, src.split_to(length).to_vec())))
    }
}
impl Encoder<(u8, Vec<u8>)> for PacketCodec {
    type Error = io::Error;
    fn encode(&mut self, (kind, data): (u8, Vec<u8>), dst: &mut BytesMut) -> io::Result<()> {
        dst.put_u8(kind);
        dst.put_u32(data.len() as u32);
        dst.extend_from_slice(&data);
        Ok(())
    }
}
async fn finish(app: Shared, path: PathBuf, attachment: Attachment) {
    // Resource release is signalled before acquiring the management mutex.
    attachment.done.cancelled().await;
    let mut control = app.control.lock().await;
    if control
        .active
        .get(&path)
        .is_some_and(|a| a.id == attachment.id)
    {
        control.active.remove(&path);
        if let Err(e) = return_to_emacs(&mut control, &path).await {
            eprintln!(
                "Automatic Emacs return failed for {}: {}",
                path.display(),
                e.1
            );
        }
    }
}
pub(super) async fn upgrade(
    State(app): State<Shared>,
    Query(q): Query<HashMap<String, String>>,
    ws: WebSocketUpgrade,
) -> WebResult<Response> {
    let path = app.session(q.get("session").ok_or_else(|| fail("Missing session"))?)?;
    let mut control = app.control.lock().await;
    if app.stopping.is_cancelled() {
        return Err(fail("Server stopping"));
    }
    let lease = common::lock(&path.join("attach.lock"), true)
        .map_err(|_| fail("다른 화면이 조작 중입니다. 조작권 가져오기를 선택하세요."))?;
    let info = manager("inspect", json!({"session":path})).await?;
    if info["status"]["state"] != "running" || info["status"]["attached_clients"] != 0 {
        return Err(fail("연결할 수 없는 세션입니다."));
    }
    if info["metadata"]["temporary"] == true {
        return Err(fail("Quick 세션은 먼저 eam-persist로 전환하세요."));
    }
    let remote_lease = common::lock(&path.join("remote.lock"), true).map_err(internal)?;
    let stream =
        UnixStream::connect(Path::new(field(&info["metadata"], "runtime")?).join("socket"))
            .await
            .map_err(internal)?;
    let attachment = Attachment {
        id: common::unique(),
        stop: CancellationToken::new(),
        done: CancellationToken::new(),
    };
    let connection = Connection {
        framed: Some(Framed::new(stream, PacketCodec)),
        lease: Some(lease),
        remote_lease: Some(remote_lease),
        awake: Some(common::KeepAwake::start()),
        done: attachment.done.clone(),
    };
    control.active.insert(path.clone(), attachment.clone());
    drop(control);
    let (failed_app, failed_path, failed_attachment) =
        (app.clone(), path.clone(), attachment.clone());
    Ok(ws.max_message_size(32768).max_frame_size(32768)
        .on_failed_upgrade(move |_| { tokio::spawn(finish(failed_app,failed_path,failed_attachment)); })
        .on_upgrade(move |mut socket|async move {
            let mut connection=connection;
            let result=tokio::select! {
                _=attachment.stop.cancelled()=>Ok(()),
                result=relay(&mut socket,connection.framed.as_mut().unwrap(),&attachment.id)=>result,
            };
            // Dropping both the Unix connection and flock must precede WS close.
            drop(connection);
            if let Err(e)=result { eprintln!("Web terminal disconnected: {}",e.1); }
            finish(app,path,attachment).await;
            let _=timeout(Duration::from_secs(1),socket.close()).await;
        }))
}
async fn send(ws: &mut WebSocket, message: Message) -> WebResult<()> {
    timeout(Duration::from_secs(5), ws.send(message))
        .await
        .map_err(|_| fail("Slow browser send"))?
        .map_err(internal)
}
// A fresh browser has no screen state once the daemon replay buffer overflows.
// Reapplying an unchanged TIOCSWINSZ does not notify the CLI. Briefly change
// one row on the first resize so TUIs repaint even on same-size reattachment.
async fn refresh_size(
    framed: &mut Framed<UnixStream, PacketCodec>,
    rows: u16,
    cols: u16,
) -> WebResult<()> {
    let packet = |rows: u16| {
        (
            b'R',
            [rows, cols, 0, 0]
                .into_iter()
                .flat_map(u16::to_ne_bytes)
                .collect(),
        )
    };
    let temporary = if rows > 1 { rows - 1 } else { 2 };
    timeout(Duration::from_secs(5), framed.send(packet(temporary)))
        .await
        .map_err(|_| fail("CLI resize timeout"))?
        .map_err(internal)?;
    // Give the foreground process an event-loop turn before restoring the size.
    tokio::time::sleep(Duration::from_millis(100)).await;
    timeout(Duration::from_secs(5), framed.send(packet(rows)))
        .await
        .map_err(|_| fail("CLI resize timeout"))?
        .map_err(internal)?;
    Ok(())
}
async fn relay(
    ws: &mut WebSocket,
    framed: &mut Framed<UnixStream, PacketCodec>,
    id: &str,
) -> WebResult<()> {
    send(
        ws,
        Message::Text(json!({"type":"connection","id":id}).to_string().into()),
    )
    .await?;
    let mut pending = 0usize;
    let mut first_resize = true;
    let mut last_receive = Instant::now();
    let mut last_ack = Instant::now();
    let mut ticks = tokio::time::interval(Duration::from_secs(5));
    loop {
        tokio::select! {
            packet=framed.next(), if pending<131072 => {
                let Some(packet)=packet else { return Ok(()); };
                let (kind, payload)=packet.map_err(internal)?;
                match kind {
                    b'O'=>{
                        if pending==0 {last_ack=Instant::now();}
                        pending+=payload.len();
                        send(ws,Message::Binary(payload.into())).await?;
                    }
                    b'H'=>{
                        let mut value:Value=serde_json::from_slice(&payload).map_err(internal)?;
                        value["type"]=json!("ready");
                        send(ws,Message::Text(value.to_string().into())).await?;
                    }
                    _=>return Err(fail("Unknown daemon packet")),
                }
            }
            message=ws.recv()=>{
                let Some(message)=message else { return Ok(()); };
                last_receive=Instant::now();
                match message.map_err(internal)? {
                    Message::Close(_)=>return Ok(()),
                    Message::Ping(bytes)=>{send(ws,Message::Pong(bytes)).await?;}
                    Message::Pong(_)=>{},
                    Message::Text(text)=>{
                        let q:Value=serde_json::from_str(&text).map_err(internal)?;
                        let packet=match field(&q,"type")? {
                            "ack"=>{
                                let n=q["bytes"].as_u64().ok_or_else(||fail("Invalid ACK"))?;
                                if n==0 || n>pending as u64 {return Err(fail("Invalid ACK"));}
                                pending-=n as usize; last_ack=Instant::now(); continue;
                            }
                            "ping"=>{send(ws,Message::Text(json!({"type":"pong","at":q["at"]}).to_string().into())).await?;continue;}
                            "input"=>{
                                let bytes=field(&q,"data")?.as_bytes();
                                if bytes.len()>16384 {return Err(fail("Input too large"));}
                                (b'I',bytes.to_vec())
                            }
                            "resize"=>{
                                let rows=q["rows"].as_u64().unwrap_or(0);
                                let cols=q["cols"].as_u64().unwrap_or(0);
                                if !(1..=1000).contains(&rows)||!(1..=1000).contains(&cols){return Err(fail("Invalid terminal size"));}
                                if first_resize {
                                    first_resize=false;
                                    refresh_size(framed,rows as u16,cols as u16).await?;
                                    continue;
                                }
                                let payload=[rows as u16,cols as u16,0,0].into_iter().flat_map(u16::to_ne_bytes).collect();
                                (b'R',payload)
                            }
                            _=>return Err(fail("Unknown terminal message")),
                        };
                        timeout(Duration::from_secs(5),framed.send(packet)).await.map_err(|_|fail("CLI input timeout"))?.map_err(internal)?;
                    }
                    _=>return Err(fail("Expected JSON terminal input")),
                }
            }
            _=ticks.tick()=>{
                if last_receive.elapsed()>Duration::from_secs(30) || (pending>0 && last_ack.elapsed()>Duration::from_secs(30)) {
                    return Err(fail("Browser heartbeat/ACK timeout"));
                }
                send(ws,Message::Ping(Vec::new().into())).await?;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn first_resize_changes_then_restores_exact_dimensions() {
        for rows in [1u16, 40, 1000] {
            let (a, b) = UnixStream::pair().unwrap();
            let mut writer = Framed::new(a, PacketCodec);
            let mut reader = Framed::new(b, PacketCodec);
            refresh_size(&mut writer, rows, 80).await.unwrap();
            for expected in [if rows > 1 { rows - 1 } else { 2 }, rows] {
                let (kind, payload) = reader.next().await.unwrap().unwrap();
                assert_eq!(kind, b'R');
                assert_eq!(
                    payload,
                    [expected, 80, 0, 0]
                        .into_iter()
                        .flat_map(u16::to_ne_bytes)
                        .collect::<Vec<_>>()
                );
            }
        }
    }
    #[test]
    fn packet_decoder_preserves_partial_frames() {
        let mut decoder = PacketCodec;
        let mut bytes = BytesMut::from(&b"O\0\0\0\x03\xed"[..]);
        assert!(decoder.decode(&mut bytes).unwrap().is_none());
        bytes.extend_from_slice(&[0x95, 0x9c]);
        assert_eq!(
            decoder.decode(&mut bytes).unwrap().unwrap(),
            (b'O', "한".as_bytes().to_vec())
        );
        assert!(bytes.is_empty());
    }
}
